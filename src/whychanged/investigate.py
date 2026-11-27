"""One investigation: what changed, by how much, where, and is it unusual.

    measure + period + baseline
      → the measure in both periods                    (2 small queries)
      → split by each dimension, both periods           (2 per dimension)
      → the measure over the 12 periods before          (1 query)
      → Findings: totals, drivers ranked by their part of the change, anomaly

Everything returned is an aggregate. Rows never leave the executor, and a model
that later puts the findings into words sees only this object.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from whychanged.anomaly import Anomaly, assess
from whychanged.decompose import (
    Driver,
    Segment,
    concentration,
    decompose_rate,
    decompose_total,
    explained,
    rate,
)
from whychanged.executors import Executor
from whychanged.measures import Dimension, Measure
from whychanged.periods import Period, align_to_date, history
from whychanged.queries import series_sql, slice_sql

# Drivers are listed until they carry this much of the change, or TOP_DRIVERS of them
EXPLAIN_TARGET = 0.7
TOP_DRIVERS = 4
# A driver carrying less than this share of the change is noise, not worth a line
MIN_SHARE = 0.02
# Below this concentration the change is spread evenly over the dimension's segments
EVEN_SPREAD = 0.15
# Queries in flight at once against the customer's database
PARALLEL = 4


@dataclass
class Findings:
    measure: str
    measure_name: str
    format: str
    is_rate: bool
    period: dict[str, Any]
    baseline: dict[str, Any]
    partial: bool
    before: float | None
    after: float | None
    change: float | None
    pct_change: float | None
    direction: str  # "up" | "down" | "flat"
    drivers: list[dict[str, Any]] = field(default_factory=list)
    offsets: list[dict[str, Any]] = field(default_factory=list)
    explained: float | None = None
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    anomaly: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _value(measure: Measure, base: float, hits: float | None) -> float | None:
    return rate(hits, base) if measure.is_rate else base


def _period_dict(p: Period) -> dict[str, Any]:
    return {
        "label": p.label,
        "start": p.start.isoformat(),
        "end": (p.end - timedelta(days=1)).isoformat(),
    }


async def investigate(
    measure: Measure,
    period: Period,
    baseline: Period,
    executor: Executor,
    *,
    today: date | None = None,
) -> Findings:
    today = today or date.today()
    current, base, partial = align_to_date(period, baseline, today)
    gate = asyncio.Semaphore(PARALLEL)
    queries: list[str] = []

    async def run(sql: str) -> list[tuple[Any, ...]]:
        queries.append(sql)
        async with gate:
            _, rows = await executor(sql)
        return rows

    # A running period is tested against the same number of days in each past
    # period, so a half month is compared with half months; whole periods whole
    past = [
        replace(w, end=min(w.end, w.start + timedelta(days=current.days))) if partial else w
        for w in history(period)
    ]

    totals_task = asyncio.gather(run(slice_sql(measure, base)), run(slice_sql(measure, current)))
    dim_tasks = [
        asyncio.gather(run(slice_sql(measure, base, d)), run(slice_sql(measure, current, d)))
        for d in measure.dimensions
    ]
    series_task = run(series_sql(measure, past))
    (before_rows, after_rows), dim_results, series_rows = await asyncio.gather(
        totals_task, asyncio.gather(*dim_tasks), series_task
    )

    def totals(rows: list[tuple[Any, ...]]) -> tuple[float, float | None]:
        row = rows[0] if rows else (0, 0)
        return _num(row[0]), (_num(row[1]) if measure.is_rate else None)

    b0, h0 = totals(before_rows)
    b1, h1 = totals(after_rows)
    v0, v1 = _value(measure, b0, h0), _value(measure, b1, h1)
    change = (v1 - v0) if v0 is not None and v1 is not None else None
    pct = (change / v0) if change is not None and v0 else None
    direction = "flat" if not change else "up" if change > 0 else "down"

    findings = Findings(
        measure=measure.id,
        measure_name=measure.name,
        format=measure.format,
        is_rate=measure.is_rate,
        period=_period_dict(current),
        baseline=_period_dict(base),
        partial=partial,
        before=v0,
        after=v1,
        change=change,
        pct_change=pct,
        direction=direction,
        queries=queries,
    )
    if partial:
        findings.notes.append(
            f"{period.label} is still running: compared on its first {current.days} days "
            f"with the same {current.days} days of {baseline.label}."
        )
    if measure.is_rate:
        findings.notes.append(
            f"{measure.name} is a rate over {int(b0):,} → {int(b1):,}; each segment's "
            "part is split into its own rate moving (rate) and the mix moving (mix)."
        )

    # Anomaly: this value against the same window in the periods before
    by_idx = {int(r[0]): (_num(r[1]), _num(r[2]) if measure.is_rate else None) for r in series_rows}
    hist = [
        _value(measure, *by_idx.get(i, (0.0, 0.0 if measure.is_rate else None)))
        for i in range(len(past))
    ]
    # Periods before the data starts are not "usually zero": the business simply
    # was not recording yet. Leading empty periods are dropped.
    while hist and not hist[0]:
        hist.pop(0)
    if v1 is not None:
        anomaly: Anomaly = assess(v1, [h for h in hist if h is not None])
        findings.anomaly = anomaly.as_dict()

    # Drivers, per dimension, then across them
    all_drivers: list[Driver] = []
    for dim, (rows0, rows1) in zip(measure.dimensions, dim_results, strict=True):
        segs = _segments(measure, rows0, rows1)
        drivers = (
            decompose_rate(dim.id, segs, measure.min_volume)
            if measure.is_rate
            else decompose_total(dim.id, segs, measure.min_volume)
        )
        all_drivers += drivers
        summary = _dimension_summary(dim, drivers, change)
        conc = concentration(drivers, segs, measure.is_rate, change) if change else None
        summary["concentration"] = _round(conc)
        summary["dominant"] = _dominant(segs)
        findings.dimensions.append(summary)
        if summary["dominant"]:
            name, share = summary["dominant"]
            findings.notes.append(
                f"By {dim.label.lower()}, {share:.0%} of the volume is {name!r}, so it "
                "cannot say where the change came from."
            )

    if change:
        # The dimension where the change is most concentrated explains it best; its
        # drivers are the answer. Other dimensions stay in `dimensions`.
        best = max(
            [d for d in findings.dimensions if not d["dominant"]],
            key=lambda d: d["concentration"] if d["concentration"] is not None else -1,
            default=None,
        )
        if best and best["concentration"] is not None and best["concentration"] < EVEN_SPREAD:
            findings.notes.append(
                "The change is spread evenly across every dimension tried: no segment "
                "stands out from the overall movement."
            )
        if best and best["explained"] is not None:
            chosen = [d for d in all_drivers if d.dimension == best["dimension"]]
            findings.drivers, findings.offsets = _rank(chosen, change)
            findings.explained = best["explained"]
    return findings


def _segments(
    measure: Measure, rows0: list[tuple[Any, ...]], rows1: list[tuple[Any, ...]]
) -> list[Segment]:
    def index(rows: list[tuple[Any, ...]]) -> dict[str, tuple[float, float | None]]:
        return {
            str(r[0] if r[0] is not None else "Not set"): (
                _num(r[1]),
                _num(r[2]) if measure.is_rate else None,
            )
            for r in rows
        }

    before, after = index(rows0), index(rows1)
    empty = (0.0, 0.0 if measure.is_rate else None)
    return [
        Segment(
            name,
            before.get(name, empty)[0],
            after.get(name, empty)[0],
            before.get(name, empty)[1],
            after.get(name, empty)[1],
        )
        for name in sorted(set(before) | set(after))
    ]


# One segment holding this much of the volume in both periods is not a split
DOMINANT_SHARE = 0.9


def _dominant(segments: list[Segment]) -> tuple[str, float] | None:
    for key in ("base_before", "base_after"):
        total = sum(getattr(s, key) for s in segments)
        if not total:
            continue
        top = max(segments, key=lambda s: getattr(s, key))
        if getattr(top, key) / total < DOMINANT_SHARE:
            return None
    after = sum(s.base_after for s in segments) or sum(s.base_before for s in segments)
    if not after:
        return None
    top = max(segments, key=lambda s: s.base_after + s.base_before)
    return top.segment, (top.base_after or top.base_before) / after


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _dimension_summary(
    dim: Dimension, drivers: list[Driver], change: float | None
) -> dict[str, Any]:
    return {
        "dimension": dim.id,
        "label": dim.label,
        "explained": _round(explained(drivers, change)) if change else None,
        "segments": len({d.segment for d in drivers}),
    }


def _rank(
    drivers: list[Driver], change: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Same-direction drivers until they carry EXPLAIN_TARGET of the change; the
    largest moves the other way are offsets ("despite …")."""
    same = sorted(
        (d for d in drivers if d.contribution * change > 0),
        key=lambda d: abs(d.contribution),
        reverse=True,
    )
    picked: list[Driver] = []
    carried = 0.0
    for d in same:
        if len(picked) >= TOP_DRIVERS or carried >= EXPLAIN_TARGET:
            break
        if d.share is not None and abs(d.share) < MIN_SHARE:
            break
        picked.append(d)
        carried += d.share or 0
    against = sorted(
        (
            d
            for d in drivers
            if d.contribution * change < 0 and (d.share is None or abs(d.share) >= MIN_SHARE)
        ),
        key=lambda d: abs(d.contribution),
        reverse=True,
    )[:2]
    return [_rounded(d) for d in picked], [_rounded(d) for d in against]


def _rounded(d: Driver) -> dict[str, Any]:
    out = d.as_dict()
    for key in ("before", "after", "change", "contribution", "share"):
        if isinstance(out[key], float):
            out[key] = round(out[key], 4)
    return out

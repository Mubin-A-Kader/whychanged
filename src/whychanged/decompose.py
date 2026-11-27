"""Splitting a change between segments, the way an analyst would.

A total (leads, revenue) changes by the sum of its segments' changes, so each
segment's share of the change is exact. A rate (lead → MQL conversion) changes for
two reasons that call for different actions: the segments' own rates moved (rate
effect), or the mix moved towards segments that convert better or worse (mix
effect). With midpoint weights the two add up exactly to the change in the rate:

    r1 − r0 = Σ w̄ₛ (r1ₛ − r0ₛ)  +  Σ (w1ₛ − w0ₛ)(r̄ₛ − R̄)

where wₛ is the segment's share of the base, rₛ its rate, bars are midpoints and R̄
the overall midpoint rate (subtracting it changes nothing in total, since the
weights' changes sum to zero, but makes each mix term read correctly: more volume
in a segment below average pulls the rate down).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Segment:
    segment: str
    base_before: float
    base_after: float
    hits_before: float | None = None
    hits_after: float | None = None


@dataclass
class Driver:
    dimension: str
    segment: str
    before: float
    after: float
    change: float
    # This segment's part of the measure's total change, in the measure's units
    contribution: float
    # contribution / total change: 0.6 is "accounts for 60 % of the change"
    share: float | None
    effect: str  # "volume" for totals; "rate" or "mix" for rates
    base_before: float
    base_after: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def rate(hits: float | None, base: float) -> float | None:
    return (hits or 0) / base if base else None


def decompose_total(dimension: str, segments: list[Segment], min_volume: int) -> list[Driver]:
    total = sum(s.base_after - s.base_before for s in segments)
    out = []
    for s in segments:
        if max(s.base_before, s.base_after) < min_volume:
            continue
        change = s.base_after - s.base_before
        out.append(
            Driver(
                dimension,
                s.segment,
                s.base_before,
                s.base_after,
                change,
                change,
                change / total if total else None,
                "volume",
                s.base_before,
                s.base_after,
            )
        )
    return out


def decompose_rate(dimension: str, segments: list[Segment], min_volume: int) -> list[Driver]:
    n0 = sum(s.base_before for s in segments)
    n1 = sum(s.base_after for s in segments)
    if not n0 or not n1:
        return []
    k0 = sum(s.hits_before or 0 for s in segments)
    k1 = sum(s.hits_after or 0 for s in segments)
    overall = ((k0 / n0) + (k1 / n1)) / 2
    total_change = k1 / n1 - k0 / n0
    out = []
    for s in segments:
        w0, w1 = s.base_before / n0, s.base_after / n1
        r0, r1 = rate(s.hits_before, s.base_before), rate(s.hits_after, s.base_after)
        # A segment absent from one period has no rate there: its own rate stands in,
        # so it contributes through the mix alone
        r0 = r1 if r0 is None else r0
        r1 = r0 if r1 is None else r1
        if r0 is None or r1 is None:
            continue
        rate_effect = (w0 + w1) / 2 * (r1 - r0)
        mix_effect = (w1 - w0) * ((r0 + r1) / 2 - overall)
        if max(s.base_before, s.base_after) < min_volume:
            continue
        for effect, value in (("rate", rate_effect), ("mix", mix_effect)):
            out.append(
                Driver(
                    dimension,
                    s.segment,
                    r0,
                    r1,
                    r1 - r0,
                    value,
                    value / total_change if total_change else None,
                    effect,
                    s.base_before,
                    s.base_after,
                )
            )
    return out


def explained(drivers: list[Driver], total_change: float, top: int = 3) -> float | None:
    """How much of the change the `top` largest same-direction drivers carry."""
    if not total_change:
        return None
    same = sorted(
        (d for d in drivers if d.contribution * total_change > 0),
        key=lambda d: abs(d.contribution),
        reverse=True,
    )[:top]
    return min(1.0, sum(d.contribution for d in same) / total_change)


def concentration(
    drivers: list[Driver], segments: list[Segment], is_rate: bool, total_change: float
) -> float | None:
    """How unevenly the change falls on this dimension's segments, from 0 (every
    segment moved alike) upwards, as a share of the total change.

    The dimension that explains a change is the one where it is *concentrated*, not
    the one whose top segments add up to the most: a dimension with two segments
    always "carries 100 %" between them, even when both simply moved with the
    whole. So each driver is compared with what it would be if the change were
    spread uniformly — every segment growing by the same percentage (totals), or
    every segment's rate moving by the same points with no mix shift (rates) — and
    the gaps are summed.
    """
    if not total_change:
        return None
    if is_rate:
        n0 = sum(s.base_before for s in segments)
        n1 = sum(s.base_after for s in segments)
        if not n0 or not n1:
            return None
        weight = {s.segment: (s.base_before / n0 + s.base_after / n1) / 2 for s in segments}

        def expected(d: Driver) -> float:
            return weight.get(d.segment, 0.0) * total_change if d.effect == "rate" else 0.0

    else:
        n0 = sum(s.base_before for s in segments)
        growth = total_change / n0 if n0 else 0.0

        def expected(d: Driver) -> float:
            return d.base_before * growth

    gap = sum(abs(d.contribution - expected(d)) for d in drivers)
    return gap / abs(total_change)

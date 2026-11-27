"""Measures: what whychanged can count, and the ways it can split the count.

A measure is written once, by someone who knows the business, in a YAML file:

    calendar:
      fiscal_year_start_month: 1         # optional; 4 for an April–March year
    dimensions:
      channel:
        label: Channel
        expr: s.channel
      plan:
        label: Plan
        joins: LEFT JOIN plans p ON p.id = s.plan_id
        expr: COALESCE(p.name, 'Not set')
    measures:
      - id: signups
        name: Signups
        from: signups s
        where: [s.is_test = false]
        date: s.created_at
        count: s.id                      # COUNT(DISTINCT s.id)
        dimensions: [channel, plan]
      - id: trial_conversion
        name: Trial conversion
        from: signups s
        date: s.created_at
        count: s.id
        hit: s.converted                 # makes it a rate: hits / count
        dimensions: [channel, plan]

Every query is assembled from these pieces plus a date window and one dimension,
so each slice is the same measure, not a fresh attempt at it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ID = re.compile(r"^[a-z][a-z0-9_]*$")
FORMATS = ("count", "number", "currency", "percent")


class MeasureError(ValueError):
    """The measures file is wrong; the message says where."""


@dataclass(frozen=True)
class Dimension:
    id: str
    label: str
    expr: str
    joins: str = ""


@dataclass(frozen=True)
class Measure:
    id: str
    name: str
    from_sql: str
    date: str
    count: str | None = None
    total: str | None = None
    hit: str | None = None
    where: tuple[str, ...] = ()
    dimensions: tuple[Dimension, ...] = ()
    format: str = "count"
    # A segment smaller than this in both periods is never reported as a driver:
    # a fall from 3 to 0 is -100 % and means nothing.
    min_volume: int = 10
    description: str = ""
    # Which database the measure lives in, for callers that hold several
    source: str = "default"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_rate(self) -> bool:
        return self.hit is not None

    @property
    def base_sql(self) -> str:
        """The aggregate every query selects as `base`."""
        if self.total:
            return f"SUM({self.total})"
        return f"COUNT(DISTINCT {self.count})"

    @property
    def hits_sql(self) -> str | None:
        """For a rate, the numerator: the same count, only where `hit` holds."""
        if not self.hit:
            return None
        return f"COUNT(DISTINCT {self.count}) FILTER (WHERE {self.hit})"


def read_document(path: Path | str) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(raw, dict):
        raise MeasureError(f"{path}: expected a mapping")
    return raw


def fiscal_year_start(source: Path | str | dict[str, Any]) -> int:
    """The month the financial year starts (`calendar: fiscal_year_start_month`);
    January, the calendar year, when not set."""
    raw = source if isinstance(source, dict) else read_document(source)
    value = (raw.get("calendar") or {}).get("fiscal_year_start_month", 1)
    if not isinstance(value, int) or not 1 <= value <= 12:
        raise MeasureError("calendar.fiscal_year_start_month must be 1–12")
    return value


def load_measures(source: Path | str | dict[str, Any]) -> dict[str, Measure]:
    """Measures from a YAML file, or from an already-parsed document."""
    raw = source if isinstance(source, dict) else read_document(source)
    path = "measures" if isinstance(source, dict) else str(source)
    dims = {key: _dimension(key, spec) for key, spec in (raw.get("dimensions") or {}).items()}
    out: dict[str, Measure] = {}
    for i, spec in enumerate(raw.get("measures") or []):
        measure = _measure(spec, dims, f"measures[{i}]")
        if measure.id in out:
            raise MeasureError(f"{path}: measure {measure.id!r} is defined twice")
        out[measure.id] = measure
    return out


def _dimension(key: str, spec: Any) -> Dimension:
    if not _ID.match(str(key)) or not isinstance(spec, dict) or not spec.get("expr"):
        raise MeasureError(f"dimension {key!r} needs a snake_case id and an expr")
    return Dimension(
        id=str(key),
        label=str(spec.get("label") or key.replace("_", " ").capitalize()),
        expr=str(spec["expr"]).strip(),
        joins=str(spec.get("joins") or "").strip(),
    )


def _measure(spec: Any, dims: dict[str, Dimension], where: str) -> Measure:
    if not isinstance(spec, dict):
        raise MeasureError(f"{where} must be a mapping")
    for key in ("id", "name", "from", "date"):
        if not spec.get(key):
            raise MeasureError(f"{where} needs {key!r}")
    mid = str(spec["id"])
    if not _ID.match(mid):
        raise MeasureError(f"{where}: id {mid!r} must be snake_case")
    if bool(spec.get("count")) == bool(spec.get("total")):
        raise MeasureError(f"{mid}: give exactly one of count (distinct things) or total (a sum)")
    if spec.get("hit") and not spec.get("count"):
        raise MeasureError(f"{mid}: a rate (hit) needs count, the things it is a share of")
    fmt = str(spec.get("format") or ("percent" if spec.get("hit") else "count"))
    if fmt not in FORMATS:
        raise MeasureError(f"{mid}: format must be one of {FORMATS}")
    unknown = [d for d in spec.get("dimensions") or [] if d not in dims]
    if unknown:
        raise MeasureError(f"{mid}: unknown dimensions {unknown}")
    conditions = spec.get("where") or []
    if isinstance(conditions, str):
        conditions = [conditions]
    return Measure(
        id=mid,
        name=str(spec["name"]),
        source=str(spec.get("source") or "default"),
        from_sql=str(spec["from"]).strip(),
        date=str(spec["date"]).strip(),
        count=str(spec["count"]).strip() if spec.get("count") else None,
        total=str(spec["total"]).strip() if spec.get("total") else None,
        hit=str(spec["hit"]).strip() if spec.get("hit") else None,
        where=tuple(str(c).strip() for c in conditions),
        dimensions=tuple(dims[d] for d in spec.get("dimensions") or []),
        format=fmt,
        min_volume=int(spec.get("min_volume", 10)),
        description=str(spec.get("description") or "").strip(),
    )

"""Findings as plain sentences, with no model.

This is the command line's view and the fallback the narrator uses when a model's
wording does not check out: every number here is read straight from the findings.
"""

from __future__ import annotations

from typing import Any

from whychanged.investigate import Findings


def fmt(value: float | None, format: str, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    sign = "+" if signed and value > 0 else ""
    if format == "percent":
        return f"{sign}{value * 100:.1f}%"
    if format == "currency":
        return f"{sign}${value:,.0f}"
    if format == "count":
        return f"{sign}{value:,.0f}"
    return f"{sign}{value:,.2f}"


def _points(value: float) -> str:
    """A change in a rate, in percentage points."""
    return f"{value * 100:+.1f} pts"


def _change(f: Findings, value: float) -> str:
    return _points(value) if f.is_rate else fmt(value, f.format, signed=True)


def headline(f: Findings) -> str:
    if f.change is None:
        return f"{f.measure_name}: no value for {f.period['label']}."
    if not f.change:
        return f"{f.measure_name} held at {fmt(f.after, f.format)} in {f.period['label']}."
    pct = f" ({f.pct_change:+.0%})" if f.pct_change is not None and not f.is_rate else ""
    moved = "rose" if f.change > 0 else "fell"
    return (
        f"{f.measure_name} {moved} from {fmt(f.before, f.format)} to "
        f"{fmt(f.after, f.format)}{pct}, {f.period['label']} against {f.baseline['label']}."
    )


def _driver_line(f: Findings, d: dict[str, Any]) -> str:
    share = f" — {abs(d['share']):.0%} of the change" if d.get("share") is not None else ""
    if f.is_rate:
        what = (
            f"its own rate went {fmt(d['before'], 'percent')} → {fmt(d['after'], 'percent')}"
            if d["effect"] == "rate"
            else f"its volume went {d['base_before']:,.0f} → {d['base_after']:,.0f}"
        )
        return f"{d['segment']} ({d['effect']}): {what}, {_points(d['contribution'])}{share}"
    return (
        f"{d['segment']}: {fmt(d['before'], f.format)} → {fmt(d['after'], f.format)} "
        f"({fmt(d['change'], f.format, signed=True)}){share}"
    )


def format_findings(f: Findings, *, show_sql: bool = False) -> str:
    lines = [headline(f)]
    lines += [f"  note: {n}" for n in f.notes]
    a = f.anomaly
    if a and a["level"] != "unknown":
        usual = (
            f"usually {fmt(a['low'], f.format)}–{fmt(a['high'], f.format)}, "
            f"median {fmt(a['median'], f.format)}"
        )
        periods = len(a["history"])
        lines.append(
            f"Against the last {periods} periods: {a['level']} (z {a['z']:+.1f}; {usual})."
        )
    if f.drivers:
        dim = next((d for d in f.dimensions if d["dimension"] == f.drivers[0]["dimension"]), None)
        label = dim["label"] if dim else f.drivers[0]["dimension"]
        carried = f", which carries {f.explained:.0%} of it" if f.explained is not None else ""
        lines.append(f"Where it changed — by {label.lower()}{carried}:")
        lines += [f"  • {_driver_line(f, d)}" for d in f.drivers]
        if f.offsets:
            lines.append("  Moving the other way:")
            lines += [f"  • {_driver_line(f, d)}" for d in f.offsets]
    elif f.change:
        lines.append("No single segment of the dimensions tried accounts for the change.")
    others = [
        d for d in f.dimensions if not f.drivers or d["dimension"] != f.drivers[0]["dimension"]
    ]
    for d in others:
        if d.get("concentration") is not None and not d.get("dominant"):
            lines.append(
                f"  (by {d['label'].lower()}: concentration {d['concentration']:.2f}, "
                "closer to an even spread)"
            )
    if show_sql:
        lines.append("")
        lines += [f"-- {i + 1}\n{q}" for i, q in enumerate(f.queries)]
    return "\n".join(lines)

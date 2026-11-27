"""Periods and baselines, compared fairly.

A period is a half-open date window [start, end). The analyst compares a period
with its baseline — the one before it, or the same one a year earlier — and a
period still running is compared like for like: 1–15 September against 1–15
August, never half a month against a whole one.

Businesses count their year differently: a calendar year, April–March (India, the
UK tax year), July–June (Australia), October–September (the US government). The
measures file sets the month the financial year starts (`calendar:
fiscal_year_start_month`); quarters and "this FY" follow it. It defaults to
January, the calendar year, and every period carries it, so a baseline or a
history window never loses it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta

CALENDAR_YEAR = 1


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # exclusive
    label: str
    grain: str  # "month" | "quarter" | "fy"
    fy_start: int = CALENDAR_YEAR  # month the financial year starts, 1–12

    @property
    def days(self) -> int:
        return (self.end - self.start).days


def _check(fy_start: int) -> int:
    if not 1 <= fy_start <= 12:
        raise ValueError(f"fiscal_year_start_month must be 1–12, got {fy_start}")
    return fy_start


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def fy_label(start_year: int, fy_start: int = CALENDAR_YEAR) -> str:
    """'FY 2026' for a calendar year; 'FY 2026-27' when the year spans two."""
    if fy_start == CALENDAR_YEAR:
        return f"FY {start_year}"
    return f"FY {start_year}-{(start_year + 1) % 100:02d}"


def _fy_of(d: date, fy_start: int) -> int:
    """The calendar year a date's financial year starts in."""
    return d.year if d.month >= fy_start else d.year - 1


def _months_into_fy(d: date, fy_start: int) -> int:
    return (d.month - fy_start) % 12


def month(year: int, m: int, fy_start: int = CALENDAR_YEAR) -> Period:
    start = date(year, m, 1)
    return Period(start, _add_months(start, 1), start.strftime("%b %Y"), "month", _check(fy_start))


def fiscal_year(start_year: int, fy_start: int = CALENDAR_YEAR) -> Period:
    """The financial year that starts in `start_year` (FY 2026-27 starts in 2026)."""
    start = date(start_year, _check(fy_start), 1)
    return Period(start, _add_months(start, 12), fy_label(start_year, fy_start), "fy", fy_start)


def quarter(fy_start_year: int, q: int, fy_start: int = CALENDAR_YEAR) -> Period:
    """Q1 is the first three months of the financial year."""
    start = _add_months(date(fy_start_year, _check(fy_start), 1), 3 * (q - 1))
    return Period(
        start,
        _add_months(start, 3),
        f"Q{q} {fy_label(fy_start_year, fy_start)}",
        "quarter",
        fy_start,
    )


def parse_period(text: str, today: date | None = None, fy_start: int = CALENDAR_YEAR) -> Period:
    """'2026-09', 'this_month', 'last_month', 'this_quarter', 'q2-2026', 'fy2026',
    'this_fy', 'last_fy'. Years in 'q2-2026' and 'fy2026' are the year the
    financial year starts in."""
    today = today or date.today()
    fy_start = _check(fy_start)
    t = text.strip().lower()
    if t in ("this_month", "month"):
        return month(today.year, today.month, fy_start)
    if t == "last_month":
        prev = _add_months(date(today.year, today.month, 1), -1)
        return month(prev.year, prev.month, fy_start)
    if t in ("this_fy", "fy"):
        return fiscal_year(_fy_of(today, fy_start), fy_start)
    if t == "last_fy":
        return fiscal_year(_fy_of(today, fy_start) - 1, fy_start)
    if t in ("this_quarter", "quarter"):
        return quarter(_fy_of(today, fy_start), _months_into_fy(today, fy_start) // 3 + 1, fy_start)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})", t):
        return month(int(m[1]), int(m[2]), fy_start)
    if m := re.fullmatch(r"fy\s*(\d{4})", t):
        return fiscal_year(int(m[1]), fy_start)
    if m := re.fullmatch(r"q([1-4])[-\s]*(?:fy\s*)?(\d{4})", t):
        return quarter(int(m[2]), int(m[1]), fy_start)
    raise ValueError(
        f"period {text!r}: use YYYY-MM, this_month, last_month, this_quarter, q2-2026, "
        "fy2026, this_fy or last_fy"
    )


_SPAN = {"month": 1, "quarter": 3, "fy": 12}


def _label(start: date, grain: str, fy_start: int) -> str:
    if grain == "month":
        return start.strftime("%b %Y")
    fy = _fy_of(start, fy_start)
    if grain == "fy":
        return fy_label(fy, fy_start)
    return f"Q{_months_into_fy(start, fy_start) // 3 + 1} {fy_label(fy, fy_start)}"


def _shift(period: Period, months: int) -> Period:
    start = _add_months(period.start, months)
    return replace(
        period,
        start=start,
        end=_add_months(period.end, months),
        label=_label(start, period.grain, period.fy_start),
    )


def baseline_for(period: Period, versus: str = "previous") -> Period:
    """The period to compare with: 'previous' (the one before) or 'last_year'."""
    if versus == "last_year":
        return _shift(period, -12)
    if versus == "previous":
        return _shift(period, -_SPAN[period.grain])
    raise ValueError("versus must be 'previous' or 'last_year'")


def align_to_date(current: Period, baseline: Period, today: date) -> tuple[Period, Period, bool]:
    """A running period ends today; its baseline is cut to the same number of days.

    Returns (current, baseline, partial). Comparing 15 days of September with all
    of August reads as a collapse that is only the calendar.
    """
    if current.end <= today + timedelta(days=1):
        return current, baseline, False
    elapsed = (today + timedelta(days=1) - current.start).days
    if elapsed <= 0:
        raise ValueError(f"{current.label} has not started yet")
    cut_current = replace(current, end=today + timedelta(days=1), label=f"{current.label} to date")
    cut_base = replace(
        baseline,
        end=min(baseline.end, baseline.start + timedelta(days=elapsed)),
        label=f"{baseline.label}, same {elapsed} days",
    )
    return cut_current, cut_base, True


# How far back "usual" reaches: a year of months, two of quarters, five years
HISTORY = {"month": 12, "quarter": 8, "fy": 5}


def history(period: Period, count: int | None = None) -> list[Period]:
    """The whole periods before this one, oldest first, for the anomaly test."""
    count = count or HISTORY[period.grain]
    return [_shift(period, -_SPAN[period.grain] * k) for k in range(count, 0, -1)]

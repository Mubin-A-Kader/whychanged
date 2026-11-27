"""The analyst's SQL: one measure, one window, at most one dimension.

Only the pieces of a measure (written in the measures file) and dates the analyst
computed go into a statement, and every statement still passes the read-only check before
it reaches the database.
"""

from __future__ import annotations

from datetime import date

from whychanged.guard import assert_read_only
from whychanged.measures import Dimension, Measure
from whychanged.periods import Period


def _window(measure: Measure, start: date, end: date) -> str:
    # Dates are `date` objects the analyst made, never text from a question
    return (
        f"{measure.date} >= '{start.isoformat()}'::date "
        f"AND {measure.date} < '{end.isoformat()}'::date"
    )


def _select(measure: Measure) -> str:
    cols = [f"{measure.base_sql} AS base"]
    if measure.hits_sql:
        cols.append(f"{measure.hits_sql} AS hits")
    return ", ".join(cols)


def slice_sql(measure: Measure, period: Period, dimension: Dimension | None = None) -> str:
    """The measure in one window, split by one dimension (or not split)."""
    conditions = " AND ".join(
        [*(f"({c})" for c in measure.where), _window(measure, period.start, period.end)]
    )
    if dimension is None:
        # Curated fragments and computed dates only; checked read-only below
        sql = f"SELECT {_select(measure)} FROM {measure.from_sql} WHERE {conditions}"  # noqa: S608
    else:
        sql = (
            f"SELECT ({dimension.expr})::text AS segment, {_select(measure)} "  # noqa: S608
            f"FROM {measure.from_sql} {dimension.joins} WHERE {conditions} GROUP BY 1"
        )
    assert_read_only(sql)
    return sql


def series_sql(measure: Measure, windows: list[Period]) -> str:
    """The measure in each of several windows, in one statement (the anomaly test)."""
    values = ", ".join(
        f"({i}, '{w.start.isoformat()}'::date, '{w.end.isoformat()}'::date)"
        for i, w in enumerate(windows)
    )
    conditions = " AND ".join(f"({c})" for c in measure.where) or "TRUE"
    sql = (
        f"SELECT w.i AS idx, {_select(measure)} "  # noqa: S608
        f"FROM {measure.from_sql} "
        f"JOIN (VALUES {values}) AS w(i, lo, hi) "
        f"ON {measure.date} >= w.lo AND {measure.date} < w.hi "
        f"WHERE {conditions} GROUP BY w.i ORDER BY w.i"
    )
    assert_read_only(sql)
    return sql

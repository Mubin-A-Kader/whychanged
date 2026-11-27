"""Periods, decomposition, anomaly, the SQL guard and the measures file."""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path

import pytest

from whychanged.anomaly import assess
from whychanged.decompose import Segment, concentration, decompose_rate, decompose_total
from whychanged.guard import UnsafeSQLError, assert_read_only
from whychanged.measures import MeasureError, fiscal_year_start, load_measures
from whychanged.periods import align_to_date, baseline_for, fiscal_year, month, parse_period
from whychanged.queries import series_sql, slice_sql

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "saas" / "measures.yaml"

# ── Periods ──────────────────────────────────────────────────────


def test_periods_parse_and_compare_like_for_like() -> None:
    today = date(2026, 9, 15)
    sep = parse_period("this_month", today)
    assert (sep.start, sep.end, sep.label) == (date(2026, 9, 1), date(2026, 10, 1), "Sep 2026")
    assert baseline_for(sep).label == "Aug 2026"
    assert baseline_for(sep, "last_year").start == date(2025, 9, 1)
    assert parse_period("fy2026", fy_start=4).label == "FY 2026-27"
    assert parse_period("q2-2026", fy_start=4).start == date(2026, 7, 1)
    assert parse_period("last_month", today).label == "Aug 2026"

    current, base, partial = align_to_date(sep, baseline_for(sep), today)
    assert partial
    assert (current.start, current.end) == (date(2026, 9, 1), date(2026, 9, 16))
    assert (base.start, base.end) == (date(2026, 8, 1), date(2026, 8, 16))


def test_a_finished_period_is_compared_whole() -> None:
    aug = month(2026, 8)
    current, base, partial = align_to_date(aug, baseline_for(aug), date(2026, 9, 15))
    assert not partial and current == aug and base.end == date(2026, 8, 1)
    assert fiscal_year(2025, fy_start=4).end == date(2026, 4, 1)


def test_the_financial_year_follows_the_business() -> None:
    today = date(2026, 9, 15)
    assert parse_period("this_fy", today).label == "FY 2026"
    assert parse_period("this_quarter", today).start == date(2026, 7, 1)
    # July–June (Australia): September is in Q1 of FY 2026-27
    q = parse_period("this_quarter", today, fy_start=7)
    assert (q.start, q.label) == (date(2026, 7, 1), "Q1 FY 2026-27")
    base = baseline_for(parse_period("this_fy", today, fy_start=4), "previous")
    assert (base.start, base.label, base.fy_start) == (date(2025, 4, 1), "FY 2025-26", 4)
    with pytest.raises(ValueError):
        parse_period("this_fy", today, fy_start=13)


# ── Decomposition ────────────────────────────────────────────────


def test_a_totals_change_splits_exactly_between_segments() -> None:
    segs = [Segment("social", 400, 150), Segment("organic", 200, 230), Segment("ads", 100, 90)]
    drivers = decompose_total("channel", segs, min_volume=10)
    assert sum(d.contribution for d in drivers) == -230
    top = min(drivers, key=lambda d: d.contribution)
    assert top.segment == "social" and round(top.share, 3) == round(250 / 230, 3)


def test_rate_and_mix_effects_add_up_to_the_change_in_the_rate() -> None:
    # organic converts well and shrank; social converts badly and grew
    segs = [
        Segment("organic", 300, 150, 90, 45),
        Segment("social", 200, 400, 20, 36),
        Segment("ads", 100, 100, 30, 25),
    ]
    drivers = decompose_rate("channel", segs, min_volume=10)
    r0 = (90 + 20 + 30) / 600
    r1 = (45 + 36 + 25) / 650
    assert abs(sum(d.contribution for d in drivers) - (r1 - r0)) < 1e-12
    mix = {d.segment: d.contribution for d in drivers if d.effect == "mix"}
    # More of a weak segment pulls the rate down; less of a strong one does too
    assert mix["social"] < 0 and mix["organic"] < 0


@pytest.mark.parametrize("seed", range(200))
def test_contributions_always_add_up_exactly(seed: int) -> None:
    """The core promise, on random data: no unexplained remainder, ever."""
    rng = random.Random(seed)
    segs = []
    for i in range(rng.randint(1, 8)):
        b0, b1 = rng.randint(0, 500), rng.randint(0, 500)
        segs.append(Segment(f"s{i}", b0, b1, rng.randint(0, b0), rng.randint(0, b1)))
    n0, n1 = sum(s.base_before for s in segs), sum(s.base_after for s in segs)

    totals = decompose_total("d", segs, min_volume=0)
    assert abs(sum(d.contribution for d in totals) - (n1 - n0)) < 1e-9

    if n0 and n1:
        r0 = sum(s.hits_before for s in segs) / n0
        r1 = sum(s.hits_after for s in segs) / n1
        rates = decompose_rate("d", segs, min_volume=0)
        # A segment present in only one period contributes through the mix alone, so
        # the sum is exact whenever every segment has a rate in both periods
        if all(s.base_before and s.base_after for s in segs):
            assert abs(sum(d.contribution for d in rates) - (r1 - r0)) < 1e-9


def test_tiny_segments_are_never_drivers() -> None:
    drivers = decompose_total("s", [Segment("Big", 500, 300), Segment("Tiny", 3, 0)], min_volume=10)
    assert [d.segment for d in drivers] == ["Big"]


def test_an_even_move_is_not_concentrated() -> None:
    # Two segments that both fell 10 %: they "carry 100 %" between them, but nothing
    # about either stands out from the whole
    even = [Segment("free", 700, 630), Segment("pro", 300, 270)]
    lumpy = [Segment("free", 700, 600), Segment("pro", 300, 300)]
    c_even = concentration(decompose_total("plan", even, 0), even, False, -100)
    c_lumpy = concentration(decompose_total("plan", lumpy, 0), lumpy, False, -100)
    assert c_even is not None and c_even < 1e-9
    assert c_lumpy is not None and c_lumpy > 0.5


# ── Anomaly ──────────────────────────────────────────────────────


def test_anomaly_is_robust_to_one_past_spike() -> None:
    history = [100, 104, 98, 101, 99, 350, 102, 97]  # one campaign month
    assert assess(40, history).level == "unusual"
    assert assess(103, history).level == "normal"
    assert assess(40, [100, 90]).level == "unknown"


# ── SQL ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM signups",
        "SELECT 1; DROP TABLE signups",
        "SELECT * INTO copy FROM signups",
        "SELECT * FROM signups FOR UPDATE",
        "WITH x AS (DELETE FROM signups RETURNING *) SELECT * FROM x",
        "",
    ],
)
def test_only_a_single_read_only_select_passes(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        assert_read_only(sql)


def test_queries_are_read_only_and_join_only_what_they_slice() -> None:
    measures = load_measures(
        {
            "dimensions": {
                "plan": {"expr": "p.name", "joins": "LEFT JOIN plans p ON p.id = s.plan_id"}
            },
            "measures": [
                {
                    "id": "signups",
                    "name": "Signups",
                    "from": "signups s",
                    "date": "s.created_at",
                    "count": "s.id",
                    "dimensions": ["plan"],
                }
            ],
        }
    )
    m = measures["signups"]
    sep = month(2026, 9)
    total, split = slice_sql(m, sep), slice_sql(m, sep, m.dimensions[0])
    assert "plans" not in total and "plans" in split
    assert "GROUP BY w.i" in series_sql(m, [sep, baseline_for(sep)])


# ── Measures file ────────────────────────────────────────────────


def test_the_example_measures_file_loads() -> None:
    measures = load_measures(EXAMPLE)
    assert {"signups", "trial_conversion"} <= set(measures)
    assert measures["trial_conversion"].is_rate
    assert fiscal_year_start(EXAMPLE) == 1


def test_a_measure_must_say_what_it_counts() -> None:
    base = {"id": "x", "name": "X", "from": "t", "date": "t.d"}
    with pytest.raises(MeasureError, match="exactly one of count"):
        load_measures({"measures": [base]})
    with pytest.raises(MeasureError, match="needs count"):
        load_measures({"measures": [{**base, "total": "t.amount", "hit": "t.ok"}]})
    with pytest.raises(MeasureError, match="unknown dimensions"):
        load_measures({"measures": [{**base, "count": "t.id", "dimensions": ["nope"]}]})

"""End to end on the example dataset, where the causes are known.

`examples/saas/make_data.py` plants two causes in October 2026: paid_search's own
conversion falls (a rate effect) and social signups double at a low conversion (a
mix effect). whychanged must find both, from the data alone, and keep them apart.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from whychanged import (
    baseline_for,
    duckdb_executor,
    format_findings,
    investigate,
    load_measures,
)
from whychanged.narrate import Narration, check, narrate
from whychanged.periods import month

ROOT = Path(__file__).resolve().parents[1] / "examples" / "saas"
TODAY = date(2026, 11, 5)


@pytest.fixture(scope="module")
def measures():  # type: ignore[no-untyped-def]
    return load_measures(ROOT / "measures.yaml")


async def _findings(measure):  # type: ignore[no-untyped-def]
    oct_ = month(2026, 10)
    db = duckdb_executor(csv={"signups": ROOT / "signups.csv"})
    return await investigate(measure, oct_, baseline_for(oct_), db, today=TODAY)


async def test_a_rate_drop_is_split_into_its_two_planted_causes(measures) -> None:  # type: ignore[no-untyped-def]
    f = await _findings(measures["trial_conversion"])
    assert f.direction == "down"
    assert f.anomaly and f.anomaly["level"] in ("notable", "unusual")
    found = {(d["dimension"], d["segment"], d["effect"]) for d in f.drivers}
    assert ("channel", "paid_search", "rate") in found
    assert ("channel", "social", "mix") in found
    # And the two together carry most of the drop
    planted = [
        d
        for d in f.drivers
        if (d["segment"], d["effect"]) in {("paid_search", "rate"), ("social", "mix")}
    ]
    assert sum(d["share"] for d in planted) > 0.5


async def test_a_dimension_with_few_segments_does_not_win_by_default(measures) -> None:  # type: ignore[no-untyped-def]
    f = await _findings(measures["trial_conversion"])
    by = {d["dimension"]: d for d in f.dimensions}
    # plan has two segments, so its top segments "carry 100 %"; it still is not the answer
    assert by["plan"]["explained"] == 1.0
    assert by["channel"]["concentration"] > 2 * by["plan"]["concentration"]


async def test_a_volume_rise_names_the_campaign(measures) -> None:  # type: ignore[no-untyped-def]
    f = await _findings(measures["signups"])
    assert f.direction == "up"
    assert (f.drivers[0]["dimension"], f.drivers[0]["segment"]) == ("channel", "social")


async def test_findings_hold_aggregates_only(measures) -> None:  # type: ignore[no-untyped-def]
    f = await _findings(measures["trial_conversion"])
    facts = {k: v for k, v in f.as_dict().items() if k != "queries"}
    flat = json.dumps(facts, default=str)
    assert "SELECT" not in flat and "created_at" not in flat
    assert "Trial conversion fell" in format_findings(f)


# ── Narration ────────────────────────────────────────────────────


async def test_a_narration_with_the_findings_numbers_passes(measures) -> None:  # type: ignore[no-untyped-def]
    f = await _findings(measures["signups"])
    top = f.drivers[0]
    good = Narration(
        headline=(
            f"Trial signups rose from {f.before:,.0f} to {f.after:,.0f}; social accounts for "
            f"{top['share'] * 100:.0f}% of the rise."
        ),
        what_happened=f"Signups rose by {f.change:,.0f}.",
        why=f"Social went from {top['before']:,.0f} to {top['after']:,.0f}.",
        impact="More trials to convert next month.",
        next_steps=["Check the social campaign's lead quality."],
    )
    assert check(good, f).checked


async def test_an_invented_number_falls_back_to_the_findings_own_words(measures) -> None:  # type: ignore[no-untyped-def]
    f = await _findings(measures["signups"])
    invented = Narration("Signups rose, mostly because ad spend went up 45%.", "", "", "")
    out = check(invented, f)
    assert not out.checked and out.unsupported == ["45"]
    assert out.headline.startswith("Trial signups rose from")


async def test_a_cause_is_never_claimed(measures) -> None:  # type: ignore[no-untyped-def]
    f = await _findings(measures["signups"])
    out = check(Narration("Signups rose, mainly due to social.", "", "", ""), f)
    assert out.checked and out.headline == "Signups rose, mainly coming from social."
    claimed = check(Narration("Signups rose.", "", "Social caused the rise.", ""), f)
    assert not claimed.checked


async def test_a_failing_model_still_answers(measures) -> None:  # type: ignore[no-untyped-def]
    f = await _findings(measures["signups"])

    async def broken(system: str, user: str) -> str:
        raise ConnectionError("no model")

    async def leaky(system: str, user: str) -> str:
        assert "SELECT" not in user  # the model never sees SQL
        return json.dumps({"headline": "Signups jumped 99%.", "why": "", "impact": ""})

    assert not (await narrate(f, broken)).checked
    out = await narrate(f, leaky)
    assert not out.checked and out.unsupported == ["99"]

"""Findings → words an analyst would say, with every number checked.

The narrator sees the findings object without its queries: totals, drivers with
their numbers, anomaly, notes — aggregates and segment names, never rows. It writes
a short spoken headline and the what / why / impact / next steps. Every number in
what it writes must be one the findings hold (in any of the ways it may be written:
1,090 or 1090, 0.62 or 62 %); one that is not means the whole narration is replaced
by the template sentences from `report`, which read straight from the findings.

The model is any async function `(system, user) -> str` that returns JSON text, so
whychanged is not tied to a provider. `openai_compatible()` builds one for OpenAI or
any OpenAI-compatible endpoint (Ollama, vLLM, OpenRouter, LiteLLM, ...).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from whychanged.investigate import Findings
from whychanged.report import _driver_line, headline

log = logging.getLogger("whychanged")

# (system prompt, user message) -> the model's reply, expected to be JSON text
Complete = Callable[[str, str], Awaitable[str]]

NARRATE_SYSTEM = """You are a business analyst explaining a change to a manager.
You are given FINDINGS computed from the data. Explain them; do not add to them.

Write:
- headline: one or two short sentences to be read aloud: what changed and the main
  reason. No markdown.
- what_happened: one or two sentences with the before, after and change.
- why: two or three sentences on where the change came from, naming the segments
  and their share of the change. Drivers with effect "rate" moved their own rate;
  "mix" means the volume shifted between segments. Mention anything moving the
  other way. If a note says a split is mostly "Not set", say the data cannot tell.
- impact: one sentence on what the change means for the business, from the numbers.
- next_steps: two or three short, concrete suggestions of what to check or do,
  following from the drivers. They are suggestions, not facts.

Rules:
- Use only numbers that appear in FINDINGS (you may round and format them, and write
  a share like 0.62 as 62%). Never compute a new number.
- Say "accounts for", "came from", "coincided with" — never "caused", "due to" or
  "because of": the findings show where the change is, not what made it happen.
- If the anomaly level is "normal", do not call the change unusual.

Reply with JSON only: {"headline", "what_happened", "why", "impact", "next_steps": []}"""


@dataclass
class Narration:
    headline: str
    what_happened: str
    why: str
    impact: str
    next_steps: list[str] = field(default_factory=list)
    checked: bool = True  # False: the template stood in for the model
    unsupported: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def template(f: Findings) -> Narration:
    """No model: the report's own sentences, read straight from the findings."""
    lines = [_driver_line(f, d) for d in f.drivers[:3]]
    why = "; ".join(lines) if lines else "No single segment accounts for the change."
    if f.offsets:
        why += f". Moving the other way: {_driver_line(f, f.offsets[0])}"
    return Narration(
        headline=headline(f),
        what_happened=headline(f),
        why=why,
        impact="",
        next_steps=[],
        checked=False,
    )


# ── The number check ──────────────────────────────────────────────

_NUMBER = re.compile(r"(?<![\w.])[-+−]?\d[\d,]*(?:\.\d+)?")


def _numbers_in(findings: Findings) -> list[float]:
    """Every number the findings hold, in each form a writer may use."""
    out: list[float] = []

    def add(value: Any, *, ratio: bool = False) -> None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return
        out.extend([float(value), abs(float(value))])
        if ratio:
            out.extend([float(value) * 100, abs(float(value)) * 100])

    rate = findings.is_rate
    for v in (findings.before, findings.after, findings.change):
        add(v, ratio=rate)
    add(findings.pct_change, ratio=True)
    add(findings.explained, ratio=True)
    for d in findings.drivers + findings.offsets:
        for key in ("before", "after", "change", "contribution"):
            add(d.get(key), ratio=rate)
        add(d.get("share"), ratio=True)
        add(d.get("base_before"))
        add(d.get("base_after"))
        add((d.get("base_after") or 0) - (d.get("base_before") or 0))
    a = findings.anomaly or {}
    for key in ("value", "median", "low", "high", "z"):
        add(a.get(key), ratio=rate and key != "z")
    add(len(a.get("history") or []))
    for dim in findings.dimensions:
        add(dim.get("explained"), ratio=True)
        add(dim.get("concentration"), ratio=True)
    # Numbers inside labels and notes: years, day counts, "FY 2026-27"
    for text_ in [findings.period["label"], findings.baseline["label"], *findings.notes]:
        out.extend(float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text_))
    return out


def unsupported_numbers(text_: str, findings: Findings) -> list[str]:
    known = _numbers_in(findings)
    bad = []
    for raw in _NUMBER.findall(text_):
        n = abs(float(raw.replace(",", "").replace("−", "-")))
        if n <= 10 and float(n).is_integer():
            continue  # counting words: "two sources", "3 steps", "Q1"
        tolerance = max(0.51, n * 0.006)  # rounding: 62.4 → 62, 1,090 → 1.1k is not allowed
        if not any(abs(n - k) <= tolerance for k in known):
            bad.append(raw)
    return bad


# ── The wording check ─────────────────────────────────────────────

# Findings show where a change is, not what made it happen. Small models write
# "due to" whatever the prompt says, so causal phrases are rewritten as where the
# change came from; a claim of cause that cannot be rewritten is not accepted.
_CAUSAL_REWRITES = (
    (
        re.compile(r"\b(due to|because of|caused by|driven by|as a result of|owing to)\b", re.I),
        "coming from",
    ),
)
_CAUSAL_LEFT = re.compile(r"\b(caused|causing|causes|led to|resulted in)\b", re.I)


def soften(text_: str) -> str:
    for pattern, replacement in _CAUSAL_REWRITES:
        text_ = pattern.sub(replacement, text_)
    return text_


def check(narration: Narration, findings: Findings) -> Narration:
    narration = Narration(
        headline=soften(narration.headline),
        what_happened=soften(narration.what_happened),
        why=soften(narration.why),
        impact=narration.impact,  # impact is about consequences: "led to fewer demos" is fine there
        next_steps=narration.next_steps,
    )
    if _CAUSAL_LEFT.search(" ".join([narration.headline, narration.what_happened, narration.why])):
        return template(findings)
    text_ = " ".join(
        [
            narration.headline,
            narration.what_happened,
            narration.why,
            narration.impact,
            *narration.next_steps,
        ]
    )
    bad = unsupported_numbers(text_, findings)
    if bad:
        fallback = template(findings)
        fallback.unsupported = bad
        return fallback
    return narration


def openai_compatible(
    model: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> Complete:
    """A `Complete` for OpenAI or any OpenAI-compatible endpoint.

    Defaults come from WHYCHANGED_MODEL, OPENAI_BASE_URL and OPENAI_API_KEY.
    Needs the `llm` extra: pip install "whychanged[llm]".
    """
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            'narration needs the openai package: pip install "whychanged[llm]"'
        ) from exc

    client = AsyncOpenAI(base_url=base_url or os.environ.get("OPENAI_BASE_URL"), api_key=api_key)
    name = model or os.environ.get("WHYCHANGED_MODEL", "gpt-4o-mini")

    async def complete(system: str, user: str) -> str:
        reply = await client.chat.completions.create(
            model=name,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=600,
        )
        return reply.choices[0].message.content or "{}"

    return complete


async def narrate(findings: Findings, complete: Complete) -> Narration:
    """Put the findings into words with a model, then check every number it wrote.

    Any failure (no model, bad JSON, an invented number, a claim of cause) returns
    the template sentences instead: the findings still answer the question.
    """
    if findings.change is None:
        return template(findings)
    facts = {k: v for k, v in findings.as_dict().items() if k != "queries"}
    try:
        raw = json.loads(
            await complete(NARRATE_SYSTEM, json.dumps({"FINDINGS": facts}, default=str)) or "{}"
        )
        narration = Narration(
            headline=str(raw.get("headline") or "").strip(),
            what_happened=str(raw.get("what_happened") or "").strip(),
            why=str(raw.get("why") or "").strip(),
            impact=str(raw.get("impact") or "").strip(),
            next_steps=[str(x).strip() for x in raw.get("next_steps") or [] if str(x).strip()][:4],
        )
    except Exception as exc:  # noqa: BLE001 - the findings still answer the question
        log.warning("narration failed: %s", exc)
        return template(findings)
    if not narration.headline:
        return template(findings)
    return check(narration, findings)

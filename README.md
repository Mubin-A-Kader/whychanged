# whychanged

**Why did this metric change?** Exact drivers, from your own database.

Point it at a measure (signups, revenue, a conversion rate), and whychanged
compares two periods, splits the change across every dimension, and tells you
where it came from — in numbers that add up to the change exactly.

```
$ whychanged examples/saas/measures.yaml trial_conversion \
    --csv signups=examples/saas/signups.csv --period 2026-10

Trial conversion fell from 19.2% to 14.1%, Oct 2026 against Sep 2026.
Against the last 12 periods: unusual (z -3.3; usually 15.5%–19.2%, median 17.8%).
Where it changed — by channel, which carries 85% of it:
  • paid_search (rate): its own rate went 15.6% → 8.2%, -2.2 pts — 43% of the change
  • social (mix): its volume went 221 → 430, -1.2 pts — 25% of the change
  • organic (rate): its own rate went 24.7% → 22.5%, -0.9 pts — 17% of the change
  Moving the other way:
  • social (rate): its own rate went 3.6% → 4.7%, +0.2 pts — 5% of the change
  (by country: concentration 0.21, closer to an even spread)
  (by plan: concentration 0.20, closer to an even spread)
```

The example data has two causes planted in October: paid_search's own conversion
dropped, and a social campaign doubled low-converting signups. whychanged finds
both, and tells them apart.

## Why it's different

- **Rate vs mix.** A conversion rate can fall while every segment holds steady,
  just because volume moved to weaker segments (Simpson's paradox). whychanged
  splits each segment's part into its *own rate moving* and *the mix moving*.
- **Exact, not approximate.** Contributions sum to the total change with no
  leftover. Midpoint weights make it exact:

  ```
  r1 − r0 = Σ w̄ₛ (r1ₛ − r0ₛ)  +  Σ (w1ₛ − w0ₛ)(r̄ₛ − R̄)
            └── rate effect ──┘    └──── mix effect ────┘
  ```

- **Picks the right dimension.** It doesn't pick the dimension whose top segments
  "carry the most" (a dimension with two segments always carries 100%). It picks
  the one where the change is most *concentrated*: furthest from every segment
  moving alike.
- **Noise-aware.** The current value is tested against its own history with a
  robust z-score (median/MAD), so one past spike doesn't hide a real move.
- **Fair comparisons.** A month still in progress is compared with the same
  number of days in the previous period. Fiscal years (April–March, July–June, ...)
  are supported.
- **LLM optional, and it can't make up numbers.** `--narrate` asks a model to write
  it up; every number it writes is checked against the findings, and phrases that
  claim a cause ("caused by") are rewritten or rejected. If the check fails, you
  get the plain findings instead.
- **Your rows stay put.** Only aggregates leave the database. Every query is
  parsed and checked to be a single read-only SELECT.

## Install

```bash
pip install whychanged              # DuckDB, CSV and Parquet
pip install "whychanged[postgres]"  # + Postgres
pip install "whychanged[llm]"       # + --narrate (OpenAI or any compatible endpoint)
```

## Define your measures

```yaml
dimensions:
  channel: { label: Channel, expr: s.channel }
  plan:
    label: Plan
    joins: LEFT JOIN plans p ON p.id = s.plan_id
    expr: COALESCE(p.name, 'Not set')

measures:
  - id: signups
    name: Signups
    from: signups s
    date: s.created_at
    count: s.id            # COUNT(DISTINCT s.id); or `total: s.amount` for a SUM
    dimensions: [channel, plan]

  - id: trial_conversion
    name: Trial conversion
    from: signups s
    date: s.created_at
    count: s.id
    hit: s.converted       # makes it a rate: hits / count
    dimensions: [channel, plan]
```

## Run it

```bash
whychanged measures.yaml --list
whychanged measures.yaml signups --db analytics.duckdb --period last_month
whychanged measures.yaml signups --db postgresql://ro@host/db --period q3-2026 --vs last_year
whychanged measures.yaml signups --csv signups=export.csv --json        # for agents
OPENAI_API_KEY=... whychanged measures.yaml signups --db x.duckdb --narrate
```

Periods: `YYYY-MM`, `this_month`, `last_month`, `this_quarter`, `q2-2026`,
`fy2026`, `this_fy`, `last_fy`. Baselines: `--vs previous` or `--vs last_year`.

## From Python

```python
import asyncio
from whychanged import baseline_for, duckdb_executor, investigate, load_measures, parse_period

measures = load_measures("measures.yaml")
period = parse_period("2026-10")
findings = asyncio.run(
    investigate(measures["trial_conversion"], period, baseline_for(period),
                duckdb_executor(csv={"signups": "signups.csv"}))
)
print(findings.drivers)
```

An executor is any async `sql -> (columns, rows)` function, so any database
works.

## Development

```bash
uv sync
python examples/saas/make_data.py   # regenerate the example data
uv run pytest
```

## Roadmap

- Confidence intervals, so small noisy segments don't rank as drivers
- Drill-down across combinations of dimensions (channel × country)
- Seasonality-aware baselines
- dbt / Cube metric adapters
- MCP server, so agents can ask "why did X change?"
- Funnels: assigning a change to each step

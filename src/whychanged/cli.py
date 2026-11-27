"""whychanged on the command line.

whychanged measures.yaml trial_conversion --db data.duckdb --period 2026-10
whychanged measures.yaml signups --csv signups=signups.csv --vs last_year --json
whychanged measures.yaml --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from typing import Any

from whychanged.executors import Executor, duckdb_executor, postgres_executor
from whychanged.investigate import investigate
from whychanged.measures import MeasureError, fiscal_year_start, load_measures, read_document
from whychanged.narrate import narrate, openai_compatible
from whychanged.periods import baseline_for, parse_period
from whychanged.report import format_findings


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="whychanged",
        description="Why did this metric change? Exact drivers, from your own database.",
    )
    p.add_argument("measures", help="the measures YAML file")
    p.add_argument("measure", nargs="?", help="id of the measure to explain")
    p.add_argument("--list", action="store_true", help="list the measures and exit")
    src = p.add_argument_group("data (pick one)")
    src.add_argument("--db", help="a DuckDB file or a postgresql:// URL")
    src.add_argument(
        "--csv",
        action="append",
        default=[],
        metavar="TABLE=FILE",
        help="load a CSV or Parquet file as a table (repeatable)",
    )
    p.add_argument(
        "--period",
        default="last_month",
        help="YYYY-MM, this_month, last_month, this_quarter, q2-2026, fy2026, this_fy, last_fy",
    )
    p.add_argument("--vs", default="previous", choices=["previous", "last_year"])
    p.add_argument("--today", type=date.fromisoformat, help="pretend today is YYYY-MM-DD")
    p.add_argument("--json", action="store_true", help="print findings as JSON")
    p.add_argument("--sql", action="store_true", help="also print the SQL that ran")
    p.add_argument(
        "--narrate",
        action="store_true",
        help="have a model write it up (number-checked); needs OPENAI_API_KEY",
    )
    return p


def _executor(args: argparse.Namespace) -> Executor:
    if args.db and args.db.startswith(("postgres://", "postgresql://")):
        return postgres_executor(args.db)
    csv = {}
    for item in args.csv:
        name, sep, path = item.partition("=")
        if not sep:
            raise SystemExit(f"--csv expects TABLE=FILE, got {item!r}")
        csv[name] = path
    if not args.db and not csv:
        raise SystemExit("give --db or at least one --csv")
    return duckdb_executor(args.db or ":memory:", csv=csv)


async def _run(args: argparse.Namespace) -> int:
    doc: dict[str, Any] = read_document(args.measures)
    measures = load_measures(doc)
    if args.list or not args.measure:
        for m in measures.values():
            kind = "rate" if m.is_rate else "total"
            dims = ", ".join(d.id for d in m.dimensions) or "-"
            print(f"{m.id:<28} {kind:<6} by {dims}")
        return 0
    if args.measure not in measures:
        print(f"unknown measure {args.measure!r}; try --list", file=sys.stderr)
        return 2

    today = args.today or date.today()
    period = parse_period(args.period, today, fiscal_year_start(doc))
    findings = await investigate(
        measures[args.measure], period, baseline_for(period, args.vs), _executor(args), today=today
    )

    if args.json:
        print(json.dumps(findings.as_dict(), indent=2, default=str))
    else:
        print(format_findings(findings, show_sql=args.sql))
    if args.narrate:
        story = await narrate(findings, openai_compatible())
        if args.json:
            print(json.dumps(story.as_dict(), indent=2))
        else:
            print()
            print(story.headline)
            for line in (story.why, story.impact, *story.next_steps):
                if line:
                    print(f"  {line}")
            if not story.checked:
                print("  (model output failed the number check; showing plain findings)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (MeasureError, ValueError) as exc:
        print(f"whychanged: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate signups.csv: 14 months of SaaS trial signups with two planted causes.

In October 2026:
  1. paid_search's own conversion falls from ~14 % to ~8 %   (a rate effect)
  2. a social campaign doubles social signups, which convert at ~5 %   (a mix effect)

Everything else (country, plan, other channels) stays as it was, apart from noise.
whychanged should find both causes, and tell them apart, from the data alone.

    python examples/saas/make_data.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).with_name("signups.csv")
START, END = date(2025, 9, 1), date(2026, 11, 1)

# channel: (signups per month, conversion rate)
CHANNELS = {
    "organic": (600, 0.22),
    "paid_search": (400, 0.14),
    "social": (200, 0.05),
    "referral": (100, 0.30),
}
COUNTRIES = [("US", 0.45), ("IN", 0.25), ("UK", 0.15), ("DE", 0.15)]
PLANS = [("free", 0.7), ("pro", 0.3)]


def pick(rng: random.Random, options: list[tuple[str, float]]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]


def main() -> None:
    rng = random.Random(7)
    rows = []
    day = START
    while day < END:
        planted = day >= date(2026, 10, 1)
        for channel, (monthly, conv) in CHANNELS.items():
            if planted and channel == "paid_search":
                conv = 0.08
            if planted and channel == "social":
                monthly *= 2
            # Poisson-ish daily volume with mild noise
            n = max(0, round(rng.gauss(monthly / 30, (monthly / 30) ** 0.5)))
            for _ in range(n):
                rows.append(
                    {
                        "id": len(rows) + 1,
                        "created_at": day.isoformat(),
                        "channel": channel,
                        "country": pick(rng, COUNTRIES),
                        "plan": pick(rng, PLANS),
                        "converted": rng.random() < conv,
                    }
                )
        day += timedelta(days=1)

    with OUT.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows):,} rows to {OUT}")


if __name__ == "__main__":
    main()

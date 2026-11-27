"""Is this value unusual for this measure? A robust z-score over its history.

Median and MAD rather than mean and standard deviation: one past spike (a campaign
month) should not make every later month look normal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

# How far from the usual a value must be to be called notable, then unusual
NOTABLE_Z = 2.0
UNUSUAL_Z = 3.0
MIN_HISTORY = 6


@dataclass
class Anomaly:
    value: float
    median: float
    low: float
    high: float
    z: float | None
    level: str  # "unusual" | "notable" | "normal" | "unknown"
    history: list[float]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess(value: float, history: list[float]) -> Anomaly:
    past = [h for h in history if h is not None]
    if len(past) < MIN_HISTORY:
        return Anomaly(
            value,
            median(past) if past else value,
            min(past or [value]),
            max(past or [value]),
            None,
            "unknown",
            past,
        )
    mid = median(past)
    mad = median(abs(h - mid) for h in past) * 1.4826
    if mad == 0:
        # A flat history: any move is a change, but its size cannot be scaled
        spread = (max(past) - min(past)) or abs(mid) * 0.1 or 1.0
        z = (value - mid) / spread
    else:
        z = (value - mid) / mad
    level = "unusual" if abs(z) >= UNUSUAL_Z else "notable" if abs(z) >= NOTABLE_Z else "normal"
    return Anomaly(value, mid, min(past), max(past), round(z, 2), level, past)

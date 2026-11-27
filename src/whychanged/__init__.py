"""whychanged: why did this number change, answered from the data.

The analysis is code, not a model. A measure is compared across two periods, the
change is split by each of its dimensions, segments are ranked by how much of the
change they carry, and the current value is tested against its own history. The
result is `Findings`: aggregates only, never rows, ready to be put into words.
"""

from whychanged.executors import dbapi_executor, duckdb_executor, postgres_executor
from whychanged.investigate import Findings, investigate
from whychanged.measures import Dimension, Measure, MeasureError, fiscal_year_start, load_measures
from whychanged.narrate import Narration, narrate, openai_compatible
from whychanged.periods import Period, baseline_for, parse_period
from whychanged.report import format_findings

__version__ = "0.1.0"

__all__ = [
    "Dimension",
    "Findings",
    "Measure",
    "MeasureError",
    "Narration",
    "Period",
    "baseline_for",
    "dbapi_executor",
    "duckdb_executor",
    "fiscal_year_start",
    "format_findings",
    "investigate",
    "load_measures",
    "narrate",
    "openai_compatible",
    "parse_period",
    "postgres_executor",
]

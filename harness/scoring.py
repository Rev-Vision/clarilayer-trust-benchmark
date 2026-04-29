"""SQL extraction, execution, and result scoring for the Trust Benchmark.

Tolerance logic (`values_match`) and SQL exec (`run_sql`) are imported
directly from `validate_questions` so the harness and the ground-truth
review surface cannot drift. Per spec §3.4.2: ≤0.1% relative diff for
floats, exact match for ints.
"""
# ruff: noqa: S608  # SQL is model-generated and runs read-only against DuckDB

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

# Allow `from scoring import ...` to also import sibling validate_questions
# regardless of how the harness is launched (`python harness.py` from the
# repo root, the scripts dir, or as a package).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from validate_questions import (  # noqa: E402
    run_one as _run_one,
    values_match as _values_match,
)

# Re-export so existing call sites keep working.
values_match = _values_match
run_sql = _run_one

# ---------------------------------------------------------------------------
# SQL extraction from model output
# ---------------------------------------------------------------------------

# Match ```sql ... ``` (case-insensitive, multi-line, lazy). Falls back to
# bare ``` ... ``` if the model omits the language tag. Both patterns also
# accept a missing closing fence — Gemini in particular has been seen to
# truncate at max_output_tokens with the SQL body intact but no closing
# fence; in that case we extract everything from the opening fence to EOS
# and let DuckDB judge the result. (Without this, truncated-fence outputs
# get passed verbatim and DuckDB chokes on the literal '```sql' prefix.)
_FENCE_SQL = re.compile(r"```sql\s*(.+?)(?:```|$)", re.IGNORECASE | re.DOTALL)
_FENCE_BARE = re.compile(r"```\s*(.+?)(?:```|$)", re.DOTALL)
# Try to spot a "variant" declaration the model might emit (e.g.
# "Using variant C:" or "I'll use variant B."). Best-effort only — primary
# signal is the SQL itself.
_VARIANT_DECL = re.compile(
    r"\b(?:using|picking|chose|selected|variant)\s+(?:variant\s+)?([A-E])\b",
    re.IGNORECASE,
)


def extract_sql(text: str) -> str:
    """Return the SQL body from a model response.

    Strategy:
        1. Prefer the first ```sql ... ``` fenced block.
        2. Fall back to the first ``` ... ``` fenced block.
        3. Otherwise, treat the whole response as SQL (post-strip).
    """
    if not text:
        return ""
    m = _FENCE_SQL.search(text)
    if m:
        return m.group(1).strip()
    m = _FENCE_BARE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def extract_variant_choice(text: str) -> str | None:
    """Best-effort variant-id extractor (A-E). Returns None when uncertain."""
    if not text:
        return None
    m = _VARIANT_DECL.search(text)
    if m:
        return m.group(1).upper()
    return None


# ---------------------------------------------------------------------------
# SQL execution + comparison
# ---------------------------------------------------------------------------


@dataclass
class ScoreResult:
    status: str  # "PASS" | "FAIL" | "ERROR"
    actual_value: Any | None
    error: str | None
    detail: str  # human-readable note (rel_diff, "no rows", etc.)


def score(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    expected_value: Any,
) -> ScoreResult:
    """Run `sql` against `con`, compare to `expected_value`, return ScoreResult.

    Status legend:
        PASS  — actual matches expected within tolerance
        FAIL  — SQL ran but result is wrong
        ERROR — SQL didn't run (syntax error, missing column, timeout, etc.)
    """
    if not sql or not sql.strip():
        return ScoreResult("ERROR", None, "empty SQL", "model returned no SQL")

    try:
        actual = run_sql(con, sql)
    except Exception as exc:  # noqa: BLE001
        return ScoreResult("ERROR", None, f"{type(exc).__name__}: {exc}", "")

    ok, note = values_match(actual, expected_value)
    return ScoreResult(
        "PASS" if ok else "FAIL",
        actual,
        None,
        note,
    )

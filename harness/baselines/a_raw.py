"""Baseline A — raw schema only.

The model receives the bare DDL of the tables touched by this metric, with
no comments and no semantic layer. Represents a naive integration: point an
LLM at a warehouse and hope.
"""

from __future__ import annotations

from pathlib import Path

from . import read_context_file

ROOT = Path(__file__).resolve().parent.parent.parent
CONTEXT_DIR = ROOT / "context-blocks" / "baseline-a"


def fetch_context(metric_key: str, _cfg=None) -> str:
    path = CONTEXT_DIR / f"{metric_key}.sql"
    if not path.exists():
        raise FileNotFoundError(
            f"Baseline A context block missing for {metric_key} at {path}"
        )
    return read_context_file(str(path))


def build_user_message(metric_key: str, question: dict, ctx: str) -> str:
    return (
        "You are answering a SQL question against a DuckDB warehouse.\n"
        "Below is the **raw schema** for the relevant tables — DDL only,\n"
        "no documentation, no semantic layer. Read it carefully; column\n"
        "names may be ambiguous or legacy-shaped.\n\n"
        f"=== SCHEMA ===\n{ctx.strip()}\n=== END SCHEMA ===\n\n"
        f"Question: {question['question']}\n\n"
        "Respond with a single SQL query that answers the question. The\n"
        "query must run against DuckDB. Wrap the SQL in a fenced code\n"
        "block like:\n"
        "```sql\nSELECT ...\n```\n"
        "Return only the SQL. No prose, no commentary."
    )

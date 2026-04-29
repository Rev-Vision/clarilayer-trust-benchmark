"""Baseline B — documented schema with column comments.

DDL plus column-level comments admitting the warehouse's known messiness:
legacy columns, mixed timezones, NULL-prone fields, refund accounting,
currency drift, etc. Represents the well-documented-warehouse surface
(dbt docs, Snowflake column comments) — the strongest "good docs" baseline
*without* a governance layer.
"""

from __future__ import annotations

from pathlib import Path

from . import read_context_file

ROOT = Path(__file__).resolve().parent.parent.parent
CONTEXT_DIR = ROOT / "context-blocks" / "baseline-b"


def fetch_context(metric_key: str, _cfg=None) -> str:
    path = CONTEXT_DIR / f"{metric_key}.sql"
    if not path.exists():
        raise FileNotFoundError(
            f"Baseline B context block missing for {metric_key} at {path}"
        )
    return read_context_file(str(path))


def build_user_message(metric_key: str, question: dict, ctx: str) -> str:
    return (
        "You are answering a SQL question against a DuckDB warehouse.\n"
        "Below is the schema for the relevant tables, including\n"
        "**column-level comments** that document known data quality\n"
        "issues (legacy column names, mixed timezones, NULL-prone fields,\n"
        "refund accounting, etc.). Read every comment before writing\n"
        "your query.\n\n"
        f"=== SCHEMA ===\n{ctx.strip()}\n=== END SCHEMA ===\n\n"
        f"Question: {question['question']}\n\n"
        "Respond with a single SQL query that answers the question. The\n"
        "query must run against DuckDB. Wrap the SQL in a fenced code\n"
        "block like:\n"
        "```sql\nSELECT ...\n```\n"
        "Return only the SQL. No prose, no commentary."
    )

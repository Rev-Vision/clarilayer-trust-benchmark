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
        "Respond in this **structured JSON format** (one JSON object,\n"
        "no surrounding prose, no markdown fences around the object):\n\n"
        "{\n"
        '  "warnings": [],\n'
        '  "clarification_request": null,\n'
        '  "sql": "SELECT ...",\n'
        '  "rationale": ""\n'
        "}\n\n"
        "Field semantics:\n"
        '- "sql" (REQUIRED, string, non-null): a single DuckDB SQL query\n'
        "  that answers the question. Always populate this field.\n"
        '- "warnings" MUST be `[]`. Baseline B is documented schema with\n'
        "  no governance metadata — there are no governance signals to\n"
        "  surface. Do NOT use warnings for schema/comment concerns,\n"
        "  generic data-quality notes, or speculative hazards.\n"
        '- "clarification_request" MUST be `null`.\n'
        '- "rationale" SHOULD be empty (`""`) unless you made a specific\n'
        "  scope choice that affects the SQL — then one short sentence.\n"
    )

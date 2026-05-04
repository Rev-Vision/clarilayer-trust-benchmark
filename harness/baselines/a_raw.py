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
        "  that answers the question using the most defensible default\n"
        "  scope you can infer from the schema. Always populate this field.\n"
        '- "warnings" MUST be `[]`. Baseline A is raw schema with no\n'
        "  governance metadata — there are no governance signals to\n"
        "  surface. Do NOT use warnings for schema concerns, ambiguous\n"
        "  columns, or speculative hazards.\n"
        '- "clarification_request" MUST be `null`.\n'
        '- "rationale" SHOULD be empty (`""`) unless you made a specific\n'
        "  scope choice that affects the SQL — then one short sentence.\n"
    )

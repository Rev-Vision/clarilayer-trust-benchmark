"""Baseline C — Cube.dev semantic layer.

Cube.dev YAML defining the metric as ONE canonical measure, plus
dimensions, joins, and Cube-default time grains. ONE definition per
measure with no governance metadata, no versioning, no deprecated
variants — the strongest non-ClariLayer prior art surface (per spec
§3.1 / §8.7).
"""

from __future__ import annotations

from pathlib import Path

from . import read_context_file

ROOT = Path(__file__).resolve().parent.parent.parent
CONTEXT_DIR = ROOT / "context-blocks" / "baseline-c"


def fetch_context(metric_key: str, _cfg=None) -> str:
    path = CONTEXT_DIR / f"{metric_key}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Baseline C context block missing for {metric_key} at {path}"
        )
    return read_context_file(str(path))


def build_user_message(metric_key: str, question: dict, ctx: str) -> str:
    return (
        "You are answering a SQL question against a DuckDB warehouse\n"
        "using a **Cube.dev semantic layer**. The YAML below defines\n"
        "the cubes (tables), their dimensions, joins, and measures. The\n"
        "measure named in the cubes is the canonical definition for\n"
        "this metric. Time grains follow Cube defaults (day / week /\n"
        "month / quarter / year). There is no fiscal-calendar override\n"
        "and no governance metadata — this is what a competent semantic\n"
        "engineer ships.\n\n"
        f"=== CUBE YAML ===\n{ctx.strip()}\n=== END CUBE YAML ===\n\n"
        f"Question: {question['question']}\n\n"
        "Respond with a single SQL query (against the underlying DuckDB\n"
        "tables) that answers the question. Inline the measure's\n"
        "definition rather than emitting Cube query JSON. Wrap the SQL\n"
        "in a fenced code block like:\n"
        "```sql\nSELECT ...\n```\n"
        "Return only the SQL. No prose, no commentary."
    )

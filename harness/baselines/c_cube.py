"""Baseline C — Cube.dev semantic layer (expert-grade).

Cube.dev YAML defining the metric as ONE canonical measure on its
primary cube, plus the full set of expert-grade Cube primitives a
senior semantic-layer engineer would author: bidirectional joins
(`many_to_one` + `one_to_many`), helper aggregate measures
(sum/avg/min/max/count_distinct), reusable segments, pre-aggregations,
custom granularities, dimension titles/descriptions, and currency /
percent format hints.

Still ONE canonical metric definition per file, no governance metadata,
no metric versioning, no deprecated variants, no fiscal-calendar
override — the strongest non-ClariLayer prior art surface (per spec
§3.1 / §6.10 / §8.7). Per-primitive coverage and Cube docs anchors
are documented in
`benchmark/context-blocks/baseline-c/adequacy-checklist.md`.
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
        "the cubes (tables), their dimensions, joins, measures,\n"
        "segments, and pre-aggregations. The measure named after the\n"
        "metric is the canonical definition for this metric. Helper\n"
        "measures (sum / avg / min / max / count_distinct) and segments\n"
        "(predefined boolean filters) are also exposed on each cube.\n"
        "Joins are bidirectional where applicable (`many_to_one` +\n"
        "`one_to_many`). Time grains follow Cube defaults (day / week\n"
        "/ month / quarter / year), with custom granularities declared\n"
        "inline on the relevant time dimensions where present.\n"
        "There is no fiscal-calendar override and no governance\n"
        "metadata — this is what a competent semantic-layer engineer\n"
        "ships.\n\n"
        f"=== CUBE YAML ===\n{ctx.strip()}\n=== END CUBE YAML ===\n\n"
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
        "  against the underlying warehouse tables. Inline the measure's\n"
        "  definition rather than emitting Cube query JSON.\n"
        '- "warnings" MUST be `[]`. Baseline C is the Cube semantic-\n'
        "  layer YAML — Cube schemas do NOT carry governance metadata\n"
        "  (no deprecation banners, no approval state, no consumer\n"
        "  pinning). Do NOT use warnings for measure/segment/join\n"
        "  observations or speculative concerns.\n"
        '- "clarification_request" MUST be `null`.\n'
        '- "rationale" SHOULD be empty (`""`) unless you made a specific\n'
        "  measure / segment / grain choice — then one short sentence.\n"
    )

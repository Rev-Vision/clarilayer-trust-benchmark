"""Baseline D — dbt MetricFlow semantic layer.

dbt MetricFlow YAML defining the metric as ONE canonical metric on its
primary semantic model, plus the full set of expert-grade MetricFlow
primitives a senior analytics engineer would author: per-table
semantic_models (with `model:` resolved to the underlying warehouse
table at generation time so the harness can execute the LLM's SQL
directly against DuckDB without a dbt rendering pass — see
`benchmark/scripts/build_context_blocks.py::_dbt_semantic_model_ref`),
primary / foreign
entities (the MetricFlow analogue of joins), categorical + time
dimensions with `type_params.time_granularity`, helper aggregate
measures (sum / average / min / max / count_distinct), top-level
metric blocks with `type: simple | ratio | derived | cumulative`
(filters live on the metric or its input measures —
`type_params.measure.filter`, `numerator.filter`,
`denominator.filter` — since MetricFlow does not support
measure-level filters), time spine + custom
fiscal_month granularity (declared on the time-spine model in the
sibling dbt project at benchmark/dbt_project/), and saved_queries
for the rollups Cube pre-aggregates.

Still ONE canonical metric definition per file, no governance metadata,
no metric versioning, no deprecated variants, no fiscal-calendar
override — the dbt MetricFlow sibling of Baseline C, scoped to the
same "expert-grade non-governed" surface (per spec §3.1 / §6.10 /
§6.11). Per-primitive coverage and dbt-metricflow docs anchors are
documented in
`benchmark/context-blocks/baseline-d/adequacy-checklist.md`.
"""

from __future__ import annotations

from pathlib import Path

from . import read_context_file

ROOT = Path(__file__).resolve().parent.parent.parent
CONTEXT_DIR = ROOT / "context-blocks" / "baseline-d"


def fetch_context(metric_key: str, _cfg=None) -> str:
    path = CONTEXT_DIR / f"{metric_key}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Baseline D context block missing for {metric_key} at {path}"
        )
    return read_context_file(str(path))


def build_user_message(metric_key: str, question: dict, ctx: str) -> str:
    return (
        "You are answering a SQL question against a DuckDB warehouse\n"
        "using a **dbt MetricFlow semantic layer**. The YAML below\n"
        "defines per-table `semantic_models` (each `model:` field names\n"
        "the underlying warehouse table — query that table directly in\n"
        "your SQL), their `entities` (primary / foreign keys MetricFlow\n"
        "uses to wire joins), `dimensions`\n"
        "(categorical + time, with `type_params.time_granularity` on\n"
        "every time dimension), `measures` (with `agg:` only — dbt\n"
        "MetricFlow does not support measure-level filters), top-level\n"
        "`metrics:` (with `type:` of simple / ratio / derived /\n"
        "cumulative + `type_params:`) where any filters live on metric\n"
        "inputs such as `filter:` (top-level on simple metrics),\n"
        "`type_params.measure.filter`, `numerator.filter`,\n"
        "`denominator.filter`, or derived metric inputs, and any\n"
        "`saved_queries:` the team has materialized. The single metric\n"
        "in this file is the canonical definition for the requested\n"
        "metric. Its name may differ from the metric_key (e.g.\n"
        "`active_user` -> `active_users`, `customer` -> `customers`,\n"
        "`new_user` -> `new_users`, `payback_period` ->\n"
        "`payback_months`, `qualified_lead` -> `qualified_leads`), so\n"
        "inspect the top-level `metrics:` block rather than assuming a\n"
        "name match. Helper measures (sum / average / min / max /\n"
        "count / count_distinct) are also exposed on each semantic\n"
        "model. A daily time spine with a calendar-aligned\n"
        "`fiscal_month` custom granularity is registered globally on\n"
        "the dbt project's time-spine model.\n"
        "There is no fiscal-calendar override and no governance\n"
        "metadata — this is what a competent dbt analytics engineer\n"
        "ships.\n\n"
        f"=== METRICFLOW YAML ===\n{ctx.strip()}\n=== END METRICFLOW YAML ===\n\n"
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
        "  against the underlying warehouse tables. Inline the metric's\n"
        "  measure SQL rather than emitting a `mf query` JSON.\n"
        '- "warnings" MUST be `[]`. Baseline D is the dbt MetricFlow\n'
        "  semantic-layer YAML — MetricFlow models do NOT carry\n"
        "  governance metadata (no deprecation banners, no approval\n"
        "  state, no consumer pinning). Do NOT use warnings for\n"
        "  measure/saved-query/grain observations or speculative\n"
        "  concerns.\n"
        '- "clarification_request" MUST be `null`.\n'
        '- "rationale" SHOULD be empty (`""`) unless you made a specific\n'
        "  metric / measure / grain choice — then one short sentence.\n"
    )

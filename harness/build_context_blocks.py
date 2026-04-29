"""
ClariLayer Trust Benchmark — static context-block generator.

Builds three context blocks per metric (Baselines A, B, C) consumed by the
benchmark harness. Reproducible: re-running regenerates byte-identical
output (modulo YAML library version churn) so this script is the sole
runtime dependency for refreshing context blocks when models change or
schemas evolve.

Inputs:
- dataset/warehouse-schema-bare.sql        (Baseline A source)
- dataset/warehouse-schema-documented.sql  (Baseline B source)
- dataset/metrics/*.yaml                   (metric catalog)

Outputs (60 files = 20 metrics x 3 baselines):
- context-blocks/baseline-a/{metric_key}.sql   raw DDL only
- context-blocks/baseline-b/{metric_key}.sql   DDL + column comments
- context-blocks/baseline-c/{metric_key}.yaml  Cube.dev semantic layer

Per the benchmark methodology (see paper/trust-benchmark-v1.md):
- Baseline A: a naive integration's surface (no comments).
- Baseline B: a well-documented warehouse's surface (column comments admit
  the messiness honestly — that's the dbt/Snowflake-with-docs prior art).
- Baseline C: the strongest non-ClariLayer prior art — a Cube.dev-style
  semantic layer. ONE canonical measure definition per metric, no
  governance metadata, no fiscal calendar — just the YAML a competent
  semantic-layer engineer would ship.
- Baseline D is NOT in scope (live API call; the harness handles it).

Run:
    python harness/build_context_blocks.py

Idempotent. Approximate runtime: <1s.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = REPO_ROOT / "benchmark"
DATASET_DIR = BENCHMARK_DIR / "dataset"
METRICS_DIR = DATASET_DIR / "metrics"
SCHEMA_BARE = DATASET_DIR / "warehouse-schema-bare.sql"
SCHEMA_DOCS = DATASET_DIR / "warehouse-schema-documented.sql"

OUT_DIR = BENCHMARK_DIR / "context-blocks"
OUT_BASE_A = OUT_DIR / "baseline-a"
OUT_BASE_B = OUT_DIR / "baseline-b"
OUT_BASE_C = OUT_DIR / "baseline-c"

# ---------------------------------------------------------------------------
# Known warehouse tables — keep in sync with warehouse-schema-bare.sql
# ---------------------------------------------------------------------------

KNOWN_TABLES: tuple[str, ...] = (
    "dim_customers",
    "dim_users",
    "fct_subscriptions",
    "fct_events",
    "fct_invoices",
    "dim_products",
    "fct_marketing_spend",
    "fct_cogs",
)
KNOWN_TABLE_SET = set(KNOWN_TABLES)

# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(
    r"(CREATE\s+TABLE\s+(\w+)\s*\(.*?\)\s*;)",
    re.IGNORECASE | re.DOTALL,
)


def parse_schema(path: Path) -> dict[str, str]:
    """Return {table_name: full CREATE TABLE statement} from a schema SQL file.

    Statements are kept verbatim — including inline comments in the
    documented schema. Order is preserved via dict insertion order so the
    generated context blocks match the canonical schema ordering.
    """
    src = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for full_stmt, table in _CREATE_TABLE_RE.findall(src):
        if table not in KNOWN_TABLE_SET:
            continue
        out[table] = full_stmt
    missing = KNOWN_TABLE_SET - set(out)
    if missing:
        raise RuntimeError(
            f"{path.name}: missing CREATE TABLE for {sorted(missing)}"
        )
    return out


# ---------------------------------------------------------------------------
# Per-metric table extraction (from variant SQL only — never prose).
# ---------------------------------------------------------------------------

_FROM_JOIN_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def collect_metric_tables(metric: dict) -> list[str]:
    """Union of warehouse tables referenced in a metric's governed/variant SQL.

    Only the literal `sql_template` strings are inspected. Prose fields
    (description, plain_language_spec, notes_for_*, etc.) are ignored —
    spec contract: "compute by inspecting the metric's governed/variant
    SQL for table references."

    Returns table names in canonical schema order (KNOWN_TABLES order),
    so output is deterministic regardless of metric author choices.
    """
    sources: list[str] = []
    governed = metric.get("governed_definition") or {}
    if isinstance(governed, dict) and governed.get("sql_template"):
        sources.append(governed["sql_template"])
    for variant in metric.get("variants") or []:
        if isinstance(variant, dict) and variant.get("sql_template"):
            sources.append(variant["sql_template"])

    found: set[str] = set()
    for sql in sources:
        for ref in _FROM_JOIN_RE.findall(sql):
            if ref in KNOWN_TABLE_SET:
                found.add(ref)

    if not found:
        raise RuntimeError(
            f"metric {metric.get('metric_key')!r}: no warehouse table "
            f"references in governed/variant SQL — generator cannot build "
            f"baselines."
        )
    return [t for t in KNOWN_TABLES if t in found]


# ---------------------------------------------------------------------------
# Baseline A / B writers
# ---------------------------------------------------------------------------

BASELINE_A_HEADER = (
    "-- ClariLayer Trust Benchmark — Baseline A (raw schema only)\n"
    "-- Metric: {metric_key}\n"
    "-- Generated by harness/build_context_blocks.py — do not edit by hand.\n"
    "-- Tables: {tables}\n"
    "\n"
)

BASELINE_B_HEADER = (
    "-- ClariLayer Trust Benchmark — Baseline B (schema + column comments)\n"
    "-- Metric: {metric_key}\n"
    "-- Generated by harness/build_context_blocks.py — do not edit by hand.\n"
    "-- Tables: {tables}\n"
    "--\n"
    "-- Column comments admit the warehouse's known messiness — legacy\n"
    "-- columns, mixed timezones, NULL-prone fields, currency drift, etc.\n"
    "-- This is the well-documented-warehouse surface (dbt docs / Snowflake\n"
    "-- column comments) — but with no governed-metric record, no canonical\n"
    "-- filter set, and no semantic layer.\n"
    "\n"
)


def render_ddl_block(
    metric_key: str,
    tables: list[str],
    schema_map: dict[str, str],
    header_template: str,
) -> str:
    body_parts = [schema_map[t] for t in tables]
    body = "\n\n".join(body_parts) + "\n"
    return header_template.format(
        metric_key=metric_key, tables=", ".join(tables)
    ) + body


# ---------------------------------------------------------------------------
# Baseline C — Cube.dev semantic layer
# ---------------------------------------------------------------------------

# Cube short-names and per-table dimension/timeDimension maps.
# Picked to mirror Cube.dev convention: cube name = short logical name,
# sql_table = the warehouse table.
CUBE_NAME: dict[str, str] = {
    "dim_customers": "customers",
    "dim_users": "users",
    "fct_subscriptions": "subscriptions",
    "fct_events": "events",
    "fct_invoices": "invoices",
    "dim_products": "products",
    "fct_marketing_spend": "marketing_spend",
    "fct_cogs": "cogs",
}

# Dimensions per table the generator surfaces in Baseline C. The
# semantic-layer engineer's choice — Baseline C does NOT carry the legacy
# `cust_id`/`account_id`/`joined_dt`/`inserted` columns: a competent Cube
# author picks the canonical column and drops the legacy. That's the
# realistic prior-art surface (Baseline B is what shows the LLM the legacy
# columns exist; Baseline C is what a clean semantic layer ships).
DIMENSIONS_PER_TABLE: dict[str, list[dict]] = {
    "dim_customers": [
        {"name": "customer_id", "sql": "{CUBE}.customer_id", "type": "string", "primary_key": True},
        {"name": "company_name", "sql": "{CUBE}.company_name", "type": "string"},
        {"name": "industry", "sql": "{CUBE}.industry", "type": "string"},
        {"name": "plan_tier", "sql": "{CUBE}.plan_tier", "type": "string"},
        {"name": "country", "sql": "{CUBE}.country", "type": "string"},
        {"name": "is_test", "sql": "{CUBE}.is_test", "type": "boolean"},
        {"name": "signup_date", "sql": "{CUBE}.signup_date", "type": "time"},
        {"name": "churned_at", "sql": "{CUBE}.churned_at", "type": "time"},
    ],
    "dim_users": [
        {"name": "user_id", "sql": "{CUBE}.user_id", "type": "string", "primary_key": True},
        {"name": "customer_id", "sql": "{CUBE}.customer_id", "type": "string"},
        {"name": "email", "sql": "{CUBE}.email", "type": "string"},
        {"name": "role", "sql": "{CUBE}.role", "type": "string"},
        {"name": "created_at", "sql": "{CUBE}.created_at", "type": "time"},
        {"name": "last_active_at", "sql": "{CUBE}.last_active_at", "type": "time"},
        {"name": "deleted_at", "sql": "{CUBE}.deleted_at", "type": "time"},
    ],
    "fct_subscriptions": [
        {"name": "sub_id", "sql": "{CUBE}.sub_id", "type": "string", "primary_key": True},
        {"name": "customer_id", "sql": "{CUBE}.customer_id", "type": "string"},
        {"name": "plan", "sql": "{CUBE}.plan", "type": "string"},
        {"name": "status", "sql": "{CUBE}.status", "type": "string"},
        {"name": "started_at", "sql": "{CUBE}.started_at", "type": "time"},
        {"name": "ended_at", "sql": "{CUBE}.ended_at", "type": "time"},
    ],
    "fct_events": [
        {"name": "event_id", "sql": "{CUBE}.event_id", "type": "string", "primary_key": True},
        {"name": "user_id", "sql": "{CUBE}.user_id", "type": "string"},
        {"name": "customer_id", "sql": "{CUBE}.customer_id", "type": "string"},
        {"name": "event_type", "sql": "{CUBE}.event_type", "type": "string"},
        {"name": "timezone", "sql": "{CUBE}.timezone", "type": "string"},
        {"name": "timestamp", "sql": '{CUBE}."timestamp"', "type": "time"},
        {"name": "inserted_at", "sql": "{CUBE}.inserted_at", "type": "time"},
    ],
    "fct_invoices": [
        {"name": "invoice_id", "sql": "{CUBE}.invoice_id", "type": "string", "primary_key": True},
        {"name": "customer_id", "sql": "{CUBE}.customer_id", "type": "string"},
        {"name": "status", "sql": "{CUBE}.status", "type": "string"},
        {"name": "processor", "sql": "{CUBE}.processor", "type": "string"},
        {"name": "paid_at", "sql": "{CUBE}.paid_at", "type": "time"},
        {"name": "due_at", "sql": "{CUBE}.due_at", "type": "time"},
    ],
    "dim_products": [
        {"name": "product_id", "sql": "{CUBE}.product_id", "type": "string", "primary_key": True},
        {"name": "name", "sql": "{CUBE}.name", "type": "string"},
        {"name": "tier", "sql": "{CUBE}.tier", "type": "string"},
        {"name": "launched_at", "sql": "{CUBE}.launched_at", "type": "time"},
    ],
    "fct_marketing_spend": [
        {"name": "channel", "sql": "{CUBE}.channel", "type": "string"},
        {"name": "currency", "sql": "{CUBE}.currency", "type": "string"},
        {"name": "period_month", "sql": "{CUBE}.period_month", "type": "time"},
    ],
    "fct_cogs": [
        {"name": "source", "sql": "{CUBE}.source", "type": "string"},
        {"name": "period_month", "sql": "{CUBE}.period_month", "type": "time"},
    ],
}

# Static dimensions with type=number need to live alongside each cube too.
NUMERIC_DIMENSIONS_PER_TABLE: dict[str, list[dict]] = {
    "fct_subscriptions": [
        {"name": "mrr", "sql": "{CUBE}.mrr", "type": "number"},
    ],
    "fct_invoices": [
        {"name": "amount", "sql": "{CUBE}.amount", "type": "number"},
    ],
    "fct_marketing_spend": [
        {"name": "amount", "sql": "{CUBE}.amount", "type": "number"},
    ],
    "fct_cogs": [
        {"name": "amount", "sql": "{CUBE}.amount", "type": "number"},
    ],
}

# Joins: the canonical FK = customer_id everywhere it's available. Per
# spec note: "Cube.dev typically picks ONE; pick the canonical
# customer_id." The legacy `cust_id` / `account_id` columns are not
# surfaced in Baseline C — that's part of why a semantic layer is the
# strongest non-governed prior art (it cleans up *some* of the messiness
# but doesn't carry governance like is_test filtering).
JOIN_GRAPH: dict[str, list[dict]] = {
    # primary cube : list of joins (relationship in Cube terms)
    "dim_customers": [],
    "dim_users": [
        {
            "to": "dim_customers",
            "sql": "{CUBE}.customer_id = {customers}.customer_id",
            "relationship": "many_to_one",
        },
    ],
    "fct_subscriptions": [
        {
            "to": "dim_customers",
            "sql": "{CUBE}.customer_id = {customers}.customer_id",
            "relationship": "many_to_one",
        },
    ],
    "fct_events": [
        {
            "to": "dim_users",
            "sql": "{CUBE}.user_id = {users}.user_id",
            "relationship": "many_to_one",
        },
        {
            "to": "dim_customers",
            "sql": "{CUBE}.customer_id = {customers}.customer_id",
            "relationship": "many_to_one",
        },
    ],
    "fct_invoices": [
        {
            "to": "dim_customers",
            "sql": "{CUBE}.customer_id = {customers}.customer_id",
            "relationship": "many_to_one",
        },
    ],
    "dim_products": [],
    "fct_marketing_spend": [],
    "fct_cogs": [],
}

CUBE_DEFAULT_TIME_GRAINS = ["day", "week", "month", "quarter", "year"]


# Per-metric Baseline C measure definition. Each entry produces ONE
# canonical measure on the metric's "primary" cube. The variant_id is the
# D2 variant we lifted into Cube.dev syntax (preferred: A or B without
# governed filters); the measure_sql is the literal Cube SQL fragment;
# filters_sql captures the WHERE-clause guards that translate cleanly to
# Cube measure filters.
#
# Cube cannot natively express NOT EXISTS subqueries, JSON-path coalescing,
# or window functions — so for measures whose D2 variants rely on those,
# we pick the simplest A/B variant a competent Cube author would actually
# write. Where the metric's only A/B variant requires a derived/join
# expression Cube can't hold cleanly, we drop down to the simplest naive
# form (e.g. a count) and call that out in a header comment in the YAML.
BASELINE_C_DEFINITIONS: dict[str, dict] = {
    "active_user": {
        "primary_cube": "dim_users",
        "variant_id": "A",
        "variant_label": "last_active_at within window",
        "measure_name": "active_users",
        "measure_type": "count_distinct",
        "measure_sql": "{CUBE}.user_id",
        "time_dimension": "last_active_at",
        # Cube `period over period` is computed from the time-dimension
        # filter at query time; no static window filter on the measure.
        "filters": [],
        "description": "Distinct users with last_active_at in the requested window. No subscription/test filters — that's the governance gap.",
    },
    "arr": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "A",
        "variant_label": "SUM(mrr) * 12 where status = 'active'",
        "measure_name": "arr",
        "measure_type": "number",
        "measure_sql": "SUM({CUBE}.mrr) * 12",
        "filters": [{"sql": "{CUBE}.status = 'active'"}],
        "time_dimension": "started_at",
        "description": "Annualized run-rate from active subscriptions. No is_test / churned / negative-MRR filters.",
    },
    "arr_contraction": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "A (approximated)",
        "variant_label": "sum of negative MRR rows in window (refund/contraction artifacts)",
        "header_notes": [
            "Cube cannot natively express delta-based contraction across periods;",
            "this approximation captures contraction-shaped artifacts via negative MRR",
            "values rather than computing customer-level deltas.",
        ],
        "measure_name": "arr_contraction",
        "measure_type": "number",
        # A Cube author can't compute MRR deltas across rows in a single
        # measure; the closest naive approximation is summing the
        # negative-MRR rows directly.
        "measure_sql": "ABS(SUM({CUBE}.mrr)) * 12",
        "filters": [
            {"sql": "{CUBE}.mrr < 0"},
        ],
        "time_dimension": "started_at",
        "description": "Annualized contraction approximated by summing negative-MRR rows (refund accounting). The simpler Cube reading of variant A.",
    },
    "arr_expansion": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "A (approximated)",
        "variant_label": "sum of positive active-MRR stock in window (run-rate proxy)",
        "header_notes": [
            "Cube cannot natively express delta-based expansion across periods;",
            "this approximation captures expansion-correlated stock rather than computing",
            "customer-level positive deltas.",
        ],
        "measure_name": "arr_expansion",
        "measure_type": "number",
        "measure_sql": "SUM({CUBE}.mrr) * 12",
        "filters": [
            {"sql": "{CUBE}.mrr > 0"},
            {"sql": "{CUBE}.status = 'active'"},
        ],
        "time_dimension": "started_at",
        "description": "Annualized run-rate of positive-MRR subscriptions, simplest Cube approximation of expansion variant A.",
    },
    "bookings": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "A",
        "variant_label": "SUM(mrr) for subscriptions started in period",
        "measure_name": "bookings",
        "measure_type": "number",
        "measure_sql": "SUM({CUBE}.mrr)",
        "filters": [],
        "time_dimension": "started_at",
        "description": "Period-bookings as the sum of MRR on subscriptions whose started_at falls in the requested window.",
    },
    "cac": {
        "primary_cube": "dim_customers",
        "variant_id": "A",
        "variant_label": "Total spend / all new signups (any subscription state)",
        "measure_name": "cac",
        "measure_type": "number",
        # Spend is a placeholder constant in this synthetic warehouse
        # (see metric YAML). Hardcoding the constant in the measure
        # mirrors what the D2 catalog has variant A do.
        "measure_sql": "300000.0 / NULLIF(COUNT(DISTINCT {CUBE}.customer_id), 0)",
        "filters": [],
        "time_dimension": "signup_date",
        "description": "Hardcoded $300K/month S+M placeholder over distinct new signups. No paying-customer or test exclusion.",
    },
    "churn_rate": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "B",
        "variant_label": "Revenue churn — ARR lost / ARR at period start",
        "measure_name": "churn_rate_pct",
        "measure_type": "number",
        # Variant A is governed in D2 ("Logo churn"); the naive non-
        # governed Cube reading is the revenue-churn variant B. Cube
        # can't elegantly compute "ARR at period start" in a single
        # measure, so the naive Cube author ships the simplest ratio:
        # MRR on cancelled subs over total MRR.
        "measure_sql": "100.0 * SUM(CASE WHEN {CUBE}.status = 'cancelled' THEN {CUBE}.mrr ELSE 0 END) / NULLIF(SUM({CUBE}.mrr), 0)",
        "filters": [],
        "time_dimension": "ended_at",
        "description": "Cancelled-subscription MRR share of total MRR. Cube approximation of variant B revenue churn. No period-anchored start-cohort denominator, no is_test exclusion.",
    },
    "customer": {
        "primary_cube": "dim_customers",
        "variant_id": "A",
        "variant_label": "All rows in dim_customers",
        "measure_name": "customers",
        "measure_type": "count",
        "measure_sql": "*",
        "filters": [],
        "time_dimension": "signup_date",
        "description": "Headcount of dim_customers rows. No paying / non-test / non-churned filters.",
    },
    "daily_active_users": {
        "primary_cube": "dim_users",
        "variant_id": "A",
        "variant_label": "Naive UTC day bucket on last_active_at",
        "measure_name": "daily_active_users",
        "measure_type": "count_distinct",
        "measure_sql": "{CUBE}.user_id",
        "filters": [],
        "time_dimension": "last_active_at",
        "description": "Distinct user_ids per day from last_active_at. No timezone normalization, no subscription gate.",
    },
    "gross_margin": {
        "primary_cube": "fct_invoices",
        "variant_id": "B",
        "variant_label": "(Revenue - 25% COGS) / Revenue, raw invoices (no test exclusion)",
        "measure_name": "gross_margin_pct",
        "measure_type": "number",
        "measure_sql": "(SUM({CUBE}.amount) - SUM({CUBE}.amount) * 0.25) / NULLIF(SUM({CUBE}.amount), 0) * 100",
        "filters": [{"sql": "{CUBE}.status = 'paid'"}],
        "time_dimension": "paid_at",
        "description": "Revenue minus 25% COGS placeholder over paid invoices. No is_test exclusion. Variant B in the D2 catalog.",
    },
    "gross_retention": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "A",
        "variant_label": "Logo retention — customers retained / customers at start",
        "measure_name": "gross_retention",
        "measure_type": "number",
        # Cube can't express start-cohort tracking in one measure cleanly
        # — naive form: share of subs still active.
        "measure_sql": "1.0 * COUNT(CASE WHEN {CUBE}.status = 'active' THEN 1 END) / NULLIF(COUNT(*), 0)",
        "filters": [],
        "time_dimension": "started_at",
        "description": "Active-subscription share — Cube approximation of variant A logo retention. No revenue weighting.",
    },
    "ltv": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "A",
        "variant_label": "ARPU * 1/Churn (no margin adjustment)",
        "measure_name": "ltv",
        "measure_type": "number",
        # Pure Cube measures can't multiply two aggregates derived from
        # different filter contexts. Naive Cube author lifts ARPU and
        # leaves the lifetime divisor as a derived calc the consumer
        # plugs in — here we approximate with AVG(mrr)*12 (one year
        # equivalent, no real churn rate).
        "measure_sql": "AVG({CUBE}.mrr) * 12",
        "filters": [{"sql": "{CUBE}.status = 'active'"}],
        "time_dimension": "started_at",
        "description": "ARPU annualized as an LTV proxy. No gross-margin, no churn divisor — that's the gap variant A lives in.",
    },
    "mrr": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "A",
        "variant_label": "SUM(mrr) where status = 'active'",
        "measure_name": "mrr",
        "measure_type": "number",
        "measure_sql": "SUM({CUBE}.mrr)",
        "filters": [{"sql": "{CUBE}.status = 'active'"}],
        "time_dimension": "started_at",
        "description": "Sum of MRR on active subscriptions. No is_test / churned / negative-MRR filters.",
    },
    "net_retention": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "A",
        "variant_label": "(Starting ARR + expansion - contraction - churn) / Starting ARR",
        "measure_name": "net_retention",
        "measure_type": "number",
        # Cube can't compose period-anchored starting-ARR cohorts in a
        # single measure. Naive Cube author falls back to a simpler
        # active-revenue ratio.
        "measure_sql": "1.0 * SUM(CASE WHEN {CUBE}.status = 'active' THEN {CUBE}.mrr ELSE 0 END) / NULLIF(SUM({CUBE}.mrr), 0)",
        "filters": [],
        "time_dimension": "started_at",
        "description": "Active-MRR share of total MRR. Coarse Cube approximation of NRR variant A — no per-customer cohorting, no start/end period anchors.",
    },
    "new_user": {
        "primary_cube": "dim_users",
        "variant_id": "A",
        "variant_label": "Users with created_at in window (no filters)",
        "measure_name": "new_users",
        "measure_type": "count_distinct",
        "measure_sql": "{CUBE}.user_id",
        "filters": [],
        "time_dimension": "created_at",
        "description": "Distinct users with created_at in the window. No is_test, deleted_at, or subscription filters.",
    },
    "payback_period": {
        "primary_cube": "fct_subscriptions",
        "variant_id": "A",
        "variant_label": "CAC / monthly gross-margin-adjusted ARPU",
        "measure_name": "payback_months",
        "measure_type": "number",
        # Same Cube-can't-multiply-aggregates limitation as LTV.
        # Naive Cube version uses the placeholder spend / ARPU.
        "measure_sql": "300000.0 / NULLIF(AVG({CUBE}.mrr), 0)",
        "filters": [{"sql": "{CUBE}.status = 'active'"}],
        "time_dimension": "started_at",
        "description": "CAC placeholder over ARPU as a months-to-payback proxy. No gross-margin scaling — that's the gap.",
    },
    "pipeline_coverage": {
        "primary_cube": "fct_events",
        "variant_id": "A",
        "variant_label": "Sum of all opp deal values / target",
        "measure_name": "pipeline_coverage",
        "measure_type": "number",
        # Cube measure can't NOT-EXISTS over follow-up events; naive
        # Cube author sums all opp_created deal_values and divides by
        # the placeholder target.
        "measure_sql": "SUM(CAST({CUBE}.properties_jsonb->>'deal_value' AS numeric)) / 5000000.0",
        "filters": [{"sql": "{CUBE}.event_type = 'opp_created'"}],
        "time_dimension": "timestamp",
        "description": "All opp_created deal values divided by $5M target. No open-status NOT EXISTS filter, no JSON-path coalesce — a competent Cube author would not catch the camelCase form.",
    },
    "qualified_lead": {
        "primary_cube": "dim_users",
        "variant_id": "A (approximated)",
        "variant_label": "Distinct users created in window (event requirement omitted)",
        "header_notes": [
            "Cube cannot natively express NOT EXISTS / event-count gate via a",
            "JOIN-based measure; this approximation drops the event existence filter,",
            "which is itself the kind of gap a competent semantic-layer engineer ships",
            "without governance context.",
        ],
        "measure_name": "qualified_leads",
        "measure_type": "count_distinct",
        "measure_sql": "{CUBE}.user_id",
        # Cube measure-level filter can't express EXISTS over a child
        # cube efficiently — naive Cube author counts new users and
        # leaves the engagement filter for the consumer.
        "filters": [],
        "time_dimension": "created_at",
        "description": "Distinct users created in window. No engagement-threshold or industry filter — those are too complex for a single Cube measure.",
    },
    "revenue": {
        "primary_cube": "fct_invoices",
        "variant_id": "A",
        "variant_label": "SUM(amount) on paid invoices",
        "measure_name": "revenue",
        "measure_type": "number",
        "measure_sql": "SUM({CUBE}.amount)",
        "filters": [{"sql": "{CUBE}.status = 'paid'"}],
        "time_dimension": "paid_at",
        "description": "Sum of paid-invoice amounts over the period. No is_test exclusion, no processor-timezone normalization.",
    },
    "win_rate": {
        "primary_cube": "fct_events",
        "variant_id": "B",
        "variant_label": "won / (won + lost + no_decision)",
        "measure_name": "win_rate",
        "measure_type": "number",
        # Variant A is governed in D2; variant B is the natural naive
        # Cube reading — include no_decision in the denominator the way
        # most CRM-attached dashboards report close rate.
        "measure_sql": "1.0 * COUNT(CASE WHEN {CUBE}.event_type = 'opp_won' THEN 1 END) / NULLIF(COUNT(CASE WHEN {CUBE}.event_type IN ('opp_won', 'opp_lost', 'opp_no_decision') THEN 1 END), 0)",
        "filters": [],
        "time_dimension": "timestamp",
        "description": "Won events divided by (won + lost + no_decision). Includes no_decision — variant B's trap (governed variant A excludes it).",
    },
}


def _yaml_block_string(s: str) -> str:
    """Return s as a single-line YAML scalar — Cube SQL is most readable
    inline, even when long."""
    return s.strip()


def render_cube_yaml(metric_key: str, metric: dict, tables: list[str]) -> str:
    """Compose the Baseline C YAML for one metric.

    Layout: a leading comment block describing the metric and the variant
    we lifted, then a `cubes:` list. The primary cube carries the metric's
    measure; auxiliary cubes (other tables in the metric's footprint)
    carry only dimensions + joins so the LLM has the relational context.
    """
    if metric_key not in BASELINE_C_DEFINITIONS:
        raise RuntimeError(
            f"baseline-c definition missing for metric {metric_key!r}"
        )

    spec = BASELINE_C_DEFINITIONS[metric_key]
    primary_table = spec["primary_cube"]
    if primary_table not in tables:
        # The metric's chosen Cube measure must live on a table the
        # metric actually queries. If it doesn't, we extend the cube set
        # so the YAML still anchors the measure on its table; downstream
        # readers see the union.
        tables = sorted(set(tables) | {primary_table}, key=KNOWN_TABLES.index)

    cubes: list[dict] = []
    for table in tables:
        cube_name = CUBE_NAME[table]
        is_primary = table == primary_table

        dims: list[dict] = []
        for d in DIMENSIONS_PER_TABLE[table]:
            dims.append(dict(d))
        for d in NUMERIC_DIMENSIONS_PER_TABLE.get(table, []):
            dims.append(dict(d))

        measures: list[dict] = []
        if is_primary:
            measure_entry: dict = {
                "name": spec["measure_name"],
                "type": spec["measure_type"],
                "description": spec["description"],
            }
            # `count` measures use sql: "*" (Cube convention); others
            # carry an explicit aggregate expression.
            measure_entry["sql"] = _yaml_block_string(spec["measure_sql"])
            if spec.get("filters"):
                measure_entry["filters"] = [
                    {"sql": _yaml_block_string(f["sql"])} for f in spec["filters"]
                ]
            measures.append(measure_entry)
            # Always emit a row-count measure too — Cube convention
            # makes this a free dimension-of-cardinality.
            measures.append(
                {
                    "name": "count",
                    "type": "count",
                    "sql": "*",
                }
            )
        else:
            measures.append({"name": "count", "type": "count", "sql": "*"})

        joins = []
        for j in JOIN_GRAPH[table]:
            target = j["to"]
            if target not in tables:
                continue
            joins.append(
                {
                    "name": CUBE_NAME[target],
                    "sql": j["sql"],
                    "relationship": j["relationship"],
                }
            )

        cube: dict = {
            "name": cube_name,
            "sql_table": table,
        }
        if joins:
            cube["joins"] = joins
        cube["dimensions"] = dims
        cube["measures"] = measures
        cubes.append(cube)

    # Canonical time grains live at the file head (Cube applies the
    # default set unless overridden per measure).
    doc = {
        "cubes": cubes,
    }

    header_lines = [
        f"# ClariLayer Trust Benchmark - Baseline C (Cube.dev semantic layer)",
        f"# Metric: {metric_key} ({metric.get('name', '')})",
        f"# Generated by harness/build_context_blocks.py - do not edit by hand.",
        f"#",
        f"# Variant lifted into Cube measure: {spec['variant_id']} - {spec['variant_label']!r}",
    ]
    # Per-metric notes describing the Cube approximation (when the measure
    # body diverges from the variant's actual semantics — e.g. NOT EXISTS,
    # JSON-path coalesce, multi-aggregate composition, or window functions
    # the Cube author can't natively express). Honest relabeling: header
    # describes what the measure ACTUALLY computes, not the variant it was
    # derived from.
    for note in spec.get("header_notes") or []:
        header_lines.append(f"# {note}")
    header_lines.extend([
        f"# Note: ONE canonical definition per measure. NO governance metadata,",
        f"# NO versioning, NO deprecated variants, NO fiscal-calendar override -",
        f"# this is the strongest non-ClariLayer prior art surface.",
        f"# Time grains: {', '.join(CUBE_DEFAULT_TIME_GRAINS)} (Cube.dev default; no fiscal calendar).",
        "",
    ])
    body = yaml.safe_dump(
        doc,
        sort_keys=False,
        default_flow_style=False,
        width=100,
        allow_unicode=True,
    )
    return "\n".join(header_lines) + body


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def ensure_clean_dir(path: Path) -> None:
    """Create directory and clear any existing generator output so re-runs
    leave the directory in a deterministic state. Only deletes files this
    generator owns (suffix-based)."""
    path.mkdir(parents=True, exist_ok=True)
    for f in path.iterdir():
        if f.is_file() and f.suffix in {".sql", ".yaml"}:
            f.unlink()


def load_metrics() -> list[tuple[str, dict]]:
    """Return [(metric_key, metric_dict)] sorted by metric_key for
    deterministic output ordering."""
    out: list[tuple[str, dict]] = []
    for path in sorted(METRICS_DIR.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as f:
            metric = yaml.safe_load(f)
        if not isinstance(metric, dict) or not metric.get("metric_key"):
            raise RuntimeError(f"{path}: invalid metric YAML")
        out.append((metric["metric_key"], metric))
    return out


def main() -> int:
    bare = parse_schema(SCHEMA_BARE)
    docs = parse_schema(SCHEMA_DOCS)
    metrics = load_metrics()
    if len(metrics) != 20:
        sys.stderr.write(
            f"warning: expected 20 metrics, found {len(metrics)} in {METRICS_DIR}\n"
        )

    # Sanity: all Baseline C definitions must map to known metrics.
    metric_keys = {k for k, _ in metrics}
    extra_keys = set(BASELINE_C_DEFINITIONS) - metric_keys
    missing_keys = metric_keys - set(BASELINE_C_DEFINITIONS)
    if extra_keys:
        raise RuntimeError(
            f"BASELINE_C_DEFINITIONS has stale entries not in metrics dir: {sorted(extra_keys)}"
        )
    if missing_keys:
        raise RuntimeError(
            f"BASELINE_C_DEFINITIONS missing entries for metrics: {sorted(missing_keys)}"
        )

    ensure_clean_dir(OUT_BASE_A)
    ensure_clean_dir(OUT_BASE_B)
    ensure_clean_dir(OUT_BASE_C)

    counts = {"a": 0, "b": 0, "c": 0}
    for metric_key, metric in metrics:
        tables = collect_metric_tables(metric)

        a_text = render_ddl_block(metric_key, tables, bare, BASELINE_A_HEADER)
        (OUT_BASE_A / f"{metric_key}.sql").write_text(a_text, encoding="utf-8")
        counts["a"] += 1

        b_text = render_ddl_block(metric_key, tables, docs, BASELINE_B_HEADER)
        (OUT_BASE_B / f"{metric_key}.sql").write_text(b_text, encoding="utf-8")
        counts["b"] += 1

        c_text = render_cube_yaml(metric_key, metric, tables)
        (OUT_BASE_C / f"{metric_key}.yaml").write_text(c_text, encoding="utf-8")
        counts["c"] += 1

    total = sum(counts.values())
    print(
        f"context-blocks generated: {counts['a']} baseline-a + "
        f"{counts['b']} baseline-b + {counts['c']} baseline-c = {total} files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Static context blocks (Baselines A / B / C)

Static input the benchmark harness feeds to LLMs alongside each test
question. Each baseline represents what a model sees under one context
configuration — see [`paper/trust-benchmark-v1.md`](../paper/trust-benchmark-v1.md)
for the complete methodology, including the choice of Cube.dev syntax
for Baseline C.

There are 60 files total: 20 metrics × 3 baselines (A, B, C). Baseline D
is **not** in this directory — it's a live API call, not a static file
(see "Why Baseline D isn't here" below).

## What each baseline carries

### `baseline-a/{metric_key}.sql` — Raw schema only
- The `CREATE TABLE` DDL of only the tables this metric queries.
- No comments, no column descriptions, no documentation prose.
- Verbatim from `dataset/warehouse-schema-bare.sql`.
- **Represents:** A naïve integration. Pointing an LLM at a bare
  warehouse with no semantic surface.

### `baseline-b/{metric_key}.sql` — Schema + column comments
- Same DDL set as Baseline A, but with column-level comments admitting
  the warehouse's known messiness — legacy columns, mixed timezones,
  NULL-prone fields, currency drift, refund-accounting MRR rows, etc.
- Verbatim from `dataset/warehouse-schema-documented.sql`.
- **Represents:** A well-documented warehouse following typical
  data-team hygiene — dbt projects with `docs.md`, Snowflake warehouses
  with column comments. The honest version of "what good docs look
  like" without governance.

### `baseline-c/{metric_key}.yaml` — Cube.dev semantic layer
- Cube.dev-style YAML defining the metric as ONE canonical measure, plus
  dimensions, joins (canonical FK = `customer_id`), and the Cube-default
  time grains (day / week / month / quarter / year — no fiscal calendar
  override).
- The measure lifts a non-governed variant from
  the metric's `dataset/metrics/{metric_key}.yaml` — the
  realistic surface a competent semantic-layer engineer ships *without*
  governance context.
- **No** versioning, **no** deprecated variants, **no** owner field, **no**
  approved/deprecated lifecycle state. ONE canonical definition per
  measure — that's exactly what differentiates Baseline C from
  Baseline D.
- Written in Cube.dev syntax, but results generalize to dbt Semantic
  Layer / LookML.
- **Represents:** The strongest non-ClariLayer prior art. The baseline
  the industry thinks is "good enough."

## Why Baseline D isn't here

Baseline D is the **live ClariLayer Canonical Metric API response** —
governed metric record with version, owner, lifecycle status, time
logic, canonical filters, and deprecated prior versions.

> Baseline D MUST hit the live Metric API, not a mocked JSON file or
> hand-curated shape.

The harness fetches Baseline D at run time per metric. There is no
static artifact for it in this directory. The rationale: anything else
is a credibility grenade when a third party tries to reproduce against
their own ClariLayer account.

## Regeneration

All 60 files are produced by:

```sh
python harness/build_context_blocks.py
```

Inputs:
- `dataset/warehouse-schema-bare.sql`
- `dataset/warehouse-schema-documented.sql`
- `dataset/metrics/*.yaml`

Output: `context-blocks/baseline-{a,b,c}/{metric_key}.{sql,yaml}`.

Re-run produces byte-identical output. Re-runs are expected when:
- A metric YAML in `dataset/metrics/` is added or revised.
- The warehouse schema (bare or documented) changes.
- The harness needs a fresh run (e.g. before a benchmark run).

## Table-extraction logic

For each metric, the generator parses `governed_definition.sql_template`
and every `variants[].sql_template` for `FROM <table>` and
`JOIN <table>` references. Only references matching the eight known
warehouse tables count — CTE aliases and prose mentions are ignored.
The union of referenced tables drives the DDL set in Baselines A / B
and the cube list in Baseline C.

If a metric ever queries all 8 tables, Baseline A / B include the full
schema. If it only touches `dim_customers` + `fct_subscriptions`, the
context block shows only those two `CREATE TABLE`s.

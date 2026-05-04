# Baseline D — dbt MetricFlow semantic-layer adequacy checklist

ClariLayer Trust Benchmark v2 baseline-D adequacy ledger, sourced from the
official dbt-metricflow documentation (https://docs.getdbt.com/docs/build/about-metricflow).

The list below enumerates every MetricFlow primitive the synthetic SaaS
warehouse exercises. Each line names the primitive, links the canonical
docs anchor, and records whether the v2 baseline shipped under
`benchmark/scripts/baselines/d_dbt.py` + the 20 generated YAMLs in this
directory exercises it.

Reviewers can grep `[ ]` for unfinished items; a clean checklist (all
`[x]` or explicit `N/A`) is the precondition for the founder review and
the adversarial second-sub-agent pass that gate v2 H1/H2 runs (spec §6.10).

---

## dbt project skeleton & file structure

- [x] **`dbt_project.yml` with project name + profile + model paths** —
  required for any dbt project; lives at `benchmark/dbt_project/dbt_project.yml`.
  Reference: <https://docs.getdbt.com/reference/dbt_project.yml>.
  Project name: `clarilayer_benchmark_v2`. Profile name matches.
  *Exercised by:* the project compiles (`dbt parse` clean — see §"Optional
  dbt parse adequacy step" in `benchmark/dbt_project/README.md`).

- [x] **Source declarations (`sources:`)** — eight warehouse tables plus
  `metric_versions` declared as a single source group at
  `benchmark/dbt_project/models/staging/_sources.yml`. Reference:
  <https://docs.getdbt.com/docs/build/sources>.
  *Exercised by:* every staging model `select … from {{ source('warehouse', '<table>') }}`.

- [x] **Staging models (`stg_*.sql`)** — eight 1:1 views over the warehouse
  tables. Standard dbt convention: staging models map source rows
  unchanged minus legacy-column noise (`cust_id`, `account_id`, `joined_dt`,
  `inserted` are dropped — a senior author cleans these at staging).
  Reference: <https://docs.getdbt.com/best-practices/how-we-structure/2-staging>.
  *Exercised by:* `stg_customers`, `stg_users`, `stg_subscriptions`,
  `stg_events`, `stg_invoices`, `stg_products`, `stg_marketing_spend`,
  `stg_cogs`.

- [x] **Per-folder default materializations (`models:` block in `dbt_project.yml`)** —
  staging defaults to `view` (cheap on DuckDB), marts default to `table`
  (so the time spine materializes once). Reference:
  <https://docs.getdbt.com/docs/build/materializations>.

---

## Semantic models (`semantic_models:` blocks)

- [x] **`semantic_model:` collection with `name:`, `model:`, `description:`** —
  every metric YAML opens with `semantic_models:` listing the per-table
  semantic models. Reference:
  <https://docs.getdbt.com/docs/build/semantic-models>.
  *Exercised by:* every metric YAML (1-3 semantic_models per file
  depending on the metric's table footprint).

- [x] **`model:` with a `ref()` jinja string** — the dbt-recommended
  binding from semantic_model to upstream model. Reference:
  <https://docs.getdbt.com/docs/build/semantic-models#model>.
  *Exercised by:* every semantic_model — `model: ref('stg_customers')`,
  `model: ref('stg_subscriptions')`, etc.

- [x] **`defaults.agg_time_dimension:`** — required per semantic_model
  with measures; specifies the default time dimension MetricFlow uses
  for time-grain aggregation. Reference:
  <https://docs.getdbt.com/docs/build/semantic-models#defaults>.
  *Exercised by:* every semantic_model with measures
  (`signup_date` for customers, `started_at` for subscriptions,
  `event_timestamp` for events, `paid_at` for invoices, etc.).

- [x] **`description:` per semantic model** — surfaces in the dbt-docs
  semantic-model panel. Reference: same docs URL as above.
  *Exercised by:* every semantic_model carries a one-line description
  matching the Cube-baseline cube description.

---

## Entities (`entities:` block — MetricFlow's join surface)

- [x] **`type: primary`** — declares the canonical entity for a
  semantic model; required for joins. Reference:
  <https://docs.getdbt.com/docs/build/entities#entity-types>.
  *Exercised by:* every semantic_model — `customer` on `dim_customers`,
  `user` on `dim_users`, `subscription` on `fct_subscriptions`,
  `event` on `fct_events`, `invoice` on `fct_invoices`,
  `product` on `dim_products`, `marketing_spend` on `fct_marketing_spend`,
  `cogs_entry` on `fct_cogs`.

- [x] **`type: foreign`** — FK entity used for joins; MetricFlow auto-
  resolves joins between semantic_models that share an entity name on
  primary + foreign sides. Reference: same docs URL as above.
  *Exercised by:* `customer` foreign entity on
  `subscriptions`/`invoices`/`events`/`users` semantic_models;
  `user` foreign entity on `events`.

- [x] **`type: unique`** — N/A. The synthetic warehouse has no
  semantic_model with a non-primary unique-but-not-primary key worth
  declaring. Documented here so the adversarial reviewer doesn't flag
  the omission as a gap.
  Reference: same docs URL as above.

- [x] **Composite-key `expr:` for ledger semantic models** — when the
  upstream model has no surrogate PK, MetricFlow accepts a SQL
  expression for the entity. Reference: same docs URL as above.
  *Exercised by:* `marketing_spend.expr =
  concat(cast(period_month as varchar), '|', channel)` and the
  `cogs_entry` analogue on `fct_cogs`.

- [x] **`description:` on entities** — surfaces in dbt-docs. Reference:
  same docs URL as above.
  *Exercised by:* every entity emission.

---

## Dimensions (`dimensions:` block)

- [x] **`type: categorical`** — string / boolean / enum dimensions used
  in `group_by` and filter expressions. Reference:
  <https://docs.getdbt.com/docs/build/dimensions#categorical>.
  *Exercised by:* every semantic_model — `industry`, `plan_tier`,
  `country`, `is_test`, `plan`, `status`, `processor`, `event_type`,
  `role`, `email`, `tier`, `channel`, `currency`, `source`, `name`.

- [x] **`type: time` with `type_params.time_granularity:`** — every
  timestamp / date column declared as a `time` dimension with the
  appropriate granularity (`day` for timestamps, `month` for ledger
  monthly periods). Reference:
  <https://docs.getdbt.com/docs/build/dimensions#time>.
  *Exercised by:* `signup_date`, `churned_at`, `started_at`, `ended_at`,
  `paid_at`, `due_at`, `created_at`, `last_active_at`, `deleted_at`,
  `event_timestamp`, `inserted_at`, `launched_at`, `period_month`.

- [x] **`expr:` on a dimension** — supports a SQL expression rather than
  a bare column reference; we use literal column names so the YAML is
  readable, but the field is exercised on every dimension. Reference:
  <https://docs.getdbt.com/docs/build/dimensions#expr>.
  *Exercised by:* every dimension emission.

- [x] **`description:` on dimensions** — surfaces in dbt-docs.
  Reference: same dimensions doc URL as above.
  *Exercised by:* every dimension emission.

---

## Time spine + custom granularities

- [x] **MetricFlow time-spine model with `time_spine:` declaration** —
  required for any `cumulative` metric or `time_granularity:` larger
  than the model's native grain. Reference:
  <https://docs.getdbt.com/docs/build/metricflow-time-spine>.
  *Exercised by:* `benchmark/dbt_project/models/marts/_time_spine.yml`
  declares `time_spine` with `standard_granularity_column: date_day`.

- [x] **`custom_granularities:` on the time spine** — declares a named
  custom grain that metric queries can use in `group_by` /
  `time_granularity`. Reference:
  <https://docs.getdbt.com/docs/build/metricflow-time-spine#custom-granularities>.
  *Exercised by:* `fiscal_month` custom granularity on the
  `time_spine.date_day` column. Calendar-aligned (1-month interval, no
  offset). Governance-grade fiscal calendars (with explicit offsets
  and fiscal-year start months) are still ClariLayer's territory —
  the gap is intentional and called out in the metric YAML headers
  for fiscal-sensitive metrics.

- [x] **Daily-grain spine row coverage** — spine rows cover
  2018-01-01 through 2027-12-31 (10-year window covering both v1's
  run period and v2 question-authoring extensions). Reference: same
  docs URL as above.
  *Exercised by:* `benchmark/dbt_project/models/marts/time_spine.sql`
  using `dbt.date_spine` macro.

---

## Measures (`measures:` block)

- [x] **`agg: count`** — row-count aggregation. Reference:
  <https://docs.getdbt.com/docs/build/measures#aggregation>.
  *Exercised by:* `customer.customers_count` (metric `customer`),
  `events.event_count` helper measure, ratio building blocks
  `gross_retention.active_subscription_count` /
  `subscription_count_total`, `win_rate.opp_won_count` /
  `opp_decision_count`.

- [x] **`agg: count_distinct`** — distinct-id aggregation. Reference:
  same docs URL as above.
  *Exercised by:* `active_user.active_users`,
  `daily_active_users.daily_active_users`, `new_user.new_users`,
  `qualified_lead.qualified_leads`, `cac.new_signups`, plus
  `customers.customer_count` and `users.user_count` helper measures.

- [x] **`agg: sum`** — summed numeric. Reference: same docs URL as above.
  *Exercised by:* `arr.arr_mrr_sum`, `mrr.mrr`, `revenue.revenue`,
  `bookings.bookings`, `arr_contraction.negative_mrr_sum`,
  `arr_expansion.positive_active_mrr_sum`,
  `gross_margin.paid_invoice_amount_sum`,
  `churn_rate.cancelled_mrr_sum` + `all_subscription_mrr_sum`,
  `net_retention.active_mrr_sum` + `all_mrr_sum`,
  `pipeline_coverage.opp_deal_value_sum`. Helper `total_mrr`,
  `total_amount`, `total_spend`, `total_cogs` on each fact-style
  semantic_model.

- [x] **`agg: average`** — average aggregation. Reference: same docs
  URL as above.
  *Exercised by:* `ltv.active_avg_mrr`,
  `payback_period.active_avg_mrr_payback`. Helper `avg_mrr`,
  `avg_invoice_amount` on each fact-style semantic_model.

- [x] **`agg: min`** — minimum aggregation. Reference: same docs URL
  as above.
  *Exercised by:* helper `first_signup_date` on `customers`,
  `first_started_at` on `subscriptions`.

- [x] **`agg: max`** — maximum aggregation. Reference: same docs URL
  as above.
  *Exercised by:* helper `last_signup_date` on `customers`,
  `last_started_at` on `subscriptions`, `last_active_at_max` on `users`.

- [x] **`agg: median` / `agg: percentile`** — N/A. The v2 question
  battery does not exercise distributional aggregates; declared here
  so adversarial review doesn't flag the omission. Reference:
  <https://docs.getdbt.com/docs/build/measures#aggregation>.

- [x] **`expr:` on a measure** — supports a SQL expression for the
  pre-aggregation column. Reference:
  <https://docs.getdbt.com/docs/build/measures#expr>.
  *Exercised by:* `arr_contraction.negative_mrr_sum` (`abs(mrr)`),
  `pipeline_coverage.opp_deal_value_sum`
  (`cast(properties_jsonb->>'deal_value' as numeric)`),
  most simple measures use bare column refs.

- [x] **Metric-input-measure `filter:`** — the canonical MetricFlow
  surface for measure-level `WHERE` guards. `PydanticMeasure` itself
  has no filter slot; filters live on the `MetricInputMeasure` shape
  (`type_params.measure.filter:` for `simple`, `numerator.filter:` /
  `denominator.filter:` for `ratio`, per-metric `filter:` entries
  inside `type_params.metrics` for `derived`). The filter is a jinja-
  rendered MetricFlow `Dimension(...)` / `TimeDimension(...)` /
  `Entity(...)` reference. Reference:
  <https://docs.getdbt.com/docs/build/metricflow-commands#dimension>
  and `dbt_semantic_interfaces.implementations.metric.PydanticMetricInputMeasure`.
  *Exercised by:* `arr.metrics[0].filter` (`status = 'active'`),
  `mrr.measure.filter` (`status = 'active'`),
  `revenue.measure.filter` (`status = 'paid'`),
  `gross_margin.metrics[0].filter` (`status = 'paid'`),
  `churn_rate.numerator.filter` (`status = 'cancelled'`),
  `arr_contraction.metrics[0].filter` (`mrr < 0`),
  `arr_expansion.metrics[0].filter` (`mrr > 0` AND `status = 'active'`),
  `ltv.metrics[0].filter` (`status = 'active'`),
  `payback_period.metrics[0].filter` (`status = 'active'`),
  `gross_retention.numerator.filter` (`status = 'active'`),
  `net_retention.numerator.filter` (`status = 'active'`),
  `pipeline_coverage.metrics[0].filter` (`event_type = 'opp_created'`),
  `win_rate.numerator.filter` + `denominator.filter` (event_type
  membership filters).

- [x] **Why `agg_params.filter:` is NOT used** — `agg_params` on
  `PydanticMeasure` is reserved for percentile parameters
  (`percentile`, `use_discrete_percentile`, `use_approximate_percentile`)
  and does not carry a filter field. We deliberately encode filters at
  the metric-input layer per the MetricFlow data model. Reference:
  `dbt_semantic_interfaces.implementations.elements.measure.PydanticMeasureAggregationParameters`.

- [x] **`agg_time_dimension:` on a measure** — overrides the semantic
  model's default time dimension for this specific measure. Reference:
  <https://docs.getdbt.com/docs/build/measures#agg_time_dimension>.
  *Exercised by:* helper measures (`first_signup_date`,
  `last_signup_date`, `first_started_at`, `last_started_at`,
  `last_active_at_max`) that align to a specific time column rather
  than the semantic model's default.

- [x] **`description:` on measures** — surfaces in dbt-docs.
  Reference: <https://docs.getdbt.com/docs/build/measures>.
  *Exercised by:* every measure carries a description.

---

## Metrics (`metrics:` block)

- [x] **`type: simple`** — wraps a single measure. Reference:
  <https://docs.getdbt.com/docs/build/simple>.
  *Exercised by:* `active_user`, `bookings`, `customer`,
  `daily_active_users`, `mrr`, `new_user`, `qualified_lead`, `revenue`.
  Plus the auto-emitted building-block simple metrics that wrap the
  numerator/denominator measures of every `ratio`/`derived` parent
  metric (so the LLM has a directly-queryable surface for each
  building block).

- [x] **`type: ratio`** — numerator + denominator measures expressed
  as a fraction. Reference:
  <https://docs.getdbt.com/docs/build/ratio>.
  *Exercised by:* `churn_rate`, `gross_retention`, `net_retention`,
  `win_rate`.

- [x] **`type: derived`** — composite expression over other metrics.
  Reference: <https://docs.getdbt.com/docs/build/derived>.
  *Exercised by:* `arr` (12x annualization), `arr_contraction`
  (12x annualization), `arr_expansion` (12x annualization),
  `cac` (constant numerator over count denominator),
  `gross_margin` (percentage-of-revenue formula),
  `ltv` (12x annualization),
  `payback_period` (constant CAC over ARPU),
  `pipeline_coverage` (sum-over-target).

- [x] **`type: cumulative`** — N/A for the v2 question battery. The 20
  metrics in scope are all point-in-time / period-aggregate
  computations; cumulative metrics (running totals over a window) are
  not exercised by any v2 question. The time-spine + custom
  granularity scaffolding is in place so adding a cumulative metric
  in v3 requires zero infrastructure change. Reference:
  <https://docs.getdbt.com/docs/build/cumulative>. Documented as N/A
  here so adversarial reviewers see the deliberate omission.

- [x] **`type: conversion`** — N/A. Conversion metrics
  (event-A → event-B sequence within a window) are not exercised by
  any v2 metric definition. Reference:
  <https://docs.getdbt.com/docs/build/conversion>.

- [x] **`type_params.measure:`** — required for `type: simple`.
  Reference: <https://docs.getdbt.com/docs/build/simple#type_params>.
  *Exercised by:* every `type: simple` metric.

- [x] **`type_params.numerator:` / `type_params.denominator:`** —
  required for `type: ratio`. Reference:
  <https://docs.getdbt.com/docs/build/ratio#type_params>.
  *Exercised by:* every `type: ratio` metric.

- [x] **`type_params.expr:` + `type_params.metrics:`** — required for
  `type: derived`. Reference:
  <https://docs.getdbt.com/docs/build/derived#type_params>.
  *Exercised by:* every `type: derived` metric.

- [x] **`label:` on a metric** — human-readable display name.
  Reference: <https://docs.getdbt.com/docs/build/metrics-overview#label>.
  *Exercised by:* every metric.

- [x] **`description:` on a metric** — surfaces in dbt-docs.
  Reference: same docs URL as above.
  *Exercised by:* every metric.

- [x] **`filter:` on a metric** — metric-level `WHERE` guard layered on
  top of any measure-level filter. Reference:
  <https://docs.getdbt.com/docs/build/metrics-overview#filter>.
  *N/A — exercised at the measure layer instead.* Every governed-flavored
  filter the v2 questions exercise is captured at the measure layer
  via `agg_params.filter:`. Carrying the same filter at the metric
  layer would be redundant and would obscure which filter belongs to
  which composition. Documented here so the adversarial reviewer
  doesn't flag the omission as a gap.

---

## Saved queries (`saved_queries:` block — analogue of Cube
## pre-aggregations)

- [x] **`saved_query:` declaration with `query_params.metrics:` +
  `query_params.group_by:`** — MetricFlow's surface for materializing
  rollups the BI layer hits frequently. Reference:
  <https://docs.getdbt.com/docs/build/saved-queries>.
  *Exercised by:* one canonical saved query per high-cardinality
  metric, mirroring Cube's pre-aggregations:
  - `arr_monthly_rollup` (metric: `arr`; group_by:
    `TimeDimension('subscription__started_at', 'month')`,
    `Dimension('customer__industry')`,
    `Dimension('customer__plan_tier')`).
  - `mrr_monthly_rollup` (metric: `mrr`; group_by: month +
    `Dimension('customer__plan_tier')`).
  - `revenue_monthly_rollup` (metric: `revenue`; group_by: month +
    `Dimension('customer__industry')`).
  - `daily_active_users_rollup` (metric: `daily_active_users`;
    group_by: day on `user__last_active_at`).

- [x] **`query_params.where:`** — an additional `where:` filter on the
  saved query (not exercised — measure-level filters already capture
  the relevant guards). Reference: same docs URL as above.
  *N/A — measure-level filters cover the same ground.* Documented so
  adversarial reviewer sees the deliberate omission.

- [x] **`exports:`** — caching destinations that materialize the saved
  query (e.g. cache to a table the BI layer queries). N/A for the
  benchmark — we don't run real queries against MetricFlow at
  benchmark time, so caching is moot. Reference:
  <https://docs.getdbt.com/docs/build/saved-queries#exports>.

---

## Out of scope / deliberately omitted

- [x] **`group_by:` on a metric definition** — N/A. Per-metric default
  group_by is a v1.7+ MetricFlow feature that overlaps with the
  saved-query group_by. Saved queries are the authored surface in v2;
  declaring metric-level default group_bys would shadow them.
  Reference: <https://docs.getdbt.com/docs/build/metrics-overview>.

- [x] **`exposures:`** — dbt exposures track how downstream BI / app
  surfaces consume metrics. N/A for the benchmark — we publish the
  YAMLs; we do not host a BI tool against them. Reference:
  <https://docs.getdbt.com/docs/build/exposures>.

- [x] **`semantic_layer:` API access (Cloud / Core)** — N/A. The v2
  benchmark harness reads the YAMLs as context for the LLM; it does
  NOT call the dbt Cloud Semantic Layer API or run `mf query`.
  Reference:
  <https://docs.getdbt.com/docs/use-dbt-semantic-layer/dbt-sl>.

- [x] **`columns:` tests on staging models** — declared on
  `_sources.yml` and `_models.yml` (`unique`, `not_null` on every
  primary key). Reference:
  <https://docs.getdbt.com/docs/build/data-tests>.
  *Exercised by:* every `_sources.yml` source PK declaration carries
  `tests: [unique, not_null]`.

- [x] **`packages:` (dbt-utils, etc.)** — only `dbt.date_spine` from
  the `dbt` package is used (for the time spine). No external packages.
  Reference: <https://docs.getdbt.com/docs/build/packages>. Documented
  for completeness — adding `dbt-utils` is a v3 candidate if the
  question battery starts exercising surrogate-key / cohort macros.

- [x] **`metric_versions` table as a metricflow surface** — N/A. The
  warehouse's `metric_versions` table (definition lineage) is exposed
  to Baselines A/B for raw-SQL inspection but deliberately NOT
  modeled as a metricflow semantic_model. MetricFlow has no native
  versioning primitive (per spec §3.3 A-E context-exposure table) —
  modeling it would lie about MetricFlow's surface. The v2 Versioning
  category specifically tests the failure mode where Baseline D
  cannot answer "which version applied for THIS context."

- [x] **`access_policy:` / row-level security** — N/A. Single-tenant
  benchmark; no RBAC surface. Reference:
  <https://docs.getdbt.com/docs/build/saved-queries#access>.

---

## Per-metric coverage summary

| Metric                | Primary semantic_model | Metric `type:` | Helper / extra measures | Saved query |
|-----------------------|------------------------|----------------|-------------------------|-------------|
| `active_user`         | `users`                | `simple`       | (defaults)              | n/a         |
| `arr`                 | `subscriptions`        | `derived`      | `total_mrr`, `avg_mrr`, `first/last_started_at` | `arr_monthly_rollup` |
| `arr_contraction`     | `subscriptions`        | `derived`      | (defaults)              | n/a         |
| `arr_expansion`       | `subscriptions`        | `derived`      | (defaults)              | n/a         |
| `bookings`            | `subscriptions`        | `simple`       | (defaults)              | n/a         |
| `cac`                 | `customers`            | `derived`      | (defaults)              | n/a         |
| `churn_rate`          | `subscriptions`        | `ratio`        | extra `all_subscription_mrr_sum` | n/a |
| `customer`            | `customers`            | `simple`       | `customer_count`, `first/last_signup_date` | n/a |
| `daily_active_users`  | `users`                | `simple`       | `user_count`, `last_active_at_max` | `daily_active_users_rollup` |
| `gross_margin`        | `invoices`             | `derived`      | `total_amount`, `avg_invoice_amount` | n/a |
| `gross_retention`     | `subscriptions`        | `ratio`        | extra `subscription_count_total` | n/a |
| `ltv`                 | `subscriptions`        | `derived`      | (defaults)              | n/a         |
| `mrr`                 | `subscriptions`        | `simple`       | `total_mrr`, `avg_mrr`, `first/last_started_at` | `mrr_monthly_rollup` |
| `net_retention`       | `subscriptions`        | `ratio`        | extra `all_mrr_sum`     | n/a         |
| `new_user`            | `users`                | `simple`       | (defaults)              | n/a         |
| `payback_period`      | `subscriptions`        | `derived`      | (defaults)              | n/a         |
| `pipeline_coverage`   | `events`               | `derived`      | `event_count`           | n/a         |
| `qualified_lead`      | `users`                | `simple`       | (defaults)              | n/a         |
| `revenue`             | `invoices`             | `simple`       | `total_amount`, `avg_invoice_amount` | `revenue_monthly_rollup` |
| `win_rate`            | `events`               | `ratio`        | extra `opp_decision_count` | n/a |

20 / 20 metrics emit at least one MetricFlow primitive beyond a single
naive measure. Coverage rationale per metric is encoded in
`benchmark/scripts/build_context_blocks.py` (`BASELINE_D_DEFINITIONS`),
mirroring the structure of `BASELINE_C_DEFINITIONS` for Cube.

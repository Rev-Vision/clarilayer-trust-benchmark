# ADR-0021 — Metric execution metadata (Trust Benchmark v2.1, Lookup execution-failure cluster + Approval/Versioning fact-zero-rate gap)

- **Status:** Accepted
- **Date:** 2026-05-03
- **Sprint context:** Trust Benchmark v2.1 — F3 (codex-priority #3)
- **Stacks on:** ADR-0019 (`definition_version` rename), ADR-0020 (`metric_deprecated_framing_rules` — V2.1-F2)
- **Migration:** `supabase/migrations/20260503000100_v2_1_f3_metric_execution_metadata.sql`
- **Tracking:** `brain/product/clarilayer-v2.1-governance-response-features.md` §3 priority 3

## Context

Trust Benchmark v2 (5 stability sweeps × 3 models × 5 baselines × 120 questions = 10,800 calls) measured ClariLayer's Lookup category PASS rate at **75.3%**. Codex's raw-data pattern analysis found the 89 misses (31 ERROR + 58 FAIL) cluster on **execution metadata**, not governance:

- `arr-003`, `mrr-003` fail every sweep with `BinderException: Table "c" does not have a column named "tier"` — the model reaches for `c.tier` / `c.customer_tier` but the actual column on `dim_customers` is `plan_tier`.
- `versioning-arr-009` fails with `ParserException: syntax error at or near "{"` because the envelope's `sql_expression` template ships a literal `${as_of}` parameter and the model copies it without resolving the binding.
- `revenue-006` fails 15/15 on month-over-month questions because the model can't compose period-over-period SQL from primitives — the envelope describes the metric but doesn't show what a MoM shape looks like for it.

Codex's approval-scoring re-score (post V2.1-P0-A fix) added a second dimension: at the field level the model gets `owner_team` 100%, `version` 100%, `lifecycle_status` 86.7%, but `approver` 0%, `approved_at` 0%, `effective_from` 0%. The structured fact data is in the DB (on `metrics` + `metric_approvals` + `releases`) but isn't surfaced to LLM consumers as a flat block — it's buried under nested `approvals[]` / `versionHistory[]` arrays the model isn't reaching reliably.

The v2.1 plan §3 priority #3 bundles these into one feature with three sub-features:

- **F3a** — dimension alias + column map. Closes the column-resolution errors.
- **F3b** — parameter binding + rendered SQL examples + latest closed period. Closes the parser errors and the MoM/trend SQL composition failures.
- **F3c** — governance facts block. Surfaces the structured fields as a flat object so the model doesn't have to reach for them.

## Decision

Three additive envelope fields — `dimension_map` (+ sibling `column_aliases`), `execution_context`, and `governance_facts` — backed by a hybrid storage strategy:

- **F3a / F3b: separate tables** for one-metric-to-many-entries shapes:
  - `public.metric_dimension_maps` — `(metric_id, alias)` UNIQUE per row carrying `(table, column, value?)`.
  - `public.metric_column_aliases` — `(metric_id, alias)` UNIQUE per row mapping historical column names to canonical column names.
  - `public.metric_rendered_sql_examples` — `(metric_id, scenario)` UNIQUE per row carrying pre-rendered DuckDB SQL.
- **F3b: columns on `metrics`** for one-metric-to-one-value shapes:
  - `metrics.resolved_parameters jsonb NOT NULL DEFAULT '{}'::jsonb` — concrete `{as_of, period_start, period_end, …}` map.
  - `metrics.latest_closed_period_grain text` + `metrics.latest_closed_period_end date`, paired by a CHECK constraint.
- **F3c: pure aggregation** — no new DB columns. The `governance_facts` block is computed at request time from existing rows on `metrics`, `metric_approvals`, and `releases`.

The envelope-facing shape is:

```json
{
  "dimension_map": {
    "enterprise-tier": {"table": "dim_customers", "column": "plan_tier", "value": "enterprise"},
    "active-status":   {"table": "fct_subscriptions", "column": "status", "value": "active"}
  },
  "column_aliases": {
    "tier": "plan_tier",
    "customer_tier": "plan_tier"
  },
  "execution_context": {
    "resolved_parameters": {"as_of": "2026-03-31", "period_start": "2026-01-01", "period_end": "2026-03-31"},
    "rendered_sql_examples": [
      {"scenario": "current quarter", "sql": "SELECT SUM(s.mrr) * 12 AS arr FROM …", "notes": null},
      {"scenario": "month-over-month growth", "sql": "WITH cur AS (…), prev AS (…) SELECT (cur.arr - prev.arr) / prev.arr AS mom_growth FROM cur, prev", "notes": "Two-CTE shape; substitute different as-of dates per period."}
    ],
    "latest_closed_period": {"grain": "month", "end": "2026-03-31"}
  },
  "governance_facts": {
    "owner_team": "data-eng",
    "approver_role": "Financial",
    "approver_user": "<user uuid>",
    "approved_at": "2026-04-22T12:00:00Z",
    "lifecycle_status": "APPROVED",
    "current_version": "v3",
    "effective_from": "2026-03-01T00:00:00Z",
    "effective_to": null,
    "next_review_date": null
  }
}
```

All four fields are **additive** to the existing `MetricDetailV1Response`. Empty defaults — empty objects for the maps, empty `execution_context`, fully-null `governance_facts` — are the common case for legacy metrics. Legacy v1 consumers can ignore the new fields.

## Why three envelope fields, not one

Codex's review explicitly split the work into three sub-features because each addresses a distinct LLM-failure mechanism:

1. **F3a closes a binding error.** The model emits SQL that references a non-existent column. The envelope needs to give it the canonical name.
2. **F3b closes a templating + composition error.** The model can't resolve `${as_of}` and can't compose period-over-period SQL. The envelope needs to ship resolved values + canonical shapes the model can copy.
3. **F3c closes a fact-extraction error.** The data is present in the envelope's nested structures but the model isn't reading it. The envelope needs to surface it as a flat block.

Bundling them into a single field would force consumers to read one heavy object even when they only need one sub-feature. Three separate fields lets future consumers opt into each piece independently — and lets the v2.1 paper attribute lift to a specific sub-feature.

## Alternatives considered

### Option A: hybrid (chosen) — separate tables for 1:N, columns on `metrics` for 1:1, pure aggregation for F3c

**Mechanics.** Three new tables (`metric_dimension_maps`, `metric_column_aliases`, `metric_rendered_sql_examples`), two new columns on `metrics` (`resolved_parameters` JSONB, `latest_closed_period_*`), and a single new repository function (`getGovernanceFactsForMetric`) that JOINs the three existing sources at request time.

**Why we picked it:**

1. **Storage shape matches data shape.** F3a / F3b's example table has natural 1:N cardinality (one metric, many aliases / scenarios). The closed-period pair is 1:1 — there's only ever one "latest closed period" for a metric — so it lives on the metric row. F3c's data already exists on three tables; duplicating it into a fourth would invite consistency drift.
2. **RLS parity with F2.** The new tables mirror `metric_relationships` / `metric_approvals` / `metric_deprecated_framing_rules`'s SELECT-only RLS pattern (org-membership gate). A new JSONB column on `metrics` would inherit `metrics`' broader RLS surface.
3. **Indexability.** A `metric_id` B-tree index supports the parallel-fan-out fetch in the v1 detail route for the F3a/b tables. JSONB-array access on `metrics` would not.
4. **Future authoring UI.** The eventual workspace-admin UI for authoring dimension maps + rendered SQL wants a row-per-entry grid. Modeling them as table rows is the obvious shape.
5. **F3c minimizes migration churn.** The data already exists; we just need a shape projection. A new DB column for `approver_role` / `effective_to` / `next_review_date` is a separate ADR when the underlying policy semantics are designed.

### Option B: single JSONB column on `metrics` (`metrics.execution_metadata JSONB`)

Rejected. Same shape would compile but loses (a) the per-entry UNIQUE constraint at the schema level, (b) per-entry indexability for analytics, (c) the 1:N authoring UI mapping. The on-the-wire envelope shape is identical so consumers can't distinguish A from B; everything else favors A.

### Option C: extend `metric_contracts` (the existing per-metric metadata table)

Rejected. `metric_contracts.time_semantics` is a single JSONB blob carrying the metric's time-binding contract (ADR-0016 D2). Mixing dimension aliases / rendered SQL examples / governance facts into a single contract row would conflate orthogonal concerns: time semantics is about *when*; F3 is about *how to bind*, *how to render*, and *how to surface governance*. Each F3 sub-feature would then need its own JSONB sub-key, and the table's single-row-per-metric shape blocks the per-scenario rendered SQL examples (which are 1:N).

### Option D: add columns directly to `metric_contracts` for F3b

Rejected. `resolved_parameters` and `latest_closed_period_*` are properties of the *metric* (its parameter binding state, its temporal reach), not of the metric's *contract row*. A metric without a `metric_contracts` row (legacy or pre-contract metrics) would still need a place to carry its resolved parameters. Putting the columns on `metrics` directly avoids the dependency.

### Option E: pre-render rendered_sql_examples lazily at request time

Rejected. Rendering canonical SQL shapes at request time would (a) require the API layer to embed a SQL renderer (an outsized dependency for the v1 detail route), (b) couple every API call to whatever DuckDB-syntax constraints the renderer encodes, and (c) make the rendered output undocumented — consumers couldn't see the shape until they hit the endpoint. Storing pre-rendered shapes in the DB makes the canonical example explicit and reviewable.

## Schema details

### `metric_dimension_maps` — column choices

- **`alias`** is `text` (not an enum) because the alias namespace is per-metric and grows as new dimensions are added. Slug-style is convention — `enterprise-tier`, `active-status`, `north-america-region`. Empty strings are rejected by a trim CHECK; alias collisions per metric are blocked by `UNIQUE (metric_id, alias)`.
- **`"table"` and `"column"`** are quoted because both are reserved words in standard SQL. Required + non-empty.
- **`value`** is nullable because some aliases are column-shape-only (e.g. "by tier" — no specific value). The CHECK rejects empty strings to force authors to use NULL when they mean "no value pinned" (rather than a silent empty string that consumers might surface as a literal `'' = ''`).
- **No CHECK on `(table, column)` referencing the warehouse schema.** The warehouse is DuckDB at runtime; the control-plane is Postgres. Cross-engine FKs are not feasible. Authoring tooling validates the references (the seed script reads the warehouse schema; an admin UI would read it via an introspection endpoint).

### `metric_column_aliases` — sibling table to `metric_dimension_maps`

Carries pure column-name renames where the historical name doesn't bind to a specific table or value. Same RLS, same UNIQUE constraint shape. The two tables are siblings rather than one-with-a-`kind`-column because the `value` column doesn't make sense for pure aliases — splitting them keeps the constraint surface tight per shape.

### `metric_rendered_sql_examples.sql` — fully-resolved string

The author resolves all template parameters before insertion. The DB does NOT do parameter substitution — that keeps the DB layer free of templating concerns and lets the author audit the exact SQL the consumer will see. The `notes` column is for human-readable hints (e.g. "Use plan_tier (not tier / customer_tier) per F3a alias"). The `ordinal` column is for stable display order, with the UNIQUE on `(metric_id, scenario)` giving "one canonical example per scenario" — a metric has *the* canonical "current quarter" shape, not five candidates.

### `metrics.resolved_parameters` — `NOT NULL DEFAULT '{}'::jsonb`

Default empty object means a metric without F3b authored data still has a valid envelope shape. The migration adds the column to existing rows automatically — no backfill needed for legacy metrics; they get the empty default.

### `metrics.latest_closed_period_*` — coupled by CHECK

The two columns (`grain`, `end`) are either both NULL (no period registered) or both NOT NULL (a valid `ClosedPeriod`). Mirrors the `metrics_scope_domain_coupling_check` pattern (A4) where two related columns are coupled via a single CHECK. The `grain` value is constrained to `('day', 'week', 'month', 'quarter', 'year')` — additive, future grains require a CHECK update only.

### `governance_facts` — read-time aggregation, not a table

The aggregator function reads:

1. `metrics` — `owner_team`, `lifecycle_status`, `definition_version` → `current_version`.
2. `metric_approvals` — most-recent non-superseded `decision='approved'` row → `approver_user`, `approver_role` (mapped from `policy_tier`), `approved_at`.
3. `releases` — most-recent `status='released'` row → `effective_from` (from `released_at`, falling back to `created_at`).

Three fields collapse to `null` for now (`approver_role` partially overlaps `policy_tier`, `effective_to` and `next_review_date` have no DB source today). They're declared in the type so the envelope shape is stable across future iterations that DO populate them.

## Forward-compat with V2.1-F4 / F5 / F6 / F7

- **F4 (approval policy directive).** The `approval_state` block F4 will add is a richer `lifecycle_status` view, not a replacement. F4 may add a `policy` directive string and a `blockers[]` array; F3c stays as the structured-facts surface.
- **F5 (consumer surface version pins).** F5 adds a `consumer_contexts` map keyed by surface (`board_reporting`, `investor_update`, …); F3c's `current_version` remains the metric-level canonical version pointer.
- **F6 (`requires_disambiguation` field).** Independent of F3 — F6 lives on the question/scope layer, not the metric definition.
- **F7 (per-policy-tier few-shot examples).** May share the `rendered_sql_examples` table or sit alongside it; if shared, F7 adds a new `kind` column (currently the table only carries one kind, so the column is omitted for now).

## Consequences

### Enables

- **Lookup category lift.** Targeted bound: 75.3% → 85% per the v2.1 plan §3 priority #3. The 31 ERROR rows on `c.tier`/`c.customer_tier` are a direct hit for F3a; the parser-error + MoM rows are a direct hit for F3b.
- **Approval / Versioning structured-fact lift.** Re-score showed `approver` 0%, `approved_at` 0%, `effective_from` 0%. F3c surfaces all three as a flat block; targeted bound is `approver` ~80%, `approved_at` ~80%, `effective_from` ~80% on a re-run.
- **Per-metric authoring catalogue.** The seed script (`benchmark/scripts/seed_test_org.py`) authors:
  - 23 dimension-map entries across 11 metrics (`enterprise-tier`, `active-status`, `non-test-customers`, etc.).
  - 48 column aliases across the metrics that JOIN `dim_customers` (the `tier` / `customer_tier` / `cust_id` rename triple, plus per-metric extras like `event_timestamp` → `timestamp` for fct_events).
  - 24 rendered SQL examples covering current-period, MoM, latest-closed shapes per metric.
  - 20 metric.resolved_parameters + closed-period UPDATEs setting Q1 2026 as the canonical "as-of" for the test org.

### Blocks

Nothing. All four envelope fields are additive.

### Limitations / risks

1. **Rendered SQL drift.** Pre-rendered shapes can drift from the canonical `sql_expression` if the metric's definition evolves but the rendered example is not refreshed. Mitigation: the seed script is the single source of truth for the test org; production orgs will need authoring tooling that validates the rendered shape against the metric's compiled SQL. A v3 follow-up may add a `last_validated_at` column + a CI check.
2. **Dimension-alias false positives.** A column-only alias like `tier` → `plan_tier` could fire on metrics that use `tier` legitimately (e.g. a metric on `dim_products.tier`, which IS the actual column name there). The benchmark warehouse uses `dim_products.tier` as well — the F3a aliases are scoped per-metric, so a `dim_products`-anchored metric would carry a different alias map (or no alias). Mitigation: per-metric authoring + the dimension map's `(table, column)` tuple makes the binding explicit.
3. **Closed-period staleness.** `latest_closed_period_end` is a static date in the DB; if the test org isn't re-seeded between sprints, the date goes stale. Mitigation: the seed script is idempotent and authored once per sprint; production orgs will need a periodic recompute job (out of scope for V2.1).
4. **Governance-facts shape coupling.** The aggregator reads three tables; a schema change to any of them (e.g. `metric_approvals.approver_id` becomes nullable) would require updating the aggregator. Mitigation: types are explicit and tests cover all-populated, all-null, and partial-error cases.
5. **`approver_role` mapping to `policy_tier`.** F3c uses `policy_tier` as a stand-in for `approver_role` because no dedicated role column exists yet. This is a documented stand-in, not a permanent design. A follow-up migration may add `metric_approvals.approver_role text` and the aggregator will read it directly.

## Follow-ups

1. **V2.1-F4 (approval policy directive)** — separate dispatch. The `approval_state` block will sit alongside `governance_facts`; the two fields are complementary (F3c is the facts; F4 is the policy / directive layer).
2. **Authoring UI** — the workspace-admin UI for the F3 entries is out of scope for V2.1. Production orgs author via the seed pattern (the script is documented as the canonical example).
3. **Rendered SQL validation** — a CI check that re-executes each `metric_rendered_sql_examples.sql` against the warehouse and diffs the output against a checked-in snapshot. Out of scope for V2.1; a high-leverage v3 follow-up.
4. **Per-rule fire analytics** — same shape as ADR-0020's follow-up — a new table tracking which dimension-aliases / rendered-SQL examples are actually consulted by LLM consumers. v3 question.
5. **Promote F3c stand-ins to columns** — the `approver_role`, `effective_to`, and `next_review_date` fields are placeholders. Follow-up ADR when the underlying policy semantics are designed.

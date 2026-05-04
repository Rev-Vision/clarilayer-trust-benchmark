# ADR-0018 — Rename `metrics.tier` to `metrics.policy_tier` (Trust Benchmark v2 readiness)

- **Status:** Accepted
- **Date:** 2026-05-02
- **Sprint context:** Trust Benchmark v2 (B0.2)
- **Stacks on:** B0 (Baseline E readiness gate harness)
- **Migration:** `supabase/migrations/20260502000000_b0_2_rename_metrics_tier_to_policy_tier.sql`
- **Tracking:** `BENCHMARK-V2-SPRINT.md`

## Context

The Trust Benchmark v1 (published 2026-04-27) disclosed in §Limitations that
the Canonical Metric API's governed-context emitter (Baseline E in v2,
Baseline D in v1) emits a top-level `"tier"` JSON key on every governed
metric. The warehouse has a column `dim_products.tier`, but the join graph
of every governed metric (24/2,136 calls failed in v1) does NOT include
`dim_products` in its scope; instead, the join graph aliases
`dim_customers` as `c`, and `dim_customers` does not have a `tier` column.

When the model reads the rendered envelope:

```json
{
  "metric": {
    "tier": "Financial",
    ...
  }
}
```

…it conflates the JSON metadata key with a column reference and emits
`c.tier`. DuckDB rejects with:

```text
BinderException: column "tier" does not exist
```

The B0 readiness-gate harness (`benchmark/scripts/check_envelope_columns.py`)
confirmed this is **structurally universal** across all 20 governed
metrics — the v1 question battery only landed tier-segmentation queries on
3 metrics × 8 questions, hiding the bug behind specific question shapes.
v2 cannot run with this latent.

## Considered options

### Option A: rename `metrics.tier` → `metrics.policy_tier` everywhere (chosen)

**Mechanics.** A single ALTER TABLE rename of the row column, plus
re-creation of the two `plpgsql` functions that read `v_metric.tier` from
a `public.metrics%ROWTYPE` declaration. All API serializers, repositories,
TypeScript types, frontend filters, seed scripts, and tests follow the
rename through their type chain.

**Why we picked it:**

1. **Eliminates the structural bug permanently.** No JSON key named
   `"tier"` will ever again appear in the envelope. `policy_tier` does
   not exist as a column on any warehouse table, so there is no
   collision channel for any future metric to re-introduce.

2. **Harmonizes naming with `approval_runs.policy_tier` and
   `approval_decisions.policy_tier`** (added in
   `20260314000004_approval_workflow_phase1_foundation.sql`). The metric
   row's tier classification is, conceptually, the same value as the
   policy tier surfaced on approval rows — and they are now named the
   same way.

3. **The v1 API has no documented external consumers.** ClariLayer is
   pre-pivot pilot. The v1 publication (`benchmark/publication/...`)
   documents the API but does not commit to a contract; the §Limitations
   note already flags the channel as actionable. Existing internal
   consumers are the workspace UI, the benchmark seed, and the demo
   scripts — all in this repo.

4. **The clean break is documentable in one ADR.** Option B (below) was
   considered first because it preserves backward compatibility, but the
   resulting code surface (dual-emission serializer + v1.1 deprecation
   note + transitional repository accessors) was strictly larger than
   the rename, and the resulting envelope shape would still carry the
   collision channel until the deprecation window closed.

### Option B: envelope-serializer guard with dual emission (rejected)

**Mechanics.** Keep `metrics.tier` as the row column. Update the v1
serializer to emit BOTH `"tier"` (deprecated, retained for the v1.x
window) and `"policy_tier"` (the canonical name). Update the rendered
envelope-to-prompt template (`baselines/d_clarilayer.py:_render_envelope`)
to either prefix the JSON with a header explaining that `tier` is a
metadata classification, not a column, OR drop the `"tier"` key from the
rendered prompt while keeping it in the API envelope.

**Why we did not pick it:**

1. **Doesn't fix the bug — only papers over it.** The structural
   collision (`"tier"` key + warehouse column with same name) remains
   in the API envelope. Any future consumer that ingests the raw
   envelope and feeds it to a model would re-encounter the bug. The
   harness would still flag the FAIL on the live API.

2. **Two parallel sources of truth.** Operating both `"tier"` and
   `"policy_tier"` in the envelope means any future read path needs to
   pick one. Rolling forward eventually requires the rename anyway —
   we'd be adding a dual-emit phase for no benefit.

3. **Increases consumer-side complexity.** v1 consumers reading the
   envelope would need to migrate AT LEAST when v2 freezes the
   contract; keeping both indefinitely means two stable JSON shapes
   forever.

4. **Doesn't harmonize naming.** `metrics.tier` and
   `approval_runs.policy_tier` would still disagree on naming, defeating
   one of the cleanup wins.

### Option C: rename `dim_products.tier` warehouse column (rejected)

Renaming the warehouse column would break every customer's ETL. Out of
scope for a dataset-engineering decision; ClariLayer does not own
customer warehouse schemas.

## Decision

Option A: rename `metrics.tier` to `metrics.policy_tier`. Single
coordinated PR (this one). No backwards-compatibility shim. The change
is documented as a v2 breaking change for any v1 API consumer.

## Migration path for v1 consumers

There are no documented external v1 API consumers as of 2026-05-02
(verified via `brain/gtm/`, `LAUNCH-PREP-PLAN.md`,
`MVP_DEMO_GUIDE.md`, and `README.md`). If any v1 consumer surfaces
that has been reading the `tier` JSON key, the migration is mechanical:

1. Replace `?tier=` query parameter with `?policy_tier=` on
   `GET /api/v1/metrics`.
2. Replace `metric.tier` access with `metric.policy_tier` on
   `GET /api/v1/metrics/:id`.
3. The valid set of values is unchanged (`Experimental`, `Operational`,
   `Financial`).

All changes are name-only — semantics, validation, and value enum are
identical to v1.

## What this rename touches

Layer-by-layer, in this PR:

### Database (`supabase/migrations/20260502000000_b0_2_rename_metrics_tier_to_policy_tier.sql`)

- `ALTER TABLE public.metrics RENAME COLUMN tier TO policy_tier;`
- `DROP INDEX idx_metrics_tier_lifecycle;`
- `CREATE INDEX idx_metrics_policy_tier_lifecycle ON public.metrics(policy_tier, lifecycle_status) WHERE policy_tier IS NOT NULL;`
- `CREATE OR REPLACE FUNCTION public.resolve_metric_approval_workflow_match(...)` — body
  copied from `20260418110002` with `v_metric.tier` → `v_metric.policy_tier`.
- `CREATE OR REPLACE FUNCTION public.validate_release_candidate_approval_gate(...)` —
  body copied from `20260314000005` with the same single substitution.

The `metric_lifecycle_policies.tier` column is **NOT** renamed: that table
is correctly named (it IS the tier classification on the policies table).
Functions that JOIN `metric_lifecycle_policies p ON p.tier = v_metric.policy_tier`
continue to reference both columns intentionally — they're on different
tables and carry the same value.

The `approval_workflow_matchers.matcher_type IN ('tier', ...)` literal
also stays. The matcher type is named after what it matches against (the
metric's policy tier classification); renaming the matcher_type would
force a data migration on every active workflow row. Old
`matcher_type='tier'` rows continue to evaluate `v_metric.policy_tier =
m.matcher_value` correctly under the new column name.

### TypeScript types

- `packages/core/src/model/metric.ts` — `Metric.tier` → `Metric.policy_tier`
- `packages/core/src/approval/index.ts` — `ApprovalWorkflowMetricContext.tier` →
  `ApprovalWorkflowMetricContext.policyTier`
- `packages/core/src/crystallization/mapping.ts` — `MetricSavePayload.tier` →
  `MetricSavePayload.policy_tier`
- `packages/db/src/repositories/metrics.ts` — `MetricRow.tier` →
  `MetricRow.policy_tier`; same for `CreateMetricInput`, `UpdateMetricInput`
- `packages/db/src/repositories/metric-registry.ts` — `RegistryMetricRow.tier`,
  list/search options, query filters
- `packages/db/src/repositories/derived-metrics.ts` — `CreateDerivedMetricInput.tier`
- `packages/db/src/repositories/releases.ts` — `ProjectReleaseRow.metric.tier`
- `apps/web/lib/api-v1/types.ts` — `MetricSummary.tier`, `MetricDetailV1Metric.tier`
- `apps/web/lib/metric-detail-types.ts` — `MetricDetailMetric.tier`
- `apps/web/lib/health-types.ts` — `HealthDashboardMetricRow.tier`
- `apps/web/lib/dbt-control-plane.ts` — `DbtMetricGenerationSource.tier`

### API routes (envelope JSON keys, query params)

- `GET /api/v1/metrics` — JSON response field `tier` → `policy_tier`,
  query parameter `?tier=` → `?policy_tier=`
- `GET /api/v1/metrics/:id` — JSON response field `tier` → `policy_tier`
- `GET /api/metrics` — JSON response field `tier` → `policy_tier`
- `GET /api/metrics/:id` — JSON response field `tier` → `policy_tier`
- `GET /api/metrics/:id/policy` — Reads `metrics.policy_tier` to look up
  the matching `metric_lifecycle_policies.tier` row
- `POST /api/metrics/derived` — Request body field `tier` → `policy_tier`

### Frontend

- `apps/web/app/(app)/metrics/page.tsx` — URL search param
  `?tier=` → `?policy_tier=`; legacy URLs become unfiltered
- `apps/web/app/(app)/metrics/_components/registry-view.tsx` — filter,
  display
- `apps/web/app/(app)/dashboard/logic.ts` — Supabase select
- `apps/web/app/(app)/govern/logic.ts` — filter shape, Supabase select
- `apps/web/app/(app)/govern/[id]/page.tsx` — display
- `apps/web/components/metric-detail/{advanced-drawer,deploy-tab,hero-card}.tsx`
  — display, deploy gate
- `apps/web/components/metric-edit-form.tsx` — initial form value

### Benchmark + scripts

- `benchmark/scripts/seed_test_org.py` — UPSERT row builder, governed/
  variant/deprecated rows, `_render_metric_upsert` cols + update_cols
- `benchmark/scripts/check_envelope_columns.py:228` — synthetic envelope
  emitter (offline harness mode); kept faithful to the API output
- `scripts/provision-hosted-demo.ts` — 8 demo metric upserts
- `scripts/demo-core.ts` — in-memory `Metric` fixture

### Tests

- `apps/web/app/api/v1/metrics/route.test.ts` — fixture rows + assertion
- `apps/web/app/api/v1/metrics/[id]/route.test.ts` — fixture row +
  assertion
- 13 other test files across `apps/web/` (deployment automation, release
  hub, approvals, dbt, validation, health, overlap candidates, govern
  logic, metrics logic, workflow-manager-helpers)
- 4 tests in `packages/core/test/` (`core`, `probes`,
  `crystallization-mapping`, `approval-workflow`)
- 2 tests in `packages/db/src/repositories/`
  (`approval-workflows.test.ts`, `metric-registry.test.ts`)

## Verification

The B0 harness confirms the fix:

```text
# Before this PR (offline mode)
21 FAIL, 35 WARN, 20 metrics audited.

# After this PR (offline mode)
0 FAIL, 35 WARN, 20 metrics audited.
```

The 35 unrelated WARN findings (`legacy_braintree`, `lead_qualified`,
`opp_won`, etc.) are about column-like prose tokens in plain-language
metric descriptions; they are softer than the structural FAIL and are
out of scope for this rename. They do NOT block v2 readiness.

## What this rename does NOT touch

- `dim_products.tier` (warehouse column — customer-owned schema)
- `metric_lifecycle_policies.tier` (separate table; column is correctly
  named in its own context)
- `approval_runs.policy_tier`, `approval_decisions.policy_tier`
  (already correctly named)
- `subscriptions.plan_tier` (different field)
- `templates.field_constraints` JSONB `tier` key (template constraint
  shape; renaming would force a templates-data migration)
- `drafts.intent` JSONB `tier` key (draft intent shape; renaming would
  force a drafts-data migration)
- `dbt-generator` YAML output `tier:` key (dbt YAML contract — external
  tool consumes it)
- `approval_workflow_matchers.matcher_type IN ('tier', …)` literal
  (matcher type identifier; rename would force a workflow-rows migration)
- `Tier` enum values (`experimental`, `operational`, `financial`) — only
  the column / property names change

## Breaking-change notice for v1 consumers

If you are consuming `GET /api/v1/metrics` or `GET /api/v1/metrics/:id`
on a deployment that has applied B0.2 (`20260502000000`):

- The envelope JSON field `tier` is now `policy_tier`.
- The query parameter `?tier=` is now `?policy_tier=`.
- All other shapes are unchanged.

Update your client to read `policy_tier`.

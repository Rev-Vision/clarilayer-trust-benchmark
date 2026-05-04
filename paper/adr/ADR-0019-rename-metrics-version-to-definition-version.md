# ADR-0019 — Rename `metrics.version` to `metrics.definition_version` (Trust Benchmark v2 readiness)

- **Status:** Accepted
- **Date:** 2026-05-02
- **Sprint context:** Trust Benchmark v2 (B0.3)
- **Stacks on:** B0 (Baseline E readiness gate harness, warehouse `metric_versions` extension), B0.2 (`metrics.tier` -> `metrics.policy_tier` rename, ADR-0018)
- **Migration:** `supabase/migrations/20260502000100_b0_3_rename_metrics_version_to_definition_version.sql`
- **Tracking:** `BENCHMARK-V2-SPRINT.md`

## Context

B0 added a `metric_versions` table to the warehouse to support
v2's Versioning-category benchmark questions (definition lineage —
"when was a metric's SQL last updated", "what was the previous
definition", etc.). Building the Baseline E readiness-gate harness
(`benchmark/scripts/check_envelope_columns.py`) scope-aware exposed a
NEW envelope-column collision with the same shape as the `tier`
collision documented in v1 §Limitations and closed in B0.2 by ADR-0018:

The metric envelope serializes a top-level `"version"` JSON key (e.g.
`"version": "v3"`). The warehouse now has a `metric_versions.version`
column. No governed metric's join graph includes `metric_versions`,
which means the model reading the envelope sees:

```json
{
  "metric": {
    "version": "v3",
    ...
  }
}
```

…and conflates the JSON metadata key with a SQL column reference,
emitting `c.version` (where `c` aliases `dim_customers`, the metric's
default join base). DuckDB rejects with:

```text
BinderException: column "version" does not exist
```

The B0 harness with `"version"` added to
`_ENVELOPE_FIELDS_THAT_LOOK_LIKE_COLUMNS` confirms this is
**structurally universal** across all 20 governed metrics — same
pattern as the `tier` collision. After B0.2 closed the `tier` channel,
the post-B0.2 baseline of the harness (with `version` added to the
audit) reports `21 FAIL` (20 metrics × envelope.version + 1 unrelated
`bookings.yaml` content bug). v2 cannot run with this latent.

## Considered options

### Option A: rename `metrics.version` -> `metrics.definition_version` everywhere (chosen)

**Mechanics.** A single `ALTER TABLE` rename of the row column. No
indexes reference the column directly (verified by grep across
`supabase/migrations/`). No plpgsql functions read `v_metric.version`
from a `public.metrics%ROWTYPE` declaration (also verified — the only
plpgsql function bodies that touch metric rows reference
`v_metric.policy_tier`, `v_metric.metric_type`, `v_metric.scope`,
etc., never `v_metric.version`). All API serializers, repositories,
TypeScript types, frontend filters, seed scripts, and tests follow the
rename through their type chain.

**Why we picked it:**

1. **Eliminates the structural bug permanently.** No JSON key named
   `"version"` will ever again appear in the metric envelope.
   `definition_version` does not exist as a column on any warehouse
   table, so there is no collision channel for any future metric to
   re-introduce.

2. **Harmonizes naming with v2's vocabulary.** The warehouse
   `metric_versions.version` is the **definition-history lineage**
   table (one row per definition revision, with `effective_from` /
   `effective_to` / `deprecated_at` / `replaced_by` columns). The
   metric row column is the *current* `definition_version` pointer.
   Calling them the same name (`version`) would be confusing even
   without the collision; calling the row column `definition_version`
   names it for what it is.

3. **`definition_version` is also semantically clearer than bare
   `version`.** ClariLayer has multiple `version` concepts —
   `releases.version` (release version), `deployments.version`
   (deployment version), `release_candidates.suggested_version`
   (proposed next definition version), `metric_versions.version`
   (warehouse definition lineage), and `templates.version` (template
   version). Naming the metric row column `definition_version`
   disambiguates without forcing readers to context-look-up. The other
   `version` columns in the schema are intentionally NOT renamed —
   they're each correctly named in their own context.

4. **The v1 API has no documented external consumers.** Confirmed
   exhaustively in B0.2 (ADR-0018 §"Migration path for v1 consumers").
   ClariLayer is pre-pivot pilot. No live downstream consumer exists
   that reads `metric.version` from the v1 envelope.

5. **The clean break is documentable in one ADR.** Same trade-off
   reasoning as B0.2: the dual-emit alternative (Option B) was
   strictly more code and STILL left the collision channel open until
   the deprecation window closed. We've already taken the clean break
   on `tier`; doubling down on the same approach for `version` is the
   coherent choice.

6. **This is the SECOND breaking rename in v2 readiness.** Alongside
   ADR-0018, v2 itself is a benchmark version increment so the API
   breakage is part of the coherent release story — readers of the
   v2 publication will see a single combined "v1 -> v2 envelope
   schema diff" rather than a series of trickled deprecations.

### Option B: envelope-serializer guard with dual emission (rejected)

**Mechanics.** Keep `metrics.version` as the row column. Update the
v1 serializer to emit BOTH `"version"` (deprecated, retained for the
v1.x window) and `"definition_version"` (the canonical name). Update
the rendered envelope-to-prompt template to either prefix the JSON
with a header explaining that `version` is metadata, not a column, OR
drop the `"version"` key from the rendered prompt while keeping it in
the API envelope.

**Why we did not pick it:** identical reasoning to ADR-0018 §Option B.
Doesn't fix the bug (just papers over it); two parallel sources of
truth; increases consumer-side complexity for downstream API readers
(none today, but the deprecation cleanup tax persists); doesn't
harmonize with v2 vocabulary.

### Option C: rename the warehouse `metric_versions.version` column (rejected)

`metric_versions.version` is the warehouse definition-lineage table.
It is correctly named in its own context (it IS the version column on
a "versions" table). Renaming it would (a) push the resolution into
customer-owned ETL, which violates the same "ClariLayer does not own
customer warehouse schemas" principle that drove ADR-0018's rejection
of Option C for `dim_products.tier`, and (b) be semantically wrong —
the `metric_versions.version` column genuinely is a version column on
a versioning table. The collision is on the metric envelope's side,
not the warehouse's.

## Decision

Option A: rename `metrics.version` to `metrics.definition_version`.
Single coordinated PR (this one). No backwards-compatibility shim. The
change is documented as a v2 breaking change for any v1 API consumer.

## Migration path for v1 consumers

There are no documented external v1 API consumers as of 2026-05-02.
If any v1 consumer surfaces that has been reading the `version` JSON
key, the migration is mechanical:

1. Replace `metric.version` access with `metric.definition_version` on
   `GET /api/v1/metrics` (list response shape).
2. Replace `metric.version` access with `metric.definition_version` on
   `GET /api/v1/metrics/:id` (detail response shape).
3. Replace `version` field on `POST /api/metrics/derived` request body
   with `definition_version`.

All changes are name-only — semantics, validation, and value format
(SemVer) are identical to v1.

`MetricDetailV1Release.version`, `MetricDeploymentV1.version`, and the
`versionHistory[]` array name are explicitly NOT renamed — those are
release-version / deployment-version / array-name fields, conceptually
distinct from the metric definition version. They have no envelope
collision (no warehouse table exposes a top-level join with the
relevant column under the metric's join graph).

## What this rename touches

Layer-by-layer, in this PR:

### Database (`supabase/migrations/20260502000100_b0_3_rename_metrics_version_to_definition_version.sql`)

- `ALTER TABLE public.metrics RENAME COLUMN version TO definition_version;`
- Re-state column COMMENT for grep-ability.

The migration is materially shorter than B0.2's because:
- No partial index references the renamed column (verified against
  `20260226000009_v2_indexes_rls.sql`).
- No plpgsql function reads `v_metric.version` from a
  `public.metrics%ROWTYPE` declaration (verified by exhaustive grep
  across `supabase/migrations/`).
- The column has no CHECK constraint to drop / recreate (just a
  `NOT NULL DEFAULT '1.0.0'` which Postgres rewrites in-place).

### TypeScript types

- `packages/core/src/model/metric.ts` — `Metric.version` -> `Metric.definition_version`
- `packages/core/src/crystallization/mapping.ts` — `MetricSavePayload.version` -> `MetricSavePayload.definition_version`
- `packages/db/src/repositories/metrics.ts` — `MetricRow.version`, `CreateMetricInput.version`, `UpdateMetricInput.version` all renamed
- `packages/db/src/repositories/metric-registry.ts` — `RegistryMetricRow.version`, `REGISTRY_SELECT` projection, `flattenRow` mapping
- `packages/db/src/repositories/derived-metrics.ts` — `CreateDerivedMetricInput.version` -> `CreateDerivedMetricInput.definition_version`
- `apps/web/lib/api-v1/types.ts` — `MetricSummary.version`, `MetricDetailV1Metric.version`
- `apps/web/lib/metric-detail-types.ts` — `MetricDetailMetric.version`

### API routes (envelope JSON keys, request body)

- `GET /api/v1/metrics` — JSON response field `version` -> `definition_version`
- `GET /api/v1/metrics/:id` — JSON response field `version` -> `definition_version`
- `GET /api/metrics/:id` — JSON response field `version` -> `definition_version`
- `POST /api/metrics/derived` — Request body field `version` -> `definition_version`

### Frontend

- `apps/web/app/(app)/metrics/_components/registry-view.tsx` — display
- `apps/web/components/metric-detail/advanced-drawer.tsx` — display
- `apps/web/lib/services/save-crystallized-metric.ts` — payload field passthrough

### Benchmark + scripts

- `benchmark/scripts/seed_test_org.py` — outgoing UPSERT row builder
  (governed / variant / deprecated rows), `_render_metric_upsert`
  cols + update_cols, SQL value list. The YAML SOURCE side
  (`gov_md.get("version", ...)` reading `governance_metadata.version`)
  is unchanged.
- `benchmark/scripts/check_envelope_columns.py:228` — synthetic
  envelope emitter (offline harness mode); the JSON key emitted to
  the rendered envelope is now `"definition_version"`.
- `benchmark/scripts/check_envelope_columns.py:_ENVELOPE_FIELDS_THAT_LOOK_LIKE_COLUMNS`
  — `"version"` added to the audit list (alongside the existing
  `"tier"`) so any future regression that re-introduces a `"version"`
  key on the metric envelope is caught structurally.

### Tests

- `apps/web/app/api/v1/metrics/route.test.ts` — fixture rows + assertion
- `apps/web/app/api/v1/metrics/[id]/route.test.ts` — fixture row + assertion
- `apps/web/app/api/v1/metrics/[id]/deployments/route.test.ts` — METRIC fixture (DEPLOYMENT fixtures retain `version`)
- `apps/web/app/api/metrics/[id]/route.test.ts` — METRIC fixture (release fixtures retain `version`)
- `apps/web/app/api/metrics/[id]/deploy/route.test.ts` — METRIC fixture (DEPLOYMENT fixtures retain `version`)
- `apps/web/app/api/metrics/[id]/dbt/route.test.ts` — METRIC fixture
- `apps/web/app/api/metrics/[id]/validation/route.test.ts` — METRIC fixture
- `apps/web/app/api/metrics/overlaps/logic.overlap.test.ts` — `makeMetric` factory
- `apps/web/app/api/approval-workflows/route.test.ts` — METRIC fixture
- `apps/web/app/(app)/metrics/logic.test.ts` — SEED_METRICS fixtures (×5)
- `apps/web/lib/deployment-automation-core.test.ts` — METRIC fixture
- `packages/core/test/probes.test.ts` — `makeMetric` factory
- `packages/core/test/core.test.ts` — `makeMetric` factory
- `packages/core/test/crystallization-mapping.test.ts` — payload assertion
- `packages/db/src/repositories/metric-detail.test.ts` — METRIC fixture
- `packages/db/src/repositories/metric-registry.test.ts` — SEED_METRICS fixtures (×6)
- `packages/db/src/repositories/approval-workflows.test.ts` — METRIC fixture

### Other call sites

- `apps/web/lib/deployment-automation-core.ts` — every `metric.version`
  audit-metadata / display-string / deployable-builder access (≥18
  refs) renamed to `metric.definition_version`. The downstream
  audit-metadata key `version` (the `getOrgAdminAuditMetadataSummary`
  display contract) and the `DeployableMetric.version` adapter
  contract field stay — those are separate API contracts.
- `apps/web/app/api/metrics/[id]/dbt/route-impl.ts` — call site reads
  `metric.definition_version`; the dbt-builder option label `version`
  on `buildDbtGitHubCommitPlan` is unchanged (separate API contract).
- `apps/web/app/api/metrics/[id]/validation/route-impl.ts` — call site
  passes `metric.definition_version` to `generator.generateFromSql`;
  the probe-bundle output field name `metric_version` is unchanged
  (separate schema decision).
- `packages/core/src/validation/probes.ts` — three `metric.version`
  reads renamed; output `metric_version` schema field unchanged.

## Verification

The B0 harness (with `"version"` in the audit list) confirms the fix:

```text
# Before this PR (post-B0.2 baseline, with `version` added to the audit)
21 FAIL, 35 WARN, 20 metrics audited.
  (20 envelope.version FAILs across all governed metrics
   + 1 unrelated bookings.yaml content bug)

# After this PR (offline mode)
1 FAIL, 35 WARN, 20 metrics audited.
  (only the unrelated bookings.yaml content FAIL persists)
```

The 35 WARN findings (`legacy_braintree`, `lead_qualified`, `opp_won`,
etc.) and the 1 unrelated `bookings.yaml` `paid_at`
plain-language-spec FAIL are out of scope for this rename. They do
NOT block v2 readiness.

## What this rename does NOT touch

- `metric_versions.version` (warehouse column — definition-history
  lineage table; correctly named in its own context)
- `releases.version`, `release_candidates.suggested_version`,
  `release_candidates.dependency_resolution`,
  `ResolvedDependencyVersion.version` (release versions — distinct
  from metric definition version)
- `deployments.version`, `MetricHealthDeploymentSignal.version`,
  `MetricDeploymentV1.version`, `MetricDetailDeployment.version`
  (deployment versions)
- `MetricDetailV1Release.version`, `MetricDetailRelease.version`
  (release-row version surfaces; conceptually distinct)
- `versionHistory[]` array name on `MetricDetailV1Response` /
  `MetricDetailResponse` (array of release rows)
- `templates.version`, `template.version` fixtures (template version
  on a separate table)
- `approval_workflow_versions.version_number` (workflow versioning
  column; different table)
- `snapshot_version` in pricing snapshots (different concept)
- The probe bundle output field `metric_version` in
  `packages/core/src/validation/probes.ts` (probe-bundle schema name
  is a separate decision)
- The dbt commit-plan option label `version` in
  `buildDbtGitHubCommitPlan` (a separate API contract; the metric
  source field is what changes)
- The audit-metadata display key `version` in
  `getOrgAdminAuditMetadataSummary` (separate audit-event schema)
- The deployable adapter contract field `DeployableMetric.version`
  (separate adapter API contract)
- Package versions in `package.json`, API path versions like
  `/api/v1/`, GitHub Actions versions, React/Next.js versions

## Breaking-change notice for v1 consumers

If you are consuming `GET /api/v1/metrics` or `GET /api/v1/metrics/:id`
on a deployment that has applied B0.3 (`20260502000100`):

- The envelope JSON field `version` on the `metric` object (or on
  each list element) is now `definition_version`.
- The `versionHistory[]` array on the detail response is unchanged
  (each element still exposes `version` because that is the *release*
  version, not the metric definition version).
- All other shapes are unchanged.

Update your client to read `definition_version` for the metric's
definition version. Continue to read `version` from the
`versionHistory[]` array elements for release versions.

## Coherent v2 readiness story

This is the **second** breaking rename in v2 readiness, alongside
ADR-0018. The combined v2 envelope diff for any v1 consumer:

```text
# v1 envelope
{
  "metric": {
    "tier": "Financial",       # B0.2: now `policy_tier`
    "version": "v3",            # B0.3: now `definition_version`
    ...
  }
}

# v2 envelope
{
  "metric": {
    "policy_tier": "Financial",
    "definition_version": "v3",
    ...
  }
}
```

Both renames close column-collision channels with the warehouse
schema. Both are documented in their own ADRs. v2 itself is a
benchmark publication increment, so the API surface change is part
of the coherent release story rather than a series of trickled
deprecations.

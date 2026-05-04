# ADR-0023 — Metric `consumer_contexts` envelope field with per-surface version pins (Trust Benchmark v2.1, Versioning lift)

- **Status:** Accepted
- **Date:** 2026-05-03
- **Sprint context:** Trust Benchmark v2.1 — F5 (codex-priority #5)
- **Stacks on:** ADR-0020 (`metric_deprecated_framing_rules` — V2.1-F2), ADR-0021 (execution metadata + `governance_facts` — V2.1-F3), ADR-0022 (`approval_state` directive — V2.1-F4)
- **Migration:** `supabase/migrations/20260503000300_v2_1_f5_metric_consumer_contexts.sql`
- **Tracking:** `brain/product/clarilayer-v2.1-governance-response-features.md` §3 priority 5

## Context

Trust Benchmark v2 measured ClariLayer's Versioning category PASS rate at **5.6%** (only 20 of 360 questions — codex-priority #5 in the v2.1 plan). Versioning questions ask things like "what arr should I use in the board deck for Q1?" or "is the investor-update arr the same as the trend-chart arr?" The answer depends on **which consumer surface** the data goes to: board reporting may pin to a frozen version while investor updates always use the latest canonical, and audit reconciliation typically pins to a legacy version for historical comparability.

The current envelope has `versionHistory[]` (lineage) and `governance_facts.current_version` (the global canonical) but no per-consumer-surface pinning. The LLM is forced to derive consumer-specific pins from raw lineage — a job it does poorly.

F5 adds an explicit per-consumer-surface mapping so the envelope can tell the LLM "for board reporting, use v2; for investor update, use v3" rather than making it infer that from `versionHistory[]` deprecation timestamps.

## Decision

Add a new table `public.metric_consumer_contexts` carrying per-(metric, consumer_surface) version pins + policy directives, surfaced as the v1 envelope `consumer_contexts` field of type `MetricConsumerContexts` (a `Partial<Record<ConsumerSurface, ConsumerContextEntry>>`).

The envelope-facing shape is:

```json
{
  "consumer_contexts": {
    "board_reporting": {
      "pinned_version": "v2",
      "policy": "Frozen at v2 for the current FY's board materials. Update at FY27 close."
    },
    "investor_update": {
      "pinned_version": "v3",
      "policy": "Always use latest canonical (v3) for investor-facing communications."
    },
    "trend_chart": {
      "pinned_version": null,
      "policy": "canonical_now"
    },
    "audit_reconciliation": {
      "pinned_version": "v1",
      "policy": "Use legacy v1 definition only for historical-period reconciliation against pre-2025 audited financials."
    }
  }
}
```

For metrics **without** consumer-surface pins — single-version metrics or metrics whose consumers don't differentiate — `consumer_contexts` is `{}` (empty record, always present, never null/undefined). Surfaces NOT in the record signal "no surface-specific directive — the LLM should fall back to the canonical version."

The field is **additive** to the existing `MetricDetailV1Response`. Legacy v1 consumers can ignore it. F5-aware consumers iterate the record's keys (e.g. "is `board_reporting` present? if yes, use its pin; otherwise use `governance_facts.current_version`").

## Why a separate envelope field, not an extension of `governance_facts` or a sibling of `versionHistory`

This is the load-bearing decision and the one that motivated the dispatch as a separate feature.

F3c's `governance_facts.current_version` is the **"what's the canonical now"** snapshot — a single version pointer for the whole metric. It answers "if I had to pick one version of this metric, what would it be?"

F5's `consumer_contexts` is the **"what version does each consumer use"** application contract. It answers "given that the metric has a lineage, which version applies in each context where the metric gets read?"

The two concerns differ on three axes:

1. **Shape.** `governance_facts.current_version` is a single string. `consumer_contexts` is a map keyed by surface. Folding them would force `governance_facts` to carry either an additional map (bloating the F3c contract every metric carries) or a degenerate "default surface" entry (which doesn't match how authors think about consumer pinning).
2. **Audience inside the LLM consumer.** `governance_facts` answers structured questions about the metric ("what version is current?"). `consumer_contexts` injects per-surface routing into the response generation ("the user's question is about board reporting, so use v2"). The LLM consumes them at different stages of the prompt.
3. **Authoring source.** `governance_facts` is auto-derived from `metrics.definition_version`. `consumer_contexts.policy` is hand-authored — the directive string is the value, written for the specific reason this metric pins this surface to this version.

`versionHistory[]` (the existing lineage array) is similarly orthogonal: it lists what versions exist with their lifecycle status and timestamps. It does NOT say which version each consumer surface should pin to. F5 fills that gap.

Folding F5 into `governance_facts` or `versionHistory` would force every metric to carry either a partial-map `current_versions_by_surface` field or a `consumer_pin` field on every release row — both shapes invite consumer drift ("does the absence of a pin mean fall-back-to-canonical or unspecified?") and bloat the common case for single-version metrics.

## Why the `consumer_surface` enum is CHECK-constrained (not free-text)

The plan-spec called for a tight controlled vocabulary. Rationale:

1. **Deterministic LLM branching.** With a tight enum, the consumer harness can build prompts like "if the user is asking about the board deck, look at consumer_contexts.board_reporting." A free-text vocabulary would require the LLM to reason about whether "board_review" (typo'd by the author) maps to "board_reporting" (canonical) — an unnecessary fragility.
2. **Author discipline.** A new surface is a meaningful event — it warrants a CHECK constraint update + a release note. Free-text would let any author invent a surface name that the LLM consumer doesn't know about.
3. **Forward-compat is cheap.** Adding a new surface (e.g. `regulatory_filing`, `investor_diligence`) requires a migration to extend the CHECK constraint and a new entry in the `ConsumerSurface` union. The whole change is additive and can be released without breaking F5-aware consumers.

The six initial values cover the v2.1 dataset's plausible surfaces:

- `board_reporting` — recurring board materials.
- `investor_update` — outbound investor communications.
- `trend_chart` — internal/external time-series charts where back-cast consistency matters.
- `audit_reconciliation` — historical reconciliation against audited financials.
- `operational_dashboard` — internal day-to-day dashboards.
- `finance_close` — month/quarter/year close workflows.

If the test org needs more surfaces during seed authoring, we extend the enum once and extend the catalog. Cheap.

## Why `pinned_version` is nullable

The plan-spec carried two shapes — a `{version, policy}` record AND a string-only shorthand (`trend_chart_policy: "canonical_now"`). F5 unifies both by making `pinned_version` nullable:

- A row with `pinned_version = NULL, policy = "canonical_now"` projects to `{pinned_version: null, policy: "canonical_now"}` — which is the same shape an entry with a version pin carries, just with a null pin.
- Forces consumers to read both fields and decide whether the directive is purely behavioral ("always use latest canonical") or a hard pin ("frozen at v2").
- Avoids creating two parallel shapes (`{version, policy}` vs `{policy}`) that would force the LLM to branch on which shape it sees.

The migration's `pinned_version text` (NULLABLE) supports this directly. The repository projector preserves the null verbatim — collapsing it to `undefined` would break the consumer contract.

The tradeoff is that consumers always check `pinned_version != null` before treating it as a hard pin. That's a one-line check — much cheaper than maintaining two parallel projection shapes.

## Alternatives considered

### Option A: separate table `metric_consumer_contexts` with envelope `consumer_contexts` record (chosen)

**Mechanics.** New table keyed by `metric_id`. UNIQUE `(metric_id, consumer_surface)` — at most one entry per (metric, surface) pair. FK to `metrics(id) ON DELETE CASCADE`. RLS mirrors `metric_relationships` / `metric_approvals` / `metric_deprecated_framing_rules` / `metric_pending_state` (org-scoped SELECT). Repository projects rows into a `Partial<Record<ConsumerSurface, ConsumerContextEntry>>` for the envelope.

**Why we picked it:**

1. **Shape matches data shape.** A metric has 0..N entries; each entry binds one surface to a `(pinned_version, policy)` pair. The N×1 cardinality is enforced by `UNIQUE (metric_id, consumer_surface)` at the schema level — a JSONB blob would lose that.
2. **RLS parity with F2 / F3 / F4.** The new table mirrors the existing SELECT-only RLS pattern (org-membership gate). A new column on `metrics` would inherit `metrics`' broader RLS surface.
3. **Indexability.** A `metric_id` B-tree index supports the parallel-fan-out fetch in the v1 detail route. JSONB-column access on `metrics` would not.
4. **Future authoring UI.** The eventual workspace-admin UI for managing consumer pins (e.g. "show me every metric pinned to v2 for board_reporting") wants a row-per-(metric, surface) grid. Modeling them as table rows is the obvious shape; a JSONB column would force every "what's pinned where?" query to scan the entire metrics table.
5. **Authoring lifecycle.** A consumer pin gets retired when the surface unpins (e.g. board_reporting moves from frozen v2 to canonical at FY close). A row-delete pattern is cleaner than null-out-the-key on a JSONB column, and makes the absence-of-pin semantic obvious to humans reading the schema.

### Option B: extend `governance_facts` with the F5 fields

Rejected per §"Why a separate envelope field" above. The shapes don't align (single string vs map keyed by surface), the authoring sources differ (auto-derived vs hand-authored directives), and the LLM consumes them at different prompt stages (snapshot vs per-surface routing).

### Option C: per-row column on `metric_releases` / `metric_versions`

Rejected. `metric_releases` rows are about a version's lifecycle (when it was released, which environment, status). Adding a `pinned_consumer_surfaces text[]` or similar column would cross-cut concerns: the release row would describe both "this version was released on X date" AND "this version is pinned by Y consumer surfaces." The query "what's pinned where?" would then have to JOIN across releases AND scan the array column.

A separate table keeps each table's column set focused, and the F5 question ("which surface pins to which version?") answers in a single index lookup.

### Option D: JSONB column on `metrics` (`metrics.consumer_contexts JSONB`)

Rejected. Same shape compiles but loses (a) the UNIQUE constraint enforcing one entry per (metric, surface), (b) per-row indexability for analytics queries ("metrics pinned to v2 for board_reporting"), (c) the row-delete pattern that makes the "no row = no pin" semantic obvious. The on-the-wire envelope shape is identical, so consumers can't distinguish A from D — but everything else favors A.

### Option E: free-text `consumer_surface` (no enum)

Rejected per §"Why the consumer_surface enum is CHECK-constrained" above. The cost of a CHECK constraint update on a new surface is one migration; the cost of letting authors invent surface names that LLM consumers don't know about is consumer-side fragility for the lifetime of the schema.

### Option F: surface-specific tables (e.g. `metric_board_pins`, `metric_investor_pins`, `metric_audit_pins`)

Rejected. Surfaces are an enum, not a type hierarchy. Each surface's pin shape is identical (`pinned_version + policy`), so a single table with a `consumer_surface` column is the right shape. Splitting into per-surface tables would force every "what's pinned where for this metric?" query to UNION across N tables.

## Schema details

### `consumer_surface` CHECK constraint

Pinned to the 6-value enum (`board_reporting`, `investor_update`, `trend_chart`, `audit_reconciliation`, `operational_dashboard`, `finance_close`). Future values are additive — only require a CHECK constraint update + a new entry in the `ConsumerSurface` TypeScript union. No data migration. Mirrors F4's pattern of using CHECK constraints (not Postgres ENUMs) for consumer-facing enums.

### `pinned_version` nullable text

`text` (no length cap; semver and informal "v1"/"v2"/"v3" both fit). Nullable per §"Why pinned_version is nullable" — the projector round-trips null verbatim.

### `policy` REQUIRED non-empty

The DB enforces `length(trim(policy)) > 0`. The whole point of F5 is the directive — a row with an empty policy would be a contract violation between the table author and the LLM consumer. The CHECK fails closed. Mirrors F4's `metric_pending_state.policy` constraint.

### UNIQUE `(metric_id, consumer_surface)`

A metric can have at most one pin per surface. The UNIQUE constraint at the schema level enforces this — attempts to insert a second row for the same (metric, surface) pair fail with a constraint-violation error. State transitions are modeled as UPDATE on the existing row; full retirement of a pin is modeled as DELETE.

### RLS — SELECT-only org-membership gate

Mirrors `metric_relationships` / `metric_approvals` / `metric_deprecated_framing_rules` / `metric_pending_state`. `INSERT` / `UPDATE` / `DELETE` go through the service-role repository layer (per the codebase convention noted in `20260226000009_v2_indexes_rls.sql` §6-9). This keeps the workflow tooling that *changes* a consumer pin behind the same auth surface as approval workflow writes.

## Forward-compat with F6 / F7

- **F6 (`requires_disambiguation` field).** Independent — F6 lives on the question/scope layer, not the version-pinning layer. F6 may *reference* the F5 record when the user's question is ambiguous about consumer surface (e.g. "what's the latest arr?" with no surface specified — F6 may surface the consumer_contexts surfaces as candidate scopings), but the storage is independent.
- **F7 (per-policy-tier few-shot examples).** May reference `consumer_contexts` as a "directive style" template source — e.g. show the LLM what a typical board_reporting pin looks like. Storage is independent.
- **Future authoring UI.** F5's table can power a UI for managing consumer pins (e.g. "show me every metric pinned to v2 for board_reporting", "metrics with no investor_update pin"). The per-row indexability + UNIQUE (metric_id, consumer_surface) shape makes this trivial. That UI is out of scope for V2.1.
- **Future surfaces.** Adding `regulatory_filing`, `investor_diligence`, `data_room`, etc. is a single migration + a new entry in the `ConsumerSurface` union. No data migration; existing rows remain valid.

## Forward-compat / fallback semantics

When a consumer reads `consumer_contexts` and the user's question is about a surface that's NOT in the record, the convention is: **fall back to the canonical version** (`governance_facts.current_version`). The empty record `{}` carries the same semantic for every surface — "no surface-specific directive; use the canonical." This makes F5 strictly additive: an LLM consumer that doesn't know about F5 reads `governance_facts.current_version` and behaves correctly for every surface; an F5-aware consumer reads the per-surface pin first and only falls back to canonical when the surface isn't in the record.

The defense-in-depth catch in the route layer degrades a thrown repository error to `consumer_contexts: {}`, which means a transient DB failure for the F5 query gracefully degrades to "no surface pins" rather than 500-ing. This mirrors the F2/F3c/F4 catch patterns.

## Consequences

### Enables

- **Versioning category lift on top of F2 + F3.** F2 directs the LLM to reject deprecated framings; F3c surfaces the canonical version; F5 adds the per-consumer-surface routing the LLM was forced to derive from lineage. The combination should lift Versioning pass rate from 5.6% toward the plan's 15-25% target by removing the LLM's guesswork about which version each surface uses.
- **Per-surface authoring vocabulary.** The seed catalog (`CONSUMER_CONTEXT_OVERRIDES`) carries entries for ~10-12 metrics with version history. Surface-specific policies direct the LLM's behavior:
  - `board_reporting` → typically pinned to a previous version with "frozen until close" framing.
  - `investor_update` → typically pinned to current canonical with "always use latest" framing.
  - `audit_reconciliation` → typically pinned to legacy version with "historical-period only" framing.
  - `trend_chart` → typically no version pin, policy `"canonical_now"` (back-cast consistency).
- **Self-contained directive block.** Each `ConsumerContextEntry` is `(pinned_version, policy)` — consumers can read the per-surface pin without needing `versionHistory` or `governance_facts`. Extra ~30-50 bytes per entry of envelope cost for a stable, focused consumer surface.

### Blocks

Nothing. The `consumer_contexts` field is additive; legacy v1 consumers can ignore it. F5-aware consumers fall back to `governance_facts.current_version` for any surface not in the record.

### Limitations / risks

1. **Stale pins.** A consumer pin that was authored when v2 was current may need an update when v3 ships and the surface should pin to it. Mitigation: future authoring tooling (out of scope for V2.1) wires the pin update into the version-promote workflow; for the test org, the seed script is the canonical source of truth and is authored once per sprint.
2. **Surface enum extensibility cost.** Future surfaces require a CHECK constraint update + a new `ConsumerSurface` value + (potentially) consumer-side handling. Mitigation: the six-value enum was sized for the v2 dataset's needs; new surfaces are a meaningful enough event to warrant the small migration.
3. **Per-metric directive drift across surfaces.** Two metrics in the same surface could carry different policies (e.g. one pinning board_reporting to v2 with "frozen until close", another with "use canonical"). That's by design — the directive is per-(metric, surface) — but it means the LLM consumer can't pre-cache "here's what a board_reporting pin looks like." Each request reads the per-(metric, surface) directive. Mitigation: this is the feature, not a bug.
4. **No version-FK enforcement.** `pinned_version` is text, not FK to `metric_versions`. An author could pin to a version that doesn't exist. Mitigation: `metric_versions` lineage is itself authored from the same YAML source as the pins; in practice the seed script keeps both in sync. Future authoring tooling can add a soft-validation step.
5. **No audit trail.** F5 doesn't carry "who set this pin to this version" — the row's `created_at` / `updated_at` give the timestamp, but not the actor. Mitigation: future migration may add `set_by uuid REFERENCES profiles(id)` columns when the workflow tooling needs to surface "pinned by Alice on 2026-04-22." Out of scope for V2.1.

## Follow-ups

1. **V2.1-F6 / F7** — separate dispatches. F6/F7 don't share storage or shape with F5; they live in their own ADRs.
2. **Consumer-pins workflow UI** — the workspace-admin UI for managing consumer pins is out of scope for V2.1. Production orgs author via the seed pattern (the script is documented as the canonical example).
3. **Audit columns on `metric_consumer_contexts`** — `set_by` / `last_transition_at` columns when the workflow tooling lands. Follow-up ADR.
4. **Per-surface default policies** — a workspace-default "policy template" per surface (so the seed doesn't have to author the directive string from scratch every time). Follow-up ADR when the authoring UI is designed.
5. **Pin transition history** — a sibling `metric_consumer_context_transitions` table that logs every pin change. Useful for "metric X moved from v2-pinned to v3-pinned for board_reporting on 2026-08-15." Out of scope for V2.1; v3 question.
6. **Soft FK from `pinned_version` to `metric_versions.version`** — a runtime check (not a DB constraint) that warns if a pin references a non-existent version. Follow-up tooling work.

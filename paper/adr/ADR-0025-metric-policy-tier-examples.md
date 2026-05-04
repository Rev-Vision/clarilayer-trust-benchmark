# ADR-0025 — Per-(policy_tier, mode) ideal-response few-shot examples envelope field (Trust Benchmark v2.1, response-format adherence)

- **Status:** Accepted
- **Date:** 2026-05-03
- **Sprint context:** Trust Benchmark v2.1 — F7 (codex-priority #7)
- **Stacks on:** ADR-0020 (`metric_deprecated_framing_rules` — V2.1-F2), ADR-0021 (execution metadata + `governance_facts` — V2.1-F3), ADR-0022 (`approval_state` directive — V2.1-F4), ADR-0023 (`consumer_contexts` per-surface pins — V2.1-F5), ADR-0024 (`requires_disambiguation` — V2.1-F6)
- **Migration:** `supabase/migrations/20260503000500_v2_1_f7_policy_tier_examples.sql`
- **Tracking:** `brain/product/clarilayer-v2.1-governance-response-features.md` §3 priority 7
- **Closes:** the last v2.1 product feature gate before the Phase 4 / V2.1-RR re-run.

## Context

Trust Benchmark v2 + V2.1-F1-F6 give the LLM an envelope rich in structured governance data — `deprecated_framing_rules`, `approval_state`, `consumer_contexts`, `requires_disambiguation`, `governance_facts`, the F3 execution-context block. The directives are clear at the field level: "this metric is deprecated; use canonical_version v3," "this metric is in pending_review; surface the policy directive," "this metric requires disambiguation; use default_scope and disclose."

What's still missing is response-format adherence. The LLM sees the per-metric directives but doesn't have a worked example of how the final response should look — what shape the F1 prose-with-SQL contract takes when each directive applies, how the warnings array should be phrased, how the SQL should structurally relate to the canonical_version, how the rationale should reference the envelope field that drove the choice.

This is a classic prompt-engineering finding: few-shot beats zero-shot for response-format adherence. The model can be told "respond in this shape" and still drift; it can be shown "respond like this for scenario X, like this for scenario Y" and the drift collapses. The plan's §3 Priority 7 calls F7 a "modest lift on every category" — not a category-specific tactic like F2-F6, but a polish layer that lifts every governance-aware response toward the F1 contract's canonical shape.

The shape question for F7 is whether to surface examples per metric (one set per `metrics.id`) or per (policy_tier, mode) cell (one set in a global catalog joined to metrics by tier). The per-metric framing is the obvious one but redundant: the example for "Financial × warn-and-redirect" is the same useful pattern whether the metric is `arr` or `mrr` or `revenue`. Authoring per metric would produce ~20× redundancy across the test org's metrics for zero new information. The catalog framing collapses the redundancy: one example per cell, joined at envelope serialization time.

## Decision

Add a new table `public.policy_tier_examples` carrying ideal-response few-shot examples per `(policy_tier, mode, ordinal)` cell, surfaced as the v1 envelope `policy_examples` field of type `PolicyTierExample[]`.

The envelope-facing shape is:

```json
{
  "policy_examples": [
    {
      "scenario": "User asks for the deprecated metric by name",
      "policy_tier": "Financial",
      "mode": "warn-and-redirect",
      "ideal_response": "{\"warnings\": [\"The 'arr v1' framing was retired after a $4M GL discrepancy in Q3-2025. I'll use the current canonical v3.\"], \"clarification_request\": null, \"sql\": \"SELECT SUM(mrr)*12 AS arr_v3 FROM ...\", \"rationale\": \"deprecated_framing_rules.canonical_action = use_current_definition; canonical_version = v3.\"}"
    }
  ]
}
```

Each example carries:
- `scenario` — author-supplied editorial label.
- `policy_tier` — TitleCase enum matching `metrics.policy_tier`.
- `mode` — one of six PolicyExampleMode values (`warn-and-redirect`, `warn-and-defer`, `answer-with-caveat`, `ask-clarification`, `answer-direct`, `answer-with-default-scope-and-disclose`).
- `ideal_response` — JSON STRING conforming to F1's prose-with-SQL contract (`{warnings, clarification_request, sql, rationale}`). The text is preserved verbatim — it's an example response shape the LLM should pattern-match.

For tiers with no authored examples, `policy_examples` is `[]`. The catalog is sparse — not every (tier × mode) cell needs an example, only the cells the test org's metrics actually exercise. The field is **always an array** (never null) so consumers iterate defensively without a null check.

The field is **additive** to the existing `MetricDetailV1Response`. Legacy v1 consumers can ignore it. F7-aware consumers read the examples (when present) and incorporate them into the F1-shaping prompt context.

## Why a CATALOG table (not per-metric)

This is the load-bearing decision. Three forces favored the catalog framing:

1. **Redundancy collapse.** The example for `Financial × warn-and-redirect` is the same useful pattern across every Financial metric whose deprecated_framing_rules fires. Authoring per metric would multiply by ~20× (one example per metric per tier per mode) for zero new information. The catalog row is authored once per (tier, mode) cell and joined at envelope-serialization time by the metric's `policy_tier`.

2. **Lifecycle differs from F2-F6 directives.** The F2-F6 child tables are per-metric editorial content (`deprecated_framing_rules` per-metric trigger patterns, `requires_disambiguation` per-metric ambiguity surface). They evolve as a metric's authentic governance shape evolves. The F7 examples are a centrally-authored body of best-practice F1-contract patterns. They evolve as the F1 contract evolves or as authors discover a new useful response shape — both are global concerns, not per-metric ones.

3. **Authoring scale.** A per-metric F7 catalog would require an author to think about every (metric × tier × mode) cell — for the test org, that's 20 metrics × 3 tiers × 6 modes = 360 cells. Most of those would be authored as duplicates of a small underlying set of patterns. Centralizing the authoring as a per-(tier, mode) catalog cuts the surface to 18 cells and eliminates the duplication burden. Future workflow tooling for editing the catalog can land cleanly on the (tier, mode) shape.

The catalog framing has one tradeoff: per-metric overrides aren't possible without a follow-up "override table" (e.g. `metric_policy_examples_overrides` keyed by `(metric_id, mode)` joined ahead of the catalog at request time). That's the correct shape for production integrations that want per-metric customization — and it's strictly additive over F7's catalog. V2.1 doesn't ship the override table; the catalog is enough.

## Why `ideal_response_json` is text (not JSONB)

This is the second load-bearing decision and the one most likely to be questioned in code review.

The field's whole purpose is to carry the EXACT wire format of the F1 prose-with-SQL contract — a JSON string the LLM pattern-matches its own response against. Three considerations point at text storage:

1. **Byte-for-byte preservation.** Storing as JSONB would round-trip through PostgREST's JSON parser at read time, normalize whitespace and key order, and strip any whitespace-significant formatting the example author chose. Text storage preserves the author's literal shape — including key order, indentation choices, escape character placement, etc. The LLM can pattern-match against `{"warnings": ["..."]}` more reliably when the byte sequence is exactly what the author wrote.

2. **No structural consumption at the route layer.** The route serializer doesn't consume `ideal_response` as an object — it just plugs the text onto the envelope. JSONB would force a `::text` cast on every read with no win. The DB CHECK still enforces non-emptiness so authors can't ship a literally-empty value.

3. **Diverges intentionally from F2-F6.** The F2-F6 features store structured data (default_scope dictionary, blockers array, etc.) as JSONB because the route serializer DOES consume them as objects to project into the envelope. F7's payload is consumed as a literal string. The storage format follows the consumption pattern.

Trade-off: a JSONB-side validator could reject an example whose JSON doesn't parse. We mitigate at the seed-author layer instead — `build_policy_tier_example_rows()` runs `json.loads()` on every entry at author time and fails-fast with a clear pointer if the F1 string is malformed. The DB CHECK only enforces non-emptiness; structural validation lives where it can produce a useful error message.

## Why six modes in the enum

The `mode` CHECK enum carries six values that map to the F1-F6 response shapes plus the canonical happy path:

| Mode | Maps to | Example response shape |
|------|---------|------------------------|
| `warn-and-redirect` | F2 deprecated_framing_rules | warnings present, sql uses canonical_version |
| `warn-and-defer` | F4 approval_state pending | warnings surface pending status, sql uses best-available |
| `answer-with-caveat` | F5 consumer_contexts pinned-version | warnings disclose pin, sql uses pinned version |
| `ask-clarification` | production-mode F6 | clarification_request set, sql is canonical fallback |
| `answer-direct` | canonical happy path | warnings empty, sql is direct canonical |
| `answer-with-default-scope-and-disclose` | F6 benchmark mode | warnings disclose assumption, sql uses default_scope |

`ask-clarification` is the production-mode counterpart to F6's benchmark-mode `answer-with-default-scope-and-disclose`. It's NOT exercised by the v2.1 benchmark (no production org's seed authors `ask-clarification-first` rows in `metric_disambiguation`), but it's in the enum so the catalog can carry an example for production integrations that DO opt into it. The example honors F1's contract by including a fallback SQL — the H1 numeric is preserved even when the LLM is asking for clarification.

`answer-direct` is the canonical happy path — APPROVED metric, no ambiguity, no deprecation, no pending blocker. Without an explicit example for this mode the LLM might over-warn (surface a governance signal where none is warranted). The example anchors "warnings: []" as a valid F1 response shape.

Future modes are additive — only require a CHECK constraint update on `policy_tier_examples.mode` and a new entry in the `PolicyExampleMode` TypeScript union.

## Alternatives considered

### Option A: catalog table `policy_tier_examples` keyed by (policy_tier, mode, ordinal), text storage for ideal_response_json (chosen)

**Mechanics.** New table with three-column UNIQUE (`policy_tier, mode, ordinal`). No metric_id column — the table is org-agnostic. RLS allows authenticated SELECT (same shape as `observe_loop` catalog from migration 20260121000000) and service-role writes only. Repository function keyed by `(tier, optional mode)` rather than `metric_id`. Route serializer fans out per metric using `metric.policy_tier`.

**Why we picked it:**

1. **Shape matches data shape.** Examples per (tier, mode) cell are the editorial unit. The (policy_tier, mode, ordinal) UNIQUE makes the cardinality explicit — multiple examples per cell allowed via ordinal, but no duplicate (tier, mode, ordinal) triple. The route serializer fetches by `WHERE policy_tier = $1` for the per-metric envelope.
2. **Org-agnostic by design.** No `org_id` column; the catalog is global. Future per-org overrides land as a separate table joined ahead of the catalog at request time — strictly additive.
3. **Lifecycle matches authoring rhythm.** Centrally authored content evolves with the F1 contract, not per metric. The seed pattern is the canonical authoring path; future workflow tooling for editing the catalog can land cleanly on the (tier, mode) shape.
4. **Text storage preserves byte-for-byte fidelity.** The LLM consumes `ideal_response` as a literal pattern; JSONB normalization would silently rewrite the author's shape. Text is the right primitive.

### Option B: per-metric examples table `metric_policy_examples` keyed by (metric_id, mode)

Rejected per §"Why a CATALOG table" above. Three independent forces favored the catalog: redundancy collapse (~20× saving for zero new information), lifecycle mismatch (F7 is centrally authored, F2-F6 are per-metric), and authoring scale (18 cells vs 360). The catalog wins on all three.

If V2.1's benchmark re-run shows a per-metric tuning need (e.g. specific metrics where the (tier, mode) catalog example doesn't fit the metric's actual response shape), a sibling override table `metric_policy_examples_overrides` keyed by `(metric_id, mode)` joined AHEAD of the catalog at request time is the cleanest follow-up — strictly additive over the catalog. V2.1 doesn't ship it.

### Option C: JSONB column `metrics.policy_examples`

Rejected. Would force per-metric storage (same redundancy problem as Option B), couple the F7 catalog to the metrics row (no clean per-org override path), and lose the byte-for-byte fidelity that text storage preserves (any update would re-serialize the JSONB and normalize whitespace). Would also make the (policy_tier, mode, ordinal) UNIQUE shape unenforceable at the schema level.

### Option D: JSONB storage for ideal_response_json (with structural validation)

Rejected per §"Why ideal_response_json is text". The route serializer doesn't consume the value as an object; JSONB would normalize whitespace and key order on every read. The seed-author layer's `json.loads()` validation produces clearer errors than a DB CHECK on JSONB structure would, and the text storage format leaves room for future authors to use whitespace meaningfully (e.g. multi-line SQL bodies preserved verbatim).

### Option E: ship F7 as a static prompt asset (file in `apps/web/lib/prompts/`)

Rejected. A static prompt asset would (a) couple the F7 catalog to the web-app deploy cadence rather than the metric-authoring cadence, (b) bypass the per-(tier, mode) join that lets the route surface only the relevant examples, (c) produce a strictly-larger prompt for every metric request even when only one (tier, mode) cell is relevant, and (d) make per-org overrides essentially impossible. The catalog table preserves all of those properties.

### Option F: defer F7 to v3 (skip the polish layer)

Considered. The plan's §3 Priority 7 explicitly calls F7 "modest lift on every category" — smaller than F1's removal of the SQL-only floor or F2's targeted Drift lift. The argument for shipping it now is that it's the LAST V2.1 product feature gate (per the brief), the engineering surface is small (one table + one repo + one route fan-out + one catalog), and the cumulative effect of F1-F7 is the apples-to-apples comparison against v2.0. Deferring would mean re-running B4.2 against an incomplete plan — the v2.1 paper's narrative is "v2.0 → v2.1 with all features," not "v2.0 → v2.1 with most features."

## Schema details

### Table shape

```sql
CREATE TABLE public.policy_tier_examples (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_tier text NOT NULL CHECK (policy_tier IN ('Experimental', 'Operational', 'Financial')),
    mode text NOT NULL CHECK (mode IN (
        'warn-and-redirect', 'warn-and-defer', 'answer-with-caveat',
        'ask-clarification', 'answer-direct', 'answer-with-default-scope-and-disclose'
    )),
    scenario text NOT NULL CHECK (length(trim(scenario)) > 0),
    ideal_response_json text NOT NULL CHECK (length(trim(ideal_response_json)) > 0),
    ordinal integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (policy_tier, mode, ordinal)
);
```

### `policy_tier` CHECK enum mirrors `metrics.policy_tier`

Same TitleCase value space (`'Experimental'`, `'Operational'`, `'Financial'`) used by `metrics.policy_tier` (CHECK from migration 20260226000004 + 20260502000000 rename). A typo at author time would silently produce orphan examples that no metric joins to; the CHECK fails-fast at INSERT.

The `Tier` enum in `@cl/core/src/model/base.ts` uses LOWERCASE values (`"experimental"`, `"operational"`, `"financial"`) as its TypeScript-side representation. The new `PolicyTier` type added in this ADR uses TitleCase to match the wire shape directly — a typo in authoring code is a TypeScript error rather than a CHECK violation at INSERT time. The two types co-exist; the lowercase `Tier` is reserved for legacy callers, the TitleCase `PolicyTier` is what the F7 envelope ships.

### `mode` CHECK enum lists six values

See §"Why six modes in the enum" above. Future modes are additive — only require a CHECK update + TypeScript union update.

### `ideal_response_json` is text NOT NULL CHECK (length > 0)

Stored as text per §"Why ideal_response_json is text". The DB CHECK enforces non-emptiness; the seed-author layer enforces JSON parseability. No JSONB structural validation — the text is consumed as-is by the LLM.

### `ordinal` integer NOT NULL DEFAULT 0

Stable sort order per (policy_tier, mode) cell. Multi-example cells use ordinals 0, 1, 2... — the route serializer's `ORDER BY mode, ordinal` makes the order author-deterministic across reseeds. Single-example cells use the default 0. The `UNIQUE (policy_tier, mode, ordinal)` makes per-cell ordinal collisions impossible.

### RLS — authenticated SELECT, service-role writes only

The catalog is org-agnostic "best-practice" content. Authenticated callers can read every catalog row (the v1 envelope serializer reads via the service-role admin client, but the open SELECT keeps the table readable from anywhere a workspace-admin UI lives). INSERT / UPDATE / DELETE go exclusively through the service-role client (the seed script). This mirrors the `observe_loop` catalog pattern in migration 20260121000000 §76-94 (open SELECT to authenticated, writes via service_role only). Note the difference from the F2-F6 child tables, which RLS by org-membership — F7 has no `org_id` to filter on.

## Forward-compat / fallback semantics

When a consumer reads `policy_examples` and the value is `[]`, the convention is: **no authored examples for this metric's policy_tier; the LLM should fall back to the F1 contract directly without few-shot anchoring**. This makes F7 strictly additive: an LLM consumer that doesn't know about F7 sees the empty array for tiers without examples and behaves as before; an F7-aware consumer reads the examples (when present) and incorporates them into the F1-shaping prompt context.

The defense-in-depth catch in the route layer degrades a thrown repository error to `policy_examples: []`, which means a transient DB failure for the F7 query gracefully degrades to "no examples" rather than 500-ing. This mirrors the F2 `catchList("DeprecatedFramingRules")`, F4 `pendingStateCatch`, F5 `consumerContextsCatch`, and F6 `disambiguationCatch` patterns. The result-shape error path (when `policy_examples_result.error` is set) is also handled explicitly via an IIFE that logs the error and returns the empty default — the same pattern F6 introduced for visibility into result-shape failures.

### Forward-compat with future workflow tooling

- **Workspace-admin UI for editing the catalog.** A simple table view of `policy_tier_examples` rows grouped by `(policy_tier, mode)` would let internal authors add / edit / reorder examples without touching the seed script. Out of scope for V2.1; the seed pattern is the canonical authoring path. The migration's RLS shape supports this: authenticated SELECT lets a workspace-admin UI read the catalog; service-role writes preserve the audit boundary.
- **Per-org override table.** Production integrations that want per-metric customization would author rows in a new `metric_policy_examples_overrides` table keyed by `(metric_id, mode)`, joined ahead of the catalog at request time so per-metric overrides win over catalog defaults. Storage shape is straightforward; UI is the missing piece. Out of scope for V2.1.
- **Per-org catalog overrides.** A separate `org_policy_tier_examples` table keyed by `(org_id, policy_tier, mode, ordinal)` would let an org override the global catalog for specific cells without touching the global table. Same join-ahead pattern. Out of scope for V2.1.
- **Versioning / rollback.** A future migration could add `version` and `previous_version_id` columns to track example evolution. The current shape doesn't preclude that — the columns would be additive.

## Consequences

### Enables

- **Response-format adherence lift across every governance-aware response.** Few-shot examples per (tier, mode) cell give the LLM concrete patterns of what an F1-contract response should look like for each governance-response shape. Should produce the plan's "modest lift on every category" — not category-specific like F2-F6, but a polish layer that lifts every governance-aware response toward the canonical F1 shape.
- **Catalog as central authoring surface.** Future authors can add examples to `POLICY_TIER_EXAMPLES_CATALOG` without touching per-metric content. The catalog is the single source of truth for "what does an ideal F1 response look like for a given (tier, mode) scenario."
- **Forward-compat for per-org overrides.** The catalog framing leaves room for per-org override tables that join ahead at request time. Production integrations that want per-metric or per-org customization have a clean path.
- **Explicit `answer-direct` mode anchor.** Without an example for the canonical happy path, the LLM might over-warn (surface a governance signal where none is warranted). The `answer-direct` examples anchor "warnings: []" as a valid F1 response shape.

### Blocks

Nothing. The `policy_examples` field is additive; legacy v1 consumers can ignore it. F7-aware consumers see `[]` for tiers without examples and branch on the array's contents when present.

### Limitations / risks

1. **No per-metric overrides in V2.1.** The catalog framing means every Financial metric sees the same `Financial × warn-and-redirect` example. Most of the time that's the right pattern, but a metric with an unusually-shaped warning (e.g. a metric whose deprecation reason is fundamentally different from the catalog's example) might benefit from a per-metric override. Mitigation: the per-metric override table is a clean follow-up; V2.1 doesn't need it.
2. **Catalog quality is editorial.** A poorly-authored example (e.g. SQL that doesn't compile, rationale that doesn't reference the envelope field) would degrade rather than lift the response quality. Mitigation: the seed-author layer's JSON parseability check catches malformed F1 strings; future workflow tooling can add structural validators (e.g. SQL parseability via DuckDB at author time).
3. **Forward-compat for ask-clarification mode is forward-looking.** The `ask-clarification` examples in the catalog won't be exercised by V2.1's benchmark (no production org's seed authors `ask-clarification-first`). The risk is that the example shape drifts from production usage by the time someone authors a production-mode metric. Mitigation: the catalog is centrally authored, so a future audit can update the examples as the pattern matures.
4. **Operational tier coverage is thin.** The test org has no Operational metrics; we author one example per Operational mode for forward-compat. If a production org seeds many Operational metrics, the catalog will need additional examples for the modes those metrics exercise. Mitigation: the catalog is additive — adding examples doesn't break existing consumers.
5. **The `mode` value the example illustrates isn't structurally enforced against the metric's actual governance state.** A metric whose `approval_state` is null might still see a `warn-and-defer` example — the LLM has to figure out from the example's `scenario` label that the example doesn't apply to the current request. Mitigation: future per-mode filtering at the route layer (e.g. only surface `warn-and-defer` examples when `approval_state` is set) is a clean follow-up. V2.1 surfaces all examples for the metric's tier; the LLM does the per-request filtering.

## Follow-ups

1. **Phase 4 / V2.1-RR re-run** — F7 is the LAST product feature gate. The next dispatch is the Trust Benchmark v2.1 re-run (B4.2 against the v2.1 envelope). See `brain/product/clarilayer-v2.1-governance-response-features.md` §4.
2. **Per-metric override table** — `metric_policy_examples_overrides` keyed by `(metric_id, mode)`. Storage is straightforward; UI is the missing piece. Follow-up ADR.
3. **Per-org catalog overrides** — `org_policy_tier_examples` keyed by `(org_id, policy_tier, mode, ordinal)`. Same join-ahead pattern as the per-metric overrides. Follow-up ADR.
4. **Workspace-admin authoring UI** — table view of `policy_tier_examples` grouped by `(policy_tier, mode)`. Out of scope for V2.1; the seed pattern is the canonical authoring path. Follow-up ADR when the UI surface is designed.
5. **Per-mode example filtering at the route layer** — surface only the examples whose `mode` matches the metric's actual governance state (e.g. only `warn-and-defer` examples when `approval_state` is set). V2.1 surfaces all examples for the metric's tier; the per-mode filtering is a tightening that requires the route to read all sibling envelope fields first. Follow-up ADR.
6. **Versioning columns on `policy_tier_examples`** — `version` + `previous_version_id` to track example evolution. Follow-up ADR.
7. **SQL parseability validation at author time** — run authored SQL through DuckDB to catch syntactic errors. The seed-author layer currently only validates JSON parseability. Follow-up sprint.

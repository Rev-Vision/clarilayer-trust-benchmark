# ADR-0020 — Query-conditioned `deprecated_framing_rules` (Trust Benchmark v2.1, Drift lift)

- **Status:** Accepted
- **Date:** 2026-05-03
- **Sprint context:** Trust Benchmark v2.1 — F2 (codex-priority #2)
- **Stacks on:** B0.2 (ADR-0018, `policy_tier` rename), B0.3 (ADR-0019, `definition_version` rename), `metric_relationships` + ADR-0017 (replaces-edge reason capture)
- **Migration:** `supabase/migrations/20260503000000_v2_1_f2_metric_deprecated_framing_rules.sql`
- **Tracking:** `brain/product/clarilayer-v2.1-governance-response-features.md` §3 priority 2

## Context

Trust Benchmark v2 (5 stability sweeps × 3 models × 5 baselines × 120 questions = 10,800 calls) measured ClariLayer's Drift category PASS rate at **5.3%** — only 19 of 360 Drift responses produced `canonical-with-rejection`. The remaining 341 either silently complied with the deprecated framing (164 silent-canonical) or returned the deprecated answer outright (177 deprecated). The metric envelope already carried the deprecation lineage (via `metric_relationships.replaces` edges + `metric_versions` history), but the LLM had to **infer** when to reject the user's framing from descriptive text and structured-but-passive metadata.

The v0.1 sketch of the v2.1 plan proposed a static per-metric `mode: warn-and-redirect` enum to make the directive explicit. Codex review (see plan §1c, codex finding D-012) flagged this as too blunt:

> Per-metric static policies fire on every request. A metric with deprecated versions can be requested *normally* — the warning shouldn't trigger when the user asks for the canonical version, only when their query references the deprecated framing.

That observation is the entire ADR.

## Decision

Add a new table `public.metric_deprecated_framing_rules` carrying **query-conditioned** rules: each rule has a list of `trigger_patterns` and a `trigger_match_mode` (`any` / `all` / `regex`). The consumer harness combines patterns at request time; rules fire only when the user's query matches.

The envelope-facing shape is:

```json
{
  "deprecated_framing_rules": [
    {
      "rule_id": "arr-v1-trigger",
      "trigger_patterns": ["v1", "no filters", "include test customers", "raw arr"],
      "trigger_match_mode": "any",
      "reject_because": "ARR v1 was retired after a $4M GL discrepancy in Q3-2025.",
      "canonical_action": "use_current_definition",
      "canonical_version": "v3",
      "rejection_template": "I'll use the current canonical {canonical_version} ({reject_because})."
    }
  ]
}
```

The field is **additive** to the existing `MetricDetailV1Response`. Empty array is the common case (metrics without a deprecation lineage). Legacy v1 consumers can ignore it.

## Alternatives considered

### Option A: separate table with query-conditioned rules (chosen)

**Mechanics.** New table `public.metric_deprecated_framing_rules` keyed by `(metric_id, rule_id)`, FK to `metrics(id) ON DELETE CASCADE`. Same RLS shape as `metric_relationships` / `metric_approvals` (org-scoped SELECT; writes via the service-role repo layer). Surfaces in the envelope serializer alongside `relationships` / `approvals` / `versionHistory`.

**Why we picked it:**

1. **One metric : N rules.** Several metrics in the benchmark dataset have multiple deprecated versions (e.g. `arr` has v1 and v2 retired; `active_user` has v1, v2, and v3-pre retired). Each retired version typically needs its own trigger pattern set targeting a different framing. A child table makes the 1:N relationship natural.
2. **RLS parity.** The codebase already runs three child-of-metric tables (`metric_relationships`, `metric_approvals`, `metric_contracts`) on the same SELECT-only RLS pattern. A new child table inherits the pattern; a new JSONB column on `metrics` would inherit `metrics`' broader RLS surface.
3. **Indexability.** A `metric_id` B-tree index supports the parallel-fan-out fetch in the v1 detail route. JSONB arrays are not indexable on this access shape.
4. **Future authoring UI.** The eventual workspace-admin UI for authoring rules wants a row-per-rule grid. Modeling rules as table rows is the obvious shape; modeling them as JSONB array elements would force an emulation.
5. **Forward-compat.** New `canonical_action` values, new audit columns (`authored_by`, `last_validated_at`), and per-rule analytics (rule-fire counts) are all easier to add to a real table than to a JSONB array element.

### Option B: JSONB column on `metrics` (`metrics.deprecated_framing_rules JSONB`)

Rejected. Same shape would compile but loses (a) the (metric_id, rule_id) UNIQUE constraint at the schema level, (b) the natural CASCADE-on-metric-delete, (c) authoring-UI mapping, (d) per-rule analytics. The on-the-wire envelope shape is identical so consumers can't distinguish A from B; everything else favors A.

### Option C: Static per-metric `mode` enum on `metrics` (the v0.1 plan sketch)

Rejected per codex review. The static mode fires on every request. A metric with `mode='warn-and-redirect'` would warn on questions like "what's the current ARR definition?" — an obviously legitimate query for the canonical version. Query-conditioned rules give the LLM an explicit per-metric directive that fires only when the framing actually references a retired version.

### Option D: Per-version rule embedded in `metric_versions` (the warehouse table)

Rejected. The `metric_versions` table is a *warehouse* surface (compiled SQL output, used for the `metric_versions.version` lineage column). Mixing governance rules into a warehouse-side table conflates the responsibilities surfaced in the v2 collision discovery (ADR-0019). Rules are a *control-plane* concern; they belong in `public.*` next to `metric_relationships`.

## Schema details

### `trigger_match_mode` semantics

- **`any`** (default) — at least one pattern matches the user's query. Broadest fire surface; suitable for rules with several distinct trigger phrases pointing at the same deprecated framing.
- **`all`** — every pattern must match. Stricter; suitable for rules that need multiple cues (e.g. v2 triggers that need both an explicit version reference AND a symptomatic framing).
- **`regex`** — each pattern is treated as a regex by the consumer harness; first match wins. Forward-compat — runtime regex evaluation lives in the consumer harness, not the DB. This avoids the operational cost of letting the DB compile arbitrary user regexes per row.

### `canonical_action` values

- **`use_current_definition`** (default) — substitute and answer with the canonical version. Covers the common deprecated→current routing case.
- **`refuse`** — refuse to answer; surface the rejection_template only. Reserved for cases where the deprecated framing is actively unsafe (e.g. a metric whose deprecated version is misleading enough that no answer is preferable to the deprecated one).
- **`warn_only`** — answer with the deprecated framing but surface the rejection_template as a warning. Reserved for cases where the user's framing is technically valid but the team wants visible disclosure (e.g. historical-period reconciliation where the deprecated version is still the right tool).

The enum is **additive**. Future v3 rules may add `audit_only`, `escalate_to_human`, etc. — adding values requires only a CHECK-constraint update.

### `canonical_version` nullability

Nullable because:
1. `canonical_action='refuse'` has no redirect target.
2. The author may want the LLM to read the version from `metric.definition_version` at request time (avoids the rule going stale when the metric's canonical version bumps).

The consumer's substitution layer is expected to resolve `{canonical_version}` from `metric.definition_version` when the rule's column is null.

### `rejection_template` substitution

Stored as a literal template — the consumer harness resolves `{canonical_version}`, `{reject_because}`, etc. at request time. The DB does **not** substitute. This keeps the DB layer free of templating concerns and lets the consumer evolve the substitution rules without DB migrations.

## Consequences

### Enables

- **Drift category lift.** Targeted bound: 5.3% → 15-25% (codex estimate per the v2.1 plan). The 341 silent-canonical + deprecated near-wins on E (out of 360 Drift rows) are the addressable surface — most of them HAVE the deprecation context but don't act on it.
- **Per-metric trigger authoring.** Each rule's `trigger_patterns` are sourced from the YAML's `governance_metadata.deprecated_versions[].reason`. The seed script (`benchmark/scripts/seed_test_org.py`) authors ~23 rules across 16 metrics with deprecation lineages.
- **Forward-compat with V2.1-F1 (prose-with-SQL contract).** When F1 lands, the response shape carries `warnings[]` and `rationale`. A fired rule's `rejection_template` is a natural fit for the `warnings` slot; the canonical SQL substitution drives the `sql` field.

### Blocks

Nothing. The field is additive.

### Limitations / risks

1. **Trigger-pattern false positives.** A rule with `match_mode='any'` and a broad pattern (e.g. just `"v1"`) could fire on questions that mention "v1" coincidentally (e.g. "show me v1 vs v2 numbers side by side"). Mitigation: prefer 2-3 distinctive phrases per rule. The seed catalogue authored here uses the most distinctive symptomatic phrases from each YAML's deprecation reason, not generic version literals alone.
2. **Trigger-pattern staleness.** If a YAML's `deprecation_reason` evolves but the rule's `trigger_patterns` aren't updated, the rule may miss new framings. Mitigation: the seed script is the single source of truth for the test org; production orgs author rules through (eventual) admin tooling. Per-rule audit columns (`last_validated_at`) are a v3 follow-up.
3. **Match-semantics drift between consumers.** The DB stores `trigger_match_mode` as a string; the consumer harness implements the actual matching. Two consumers could disagree on what `match_mode='regex'` means (PCRE vs POSIX vs JavaScript regex). Mitigation: documented expectation that the canonical consumer is the benchmark harness; other consumers must mirror its matching semantics. A future v3 rule may pin a regex flavor explicitly.

## Follow-ups

1. **V2.1-F1 (prose-with-SQL contract)** — when the harness changes its response contract, document the rule-fire→`warnings`-slot mapping in the harness's user-message template.
2. **Workspace admin UI for rule authoring** — out of scope for V2.1-F2; rules are seeded via `seed_test_org.py` for the benchmark workspace. Production orgs need authoring tooling before this lands more broadly.
3. **Per-rule fire analytics** — a follow-up table `metric_deprecated_framing_rule_fires` (rule_id, query_hash, fired_at) would let teams see which rules are firing in production. v3 question; out of scope here.
4. **Match-flavor pin** — extend `trigger_match_mode` to encode the regex flavor explicitly when needed (e.g. `regex_pcre`, `regex_posix`). Additive enum change; mechanically the same CHECK-constraint update.

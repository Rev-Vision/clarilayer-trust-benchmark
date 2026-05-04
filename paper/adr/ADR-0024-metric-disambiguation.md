# ADR-0024 — Metric `requires_disambiguation` envelope field with answer-with-default-scope-and-disclose policy (Trust Benchmark v2.1, Ambiguity lift)

- **Status:** Accepted
- **Date:** 2026-05-03
- **Sprint context:** Trust Benchmark v2.1 — F6 (codex-priority #6)
- **Stacks on:** ADR-0020 (`metric_deprecated_framing_rules` — V2.1-F2), ADR-0021 (execution metadata + `governance_facts` — V2.1-F3), ADR-0022 (`approval_state` directive — V2.1-F4), ADR-0023 (`consumer_contexts` per-surface pins — V2.1-F5)
- **Migration:** `supabase/migrations/20260503000400_v2_1_f6_metric_disambiguation.sql`
- **Tracking:** `brain/product/clarilayer-v2.1-governance-response-features.md` §3 priority 6

## Context

Trust Benchmark v2 measured ClariLayer's Ambiguity category PASS rate at **2.8%** (10 of 360 questions) — the **only** category where E loses to Cube/dbt (both at 4.2%, a 1.4pp gap). Codex's high-signal pattern from the v2 raw data: E's 10 Ambiguity passes are ALL on `ambiguity-gross_margin-001`; C/D pass that same question 15/15. So Ambiguity scoring is dominated by one question, and the gap is real.

Codex's hypothesis for the gap: ClariLayer's rich envelope causes **envelope-induced overconfidence**. The LLM finds enough in ClariLayer's text-rich envelope to anchor on, produces a confident wrong answer instead of recognizing ambiguity, and slides past the question's actual scope-confusion shape. Ambiguity questions like "what's our ARR?" need the LLM to either (a) ask about region/segment/plan-tier scope OR (b) answer with the canonical default and disclose what was assumed.

The original v0.1 plan (the founder's hand draft) framed F6 as "ask-clarification-first": when a metric is ambiguity-prone, surface a `requires_disambiguation` block that tells the LLM to **ask for clarification before producing SQL**. Codex's review (D-012) flagged a fatal issue with this framing: **pure clarification-only responses crater H1's strict numeric scoring** because the H1 scorer needs an executable SQL with a numeric answer; "I need clarification" with no SQL = FAIL by definition.

The reframe: F6 surfaces the disambiguation directive AND a canonical default scope. The LLM produces SQL using the default scope AND discloses what was assumed in F1's prose-with-SQL contract (`warnings` / `clarification_request` field). The disclosure preserves the trust signal — the user is told what default was assumed and how to narrow scope. The SQL preserves the H1 numeric answer.

## Decision

Add a new table `public.metric_disambiguation` carrying per-metric ambiguity-handling directives, surfaced as the v1 envelope `requires_disambiguation` field of type `MetricDisambiguation | null`.

The envelope-facing shape is:

```json
{
  "requires_disambiguation": {
    "missing_dimensions": ["region", "product_line"],
    "default_scope": {
      "region": "all_regions",
      "product_line": "all_product_lines"
    },
    "disclosure_template": "Answered for all regions and product lines (no scope specified). Specify region/product_line to narrow.",
    "policy": "answer-with-default-scope-and-disclose"
  }
}
```

For metrics **without** ambiguity surface — single-definition / single-scope metrics like `payback_period`, `new_user`, `pipeline_coverage` — `requires_disambiguation` is `null`. The common case is lean: no row in `metric_disambiguation`, no token bloat on the envelope. (`bookings` is NOT in this list — the seed catalog authors a `metric_disambiguation` row for it because of the recognition-basis ambiguity, defaulting to committed-contract value with the `recognition_basis=billed` opt-out disclosed.)

The field is **additive** to the existing `MetricDetailV1Response`. Legacy v1 consumers can ignore it. F6-aware consumers read the disambiguation directive (when present) and substitute `disclosure_template` into F1's `warnings` / `clarification_request` field while producing SQL using `default_scope`.

## Why "answer-with-default-scope-and-disclose" not "ask-clarification-first" (codex finding D-012)

This is the load-bearing decision and the one that motivated the codex review's REQUEST-CHANGES on the v0.1 plan.

The H1 scoring rubric requires an executable SQL with a numeric answer. The original framing — "tell the LLM to ask for clarification before producing SQL" — would produce responses like:

> "I need to clarify scope first. Should I include all regions or a specific region? All segments or just enterprise?"

These responses score 0 on H1 numeric. Even though they're the *right* product behavior in a production interactive UX, they're the *wrong* benchmark behavior under the strict numeric scoring rubric.

The reframe lets the LLM:

1. **Recognize the ambiguity** (the `missing_dimensions` array tells it what to think about).
2. **Produce SQL** using a canonical default scope (the `default_scope` map gives it the values to use). This is the H1-compliant numeric answer.
3. **Disclose the assumption** in F1's contract (the `disclosure_template` is the trust signal, substituted into `warnings` / `clarification_request`). This preserves the governance signal — the user knows what default was assumed and how to narrow.

The result is an answer that scores on H1 numeric AND carries the trust disclosure that distinguishes E from raw text-to-SQL baselines. The `policy` enum carries the directive value (`answer-with-default-scope-and-disclose`); a production-mode `ask-clarification-first` value exists in the enum for non-benchmark consumers that prefer the strict ask-first UX.

The benchmark exclusively exercises `answer-with-default-scope-and-disclose`; the seed catalog (`DISAMBIGUATION_OVERRIDES`) authors only that policy value. Production integrations that opt into `ask-clarification-first` would author the production-mode value per metric — that storage path is in place but not exercised by V2.1.

## Why a separate envelope field, not an extension of `governance_facts`

F3c's `governance_facts` is the **"what's true now"** snapshot — owner_team, approver_role, approver_user, approved_at, lifecycle_status, current_version, effective_from, effective_to, next_review_date. It answers "should you trust this metric?" and "what version is canonical?" The fields are aggregated from existing data on `metrics` / `metric_approvals` / `releases` — no new data, just structured surfacing.

F6's `requires_disambiguation` is the **"how to scope when the user's question doesn't say"** directive. It answers "what dimensions does this metric vary along?" and "what defaults should I use?" The data is genuinely new (`missing_dimensions`, `default_scope`, `disclosure_template`) and authored per metric — there's no existing source to aggregate from.

The two concerns differ on three axes:

1. **Audience inside the LLM consumer.** `governance_facts` answers structured questions about the metric ("what version is current?"). `requires_disambiguation` injects scope-handling into the response generation ("when scope isn't specified, use these defaults and disclose"). The LLM consumes them at different stages of the prompt.
2. **Authoring source.** `governance_facts` is auto-derived from existing structured data. `requires_disambiguation` is hand-authored per metric — the dimension list, the canonical defaults, and the disclosure template are all editorial choices about the metric's authentic ambiguity surface.
3. **Lifecycle.** `governance_facts` updates whenever the metric's underlying approval / release state changes. `requires_disambiguation` updates rarely — only when a metric's authentic ambiguity surface changes (e.g. the metric gets a new dimension authors should think about).

Folding F6 into `governance_facts` would (a) bloat the F3c contract every metric carries with mostly-null fields, (b) conflate "what's true" with "how to scope," and (c) force the LLM to read both concerns from the same prompt block when they belong at different stages of the response generation.

## Why `default_scope` is JSONB (not text[] or a normalized child table)

`default_scope` is dimension_name -> default_value, where today the value is always a string (e.g. `"all_regions"`, `"logo_definition"`, `"MAU"`). JSONB rather than `Record<string, string>` for two reasons:

1. **Forward-compat for richer values.** A future authoring round may want numbers (e.g. `attribution_window: 30`), booleans (e.g. `include_test_accounts: false`), or nested objects (e.g. `timezone: {tz: "UTC", fold: "civil"}`). JSONB preserves that extensibility without a future migration; `Record<string, string>` would lock us in. The TypeScript type signals the current shape (`Record<string, string>`); the wire shape (JSONB) is forward-compatible.
2. **F3's lesson learned.** Codex's CodeRabbit review of F3's `resolved_parameters` flagged the absence of a `jsonb_typeof = 'object'` CHECK as a footgun — without it, a seed author can accidentally store an array or scalar where a key-value object is required. The F6 migration includes that CHECK from day one (`CHECK (jsonb_typeof(default_scope) = 'object')`).

The DB CHECK enforces the object-shape invariant; the seed-author layer (`build_disambiguation_rows`) enforces non-empty per-key contents to fail-fast at author time with a clearer message than a CHECK violation at INSERT time. The author validation also enforces that every `missing_dimensions` entry has a corresponding `default_scope` key — a missing default would leave the LLM with nothing to fall back to.

## Why two policies in the enum (benchmark + production modes)

The enum has two values:

- `answer-with-default-scope-and-disclose` — the **benchmark mode** (default). The LLM produces SQL using `default_scope` AND surfaces `disclosure_template` in the F1 contract's `warnings` / `clarification_request` field. SQL is always non-null — non-negotiable for H1 numeric scoring per codex finding D-012.
- `ask-clarification-first` — a **production-mode UX** where the consumer prefers an interactive clarification round-trip BEFORE producing SQL. NOT exercised by the benchmark; left in the enum so production integrations can opt in per metric.

The dual-mode enum exists because the production user experience and the benchmark scoring rubric pull in opposite directions:

- **Production users** often prefer "what did you mean?" before getting an answer. A board-deck owner asking "what's our ARR?" probably wants to confirm region/segment scope before seeing the number.
- **Benchmarks** strictly score the executable SQL. A "I need clarification" response with no SQL = FAIL.

Keeping both modes in the enum (with the benchmark-correct mode as the DEFAULT) lets the same authoring layer serve both consumer types. The seed catalog exclusively authors the benchmark mode; production integrations could later author rows with `ask-clarification-first` for metrics where the interactive UX matters more than the H1 scoring.

Future modes are additive — only require a CHECK constraint update on `metric_disambiguation.policy` and a new entry in the `DisambiguationPolicy` TypeScript union.

## Alternatives considered

### Option A: separate table `metric_disambiguation` with envelope `requires_disambiguation` field, default policy `answer-with-default-scope-and-disclose` (chosen)

**Mechanics.** New table keyed by `metric_id`. UNIQUE on `metric_id` enforces 1:1 cardinality (a metric has 0 or 1 disambiguation row). FK to `metrics(id) ON DELETE CASCADE`. RLS mirrors `metric_pending_state` / `metric_consumer_contexts` / `metric_deprecated_framing_rules` (org-scoped SELECT). Repository projects a row to a `MetricDisambiguation` record (or null) for the envelope.

**Why we picked it:**

1. **Shape matches data shape.** Each ambiguity-prone metric has exactly one disambiguation directive (the `missing_dimensions` set, the `default_scope` map, the `disclosure_template` string, the `policy` enum). 1:1 cardinality is enforced by `UNIQUE (metric_id)` at the schema level — a JSONB blob would lose that.
2. **RLS parity with F2 / F3 / F4 / F5.** The new table mirrors the existing SELECT-only RLS pattern (org-membership gate). A new column on `metrics` would inherit `metrics`' broader RLS surface.
3. **Indexability.** A `metric_id` B-tree index supports the parallel-fan-out fetch in the v1 detail route. JSONB-column access on `metrics` would not.
4. **Authoring lifecycle.** A disambiguation directive evolves rarely (only when a metric's authentic ambiguity surface changes). A row-update pattern is cleaner than null-out-the-key on a JSONB column, and the `updated_at` column gives clear provenance.

### Option B: extend `governance_facts` with the F6 fields

Rejected per §"Why a separate envelope field" above. The shapes don't align (governance_facts is single-value; requires_disambiguation is multi-field with a directive enum), the authoring sources differ (auto-derived vs hand-authored), and the LLM consumes them at different prompt stages (snapshot vs scope-handling).

### Option C: extend `metric_consumer_contexts` (F5) with a disambiguation key

Rejected. F5's table is keyed by `(metric_id, consumer_surface)` — N rows per metric. F6 is 1:1 (a metric has one disambiguation directive regardless of consumer surface). Folding into F5 would force F6 entries to either pick a fake consumer_surface value or duplicate across all surfaces. The two concerns are orthogonal: F5 is "which version does each surface pin to?", F6 is "how to scope when the user's question doesn't say?"

### Option D: JSONB column on `metrics` (`metrics.requires_disambiguation JSONB`)

Rejected. Same shape compiles but loses (a) the per-row indexability for analytics queries ("metrics with N+ missing_dimensions"), (b) the RLS surface parity with F2/F3/F4/F5 child tables, (c) the row-delete pattern that makes the "no row = no directive" semantic obvious. The on-the-wire envelope shape is identical, so consumers can't distinguish A from D — but everything else favors A.

### Option E: pure ask-clarification-first (rejected per codex D-012)

The original v0.1 plan. Rejected because pure clarification-only responses crater H1's strict numeric scoring (no executable SQL = FAIL). The reframe to answer-with-default-and-disclose preserves both the H1 score AND the trust signal.

### Option F: surface defaults inline on `metrics` columns (e.g. `metrics.default_region`, `metrics.default_segment`)

Rejected. The dimension space is metric-specific (gross_margin's default is `include_overhead`, churn_rate's is `definition`). Adding columns per dimension would explode the metrics row; a JSONB map keyed by dimension name is the right shape. The separate-table approach gets us all of (a) the JSONB shape, (b) the RLS parity, and (c) the per-row indexability without bloating `metrics`.

## Schema details

### `missing_dimensions` text[] NOT NULL CHECK (cardinality > 0)

Free-text dimension names. The set is small — typically 1-3 dimensions per metric. CHECK enforces at-least-one entry; an empty array would signal "this metric is ambiguity-prone but I can't tell you what dimensions matter," which is a contradiction. The seed-author layer enforces per-entry non-emptiness for a clearer error than the CHECK provides.

### `default_scope` jsonb NOT NULL CHECK (jsonb_typeof = 'object' AND default_scope <> '{}'::jsonb)

JSONB object mapping dimension_name -> canonical default value. The DB CHECK enforces (a) object-shape (vs array / scalar) and (b) non-empty (rejects `'{}'::jsonb` — the row's whole point is to provide defaults; an empty map is incoherent). No DEFAULT — callers must supply a non-empty `default_scope` explicitly. The seed-author layer additionally enforces (a) every `missing_dimensions` entry has a corresponding key, (b) per-key non-emptiness — fail-fast at author time with a clearer error than the CHECK provides.

Note: the `<> '{}'::jsonb` predicate is the portable Postgres-15-compatible "non-empty object" check; `jsonb_object_length()` would be simpler but requires Postgres 17+.

### `disclosure_template` text NOT NULL CHECK (length > 0)

The trust signal. The DB CHECK enforces non-empty (length(trim) > 0). Templated — the consumer harness can substitute placeholders at request time; the DB stores the literal template. Mirrors F4's `metric_pending_state.policy` pattern.

### `policy` text NOT NULL DEFAULT 'answer-with-default-scope-and-disclose' CHECK (enum)

Two values: `answer-with-default-scope-and-disclose` (benchmark mode, default) and `ask-clarification-first` (production mode). DEFAULT to the benchmark mode so any seed row that omits the column gets the benchmark-correct value. CHECK enforces the value space; future modes are additive.

### UNIQUE (metric_id)

A metric has at most one disambiguation directive. The UNIQUE constraint at the schema level enforces this — attempts to insert a second row for the same metric fail with a constraint-violation error. State transitions are modeled as UPDATE on the existing row.

### RLS — SELECT-only org-membership gate

Mirrors `metric_relationships` / `metric_approvals` / `metric_deprecated_framing_rules` / `metric_pending_state` / `metric_consumer_contexts`. `INSERT` / `UPDATE` / `DELETE` go through the service-role repository layer (per the codebase convention noted in `20260226000009_v2_indexes_rls.sql` §6-9).

## Forward-compat with F7 and future workflow tooling

- **F7 (per-policy-tier few-shot examples).** May reference `requires_disambiguation` as a "directive style" template source — e.g. show the LLM what a typical disambiguation disclosure looks like for an Ambiguity-tier question. Storage is independent.
- **Future authoring UI.** F6's table can power a UI for managing disambiguation directives (e.g. "show me every metric with `region` in missing_dimensions", "metrics with no disambiguation but multiple variants"). The per-row indexability + UNIQUE (metric_id) shape makes this trivial. That UI is out of scope for V2.1.
- **Future production-mode rows.** If a production org wants to opt specific metrics into `ask-clarification-first`, they author rows with that policy value. The benchmark mode keeps producing SQL; the production mode produces an interactive prompt. The same envelope field carries both — consumers branch on the policy value.
- **Future dimension types.** The `default_scope` JSONB shape is forward-compatible with numbers / booleans / nested objects without a migration. The TypeScript type today is `Record<string, string>`; the type can widen as new value shapes are introduced.

## Forward-compat / fallback semantics

When a consumer reads `requires_disambiguation` and the value is `null`, the convention is: **the metric has no ambiguity surface; produce SQL using the canonical default**. This makes F6 strictly additive: an LLM consumer that doesn't know about F6 sees `null` for non-ambiguous metrics and behaves as before; an F6-aware consumer reads the directive (when present) and substitutes the disclosure template into F1's contract.

The defense-in-depth catch in the route layer degrades a thrown repository error to `requires_disambiguation: null`, which means a transient DB failure for the F6 query gracefully degrades to "no directive" rather than 500-ing. This mirrors the F4 `pendingStateCatch` and F5 `consumerContextsCatch` patterns.

## Consequences

### Enables

- **Ambiguity category lift on top of F1's prose-with-SQL contract.** The disambiguation directive lets the LLM produce SQL using canonical defaults AND disclose the assumption in F1's `warnings` / `clarification_request` field. The combination should lift the Ambiguity pass rate from 2.8% toward the plan's 8-12% target by giving the LLM the explicit handle for what to do when scope isn't specified — instead of envelope-induced overconfidence producing a confident wrong answer.
- **Authentic ambiguity catalog per metric.** The seed catalog (`DISAMBIGUATION_OVERRIDES`) carries entries for 11 metrics — the ambiguity-prone ones in the v2 dataset. Each entry pairs the dimension axes with canonical defaults the LLM can use for SQL, plus the trust signal for disclosure.
- **Locks in the gross_margin win.** v2.0's only Ambiguity passes were on `ambiguity-gross_margin-001`; the F6 directive for gross_margin (default `exclude_overhead`) gives the LLM the explicit assumption to use AND disclose, so the win shouldn't regress.
- **Self-contained directive block.** The `MetricDisambiguation` shape — `missing_dimensions`, `default_scope`, `disclosure_template`, `policy` — is everything the LLM needs to handle the ambiguity at request time. No need to reach into other envelope fields.

### Blocks

Nothing. The `requires_disambiguation` field is additive; legacy v1 consumers can ignore it. F6-aware consumers see `null` for non-ambiguous metrics and branch on the directive when present.

### Limitations / risks

1. **Default-scope drift from product reality.** A canonical default (e.g. "MAU" for active_user definition window) may not match every team's convention. Mitigation: the disclosure_template tells the user what default was assumed — they can correct downstream. Future workspace-admin UI could let orgs override the seed defaults per metric.
2. **The benchmark-vs-production tension is real.** The `answer-with-default-scope-and-disclose` mode is right for H1 scoring but might feel pushy in a production interactive UX (the user might prefer to be asked). The two-policy enum lets production integrations opt out per metric — but only if someone authors the production-mode rows, which V2.1 doesn't.
3. **Dimension naming inconsistency across metrics.** "definition" means different things on different metrics (it's the active_user window definition vs the qualified_lead funnel-stage definition). The LLM has to read each metric's `disclosure_template` to know what its dimensions actually mean. Mitigation: the disclosure_template carries the human-readable framing; future workflow tooling could enforce a controlled vocabulary if dimension drift becomes a problem.
4. **Stale defaults vs evolving metric reality.** A metric's authentic ambiguity surface may evolve (e.g. churn_rate gets a new "include_paused_subscriptions" axis that current defaults don't address). Mitigation: the seed catalog is authored per sprint; future workflow tooling can wire updates into the metric-revision workflow.
5. **Single-axis fail-fast loss.** If the LLM uses a slightly-different default than the catalog's (e.g. picks `MAU` when catalog says `WAU`), the disclosure still claims the catalog default — a small consistency gap. Mitigation: the F1 contract requires the SQL match the disclosed default; consumers that diverge would be caught by the F1 ablation. Out of scope for V2.1.

## Follow-ups

1. **V2.1-F7** — separate dispatch. F7 (few-shot examples per policy_tier) doesn't share storage or shape with F6; it lives in its own ADR.
2. **Disambiguation workflow UI** — the workspace-admin UI for managing disambiguation directives is out of scope for V2.1. Production orgs author via the seed pattern (the script is documented as the canonical example).
3. **Per-org default-scope overrides** — a workspace-default "default scope" per dimension (so the seed doesn't have to author the same default across every metric). Follow-up ADR when the authoring UI is designed.
4. **Audit columns on `metric_disambiguation`** — `set_by` / `last_transition_at` columns when the workflow tooling lands. Follow-up ADR.
5. **Production-mode rows** — when a production org wants `ask-clarification-first` per metric, they author rows with that policy value. Storage is in place; UI is the missing piece.
6. **Wider value types in `default_scope`** — when authors want number / boolean / nested values, the TypeScript type widens beyond `Record<string, string>`. The JSONB column already accepts these; the consumer harness needs to know how to serialize them into SQL. Out of scope for V2.1.

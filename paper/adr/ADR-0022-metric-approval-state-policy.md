# ADR-0022 — Metric `approval_state` field with policy directive (Trust Benchmark v2.1, non-approved-state surfacing)

- **Status:** Accepted
- **Date:** 2026-05-03
- **Sprint context:** Trust Benchmark v2.1 — F4 (codex-priority #4)
- **Stacks on:** ADR-0020 (`metric_deprecated_framing_rules` — V2.1-F2), ADR-0021 (execution metadata + `governance_facts` — V2.1-F3)
- **Migration:** `supabase/migrations/20260503000200_v2_1_f4_metric_pending_state.sql`
- **Tracking:** `brain/product/clarilayer-v2.1-governance-response-features.md` §3 priority 4

## Context

Trust Benchmark v2 (5 stability sweeps × 3 models × 5 baselines × 120 questions = 10,800 calls) measured ClariLayer's Approval category PASS rate at a meaningless **4.2%** because of a scoring bug (V2.1-P0-A); the post-fix re-score showed `owner_team` 100%, `version` 100%, `lifecycle_status` 86.7%, and `approver` / `approved_at` / `effective_from` at 0%. F3c (ADR-0021) closed the structured-fact gap by aggregating `governance_facts` into a flat envelope block — but the gap that remains is **non-approved-state surfacing**.

Even with the F3c block, metrics in non-approved states (PENDING / BLOCKED / IN_REVIEW / CHANGES_REQUESTED) cannot be reliably surfaced to the LLM because the envelope has no rich pending-state context. F3c's `governance_facts.lifecycle_status` reports `pending_review` as a string, but it doesn't carry:

- **Why** the metric is in that state (blockers, expected resolution).
- **What the LLM should DO** about it (the policy directive).
- **When** the metric entered the state (the since-date).

Codex priority #4 in the v2.1 plan (D-012) calls for a structured `approval_state` envelope field that gives the LLM a self-contained directive when a metric is in a non-approved state.

## Decision

Add a new table `public.metric_pending_state` carrying rich non-approved-state context, surfaced as the v1 envelope `approval_state` field of type `MetricApprovalState | null`.

The envelope-facing shape is:

```json
{
  "approval_state": {
    "status": "pending_review",
    "owner_team": "data-eng",
    "expected_resolution": "2026-05-15",
    "blockers": ["awaiting finance sign-off on revenue recognition treatment"],
    "policy": "DO NOT use for production answers. Surface this status when the metric is requested.",
    "since": "2026-04-22"
  }
}
```

For **APPROVED** metrics — the common case — `approval_state` is **`null`** (no row in `metric_pending_state`, no token bloat on the envelope). Only metrics in one of the four non-approved states have a row.

The field is **additive** to the existing `MetricDetailV1Response`. Legacy v1 consumers can ignore it. F4-aware consumers branch on `approval_state !== null`.

## Why a separate envelope field, not an extension of `governance_facts`

This is the load-bearing decision and the one that motivated the dispatch as a separate feature.

F3c's `governance_facts` is the **"what's true now"** snapshot: who owns the metric, who approved its current version, when that version became effective, what its current lifecycle status is. It's a flat block of structured *facts* the LLM consumes alongside the metric definition itself. Every metric — approved or not — carries `governance_facts`; the block is shape-stable.

F4's `approval_state` is the **"what to DO when not approved"** directive. It addresses a different question: when a metric is *not* in a normal serviceable state, what should the LLM tell the user? That's a *behavioral* contract between the envelope author and the LLM consumer, not a fact about the metric.

The two concerns differ on three axes:

1. **Audience inside the LLM consumer.** `governance_facts` answers structured questions ("who approved this metric?"). `approval_state` injects a directive into the response generation ("if this metric is requested, surface the status").
2. **Lifecycle.** `governance_facts.lifecycle_status` ticks every time the metric's lifecycle moves. `metric_pending_state` rows are inserted when a metric *enters* a non-approved state and deleted when the metric is approved (back to the common case). One table answers a "current snapshot" query; the other answers a "what's the temporary contract until this resolves?" query.
3. **Authoring.** `governance_facts` is mostly auto-derived from existing tables. `approval_state.policy` is hand-authored — its whole value is the directive string written for the specific reason this metric isn't approved.

Folding F4 into F3c would force every approved metric to carry blank `policy` / `blockers` / `expected_resolution` fields, inviting consumer drift ("is the policy required? sometimes empty? what does empty mean?") and bloating the common case. Splitting them keeps each block focused.

## Why `null` for APPROVED metrics

The plan's question — `approval_state: null` vs a minimal `{status: "approved", policy: "answer-direct"}` default — was decided in favor of **null**:

1. **No token bloat on the common case.** Every approved metric on every detail-endpoint response would carry the same boilerplate. The envelope already serializes `governance_facts.lifecycle_status: "APPROVED"` for these metrics; a minimal `approval_state: {status: "approved", policy: "answer-direct"}` would duplicate the lifecycle status and add a boilerplate directive the LLM doesn't need (the absence of friction *is* the directive).
2. **Clear consumer branch.** `approval_state == null` is unambiguous: "no special handling required." A minimal default would force consumers to inspect the policy string to confirm "yes, you can answer normally," which adds a parsing step.
3. **Storage shape parity.** No row in `metric_pending_state` corresponds to no envelope context. The DB shape and the envelope shape stay aligned.

The tradeoff is that an F4-aware consumer always reads `approval_state` defensively (`if (approval_state) { ... }`). That's a one-line check — much cheaper than the boilerplate alternative.

## Status enum choice

The migration's CHECK constraint pins `status` to four values:

- **`pending_review`** — initial review by the owning team has not started or is in progress; nothing concrete is blocking.
- **`blocked`** — explicit blocker preventing approval (e.g. external sign-off, missing warehouse data, legal review).
- **`in_review`** — under active review; approval expected soon barring new findings.
- **`changes_requested`** — a reviewer has requested changes; the metric author is iterating.

These four cover the v2 dataset's needs and the seed catalog. The enum is **additive** — future statuses (e.g. `awaiting_legal`, `escalated`) only require a CHECK constraint update + a new entry in `MetricPendingStatus`. No data migration needed.

The enum was deliberately kept tighter than the broader Approval-vs-Rejected dichotomy in `metric_approvals` because F4 only models the **transition states**. The terminal states (APPROVED / REJECTED) live on `metric_approvals` and `metrics.lifecycle_status`. Crossing the boundary inflates the consumer-facing space and conflates "what to do" with "what happened."

## Alternatives considered

### Option A: separate table `metric_pending_state` with envelope `approval_state` (chosen)

**Mechanics.** New table keyed by `metric_id` (UNIQUE — a metric is in one pending state at a time), FK to `metrics(id) ON DELETE CASCADE`. Same RLS shape as `metric_relationships` / `metric_approvals` / `metric_deprecated_framing_rules` (org-scoped SELECT). Surfaced in the envelope serializer alongside `governance_facts`.

**Why we picked it:**

1. **Shape matches data shape.** A metric is in one pending state at a time. The 1:1 cardinality is enforced by `UNIQUE (metric_id)` at the schema level — a JSONB blob would lose that.
2. **RLS parity with F2 / F3a / F3b.** The new table mirrors the existing SELECT-only RLS pattern (org-membership gate). A new column on `metrics` would inherit `metrics`' broader RLS surface.
3. **Indexability.** A `metric_id` B-tree index supports the parallel-fan-out fetch in the v1 detail route. JSONB-column access on `metrics` would not.
4. **Future authoring UI.** The eventual workspace-admin UI for tracking pending metrics wants a row-per-pending-metric grid (e.g. "show me all metrics blocked >30 days"). Modeling them as table rows is the obvious shape; modeling them as a JSONB column on `metrics` would force every "blocked-metrics dashboard" query to scan the entire metrics table.
5. **Authoring lifecycle.** A pending metric eventually gets approved — at which point the pending-state row is *deleted*. A JSONB column would be set to NULL; both work, but the row-delete pattern is cleaner and makes the "no row = approved" semantic obvious to humans reading the schema.

### Option B: extend `governance_facts` with the F4 fields

Rejected per §"Why a separate envelope field, not an extension of `governance_facts`" above. The concerns differ (facts vs. directive), the lifecycles differ (snapshot vs. transition contract), the authoring sources differ (auto-derived vs. hand-authored). Folding them together would invite consumer drift and bloat the common case.

### Option C: extend `metric_approvals` with pending-state columns

Rejected. `metric_approvals` carries APPROVED / REJECTED *decisions* with an `approver_id` FK. Pending-state context is a different concern — it describes what's *holding up* a decision, not who *made* one. Mixing the two would force every pending-state row to invent a fake `approver_id`, breaking the FK semantic. A separate table keeps each table's column set focused.

### Option D: JSONB column on `metrics` (`metrics.pending_state JSONB`)

Rejected. Same shape compiles but loses (a) the UNIQUE constraint and FK semantics, (b) per-row indexability for analytics queries ("metrics blocked >30 days"), (c) the row-delete-on-approval pattern that makes the "no row = approved" semantic obvious. The on-the-wire envelope shape is identical, so consumers can't distinguish A from D — but everything else favors A.

### Option E: Surface only the policy directive (no structured context)

Rejected. The plan-spec's value isn't just the directive string — it's the *self-contained* block: status + owner_team + expected_resolution + blockers + policy + since. An LLM that gets only `policy: "DO NOT use..."` can surface the directive but can't answer follow-up questions ("when will it be ready?" "what's blocking?"). The block lets the LLM both *behave* per the directive AND *explain* the situation.

## Schema details

### `status` CHECK constraint

Pinned to the 4-value enum (`pending_review`, `blocked`, `in_review`, `changes_requested`). Future values are additive — only require a CHECK constraint update and a new entry in `MetricPendingStatus`. No data migration. Mirrors the codebase pattern of using CHECK constraints (not Postgres ENUMs) for this — CHECK constraints are easier to extend than ENUMs.

### `policy` REQUIRED non-empty

The DB enforces `length(trim(policy)) > 0`. The whole point of F4 is the directive — a row with an empty policy would be a contract violation between the table author and the LLM consumer. The CHECK fails closed.

### `blockers` array with empty-default

`text[] NOT NULL DEFAULT ARRAY[]::text[]`. Empty array is valid — a `pending_review` metric simply in queue with nothing concrete blocking it. The empty default keeps the seed terser and the schema's behavior obvious. No CHECK on cardinality (unlike F2's `trigger_patterns`) because empty is a meaningful state for F4.

### `since` date with `CURRENT_DATE` default

Defaults to today; explicit when backdating a state transition (e.g. seed authoring). Useful so the LLM can surface "in this state since X" framing without the consumer having to compute durations.

### `expected_resolution` nullable

`null` when the owner can't commit to a date — a perfectly valid shape ("we'll get to it" is sometimes the truth). The consumer surfaces this as "no committed resolution date."

### `metric_id` UNIQUE

A metric is in ONE pending state at a time. The UNIQUE constraint at the schema level enforces this — attempts to insert a second row for the same metric fail with a constraint-violation error. State transitions are modeled as UPDATE on the existing row; full transitions OUT of the pending state are modeled as DELETE.

### RLS — SELECT-only org-membership gate

Mirrors `metric_relationships` / `metric_approvals` / `metric_deprecated_framing_rules`. `INSERT` / `UPDATE` / `DELETE` go through the service-role repository layer (per the codebase convention noted in `20260226000009_v2_indexes_rls.sql` §6-9). This keeps the workflow tooling that *changes* a metric's pending state behind the same auth surface as approval workflow writes.

## Forward-compat with F5 / F6 / F7

- **F5 (consumer surface version pins).** Independent — F5's `consumer_contexts` lives on the metric/version layer, not the workflow layer. F5 may *read* `approval_state` to decide whether to pin to a non-approved version (e.g. "don't pin the board-reporting surface to a metric in `blocked` state"), but the field doesn't share storage.
- **F6 (`requires_disambiguation` field).** Independent — F6 lives on the question/scope layer, not the metric definition layer.
- **F7 (per-policy-tier few-shot examples).** May reference `approval_state.policy` as a "directive style" template source, but the storage is independent.
- **Future workflow tooling.** F4's table can power a UI for tracking pending metrics (e.g. "show all metrics blocked >30 days", "metrics with no expected_resolution") — the per-row indexability + row-per-pending-metric shape make this trivial. That UI is out of scope for V2.1.

## Consequences

### Enables

- **Approval category lift on top of F3c.** F3c surfaces the lifecycle_status string; F4 surfaces the directive. The combination should lift Approval pass rate above the F3c baseline by removing the LLM's guesswork about how to *behave* when a metric is non-approved.
- **Per-status authoring vocabulary.** The seed catalog (`PENDING_STATE_OVERRIDES`) carries 3 metrics with status-specific policies that direct the LLM's behavior:
  - `qualified_lead` — pending_review with "OK to use with caveat..." policy.
  - `gross_margin` — blocked with "DO NOT use for production answers..." policy.
  - `daily_active_users` — in_review with "Use with caveat: under active review..." policy.
- **Self-contained directive block.** Repeating `owner_team` on the F4 block (also in F3c) lets consumers read `approval_state` without needing `governance_facts`. Extra ~12 bytes of envelope cost for a stable, clean consumer surface.

### Blocks

Nothing. The `approval_state` field is additive; legacy v1 consumers can ignore it.

### Limitations / risks

1. **Stale pending-state rows.** A metric that gets approved without the workflow tooling deleting the corresponding `metric_pending_state` row would carry a stale `approval_state` block on the envelope. Mitigation: future authoring tooling (out of scope for V2.1) wires the row deletion into the approval action; for the test org, the seed script is the canonical source of truth and is authored once per sprint.
2. **Status enum extensibility cost.** Future statuses require a CHECK constraint update + a new `MetricPendingStatus` value + (potentially) consumer-side handling. Mitigation: the four-value enum was sized for the v2 dataset's needs; new statuses are a meaningful enough event to warrant the small migration.
3. **Policy directive drift across metrics.** Two metrics in the same status could carry different policies (e.g. one pending_review with "OK to use with caveat", another with "DO NOT use"). That's by design — the directive is per-metric — but it means the LLM consumer can't pre-cache "here's what a pending_review metric looks like." Each request reads the per-metric directive. Mitigation: this is the feature, not a bug.
4. **`owner_team` duplication.** F4's `owner_team` mirrors F3c's `governance_facts.owner_team`. A metric whose owner changes will have both fields update via the same `metrics.owner_team` source — but a future schema change to one (e.g. F3c surfacing display name; F4 keeping the UUID) would need to propagate. Mitigation: F4 reads `owner_team` from the metric row (passed in by the route layer), not from `metric_pending_state`. The duplication is on the wire, not in the storage.
5. **No audit trail.** F4 doesn't carry "who put this metric into this state" — the row's `created_at` / `updated_at` give the timestamp, but not the actor. Mitigation: future migration may add `set_by uuid REFERENCES profiles(id)` columns when the workflow tooling needs to surface "blocked by Alice on 2026-04-22." Out of scope for V2.1.

## Follow-ups

1. **V2.1-F5 / F6 / F7** — separate dispatches. F5/F6/F7 don't share storage or shape with F4; they live in their own ADRs.
2. **Pending-metrics workflow UI** — the workspace-admin UI for tracking pending metrics + transitioning states is out of scope for V2.1. Production orgs author via the seed pattern (the script is documented as the canonical example).
3. **Audit columns on `metric_pending_state`** — `set_by` / `last_transition_at` columns when the workflow tooling lands. Follow-up ADR.
4. **Per-status default policies** — a workspace-default "policy template" per status (so the seed doesn't have to author the directive string from scratch every time). Follow-up ADR when the authoring UI is designed.
5. **Status transition history** — a sibling `metric_pending_state_transitions` table that logs every state change. Useful for "metric X has been in `blocked` for 47 days, with 3 status transitions." Out of scope for V2.1; v3 question.

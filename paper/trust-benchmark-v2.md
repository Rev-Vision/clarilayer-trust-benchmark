# The ClariLayer Trust Benchmark, v2 (updated for V2.1-RR)

> **In-place update.** This is the v2 paper, updated with the V2.1-RR re-run. The v2.0 dataset (`v2-stability-{1..5}`) is preserved in the parent research repo, but the paper now reports the V2.1-RR numbers (`v2.1-stability-{1..5}`) as canonical. v2.0 figures are kept as reference points where the v2.0 → v2.1 progression is informative. v1 paper untouched at [`trust-benchmark-v1.md`](trust-benchmark-v1.md).

**Across 9,000 single-turn SQL questions over five stability runs, the four non-governed baselines (raw schema, documented warehouse, expert-configured Cube, dbt MetricFlow) returned the canonical, committee-approved answer at most 2.94% of the time. ClariLayer's governed envelope (Baseline E) returned the canonical answer 47.17% of the time — a ~16× lift on the lowest-bar comparator and a ~71× lift on raw-schema. On Drift specifically — the scenario where governance is supposed to flag deprecated framings — ClariLayer cleared 82.50% PASS while every non-governed baseline returned 0/360 except one PASS row on Cube (1/360 in chunk 5). That is a ~15.6× lift on the v2.0 measurement of E's Drift behavior (5.3% → 82.5%), and it is the headline of the V2.1-RR re-run.**

We tested the same three production-grade LLMs against the same five context configurations on the same 120-question battery and the same deterministically-seeded synthetic SaaS warehouse as v2.0. What changed between v2.0 and V2.1-RR is the product surface: seven envelope features (F1 through F7) plus two breaking column renames (ADR-0018 and ADR-0019, already shipped in v2.0). The F1 prose-with-SQL response contract is applied to all five baselines so the comparison is apples-to-apples; the F2–F7 governance metadata is applied to Baseline E only — that is the lift we measure. Total spend on the V2.1-RR clean re-run is $303.50 over five chunks; an additional $138.60 of first-fire sunk cost (caught by an F1-contract verbosity bug) brings the V2.1 phase grand total to $442.10, including the $50 founder-approved overage on the original $400 ceiling.

The result is qualitative as well as quantitative:

> **On the Drift category, ClariLayer's E baseline produced 297 `canonical-with-rejection` outputs across 360 calls (82.50%).** v2.0's E produced 19/360 (5.3%) on the same battery, same models, same warehouse — the difference is the v2.1 envelope. Every non-governed baseline produced 0/360 PASS on Drift in v2.0; in V2.1-RR, A/B/D each remain at 0/360, and C produced exactly 1/360 (one PASS row on Cube, in chunk 5). The qualitative dominance of v2.0 — "ClariLayer is the only baseline that produces canonical-with-rejection on Drift, ever" — survives V2.1-RR, but the absolute lift is now an order of magnitude larger.

The headline of v2.0 was "the other four baselines cannot produce the right *behavior* on Drift, ever, even when they coincidentally produce the right *number*." The headline of V2.1-RR is "with the v2.1 envelope, governance-aware behavior shifts from a 5% rare event to an 82% routine outcome." The mechanism (the governed envelope explicitly carrying the deprecation history the model can use to reject the framing) is the same — what changed is how the envelope surfaces that information so the model actually exploits it.

Run series `v2.1-stability-{1..5}` (commit `8e22fe08`). 5 baselines × 3 models × 120 questions × 5 stability runs = 9,000 main calls + 1,800 Drift LLM-as-judge calls = 10,800 total per re-run. v1 paper preserved at [`trust-benchmark-v1.md`](trust-benchmark-v1.md); the v2.0 stability dataset (`v2-stability-{1..5}`) is preserved in the parent research repo and is not duplicated in this companion repo — see the Reproducibility section for the V2.1-RR datasets that are mirrored here.

---

## What changed since v2.0, and what each change explains

v2.0's published Limitations section called out three honest gaps: (1) H1's ≥30 pp lift threshold was met on Approval but not on Ambiguity / Versioning / Drift; (2) the absolute numbers were floor-bound by the question battery design; (3) bug-fix attribution from v1 → v2.0 was partial. The v2.1 product iteration was scoped specifically against gap (1) — does the v2.0 gap-vs-hypothesis come from the envelope's information being absent, or from the model not exploiting information that is already there?

The diagnosis was the latter. v2.0's E shipped the right facts — `deprecated_at`, `replaced_by`, `policy_tier`, `metric_versions`, `lifecycle_status`, `owner_team`, lineage — but they were buried inside descriptive text and JSON the model had to infer over. v2.1's seven envelope features convert those implicit signals into explicit directives the model can act on directly. ADR-0018 and ADR-0019 (the column renames that landed inside v2.0's B0 readiness gate) stay in place — V2.1-RR does not re-litigate those.

| v2.0 → V2.1-RR change | What it fixes | Effect on numbers |
|---|---|---|
| **F1 — prose-with-SQL response contract** (all 5 baselines) | v2.0's "SQL only, no prose" directive mechanically suppressed governance signal; the LLM had no place to surface a warning even when it had the deprecation history available | F1 alone bumps E's Drift signal floor: prose-channel for warnings + structured `sql` field stays mandatory. Applied to A/B/C/D as well so the comparison stays apples-to-apples (A–D scores remain at v2.0 levels within sampling noise) |
| **F2 — query-conditioned `deprecated_framing_rules`** ([ADR-0020](adr/ADR-0020-metric-deprecated-framing-rules.md)) | v2.0's E carried deprecated-version history but no per-rule trigger. The model had to infer "this question targets a deprecated framing." F2 ships explicit `trigger_patterns` per rule + a `rejection_template` the model surfaces verbatim | The Drift lift's mechanical driver. v2.0 E Drift = 5.3% → V2.1-RR E Drift = 82.5% |
| **F3 — execution metadata + governance facts** ([ADR-0021](adr/ADR-0021-metric-execution-metadata.md)) | v2.0's Lookup failures clustered on column-alias / parameter / dimension execution issues, not governance. F3 adds a `governance_facts` block (e.g. `approver`, `approved_at`, `effective_from`) and resolves template parameters before the envelope is rendered | Lookup floor-shifted (v2.0 E Lookup = 75.3% → V2.1-RR E Lookup = 87.5%); Approval and Versioning gain access to the structured fields v2.0 was missing |
| **F4 — `approval_state` field with policy directive** ([ADR-0022](adr/ADR-0022-metric-approval-state-policy.md)) | v2.0's E surfaced `lifecycle_status: APPROVED` but had no directive for non-APPROVED states (PENDING / PROVISIONAL / DEPRECATED). F4 ships an `approval_state.policy` field the model surfaces verbatim when state ≠ APPROVED | Surfaces non-approved-state behavior the v2.0 envelope had no way to express |
| **F5 — `consumer_contexts` per-surface version pins** ([ADR-0023](adr/ADR-0023-metric-consumer-contexts.md)) | v2.0 had no surface-level versioning — different products (ad-hoc dashboard, exec deck) couldn't pin to different metric versions. F5 ships per-surface `version` pins with policy | Versioning lift mechanism: when a surface's pin diverges from canonical, the model surfaces the pin policy |
| **F6 — `requires_disambiguation` reframe** ([ADR-0024](adr/ADR-0024-metric-disambiguation.md)) | v2.0's Ambiguity scored E = 2.8% — the spec's `ask-clarification-first` policy triggered the model to ask for missing scope, but the strict-pass scoring rubric counted clarification questions as fails. F6 introduces `answer-with-default-scope-and-disclose` as the canonical default and reframes the policy enum | Ambiguity lift mechanism: the model returns the canonical-default answer + discloses what scope it assumed, which the rubric scores as PASS |
| **F7 — per-(policy_tier, mode) few-shot examples** ([ADR-0025](adr/ADR-0025-metric-policy-tier-examples.md)) | v2.0's E had no in-prompt response-format examples. The model occasionally produced almost-right shapes (e.g. extra prose around the JSON object) that the F1 parser rejected | Response-format adherence — fewer parser-driven near-fails, fewer ERROR rows |

ADR-0018 (`metrics.tier` → `metrics.policy_tier`) and ADR-0019 (`metric_versions.version` → `metric_versions.definition_version`) shipped in v2.0's B0 sprint. They are not v2.1 features — but F2 / F4 / F5 / F6 directly reference `policy_tier` and `definition_version` in their envelope shapes, so the renames are a precondition for the v2.1 surface.

Below, every results table includes a v2.0↔V2.1-RR row or column so the reader can see which numbers moved and which didn't.

---

## Methodology

### Five baselines (v2.0 → V2.1-RR contract change)

| V2.1-RR | v2.0 | What the model sees |
|---|---|---|
| **A — Bare schema** (F1 contract) | A | `CREATE TABLE` DDL only — column names + types. F1 envelope contract applied; `warnings` MUST be `[]` (no governance signal available) |
| **B — Documented warehouse** (F1 contract) | B | DDL + column comments + table descriptions. F1 envelope contract applied; `warnings` MUST be `[]` |
| **C — Cube semantic layer (expert-configured)** (F1 contract) | C | Cubes, joins, measures, dimensions, segments, pre-aggregations. Adequacy checklist published per spec §3.1. F1 envelope contract applied; `warnings` MUST be `[]` |
| **D — dbt Semantic Layer (MetricFlow)** (F1 contract) | D | `dbt_project.yml`, 8 staging models, 8 semantic models, 36 metrics, 4 saved queries. Adequacy checklist published per spec §3.1. F1 envelope contract applied; `warnings` MUST be `[]` |
| **E — ClariLayer governed context** (F1 contract + F2–F7 envelope) | E | Canonical Metric API output: definitions, version, ownership, approval state, deprecation history, plus the v2.1 envelope features F2–F7 — query-conditioned `deprecated_framing_rules`, `governance_facts`, `approval_state.policy`, `consumer_contexts`, `requires_disambiguation`, per-(`policy_tier`, mode) few-shot examples |

The F1 prose-with-SQL contract is applied uniformly to all five baselines — i.e. A/B/C/D's prompt also changed between v2.0 and V2.1-RR. We disclose this explicitly because the F1 contract is the closest thing to a confounder in the V2.1-RR design: if A–D's accuracy moved between v2.0 and V2.1-RR, the F1 contract is partly responsible. In practice A–D's V2.1-RR aggregates (0.67% / 2.94% / 1.78% / 1.39%) track v2.0's (0.6% / 2.4% / 2.1% / 2.0%) within sampling noise — the F1 contract does not produce non-governance lift on baselines that have no governance metadata to surface, which is exactly the directional finding we'd expect.

The F2–F7 envelope features apply to Baseline E only. That is the v2.0 → V2.1-RR causal lift we are measuring.

Each baseline ships an adequacy checklist sourced from the official Cube / dbt-metricflow docs, demonstrating that every primitive in the docs the warehouse exercises is present in the config. Both checklists pass. Configs and checklists are published in the public companion repo.

### Three models, five stability runs

Same three models v2.0 ran:

- Claude Opus 4.7 (1M context) — `anthropic/claude-opus-4.7`
- GPT-5.4 — `openai/gpt-5.4`
- Claude Sonnet 4.5 — `anthropic/claude-sonnet-4.5`

All calls routed via Vercel AI Gateway. Five stability runs match v2.0; the per-(model, baseline) aggregate variance across the 5 runs is reported in §Stability variance.

### Question battery — 5 categories × 24 questions = 120 (unchanged from v2.0)

The V2.1-RR question battery is byte-identical to v2.0's. The v2.0 → V2.1-RR delta is product surface, not question pool.

| Category | N | What it tests | Solvable by semantic layer alone? |
|---|---:|---|---|
| **Lookup** | 24 | "What was MRR for Q3 2026?" — direct retrieval | Expected yes |
| **Ambiguity** | 24 | "What's our churn rate?" — multiple defensible defs in warehouse, one canonical | No |
| **Versioning** | 24 | "MRR was redefined; what's the current vs Q2 def, and which applied to the Q1 board deck?" | No |
| **Approval** | 24 | "Who owns this metric? Is the current version approved for board use?" | No |
| **Drift / Adversarial** | 24 | "Use the deprecated MRR def" / "compute revenue gross-of-refunds the way Marketing prefers" | No |

### Warehouse (unchanged from v2.0)

V2.1-RR uses the v2.0 warehouse byte-for-byte: deterministically-seeded synthetic SaaS, 8 tables + the `metric_versions` extension for Versioning. Deliberately NOT in the warehouse (still): the consumer-usage record, current-canonical mapping, and approval/ownership state. Those stay in the ClariLayer envelope only — otherwise A/B/C/D could trivially answer Versioning by table-lookup, defeating the test.

### Drift LLM-as-judge protocol (re-locked for v2.1)

The Drift category PASS criterion is unchanged: judge label ∈ {`flagged`, `canonical-with-rejection`}. The judge model and temperature are unchanged: `anthropic/claude-sonnet-4.5` at T=0.0. The judge prompt was re-locked under v2.1 because the F1 contract changed the response shape (the judge must now read structured `warnings` + `clarification_request` + `sql` + `rationale` fields rather than free-text SQL).

- **Calibration set:** `benchmark/dataset/judge_calibration_set.json` — re-balanced for v2.1 to 12/12/13/13 across the four labels using the F1-structured response shape `{warnings, clarification_request, sql, rationale}`.
- **Calibration agreement:** the v2.1-locked judge clears the spec's ≥85% gate at **96.0%** on the 50-entry calibration set (vs v2.0's 88.0%), with all four labels above the 85% per-label recall floor. Report at [`results/judge_calibration_2026-05-03-v2.1.md`](../results/judge_calibration_2026-05-03-v2.1.md). The v2.0 calibration report (`judge_calibration_2026-05-03.md`) is preserved in the parent research repo as a historical record but no longer governs the V2.1-RR judge.
- **Inputs to the judge:** the model's parsed `warnings` array, parsed `clarification_request`, the answer text (`sql` + `rationale`), the original adversarial question, and the question's canonical/deprecated metadata (`canonical_answer_value`, `deprecated_answer_value`, `canonical_answer_sql` from the question YAML). **Blinded from the judge:** the baseline label (A/B/C/D/E), the source-model identity (Opus / Sonnet / GPT), and the context-block excerpt. The canonical/deprecated metadata is necessary ground truth — there is no fully-blind judge protocol that can distinguish "silent-canonical" from "flagged" without knowing what the canonical answer is. ClariLayer (E) does not get to "look like ClariLayer" to the judge.

### Statistical protocol

The v2.0 paired-bootstrap analysis is preserved as the v2.0 artifact: `benchmark/analysis/v2_analysis.py` reads `v2-stability-{1..5}` and produces `benchmark/analysis/v2_results.json`. The v2.0 paper's CIs and p-values still cite that artifact, and any v2.0 number cited in this paper is sourced from it.

V2.1-RR per-baseline aggregates and σ are computed directly from the raw JSONL across `v2.1-stability-{1..5}` (the chunk `SUMMARY.md` files in each `v2.1-stability-N/` show the per-chunk computation; this paper's headline numbers are the row sums across the 5 chunks). Paired-bootstrap 95% CIs for V2.1-RR are not yet computed — they are deferred to the v2.1.1 hardening pass — so V2.1-RR cells are reported as point estimates with stability σ across the 5 chunks rather than as bootstrap CIs.

- **Primary unit (V2.1-RR):** the (question × model) pair. Stability runs are *averaged* within a (model, baseline, question) cell to reduce variance — they are not multiplied for additional N. Per-(model, baseline) aggregates pool 5 runs × 5 categories × 24 questions = 600 rows; per-(category, baseline) cells pool 3 models × 5 runs × 24 questions = 360 rows.
- **Stability σ:** the standard deviation of a cell's PASS rate across the 5 stability chunks (sample standard deviation, n=5). Reported in §Stability variance for the headline cells.
- **v2.0 paired-bootstrap (preserved, cited where this paper makes a v2.0 claim):** 1,000 percentile-method resamples at the (model, question) unit, RNG seed `12345`, plus-one correction on the one-sided p-value. Output preserved in `benchmark/analysis/v2_results.json`.

---

## Bug-fix attribution (v2.0 → V2.1-RR)

V2.1-RR's bug-fix attribution is layered on top of v2.0's. The v1 → v2.0 ADR-0018 + ADR-0019 column renames stay in place and stay attributable to that earlier sprint. The V2.1-RR-specific bug-fix attribution is two items:

1. **F1-contract verbosity catch (first-fire sunk cost = $138.60).** The first V2.1-RR run surfaced an F1-contract verbosity issue: when `warnings` defaulted to a soft-spec ("populate warnings whenever helpful") rather than a hard-spec ("populate warnings ONLY on explicit governance triggers"), the model produced verbose-warnings on routine APPROVED-canonical metric calls. This pushed completion-token cost out of band, surfaced false-positive PASS shapes the strict scorer rejected, and triggered a re-design of the F1 prompt sub-spec for sparse `warnings`. The clean V2.1-RR re-run cost $303.50; the first-fire $138.60 is sunk. Total V2.1 phase spend $442.10, $50 over the original $400 ceiling per founder-approved overage.

2. **F1-contract applied uniformly to A/B/C/D.** The first version of F1 applied the structured-response shape to E only. CodeRabbit caught this as a confounder (the comparison would not be apples-to-apples if A–D answered SQL-only and E answered structured-JSON). The fix: F1 contract applied to all five baseline modules. The harness's `SYSTEM_PROMPT` was also brought into line. A–D's V2.1-RR numbers (0.67% / 2.94% / 1.78% / 1.39%) within sampling noise of v2.0's (0.6% / 2.4% / 2.1% / 2.0%) confirms that F1 alone does not produce lift on baselines without governance metadata.

**Lift attribution.** v2.0's E aggregate was 28.3%; V2.1-RR's E aggregate is 47.17%. The +18.87 pp aggregate lift is the union of F2–F7 (governance metadata applied to E only). Per category, the lift attribution is:

- Lookup: 75.3% → 87.5% (+12.2 pp; F3 is the mechanical driver — execution metadata, parameter resolution, governance facts)
- Ambiguity: 2.8% → 9.7% (+6.9 pp; F6 reframe, plus F2/F3 spillover)
- Versioning: 5.6% → 10.3% (+4.7 pp; F5 + F3 are the mechanical drivers)
- Approval: 52.5% → 45.8% (−6.7 pp; modest regression — see Limitations §H1's "v2.1-specific" note)
- Drift: 5.3% → 82.5% (+77.2 pp; F2 is the mechanical driver — query-conditioned `deprecated_framing_rules` with explicit `trigger_patterns` and `rejection_template`)

The Drift lift is roughly 15.6× the v2.0 baseline. The aggregate-level Drift lift dominates the headline because Drift is the category where the v2.0 envelope's information was most-implicit and v2.1's F2 directives most-explicit.

---

## Results

### 1. Per-baseline aggregate accuracy (all categories, 3 models, 5 runs)

Each baseline cell pools 5 categories × 3 models × 5 runs × 24 questions = 1,800 rows. v2.0 numbers shown alongside V2.1-RR for direct comparison.

| Baseline | v2.0 (120 Q, mixed) | V2.1-RR (120 Q, mixed) | V2.1-RR PASS / N |
|---|---|---|---|
| A — Bare schema (F1) | 0.6% | **0.67%** | 12 / 1800 |
| B — Documented warehouse (F1) | 2.4% | **2.94%** | 53 / 1800 |
| C — Cube (F1) | 2.1% | **1.78%** | 32 / 1800 |
| D — dbt Semantic Layer (F1) | 2.0% | **1.39%** | 25 / 1800 |
| **E — ClariLayer (F1 + F2–F7)** | **28.3%** | **47.17%** | **849 / 1800** |

Aggregate PASS across all 5 baselines × 9,000 calls: **971 / 9,000 = 10.79%** (vs v2.0's 7.07% on the corrected scoring run).

A–D track v2.0 within sampling noise — the F1 contract change alone did not lift the four non-governed baselines. E moved from 28.3% to 47.17% — the +18.87 pp aggregate lift attributable to F2–F7. v2.0's paired-bootstrap CIs on E (1,000 resamples at the (model, question) unit) place v2.0's E aggregate point estimate well below V2.1-RR's; V2.1-RR paired-bootstrap CIs are deferred to v2.1.1 (see §Statistical protocol). The §2 deltas below are direct aggregate deltas across the V2.1-RR run.

### 2. Aggregate deltas, E vs each other baseline (V2.1-RR)

Aggregate delta (E PASS rate − comparator PASS rate) pooled across all 5 categories × 3 models × 5 stability runs × 24 questions = 1,800 rows per baseline. v2.0 column reports the v2.0 paper's paired-bootstrap mean delta for reference.

| Comparison | v2.0 mean paired delta | V2.1-RR aggregate delta |
|---|---|---|
| E vs A | +27.7 pp | **+46.50 pp** |
| E vs B | +25.9 pp | **+44.23 pp** |
| E vs C | +26.2 pp | **+45.39 pp** |
| E vs D (dbt) | +26.3 pp | **+45.78 pp** |

The four V2.1-RR deltas cluster within a ~2.3 pp band (44.23 → 46.50). This is the right directional finding: governance lifts over *every* alternative on this question battery, not preferentially over one of them. Paired-bootstrap CIs and one-sided p-values for V2.1-RR are deferred to v2.1.1; the v2.0 deltas in the left column come with v2.0's paired-bootstrap p < 0.001 and are preserved as the prior reference point.

The H1 spec threshold (≥30 pp on each governance-required category, paired delta E − max(C, D)) is **partially met**: Approval and Drift clear convincingly (E − max(C, D) on Drift = +82.22 pp; on Approval, +45.83 pp); Ambiguity (+5.56 pp) and Versioning (+10.00 pp) do not clear the 30 pp bar. We discuss the per-category breakdown in §Limitations.

### 3. Per-category × per-baseline matrix (5 × 5, V2.1-RR)

Each cell pools 3 models × 5 stability runs × 24 questions = 360 rows. PASS criterion is category-specific (numerical for Lookup/Ambiguity/Versioning, substring/aliases for Approval, judge label ∈ {flagged, canonical-with-rejection} for Drift).

V2.1-RR numbers; v2.0 in italics for comparison.

| Category \ Baseline | A (bare) | B (docs) | C (Cube) | D (dbt) | E (ClariLayer) |
|---|---|---|---|---|---|
| **Lookup** | 0.0% *(0.0%)* | 7.8% *(8.6%)* | 4.2% *(4.2%)* | 2.8% *(4.2%)* | **87.50%** *(75.3%)* |
| **Ambiguity** | 1.1% *(1.4%)* | 0.0% *(0.0%)* | 4.2% *(4.2%)* | 4.2% *(4.2%)* | **9.72%** *(2.8%)* |
| **Approval** | 0.6% *(0.3%)* | 0.3% *(0.0%)* | 0.0% *(0.3%)* | 0.0% *(0.3%)* | **45.83%** *(52.5%)* |
| **Drift** (judge-scored) | 0.0% *(0.0%)* | 0.0% *(0.0%)* | 0.3% *(0.0%)* | 0.0% *(0.0%)* | **82.50%** *(5.3%)* |
| **Versioning** | 1.7% *(1.4%)* | 6.7% *(3.3%)* | 0.3% *(1.7%)* | 0.0% *(1.4%)* | **10.28%** *(5.6%)* |

Five findings jump out and warrant separate treatment:

1. **Drift is the headline.** v2.0 → V2.1-RR on E moves from 5.3% to 82.5% — a ~15.6× absolute lift. The mechanism is F2: the envelope ships query-conditioned `deprecated_framing_rules` with explicit `trigger_patterns` and a `rejection_template` the model surfaces verbatim. v2.0 had the deprecation history available but required the model to *infer* "the user is asking about a deprecated framing"; V2.1-RR makes that inference explicit. We treat Drift in its own subsection §4.
2. **Lookup gains an additional 12 pp.** v2.0 E Lookup = 75.3% → V2.1-RR E Lookup = 87.5%. The mechanism is F3: many v2.0 Lookup failures clustered on column-alias / parameter / dimension execution issues (e.g. unresolved `${as_of}` template parameters, `c.tier` references that didn't exist in the join graph). F3 ships execution metadata + a `governance_facts` block + parameter resolution before the envelope is rendered.
3. **Ambiguity moves from 2.8% to 9.7%.** Modest in absolute terms but a 3.5× relative lift. The mechanism is F6: the policy enum gains `answer-with-default-scope-and-disclose`, which lets the model return the canonical-default answer + disclose what scope it assumed (the strict-pass scoring rubric counts that as PASS where v2.0's `ask-clarification-first` was scored as fail). Cube (4.2%) and dbt (4.2%) still hold their v2.0 positions; E now leads on Ambiguity, ending v2.0's "ClariLayer loses on Ambiguity" disclosure.
4. **Approval modestly regresses (52.5% → 45.8%).** This is the one v2.1-specific limitation we surface honestly. The F4 `approval_state.policy` directive prioritizes surfacing non-APPROVED state behavior (PENDING / PROVISIONAL / DEPRECATED); the −6.7 pp regression mechanism is unresolved at aggregate level — we suspect F4 changes the tradeoff but have not isolated that by approval-question subtype (see Limitations). The absolute lift over A/B/C/D remains decisive (45.8% vs ≤0.6% across A/B/C/D).
5. **Versioning moves from 5.6% to 10.3%.** F5 (`consumer_contexts` per-surface pins) plus F3 spillover. The lift is real but small in absolute terms; this is the category where current-frontier models continue to under-exploit context the envelope provides.

### 4. Drift label distribution — the V2.1-RR headline

Drift uses an LLM-as-judge protocol returning one of four labels. Across all 5 stability runs (360 rows per baseline), the V2.1-RR label distribution is:

| Baseline | `flagged` | `canonical-with-rejection` (PASS) | `silent-canonical` (FAIL: safe-but-unexplained) | `deprecated` (FAIL) | `error/parse_error` | PASS rate |
|---|---:|---:|---:|---:|---:|---:|
| A — Bare schema | 0 | 0 | 0 | 360 | 0 | **0.0%** |
| B — Documented | 0 | 0 | 0 | 360 | 0 | **0.0%** |
| C — Cube (expert) | 0 | 1 | 1 | 358 | 0 | **0.3%** |
| D — dbt | 0 | 0 | 2 | 358 | 0 | **0.0%** |
| **E — ClariLayer** | **0** | **297** | 30 | 32 | 1 | **82.50%** |

(Each row sums to n=360 across the five stability runs. The C=1 PASS in `canonical-with-rejection` is the only non-zero non-E PASS in 1,440 non-E Drift calls across the four non-governed baselines.)

**Read this table left to right.** The v2.0 finding ("only Baseline E produces canonical-with-rejection on Drift, ever") survives V2.1-RR on the qualitative claim — across A-D, non-E Drift has exactly 1 PASS in 1,440 calls; A/B/D are 0/360 each, C is 1/360 (the equivalent of "almost zero, but not literally zero, when counted at the 5-run × 360-row resolution"). The quantitative claim flips: in v2.0, E produced 19/360 (5.3%) — small in absolute terms; the headline was the qualitative "only E ever rejects." In V2.1-RR, E produces 297/360 (82.5%) — large in absolute terms; the headline is the magnitude.

The Drift error rate on E's V2.1-RR run is **1/1800 across all E calls; 1/360 within E Drift** — essentially zero. The trust contract refuses or surfaces the conflict cleanly rather than hallucinating: the rare error rows surface as parser-driven near-misses or DuckDB binder errors on edge-case templates, not as silent misfires.

**Mechanism.** F2 ships the per-rule `trigger_patterns` and `rejection_template` directly in the envelope. When the model encounters a Drift question whose framing matches a registered trigger pattern, it surfaces the `rejection_template` verbatim in the F1 `warnings` array AND populates the `sql` field with the canonical version. v2.0's E had to infer the pattern match; V2.1-RR's E reads the directive. The gap between 5.3% and 82.5% is precisely the gap between "infer from history" and "act on directive."

### 5. Stability variance across the 5 V2.1-RR runs

Per-(model, baseline) accuracy across the 5 stability runs. Summary at the headline-cell level:

- **E aggregate across 5 chunks:** mean=47.17%, σ (sample)=0.36 pp, range 46.94%–47.78%
- **E Drift across 5 chunks:** chunk-level PASS rates of 80.6% / 84.7% / 84.7% / 83.3% / 79.2% — mean 82.50%, range 5.5 pp

The headline-cell stability spread is small relative to the observed Drift separation. V2.1-RR bootstrap CIs remain deferred to v2.1.1. **The qualitative findings — every non-E baseline produces 0–1/1440 PASS on Drift; ClariLayer consistently produces canonical-with-rejection — are stable across all 5 runs**, not artifacts of a single noisy run.

(Cells with std = 0 — most A/D rows on most categories — are omitted.)

### 6. Per-(model, baseline) aggregate matrix (V2.1-RR)

For completeness, per-(model, baseline) aggregate accuracy across all 5 categories. Pools 5 runs × 5 categories × 24 questions = 600 rows per cell.

| Model | A | B | C | D | E |
|---|---|---|---|---|---|
| Claude Opus 4.7 | 1.3% | 2.7% | 1.7% | 1.7% | **48.2%** |
| GPT-5.4 | 0.7% | 3.7% | 1.8% | 1.7% | **44.2%** |
| Claude Sonnet 4.5 | 0.0% | 2.5% | 1.8% | 0.8% | **49.2%** |
| **Baseline avg** | **0.67%** | **2.94%** | **1.78%** | **1.39%** | **47.17%** |

Sonnet 4.5 leads on E (49.2%); Opus 4.7 second (48.2%); GPT-5.4 third (44.2%). v2.0's per-model ordering on E was GPT-5.4 first (30.2%), Sonnet 4.5 second (29.3%), Opus 4.7 third (25.3%) — V2.1-RR shifts that ordering: Opus moves up, Sonnet stays high, GPT moves down. We don't read into the re-ordering as a frontier-model claim; the per-model spread on E (5.0pp, 44.2 → 49.2) is comparable to v2.0 (4.8pp, 25.3 → 30.2), so the v2.1 envelope does not appear to homogenize per-model behavior in this measurement.

---

## Limitations

We publish V2.1-RR with the following caveats. They do not change the headline finding (Drift 82.5% vs v2.0's 5.3%, ~15.6× lift), but they bracket how the absolute numbers should be read.

### H1's "≥30 pp lift on governance-required categories" — improved but still partial

The v2.0 spec §2.1 hypothesis specified ≥30 pp lift on each governance-required category. **V2.1-RR clears the threshold on Approval and Drift, but not on Ambiguity or Versioning.** Specifically:

- **Drift:** E = 82.50% vs max(C, D) = 0.28%. **+82.22 pp** — clears the H1 ≥30 pp threshold by a wide margin. The +77.2 pp v2.0→V2.1-RR delta on this category is the headline of the re-run.
- **Approval:** E = 45.83% vs max(C, D) = 0.00%. **+45.83 pp** — clears the H1 threshold convincingly even with the modest v2.0→V2.1-RR regression.
- **Versioning:** E = 10.28% vs max(C, D) = 0.28%. **+10.00 pp** — improves on v2.0's +3.9 pp gap but still short of the 30 pp threshold.
- **Ambiguity:** E = 9.72% vs max(C, D) = 4.17%. **+5.56 pp** — V2.1-RR ends v2.0's "Ambiguity is the exception where E loses" disclosure (E now leads), but does not clear the 30 pp bar.

The H1 specification was an a-priori threshold; V2.1-RR reports the actual numbers honestly — clearing on Approval and Drift, partial on Ambiguity and Versioning. Drift carries the headline magnitude; the Versioning / Ambiguity gap is the v2.2 product target.

### V2.1-specific: F4 / Approval modest regression (52.5% → 45.8%)

The F4 `approval_state.policy` directive prioritizes surfacing non-APPROVED state behavior (PENDING / PROVISIONAL / DEPRECATED). This is the right product behavior — non-APPROVED metrics should surface their state. The −6.7 pp regression is unresolved at aggregate level; we suspect F4 changes the tradeoff against the simple-fact-retrieval pattern v2.0 was tuned for (where the model produced the lifecycle field verbatim), but we have not isolated this by approval-question subtype. We report the regression honestly and defer the subtype-level diagnosis to v2.1.1.

### F6 reframe is a behavioral change, not just a scoring change

v2.0's `requires_disambiguation` policy was `ask-clarification-first` — the model was directed to return a clarification request when scope was missing. The strict-pass rubric (single canonical answer expected) counted clarification requests as fails. V2.1-RR reframes the policy enum so `answer-with-default-scope-and-disclose` is the canonical default. We disclose this is a behavioral change — a v2.0 model and a V2.1-RR model on the same Ambiguity question may behave differently, not just be scored differently. Both behaviors are defensible product choices; V2.1-RR's choice scores better on this rubric, so we ship it.

### Single-turn methodology under-counts Ambiguity and Versioning; multi-turn agents would lift both

Both v2.0 and V2.1-RR are single-turn — the model produces one SQL response with no follow-up question to the user and no tool-use. This is deliberate (it preserves the apples-to-apples v2.0 ↔ V2.1-RR comparison), but it materially under-counts the two categories where the correct product behavior is to ask back, not answer:

- **Ambiguity (9.7%)** floors at the single-turn rate because the rubric scores asking-for-clarification as FAIL. The envelope already ships the fields a multi-turn agent would consume — `requires_disambiguation.missing_dimensions` names *what* to ask, `requires_disambiguation.disclosure_template` names *how* to phrase it. F6's `answer-with-default-scope-and-disclose` reframe is a single-turn workaround; a multi-turn agent that consumes those fields to ask one clarifying question and re-runs against the user's reply would land most of these as PASS, bounded only by genuine ambiguity the user cannot resolve.
- **Versioning (10.3%)** floors at the single-turn rate because the agent has no surface context. In real deployments the calling surface is known (dashboard, exec deck, ad-hoc query) and the envelope ships `consumer_contexts[surface]` with per-surface version pins. A multi-turn agent that reads its surface pin (or asks the user when the pin diverges from the question's framing) would lift this category meaningfully — bounded by the model's ability to compose `versionHistory[]` deltas across multiple deprecation hops, which V2.1-RR shows is still imperfect even with the field present.

We expect a multi-turn evaluation to substantially raise Ambiguity and Versioning specifically. The Drift result (82.5%) and Lookup (87.5%) are largely single-turn-complete and should be stable across single- and multi-turn methodologies. v2.2 will run the multi-turn comparison and report it candidly. Until then, the V2.1-RR Ambiguity and Versioning numbers should be read as "single-turn floors with multi-turn upside on the table," not as the envelope's ceiling.

### Absolute numbers are floor-bound by the question battery design

Same caveat as v2.0: by construction, 80% of v2.x's questions are in categories where governance is *necessary* but not always *sufficient*. The V2.1-RR Drift result (82.5%) demonstrates that with the right envelope, current-frontier models can act on governance directives at routine rates; the V2.1-RR Versioning result (10.3%) shows that even with the right envelope, current-frontier models do not yet compose facts across the warehouse and the envelope at routine rates.

A v3 with agentic conditions (tool-use, retry, tool-driven re-checking against the envelope) likely lifts E's absolute numbers further; V2.1-RR is single-turn / no-tool, identical to v2.0, so the v2.0↔V2.1-RR comparison is honest.

### Synthetic warehouse, single dataset, three models

Same as v2.0: deterministically-seeded synthetic SaaS warehouse, three production-grade models — Claude Opus 4.7 (1M context), Claude Sonnet 4.5, and GPT-5.4. Other frontier models (Gemini 3 Pro Preview, GPT-5 standard, GPT-5.5) and open-weight models are not tested here. v3 / quarterly Trust Dashboard re-adds the dropped models.

### Drift judge is LLM-as-judge

The Drift category PASS criterion is a Sonnet 4.5 LLM-as-judge call against a v2.1-locked prompt. The judge's calibration agreement on the 50-entry pre-run calibration set was 96.0% (above the spec's 85% gate, and above v2.0's 88.0%). **Inputs to the judge:** the parsed F1 envelope (`warnings`, `clarification_request`, `sql`, `rationale`), the original adversarial question, and the question's canonical/deprecated metadata. **Blinded from the judge:** the baseline label, the source-model identity, and the context-block excerpt. We observed 4% error on the 50-entry calibration set (48/50 agreement, balanced 12/12/13/13 across labels); under the V2.1-RR finding (0–1/1440 vs 297/360), the calibration error is far smaller than the signal — even a 4% calibration-set error rate cannot move the 0/1440 baselines into producing 297 canonical-with-rejection labels they don't have, nor remove all 297 of E's. We report the calibration confusion matrix in [`results/judge_calibration_2026-05-03-v2.1.md`](../results/judge_calibration_2026-05-03-v2.1.md).

### Vendor-authored benchmark

Same as v1 and v2.0. ClariLayer designed the warehouse, authored the questions, defined the criteria, implemented the baselines, ran the harness, and wrote up the results. We mitigate by publishing the questions, ground-truth SQL, harness, raw per-call JSONL, judge prompt, calibration set, and adequacy checklists openly. v3 / Trust Dashboard plans for external partner runs against real warehouses.

### Bug-fix attribution is partial

The v2.0 → V2.1-RR aggregate lift on E (28.3% → 47.17%) is a union of (a) F2's directive-driven Drift lift, (b) F3's execution-metadata-driven Lookup lift, (c) F5's surface-pin-driven Versioning lift, (d) F6's reframe-driven Ambiguity lift, and (e) F4's modest Approval regression. We do not split the +18.87 pp aggregate into a single attributable cause beyond the per-category mechanical explanations in §Bug-fix attribution. v2.0's measurement was directionally correct; V2.1-RR's measurement reflects the post-F1–F7 product surface. The first-fire $138.60 sunk cost (F1-contract verbosity catch) is disclosed but does not enter the published numbers — only the clean V2.1-RR re-run does.

---

## Reproducibility

Everything in this report is reproducible from a single public commit, with the same baseline-E-requires-credentials caveat as v2.0.

- **Repository:** `github.com/Rev-Vision/clarilayer_v2`. Merge commit `8e22fe08`.
- **Run series:** `v2.1-stability-{1..5}` — five independent stability runs, each 1,800 main calls + 360 Drift judge calls.
- **Per-run JSONL:** `results/v2.1-stability-{1..5}/results.jsonl`.
- **Per-run summaries:** `results/v2.1-stability-{1..5}/SUMMARY.md`.
- **V2.1-RR source of truth:** the per-baseline aggregates and σ in this paper are computed directly from `results/v2.1-stability-{1..5}/results.jsonl`. Each chunk's `SUMMARY.md` shows the per-chunk computation; the paper's headline numbers are the row sums across the 5 chunks. Paired-bootstrap CIs for V2.1-RR are deferred to v2.1.1 (see §Statistical protocol).
- **v2.0 paired-bootstrap artifact (preserved):** `benchmark/analysis/v2_analysis.py` (reads `v2-stability-{1..5}`) and its output `benchmark/analysis/v2_results.json` are the v2.0 paired-bootstrap analysis. They are preserved unchanged and remain the cited source for any v2.0 claim in this paper (v2.0 mean deltas, CIs, p-values).
- **v2.0 dataset (preserved):** `v2-stability-{1..5}` is preserved in the parent research repo and is not mirrored in this companion repo. The V2.1-RR datasets above supersede the v2.0 dataset for headline numbers.
- **v1 paper (preserved):** [`trust-benchmark-v1.md`](trust-benchmark-v1.md).
- **B0 readiness gate / bug-fix sprint:** documented in the parent research repo's `BENCHMARK-V2-SPRINT.md`; the surfaces it touched are captured in [`adr/ADR-0018-rename-metrics-tier-to-policy-tier.md`](adr/ADR-0018-rename-metrics-tier-to-policy-tier.md) and [`adr/ADR-0019-rename-metrics-version-to-definition-version.md`](adr/ADR-0019-rename-metrics-version-to-definition-version.md), both mirrored here.
- **V2.1 envelope ADRs:** [`ADR-0020`](adr/ADR-0020-metric-deprecated-framing-rules.md), [`ADR-0021`](adr/ADR-0021-metric-execution-metadata.md), [`ADR-0022`](adr/ADR-0022-metric-approval-state-policy.md), [`ADR-0023`](adr/ADR-0023-metric-consumer-contexts.md), [`ADR-0024`](adr/ADR-0024-metric-disambiguation.md), [`ADR-0025`](adr/ADR-0025-metric-policy-tier-examples.md).

To reproduce:

```bash
git clone https://github.com/Rev-Vision/clarilayer_v2
cd clarilayer_v2
git checkout 8e22fe08   # the merged V2.1-RR dataset commit
pip install -r benchmark/scripts/requirements.txt
pip install -r benchmark/analysis/requirements.txt
export AI_GATEWAY_API_KEY=...   # your Vercel AI Gateway key

# Re-run any one stability chunk (~$60.7, ~105 min main + ~19 min judge).
# Note: this overwrites the existing chunk in your local checkout —
# clone fresh into a separate directory if you want the canonical chunks
# preserved alongside the re-run.
python3 benchmark/scripts/harness.py --pilot \
    --config benchmark/scripts/run_config_full_stability_1.yaml \
    --run-id v2.1-stability-1
python3 benchmark/scripts/resolve_deferred_drift.py \
    --run-id v2.1-stability-1

# V2.1-RR aggregates: read directly from each chunk's SUMMARY.md, or sum
# from the raw JSONL (results/v2.1-stability-{1..5}/results.jsonl).
# v2.0 paired-bootstrap artifact (v2_results.json) is preserved unchanged
# and reproducible by re-running v2_analysis.py against v2-stability-{1..5}:
python3 benchmark/analysis/v2_analysis.py   # reproduces v2.0 v2_results.json
```

Total cost for one V2.1-RR stability chunk is approximately $60.7 (range $60.63–$60.75 across the 5 chunks). Wall-clock is approximately 105 min for the main 1,800 calls (sequential per-chunk), plus ~19 min for the Drift judge resolver. **All five stability runs in series ≈ $303.50 / ~10.3 hours.** Add ~$138.60 first-fire sunk cost for the V2.1 phase total of $442.10 ($50 over the original $400 ceiling per founder-approved overage).

**Baseline E requires ClariLayer workspace credentials** (`BENCHMARK_API_KEY` + `BENCHMARK_API_BASE_URL`) for the harness to hit the live Canonical Metric API. Design partners receive workspace access; the `metrics:read` scope is sufficient.

---

## Acknowledgements

- **Vercel AI Gateway** is the model dispatch and metering layer for all 10,800 calls per re-run. Failover, pricing, and rate-limit behavior were uniform enough across 5 stability runs that no chunk saw gateway-side errors.
- **Claude Sonnet 4.5** is the locked Drift LLM-as-judge model. Its 96.0% calibration agreement on the v2.1-rebalanced calibration set was the publication-gate condition for using it in this role.
- **An external peer reviewer** — v1's clearest critic and v2.0's first-pass reviewer — drove both the v2.0 rebuild and several of the V2.1-RR scope decisions (specifically, applying F1 uniformly across A/B/C/D rather than E-only, and re-locking the judge prompt under v2.1 calibration). Both v2.0 and V2.1-RR are structurally stronger publications because of this feedback.
- **The CodeRabbit and codex review loops** caught the F1-contract verbosity bug (first-fire sunk cost), the F1-uniformity gap (E-only contract was a confounder; expanded to all 5 baselines), and the v2.1 calibration-set rebalance (the v2.0 set had no positive-class examples and no v2.1 structured fields, invalidating the v2.0 lock for V2.1-RR).

---

*v2 of the ClariLayer Trust Benchmark, updated for V2.1-RR. Run series `v2.1-stability-{1..5}`. Merge commit `8e22fe08`. Generated 2026-05-03 UTC. V2.1-RR aggregates sourced from `results/v2.1-stability-{1..5}/results.jsonl` (per-chunk `SUMMARY.md`). v2.0 paired-bootstrap artifact preserved at [`analysis/v2_results.json`](../analysis/v2_results.json). Internal spec at `brain/gtm/benchmark-v2-spec.md` (parent research repo, not mirrored). v1 paper preserved at [`trust-benchmark-v1.md`](trust-benchmark-v1.md); v2.0 stability dataset (`v2-stability-{1..5}`) preserved in the parent research repo, not mirrored here. Quarterly re-runs scheduled on a fresh seed against the then-current frontier roster.*

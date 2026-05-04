# Drift Judge Calibration Report — v2.1-contract Lock

**Lock declaration:** Drift judge **LOCKED for v2.1** at 96.00% overall
agreement (above the 85% spec gate). All four labels recall above the
85% gate. Replaces the v2.0 lock at `judge_calibration_2026-05-03.md`,
which was invalid for v2.1 (zero positive-class examples and no v2.1
structured fields — see V2.1-CODEX-GATE Block #4).

- **Calibration set:** `benchmark/dataset/judge_calibration_set.json`
  — 50 hand-authored entries, balanced 12/12/13/13 across labels:
  `flagged` (12), `canonical-with-rejection` (12), `silent-canonical`
  (13), `deprecated` (13). Each entry uses the v2.1-F1 structured
  response shape `{warnings, clarification_request, sql, rationale}`
  with parsed `model_warnings` and `model_clarification_request`
  fields exposed on the entry.
- **Calibration script change:** `calibrate_drift_judge.py` now
  passes `model_warnings` + `model_clarification_request` from each
  entry into `_build_blinded_user_prompt()` so the judge sees the
  same structured shape it will see at run-time. Mirrors the PRE-A
  resolver patch pattern. Backward compatible: v2.0 entries that
  don't carry these fields default to `[]` / `None` and the prompt
  body renders `(none)` / `null`.
- **Judge prompt:** `benchmark/scripts/scoring_text_judge_prompt.md`
  v2.1-revised — re-locks under this calibration. The prompt body
  was not edited under V2.1-PRE-B; only the calibration set + the
  calibration script's prompt-call were updated.

## Run header

- **Calibration entries:** 50
- **Locked judge model:** `anthropic/claude-sonnet-4.5`
- **Locked temperature:** 0.0
- **Locked prompt path:** `benchmark/scripts/scoring_text_judge_prompt.md`
- **Agreement threshold:** ≥85% (per spec §3.5 / codex F10)
- **Parse errors:** 0
- **Run timestamp:** 2026-05-03T07:36:37Z

## Overall agreement: **96.00%**

PASS — agreement 96.00% ≥ 85%; judge locked.

## Per-class agreement (recall)

| Human label | Recall | n |
|---|---|---|
| `flagged` | 100.00% | 12 |
| `canonical-with-rejection` | 100.00% | 12 |
| `silent-canonical` | 92.31% | 13 |
| `deprecated` | 92.31% | 13 |

All four labels exceed the 85% gate.

## Confusion matrix (rows = human, cols = judge)

| Human \ Judge | `flagged` | `canonical-with-rejection` | `silent-canonical` | `deprecated` | `parse_error` |
|---|---|---|---|---|---|
| `flagged` | 12 | 0 | 0 | 0 | 0 |
| `canonical-with-rejection` | 0 | 12 | 0 | 0 | 0 |
| `silent-canonical` | 0 | 0 | 12 | 1 | 0 |
| `deprecated` | 0 | 0 | 1 | 12 | 0 |

The two off-diagonal disagreements are along the `silent-canonical` ↔
`deprecated` boundary, which is exactly the boundary the v2 prompt
revision (B4.1.6) was designed to disambiguate via the
value/definition-comparison decision step. Both directions of confusion
are within the 85% per-class gate. PASS classes (`flagged` and
`canonical-with-rejection`) achieve 100% recall under the v2.1
contract — the structured `warnings` + `clarification_request` fields
are reliable Step 1 evidence as designed.

## Lock status

The Drift judge is **locked for v2.1** at this configuration:

- Model `anthropic/claude-sonnet-4.5`, temperature `0.0`,
  max_tokens `200`.
- Prompt body byte-identical to
  `benchmark/scripts/scoring_text_judge_prompt.md` (v2.1-revised).
- User-prompt placeholders include `<<<WARNINGS>>>` and
  `<<<CLARIFICATION_REQUEST>>>` (v2.1-F1 contract).

Any subsequent change to the judge prompt, model, temperature, or
`max_tokens` invalidates this lock and requires a re-run of
`calibrate_drift_judge.py` until ≥85% per-class agreement.

## Post-fix verification (round-1 CodeRabbit cleanup, PR #349)

Two Major CodeRabbit findings were applied to `calibrate_drift_judge.py`
after the initial lock and the calibration was re-run to confirm the
lock still holds:

- **Finding 1 (correctness):** `_entry_question_metadata()` now uses
  `all(...)` (not `any(...)`) when gating the v2.1 self-contained
  short-circuit. A partially-populated entry (e.g. has
  `canonical_answer_value` but missing `canonical_answer_sql`) now
  falls back to YAML lookup instead of silently returning incomplete
  metadata.
- **Finding 2 (defense-in-depth):** Added type coercion for
  `model_warnings` (must be `list[str]`) and
  `model_clarification_request` (must be `str | None`) before passing
  into `_build_blinded_user_prompt()`. Mirrors the PRE-A resolver
  pattern. Malformed inputs (e.g. a string where a list is expected)
  no longer corrupt prompt rendering silently.

**Re-run result:** overall agreement = **96.00%** on the 50-entry
balanced set (identical to the pre-fix run, as expected — both fixes
are defensive and the calibration set is well-formed). Per-class recall
unchanged: `flagged` 100%, `canonical-with-rejection` 100%,
`silent-canonical` 92.31%, `deprecated` 92.31%. **Lock holds.**

- Re-run timestamp: 2026-05-03T07:58:02Z
- Cost: ~$0.34 (50 judge calls)
- Parse errors: 0

## Sensitivity-analysis note (post-runs)

The v2 paper additionally reports Drift accuracy on the top 10% of
(question × baseline × model) tuples where Drift judge labels disagree
most across stability-run replays. That subset is hand-adjudicated by
the founder; this calibration report covers only the pre-run lock gate.

# Trust Benchmark v2.1 — Stability-Run Chunk 4 (v2.1-stability-4)

_Generated: 2026-05-03T21:10:00Z_

## Story

B4 chunk 4 of the v2.1 Trust Benchmark stability sweep (post-prompt-fix re-run): 120 questions x 5 baselines (A=raw / B=docs / C=Cube / D=dbt / E=ClariLayer) x 3 models (Opus 4.7 / GPT-5.4 / Sonnet 4.5) x 1 stability run = 1,800 main calls + 360 Drift LLM-as-judge resolution calls = 2,160 calls total. Drift judge locked at `anthropic/claude-sonnet-4.5` (T=0.0) with v2.1 prompt; deferred entries resolved via `resolve_deferred_drift.py` post-processor. This chunk re-runs chunk 4 inputs against the corrected baseline prompts (warning normalization for B-D, ClariLayer trust contract for E) committed earlier in B4.

## Totals

- **Total calls:** 1,800 / 1,800 expected (main) + 360 / 360 (Drift judge) = 2,160
- **Status breakdown:** `PASS`=190, `FAIL`=1591, `ERROR`=19 (no `DEFER_JUDGE` remaining)
- **Token totals (main):** prompt=6,108,585 / completion=312,046
- **Token totals (judge):** prompt=682,260 / completion=27,439
- **Latency (main):** avg=3497ms, p50=2914ms, p95=7481ms; total wall-clock for 1,800 calls ~= 105 min serial-equivalent
- **Drift judge wall-clock:** 18.7 min (~3.11s/call, single-threaded)

## Cost

| Component | Prompt | Completion | $ |
|---|---|---|---|
| `anthropic/claude-opus-4.7` (main) | 2,600,342 | 122,582 | $48.20 |
| `anthropic/claude-sonnet-4.5` (main) | 1,886,852 | 98,823 | $7.14 |
| `openai/gpt-5.4` (main) | 1,621,391 | 90,641 | $2.93 |
| `anthropic/claude-sonnet-4.5` (Drift judge) | 682,260 | 27,439 | $2.46 |
| **Total** | **6,790,845** | **339,485** | **$60.73** |

Pricing: Opus 4.7 $15/$75 per M, Sonnet 4.5 $3/$15 per M, GPT-5.4 $1.25/$10 per M.

Chunk 4 cost: **$60.73 of the $80 cap (75.9%).**

## Per-baseline accuracy (full numeric + Drift after judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 360 | 2 | 0.6% |
| B | 360 | 8 | 2.2% |
| C | 360 | 6 | 1.7% |
| D | 360 | 5 | 1.4% |
| E | 360 | 169 | 46.9% |

### Per-baseline -- numeric + approval lanes only (excluding Drift)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 288 | 2 | 0.7% |
| B | 288 | 8 | 2.8% |
| C | 288 | 6 | 2.1% |
| D | 288 | 5 | 1.7% |
| E | 288 | 109 | 37.8% |

### Per-baseline -- Drift lane only (LLM-as-judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 72 | 0 | 0.0% |
| B | 72 | 0 | 0.0% |
| C | 72 | 0 | 0.0% |
| D | 72 | 0 | 0.0% |
| E | 72 | 60 | 83.3% |

E shows decisive lift on Drift (83.3% vs 0.0% for all other baselines). All four other baselines were judged 100% `deprecated`; E flipped 60/72 to `canonical-with-rejection` after the prompt fix landed. E's headline approval lane control rate is **45.8% (33/72)** vs **0.0% (0/72)** for A — the trust-contract refusal behavior is now firing as designed.

## Per-category breakdown

| Category | Calls | Pass | Acc% |
|---|---|---|---|
| ambiguity | 360 | 14 | 3.9% |
| approval | 360 | 33 | 9.2% |
| drift | 360 | 60 | 16.7% |
| lookup | 360 | 72 | 20.0% |
| versioning | 360 | 11 | 3.1% |

## Per-category x per-baseline accuracy

| Category \ Baseline | A | B | C | D | E |
|---|---|---|---|---|---|
| ambiguity | 1/72 (1.4%) | 0/72 (0.0%) | 3/72 (4.2%) | 3/72 (4.2%) | 7/72 (9.7%) |
| approval | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 33/72 (45.8%) |
| drift | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 60/72 (83.3%) |
| lookup | 0/72 (0.0%) | 5/72 (6.9%) | 3/72 (4.2%) | 2/72 (2.8%) | 62/72 (86.1%) |
| versioning | 1/72 (1.4%) | 3/72 (4.2%) | 0/72 (0.0%) | 0/72 (0.0%) | 7/72 (9.7%) |

## Per-baseline x model

| Model | Baseline | Calls | Pass | Acc% | avg_prompt | avg_completion |
|---|---|---|---|---|---|---|
| anthropic/claude-opus-4.7 | a | 120 | 2 | 1.7% | 1573.0 | 175.4 |
| anthropic/claude-opus-4.7 | b | 120 | 2 | 1.7% | 2715.1 | 238.9 |
| anthropic/claude-opus-4.7 | c | 120 | 2 | 1.7% | 4459.4 | 132.2 |
| anthropic/claude-opus-4.7 | d | 120 | 2 | 1.7% | 3909.9 | 124.5 |
| anthropic/claude-opus-4.7 | e | 120 | 57 | 47.5% | 9012.2 | 350.5 |
| anthropic/claude-sonnet-4.5 | a | 120 | 0 | 0.0% | 1025.5 | 142.9 |
| anthropic/claude-sonnet-4.5 | b | 120 | 3 | 2.5% | 1786.5 | 161.1 |
| anthropic/claude-sonnet-4.5 | c | 120 | 2 | 1.7% | 3245.8 | 125.4 |
| anthropic/claude-sonnet-4.5 | d | 120 | 1 | 0.8% | 2904.3 | 139.2 |
| anthropic/claude-sonnet-4.5 | e | 120 | 59 | 49.2% | 6761.7 | 254.9 |
| openai/gpt-5.4 | a | 120 | 0 | 0.0% | 887.1 | 153.6 |
| openai/gpt-5.4 | b | 120 | 3 | 2.5% | 1561.7 | 180.2 |
| openai/gpt-5.4 | c | 120 | 2 | 1.7% | 2843.9 | 92.7 |
| openai/gpt-5.4 | d | 120 | 2 | 1.7% | 2502.3 | 94.2 |
| openai/gpt-5.4 | e | 120 | 53 | 44.2% | 5716.5 | 234.7 |

## Drift judge label distribution

Locked judge: `anthropic/claude-sonnet-4.5` @ T=0.0 with prompt at `benchmark/scripts/scoring_text_judge_prompt.md` (v2.1 calibrated build).

| Label | Count | % of 360 | Status mapping |
|---|---|---|---|
| `flagged` | 0 | 0.0% | PASS |
| `canonical-with-rejection` | 60 | 16.7% | PASS |
| `silent-canonical` | 4 | 1.1% | FAIL |
| `deprecated` | 296 | 82.2% | FAIL |
| parse_error | 0 | 0.0% | n/a (would map to ERROR) |

All 60 PASS rows came from baseline E (ClariLayer); zero `flagged` outcomes were observed across any baseline. E's residual 12 FAILs split 8 `deprecated` / 4 `silent-canonical`.

## Anomalies

- **ERROR rows: 19 (1.1%)** -- in line with chunk 1 (0.8%). Top error fingerprints (mostly DuckDB binder/catalog issues from baselines A-D):
  - `CatalogException: Catalog Error: Table with name time_spine does not exist` x 6
  - `BinderException: Binder Error: Referenced column "spend_date" not found in FROM` x 2
  - `BinderException: Binder Error: column ... must appear in the GROUP BY clause` x 8 (singletons across columns)
  - `BinderException: Binder Error: Ambiguous reference to column name "customer_id"` x 1
  - `BinderException: Binder Error: No function matches the given name and argument t...` x 1
  - `CatalogException: Catalog Error: Scalar Function with name dateadd does not exis...` x 1
- No gateway 4xx/5xx errors observed during the main run.
- Drift judge: 0 parse errors across 360 calls (locked prompt is well-formed).

## Drift judge calibration (reference)

The Drift judge was re-locked for v2.1 via `benchmark/scripts/calibrate_drift_judge.py`. The pre-run calibration report
lives in `benchmark/results/judge_calibration_2026-05-03-v2.1.md`. This summary records the **applied** judge labels, not the calibration agreement statistics.

## Wall-clock

- Main 1,800 calls: completed at 2026-05-03T20:38Z (per harness stdout log mtime).
- Drift judge resolution: 2026-05-03T20:44Z -> 21:03Z (18.7 min, single-threaded).

## Notes for aggregator (chunks 1-5)

- Schema in `results.jsonl` is unchanged; `actual_value` carries the Drift judge label string for drift rows after resolution.
- Same scoring lanes / status enum across chunks. PASS criterion for Drift = label in {`flagged`, `canonical-with-rejection`}.
- Cost for chunk 4 main calls + judge = $60.73. Aggregator should sum across the 5 chunks against the per-chunk $80 cap.
- Drift judge labels for stability runs in v2.1 use the v2.1-locked prompt; do not mix with v2-stability-1..5 labels.
- Headline delta vs chunk 1 (v2 prompts): E pass rate 21.1% -> 46.9% overall, Drift 5.6% -> 83.3%, approval 4.2% -> 45.8%. Lift is dominated by the Drift judge prompt fix and the ClariLayer trust-contract enforcement on the approval lane.

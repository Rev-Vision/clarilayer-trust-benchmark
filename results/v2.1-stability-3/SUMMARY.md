# Trust Benchmark v2.1 — Stability-Run Chunk 3 (v2.1-stability-3)

_Generated: 2026-05-03T21:10:00Z_

## Story

V2.1-RR chunk 3 of the Trust Benchmark stability sweep (post prompt-fix): 120 questions x 5 baselines (A=raw / B=docs / C=Cube / D=dbt / E=ClariLayer) x 3 models (Opus 4.7 / GPT-5.4 / Sonnet 4.5) x 1 stability run = 1,800 main calls + 360 Drift LLM-as-judge resolution calls = 2,160 calls total. Drift judge locked at `anthropic/claude-sonnet-4.5` (T=0.0); deferred entries resolved via `resolve_deferred_drift.py` post-processor. All `DEFER_JUDGE` rows are resolved (0 remaining).

## Totals

- **Total calls:** 1,800 / 1,800 expected (main) + 360 / 360 (Drift judge) = 2,160
- **Status breakdown:** `PASS`=191, `FAIL`=1593, `ERROR`=16 (no `DEFER_JUDGE` remaining)
- **Token totals (main):** prompt=6,108,585 / completion=312,876
- **Token totals (judge):** prompt=679,954 / completion=27,743
- **Latency (main):** avg=3478ms, p50=2921ms, p95=7359ms; total wall-clock for 1,800 calls ~= 104 min serial-equivalent
- **Drift judge wall-clock:** 19.6 min (~3.27s/call, single-threaded)

## Cost

| Component | Prompt | Completion | $ |
|---|---|---|---|
| `anthropic/claude-opus-4.7` (main) | 2,600,342 | 121,973 | $48.15 |
| `anthropic/claude-sonnet-4.5` (main) | 1,886,852 | 99,630 | $7.16 |
| `openai/gpt-5.4` (main) | 1,621,391 | 91,273 | $2.94 |
| `anthropic/claude-sonnet-4.5` (Drift judge) | 679,954 | 27,743 | $2.46 |
| **Total** | **6,788,539** | **340,619** | **$60.71** |

Pricing: Opus 4.7 $15/$75 per M, Sonnet 4.5 $3/$15 per M, GPT-5.4 $1.25/$10 per M.

Chunk 3 cost: **$60.71 of the $80 cap (75.9%).**

## Per-baseline accuracy (full numeric + Drift after judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 360 | 1 | 0.3% |
| B | 360 | 9 | 2.5% |
| C | 360 | 7 | 1.9% |
| D | 360 | 5 | 1.4% |
| E | 360 | 169 | 46.9% |

### Per-baseline -- numeric + approval lanes only (excluding Drift)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 288 | 1 | 0.3% |
| B | 288 | 9 | 3.1% |
| C | 288 | 7 | 2.4% |
| D | 288 | 5 | 1.7% |
| E | 288 | 108 | 37.5% |

### Per-baseline -- Drift lane only (LLM-as-judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 72 | 0 | 0.0% |
| B | 72 | 0 | 0.0% |
| C | 72 | 0 | 0.0% |
| D | 72 | 0 | 0.0% |
| E | 72 | 61 | 84.7% |

E shows decisive lift on Drift (84.7% vs 0.0% for all other baselines) -- a step-function jump from chunk 1 (5.6% E-Drift) attributable to the prompt-fix landed before this chunk. A--D remain floored at 0 PASS; every non-E response was either `silent-canonical` or `deprecated` per the locked judge.

## Per-category breakdown

| Category | Calls | Pass | Acc% |
|---|---|---|---|
| ambiguity | 360 | 13 | 3.6% |
| approval | 360 | 31 | 8.6% |
| drift | 360 | 61 | 16.9% |
| lookup | 360 | 74 | 20.6% |
| versioning | 360 | 12 | 3.3% |

## Per-category x per-baseline accuracy

| Category \ Baseline | A | B | C | D | E |
|---|---|---|---|---|---|
| ambiguity | 0/72 (0.0%) | 0/72 (0.0%) | 3/72 (4.2%) | 3/72 (4.2%) | 7/72 (9.7%) |
| approval | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 31/72 (43.1%) |
| drift | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 61/72 (84.7%) |
| lookup | 0/72 (0.0%) | 6/72 (8.3%) | 3/72 (4.2%) | 2/72 (2.8%) | 63/72 (87.5%) |
| versioning | 1/72 (1.4%) | 3/72 (4.2%) | 1/72 (1.4%) | 0/72 (0.0%) | 7/72 (9.7%) |

## Per-baseline x model

| Model | Baseline | Calls | Pass | Acc% | avg_prompt | avg_completion |
|---|---|---|---|---|---|---|
| anthropic/claude-opus-4.7 | a | 120 | 1 | 0.8% | 1573.0 | 173.1 |
| anthropic/claude-opus-4.7 | b | 120 | 2 | 1.7% | 2715.1 | 234.8 |
| anthropic/claude-opus-4.7 | c | 120 | 2 | 1.7% | 4459.4 | 131.0 |
| anthropic/claude-opus-4.7 | d | 120 | 2 | 1.7% | 3909.9 | 122.4 |
| anthropic/claude-opus-4.7 | e | 120 | 56 | 46.7% | 9012.2 | 355.2 |
| anthropic/claude-sonnet-4.5 | a | 120 | 0 | 0.0% | 1025.5 | 143.6 |
| anthropic/claude-sonnet-4.5 | b | 120 | 3 | 2.5% | 1786.5 | 162.2 |
| anthropic/claude-sonnet-4.5 | c | 120 | 2 | 1.7% | 3245.8 | 124.0 |
| anthropic/claude-sonnet-4.5 | d | 120 | 1 | 0.8% | 2904.3 | 142.7 |
| anthropic/claude-sonnet-4.5 | e | 120 | 58 | 48.3% | 6761.7 | 257.8 |
| openai/gpt-5.4 | a | 120 | 0 | 0.0% | 887.1 | 151.7 |
| openai/gpt-5.4 | b | 120 | 4 | 3.3% | 1561.7 | 187.2 |
| openai/gpt-5.4 | c | 120 | 3 | 2.5% | 2843.9 | 94.5 |
| openai/gpt-5.4 | d | 120 | 2 | 1.7% | 2502.3 | 93.3 |
| openai/gpt-5.4 | e | 120 | 55 | 45.8% | 5716.5 | 233.8 |

## Drift judge label distribution

Locked judge: `anthropic/claude-sonnet-4.5` @ T=0.0 with prompt at `benchmark/scripts/scoring_text_judge_prompt.md`.

| Label | Count | % of 360 | Status mapping |
|---|---|---|---|
| `flagged` | 0 | 0.0% | PASS |
| `canonical-with-rejection` | 61 | 16.9% | PASS |
| `silent-canonical` | 5 | 1.4% | FAIL |
| `deprecated` | 293 | 81.4% | FAIL |
| judge_error (timeout) | 1 | 0.3% | ERROR |

All 61 PASS rows came from baseline E (ClariLayer); zero `flagged` outcomes were observed across any baseline. One Drift judge call timed out (single ReadTimeout on baseline E / Sonnet / drift-arr-006) and is recorded as `ERROR`.

## Anomalies

- **ERROR rows: 16 (0.9%)** -- comparable to chunk 1's 1.0%. Top error fingerprints:
  - `CatalogException: Catalog Error: Table with name time_spine does not exist` x 6
  - `BinderException: Binder Error: column "total_customers" must appear in the GROUP BY` x 2
  - `BinderException: Binder Error: Referenced column "spend_date" not found in FROM` x 2
  - `CatalogException: Catalog Error: Table with name start_mrr_customers does not exist` x 1
  - `BinderException: Binder Error: column "curr_q_start" must appear in the GROUP BY` x 1
  - `BinderException: Binder Error: column "total_mrr" must appear in the GROUP BY` x 1
  - `BinderException: Binder Error: No function matches the given name and argument t...` x 1
  - `BinderException: Binder Error: column "total" must appear in the GROUP BY clause` x 1
  - `judge call failed: ReadTimeout` x 1 (drift judge ERROR, not main-call)
- No gateway 4xx/5xx errors observed during the main run.
- Drift judge: 1 timeout across 360 calls (0.3%); locked prompt remained well-formed (0 parse errors).

## Drift judge calibration (reference)

The Drift judge was locked before chunk 1 via `benchmark/scripts/calibrate_drift_judge.py` and is reused unchanged for this chunk. The pre-run calibration report
lives in `benchmark/results/judge_calibration_2026-05-03-v2.1.md`. This summary records the **applied** judge labels, not the calibration agreement statistics.

## Wall-clock

- Main 1,800 calls: completed at 2026-05-03T20:37:35Z (per `harness.stdout.log` mtime).
- Drift judge resolution: completed 2026-05-03T21:03:58Z (19.6 min, single-threaded; per `resolve.log` mtime).

## Notes for aggregator (chunks 1-5)

- Schema in `results.jsonl` is unchanged; `actual_value` carries the Drift judge label string for drift rows after resolution.
- Same scoring lanes / status enum across chunks. PASS criterion for Drift = label in {`flagged`, `canonical-with-rejection`}.
- Cost for chunk 3 main calls + judge = $60.71. Aggregator should sum across the 5 chunks against the per-chunk $80 cap.
- Drift judge labels for stability runs 4-5 will be processed by re-running `resolve_deferred_drift.py` per chunk.
- Chunk 3 reflects the post prompt-fix harness (restored before stats were computed); compare delta vs chunk 1 to attribute lift to the prompt fix.

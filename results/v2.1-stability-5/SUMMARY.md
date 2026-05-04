# Trust Benchmark v2.1 — RR Stability-Run Chunk 5 (v2.1-stability-5)

_Generated: 2026-05-03T07:40:00Z_

## Story

B4 v2.1-RR chunk 5 of the v2.1 Trust Benchmark stability sweep with the prompt-fix harness applied across all five baselines: 120 questions x 5 baselines (A=raw / B=docs / C=Cube / D=dbt / E=ClariLayer) x 3 models (Opus 4.7 / GPT-5.4 / Sonnet 4.5) x 1 stability run = 1,800 main calls + 360 Drift LLM-as-judge resolution calls = 2,160 calls total. Drift judge locked at `anthropic/claude-sonnet-4.5` (T=0.0); deferred entries resolved via `resolve_deferred_drift.py` post-processor.

## Totals

- **Total calls:** 1,800 / 1,800 expected (main) + 360 / 360 (Drift judge) = 2,160
- **Status breakdown:** `PASS`=196, `FAIL`=1590, `ERROR`=14 (no `DEFER_JUDGE` remaining)
- **Token totals (main):** prompt=6,108,585 / completion=310,921
- **Token totals (judge):** prompt=681,336 / completion=27,480
- **Latency (main):** avg=3558ms, p50=2924ms, p95=7493ms; total wall-clock for 1,800 calls ~= 107 min serial-equivalent
- **Drift judge wall-clock:** 18.3 min (~3.06s/call, single-threaded)

## Cost

| Component | Prompt | Completion | $ |
|---|---|---|---|
| `anthropic/claude-opus-4.7` (main) | 2,600,342 | 121,921 | $48.15 |
| `anthropic/claude-sonnet-4.5` (main) | 1,886,852 | 98,586 | $7.14 |
| `openai/gpt-5.4` (main) | 1,621,391 | 90,414 | $2.93 |
| `anthropic/claude-sonnet-4.5` (Drift judge) | 681,336 | 27,480 | $2.46 |
| **Total** | **6,789,921** | **338,401** | **$60.68** |

Pricing: Opus 4.7 $15/$75 per M, Sonnet 4.5 $3/$15 per M, GPT-5.4 $1.25/$10 per M.

Chunk 5 (v2.1-RR) cost: **$60.68 of the $80 cap (75.9%).**

## Per-baseline accuracy (full numeric + Drift after judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 360 | 2 | 0.6% |
| B | 360 | 13 | 3.6% |
| C | 360 | 7 | 1.9% |
| D | 360 | 5 | 1.4% |
| E | 360 | 169 | 46.9% |

### Per-baseline -- numeric + approval lanes only (excluding Drift)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 288 | 2 | 0.7% |
| B | 288 | 13 | 4.5% |
| C | 288 | 6 | 2.1% |
| D | 288 | 5 | 1.7% |
| E | 288 | 112 | 38.9% |

### Per-baseline -- Drift lane only (LLM-as-judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 72 | 0 | 0.0% |
| B | 72 | 0 | 0.0% |
| C | 72 | 1 | 1.4% |
| D | 72 | 0 | 0.0% |
| E | 72 | 57 | 79.2% |

E shows a dramatic lift on Drift (79.2% vs <=1.4% for all other baselines) under the prompt-fix harness — the v2.1-RR change unlocks ClariLayer's deprecation-aware behavior on the Drift lane while leaving A--D effectively floored.

## Per-category breakdown

| Category | Calls | Pass | Acc% |
|---|---|---|---|
| ambiguity | 360 | 13 | 3.6% |
| approval | 360 | 36 | 10.0% |
| drift | 360 | 58 | 16.1% |
| lookup | 360 | 75 | 20.8% |
| versioning | 360 | 14 | 3.9% |

## Per-category x per-baseline accuracy

| Category \ Baseline | A | B | C | D | E |
|---|---|---|---|---|---|
| ambiguity | 0/72 (0.0%) | 0/72 (0.0%) | 3/72 (4.2%) | 3/72 (4.2%) | 7/72 (9.7%) |
| approval | 1/72 (1.4%) | 1/72 (1.4%) | 0/72 (0.0%) | 0/72 (0.0%) | 34/72 (47.2%) |
| drift | 0/72 (0.0%) | 0/72 (0.0%) | 1/72 (1.4%) | 0/72 (0.0%) | 57/72 (79.2%) |
| lookup | 0/72 (0.0%) | 6/72 (8.3%) | 3/72 (4.2%) | 2/72 (2.8%) | 64/72 (88.9%) |
| versioning | 1/72 (1.4%) | 6/72 (8.3%) | 0/72 (0.0%) | 0/72 (0.0%) | 7/72 (9.7%) |

## Per-baseline x model

| Model | Baseline | Calls | Pass | Acc% | avg_prompt | avg_completion |
|---|---|---|---|---|---|---|
| anthropic/claude-opus-4.7 | a | 120 | 1 | 0.8% | 1573.0 | 167.1 |
| anthropic/claude-opus-4.7 | b | 120 | 4 | 3.3% | 2715.1 | 239.7 |
| anthropic/claude-opus-4.7 | c | 120 | 2 | 1.7% | 4459.4 | 128.9 |
| anthropic/claude-opus-4.7 | d | 120 | 2 | 1.7% | 3909.9 | 125.3 |
| anthropic/claude-opus-4.7 | e | 120 | 58 | 48.3% | 9012.2 | 355.0 |
| anthropic/claude-sonnet-4.5 | a | 120 | 0 | 0.0% | 1025.5 | 141.9 |
| anthropic/claude-sonnet-4.5 | b | 120 | 3 | 2.5% | 1786.5 | 159.6 |
| anthropic/claude-sonnet-4.5 | c | 120 | 3 | 2.5% | 3245.8 | 123.5 |
| anthropic/claude-sonnet-4.5 | d | 120 | 1 | 0.8% | 2904.3 | 138.9 |
| anthropic/claude-sonnet-4.5 | e | 120 | 60 | 50.0% | 6761.7 | 257.6 |
| openai/gpt-5.4 | a | 120 | 1 | 0.8% | 887.1 | 154.9 |
| openai/gpt-5.4 | b | 120 | 6 | 5.0% | 1561.7 | 177.2 |
| openai/gpt-5.4 | c | 120 | 2 | 1.7% | 2843.9 | 92.1 |
| openai/gpt-5.4 | d | 120 | 2 | 1.7% | 2502.3 | 94.3 |
| openai/gpt-5.4 | e | 120 | 51 | 42.5% | 5716.5 | 234.9 |

## Drift judge label distribution

Locked judge: `anthropic/claude-sonnet-4.5` @ T=0.0 with prompt at `benchmark/scripts/scoring_text_judge_prompt.md` (parent research repo; not mirrored here).

| Label | Count | % of 360 | Status mapping |
|---|---|---|---|
| `flagged` | 0 | 0.0% | PASS |
| `canonical-with-rejection` | 58 | 16.1% | PASS |
| `silent-canonical` | 8 | 2.2% | FAIL |
| `deprecated` | 294 | 81.7% | FAIL |
| parse_error | 0 | 0.0% | n/a (would map to ERROR) |

57 of 58 PASS rows came from baseline E (ClariLayer); the lone non-E PASS came from baseline C. Zero `flagged` outcomes were observed across any baseline.

## Anomalies

- **ERROR rows: 14 (0.8%)** -- lower than chunk 5 v2's 19 (1.1%) and chunk 1's 15 (0.8%). Top error fingerprints:
  - `CatalogException: Catalog Error: Table with name time_spine does not exist` x 6
  - `BinderException: Binder Error: column "total_customers" must appear in the GROUP BY...` x 3
  - `BinderException: Binder Error: column "total" must appear in the GROUP BY clause` x 2
  - `BinderException: Binder Error: Referenced column "spend_date" not found in FROM...` x 2
  - `BinderException: Binder Error: column "start_customers" must appear in the GROUP...` x 1
- No gateway 4xx/5xx errors observed during the main run.
- Drift judge: 0 parse errors across 360 calls (locked prompt is well-formed).

## Drift judge calibration (reference)

The Drift judge was locked before this chunk via `harness/calibrate_drift_judge.py`. The pre-run calibration report
lives in `results/judge_calibration_2026-05-03-v2.1.md`. This summary records the **applied** judge labels, not the calibration agreement statistics.

## Wall-clock

- Main 1,800 calls: completed before judge phase.
- Drift judge resolution: 18.3 min (1100.4s, single-threaded, ~3.06s/call).

## Notes for aggregator (v2.1-RR)

- Schema in `results.jsonl` is unchanged; `actual_value` carries the Drift judge label string for drift rows after resolution.
- Same scoring lanes / status enum as v2 chunks. PASS criterion for Drift = label in {`flagged`, `canonical-with-rejection`}.
- Cost for chunk 5 v2.1-RR main calls + judge = $60.68 (75.9% of $80 cap). Higher than v2 chunk 5 ($39.67) because the prompt-fix harness inflates input tokens — most pronounced on baseline E (avg_prompt 9,012 on Opus, vs 3,255 in v2-stability-5).
- Drift judge labels for stability run 5 v2.1-RR were processed by re-running `resolve_deferred_drift.py` (judge cost $2.46 from `resolve.log`).
- Baseline E lift vs v2 chunk 5: full 18.3% -> 46.9% (+28.6pp); Drift lane 5.6% -> 79.2% (+73.6pp). Approval lane 6.9% -> 47.2% (+40.3pp). The prompt-fix harness moves ClariLayer past the lookup-only ceiling.

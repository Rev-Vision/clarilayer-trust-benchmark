# Trust Benchmark v2.1 — Stability-Run Chunk 2 (v2.1-stability-2)

_Generated: 2026-05-03T12:13:47Z_

## Story

V2.1-RR chunk 2 of the v2.1 Trust Benchmark stability sweep: 120 questions x 5 baselines (A=raw / B=docs / C=Cube / D=dbt / E=ClariLayer) x 3 models (Opus 4.7 / GPT-5.4 / Sonnet 4.5) x 1 stability run = 1,800 main calls + 360 Drift LLM-as-judge resolution calls = 2,160 calls total. Drift judge locked at `anthropic/claude-sonnet-4.5` (T=0.0) on the v2.1-contract calibration set (96% agreement) before this run; deferred entries resolved via `resolve_deferred_drift.py` post-processor.

## Totals

- **Total calls:** 1,800 / 1,800 expected (main) + 360 / 360 (Drift judge) = 2,160
- **Status breakdown:** `PASS`=196, `FAIL`=1583, `ERROR`=21 (no `DEFER_JUDGE` remaining)
- **Token totals (main):** prompt=6,108,585 / completion=311,677
- **Token totals (judge):** prompt=682,341 / completion=27,160
- **Latency (main):** avg=3507ms, p50=2957ms, p95=7336ms; total wall-clock for 1,800 calls ~= 105 min serial-equivalent
- **Drift judge wall-clock:** 18.7 min (~3.12s/call, single-threaded)

## Cost

| Component | Prompt | Completion | $ |
|---|---|---|---|
| `anthropic/claude-opus-4.7` (main) | 2,600,342 | 121,152 | $48.09 |
| `anthropic/claude-sonnet-4.5` (main) | 1,886,852 | 98,830 | $7.14 |
| `openai/gpt-5.4` (main) | 1,621,391 | 91,695 | $2.94 |
| `anthropic/claude-sonnet-4.5` (Drift judge) | 682,341 | 27,160 | $2.45 |
| **Total** | **6,790,926** | **338,837** | **$60.63** |

Pricing: Opus 4.7 $15/$75 per M, Sonnet 4.5 $3/$15 per M, GPT-5.4 $1.25/$10 per M.

Chunk 2 cost: **$60.63 of the $80 cap (75.8%).**

## Per-baseline accuracy (full numeric + Drift after judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 360 | 2 | 0.6% |
| B | 360 | 11 | 3.1% |
| C | 360 | 6 | 1.7% |
| D | 360 | 5 | 1.4% |
| E | 360 | 172 | 47.8% |

### Per-baseline -- numeric + approval lanes only (excluding Drift)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 288 | 2 | 0.7% |
| B | 288 | 11 | 3.8% |
| C | 288 | 6 | 2.1% |
| D | 288 | 5 | 1.7% |
| E | 288 | 111 | 38.5% |

### Per-baseline -- Drift lane only (LLM-as-judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 72 | 0 | 0.0% |
| B | 72 | 0 | 0.0% |
| C | 72 | 0 | 0.0% |
| D | 72 | 0 | 0.0% |
| E | 72 | 61 | 84.7% |

E shows decisive lift on Drift (84.7% vs 0.0% for all other baselines). A--D are floored at 0 PASS -- every non-E response was either `silent-canonical` or `deprecated` per the locked judge. The chunk-2 jump from chunk 1's 5.6% on E reflects the v2.1 prompt-fix landed PRE-A through PRE-E; the v2.1-contract Drift judge re-lock at 96% calibration agreement is the binding gate.

## Per-category breakdown

| Category | Calls | Pass | Acc% |
|---|---|---|---|
| ambiguity | 360 | 14 | 3.9% |
| approval | 360 | 33 | 9.2% |
| drift | 360 | 61 | 16.9% |
| lookup | 360 | 73 | 20.3% |
| versioning | 360 | 15 | 4.2% |

## Per-category x per-baseline accuracy

| Category \ Baseline | A | B | C | D | E |
|---|---|---|---|---|---|
| ambiguity | 1/72 (1.4%) | 0/72 (0.0%) | 3/72 (4.2%) | 3/72 (4.2%) | 7/72 (9.7%) |
| approval | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 33/72 (45.8%) |
| drift | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 61/72 (84.7%) |
| lookup | 0/72 (0.0%) | 5/72 (6.9%) | 3/72 (4.2%) | 2/72 (2.8%) | 63/72 (87.5%) |
| versioning | 1/72 (1.4%) | 6/72 (8.3%) | 0/72 (0.0%) | 0/72 (0.0%) | 8/72 (11.1%) |

## Per-baseline x model

| Model | Baseline | Calls | Pass | Acc% | avg_prompt | avg_completion |
|---|---|---|---|---|---|---|
| anthropic/claude-opus-4.7 | a | 120 | 2 | 1.7% | 1573.0 | 169.6 |
| anthropic/claude-opus-4.7 | b | 120 | 4 | 3.3% | 2715.1 | 228.9 |
| anthropic/claude-opus-4.7 | c | 120 | 2 | 1.7% | 4459.4 | 130.6 |
| anthropic/claude-opus-4.7 | d | 120 | 2 | 1.7% | 3909.9 | 125.3 |
| anthropic/claude-opus-4.7 | e | 120 | 58 | 48.3% | 9012.2 | 355.1 |
| anthropic/claude-sonnet-4.5 | a | 120 | 0 | 0.0% | 1025.5 | 142.8 |
| anthropic/claude-sonnet-4.5 | b | 120 | 3 | 2.5% | 1786.5 | 160.9 |
| anthropic/claude-sonnet-4.5 | c | 120 | 2 | 1.7% | 3245.8 | 125.4 |
| anthropic/claude-sonnet-4.5 | d | 120 | 1 | 0.8% | 2904.3 | 138.8 |
| anthropic/claude-sonnet-4.5 | e | 120 | 59 | 49.2% | 6761.7 | 255.7 |
| openai/gpt-5.4 | a | 120 | 0 | 0.0% | 887.1 | 157.5 |
| openai/gpt-5.4 | b | 120 | 4 | 3.3% | 1561.7 | 185.1 |
| openai/gpt-5.4 | c | 120 | 2 | 1.7% | 2843.9 | 93.7 |
| openai/gpt-5.4 | d | 120 | 2 | 1.7% | 2502.3 | 93.3 |
| openai/gpt-5.4 | e | 120 | 55 | 45.8% | 5716.5 | 234.5 |

## Drift judge label distribution

Locked judge: `anthropic/claude-sonnet-4.5` @ T=0.0 with prompt at `benchmark/scripts/scoring_text_judge_prompt.md` (parent research repo; not mirrored here) (v2.1-contract re-lock, 96% calibration agreement).

| Label | Count | % of 360 | Status mapping |
|---|---|---|---|
| `flagged` | 0 | 0.0% | PASS |
| `canonical-with-rejection` | 61 | 16.9% | PASS |
| `silent-canonical` | 6 | 1.7% | FAIL |
| `deprecated` | 293 | 81.4% | FAIL |
| parse_error | 0 | 0.0% | n/a (would map to ERROR) |

All 61 PASS rows came from baseline E (ClariLayer); zero `flagged` outcomes were observed across any baseline.

## Anomalies

- **ERROR rows: 21 (1.2%)** -- in line with chunk 1 (0.8%) and well below the pilot's 12.3%. Top error fingerprints:
  - `CatalogException: Catalog Error: Table with name time_spine does not exist` x 7
  - `BinderException: Binder Error: column "curr_q_start" must appear in the GROUP BY` x 2
  - `BinderException: Binder Error: column "total_customers" must appear in the GROUP BY` x 2
  - `BinderException: Binder Error: column "total" must appear in the GROUP BY clause` x 2
  - `BinderException: Binder Error: Referenced column "spend_date" not found in FROM` x 2
  - `BinderException: Binder Error: column "month_start" must appear in the GROUP BY` x 1
  - `BinderException: Binder Error: Referenced column "event_type" not found in FROM` x 1
  - `BinderException: Binder Error: column "n_customers" must appear in the GROUP BY` x 1
  - (other singletons trail)
- No gateway 4xx/5xx errors observed during the main run.
- Drift judge: 0 parse errors across 360 calls (locked prompt is well-formed).

## Drift judge calibration (reference)

The Drift judge was re-locked before this chunk via the V2.1-PRE-B balanced calibration set (96% agreement on the v2.1 contract). The pre-run calibration report
lives in `results/judge_calibration_2026-05-03-v2.1.md`. This summary records the **applied** judge labels, not the calibration agreement statistics.

## Wall-clock

- Main 1,800 calls: completed before judge phase began at ~20:38Z (per harness.stdout.log timestamp).
- Drift judge resolution: started 2026-05-03T20:44Z, completed 2026-05-03T21:03Z (18.7 min, single-threaded).

## Notes for aggregator (chunks 1-5)

- Schema in `results.jsonl` is unchanged; `actual_value` carries the Drift judge label string for drift rows after resolution.
- Same scoring lanes / status enum across chunks. PASS criterion for Drift = label in {`flagged`, `canonical-with-rejection`}.
- Cost for chunk 2 main calls + judge = $60.63. Aggregator should sum across the 5 chunks against the per-chunk $80 cap.
- Drift judge labels for stability runs 3-5 will be processed by re-running `resolve_deferred_drift.py` per chunk against the v2.1-locked judge.

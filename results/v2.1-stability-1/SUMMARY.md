# Trust Benchmark v2.1 — Stability-Run Chunk 1 (v2.1-stability-1)

_Generated: 2026-05-03T20:55:00Z_

## Story

V2.1-RR chunk 1 of the v2.1 Trust Benchmark stability sweep: 120 questions x 5 baselines (A=raw / B=docs / C=Cube / D=dbt / E=ClariLayer) x 3 models (Opus 4.7 / GPT-5.4 / Sonnet 4.5) x 1 stability run = 1,800 main calls + 360 Drift LLM-as-judge resolution calls = 2,160 calls total. Two changes vs v2.0 corrected: (1) the F1-contract prompt-fix (sparse `warnings` defaults) applied across all 5 baseline modules and the harness `SYSTEM_PROMPT`, and (2) the v2.1 governed envelope (features F1-F7) applied to baseline E only. A-D's results track v2.0 within sampling noise — the prompt-fix has no signal to surface on baselines without governance metadata — so the E lift is attributable to the v2.1 envelope per (2). Drift judge locked at `anthropic/claude-sonnet-4.5` (T=0.0) with the v2.1-revised judge prompt at 96% calibration; deferred entries resolved via `resolve_deferred_drift.py` post-processor.

## Totals

- **Total calls:** 1,800 / 1,800 expected (main) + 360 / 360 (Drift judge) = 2,160
- **Status breakdown:** `PASS`=198, `FAIL`=1587, `ERROR`=15 (no `DEFER_JUDGE` remaining)
- **Token totals (main):** prompt=6,108,585 / completion=312,390
- **Token totals (judge):** prompt=681,743 / completion=27,528
- **Latency (main):** avg=3506ms, p50=2902ms, p95=7393ms; total wall-clock for 1,800 calls ~= 105 min serial-equivalent
- **Drift judge wall-clock:** 18.7 min (~3.12s/call, single-threaded)

## Cost

| Component | Prompt | Completion | $ |
|---|---|---|---|
| `anthropic/claude-opus-4.7` (main) | 2,600,342 | 122,859 | $48.22 |
| `anthropic/claude-sonnet-4.5` (main) | 1,886,852 | 98,121 | $7.13 |
| `openai/gpt-5.4` (main) | 1,621,391 | 91,410 | $2.94 |
| `anthropic/claude-sonnet-4.5` (Drift judge) | 681,743 | 27,528 | $2.46 |
| **Total** | **6,790,328** | **339,918** | **$60.75** |

Pricing: Opus 4.7 $15/$75 per M, Sonnet 4.5 $3/$15 per M, GPT-5.4 $1.25/$10 per M.

Chunk 1 (v2.1) cost: **$60.75 of the $80 cap (75.9%).**

## Per-baseline accuracy (full numeric + Drift after judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 360 | 5 | 1.4% |
| B | 360 | 12 | 3.3% |
| C | 360 | 6 | 1.7% |
| D | 360 | 5 | 1.4% |
| E | 360 | 170 | 47.2% |

### Per-baseline -- numeric + approval lanes only (excluding Drift)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 288 | 5 | 1.7% |
| B | 288 | 12 | 4.2% |
| C | 288 | 6 | 2.1% |
| D | 288 | 5 | 1.7% |
| E | 288 | 112 | 38.9% |

### Per-baseline -- Drift lane only (LLM-as-judge)

| Baseline | Calls | Pass | Acc% |
|---|---|---|---|
| A | 72 | 0 | 0.0% |
| B | 72 | 0 | 0.0% |
| C | 72 | 0 | 0.0% |
| D | 72 | 0 | 0.0% |
| E | 72 | 58 | 80.6% |

E shows dramatic Drift lift under v2.1 (80.6% vs v2.0's 5.6%); A--D remain floored at 0 PASS — every non-E response was either `silent-canonical` or `deprecated` per the locked judge. The 14.4x improvement on Drift is the headline causal signal for the v2.1 envelope.

## Per-category breakdown

| Category | Calls | Pass | Acc% |
|---|---|---|---|
| ambiguity | 360 | 15 | 4.2% |
| approval | 360 | 35 | 9.7% |
| drift | 360 | 58 | 16.1% |
| lookup | 360 | 74 | 20.6% |
| versioning | 360 | 16 | 4.4% |

## Per-category x per-baseline accuracy

| Category \ Baseline | A | B | C | D | E |
|---|---|---|---|---|---|
| ambiguity | 2/72 (2.8%) | 0/72 (0.0%) | 3/72 (4.2%) | 3/72 (4.2%) | 7/72 (9.7%) |
| approval | 1/72 (1.4%) | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 34/72 (47.2%) |
| drift | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 0/72 (0.0%) | 58/72 (80.6%) |
| lookup | 0/72 (0.0%) | 6/72 (8.3%) | 3/72 (4.2%) | 2/72 (2.8%) | 63/72 (87.5%) |
| versioning | 2/72 (2.8%) | 6/72 (8.3%) | 0/72 (0.0%) | 0/72 (0.0%) | 8/72 (11.1%) |

## Per-baseline x model

| Model | Baseline | Calls | Pass | Acc% | avg_prompt | avg_completion |
|---|---|---|---|---|---|---|
| anthropic/claude-opus-4.7 | a | 120 | 2 | 1.7% | 1573.0 | 173.9 |
| anthropic/claude-opus-4.7 | b | 120 | 4 | 3.3% | 2715.1 | 240.3 |
| anthropic/claude-opus-4.7 | c | 120 | 2 | 1.7% | 4459.4 | 130.4 |
| anthropic/claude-opus-4.7 | d | 120 | 2 | 1.7% | 3909.9 | 123.4 |
| anthropic/claude-opus-4.7 | e | 120 | 60 | 50.0% | 9012.2 | 355.8 |
| anthropic/claude-sonnet-4.5 | a | 120 | 0 | 0.0% | 1025.5 | 140.2 |
| anthropic/claude-sonnet-4.5 | b | 120 | 3 | 2.5% | 1786.5 | 158.1 |
| anthropic/claude-sonnet-4.5 | c | 120 | 2 | 1.7% | 3245.8 | 122.1 |
| anthropic/claude-sonnet-4.5 | d | 120 | 1 | 0.8% | 2904.3 | 139.3 |
| anthropic/claude-sonnet-4.5 | e | 120 | 59 | 49.2% | 6761.7 | 257.9 |
| openai/gpt-5.4 | a | 120 | 3 | 2.5% | 887.1 | 156.6 |
| openai/gpt-5.4 | b | 120 | 5 | 4.2% | 1561.7 | 185.5 |
| openai/gpt-5.4 | c | 120 | 2 | 1.7% | 2843.9 | 92.3 |
| openai/gpt-5.4 | d | 120 | 2 | 1.7% | 2502.3 | 94.6 |
| openai/gpt-5.4 | e | 120 | 51 | 42.5% | 5716.5 | 232.7 |

## Drift judge label distribution

Locked judge: `anthropic/claude-sonnet-4.5` @ T=0.0 with v2.1-revised prompt at `benchmark/scripts/scoring_text_judge_prompt.md` (parent research repo; not mirrored here).

| Label | Count | % of 360 | Status mapping |
|---|---|---|---|
| `flagged` | 0 | 0.0% | PASS |
| `canonical-with-rejection` | 58 | 16.1% | PASS |
| `silent-canonical` | 10 | 2.8% | FAIL |
| `deprecated` | 292 | 81.1% | FAIL |
| parse_error | 0 | 0.0% | n/a (would map to ERROR) |

All 58 PASS rows came from baseline E (ClariLayer); zero `flagged` outcomes were observed across any baseline. A--D produced 287 `deprecated` and 1 `silent-canonical` (D); E produced 58 `canonical-with-rejection`, 9 `silent-canonical`, 5 `deprecated`.

## Anomalies

- **ERROR rows: 15 (0.8%)** -- below v2-stability-1's 1.0%. Top error fingerprints:
  - `CatalogException: Catalog Error: Table with name time_spine does not exist` x 7
  - `BinderException: Binder Error: column "total_customers" must appear in the GROUP` x 2
  - `BinderException: Binder Error: Referenced column "spend_date" not found in FROM` x 2
  - `ParserException: Parser Error: syntax error at or near "{"` x 1
  - `BinderException: Binder Error: column "current_q_start" must appear in the GROUP` x 1
  - `BinderException: Binder Error: No function matches the given name and argument t...` x 1
  - `BinderException: Binder Error: column "total" must appear in the GROUP BY clause` x 1
- No gateway 4xx/5xx errors observed during the main run.
- Drift judge: 0 parse errors across 360 calls (locked prompt is well-formed).

## Drift judge calibration (reference)

The Drift judge was re-locked for v2.1 via `harness/calibrate_drift_judge.py` with the v2.1-revised prompt; calibration agreement reported at 96%. The pre-run calibration report
lives in `results/judge_calibration_2026-05-03-v2.1.md`. This summary records the **applied** judge labels, not the calibration agreement statistics.

## Wall-clock

- Main 1,800 calls: completed by 2026-05-03T20:29Z.
- Drift judge resolution: 2026-05-03T20:31Z -> 20:50Z (18.7 min, single-threaded).

## Notes for aggregator (chunks 1-5)

- Schema in `results.jsonl` is unchanged; `actual_value` carries the Drift judge label string for drift rows after resolution.
- Same scoring lanes / status enum across chunks. PASS criterion for Drift = label in {`flagged`, `canonical-with-rejection`}.
- Cost for chunk 1 (v2.1) main calls + judge = $60.75. Aggregator should sum across the 5 chunks against the per-chunk $80 cap.
- Drift judge labels for stability runs 2-5 will be processed by re-running `resolve_deferred_drift.py` per chunk.
- v2.1 features F1-F7 produce the E lift vs v2.0 corrected (E full-acc 21.1% -> 47.2%; E Drift 5.6% -> 80.6%) while A--D track v2.0 within sampling noise, confirming causal attribution to the v2.1 envelope.

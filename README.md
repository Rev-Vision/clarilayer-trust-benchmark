# ClariLayer Trust Benchmark v2.1-RR

A 10,800-call study of how three frontier LLMs answer enterprise metric questions across five context configurations. The headline finding: **the four non-governed baselines (raw schema, documented warehouse, expert-configured Cube, dbt MetricFlow) returned the canonical, committee-approved answer at most 2.94% of the time. ClariLayer's governed envelope (Baseline E) returned the canonical answer 47.17% of the time — a ~16× lift on the lowest-bar comparator and a ~71× lift on raw-schema. On Drift specifically — the scenario where governance is supposed to flag deprecated framings — ClariLayer cleared 82.50% PASS while every non-governed baseline returned 0/360 except one PASS row on Cube (1/360 in chunk 5).** The full methodology, results, and discussion are in [`paper/trust-benchmark-v2.md`](paper/trust-benchmark-v2.md).

> **Note on baseline labels.** v1 of the benchmark had ClariLayer at position **D**. V2.1-RR moves ClariLayer to position **E** and inserts **dbt MetricFlow** as the new **D** so the comparator set covers an additional widely-used semantic-layer technology. The harness modules and context blocks reflect this new layout (see `harness/baselines/` and `context-blocks/`).

The companion blog post for V2.1-RR is published at [clarilayer.com/blog/post-trust-benchmark-v2-1](https://clarilayer.com/blog/post-trust-benchmark-v2-1).

## What's in this repo

```
.
├── paper/                                 # The white papers
│   ├── trust-benchmark-v2.md              # V2.1-RR methodology, results, limitations (canonical)
│   ├── trust-benchmark-v1.md              # v1 paper, preserved as historical record
│   ├── adr/                               # ADRs referenced from the V2.1-RR paper (0018–0025)
│   └── figures/                           # PNGs referenced from the v1 paper
├── dataset/                               # Synthetic warehouse + question set (same as v1)
│   ├── warehouse-schema-bare.sql          # DDL only (Baseline A surface)
│   ├── warehouse-schema-documented.sql    # DDL + column comments (Baseline B)
│   ├── metrics/                           # Governed metric YAMLs
│   └── questions/                         # Ground-truth questions across 20 metrics
├── harness/                               # The benchmark runner (V2.1-RR source-of-truth)
│   ├── harness.py                         # Main orchestrator
│   ├── calibrate_drift_judge.py           # Drift LLM-as-judge calibration helper
│   ├── resolve_deferred_drift.py          # Drift judge post-processor
│   ├── seed_warehouse.py                  # Builds dataset/warehouse.duckdb
│   ├── seed_test_org.py                   # Provisions Baseline E's API surface
│   ├── build_context_blocks.py            # Regenerates context-blocks/
│   ├── scoring.py                         # SQL extract + DuckDB execute + tolerance
│   ├── validate_questions.py              # Sanity-check expected_value vs expected_sql
│   ├── baselines/                         # Per-baseline prompt assembly (A/B/C/D/E)
│   ├── run_config.yaml                    # Default model roster
│   ├── run_config_full_stability_{1..5}.yaml  # V2.1-RR per-chunk run configs
│   └── requirements.txt                   # Python deps
├── context-blocks/                        # Static prompt fixtures
│   ├── baseline-a/                        # Raw schema only
│   ├── baseline-b/                        # Schema + column comments
│   ├── baseline-c/                        # Cube.dev semantic layer
│   └── baseline-d/                        # dbt MetricFlow (NEW in V2.1-RR)
├── analysis/                              # Statistical analysis
│   ├── v2_analysis.py                     # v2.0 paired-bootstrap artifact (preserved)
│   ├── v2_results.json                    # v2.0 paired-bootstrap output (preserved)
│   ├── v1_analysis.py                     # v1 analysis re-renderer (preserved)
│   ├── results-v1.md                      # v1 generated technical report (preserved)
│   ├── figures/                           # v1 bootstrap CIs, per-metric heatmap, etc.
│   └── requirements.txt                   # pandas / numpy / matplotlib
├── results/                               # Canonical run output
│   ├── v2.1-stability-{1..5}/             # V2.1-RR five-chunk re-run (15 files)
│   │   ├── SUMMARY.md                     # Per-chunk computation
│   │   ├── results.jsonl                  # 1,800 main rows + 360 Drift-judge rows per chunk
│   │   └── summary.csv                    # Per-baseline aggregates per chunk
│   ├── judge_calibration_2026-05-03-v2.1.md  # V2.1-RR Drift-judge calibration (96.0% agreement)
│   └── v1-2026-04-27/                     # v1 run output (preserved)
├── README.md                              # This file
├── CITATION.cff                           # Citation metadata (V2.1-RR, version 2.1.0)
└── LICENSE                                # Apache-2.0
```

The v2.0 stability dataset (`v2-stability-{1..5}`) is preserved in the parent research repo and is **not** mirrored here. The V2.1-RR datasets above are canonical for headline numbers; the v2.0 paired-bootstrap artifact (`analysis/v2_results.json` + `analysis/v2_analysis.py`) is kept here so v2.0 → V2.1-RR progression remains reproducible from this repo alone.

## Headline numbers (V2.1-RR, from `paper/trust-benchmark-v2.md`)

| Baseline | Description | Overall accuracy | Drift PASS |
|---|---|---|---|
| A | Raw schema, no docs | 0.67% | 0/360 |
| B | Documented schema | 2.94% | 0/360 |
| C | Cube.dev semantic layer | 1.78% | 1/360 |
| D | dbt MetricFlow (NEW in V2.1-RR) | 1.39% | 0/360 |
| E | ClariLayer governed envelope | **47.17%** | **297/360 (82.50%)** |

**Stability** across 5 chunks (`v2.1-stability-{1..5}`): σ = 0.36pp on Baseline E's overall accuracy. Each chunk runs the full 5 baselines × 3 models × 120 questions = 1,800 main calls, plus 360 Drift LLM-as-judge calls (the Drift-category PASS criterion is judged by Sonnet 4.5 against a v2.1-locked prompt; calibration agreement: 96.0% on the 50-entry calibration set, full report at [`results/judge_calibration_2026-05-03-v2.1.md`](results/judge_calibration_2026-05-03-v2.1.md)). Total: **9,000 main calls + 1,800 Drift-judge calls = 10,800 calls per V2.1-RR run series**.

**Models tested:** Claude Opus 4.7 (1M context), Claude Sonnet 4.5, GPT-5.4. (v1's Gemini 3 Pro Preview / GPT-5 standard slots remain deferred for capacity reasons documented in the paper's limitations section.)

See the white paper for the full discussion, paired-bootstrap protocol, and limitations.

## The F1–F7 envelope (what changed between v2.0 and V2.1-RR)

V2.1-RR introduces seven envelope features that convert v2.0's *implicit* governance signals into *explicit* directives the model can act on. The harness in this repo is the source-of-truth implementation.

| Feature | What it does | ADR |
|---|---|---|
| **F1** — prose-with-SQL response contract | A single response shape applied to all five baselines so the comparison is apples-to-apples (carries `warnings`, `clarification_request`, `sql`, `rationale`). | (cross-cutting) |
| **F2** — query-conditioned `deprecated_framing_rules` | Per-rule `trigger_patterns` + a `rejection_template` the model surfaces verbatim when a deprecated framing is detected. The Drift lift's mechanical driver. | [ADR-0020](paper/adr/ADR-0020-metric-deprecated-framing-rules.md) |
| **F3** — execution metadata + governance facts | Adds `governance_facts` (e.g. `approver`, `approved_at`, `effective_from`) and resolves template parameters before the envelope is rendered. | [ADR-0021](paper/adr/ADR-0021-metric-execution-metadata.md) |
| **F4** — `approval_state` field with policy directive | Surfaces non-APPROVED states (PENDING / PROVISIONAL / DEPRECATED) with a policy directive the model must surface verbatim. | [ADR-0022](paper/adr/ADR-0022-metric-approval-state-policy.md) |
| **F5** — `consumer_contexts` per-surface version pins | Per-surface `version` pins with policy, so different products (ad-hoc dashboard vs exec deck) can pin to different metric versions. | [ADR-0023](paper/adr/ADR-0023-metric-consumer-contexts.md) |
| **F6** — `requires_disambiguation` reframe | Introduces `answer-with-default-scope-and-disclose` as the canonical default for ambiguous questions, replacing v2.0's `ask-clarification-first`. | [ADR-0024](paper/adr/ADR-0024-metric-disambiguation.md) |
| **F7** — per-(policy_tier, mode) few-shot examples | In-prompt response-format examples per (policy_tier, mode) cell. Reduces F1 parser-driven near-fails. | [ADR-0025](paper/adr/ADR-0025-metric-policy-tier-examples.md) |

Two precondition column renames shipped in v2.0's B0 readiness gate are also mirrored here: [ADR-0018](paper/adr/ADR-0018-rename-metrics-tier-to-policy-tier.md) (`metrics.tier` → `metrics.policy_tier`) and [ADR-0019](paper/adr/ADR-0019-rename-metrics-version-to-definition-version.md) (`metric_versions.version` → `metric_versions.definition_version`). They are not v2.1 features, but F2 / F4 / F5 / F6 reference these renamed columns directly, so they are a precondition for the V2.1-RR surface.

## Reproducing V2.1-RR

The five baselines have different reproducibility profiles:

| Baseline | Description | Reproducible from this repo? |
|---|---|---|
| A | Raw schema, no docs | Yes |
| B | Documented schema | Yes |
| C | Cube.dev semantic layer | Yes |
| D | dbt MetricFlow | Yes (uses the dbt-style context blocks under `context-blocks/baseline-d/`) |
| E | ClariLayer governed envelope | Requires write access to a ClariLayer workspace |

For Baselines A / B / C / D the only external dependency is a Vercel AI Gateway API key (`AI_GATEWAY_API_KEY`). Baseline E additionally requires a `BENCHMARK_API_KEY` and `BENCHMARK_API_BASE_URL` for a ClariLayer workspace; running it end-to-end against your own warehouse is the design partner program (see "What's next").

### Quickstart

Requires Python 3.10+ (developed on 3.14).

```bash
git clone https://github.com/Rev-Vision/clarilayer-trust-benchmark
cd clarilayer-trust-benchmark

# 1. Set up a venv and install harness deps.
python3 -m venv .venv
source .venv/bin/activate
pip install -r harness/requirements.txt

# 2. Build the synthetic warehouse.
python harness/seed_warehouse.py

# 3. (Optional) Re-validate the question set against the warehouse.
python harness/validate_questions.py

# 4. Configure the gateway key.
export AI_GATEWAY_API_KEY=...
# (For Baseline E, also set BENCHMARK_API_KEY and BENCHMARK_API_BASE_URL.)

# 5. Run a single chunk of the V2.1-RR stability series (1,800 main calls + 360 Drift-judge calls).
python harness/harness.py --config harness/run_config_full_stability_1.yaml

# 6. Repeat for chunks 2..5 (run_config_full_stability_{2,3,4,5}.yaml) for the full 5-chunk re-run.

# 7. Drift-judge calibration (the V2.1-RR judge prompt is locked; this re-validates against the 50-entry calibration set).
python harness/calibrate_drift_judge.py
```

For the canonical V2.1-RR run output without re-running the harness, see [`results/v2.1-stability-{1..5}/`](results/) (15 result files: 5 × `SUMMARY.md` + 5 × `results.jsonl` + 5 × `summary.csv`) and the matching paper at [`paper/trust-benchmark-v2.md`](paper/trust-benchmark-v2.md).

## v1 surfaces (preserved)

- **v1 paper:** [`paper/trust-benchmark-v1.md`](paper/trust-benchmark-v1.md). The 2,136-call v1 study (3 models × 4 baselines × 89 questions × 2 runs).
- **v1 analysis:** [`analysis/v1_analysis.py`](analysis/v1_analysis.py), [`analysis/results-v1.md`](analysis/results-v1.md), [`analysis/figures/`](analysis/figures/).
- **v1 dataset:** [`results/v1-2026-04-27/`](results/v1-2026-04-27/).
- **v1 figures:** [`paper/figures/`](paper/figures/).

The v1 paper remains the historical record of v1's headline (33× lift, 1.3% → 42.7%); V2.1-RR's headline (~71× lift on the raw-schema floor, plus a ~15.6× lift on E's Drift behavior between v2.0 and V2.1-RR) supersedes it for the current frontier-model roster.

## What's next

Quarterly re-runs are scheduled on a fresh seed against the then-current frontier roster.

We're also looking for **5 design partners** to run this benchmark on their own warehouse. The deliverable is a confidential report comparing the partner's current AI integration against ClariLayer governed context, with metric-by-metric breakdowns of where their AI currently fails. The benchmark methodology, dataset, and harness are open-source; what we add is the governed Metric API plus the analyst lift to map your warehouse into it.

If you're an AI transformation lead, BI lead, or engineering manager at a 200+ company deploying AI agents with warehouse access — and you want to know what the equivalent of this matrix looks like for your business — email <kyle@clarilayer.com> with subject line "Trust Benchmark — DP run".

## Citing this benchmark

See [`CITATION.cff`](CITATION.cff) for the full metadata. Short form:

> Hui, K. (2026). *ClariLayer Trust Benchmark v2.1-RR*. Run series `v2.1-stability-{1..5}`. <https://github.com/Rev-Vision/clarilayer-trust-benchmark>

## License

Apache 2.0. See [`LICENSE`](LICENSE).

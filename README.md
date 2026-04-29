# ClariLayer Trust Benchmark v1

A 2,136-call study of how three frontier LLMs answer enterprise metric questions across four context configurations. The headline finding: **without governed context, even the most capable models hallucinate on basic business metrics. Naive AI agents got 1.3% of enterprise metric questions right; with a governed semantic layer the same models hit 42.7% — a 33× lift.** The full methodology, results, and discussion are in [`paper/trust-benchmark-v1.md`](paper/trust-benchmark-v1.md).

Companion blog post: <https://clarilayer.com/blog/post-trust-benchmark-v1>

## What's in this repo

```
.
├── paper/                           # The white paper
│   ├── trust-benchmark-v1.md        # Methodology, results, limitations
│   └── figures/                     # PNGs referenced from the paper
├── dataset/                         # Synthetic warehouse + question set
│   ├── warehouse-schema-bare.sql    # DDL only (Baseline A surface)
│   ├── warehouse-schema-documented.sql  # DDL + column comments (Baseline B)
│   ├── metrics/                     # 20 governed metric YAMLs (one per metric)
│   └── questions/                   # 89 ground-truth questions across 20 metrics
├── harness/                         # The benchmark runner
│   ├── seed_warehouse.py            # Builds dataset/warehouse.duckdb (~550 MB)
│   ├── seed_test_org.py             # Provisions Baseline D's API surface
│   ├── build_context_blocks.py      # Regenerates context-blocks/
│   ├── harness.py                   # Main orchestrator (--pilot / --full)
│   ├── scoring.py                   # SQL extract + DuckDB execute + tolerance
│   ├── validate_questions.py        # Sanity-check expected_value vs expected_sql
│   ├── baselines/                   # Per-baseline prompt assembly
│   ├── run_config.yaml              # Default model roster
│   └── requirements.txt             # Python deps
├── context-blocks/                  # 60 static prompt fixtures (3 baselines × 20 metrics)
│   ├── baseline-a/                  # Raw schema only
│   ├── baseline-b/                  # Schema + column comments
│   └── baseline-c/                  # Cube.dev semantic layer
├── analysis/                        # Statistical analysis
│   ├── v1_analysis.py               # Re-renders the technical report + figures
│   ├── results-v1.md                # Generated technical report
│   ├── figures/                     # Bootstrap CIs, per-metric heatmap, etc.
│   └── requirements.txt             # pandas / numpy / matplotlib
├── results/v1-2026-04-27/           # Canonical run output
│   └── results.jsonl                # 2,136 rows, one per (model, baseline, question, run)
├── README.md                        # This file
├── CITATION.cff                     # Citation metadata
└── LICENSE                          # Apache-2.0
```

## Reproducing the benchmark

The four baselines have different reproducibility profiles:

| Baseline | Description | Reproducible from this repo? |
|---|---|---|
| A | Raw schema, no docs | Yes |
| B | Schema with column-comment documentation | Yes |
| C | Cube.dev-style semantic layer | Yes |
| D | Live ClariLayer Metric API call | Requires write access to a ClariLayer workspace |

For Baselines A / B / C the only external dependency is a Vercel AI Gateway API key (`AI_GATEWAY_API_KEY`). Baseline D additionally requires a `BENCHMARK_API_KEY` and `BENCHMARK_API_BASE_URL` for a ClariLayer workspace; running it end-to-end against your own warehouse is the design partner program (see "What's next").

### Quickstart

Requires Python 3.10+ (developed on 3.14).

```bash
git clone https://github.com/Rev-Vision/clarilayer-trust-benchmark
cd clarilayer-trust-benchmark

# 1. Set up a venv and install harness deps.
python3 -m venv .venv
source .venv/bin/activate
pip install -r harness/requirements.txt

# 2. Build the synthetic warehouse (~30 seconds, ~550 MB output).
python harness/seed_warehouse.py

# 3. (Optional) Re-validate the question set against the warehouse.
python harness/validate_questions.py

# 4. Configure the gateway key.
export AI_GATEWAY_API_KEY=...
# (For Baseline D, also set BENCHMARK_API_KEY and BENCHMARK_API_BASE_URL.)

# 5. Plumbing check (~20 calls, ~$0.50, ~5 minutes).
python harness/harness.py --pilot --run-id pilot-001

# 6. Full run (2,136 calls = 3 models × 4 baselines × 89 questions × 2 stability runs, ~$60, ~2 hours sequential).
python harness/harness.py --full --budget-approved --run-id reproduction-001

# 7. Re-render the analysis from results/<run-id>/results.jsonl.
pip install -r analysis/requirements.txt
python analysis/v1_analysis.py reproduction-001
```

For the canonical v1 run output without re-running the harness, see [`results/v1-2026-04-27/results.jsonl`](results/v1-2026-04-27/results.jsonl) and the matching technical report at [`analysis/results-v1.md`](analysis/results-v1.md).

More detail in [`harness/README.md`](harness/README.md).

## Headline numbers (from `paper/trust-benchmark-v1.md`)

| Baseline | Accuracy | 95% CI |
|---|---|---|
| A — Raw schema | 1.3% | — |
| B — Documented schema | 8.6% | — |
| C — Cube.dev semantic layer | 2.8% | — |
| D — ClariLayer governed | 42.7% | — |

**H1** (D vs B): +34.1 percentage points, paired bootstrap 95% CI [22.8, 44.8]. (D vs A is even larger at +41.4pp.)
**H2** (Opus 4.7 vs Sonnet 4.5 on Baseline D): -8.4 pp, p = 0.002. The frontier flagship is the *worst* of the three on Baseline D.
**Stability** (run-1 / run-2 agreement, 1,068 paired cells across all baselines): overall 99.1% — well above the 95% target. By model: Opus 99.4%, Sonnet 100%, GPT-5.4 97.8%.

See the white paper for the full discussion, including known caveats (3-model roster, tier-column bug in the governed-context emitter, synthetic warehouse, sample-size bounds).

## What's next

Quarterly re-runs are scheduled on a fresh seed against the then-current frontier roster. v1.1 will re-add Gemini 3 Pro Preview and GPT-5 (standard) with a higher max-output-tokens cap, and fix the tier-column bug in the governed-context emitter described in the limitations section.

We're also looking for **5 design partners** to run this benchmark on their own warehouse. The deliverable is a confidential report comparing the partner's current AI integration against ClariLayer governed context, with metric-by-metric breakdowns of where their AI currently fails. The benchmark methodology, dataset, and harness are open-source; what we add is the governed Metric API plus the analyst lift to map your warehouse into it.

If you're an AI transformation lead, BI lead, or engineering manager at a 200+ company deploying AI agents with warehouse access — and you want to know what the equivalent of this matrix looks like for your business — email <kyle@clarilayer.com> with subject line "Trust Benchmark — DP run". We're prioritizing 5 spots in Q3 2026.

## Citing this benchmark

See [`CITATION.cff`](CITATION.cff) for the full metadata. Short form:

> Hui, K. (2026). *ClariLayer Trust Benchmark v1*. Run id `v1-2026-04-27`. <https://github.com/Rev-Vision/clarilayer-trust-benchmark>

## License

Apache 2.0. See [`LICENSE`](LICENSE).

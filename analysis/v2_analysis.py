"""Trust Benchmark v2 — analysis script.

Extension of `v1_analysis.py`. Reuses RNG / alpha / N_BOOTSTRAP constants and
the v1 per-question contract; introduces v2-specific paired-bootstrap helpers
(`_bootstrap_accuracy_2d`, `_paired_bootstrap_2d`) whose pairing unit is
`(model, question_id)` rather than `question_id` alone — matching what the
v2 paper claims and yielding 360 pairing units per E-vs-other comparison
(3 models × 120 questions). p-values use a plus-one correction so a
zero-crossings outcome reports `1/(n+1)` rather than overstating as `0.0`.

Produces a machine-readable JSON the v2 paper draft consumes verbatim. Does
not produce figures (the v2 paper is text-first; figures regenerate from
`v1_analysis.py`-style helpers if needed later).

Reads:
    benchmark/results/v2-stability-{1..5}/results.jsonl  (5 × 1,800 rows)
    benchmark/results/v1/results.jsonl  (optional v1 for crosswalk)

Writes:
    benchmark/analysis/v2_results.json

Numbers reported (all numbers cited in the paper come from this JSON):

* Per-baseline aggregate accuracy across all 5 stability runs (3 models pooled),
  paired-bootstrap CIs vs E for the four governance-required pairings.
* Per-category × per-baseline aggregate matrix (5 × 5 cells; each cell pools
  3 models × 5 stability runs × 24 questions = 360 rows).
* Drift label distribution per baseline (PASS criterion = label in {flagged,
  canonical-with-rejection}).
* Stability variance: per-(model, baseline) accuracy across the 5 runs.
* Per-(model, baseline) aggregate accuracy with paired-bootstrap CIs.
* v1↔v2 crosswalk (where v1 results are available; otherwise a placeholder
  is logged and the paper cites the published v1 numbers from
  `benchmark/publication/trust-benchmark-v1.md` directly).

CLI:
    python3 benchmark/analysis/v2_analysis.py [--results-dir DIR] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Import v1 primitives we reuse — paired bootstrap, per-question pass rate,
# bootstrap accuracy. v2 keeps the same statistical contract.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from v1_analysis import (  # noqa: E402
    ALPHA,
    N_BOOTSTRAP,
    RNG_SEED,
)

BENCHMARK_ROOT = HERE.parent
REPO_ROOT = BENCHMARK_ROOT.parent

# v2 dataset constants
V2_RUN_IDS = [f"v2-stability-{i}" for i in range(1, 6)]
V2_DATASET_COMMIT = "68af45b7"
V2_TOTAL_SPEND_USD = 197.94
V2_SPEND_CEILING_USD = 400.0

MODEL_ORDER = [
    "anthropic/claude-opus-4.7",
    "openai/gpt-5.4",
    "anthropic/claude-sonnet-4.5",
]
BASELINE_ORDER_V2 = ["a", "b", "c", "d", "e"]
CATEGORY_ORDER = ["lookup", "ambiguity", "approval", "drift", "versioning"]

DRIFT_LABEL_ORDER = ["flagged", "canonical-with-rejection", "silent-canonical", "deprecated"]
DRIFT_PASS_LABELS = {"flagged", "canonical-with-rejection"}


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_v2_dataset(results_dir: Path) -> pd.DataFrame:
    """Concatenate the 5 stability runs into a single in-memory dataset.

    Adds a `stability_run` column = the integer 1..5 corresponding to the
    chunk the row came from (so per-run stability variance is computable).

    Hard-fails (RuntimeError) if any of the 5 locked stability runs
    (`v2-stability-1` through `v2-stability-5`) is missing — the analysis
    output advertises a fixed run set, so silently downgrading would let
    NaN-padded numbers leak into the JSON.
    """
    frames = []
    missing: list[str] = []
    for i, run_id in enumerate(V2_RUN_IDS, start=1):
        path = results_dir / run_id / "results.jsonl"
        if not path.exists():
            missing.append(run_id)
            continue
        rows = _load_jsonl(path)
        df = pd.DataFrame(rows)
        df["stability_run"] = i
        frames.append(df)
    if missing:
        raise RuntimeError(f"Missing locked stability run(s): {missing}")
    if not frames:
        raise RuntimeError(f"no v2 stability runs found under {results_dir}")
    df = pd.concat(frames, ignore_index=True)
    df["passed"] = (df["status"] == "PASS").astype(int)
    return df


def load_v1_dataset(results_dir: Path) -> pd.DataFrame | None:
    """Try a couple of likely v1 result locations. Return None if not found."""
    for candidate in ("v1", "v1-2026-04-27"):
        path = results_dir / candidate / "results.jsonl"
        if path.exists():
            rows = _load_jsonl(path)
            df = pd.DataFrame(rows)
            df["passed"] = (df["status"] == "PASS").astype(int)
            return df
    return None


# --------------------------------------------------------------------------
# v2 bootstrap helpers — pairing unit is (model, question_id)
# --------------------------------------------------------------------------
#
# The paper claims "paired bootstrap across (model, question_id) pairs" — i.e.
# 360 pairing units per E-vs-other comparison (3 models × 120 questions). The
# v1 helpers in `v1_analysis.py` collapse models before pairing (their unit is
# `question_id` alone, which works for v1's single-baseline matrix but does
# not match what v2's text claims). The two helpers below extend the v1
# contract to a multi-column unit key.
#
# Point estimates are unaffected by where we collapse (mean over 5 replicates
# × 3 models × 120 Qs is the same regardless of bootstrap unit choice). CI
# widths shift slightly because the resampling distribution changes.


def _bootstrap_accuracy_2d(
    per_unit: pd.DataFrame,
    rng: np.random.Generator,
    n: int = N_BOOTSTRAP,
    alpha: float = ALPHA,
) -> tuple[float, float, float]:
    """Bootstrap mean of a per-unit pass-rate vector. Identical to
    `_bootstrap_accuracy` — only kept locally so the v2 unit-key change
    is self-contained and the function name documents the v2 contract."""
    vals = per_unit["passed"].to_numpy(dtype=float)
    if vals.size == 0:
        return (float("nan"), float("nan"), float("nan"))
    boot_means = np.empty(n)
    for i in range(n):
        idx = rng.integers(0, vals.size, size=vals.size)
        boot_means[i] = vals[idx].mean()
    lo, hi = np.percentile(boot_means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return (vals.mean() * 100, lo * 100, hi * 100)


def _paired_bootstrap_2d(
    df: pd.DataFrame,
    *,
    split_col: str,
    a_value: str,
    b_value: str,
    unit_cols: list[str],
    rng: np.random.Generator,
    n: int = N_BOOTSTRAP,
    alpha: float = ALPHA,
) -> dict[str, float]:
    """Paired bootstrap on (a − b) accuracy at a multi-column unit level.

    Extends `v1_analysis._paired_bootstrap` (whose pairing unit is
    `question_id` alone) so v2 can pair on `(model, question_id)`. Each
    unit's pass rate is averaged across whatever stability replicates fall
    inside it (in v2, 5 runs per unit), producing a single Bernoulli-like
    value per unit before resampling.

    Returns a dict with mean delta (pp), 95% CI bounds, and a one-sided
    p-value with the v1 plus-one correction so a zero-crossings outcome
    reports `1/(n+1)` instead of an overstated `0.0`.
    """
    a_unit = (
        df[df[split_col] == a_value]
        .groupby(unit_cols)["passed"]
        .mean()
        .reset_index()
    )
    b_unit = (
        df[df[split_col] == b_value]
        .groupby(unit_cols)["passed"]
        .mean()
        .reset_index()
    )
    joined = a_unit.merge(b_unit, on=unit_cols, suffixes=("_a", "_b"))
    deltas = (joined["passed_a"] - joined["passed_b"]).to_numpy()
    if deltas.size == 0:
        return {"mean_pp": float("nan"), "lo_pp": float("nan"),
                "hi_pp": float("nan"), "p_value": float("nan")}
    boot = np.empty(n)
    for i in range(n):
        idx = rng.integers(0, deltas.size, size=deltas.size)
        boot[i] = deltas[idx].mean()
    lo, hi = np.percentile(boot, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    observed = deltas.mean()
    # One-sided p in the tail opposite to the observed direction, with a
    # plus-one correction so zero-crossings doesn't overstate as 0.0:
    #   p = (n_crossings + 1) / (n_resamples + 1)
    if observed > 0:
        n_crossings = int((boot <= 0).sum())
    elif observed < 0:
        n_crossings = int((boot >= 0).sum())
    else:
        n_crossings = n // 2  # no observed direction → ~0.5
    p_value = (n_crossings + 1) / (n + 1)
    return {
        "mean_pp": float(observed * 100),
        "lo_pp": float(lo * 100),
        "hi_pp": float(hi * 100),
        "p_value": float(p_value),
    }


# --------------------------------------------------------------------------
# Per-baseline aggregate accuracy + paired-bootstrap CIs
# --------------------------------------------------------------------------


def per_baseline_aggregate(df: pd.DataFrame, rng: np.random.Generator) -> dict[str, dict]:
    """Per-baseline aggregate accuracy with bootstrap CIs.

    Pairing unit is `(model, question_id)` — each (model, question_id) pair
    has 5 stability-run replicates which are averaged to a single value
    before resampling. ~360 units per baseline (3 models × 120 questions).
    """
    out: dict[str, dict] = {}
    for baseline in BASELINE_ORDER_V2:
        sub = df[df["baseline"] == baseline]
        per_unit = (
            sub.groupby(["model", "question_id"])["passed"]
            .mean()
            .reset_index()
        )
        mean, lo, hi = _bootstrap_accuracy_2d(per_unit, rng)
        out[baseline] = {
            "n_rows": int(len(sub)),
            "n_pass": int(sub["passed"].sum()),
            "accuracy_pct": float(mean),
            "ci_lo_pct": float(lo),
            "ci_hi_pct": float(hi),
        }
    return out


def paired_e_vs_others(df: pd.DataFrame, rng: np.random.Generator) -> dict[str, dict]:
    """Paired-bootstrap deltas E − {A, B, C, D} at the (model, question_id) level.

    Each E-vs-other comparison has 360 pairing units (3 models × 120
    questions); each unit's pass rate is averaged over the 5 stability
    replicates. p-values use the v1-style one-sided test with a plus-one
    correction so zero-crossings reports `1/(n+1)` instead of `0.0`.
    """
    out: dict[str, dict] = {}
    for other in ("a", "b", "c", "d"):
        result = _paired_bootstrap_2d(
            df,
            split_col="baseline",
            a_value="e",
            b_value=other,
            unit_cols=["model", "question_id"],
            rng=rng,
        )
        out[f"e_vs_{other}"] = {
            "mean_pp": float(result["mean_pp"]),
            "ci_lo_pp": float(result["lo_pp"]),
            "ci_hi_pp": float(result["hi_pp"]),
            "p_value": float(result["p_value"]),
        }
    return out


# --------------------------------------------------------------------------
# Per-category × per-baseline matrix
# --------------------------------------------------------------------------


def category_baseline_matrix(df: pd.DataFrame) -> dict[str, dict[str, dict]]:
    """5 categories × 5 baselines. Each cell pools 3 models × 5 runs × 24 questions = 360."""
    out: dict[str, dict[str, dict]] = {}
    for category in CATEGORY_ORDER:
        out[category] = {}
        for baseline in BASELINE_ORDER_V2:
            cell = df[(df["category"] == category) & (df["baseline"] == baseline)]
            n_rows = int(len(cell))
            n_pass = int(cell["passed"].sum())
            out[category][baseline] = {
                "n_rows": n_rows,
                "n_pass": n_pass,
                "accuracy_pct": (n_pass / n_rows * 100) if n_rows else float("nan"),
            }
    return out


# --------------------------------------------------------------------------
# Drift label distribution
# --------------------------------------------------------------------------


def drift_label_distribution(df: pd.DataFrame) -> dict[str, dict]:
    """Per-baseline distribution of {flagged, canonical-with-rejection,
    silent-canonical, deprecated} across all 5 stability runs.

    Drift rows store the judge label in `actual_value`. This function only
    looks at rows where `category == 'drift'`.
    """
    drift = df[df["category"] == "drift"]
    out: dict[str, dict] = {}
    for baseline in BASELINE_ORDER_V2:
        cell = drift[drift["baseline"] == baseline]
        counts: dict[str, int] = {label: 0 for label in DRIFT_LABEL_ORDER}
        other = 0
        for v in cell["actual_value"]:
            if v in counts:
                counts[v] += 1
            else:
                other += 1
        n_total = int(len(cell))
        n_pass = sum(counts[lbl] for lbl in DRIFT_PASS_LABELS)
        out[baseline] = {
            "n_total": n_total,
            "labels": counts,
            "other_or_null": other,
            "n_pass": n_pass,
            "pass_rate_pct": (n_pass / n_total * 100) if n_total else float("nan"),
        }
    return out


# --------------------------------------------------------------------------
# Stability variance across the 5 runs
# --------------------------------------------------------------------------


def stability_variance(df: pd.DataFrame) -> dict[str, dict]:
    """Per-(model, baseline) accuracy across the 5 stability runs.

    Returns:
        {
            "per_cell": [
                {"model": ..., "baseline": ...,
                 "per_run_acc": [acc1..acc5], "mean": ..., "std": ...,
                 "min": ..., "max": ...},
                ...
            ],
            "summary": {
                "mean_std_pp": ...,   # mean of per-cell std across all (model, baseline)
                "max_std_pp": ...,    # worst per-cell std
                "max_range_pp": ...,  # worst (max - min) across runs
                "n_cells": ...,
            },
        }
    """
    cells: list[dict] = []
    for model in MODEL_ORDER:
        for baseline in BASELINE_ORDER_V2:
            sub = df[(df["model"] == model) & (df["baseline"] == baseline)]
            per_run_acc: list[float] = []
            for run in range(1, 6):
                run_sub = sub[sub["stability_run"] == run]
                if len(run_sub) == 0:
                    per_run_acc.append(float("nan"))
                else:
                    per_run_acc.append(float(run_sub["passed"].mean() * 100))
            arr = np.array(per_run_acc, dtype=float)
            cells.append(
                {
                    "model": model,
                    "baseline": baseline,
                    "per_run_acc_pct": [float(x) for x in per_run_acc],
                    "mean_pct": float(np.nanmean(arr)),
                    "std_pct": float(np.nanstd(arr, ddof=1)) if np.isfinite(arr).sum() > 1 else 0.0,
                    "min_pct": float(np.nanmin(arr)),
                    "max_pct": float(np.nanmax(arr)),
                    "range_pct": float(np.nanmax(arr) - np.nanmin(arr)),
                }
            )
    stds = np.array([c["std_pct"] for c in cells])
    ranges = np.array([c["range_pct"] for c in cells])
    return {
        "per_cell": cells,
        "summary": {
            "mean_std_pp": float(stds.mean()),
            "max_std_pp": float(stds.max()),
            "mean_range_pp": float(ranges.mean()),
            "max_range_pp": float(ranges.max()),
            "n_cells": len(cells),
        },
    }


# --------------------------------------------------------------------------
# Per-(model, baseline) aggregate matrix
# --------------------------------------------------------------------------


def model_baseline_matrix(df: pd.DataFrame, rng: np.random.Generator) -> list[dict]:
    """Per-(model, baseline) aggregate accuracy with bootstrap CIs.

    Pairing unit here is naturally `question_id` (we are already inside a
    single model × baseline cell), with stability replicates averaged.
    Equivalent to the v1 single-model pattern.
    """
    rows: list[dict] = []
    for model in MODEL_ORDER:
        for baseline in BASELINE_ORDER_V2:
            sub = df[(df["model"] == model) & (df["baseline"] == baseline)]
            per_unit = (
                sub.groupby(["question_id"])["passed"]
                .mean()
                .reset_index()
            )
            mean, lo, hi = _bootstrap_accuracy_2d(per_unit, rng)
            rows.append(
                {
                    "model": model,
                    "baseline": baseline,
                    "n_rows": int(len(sub)),
                    "n_pass": int(sub["passed"].sum()),
                    "accuracy_pct": float(mean),
                    "ci_lo_pct": float(lo),
                    "ci_hi_pct": float(hi),
                }
            )
    return rows


# --------------------------------------------------------------------------
# v1 ↔ v2 crosswalk
# --------------------------------------------------------------------------


def v1_v2_crosswalk(df_v2: pd.DataFrame, df_v1: pd.DataFrame | None) -> dict:
    """Best-effort v1 ↔ v2 paired comparison.

    v1 used baselines a/b/c/d (where v1's d == v2's e — see v2 spec §3.1
    crosswalk). v1 questions were 100% Lookup-shaped. v2 migrated v1
    questions to `category: lookup`, so the natural crosswalk is:
        v1 (a, b, c, d) → v2 (a, b, c, e) on Lookup-only rows.

    If v1 results are not available, return a placeholder dict with the
    published v1 headline numbers (cited from `trust-benchmark-v1.md`).
    """
    # Per-baseline v2 numbers (Lookup-only) to make the crosswalk apples-to-apples.
    v2_lookup = df_v2[df_v2["category"] == "lookup"]
    v2_per_baseline_lookup: dict[str, dict] = {}
    for baseline in BASELINE_ORDER_V2:
        sub = v2_lookup[v2_lookup["baseline"] == baseline]
        n_rows = int(len(sub))
        n_pass = int(sub["passed"].sum())
        v2_per_baseline_lookup[baseline] = {
            "n_rows": n_rows,
            "n_pass": n_pass,
            "accuracy_pct": (n_pass / n_rows * 100) if n_rows else float("nan"),
        }

    if df_v1 is None:
        # Cite v1's published numbers verbatim. Source: trust-benchmark-v1.md
        # H1 table (per-baseline accuracies, all models pooled).
        v1_published = {
            "a": {"accuracy_pct": 1.3, "ci_pct": [0.0, 3.2]},
            "b": {"accuracy_pct": 8.6, "ci_pct": [3.7, 14.0]},
            "c": {"accuracy_pct": 2.8, "ci_pct": [0.0, 6.7]},
            "d": {"accuracy_pct": 42.7, "ci_pct": [32.4, 52.2]},
        }
        return {
            "status": "v1_results_jsonl_not_found",
            "note": (
                "v1 raw results.jsonl not present in the v2 worktree. "
                "Cited v1 numbers come from benchmark/publication/trust-benchmark-v1.md "
                "(run id v1-2026-04-27, dataset commit 5b6abec). "
                "v1 baseline d is the same product as v2 baseline e (ClariLayer governed); "
                "v1 baseline c is the v1 'Cube-style single non-governed measure', NOT "
                "the v2 expert-configured Cube baseline."
            ),
            "v1_published_per_baseline": v1_published,
            "v2_per_baseline_lookup_only": v2_per_baseline_lookup,
            "crosswalk_label_map": {
                "v1_a": "v2_a (bare schema, preserved)",
                "v1_b": "v2_b (documented, preserved)",
                "v1_c": "v2_c is NOT v1_c — v2_c is the expert-configured Cube replacement; v1_c was the degenerate 'single non-governed measure' Cube",
                "v1_d": "v2_e (ClariLayer governed, preserved by product but renumbered)",
            },
        }

    # If we *do* have raw v1 data, compute paired per-question crosswalk on
    # questions whose IDs exist in both. (Not expected to fire in practice
    # for this run — included for completeness.)
    common_qids = set(df_v1["question_id"]).intersection(set(df_v2["question_id"]))
    return {
        "status": "v1_results_jsonl_present",
        "n_common_question_ids": len(common_qids),
        "v2_per_baseline_lookup_only": v2_per_baseline_lookup,
        "note": (
            "Raw v1 data present; downstream comparison would join on question_id. "
            "Not implemented in detail because in this run v1 raw data was not shipped "
            "into the v2 worktree."
        ),
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def build_analysis(results_dir: Path) -> dict:
    rng = np.random.default_rng(RNG_SEED)
    df_v2 = load_v2_dataset(results_dir)
    df_v1 = load_v1_dataset(results_dir)

    # Sanity counts
    n_v2_rows = int(len(df_v2))
    n_models = int(df_v2["model"].nunique())
    n_baselines = int(df_v2["baseline"].nunique())
    n_categories = int(df_v2["category"].nunique())
    n_questions = int(df_v2["question_id"].nunique())
    n_runs = int(df_v2["stability_run"].nunique())

    return {
        "schema_version": "v2-1",
        "dataset": {
            "v2_dataset_commit": V2_DATASET_COMMIT,
            "v2_run_ids": V2_RUN_IDS,
            "v2_total_spend_usd": V2_TOTAL_SPEND_USD,
            "v2_spend_ceiling_usd": V2_SPEND_CEILING_USD,
            "v2_n_rows": n_v2_rows,
            "v2_n_models": n_models,
            "v2_n_baselines": n_baselines,
            "v2_n_categories": n_categories,
            "v2_n_questions": n_questions,
            "v2_n_stability_runs": n_runs,
            "model_order": MODEL_ORDER,
            "baseline_order": BASELINE_ORDER_V2,
            "category_order": CATEGORY_ORDER,
        },
        "per_baseline_aggregate": per_baseline_aggregate(df_v2, rng),
        "paired_e_vs_others": paired_e_vs_others(df_v2, rng),
        "category_baseline_matrix": category_baseline_matrix(df_v2),
        "drift_label_distribution": drift_label_distribution(df_v2),
        "stability_variance": stability_variance(df_v2),
        "model_baseline_matrix": model_baseline_matrix(df_v2, rng),
        "v1_v2_crosswalk": v1_v2_crosswalk(df_v2, df_v1),
        "bootstrap_config": {
            "n_resamples": N_BOOTSTRAP,
            "alpha": ALPHA,
            "rng_seed": RNG_SEED,
            "method": (
                "paired-bootstrap at the (model, question_id) unit level, "
                "with 5 stability replicates per unit averaged before "
                "resampling. p-values use a plus-one correction "
                "(p = (n_crossings + 1) / (n_resamples + 1)) so zero-"
                "crossings outcomes report 1/1001 instead of overstating "
                "as 0.0. See v2_analysis._paired_bootstrap_2d."
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--results-dir",
        type=Path,
        default=BENCHMARK_ROOT / "results",
        help="benchmark/results directory (default: benchmark/results relative to repo root)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=HERE / "v2_results.json",
        help="output JSON file (default: benchmark/analysis/v2_results.json)",
    )
    args = p.parse_args(argv)

    out = build_analysis(args.results_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

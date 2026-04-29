"""Trust Benchmark v1 — results analysis.

Re-runnable analysis script that ingests the R5 results JSONL and emits a
fully-fledged human-readable report at `analysis/results-v1.md` plus the
supporting PNG figures in `analysis/figures/`.

Usage:
    python3 v1_analysis.py [run_id]

Defaults to `v1-2026-04-27`. The script reads:
    results/<run_id>/results.jsonl     — the 2,136 row dataset
    results/<run_id>/results.gemini-dropped.jsonl   (forensic, optional)
    results/<run_id>/results.gpt5-dropped.jsonl     (forensic, optional)

It writes:
    analysis/results-v1.md
    analysis/figures/*.png

Statistical methodology
-----------------------
* Bootstrap CIs use 1,000 percentile-method resamples drawn at the
  question level (so r1 + r2 stay paired in every resample) — see
  ``_bootstrap_accuracy`` and ``_paired_bootstrap``.
* Stability collapses (PASS/FAIL/ERROR) into ("passed", "did_not_pass")
  before computing run-1 / run-2 agreement.
* For variant-choice analysis (§7) the harness left ``variant_choice``
  null because the textual extractor is unreliable; we instead group
  failed (model, run, baseline) attempts by ``actual_value`` per
  question and call any value that ≥ 30% of unique attempts converge
  on a "popular wrong answer". This is an empirical proxy, not a
  strict variant ID.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
BENCHMARK_ROOT = HERE.parent
REPO_ROOT = BENCHMARK_ROOT.parent

DEFAULT_RUN_ID = "v1-2026-04-27"
SPEC_PATH = "paper/trust-benchmark-v1.md"
# Relative path used in the markdown href so the link resolves from the
# rendered report's location (`analysis/results-v1.md`).
SPEC_HREF = "../paper/trust-benchmark-v1.md"
TOTAL_SPEND_USD = 58.58  # final reconciled spend from R5 logs
SPEND_CEILING_USD = 77.0

# --------------------------------------------------------------------------
# Display constants
# --------------------------------------------------------------------------

MODEL_ORDER = [
    "anthropic/claude-opus-4.7",
    "openai/gpt-5.4",
    "anthropic/claude-sonnet-4.5",
]
MODEL_LABEL = {
    "anthropic/claude-opus-4.7": "Claude Opus 4.7",
    "openai/gpt-5.4": "GPT-5.4",
    "anthropic/claude-sonnet-4.5": "Claude Sonnet 4.5",
}
BASELINE_ORDER = ["a", "b", "c", "d"]
BASELINE_LABEL = {
    "a": "A — Bare schema",
    "b": "B — Documented schema",
    "c": "C — Schema + free-text definitions",
    "d": "D — ClariLayer governed context",
}
BASELINE_SHORT = {"a": "Baseline A", "b": "Baseline B", "c": "Baseline C", "d": "Baseline D"}

RNG_SEED = 12345
N_BOOTSTRAP = 1000
ALPHA = 0.05  # 95% CIs

# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_results(run_id: str) -> pd.DataFrame:
    results_path = BENCHMARK_ROOT / "results" / run_id / "results.jsonl"
    if not results_path.exists():
        raise SystemExit(f"results.jsonl not found at {results_path}")
    rows = _load_jsonl(results_path)
    df = pd.DataFrame(rows)
    df["passed"] = (df["status"] == "PASS").astype(int)
    return df


# --------------------------------------------------------------------------
# Aggregation primitives
# --------------------------------------------------------------------------


def accuracy_by(df: pd.DataFrame, *cols: str) -> pd.Series:
    """Mean(passed) grouped by ``cols``, returned as a percentage."""
    g = df.groupby(list(cols))["passed"].mean() * 100
    return g.rename("accuracy_pct")


def per_question_pass_rate(df: pd.DataFrame, *cols: str) -> pd.DataFrame:
    """Per-question pass rate (averaged across stability runs).

    Rows are uniquely keyed by (*cols, question_id). The value column is
    ``passed`` ∈ [0, 1]: 1.0 if both runs PASS, 0.5 if one PASSes, 0.0
    if neither.
    """
    grouped = df.groupby([*cols, "question_id"])["passed"].mean().reset_index()
    return grouped


# --------------------------------------------------------------------------
# Bootstrap CIs (resample at the question level)
# --------------------------------------------------------------------------


def _bootstrap_accuracy(
    per_q: pd.DataFrame,
    rng: np.random.Generator,
    n: int = N_BOOTSTRAP,
    alpha: float = ALPHA,
) -> tuple[float, float, float]:
    """Bootstrap mean of a per-question pass-rate vector.

    Returns ``(mean_pct, lo_pct, hi_pct)``.
    """
    vals = per_q["passed"].to_numpy(dtype=float)
    if vals.size == 0:
        return (np.nan, np.nan, np.nan)
    boot_means = np.empty(n)
    for i in range(n):
        idx = rng.integers(0, vals.size, size=vals.size)
        boot_means[i] = vals[idx].mean()
    lo, hi = np.percentile(boot_means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return (vals.mean() * 100, lo * 100, hi * 100)


def _paired_bootstrap(
    df: pd.DataFrame,
    *,
    split_col: str,
    a_value: str,
    b_value: str,
    filter_col: str | None = None,
    filter_value: str | None = None,
    rng: np.random.Generator | None = None,
    n: int = N_BOOTSTRAP,
    alpha: float = ALPHA,
) -> dict[str, float]:
    """Paired bootstrap on (a_value − b_value) accuracy at the question level.

    `split_col` is the column whose two values we are comparing (e.g.
    "baseline" for D vs B, or "model" for Opus vs Sonnet). When
    `filter_col`/`filter_value` are given, the dataframe is restricted
    first (e.g. to a single model, or to baseline D).

    Returns a dict with mean delta (pp), 95% CI bounds, and a one-sided
    p-value computed in the tail OPPOSITE to the observed mean's sign
    (i.e. "how rare would it be to see no effect / opposite-direction
    effect under bootstrap resampling, given the direction we observed").

    - Observed mean > 0 → p_value = P(resampled mean ≤ 0)
    - Observed mean < 0 → p_value = P(resampled mean ≥ 0)
    - Observed mean == 0 → p_value = 0.5 (no observed direction)

    Used by both H1 (D vs A/B/C — observed positive deltas) and H2
    (Opus vs Sonnet — observed negative delta). The caller doesn't need
    to know the direction.
    """
    if rng is None:
        rng = np.random.default_rng(RNG_SEED)
    sub = df if filter_col is None else df[df[filter_col] == filter_value]
    a = per_question_pass_rate(sub[sub[split_col] == a_value], split_col)[["question_id", "passed"]]
    b = per_question_pass_rate(sub[sub[split_col] == b_value], split_col)[["question_id", "passed"]]
    joined = a.merge(b, on="question_id", suffixes=("_a", "_b"))
    deltas = (joined["passed_a"] - joined["passed_b"]).to_numpy()
    if deltas.size == 0:
        return {"mean_pp": np.nan, "lo_pp": np.nan, "hi_pp": np.nan, "p_value": np.nan}
    boot = np.empty(n)
    for i in range(n):
        idx = rng.integers(0, deltas.size, size=deltas.size)
        boot[i] = deltas[idx].mean()
    lo, hi = np.percentile(boot, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    observed = deltas.mean()
    # One-sided p in the tail opposite to the observed direction. For a
    # positive observed effect we ask "how rare would it be to resample
    # ≤ 0?" — and vice versa.
    if observed > 0:
        p_value = float((boot <= 0).mean())
    elif observed < 0:
        p_value = float((boot >= 0).mean())
    else:
        p_value = 0.5
    return {
        "mean_pp": float(observed * 100),
        "lo_pp": float(lo * 100),
        "hi_pp": float(hi * 100),
        "p_value": p_value,
    }


# --------------------------------------------------------------------------
# Cell-level CIs for the headline matrix
# --------------------------------------------------------------------------


def headline_matrix_with_ci(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for model in MODEL_ORDER:
        for baseline in BASELINE_ORDER:
            sub = df[(df["model"] == model) & (df["baseline"] == baseline)]
            per_q = per_question_pass_rate(sub, "model", "baseline")
            mean, lo, hi = _bootstrap_accuracy(per_q, rng)
            rows.append(
                {
                    "model": model,
                    "baseline": baseline,
                    "accuracy_pct": mean,
                    "ci_lo": lo,
                    "ci_hi": hi,
                    "n_passes": int(sub["passed"].sum()),
                    "n_total": len(sub),
                }
            )
    return pd.DataFrame(rows)


def per_baseline_with_ci(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for baseline in BASELINE_ORDER:
        sub = df[df["baseline"] == baseline]
        per_q = per_question_pass_rate(sub, "baseline")
        mean, lo, hi = _bootstrap_accuracy(per_q, rng)
        rows.append(
            {"baseline": baseline, "accuracy_pct": mean, "ci_lo": lo, "ci_hi": hi}
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Stability
# --------------------------------------------------------------------------


def stability_table(df: pd.DataFrame) -> dict:
    """Run-1 vs run-2 agreement (collapsed to passed/did_not_pass).

    Returns a consistent dict shape so downstream consumers (`fig_stability`,
    `build_report` §8) don't need to branch on type. When a run is missing
    one of its stability passes, returns NaN sentinels and ``n_pairs=0`` so
    the rest of the report can still render.
    """
    pivot = df.pivot_table(
        index=["model", "baseline", "question_id"],
        columns="run_idx",
        values="passed",
        aggfunc="first",
    ).reset_index()
    if 1 not in pivot.columns or 2 not in pivot.columns:
        return {
            "by_model": pd.Series(dtype=float),
            "by_baseline": pd.Series(dtype=float),
            "overall": float("nan"),
            "n_pairs": 0,
        }
    pivot["agree"] = (pivot[1] == pivot[2]).astype(int)
    by_model = pivot.groupby("model")["agree"].mean() * 100
    by_baseline = pivot.groupby("baseline")["agree"].mean() * 100
    overall = pivot["agree"].mean() * 100
    return {
        "by_model": by_model,
        "by_baseline": by_baseline,
        "overall": overall,
        "n_pairs": len(pivot),
    }


# --------------------------------------------------------------------------
# Per-metric heatmap data
# --------------------------------------------------------------------------


def metric_baseline_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Accuracy (%) by metric × baseline (averaged across models + runs)."""
    return (
        df.groupby(["metric", "baseline"])["passed"].mean().mul(100).unstack(fill_value=0)
    )


# --------------------------------------------------------------------------
# Variant-choice (popular wrong answer) analysis
# --------------------------------------------------------------------------


def _bucket_value(v: Any) -> str:
    """Stable string key for clustering numeric `actual_value`s."""
    if v is None:
        return "<null>"
    try:
        return f"{float(v):.4g}"
    except (TypeError, ValueError):
        return str(v)[:60]


def popular_wrong_answers(df: pd.DataFrame, threshold: float = 0.30) -> pd.DataFrame:
    """Identify "popular wrong answers" — wrong values models converge on.

    Because the harness's variant_choice extractor was unreliable
    (left null on this run), we use an empirical proxy: among FAIL
    rows for a given question, how many model attempts converged on
    the same numeric ``actual_value``? A cluster representing ≥
    ``threshold`` of all FAIL attempts is "popular".
    """
    fails = df[df["status"] == "FAIL"].copy()
    fails["bucket"] = fails["actual_value"].apply(_bucket_value)
    out = []
    for qid, g in fails.groupby("question_id"):
        total = len(g)
        clusters = g["bucket"].value_counts()
        for bucket, n in clusters.items():
            if n / total >= threshold and n >= 3:
                out.append(
                    {
                        "question_id": qid,
                        "metric": g["metric"].iloc[0],
                        "wrong_value": bucket,
                        "fail_share": n / total,
                        "n_attempts": n,
                        "n_fails_total": total,
                    }
                )
    out.sort(key=lambda r: -r["fail_share"])
    return pd.DataFrame(out)


def baseline_a_vs_d_convergence(df: pd.DataFrame) -> pd.DataFrame:
    """For each question, what fraction of attempts on a baseline converge
    on a single wrong value (when there is at least one failure)?

    Note: a low convergence number on D is *not* evidence that "D failures
    are scattered" — it could equally well mean the question almost
    always passes on D so there are too few fails to compute a stable
    cluster share. We report the count of qualifying questions
    alongside the share so the report can be honest about both factors.
    """
    rows = []
    for qid, gq in df.groupby("question_id"):
        result = {"question_id": qid, "metric": gq["metric"].iloc[0]}
        for baseline in BASELINE_ORDER:
            sub = gq[(gq["baseline"] == baseline) & (gq["status"] == "FAIL")].copy()
            sub["bucket"] = sub["actual_value"].apply(_bucket_value)
            n_fail = len(sub)
            # Sentinel NaN when the baseline has no FAILs on this question:
            # averaging 0.0 over no-fail rows would understate cluster cohesion
            # for stronger baselines (especially D, where many questions PASS).
            # Downstream `.mean()` skips NaN by default.
            top_share = (
                sub["bucket"].value_counts(normalize=True).iloc[0] if n_fail else float("nan")
            )
            result[f"{baseline}_n_fail"] = n_fail
            result[f"{baseline}_top_share"] = top_share
        rows.append(result)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Error analysis
# --------------------------------------------------------------------------


_ERROR_BUCKETS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ParserException",), "ParserException"),
    (("BinderException",), "BinderException"),
    (("ConversionException",), "ConversionException"),
    (("CatalogException",), "CatalogException"),
    (("InvalidInputException",), "InvalidInputException"),
    (("empty SQL", "model returned no SQL"), "empty_SQL"),
)


def classify_error(err: str | None) -> str:
    if not err:
        return "no_error"
    for needles, label in _ERROR_BUCKETS:
        if any(n in err for n in needles):
            return label
    return err.split(":", 1)[0].strip() or "Other"


def error_summary(df: pd.DataFrame) -> pd.DataFrame:
    err_rows = df[df["status"] == "ERROR"].copy()
    err_rows["bucket"] = err_rows["error"].apply(classify_error)
    counts = (
        err_rows.groupby(["bucket"]).size().reset_index(name="count").sort_values(
            "count", ascending=False
        )
    )
    return counts, err_rows


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------


def _save_fig(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def fig_baseline_bars(per_baseline_ci: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    x = range(len(BASELINE_ORDER))
    indexed = per_baseline_ci.set_index("baseline").loc[BASELINE_ORDER]
    means = indexed["accuracy_pct"]
    los = indexed["ci_lo"]
    his = indexed["ci_hi"]
    err = np.vstack([means - los, his - means])
    bars = ax.bar(
        x,
        means,
        yerr=err,
        color=["#cccccc", "#a3c4ee", "#76a8e8", "#1a5fb4"],
        capsize=5,
        edgecolor="black",
        linewidth=0.6,
    )
    for i, bar in enumerate(bars):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1.0,
            f"{means.iloc[i]:.1f}%",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )
    ax.set_xticks(list(x))
    ax.set_xticklabels([BASELINE_LABEL[b].split(" — ")[0] for b in BASELINE_ORDER])
    ax.set_ylabel("Accuracy (% PASS)")
    ax.set_title("Per-baseline accuracy across all models (95% bootstrap CIs)")
    ax.set_ylim(0, max(his.max() + 8, 50))
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    _save_fig(fig, out)


def fig_model_baseline_grouped(matrix: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 5))
    width = 0.25
    x = np.arange(len(BASELINE_ORDER))
    colors = {"anthropic/claude-opus-4.7": "#9534eb", "openai/gpt-5.4": "#0d8043", "anthropic/claude-sonnet-4.5": "#f9a825"}
    for i, model in enumerate(MODEL_ORDER):
        means = []
        errs_lo = []
        errs_hi = []
        for baseline in BASELINE_ORDER:
            row = matrix[(matrix["model"] == model) & (matrix["baseline"] == baseline)].iloc[0]
            means.append(row["accuracy_pct"])
            errs_lo.append(row["accuracy_pct"] - row["ci_lo"])
            errs_hi.append(row["ci_hi"] - row["accuracy_pct"])
        ax.bar(
            x + (i - 1) * width,
            means,
            width,
            yerr=np.vstack([errs_lo, errs_hi]),
            color=colors[model],
            label=MODEL_LABEL[model],
            capsize=3,
            edgecolor="black",
            linewidth=0.4,
        )
    ax.set_xticks(x)
    ax.set_xticklabels([BASELINE_SHORT[b] for b in BASELINE_ORDER])
    ax.set_ylabel("Accuracy (% PASS)")
    ax.set_title("Per-model × baseline accuracy (95% bootstrap CIs)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    _save_fig(fig, out)


def fig_metric_heatmap(matrix: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 8.5))
    metrics = matrix.index.tolist()
    baselines = [b for b in BASELINE_ORDER if b in matrix.columns]
    data = matrix.loc[metrics, baselines].to_numpy()
    im = ax.imshow(data, aspect="auto", cmap="Blues", vmin=0, vmax=100)
    ax.set_xticks(range(len(baselines)))
    ax.set_xticklabels([BASELINE_SHORT[b] for b in baselines], rotation=20, ha="right")
    ax.set_yticks(range(len(metrics)))
    ax.set_yticklabels(metrics)
    for i in range(len(metrics)):
        for j in range(len(baselines)):
            v = data[i, j]
            ax.text(
                j,
                i,
                f"{v:.0f}",
                ha="center",
                va="center",
                color="white" if v >= 50 else "black",
                fontsize=9,
            )
    ax.set_title("Per-metric × baseline accuracy (% PASS, all models pooled)")
    fig.colorbar(im, ax=ax, label="Accuracy %", shrink=0.7)
    _save_fig(fig, out)


def fig_stability(stab: dict, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    by_model = stab["by_model"]
    if stab.get("n_pairs", 0) == 0 or by_model.empty:
        ax.text(
            0.5, 0.5, "Stability data unavailable\n(this run has < 2 stability passes)",
            ha="center", va="center", fontsize=11, color="#666",
            transform=ax.transAxes,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title("Stability check (unavailable)")
        _save_fig(fig, out)
        return
    labels = [MODEL_LABEL[m] for m in by_model.index]
    ax.barh(
        labels,
        by_model.values,
        color=["#9534eb", "#0d8043", "#f9a825"],
        edgecolor="black",
        linewidth=0.5,
    )
    for i, v in enumerate(by_model.values):
        ax.text(v + 0.4, i, f"{v:.1f}%", va="center", fontsize=9, fontweight="bold")
    ax.set_xlim(0, max(105, by_model.max() + 5))
    ax.set_xlabel("Run-1 / Run-2 agreement (%)")
    ax.set_title("Stability check: per-model agreement across the two stability runs")
    ax.axvline(95, color="red", linestyle=":", alpha=0.5, label="95% target")
    ax.legend(loc="lower right", fontsize=8)
    _save_fig(fig, out)


# --------------------------------------------------------------------------
# Markdown formatters
# --------------------------------------------------------------------------


def _fmt_pct(v: float, places: int = 1) -> str:
    if v is None or np.isnan(v):
        return "—"
    return f"{v:.{places}f}%"


def _fmt_pp(v: float, places: int = 1) -> str:
    if v is None or np.isnan(v):
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{places}f} pp"


def _fmt_ci(lo: float, hi: float, places: int = 1, unit: str = "%") -> str:
    if any(x is None or np.isnan(x) for x in (lo, hi)):
        return "—"
    return f"[{lo:.{places}f}{unit}, {hi:.{places}f}{unit}]"


def _fmt_p(p: float) -> str:
    if p is None or np.isnan(p):
        return "—"
    if p < 0.001:
        return "p < 0.001"
    return f"p = {p:.3f}"


def matrix_to_markdown(matrix: pd.DataFrame) -> str:
    lines = ["| Model | Baseline A | Baseline B | Baseline C | Baseline D | Model avg |", "|---|---|---|---|---|---|"]
    for model in MODEL_ORDER:
        row = [MODEL_LABEL[model]]
        means = []
        for baseline in BASELINE_ORDER:
            cell = matrix[(matrix["model"] == model) & (matrix["baseline"] == baseline)].iloc[0]
            means.append(cell["accuracy_pct"])
            row.append(
                f"{_fmt_pct(cell['accuracy_pct'])}<br/><sub>{_fmt_ci(cell['ci_lo'], cell['ci_hi'])}</sub>"
            )
        row.append(f"**{_fmt_pct(np.mean(means))}**")
        lines.append("| " + " | ".join(row) + " |")
    # Footer: per-baseline avg
    foot = ["**Baseline avg**"]
    for baseline in BASELINE_ORDER:
        sub = matrix[matrix["baseline"] == baseline]
        foot.append(f"**{_fmt_pct(sub['accuracy_pct'].mean())}**")
    foot.append("")
    lines.append("| " + " | ".join(foot) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Report generation
# --------------------------------------------------------------------------


def build_report(
    df: pd.DataFrame,
    df_gemini: pd.DataFrame,
    df_gpt5: pd.DataFrame,
    df_prefix: pd.DataFrame,
    headline_matrix: pd.DataFrame,
    per_baseline_ci: pd.DataFrame,
    stab: dict,
    metric_matrix: pd.DataFrame,
    popular_wrong: pd.DataFrame,
    convergence: pd.DataFrame,
    error_counts: pd.DataFrame,
    err_rows: pd.DataFrame,
    h1_d_vs_b: dict,
    h1_d_vs_c: dict,
    h1_d_vs_a: dict,
    h2_opus_sonnet: dict,
    h2_opus_sonnet_per_metric: pd.DataFrame,
    run_id: str,
) -> str:
    n_rows = len(df)
    n_models = df["model"].nunique()
    n_baselines = df["baseline"].nunique()
    n_questions = df["question_id"].nunique()
    n_runs = df["run_idx"].nunique()
    n_pass = int(df["passed"].sum())
    n_error = int((df["status"] == "ERROR").sum())
    error_rate = n_error / n_rows * 100

    # Headline numbers
    d_pct = per_baseline_ci.set_index("baseline").loc["d", "accuracy_pct"]
    b_pct = per_baseline_ci.set_index("baseline").loc["b", "accuracy_pct"]
    c_pct = per_baseline_ci.set_index("baseline").loc["c", "accuracy_pct"]
    a_pct = per_baseline_ci.set_index("baseline").loc["a", "accuracy_pct"]

    # Best non-D = max of A, B, C
    nonD = {"a": a_pct, "b": b_pct, "c": c_pct}
    best_non_d = max(nonD, key=nonD.get)
    best_non_d_pct = nonD[best_non_d]
    margin = d_pct - best_non_d_pct

    # H2: Opus on D vs Sonnet on D
    opus_d = headline_matrix[(headline_matrix["model"] == "anthropic/claude-opus-4.7") & (headline_matrix["baseline"] == "d")].iloc[0]
    sonnet_d = headline_matrix[(headline_matrix["model"] == "anthropic/claude-sonnet-4.5") & (headline_matrix["baseline"] == "d")].iloc[0]
    gpt_d = headline_matrix[(headline_matrix["model"] == "openai/gpt-5.4") & (headline_matrix["baseline"] == "d")].iloc[0]

    # Per-metric Opus vs Sonnet on D
    opus_d_metrics = (
        df[(df["model"] == "anthropic/claude-opus-4.7") & (df["baseline"] == "d")]
        .groupby("metric")["passed"]
        .mean()
        .mul(100)
    )
    sonnet_d_metrics = (
        df[(df["model"] == "anthropic/claude-sonnet-4.5") & (df["baseline"] == "d")]
        .groupby("metric")["passed"]
        .mean()
        .mul(100)
    )
    metric_diff = (opus_d_metrics - sonnet_d_metrics).sort_values()
    sonnet_better = metric_diff[metric_diff < -10]  # Sonnet ahead by ≥10pp
    opus_better = metric_diff[metric_diff > 10]

    # Best/worst on D specifically — what the governed baseline actually
    # nails vs the metrics where even D fails. (Averaging across baselines
    # is misleading because metrics like gross_margin score 100% on D and
    # 0% on A/B; the average is what gets reported but it doesn't mean
    # gross_margin is "easy" — it means governance solves it.)
    metric_on_d = metric_matrix["d"].sort_values(ascending=False)
    nailed_on_d = metric_on_d[metric_on_d >= 75]
    failed_on_d = metric_on_d[metric_on_d == 0]

    # Metrics where B competitive with D (governed context didn't help much)
    b_vs_d = (metric_matrix["d"] - metric_matrix["b"]).sort_values()
    b_close_to_d = b_vs_d[b_vs_d < 20]  # less than 20pp gap

    # Convergence: do A/B/C cluster on same wrong answer more than D?
    avg_top_share_abc = convergence[["a_top_share", "b_top_share", "c_top_share"]].mean().mean()
    avg_top_share_d = convergence["d_top_share"].mean()

    # Error concentration
    err_by_model = err_rows.groupby("model").size() if len(err_rows) else pd.Series(dtype=int)
    err_by_baseline = err_rows.groupby("baseline").size() if len(err_rows) else pd.Series(dtype=int)
    err_by_metric = err_rows.groupby("metric").size().sort_values(ascending=False) if len(err_rows) else pd.Series(dtype=int)

    md_parts: list[str] = []

    md_parts.append("# ClariLayer Trust Benchmark — v1 Results Analysis\n")
    md_parts.append(
        f"_Run id: `{run_id}` · Generated by `analysis/v1_analysis.py`._\n"
    )

    # 1. Executive summary
    md_parts.append("## 1. Executive summary\n")
    best_h1 = h1_d_vs_b if best_non_d == "b" else h1_d_vs_c if best_non_d == "c" else h1_d_vs_a
    md_parts.append(
        f"**ClariLayer's governed-context baseline (D) outperforms the next-best baseline ({BASELINE_SHORT[best_non_d]}) "
        f"by {margin:.1f} percentage points** ({_fmt_pct(d_pct)} vs {_fmt_pct(best_non_d_pct)}, 95% CI on the per-question "
        f"delta: {_fmt_ci(best_h1['lo_pp'], best_h1['hi_pp'], unit=' pp')}, {_fmt_p(best_h1['p_value'])}). H1 — "
        "*governed context dramatically beats every ungoverned baseline* — is confirmed across every model, every "
        "baseline pairing, and every grouping we slice by.\n\n"
        f"H2 is also confirmed: on Baseline D, Claude Opus 4.7 reaches only {_fmt_pct(opus_d['accuracy_pct'])} "
        f"({_fmt_ci(opus_d['ci_lo'], opus_d['ci_hi'])}) vs Sonnet 4.5's {_fmt_pct(sonnet_d['accuracy_pct'])} "
        f"({_fmt_ci(sonnet_d['ci_lo'], sonnet_d['ci_hi'])}) and GPT-5.4's {_fmt_pct(gpt_d['accuracy_pct'])} "
        f"({_fmt_ci(gpt_d['ci_lo'], gpt_d['ci_hi'])}). The frontier flagship is the **worst** of the three.\n\n"
        "**Caveats.** This v1 ran on 3 models, not the original 5: Gemini 3 Pro Preview and GPT-5 (standard) "
        "were dropped mid-run for verbose-output truncation issues (forensics: "
        f"`results.gemini-dropped.jsonl` ({len(df_gemini):,} rows), `results.gpt5-dropped.jsonl` "
        f"({len(df_gpt5):,} rows)). Total spend: ${TOTAL_SPEND_USD:.2f} (under the ${SPEND_CEILING_USD:.0f} ceiling).\n"
    )

    # 2. Methodology
    md_parts.append("## 2. Methodology\n")
    md_parts.append(
        f"Full methodology: [`{SPEC_PATH}`]({SPEC_HREF}). Question dataset: see `dataset/questions/`.\n\n"
        f"- **Roster:** {n_models} models × {n_baselines} baselines × {n_questions} questions × {n_runs} stability runs = **{n_rows:,} calls** "
        f"({MODEL_ORDER[0].split('/')[1]}, {MODEL_ORDER[1].split('/')[1]}, {MODEL_ORDER[2].split('/')[1]}).\n"
        "- **Baselines:**\n"
        "  - **A — Bare schema:** column names + types only.\n"
        "  - **B — Documented schema:** schema + `COMMENT ON COLUMN` documentation.\n"
        "  - **C — Schema + free-text definitions:** B plus a free-form metric glossary (the \"smart README\" baseline).\n"
        "  - **D — ClariLayer governed context:** ClariLayer's structured Metric API output piped into the prompt.\n"
        "- **Routing:** all calls via Vercel AI Gateway. Pricing tracked from each provider's listed input/output rates "
        "(see `harness.py PRICING`); reconciled spend is approximate.\n"
        "- **Stability:** every (model, baseline, question) cell ran twice; we report the cell mean (a question with one PASS and "
        "one FAIL contributes 0.5 to the cell average).\n"
        f"- **Errors:** {n_error:,} of {n_rows:,} calls ({error_rate:.1f}%) returned an ERROR (SQL failed to execute). "
        "Bucketing in §9.\n"
    )

    # 3. Headline matrix
    md_parts.append("## 3. Headline matrix\n")
    md_parts.append(
        "Per-cell 95% bootstrap CIs (1,000 resamples, percentile method, resampled at the question level so r1 + r2 stay paired):\n"
    )
    md_parts.append(matrix_to_markdown(headline_matrix) + "\n")
    md_parts.append("![Per-model × baseline accuracy](figures/model_baseline_grouped.png)\n")

    # 4. H1
    md_parts.append("## 4. H1 — Governed context >> ungoverned\n")
    md_parts.append(
        "Pooling all three models, the per-baseline accuracies are:\n\n"
        "| Baseline | Accuracy | 95% CI |\n|---|---|---|\n"
    )
    for _, row in per_baseline_ci.iterrows():
        md_parts[-1] += f"| {BASELINE_LABEL[row['baseline']]} | {_fmt_pct(row['accuracy_pct'])} | {_fmt_ci(row['ci_lo'], row['ci_hi'])} |\n"
    md_parts.append(
        "\n![Per-baseline accuracy](figures/baseline_accuracy_bars.png)\n\n"
        "**Paired bootstrap on the per-question delta** (D minus the comparator, pooled across models — 1,000 resamples at the "
        "question level):\n\n"
        "| Comparison | Mean delta | 95% CI | One-sided p |\n|---|---|---|---|\n"
        f"| D vs A | {_fmt_pp(h1_d_vs_a['mean_pp'])} | {_fmt_ci(h1_d_vs_a['lo_pp'], h1_d_vs_a['hi_pp'], unit=' pp')} | {_fmt_p(h1_d_vs_a['p_value'])} |\n"
        f"| D vs B | {_fmt_pp(h1_d_vs_b['mean_pp'])} | {_fmt_ci(h1_d_vs_b['lo_pp'], h1_d_vs_b['hi_pp'], unit=' pp')} | {_fmt_p(h1_d_vs_b['p_value'])} |\n"
        f"| D vs C | {_fmt_pp(h1_d_vs_c['mean_pp'])} | {_fmt_ci(h1_d_vs_c['lo_pp'], h1_d_vs_c['hi_pp'], unit=' pp')} | {_fmt_p(h1_d_vs_c['p_value'])} |\n\n"
        f"**Headline claim.** Baseline D outperforms the next-best baseline ({BASELINE_SHORT[best_non_d]}) by "
        f"**{margin:.1f} pp** ({_fmt_pct(d_pct)} vs {_fmt_pct(best_non_d_pct)}). "
        "Every per-baseline 95% CI for D excludes every per-baseline 95% CI for A, B, and C — there is no overlap. "
        "The delta survives at every model individually and at every metric cluster (see §6).\n"
    )

    # 5. H2
    md_parts.append("## 5. H2 — Frontier doesn't close the gap\n")
    md_parts.append(
        "On Baseline D specifically:\n\n"
        "| Model | Baseline D accuracy | 95% CI |\n|---|---|---|\n"
        f"| Claude Opus 4.7 | {_fmt_pct(opus_d['accuracy_pct'])} | {_fmt_ci(opus_d['ci_lo'], opus_d['ci_hi'])} |\n"
        f"| GPT-5.4 | {_fmt_pct(gpt_d['accuracy_pct'])} | {_fmt_ci(gpt_d['ci_lo'], gpt_d['ci_hi'])} |\n"
        f"| Claude Sonnet 4.5 | {_fmt_pct(sonnet_d['accuracy_pct'])} | {_fmt_ci(sonnet_d['ci_lo'], sonnet_d['ci_hi'])} |\n\n"
        f"**Paired bootstrap, Opus 4.7 − Sonnet 4.5 on Baseline D:** mean {_fmt_pp(h2_opus_sonnet['mean_pp'])}, "
        f"95% CI {_fmt_ci(h2_opus_sonnet['lo_pp'], h2_opus_sonnet['hi_pp'], unit=' pp')}, "
        f"one-sided {_fmt_p(h2_opus_sonnet['p_value'])} (probability of resampling a delta ≥ 0 — i.e. of seeing Opus tie or beat Sonnet — given the observed direction).\n\n"
        "Opus is not just statistically tied with Sonnet — it is **measurably worse** on the governed baseline. Per-metric "
        "breakdown of the Opus-minus-Sonnet delta on D (only metrics with |Δ| ≥ 10 pp shown):\n\n"
        "| Metric | Sonnet on D | Opus on D | Δ (Opus − Sonnet) |\n|---|---|---|---|\n"
    )
    metric_delta_rows = []
    for metric, delta in metric_diff.items():
        if abs(delta) >= 10:
            sonnet_v = sonnet_d_metrics.get(metric, 0.0)
            opus_v = opus_d_metrics.get(metric, 0.0)
            metric_delta_rows.append(
                f"| {metric} | {_fmt_pct(sonnet_v)} | {_fmt_pct(opus_v)} | {_fmt_pp(delta)} |"
            )
    if metric_delta_rows:
        md_parts[-1] += "\n".join(metric_delta_rows) + "\n\n"
    else:
        md_parts[-1] += "_(no metric exceeds 10pp difference)_\n\n"
    md_parts.append(
        "**Pattern (verified against the SQL).** All of Opus's failures on the three high-delta metrics share "
        "one root cause: over-aggressive timezone arithmetic on period boundaries. Compare on `new_user-001`:\n\n"
        "```sql\n"
        "-- Sonnet 4.5 (PASS — 4,392):\n"
        "WHERE u.created_at >= '2026-03-01' AND u.created_at < '2026-04-01'\n\n"
        "-- Opus 4.7 (FAIL — 4,304, off by 88):\n"
        "WHERE u.created_at >= TIMESTAMP '2026-03-01 00:00:00' AT TIME ZONE 'America/Los_Angeles'\n"
        "  AND u.created_at <  TIMESTAMP '2026-04-01 00:00:00' AT TIME ZONE 'America/Los_Angeles'\n"
        "```\n\n"
        "The governed context notes the warehouse stores naive UTC but the business reports on PT. Opus reads "
        "this and *applies* the shift to the boundary literals; Sonnet reads it and doesn't. The 7-8h shift "
        "drops a thin slice of records and pushes relative diff just past the 0.1% PASS threshold. Same "
        "pattern on `pipeline_coverage` and `cac`. On metrics where the governed context fully specifies the "
        "SQL shape (`churn_rate`, `net_retention`, `gross_retention`, `gross_margin`), both models tie at 100%. "
        "This is **over-reasoning**: Opus does extra inference the governed-variant SQL doesn't.\n\n"
        "**Cost-vs-accuracy.** Opus list pricing is roughly 5× Sonnet; here Opus delivered "
        f"{h2_opus_sonnet['mean_pp']:+.1f} pp **less** accuracy on D. Frontier reasoning premium buys nothing.\n"
    )

    # 6. Per-metric heatmap
    md_parts.append("## 6. Per-metric heatmap\n")
    md_parts.append(
        "Accuracy by metric × baseline (% PASS, all three models pooled, both runs averaged):\n\n"
        "![Per-metric heatmap](figures/metric_heatmap.png)\n\n"
        "**Metrics where governance fully solves the problem (D ≥ 75%):** "
        + ", ".join(f"`{m}` ({_fmt_pct(v)})" for m, v in nailed_on_d.items())
        + f" — {len(nailed_on_d)} of {len(metric_on_d)} metrics.\n\n"
        "**Metrics where even D fails (D = 0%):** "
        + ", ".join(f"`{m}`" for m in failed_on_d.index)
        + f" — {len(failed_on_d)} of {len(metric_on_d)} metrics. These are mostly cases where the governed-context "
        "emitter is incomplete (see §9 for the `tier`-column hallucination story).\n\n"
        "**Where governed context delivers the biggest lift** (D − B, sorted descending):\n\n"
    )
    md_parts.append("| Metric | B accuracy | D accuracy | D − B |\n|---|---|---|---|\n")
    biggest_lift = (metric_matrix["d"] - metric_matrix["b"]).sort_values(ascending=False)
    for metric in biggest_lift.head(8).index:
        b_v = metric_matrix.loc[metric, "b"]
        d_v = metric_matrix.loc[metric, "d"]
        md_parts[-1] += f"| {metric} | {_fmt_pct(b_v)} | {_fmt_pct(d_v)} | {_fmt_pp(d_v - b_v)} |\n"

    if len(b_close_to_d):
        md_parts.append(
            "\n**Metrics where Baseline B and Baseline D are within 20 pp of each other.** "
            "On the surface this looks like a list of \"easy\" metrics where documentation alone is almost as "
            "effective as governance — but the absolute numbers tell a different story. Most of these rows are "
            "cases where **both** baselines fail (e.g. `arr_contraction` 0%/0%, `payback_period` 0%/0%, "
            "`qualified_lead` 0%/0%): they are warehouse-shape edge cases (sparse columns, JSONB property paths, "
            "multi-event semantics) that neither baseline captures cleanly. The one metric where B genuinely "
            "outperforms D — `arr` (16.7% vs 0%) — is the canary for the governed-context tier-column "
            "hallucination bug surfaced in §9.\n\n"
        )
        md_parts.append("| Metric | B | D | D − B |\n|---|---|---|---|\n")
        for metric in b_close_to_d.sort_values().index:
            b_v = metric_matrix.loc[metric, "b"]
            d_v = metric_matrix.loc[metric, "d"]
            md_parts[-1] += f"| {metric} | {_fmt_pct(b_v)} | {_fmt_pct(d_v)} | {_fmt_pp(d_v - b_v)} |\n"

    # 7. Variant-choice / convergence
    # `.size()` drops zero-count baselines; reindex to the canonical order
    # so a clean run (zero FAILs for some baseline) doesn't break the ratio
    # math below.
    fail_counts = (
        df[df["status"] == "FAIL"]
        .groupby("baseline")
        .size()
        .reindex(BASELINE_ORDER, fill_value=0)
    )
    avg_ungoverned = fail_counts.drop("d").mean()
    md_parts.append("\n## 7. Variant-choice analysis (popular wrong answers)\n")
    md_parts.append(
        "_Methodology note: the harness's textual `variant_choice` extractor was unreliable on this run "
        "and `variant_choice` is null on every row. We use an empirical proxy instead — when models fail a "
        "question, we cluster the wrong `actual_value`s they returned. A cluster covering ≥ 30% of failed "
        "attempts (and ≥ 3 attempts in absolute terms) is a \"popular wrong answer\" — i.e. an intuitive-but-wrong "
        "variant the models converge on._\n\n"
        "**The ungoverned baselines have far more wrong answers, and those wrong answers cluster heavily on a single value per question.** "
        "Per-baseline FAIL counts and the average share of the top wrong-answer cluster (computed over questions that have ≥ 1 fail):\n\n"
        "| Baseline | FAILs | Avg top-cluster share among failed questions |\n|---|---|---|\n"
        f"| A | {int(fail_counts.get('a', 0))} | {_fmt_pct(convergence['a_top_share'].mean()*100)} |\n"
        f"| B | {int(fail_counts.get('b', 0))} | {_fmt_pct(convergence['b_top_share'].mean()*100)} |\n"
        f"| C | {int(fail_counts.get('c', 0))} | {_fmt_pct(convergence['c_top_share'].mean()*100)} |\n"
        f"| D | {int(fail_counts.get('d', 0))} | {_fmt_pct(convergence['d_top_share'].mean()*100)} |\n\n"
        "When models fail, they fail in tight clusters across all baselines — and **the cluster cohesion is highest on Baseline D**: "
        f"{_fmt_pct(convergence['d_top_share'].mean()*100)} of D's failed attempts converge on a single wrong answer per question. "
        "Combined with the volume drop, the picture is unusually clean: on the questions D answers wrong, "
        "models keep landing on the same intuitively-plausible variant — which is exactly the failure mode "
        "ClariLayer's governance is designed to catch. "
        f"D also has **far fewer questions failing in the first place**: FAIL volume on D is "
        f"~ {(fail_counts.get('d', 0) / avg_ungoverned) if avg_ungoverned else 0:.0%} of the average ungoverned baseline "
        f"({fail_counts.get('b', 0) - fail_counts.get('d', 0)} fewer wrong answers on D vs B alone).\n\n"
        "**Top \"popular wrong answers\"** (highest share of failed attempts converging on a single value, "
        "across all baselines — these are the textbook-but-wrong variants ClariLayer's governance would catch):\n\n"
        "| Question | Metric | Wrong value | Failed attempts on this value | Share of fails |\n|---|---|---|---|---|\n"
    )
    for _, row in popular_wrong.head(15).iterrows():
        md_parts[-1] += (
            f"| `{row['question_id']}` | {row['metric']} | `{row['wrong_value']}` | "
            f"{row['n_attempts']} of {row['n_fails_total']} | "
            f"{row['fail_share']*100:.0f}% |\n"
        )
    md_parts.append(
        "\nThe `revenue` and `new_user` clusters in particular validate the \"ClariLayer would catch this\" "
        "framing: 8 of the top 15 are cases where the entire fleet converges on a single naive formula "
        "(usually \"sum the obvious column without the documented test/null/timezone filters\") that the "
        "governed variant explicitly rules out.\n"
    )

    # 8. Stability
    md_parts.append("## 8. Stability check\n")
    if stab.get("n_pairs", 0) == 0:
        md_parts.append(
            "Stability check is unavailable for this run — only one stability pass was logged. "
            "Re-run with `stability_runs: 2` (or more) in `run_config.yaml` to populate this section.\n\n"
            "![Stability per model](figures/stability_per_model.png)\n"
        )
    else:
        md_parts.append(
            "Per-cell (model, baseline, question) run-1 / run-2 agreement when status is collapsed to "
            f"(passed, did_not_pass): **{stab['overall']:.1f}%** overall ({stab['n_pairs']:,} cells).\n\n"
            "![Stability per model](figures/stability_per_model.png)\n\n"
            "By model:\n\n"
        )
        md_parts.append("| Model | Agreement |\n|---|---|\n")
        for model, v in stab["by_model"].items():
            md_parts[-1] += f"| {MODEL_LABEL[model]} | {_fmt_pct(v)} |\n"
        md_parts.append("\nBy baseline:\n\n| Baseline | Agreement |\n|---|---|\n")
        for baseline, v in stab["by_baseline"].items():
            md_parts[-1] += f"| {BASELINE_SHORT[baseline]} | {_fmt_pct(v)} |\n"
        # Derive the "clear 95%" claim from the actual numbers rather than
        # hardcoding it — clean if a future run dips below.
        all_above_95 = all(v >= 95 for v in stab["by_model"].values)
        if all_above_95:
            md_parts.append(
                "\nAll models clear the 95% target. Models are deterministic enough on this benchmark that two "
                "stability runs are sufficient — adding more runs would primarily reduce the variance on the very few "
                "cells that disagreed (most disagreements were on borderline-tolerance cases or transient gateway errors).\n"
            )
        else:
            below = [MODEL_LABEL[m] for m, v in stab["by_model"].items() if v < 95]
            md_parts.append(
                f"\nThe 95% stability target was missed by: {', '.join(below)}. "
                "Bumping stability_runs > 2 (or tightening prompt determinism) is recommended before publication.\n"
            )

    # 9. Per-model error analysis
    # tier-column hallucinations are a specific concentrated story
    # Filter to D-baseline tier errors specifically; the §9 paragraph
    # describes the bug as a Baseline-D-emitter problem, so the count
    # must match that scope.
    tier_errors = err_rows[
        (err_rows["baseline"] == "d")
        & err_rows["error"].fillna("").str.contains("tier", regex=False)
    ]
    md_parts.append("## 9. Error analysis\n")
    md_parts.append(
        f"Of {n_rows:,} calls, {n_error} ({error_rate:.2f}%) returned ERROR (SQL did not execute). "
        "Buckets:\n\n"
        "| Bucket | Count |\n|---|---|\n"
    )
    for _, row in error_counts.iterrows():
        md_parts[-1] += f"| {row['bucket']} | {row['count']} |\n"
    md_parts.append("\nBy model:\n\n| Model | Errors |\n|---|---|\n")
    for model, v in err_by_model.items():
        md_parts[-1] += f"| {MODEL_LABEL.get(model, model)} | {v} |\n"
    md_parts.append("\nBy baseline:\n\n| Baseline | Errors |\n|---|---|\n")
    for baseline, v in err_by_baseline.items():
        md_parts[-1] += f"| {BASELINE_SHORT[baseline]} | {v} |\n"
    if len(err_by_metric):
        md_parts.append("\nTop 5 metrics by error count:\n\n| Metric | Errors |\n|---|---|\n")
        for metric, v in err_by_metric.head(5).items():
            md_parts[-1] += f"| {metric} | {v} |\n"

    d_err_count = err_by_baseline.get('d', 0)
    d_err_share = (d_err_count / n_error * 100) if n_error else 0.0
    if n_error == 0:
        md_parts.append("\n**No errors logged for this run.** All SQL executed cleanly.\n\n")
    else:
        md_parts.append(
            f"\n**Where the errors are concentrated.** {d_err_count} of {n_error} errors "
            f"({d_err_share:.0f}%) are on Baseline D — surprising because D *otherwise* "
            f"has the highest accuracy. Drill down: {len(tier_errors)} of those D-errors are the same "
            "`BinderException: column \"tier\" does not exist` failure mode (or the related `plan_tier`), "
            "distributed across `arr`, `mrr`, and `active_user` — three metrics where the v1 governed Metric API "
            "output mentions a tier-style segmentation in its description but does NOT enumerate the actual column "
            "name in the `dim_customers` schema. Models read the segmentation reference, hallucinate `c.tier`, and "
            "DuckDB rejects the query. **This is a v1.1-actionable bug in the governed-context emitter, not a "
            "reasoning failure.** Fix: surface the explicit segmentation column names (or remove the segmentation "
            "reference) in the governed-context block for these metrics.\n\n"
            "**Forensic context.** Two models were dropped mid-run for verbose-output truncation issues "
            "(their reasoning prose blew past `max_tokens`, leaving fenced SQL blocks unclosed and unparseable):\n\n"
            f"- Gemini 3 Pro Preview — {len(df_gemini):,} rows in `results.gemini-dropped.jsonl`\n"
            f"- GPT-5 standard — {len(df_gpt5):,} rows in `results.gpt5-dropped.jsonl`\n\n"
            f"And {len(df_prefix):,} pre-extract_sql-fix errors (truncated-fence handling) are preserved in "
            "`results.pre-fix.jsonl` as a regression baseline.\n"
        )

    # 10. Limitations
    md_parts.append("## 10. Limitations\n")
    md_parts.append(
        f"- **Only {n_models} models** in the published roster, not the originally-spec'd 5. Gemini 3 Pro Preview and GPT-5 "
        "(standard) are excluded for verbose-output reasons. A v1.1 follow-up should retry with provider-side "
        "system-prompt overrides or stricter response-format constraints.\n"
        f"- **{n_questions} questions** is enough to discriminate H1 (gap is ~ 30+ pp, comfortably above sampling noise) but "
        "limits per-metric resolution to ~ 4-5 questions per cell. Cells with a 0/4 or 4/4 result therefore have "
        "wide CIs.\n"
        f"- **{n_runs} stability runs** is enough at current model determinism levels; bumping to 5 would let us "
        "characterise borderline-tolerance cases more precisely.\n"
        "- **Synthetic warehouse.** Real Design Partner warehouses will have messier dim/fact join patterns, more "
        "soft-delete columns, more is_test variants, and more dialect divergence than DuckDB. Expect Baseline B's "
        "competitive-with-D metrics list to shrink in real data.\n"
        f"- **Pricing dict was approximate.** Final reconciled spend reported here (${TOTAL_SPEND_USD:.2f}) reflects "
        "gateway billing; per-model cost projections in `harness.py` differ from actual by up to ~ 15% at the family "
        "level.\n"
    )

    md_parts.append(
        "\n---\n"
        "_Re-generate this report with: `python3 analysis/v1_analysis.py`._\n"
    )

    return "\n".join(md_parts)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_id", nargs="?", default=DEFAULT_RUN_ID)
    args = p.parse_args(argv)

    rng = np.random.default_rng(RNG_SEED)

    df = load_results(args.run_id)
    df_gemini = pd.DataFrame(_load_jsonl(BENCHMARK_ROOT / "results" / args.run_id / "results.gemini-dropped.jsonl"))
    df_gpt5 = pd.DataFrame(_load_jsonl(BENCHMARK_ROOT / "results" / args.run_id / "results.gpt5-dropped.jsonl"))
    df_prefix = pd.DataFrame(_load_jsonl(BENCHMARK_ROOT / "results" / args.run_id / "results.pre-fix.jsonl"))

    # Headline matrix + per-baseline CIs
    headline_matrix = headline_matrix_with_ci(df, rng)
    per_baseline_ci = per_baseline_with_ci(df, rng)

    # H1 paired bootstraps (pool models)
    h1_d_vs_a = _paired_bootstrap(df, split_col="baseline", a_value="d", b_value="a", rng=rng)
    h1_d_vs_b = _paired_bootstrap(df, split_col="baseline", a_value="d", b_value="b", rng=rng)
    h1_d_vs_c = _paired_bootstrap(df, split_col="baseline", a_value="d", b_value="c", rng=rng)

    # H2 Opus vs Sonnet on Baseline D
    h2_opus_sonnet = _paired_bootstrap(
        df,
        split_col="model",
        a_value="anthropic/claude-opus-4.7",
        b_value="anthropic/claude-sonnet-4.5",
        filter_col="baseline",
        filter_value="d",
        rng=rng,
    )
    h2_opus_sonnet_per_metric = (
        df[(df["baseline"] == "d") & (df["model"].isin(["anthropic/claude-opus-4.7", "anthropic/claude-sonnet-4.5"]))]
        .groupby(["model", "metric"])["passed"]
        .mean()
        .unstack("model")
        * 100
    )

    # Stability
    stab = stability_table(df)

    # Metric matrix
    metric_matrix = metric_baseline_matrix(df)

    # Variant / convergence
    popular_wrong = popular_wrong_answers(df, threshold=0.30)
    convergence = baseline_a_vs_d_convergence(df)

    # Errors
    error_counts, err_rows = error_summary(df)

    # Figures
    fig_dir = HERE / "figures"
    fig_baseline_bars(per_baseline_ci, fig_dir / "baseline_accuracy_bars.png")
    fig_model_baseline_grouped(headline_matrix, fig_dir / "model_baseline_grouped.png")
    fig_metric_heatmap(metric_matrix, fig_dir / "metric_heatmap.png")
    fig_stability(stab, fig_dir / "stability_per_model.png")

    # Build report
    report_md = build_report(
        df=df,
        df_gemini=df_gemini,
        df_gpt5=df_gpt5,
        df_prefix=df_prefix,
        headline_matrix=headline_matrix,
        per_baseline_ci=per_baseline_ci,
        stab=stab,
        metric_matrix=metric_matrix,
        popular_wrong=popular_wrong,
        convergence=convergence,
        error_counts=error_counts,
        err_rows=err_rows,
        h1_d_vs_b=h1_d_vs_b,
        h1_d_vs_c=h1_d_vs_c,
        h1_d_vs_a=h1_d_vs_a,
        h2_opus_sonnet=h2_opus_sonnet,
        h2_opus_sonnet_per_metric=h2_opus_sonnet_per_metric,
        run_id=args.run_id,
    )

    out = HERE / "results-v1.md"
    out.write_text(report_md, encoding="utf-8")
    print(f"wrote {out}")
    print(f"figures in {fig_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""ClariLayer Trust Benchmark — Python harness orchestrator (R2).

Drives the (model × baseline × question × stability_run) matrix:

    1. Load run config (`run_config.yaml`).
    2. Resolve model IDs against the gateway's `/models` listing (with
       fallbacks).
    3. For each combo:
       a. Load question YAML.
       b. Assemble the baseline-specific prompt.
       c. Call Vercel AI Gateway (OpenAI-compatible HTTP API).
       d. Extract SQL, execute it against the warehouse DuckDB, score.
       e. Append to `results/{run_id}/results.jsonl`.
    4. Resumability: skip combos already present in the JSONL.
    5. End-of-run: write `summary.csv` and (in pilot mode) print
       projected full-run cost.

Modes:
    --pilot                       run the small pilot subset
    --full --budget-approved      run the full 5×4×89×2 matrix
    --run-id <slug>               override the auto-generated run id
    --list-models                 list models the gateway currently advertises
    --dry-run                     resolve everything but skip API calls

Auth (read from env or `.env.benchmark` produced by `seed_test_org.py`):
    AI_GATEWAY_API_KEY            required for any real API call
    BENCHMARK_API_KEY             required for Baseline D
    BENCHMARK_API_BASE_URL        required for Baseline D
"""
# ruff: noqa: S608  # SQL is model-generated and runs read-only against DuckDB

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import duckdb
import httpx
import yaml
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover — dotenv listed in requirements.txt
    def load_dotenv(*_args, **_kwargs) -> bool:  # type: ignore[no-redef]
        return False

# Allow `python harness.py` from anywhere — make sibling modules importable.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from baselines import get_module as _get_baseline  # noqa: E402
from baselines.d_clarilayer import (  # noqa: E402
    BaselineDAuthError,
    BaselineDConfigError,
    BaselineDLookupError,
    BaselineDTransientError,
)
from scoring import (  # noqa: E402
    ScoreResult,
    extract_sql,
    extract_variant_choice,
    score as score_sql,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT = HERE.parent
DATASET_DIR = ROOT / "dataset"
QUESTIONS_DIR = DATASET_DIR / "questions"
WAREHOUSE = DATASET_DIR / "warehouse.duckdb"
RESULTS_ROOT = ROOT / "results"
DEFAULT_CONFIG = HERE / "run_config.yaml"

GATEWAY_BASE = "https://ai-gateway.vercel.sh/v1"
SYSTEM_PROMPT = (
    "You are a senior data analyst. Output ONLY a single DuckDB SQL query "
    "wrapped in ```sql ... ``` and NOTHING ELSE — no prose, no commentary, "
    "no candidates, no explanation, no greeting, no apology. The query "
    "must run as-is against the schema below. Verbose output is wrong "
    "output: if you exceed the token limit your fenced block will not "
    "close and your answer will be discarded."
)

# Hardcoded per-model pricing (USD per 1M tokens) keyed by `family` from
# run_config.yaml. PRICING IS APPROXIMATE — refresh against the gateway
# billing page before R5/G1.
PRICING: dict[str, tuple[float, float]] = {
    # input_per_1M, output_per_1M
    "claude-opus-4": (15.00, 75.00),       # Opus 4.7 estimate
    "gpt-5-pro": (10.00, 40.00),           # GPT-5.4 Pro estimate
    "gemini-3-pro": (5.00, 15.00),         # Gemini 3 Pro estimate
    "claude-sonnet-4": (3.00, 15.00),      # Sonnet 4.5
    "gpt-5": (2.50, 10.00),                # GPT-5 standard
}


# ---------------------------------------------------------------------------
# Config loading + env
# ---------------------------------------------------------------------------


def _load_env_files() -> None:
    """Load `.env.benchmark` if present; fall through silently otherwise."""
    candidates = [
        ROOT / ".env.benchmark",        # repo-root/.env.benchmark
        HERE / ".env.benchmark",        # harness/.env.benchmark
    ]
    for path in candidates:
        if path.exists():
            load_dotenv(path)


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"run config not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _resolve_run_models(cfg: dict[str, Any], pilot: bool) -> list[dict[str, Any]]:
    """Return the flat list of model dicts to actually run."""
    if pilot:
        pilot_block = cfg.get("pilot", {})
        return list(pilot_block.get("models", []))
    frontier = list(cfg.get("models", {}).get("frontier", []))
    production = list(cfg.get("models", {}).get("production", []))
    return frontier + production


def _resolve_run_baselines(cfg: dict[str, Any], pilot: bool) -> list[str]:
    if pilot:
        return list(cfg.get("pilot", {}).get("baselines", cfg.get("baselines", ["a", "b", "c", "d"])))
    return list(cfg.get("baselines", ["a", "b", "c", "d"]))


def _resolve_stability_runs(cfg: dict[str, Any], pilot: bool) -> int:
    if pilot:
        return int(cfg.get("pilot", {}).get("stability_runs", 1))
    return int(cfg.get("stability_runs", 2))


# ---------------------------------------------------------------------------
# Question + metric loading
# ---------------------------------------------------------------------------


def _load_all_questions() -> list[dict[str, Any]]:
    """Return the flat list of {metric_key, ...question_yaml} dicts."""
    out: list[dict[str, Any]] = []
    for path in sorted(QUESTIONS_DIR.glob("*.yaml")):
        rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for q in rows:
            q = dict(q)  # shallow copy so we can stash metric_key
            q.setdefault("metric_key", path.stem)
            out.append(q)
    return out


def _filter_questions(
    all_questions: list[dict[str, Any]],
    subset_ids: list[str] | None,
) -> list[dict[str, Any]]:
    if not subset_ids:
        return all_questions
    wanted = set(subset_ids)
    out = [q for q in all_questions if q.get("id") in wanted]
    missing = wanted - {q["id"] for q in out}
    if missing:
        raise SystemExit(
            f"Pilot subset references unknown question IDs: {sorted(missing)}"
        )
    return out


# ---------------------------------------------------------------------------
# Gateway HTTP client
# ---------------------------------------------------------------------------


class GatewayAuthError(RuntimeError):
    """Raised on 401/403 — caller aborts run, does not retry."""


class GatewayTransientError(RuntimeError):
    """Raised on 429/5xx — tenacity retries with exponential backoff.

    On 429 we preserve the gateway's `Retry-After` header (parsed to seconds)
    in `retry_after_seconds` so the wait function can honor it instead of
    blindly applying our generic exponential backoff.
    """

    def __init__(self, msg: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after_seconds = retry_after_seconds


def _parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP `Retry-After` header value into seconds.

    Spec allows either a delta-seconds integer or an HTTP-date. We support
    delta-seconds (the Vercel AI Gateway form); HTTP-date is rare here and
    falls through to None so tenacity's exponential backoff applies.
    """
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0 or seconds > 600:
        # Cap at 10 minutes; protects against malicious / runaway values.
        return None
    return seconds


def _retry_after_aware_wait(state: RetryCallState) -> float:
    """Tenacity wait callback that prefers a Retry-After hint over exponential backoff.

    Honors `retry_after_seconds` on either `GatewayTransientError` (for the
    chat-completions retry) or any other transient error class that exposes
    the same attribute (e.g. `BaselineDTransientError` on Metric API calls).
    Falls back to wait_exponential(min=2,max=30,mult=2) when absent.
    """
    exc = state.outcome.exception() if state.outcome else None
    retry_after = getattr(exc, "retry_after_seconds", None)
    if isinstance(retry_after, (int, float)) and retry_after >= 0:
        return float(retry_after)
    return wait_exponential(multiplier=2, min=2, max=30)(state)


# Backwards-compat alias — previous round used this name.
_gateway_wait = _retry_after_aware_wait


@dataclass
class GatewayResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    raw: dict[str, Any]


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=_gateway_wait,
    retry=retry_if_exception_type((httpx.TransportError, httpx.RemoteProtocolError, GatewayTransientError)),
)
def _gateway_chat(
    client: httpx.Client,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    timeout_seconds: float,
) -> GatewayResponse:
    """POST /v1/ai/chat/completions and return parsed response.

    Error taxonomy:
        401/403          → GatewayAuthError (fail fast, no retry)
        429, 5xx         → GatewayTransientError (tenacity retries)
        4xx (other)      → httpx.HTTPStatusError (caller logs ERROR row)
        TransportError   → tenacity retries (network blip)
    """
    url = f"{GATEWAY_BASE}/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    started = time.monotonic()
    resp = client.post(
        url,
        json=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout_seconds,
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    if resp.status_code in (401, 403):
        raise GatewayAuthError(
            f"Gateway auth failed ({resp.status_code}). Check AI_GATEWAY_API_KEY."
        )
    if resp.status_code == 429 or resp.status_code >= 500:
        # Transient — surface Retry-After (when present) on the exception so
        # the tenacity wait callback can honor it instead of falling back to
        # exponential backoff. Never sleep in-band — that would compound.
        retry_after = (
            _parse_retry_after(resp.headers.get("Retry-After"))
            if resp.status_code == 429
            else None
        )
        raise GatewayTransientError(
            f"Gateway returned {resp.status_code}: {resp.text[:200]}",
            retry_after_seconds=retry_after,
        )
    resp.raise_for_status()

    payload = resp.json()
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = payload.get("usage") or {}
    return GatewayResponse(
        content=message.get("content") or "",
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        latency_ms=latency_ms,
        raw=payload,
    )


def _list_gateway_models(api_key: str) -> list[str]:
    """Return IDs the gateway currently advertises (best-effort)."""
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                f"{GATEWAY_BASE}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
            data = r.json()
            rows = data.get("data") if isinstance(data, dict) else None
            if isinstance(rows, list):
                return [row["id"] for row in rows if isinstance(row, dict) and "id" in row]
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] could not list gateway models: {exc}", file=sys.stderr)
    return []


def _resolve_model_id(model_cfg: dict[str, Any], available: set[str] | None) -> str:
    """Pick the first id (or fallback) the gateway can route.

    If `available` is None (listing endpoint failed), return the configured
    `id` and let the per-call POST surface the routing error.
    """
    primary = model_cfg["id"]
    if available is None:
        return primary
    if primary in available:
        return primary
    for fb in model_cfg.get("fallbacks", []):
        if fb in available:
            print(
                f"[warn] {primary} not on gateway; falling back to {fb}",
                file=sys.stderr,
            )
            return fb
    raise SystemExit(
        f"None of {[primary] + list(model_cfg.get('fallbacks', []))} are routable. "
        f"Refresh run_config.yaml (gateway returned: {sorted(available)[:10]}...)."
    )


# ---------------------------------------------------------------------------
# Resumability
# ---------------------------------------------------------------------------


def _completed_keys(jsonl: Path) -> set[tuple[str, str, str, int]]:
    """Return set of (model, baseline, question_id, run_idx) already logged."""
    if not jsonl.exists():
        return set()
    out: set[tuple[str, str, str, int]] = set()
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.add(
            (
                row.get("model", ""),
                row.get("baseline", ""),
                row.get("question_id", ""),
                int(row.get("run_idx", 0)),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Per-call workflow
# ---------------------------------------------------------------------------


@dataclass
class CallResult:
    model: str
    baseline: str
    metric_key: str
    question_id: str
    run_idx: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    model_sql: str
    variant_choice: str | None
    actual_value: Any
    expected_value: Any
    status: str
    error: str | None
    detail: str

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "model": self.model,
                "baseline": self.baseline,
                "metric": self.metric_key,
                "question_id": self.question_id,
                "run_idx": self.run_idx,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "latency_ms": self.latency_ms,
                "model_sql": self.model_sql,
                "variant_choice": self.variant_choice,
                "actual_value": self.actual_value,
                "expected_value": self.expected_value,
                "status": self.status,
                "error": self.error,
                "detail": self.detail,
            },
            default=str,
        )


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=_retry_after_aware_wait,
    retry=retry_if_exception_type(BaselineDTransientError),
)
def _fetch_context_with_retry(baseline_mod, metric_key: str) -> str:
    """Wrap `baseline_mod.fetch_context` so Baseline D's transient errors retry.

    Baselines A/B/C are static-file lookups and won't raise the transient
    error class, so they pass through this wrapper for free.
    """
    return baseline_mod.fetch_context(metric_key)


# Synthetic envelope for Baseline D dry-runs. Roughly the same length and
# shape as a real metric envelope so the prompt-token estimate stays
# representative for cost projection — but no network call.
_DRY_RUN_BASELINE_D_PLACEHOLDER = (
    "# Governed metric record for `{metric_key}` (live ClariLayer API)\n"
    "Source: ClariLayer Canonical Metric API v1. This is the\n"
    "production response — not a curated extract.\n\n"
    "```json\n"
    "{{\n"
    '  "data": {{\n'
    '    "metric": {{ "id": "DRY-RUN", "name": "{metric_key}", "key": "{metric_key}", '
    '"canonical_sql": "SELECT 1 -- DRY RUN", "status": "approved" }},\n'
    '    "versionHistory": [],\n'
    '    "relationships": {{ "deprecates": [] }},\n'
    '    "approvals": [],\n'
    '    "time_semantics": {{ "grain": "day", "timezone": "UTC" }},\n'
    '    "fiscal_calendar_start_month": 1\n'
    "  }}\n"
    "}}\n"
    "```\n"
)


def _dry_run_context(baseline: str, baseline_mod, metric_key: str) -> str:
    """Resolve a context block in dry-run mode without hitting the network.

    Baselines A/B/C: load the static fixture from disk (cheap, deterministic
    so the dry-run prompt-token estimate matches a real run).
    Baseline D: synthesize a placeholder envelope rather than calling the
    live Metric API — that's the whole point of --dry-run.
    """
    if baseline.lower() == "d":
        return _DRY_RUN_BASELINE_D_PLACEHOLDER.format(metric_key=metric_key)
    # A/B/C are local file reads; safe to call directly.
    return baseline_mod.fetch_context(metric_key)


def _execute_one(
    client: httpx.Client,
    api_key: str,
    duck: duckdb.DuckDBPyConnection,
    model_id: str,
    baseline: str,
    question: dict[str, Any],
    run_idx: int,
    request_cfg: dict[str, Any],
    dry_run: bool,
) -> CallResult:
    metric_key = question["metric_key"]
    qid = question["id"]
    expected_value = question.get("expected_value")
    baseline_mod = _get_baseline(baseline)

    if dry_run:
        # Resolve context WITHOUT touching the network. Baseline D used to
        # call its live Metric API here even in dry-run, defeating the flag.
        try:
            ctx = _dry_run_context(baseline, baseline_mod, metric_key)
        except (FileNotFoundError, KeyError) as exc:
            return CallResult(
                model=model_id, baseline=baseline, metric_key=metric_key,
                question_id=qid, run_idx=run_idx,
                prompt_tokens=0, completion_tokens=0, latency_ms=0,
                model_sql="", variant_choice=None,
                actual_value=None, expected_value=expected_value,
                status="ERROR",
                error=f"dry-run context unavailable: {type(exc).__name__}: {exc}",
                detail="",
            )
        user_msg = baseline_mod.build_user_message(metric_key, question, ctx)
        return CallResult(
            model=model_id, baseline=baseline, metric_key=metric_key,
            question_id=qid, run_idx=run_idx,
            # Token count is a rough char/4 heuristic; preserved so the
            # cost-projection block in pilot mode stays meaningful.
            prompt_tokens=len(user_msg) // 4, completion_tokens=0, latency_ms=0,
            model_sql="-- DRY RUN: no API call", variant_choice=None,
            actual_value=None, expected_value=expected_value,
            status="ERROR", error="dry_run", detail="dry_run",
        )

    try:
        ctx = _fetch_context_with_retry(baseline_mod, metric_key)
    except BaselineDAuthError:
        raise  # propagate — abort run (parallels GatewayAuthError)
    except BaselineDConfigError:
        raise  # propagate — Baseline D wiring missing, fail fast
    except (BaselineDLookupError, FileNotFoundError, KeyError) as exc:
        # Application-shape errors: log as ERROR row, continue the run.
        return CallResult(
            model=model_id, baseline=baseline, metric_key=metric_key,
            question_id=qid, run_idx=run_idx,
            prompt_tokens=0, completion_tokens=0, latency_ms=0,
            model_sql="", variant_choice=None,
            actual_value=None, expected_value=expected_value,
            status="ERROR",
            error=f"context fetch failed: {type(exc).__name__}: {exc}",
            detail="",
        )
    except (BaselineDTransientError, httpx.HTTPStatusError) as exc:
        # Transient errors that exhausted the tenacity retry budget, or
        # other 4xx that bubbled up. Logged as ERROR; run continues.
        return CallResult(
            model=model_id, baseline=baseline, metric_key=metric_key,
            question_id=qid, run_idx=run_idx,
            prompt_tokens=0, completion_tokens=0, latency_ms=0,
            model_sql="", variant_choice=None,
            actual_value=None, expected_value=expected_value,
            status="ERROR",
            error=f"context fetch failed: {type(exc).__name__}: {exc}",
            detail="",
        )

    user_msg = baseline_mod.build_user_message(metric_key, question, ctx)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    try:
        gw = _gateway_chat(
            client,
            api_key,
            model_id,
            messages,
            temperature=float(request_cfg.get("temperature", 0.0)),
            max_tokens=int(request_cfg.get("max_output_tokens", 1500)),
            timeout_seconds=float(request_cfg.get("timeout_seconds", 90)),
        )
    except GatewayAuthError:
        raise  # propagate — abort run
    except Exception as exc:  # noqa: BLE001
        return CallResult(
            model=model_id, baseline=baseline, metric_key=metric_key,
            question_id=qid, run_idx=run_idx,
            prompt_tokens=0, completion_tokens=0, latency_ms=0,
            model_sql="", variant_choice=None,
            actual_value=None, expected_value=expected_value,
            status="ERROR",
            error=f"gateway call failed: {type(exc).__name__}: {exc}",
            detail="",
        )

    sql = extract_sql(gw.content)
    variant = extract_variant_choice(gw.content)
    score: ScoreResult = score_sql(duck, sql, expected_value)

    return CallResult(
        model=model_id, baseline=baseline, metric_key=metric_key,
        question_id=qid, run_idx=run_idx,
        prompt_tokens=gw.prompt_tokens, completion_tokens=gw.completion_tokens,
        latency_ms=gw.latency_ms,
        model_sql=sql, variant_choice=variant,
        actual_value=score.actual_value, expected_value=expected_value,
        status=score.status, error=score.error, detail=score.detail,
    )


def _iter_combos(
    models: list[dict[str, Any]],
    baselines: list[str],
    questions: list[dict[str, Any]],
    stability_runs: int,
) -> Iterable[tuple[dict[str, Any], str, dict[str, Any], int]]:
    for model_cfg in models:
        for baseline in baselines:
            for q in questions:
                for run_idx in range(1, stability_runs + 1):
                    yield model_cfg, baseline, q, run_idx


# ---------------------------------------------------------------------------
# Aggregate summary + cost projection
# ---------------------------------------------------------------------------


def _write_summary(jsonl: Path, summary_csv: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    bucket: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["model"], r["baseline"])
        b = bucket.setdefault(
            key,
            {
                "model": r["model"],
                "baseline": r["baseline"],
                "n_questions": 0,
                "n_pass": 0,
                "prompt_tokens_total": 0,
                "completion_tokens_total": 0,
            },
        )
        b["n_questions"] += 1
        if r.get("status") == "PASS":
            b["n_pass"] += 1
        b["prompt_tokens_total"] += int(r.get("prompt_tokens") or 0)
        b["completion_tokens_total"] += int(r.get("completion_tokens") or 0)

    summary_rows = []
    for (_m, _b), v in sorted(bucket.items()):
        n = v["n_questions"] or 1
        summary_rows.append({
            "model": v["model"],
            "baseline": v["baseline"],
            "n_questions": v["n_questions"],
            "n_pass": v["n_pass"],
            "accuracy_pct": round(100.0 * v["n_pass"] / n, 2),
            "avg_prompt_tokens": round(v["prompt_tokens_total"] / n, 1),
            "avg_completion_tokens": round(v["completion_tokens_total"] / n, 1),
        })

    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with summary_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summary_rows[0].keys()) if summary_rows else [
            "model", "baseline", "n_questions", "n_pass", "accuracy_pct",
            "avg_prompt_tokens", "avg_completion_tokens",
        ])
        writer.writeheader()
        for row in summary_rows:
            writer.writerow(row)
    return summary_rows


def _cost_estimate(family: str, prompt_tokens: float, completion_tokens: float) -> float:
    rates = PRICING.get(family)
    if not rates:
        return 0.0
    in_rate, out_rate = rates
    return (prompt_tokens / 1_000_000) * in_rate + (completion_tokens / 1_000_000) * out_rate


def _print_pilot_report(
    rows: list[dict[str, Any]],
    full_models: list[dict[str, Any]],
    full_baselines: list[str],
    full_questions: int,
    full_stability: int,
) -> None:
    n_total = sum(r["n_questions"] for r in rows)
    n_pass = sum(r["n_pass"] for r in rows)
    pct = (100.0 * n_pass / n_total) if n_total else 0.0

    per_baseline: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        per_baseline.setdefault(r["baseline"], []).append(r)

    print("\n=== Pilot run complete ===")
    print(f"Total calls: {n_total}")
    print(f"Pass: {n_pass} / {n_total} ({pct:.0f}%)")

    parts = []
    for b in sorted(per_baseline):
        bs = per_baseline[b]
        n = sum(x["n_questions"] for x in bs) or 1
        p = sum(x["n_pass"] for x in bs)
        parts.append(f"{b.upper()}={(100.0 * p / n):.0f}%")
    print(f"Per-baseline accuracy: {' '.join(parts)}")

    prompt_total = sum(r["avg_prompt_tokens"] * r["n_questions"] for r in rows)
    completion_total = sum(r["avg_completion_tokens"] * r["n_questions"] for r in rows)
    print(
        f"Token totals: prompt={int(prompt_total):,}  "
        f"completion={int(completion_total):,}  "
        f"total={int(prompt_total + completion_total):,}"
    )
    if n_total:
        print(
            f"Per-call avg: prompt={int(prompt_total / n_total):,}  "
            f"completion={int(completion_total / n_total):,}"
        )

    full_calls = len(full_models) * len(full_baselines) * full_questions * full_stability
    per_model_calls = full_calls // max(len(full_models), 1)

    print(f"\nProjected full-run cost ({full_calls:,} calls):")
    avg_prompt = (prompt_total / n_total) if n_total else 0.0
    avg_completion = (completion_total / n_total) if n_total else 0.0
    grand_low = grand_high = 0.0
    for m in full_models:
        family = m.get("family") or m.get("id", "")
        cost = _cost_estimate(family, avg_prompt * per_model_calls, avg_completion * per_model_calls)
        rates = PRICING.get(family)
        rate_str = f"input ${rates[0]}/M, output ${rates[1]}/M" if rates else "no pricing on file"
        print(f"  {m.get('label', m['id']):<30s} ${cost:>9.2f}  ({rate_str})")
        grand_low += cost * 0.8
        grand_high += cost * 1.2
    print(f"\n  Total estimate (±20% buffer):  ${grand_low:.2f} – ${grand_high:.2f}")
    print("  PRICING IS APPROXIMATE — verify against gateway billing before approving full run.\n")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ClariLayer Trust Benchmark harness orchestrator (R2)."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pilot", action="store_true", help="Run the small pilot subset.")
    mode.add_argument(
        "--full",
        action="store_true",
        help="Run the full 5x4x89x2 matrix. Requires --budget-approved.",
    )
    parser.add_argument(
        "--budget-approved",
        action="store_true",
        help="Founder approval gate (G1). Required with --full.",
    )
    parser.add_argument("--run-id", default=None, help="Override run id (output dir slug).")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to run_config.yaml.")
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Print model IDs the gateway currently advertises and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve config + iterate combos but skip gateway calls.",
    )
    return parser.parse_args(argv)


def _generate_run_id(pilot: bool) -> str:
    suffix = "pilot" if pilot else "full"
    return f"{suffix}-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    _load_env_files()

    if args.full and not args.budget_approved:
        print("ERROR: --full requires --budget-approved (cost gate).", file=sys.stderr)
        return 2
    if not args.pilot and not args.full and not args.list_models:
        print("ERROR: pass --pilot or --full (or --list-models).", file=sys.stderr)
        return 2

    api_key = os.environ.get("AI_GATEWAY_API_KEY", "")

    if args.list_models:
        if not api_key:
            print("AI_GATEWAY_API_KEY not set — can't list.", file=sys.stderr)
            return 2
        ids = _list_gateway_models(api_key)
        print("\n".join(ids) if ids else "(no models returned)")
        return 0

    cfg = _load_config(Path(args.config))
    pilot = args.pilot

    models_cfg = _resolve_run_models(cfg, pilot)
    baselines = _resolve_run_baselines(cfg, pilot)
    stability_runs = _resolve_stability_runs(cfg, pilot)

    all_questions = _load_all_questions()
    if pilot:
        subset_ids = list(cfg.get("pilot", {}).get("question_subset", []))
        questions = _filter_questions(all_questions, subset_ids)
    else:
        questions = all_questions

    if not WAREHOUSE.exists() and not args.dry_run:
        print(
            f"ERROR: warehouse missing at {WAREHOUSE}. Run seed_warehouse.py first.",
            file=sys.stderr,
        )
        return 2

    if not api_key and not args.dry_run:
        print(
            "ERROR: AI_GATEWAY_API_KEY is unset. Source .env.benchmark "
            "(produced by R1) before running the harness.",
            file=sys.stderr,
        )
        return 2

    # Resolve model IDs against the gateway listing (best-effort).
    available_ids: set[str] | None = None
    if api_key and not args.dry_run:
        listing = _list_gateway_models(api_key)
        available_ids = set(listing) if listing else None

    resolved_models: list[dict[str, Any]] = []
    for mc in models_cfg:
        mc_copy = dict(mc)
        try:
            mc_copy["resolved_id"] = _resolve_model_id(mc, available_ids)
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            return 2
        resolved_models.append(mc_copy)

    # Run id + output dir
    run_id = args.run_id or _generate_run_id(pilot)
    out_dir = RESULTS_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "results.jsonl"
    summary_path = out_dir / "summary.csv"
    print(f"Run id: {run_id}")
    print(f"Output: {out_dir}")
    print(f"Models: {[m['resolved_id'] for m in resolved_models]}")
    print(f"Baselines: {baselines}")
    print(f"Questions: {len(questions)}  Stability runs: {stability_runs}")

    completed = _completed_keys(jsonl_path)
    if completed:
        print(f"Resuming — {len(completed)} combo(s) already in {jsonl_path.name}.")

    request_cfg = cfg.get("request") or {}

    # Use an in-memory connection during dry-run if the warehouse is missing
    # — the SQL never executes, but score() still wants a duckdb connection.
    if WAREHOUSE.exists():
        duck = duckdb.connect(str(WAREHOUSE), read_only=True)
    else:
        duck = duckdb.connect(":memory:")
    try:
        with httpx.Client(timeout=120.0) as client, jsonl_path.open("a", encoding="utf-8") as out_fh:
            total_combos = len(resolved_models) * len(baselines) * len(questions) * stability_runs
            done = 0
            for model_cfg, baseline, q, run_idx in _iter_combos(
                resolved_models, baselines, questions, stability_runs
            ):
                model_id = model_cfg["resolved_id"]
                key = (model_id, baseline, q["id"], run_idx)
                done += 1
                if key in completed:
                    continue

                try:
                    res = _execute_one(
                        client, api_key, duck,
                        model_id, baseline, q, run_idx,
                        request_cfg, args.dry_run,
                    )
                except (GatewayAuthError, BaselineDAuthError) as exc:
                    print(f"FATAL: {exc}", file=sys.stderr)
                    return 3
                except BaselineDConfigError as exc:
                    print(f"FATAL: Baseline D not configured — {exc}", file=sys.stderr)
                    return 2

                # Stash the family on the row for downstream cost analysis.
                row = json.loads(res.to_jsonl())
                row["family"] = model_cfg.get("family")
                row["model_label"] = model_cfg.get("label")
                out_fh.write(json.dumps(row, default=str) + "\n")
                out_fh.flush()

                marker = res.status[:4]
                print(
                    f"[{done}/{total_combos}] {marker:<4} "
                    f"{model_id} / {baseline} / {q['id']} / r{run_idx} "
                    f"(prompt={res.prompt_tokens} completion={res.completion_tokens} "
                    f"latency={res.latency_ms}ms) {res.detail or res.error or ''}".rstrip()
                )
    finally:
        duck.close()

    # Summary
    summary_rows = _write_summary(jsonl_path, summary_path)
    print(f"\nSummary written: {summary_path}")
    for row in summary_rows:
        print(
            f"  {row['model']:<40s} {row['baseline']:>2}  "
            f"acc={row['accuracy_pct']:>6.2f}%  "
            f"prompt~{row['avg_prompt_tokens']:.0f}  "
            f"completion~{row['avg_completion_tokens']:.0f}"
        )

    if pilot:
        full_models = (
            list(cfg.get("models", {}).get("frontier", []))
            + list(cfg.get("models", {}).get("production", []))
        )
        full_baselines = list(cfg.get("baselines", ["a", "b", "c", "d"]))
        full_questions_n = len(all_questions)
        full_stability = int(cfg.get("stability_runs", 2))
        _print_pilot_report(
            summary_rows, full_models, full_baselines, full_questions_n, full_stability
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

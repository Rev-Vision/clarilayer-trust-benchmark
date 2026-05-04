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

Auth (read from env or `.env.benchmark` produced by R1):
    AI_GATEWAY_API_KEY            required for any real API call
    BENCHMARK_API_KEY             required for Baseline E (ClariLayer)
    BENCHMARK_API_BASE_URL        required for Baseline E (ClariLayer)
"""
# ruff: noqa: S608  # SQL is model-generated and runs read-only against DuckDB

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
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
from baselines.e_clarilayer import (  # noqa: E402
    BaselineEAuthError,
    BaselineEConfigError,
    BaselineELookupError,
    BaselineETransientError,
)
from scoring import (  # noqa: E402
    ScoreResult,
    extract_sql,
    extract_variant_choice,
    score as score_sql,
)
from scoring_text import score_approval  # noqa: E402


# ---------------------------------------------------------------------------
# v2.1-F1: Structured JSON response parser
# ---------------------------------------------------------------------------
#
# The v2.1 response contract asks the model for a JSON object of the form
#   {warnings: [str, ...], clarification_request: str|null, sql: str,
#    rationale: str}
# We accept either a bare JSON object OR a fenced ```json ... ``` block (some
# models reflexively wrap their JSON in markdown despite being told not to).
#
# Backward compatibility: if JSON parse fails entirely, callers fall back to
# `extract_sql()` (fenced-code-block / bare-text extraction) for the `sql`
# field, while warnings / clarification_request / rationale default to
# empty / None / empty string respectively. This keeps the harness robust
# against models that ignore the contract.

_JSON_FENCE_BLOCK = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE
)


def _parse_response_contract(text: str) -> dict[str, Any] | None:
    """Try to parse the v2.1 structured response contract.

    Returns the parsed dict on success, or None on any parse failure.
    Tolerates a leading / trailing prose wrapper around the JSON, and
    accidental markdown fences. Does NOT validate the schema beyond
    "is it a JSON object" — that's the caller's job (via the per-field
    extractors below) so we degrade gracefully on partial responses.
    """
    if not text or not text.strip():
        return None
    raw = text.strip()
    # 1) Fast path: the whole response IS the JSON object.
    if raw.startswith("{") and raw.endswith("}"):
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
    # 2) Fenced ```json ... ``` block.
    m = _JSON_FENCE_BLOCK.search(raw)
    if m:
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
    # 3) Last resort: greedy first { ... last }. This is fragile against
    #    SQL strings that contain { } themselves, but we only fall back here
    #    when the model wrapped a clean object in extra prose.
    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last > first:
        try:
            obj = json.loads(raw[first : last + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _coerce_warnings(value: Any) -> list[str]:
    """Coerce the warnings field into a list[str]. Tolerates wrong shapes.

    Models occasionally return `null`, a single string, or a dict where we
    expect an array. We coerce: strings become single-element lists, dicts
    become their stringified values, anything else becomes [].

    Filtering rule: `None` items and whitespace-only strings are dropped.
    Since `warnings` now feeds positive governance signal downstream
    (score_drift counts non-empty warnings as a rejection signal), blank
    entries can't be allowed to produce false `flagged` /
    `canonical-with-rejection` labels. Non-empty strings are stripped of
    surrounding whitespace and kept.
    """
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            stripped = str(item).strip()
            if stripped:
                out.append(stripped)
        return out
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        out_d: list[str] = []
        for v in value.values():
            if v is None:
                continue
            stripped = str(v).strip()
            if stripped:
                out_d.append(stripped)
        return out_d
    return []


def _coerce_optional_str(value: Any) -> str | None:
    """Coerce optional-string fields. Empty strings collapse to None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    return str(value)


def _coerce_str(value: Any) -> str:
    """Coerce string fields with empty-string default."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _is_valid_sql_value(value: Any) -> bool:
    """Return True iff `value` is a non-empty, non-whitespace-only string.

    Used by `parse_model_response` to gate the JSON `sql` field. When a model
    emits something like `{"sql": []}`, `{"sql": null}`, or
    `{"clarification_request": "...", "sql": "  "}`, we must treat the field
    as MISSING — not stringify it (`str([]) == "[]"`) or fall through
    `extract_sql()` on the JSON-stringified payload, both of which produce
    non-executable junk that the harness then tries to run as SQL.
    """
    return isinstance(value, str) and bool(value.strip())


# Fenced-block-only SQL extraction. Distinct from `scoring.extract_sql()`
# (which has a third fallback that treats the whole text as SQL). Used by
# `parse_model_response` when the JSON `sql` field is invalid: we want to
# recover SQL the model put in a fenced block alongside the JSON envelope,
# but we DO NOT want to fall through to the bare-text path because the
# text is the JSON envelope itself, not SQL.
_FENCED_SQL_BLOCK_RE = re.compile(
    r"```sql\s*(.+?)(?:```|$)", re.IGNORECASE | re.DOTALL
)
_FENCED_BARE_BLOCK_RE = re.compile(r"```\s*(.+?)(?:```|$)", re.DOTALL)


def _extract_fenced_sql_block(text: str) -> str:
    """Extract SQL from a fenced ```sql ... ``` (or bare ``` ... ```) block.

    Returns empty string if no fenced block is found. Unlike
    `scoring.extract_sql()`, never falls back to treating the whole text
    as SQL — this matters when the input text is a JSON envelope and we
    must NOT run JSON noise as SQL.
    """
    if not text:
        return ""
    m = _FENCED_SQL_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _FENCED_BARE_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    return ""


@dataclass
class ParsedResponse:
    """Decomposed view of a model response under the v2.1 contract.

    Always set:
      - sql:                    extracted SQL (parsed JSON `sql` field, or
                                fenced-code-block fallback). Empty string
                                only when neither path yielded anything.
      - response_json:          the parsed JSON object, or None if parse failed.
      - warnings:               list[str], possibly empty.
      - clarification_request:  str or None.
      - rationale:              str, possibly empty.
    """
    sql: str
    response_json: dict[str, Any] | None
    warnings: list[str]
    clarification_request: str | None
    rationale: str


def parse_model_response(text: str) -> ParsedResponse:
    """Decompose a raw model response per the v2.1 structured contract.

    Behavior:
      1. Try to parse the response as the v2.1 JSON contract object.
      2. If parse succeeds: extract `sql`, `warnings`, `clarification_request`,
         `rationale` with defensive coercion. If `sql` is missing or empty
         in the parsed object, fall back to fenced-code-block extraction
         on the raw text so we never lose SQL we could otherwise execute.
      3. If parse fails: set `response_json=None`, extract `sql` via
         `extract_sql()` (legacy fenced-code-block path), and default the
         other fields to empty/None.

    This is the central backward-compat shim — the numeric scorer keeps
    seeing `model_sql` exactly as it did under v2.0, and the Drift scorer
    gets richer signal when the model honors the contract.
    """
    raw = text or ""
    parsed = _parse_response_contract(raw)
    if parsed is not None:
        sql_raw = parsed.get("sql")
        if _is_valid_sql_value(sql_raw):
            sql_field = sql_raw.strip()
        else:
            # Invalid `sql` in JSON (list / None / empty / whitespace-only).
            # Treat as MISSING and fall back to fenced-code-block extraction
            # on the original raw text — never on the JSON-stringified
            # payload (which would produce junk like `str([]) == "[]"`)
            # nor via `extract_sql()` (which would treat the JSON envelope
            # itself as SQL when no fenced block is present).
            sql_field = _extract_fenced_sql_block(raw)
        return ParsedResponse(
            sql=sql_field,
            response_json=parsed,
            warnings=_coerce_warnings(parsed.get("warnings")),
            clarification_request=_coerce_optional_str(
                parsed.get("clarification_request")
            ),
            rationale=_coerce_str(parsed.get("rationale")),
        )
    # Parse failed entirely — backward-compat fenced-code-block path.
    return ParsedResponse(
        sql=extract_sql(raw),
        response_json=None,
        warnings=[],
        clarification_request=None,
        rationale="",
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
# v2.1-F1 (prose-with-SQL contract): the system prompt asks for a single
# JSON object matching the structured response contract documented in
# `RESPONSE_CONTRACT.md`. The user message (built per-baseline) restates
# the schema + field semantics. The `sql` field is REQUIRED and must
# always be populated with executable DuckDB SQL — `warnings` and
# `clarification_request` are governance signal, not an excuse to omit
# SQL. This replaces the v2.0 "SQL only, no prose" directive that
# mechanically suppressed Drift / Approval / Versioning signal.
SYSTEM_PROMPT = (
    "You are a senior data analyst. Respond with a single JSON object "
    "matching this schema:\n"
    '{"warnings": [string, ...], "clarification_request": string|null, '
    '"sql": string, "rationale": string}\n'
    "**The `sql` field is REQUIRED and must always be a non-null, "
    "executable DuckDB SQL query.**\n\n"
    "**`warnings` MUST default to an empty array `[]`.** ONLY populate "
    "warnings when the metric envelope contains an EXPLICIT governance "
    "signal that fires for the user's question — concretely: a "
    "`deprecated_framing_rules[*]` whose trigger_patterns match the "
    "user's framing, a non-null `approval_state` whose policy directs you "
    "to surface its status, a `requires_disambiguation` whose policy is "
    "`answer-with-default-scope-and-disclose` and the user did not "
    "specify scope, or a `consumer_contexts` entry whose pinning differs "
    "from the canonical version. Do NOT populate warnings for generic "
    "schema concerns (ambiguous columns, legacy aliases, NULL-prone "
    "fields, etc.) — those are NOT governance signals. If the metric "
    "envelope is missing or has no triggering governance fields, "
    "warnings MUST be `[]`.\n\n"
    "**`clarification_request` MUST default to `null`.** ONLY populate it "
    "when `requires_disambiguation.policy == 'ask-clarification-first'` "
    "and the question is missing required scope. Even then, you must "
    "still produce a non-null `sql` using `default_scope`.\n\n"
    "**`rationale` is optional and SHOULD BE EMPTY** unless you made a "
    "specific scope or version choice that affects the SQL. Keep it to "
    "one short sentence at most.\n\n"
    "Output the JSON object only — no surrounding prose, no markdown "
    "fences. Verbose output is wrong output: empty `warnings`/`null` "
    "`clarification_request`/empty `rationale` is the correct shape for "
    "the vast majority of calls."
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
        ROOT / ".env.benchmark",        # benchmark/.env.benchmark
        HERE / ".env.benchmark",        # benchmark/scripts/.env.benchmark
        ROOT.parent / ".env.benchmark", # repo-root/.env.benchmark
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
        return list(cfg.get("pilot", {}).get("baselines", cfg.get("baselines", ["a", "b", "c", "d", "e"])))
    return list(cfg.get("baselines", ["a", "b", "c", "d", "e"]))


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
    the same attribute (e.g. `BaselineETransientError` on Metric API calls).
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
    # v2 additions (B4.1):
    #   - rubric_lane: which scoring path was used ("numeric" | "approval" | "drift")
    #   - category: question category for downstream summarisation
    #   - model_text: full gateway response content. Required by the Drift
    #     auto-label step (calibrate_drift_judge.py) which needs the raw
    #     candidate answer to feed into the LLM-as-judge. Numeric/Approval
    #     callers can ignore this field.
    rubric_lane: str = "numeric"
    category: str | None = None
    model_text: str = ""
    # v2.1-F1 additions (prose-with-SQL contract):
    #   - model_response_json: parsed v2.1 contract object, or None if the
    #     model returned malformed JSON. Lets downstream tooling distinguish
    #     "honored the contract" from "fell back to fenced-code-block".
    #   - model_warnings: array of governance / scope warnings.
    #   - model_clarification_request: clarification text or None.
    #   - model_rationale: brief rationale (one-line per contract).
    # The Drift judge consumes warnings + clarification_request as positive
    # signal for `flagged` / `canonical-with-rejection` labels.
    model_response_json: dict[str, Any] | None = None
    model_warnings: list[str] = field(default_factory=list)
    model_clarification_request: str | None = None
    model_rationale: str = ""

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
                "rubric_lane": self.rubric_lane,
                "category": self.category,
                "model_text": self.model_text,
                "model_response_json": self.model_response_json,
                "model_warnings": self.model_warnings,
                "model_clarification_request": self.model_clarification_request,
                "model_rationale": self.model_rationale,
            },
            default=str,
        )


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=_retry_after_aware_wait,
    retry=retry_if_exception_type(BaselineETransientError),
)
def _fetch_context_with_retry(baseline_mod, metric_key: str) -> str:
    """Wrap `baseline_mod.fetch_context` so Baseline E's transient errors retry.

    Baselines A/B/C/D are static-file lookups and won't raise the transient
    error class, so they pass through this wrapper for free.
    """
    return baseline_mod.fetch_context(metric_key)


# Synthetic envelope for Baseline E dry-runs. Roughly the same length and
# shape as a real metric envelope so the prompt-token estimate stays
# representative for cost projection — but no network call.
_DRY_RUN_BASELINE_E_PLACEHOLDER = (
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

    Baselines A/B/C/D: load the static fixture from disk (cheap, deterministic
    so the dry-run prompt-token estimate matches a real run).
    Baseline E: synthesize a placeholder envelope rather than calling the
    live Metric API — that's the whole point of --dry-run.
    """
    if baseline.lower() == "e":
        return _DRY_RUN_BASELINE_E_PLACEHOLDER.format(metric_key=metric_key)
    # A/B/C/D are local file reads; safe to call directly.
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
        # Resolve context WITHOUT touching the network. Baseline E used to
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
    except BaselineEAuthError:
        raise  # propagate — abort run (parallels GatewayAuthError)
    except BaselineEConfigError:
        raise  # propagate — Baseline E wiring missing, fail fast
    except (BaselineELookupError, FileNotFoundError, KeyError) as exc:
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
    except (BaselineETransientError, httpx.HTTPStatusError) as exc:
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

    # v2.1-F1: parse the structured-JSON response contract (with graceful
    # fenced-code-block fallback for malformed JSON). `parsed.sql` is the
    # canonical SQL field — use it for execution / scoring, keeping the raw
    # `model_text` for backward-compat diff'ing against v2.0 datasets.
    parsed = parse_model_response(gw.content or "")
    sql = parsed.sql
    variant = extract_variant_choice(gw.content)

    # v2 B4.1: dispatch on rubric_lane.
    # - numeric  → score_sql against the warehouse (v1 path).
    # - approval → score_approval (deterministic substring + alias match).
    # - drift    → defer judge call. The harness persists the raw model_text
    #              and a placeholder status; calibrate_drift_judge.py + the
    #              auto-label step in B4.1 consume model_text to populate
    #              the calibration set, after which the judge can be locked
    #              and applied to all Drift rows.
    rubric_lane = (question.get("rubric_lane") or "numeric").lower()
    category = question.get("category")
    if rubric_lane == "approval":
        text_score = score_approval(question, gw.content or "")
        score = ScoreResult(
            status=text_score.status,
            actual_value=text_score.actual_value,
            error=text_score.error,
            detail=text_score.detail,
        )
    elif rubric_lane == "drift":
        # Calibration not yet locked — judge runs in B4.1 phase 3 separately.
        # Persist model_text + structured fields so the auto-labeller has
        # the full response and the v2.1 governance signal.
        score = ScoreResult(
            status="DEFER_JUDGE",
            actual_value=None,
            error=None,
            detail="drift judge not yet calibrated; raw text persisted for offline labelling",
        )
    else:
        score = score_sql(duck, sql, expected_value)

    return CallResult(
        model=model_id, baseline=baseline, metric_key=metric_key,
        question_id=qid, run_idx=run_idx,
        prompt_tokens=gw.prompt_tokens, completion_tokens=gw.completion_tokens,
        latency_ms=gw.latency_ms,
        model_sql=sql, variant_choice=variant,
        actual_value=score.actual_value, expected_value=expected_value,
        status=score.status, error=score.error, detail=score.detail,
        rubric_lane=rubric_lane, category=category,
        model_text=gw.content or "",
        model_response_json=parsed.response_json,
        model_warnings=parsed.warnings,
        model_clarification_request=parsed.clarification_request,
        model_rationale=parsed.rationale,
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
        print("ERROR: --full requires --budget-approved (G1 founder approval gate).", file=sys.stderr)
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
                except (GatewayAuthError, BaselineEAuthError) as exc:
                    print(f"FATAL: {exc}", file=sys.stderr)
                    return 3
                except BaselineEConfigError as exc:
                    print(f"FATAL: Baseline E not configured — {exc}", file=sys.stderr)
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
        full_baselines = list(cfg.get("baselines", ["a", "b", "c", "d", "e"]))
        full_questions_n = len(all_questions)
        full_stability = int(cfg.get("stability_runs", 2))
        _print_pilot_report(
            summary_rows, full_models, full_baselines, full_questions_n, full_stability
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

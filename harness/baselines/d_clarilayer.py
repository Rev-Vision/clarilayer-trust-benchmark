"""Baseline D — live ClariLayer Canonical Metric API.

Per spec §3.1, Baseline D MUST hit the production Metric API at run-time
— not a mocked JSON or hand-curated shape. This module:

1. Hits `GET {api_base}/api/v1/metrics?q=<metric_key>` to find the metric ID
   in the seeded test org (created by R1 / `seed_test_org.py`).
2. Hits `GET {api_base}/api/v1/metrics/{id}` to fetch the full envelope:
   metric body, deprecated versions (versionHistory + relationships),
   approvals, time semantics, fiscal calendar.
3. Renders that envelope as a structured prompt context.

Error taxonomy (parallels the gateway client in harness.py):
- `BaselineDConfigError`     — env vars missing. Fail fast at run start.
- `BaselineDAuthError`       — 401/403 from the Metric API. Fail fast;
                               does NOT downgrade to per-row ERROR (that
                               would silently fail every Baseline D call
                               for a whole run).
- `BaselineDTransientError`  — 429/5xx/timeouts/transport blips. Retried
                               by the harness's tenacity policy (alongside
                               gateway transients).
- `BaselineDLookupError`     — metric not found in seeded org (i.e.
                               application-shape error). Surfaces as ERROR
                               row so the rest of the run continues.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


class BaselineDConfigError(RuntimeError):
    """Raised when env vars or wiring for Baseline D are missing."""


class BaselineDAuthError(RuntimeError):
    """Raised on 401/403 — caller aborts run, does not retry."""


class BaselineDTransientError(RuntimeError):
    """Raised on 429/5xx/transport errors — tenacity retries.

    On 429 we surface the Metric API's `Retry-After` header (parsed to
    seconds) so the harness's wait callback can honor it; falls back to
    exponential backoff when absent.
    """

    def __init__(self, msg: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after_seconds = retry_after_seconds


class BaselineDLookupError(LookupError):
    """Raised when a seeded metric_key is missing — application-shape error."""


_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Per-metric cache for a single harness run — avoids hammering the test org
# during stability_runs > 1 or when the same metric appears in multiple
# questions. Cleared between full and pilot runs.
_CACHE: dict[str, str] = {}
# Lazy-built `metric_key -> id` map. Populated on first Baseline D call by
# listing governed metrics (lifecycle_status=APPROVED) and fetching each
# detail (the list endpoint omits `metric.key`; only the detail surfaces it).
_KEY_TO_ID: dict[str, str] | None = None


def _config() -> tuple[str, str]:
    base = (os.environ.get("BENCHMARK_API_BASE_URL") or "").rstrip("/")
    key = os.environ.get("BENCHMARK_API_KEY", "")
    if not base:
        raise BaselineDConfigError(
            "BENCHMARK_API_BASE_URL is unset — Baseline D requires the live "
            "ClariLayer Metric API. Source `.env.benchmark` (produced by R1) "
            "before running the harness."
        )
    if not key:
        raise BaselineDConfigError(
            "BENCHMARK_API_KEY is unset — Baseline D requires a metrics:read "
            "API key. Source `.env.benchmark` (produced by R1) before running "
            "the harness."
        )
    return base, key


def _http_get(client: httpx.Client, url: str, *, key: str) -> dict[str, Any]:
    """GET `url` with Bearer auth, mapped to typed errors.

    401/403          → BaselineDAuthError      (fail fast, no retry)
    429, 5xx         → BaselineDTransientError (tenacity retries)
    Transport errors → BaselineDTransientError (network blip, retried)
    other 4xx        → httpx.HTTPStatusError   (caller logs ERROR row)
    """
    try:
        resp = client.get(url, headers={"Authorization": f"Bearer {key}"})
    except (httpx.TransportError, httpx.RemoteProtocolError) as exc:
        raise BaselineDTransientError(
            f"Metric API transport error for {url}: {type(exc).__name__}: {exc}"
        ) from exc

    if resp.status_code in (401, 403):
        raise BaselineDAuthError(
            f"Metric API auth failed ({resp.status_code}) for {url}. "
            f"Check BENCHMARK_API_KEY."
        )
    if resp.status_code == 429 or resp.status_code >= 500:
        retry_after: float | None = None
        if resp.status_code == 429:
            raw = resp.headers.get("Retry-After")
            if raw:
                try:
                    parsed = float(raw.strip())
                except (TypeError, ValueError):
                    parsed = None
                if parsed is not None and 0 <= parsed <= 600:
                    retry_after = parsed
        raise BaselineDTransientError(
            f"Metric API returned {resp.status_code} for {url}: {resp.text[:200]}",
            retry_after_seconds=retry_after,
        )
    resp.raise_for_status()
    return resp.json()


def _build_key_to_id_map(client: httpx.Client, base: str, api_key: str) -> dict[str, str]:
    """List governed metrics and resolve each to its `key` via the detail endpoint.

    The list endpoint (`/api/v1/metrics`) does NOT surface `metric.key` — that
    field is None on every row in the response. Only the detail endpoint
    (`/api/v1/metrics/{id}`) returns it. So the resolver lists governed
    (`lifecycle_status=APPROVED` returns exactly the 20 governed metrics
    R1 seeded) and walks each one for its key.

    20 detail calls at startup; cached for the rest of the run.
    """
    url = f"{base}/api/v1/metrics?lifecycle_status=APPROVED&pageSize=100"
    payload = _http_get(client, url, key=api_key)
    out: dict[str, str] = {}
    for row in payload.get("data", []):
        mid = row.get("id")
        if not mid:
            continue
        detail_url = f"{base}/api/v1/metrics/{mid}"
        detail = _http_get(client, detail_url, key=api_key)
        # Detail wraps the metric body under `data.metric` (or sometimes
        # `metric` at the top level depending on serializer version).
        body = detail.get("data") or detail
        metric = body.get("metric") if isinstance(body, dict) else None
        if not isinstance(metric, dict):
            metric = body if isinstance(body, dict) else {}
        mk = metric.get("key")
        if isinstance(mk, str) and mk:
            out[mk] = mid
    return out


def _find_metric_id(client: httpx.Client, base: str, key: str, metric_key: str) -> str:
    """Resolve the seeded metric's UUID from its `key` (e.g. 'arr').

    Uses a module-level cache populated on first call so we only pay the
    20-detail-fetch startup cost once per harness run.
    """
    global _KEY_TO_ID
    if _KEY_TO_ID is None:
        _KEY_TO_ID = _build_key_to_id_map(client, base, key)
    mid = _KEY_TO_ID.get(metric_key)
    if mid:
        return mid
    raise BaselineDLookupError(
        f"Baseline D: no governed metric with key={metric_key!r} found in "
        f"seeded test org ({base}). Available governed keys: "
        f"{sorted(_KEY_TO_ID.keys())[:10]}... (total {len(_KEY_TO_ID)})"
    )


def fetch_context(metric_key: str, _cfg=None) -> str:
    """Fetch the live governed-metric envelope and serialize to a prompt block."""
    if metric_key in _CACHE:
        return _CACHE[metric_key]

    base, api_key = _config()
    with httpx.Client(timeout=_DEFAULT_TIMEOUT) as client:
        metric_id = _find_metric_id(client, base, api_key, metric_key)
        detail_url = f"{base}/api/v1/metrics/{metric_id}"
        envelope = _http_get(client, detail_url, key=api_key)

    rendered = _render_envelope(metric_key, envelope)
    _CACHE[metric_key] = rendered
    return rendered


def _render_envelope(metric_key: str, payload: dict[str, Any]) -> str:
    """Render the v1 metric-detail envelope as plain-text prompt context.

    We deliberately serialize the live API JSON shape rather than a
    summarized form — Baseline D's contract per spec §3.1 is "what the
    Metric API actually returns." The harness prepends a brief header so
    the model knows what it's reading.
    """
    data = payload.get("data") if "data" in payload else payload
    # Some envelopes nest under {data: {...}}, others return the
    # MetricDetailV1Response shape directly. Handle both.
    if isinstance(data, dict) and "metric" in data:
        body = data
    else:
        body = payload

    return (
        f"# Governed metric record for `{metric_key}` (live ClariLayer API)\n"
        "Source: ClariLayer Canonical Metric API v1. This is the\n"
        "production response — not a curated extract.\n\n"
        f"```json\n{json.dumps(body, indent=2, default=str)}\n```\n"
    )


def build_user_message(metric_key: str, question: dict, ctx: str) -> str:
    return (
        "You are answering a SQL question against a DuckDB warehouse\n"
        "using a **governed metric record** fetched live from the\n"
        "ClariLayer Canonical Metric API. The JSON below is the\n"
        "production response — including the canonical SQL template,\n"
        "deprecated prior versions (and why they were retired), time\n"
        "logic (fiscal calendar, timezone, grain), canonical filters,\n"
        "owner, lifecycle status, and approvals. Treat the canonical\n"
        "definition as authoritative — the deprecated versions are\n"
        "*known wrong* and the governance metadata exists to disambiguate.\n\n"
        f"=== METRIC ENVELOPE ===\n{ctx.strip()}\n=== END METRIC ENVELOPE ===\n\n"
        f"Question: {question['question']}\n\n"
        "Respond with a single SQL query that answers the question using\n"
        "the canonical (non-deprecated) governed definition. The query\n"
        "must run against DuckDB. Wrap the SQL in a fenced code block:\n"
        "```sql\nSELECT ...\n```\n"
        "Return only the SQL. No prose, no commentary."
    )

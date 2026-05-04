#!/usr/bin/env python3
"""Resolve `DEFER_JUDGE` rows in a v2 stability-run results.jsonl.

The harness emits `status=DEFER_JUDGE` for every Drift-lane row because the
LLM-as-judge call was held until calibration was locked (per spec §3.5 /
codex F10). After calibration locks the judge model + prompt + temperature,
this script fans the deferred rows through `scoring_text.score_drift()`
using the same gateway client pattern the calibration harness uses.

Usage
-----
    python3 benchmark/scripts/resolve_deferred_drift.py \
        --results benchmark/results/v2-stability-1/results.jsonl

The script:
    1. Reads results.jsonl, indexes the rows by line.
    2. Loads drift question metadata (canonical_answer_value /
       deprecated_answer_value / canonical_answer_sql) from the YAMLs.
    3. For each row with status==DEFER_JUDGE:
         - Builds question dict with canonical/deprecated metadata.
         - Calls score_drift() with a gateway-backed JudgeClient.
         - Updates the row's status (PASS/FAIL/ERROR), actual_value
           (= judge label), and detail (= "label: rationale").
    4. Writes results.jsonl back atomically.
    5. Emits a small label-distribution + cost summary to stdout.

No changes to harness or scoring logic — this is a strict post-processor.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_a, **_k):  # type: ignore[no-redef]
        return False

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUESTIONS_DIR = ROOT / "dataset" / "questions"
sys.path.insert(0, str(HERE))

# Load .env.benchmark before importing scoring_text (which doesn't itself
# read env, but the gateway call below needs AI_GATEWAY_API_KEY).
for p in (
    ROOT / ".env.benchmark",
    HERE / ".env.benchmark",
    ROOT.parent / ".env.benchmark",
):
    if p.exists():
        load_dotenv(p)

from scoring_text import (  # noqa: E402
    JUDGE_MAX_TOKENS,
    JUDGE_MODEL,
    JUDGE_TEMPERATURE,
    score_drift,
)

GATEWAY_BASE = "https://ai-gateway.vercel.sh/v1"


def _load_drift_questions_by_id() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(QUESTIONS_DIR.glob("drift-*.yaml")):
        rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for q in rows:
            qid = q.get("id")
            if qid:
                out[qid] = q
    return out


def _build_judge_client(api_key: str, *, timeout: float = 60.0):
    """Return a JudgeClient closure that calls the Vercel AI Gateway.

    Tracks (calls, prompt_tokens, completion_tokens) on the closure
    object so the caller can compute cost.
    """
    stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def _client(
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        url = f"{GATEWAY_BASE}/chat/completions"
        body = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        resp.raise_for_status()
        payload = resp.json()
        usage = payload.get("usage") or {}
        stats["calls"] += 1
        stats["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        stats["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        choice = (payload.get("choices") or [{}])[0]
        return ((choice.get("message") or {}).get("content") or "")

    _client.stats = stats  # type: ignore[attr-defined]
    return _client


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--results",
        type=Path,
        required=True,
        help="Path to results.jsonl produced by harness.py.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional: only process the first N DEFER_JUDGE rows (debug).",
    )
    args = ap.parse_args()

    if not args.results.exists():
        print(f"ERROR: {args.results} not found", file=sys.stderr)
        return 2

    api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
    if not api_key:
        print(
            "ERROR: AI_GATEWAY_API_KEY unset. Source .env.benchmark.",
            file=sys.stderr,
        )
        return 2

    questions_by_id = _load_drift_questions_by_id()
    if not questions_by_id:
        print(
            "ERROR: no drift-*.yaml questions loaded — check QUESTIONS_DIR",
            file=sys.stderr,
        )
        return 2

    rows: list[dict[str, Any]] = []
    with args.results.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    defer_indices = [
        i for i, r in enumerate(rows) if r.get("status") == "DEFER_JUDGE"
    ]
    if not defer_indices:
        print("No DEFER_JUDGE rows found — nothing to do.")
        return 0

    if args.limit is not None:
        defer_indices = defer_indices[: args.limit]

    print(
        f"Resolving {len(defer_indices)} DEFER_JUDGE rows via "
        f"{JUDGE_MODEL} @ T={JUDGE_TEMPERATURE} (locked)…"
    )

    judge = _build_judge_client(api_key)
    label_counter: dict[str, int] = {}
    status_counter: dict[str, int] = {}
    errors: list[tuple[str, str]] = []

    t0 = time.time()
    for n, idx in enumerate(defer_indices, start=1):
        row = rows[idx]
        qid = row.get("question_id")
        question_meta = questions_by_id.get(qid)
        if not question_meta:
            row["status"] = "ERROR"
            row["error"] = f"judge resolver: unknown drift question_id: {qid!r}"
            row["detail"] = (row.get("detail") or "") + " | missing-question-metadata"
            status_counter["ERROR"] = status_counter.get("ERROR", 0) + 1
            errors.append((qid or "?", "unknown drift question_id"))
            continue
        # Build the question dict score_drift() expects. We need a
        # `question` text + canonical / deprecated metadata. Pull text
        # from the YAML (the harness doesn't echo the prompt back).
        q = {
            "question": question_meta.get("question") or "",
            "canonical_answer_value": question_meta.get("canonical_answer_value"),
            "deprecated_answer_value": question_meta.get("deprecated_answer_value"),
            "canonical_answer_sql": question_meta.get("canonical_answer_sql"),
        }
        response = row.get("model_text") or row.get("model_sql") or ""
        # v2.1-F1 (prose-with-SQL contract): forward the harness-parsed
        # warnings + clarification_request through to score_drift so the
        # judge can credit explicit governance signal as evidence for
        # `flagged` / `canonical-with-rejection`. Default to [] / None
        # for legacy v2.0 JSONLs that predate F1 (the column is absent
        # → row.get() returns None → we coerce to safe defaults that
        # match score_drift's own contract).
        # CodeRabbit defense-in-depth: malformed rows could carry a
        # non-list `model_warnings` (e.g. a string from an upstream
        # tool that flattened the array) or a non-string
        # `model_clarification_request`. Coerce both to safe shapes
        # before forwarding to score_drift so the judge prompt body is
        # always well-typed.
        raw_warnings = row.get("model_warnings")
        if isinstance(raw_warnings, list):
            warnings = [str(w) for w in raw_warnings if w is not None]
        else:
            warnings = []
        raw_clarif = row.get("model_clarification_request")
        if raw_clarif is None or isinstance(raw_clarif, str):
            clarification_request = raw_clarif
        else:
            clarification_request = str(raw_clarif)
        try:
            res = score_drift(
                q,
                response,
                judge,
                warnings=warnings,
                clarification_request=clarification_request,
            )
        except Exception as exc:  # noqa: BLE001
            res = None
            errors.append((qid or "?", f"{type(exc).__name__}: {exc}"))
            row["status"] = "ERROR"
            row["error"] = f"judge resolver: {type(exc).__name__}: {exc}"
            row["detail"] = (row.get("detail") or "") + " | resolver-failed"
            status_counter["ERROR"] = status_counter.get("ERROR", 0) + 1
            continue

        row["status"] = res.status
        # res.actual_value carries the judge label (PASS/FAIL path) when
        # the judge returned a parseable label; ERROR path leaves it None.
        row["actual_value"] = res.actual_value
        row["detail"] = res.detail or row.get("detail") or ""
        row["error"] = res.error
        status_counter[res.status] = status_counter.get(res.status, 0) + 1
        label = res.actual_value
        if isinstance(label, str):
            label_counter[label] = label_counter.get(label, 0) + 1

        if n % 30 == 0:
            elapsed = time.time() - t0
            print(
                f"  …{n}/{len(defer_indices)} "
                f"({elapsed:.1f}s, ~{elapsed/n:.2f}s/call)"
            )

    elapsed_total = time.time() - t0

    # Atomic write: tmp file then rename.
    tmp = args.results.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(args.results)

    stats = judge.stats  # type: ignore[attr-defined]
    # Sonnet 4.5 pricing (per million tokens)
    sonnet_in = 3.0
    sonnet_out = 15.0
    cost = (
        stats["prompt_tokens"] * sonnet_in / 1e6
        + stats["completion_tokens"] * sonnet_out / 1e6
    )

    print()
    print(f"Resolved {len(defer_indices)} rows in {elapsed_total:.1f}s.")
    print(f"  judge calls:      {stats['calls']}")
    print(f"  prompt tokens:    {stats['prompt_tokens']:,}")
    print(f"  completion tokens:{stats['completion_tokens']:,}")
    print(f"  judge cost (est): ${cost:.2f}")
    print()
    print("Status distribution after resolution:")
    for s, c in sorted(status_counter.items()):
        print(f"  {s}: {c}")
    print("Drift label distribution:")
    for lab, c in sorted(label_counter.items()):
        print(f"  {lab}: {c}")
    if errors:
        print(f"Errors ({len(errors)}):")
        for qid, msg in errors[:10]:
            print(f"  {qid}: {msg}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

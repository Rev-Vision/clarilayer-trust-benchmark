#!/usr/bin/env python3
"""Drift judge calibration harness — locks the LLM-as-judge before runs.

Per `brain/gtm/benchmark-v2-spec.md` §3.5 / §6.13 (codex F10), the Drift
judge **must** be calibrated against a hand-labeled set with ≥85%
per-class agreement BEFORE any v2 run uses it. This script:

1. Loads `benchmark/dataset/judge_calibration_set.json`. Refuses to run
   if the entries array is empty (the file ships as a placeholder; B4 /
   founder is responsible for populating it).
2. Calls the judge once per entry with the locked prompt + config from
   `scoring_text.py`. The judge call is fully blinded — only the
   question + answer text reach the judge, never baseline / model.
3. Computes:
       - overall agreement rate (judge label == primary human label)
       - per-class confusion matrix (rows = human, cols = judge)
       - per-class agreement (recall) for each of the 4 labels
4. Writes a markdown report to
   `benchmark/results/judge_calibration_<UTC-date>.md`.
5. Exits non-zero if overall agreement < 85% — the gate that locks the
   judge before B4.

Usage:
    python3 benchmark/scripts/calibrate_drift_judge.py
    python3 benchmark/scripts/calibrate_drift_judge.py --threshold 0.90
    python3 benchmark/scripts/calibrate_drift_judge.py --dry-run

`--dry-run` runs everything *except* the judge calls and is intended for
verifying the placeholder format without burning gateway tokens.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover — dotenv listed in requirements.txt
    def load_dotenv(*_args, **_kwargs) -> bool:  # type: ignore[no-redef]
        return False

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUESTIONS_DIR = ROOT / "dataset" / "questions"
sys.path.insert(0, str(HERE))


def _load_env_files() -> None:
    """Load `.env.benchmark` if present; mirrors harness.py's loader."""
    candidates = [
        ROOT / ".env.benchmark",
        HERE / ".env.benchmark",
        ROOT.parent / ".env.benchmark",
    ]
    for p in candidates:
        if p.exists():
            load_dotenv(p)

from scoring_text import (  # noqa: E402
    DRIFT_LABELS,
    JUDGE_MAX_TOKENS,
    JUDGE_MODEL,
    JUDGE_TEMPERATURE,
    _build_blinded_user_prompt,
    _load_judge_prompt,
    _parse_judge_output,
)

CALIBRATION_SET_PATH = ROOT / "dataset" / "judge_calibration_set.json"
RESULTS_DIR = ROOT / "results"
DEFAULT_THRESHOLD = 0.85
GATEWAY_BASE = "https://ai-gateway.vercel.sh/v1"


def _load_drift_questions_by_id() -> dict[str, dict[str, Any]]:
    """Load all drift question YAMLs and index by question id.

    Used by the calibration loop to look up `canonical_answer_value`,
    `deprecated_answer_value`, and `canonical_answer_sql` for each
    calibration entry's source question — so the locked v2 judge prompt
    can apply its value/definition-comparison decision step.

    Calibration entries that don't carry `source_pilot_row.question_id`
    (older format) get the empty-metadata fallback (canonical_value =
    None, deprecated_value = None, canonical_sql = None) — the judge
    then falls back to v1-style text-only classification for that row.
    """
    out: dict[str, dict[str, Any]] = {}
    if not QUESTIONS_DIR.exists():
        return out
    for path in sorted(QUESTIONS_DIR.glob("drift-*.yaml")):
        rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for q in rows:
            qid = q.get("id")
            if qid:
                out[qid] = q
    return out


def _entry_question_metadata(
    entry: dict[str, Any],
    questions_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Pick out the canonical/deprecated metadata for a calibration entry.

    Resolution order (v2.1 schema → v2.0 schema → empty fallback):
      1. The entry itself carries `canonical_answer_value` /
         `deprecated_answer_value` / `canonical_answer_sql` (the v2.1
         hand-authored format embeds these so the calibration set is
         self-contained and re-runnable without the questions dir).
      2. The legacy auto-labeled v2.0 entries point at a question via
         `source_pilot_row.question_id`; we look that up in the YAML
         registry.
      3. New v2.1 entries also accept a top-level `question_id` (no
         pilot-row wrapper) as a YAML lookup key.
      4. If none of the above, return all-None and the judge prompt
         falls back to its v1-style text-only classification.
    """
    # v2.1 self-contained shape: prefer fields embedded directly on the entry.
    # Require ALL embedded keys to be present before short-circuiting the YAML
    # lookup. A partially-populated row (e.g. has canonical_answer_value but
    # missing canonical_answer_sql) would otherwise bypass YAML resolution and
    # silently return incomplete metadata, weakening the judge's value/
    # definition comparison path.
    embedded_keys = (
        "canonical_answer_value",
        "deprecated_answer_value",
        "canonical_answer_sql",
    )
    if all(k in entry for k in embedded_keys):
        return {k: entry.get(k) for k in embedded_keys}

    # v2.0 / v2.1 YAML-lookup fallback.
    qid = (entry.get("source_pilot_row") or {}).get("question_id") or entry.get(
        "question_id"
    )
    q = questions_by_id.get(qid) if qid else None
    if not q:
        return {k: None for k in embedded_keys}
    return {
        "canonical_answer_value": q.get("canonical_answer_value"),
        "deprecated_answer_value": q.get("deprecated_answer_value"),
        "canonical_answer_sql": q.get("canonical_answer_sql"),
    }


def _load_calibration_set(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"calibration set not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise SystemExit(
            f"calibration set malformed: 'entries' must be a list. Got "
            f"{type(entries).__name__} in {path}"
        )
    return entries


def _validate_entries(entries: list[dict[str, Any]]) -> None:
    """Per-row validation. Refuses to run on empty / malformed sets."""
    if not entries:
        raise SystemExit(
            "PLACEHOLDER: calibration set is empty. The Drift judge MUST be "
            "calibrated against ≥30 hand-labeled responses (≥85% agreement) "
            "before any v2 run. Populate "
            f"{CALIBRATION_SET_PATH.name} with entries matching the format "
            "documented in its _format block, then re-run this script."
        )
    if len(entries) < 30:
        raise SystemExit(
            f"calibration set has only {len(entries)} entries; spec requires "
            "≥30 (recommended 50). Add more before locking the judge."
        )
    required = {"id", "question", "response", "human_label"}
    for i, e in enumerate(entries):
        missing = required - set(e.keys())
        if missing:
            raise SystemExit(
                f"calibration entry {i} (id={e.get('id')!r}) missing fields: {sorted(missing)}"
            )
        if e["human_label"] not in DRIFT_LABELS:
            raise SystemExit(
                f"calibration entry {e.get('id')!r} has invalid human_label "
                f"{e['human_label']!r} — must be one of {DRIFT_LABELS}"
            )


def _call_judge(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    *,
    timeout: float = 60.0,
) -> str:
    """POST one judge call to the Vercel AI Gateway. Returns raw text."""
    url = f"{GATEWAY_BASE}/chat/completions"
    body = {
        "model": JUDGE_MODEL,
        "temperature": JUDGE_TEMPERATURE,
        "max_tokens": JUDGE_MAX_TOKENS,
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
    return ((payload.get("choices") or [{}])[0].get("message") or {}).get(
        "content", ""
    ) or ""


def _confusion_matrix(
    entries: list[dict[str, Any]], judge_labels: list[str | None]
) -> dict[str, dict[str, int]]:
    """rows=human, cols=judge. Includes a synthetic 'parse_error' col."""
    matrix: dict[str, dict[str, int]] = {
        h: {j: 0 for j in (*DRIFT_LABELS, "parse_error")} for h in DRIFT_LABELS
    }
    if len(entries) != len(judge_labels):
        raise ValueError(
            f"entry/judge_label length mismatch: {len(entries)} vs {len(judge_labels)}"
        )
    for e, jl in zip(entries, judge_labels):
        h = e["human_label"]
        col = jl if jl in DRIFT_LABELS else "parse_error"
        matrix[h][col] += 1
    return matrix


def _per_class_agreement(matrix: dict[str, dict[str, int]]) -> dict[str, float]:
    """Recall: judge_label == human_label / total of that human_label."""
    out: dict[str, float] = {}
    for h, row in matrix.items():
        total = sum(row.values())
        if total == 0:
            out[h] = float("nan")
        else:
            out[h] = row.get(h, 0) / total
    return out


def _format_markdown(
    *,
    threshold: float,
    overall: float,
    per_class: dict[str, float],
    matrix: dict[str, dict[str, int]],
    n_entries: int,
    n_parse_errors: int,
    timestamp_utc: str,
) -> str:
    lines: list[str] = []
    lines.append(f"# Drift Judge Calibration Report — {timestamp_utc}")
    lines.append("")
    lines.append(
        f"- **Calibration entries:** {n_entries}\n"
        f"- **Locked judge model:** `{JUDGE_MODEL}`\n"
        f"- **Locked temperature:** {JUDGE_TEMPERATURE}\n"
        f"- **Locked prompt path:** `benchmark/scripts/scoring_text_judge_prompt.md`\n"
        f"- **Agreement threshold:** ≥{threshold:.0%} (per spec §3.5 / codex F10)\n"
        f"- **Parse errors:** {n_parse_errors}\n"
    )
    lines.append(f"## Overall agreement: **{overall:.2%}**")
    if overall >= threshold:
        lines.append("")
        lines.append(f"PASS — agreement {overall:.2%} ≥ {threshold:.0%}; judge locked.")
    else:
        lines.append("")
        lines.append(
            f"FAIL — agreement {overall:.2%} < {threshold:.0%}. Iterate the prompt "
            "(this BUMPS the lock version) and re-run before B4."
        )
    lines.append("")
    lines.append("## Per-class agreement (recall)")
    lines.append("")
    lines.append("| Human label | Recall | n |")
    lines.append("|---|---|---|")
    for label in DRIFT_LABELS:
        row_total = sum(matrix[label].values())
        recall = per_class[label]
        recall_s = f"{recall:.2%}" if recall == recall else "n/a"  # NaN check
        lines.append(f"| `{label}` | {recall_s} | {row_total} |")
    lines.append("")
    lines.append("## Confusion matrix (rows = human, cols = judge)")
    lines.append("")
    cols = list(DRIFT_LABELS) + ["parse_error"]
    header = "| Human \\ Judge | " + " | ".join(f"`{c}`" for c in cols) + " |"
    sep = "|---|" + "---|" * len(cols)
    lines.append(header)
    lines.append(sep)
    for human in DRIFT_LABELS:
        row = matrix[human]
        cells = " | ".join(str(row[c]) for c in cols)
        lines.append(f"| `{human}` | {cells} |")
    lines.append("")
    lines.append("## Sensitivity-analysis note (post-runs)")
    lines.append(
        "\nThe v2 paper additionally reports Drift accuracy on the top "
        "10% of (question × baseline × model) tuples where Drift judge "
        "labels disagree most across stability-run replays. That subset "
        "is hand-adjudicated by the founder; this calibration report "
        "covers only the pre-run lock gate."
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Calibrate the Drift LLM-as-judge against a hand-labeled set."
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Agreement threshold (default {DEFAULT_THRESHOLD}).",
    )
    p.add_argument(
        "--calibration-set",
        type=Path,
        default=CALIBRATION_SET_PATH,
        help="Path to judge_calibration_set.json.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate format only — skip judge calls; exit 0 if format ok.",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    _load_env_files()
    entries = _load_calibration_set(args.calibration_set)
    _validate_entries(entries)

    if args.dry_run:
        print(
            f"[dry-run] {args.calibration_set.name}: {len(entries)} entries, "
            f"format ok (no judge calls made)."
        )
        return 0

    api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
    if not api_key:
        raise SystemExit(
            "AI_GATEWAY_API_KEY is unset. Source `.env.benchmark` (produced by R1) "
            "before running calibration."
        )

    system_prompt, _user_template = _load_judge_prompt()
    questions_by_id = _load_drift_questions_by_id()

    judge_labels: list[str | None] = []
    parse_errors = 0
    for i, e in enumerate(entries, start=1):
        meta = _entry_question_metadata(e, questions_by_id)
        # v2.1 (V2.1-PRE-B): pass the candidate's parsed `model_warnings` and
        # `model_clarification_request` into the prompt so the judge sees the
        # same shape it will see at run-time (mirrors PRE-A's resolver patch).
        # v2.0 entries that don't carry these fields default to [] / None and
        # the prompt body renders "(none)" / "null" — backward compatible.
        # Defensive coercion: malformed inputs (e.g. a string where a list is
        # expected) would otherwise corrupt the prompt rendering silently.
        raw_warnings = e.get("model_warnings")
        if isinstance(raw_warnings, list):
            warnings = [str(w) for w in raw_warnings if w is not None]
        else:
            warnings = []
        raw_clarif = e.get("model_clarification_request")
        if raw_clarif is None or isinstance(raw_clarif, str):
            clarification_request = raw_clarif
        else:
            clarification_request = str(raw_clarif)
        user_prompt = _build_blinded_user_prompt(
            e["question"],
            e["response"],
            canonical_value=meta["canonical_answer_value"],
            deprecated_value=meta["deprecated_answer_value"],
            canonical_sql=meta["canonical_answer_sql"],
            warnings=warnings,
            clarification_request=clarification_request,
        )
        try:
            raw = _call_judge(api_key, system_prompt, user_prompt)
            label, _rationale = _parse_judge_output(raw)
            judge_labels.append(label)
        except (ValueError, httpx.HTTPError) as exc:
            print(f"[warn] entry {e.get('id', i)}: judge parse failed ({exc!s})", file=sys.stderr)
            judge_labels.append(None)
            parse_errors += 1

    matrix = _confusion_matrix(entries, judge_labels)
    per_class = _per_class_agreement(matrix)
    n = len(entries)
    if len(entries) != len(judge_labels):
        raise ValueError(
            f"entry/judge_label length mismatch: {len(entries)} vs {len(judge_labels)}"
        )
    n_correct = sum(
        1 for e, jl in zip(entries, judge_labels) if jl == e["human_label"]
    )
    overall = n_correct / n if n else 0.0

    timestamp_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = _format_markdown(
        threshold=args.threshold,
        overall=overall,
        per_class=per_class,
        matrix=matrix,
        n_entries=n,
        n_parse_errors=parse_errors,
        timestamp_utc=timestamp_utc,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"judge_calibration_{dt.date.today().isoformat()}.md"
    out.write_text(report, encoding="utf-8")
    print(f"calibration report: {out}")
    print(f"overall agreement: {overall:.2%}  (threshold {args.threshold:.0%})")
    if overall < args.threshold:
        print("FAIL — judge is NOT locked. Iterate prompt + re-run.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

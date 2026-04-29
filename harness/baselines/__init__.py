"""Baseline-specific prompt assemblers for the Trust Benchmark harness.

Each module defines `build_user_message(metric_key, question, ctx) -> str`,
returning the user-message body the harness sends to the gateway alongside
the shared system prompt. Baseline D additionally exposes
`fetch_context(metric_key)` which performs the live API call.
"""

from functools import lru_cache
from pathlib import Path

__all__ = ["a_raw", "b_documented", "c_cube", "d_clarilayer", "read_context_file"]


@lru_cache(maxsize=None)
def read_context_file(path: str) -> str:
    """Read a baseline-A/B/C context file from disk, cached for the harness run.

    Context blocks are static fixtures committed under
    `context-blocks/baseline-{a,b,c}/<metric>.{sql,yaml}`. With
    3,560 calls in the full matrix and 22 metrics, each file is otherwise
    re-read ~80 times. Cached for clarity, not throughput — file I/O is
    dwarfed by gateway latency.
    """
    return Path(path).read_text(encoding="utf-8")


# Submodules import `read_context_file` from this package, so they must
# load AFTER the helper is defined.
from . import a_raw, b_documented, c_cube, d_clarilayer  # noqa: E402


def get_module(baseline: str):
    """Return the baseline module for the given key (a / b / c / d)."""
    mapping = {
        "a": a_raw,
        "b": b_documented,
        "c": c_cube,
        "d": d_clarilayer,
    }
    key = baseline.lower()
    if key not in mapping:
        raise ValueError(f"Unknown baseline {baseline!r}; expected one of {sorted(mapping)}")
    return mapping[key]

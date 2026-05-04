"""Baseline-specific prompt assemblers for the Trust Benchmark harness.

Each module defines `build_user_message(metric_key, question, ctx) -> str`,
returning the user-message body the harness sends to the gateway alongside
the shared system prompt. The ClariLayer baseline additionally exposes
`fetch_context(metric_key)` which performs the live API call.

Baseline-key conventions (v2 spec §3.1, §6.1, §6.11)
====================================================

The v2 spec locks the baseline labels to A/B/C/D/E::

    a -> a_raw          (bare schema)
    b -> b_documented   (documented schema)
    c -> c_cube         (Cube semantic layer — expert-graded by B1-C)
    d -> d_dbt          (dbt MetricFlow Semantic Layer — added greenfield by B1-D)
    e -> e_clarilayer   (ClariLayer governed context — was v1's Baseline D)

The B0.5 renumbering reconciled v1's pattern (where ``d`` mapped to
ClariLayer) to the v2 spec. The v1 published numbers under
`benchmark/publication/trust-benchmark-v1.md` keep the original
D=ClariLayer labelling; v2 reconciles via crosswalk per spec §3.1.
"""

from functools import lru_cache
from pathlib import Path

__all__ = ["a_raw", "b_documented", "c_cube", "d_dbt", "e_clarilayer", "read_context_file"]


@lru_cache(maxsize=None)
def read_context_file(path: str) -> str:
    """Read a baseline-A/B/C/D context file from disk, cached for the harness run.

    Context blocks are static fixtures committed under
    `benchmark/context-blocks/baseline-{a,b,c,d}/<metric>.{sql,yaml}`. With
    3,560 calls in the full matrix and 22 metrics, each file is otherwise
    re-read ~80 times. Cached for clarity, not throughput — file I/O is
    dwarfed by gateway latency.
    """
    return Path(path).read_text(encoding="utf-8")


# Submodules import `read_context_file` from this package, so they must
# load AFTER the helper is defined.
from . import a_raw, b_documented, c_cube, d_dbt, e_clarilayer  # noqa: E402


def get_module(baseline: str):
    """Return the baseline module for the given key.

    Recognized keys (v2 spec §3.1):
      - ``a`` -> a_raw          (bare DDL)
      - ``b`` -> b_documented   (documented DDL)
      - ``c`` -> c_cube         (Cube semantic layer)
      - ``d`` -> d_dbt          (dbt MetricFlow Semantic Layer)
      - ``e`` -> e_clarilayer   (ClariLayer governed context)
    """
    mapping = {
        "a": a_raw,
        "b": b_documented,
        "c": c_cube,
        "d": d_dbt,
        "e": e_clarilayer,
    }
    key = baseline.lower()
    if key not in mapping:
        raise ValueError(f"Unknown baseline {baseline!r}; expected one of {sorted(mapping)}")
    return mapping[key]

"""
ClariLayer Trust Benchmark — question validator.

Reads each `dataset/questions/*.yaml`, executes every question's
`expected_sql` against `dataset/warehouse.duckdb`, and prints results
so the author can confirm `expected_value` matches what's committed.

Usage:
    python harness/validate_questions.py
    python harness/validate_questions.py arr.yaml customer.yaml
"""
# ruff: noqa: S608  # all SQL is read from author-controlled fixture YAMLs

from __future__ import annotations

import math
import numbers
import sys
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE = ROOT / "dataset" / "warehouse.duckdb"
QUESTIONS_DIR = ROOT / "dataset" / "questions"

TOLERANCE = 1e-6  # exact for ints; ≤0.1% for floats per spec §3.4.2


def values_match(actual, expected) -> tuple[bool, str]:
    """Compare actual vs expected per spec §3.4.2.

    - bool: strict equality.
    - int (and not bool): exact equality after numeric coercion. e.g.
      expected=100 with actual=100.09 → False (no fuzz on integer answers).
    - float: relative diff ≤ 0.001 (0.1%); special-case expected==0 with
      absolute tolerance TOLERANCE so we don't divide by zero.
    - non-numeric: fall back to ``actual == expected``.
    """
    if expected is None:
        return False, "expected_value is None"
    if actual is None:
        return False, "actual is NULL"
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual == expected, ""
    # Integer answers must be exact — apply only float fuzz to floats.
    if isinstance(expected, int):
        # Compare integers directly to preserve precision for large
        # values (>2^53 loses bits when coerced through float).
        if isinstance(actual, numbers.Integral) and not isinstance(actual, bool):
            return int(actual) == expected, ""
        try:
            a = float(actual)
        except (TypeError, ValueError):
            return actual == expected, ""
        # Reject non-integer-valued actuals (e.g. 100.09 vs expected=100).
        if not math.isfinite(a) or not a.is_integer():
            return False, f"expected int, got {actual!r}"
        return int(a) == expected, ""
    try:
        a = float(actual)
        e = float(expected)
    except (TypeError, ValueError):
        return actual == expected, ""
    if math.isnan(e) and math.isnan(a):
        return True, ""
    if e == 0:
        return abs(a) < TOLERANCE, ""
    rel = abs(a - e) / abs(e)
    return rel <= 0.001, f"rel_diff={rel:.4%}"


def run_one(con, sql: str):
    rows = con.execute(sql).fetchall()
    if not rows:
        return None
    first_row = rows[0]
    if first_row is None or len(first_row) == 0:
        return None
    return first_row[0]


def validate_file(con, path: Path) -> tuple[int, int]:
    questions = yaml.safe_load(path.read_text())
    if not questions:
        print(f"  {path.name}: (empty)")
        return 0, 0
    total = 0
    failed = 0
    for q in questions:
        total += 1
        qid = q.get("id", "<no id>")
        sql = q.get("expected_sql", "")
        expected = q.get("expected_value")
        try:
            actual = run_one(con, sql)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {qid}: SQL error: {exc}")
            continue
        ok, note = values_match(actual, expected)
        marker = "OK" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{marker}] {qid}: actual={actual!r} expected={expected!r} {note}")
    return total, failed


def main(argv: list[str]) -> int:
    if not WAREHOUSE.exists():
        print(f"ERROR: warehouse not found at {WAREHOUSE}; run seed_warehouse.py first")
        return 2
    con = duckdb.connect(str(WAREHOUSE), read_only=True)

    if argv:
        files = [QUESTIONS_DIR / a for a in argv]
    else:
        files = sorted(QUESTIONS_DIR.glob("*.yaml"))

    grand_total = 0
    grand_failed = 0
    for path in files:
        if not path.exists():
            print(f"SKIP {path.name}: not found")
            continue
        print(f"\n=== {path.name} ===")
        t, f = validate_file(con, path)
        grand_total += t
        grand_failed += f

    print(f"\n=== Summary: {grand_total - grand_failed}/{grand_total} pass; {grand_failed} fail ===")
    return 0 if grand_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

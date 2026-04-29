#!/usr/bin/env python3
"""
Trust Benchmark — test-org seeding script.

Reads the 20 governed metric YAMLs in `dataset/metrics/*.yaml`
and idempotently UPSERTs them into a ClariLayer workspace, so
Baseline D ("live ClariLayer Metric API context") of the Trust
Benchmark can fetch governed definitions via the
`/api/v1/metrics` endpoint.

This script is included for transparency / reproducibility of how
Baseline D's API surface was provisioned. To reproduce Baseline D
end-to-end you would need write access to a ClariLayer workspace
(which is not part of this open-source release). Baselines A, B, and
C are fully reproducible from this repo without it; for D, third
parties typically run against their own ClariLayer workspace.

What this script writes
-----------------------
For each governed metric YAML it inserts/updates:

1. `metrics` row for the GOVERNED definition
   (lifecycle_status=APPROVED, scope=enterprise_canonical, version=v3 etc.)
2. `metrics` row for each non-governed variant in the YAML
   (lifecycle_status=DRAFT, scope=exploratory, key=`<metric>__variant_<id>`)
3. `metrics` row for each deprecated_version in the YAML
   (lifecycle_status=DEPRECATED, key=`<metric>__deprecated_<version>`)
4. `metric_contracts` row attached to the governed metric carrying
   `time_semantics` (fiscal_calendar_start_month, timezone, grain,
   default_grain) — surfaced verbatim by the v1 detail endpoint.
5. `metric_relationships`:
   - `variant_of` edges from each variant metric -> governed metric
   - `replaces` edges from governed metric -> each deprecated metric
     (with description = the deprecation reason from the YAML)

Idempotency
-----------
Re-running the script after a YAML change updates only the affected
rows. The composite UPSERT keys are:
- `metrics`: (project_id, key) — schema-level UNIQUE
- `metric_contracts`: (metric_id) — schema-level UNIQUE
- `metric_relationships`: (source_metric_id, target_metric_id, relationship_type)
  — schema-level UNIQUE

Connection
----------
Reads SUPABASE_DB_URL (or SUPABASE_CONNECTION_STRING) from the
environment. The connection must be a service-role / postgres user
URL because RLS on `metrics` would otherwise block writes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # PyYAML

try:
    import psycopg2  # type: ignore
    import psycopg2.extras  # type: ignore
except ImportError:
    psycopg2 = None

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

# Target workspace is `benchmark-trust-v1` in the production ClariLayer_v2
# Supabase project (ref wgkjcukgmjfdxmmlghro). The script resolves these IDs
# at runtime from the `orgs` / `projects` tables — these constants are only
# fallback / documentation values used when --print-only is set.
DEFAULT_ORG_NAME = "benchmark-trust-v1"
DEFAULT_PROJECT_KEY = "default"

REPO_ROOT = Path(__file__).resolve().parents[2]
METRICS_DIR = REPO_ROOT / "benchmark" / "dataset" / "metrics"


# -----------------------------------------------------------------------------
# YAML loading
# -----------------------------------------------------------------------------


def load_metric_yamls() -> List[Dict[str, Any]]:
    """Load all *.yaml files under dataset/metrics/, sorted by key."""
    files = sorted(METRICS_DIR.glob("*.yaml"))
    if not files:
        raise SystemExit(f"No metric YAMLs found under {METRICS_DIR}")
    out: List[Dict[str, Any]] = []
    for f in files:
        with f.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        if not isinstance(doc, dict):
            raise SystemExit(f"{f.name}: top-level must be a mapping")
        for required in ("metric_key", "name", "governed_definition", "governance_metadata"):
            if required not in doc:
                raise SystemExit(f"{f.name}: missing required key '{required}'")
        if not isinstance(doc.get("governed_definition"), dict):
            raise SystemExit(f"{f.name}: 'governed_definition' must be a mapping")
        if not isinstance(doc.get("governance_metadata"), dict):
            raise SystemExit(f"{f.name}: 'governance_metadata' must be a mapping")
        if "variants" in doc and not isinstance(doc.get("variants"), list):
            raise SystemExit(f"{f.name}: 'variants' must be a list when present")
        out.append(doc)
    return out


# -----------------------------------------------------------------------------
# YAML -> SQL row shapes
# -----------------------------------------------------------------------------


def _slug(value: str) -> str:
    """Normalize a YAML value to a snake_case slug suitable for a DB key suffix."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(value).strip().lower())
    return s.strip("_")


def build_governed_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return the metrics-table row for the governed definition."""
    governed = doc["governed_definition"]
    gov_md = doc["governance_metadata"]
    time_logic = gov_md.get("time_logic") or {}
    grains = time_logic.get("grain") or []
    default_grain = time_logic.get("default_grain")
    return {
        "key": doc["metric_key"],
        "name": doc["name"],
        "description": doc.get("description"),
        "version": gov_md.get("version", "v1"),
        "tier": "Financial",
        # owner_team is `text` on metrics; route uses it as a UUID lookup
        # against profiles. Storing the team string is acceptable — the
        # profile lookup just collapses to null and the raw value passes
        # through to `owner_team` in the v1 envelope (per the canonical
        # metric resolution rules).
        "owner_team": gov_md.get("owner_team"),
        "tags": ["benchmark-governed"],
        "metric_type": "base",
        "measure_type": "simple",
        "sql_expression": governed.get("sql_template"),
        "grains": grains,
        "default_grain": default_grain,
        "dimensions": None,
        "filters": gov_md.get("canonical_filters") or [],
        "source_refs": [],
        "lifecycle_status": "APPROVED",
        "scope": "enterprise_canonical",
    }


def build_variant_rows(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Variants from the YAML, excluding the governed-equivalent entry."""
    governed_id = doc["governed_definition"].get("variant_id")
    out: List[Dict[str, Any]] = []
    for i, v in enumerate(doc.get("variants") or []):
        if not isinstance(v, dict):
            raise SystemExit(
                f"{doc.get('metric_key', '<unknown>')}: variants[{i}] must be a mapping"
            )
        vid = v.get("id")
        if vid is None or not isinstance(vid, str):
            raise SystemExit(
                f"{doc.get('metric_key', '<unknown>')}: variants[{i}].id must be "
                f"a string (got {vid!r}; governed variant_id={governed_id!r})"
            )
        if vid == governed_id:
            # Skip the variant that IS the governed definition (it's
            # already represented by the governed row).
            continue
        suffix = _slug(vid)
        out.append({
            "key": f"{doc['metric_key']}__variant_{suffix}",
            "name": f"{doc['name']} — variant {vid}",
            "description": v.get("label"),
            "version": "0.0.0",
            "tier": "Experimental",
            "owner_team": None,
            "tags": ["benchmark-variant", f"benchmark-variant-{suffix}"],
            "metric_type": "base",
            "measure_type": "simple",
            "sql_expression": v.get("sql_template"),
            "grains": [],
            "default_grain": None,
            "dimensions": None,
            "filters": [],
            "source_refs": [],
            "lifecycle_status": "DRAFT",
            "scope": "exploratory",
        })
    return out


def build_deprecated_rows(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deprecated predecessor versions from the YAML."""
    gov_md = doc["governance_metadata"]
    out: List[Dict[str, Any]] = []
    for i, dep in enumerate(gov_md.get("deprecated_versions") or []):
        if not isinstance(dep, dict):
            raise SystemExit(
                f"{doc.get('metric_key', '<unknown>')}: governance_metadata.deprecated_versions[{i}] must be a mapping"
            )
        version = dep.get("version")
        if not version or not isinstance(version, str):
            raise SystemExit(
                f"{doc.get('metric_key', '<unknown>')}: "
                f"governance_metadata.deprecated_versions[{i}].version must be "
                f"a non-empty string (got {version!r})"
            )
        suffix = _slug(version)
        reason = (dep.get("reason") or "").strip()
        if not reason:
            # The metric_relationships_replaces_requires_description CHECK
            # would reject empty descriptions. Use a visible placeholder
            # consistent with the migration's backfill literal.
            reason = "[reason not captured]"
        out.append({
            "key": f"{doc['metric_key']}__deprecated_{suffix}",
            "name": f"{doc['name']} — deprecated {version}",
            "description": reason,
            "version": version,
            "tier": "Experimental",
            "owner_team": None,
            "tags": ["benchmark-deprecated", f"benchmark-deprecated-{suffix}"],
            "metric_type": "base",
            "measure_type": "simple",
            "sql_expression": None,
            "grains": [],
            "default_grain": None,
            "dimensions": None,
            "filters": [],
            "source_refs": [],
            # INSERT directly into DEPRECATED is allowed — the lifecycle
            # trigger only fires on UPDATE OF lifecycle_status (see
            # migrations 20260124000000 and 20260424000000).
            "lifecycle_status": "DEPRECATED",
            "scope": "exploratory",
        })
    return out


def build_time_semantics(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Build the metric_contracts.time_semantics JSONB blob from time_logic."""
    time_logic = doc["governance_metadata"].get("time_logic") or {}
    return {
        "fiscal_calendar_start_month": time_logic.get(
            "fiscal_calendar_start_month", 1
        ),
        "timezone": time_logic.get("timezone", "UTC"),
        "grain": time_logic.get("grain") or [],
        "default_grain": time_logic.get("default_grain"),
    }


# -----------------------------------------------------------------------------
# SQL generation
# -----------------------------------------------------------------------------


def _sqlquote_text(value: Optional[str]) -> str:
    """Return a Postgres-quoted text literal (or NULL)."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _sqlquote_jsonb(value: Any) -> str:
    """Return a Postgres-quoted JSONB literal."""
    if value is None:
        return "NULL"
    return _sqlquote_text(json.dumps(value)) + "::jsonb"


def _sqlquote_text_array(values: List[str]) -> str:
    if not values:
        return "ARRAY[]::text[]"
    return "ARRAY[" + ", ".join(_sqlquote_text(v) for v in values) + "]::text[]"


def _sql_comment(value: Any) -> str:
    """Return a single-line value safe to interpolate inside a SQL `--` comment.

    A newline in the interpolated value would terminate the comment and let the
    remainder be parsed as executable SQL — even with CL-controlled inputs we
    keep the surface tight so accidental multi-line metric_keys / UUIDs can't
    break the seed script.
    """
    return re.sub(r"[\r\n]+", " ", str(value))


def render_seed_sql(
    org_id: str,
    project_id: str,
    docs: List[Dict[str, Any]],
) -> str:
    """Render a single transactional SQL script that idempotently seeds
    the test org with the 20 governed metrics + variants + deprecateds +
    contracts + relationships."""
    org_lit = _sqlquote_text(org_id)
    proj_lit = _sqlquote_text(project_id)

    parts: List[str] = [
        "BEGIN;",
        "",
        "-- =========================================================",
        "-- Trust Benchmark R1: idempotent seed for benchmark-trust-v1",
        f"-- org_id     = {_sql_comment(org_id)}",
        f"-- project_id = {_sql_comment(project_id)}",
        "-- =========================================================",
        "",
    ]

    # 1. Metric upserts
    parts.append("-- 1. Metrics (governed + variants + deprecated)")
    parts.append("-- --------------------------------------------------------")
    parts.append("-- Conflict on (project_id, key); we only ever overwrite")
    parts.append("-- benchmark-owned columns. lifecycle_status is set in")
    parts.append("-- the INSERT path and intentionally NOT updated on")
    parts.append("-- conflict because the trigger validates state changes.")
    parts.append("-- --------------------------------------------------------")

    for doc in docs:
        parts.append("")
        parts.append(f"-- {_sql_comment(doc['metric_key'])}")
        # Governed
        parts.append(_render_metric_upsert(org_lit, proj_lit, build_governed_row(doc)))
        # Variants
        for row in build_variant_rows(doc):
            parts.append(_render_metric_upsert(org_lit, proj_lit, row))
        # Deprecated
        for row in build_deprecated_rows(doc):
            parts.append(_render_metric_upsert(org_lit, proj_lit, row))

    # 2. metric_contracts
    parts.append("")
    parts.append("-- 2. metric_contracts.time_semantics (governed only)")
    parts.append("-- --------------------------------------------------------")
    parts.append("-- Conflict on (metric_id) — UNIQUE per migration 20260226000005.")
    parts.append("-- --------------------------------------------------------")
    for doc in docs:
        ts = build_time_semantics(doc)
        ts_grain = doc["governance_metadata"].get("time_logic", {}).get(
            "default_grain"
        )
        parts.append("")
        parts.append(f"-- contract for {_sql_comment(doc['metric_key'])}")
        parts.append(
            f"INSERT INTO public.metric_contracts (org_id, metric_id, grain, time_semantics)\n"
            f"SELECT {org_lit}, m.id, {_sqlquote_text(ts_grain)}, {_sqlquote_jsonb(ts)}\n"
            f"FROM public.metrics m\n"
            f"WHERE m.project_id = {proj_lit} AND m.key = {_sqlquote_text(doc['metric_key'])}\n"
            f"ON CONFLICT (metric_id) DO UPDATE\n"
            f"  SET grain = EXCLUDED.grain,\n"
            f"      time_semantics = EXCLUDED.time_semantics;"
        )

    # 3. relationships: variant_of (set-based join, key-suffix driven)
    parts.append("")
    parts.append("-- 3. metric_relationships: variant_of (variant -> governed)")
    parts.append("-- --------------------------------------------------------")
    parts.append("-- Set-based: every metric whose key matches `<gov>__variant_<x>` is")
    parts.append("-- linked to its `<gov>` parent via a `variant_of` edge. Idempotent")
    parts.append("-- via the (source_metric_id, target_metric_id, relationship_type)")
    parts.append("-- UNIQUE constraint added in migration 20260226000006.")
    parts.append("-- --------------------------------------------------------")
    parts.append(
        "INSERT INTO public.metric_relationships (org_id, source_metric_id, target_metric_id, relationship_type, description)\n"
        "SELECT\n"
        "    src.org_id,\n"
        "    src.id AS source_metric_id,\n"
        "    tgt.id AS target_metric_id,\n"
        "    'variant_of'::text AS relationship_type,\n"
        "    'Defensibly-different alternative to the governed definition. Surfaced as a benchmark variant so an LLM evaluator can see the alternatives a competent analyst might pick.'::text AS description\n"
        "FROM public.metrics src\n"
        "JOIN public.metrics tgt\n"
        "    ON tgt.project_id = src.project_id\n"
        "   AND tgt.key = split_part(src.key, '__variant_', 1)\n"
        f"WHERE src.project_id = {proj_lit}\n"
        # `_` is a single-char LIKE wildcard; escape both underscores so
        # the literal '__variant_' delimiter is required (not just any
        # 11-character span containing 'variant').
        r"  AND src.key LIKE '%\_\_variant\_%' ESCAPE '\'" "\n"
        "  AND tgt.lifecycle_status = 'APPROVED'\n"
        "ON CONFLICT (source_metric_id, target_metric_id, relationship_type)\n"
        "    DO UPDATE SET description = EXCLUDED.description;"
    )

    # 4. relationships: replaces (set-based join, key-suffix driven)
    parts.append("")
    parts.append("-- 4. metric_relationships: replaces (governed -> deprecated)")
    parts.append("-- --------------------------------------------------------")
    parts.append("-- Description is sourced from the deprecated metric's `description`")
    parts.append("-- column (which carries the YAML deprecation reason). The partial")
    parts.append("-- CHECK in `metric_relationships_replaces_requires_description`")
    parts.append("-- (migration 20260424000000) requires non-empty description on")
    parts.append("-- replaces edges; the COALESCE with '[reason not captured]' is the")
    parts.append("-- defense-in-depth fallback consistent with that migration's backfill.")
    parts.append("-- --------------------------------------------------------")
    parts.append(
        "INSERT INTO public.metric_relationships (org_id, source_metric_id, target_metric_id, relationship_type, description)\n"
        "SELECT\n"
        "    src.org_id,\n"
        "    src.id AS source_metric_id,\n"
        "    tgt.id AS target_metric_id,\n"
        "    'replaces'::text AS relationship_type,\n"
        "    COALESCE(NULLIF(trim(tgt.description), ''), '[reason not captured]')\n"
        "        AS description\n"
        "FROM public.metrics src\n"
        "JOIN public.metrics tgt\n"
        "    ON tgt.project_id = src.project_id\n"
        # `_` is a single-char LIKE wildcard; escape so the literal
        # '__deprecated_' delimiter is required.
        r"   AND tgt.key LIKE src.key || '\_\_deprecated\_%' ESCAPE '\'" "\n"
        f"WHERE src.project_id = {proj_lit}\n"
        "  AND src.lifecycle_status = 'APPROVED'\n"
        "ON CONFLICT (source_metric_id, target_metric_id, relationship_type)\n"
        "    DO UPDATE SET description = EXCLUDED.description;"
    )

    parts.append("")
    parts.append("COMMIT;")
    return "\n".join(parts) + "\n"


def _render_metric_upsert(
    org_lit: str, proj_lit: str, row: Dict[str, Any]
) -> str:
    cols = [
        "org_id",
        "project_id",
        "metric_type",
        "key",
        "name",
        "description",
        "version",
        "tier",
        "owner_team",
        "tags",
        "measure_type",
        "sql_expression",
        "grains",
        "default_grain",
        "dimensions",
        "filters",
        "source_refs",
        "lifecycle_status",
        "scope",
    ]
    values = [
        org_lit,
        proj_lit,
        _sqlquote_text(row["metric_type"]),
        _sqlquote_text(row["key"]),
        _sqlquote_text(row["name"]),
        _sqlquote_text(row.get("description")),
        _sqlquote_text(row.get("version", "1.0.0")),
        _sqlquote_text(row.get("tier")),
        _sqlquote_text(row.get("owner_team")),
        _sqlquote_text_array(row.get("tags") or []),
        _sqlquote_text(row.get("measure_type")),
        _sqlquote_text(row.get("sql_expression")),
        _sqlquote_jsonb(row.get("grains") or []),
        _sqlquote_text(row.get("default_grain")),
        _sqlquote_jsonb(row.get("dimensions")),
        _sqlquote_jsonb(row.get("filters") or []),
        _sqlquote_jsonb(row.get("source_refs") or []),
        f"'{row['lifecycle_status']}'::public.metric_lifecycle_status",
        _sqlquote_text(row.get("scope") or "exploratory"),
    ]
    columns_sql = ", ".join(cols)
    values_sql = ",\n  ".join(values)
    # Update everything except lifecycle_status (trigger-protected) and key/project/org.
    update_cols = [
        "name",
        "description",
        "version",
        "tier",
        "owner_team",
        "tags",
        "measure_type",
        "sql_expression",
        "grains",
        "default_grain",
        "dimensions",
        "filters",
        "source_refs",
        "scope",
    ]
    update_sql = ",\n      ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    return (
        f"INSERT INTO public.metrics ({columns_sql}) VALUES (\n  {values_sql}\n)\n"
        f"ON CONFLICT (project_id, key) DO UPDATE\n  SET {update_sql};"
    )


# -----------------------------------------------------------------------------
# Org / project resolution
# -----------------------------------------------------------------------------


def _resolve_or_create_workspace(
    cur: Any, org_name: str, project_key: str
) -> Dict[str, str]:
    """Idempotently ensure org + project + prod environment exist; return the IDs."""
    cur.execute(
        """
        INSERT INTO public.orgs (name, fiscal_calendar_start_month)
        VALUES (%s, 1)
        ON CONFLICT DO NOTHING
        RETURNING id;
        """,
        (org_name,),
    )
    row = cur.fetchone()
    if row:
        org_id = row[0]
    else:
        cur.execute("SELECT id FROM public.orgs WHERE name = %s LIMIT 1;", (org_name,))
        result = cur.fetchone()
        if not result:
            raise SystemExit(f"Failed to resolve org '{org_name}'")
        org_id = result[0]

    cur.execute(
        """
        INSERT INTO public.projects (org_id, key, name)
        VALUES (%s, %s, 'Default Project')
        ON CONFLICT (org_id, key) DO NOTHING
        RETURNING id;
        """,
        (org_id, project_key),
    )
    row = cur.fetchone()
    if row:
        project_id = row[0]
    else:
        cur.execute(
            "SELECT id FROM public.projects WHERE org_id = %s AND key = %s LIMIT 1;",
            (org_id, project_key),
        )
        result = cur.fetchone()
        if not result:
            raise SystemExit("Failed to resolve project")
        project_id = result[0]

    cur.execute(
        """
        INSERT INTO public.environments (org_id, project_id, name)
        VALUES (%s, %s, 'prod')
        ON CONFLICT (project_id, name) DO NOTHING;
        """,
        (org_id, project_id),
    )

    return {"org_id": str(org_id), "project_id": str(project_id)}


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--org-name",
        default=DEFAULT_ORG_NAME,
        help=f"Workspace name (default: {DEFAULT_ORG_NAME})",
    )
    parser.add_argument(
        "--project-key",
        default=DEFAULT_PROJECT_KEY,
        help=f"Project key (default: {DEFAULT_PROJECT_KEY})",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help=(
            "Print the rendered SQL to stdout without connecting to Supabase. "
            "Requires --org-id and --project-id when used."
        ),
    )
    parser.add_argument("--org-id", help="Override org_id (used with --print-only).")
    parser.add_argument(
        "--project-id", help="Override project_id (used with --print-only)."
    )
    args = parser.parse_args()

    docs = load_metric_yamls()
    print(f"Loaded {len(docs)} metric YAMLs from {METRICS_DIR}", file=sys.stderr)

    if args.print_only:
        if not args.org_id or not args.project_id:
            parser.error("--print-only requires --org-id and --project-id")
        sql = render_seed_sql(args.org_id, args.project_id, docs)
        sys.stdout.write(sql)
        return 0

    if psycopg2 is None:
        raise SystemExit(
            "psycopg2 is not installed. Install with `pip install psycopg2-binary` "
            "or use --print-only and pipe the rendered SQL into psql."
        )

    db_url = os.environ.get("SUPABASE_DB_URL") or os.environ.get(
        "SUPABASE_CONNECTION_STRING"
    )
    if not db_url:
        raise SystemExit(
            "Set SUPABASE_DB_URL (postgres://...) or SUPABASE_CONNECTION_STRING"
        )

    conn = psycopg2.connect(db_url)
    try:
        with conn:
            with conn.cursor() as cur:
                workspace = _resolve_or_create_workspace(
                    cur, args.org_name, args.project_key
                )
                print(
                    f"Resolved org_id={workspace['org_id']} project_id={workspace['project_id']}",
                    file=sys.stderr,
                )
                sql = render_seed_sql(
                    workspace["org_id"], workspace["project_id"], docs
                )
                cur.execute(sql)
        print("Seed complete.", file=sys.stderr)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

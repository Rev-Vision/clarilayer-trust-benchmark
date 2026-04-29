# Trust Benchmark — Harness

Three artifacts live here:

- `seed_warehouse.py` — synthetic SaaS warehouse generator (DuckDB, ~550 MB output).
- `seed_test_org.py` — provisions the 20 governed metric definitions in a ClariLayer workspace so Baseline D can fetch them via `/api/v1/metrics`. Included for transparency; running it requires write access to a ClariLayer workspace and is not part of the open-source reproduction path.
- `harness.py` — orchestrates the (model × baseline × question × stability_run) matrix and writes per-call results to `results/<run_id>/results.jsonl`.

For the methodology and headline findings see [`paper/trust-benchmark-v1.md`](../paper/trust-benchmark-v1.md). Companion blog post: <https://clarilayer.com/blog/post-trust-benchmark-v1>.

---

## Synthetic warehouse generator

Deterministic generator that produces the messy synthetic SaaS warehouse used as the substrate for the benchmark.

The output is **deliberately messy** — a pristine schema would let any LLM look smart. The warehouse simulates real-company data-warehouse reality (legacy column names, orphan rows, mixed timezones, soft-delete divergence, half-broken values, near-duplicate rows, late-arriving facts). This messiness is what makes Baseline D's governed-context lift visible vs. Baselines A / B / C.

### What it produces

`dataset/warehouse.duckdb` — a single DuckDB file containing eight tables:

| Table                  | Rows        | Notes |
|------------------------|-------------|-------|
| `dim_customers`        |  10,000     | ~0.5% near-duplicates, ~5% test accounts |
| `dim_users`            | 100,000     | ~1% NULL/noreply emails, ~20% deleted_at coverage |
| `fct_subscriptions`    | ~30,200     | 30,000 base rows + ~7% of eligible (~200) mid-tenure downgrade rows. ~2% negative MRR (refund accounting). Downgrades mutate the original sub's `ended_at` + `status='cancelled'` and append a new active sub at the same date with 50-80% retained MRR |
| `fct_events`           | ~5,250,000+| ~3% orphan user_id, ~30% LA timezone, ~5% late-arriving. ~95% engagement events + ~5% opportunity-pipeline events (`opp_created` / `opp_stage_advanced` / `opp_won` / `opp_lost` / `opp_no_decision`) + a small `lead_qualified` slice (~6% of users created in the last 6 months emit one in their first 14 days post-signup); JSON keys split ~50/50 camelCase vs snake_case |
| `fct_invoices`         | 100,000     | ~1% orphan customer_id, ~35% legacy_braintree (NY tz) |
| `dim_products`         |      50     | |
| `fct_marketing_spend`  |      36     | One row per month for 2023-01 → 2025-12. ~80% USD / ~20% EUR with **no FX normalization layer** — governed metrics must filter to USD or convert |
| `fct_cogs`             |   ~50-60    | Monthly COGS. ~30%+ of months have BOTH a `cost_of_revenue` AND a `cost_of_goods_sold` row for the same period — naive `SUM(amount)` double-counts those months |

Approximate file size: **~550 MB**. The file is gitignored — regenerate it locally.

Two schema documentation files accompany the generator:

- `dataset/warehouse-schema-bare.sql` — Baseline A surface (raw DDL, no comments).
- `dataset/warehouse-schema-documented.sql` — Baseline B surface (DDL + column-level comments documenting the messiness).

### Determinism

Re-running the generator produces **logically identical** data (same row contents, same byte-level content per ordered table). Verified by content-hashing all eight tables across two independent runs.

> Note: the raw DuckDB file bytes can differ between runs because DuckDB embeds internal block-layout metadata. What matters for the benchmark is logical content, which is reproducible. To verify yourself, see "Verifying determinism" below.

### Setup

Requires Python 3.10+ (developed on 3.14). DuckDB, pandas, numpy, faker.

```bash
# create venv
python3 -m venv .venv
source .venv/bin/activate

# install deps
pip install -r harness/requirements.txt
```

### Run

```bash
python harness/seed_warehouse.py
```

Expected runtime on an M-series Mac: **~30-35 seconds**. The 5M-row engagement-event payload generation + INSERT dominates (~25s); the two monthly fact tables and 250K opportunity events add ~3-5s combined.

The script prints:

1. Per-step timing for each table generation and each INSERT.
2. Final row counts per table.
3. **Messiness verification queries** — every messiness layer measured directly against the produced DuckDB so you can confirm the layers landed at the expected rates.

### Verifying messiness landed (smoke checks)

The generator runs these automatically; you can also run them ad hoc against the DuckDB file:

```bash
duckdb dataset/warehouse.duckdb
```

```sql
-- L1 naming inconsistency: legacy cust_id column populated for ~50% of customers
SELECT COUNT(*) FROM dim_customers WHERE cust_id IS NOT NULL;
-- expected: ~5,000 (≈50% of 10K)

-- L1 naming inconsistency: properties_jsonb mixes camelCase and snake_case keys
SELECT
  COUNT(*) FILTER (WHERE properties_jsonb LIKE '%"userId"%') AS camel,
  COUNT(*) FILTER (WHERE properties_jsonb LIKE '%"user_id"%') AS snake
FROM fct_events;
-- expected: roughly 50/50 split, both ~2.5M

-- L2 orphan events (~3%)
SELECT 100.0 * SUM(CASE WHEN u.user_id IS NULL THEN 1 ELSE 0 END) / COUNT(*) AS orphan_pct
FROM fct_events e LEFT JOIN dim_users u ON u.user_id = e.user_id;
-- expected: ~3.0

-- L3 mixed timezones in fct_events
SELECT timezone, COUNT(*) FROM fct_events GROUP BY timezone;
-- expected: UTC ~3.5M, America/Los_Angeles ~1.5M

-- L3 mixed processors in fct_invoices (paid_at timezone differs by processor)
SELECT processor, COUNT(*) FROM fct_invoices GROUP BY processor;
-- expected: stripe ~65K, legacy_braintree ~35K

-- L4 soft-delete divergence: dim_customers vs fct_subscriptions disagree
SELECT
  (SELECT COUNT(*) FROM dim_customers WHERE churned_at IS NOT NULL)             AS churned_via_dim,
  (SELECT COUNT(DISTINCT customer_id) FROM fct_subscriptions WHERE status = 'cancelled') AS cancelled_via_sub;
-- expected: numbers differ — this is the divergence governed context resolves

-- L4 soft-delete divergence: dim_users.deleted_at populated for only ~20% of departed users
SELECT
  COUNT(*) FILTER (WHERE deleted_at IS NOT NULL)                       AS deleted_at_set,
  COUNT(*) FILTER (WHERE last_active_at < DATE '2026-01-01')           AS stale_users
FROM dim_users;
-- expected: deleted_at_set << stale_users

-- L5 negative MRR (~2%)
SELECT 100.0 * SUM(CASE WHEN mrr < 0 THEN 1 ELSE 0 END) / COUNT(*) AS pct
FROM fct_subscriptions;
-- expected: ~2.0

-- L5 NULL/noreply emails (~1%)
SELECT 100.0 * SUM(CASE WHEN email IS NULL OR email = 'noreply@example.com' THEN 1 ELSE 0 END) / COUNT(*) AS pct
FROM dim_users;
-- expected: ~1.0

-- L5 industry casing/spelling variants
SELECT
  COUNT(DISTINCT industry)                                                                         AS distinct_raw,
  COUNT(DISTINCT LOWER(REPLACE(REPLACE(TRIM(industry), '-', ' '), '_', ' ')))                       AS distinct_normalized
FROM dim_customers;
-- expected: distinct_raw > distinct_normalized (multiple raw spellings collapse)

-- L6 near-duplicate companies (same normalized company_name, different customer_id)
SELECT COUNT(*) FROM (
  SELECT LOWER(TRIM(REGEXP_REPLACE(company_name, ',\s*inc\.', ''))) AS norm, COUNT(*) AS n
  FROM dim_customers GROUP BY 1 HAVING COUNT(*) > 1
);
-- expected: ~50 collisions (~0.5% near-duplicate rate * 10K customers)

-- L7 late-arriving events (timestamp > 24h before inserted_at, ~5%)
SELECT 100.0 * SUM(CASE WHEN "timestamp" < inserted_at - INTERVAL 1 DAY THEN 1 ELSE 0 END) / COUNT(*) AS pct
FROM fct_events;
-- expected: ~5.0

-- Test-account contamination (~5%)
SELECT 100.0 * SUM(CASE WHEN is_test THEN 1 ELSE 0 END) / COUNT(*) AS pct FROM dim_customers;
-- expected: ~5.0

-- fct_marketing_spend currency mix (~80% USD, ~20% EUR)
SELECT currency, COUNT(*) FROM fct_marketing_spend GROUP BY 1 ORDER BY 2 DESC;
-- expected: USD ~28-30 rows, EUR ~6-8 rows

-- fct_cogs source double-counting (≥30% of months have both)
SELECT 100.0 * SUM(CASE WHEN n_sources >= 2 THEN 1 ELSE 0 END) / COUNT(*) AS pct
FROM (
  SELECT period_month, COUNT(DISTINCT source) AS n_sources
  FROM fct_cogs GROUP BY period_month
);
-- expected: ≥30.0

-- Opportunity events share of fct_events (~5%)
SELECT 100.0 * SUM(CASE WHEN event_type LIKE 'opp_%' THEN 1 ELSE 0 END) / COUNT(*) AS pct
FROM fct_events;
-- expected: ~4.7-5.0

-- Opp event camelCase / snake_case JSON-key share (~50/50)
SELECT
  COUNT(*) FILTER (WHERE properties_jsonb LIKE '%"oppId"%')   AS camel,
  COUNT(*) FILTER (WHERE properties_jsonb LIKE '%"opp_id"%') AS snake
FROM fct_events
WHERE event_type LIKE 'opp_%';
-- expected: roughly 50/50 split among the ~250K opp events

-- Attributable downgrade rate (sub_d6_* rows added by the downgrade lifecycle)
SELECT
  COUNT(DISTINCT customer_id) AS downgraded_customers,
  (SELECT COUNT(DISTINCT s.customer_id)
     FROM fct_subscriptions s
     JOIN dim_customers c ON c.customer_id = s.customer_id
     WHERE c.is_test = false
       AND c.signup_date <= DATE '2026-04-01' - INTERVAL '18 months'
       AND s.status = 'active') AS eligible_customers
FROM fct_subscriptions
WHERE sub_id LIKE 'sub_d6_%';
-- expected: ~7% of eligible (~200/2,700)

-- lead_qualified event coverage among in-window users (created last 6 months)
SELECT
  100.0 * COUNT(DISTINCT u.user_id) /
    NULLIF((SELECT COUNT(*) FROM dim_users WHERE created_at >= TIMESTAMP '2025-10-01'), 0) AS pct
FROM dim_users u
JOIN fct_events e ON e.user_id = u.user_id
WHERE u.created_at >= TIMESTAMP '2025-10-01'
  AND e.event_type = 'lead_qualified'
  AND e."timestamp" BETWEEN u.created_at AND u.created_at + INTERVAL '14 days';
-- expected: ~5.5-6.5
```

### Verifying determinism

```bash
# Run twice and compare content hashes per table
python harness/seed_warehouse.py
python -c "
import duckdb, hashlib
con = duckdb.connect('dataset/warehouse.duckdb', read_only=True)
# 6 tables with stable PKs
pks = {'dim_customers':'customer_id','dim_users':'user_id','fct_subscriptions':'sub_id','fct_events':'event_id','fct_invoices':'invoice_id','dim_products':'product_id'}
for t, pk in pks.items():
    rows = con.execute(f'SELECT * FROM {t} ORDER BY {pk}').fetchall()
    h = hashlib.sha256()
    for r in rows: h.update(repr(r).encode())
    print(f'{t:<24} {h.hexdigest()}')
# 2 monthly-fact tables (no PK — order by all columns)
for t in ['fct_marketing_spend', 'fct_cogs']:
    rows = con.execute(f'SELECT * FROM {t} ORDER BY ALL').fetchall()
    h = hashlib.sha256()
    for r in rows: h.update(repr(r).encode())
    print(f'{t:<24} {h.hexdigest()}')
"
# Repeat after re-running seed_warehouse.py — hashes must match.
```

### Tuning / adapting

Constants near the top of `seed_warehouse.py` control row counts and messiness rates. They are intentionally **not** parameterized via CLI — the benchmark contract requires fixed numbers. Edit the constants only if you intend to fork the benchmark for a different setup.

`SEED = 20260424` is the fixed seed. Do not change unless you intend to invalidate all downstream ground-truth SQL and context blocks that were computed against the previous seed.

### Where the messiness comes from in the generator

| Layer | Implementation pointer in `seed_warehouse.py` |
|-------|------------------------------------------------|
| L1 — naming inconsistency  | `gen_dim_customers` (cust_id, joined_dt), `gen_dim_users` (account_id, joined_dt), `gen_fct_subscriptions` (cust_id, inserted), `_build_events_batch` (camelCase vs snake_case engagement payloads), `gen_opp_events` (camelCase vs snake_case opp payloads + canonical-vs-Stage-N stage label drift) |
| L2 — orphan rows           | `_gen_fct_events_bulk` (orphan_user_ids), `gen_fct_invoices` (orphan_mask), `gen_opp_events` (~3% orphan owner_user_id) |
| L3 — mixed timezones       | `_gen_fct_events_bulk` (timezone column), `gen_fct_invoices` (processor column drives paid_at tz semantics), `gen_opp_events` (LA share applied to opp events too) |
| L4 — soft-delete divergence| `gen_dim_customers` (churned_at), `gen_fct_subscriptions` (status='cancelled'), `gen_dim_users` (last_active_at + sparse deleted_at) |
| L5 — half-broken values    | negative MRR in `gen_fct_subscriptions`; NULL/noreply emails in `gen_dim_users`; INDUSTRY_VARIANTS list |
| L6 — near-duplicate rows   | `gen_dim_customers` (`munge` function) |
| L7 — late-arriving facts   | `_gen_fct_events_bulk` (late_mask, late_delays_seconds) |
| Currency mix               | `gen_fct_marketing_spend` (~80% USD / ~20% EUR, no FX normalization layer) |
| Source double-count        | `gen_fct_cogs` (~30%+ of months get BOTH `cost_of_revenue` and `cost_of_goods_sold` rows covering overlapping periods) |
| Downgrade lifecycle        | `apply_downgrade_lifecycle` (~7% of eligible non-test, ≥18-month-tenured customers' active sub is replaced by a lower-MRR sub between months 6 and 18 of tenure; the original sub's `ended_at` + `status='cancelled'` is mutated in place, a new `sub_d6_*` row is appended) |
| `lead_qualified` events    | `gen_lead_qualified_events` (~6% of users created in the last 6 months emit a `lead_qualified` event in their first 14 days post-signup; carries `qualified_score` (40-90) and `source` ('webform' / 'sales_outreach' / 'product_signal') in `properties_jsonb` with the same camelCase/snake_case mix as engagement events) |

---

## Provisioning Baseline D's API surface (`seed_test_org.py`)

Baseline D is a *live* call to a ClariLayer workspace's `/api/v1/metrics` endpoint, not a static fixture. The harness needs the 20 governed metric definitions to exist in a ClariLayer workspace before it can fetch them.

`seed_test_org.py` is the script we used to provision that workspace — it reads the 20 governed metric YAMLs in `dataset/metrics/*.yaml` and idempotently UPSERTs them into a ClariLayer workspace's database.

For third parties reproducing the benchmark, Baselines A, B, and C run end-to-end from this repo. To reproduce Baseline D you need write access to a ClariLayer workspace. Email <kyle@clarilayer.com> if you'd like to discuss running it against your own workspace as part of the design partner program.

The script reads `SUPABASE_DB_URL` (or `SUPABASE_CONNECTION_STRING`) from the environment for the connection string.

### Required environment variables (Baseline D)

The harness reads (and stops if missing for D):

```dotenv
BENCHMARK_TEST_ORG_ID=<uuid>
BENCHMARK_API_KEY=cl_<40-hex>
BENCHMARK_API_BASE_URL=https://app.clarilayer.com
```

Place them in a `.env.benchmark` file at the repo root or under `harness/`. The file is gitignored — never commit it.

### Verification (live curl)

```bash
curl -sS \
  -H "Authorization: Bearer $BENCHMARK_API_KEY" \
  "$BENCHMARK_API_BASE_URL/api/v1/metrics?pageSize=100&lifecycle_status=APPROVED" \
  | jq '.pagination.total'
# expected: 20
```

The detail endpoint surfaces the governed-definition shape:

```bash
curl -sS \
  -H "Authorization: Bearer $BENCHMARK_API_KEY" \
  "$BENCHMARK_API_BASE_URL/api/v1/metrics/<metric-id>" \
  | jq '{
      sql: .data.metric.sql_expression,
      version: .data.metric.version,
      owner_team: .data.metric.owner_team,
      filters: .data.metric.filters,
      grains: .data.metric.grains,
      default_grain: .data.metric.default_grain,
      fiscal_calendar_start_month: .data.fiscal_calendar_start_month,
      time_semantics: .data.time_semantics,
      relationships: [.data.relationships[] | {type: .relationship_type, src: .source_metric_name, tgt: .target_metric_name}]
    }'
```

### What the seed maps from the YAMLs to the schema

| YAML field | DB column |
|------------|-----------|
| `metric_key` | `metrics.key` |
| `name` | `metrics.name` |
| `description` | `metrics.description` |
| `governed_definition.sql_template` | `metrics.sql_expression` |
| `governance_metadata.version` | `metrics.version` |
| `governance_metadata.lifecycle_status` (`APPROVED`) | `metrics.lifecycle_status` |
| `governance_metadata.owner_team` | `metrics.owner_team` (raw text) |
| `governance_metadata.canonical_filters[]` | `metrics.filters` (JSONB array) |
| `governance_metadata.time_logic.{fiscal_calendar_start_month,timezone,grain,default_grain}` | `metric_contracts.time_semantics` (JSONB), `metrics.grains`, `metrics.default_grain` |
| `governance_metadata.deprecated_versions[]` | `metrics` rows with `lifecycle_status=DEPRECATED` + `replaces` edges in `metric_relationships` (description = the `reason` text from the YAML) |
| `variants[]` (excluding the variant equal to `governed_definition.variant_id`) | `metrics` rows with `lifecycle_status=DRAFT`, `scope=exploratory` + `variant_of` edges in `metric_relationships` |

The 20 governed metrics are tagged `benchmark-governed`, the variants are tagged `benchmark-variant` plus `benchmark-variant-<id>`, and the deprecated rows are tagged `benchmark-deprecated` plus `benchmark-deprecated-<version>`. The harness can filter to the governed-only set via `?lifecycle_status=APPROVED` (returns exactly 20) or via `?scope=enterprise_canonical`.

### Two ways to run

```bash
# (A) Direct apply (requires SUPABASE_DB_URL with service-role privileges).
pip install pyyaml psycopg2-binary
python harness/seed_test_org.py

# (B) Render-only mode (review before applying via psql).
python harness/seed_test_org.py \
  --print-only \
  --org-id $BENCHMARK_TEST_ORG_ID \
  --project-id $BENCHMARK_PROJECT_ID \
  > /tmp/seed.sql
```

Re-runs after a YAML change update only the affected rows. UPSERTs are keyed on `(project_id, key)` for `metrics`, `(metric_id)` for `metric_contracts`, and `(source_metric_id, target_metric_id, relationship_type)` for `metric_relationships`.

---

## Benchmark harness

The harness in `harness.py` orchestrates the (model × baseline × question × stability_run) matrix and writes per-call results to `results/<run_id>/results.jsonl`.

### Layout

```
harness/
├── harness.py            # main orchestrator (CLI: --pilot / --full)
├── baselines/            # per-baseline prompt assembly
│   ├── a_raw.py          # raw schema DDL (Baseline A)
│   ├── b_documented.py   # DDL + column comments (Baseline B)
│   ├── c_cube.py         # Cube.dev semantic-layer YAML (Baseline C)
│   └── d_clarilayer.py   # live ClariLayer Metric API call (Baseline D)
├── scoring.py            # SQL extraction, DuckDB execution, tolerance match
├── run_config.yaml       # default model roster + pilot subset
└── requirements.txt      # adds httpx / tenacity / python-dotenv
```

`results/<run_id>/` holds large per-call JSONL plus an aggregate CSV.

### Environment

The harness loads (in order) from any of these `.env.benchmark` files if present:

- `<repo-root>/.env.benchmark`
- `harness/.env.benchmark`

Required env vars:

| Var | Required for | Source |
|-----|--------------|--------|
| `AI_GATEWAY_API_KEY` | every real call | Vercel AI Gateway |
| `BENCHMARK_API_KEY` | Baseline D only | provisioned by `seed_test_org.py` |
| `BENCHMARK_API_BASE_URL` | Baseline D only | usually `https://app.clarilayer.com` |

The `.env.benchmark` file is gitignored — never check it in.

### Usage

```bash
# 1. Refresh dependencies (adds httpx / tenacity / python-dotenv).
pip install -r harness/requirements.txt

# 2. Confirm the gateway routes the configured model IDs (best-effort listing).
python harness/harness.py --list-models

# 3. Pilot run — 1 model × 4 baselines × 5 questions × 1 stability = 20 calls.
python harness/harness.py --pilot --run-id pilot-001

# 4. Full run, 5 models × 4 baselines × 89 questions × 2 stability = 3,560 calls.
python harness/harness.py --full --budget-approved --run-id v1-2026-04-26
```

`--full` refuses to start without `--budget-approved`. `--pilot` does not require it.

`--dry-run` walks the combo matrix and writes `ERROR` rows without firing API calls — useful to verify resumability and the JSONL shape.

`--run-id <slug>` overrides the auto-generated id. Reusing a previous id resumes from that JSONL: completed (model, baseline, question_id, run_idx) tuples are skipped.

### Outputs

`results/<run_id>/results.jsonl` — one row per (model, baseline, question, run_idx). Schema:

```json
{
  "model": "anthropic/claude-opus-4-7",
  "baseline": "d",
  "metric": "arr",
  "question_id": "arr-001",
  "run_idx": 1,
  "prompt_tokens": 1234,
  "completion_tokens": 200,
  "latency_ms": 4500,
  "model_sql": "SELECT ...",
  "variant_choice": "C",
  "actual_value": 24454041.00,
  "expected_value": 24454041.00,
  "status": "PASS",
  "error": null,
  "detail": "rel_diff=0.0001%",
  "family": "claude-opus-4",
  "model_label": "Claude Opus 4.7 (1M)"
}
```

`results/<run_id>/summary.csv` — aggregate accuracy by (model, baseline) plus average prompt / completion token counts.

In pilot mode the harness additionally prints a token-grounded full-run cost projection.

### Updating model IDs / pricing

Two files must stay aligned:

- `run_config.yaml` — `id`, `family`, `fallbacks` per model.
- `harness.py` — `PRICING` dict keyed by `family` (input / output USD per 1M tokens).

Refresh both before any new run. The pilot-mode summary explicitly prints "PRICING IS APPROXIMATE — verify against gateway billing before approving full run."

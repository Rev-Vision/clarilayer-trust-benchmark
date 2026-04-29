-- ClariLayer Trust Benchmark — synthetic SaaS warehouse schema (documented)
-- Baseline B surface: DDL + column-level comments describing intent and known
-- messiness. The DuckDB tables themselves do NOT carry these comments — the
-- comments live only in this file because Baseline B feeds the schema *with
-- documentation* to the LLM, but the data warehouse it actually queries is
-- the same physical tables Baseline A sees.
-- Mirror of the DDL produced by harness/seed_warehouse.py; keep in sync with the generator.
--
-- Tables: dim_customers, dim_users, fct_subscriptions, fct_events,
-- fct_invoices, dim_products, fct_marketing_spend, fct_cogs (8 total).
-- The last two (and an extended fct_events.event_type enum that adds
-- opportunity-pipeline events) were added 2026-04-25 per spec §3.3.1
-- (D-B1-006) to unblock metrics that depend on data not in the original
-- 6-table sketch — cac/ltv/payback_period (S+M spend), win_rate /
-- pipeline_coverage (opportunity events), gross_margin (COGS).
-- D6 (2026-04-25) added two further lifecycle patterns: a mid-tenure
-- DOWNGRADE pattern in fct_subscriptions (~7% of long-tenured non-test
-- customers' active sub is replaced by a lower-MRR sub between months 6
-- and 18 of tenure — the original sub gets ended_at + status='cancelled',
-- a new sub appears with the same customer_id, started_at = old ended_at,
-- and contracted MRR), and a 'lead_qualified' MQL event in
-- fct_events.event_type (~6% of users created in the last 6 months emit
-- one in their first 14 days).

CREATE TABLE dim_customers (
    customer_id        VARCHAR PRIMARY KEY,  -- canonical customer surrogate key (use this)
    cust_id            VARCHAR,              -- LEGACY: alternative spelling of customer_id from old CRM import; populated for ~50% of rows, NULL elsewhere. Prefer customer_id.
    company_name       VARCHAR,              -- free-text; ~0.5% near-duplicate rows exist with whitespace/case variants of the same logical company
    industry           VARCHAR,              -- mixed free-text + coded enum: 'B2B SaaS', 'b2b saas', 'B2B-SaaS', 'saas', 'SaaS', 'Software-as-a-Service' all appear; normalize via LOWER(TRIM()) for grouping
    signup_date        DATE,                 -- DATE only (no timezone). When the customer first appeared in our system.
    joined_dt          DATE,                 -- LEGACY: duplicate of signup_date from a prior schema; populated for ~30% of rows. Prefer signup_date.
    churned_at         TIMESTAMP,            -- TIMESTAMP UTC. NULL = customer is active. NOT NULL = customer churned at this time. (See also fct_subscriptions.status='cancelled' — the two signals don't always agree.)
    is_test            BOOLEAN,              -- ~5% of rows are test accounts. NOT consistently filtered upstream — fact tables include their data. Governed metrics MUST exclude is_test = true.
    plan_tier          VARCHAR,              -- denormalized current plan: 'starter', 'pro', 'enterprise'. May lag fct_subscriptions if not refreshed.
    country            VARCHAR               -- ISO-2 country code
);

CREATE TABLE dim_users (
    user_id            VARCHAR PRIMARY KEY,
    customer_id        VARCHAR,              -- FK to dim_customers.customer_id
    account_id         VARCHAR,              -- LEGACY: alternative spelling of customer_id from old auth provider; populated for ~40% of rows. Prefer customer_id.
    email              VARCHAR,              -- ~1% NULL or 'noreply@example.com' (system-generated accounts)
    role               VARCHAR,              -- 'admin', 'editor', 'viewer'
    created_at         TIMESTAMP,            -- TIMESTAMP UTC
    joined_dt          DATE,                 -- LEGACY: alternative timestamp column; date-only, populated for ~30% of rows. Prefer created_at.
    last_active_at     TIMESTAMP,            -- TIMESTAMP UTC. Goes stale — does not necessarily mean the user is deleted.
    deleted_at         TIMESTAMP             -- Populated for ~20% of departed users. Many departed users only show as stale last_active_at, NOT a deleted_at value. So "active user" depends on which signal you trust.
);

CREATE TABLE fct_subscriptions (
    sub_id             VARCHAR PRIMARY KEY,
    customer_id        VARCHAR,              -- FK to dim_customers.customer_id
    cust_id            VARCHAR,              -- LEGACY: alternative spelling; populated sporadically. Prefer customer_id.
    plan               VARCHAR,              -- 'starter', 'pro', 'enterprise'
    mrr                DOUBLE,               -- monthly recurring revenue in USD. ~2% are NEGATIVE values from refund accounting — filter or sum carefully.
    started_at         TIMESTAMP,            -- TIMESTAMP UTC
    ended_at           TIMESTAMP,            -- TIMESTAMP UTC. NULL = still active.
    status             VARCHAR,              -- 'active', 'cancelled', 'paused', 'trial'. status='cancelled' is the canonical churn signal at the subscription grain (vs. dim_customers.churned_at at the customer grain). NOT all 'cancelled' rows are churn — some are mid-tenure downgrades where the customer's NEXT sub (started_at = the cancelled sub's ended_at, within 1 day) is 'active' with lower MRR. Governed retention metrics must distinguish downgrade-cancellations from true churn.
    inserted           TIMESTAMP             -- LEGACY: warehouse load timestamp from a prior ETL system. Same semantics as inserted_at elsewhere.
);

CREATE TABLE fct_events (
    event_id           VARCHAR PRIMARY KEY,
    user_id            VARCHAR,              -- ~3% reference users not present in dim_users (late-arriving identity stitching). INNER JOIN or filter required for accurate counts.
    customer_id        VARCHAR,              -- denormalized for query speed; may be stale for backfilled events
    event_type         VARCHAR,              -- engagement events: 'login', 'page_view', 'action_performed', 'feature_used', 'signup', 'logout' (~95% of rows). Plus opportunity-pipeline events: 'opp_created', 'opp_won', 'opp_lost', 'opp_no_decision', 'opp_stage_advanced' (~5% of rows; carry opp_id / deal_value / stage / owner_user_id in properties_jsonb — keys mix camelCase/snake_case like the engagement payloads). Plus marketing-qualification events: 'lead_qualified' (emitted within 14 days of user signup for ~6% of new users; carries qualified_score (40-90) / source ('webform'|'sales_outreach'|'product_signal') in properties_jsonb with the same camelCase/snake_case mix).
    "timestamp"        TIMESTAMP,            -- ~70% UTC, ~30% America/Los_Angeles (legacy logger). The 'timezone' column tells you which.
    timezone           VARCHAR,              -- 'UTC' or 'America/Los_Angeles'. Required to interpret the timestamp column correctly.
    inserted_at        TIMESTAMP,            -- when the row landed in the warehouse. ~5% of rows have timestamp > 24h before inserted_at (late-arriving / backfilled).
    properties_jsonb   JSON                  -- event-payload bag. Keys are inconsistent: ~50% camelCase ('userId', 'eventType', 'sessionId'), ~50% snake_case ('user_id', 'event_type', 'session_id'). Normalize before grouping.
);

CREATE TABLE fct_invoices (
    invoice_id         VARCHAR PRIMARY KEY,
    customer_id        VARCHAR,              -- ~1% orphaned (customer_id not in dim_customers). Filter or INNER JOIN.
    account_id         VARCHAR,              -- LEGACY: alternative spelling; populated for ~30% of rows. Prefer customer_id.
    amount             DOUBLE,               -- USD
    paid_at            TIMESTAMP,            -- timezone depends on processor: 'stripe' rows are UTC; 'legacy_braintree' rows are America/New_York. Read the processor column to interpret.
    due_at             TIMESTAMP,            -- TIMESTAMP UTC
    status             VARCHAR,              -- 'paid', 'unpaid', 'void', 'refunded'
    processor          VARCHAR               -- 'stripe' (UTC) or 'legacy_braintree' (America/New_York for paid_at)
);

CREATE TABLE dim_products (
    product_id         VARCHAR PRIMARY KEY,
    name               VARCHAR,
    tier               VARCHAR,              -- 'starter', 'pro', 'enterprise'
    launched_at        DATE
);

CREATE TABLE fct_marketing_spend (
    period_month       DATE,                 -- first day of the month (UTC)
    amount             DOUBLE,               -- spend amount in the row's currency. NOT normalized to USD — see currency column. Sum across rows without coercion will mix currencies.
    channel            VARCHAR,              -- 'paid_search', 'paid_social', 'content', 'events', 'partnerships', etc. Free-form-ish; spelling drift is rare but possible.
    currency           VARCHAR               -- ~80% 'USD', ~20% 'EUR' (international subsidiaries report in local currency). NO normalization layer — governed metrics that read this table must filter to USD or apply an FX conversion before summing.
);

CREATE TABLE fct_cogs (
    period_month       DATE,                 -- first day of the month (UTC)
    amount             DOUBLE,               -- cost of goods/revenue in USD for the period. Periodic accounting close.
    source             VARCHAR               -- 'cost_of_revenue' or 'cost_of_goods_sold'. ~30%+ of months have BOTH labels populated covering overlapping periods (the GAAP and management-accounting teams each filed their own row). Naive SUM(amount) double-counts those months — the governed approach is to pick one source and filter, or de-dup per period_month before summing.
);

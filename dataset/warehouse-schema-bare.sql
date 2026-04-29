-- ClariLayer Trust Benchmark — synthetic SaaS warehouse schema (bare)
-- Baseline A surface: raw DDL only, no comments, no documentation.
-- Mirror of the DDL produced by harness/seed_warehouse.py; keep in sync with the generator.

CREATE TABLE dim_customers (
    customer_id        VARCHAR PRIMARY KEY,
    cust_id            VARCHAR,
    company_name       VARCHAR,
    industry           VARCHAR,
    signup_date        DATE,
    joined_dt          DATE,
    churned_at         TIMESTAMP,
    is_test            BOOLEAN,
    plan_tier          VARCHAR,
    country            VARCHAR
);

CREATE TABLE dim_users (
    user_id            VARCHAR PRIMARY KEY,
    customer_id        VARCHAR,
    account_id         VARCHAR,
    email              VARCHAR,
    role               VARCHAR,
    created_at         TIMESTAMP,
    joined_dt          DATE,
    last_active_at     TIMESTAMP,
    deleted_at         TIMESTAMP
);

CREATE TABLE fct_subscriptions (
    sub_id             VARCHAR PRIMARY KEY,
    customer_id        VARCHAR,
    cust_id            VARCHAR,
    plan               VARCHAR,
    mrr                DOUBLE,
    started_at         TIMESTAMP,
    ended_at           TIMESTAMP,
    status             VARCHAR,
    inserted           TIMESTAMP
);

CREATE TABLE fct_events (
    event_id           VARCHAR PRIMARY KEY,
    user_id            VARCHAR,
    customer_id        VARCHAR,
    event_type         VARCHAR,
    "timestamp"        TIMESTAMP,
    timezone           VARCHAR,
    inserted_at        TIMESTAMP,
    properties_jsonb   JSON
);

CREATE TABLE fct_invoices (
    invoice_id         VARCHAR PRIMARY KEY,
    customer_id        VARCHAR,
    account_id         VARCHAR,
    amount             DOUBLE,
    paid_at            TIMESTAMP,
    due_at             TIMESTAMP,
    status             VARCHAR,
    processor          VARCHAR
);

CREATE TABLE dim_products (
    product_id         VARCHAR PRIMARY KEY,
    name               VARCHAR,
    tier               VARCHAR,
    launched_at        DATE
);

CREATE TABLE fct_marketing_spend (
    period_month       DATE,
    amount             DOUBLE,
    channel            VARCHAR,
    currency           VARCHAR
);

CREATE TABLE fct_cogs (
    period_month       DATE,
    amount             DOUBLE,
    source             VARCHAR
);

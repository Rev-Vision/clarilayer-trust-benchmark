"""
ClariLayer Trust Benchmark — synthetic SaaS warehouse generator.

Produces dataset/warehouse.duckdb deterministically from a fixed
seed. The output is the substrate for ground-truth SQL and context
blocks. The schema is *deliberately* messy — see paper/trust-benchmark-v1.md
for the messiness recipe.

Run:
    python harness/seed_warehouse.py

Re-running produces an identical DuckDB file (deterministic seeding).

Approximate runtime on M-series Mac: 2-4 minutes (5M event rows dominate).
"""
# ruff: noqa: S311
# Ruff S311 ("Standard pseudo-random generators are not suitable for
# cryptographic purposes") is a false positive across this file. The whole
# point of this script is a *deterministic* synthetic-data generator seeded
# from a fixed value (SEED below) so re-runs produce byte-identical content.
# `random.*` and `np.random.*` calls are intentional — never used for any
# auth/secret/token/cryptographic decision.

from __future__ import annotations

import json
import random
import string
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from faker import Faker

# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

SEED = 20260424
random.seed(SEED)
np.random.seed(SEED)
FAKE = Faker()
Faker.seed(SEED)

# ---------------------------------------------------------------------------
# Configuration — row count targets per spec §3.3.1
# ---------------------------------------------------------------------------

N_CUSTOMERS = 10_000
N_USERS = 100_000
N_SUBSCRIPTIONS = 30_000
N_EVENTS = 5_000_000
N_INVOICES = 100_000
N_PRODUCTS = 50

# Marketing-spend / COGS extension.
# 36 months covers Jan 2023 → Dec 2025 (3 years of history, matches the
# rest of the fact tables). Recent 2026 windows simply have no
# spend/COGS rows — itself a realistic gap downstream metric variants
# must contend with.
N_SPEND_MONTHS = 36
N_COGS_MONTHS = 36

# Share of fct_events that are opportunity-pipeline events. Holds the
# remaining 95% as engagement events (the 6 original event_types).
OPP_EVENT_SHARE = 0.05  # ~250K of 5M

# Opportunity-event JSON-key contamination: half camelCase, half snake_case
# (per spec §3.3.1 messiness layer 1). Reusing CAMELCASE_PROPERTY_RATE
# would mix RNG with the engagement loop; the opp loop uses its own
# coin-flip drawn from the global random module, kept near 50/50 by the
# spec (the win_rate v1 deprecation notice depends on ~40% of opp events
# missing under a single-form JSON path).
OPP_CAMELCASE_RATE = 0.50

# Messiness rates per spec §3.3.1
ORPHAN_EVENT_RATE = 0.03         # ~3% of fct_events.user_id reference missing users
ORPHAN_INVOICE_RATE = 0.01       # ~1% of fct_invoices.customer_id orphaned
LA_TIMEZONE_EVENT_RATE = 0.30    # ~30% of events use America/Los_Angeles
NY_INVOICE_PROCESSOR_RATE = 0.35 # share of invoices via legacy_braintree (NY tz)
TEST_ACCOUNT_RATE = 0.05         # ~5% of customers are test accounts
NEAR_DUPLICATE_RATE = 0.005      # ~0.5% near-duplicate dim_customers
NEGATIVE_MRR_RATE = 0.02         # ~2% of subscription rows have negative MRR
NULL_EMAIL_RATE = 0.01           # ~1% of dim_users have NULL or noreply email
LATE_ARRIVING_EVENT_RATE = 0.05  # ~5% of events arrive >24h after their timestamp
DELETED_AT_RATE = 0.20           # ~20% of departed users get deleted_at populated
CUST_ID_LEGACY_RATE = 0.50       # share of dim_customers carrying legacy cust_id
ACCOUNT_ID_LEGACY_RATE = 0.40    # share of dim_users carrying legacy account_id
JOINED_DT_LEGACY_RATE = 0.30     # share of rows carrying legacy joined_dt
CAMELCASE_PROPERTY_RATE = 0.50   # share of event payloads using camelCase keys

# D6 lifecycle additions — see paper/trust-benchmark-v1.md.
# Mid-tenure downgrade pattern: ~7% of customers eligible for downgrade
# (non-test, tenure ≥ 18 months, has at least one current active sub) get a
# sub-replacement event between months 6 and 18 of tenure that contracts MRR
# by 20-50%. Without this, the existing seed produced trivially-zero
# arr_contraction and trivially-100% gross_retention.
DOWNGRADE_RATE = 0.07            # share of eligible customers receiving a mid-tenure downgrade
DOWNGRADE_MIN_TENURE_MONTHS = 18 # eligibility floor — customer must have ≥18 months tenure
DOWNGRADE_WINDOW_MIN_MONTH = 6   # downgrade fires no earlier than month 6 of tenure
DOWNGRADE_WINDOW_MAX_MONTH = 18  # downgrade fires no later than month 18 of tenure
DOWNGRADE_MRR_RETAIN_LOW = 0.50  # new MRR is at LEAST 50% of old MRR (50% drop)
DOWNGRADE_MRR_RETAIN_HIGH = 0.80 # new MRR is at MOST 80% of old MRR (20% drop)

# Lead-qualified MQL events — explicit signal that D5's qualified_lead governed
# definition queries. ~6% of users created in the last 6 months emit a
# 'lead_qualified' event in their first 14 days post-signup.
LEAD_QUALIFIED_RATE = 0.06            # share of in-window users emitting a lead_qualified event
LEAD_QUALIFIED_WINDOW_DAYS = 14       # event must fall within first 14 days post-signup
LEAD_QUALIFIED_LOOKBACK_MONTHS = 6    # eligibility window — users created in last 6 months

# Time anchor — synthetic warehouse covers data through this date
NOW = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
HISTORY_START = datetime(2023, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

# Plan economics
PLAN_TIERS = ["starter", "pro", "enterprise"]
PLAN_WEIGHTS = [0.55, 0.35, 0.10]            # starter dominates free-tier-ish
PLAN_BASE_MRR = {"starter": 49.0, "pro": 299.0, "enterprise": 1499.0}
PLAN_EVENT_VELOCITY = {"starter": 50, "pro": 250, "enterprise": 1200}  # events/customer/active-day weighting

# Industry messiness — same logical industry written multiple ways
INDUSTRY_VARIANTS = [
    "B2B SaaS", "b2b saas", "B2B-SaaS", "saas", "SaaS", "Software-as-a-Service",
    "Fintech", "fintech", "FinTech",
    "Healthcare", "healthcare", "Health Care",
    "E-commerce", "ecommerce", "E-Commerce",
    "Marketing", "Marketing & Advertising",
    "Education", "EdTech", "edtech",
    "Manufacturing",
    "Retail",
    "Logistics",
]

EVENT_TYPES = ["login", "page_view", "action_performed", "feature_used", "signup", "logout"]
EVENT_TYPE_WEIGHTS = [0.20, 0.45, 0.15, 0.12, 0.03, 0.05]

ROLES = ["admin", "editor", "viewer"]
ROLE_WEIGHTS = [0.15, 0.40, 0.45]

INVOICE_STATUSES = ["paid", "unpaid", "void", "refunded"]
INVOICE_STATUS_WEIGHTS = [0.85, 0.08, 0.04, 0.03]

SUB_STATUSES = ["active", "cancelled", "paused", "trial"]

OUT_DIR = Path(__file__).resolve().parent.parent / "dataset"
OUT_DB = OUT_DIR / "warehouse.duckdb"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def step(label: str) -> "_StepTimer":
    return _StepTimer(label)


@dataclass
class _StepTimer:
    label: str
    _t0: float = 0.0

    def __enter__(self) -> "_StepTimer":
        self._t0 = time.perf_counter()
        sys.stdout.write(f"  {self.label} ... ")
        sys.stdout.flush()
        return self

    def __exit__(self, *_exc) -> None:
        elapsed = time.perf_counter() - self._t0
        sys.stdout.write(f"done ({elapsed:.1f}s)\n")
        sys.stdout.flush()


def random_id(prefix: str, n: int) -> np.ndarray:
    """Deterministic IDs: prefix + zero-padded sequential index. Stable across runs."""
    return np.array([f"{prefix}_{i:09d}" for i in range(n)], dtype=object)


def utc_naive(ts: datetime) -> datetime:
    """Strip tzinfo for storage as DuckDB TIMESTAMP (naive)."""
    if ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def to_naive_ts(arr: np.ndarray) -> pd.Series:
    """Coerce a numpy array of datetime64 (UTC) into pandas tz-naive Timestamps."""
    result = pd.to_datetime(arr)
    return result.tz_localize(None) if result.tz is not None else result


# ---------------------------------------------------------------------------
# Generators per table
# ---------------------------------------------------------------------------

def gen_dim_products() -> pd.DataFrame:
    rows = []
    for i in range(N_PRODUCTS):
        tier = random.choices(PLAN_TIERS, weights=PLAN_WEIGHTS, k=1)[0]
        # spread launches across history
        days_ago = random.randint(0, (NOW.date() - HISTORY_START.date()).days)
        launched = NOW.date() - timedelta(days=days_ago)
        rows.append({
            "product_id": f"prod_{i:03d}",
            "name": FAKE.bs().title(),
            "tier": tier,
            "launched_at": launched,
        })
    return pd.DataFrame(rows)


def gen_dim_customers() -> pd.DataFrame:
    """Generate customers, including ~0.5% near-duplicates and ~5% test accounts."""
    n_unique = int(N_CUSTOMERS * (1 - NEAR_DUPLICATE_RATE))
    n_dupes = N_CUSTOMERS - n_unique

    customer_ids = random_id("cust", N_CUSTOMERS)

    company_names_unique = [FAKE.unique.company() for _ in range(n_unique)]

    # near-duplicates: take random unique companies and create a slight variant
    dup_source_idx = np.random.choice(n_unique, size=n_dupes, replace=False)

    def munge(name: str) -> str:
        # apply one of: extra whitespace, casing flip, trailing punctuation
        choice = random.random()
        if choice < 0.34:
            return f"  {name.upper()}  "
        if choice < 0.67:
            return name.lower() + ", inc."
        return f" {name.title()}"

    company_names: list[str] = list(company_names_unique)
    for src in dup_source_idx:
        company_names.append(munge(company_names_unique[src]))

    # shuffle deterministically so duplicates aren't all clustered at the end
    perm = np.random.permutation(N_CUSTOMERS)
    company_names = [company_names[i] for i in perm]

    industries = [random.choice(INDUSTRY_VARIANTS) for _ in range(N_CUSTOMERS)]

    # signup_date — DATE only, spread across history with cohort skew (more recent rows)
    days_total = (NOW.date() - HISTORY_START.date()).days
    # bias toward recent: triangular distribution, mode = 75% through history
    signup_offset = np.random.triangular(0, int(days_total * 0.75), days_total, N_CUSTOMERS).astype(int)
    signup_dates = [HISTORY_START.date() + timedelta(days=int(d)) for d in signup_offset]

    # joined_dt — legacy duplicate of signup_date populated for ~30% of rows, NULL otherwise
    joined_dt_mask = np.random.random(N_CUSTOMERS) < JOINED_DT_LEGACY_RATE
    joined_dts = [signup_dates[i] if joined_dt_mask[i] else None for i in range(N_CUSTOMERS)]

    # cust_id — legacy spelling populated for ~50% of rows, NULL elsewhere
    cust_id_mask = np.random.random(N_CUSTOMERS) < CUST_ID_LEGACY_RATE
    cust_ids = [customer_ids[i] if cust_id_mask[i] else None for i in range(N_CUSTOMERS)]

    # plan tier — assigned per customer
    plan_tiers = random.choices(PLAN_TIERS, weights=PLAN_WEIGHTS, k=N_CUSTOMERS)

    # is_test — ~5% test accounts (uniform across cohorts)
    is_test = np.random.random(N_CUSTOMERS) < TEST_ACCOUNT_RATE

    # churn — cohort-based survival. Older cohorts have higher cumulative churn.
    # Probability customer has churned by NOW = 1 - exp(-lambda * tenure_years)
    # lambda calibrated so ~25% of 3-year-old cohort has churned, ~5% of recent.
    LAMBDA = 0.10  # per year
    churned_at: list[datetime | None] = []
    for i in range(N_CUSTOMERS):
        tenure_days = (NOW.date() - signup_dates[i]).days
        tenure_years = tenure_days / 365.0
        p_churned = 1.0 - np.exp(-LAMBDA * tenure_years)
        # test accounts churn less (they're internal)
        if is_test[i]:
            p_churned *= 0.3
        if random.random() < p_churned:
            # uniform-random churn date between signup and now
            min_churn = signup_dates[i] + timedelta(days=30)  # at least 30 days tenure
            max_churn = NOW.date()
            if min_churn >= max_churn:
                churned_at.append(None)
                continue
            span = (max_churn - min_churn).days
            churn_date = min_churn + timedelta(days=random.randint(0, span))
            churn_dt = datetime.combine(churn_date, datetime.min.time()) + timedelta(
                seconds=random.randint(0, 86399)
            )
            churned_at.append(utc_naive(churn_dt))
        else:
            churned_at.append(None)

    countries = [FAKE.country_code() for _ in range(N_CUSTOMERS)]

    df = pd.DataFrame({
        "customer_id": customer_ids,
        "cust_id": cust_ids,
        "company_name": company_names,
        "industry": industries,
        "signup_date": signup_dates,
        "joined_dt": joined_dts,
        "churned_at": churned_at,
        "is_test": is_test,
        "plan_tier": plan_tiers,
        "country": countries,
    })
    return df


def gen_dim_users(customers: pd.DataFrame) -> pd.DataFrame:
    """Distribute users across customers, with realistic per-customer counts skewed by plan tier."""
    # Larger customers (enterprise) have more users
    plan_user_mean = {"starter": 3, "pro": 15, "enterprise": 80}

    customer_id_arr = customers["customer_id"].to_numpy()
    plan_arr = customers["plan_tier"].to_numpy()
    signup_arr = customers["signup_date"].to_numpy()
    churned_arr = customers["churned_at"].to_numpy()

    # Assign each user to a customer: weight customers by plan-implied user count
    weights = np.array([plan_user_mean[p] for p in plan_arr], dtype=float)
    weights = weights / weights.sum()
    user_customer_idx = np.random.choice(len(customer_id_arr), size=N_USERS, p=weights)

    user_ids = random_id("user", N_USERS)
    customer_ids = customer_id_arr[user_customer_idx]

    # account_id — legacy spelling for ~40% of rows
    account_id_mask = np.random.random(N_USERS) < ACCOUNT_ID_LEGACY_RATE
    account_ids = np.where(account_id_mask, customer_ids, None)

    # email — ~1% NULL or noreply
    emails: list[str | None] = []
    for _ in range(N_USERS):
        r = random.random()
        if r < NULL_EMAIL_RATE / 2:
            emails.append(None)
        elif r < NULL_EMAIL_RATE:
            emails.append("noreply@example.com")
        else:
            emails.append(FAKE.email())

    roles = random.choices(ROLES, weights=ROLE_WEIGHTS, k=N_USERS)

    # created_at: between customer signup and (churned_at or NOW)
    created_at_arr: list[datetime] = []
    last_active_arr: list[datetime] = []
    deleted_at_arr: list[datetime | None] = []
    joined_dt_arr: list[date | None] = []

    joined_dt_mask = np.random.random(N_USERS) < JOINED_DT_LEGACY_RATE
    deleted_at_mask = np.random.random(N_USERS) < DELETED_AT_RATE

    for i in range(N_USERS):
        cust_signup = signup_arr[user_customer_idx[i]]
        cust_churn = churned_arr[user_customer_idx[i]]
        # convert to datetime
        if isinstance(cust_signup, np.datetime64):
            signup_dt = pd.Timestamp(cust_signup).to_pydatetime()
        elif isinstance(cust_signup, date):
            signup_dt = datetime.combine(cust_signup, datetime.min.time())
        else:
            signup_dt = datetime(2024, 1, 1)

        if cust_churn is None or pd.isna(cust_churn):
            window_end = NOW.replace(tzinfo=None)
        else:
            window_end = pd.Timestamp(cust_churn).to_pydatetime() if not isinstance(cust_churn, datetime) else cust_churn

        if window_end <= signup_dt:
            window_end = signup_dt + timedelta(days=1)

        span_seconds = int((window_end - signup_dt).total_seconds())
        # bias creation to early in customer's life (first 30% of window)
        early_bias = random.random() ** 2  # square: skew toward 0 (early)
        created = signup_dt + timedelta(seconds=int(span_seconds * early_bias))
        created_at_arr.append(created)

        joined_dt_arr.append(created.date() if joined_dt_mask[i] else None)

        # last_active_at: somewhere between created and window_end
        # Some users go stale early — geometric decay
        active_window = max(0, int((window_end - created).total_seconds()))
        if active_window <= 0:
            last_active = created
        else:
            # 70% of users active recently, 30% stale
            if random.random() < 0.70:
                # active recently — last_active in last 25% of window
                last_active = created + timedelta(seconds=int(active_window * (0.75 + 0.25 * random.random())))
            else:
                # stale — last_active anywhere
                last_active = created + timedelta(seconds=int(active_window * random.random()))
        last_active_arr.append(last_active)

        # deleted_at: only for ~20% of departed/stale users
        days_since_active = (window_end - last_active).total_seconds() / 86400
        is_departed = days_since_active > 90  # heuristic: gone if not seen in 90 days
        if is_departed and deleted_at_mask[i]:
            # deleted_at is sometime after last_active_at
            del_offset = random.randint(1, max(1, int((window_end - last_active).total_seconds())))
            deleted_at_arr.append(last_active + timedelta(seconds=del_offset))
        else:
            deleted_at_arr.append(None)

    df = pd.DataFrame({
        "user_id": user_ids,
        "customer_id": customer_ids,
        "account_id": account_ids,
        "email": emails,
        "role": roles,
        "created_at": created_at_arr,
        "joined_dt": joined_dt_arr,
        "last_active_at": last_active_arr,
        "deleted_at": deleted_at_arr,
    })
    return df


def gen_fct_subscriptions(customers: pd.DataFrame) -> pd.DataFrame:
    """Subscriptions tied to customers; some customers have multiple over time (churn → re-signup, plan changes)."""
    # Base 1 sub per customer; some customers have 2-4 (plan changes, expansions)
    extra = np.random.poisson(lam=2.0, size=N_CUSTOMERS)  # mean 2 extras per customer
    extra = np.minimum(extra, 5)
    customer_sub_count = 1 + extra
    # truncate to N_SUBSCRIPTIONS total
    cumulative = np.cumsum(customer_sub_count)
    cutoff = np.searchsorted(cumulative, N_SUBSCRIPTIONS)
    customer_sub_count[cutoff:] = 0
    if cutoff < len(customer_sub_count):
        # adjust the boundary customer to land exactly on N_SUBSCRIPTIONS
        diff = N_SUBSCRIPTIONS - (cumulative[cutoff - 1] if cutoff > 0 else 0)
        customer_sub_count[cutoff] = diff

    sub_ids = random_id("sub", N_SUBSCRIPTIONS)

    customer_id_arr = customers["customer_id"].to_numpy()
    plan_arr = customers["plan_tier"].to_numpy()
    signup_arr = customers["signup_date"].to_numpy()
    churned_arr = customers["churned_at"].to_numpy()

    rows: list[dict] = []
    sub_idx = 0
    for ci in range(len(customer_id_arr)):
        n_subs = customer_sub_count[ci]
        if n_subs == 0:
            continue

        signup = signup_arr[ci]
        if isinstance(signup, np.datetime64):
            signup_dt = pd.Timestamp(signup).to_pydatetime()
        elif isinstance(signup, date):
            signup_dt = datetime.combine(signup, datetime.min.time())
        else:
            signup_dt = datetime(2024, 1, 1)

        churn = churned_arr[ci]
        if churn is None or pd.isna(churn):
            cust_end = NOW.replace(tzinfo=None)
        else:
            cust_end = pd.Timestamp(churn).to_pydatetime() if not isinstance(churn, datetime) else churn

        # Distribute n_subs sequential subscription periods across [signup_dt, cust_end]
        if cust_end <= signup_dt:
            cust_end = signup_dt + timedelta(days=30)

        total_days = max((cust_end - signup_dt).days, n_subs * 30)
        # create boundary points
        boundaries = sorted(random.sample(range(1, total_days), min(n_subs - 1, total_days - 1))) if n_subs > 1 else []
        starts = [0, *boundaries]
        ends = [*boundaries, total_days]

        for k in range(n_subs):
            if sub_idx >= N_SUBSCRIPTIONS:
                break

            started_at = signup_dt + timedelta(days=starts[k])
            ended_at: datetime | None
            if k < n_subs - 1:
                ended_at = signup_dt + timedelta(days=ends[k])
                # status of historical sub: cancelled, paused, or upgraded (still 'cancelled' for simplicity)
                status = random.choices(["cancelled", "paused", "cancelled"], weights=[0.5, 0.2, 0.3], k=1)[0]
            else:
                # final sub: ended only if customer churned
                if churn is None or pd.isna(churn):
                    ended_at = None
                    status = random.choices(["active", "trial"], weights=[0.92, 0.08], k=1)[0]
                else:
                    ended_at = cust_end
                    status = "cancelled"

            # plan: usually customer's plan_tier, occasionally a different historical tier
            if random.random() < 0.85:
                plan = plan_arr[ci]
            else:
                plan = random.choices(PLAN_TIERS, weights=PLAN_WEIGHTS, k=1)[0]

            base_mrr = PLAN_BASE_MRR[plan]
            # add expansion/contraction noise (±25%)
            mrr = base_mrr * (1.0 + 0.5 * (random.random() - 0.5))
            # ~2% are negative (refund accounting)
            if random.random() < NEGATIVE_MRR_RATE:
                mrr = -abs(mrr)

            # cust_id legacy column populated sporadically (~30%)
            cust_id_legacy = customer_id_arr[ci] if random.random() < 0.30 else None

            # inserted (legacy ETL load timestamp): some time after started_at
            inserted = started_at + timedelta(hours=random.randint(1, 72))

            rows.append({
                "sub_id": sub_ids[sub_idx],
                "customer_id": customer_id_arr[ci],
                "cust_id": cust_id_legacy,
                "plan": plan,
                "mrr": round(mrr, 2),
                "started_at": started_at,
                "ended_at": ended_at,
                "status": status,
                "inserted": inserted,
            })
            sub_idx += 1

        if sub_idx >= N_SUBSCRIPTIONS:
            break

    # Pad remainder if we under-shot (rare)
    while sub_idx < N_SUBSCRIPTIONS:
        ci = random.randint(0, len(customer_id_arr) - 1)
        signup = signup_arr[ci]
        signup_dt = datetime.combine(signup, datetime.min.time()) if isinstance(signup, date) else datetime(2024, 1, 1)
        plan = plan_arr[ci]
        rows.append({
            "sub_id": sub_ids[sub_idx],
            "customer_id": customer_id_arr[ci],
            "cust_id": None,
            "plan": plan,
            "mrr": round(PLAN_BASE_MRR[plan], 2),
            "started_at": signup_dt,
            "ended_at": None,
            "status": "active",
            "inserted": signup_dt + timedelta(hours=24),
        })
        sub_idx += 1

    return pd.DataFrame(rows)


def gen_fct_invoices(customers: pd.DataFrame, subs: pd.DataFrame) -> pd.DataFrame:
    """Invoices generally tied to subscriptions; ~1% orphaned with bad customer_id."""
    invoice_ids = random_id("inv", N_INVOICES)

    # weight customers by subscription count to make active customers more invoice-heavy
    sub_per_cust = subs.groupby("customer_id").size()
    customer_weights = sub_per_cust.reindex(customers["customer_id"]).fillna(1).to_numpy(dtype=float)
    customer_weights = customer_weights / customer_weights.sum()

    customer_id_arr = customers["customer_id"].to_numpy()
    customer_idx = np.random.choice(len(customer_id_arr), size=N_INVOICES, p=customer_weights)

    customer_ids = customer_id_arr[customer_idx]

    # ~1% orphan: replace with fake customer_id not in dim_customers
    orphan_mask = np.random.random(N_INVOICES) < ORPHAN_INVOICE_RATE
    n_orphans = int(orphan_mask.sum())
    if n_orphans > 0:
        orphan_ids = np.array([f"cust_orphan_{i:06d}" for i in range(n_orphans)], dtype=object)
        customer_ids = customer_ids.copy().astype(object)
        customer_ids[orphan_mask] = orphan_ids

    # account_id legacy populated for ~30% of rows (mirrors customer_id when present)
    account_id_mask = np.random.random(N_INVOICES) < 0.30
    account_ids = np.where(account_id_mask, customer_ids, None)

    # amount: distribution roughly $50-$5000 with long tail
    amounts = np.round(np.random.lognormal(mean=5.5, sigma=0.9, size=N_INVOICES), 2)
    amounts = np.minimum(amounts, 100_000.0)

    # processor split — drives timezone interpretation
    processors = np.where(np.random.random(N_INVOICES) < NY_INVOICE_PROCESSOR_RATE, "legacy_braintree", "stripe")

    # status — mostly paid
    statuses = np.array(random.choices(INVOICE_STATUSES, weights=INVOICE_STATUS_WEIGHTS, k=N_INVOICES), dtype=object)

    # Time fields. Spread across history.
    days_total = (NOW.date() - HISTORY_START.date()).days
    days_offset = np.random.randint(0, days_total, size=N_INVOICES)
    seconds_offset = np.random.randint(0, 86400, size=N_INVOICES)

    paid_at_list: list[datetime | None] = []
    due_at_list: list[datetime] = []

    history_start_naive = HISTORY_START.replace(tzinfo=None)

    for i in range(N_INVOICES):
        due = history_start_naive + timedelta(days=int(days_offset[i]), seconds=int(seconds_offset[i]))
        due_at_list.append(due)

        if statuses[i] == "paid":
            # paid 0-30 days from due (sometimes early, sometimes late)
            offset_days = random.randint(-5, 30)
            paid = due + timedelta(days=offset_days)
            # If processor is legacy_braintree, the paid_at value is in America/New_York wall-clock;
            # we store the naive timestamp as if it were already in that local timezone (no shift).
            # SQL queries that need UTC must convert.
            paid_at_list.append(paid)
        elif statuses[i] == "refunded":
            paid_at_list.append(due + timedelta(days=random.randint(-2, 15)))
        else:
            paid_at_list.append(None)

    df = pd.DataFrame({
        "invoice_id": invoice_ids,
        "customer_id": customer_ids,
        "account_id": account_ids,
        "amount": amounts,
        "paid_at": paid_at_list,
        "due_at": due_at_list,
        "status": statuses,
        "processor": processors,
    })
    return df


# ---------------------------------------------------------------------------
# fct_events generation — STREAMED for memory pressure (CR Major #1)
#
# The original implementation built the full 5M-row DataFrame plus the 5M
# Python-string properties_json list in memory before INSERT. Peak RSS was
# ~4.5 GB (≈9× the ~525 MB output). This streamed implementation generates
# the bulk vectorized arrays once at full 5M scale (preserves the global
# RNG consumption order) and then walks them in EVENTS_BATCH_SIZE-row batches,
# building each batch's properties_json list and DataFrame, INSERTing it,
# and dropping the references so Python/pandas can free them between batches.
#
# Determinism contract — verified by per-table content hash on a full
# round-trip vs. the prior (single-shot) implementation:
#  1. All np.random.* calls remain at full N_EVENTS scale, in the same
#     order, so the resulting arrays are bit-identical to before.
#  2. The per-event Python loop (using `random.choice` / `random.randint`
#     for `extra` payload keys) iterates batches in increasing order across
#     the same `range(N_EVENTS)`, so the global `random` module state is
#     consumed in the same sequence — same calls in the same order.
#  3. Per-batch event_ids are computed from absolute index (deterministic,
#     no RNG), matching the original `random_id("evt", N_EVENTS)` slice.
# ---------------------------------------------------------------------------

EVENTS_BATCH_SIZE = 100_000  # rows per INSERT — caps Python-string + DataFrame peak memory

# Module-level pools used by both the bulk-array prep and the per-batch builder.
# Defined once because they are static (no RNG) and are referenced inside the
# per-event payload loop on every batch.
_EVENT_PAGE_PATHS = ["/dashboard", "/billing", "/settings", "/reports", "/users", "/integrations", "/home", "/api"]
_EVENT_FEATURE_NAMES = ["bulk_export", "advanced_filters", "saved_views", "shared_dashboard", "api_keys", "audit_log"]
_EVENT_SESSION_POOL = [f"sess_{i:08d}" for i in range(20_000)]


@dataclass
class _FctEventsBulk:
    """All vectorized arrays for fct_events, generated once at full N_EVENTS scale.

    Held briefly in memory; the per-batch builder slices into these to assemble
    INSERT-ready DataFrames without re-running RNG.
    """

    all_user_ids: np.ndarray
    all_customer_ids: np.ndarray
    event_types: np.ndarray
    timestamps: pd.DatetimeIndex
    timezones: np.ndarray
    inserted_at: pd.DatetimeIndex
    camel_mask: np.ndarray
    perm: np.ndarray  # original shuffle perm — also seeds session id selection


def _gen_fct_events_bulk(customers: pd.DataFrame, users: pd.DataFrame) -> _FctEventsBulk:
    """Generate the bulk vectorized arrays for fct_events at full 5M scale.

    All np.random.* and bulk random.choices calls happen here, in the SAME ORDER
    as the prior single-shot implementation, so the produced arrays are
    bit-identical. The per-event `random.choice`/`random.randint` calls (for
    the JSON extra payload) are deferred to the batch builder.
    """
    customer_id_arr = customers["customer_id"].to_numpy()
    plan_arr = customers["plan_tier"].to_numpy()
    customer_id_to_idx = {cid: i for i, cid in enumerate(customer_id_arr)}

    user_id_arr = users["user_id"].to_numpy()
    user_customer_arr = users["customer_id"].to_numpy()

    # Map each user to its customer's plan tier; weight events by tier-implied velocity
    user_plan_idx = np.array([customer_id_to_idx.get(c, 0) for c in user_customer_arr])
    user_plans = plan_arr[user_plan_idx]
    user_weights = np.array([PLAN_EVENT_VELOCITY[p] for p in user_plans], dtype=float)
    user_weights = user_weights / user_weights.sum()

    # Reserve ~3% for orphan events (user_id not in dim_users)
    n_orphan = int(N_EVENTS * ORPHAN_EVENT_RATE)
    n_real = N_EVENTS - n_orphan

    real_user_idx = np.random.choice(len(user_id_arr), size=n_real, p=user_weights)
    event_user_ids = user_id_arr[real_user_idx]
    event_customer_ids = user_customer_arr[real_user_idx]

    # Orphan events: synthetic user_ids that don't exist in dim_users
    orphan_user_ids = np.array([f"user_orphan_{i:09d}" for i in range(n_orphan)], dtype=object)
    # Orphan events still need a customer_id (denormalized) — pick a random real customer
    orphan_customer_ids = np.random.choice(customer_id_arr, size=n_orphan)

    all_user_ids = np.concatenate([event_user_ids, orphan_user_ids])
    all_customer_ids = np.concatenate([event_customer_ids, orphan_customer_ids])

    # Shuffle so orphans aren't bunched at the end
    perm = np.random.permutation(N_EVENTS)
    all_user_ids = all_user_ids[perm]
    all_customer_ids = all_customer_ids[perm]

    # event_type — weighted random
    event_types = np.array(random.choices(EVENT_TYPES, weights=EVENT_TYPE_WEIGHTS, k=N_EVENTS), dtype=object)

    # Timestamps: bias toward recent (most recent 12 months get ~60% of events)
    days_total = (NOW.date() - HISTORY_START.date()).days
    # Triangular: mode = days_total - 180 (recent skew)
    ts_offsets_days = np.random.triangular(0, days_total - 180, days_total, N_EVENTS)
    ts_offsets_seconds = np.random.randint(0, 86400, size=N_EVENTS)

    history_start_naive = HISTORY_START.replace(tzinfo=None)
    base_ts = pd.Timestamp(history_start_naive)
    timestamps = base_ts + pd.to_timedelta(ts_offsets_days.astype(int), unit="D") + pd.to_timedelta(ts_offsets_seconds, unit="s")

    # Timezone column: ~30% America/Los_Angeles, rest UTC
    tz_mask = np.random.random(N_EVENTS) < LA_TIMEZONE_EVENT_RATE
    timezones = np.where(tz_mask, "America/Los_Angeles", "UTC")

    # inserted_at: usually within an hour of timestamp; ~5% are late-arriving (>24h late)
    late_mask = np.random.random(N_EVENTS) < LATE_ARRIVING_EVENT_RATE
    # late: 1-30 days delay
    late_delays_seconds = np.random.randint(86400 * 1, 86400 * 30, size=N_EVENTS)
    # normal: 0-3600 seconds delay
    normal_delays_seconds = np.random.randint(0, 3600, size=N_EVENTS)
    delays_seconds = np.where(late_mask, late_delays_seconds, normal_delays_seconds)
    inserted_at = timestamps + pd.to_timedelta(delays_seconds, unit="s")

    # properties_jsonb: half camelCase, half snake_case keys.
    camel_mask = np.random.random(N_EVENTS) < CAMELCASE_PROPERTY_RATE

    return _FctEventsBulk(
        all_user_ids=all_user_ids,
        all_customer_ids=all_customer_ids,
        event_types=event_types,
        timestamps=timestamps,
        timezones=timezones,
        inserted_at=inserted_at,
        camel_mask=camel_mask,
        perm=perm,
    )


def _build_events_batch(bulk: _FctEventsBulk, batch_start: int, batch_end: int) -> pd.DataFrame:
    """Build a single fct_events batch DataFrame for rows [batch_start, batch_end).

    Consumes the global `random` module state for the per-event `extra` payload.
    Caller MUST invoke this with strictly-increasing, non-overlapping
    [batch_start, batch_end) ranges covering [0, N_EVENTS) — the per-event RNG
    consumption order must match the prior single-shot loop for byte-identical
    output. See the streaming-block comment above for the full determinism
    contract.
    """
    n = batch_end - batch_start

    # Event ids — deterministic from absolute index (no RNG).
    event_ids = np.array([f"evt_{i:09d}" for i in range(batch_start, batch_end)], dtype=object)

    # Slice all bulk arrays to the batch range.
    batch_user_ids = bulk.all_user_ids[batch_start:batch_end]
    batch_customer_ids = bulk.all_customer_ids[batch_start:batch_end]
    batch_event_types = bulk.event_types[batch_start:batch_end]
    batch_timestamps = bulk.timestamps[batch_start:batch_end]
    batch_timezones = bulk.timezones[batch_start:batch_end]
    batch_inserted_at = bulk.inserted_at[batch_start:batch_end]
    batch_camel_mask = bulk.camel_mask[batch_start:batch_end]
    batch_perm = bulk.perm[batch_start:batch_end]

    properties_json: list[str] = [None] * n  # type: ignore[list-item]
    session_pool_len = len(_EVENT_SESSION_POOL)

    for j in range(n):
        et = batch_event_types[j]
        sess = _EVENT_SESSION_POOL[int(batch_perm[j]) % session_pool_len]
        # vary payload by event_type — same RNG consumption pattern as prior loop
        if et == "page_view":
            extra = {"path": random.choice(_EVENT_PAGE_PATHS)}
        elif et == "feature_used":
            extra = {"feature": random.choice(_EVENT_FEATURE_NAMES)}
        elif et == "action_performed":
            extra = {"action": f"action_{random.randint(1, 50):03d}"}
        else:
            extra = {}

        if batch_camel_mask[j]:
            payload = {
                "userId": str(batch_user_ids[j]),
                "customerId": str(batch_customer_ids[j]),
                "eventType": et,
                "sessionId": sess,
            }
            payload.update(extra)
        else:
            payload = {
                "user_id": str(batch_user_ids[j]),
                "customer_id": str(batch_customer_ids[j]),
                "event_type": et,
                "session_id": sess,
            }
            payload.update(extra)
        properties_json[j] = json.dumps(payload, separators=(",", ":"))

    return pd.DataFrame({
        "event_id": event_ids,
        "user_id": batch_user_ids,
        "customer_id": batch_customer_ids,
        "event_type": batch_event_types,
        "timestamp": batch_timestamps,
        "timezone": batch_timezones,
        "inserted_at": batch_inserted_at,
        "properties_jsonb": properties_json,
    })


# ---------------------------------------------------------------------------
# D1.5 generators — fct_marketing_spend, fct_cogs, opportunity-pipeline events.
#
# DETERMINISM CONTRACT
# These generators run AFTER the engagement-event bulk arrays are built,
# so the global `random` and `np.random` states they consume are downstream
# of every D1 generator. The 6 original tables (dim_customers, dim_users,
# fct_subscriptions, fct_events engagement rows, fct_invoices, dim_products)
# hash identically to D1 because nothing here is invoked before them.
#
# Opportunity events are APPENDED to fct_events (after the 5M engagement
# rows are inserted). evt_* primary keys for opp events use an offset
# starting at N_EVENTS to avoid collision with engagement evt_000000000
# through evt_004999999. The fct_events total row count grows to
# N_EVENTS + N_OPP_EVENTS (~5.25M), and the row-count assertion is
# updated to reflect that.
# ---------------------------------------------------------------------------

# Marketing channels — 5-7 channels per spec, no enum normalization
_SPEND_CHANNELS = [
    "paid_search", "paid_social", "content", "events",
    "partnerships", "outbound", "affiliate",
]
# rough share of monthly spend per channel (informs split, not strictly weighted)
_SPEND_CHANNEL_WEIGHTS = [0.30, 0.20, 0.10, 0.20, 0.10, 0.07, 0.03]

# Currency mix per spec: ~80% USD, ~20% EUR (no FX normalization layer)
_SPEND_USD_RATE = 0.80

# Approximate target CAC band: $3K-$8K per new paying customer.
# D1's customer-acquisition curve grows over the 3 years (triangular dist
# skewed toward recent). We model spend as a similar growth curve:
# - 2023 monthly base: ~$300K
# - 2025 monthly base: ~$1.2M
# scaled with ±25% noise per month.
_SPEND_BASE_2023 = 300_000.0
_SPEND_BASE_2025 = 1_200_000.0


def gen_fct_marketing_spend() -> pd.DataFrame:
    """Generate ~36 rows (one per month for 3 years).

    Each row is a monthly marketing-spend summary tagged with a single
    "dominant" channel and a single currency. ~80% of rows are USD,
    ~20% EUR (international subsidiaries). NO FX normalization layer —
    governed metrics that read this table must filter to USD or apply
    FX before summing; baselines that don't will silently mix currencies.

    Hand-tuned so monthly spend lands in the $300K (early 2023) → $1.2M
    (late 2025) growth band. Combined with D1's monthly new-paying-
    customer curve (~80-300/month), this back-derives to a CAC of
    ~$3K-$8K — the target band per the task.
    """
    rows: list[dict] = []
    # Months: 2023-01 through 2025-12 (36 months)
    y, m = 2023, 1
    for month_idx in range(N_SPEND_MONTHS):
        period = date(y, m, 1)

        # Linear growth from 2023-01 base → 2025-12 base
        progress = month_idx / max(N_SPEND_MONTHS - 1, 1)
        base = _SPEND_BASE_2023 + progress * (_SPEND_BASE_2025 - _SPEND_BASE_2023)
        # ±25% monthly noise
        monthly_total = base * (1.0 + 0.5 * (random.random() - 0.5))

        channel = random.choices(_SPEND_CHANNELS, weights=_SPEND_CHANNEL_WEIGHTS, k=1)[0]
        currency = "USD" if random.random() < _SPEND_USD_RATE else "EUR"

        rows.append({
            "period_month": period,
            "amount": round(monthly_total, 2),
            "channel": channel,
            "currency": currency,
        })

        m += 1
        if m > 12:
            m = 1
            y += 1

    return pd.DataFrame(rows)


def gen_fct_cogs(invoices: pd.DataFrame) -> pd.DataFrame:
    """Generate ~36 rows of monthly COGS targeting ~25% of monthly revenue.

    Source double-counting messiness: at least 30% of months get BOTH a
    'cost_of_revenue' row AND a 'cost_of_goods_sold' row covering the same
    month (the GAAP and management-accounting teams each filed a row).
    Naive SUM(amount) double-counts those months — the governed metric
    must pick one source or de-dup.
    """
    # Monthly revenue from paid invoices (in USD)
    inv = invoices[invoices["status"] == "paid"].copy()
    inv["paid_at_dt"] = pd.to_datetime(inv["paid_at"])
    inv["period_month"] = inv["paid_at_dt"].dt.to_period("M").dt.to_timestamp()
    monthly_rev = inv.groupby("period_month")["amount"].sum()

    rows: list[dict] = []
    y, m = 2023, 1
    for _ in range(N_COGS_MONTHS):
        period_ts = pd.Timestamp(year=y, month=m, day=1)
        period_date = period_ts.to_pydatetime().date()

        # Monthly COGS targets ~25% of that month's revenue. If we have
        # no invoice rows for the month (rare for early months), fall
        # back to a tier-appropriate floor.
        rev = float(monthly_rev.get(period_ts, 0.0)) if period_ts in monthly_rev.index else 0.0
        target_cogs = max(rev * 0.25, 50_000.0)
        # ±15% monthly noise
        cogs = target_cogs * (1.0 + 0.30 * (random.random() - 0.5))

        # Decide which source(s) populate this month.
        # 35% of months get BOTH (slightly above the spec's ≥30% floor
        # so verification passes with comfortable headroom).
        # 35% get only cost_of_revenue, 30% get only cost_of_goods_sold.
        r = random.random()
        if r < 0.35:
            # Both sources — split with overlap: each row reports the
            # full month's COGS (both teams filed independently). Naive
            # SUM doubles the month's COGS.
            rows.append({"period_month": period_date, "amount": round(cogs, 2),
                         "source": "cost_of_revenue"})
            # Slight wobble on the second team's number — accounting
            # rarely lines up cent-for-cent across reports.
            wobble = cogs * (1.0 + 0.05 * (random.random() - 0.5))
            rows.append({"period_month": period_date, "amount": round(wobble, 2),
                         "source": "cost_of_goods_sold"})
        elif r < 0.70:
            rows.append({"period_month": period_date, "amount": round(cogs, 2),
                         "source": "cost_of_revenue"})
        else:
            rows.append({"period_month": period_date, "amount": round(cogs, 2),
                         "source": "cost_of_goods_sold"})

        m += 1
        if m > 12:
            m = 1
            y += 1

    return pd.DataFrame(rows)


# Opportunity-pipeline events (extension to fct_events).
# ~250K rows (~5% of N_EVENTS), appended to the engagement events. Stage
# labels mix consistent ('lead'/'qualified'/...) and inconsistent
# ('Stage 1'/'Stage 2'/...) terminology. Owner user_ids carry ~3% orphans
# (matching the engagement-event orphan rate).

# Opportunity stages — mostly canonical, ~30% drift to Stage-N labels
_OPP_STAGES_CANONICAL = ["lead", "qualified", "proposal", "negotiation", "closed"]
_OPP_STAGES_DRIFT = ["Stage 1", "Stage 2", "Stage 3", "Stage 4", "Stage 5"]

# Terminal split — ~50% won, ~30% lost, ~20% no_decision
_TERMINAL_TYPES = ["opp_won", "opp_lost", "opp_no_decision"]
_TERMINAL_WEIGHTS = [0.50, 0.30, 0.20]

# Per-opportunity events: 1 created + (0-3) advances + 1 terminal.
# Average ~2.5 events/opp → 100K opps for 250K events.
_OPP_TARGET_EVENTS = int(N_EVENTS * OPP_EVENT_SHARE)  # 250,000


def gen_opp_events(users: pd.DataFrame, customers: pd.DataFrame, evt_offset: int) -> pd.DataFrame:
    """Generate ~250K opportunity events. Each opp gets created → advances → terminal.

    JSON keys split ~50/50 between camelCase ('oppId', 'dealValue', 'stage',
    'ownerUserId') and snake_case ('opp_id', 'deal_value', 'stage', 'owner_user_id').
    The win_rate v1 deprecation notice in the metric YAML depends on this
    split being near 50/50 — variants that only handle one form miss ~half.

    `evt_offset` is the starting integer for evt_<n> primary keys; opp
    events use evt_<N_EVENTS+i> to avoid collision with the engagement
    rows that took evt_0 through evt_(N_EVENTS-1).
    """
    user_id_arr = users["user_id"].to_numpy()
    user_customer_arr = users["customer_id"].to_numpy()
    customer_id_arr = customers["customer_id"].to_numpy()

    history_start_naive = HISTORY_START.replace(tzinfo=None)
    days_total = (NOW.date() - HISTORY_START.date()).days

    rows: list[dict] = []
    opp_seq = 0
    evt_seq = 0

    # Loop building opportunities until we've produced ~_OPP_TARGET_EVENTS rows.
    # We don't pre-compute the exact opp count because per-opp event count
    # varies; we stop once we've crossed the target.
    while evt_seq < _OPP_TARGET_EVENTS:
        # Owner — 3% orphan, otherwise pick from real users
        if random.random() < ORPHAN_EVENT_RATE:
            owner_user_id = f"user_orphan_opp_{opp_seq:09d}"
            # Orphan owners still report a customer (denormalized, picked at random)
            cust_idx = random.randint(0, len(customer_id_arr) - 1)
            customer_id = customer_id_arr[cust_idx]
        else:
            uidx = random.randint(0, len(user_id_arr) - 1)
            owner_user_id = str(user_id_arr[uidx])
            customer_id = str(user_customer_arr[uidx])

        # Each opp_id is a UUID-style string (no real UUID dep needed —
        # 16-hex-char block deterministic per opp_seq is sufficient and
        # cheap).
        opp_id = f"opp_{opp_seq:09d}_{''.join(random.choices(string.hexdigits.lower(), k=8))}"

        # Deal value — lognormal $5K to $500K
        deal_value = round(float(np.exp(np.random.normal(loc=10.5, scale=1.0))), 2)
        deal_value = min(max(deal_value, 1_000.0), 1_000_000.0)

        # camelCase or snake_case for THIS opportunity (all events under
        # one opp use the same convention — that's how the win_rate v1
        # deprecation notice is realistic: a producer settled on one
        # form per opportunity).
        use_camel = random.random() < OPP_CAMELCASE_RATE

        # Created timestamp — biased toward recent like engagement events
        # (triangular with mode at days_total - 180)
        created_offset_days = int(random.triangular(0, days_total - 180, days_total))
        created_offset_seconds = random.randint(0, 86399)
        created_ts = (
            pd.Timestamp(history_start_naive)
            + pd.Timedelta(days=created_offset_days, seconds=created_offset_seconds)
        )

        # Number of advances between created and terminal (0-3)
        n_advances = random.choices([0, 1, 2, 3], weights=[0.20, 0.40, 0.30, 0.10], k=1)[0]

        # Stage labels: opportunity uses either canonical labels OR drift labels
        use_drift_stages = random.random() < 0.30
        stage_pool = _OPP_STAGES_DRIFT if use_drift_stages else _OPP_STAGES_CANONICAL
        # Pick the initial stage (first item) and a sequence of advanced stages
        initial_stage = stage_pool[0]

        # Terminal type — weighted draw
        terminal_type = random.choices(_TERMINAL_TYPES, weights=_TERMINAL_WEIGHTS, k=1)[0]

        # Sales-cycle length: 7-180 days from created to terminal (skewed shorter)
        cycle_days = max(7, int(np.random.lognormal(mean=3.6, sigma=0.7)))
        cycle_days = min(cycle_days, 180)
        terminal_ts = created_ts + pd.Timedelta(days=cycle_days)

        # Helper: build the JSON payload using the camelCase-or-snake convention
        def make_props(stage_value: str) -> dict:
            if use_camel:
                return {
                    "oppId": opp_id,
                    "dealValue": deal_value,
                    "stage": stage_value,
                    "ownerUserId": owner_user_id,
                }
            return {
                "opp_id": opp_id,
                "deal_value": deal_value,
                "stage": stage_value,
                "owner_user_id": owner_user_id,
            }

        # 1. opp_created at created_ts
        rows.append({
            "event_id": f"evt_{evt_offset + evt_seq:09d}",
            "user_id": owner_user_id,
            "customer_id": customer_id,
            "event_type": "opp_created",
            "timestamp": created_ts,
            "timezone": "America/Los_Angeles" if random.random() < LA_TIMEZONE_EVENT_RATE else "UTC",
            "inserted_at": created_ts + pd.Timedelta(seconds=random.randint(0, 3600)),
            "properties_jsonb": json.dumps(make_props(initial_stage), separators=(",", ":")),
        })
        evt_seq += 1

        # 2. n_advances stage advances spaced between created and terminal.
        # Reserve one slot for the terminal event so every opp_created is
        # guaranteed a matching terminal — the "exactly one terminal per
        # opportunity" contract is strict (Baseline D SQL relies on it).
        remaining_slots = _OPP_TARGET_EVENTS - evt_seq
        max_advances = max(0, min(n_advances, remaining_slots - 1))
        for k in range(max_advances):
            # Distribute advances across the sales cycle
            frac = (k + 1) / (n_advances + 1)
            advance_ts = created_ts + pd.Timedelta(seconds=int(cycle_days * 86400 * frac))
            stage_idx = min(k + 1, len(stage_pool) - 1)
            new_stage = stage_pool[stage_idx]
            rows.append({
                "event_id": f"evt_{evt_offset + evt_seq:09d}",
                "user_id": owner_user_id,
                "customer_id": customer_id,
                "event_type": "opp_stage_advanced",
                "timestamp": advance_ts,
                "timezone": "America/Los_Angeles" if random.random() < LA_TIMEZONE_EVENT_RATE else "UTC",
                "inserted_at": advance_ts + pd.Timedelta(seconds=random.randint(0, 3600)),
                "properties_jsonb": json.dumps(make_props(new_stage), separators=(",", ":")),
            })
            evt_seq += 1

        # 3. Terminal event — always emitted (slot reserved above).
        terminal_stage = stage_pool[-1]  # 'closed' or 'Stage 5'
        rows.append({
            "event_id": f"evt_{evt_offset + evt_seq:09d}",
            "user_id": owner_user_id,
            "customer_id": customer_id,
            "event_type": terminal_type,
            "timestamp": terminal_ts,
            "timezone": "America/Los_Angeles" if random.random() < LA_TIMEZONE_EVENT_RATE else "UTC",
            "inserted_at": terminal_ts + pd.Timedelta(seconds=random.randint(0, 3600)),
            "properties_jsonb": json.dumps(make_props(terminal_stage), separators=(",", ":")),
        })
        evt_seq += 1

        opp_seq += 1

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# D6 lifecycle additions — see paper/trust-benchmark-v1.md.
#
# DETERMINISM CONTRACT
# These generators run AFTER the D1.5 generators (fct_marketing_spend,
# fct_cogs, opp_events) so the global `random` and `np.random` states they
# consume are downstream of every D1 + D1.5 generator. The 6 D1 tables and
# the 2 D1.5 tables (fct_marketing_spend, fct_cogs) hash identically to
# pre-D6 because nothing here is invoked before them.
# ---------------------------------------------------------------------------


_DOWNGRADE_PLAN_DOWNSHIFT = {
    "enterprise": "pro",
    "pro": "starter",
    "starter": "starter",
}


def apply_downgrade_lifecycle(subs: pd.DataFrame, customers: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Mutate ~7% of eligible customers' current active sub into a downgrade pair.

    Eligibility: non-test customers with tenure ≥ DOWNGRADE_MIN_TENURE_MONTHS
    months AND at least one current active subscription (status='active' AND
    ended_at IS NULL).

    Mechanic: pick a downgrade date uniformly between months
    DOWNGRADE_WINDOW_MIN_MONTH and DOWNGRADE_WINDOW_MAX_MONTH of tenure.
    Mutate the customer's CURRENT active sub:
        ended_at = downgrade_date
        status = 'cancelled'
    Append a NEW sub starting at downgrade_date:
        started_at = downgrade_date
        ended_at = NULL
        status = 'active'
        plan = downshift(old_plan)  -- enterprise→pro, pro→starter, else same
        mrr = old_mrr × random(DOWNGRADE_MRR_RETAIN_LOW, DOWNGRADE_MRR_RETAIN_HIGH)

    Determinism: the new sub_ids use a `sub_d6_NNNNNN` namespace to avoid
    colliding with the existing `sub_NNNNNNNNN` namespace. Mutation order
    is iteration order of eligible customers (sorted by customer_id), so
    the same RNG draws hit the same customers across runs.

    Returns (updated_subs, stats) where stats records the eligible /
    selected counts so the verification block can assert against them.
    """
    now_naive = NOW.replace(tzinfo=None)

    # Pull the customer_id → (is_test, signup_date) lookup.
    cust_id_arr = customers["customer_id"].to_numpy()
    is_test_arr = customers["is_test"].to_numpy()
    signup_arr = customers["signup_date"].to_numpy()
    cust_meta = {
        cid: (
            bool(is_test),
            signup if not isinstance(signup, np.datetime64)
            else pd.Timestamp(signup).to_pydatetime().date(),
        )
        for cid, is_test, signup in zip(cust_id_arr, is_test_arr, signup_arr)
    }

    # 18 months in days — used for the eligibility floor.
    eighteen_months = timedelta(days=int(DOWNGRADE_MIN_TENURE_MONTHS * 30.4375))

    # Build the eligibility set: customers with at least one current active sub
    # (status='active' AND ended_at IS NULL/NaT) AND tenure ≥ 18 months AND
    # not is_test. We track each eligible customer's CURRENT active sub
    # row index in the subs DataFrame. Vectorize the active-sub filter, then
    # iterate the (much smaller) candidate set to apply the customer-level
    # tenure / is_test filters. "First encountered" semantics are preserved
    # because the candidate index array is in DataFrame row order.
    # Exclude refund-accounting rows (mrr <= 0) from the downgrade pool —
    # otherwise abs() in the new_mrr calc would silently flip a refund into a
    # paying replacement sub, breaking arr_contraction ground truth (CR fix).
    active_mask = (
        (subs["status"].to_numpy() == "active")
        & subs["ended_at"].isna().to_numpy()
        & (subs["mrr"].to_numpy() > 0)
    )
    candidate_idx = np.flatnonzero(active_mask)
    candidate_cids = subs["customer_id"].to_numpy()[candidate_idx]
    cust_to_active_sub_idx: dict[str, int] = {}
    for idx, cid in zip(candidate_idx, candidate_cids):
        if cid in cust_to_active_sub_idx:
            continue
        meta = cust_meta.get(cid)
        if meta is None:
            continue
        is_test, signup_date = meta
        if is_test:
            continue
        if (now_naive.date() - signup_date) < eighteen_months:
            continue
        cust_to_active_sub_idx[cid] = int(idx)

    eligible_count = len(cust_to_active_sub_idx)

    # Sort eligible customer_ids so the iteration order is deterministic (the
    # underlying dict is insertion-ordered, but we want a fully content-driven
    # ordering independent of any prior iteration quirks).
    eligible_cids = sorted(cust_to_active_sub_idx.keys())

    # Decide which eligible customers actually downgrade. We use np.random
    # via boolean mask to make the selection deterministic.
    selection_draws = np.random.random(eligible_count)
    selected_cids: list[str] = [
        eligible_cids[i] for i in range(eligible_count) if selection_draws[i] < DOWNGRADE_RATE
    ]
    selected_count = len(selected_cids)

    # Walk the selected customers, mutate their current active sub, and append
    # a new sub for each. We collect the new rows in a list and concat at end
    # to avoid pandas append-in-loop overhead.
    new_rows: list[dict] = []
    new_sub_seq = 0
    for cid in selected_cids:
        old_idx = cust_to_active_sub_idx[cid]
        old_started = subs.iat[old_idx, subs.columns.get_loc("started_at")]
        old_plan = subs.iat[old_idx, subs.columns.get_loc("plan")]
        old_mrr = subs.iat[old_idx, subs.columns.get_loc("mrr")]

        # Coerce old_started to a python datetime
        if isinstance(old_started, pd.Timestamp):
            old_started_dt = old_started.to_pydatetime()
        elif isinstance(old_started, np.datetime64):
            old_started_dt = pd.Timestamp(old_started).to_pydatetime()
        elif isinstance(old_started, datetime):
            old_started_dt = old_started
        else:
            # Defensive fallback: shouldn't happen in practice.
            old_started_dt = datetime(2024, 1, 1)

        # Customer signup_date — we need this to anchor the downgrade window
        # at months [6, 18] of tenure (NOT of the current sub's tenure;
        # downgrade is a customer-lifecycle event).
        _, signup_date = cust_meta[cid]
        signup_dt = datetime.combine(signup_date, datetime.min.time())

        # Pick the downgrade date uniformly between months 6 and 18 of tenure.
        # Use random.* (not np.random.*) here so the per-customer RNG draws
        # interleave with the existing per-event payload pattern.
        window_min_days = int(DOWNGRADE_WINDOW_MIN_MONTH * 30.4375)
        window_max_days = int(DOWNGRADE_WINDOW_MAX_MONTH * 30.4375)
        downgrade_offset_days = random.randint(window_min_days, window_max_days)
        downgrade_dt = signup_dt + timedelta(days=downgrade_offset_days)

        # Edge case: clamp downgrade_dt into the open interval
        # (old_started_dt, now_naive) so downgrade_dt is strictly after
        # the old sub's started_at AND strictly before NOW. Two separate
        # `if` clamps applied sequentially can collide on customers near
        # both bounds — e.g. signup ≥ NOW − 18mo + max_window_days could
        # have the lower-bound clamp push downgrade_dt forward past
        # now_naive, and the upper-bound clamp pull it back to NOW − 1d
        # ≤ old_started_dt, yielding ended_at ≤ started_at on the
        # mutated old sub. Combining into a single min(max(...), ...)
        # picks one bound consistently.
        downgrade_dt = min(
            max(downgrade_dt, old_started_dt + timedelta(days=1)),
            now_naive - timedelta(days=1),
        )

        # Compute new MRR: contract by 20-50% (i.e. retain 50-80%).
        retain = DOWNGRADE_MRR_RETAIN_LOW + (
            DOWNGRADE_MRR_RETAIN_HIGH - DOWNGRADE_MRR_RETAIN_LOW
        ) * random.random()
        # old_mrr is guaranteed > 0 by the eligibility filter above; no abs() needed.
        new_mrr = round(float(old_mrr) * retain, 2)

        # Plan downshift: enterprise→pro, pro→starter, else same.
        new_plan = _DOWNGRADE_PLAN_DOWNSHIFT.get(old_plan, old_plan)

        # Mutate the old sub in place: terminate at downgrade_dt, status='cancelled'.
        subs.iat[old_idx, subs.columns.get_loc("ended_at")] = downgrade_dt
        subs.iat[old_idx, subs.columns.get_loc("status")] = "cancelled"

        # Append the new sub.
        new_sub_id = f"sub_d6_{new_sub_seq:06d}"
        new_sub_seq += 1
        new_rows.append({
            "sub_id": new_sub_id,
            "customer_id": cid,
            "cust_id": cid if random.random() < 0.30 else None,
            "plan": new_plan,
            "mrr": new_mrr,
            "started_at": downgrade_dt,
            "ended_at": None,
            "status": "active",
            "inserted": downgrade_dt + timedelta(hours=random.randint(1, 72)),
        })

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        # Cast columns to match `subs` dtypes where possible (so the concat
        # doesn't widen ended_at to object). pd.concat handles mismatched
        # dtypes by upcasting, which is fine for our purposes.
        updated = pd.concat([subs, new_df], ignore_index=True)
    else:
        updated = subs

    return updated, {
        "eligible": eligible_count,
        "selected": selected_count,
        "new_subs_added": len(new_rows),
    }


# Lead-qualified event mix: weighted source distribution per spec.
_LEAD_SOURCES = ["webform", "sales_outreach", "product_signal"]
_LEAD_SOURCE_WEIGHTS = [0.50, 0.30, 0.20]


def gen_lead_qualified_events(users: pd.DataFrame, evt_offset: int) -> pd.DataFrame:
    """Generate ~6% lead_qualified events for users created in the last 6 months.

    Each selected user emits a single lead_qualified event with timestamp
    in [user.created_at, min(user.created_at + 14 days, NOW)]. Properties
    JSON carries `qualified_score` (40-90) and `source` (webform / sales_outreach /
    product_signal weighted). Keys mix camelCase / snake_case at
    CAMELCASE_PROPERTY_RATE for consistency with the engagement payloads.

    `evt_offset` is the starting integer for evt_<n> primary keys; this
    function uses evt_<evt_offset + i> to avoid collision with engagement
    (evt_0..evt_(N_EVENTS-1)) and opportunity (evt_N_EVENTS..) events.
    """
    now_naive = NOW.replace(tzinfo=None)
    lookback_days = int(LEAD_QUALIFIED_LOOKBACK_MONTHS * 30.4375)
    lookback_floor = now_naive - timedelta(days=lookback_days)

    user_id_arr = users["user_id"].to_numpy()
    user_customer_arr = users["customer_id"].to_numpy()
    user_created_arr = users["created_at"].to_numpy()

    # Identify in-window users: created_at >= NOW - 6 months.
    in_window_idx = np.flatnonzero(
        pd.to_datetime(user_created_arr).to_numpy() >= np.datetime64(lookback_floor)
    )

    # Roll a per-user random draw to decide who gets a lead_qualified event.
    selection_draws = np.random.random(len(in_window_idx))
    selected_idx = in_window_idx[selection_draws < LEAD_QUALIFIED_RATE]

    rows: list[dict] = []
    for i, ui in enumerate(selected_idx):
        user_id = str(user_id_arr[ui])
        customer_id = str(user_customer_arr[ui])
        created_at = pd.Timestamp(user_created_arr[ui]).to_pydatetime()

        # Event timestamp: uniform in [created_at, min(created_at+14d, NOW)].
        window_end = min(created_at + timedelta(days=LEAD_QUALIFIED_WINDOW_DAYS), now_naive)
        if window_end <= created_at:
            # Defensive: created_at exactly at NOW (shouldn't happen but cap to NOW).
            window_end = created_at + timedelta(seconds=1)
        span_seconds = max(1, int((window_end - created_at).total_seconds()))
        event_ts = created_at + timedelta(seconds=random.randint(0, span_seconds))

        # Mostly UTC, ~LA_TIMEZONE_EVENT_RATE share LA (matches engagement/opp pattern).
        timezone_str = "America/Los_Angeles" if random.random() < LA_TIMEZONE_EVENT_RATE else "UTC"

        # Inserted slightly after the event timestamp.
        inserted_at = event_ts + timedelta(seconds=random.randint(0, 3600))

        # Property payload — mix camelCase/snake_case at the same rate as
        # engagement events so D5's qualified_lead governed definition has
        # to handle both forms.
        qualified_score = random.randint(40, 90)
        source = random.choices(_LEAD_SOURCES, weights=_LEAD_SOURCE_WEIGHTS, k=1)[0]
        if random.random() < CAMELCASE_PROPERTY_RATE:
            payload = {
                "userId": user_id,
                "customerId": customer_id,
                "qualifiedScore": qualified_score,
                "source": source,
            }
        else:
            payload = {
                "user_id": user_id,
                "customer_id": customer_id,
                "qualified_score": qualified_score,
                "source": source,
            }

        rows.append({
            "event_id": f"evt_{evt_offset + i:09d}",
            "user_id": user_id,
            "customer_id": customer_id,
            "event_type": "lead_qualified",
            "timestamp": event_ts,
            "timezone": timezone_str,
            "inserted_at": inserted_at,
            "properties_jsonb": json.dumps(payload, separators=(",", ":")),
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# DDL — keep in sync with warehouse-schema-bare.sql
# ---------------------------------------------------------------------------

DDL_STATEMENTS = [
    """
    CREATE TABLE dim_customers (
        customer_id   VARCHAR PRIMARY KEY,
        cust_id       VARCHAR,
        company_name  VARCHAR,
        industry      VARCHAR,
        signup_date   DATE,
        joined_dt     DATE,
        churned_at    TIMESTAMP,
        is_test       BOOLEAN,
        plan_tier     VARCHAR,
        country       VARCHAR
    )
    """,
    """
    CREATE TABLE dim_users (
        user_id        VARCHAR PRIMARY KEY,
        customer_id    VARCHAR,
        account_id     VARCHAR,
        email          VARCHAR,
        role           VARCHAR,
        created_at     TIMESTAMP,
        joined_dt      DATE,
        last_active_at TIMESTAMP,
        deleted_at     TIMESTAMP
    )
    """,
    """
    CREATE TABLE fct_subscriptions (
        sub_id      VARCHAR PRIMARY KEY,
        customer_id VARCHAR,
        cust_id     VARCHAR,
        plan        VARCHAR,
        mrr         DOUBLE,
        started_at  TIMESTAMP,
        ended_at    TIMESTAMP,
        status      VARCHAR,
        inserted    TIMESTAMP
    )
    """,
    """
    CREATE TABLE fct_events (
        event_id         VARCHAR PRIMARY KEY,
        user_id          VARCHAR,
        customer_id      VARCHAR,
        event_type       VARCHAR,
        "timestamp"      TIMESTAMP,
        timezone         VARCHAR,
        inserted_at      TIMESTAMP,
        properties_jsonb JSON
    )
    """,
    """
    CREATE TABLE fct_invoices (
        invoice_id  VARCHAR PRIMARY KEY,
        customer_id VARCHAR,
        account_id  VARCHAR,
        amount      DOUBLE,
        paid_at     TIMESTAMP,
        due_at      TIMESTAMP,
        status      VARCHAR,
        processor   VARCHAR
    )
    """,
    """
    CREATE TABLE dim_products (
        product_id  VARCHAR PRIMARY KEY,
        name        VARCHAR,
        tier        VARCHAR,
        launched_at DATE
    )
    """,
    """
    CREATE TABLE fct_marketing_spend (
        period_month DATE,
        amount       DOUBLE,
        channel      VARCHAR,
        currency     VARCHAR
    )
    """,
    """
    CREATE TABLE fct_cogs (
        period_month DATE,
        amount       DOUBLE,
        source       VARCHAR
    )
    """,
]


# ---------------------------------------------------------------------------
# Verification — every check is an assertion with explicit bounds. (CR Major #2)
#
# Previously these printed-only; the script exited 0 even when a query failed
# or a messiness rate drifted wildly. Downstream consumers (D3 ground-truth
# SQL, D4 context blocks) cannot tolerate a silently-broken warehouse, so the
# script now exits non-zero (via main() returning 1) on any failure and
# enumerates every offending check.
#
# Bounds rationale per spec §3.3.1:
#  - Row counts: exact (==). The generator's row counts are constants, not
#    sampled — any drift is a real bug worth surfacing immediately.
#  - Messiness rates: ±10% relative envelope around the target rate, which
#    absorbs the natural variance of a stochastic generator at this row scale.
#  - Layer-presence asserts (e.g. "raw industry > normalized") use direct
#    inequality where the messiness contract is structural rather than rate-y.
# ---------------------------------------------------------------------------


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> float | int | None:
    """Run a 1-row 1-column query and return the scalar value."""
    row = con.execute(sql).fetchone()
    if row is None:
        return None
    return row[0]


def verify_warehouse(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Run every smoke check against the produced DuckDB; return list of failure messages.

    An empty return list means the warehouse passed every check. Caller is
    expected to fail the run when this returns a non-empty list.

    Diagnostic value (raw measured numbers per check) is printed to stdout so
    the operator running the generator can see at a glance that each layer
    landed at the expected rate, even on a passing run.
    """
    failures: list[str] = []

    def check_count(table: str, expected: int) -> None:
        actual = _scalar(con, f"SELECT COUNT(*) FROM {table}")  # noqa: S608
        ok = actual == expected
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] row count {table:<20s} actual={actual:>10,} expected={expected:>10,}")
        if not ok:
            failures.append(f"row count {table}: expected {expected:,}, got {actual:,}")

    def check_range(label: str, value: float, lo: float, hi: float, unit: str = "%") -> None:
        ok = lo <= value <= hi
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] {label}: {value:.3f}{unit} (bounds [{lo:.3f}, {hi:.3f}]{unit})")
        if not ok:
            failures.append(f"{label}: {value:.3f}{unit} outside [{lo:.3f}, {hi:.3f}]{unit}")

    # ---------- Row counts (5 exact + fct_subscriptions ranged + fct_events ranged + 2 D1.5 ranged) ----------
    check_count("dim_customers", N_CUSTOMERS)
    check_count("dim_users", N_USERS)
    # fct_subscriptions: N_SUBSCRIPTIONS D1 rows + ~7% × eligible-customer count
    # of D6 downgrade additions (each downgrade appends one new active sub).
    # The eligible-customer count depends on cohort distribution; bounds
    # absorb a ±2pp envelope around the 7% target across ~2,500-3,000 eligible
    # customers, i.e. ~125-250 new subs. Using a generous [50, 500] window
    # so the verification still flags egregious drift but doesn't fail on
    # benign sampling variance.
    actual_subs = _scalar(con, "SELECT COUNT(*) FROM fct_subscriptions")
    expected_subs_min = N_SUBSCRIPTIONS + 50
    expected_subs_max = N_SUBSCRIPTIONS + 500
    subs_ok = expected_subs_min <= actual_subs <= expected_subs_max
    print(f"  [{'OK' if subs_ok else 'FAIL'}] row count fct_subscriptions   "
          f"actual={actual_subs:>10,} expected ∈ [{expected_subs_min:,}, {expected_subs_max:,}] "
          f"(N_SUBSCRIPTIONS + D6 downgrade additions)")
    if not subs_ok:
        failures.append(
            f"row count fct_subscriptions: expected in [{expected_subs_min:,}, "
            f"{expected_subs_max:,}], got {actual_subs:,}"
        )
    # fct_events: N_EVENTS engagement rows (exact) + ~_OPP_TARGET_EVENTS
    # opportunity rows (variable — the opp generator stops once it crosses
    # the target, so total can exceed it by up to 2 events) + D6
    # lead_qualified rows (~6% of in-window users; ~5K-7K rows). The
    # engagement count is asserted exactly via the L1 camel+snake total
    # check below.
    actual_events = _scalar(con, "SELECT COUNT(*) FROM fct_events")
    expected_events_min = N_EVENTS + _OPP_TARGET_EVENTS + 1_000  # conservative D6 floor
    expected_events_max = N_EVENTS + _OPP_TARGET_EVENTS + 15_000  # conservative D6 ceiling
    events_ok = expected_events_min <= actual_events <= expected_events_max
    print(f"  [{'OK' if events_ok else 'FAIL'}] row count fct_events            "
          f"actual={actual_events:>10,} expected ∈ [{expected_events_min:,}, "
          f"{expected_events_max:,}] (N_EVENTS + opp + lead_qualified)")
    if not events_ok:
        failures.append(
            f"row count fct_events: expected in [{expected_events_min:,}, "
            f"{expected_events_max:,}], got {actual_events:,}"
        )
    check_count("fct_invoices", N_INVOICES)
    check_count("dim_products", N_PRODUCTS)
    # D1.5 new tables — ±5% tolerance per task spec
    spend_count = _scalar(con, "SELECT COUNT(*) FROM fct_marketing_spend")
    spend_ok = N_SPEND_MONTHS * 0.95 <= spend_count <= N_SPEND_MONTHS * 1.05
    print(f"  [{'OK' if spend_ok else 'FAIL'}] row count fct_marketing_spend "
          f"actual={spend_count:>10,} expected≈{N_SPEND_MONTHS} (±5%)")
    if not spend_ok:
        failures.append(
            f"row count fct_marketing_spend: expected ≈{N_SPEND_MONTHS} (±5%), got {spend_count:,}"
        )
    cogs_count = _scalar(con, "SELECT COUNT(*) FROM fct_cogs")
    # COGS has more rows than months — ~35% of months get TWO rows (double-count messiness),
    # so total is ~1.35 * N_COGS_MONTHS. Allow [N, 2N] to absorb generator variance.
    cogs_ok = N_COGS_MONTHS <= cogs_count <= N_COGS_MONTHS * 2
    print(f"  [{'OK' if cogs_ok else 'FAIL'}] row count fct_cogs              "
          f"actual={cogs_count:>10,} expected ∈ [{N_COGS_MONTHS}, {N_COGS_MONTHS * 2}] "
          f"(double-counting layer adds rows)")
    if not cogs_ok:
        failures.append(
            f"row count fct_cogs: expected in [{N_COGS_MONTHS}, {N_COGS_MONTHS * 2}], got {cogs_count:,}"
        )

    # ---------- L1 naming inconsistency ----------
    cust_id_pop = _scalar(con, "SELECT COUNT(*) FROM dim_customers WHERE cust_id IS NOT NULL")
    cust_id_pct = 100.0 * cust_id_pop / N_CUSTOMERS
    target = CUST_ID_LEGACY_RATE * 100
    check_range("L1 dim_customers.cust_id legacy populated",
                cust_id_pct, target * 0.9, target * 1.1)

    account_id_pop = _scalar(con, "SELECT COUNT(*) FROM dim_users WHERE account_id IS NOT NULL")
    account_id_pct = 100.0 * account_id_pop / N_USERS
    target = ACCOUNT_ID_LEGACY_RATE * 100
    check_range("L1 dim_users.account_id legacy populated",
                account_id_pct, target * 0.9, target * 1.1)

    # Engagement-event camelCase / snake_case (userId / user_id keys).
    # Opportunity events use a different key family (oppId / opp_id);
    # they're checked separately in the D1.5 block below.
    camel_count = _scalar(con,
                          "SELECT COUNT(*) FROM fct_events "
                          "WHERE event_type IN ('login','page_view','action_performed',"
                          "'feature_used','signup','logout') "
                          "AND properties_jsonb LIKE '%\"userId\"%'")  # noqa: S608
    snake_count = _scalar(con,
                          "SELECT COUNT(*) FROM fct_events "
                          "WHERE event_type IN ('login','page_view','action_performed',"
                          "'feature_used','signup','logout') "
                          "AND properties_jsonb LIKE '%\"user_id\"%'")  # noqa: S608
    if camel_count + snake_count != N_EVENTS:
        failures.append(
            f"L1 properties_jsonb keys must be camel XOR snake (engagement events); "
            f"got camel={camel_count:,} snake={snake_count:,} total={camel_count + snake_count:,} "
            f"(expected {N_EVENTS:,})"
        )
    camel_pct = 100.0 * camel_count / N_EVENTS
    target = CAMELCASE_PROPERTY_RATE * 100
    check_range("L1 properties_jsonb camelCase share (engagement)",
                camel_pct, target * 0.9, target * 1.1)

    # ---------- L2 orphan rows ----------
    orphan_event_pct = _scalar(con,
                               "SELECT 100.0 * SUM(CASE WHEN u.user_id IS NULL THEN 1 ELSE 0 END) / COUNT(*) "
                               "FROM fct_events e LEFT JOIN dim_users u ON u.user_id = e.user_id")
    target = ORPHAN_EVENT_RATE * 100
    check_range("L2 fct_events orphan rate",
                orphan_event_pct, target * 0.9, target * 1.1)

    orphan_inv_pct = _scalar(con,
                             "SELECT 100.0 * SUM(CASE WHEN c.customer_id IS NULL THEN 1 ELSE 0 END) / COUNT(*) "
                             "FROM fct_invoices i LEFT JOIN dim_customers c ON c.customer_id = i.customer_id")
    target = ORPHAN_INVOICE_RATE * 100
    check_range("L2 fct_invoices orphan rate",
                orphan_inv_pct, target * 0.9, target * 1.1)

    # ---------- L3 mixed timezones / processors ----------
    la_pct = _scalar(con,
                     "SELECT 100.0 * SUM(CASE WHEN timezone = 'America/Los_Angeles' THEN 1 ELSE 0 END) / COUNT(*) "
                     "FROM fct_events")
    target = LA_TIMEZONE_EVENT_RATE * 100
    check_range("L3 fct_events America/Los_Angeles share",
                la_pct, target * 0.9, target * 1.1)

    braintree_pct = _scalar(con,
                            "SELECT 100.0 * SUM(CASE WHEN processor = 'legacy_braintree' THEN 1 ELSE 0 END) / COUNT(*) "
                            "FROM fct_invoices")
    target = NY_INVOICE_PROCESSOR_RATE * 100
    check_range("L3 fct_invoices legacy_braintree share",
                braintree_pct, target * 0.9, target * 1.1)

    # ---------- L4 soft-delete divergence ----------
    # The whole point of this layer: dim and fact tables disagree about cancellation.
    # We assert the divergence is non-trivial (both populated, and they differ
    # by a meaningful amount).
    churned_via_dim = _scalar(con, "SELECT COUNT(*) FROM dim_customers WHERE churned_at IS NOT NULL")
    cancelled_via_sub = _scalar(con,
                                "SELECT COUNT(DISTINCT customer_id) FROM fct_subscriptions "
                                "WHERE status = 'cancelled'")
    divergence = abs(cancelled_via_sub - churned_via_dim)
    l4_ok = churned_via_dim > 0 and cancelled_via_sub > 0 and divergence >= 500
    print(f"  [{'OK' if l4_ok else 'FAIL'}] L4 dim/fact churn divergence: "
          f"churned_via_dim={churned_via_dim:,}, cancelled_via_sub={cancelled_via_sub:,}, |Δ|={divergence:,} (need ≥500)")
    if churned_via_dim <= 0:
        failures.append(f"L4 dim_customers.churned_at is empty (got {churned_via_dim}) — divergence layer broken")
    if cancelled_via_sub <= 0:
        failures.append(f"L4 fct_subscriptions cancelled count is empty (got {cancelled_via_sub}) — divergence layer broken")
    if churned_via_dim > 0 and cancelled_via_sub > 0 and divergence < 500:
        failures.append(
            f"L4 soft-delete divergence too small: |cancelled_via_sub - churned_via_dim| = "
            f"{divergence} < 500 (the dim/fact mismatch is the layer)"
        )

    deleted_at_set = _scalar(con,
                             "SELECT COUNT(*) FROM dim_users WHERE deleted_at IS NOT NULL")
    stale_users = _scalar(con,
                          "SELECT COUNT(*) FROM dim_users WHERE last_active_at < DATE '2026-01-01'")
    sparsity_ok = stale_users > 0 and deleted_at_set <= stale_users
    print(f"  [{'OK' if sparsity_ok else 'FAIL'}] L4 dim_users.deleted_at sparsity: "
          f"deleted_at_set={deleted_at_set:,} stale_users={stale_users:,} (need set ≤ stale)")
    if stale_users <= 0:
        failures.append(f"L4 stale-user count is zero (got {stale_users}) — divergence layer broken")
    elif deleted_at_set > stale_users:
        failures.append(
            f"L4 dim_users.deleted_at populated for MORE rows than are stale "
            f"({deleted_at_set:,} > {stale_users:,}); deleted_at sparsity layer broken"
        )

    # ---------- L5 half-broken values ----------
    neg_mrr_pct = _scalar(con,
                          "SELECT 100.0 * SUM(CASE WHEN mrr < 0 THEN 1 ELSE 0 END) / COUNT(*) "
                          "FROM fct_subscriptions")
    target = NEGATIVE_MRR_RATE * 100
    check_range("L5 fct_subscriptions negative MRR rate",
                neg_mrr_pct, target * 0.9, target * 1.1)

    bad_email_pct = _scalar(con,
                            "SELECT 100.0 * SUM(CASE WHEN email IS NULL OR email = 'noreply@example.com' "
                            "THEN 1 ELSE 0 END) / COUNT(*) FROM dim_users")
    target = NULL_EMAIL_RATE * 100
    check_range("L5 dim_users null/noreply email rate",
                bad_email_pct, target * 0.9, target * 1.1)

    distinct_raw = _scalar(con, "SELECT COUNT(DISTINCT industry) FROM dim_customers")
    distinct_norm = _scalar(con,
                            "SELECT COUNT(DISTINCT LOWER(REPLACE(REPLACE(TRIM(industry), '-', ' '), '_', ' '))) "
                            "FROM dim_customers")
    industry_ok = distinct_raw > distinct_norm
    print(f"  [{'OK' if industry_ok else 'FAIL'}] L5 industry messiness: "
          f"distinct_raw={distinct_raw} > distinct_normalized={distinct_norm}")
    if not industry_ok:
        failures.append(
            f"L5 industry messiness layer collapsed: distinct_raw={distinct_raw} <= distinct_normalized={distinct_norm}"
        )

    # ---------- L6 near-duplicate rows ----------
    near_dupe_count = _scalar(con,
                              "SELECT COUNT(*) FROM ("
                              "  SELECT LOWER(TRIM(REGEXP_REPLACE(company_name, ',\\s*inc\\.', ''))) AS norm, "
                              "         COUNT(*) AS n "
                              "  FROM dim_customers GROUP BY 1 HAVING COUNT(*) > 1"
                              ")")
    expected = int(N_CUSTOMERS * NEAR_DUPLICATE_RATE)  # 50
    check_range("L6 near-duplicate company collisions",
                float(near_dupe_count), expected * 0.9, expected * 1.1, unit=" rows")

    # ---------- L7 late-arriving facts ----------
    late_pct = _scalar(con,
                       "SELECT 100.0 * SUM(CASE WHEN \"timestamp\" < inserted_at - INTERVAL 1 DAY "
                       "THEN 1 ELSE 0 END) / COUNT(*) FROM fct_events")
    target = LATE_ARRIVING_EVENT_RATE * 100
    check_range("L7 fct_events late-arriving rate",
                late_pct, target * 0.9, target * 1.1)

    # ---------- Test-account contamination (5%) ----------
    test_pct = _scalar(con,
                       "SELECT 100.0 * SUM(CASE WHEN is_test THEN 1 ELSE 0 END) / COUNT(*) "
                       "FROM dim_customers")
    target = TEST_ACCOUNT_RATE * 100
    check_range("Test-account contamination rate",
                test_pct, target * 0.9, target * 1.1)

    # ---------- D1.5: fct_marketing_spend currency mix ----------
    # Target USD ~80%, EUR ~20% with ±5pp absolute tolerance.
    usd_pct = _scalar(con,
                      "SELECT 100.0 * SUM(CASE WHEN currency = 'USD' THEN 1 ELSE 0 END) / COUNT(*) "
                      "FROM fct_marketing_spend")
    target = _SPEND_USD_RATE * 100
    check_range("D1.5 fct_marketing_spend USD share",
                usd_pct, target - 5.0, target + 5.0)

    # ---------- D1.5: fct_cogs source double-population ----------
    # ≥30% of months should have BOTH a cost_of_revenue row AND a
    # cost_of_goods_sold row (intentional accounting double-count).
    both_months = _scalar(con,
                          "SELECT COUNT(*) FROM ("
                          "  SELECT period_month "
                          "  FROM fct_cogs "
                          "  GROUP BY period_month "
                          "  HAVING COUNT(DISTINCT source) >= 2"
                          ")")
    distinct_months = _scalar(con,
                              "SELECT COUNT(DISTINCT period_month) FROM fct_cogs")
    both_share_pct = 100.0 * (both_months or 0) / max(distinct_months or 1, 1)
    both_ok = both_share_pct >= 30.0
    print(f"  [{'OK' if both_ok else 'FAIL'}] D1.5 fct_cogs dual-source months: "
          f"{both_months}/{distinct_months} = {both_share_pct:.1f}% (need ≥30%)")
    if not both_ok:
        failures.append(
            f"D1.5 fct_cogs dual-source share too low: {both_share_pct:.1f}% < 30% — "
            f"double-counting messiness layer is broken"
        )

    # ---------- D1.5: opportunity events share of fct_events ----------
    opp_count = _scalar(con,
                        "SELECT COUNT(*) FROM fct_events "
                        "WHERE event_type IN ('opp_created', 'opp_won', 'opp_lost', "
                        "'opp_no_decision', 'opp_stage_advanced')")
    total_events = _scalar(con, "SELECT COUNT(*) FROM fct_events")
    opp_share_pct = 100.0 * (opp_count or 0) / max(total_events or 1, 1)
    target = OPP_EVENT_SHARE * 100  # 5%
    # ±0.5pp absolute tolerance per task spec
    check_range("D1.5 opportunity events share of fct_events",
                opp_share_pct, target - 0.5, target + 0.5)

    # ---------- D1.5: opportunity events camelCase JSON-key share ----------
    # Engagement events use userId/user_id; opp events use oppId/opp_id.
    # Target is ~50% camelCase among opp events with ±5pp tolerance.
    opp_camel = _scalar(con,
                        "SELECT COUNT(*) FROM fct_events "
                        "WHERE event_type IN ('opp_created', 'opp_won', 'opp_lost', "
                        "'opp_no_decision', 'opp_stage_advanced') "
                        "AND properties_jsonb LIKE '%\"oppId\"%'")  # noqa: S608
    opp_snake = _scalar(con,
                        "SELECT COUNT(*) FROM fct_events "
                        "WHERE event_type IN ('opp_created', 'opp_won', 'opp_lost', "
                        "'opp_no_decision', 'opp_stage_advanced') "
                        "AND properties_jsonb LIKE '%\"opp_id\"%'")  # noqa: S608
    if (opp_camel or 0) + (opp_snake or 0) != opp_count:
        failures.append(
            f"D1.5 opp events: properties_jsonb keys must be camel XOR snake; "
            f"got camel={opp_camel:,} snake={opp_snake:,} total={(opp_camel or 0) + (opp_snake or 0):,} "
            f"(opp_count {opp_count:,})"
        )
    opp_camel_pct = 100.0 * (opp_camel or 0) / max(opp_count or 1, 1)
    target = OPP_CAMELCASE_RATE * 100  # 50%
    check_range("D1.5 opp events camelCase share",
                opp_camel_pct, target - 5.0, target + 5.0)

    # ---------- D1.5: opportunity terminal-event uniqueness ----------
    # Every opp_id must have exactly ONE terminal event (won / lost /
    # no_decision). The COALESCE handles the camelCase/snake_case mix.
    multi_terminal = _scalar(con,
                             "SELECT COUNT(*) FROM ("
                             "  SELECT COALESCE(properties_jsonb->>'opp_id', "
                             "                  properties_jsonb->>'oppId') AS oid, "
                             "         COUNT(*) AS n "
                             "  FROM fct_events "
                             "  WHERE event_type IN ('opp_won', 'opp_lost', 'opp_no_decision') "
                             "  GROUP BY oid "
                             "  HAVING COUNT(*) > 1"
                             ")")
    no_terminal = _scalar(con,
                          "WITH all_opps AS ("
                          "  SELECT DISTINCT COALESCE(properties_jsonb->>'opp_id', "
                          "                           properties_jsonb->>'oppId') AS oid "
                          "  FROM fct_events WHERE event_type = 'opp_created'"
                          "), terminals AS ("
                          "  SELECT DISTINCT COALESCE(properties_jsonb->>'opp_id', "
                          "                           properties_jsonb->>'oppId') AS oid "
                          "  FROM fct_events "
                          "  WHERE event_type IN ('opp_won', 'opp_lost', 'opp_no_decision')"
                          ") "
                          "SELECT COUNT(*) FROM all_opps "
                          "WHERE oid NOT IN (SELECT oid FROM terminals)")
    terminal_ok = (multi_terminal or 0) == 0 and (no_terminal or 0) == 0
    print(f"  [{'OK' if terminal_ok else 'FAIL'}] D1.5 opp terminal-event uniqueness: "
          f"multi-terminal opps={multi_terminal}, opps with no terminal={no_terminal} "
          f"(need 0 multi, 0 missing)")
    if (multi_terminal or 0) > 0:
        failures.append(
            f"D1.5 opp terminal-event uniqueness: {multi_terminal} opp_id(s) have >1 terminal event"
        )
    if (no_terminal or 0) > 0:
        failures.append(
            f"D1.5 opp terminal-event coverage: {no_terminal} opp_id(s) have no terminal event "
            f"(expected 0; each opportunity must have exactly one terminal event)"
        )

    # ---------- D6: sub-replacement-downgrade pattern ----------
    # A downgrade pair is defined as: customer has two subs A and B where
    # A.ended_at == B.started_at (within 1 day), B.mrr < A.mrr × 0.85
    # (at least 15% MRR drop), B.status == 'active'. Assert the count is
    # ≥6.5% of eligible customers (giving ~0.5pp headroom under the 7%
    # target).
    #
    # Eligible customer count denominator (matches the generator's
    # eligibility filter exactly): non-test customers with tenure ≥
    # 18 months AND at least one sub with status='active' AND
    # ended_at IS NULL AND mrr > 0. apply_downgrade_lifecycle()
    # selects from this pool; the verification denominator must use
    # the same three-way active-sub filter or it overcounts and
    # produces false negatives in the 6.5% rate checks below.
    eligible_for_downgrade = _scalar(con,
        "SELECT COUNT(DISTINCT s.customer_id) "
        "FROM fct_subscriptions s "
        "JOIN dim_customers c ON c.customer_id = s.customer_id "
        f"WHERE c.is_test = false "
        f"  AND c.signup_date <= DATE '{NOW.date().isoformat()}' "
        f"             - INTERVAL '{DOWNGRADE_MIN_TENURE_MONTHS} months' "
        f"  AND s.status = 'active' "
        f"  AND s.ended_at IS NULL "
        f"  AND s.mrr > 0"
    )
    downgrade_pairs = _scalar(con,
        "SELECT COUNT(*) FROM ("
        "  SELECT a.customer_id "
        "  FROM fct_subscriptions a "
        "  JOIN fct_subscriptions b "
        "    ON a.customer_id = b.customer_id "
        "    AND a.sub_id != b.sub_id "
        "    AND ABS(EPOCH(a.ended_at) - EPOCH(b.started_at)) <= 86400 "
        "  WHERE a.ended_at IS NOT NULL "
        "    AND b.started_at IS NOT NULL "
        "    AND b.status = 'active' "
        "    AND b.mrr < ABS(a.mrr) * 0.85 "
        "  GROUP BY a.customer_id"
        ")"
    )
    downgrade_pct = 100.0 * (downgrade_pairs or 0) / max(eligible_for_downgrade or 1, 1)
    # Target ≥6.5% of eligible (the spec's headroom-from-7% floor).
    downgrade_ok = downgrade_pct >= 6.5
    print(f"  [{'OK' if downgrade_ok else 'FAIL'}] D6 sub-replacement-downgrade pattern: "
          f"{downgrade_pairs}/{eligible_for_downgrade} = {downgrade_pct:.2f}% "
          f"of eligible (need ≥6.5%)")
    if not downgrade_ok:
        failures.append(
            f"D6 sub-replacement-downgrade rate: {downgrade_pct:.2f}% < 6.5% "
            f"of eligible customers ({downgrade_pairs}/{eligible_for_downgrade})"
        )

    # ---------- D6: attributable downgrade rate (sub_d6_* IDs) ----------
    # The check above measures the broad pattern but the pre-D6 generator
    # already produces such pairs incidentally via ±25% MRR noise across
    # multi-sub customers, so the broad check passes trivially even at zero
    # D6 injection. This stronger check counts ONLY sub_d6_* rows — the
    # rows the D6 generator explicitly inserted — and asserts the rate is
    # at least 6.5% of eligible customers. This is the assertion that
    # actually fails if the D6 mutation is broken or skipped.
    d6_attributable = _scalar(con,
        "SELECT COUNT(DISTINCT customer_id) FROM fct_subscriptions "
        "WHERE sub_id LIKE 'sub_d6_%'"
    )
    d6_attr_pct = 100.0 * (d6_attributable or 0) / max(eligible_for_downgrade or 1, 1)
    d6_attr_ok = d6_attr_pct >= 6.5
    print(f"  [{'OK' if d6_attr_ok else 'FAIL'}] D6 attributable downgrade rate: "
          f"{d6_attributable}/{eligible_for_downgrade} = {d6_attr_pct:.2f}% "
          f"of eligible (sub_d6_* rows; need ≥6.5%)")
    if not d6_attr_ok:
        failures.append(
            f"D6 attributable downgrade rate: {d6_attr_pct:.2f}% < 6.5% "
            f"of eligible customers ({d6_attributable}/{eligible_for_downgrade})"
        )

    # ---------- D6: lead_qualified events on new users ----------
    # Denominator: users created in the last 6 months (in-window for the
    # 14-day-post-signup check given today's NOW). Numerator: users with a
    # lead_qualified event whose timestamp is within 14 days post-signup.
    # Assert ≥5.5% (giving headroom under the 6% generator target).
    lookback_iso = (NOW - timedelta(days=int(LEAD_QUALIFIED_LOOKBACK_MONTHS * 30.4375))).date().isoformat()
    in_window_users = _scalar(con,
        f"SELECT COUNT(*) FROM dim_users WHERE created_at >= TIMESTAMP '{lookback_iso} 00:00:00'"
    )
    lq_users = _scalar(con,
        "SELECT COUNT(DISTINCT u.user_id) "
        "FROM dim_users u "
        "JOIN fct_events e ON e.user_id = u.user_id "
        f"WHERE u.created_at >= TIMESTAMP '{lookback_iso} 00:00:00' "
        "  AND e.event_type = 'lead_qualified' "
        "  AND e.\"timestamp\" >= u.created_at "
        f"  AND e.\"timestamp\" <= u.created_at + INTERVAL '{LEAD_QUALIFIED_WINDOW_DAYS} days'"
    )
    lq_pct = 100.0 * (lq_users or 0) / max(in_window_users or 1, 1)
    # Target ≥5.5% of in-window users (the spec's headroom-from-6% floor).
    lq_ok = lq_pct >= 5.5
    print(f"  [{'OK' if lq_ok else 'FAIL'}] D6 lead_qualified rate (in-window users): "
          f"{lq_users}/{in_window_users} = {lq_pct:.2f}% "
          f"(denominator: dim_users.created_at >= {lookback_iso}; need ≥5.5%)")
    if not lq_ok:
        failures.append(
            f"D6 lead_qualified rate: {lq_pct:.2f}% < 5.5% "
            f"of in-window users ({lq_users}/{in_window_users})"
        )

    return failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if OUT_DB.exists():
        OUT_DB.unlink()

    overall_t0 = time.perf_counter()
    print("ClariLayer Trust Benchmark — synthetic warehouse generator")
    print(f"  seed       : {SEED}")
    print(f"  output     : {OUT_DB}")
    print(f"  row targets: customers={N_CUSTOMERS:,}  users={N_USERS:,}  "
          f"subs={N_SUBSCRIPTIONS:,}+~D6 (engagement+downgrade)  "
          f"events={N_EVENTS:,}+{_OPP_TARGET_EVENTS:,}+~D6 (engagement+opp+lead_qualified)  "
          f"invoices={N_INVOICES:,}  products={N_PRODUCTS}  "
          f"spend={N_SPEND_MONTHS}  cogs≈{N_COGS_MONTHS}")
    print()

    print("Generating tables in memory ...")
    with step("dim_products"):
        products = gen_dim_products()
    with step("dim_customers"):
        customers = gen_dim_customers()
    with step("dim_users"):
        users = gen_dim_users(customers)
    with step("fct_subscriptions"):
        subs = gen_fct_subscriptions(customers)
    with step("fct_invoices"):
        invoices = gen_fct_invoices(customers, subs)
    with step(f"fct_events bulk arrays (5M rows — streamed in {EVENTS_BATCH_SIZE:,}-row batches)"):
        events_bulk = _gen_fct_events_bulk(customers, users)
    # NOTE: D1.5 generators (fct_marketing_spend, fct_cogs, opp events)
    # run AFTER the engagement-event INSERT loop completes — see the
    # block right after the streaming INSERT below. They CANNOT run here
    # because the engagement INSERT loop uses _build_events_batch which
    # consumes the global `random` module state for per-event payloads.
    # If we generated D1.5 tables here, we'd steal RNG that would
    # otherwise have been consumed by _build_events_batch, breaking the
    # byte-for-byte content match against D1's fct_events hash.

    print()
    print("Writing to DuckDB ...")
    con = duckdb.connect(str(OUT_DB))
    try:
        for ddl in DDL_STATEMENTS:
            con.execute(ddl)

        with step("INSERT dim_products"):
            con.register("products_df", products)
            con.execute("INSERT INTO dim_products SELECT * FROM products_df")
            con.unregister("products_df")
        with step("INSERT dim_customers"):
            con.register("customers_df", customers)
            con.execute("INSERT INTO dim_customers SELECT * FROM customers_df")
            con.unregister("customers_df")
        with step("INSERT dim_users"):
            con.register("users_df", users)
            con.execute("INSERT INTO dim_users SELECT * FROM users_df")
            con.unregister("users_df")
        # NOTE: fct_subscriptions INSERT is deferred until AFTER D6's
        # downgrade-lifecycle mutation (which runs post-D1.5). The mutation
        # both updates ended_at/status on existing rows and appends new
        # downgrade rows, so we INSERT once at the end with the final
        # mutated DataFrame. Determinism is preserved because no RNG is
        # consumed between subs generation and the deferred mutation point.
        with step("INSERT fct_invoices"):
            con.register("invoices_df", invoices)
            con.execute("INSERT INTO fct_invoices SELECT * FROM invoices_df")
            con.unregister("invoices_df")
        with step(f"INSERT fct_events ({N_EVENTS:,} rows in {EVENTS_BATCH_SIZE:,}-row batches)"):
            n_batches = (N_EVENTS + EVENTS_BATCH_SIZE - 1) // EVENTS_BATCH_SIZE
            for batch_idx in range(n_batches):
                batch_start = batch_idx * EVENTS_BATCH_SIZE
                batch_end = min(batch_start + EVENTS_BATCH_SIZE, N_EVENTS)
                events_batch_df = _build_events_batch(events_bulk, batch_start, batch_end)
                con.register("events_batch_df", events_batch_df)
                con.execute("INSERT INTO fct_events SELECT * FROM events_batch_df")
                con.unregister("events_batch_df")
                # Drop reference so the batch DataFrame + properties_json list are eligible for GC
                # before the next batch allocates. Critical for keeping peak RSS bounded.
                del events_batch_df
        # Drop bulk arrays as soon as the last batch is INSERTed.
        del events_bulk

        # ---- D1.5 generation runs HERE (post-engagement-INSERT) so the
        # global random state has consumed exactly the same RNG sequence
        # as D1 by this point. _build_events_batch's per-event
        # random.choice/randint calls have all fired. Any RNG consumed
        # below has no effect on the engagement-event content. ----
        with step("fct_marketing_spend"):
            marketing_spend = gen_fct_marketing_spend()
        with step("fct_cogs"):
            cogs = gen_fct_cogs(invoices)
        with step(f"opp events ({_OPP_TARGET_EVENTS:,} rows — appended to fct_events)"):
            opp_events = gen_opp_events(users, customers, evt_offset=N_EVENTS)

        with step(f"INSERT fct_events opp events ({len(opp_events):,} rows appended)"):
            con.register("opp_events_df", opp_events)
            con.execute("INSERT INTO fct_events SELECT * FROM opp_events_df")
            con.unregister("opp_events_df")

        with step("INSERT fct_marketing_spend"):
            con.register("marketing_spend_df", marketing_spend)
            con.execute("INSERT INTO fct_marketing_spend SELECT * FROM marketing_spend_df")
            con.unregister("marketing_spend_df")
        with step("INSERT fct_cogs"):
            con.register("cogs_df", cogs)
            con.execute("INSERT INTO fct_cogs SELECT * FROM cogs_df")
            con.unregister("cogs_df")

        # ---- D6 lifecycle additions: downgrade pattern + lead_qualified events.
        # These run AFTER all D1 and D1.5 generators so the global RNG state
        # is downstream of every prior generator. The 6 D1 tables and the 2
        # D1.5 tables (fct_marketing_spend, fct_cogs) hash identically pre-D6
        # because nothing here is invoked before them. fct_subscriptions and
        # fct_events DO change — by design (downgrade mutation; lead_qualified
        # append). ----
        opp_event_count = len(opp_events)
        with step(
            f"D6 apply_downgrade_lifecycle (~{int(DOWNGRADE_RATE * 100)}% of eligible non-test, "
            f"≥{DOWNGRADE_MIN_TENURE_MONTHS}-month-tenured customers with active subs)"
        ):
            subs, downgrade_stats = apply_downgrade_lifecycle(subs, customers)
        print(
            f"  D6 downgrade: eligible={downgrade_stats['eligible']:,}, "
            f"selected={downgrade_stats['selected']:,}, "
            f"new_subs_added={downgrade_stats['new_subs_added']:,}"
        )
        with step(
            f"D6 gen_lead_qualified_events (~{int(LEAD_QUALIFIED_RATE * 100)}% of in-window users; "
            f"appended to fct_events at evt_offset={N_EVENTS + opp_event_count})"
        ):
            lead_qualified_events = gen_lead_qualified_events(
                users, evt_offset=N_EVENTS + opp_event_count,
            )

        with step(
            f"INSERT fct_subscriptions ({len(subs):,} rows — D1 + D6 downgrade additions)"
        ):
            con.register("subs_df", subs)
            con.execute("INSERT INTO fct_subscriptions SELECT * FROM subs_df")
            con.unregister("subs_df")
        with step(
            f"INSERT fct_events lead_qualified events ({len(lead_qualified_events):,} rows appended)"
        ):
            con.register("lead_qualified_df", lead_qualified_events)
            con.execute("INSERT INTO fct_events SELECT * FROM lead_qualified_df")
            con.unregister("lead_qualified_df")

        con.execute("CHECKPOINT")

        print()
        print("Row counts:")
        for table in ["dim_customers", "dim_users", "fct_subscriptions", "fct_events",
                      "fct_invoices", "dim_products", "fct_marketing_spend", "fct_cogs"]:
            # SAFE — table is one of a hardcoded list, not user input.
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            print(f"  {table:<24} {n:>10,}")

        print()
        print("Messiness verification (assertions with bounds):")
        verify_failures = verify_warehouse(con)
    finally:
        con.close()

    elapsed = time.perf_counter() - overall_t0
    size_mb = OUT_DB.stat().st_size / (1024 * 1024)
    print()

    if verify_failures:
        print(f"FAIL: {len(verify_failures)} verification check(s) failed:")
        for f in verify_failures:
            print(f"  - {f}")
        print()
        print(f"Wrote {OUT_DB} ({size_mb:.1f} MB) in {elapsed:.1f}s but it is NOT trustworthy.")
        return 1

    print("PASS: all verification checks succeeded.")
    print(f"Done. Wrote {OUT_DB} ({size_mb:.1f} MB) in {elapsed:.1f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

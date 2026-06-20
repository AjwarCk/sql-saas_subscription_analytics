"""
SaaS Subscription Analytics — Star Schema practice database.

Business domain: B2B SaaS company selling seat-based subscriptions
across 3 plan tiers (Starter / Growth / Enterprise), monthly or annual
billing, with trials, upgrades/downgrades, failed payments, dunning,
cancellations and win-backs.

This is modeled as a STAR SCHEMA:
    - dim_date            : full date dimension (for trend/period KPIs)
    - dim_customer         : SCD2 (tracks plan/segment changes over time)
    - dim_plan             : plan tier reference (current attributes)
    - dim_sales_rep        : owning rep / channel
    - fact_subscription_events : grain = one row per subscription state
                                  change event (signup, trial_end,
                                  upgrade, downgrade, payment_failed,
                                  reactivated, cancelled)
    - fact_daily_subscription_snapshot : grain = one row per
                                  subscription per day (the
                                  "snapshot fact" used for MRR-at-a-point
                                  -in-time, the standard SaaS pattern)
    - fact_invoice_payments : grain = one row per invoice/payment
                                  attempt (for revenue recognition,
                                  failed-payment / dunning KPIs)
    - fact_product_usage   : grain = one row per customer per day
                                  (login_count, feature usage) for
                                  engagement / health-score KPIs

Run:
    python saas_subscription_dw.py
Then:
    import sqlite3
    conn = sqlite3.connect('saas_subscription_dw.db')
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import date, timedelta
import random

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / ".." / "database"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "saas_subscription_dw.db"

random.seed(42)


def build_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript(
        """
        DROP TABLE IF EXISTS fact_product_usage;
        DROP TABLE IF EXISTS fact_invoice_payments;
        DROP TABLE IF EXISTS fact_daily_subscription_snapshot;
        DROP TABLE IF EXISTS fact_subscription_events;
        DROP TABLE IF EXISTS dim_customer;
        DROP TABLE IF EXISTS dim_plan;
        DROP TABLE IF EXISTS dim_sales_rep;
        DROP TABLE IF EXISTS dim_date;

        -- ============== DIMENSIONS ==============

        CREATE TABLE dim_date (
            date_key        TEXT PRIMARY KEY,   -- 'YYYY-MM-DD'
            year            INTEGER NOT NULL,
            quarter         INTEGER NOT NULL,
            month           INTEGER NOT NULL,
            month_name      TEXT NOT NULL,
            day_of_month    INTEGER NOT NULL,
            day_of_week     INTEGER NOT NULL,    -- 0=Mon
            is_weekend      INTEGER NOT NULL,
            is_month_end    INTEGER NOT NULL,
            fiscal_period   TEXT NOT NULL        -- 'YYYY-MM'
        );

        CREATE TABLE dim_plan (
            plan_key        INTEGER PRIMARY KEY,
            plan_code       TEXT NOT NULL UNIQUE,
            plan_name       TEXT NOT NULL,
            plan_tier_rank  INTEGER NOT NULL,    -- 1=Starter,2=Growth,3=Enterprise (for upgrade/downgrade direction)
            billing_period  TEXT NOT NULL,       -- monthly / annual
            list_price_per_seat REAL NOT NULL,
            currency        TEXT NOT NULL DEFAULT 'USD'
        );

        CREATE TABLE dim_sales_rep (
            rep_key         INTEGER PRIMARY KEY,
            rep_name        TEXT NOT NULL,
            team            TEXT NOT NULL,       -- SMB / MidMarket / Enterprise / SelfServe
            region          TEXT NOT NULL
        );

        -- SCD2 customer dimension: a new row is inserted whenever
        -- segment, industry, or account_tier changes. is_current flags
        -- the active row. This is the standard "slowly changing
        -- dimension type 2" pattern used to ask point-in-time
        -- attribute questions correctly.
        CREATE TABLE dim_customer (
            customer_sk     INTEGER PRIMARY KEY,  -- surrogate key, one per version
            customer_id     INTEGER NOT NULL,      -- natural/business key, stable across versions
            customer_name   TEXT NOT NULL,
            industry        TEXT NOT NULL,
            segment         TEXT NOT NULL,         -- SMB / MidMarket / Enterprise
            country         TEXT NOT NULL,
            acquisition_channel TEXT NOT NULL,      -- paid_search / outbound / referral / organic / partner
            rep_key         INTEGER NOT NULL,
            effective_start_date TEXT NOT NULL,
            effective_end_date   TEXT,              -- NULL = currently active version
            is_current      INTEGER NOT NULL,
            FOREIGN KEY (rep_key) REFERENCES dim_sales_rep(rep_key)
        );

        -- ============== FACTS ==============

        -- Grain: one row per discrete subscription lifecycle event.
        -- This is an "accumulating/transactional" fact — good for
        -- funnel, churn-reason, and event-sequence questions.
        CREATE TABLE fact_subscription_events (
            event_id        INTEGER PRIMARY KEY,
            subscription_id INTEGER NOT NULL,
            customer_id     INTEGER NOT NULL,       -- natural key -> join to dim_customer current or as-of version
            event_date      TEXT NOT NULL,
            event_type      TEXT NOT NULL,           -- trial_start / trial_converted / trial_expired / new_subscription /
                                                       -- upgrade / downgrade / payment_failed / cancelled / reactivated
            plan_key        INTEGER NOT NULL,         -- plan AFTER the event
            prior_plan_key  INTEGER,                  -- plan BEFORE the event (NULL for new/trial_start)
            seats           INTEGER NOT NULL,
            mrr_impact      REAL NOT NULL,             -- signed change in MRR caused by this event (USD)
            cancel_reason   TEXT,                       -- only populated for 'cancelled' events
            FOREIGN KEY (plan_key) REFERENCES dim_plan(plan_key),
            FOREIGN KEY (prior_plan_key) REFERENCES dim_plan(plan_key)
        );

        -- Grain: one row per ACTIVE subscription per calendar day.
        -- This is the classic SaaS "snapshot fact table" that lets you
        -- compute MRR / ARR / churn / NRR as of any date without
        -- replaying every event — exactly how real subscription
        -- analytics (Stripe/ChartMogul-style) tables are built.
        CREATE TABLE fact_daily_subscription_snapshot (
            snapshot_date   TEXT NOT NULL,
            subscription_id INTEGER NOT NULL,
            customer_id     INTEGER NOT NULL,
            plan_key        INTEGER NOT NULL,
            seats           INTEGER NOT NULL,
            mrr             REAL NOT NULL,             -- monthly recurring revenue for this sub on this date
            status          TEXT NOT NULL,             -- trialing / active / past_due / cancelled
            PRIMARY KEY (snapshot_date, subscription_id),
            FOREIGN KEY (plan_key) REFERENCES dim_plan(plan_key)
        );

        -- Grain: one row per invoice/payment attempt.
        CREATE TABLE fact_invoice_payments (
            invoice_id      INTEGER PRIMARY KEY,
            subscription_id INTEGER NOT NULL,
            customer_id     INTEGER NOT NULL,
            invoice_date    TEXT NOT NULL,
            amount_due      REAL NOT NULL,
            amount_paid     REAL NOT NULL,
            payment_status  TEXT NOT NULL,             -- paid / failed / refunded / partially_refunded
            payment_attempt_no INTEGER NOT NULL DEFAULT 1,
            paid_date       TEXT
        );

        -- Grain: one row per customer per day of product usage.
        CREATE TABLE fact_product_usage (
            usage_date      TEXT NOT NULL,
            customer_id     INTEGER NOT NULL,
            login_count     INTEGER NOT NULL DEFAULT 0,
            active_users    INTEGER NOT NULL DEFAULT 0,
            key_feature_uses INTEGER NOT NULL DEFAULT 0,
            support_tickets INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (usage_date, customer_id)
        );

        CREATE INDEX idx_events_cust ON fact_subscription_events(customer_id, event_date);
        CREATE INDEX idx_events_sub ON fact_subscription_events(subscription_id, event_date);
        CREATE INDEX idx_snap_cust ON fact_daily_subscription_snapshot(customer_id, snapshot_date);
        CREATE INDEX idx_snap_sub ON fact_daily_subscription_snapshot(subscription_id, snapshot_date);
        CREATE INDEX idx_inv_cust ON fact_invoice_payments(customer_id, invoice_date);
        CREATE INDEX idx_usage_cust ON fact_product_usage(customer_id, usage_date);
        """
    )


def seed_dim_date(cur: sqlite3.Cursor, start: date, end: date) -> None:
    rows = []
    d = start
    while d <= end:
        nxt = d + timedelta(days=1)
        is_month_end = 1 if nxt.month != d.month else 0
        rows.append((
            d.isoformat(), d.year, (d.month - 1) // 3 + 1, d.month,
            d.strftime("%B"), d.day, d.weekday(),
            1 if d.weekday() >= 5 else 0, is_month_end,
            f"{d.year}-{d.month:02d}",
        ))
        d = nxt
    cur.executemany(
        "INSERT INTO dim_date VALUES (?,?,?,?,?,?,?,?,?,?)", rows
    )


def seed_static_dims(cur: sqlite3.Cursor) -> None:
    cur.executemany(
        "INSERT INTO dim_plan VALUES (?,?,?,?,?,?,?)",
        [
            (1, "STARTER_M", "Starter Monthly", 1, "monthly", 29.0, "USD"),
            (2, "STARTER_A", "Starter Annual", 1, "annual", 24.0, "USD"),
            (3, "GROWTH_M", "Growth Monthly", 2, "monthly", 79.0, "USD"),
            (4, "GROWTH_A", "Growth Annual", 2, "annual", 65.0, "USD"),
            (5, "ENTERPRISE_M", "Enterprise Monthly", 3, "monthly", 199.0, "USD"),
            (6, "ENTERPRISE_A", "Enterprise Annual", 3, "annual", 165.0, "USD"),
        ],
    )
    cur.executemany(
        "INSERT INTO dim_sales_rep VALUES (?,?,?,?)",
        [
            (1, "Lena Brooks", "SMB", "AMER"),
            (2, "Marcus Webb", "SMB", "EMEA"),
            (3, "Priya Chandran", "MidMarket", "APAC"),
            (4, "Owen Fitzgerald", "MidMarket", "AMER"),
            (5, "Sofia Reyes", "Enterprise", "AMER"),
            (6, "Daniel Kessler", "Enterprise", "EMEA"),
            (7, "N/A SelfServe", "SelfServe", "AMER"),
        ],
    )


CUSTOMER_NAMES = [
    "Brightline Logistics", "Nimbus Analytics", "Forge & Co", "Pinecrest Health",
    "Vector Robotics", "Calderon Legal", "Tidewater Media", "Skyline Realty",
    "Aurora Biotech", "Granite Financial", "Cobalt Manufacturing", "Lumen Retail",
    "Northwind Travel", "Hatch Studio", "Solstice Energy", "Maple & Birch Co",
    "Ironclad Security", "Cascade Foods", "Beacon EdTech", "Quartz Insurance",
    "Driftwood Hospitality", "Anchor Freight", "Vista Telecom", "Redwood Capital",
    "Harborview Construction", "Echo Marketing", "Pivot HR Solutions",
    "Crestline Pharma", "Junction Software", "Wildflower Apparel",
]
INDUSTRIES = ["Logistics", "Software", "Healthcare", "Finance", "Retail",
              "Manufacturing", "Media", "Real Estate", "Energy", "Education"]
SEGMENTS = ["SMB", "MidMarket", "Enterprise"]
COUNTRIES = ["US", "UK", "Canada", "Germany", "Australia", "India", "Singapore"]
CHANNELS = ["paid_search", "outbound", "referral", "organic", "partner"]
CANCEL_REASONS = ["too_expensive", "missing_features", "switched_competitor",
                   "budget_cuts", "poor_support", "low_usage", "merger_acquired"]


def seed_dim_customer(cur: sqlite3.Cursor, n: int = 30):
    """
    Seed customers, most with a single SCD2 version, a handful with a
    second version (a mid-life segment upgrade, e.g. SMB -> MidMarket)
    to exercise point-in-time dimension joins.
    """
    sk = 1
    customer_segment_track = {}  # customer_id -> final segment, for use in event generation
    for cid in range(1, n + 1):
        name = CUSTOMER_NAMES[(cid - 1) % len(CUSTOMER_NAMES)]
        industry = INDUSTRIES[cid % len(INDUSTRIES)]
        country = COUNTRIES[cid % len(COUNTRIES)]
        channel = CHANNELS[cid % len(CHANNELS)]
        start_segment = "SMB" if cid % 3 != 0 else "MidMarket"
        rep_key = {
            "SMB": random.choice([1, 2, 7, 7, 7]),
            "MidMarket": random.choice([3, 4]),
            "Enterprise": random.choice([5, 6]),
        }[start_segment]
        signup_date = (date(2025, 1, 1) + timedelta(days=(cid * 5) % 150)).isoformat()

        # ~5 customers get promoted mid-life to a higher segment
        promote = cid in (3, 9, 15, 21, 27)
        if promote:
            promo_date = (date.fromisoformat(signup_date) + timedelta(days=70)).isoformat()
            end_segment = "Enterprise" if start_segment == "MidMarket" else "MidMarket"
            end_rep_key = {"MidMarket": random.choice([3, 4]), "Enterprise": random.choice([5, 6])}[end_segment]

            cur.execute(
                "INSERT INTO dim_customer VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sk, cid, name, industry, start_segment, country, channel,
                 rep_key, signup_date, promo_date, 0),
            )
            sk += 1
            cur.execute(
                "INSERT INTO dim_customer VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sk, cid, name, industry, end_segment, country, channel,
                 end_rep_key, promo_date, None, 1),
            )
            sk += 1
            customer_segment_track[cid] = end_segment
        else:
            cur.execute(
                "INSERT INTO dim_customer VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sk, cid, name, industry, start_segment, country, channel,
                 rep_key, signup_date, None, 1),
            )
            sk += 1
            customer_segment_track[cid] = start_segment

    return customer_segment_track


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    build_schema(cur)
    seed_dim_date(cur, date(2025, 1, 1), date(2025, 9, 30))
    seed_static_dims(cur)
    seed_dim_customer(cur, n=30)
    conn.commit()
    conn.close()
    print(f"Schema + dimensions built at {DB_PATH}")


if __name__ == "__main__":
    main()
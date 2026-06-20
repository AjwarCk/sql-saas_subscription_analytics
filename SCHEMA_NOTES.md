# SaaS Subscription Analytics — Star Schema

## Use case
A B2B SaaS company selling seat-based subscriptions across 3 tiers
(Starter / Growth / Enterprise), monthly or annual billing. Customers
go through: trial → convert/expire → active → upgrade/downgrade →
payment_failed → recovered/cancelled → (sometimes) reactivated.

This is a genuinely different domain from B2C and D2C DB, so the
query *patterns* (MRR waterfalls, cohort retention, NRR, SCD2 as-of
joins) are new practice.

## Why a star schema (not another flat OLTP dump)
Real subscription-analytics warehouses (Stripe Sigma, ChartMogul,
internal Looker/dbt models) are built this way on purpose:

- **Facts at the right grain** — there isn't one "subscriptions"
  table. There are three different fact tables because three
  different questions need three different grains:
  - `fact_subscription_events` — one row per lifecycle *event*
    (grain: event). Good for funnels, churn reasons, event sequencing.
  - `fact_daily_subscription_snapshot` — one row per subscription per
    *day* (grain: subscription × day). Good for "what was true as of
    date X" — MRR, ARR, churn rate, retention — without replaying
    every event. This is the standard pattern Stripe/ChartMogul use
    internally.
  - `fact_invoice_payments` — one row per invoice/payment attempt.
    Good for dunning, revenue recognition, payment recovery.
  - `fact_product_usage` — one row per customer per day. Good for
    engagement/health-score work.

- **dim_customer is SCD Type 2** — 5 customers have two dimension
  rows because their segment was promoted mid-life (e.g. Forge & Co
  went MidMarket → Enterprise on 2025-03-27). Querying "which segment
  was this customer in when they signed up" vs. "which segment are
  they in now" requires an *as-of* join on
  `effective_start_date` / `effective_end_date`, not just a flat
  lookup — this is the #1 thing real analytics schemas get asked
  about in real time and the #1 thing naive joins get wrong.

- **dim_date** is a proper date dimension (fiscal_period, quarter,
  month_end flags) so month-end / quarter-end snapshot questions don't
  need fragile string-matching logic in every query.

## Tables
| Table | Grain | Rows |
|---|---|---|
| dim_date | 1 row per calendar day | 273 |
| dim_plan | 1 row per plan/billing-period combo | 6 |
| dim_sales_rep | 1 row per rep | 7 |
| dim_customer | 1 row per customer *version* (SCD2) | 35 (30 customers, 5 with 2 versions) |
| fact_subscription_events | 1 row per lifecycle event | 101 |
| fact_daily_subscription_snapshot | 1 row per subscription per day | 4,252 |
| fact_invoice_payments | 1 row per invoice/payment attempt | 145 |
| fact_product_usage | 1 row per customer per day | 4,362 |

## Build it yourself
```
python 01_build_schema_and_dims.py   # creates tables + dims
python 02_seed_facts.py               # simulates and inserts all facts
```

## Known data quirk (intentional, left as a learning point)
`fact_daily_subscription_snapshot.status = 'past_due'` doesn't exist on
every date in the table — dunning periods are short (a customer is
only past_due for ~5-10 days before recovering or cancelling). Q10
pins a specific date (`2025-07-20`) that has past_due rows rather than
using `MAX(snapshot_date)`, because the very last day in the dataset
happens to have zero accounts in dunning. In a production job this
would just be `WHERE snapshot_date = CURRENT_DATE` / a pipeline
parameter — the "as of today, sometimes there's just nothing in
dunning" result is itself a realistic and correct answer.

"""
Simulates realistic subscription lifecycles per customer and populates:
  - fact_subscription_events
  - fact_daily_subscription_snapshot
  - fact_invoice_payments
  - fact_product_usage

Each customer gets ONE subscription that goes through a believable
state machine:
  trial_start -> trial_converted (or trial_expired, churn) -> active
  -> [optional upgrade / downgrade events] -> [optional payment_failed
  -> recovered or cancelled] -> [optional cancelled -> reactivated]

This deliberately creates:
  - trial-to-paid funnel drop-off
  - upgrade/downgrade (expansion & contraction) MRR movements
  - involuntary churn (failed payment -> cancelled)
  - voluntary churn with reasons
  - a few win-back / reactivation cases
  - usage data that correlates loosely with churn risk (declining
    logins before cancellation) for engagement-based KPI questions
"""
from __future__ import annotations
import sqlite3
import random
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / ".." / "database"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "saas_subscription_dw.db"
random.seed(7)

PLANS = {
    1: dict(code="STARTER_M", tier=1, price=29.0, period="monthly"),
    2: dict(code="STARTER_A", tier=1, price=24.0, period="annual"),
    3: dict(code="GROWTH_M", tier=2, price=79.0, period="monthly"),
    4: dict(code="GROWTH_A", tier=2, price=65.0, period="annual"),
    5: dict(code="ENTERPRISE_M", tier=3, price=199.0, period="monthly"),
    6: dict(code="ENTERPRISE_A", tier=3, price=165.0, period="annual"),
}
TIER_PLAN_MONTHLY = {1: 1, 2: 3, 3: 5}  # tier -> monthly plan_key (we'll mostly use monthly for simplicity/clarity)

REASONS = ["too_expensive", "missing_features", "switched_competitor",
           "budget_cuts", "poor_support", "low_usage", "merger_acquired"]

END_DATE = date(2025, 9, 30)


def mrr_for(plan_key: int, seats: int) -> float:
    p = PLANS[plan_key]
    monthly_price = p["price"] if p["period"] == "monthly" else p["price"]  # annual price already stored as effective monthly-equivalent
    return round(monthly_price * seats, 2)


def daterange(d0: date, d1: date):
    d = d0
    while d <= d1:
        yield d
        d += timedelta(days=1)


def simulate_customer(cid: int, signup_date: date, start_segment_tier: int):
    """
    Returns:
      events: list of tuples for fact_subscription_events (without event_id)
      snapshots: list of tuples for fact_daily_subscription_snapshot
      invoices: list of tuples for fact_invoice_payments (without invoice_id)
      usage: list of tuples for fact_product_usage
    subscription_id == customer_id * 10 (one subscription per customer, kept simple)
    """
    sub_id = cid * 10
    events = []
    snapshots = []
    invoices = []
    usage = []

    seats = random.choice([1, 2, 3, 5, 8]) if start_segment_tier == 1 else \
            random.choice([5, 8, 12, 20]) if start_segment_tier == 2 else \
            random.choice([15, 25, 40, 60])

    plan_key = TIER_PLAN_MONTHLY[start_segment_tier]

    trial_len = 14
    trial_start = signup_date
    trial_end = signup_date + timedelta(days=trial_len)

    events.append((sub_id, cid, trial_start.isoformat(), "trial_start",
                    plan_key, None, seats, 0.0, None))

    # Trial outcome
    convert_roll = random.random()
    if convert_roll < 0.12 and cid % 9 != 0:
        # trial expired, never converts -> no more events, minimal usage then stop
        events.append((sub_id, cid, trial_end.isoformat(), "trial_expired",
                        plan_key, plan_key, seats, 0.0, None))
        for d in daterange(trial_start, trial_end):
            usage.append((d.isoformat(), cid,
                          random.randint(0, 4), random.randint(0, 2),
                          random.randint(0, 10), 0))
        for d in daterange(trial_start, trial_end):
            snapshots.append((d.isoformat(), sub_id, cid, plan_key, seats, 0.0, "trialing"))
        return events, snapshots, invoices, usage

    # Converts to paid
    converted_mrr = mrr_for(plan_key, seats)
    events.append((sub_id, cid, trial_end.isoformat(), "trial_converted",
                    plan_key, plan_key, seats, converted_mrr, None))

    cur_plan = plan_key
    cur_seats = seats
    status = "active"
    cancel_date = None
    reactivate_date = None
    final_cancel = False

    # trial snapshots (mrr 0)
    for d in daterange(trial_start, trial_end - timedelta(days=1)):
        snapshots.append((d.isoformat(), sub_id, cid, plan_key, seats, 0.0, "trialing"))

    timeline_cursor = trial_end
    current_mrr = converted_mrr

    # Possible mid-life events after conversion: upgrade, downgrade,
    # payment_failed (-> recovered or churned), cancel (-> maybe reactivate)
    n_lifecycle_events = random.choice([0, 1, 1, 2, 2, 3])
    next_event_gap = random.randint(20, 45)

    failed_payment_dates = set()
    cancel_reason = None

    for _ in range(n_lifecycle_events):
        timeline_cursor = timeline_cursor + timedelta(days=next_event_gap)
        if timeline_cursor >= END_DATE:
            break
        roll = random.random()
        if roll < 0.30 and PLANS[cur_plan]["tier"] < 3:
            # upgrade tier
            new_tier = PLANS[cur_plan]["tier"] + 1
            new_plan = TIER_PLAN_MONTHLY[new_tier]
            new_seats = cur_seats + random.choice([0, 2, 5])
            new_mrr = mrr_for(new_plan, new_seats)
            delta = round(new_mrr - current_mrr, 2)
            events.append((sub_id, cid, timeline_cursor.isoformat(), "upgrade",
                            new_plan, cur_plan, new_seats, delta, None))
            cur_plan, cur_seats, current_mrr = new_plan, new_seats, new_mrr
        elif roll < 0.55:
            # seat expansion (same plan, more seats) -> expansion MRR
            add_seats = random.choice([1, 2, 3])
            new_seats = cur_seats + add_seats
            new_mrr = mrr_for(cur_plan, new_seats)
            delta = round(new_mrr - current_mrr, 2)
            events.append((sub_id, cid, timeline_cursor.isoformat(), "upgrade",
                            cur_plan, cur_plan, new_seats, delta, None))
            cur_seats, current_mrr = new_seats, new_mrr
        elif roll < 0.72 and PLANS[cur_plan]["tier"] > 1:
            # downgrade tier -> contraction
            new_tier = PLANS[cur_plan]["tier"] - 1
            new_plan = TIER_PLAN_MONTHLY[new_tier]
            new_seats = max(1, cur_seats - random.choice([0, 1, 2]))
            new_mrr = mrr_for(new_plan, new_seats)
            delta = round(new_mrr - current_mrr, 2)
            events.append((sub_id, cid, timeline_cursor.isoformat(), "downgrade",
                            new_plan, cur_plan, new_seats, delta, None))
            cur_plan, cur_seats, current_mrr = new_plan, new_seats, new_mrr
        elif roll < 0.88:
            # payment failed
            events.append((sub_id, cid, timeline_cursor.isoformat(), "payment_failed",
                            cur_plan, cur_plan, cur_seats, 0.0, None))
            failed_payment_dates.add(timeline_cursor.isoformat())
            recovers = random.random() < 0.55
            if not recovers:
                # involuntary churn
                events.append((sub_id, cid, (timeline_cursor + timedelta(days=10)).isoformat(),
                                "cancelled", cur_plan, cur_plan, cur_seats,
                                round(-current_mrr, 2), "payment_failure"))
                cancel_date = timeline_cursor + timedelta(days=10)
                cancel_reason = "payment_failure"
                final_cancel = True
                break
            else:
                events.append((sub_id, cid, (timeline_cursor + timedelta(days=5)).isoformat(),
                                "reactivated", cur_plan, cur_plan, cur_seats, 0.0, None))
        else:
            # voluntary cancel
            reason = random.choice(REASONS)
            events.append((sub_id, cid, timeline_cursor.isoformat(), "cancelled",
                            cur_plan, cur_plan, cur_seats, round(-current_mrr, 2), reason))
            cancel_date = timeline_cursor
            cancel_reason = reason
            # 20% chance of win-back reactivation later
            if random.random() < 0.2 and timeline_cursor + timedelta(days=30) < END_DATE:
                react_date = timeline_cursor + timedelta(days=random.randint(20, 45))
                events.append((sub_id, cid, react_date.isoformat(), "reactivated",
                                cur_plan, cur_plan, cur_seats, current_mrr, None))
                cancel_date = None  # back to active
                cancel_reason = None
            else:
                final_cancel = True
                break
        next_event_gap = random.randint(20, 45)

    # Build day-by-day snapshot from trial_end to END_DATE (or cancel_date)
    sub_end = cancel_date if (final_cancel and cancel_date) else END_DATE
    # Re-walk events in date order to know plan/seats/mrr/status per day
    paid_events = sorted(
        [e for e in events if e[3] in ("trial_converted", "upgrade", "downgrade", "payment_failed", "cancelled", "reactivated")],
        key=lambda e: e[2],
    )

    cursor_plan, cursor_seats, cursor_status = plan_key, seats, "active"
    cursor_mrr = converted_mrr
    idx = 0
    for d in daterange(trial_end, sub_end):
        ds = d.isoformat()
        # apply any events that happened ON this date
        while idx < len(paid_events) and paid_events[idx][2] == ds:
            ev = paid_events[idx]
            etype = ev[3]
            if etype == "upgrade" or etype == "downgrade":
                cursor_plan = ev[4]
                cursor_seats = ev[6]
                cursor_mrr = mrr_for(cursor_plan, cursor_seats)
                cursor_status = "active"
            elif etype == "payment_failed":
                cursor_status = "past_due"
            elif etype == "reactivated":
                cursor_status = "active"
            elif etype == "cancelled":
                cursor_status = "cancelled"
            idx += 1
        snap_mrr = cursor_mrr if cursor_status == "active" else (0.0 if cursor_status == "cancelled" else cursor_mrr)
        snapshots.append((ds, sub_id, cid, cursor_plan, cursor_seats, snap_mrr, cursor_status))
        if cursor_status == "cancelled":
            break

    # Invoices: monthly, on the 1-month anniversary of conversion, while active/past_due existed
    inv_date = trial_end
    attempt_seats = seats
    attempt_plan = plan_key
    month_n = 0
    plan_at_date = {s[0]: (s[3], s[4]) for s in snapshots}  # date -> (plan,seats)
    while inv_date <= sub_end:
        ds = inv_date.isoformat()
        plan_seats = plan_at_date.get(ds, (attempt_plan, attempt_seats))
        amt = mrr_for(plan_seats[0], plan_seats[1])
        is_failed_window = any(
            abs((inv_date - date.fromisoformat(fd)).days) <= 3 for fd in failed_payment_dates
        )
        if is_failed_window:
            invoices.append((sub_id, cid, ds, amt, 0.0, "failed", 1, None))
            # second attempt a week later, assume recovered unless this led to churn
            retry_date = inv_date + timedelta(days=7)
            if retry_date <= sub_end:
                recovered_here = not (final_cancel and cancel_reason == "payment_failure" and retry_date >= cancel_date - timedelta(days=4))
                if recovered_here:
                    invoices.append((sub_id, cid, retry_date.isoformat(), amt, amt, "paid", 2, retry_date.isoformat()))
        else:
            invoices.append((sub_id, cid, ds, amt, amt, "paid", 1, ds))
        inv_date += timedelta(days=30)
        month_n += 1

    # Usage: daily, with a soft decline trend in the 2-3 weeks before any cancellation
    base_logins = {1: (1, 5), 2: (3, 10), 3: (6, 20)}[PLANS[plan_key]["tier"]]
    for d in daterange(trial_start, sub_end):
        ds = d.isoformat()
        decline_factor = 1.0
        if cancel_date and 0 <= (cancel_date - d).days <= 21:
            decline_factor = max(0.15, (cancel_date - d).days / 21)
        logins = max(0, int(random.randint(*base_logins) * decline_factor))
        active_u = max(0, min(cursor_seats if 'cursor_seats' in dir() else seats, int(logins / 2) + random.randint(0, 1)))
        tickets = 1 if random.random() < (0.06 if decline_factor == 1.0 else 0.15) else 0
        usage.append((ds, cid, logins, active_u, max(0, logins * random.randint(0, 4)), tickets))

    return events, snapshots, invoices, usage


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    cur.execute("SELECT customer_id, MIN(effective_start_date), MAX(segment) FROM dim_customer GROUP BY customer_id ORDER BY customer_id")
    customers = cur.fetchall()
    # map starting segment tier from the FIRST dim_customer row (earliest version) per customer
    cur.execute("""
        SELECT customer_id, segment, effective_start_date
        FROM dim_customer
        WHERE (customer_id, effective_start_date) IN (
            SELECT customer_id, MIN(effective_start_date) FROM dim_customer GROUP BY customer_id
        )
        ORDER BY customer_id
    """)
    first_versions = cur.fetchall()
    tier_map = {"SMB": 1, "MidMarket": 2, "Enterprise": 3}

    all_events, all_snapshots, all_invoices, all_usage = [], [], [], []

    event_id = 1
    invoice_id = 1

    for cid, segment, start_date_str in first_versions:
        start_date = date.fromisoformat(start_date_str)
        tier = tier_map[segment]
        events, snapshots, invoices, usage = simulate_customer(cid, start_date, tier)
        for e in events:
            all_events.append((event_id, *e))
            event_id += 1
        all_snapshots.extend(snapshots)
        for inv in invoices:
            all_invoices.append((invoice_id, *inv))
            invoice_id += 1
        all_usage.extend(usage)

    cur.executemany(
        "INSERT INTO fact_subscription_events VALUES (?,?,?,?,?,?,?,?,?,?)",
        all_events,
    )
    cur.executemany(
        "INSERT INTO fact_daily_subscription_snapshot VALUES (?,?,?,?,?,?,?)",
        all_snapshots,
    )
    cur.executemany(
        "INSERT INTO fact_invoice_payments VALUES (?,?,?,?,?,?,?,?,?)",
        all_invoices,
    )
    cur.executemany(
        "INSERT INTO fact_product_usage VALUES (?,?,?,?,?,?)",
        all_usage,
    )

    conn.commit()
    print(f"events={len(all_events)} snapshots={len(all_snapshots)} invoices={len(all_invoices)} usage_rows={len(all_usage)}")
    conn.close()


if __name__ == "__main__":
    main()
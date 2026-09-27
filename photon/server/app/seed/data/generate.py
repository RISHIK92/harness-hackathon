"""One-time generator for the Meridian seed corpus volume data
(commits.jsonl, prs.jsonl, slack.jsonl, tickets.jsonl, logs.jsonl,
incidents.jsonl, accounts.json).

Run once from this directory: `python3 generate.py`. The load-bearing
fixtures (MER-412 Bangalore partner thread, Northwind webhook incident,
retry-policy conflict) are hand-authored inline below; everything else is
templated for volume. Re-running overwrites the output files, so don't
re-run against hand-edited output without diffing first.
"""
import json
import hashlib
from datetime import datetime, timedelta, timezone

OUT = __file__.rsplit("/", 1)[0]


def w(name, rows):
    with open(f"{OUT}/{name}", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} rows -> {name}")


def ts(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(s):
    return hashlib.sha1(s.encode()).hexdigest()[:12]


PEOPLE = {
    "dev.sharma": "Dev Sharma",
    "priya.nair": "Priya Nair",
    "alex.rao": "Alex Rao",
    "sam.okafor": "Sam Okafor",
    "mei.lin": "Mei Lin",
    "jordan.blake": "Jordan Blake",
    "kavya.reddy": "Kavya Reddy",
}
ROLES = {
    "dev.sharma": "Eng Lead",
    "priya.nair": "Head of Partnerships",
    "alex.rao": "Finance",
    "sam.okafor": "CEO",
    "mei.lin": "Support Lead",
    "jordan.blake": "Backend Engineer",
    "kavya.reddy": "Backend Engineer",
}

# ─────────────────────────────────────────────────────────────────────────
# COMMITS
# ─────────────────────────────────────────────────────────────────────────
commits = []


def commit(date, author, message, files):
    h = sha(message + iso(date))
    commits.append(
        {
            "hash": h,
            "author": author,
            "email": f"{author.lower().replace(' ', '.')}@meridian.dev",
            "date": iso(date),
            "message": message,
            "files": files,
        }
    )


commit(ts(2026, 1, 8), "Jordan Blake", "init: scaffold FastAPI project structure", ["app/main.py", "app/config.py"])
commit(ts(2026, 1, 9), "Jordan Blake", "add booking and account models", ["app/models.py"])
commit(ts(2026, 1, 12), "Kavya Reddy", "add database session management", ["app/database.py"])
commit(ts(2026, 1, 15), "Dev Sharma", "add availability slot calculation", ["app/availability.py"])
commit(ts(2026, 1, 20), "Kavya Reddy", "add base fare table for launch cities", ["app/services/rate_service.py"])
commit(ts(2026, 1, 22), "Dev Sharma", "add pricing module with base commission rates", ["app/pricing.py"])
commit(ts(2026, 1, 28), "Jordan Blake", "add booking creation service", ["app/services/booking_service.py"])
commit(ts(2026, 2, 3), "Kavya Reddy", "add city surcharge table (Mumbai, Delhi, Singapore, Dubai)", ["app/pricing.py"])
commit(ts(2026, 2, 5), "Dev Sharma", "add payments/settlement module", ["app/payments.py"])
commit(ts(2026, 2, 10), "Jordan Blake", "add JWT session auth", ["app/auth.py"])
commit(ts(2026, 2, 14), "Kavya Reddy", "add webhook signing + verification", ["app/webhooks.py", "app/utils/signature.py"])
commit(ts(2026, 2, 18), "Dev Sharma", "add webhook delivery via notification service", ["app/services/notification_service.py"])
commit(ts(2026, 2, 20), "Jordan Blake", "add webhooks router with inbound verification endpoint", ["app/routers/webhooks.py"])
commit(ts(2026, 2, 24), "Kavya Reddy", "add bookings router", ["app/routers/bookings.py"])
commit(ts(2026, 2, 26), "Dev Sharma", "add payments router", ["app/routers/payments.py"])
commit(ts(2026, 3, 2), "Jordan Blake", "add accounts router with webhook config endpoint", ["app/routers/accounts.py"])
commit(ts(2026, 3, 5), "Kavya Reddy", "add pricing unit tests", ["tests/test_pricing.py"])
commit(ts(2026, 3, 8), "Dev Sharma", "add webhook signature test coverage", ["tests/test_webhooks.py"])
commit(ts(2026, 3, 11), "Jordan Blake", "add enterprise tier commission reduction", ["app/pricing.py"])
commit(ts(2026, 3, 15), "Kavya Reddy", "set webhook retry backoff schedule (3 retries)", ["app/webhooks.py"])
commit(
    ts(2026, 3, 20, 15, 40),
    "Dev Sharma",
    "pricing: add Bangalore partner rate override (MER-412)",
    ["app/pricing.py", "app/services/partner_service.py"],
)
commit(ts(2026, 3, 24), "Kavya Reddy", "add PARTNER_CITIES set for partner-service eligibility checks", ["app/services/partner_service.py"])
commit(ts(2026, 4, 2), "Jordan Blake", "add structured logging helper", ["app/utils/logging.py"])
commit(ts(2026, 4, 9), "Dev Sharma", "wire structured logging into webhook delivery paths", ["app/webhooks.py", "app/services/notification_service.py"])
commit(ts(2026, 4, 18), "Kavya Reddy", "add Dubai to service coverage", ["app/services/rate_service.py", "app/availability.py"])
commit(ts(2026, 5, 6), "Jordan Blake", "settle commission at settlement time, not booking time", ["app/payments.py"])
commit(ts(2026, 5, 22), "Dev Sharma", "fix: settle_booking should reject non-completed bookings", ["app/payments.py"])
commit(ts(2026, 6, 12), "Kavya Reddy", "add api key hashing helper", ["app/auth.py"])
commit(ts(2026, 7, 1), "Jordan Blake", "bump session TTL to 12h", ["app/config.py"])
commit(ts(2026, 8, 14, 9, 5), "Kavya Reddy", "webhooks: support secret rotation from dashboard", ["app/utils/signature.py"])

w("commits.jsonl", commits)

# ─────────────────────────────────────────────────────────────────────────
# PRS
# ─────────────────────────────────────────────────────────────────────────
prs = [
    {"number": 41, "title": "Scaffold FastAPI project", "author": "Jordan Blake", "merged_at": iso(ts(2026, 1, 9)), "commits": [commits[0]["hash"], commits[1]["hash"]], "description": "Initial project structure."},
    {"number": 58, "title": "Base pricing + rate service", "author": "Dev Sharma", "merged_at": iso(ts(2026, 1, 23)), "commits": [commits[4]["hash"], commits[5]["hash"]], "description": "Adds the pricing module and per-city base fares."},
    {"number": 72, "title": "City surcharge table", "author": "Kavya Reddy", "merged_at": iso(ts(2026, 2, 4)), "commits": [commits[7]["hash"]], "description": "Adds surcharges for Mumbai, Delhi, Singapore, Dubai."},
    {"number": 89, "title": "Webhook signing and verification", "author": "Kavya Reddy", "merged_at": iso(ts(2026, 2, 15)), "commits": [commits[10]["hash"]], "description": "HMAC-SHA256 signing for outbound webhooks, verification for inbound test pings."},
    {"number": 94, "title": "Webhook delivery + retry schedule", "author": "Dev Sharma", "merged_at": iso(ts(2026, 2, 19)), "commits": [commits[11]["hash"]], "description": "Real HTTP delivery via notification_service. Retry policy TBD in a follow-up (see #101)."},
    {"number": 101, "title": "Set webhook retry backoff schedule", "author": "Kavya Reddy", "merged_at": iso(ts(2026, 3, 15)), "commits": [commits[19]["hash"]], "description": "3 retries at 30s/120s/600s. Support wanted a longer window (see #support ticket MER-388) but we shipped this for launch and left a longer-window revisit as a follow-up — never scheduled. Docs may still describe the originally proposed 24h/5-retry policy; needs a sync with docs owner."},
    {
        "number": 128,
        "title": "Add partner city rate override for Bangalore (MER-412)",
        "author": "Dev Sharma",
        "merged_at": iso(ts(2026, 3, 21, 10, 15)),
        "commits": [commits[20]["hash"], commits[21]["hash"]],
        "description": (
            "Implements the Bangalore partner rate (0.88x) agreed with BLR Mobility "
            "Partners. Context and deal terms in #pricing: "
            "https://meridian.slack.com/archives/C0PRICING/p1710939000000200 . "
            "Closes MER-412."
        ),
    },
    {"number": 145, "title": "Structured logging for webhook paths", "author": "Dev Sharma", "merged_at": iso(ts(2026, 4, 9)), "commits": [commits[24]["hash"]], "description": "Adds account_id/attempt context to webhook log lines."},
    {"number": 152, "title": "Dubai coverage launch", "author": "Kavya Reddy", "merged_at": iso(ts(2026, 4, 18)), "commits": [commits[25]["hash"]], "description": "Adds Dubai base fare and timezone."},
    {"number": 201, "title": "Webhook secret rotation from dashboard", "author": "Kavya Reddy", "merged_at": iso(ts(2026, 8, 14, 9, 30)), "commits": [commits[-1]["hash"]], "description": "Customers can now rotate their signing secret without a support ticket. No grace-period overlap between old/new secret — matches current docs."},
]
w("prs.jsonl", prs)

# ─────────────────────────────────────────────────────────────────────────
# SLACK
# ─────────────────────────────────────────────────────────────────────────
slack = []


def msg(channel, user, text, dt, thread_ts=None):
    m_ts = f"{dt.timestamp():.6f}"
    slack.append(
        {
            "channel": channel,
            "user": user,
            "user_name": PEOPLE[user],
            "user_role": ROLES[user],
            "text": text,
            "ts": m_ts,
            "thread_ts": thread_ts or m_ts,
            "datetime": iso(dt),
        }
    )
    return m_ts


# --- #pricing: the MER-412 thread (S2 load-bearing) ---
t0 = ts(2026, 3, 18, 14, 2)
root = msg(
    "pricing",
    "priya.nair",
    "Heads up on the BLR Mobility Partners deal — Legal signed the reseller agreement Friday. "
    "They guarantee us 500 bookings/month across their Bangalore fleet in exchange for a "
    "12-point discount on our commission for partner-tier accounts booking in Bangalore. "
    "Need eng to ship the rate before their April 1 activation.",
    t0,
)
msg("pricing", "alex.rao", "Confirmed on Finance's side — 0.88x multiplier for partner tier in Bangalore only, everywhere else unchanged. Signed contract is in the partnerships drive if anyone needs the exact terms.", t0 + timedelta(minutes=6), root)
msg("pricing", "dev.sharma", "Got it. So this only applies when tier=partner AND city=Bangalore, right? Not partner tier generally?", t0 + timedelta(minutes=14), root)
msg("pricing", "priya.nair", "Correct — city-specific to Bangalore for now. If it goes well we might extend to Mumbai next quarter but that's a separate negotiation, don't build for it yet.", t0 + timedelta(minutes=17), root)
msg("pricing", "sam.okafor", "Approving this — good deal for us, locks in volume from a fleet operator we've wanted for a while. Let's not put the BLR relationship details in customer-facing docs though, it's a competitive term we don't want other city operators seeing and asking to match.", t0 + timedelta(minutes=40), root)
msg("pricing", "dev.sharma", "Understood, will keep it code-only with a generic comment, no partner name in the repo.", t0 + timedelta(minutes=44), root)
msg("pricing", "priya.nair", "Filed as MER-412, assigning to you Dev.", t0 + timedelta(hours=1, minutes=2), root)
msg("pricing", "dev.sharma", "On it, targeting this week.", t0 + timedelta(hours=1, minutes=10), root)
msg("pricing", "dev.sharma", "PR up: #128. Added PARTNER_CITY_RATES with just Bangalore for now, easy to extend if Mumbai happens later.", ts(2026, 3, 20, 15, 45), root)
msg("pricing", "alex.rao", "Nice, thanks for the quick turnaround. Confirming 0.88 matches what I modeled.", ts(2026, 3, 20, 16, 2), root)
msg("pricing", "priya.nair", "Merged and live — thanks all. BLR activation confirmed for April 1.", ts(2026, 3, 21, 10, 20), root)

# --- other #pricing chatter ---
msg("pricing", "alex.rao", "Reminder: Q2 rate review is next Tuesday, send me any pending city surcharge changes before then.", ts(2026, 4, 20, 9, 0))
msg("pricing", "kavya.reddy", "Dubai base fare — used 140 flat since we don't have surcharge data yet, will revisit after a month of bookings.", ts(2026, 4, 18, 11, 30))
msg("pricing", "priya.nair", "Mumbai partner extension is officially on hold, operator wants a much bigger volume commitment than we're comfortable with. Not pursuing for now.", ts(2026, 6, 2, 13, 15))
msg("pricing", "sam.okafor", "Good call, let's revisit next fiscal year.", ts(2026, 6, 2, 13, 40))

# --- #eng ---
e0 = ts(2026, 2, 14, 10, 0)
er = msg("eng", "kavya.reddy", "Shipping webhook HMAC signing today (#89). Secret lives on Account, one secret per account for now — no key rotation overlap support yet, that's a v2 thing.", e0)
msg("eng", "dev.sharma", "Sounds good, let's flag that limitation to Support so they know what to tell customers if they ask about rotating without downtime.", e0 + timedelta(minutes=12), er)
msg("eng", "jordan.blake", "+1, I'll add a note to the internal runbook.", e0 + timedelta(minutes=20), er)
msg("eng", "dev.sharma", "Retry backoff for webhooks: going with 30s/120s/600s (3 retries, ~12 min total). Support originally asked for something closer to 24h — pushing that to a follow-up since redis-based long-delay scheduling needs more design.", ts(2026, 3, 15, 11, 0))
msg("eng", "jordan.blake", "Makes sense for launch. Can we make sure docs reflect the actual 3-retry/~12min window and not whatever Support originally asked for?", ts(2026, 3, 15, 11, 5))
msg("eng", "dev.sharma", "Yeah agreed, will ping docs. (never got to it — see #general Aug thread, this bit us)", ts(2026, 3, 15, 11, 8))
msg("eng", "kavya.reddy", "Settlement was recalculating commission at booking time, which means tier changes mid-cycle didn't apply correctly. Fixed to recalc at settlement.", ts(2026, 5, 6, 14, 0))
msg("eng", "jordan.blake", "Good catch, that was causing the Orion discrepancy from last month.", ts(2026, 5, 6, 14, 10))
msg("eng", "dev.sharma", "FYI bumping session TTL from 4h to 12h, support was getting a lot of 'logged out mid-shift' complaints from dispatch teams.", ts(2026, 7, 1, 9, 30))
msg("eng", "kavya.reddy", "Dashboard rotation flow for webhook secrets is live. Reminder: rotating immediately invalidates the old secret, there's no overlap window — matches what the docs say.", ts(2026, 8, 14, 9, 40))
msg("eng", "jordan.blake", "Worth a heads-up in #support in case customers rotate without updating their endpoint first.", ts(2026, 8, 14, 9, 45))
msg("eng", "kavya.reddy", "Good call, posting there now.", ts(2026, 8, 14, 9, 47))

# --- #support ---
s0 = ts(2026, 8, 14, 9, 50)
sr = msg("support", "kavya.reddy", "Heads up support — webhook secret rotation is now self-serve from the dashboard (Settings > Integrations > Webhooks > Signing). Rotating immediately invalidates the old secret, no grace period. If a customer reports webhooks suddenly failing with 401s after saying they 'just rotated something', that's almost certainly it — have them check their endpoint's stored secret matches what's shown in the dashboard.", s0)
msg("support", "mei.lin", "Got it, will flag in the macro for webhook issues.", s0 + timedelta(minutes=8), sr)
msg("support", "mei.lin", "Northwind (acct_northwind) opened a ticket about webhooks not firing since ~last week. Checking if this is the rotation thing.", ts(2026, 8, 20, 10, 5))
msg("support", "mei.lin", "Confirmed — their delivery log shows 401s starting right around Aug 14, same day rotation shipped. Pulling in eng to confirm root cause before I reply.", ts(2026, 8, 20, 10, 22))
msg("support", "dev.sharma", "Checked their account — webhook_signing_secret was rotated Aug 14 in our system but their endpoint is still verifying against the old one. Classic no-overlap rotation gap. They need to update their stored secret to the current one.", ts(2026, 8, 20, 11, 0))
msg("support", "mei.lin", "Thanks, replying to MER-441 now.", ts(2026, 8, 20, 11, 5))
msg("support", "priya.nair", "Reminder support team: retry policy is 3 retries over ~12 minutes, NOT the 24h/5-retry thing in the old docs draft — that text still needs fixing, sorry for the confusion it's caused.", ts(2026, 6, 10, 15, 0))
msg("support", "mei.lin", "Ugh yeah I've quoted the wrong one to a customer before. Filing a docs ticket.", ts(2026, 6, 10, 15, 5))

# --- #partnerships ---
p0 = ts(2026, 3, 1, 10, 0)
msg("partnerships", "priya.nair", "BLR Mobility Partners contract is in final review with Legal. Should close by mid-March.", p0)
msg("partnerships", "sam.okafor", "Great, this is a good template for other regional operator deals if it works out.", p0 + timedelta(hours=2))
msg("partnerships", "priya.nair", "Signed Friday 3/13. Eng thread with the rate details is in #pricing.", ts(2026, 3, 16, 9, 0))
msg("partnerships", "alex.rao", "Volume tracking dashboard for the BLR deal is up — 500/mo commitment, we'll review quarterly.", ts(2026, 4, 5, 13, 0))
msg("partnerships", "priya.nair", "First month post-activation: BLR delivered 540 bookings, above the guarantee. Good start.", ts(2026, 5, 2, 10, 0))

# --- #incidents ---
i0 = ts(2026, 5, 12, 3, 20)
ir = msg("incidents", "jordan.blake", "Payments settlement job stuck since ~03:00 UTC, Celery worker OOM. Investigating.", i0)
msg("incidents", "dev.sharma", "Restarted worker, backlog draining now. Root cause: settlement batch size wasn't capped, a large customer's end-of-month batch blew past memory limits.", i0 + timedelta(minutes=35), ir)
msg("incidents", "jordan.blake", "Postmortem: incident-2026-05-12-settlement-oom.md, adding batch size cap as follow-up.", i0 + timedelta(hours=1), ir)

i1 = ts(2026, 7, 3, 14, 0)
i1r = msg("incidents", "kavya.reddy", "Elevated 5xx on /api/bookings for the last 15 minutes, looks like a Postgres connection pool exhaustion.", i1)
msg("incidents", "jordan.blake", "Bumped pool size, monitoring.", i1 + timedelta(minutes=10), i1r)
msg("incidents", "kavya.reddy", "Recovered. Postmortem: incident-2026-07-03-db-pool.md", i1 + timedelta(minutes=25), i1r)

# --- #general ---
msg("general", "sam.okafor", "Welcome Kavya to the eng team!", ts(2026, 1, 5, 9, 0))
msg("general", "mei.lin", "Support team all-hands moved to Thursdays 10am, calendar updated.", ts(2026, 2, 1, 8, 0))
msg("general", "sam.okafor", "Great quarter everyone — Bangalore partner program off to a strong start, thanks to Priya and Dev's team.", ts(2026, 4, 30, 17, 0))
msg("general", "priya.nair", "Office closed for the holiday Monday, back Tuesday.", ts(2026, 8, 21, 9, 0))

# filler chatter to reach volume across channels without adding new facts
FILLER = [
    ("eng", "jordan.blake", "Deploying a small hotfix to staging, should be quick."),
    ("eng", "kavya.reddy", "CI's a bit slow today, FYI."),
    ("eng", "dev.sharma", "Reviewing PRs this afternoon, will get to the queue."),
    ("support", "mei.lin", "Ticket queue is caught up as of this morning."),
    ("support", "mei.lin", "Reminder to tag tickets with the right account_id for reporting."),
    ("partnerships", "priya.nair", "Syncing with Finance on Q3 partner renewals this week."),
    ("general", "sam.okafor", "Good work on last sprint everyone."),
    ("general", "mei.lin", "Anyone free for lunch?"),
    ("pricing", "alex.rao", "Rate review doc is in the shared drive if anyone wants to add items."),
    ("eng", "jordan.blake", "Bumped a few dependency versions, no behavior changes expected."),
]
d = ts(2026, 2, 20, 9, 0)
for i in range(30):
    ch, user, text = FILLER[i % len(FILLER)]
    d = d + timedelta(days=3, hours=(i % 5))
    msg(ch, user, text, d)

w("slack.jsonl", slack)
print(f"slack total: {len(slack)}")

# ─────────────────────────────────────────────────────────────────────────
# ACCOUNTS
# ─────────────────────────────────────────────────────────────────────────
accounts = [
    {
        "id": "acct_northwind",
        "name": "Northwind Logistics",
        "tier": "standard",
        "home_city": "Mumbai",
        "webhook_url": "https://hooks.northwindlogistics.com/meridian",
        "webhook_enabled": True,
        "webhook_signing_secret_current": "whsec_9f2a7c1d3b4e5061",
        "webhook_signing_secret_history": [
            {"secret": "whsec_1a2b3c4d5e6f7081", "rotated_out_at": "2026-08-14T09:12:00Z"},
        ],
        "webhook_signing_secret_note": "Endpoint has NOT updated to whsec_9f2a7c1d3b4e5061 as of 2026-08-22 — still verifying with the rotated-out secret, per delivery log.",
        "open_tickets": ["MER-441"],
        "created_at": "2025-09-02T00:00:00Z",
    },
    {
        "id": "acct_calico",
        "name": "Calico Transit",
        "tier": "partner",
        "home_city": "Bangalore",
        "webhook_url": "https://api.calicotransit.example/webhooks/meridian",
        "webhook_enabled": True,
        "webhook_signing_secret_current": "whsec_c4e1f9a2b8d3e6f0",
        "webhook_signing_secret_history": [],
        "open_tickets": [],
        "created_at": "2026-03-25T00:00:00Z",
        "partner_agreement": "BLR Mobility Partners reseller agreement (MER-412), Bangalore only, 0.88x commission",
    },
    {
        "id": "acct_orion",
        "name": "Orion Health",
        "tier": "enterprise",
        "home_city": "Singapore",
        "webhook_url": "https://integrations.orionhealth.example/hooks/meridian",
        "webhook_enabled": True,
        "webhook_signing_secret_current": "whsec_5d8e2f1a9c3b7042",
        "webhook_signing_secret_history": [],
        "open_tickets": [],
        "created_at": "2025-11-15T00:00:00Z",
    },
]
with open(f"{OUT}/accounts.json", "w") as f:
    json.dump(accounts, f, indent=2)
print("wrote accounts.json")

# ─────────────────────────────────────────────────────────────────────────
# TICKETS
# ─────────────────────────────────────────────────────────────────────────
tickets = []


def ticket(id_, account_id, title, status, opened, resolution=None, resolved=None, internal=False):
    tickets.append(
        {
            "id": id_,
            "account_id": account_id,
            "title": title,
            "status": status,
            "opened_at": iso(opened),
            "resolution": resolution,
            "resolved_at": iso(resolved) if resolved else None,
            "internal": internal,
        }
    )


ticket("MER-388", None, "Support request: longer webhook retry window for slow customer endpoints", "closed", ts(2026, 3, 10), "Eng scoped a 3-retry/~12min window for launch (see PR #101); a longer async-scheduled window was deferred, no ETA.", ts(2026, 3, 16), internal=True)
ticket("MER-412", None, "Add partner city rate override for Bangalore (BLR Mobility Partners)", "closed", ts(2026, 3, 18), "Shipped in PR #128. 0.88x commission for partner-tier accounts booking in Bangalore.", ts(2026, 3, 21), internal=True)
ticket("MER-419", "acct_orion", "Commission looked wrong on a booking settled after our tier upgrade", "closed", ts(2026, 5, 4), "Root cause: settlement was recalculating commission at booking time, not settlement time, so the tier upgrade didn't apply. Fixed 2026-05-06.", ts(2026, 5, 7))
ticket("MER-441", "acct_northwind", "Webhooks stopped firing", "open", ts(2026, 8, 20, 9, 58), None, None)
ticket("MER-395", "acct_calico", "Why is our Bangalore commission different from our other cities?", "closed", ts(2026, 4, 2), "Explained partner-tier rate applies specifically to Bangalore per your account's partner agreement; standard surcharge table applies elsewhere.", ts(2026, 4, 3))
ticket("MER-402", "acct_northwind", "Question about signing secret rotation grace period", "closed", ts(2026, 4, 10), "Confirmed there's no overlap window today — update your endpoint's stored secret before/at rotation, not after.", ts(2026, 4, 11))
ticket("MER-455", None, "Docs: webhook retry policy section is out of date vs actual code", "open", ts(2026, 6, 10, 15, 6), None, None, internal=True)
ticket("MER-430", "acct_orion", "Booking rejected with 422 outside expected hours", "closed", ts(2026, 4, 25), "Booking was requested at 23:15 local, outside the 06:00-23:00 service window. Working as intended, documented in docs/08.", ts(2026, 4, 25))
ticket("MER-360", "acct_calico", "API key not working after regenerating", "closed", ts(2026, 2, 20), "Old key was still cached client-side; new key confirmed working via curl. Customer resolved after clearing local config.", ts(2026, 2, 20))
ticket("MER-467", "acct_northwind", "Settlement 409 on retry after webhook double-delivery", "closed", ts(2026, 7, 2), "Expected: a completed booking was already settled by the first delivery; the retried duplicate correctly got a 409. No fix needed, added a note to docs/09.", ts(2026, 7, 3))

_filler_titles = [
    "Question about API rate limits",
    "Need clarification on booking cancellation window",
    "Dashboard showing stale availability data",
    "Requesting a second API key for staging",
    "Payout didn't arrive on expected date",
    "Timezone mismatch in booking confirmation email",
    "Requesting increase to webhook payload size limit",
    "CSV export missing recent bookings",
    "Question about enterprise tier discount stacking",
    "Login session expiring too quickly",
    "Requesting bulk booking import tool",
    "Clarify what counts as a 'completed' booking for settlement",
    "Webhook delivery log UI not loading",
    "Question about Dubai timezone handling",
    "Asking if Mumbai partner rates are available yet",
]
accts = ["acct_northwind", "acct_calico", "acct_orion", None]
d = ts(2026, 2, 5, 10, 0)
for i, title in enumerate(_filler_titles):
    d = d + timedelta(days=9)
    ticket(
        f"MER-{300 + i * 3}",
        accts[i % len(accts)],
        title,
        "closed",
        d,
        "Resolved via support conversation, no product change needed.",
        d + timedelta(days=1),
    )

w("tickets.jsonl", tickets)
print(f"tickets total: {len(tickets)}")

# ─────────────────────────────────────────────────────────────────────────
# LOGS (Northwind 401 spike is the load-bearing part)
# ─────────────────────────────────────────────────────────────────────────
logs = []


def log(dt, account_id, level, event, **fields):
    logs.append({"timestamp": iso(dt), "account_id": account_id, "level": level, "event": event, **fields})


# Northwind: rotation at 09:12 Aug 14, then repeated 401s on every delivery attempt since
log(ts(2026, 8, 14, 9, 12), "acct_northwind", "info", "webhook.secret_rotated", by="dashboard_self_serve")
d = ts(2026, 8, 14, 9, 20)
booking_n = 5001
while d < ts(2026, 8, 22, 8, 0):
    log(d, "acct_northwind", "warning", "webhook.delivery_unauthorized", endpoint="https://hooks.northwindlogistics.com/meridian", status=401, attempt=1, booking_id=f"bk_nw{booking_n}")
    log(d + timedelta(seconds=30), "acct_northwind", "warning", "webhook.delivery_unauthorized", endpoint="https://hooks.northwindlogistics.com/meridian", status=401, attempt=2, booking_id=f"bk_nw{booking_n}")
    log(d + timedelta(seconds=150), "acct_northwind", "warning", "webhook.delivery_unauthorized", endpoint="https://hooks.northwindlogistics.com/meridian", status=401, attempt=3, booking_id=f"bk_nw{booking_n}")
    log(d + timedelta(seconds=750), "acct_northwind", "error", "webhook.delivery_failed_permanent", endpoint="https://hooks.northwindlogistics.com/meridian", status=401, attempts=4, booking_id=f"bk_nw{booking_n}")
    booking_n += 1
    d += timedelta(hours=14)

log(ts(2026, 8, 20, 10, 22), "acct_northwind", "info", "support.ticket_opened", ticket_id="MER-441")

# Calico: healthy partner-tier activity
d = ts(2026, 4, 1, 8, 0)
booking_c = 7001
while d < ts(2026, 8, 20):
    log(d, "acct_calico", "info", "booking.completed", city="Bangalore", booking_id=f"bk_cal{booking_c}")
    log(d + timedelta(minutes=2), "acct_calico", "info", "webhook.delivered", endpoint="https://api.calicotransit.example/webhooks/meridian", status=200, booking_id=f"bk_cal{booking_c}")
    log(d + timedelta(minutes=3), "acct_calico", "info", "payment.settled", booking_id=f"bk_cal{booking_c}", commission_rate="0.88")
    booking_c += 1
    d += timedelta(days=13)

# Orion: healthy control, occasional normal activity + the May tier-recalc bug window
d = ts(2026, 1, 1, 8, 0)
booking_o = 3001
while d < ts(2026, 8, 15):
    log(d, "acct_orion", "info", "booking.completed", city="Singapore", booking_id=f"bk_or{booking_o}")
    log(d + timedelta(minutes=5), "acct_orion", "info", "webhook.delivered", endpoint="https://integrations.orionhealth.example/hooks/meridian", status=200, booking_id=f"bk_or{booking_o}")
    log(d + timedelta(minutes=6), "acct_orion", "info", "payment.settled", booking_id=f"bk_or{booking_o}")
    booking_o += 1
    d += timedelta(days=16)

log(ts(2026, 5, 4, 16, 0), "acct_orion", "warning", "payment.commission_rate_mismatch_reported", booking_id="bk_or3018", note="customer-reported, see MER-419")
log(ts(2026, 5, 6, 14, 0), "acct_orion", "info", "deploy.fix_shipped", note="settlement now recalculates commission at settlement time (commit see commits.jsonl 2026-05-06)")

w("logs.jsonl", logs)
print(f"logs total: {len(logs)}")

# ─────────────────────────────────────────────────────────────────────────
# INCIDENTS
# ─────────────────────────────────────────────────────────────────────────
incidents = [
    {
        "id": "incident-2026-05-12-settlement-oom",
        "title": "Settlement job OOM due to uncapped batch size",
        "started_at": iso(ts(2026, 5, 12, 3, 20)),
        "resolved_at": iso(ts(2026, 5, 12, 4, 20)),
        "severity": "sev2",
        "summary": "Celery settlement worker OOM'd processing a large customer's end-of-month batch. Worker restarted, backlog drained. Follow-up: cap settlement batch size.",
        "related_tickets": [],
        "related_slack_thread": "incidents:1747019200.000000",
    },
    {
        "id": "incident-2026-07-03-db-pool",
        "title": "Postgres connection pool exhaustion on /api/bookings",
        "started_at": iso(ts(2026, 7, 3, 14, 0)),
        "resolved_at": iso(ts(2026, 7, 3, 14, 25)),
        "severity": "sev3",
        "summary": "Elevated 5xx on booking creation due to connection pool exhaustion under load. Pool size increased, monitored to recovery.",
        "related_tickets": [],
        "related_slack_thread": "incidents:1751551200.000000",
    },
    {
        "id": "incident-2026-08-14-webhook-rotation-gap",
        "title": "Northwind webhook deliveries failing with 401 after self-serve secret rotation",
        "started_at": iso(ts(2026, 8, 14, 9, 20)),
        "resolved_at": None,
        "severity": "sev3",
        "summary": (
            "Northwind rotated their webhook signing secret via the new dashboard "
            "self-serve flow on 2026-08-14. Their endpoint was not updated with the "
            "new secret, so every delivery since has failed signature verification "
            "(401), retried 3x, and been marked permanently failed. This is expected "
            "behavior given the no-grace-period rotation design (see docs/06 and "
            "PR #201) but customer did not realize their endpoint needed a manual "
            "update. Customer opened MER-441 on 2026-08-20. Not yet resolved as of "
            "2026-08-22 pending customer-side endpoint update."
        ),
        "related_tickets": ["MER-441"],
        "related_slack_thread": "support:1755683400.000000",
    },
]
w("incidents.jsonl", incidents)

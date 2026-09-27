"""Tenant/account tools — deterministic reads over the seed corpus's
accounts.json / logs.jsonl / incidents.jsonl. No retrieval involved: these
are the backbone of S1 (account-aware troubleshooting) precisely because
they're exact, not similarity-matched.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.seed.loader import get_account, load_accounts, load_incidents, load_logs
from app.tools.evidence import make_evidence, tool_result


def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


async def get_account_tool(account_id: str) -> dict:
    account = get_account(account_id)
    if not account:
        return tool_result("get_account", [], note=f"no account found with id '{account_id}'")

    redacted = dict(account)
    secret = redacted.pop("webhook_signing_secret_current", None)
    if secret:
        redacted["webhook_signing_secret_last4"] = secret[-4:]
    for h in redacted.get("webhook_signing_secret_history", []):
        h.pop("secret", None)

    snippet = (
        f"{account['name']} ({account['id']}) — tier={account['tier']}, "
        f"home_city={account['home_city']}, webhook_enabled={account['webhook_enabled']}"
    )
    if account.get("webhook_signing_secret_note"):
        snippet += f". {account['webhook_signing_secret_note']}"

    evidence = [make_evidence("account", f"account:{account_id}", snippet, 1.0)]
    return tool_result("get_account", evidence)


async def get_account_logs_tool(
    account_id: str, since: str | None = None, level: str | None = None, limit: int = 20
) -> dict:
    logs = [row for row in load_logs() if row["account_id"] == account_id]
    if level:
        logs = [row for row in logs if row["level"] == level]
    if since:
        since_dt = _parse_ts(since)
        logs = [row for row in logs if _parse_ts(row["timestamp"]) >= since_dt]

    logs = sorted(logs, key=lambda r: r["timestamp"], reverse=True)[:limit]

    evidence = []
    for row in logs:
        fields = {k: v for k, v in row.items() if k not in ("timestamp", "account_id", "level", "event")}
        snippet = f"[{row['level']}] {row['event']} " + " ".join(f"{k}={v}" for k, v in fields.items())
        locator = f"log:{account_id}:{row['timestamp']}:{row['event']}"
        evidence.append(make_evidence("log", locator, snippet, 1.0))

    note = None if evidence else f"no log events found for account '{account_id}'" + (f" since {since}" if since else "")
    return tool_result("get_account_logs", evidence, note=note)


async def get_incidents_tool(since: str | None = None, account_id: str | None = None) -> dict:
    incidents = list(load_incidents())
    if since:
        since_dt = _parse_ts(since)
        incidents = [i for i in incidents if _parse_ts(i["started_at"]) >= since_dt]
    if account_id:
        incidents = [i for i in incidents if _incident_relates_to_account(i, account_id)]

    evidence = []
    for inc in incidents:
        locator = f"incident:{inc['id']}"
        evidence.append(make_evidence("incident", locator, f"{inc['title']} — {inc['summary']}", 1.0))

    return tool_result("get_incidents", evidence, note=None if evidence else "no incidents matched")


def _incident_relates_to_account(incident: dict, account_id: str) -> bool:
    from app.seed.loader import load_tickets

    related_tickets = set(incident.get("related_tickets") or [])
    account_tickets = {t["id"] for t in load_tickets() if t.get("account_id") == account_id}
    if related_tickets & account_tickets:
        return True

    account = get_account(account_id)
    if not account:
        return False
    haystack = f"{incident['title']} {incident['summary']}".lower()
    name_first_word = account["name"].split()[0].lower()
    return name_first_word in haystack or account_id.lower() in haystack


async def list_accounts_tool() -> dict:
    evidence = [
        make_evidence("account", f"account:{a['id']}", f"{a['name']} — tier={a['tier']}, city={a['home_city']}", 1.0)
        for a in load_accounts()
    ]
    return tool_result("list_accounts", evidence)

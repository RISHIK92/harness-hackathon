"""Datadog — API key + application key.

Indexes MONITORS and INCIDENTS, not metrics or raw logs. A support agent
needs "is something on fire right now, and is it this?" — a monitor's name,
message and current state answer that. Metric time-series do not embed
usefully, and shipping raw logs into a vector store is expensive and mostly
noise.
"""
from __future__ import annotations

import httpx

from app.services.connectors.base import Item

provider = "datadog"

# Datadog is region-partitioned and the wrong host simply 403s, which reads
# like a bad key. Site is part of the connection config, not a guess.
DEFAULT_SITE = "datadoghq.com"


def _base(config: dict) -> str:
    return f"https://api.{config.get('site') or DEFAULT_SITE}"


def _headers(credentials: dict) -> dict:
    return {
        "DD-API-KEY": credentials["api_key"],
        "DD-APPLICATION-KEY": credentials["app_key"],
        "Content-Type": "application/json",
    }


def verify(credentials: dict, config: dict) -> dict:
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(f"{_base(config)}/api/v1/validate", headers=_headers(credentials))
    if resp.status_code in (401, 403):
        raise ValueError(
            "Datadog rejected those keys — check the API key, the application key, "
            f"and that the site is right (currently {config.get('site') or DEFAULT_SITE})"
        )
    resp.raise_for_status()
    return {"display_name": f"Datadog ({config.get('site') or DEFAULT_SITE})"}


def list_resources(credentials: dict, config: dict) -> list[dict]:
    # Two fixed buckets rather than discovered resources: Datadog has no
    # natural "project" unit, and these are the two things worth indexing.
    return [
        {"id": "monitors", "name": "Monitors (alert definitions and current state)"},
        {"id": "incidents", "name": "Incidents (declared, with timeline)"},
    ]


def fetch(credentials: dict, config: dict, resource_id: str) -> list[Item]:
    if resource_id == "monitors":
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{_base(config)}/api/v1/monitor", headers=_headers(credentials))
            resp.raise_for_status()
            monitors = resp.json()
        return [
            Item(
                external_id=f"monitor-{m['id']}",
                title=f"[{m.get('overall_state', 'unknown')}] {m.get('name', '')}",
                text=f"{m.get('name', '')}\n{m.get('message', '')}\nquery: {m.get('query', '')}",
                url=f"https://app.{config.get('site') or DEFAULT_SITE}/monitors/{m['id']}",
                meta={"state": m.get("overall_state", "unknown"), "tags": ",".join(m.get("tags", []))},
            )
            for m in monitors
        ]

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            f"{_base(config)}/api/v2/incidents",
            headers=_headers(credentials),
            params={"page[size]": 100},
        )
        if resp.status_code == 403:
            # Incident Management is a separate Datadog product; not having
            # it is normal, not an error worth failing the whole sync over.
            return []
        resp.raise_for_status()
        data = resp.json().get("data", [])
    items = []
    for incident in data:
        attrs = incident.get("attributes") or {}
        fields = attrs.get("fields") or {}
        items.append(Item(
            external_id=f"incident-{incident.get('id')}",
            title=attrs.get("title", ""),
            text=f"{attrs.get('title', '')}\n{attrs.get('customer_impact_scope', '')}\n"
                 f"severity: {(fields.get('severity') or {}).get('value', 'unknown')}\n"
                 f"state: {attrs.get('state', 'unknown')}",
            url="",
            meta={"state": attrs.get("state", "unknown")},
        ))
    return items

"""Startup: detect -> discover -> rank -> tier -> gateway (FR-6..FR-11).

Ordering matters: the startup block is printed BEFORE repository indexing,
because indexing a large repo can exceed NFR-6's five-second guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import ConfigError
from .detect import AMBIGUOUS, PROVIDERS, Provider, detect_provider, resolve
from .discover import Discovery, discover, probe_host
from .gateway import Gateway, ModelSpec
from .rank import NoChatModel, ctx_of, select_models, tier_of
from .router import Router


@dataclass
class Bootstrap:
    provider: Provider
    discovery: Discovery
    gateway: Gateway
    router: Router
    primary: ModelSpec
    cheap: ModelSpec
    degraded: list[str]


def bring_up(cfg, log, events=None, budgets=None) -> Bootstrap:
    degraded: list[str] = []

    # FR-6: prefix only, no network.
    raw_name = detect_provider(cfg.api_key)
    prober = None
    if raw_name == AMBIGUOUS and not cfg.base_url and not cfg.provider:
        prober = lambda p: probe_host(p, cfg.api_key)      # noqa: E731
    provider = resolve(cfg.api_key, base_url=cfg.base_url,
                       forced=cfg.provider, prober=prober)

    if not provider.base_url:
        raise ConfigError(
            "Could not determine the API endpoint from the key. "
            "Set AI_BASE_URL (or HARNESS_PROVIDER) and retry.")
    if raw_name == AMBIGUOUS and provider.name == "openai" and prober:
        degraded.append("provider_guess")

    # FR-7: one GET, filtered to chat-capable models.
    disc = discover(provider, cfg.api_key, cache_dir=cfg.work_dir / "cache",
                    use_cache=not cfg.no_cache)
    if not disc.ok:
        degraded.append("no_discovery")

    # FR-8 / FR-9.
    try:
        primary_id, cheap_id = select_models(
            disc.ids, provider.name,
            model_override=cfg.model, cheap_override=cfg.cheap_model)
    except NoChatModel as exc:
        raise ConfigError(str(exc)) from exc

    primary = ModelSpec(primary_id, cfg.tier or tier_of(primary_id, provider.name),
                        ctx_of(primary_id, disc.ctx.get(primary_id)))
    cheap = ModelSpec(cheap_id, tier_of(cheap_id, provider.name),
                      ctx_of(cheap_id, disc.ctx.get(cheap_id)))
    if primary.id == cheap.id:
        degraded.append("single_model")

    cfg.effective_tier = primary.tier

    gw = Gateway(provider, cfg.api_key, cfg, log, events=events,
                 budgets=budgets)
    gw.primary, gw.cheap = primary, cheap
    router = Router(gw, cfg, log)

    return Bootstrap(provider=provider, discovery=disc, gateway=gw,
                     router=router, primary=primary, cheap=cheap,
                     degraded=degraded)


TIER_BLURB = {
    "T0": "selection-mode action space, n=4 sampling + vote, LINE_RANGE edits, "
          "context capped at 35% of window",
    "T1": "tool schema, n=2 sampling, SEARCH/REPLACE edits, 55% of window",
    "T2": "bash-first action space, n=1, terse planning, 75% of window",
}


def print_startup(bs: Bootstrap, cfg, log, repo_info: dict | None = None) -> None:
    """FR-11 / NFR-6: printed before any work begins."""
    repo_info = repo_info or {}
    log.rule("HARNESS v2.1")
    src = ("AI_BASE_URL" if cfg.base_url else
           "HARNESS_PROVIDER" if cfg.provider else
           f"key prefix {_prefix(cfg.api_key)}")
    log.raw(f"provider        {bs.provider.name}  (from {src})")
    log.raw(f"base url        {bs.provider.base_url}")

    if bs.discovery.ok:
        log.raw(f"models found    {len(bs.discovery.ids)} chat-capable "
                f"of {bs.discovery.raw_count} listed")
        shown = ", ".join(bs.discovery.ids[:6])
        if len(bs.discovery.ids) > 6:
            shown += f", +{len(bs.discovery.ids)-6} more"
        if shown:
            log.raw(f"                {shown}")
    else:
        log.raw("models found    none (discovery failed) - using overrides")

    log.raw(f"primary model   {bs.primary.id}    tier {bs.primary.tier}   "
            f"ctx {bs.primary.ctx}")
    log.raw(f"cheap model     {bs.cheap.id}    tier {bs.cheap.tier}")
    log.raw(f"tier profile    {bs.primary.tier}: "
            f"{TIER_BLURB.get(bs.primary.tier, '')}")

    if repo_info:
        log.raw(f"repository      {repo_info.get('path','')}   "
                f"language {repo_info.get('language','unknown')}")
        if repo_info.get("test_cmd"):
            log.raw(f"test command    {repo_info['test_cmd']}")
        if repo_info.get("lint_cmd"):
            log.raw(f"lint command    {repo_info['lint_cmd']}")

    log.raw(f"budgets         tokens {cfg.token_budget//1000}k - "
            f"wall {cfg.time_budget//60}m - cycles {cfg.max_cycles}")
    for tag in bs.degraded:
        log.raw(f"degraded        {tag}")
    log.rule()


def _prefix(key: str) -> str:
    for p in ("sk-ant-api", "sk-ant-", "sk-or-v1-", "sk-proj-", "sk-svcacct-",
              "sk-admin-", "gsk_", "xai-", "csk-", "AIza", "tgp_v1_", "fw_",
              "sk-"):
        if (key or "").startswith(p):
            return p
    return "unknown"

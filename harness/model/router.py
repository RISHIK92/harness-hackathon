"""Phase -> model routing (FR-8, NFR-1, SPEC.md 3.4).

Primary model for investigation, scoping and implementation; the cheapest
available model for verification and summarization.
"""
from __future__ import annotations

# role -> ("primary"|"cheap", temperature)
ROLES = {
    "p0_normalize": ("cheap", 0.0),
    "p1_hypotheses": ("primary", 0.0),
    "p1_evidence": ("primary", 0.0),
    "p1_synthesis": ("primary", 0.0),
    "p1_localize": ("primary", 0.0),
    "p2_scope": ("primary", 0.0),
    "p3_implement": ("primary", 0.0),
    "p3_repair": ("cheap", 0.0),
    "p4_judge_diff": ("cheap", 0.0),
    "p4_judge_practices": ("cheap", 0.0),
    "p4_triage": ("primary", 0.0),
    "summarize": ("cheap", 0.0),
    "repro_test": ("cheap", 0.0),
}

# Roles that may be skipped entirely under budget pressure (advisory only).
SKIPPABLE = {"p4_judge_practices", "summarize", "p0_normalize"}


class Router:
    def __init__(self, gateway, cfg, log) -> None:
        self.gw = gateway
        self.cfg = cfg
        self.log = log
        from ..watcher import Watcher
        self.watcher = Watcher(log=log)

    @property
    def single_model(self) -> bool:
        return (self.gw.primary is not None and self.gw.cheap is not None
                and self.gw.primary.id == self.gw.cheap.id)

    def spec_for(self, role: str):
        which, _ = ROLES.get(role, ("primary", 0.0))
        return self.gw.primary if which == "primary" else self.gw.cheap

    def call(self, role: str, messages: list[dict], phase: str,
             max_tokens: int = 4096, temperature: float | None = None,
             tools: list[dict] | None = None, cache_prefix: int = 0,
             samples: int = 1):
        which, default_temp = ROLES.get(role, ("primary", 0.0))
        spec = self.spec_for(role)
        if spec is None:
            raise RuntimeError(f"no model bound for role {role}")

        temp = default_temp if temperature is None else temperature

        # Single-model degradation: clamp cheap-role calls (SPEC.md 3.4).
        if which == "cheap" and self.single_model:
            max_tokens = max(256, int(max_tokens * 0.25))

        if samples <= 1:
            reply = self.gw.call(messages, spec.id, phase=phase,
                                 max_tokens=max_tokens, temperature=temp,
                                 tools=tools, cache_prefix=cache_prefix,
                                 label=role)
            # A reply cut off at the ceiling never reached its answer. Ask
            # again with room rather than handing an empty string to a phase
            # that will call it a failure and burn a cycle on it.
            budget = self.watcher.observe(role, max_tokens, reply)
            while budget:
                reply = self.gw.call(messages, spec.id, phase=phase,
                                     max_tokens=budget, temperature=temp,
                                     tools=tools, cache_prefix=cache_prefix,
                                     label=f"{role}+", no_cache=True)
                budget = self.watcher.observe(role, budget, reply)
            return reply

        replies = []
        for i in range(samples):
            replies.append(self.gw.call(
                messages, spec.id, phase=phase, max_tokens=max_tokens,
                temperature=temp if temp > 0 else 0.7, tools=tools,
                cache_prefix=cache_prefix, label=f"{role}#{i+1}"))
        return replies

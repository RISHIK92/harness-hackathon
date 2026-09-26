"""Deterministic state machine (SPEC.md 10.2).

WP0: a no-op pipeline that proves the contract end to end.  WP1-WP7 fill the
phases in; the transition table stays a table, never scattered conditionals.
"""
from __future__ import annotations

from . import exits
from .budget import Budgets
from .context.events import EventLog
from .logging_ui import Logger


class Orchestrator:
    def __init__(self, cfg, log: Logger) -> None:
        self.cfg = cfg
        self.log = log
        self.run_dir = cfg.work_dir / "run"
        self.events = EventLog(self.run_dir, secrets=[cfg.api_key])
        self.budgets = Budgets.from_config(cfg)

    def run(self) -> int:
        self.events.append("phase_start", "P0", {"issue_len": len(self.cfg.issue)})
        self.log.rule("HARNESS v2.1")
        self.log.raw(f"repository      {self.cfg.repo_path}")
        self.log.raw(f"issue           {len(self.cfg.issue)} chars")
        self.log.raw(f"budgets         tokens {self.cfg.token_budget//1000}k "
                     f"- wall {self.cfg.time_budget//60}m "
                     f"- cycles {self.cfg.max_cycles}")
        self.log.rule()
        self.log.line("pipeline not yet wired (WP0 skeleton)", phase="P0")
        self.events.append("phase_end", "P0", {}, summary="skeleton")
        return exits.NO_FIX

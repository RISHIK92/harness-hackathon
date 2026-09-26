"""Deterministic state machine (SPEC.md 10.2).

Transitions live in a table, never in scattered conditionals, so the loop is
testable in isolation (NFR-5).
"""
from __future__ import annotations

import time

from . import exits
from .budget import Budgets
from .context.events import EventLog
from .logging_ui import Logger
from .model import bootstrap as boot


class Orchestrator:
    def __init__(self, cfg, log: Logger) -> None:
        self.cfg = cfg
        self.log = log
        self.run_dir = cfg.work_dir / "run"
        self.events = EventLog(self.run_dir, secrets=[cfg.api_key])
        self.budgets = Budgets.from_config(cfg)
        self.bs = None
        self.caps = None

    # -- bootstrap ---------------------------------------------------------
    def bring_up(self) -> None:
        """FR-6..FR-11, NFR-6: everything printed before any indexing."""
        t0 = time.time()
        self.log.set_phase("P0")
        self.bs = boot.bring_up(self.cfg, self.log, events=self.events,
                                budgets=self.budgets)

        repo_info = {"path": str(self.cfg.repo_path)}
        try:
            from .repo.lang import describe          # WP3
            repo_info.update(describe(self.cfg.repo_path))
        except Exception:
            pass
        try:
            from .verify.toolchain import discover_toolchain   # WP2
            tc = discover_toolchain(self.cfg.repo_path, self.cfg)
            repo_info["test_cmd"] = tc.test_cmd
            repo_info["lint_cmd"] = tc.lint_cmd
            self.toolchain = tc
        except Exception:
            self.toolchain = None

        boot.print_startup(self.bs, self.cfg, self.log, repo_info)
        self.startup_s = time.time() - t0
        self.log.debug(f"startup took {self.startup_s:.2f}s")

    # -- run ---------------------------------------------------------------
    def run(self) -> int:
        self.events.append("phase_start", "P0",
                           {"issue_len": len(self.cfg.issue)})
        self.bring_up()

        # Capability probe runs after the block is printed, so it can never
        # delay NFR-6's five-second guarantee.
        from .model.probe import probe
        self.caps = probe(self.bs.gateway, self.bs.primary.id,
                          cache_dir=self.cfg.work_dir / "cache")
        if not self.caps.tool_calling:
            self.log.degraded("text_protocol", "model did not call the probe tool")
        if not self.caps.strict_json:
            self.log.degraded("loose_json", "using fenced-block protocol")

        self.log.phase("P0")
        self.log.line("pipeline wiring lands in WP4 (phases 0-2)")
        self.events.append("phase_end", "P0", {}, summary="bootstrap only")
        return exits.NO_FIX

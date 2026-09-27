"""Permission for outward-facing actions (SPEC.md 11.1, 30).

Only actions that leave this machine are gated: cloning someone's
repository, pushing a branch, opening a pull request, posting a comment.
Editing files and running tests are local and reversible, so they run freely
-- gating them would make the harness useless without making it safer.

Default is ASK. `HARNESS_AUTO` configures it, and an unrecognised value is
treated as ASK rather than as permission.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

CLONE = "clone"
PUSH = "push"
PR = "pr"
COMMENT = "comment"
INSTALL = "install"

# "Outward" means the action reaches beyond this working copy.
# Installing qualifies: it fetches from a public registry and can run
# a postinstall script from every transitive dependency.
OUTWARD = (CLONE, PUSH, PR, COMMENT, INSTALL)

ASK, AUTO, NEVER = "ask", "auto", "never"

DESCRIBE = {
    CLONE: "clone a repository onto this machine",
    PUSH: "push a branch to the remote",
    PR: "open a pull request",
    COMMENT: "post a comment on the issue",
    INSTALL: "install this repository's dependencies",
}


@dataclass
class Policy:
    """How each outward-facing action is decided."""
    mode: str = ASK
    only: set = None                  # actions explicitly allowed in auto

    @classmethod
    def from_env(cls) -> "Policy":
        raw = (os.environ.get("HARNESS_AUTO") or "").strip().lower()
        if not raw or raw in ("0", "off", "false", "no", ASK):
            return cls(mode=ASK)
        if raw in ("1", "on", "true", "yes", "all", AUTO):
            return cls(mode=AUTO, only=set(OUTWARD))
        if raw in ("never", "none", "offline"):
            return cls(mode=NEVER)
        # A comma list names exactly which actions may proceed unattended.
        named = {a.strip() for a in raw.split(",") if a.strip() in OUTWARD}
        if named:
            return cls(mode=AUTO, only=named)
        return cls(mode=ASK)          # unrecognised: ask, never assume yes

    def describe(self) -> str:
        if self.mode == NEVER:
            return "never (no outward-facing action)"
        if self.mode == AUTO:
            if self.only and self.only != set(OUTWARD):
                return "auto for " + ", ".join(sorted(self.only))
            return "auto (clone, push, pr, comment)"
        return "ask before anything leaves this machine"

    def allows(self, action: str) -> bool | None:
        """True to proceed, False to refuse, None to ask."""
        if self.mode == NEVER:
            return False
        if self.mode == AUTO and self.only and action in self.only:
            return True
        return None


class Gate:
    """Decides, records, and explains. `confirm` is the interactive card."""

    def __init__(self, policy: Policy, log, confirm=None) -> None:
        self.policy = policy
        self.log = log
        self.confirm = confirm
        self.decisions: list = []

    def allow(self, action: str, summary: str, rows=None,
              requested: bool = False) -> bool:
        """`requested` marks an action the operator asked for in the same
        breath. Cloning `owner/repo#123` is not a consequence of the run --
        it IS the run, and asking permission for it with nobody there to
        answer would refuse the command that was just given. Pushing and
        opening a pull request are different: nobody asked for those.
        """
        decided = self.policy.allows(action)
        if decided is False:
            self.log.note("refused", f"{DESCRIBE.get(action, action)} "
                                     f"(HARNESS_AUTO=never)")
            self.decisions.append((action, "refused"))
            return False
        if decided is True:
            self.log.note("auto", DESCRIBE.get(action, action))
            self.decisions.append((action, "auto"))
            return True

        if self.confirm is None:
            if requested:
                self.decisions.append((action, "requested"))
                return True
            # Nobody to ask: proceeding unattended would be deciding for them.
            self.log.note("skipped", f"{DESCRIBE.get(action, action)} needs "
                                     f"permission; set HARNESS_AUTO={action} "
                                     f"to allow it unattended")
            self.decisions.append((action, "skipped"))
            return False

        ok = self.confirm(action, summary, rows or [])
        self.decisions.append((action, "allowed" if ok else "declined"))
        if not ok:
            self.log.note("declined", DESCRIBE.get(action, action))
        return ok

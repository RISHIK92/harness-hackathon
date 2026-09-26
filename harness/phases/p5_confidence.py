"""Phase 5: confidence scoring and the loop decision (FR-34..FR-36).

C3, C4 and C5 are HARD gates and are checked first, so a fix can never be
submitted with unintended file changes or a disproportionate diff.  There is
no path from a failed condition to SUBMIT: FR-35 forbids silent submission,
and "the diff contains no unrelated changes" is a stated success criterion.
"""
from __future__ import annotations

from enum import Enum

from ..records import ConfidenceReport


class Action(str, Enum):
    SUBMIT = "SUBMIT"
    REVERT_AND_REIMPLEMENT = "REVERT_AND_REIMPLEMENT"
    RESCOPE = "RESCOPE"
    REIMPLEMENT_TARGETED = "REIMPLEMENT_TARGETED"
    REINVESTIGATE = "REINVESTIGATE"
    SUBMIT_BEST_EFFORT = "SUBMIT_BEST_EFFORT"


# Which phase each remedy re-enters. The orchestrator's transition table.
REMEDY_PHASE = {
    Action.REVERT_AND_REIMPLEMENT: "P3",
    Action.RESCOPE: "P2",
    Action.REIMPLEMENT_TARGETED: "P3",
    Action.REINVESTIGATE: "P1",
}


def score(root_cause, plan, verify, workspace, cfg) -> ConfidenceReport:
    """Every condition is computed from evidence, not asserted."""
    r = ConfidenceReport()

    # C1 -- root cause identified with executed evidence
    executed = [e for e in root_cause.evidence
                if e.source in ("grep", "read", "git", "test", "probe",
                                "sbfl", "bisect")]
    r.root_cause_evidenced = bool(executed) and root_cause.confidence != "low"

    # C2 -- the fix addresses the root cause.
    # A FIXED test transition is hard evidence and outranks the judge.
    fixed = bool(verify.full and verify.full.fixed) or \
        bool(verify.scoped and verify.scoped.fixed) or \
        verify.oracle_passes is True
    r.judge_conclusive = verify.judge.conclusive
    r.fix_addresses_root_cause = fixed or (
        verify.judge.addresses and verify.judge.conclusive)

    # C3 -- all existing tests pass (HARD)
    ran_tests = bool(verify.full or verify.scoped)
    r.existing_tests_pass = ran_tests and not verify.blocking

    # C4 -- no unintended file changes (HARD)
    changed = set(workspace.changed_files())
    allowed = plan.allowed
    r.no_unintended_changes = bool(changed) and changed <= allowed \
        and not (changed & plan.forbidden)

    # C5 -- diff proportional to scope (HARD)
    added, removed = workspace.diff_numstat()
    cap = int(plan.estimated_lines_changed * 1.3)
    if cfg.conservative:
        cap = min(cap, 15)
    r.diff_proportional = (added + removed) <= cap

    # C6 -- external factors ruled out or addressed
    ef = root_cause.external_factors or {}
    r.external_factors_resolved = bool(ef.get("ruled_out")) or \
        root_cause.classification in ("config", "external")

    r.blocking = [c for c in ConfidenceReport.CONDITIONS
                  if not getattr(r, c)]
    return r


def decide(r: ConfidenceReport, cycle: int, max_cycles: int) -> Action:
    """Hard gates first. No combination with a false hard gate returns SUBMIT."""
    if not r.no_unintended_changes:
        return Action.REVERT_AND_REIMPLEMENT
    if not r.diff_proportional:
        return Action.RESCOPE
    if not r.existing_tests_pass:
        return Action.REIMPLEMENT_TARGETED
    if not r.root_cause_evidenced:
        return Action.REINVESTIGATE
    if not r.fix_addresses_root_cause and r.judge_conclusive:
        return Action.REINVESTIGATE
    if not r.external_factors_resolved:
        return Action.REINVESTIGATE
    return Action.SUBMIT

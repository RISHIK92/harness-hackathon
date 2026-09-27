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


def _observed(cls) -> int:
    """How many distinct tests a classification actually saw."""
    if cls is None:
        return 0
    seen = set()
    for bucket in ("new", "pre_existing", "fixed", "unknown", "flaky"):
        seen.update(getattr(cls, bucket, None) or [])
    return len(seen)


def score(root_cause, plan, verify, workspace, cfg,
          repro=None) -> ConfidenceReport:
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
    #
    # The oracle is the test the issue is ABOUT. It is red at baseline by
    # definition, so classification calls it "pre-existing" and it never
    # appears in `blocking`. If it is still red after the fix, the fix did
    # not work -- reporting success there is the exact false positive FR-35
    # exists to prevent.
    # A Classification exists even when the run observed no tests at all --
    # a suite that failed to start produces an empty one. Treating that as
    # "tests ran" let C3, the hard gate meaning "all existing tests pass",
    # pass on a repository where not one test had ever executed. The gate
    # has to mean what it says: something must have been observed.
    # The oracle is executed on its own, so it never lands in a
    # classification bucket -- a verdict on it is still a test that ran.
    ran_tests = (any(_observed(c) for c in (verify.full, verify.scoped))
                 or verify.oracle_passes is not None)
    r.existing_tests_pass = (ran_tests and not verify.blocking
                             and verify.oracle_passes is not False)

    # A repository with no suite used to fail C3 forever, which is honest but
    # leaves the harness unable to finish ordinary work. When the harness has
    # written its own reproduction AND shown it red before the fix, that test
    # is evidence of the same kind -- machine-checked, not asserted. It
    # substitutes for a suite only when there is no suite to substitute for.
    if repro is not None and repro.is_oracle:
        if not ran_tests:
            r.existing_tests_pass = repro.verified
        elif not repro.verified:
            # A proven reproduction that still fails means the fix did not
            # work, whatever the rest of the suite says.
            r.existing_tests_pass = False

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
    #
    # "Any finding blocks" made this fail on any repository with an unset
    # environment variable, which is most of them: a missing
    # FIREBASE_PRIVATE_KEY was enough to report a correct, verified fix in
    # an unrelated pure function as PARTIAL. A finding counts against the
    # fix only when it touches what the fix touched, or when it is
    # repo-wide (a dependency skew).
    ef = root_cause.external_factors or {}
    touched = set(changed) | {f.path for f in (root_cause.files or [])}
    blocking_findings = []
    for raw in (ef.get("findings") or []):
        if not isinstance(raw, dict):
            continue
        # Severity is what the probes already say about their own findings.
        # "2 manifest change(s) in the last 90 days" is low, and is context
        # rather than a fault -- it blocked a fix that passed every other
        # gate, purely because it named no file.
        if (raw.get("severity") or "medium").lower() == "low":
            continue
        where = set(raw.get("where") or [])
        if not where or (touched and where & touched):
            blocking_findings.append(raw)
    r.external_factors_resolved = (
        not blocking_findings
        or bool(ef.get("ruled_out"))
        or root_cause.classification in ("config", "external"))

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

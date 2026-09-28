"""Exit codes (FR-4, SPEC.md 11.4)."""
SUCCESS = 0          # all hard gates green, C1/C2/C6 satisfied
PARTIAL = 2          # fix present, tests not worsened, a soft condition failed
NO_FIX = 3           # no confident root cause, or every attempt regressed
CONFIG_ERROR = 4     # no key, unreachable provider, no chat model, not a repo
INTERNAL = 5         # traceback written to .harness/run/error.log

REASON = {
    SUCCESS: "SUCCESS",
    PARTIAL: "PARTIAL",
    NO_FIX: "NO_FIX",
    CONFIG_ERROR: "CONFIG_ERROR",
    INTERNAL: "INTERNAL_ERROR",
}


def publish_refusal(exit_code: int, confidence, verification) -> str:
    """"" when a run's change may be proposed to a human, else why not.

    One rule for every way a change leaves the machine: the CLI's pull
    request, the console's menu and the service's /publish. Each used to
    decide for itself, and the service's decision was the exit code alone,
    so it published PARTIAL runs whose hard gates had failed.

    The exit code alone is not evidence. A PARTIAL run can have a failed
    HARD gate -- a broken test, a file changed outside the plan -- and one
    was opened as a pull request that turned `return 0` into
    `return "No data"` and broke the suite. Proposing a change that fails
    its own verification is the single thing this harness exists to prevent.

    `confidence` and `verification` are the run's ConfidenceReport and
    VerifyResult, or the confidence.json / verification.json they were
    written as -- the service only has the files, and must reach the same
    verdict from them.
    """
    if exit_code == SUCCESS:
        return ""
    if exit_code != PARTIAL:
        return f"the run did not produce a fix (exit {exit_code})"

    if not confidence:
        return "no confidence report to judge the change by"
    from .records import ConfidenceReport
    failed = [c for c in ConfidenceReport.HARD if not _field(confidence, c)]
    if failed:
        return "a hard gate failed: " + ", ".join(failed)

    for name in ("full", "scoped"):
        new = _field(_field(verification, name), "new")
        if new:
            return (f"the change introduces {len(new)} new test "
                    f"failure(s): {', '.join(new[:3])}")
    if _field(verification, "oracle_passes") is False:
        return "the reproduction test still fails"
    return ""


def _field(obj, name: str):
    """An attribute of a record, or the same key of its JSON form."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)

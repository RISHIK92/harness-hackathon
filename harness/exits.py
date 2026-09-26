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

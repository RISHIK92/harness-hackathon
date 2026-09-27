"""The readiness card printed by `make setup` (SPEC.md 13, 32).

Never interactive and never blocking: an evaluator may run
`make setup && make run` in a script, and a setup that waits for a keystroke
hangs the whole evaluation.

Degraded items are shown rather than hidden. Seeing `! ripgrep  not found`
and the run working anyway is evidence the degradation matrix is real.
"""
from __future__ import annotations

import sys

from .logging_ui import Logger
from .selfcheck import check

DETAIL = {
    "coverage": "coverage localization",
    "ripgrep": "fast search",
    "tree-sitter": "AST navigation",
    "ctags": "symbol extraction",
    "pytest": "harness self-tests",
    "git": "checkpoints and history",
}


def render(stream=None) -> int:
    log = Logger(stream=stream or sys.stdout)
    rows, fatal = check()
    t = log.theme

    log.banner("2.1")
    log.raw("")
    for item, status, detail in rows:
        text = detail or DETAIL.get(item, "")
        if not log.rich:
            mark = {"ok": "ok", "degraded": "degraded", "FATAL": "FATAL"}[status]
            log.raw(f"  {item:<14} {mark}" + (f"  {text}" if text else ""))
        elif status == "ok":
            log.ok(item, text)
        elif status == "degraded":
            log.step("warn", item, text, t.warn)
        else:
            log.fail(item, text)

    log.raw("")
    if fatal:
        if log.rich:
            log.fail("setup", "cannot continue: fix the items above")
        else:
            log.raw("setup cannot continue: fix the FATAL items above")
        return 1

    degraded = [r for r in rows if r[1] == "degraded"]
    if degraded:
        log.kv("note", f"{len(degraded)} optional tool(s) missing; every one "
                       f"degrades cleanly", t.dim)
    log.kv("ready", "next:  make run", t.bold)
    log.raw("")

    # What to actually type. `make setup` is where somebody meets this tool,
    # and "next: make run" does not tell them how to hand it a GitHub issue
    # or how to let it open the pull request.
    _usage(log, t)
    return 0


def _usage(log, t) -> None:
    def line(cmd: str, what: str) -> None:
        log.raw(f"    {t.accent}{cmd}{t.reset}")
        log.raw(f"      {t.dim}{what}{t.reset}")

    log.raw(f"  {t.bold}Run it{t.reset}")
    log.raw("")
    line('make run ISSUE="https://github.com/owner/repo/issues/12"',
         "fetch the issue, clone the repo, fix it, verify it")
    line('make run ISSUE="parse_date crashes on input with no separator"',
         "plain text works too, against the repository you are in")
    line("make run",
         "at a terminal with no issue: the interactive console, and chat")
    log.raw("")
    log.raw(f"  {t.bold}Preparing the machine{t.reset}")
    log.raw("")
    line('make run ISSUE="<url>" LIVE_ENV=True',
         "may install dependencies, make a venv, or use a container")
    log.raw(f"      {t.dim}default off: it clones and edits without touching "
            f"this machine,{t.reset}")
    log.raw(f"      {t.dim}but the suite usually cannot run, so verification "
            f"is weaker{t.reset}")
    log.raw("")
    log.raw(f"  {t.bold}Opening a pull request{t.reset}")
    log.raw("")
    line('make run ISSUE="<url>" PR=True',
         "push the branch and open the PR, but only if the fix verifies")
    line('make run ISSUE="<url>" PR=False',
         "never push; leave the change in the working tree")
    log.raw(f"      {t.dim}unset: asks at a terminal, declines when "
            f"unattended{t.reset}")
    log.raw("")
    log.raw(f"  {t.bold}Credentials{t.reset}")
    log.raw("")
    log.raw(f"    {t.dim}AI_API_KEY   required; read from the environment, "
            f"never stored{t.reset}")
    log.raw(f"    {t.dim}GitHub       `gh auth login`, or set GITHUB_TOKEN"
            f"{t.reset}")
    log.raw("")

    # Which keys work, and the two that need a hint. The provider is read
    # from the key's prefix without an API call; the plain `sk-` shapes are
    # indistinguishable, so they are resolved by probing in order.
    log.raw(f"  {t.bold}Providers{t.reset}   "
            f"{t.dim}detected from the key prefix{t.reset}")
    log.raw("")
    for prefix, who in (("sk-or-v1-", "OpenRouter"),
                        ("gsk_", "Groq"),
                        ("csk-", "Cerebras"),
                        ("xai-", "xAI"),
                        ("AIza", "Google"),
                        ("tgp_v1_", "Together"),
                        ("fw_", "Fireworks")):
        log.raw(f"    {t.accent}{prefix:<11}{t.reset}{t.dim}{who}{t.reset}")
    log.raw(f"    {t.accent}{'sk-':<11}{t.reset}{t.dim}DeepSeek, Qwen, "
            f"OpenAI or Mistral - probed in that order{t.reset}")
    log.raw("")
    log.raw(f"    {t.dim}HARNESS_PROVIDER=deepseek|qwen|groq|... skips the "
            f"probe{t.reset}")
    log.raw(f"    {t.dim}AI_BASE_URL sets any OpenAI-compatible endpoint "
            f"(mainland DashScope, a gateway){t.reset}")
    log.raw(f"    {t.dim}HARNESS_MODEL / HARNESS_CHEAP_MODEL name a model "
            f"outright{t.reset}")
    log.raw("")


def main() -> int:
    return render()


if __name__ == "__main__":
    raise SystemExit(main())

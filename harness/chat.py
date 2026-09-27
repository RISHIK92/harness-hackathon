"""Conversation (SPEC.md 38).

The menu asks one question and runs one pipeline. That is the right shape
for a task, and the wrong shape for everything around a task -- "what does
this module do", "why did that fail", "is the fix safe to merge". Those are
questions, and answering them by launching a six-phase run is absurd.

So: a prompt, a transcript, and the same frame. The screen already had the
parts -- `body` scrolls, `status` is pinned -- so the chat is a loop over
the existing key reader rather than a second UI.

Two rules keep it honest, and they are the reason this file is small:

  * **The model never acts from here.** It answers. Anything that touches
    the repository goes through the pipeline, with its gates and its
    evidence, started explicitly with `/run`. A chat reply that edited files
    would be a second, unverified code path to the thing the whole harness
    exists to do carefully.
  * **What it is told is what is true.** The repository facts in the system
    prompt are read off disk at send time -- the real test command, the real
    languages -- not remembered from startup.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

from . import keys as K

MAX_TURNS = 24            # older turns are dropped, oldest first
MAX_REPLY_TOKENS = 1400
INPUT_LIMIT = 4000

HELP = [
    ("/run <issue>", "fix something: the full pipeline, gates and all"),
    ("/repo", "what the harness sees here"),
    ("/diff", "changes in the working tree"),
    ("/clear", "forget the conversation"),
    ("/help", "this"),
    ("/exit", "leave (or ctrl-c twice)"),
]

SYSTEM = """You are the assistant inside an autonomous coding harness, \
talking to the engineer who runs it.

You are ANSWERING, not acting. You cannot edit files, run commands or open \
pull requests from this conversation. When the engineer wants a change made, \
tell them to run `/run <description>`, which starts the harness pipeline \
with its verification and permission gates.

Be concise and concrete. Prefer naming a specific file, function or command \
over general advice. Say plainly when you do not know something about this \
repository rather than guessing at its contents -- you are seeing a summary, \
not the code.
"""


@dataclass
class Turn:
    role: str
    text: str


@dataclass
class Chat:
    """The conversation loop. Owns no terminal state of its own."""
    console: object
    cfg: object
    log: object
    turns: list = field(default_factory=list)
    _boot: object = None

    # -- model ------------------------------------------------------------
    def _gateway(self):
        """Brought up on first use, so opening the chat costs nothing."""
        if self._boot is None:
            from .model import bootstrap as boot
            self._boot = boot.bring_up(self.cfg, self.log)
        return self._boot

    def _repo_facts(self) -> str:
        """Read now, not remembered: the toolchain can change under us."""
        from .verify.toolchain import discover_toolchain
        try:
            tc = discover_toolchain(self.cfg.repo_path, self.cfg)
        except (OSError, ValueError) as exc:
            return (f"Repository: {self.cfg.repo_path}\n"
                    f"(toolchain could not be read: {exc})")
        lines = [f"Repository: {self.cfg.repo_path}",
                 f"Language: {tc.language or 'unknown'}"
                 + (f" ({tc.framework})" if tc.framework else ""),
                 f"Test command: {tc.test_cmd or 'none discovered'}",
                 f"Lint command: {tc.lint_cmd or 'none discovered'}"]
        try:
            from .repo.search import Search
            files = Search(self.cfg.repo_path).files()
            lines.append(f"Tracked source files: {len(files)}")
            top = sorted({f.split("/")[0] for f in files if "/" in f})[:12]
            if top:
                lines.append("Top-level: " + ", ".join(top))
        except (OSError, ValueError) as exc:
            # Not fatal: the model simply gets fewer facts about the repo.
            lines.append(f"(could not list the source files: {exc})")
        return "\n".join(lines)

    def _messages(self, question: str) -> list:
        msgs = [{"role": "system",
                 "content": SYSTEM + "\n" + self._repo_facts()}]
        for turn in self.turns[-MAX_TURNS:]:
            msgs.append({"role": turn.role, "content": turn.text})
        msgs.append({"role": "user", "content": question})
        return msgs

    def ask(self, question: str) -> str:
        bs = self._gateway()
        reply = bs.gateway.call(self._messages(question), bs.primary.id,
                                phase="CHAT", max_tokens=MAX_REPLY_TOKENS,
                                temperature=0.2, label="chat")
        return (reply.text or "").strip() or "(no reply)"

    # -- rendering ---------------------------------------------------------
    def _width(self) -> int:
        screen = self.console.screen
        return max(40, min(100, (screen._cols if screen else 80) - 6))

    def _say(self, who: str, text: str) -> None:
        """One message into the transcript, wrapped to the frame."""
        t = self.log.theme
        g = self.log.g
        mark = {"you": (g.PHASE, t.accent),
                "harness": (g.MODEL, t.ok),
                "note": (g.WARN, t.warn)}.get(who, (g.DOT, t.dim))
        self.log.raw("")
        first = True
        for para in (text or "").split("\n"):
            if not para.strip():
                self.log.raw("")
                continue
            for line in textwrap.wrap(para, self._width()) or [""]:
                if first:
                    self.log.raw(f"  {t.paint(mark[0], mark[1])} {line}")
                    first = False
                else:
                    self.log.raw(f"    {line}")

    def _prompt(self, buffer: str, busy: str = "") -> None:
        t = self.log.theme
        if busy:
            self.console.screen.set_status(f"  {t.dim}{busy}{t.reset}")
        else:
            shown = buffer[-(self._width()):]
            cursor = f"{t.accent}█{t.reset}" if self.log.unicode else "_"
            self.console.screen.set_status(
                f"  {t.accent}>{t.reset} {shown}{cursor}"
                f"   {t.dim}enter send   /help   ctrl-c twice to exit{t.reset}")
        self.console.screen.render()

    # -- commands ----------------------------------------------------------
    def _command(self, text: str) -> str | None:
        """Returns "exit", "run:<issue>", or None when handled in place."""
        word, _, rest = text.partition(" ")
        word = word.lower()

        if word in ("/exit", "/quit", "/q"):
            return "exit"
        if word == "/help":
            self._say("note", "Commands")
            for name, what in HELP:
                self.log.raw(f"    {self.log.theme.accent}{name:<14}"
                             f"{self.log.theme.reset}{self.log.theme.dim}"
                             f"{what}{self.log.theme.reset}")
            return None
        if word == "/clear":
            self.turns.clear()
            self._say("note", "conversation forgotten")
            return None
        if word == "/repo":
            self._say("note", self._repo_facts())
            return None
        if word == "/diff":
            self._say("note", self._diff())
            return None
        if word == "/run":
            if not rest.strip():
                self._say("note", "say what to fix: /run <description of the "
                                  "issue>")
                return None
            return f"run:{rest.strip()}"
        self._say("note", f"unknown command {word}. /help lists them.")
        return None

    def _diff(self) -> str:
        from .repo.workspace import Workspace
        try:
            ws = Workspace(self.cfg.repo_path, self.log)
            changed = ws.changed_files()
            if not changed:
                return "working tree is clean"
            added, removed = ws.diff_numstat()
            return (f"{len(changed)} file(s) changed, +{added} -{removed}\n"
                    + "\n".join(f"  {f}" for f in changed[:20]))
        except Exception as exc:
            return f"could not read the working tree: {exc}"

    # -- the loop ----------------------------------------------------------
    def run(self) -> str | None:
        """Chat until the engineer leaves. Returns an issue to run, or None.

        Handing the issue back rather than running it here keeps the pipeline
        exactly where it was: one caller, one set of gates.
        """
        self._say("note", "Ask me about this repository, or /run <issue> to "
                          "fix something. /help for commands.")
        buffer = ""
        interrupts = 0
        self._prompt(buffer)

        while True:
            key = self.console.source.read()

            if key == K.CTRL_C:
                if buffer:
                    buffer, interrupts = "", 0
                    self._prompt(buffer)
                    continue
                interrupts += 1
                if interrupts >= 2:
                    return None
                self._say("note", "ctrl-c again to leave")
                self._prompt(buffer)
                continue
            interrupts = 0

            if key in (K.ENTER,):
                text = buffer.strip()
                buffer = ""
                if not text:
                    self._prompt(buffer)
                    continue
                self._say("you", text)

                if text.startswith("/"):
                    outcome = self._command(text)
                    if outcome == "exit":
                        return None
                    if outcome and outcome.startswith("run:"):
                        return outcome[4:]
                    self._prompt(buffer)
                    continue

                self._prompt(buffer, busy="thinking…")
                try:
                    answer = self.ask(text)
                except Exception as exc:
                    self._say("note", f"the model call failed: {exc}")
                    self._prompt(buffer)
                    continue
                self.turns.append(Turn("user", text))
                self.turns.append(Turn("assistant", answer))
                self._say("harness", answer)
                self._prompt(buffer)
                continue

            if key == K.ESC:
                buffer = ""
                self._prompt(buffer)
                continue
            if key == K.BACKSPACE:
                buffer = buffer[:-1]
                self._prompt(buffer)
                continue
            if key == K.TAB:
                # No completion to offer yet; a literal tab in a prompt is
                # never what was meant.
                continue
            if len(key) == 1 and key.isprintable() and len(buffer) < INPUT_LIMIT:
                buffer += key
                self._prompt(buffer)

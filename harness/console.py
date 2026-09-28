"""The interactive console (SPEC.md 11.1).

This is not a mode. It is what the CLI does when it has a terminal and no
issue was supplied -- the same way `git` pages and `ls` colourises. The run
itself is identical either way: same phases, same budgets, same verification,
same confidence report. Only the way you say what to work on differs.

Rendered inline rather than in an alternate screen buffer, so scrollback
survives and a crash still leaves a readable transcript.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import finder
from . import keys as K
from .screen import Screen
from .ui import human_time

HISTORY_LIMIT = 5


@dataclass
class Choice:
    """What the operator asked for."""
    issue: str = ""
    repo: Path | None = None
    github_ref: str = ""
    quit: bool = False

    def __bool__(self) -> bool:
        return bool(self.issue) and not self.quit


@dataclass
class Item:
    label: str
    hint: str = ""
    key: str = ""
    enabled: bool = True


@dataclass
class Console:
    log: object
    cfg: object
    source: K.KeySource = None
    stream: object = None
    screen: object = None
    _lines_drawn: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.stream = self.stream or sys.stdout
        if self.source is None:
            self.source = K.reader()

    # -- full-screen ownership --------------------------------------------
    @property
    def full(self) -> bool:
        return self.screen is not None and self.screen.open_

    def take_terminal(self) -> bool:
        """Own the whole terminal for the session. Falls back to inline
        rendering when the terminal is too small or is not a terminal."""
        if self.screen is None:
            self.screen = Screen(self.stream, self.log.theme,
                                 getattr(self.log, "unicode", True))
        if not self.screen.open():
            return False
        self.screen.set_header(self._header_lines())
        self.log.attach_screen(self.screen)
        self.screen.render()
        return True

    def release_terminal(self) -> str:
        """Leave the alternate buffer and hand back the transcript, so the
        session is in scrollback rather than lost with the frame."""
        if self.screen is None:
            return ""
        transcript = self.screen.close()
        self.log.detach_screen()
        return transcript

    def _header_lines(self) -> list:
        t = self.log.theme
        bar = "\u2500" * 62 if getattr(self.log, "unicode", True) else "-" * 62
        mark = "\u25c8" if getattr(self.log, "unicode", True) else "#"
        return [
            f"  {t.accent}{bar}{t.reset}",
            f"  {t.bold}{mark} HARNESS{t.reset}   "
            f"{t.dim}{self.cfg.repo_path}{t.reset}",
            f"  {t.accent}{bar}{t.reset}",
        ]

    # -- drawing -----------------------------------------------------------
    def _w(self, text: str = "") -> None:
        self.stream.write(text + "\n")
        self._lines_drawn += 1

    def _flush(self) -> None:
        self.stream.flush()

    def _erase(self) -> None:
        """Rub out what we drew, so a menu redraws in place.

        Only used in inline mode. It counts LOGICAL lines, so a line that
        wraps leaves a stale copy behind -- which is exactly why the
        full-screen path composes an absolute frame instead.
        """
        if self._lines_drawn and getattr(self.log, "rich", False):
            self.stream.write(f"\x1b[{self._lines_drawn}A\x1b[0J")
        self._lines_drawn = 0

    def _draw(self, lines: list) -> None:
        """Show a panel: a frame when we own the terminal, inline otherwise."""
        if self.full:
            self.screen.set_panel(lines)
            self.screen.render()
            return
        self._erase()
        for line in lines:
            self._w(line)
        self._flush()

    def _undraw(self) -> None:
        if self.full:
            self.screen.set_panel(None)
            self.screen.render()
            return
        self._erase()

    def menu(self, title: str, items: list, hint: str = "",
             start: int = 0) -> int | None:
        """Render a menu and return the chosen index, or None to go back.

        `start` keeps the cursor where it was: returning from an action to a
        menu that has jumped back to the top reads as the list having moved
        by itself.
        """
        t = self.log.theme
        self.source.enter_raw()      # ask_line() drops out of raw mode
        enabled = [i for i, it in enumerate(items) if it.enabled]
        if not enabled:
            return None
        selected = start if start in enabled else enabled[0]

        while True:
            lines = ["", f"    {t.bold}{title}{t.reset}", ""]
            for i, item in enumerate(items):
                mark = self.log.g.PHASE if i == selected else " "
                colour = t.accent if i == selected else ""
                body = item.label.ljust(30)
                if not item.enabled:
                    body = f"{t.dim}{body}{t.reset}"
                lines.append(f"  {t.paint(mark, t.accent)} "
                             f"{t.paint(body, colour)}"
                             f"{t.dim}{item.hint}{t.reset}")
            lines += ["", f"    {t.dim}"
                          f"{hint or '↑↓ move   tab run   q quit'}{t.reset}"]
            self._draw(lines)

            key = self.source.read()
            if key in (K.CTRL_C, "q", K.ESC):
                self._undraw()
                return None
            if key == K.UP:
                here = enabled.index(selected)
                selected = enabled[(here - 1) % len(enabled)]
            elif key == K.DOWN:
                here = enabled.index(selected)
                selected = enabled[(here + 1) % len(enabled)]
            elif key in (K.TAB, K.ENTER):
                return selected
            elif key.isdigit() and 1 <= int(key) <= len(items):
                idx = int(key) - 1
                if items[idx].enabled:
                    return idx

    # -- the @ repository picker -------------------------------------------
    def pick_repo(self, query: str = "") -> Path | None:
        """Type to filter local git repositories. `@` opens this anywhere."""
        t = self.log.theme
        self.source.enter_raw()
        self._draw(["", f"    {t.bold}Local repositories{t.reset}", "",
                    f"    {t.dim}scanning...{t.reset}"])
        repos = finder.discover(cache_dir=self.cfg.work_dir / "cache")
        if not repos:
            self._note("no git repositories found nearby")
            return None

        selected = 0
        while True:
            hits = finder.match(repos, query)
            selected = min(selected, max(0, len(hits) - 1))
            lines = ["", f"    {t.bold}Local repositories{t.reset}",
                     f"  {t.paint(self.log.g.PHASE, t.accent)} "
                     f"@{query}{t.accent}\u2588{t.reset}", ""]
            if not hits:
                lines.append(f"    {t.dim}nothing matches{t.reset}")
            for i, repo in enumerate(hits[:10]):
                mark = self.log.g.PHASE if i == selected else " "
                colour = t.accent if i == selected else ""
                lines.append(f"  {t.paint(mark, t.accent)} "
                             f"{t.paint(repo.name[:28].ljust(30), colour)}"
                             f"{t.dim}{repo.home}{t.reset}")
            more = max(0, len(hits) - 10)
            if more:
                lines.append(f"    {t.dim}+{more} more - keep typing{t.reset}")
            lines += ["", f"    {t.dim}type to filter   \u2191\u2193 move   "
                          f"tab pick   esc back{t.reset}"]
            self._draw(lines)

            key = self.source.read()
            if key in (K.ESC, K.CTRL_C):
                self._undraw()
                return None
            if key in (K.TAB, K.ENTER):
                if hits:
                    self._undraw()
                    return Path(hits[selected].path)
                continue
            if key == K.UP:
                selected = max(0, selected - 1)
            elif key == K.DOWN:
                selected = min(len(hits[:10]) - 1, selected + 1)
            elif key == K.BACKSPACE:
                query = query[:-1]
                selected = 0
            elif len(key) == 1 and key.isprintable():
                query += key
                selected = 0

    # -- input -------------------------------------------------------------
    def _edit(self, title: str, hint: str, default: str = "",
              multiline: bool = False) -> str | None:
        """A line editor inside the frame.

        Reading in raw mode rather than calling input() keeps us inside the
        alternate screen: dropping out to cooked mode for every prompt makes
        the whole interface flicker between two screens.
        """
        t = self.log.theme
        self.source.enter_raw()
        lines: list = [default] if default else [""]
        row = 0
        while True:
            panel = ["", f"    {t.bold}{title}{t.reset}"]
            if hint:
                panel.append(f"    {t.dim}{hint}{t.reset}")
            panel.append("")
            for i, text in enumerate(lines):
                caret = f"{t.accent}\u2588{t.reset}" if i == row else ""
                mark = self.log.g.PHASE if i == row else " "
                panel.append(f"  {t.paint(mark, t.accent)} {text}{caret}")
            panel.append("")
            panel.append(f"    {t.dim}"
                         + ("enter newline   tab done   esc cancel"
                            if multiline else "enter/tab done   esc cancel")
                         + f"{t.reset}")
            self._draw(panel)

            key = self.source.read()
            if key in (K.ESC, K.CTRL_C):
                self._undraw()
                return None
            if key == K.CTRL_D or key == K.TAB:
                break
            if key == K.ENTER:
                if not multiline:
                    break
                lines.insert(row + 1, "")
                row += 1
                continue
            if key == K.BACKSPACE:
                if lines[row]:
                    lines[row] = lines[row][:-1]
                elif multiline and row > 0:
                    lines.pop(row)
                    row -= 1
                continue
            if key == K.UP and row > 0:
                row -= 1
                continue
            if key == K.DOWN and row < len(lines) - 1:
                row += 1
                continue
            if key == "@" and not lines[row].strip():
                picked = self.pick_repo()
                if picked is not None:
                    lines[row] = str(picked)
                continue
            if len(key) == 1 and key.isprintable():
                lines[row] += key

        self._undraw()
        value = "\n".join(lines).strip()
        return value or None

    def clarify(self, question: str, why: str = "") -> str | None:
        """Ask the operator one question mid-run.

        Only reached when a human is present. Unattended the harness declines
        to guess instead, which is the honest behaviour when there is nobody
        to ask.
        """
        t = self.log.theme
        lines = ["", f"  {t.paint(self.log.g.WARN, t.warn)} "
                     f"{t.bold}I need one thing to continue{t.reset}"]
        if why:
            lines.append(f"    {t.dim}{why}{t.reset}")
        lines += ["", f"    {question}", ""]
        self._draw(lines)
        answer = self._edit("your answer", "tab when done, esc to skip")
        self._undraw()
        return answer

    def ask_line(self, title: str, hint: str = "",
                 default: str = "") -> str | None:
        """One line of text, with normal line editing."""
        if self.full:
            return self._edit(title, hint, default)
        t = self.log.theme
        self._w()
        self._w(f"    {t.bold}{title}{t.reset}")
        if hint:
            self._w(f"    {t.dim}{hint}{t.reset}")
        self._w()
        self._flush()
        self.source.close()              # normal line editing while typing
        try:
            try:
                import readline           # noqa: F401  (enables editing)
            except ImportError:
                pass
            prompt = f"  {t.paint(self.log.g.PHASE, t.accent)} "
            value = input(prompt).strip() or default
        except (EOFError, KeyboardInterrupt):
            return None
        finally:
            self.source.enter_raw()
        return value or None

    def ask_paste(self) -> str | None:
        if self.full:
            return self._edit("Describe the issue",
                              "enter for a new line, tab when you are done",
                              multiline=True)
        t = self.log.theme
        self._w()
        self._w(f"    {t.bold}Paste the issue{t.reset}")
        self._w(f"    {t.dim}Ctrl-D to finish, Ctrl-C to cancel{t.reset}")
        self._w()
        self._flush()
        self.source.close()
        try:
            data = sys.stdin.read()
        except (EOFError, KeyboardInterrupt):
            return None
        finally:
            self.source.enter_raw()
        return data.strip() or None

    def card(self, title: str, rows: list, hint: str) -> bool:
        """A confirmation beat before something outward-facing."""
        t = self.log.theme
        lines = ["", f"  {t.paint(self.log.g.COMPUTED, t.computed)} "
                     f"{t.bold}{title}{t.reset}"]
        for key, value in rows:
            if key:
                lines.append(f"    {t.dim}{key.ljust(14)}{t.reset}{value}")
            else:
                lines.append(f"    {t.dim}{value}{t.reset}")
        lines += ["", f"    {t.dim}{hint}{t.reset}"]
        self._draw(lines)
        self.source.enter_raw()
        while True:
            key = self.source.read()
            if key in (K.TAB, K.ENTER, "y"):
                self._undraw()
                return True
            if key in (K.ESC, "q", "n", K.CTRL_C):
                self._undraw()
                return False

    # -- history -----------------------------------------------------------
    def _history_path(self) -> Path:
        return Path.cwd() / ".harness" / "history.json"

    def history(self) -> list:
        try:
            return json.loads(self._history_path().read_text("utf-8"))[
                :HISTORY_LIMIT]
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def remember(self, issue: str, repo: str, exit_code: int,
                 seconds: float) -> None:
        import time
        entry = {"issue": issue[:300], "repo": str(repo),
                 "exit": exit_code, "seconds": round(seconds, 1),
                 "when": time.strftime("%Y-%m-%d %H:%M")}
        items = [e for e in self.history()
                 if e.get("issue") != entry["issue"]]
        items.insert(0, entry)
        try:
            p = self._history_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(items[:HISTORY_LIMIT], indent=1),
                         encoding="utf-8")
        except OSError:
            pass

    # -- the flow ----------------------------------------------------------
    def chat(self) -> str | None:
        """Talk to it. Returns an issue to run, or None when they leave."""
        from .chat import Chat
        if not self.full:
            self._note("chat needs a terminal")
            return None
        self.screen.set_panel(None)          # the transcript, not a panel
        try:
            return Chat(console=self, cfg=self.cfg, log=self.log).run()
        finally:
            self.screen.set_status("")
            self.screen.render()

    def select_task(self, bootstrap=None) -> Choice:
        """Ask what to work on. Returns an empty Choice only to quit.

        Cancelling a prompt goes BACK to the menu; only `q` on the menu
        itself ends the session. A loop rather than recursion, so backing
        out repeatedly cannot pile up stack frames.
        """
        self._header(bootstrap)
        while True:
            past = self.history()
            items = [
                Item("paste an issue", "write it in place, tab when done"),
                Item("github issue or pull request", "owner/repo#123"),
                Item("a local repository", "@ to search, or pick below"),
                Item("recent", f"{len(past)} previous run(s)",
                     enabled=bool(past)),
                Item("chat", "ask about this repository, /run to fix"),
            ]
            picked = self.menu("What should I work on?", items)
            if picked is None:
                return Choice(quit=True)

            if picked == 0:
                text = self.ask_paste()
                if not text:
                    continue                       # cancelled: back to menu
                return Choice(issue=text)

            if picked == 1:
                ref = self.ask_line(
                    "GitHub issue or pull request",
                    "owner/repo#123, or a full issue or pull-request URL")
                if not ref:
                    continue
                return Choice(issue=ref, github_ref=ref)

            if picked == 2:
                repo = self.pick_repo() if self.full else None
                if repo is None and not self.full:
                    path = self.ask_line("Repository",
                                         "absolute or relative path",
                                         default=str(self.cfg.repo_path))
                    repo = Path(path).expanduser().resolve() if path else None
                    if repo is not None and not repo.is_dir():
                        self._note(f"no such directory: {repo}")
                        continue
                if repo is None:
                    continue
                text = self.ask_paste()
                if not text:
                    continue
                return Choice(issue=text, repo=repo)

            if picked == 4:
                issue = self.chat()
                if not issue:
                    continue                       # left the chat: back here
                return Choice(issue=issue)

            entries = [Item(e["issue"].splitlines()[0][:44],
                            f"{e['when']}  exit {e['exit']}") for e in past]
            which = self.menu("Recent runs", entries,
                              "↑↓ move   tab rerun   esc back")
            if which is None:
                continue
            chosen = past[which]
            return Choice(issue=chosen["issue"], repo=Path(chosen["repo"]))

    def consent_card(self, action: str, summary: str, rows: list) -> bool:
        """The Gate's question, as a card. Esc declines."""
        from . import consent
        t = self.log.theme
        lines = ["", f"  {t.paint(self.log.g.WARN, t.warn)} "
                     f"{t.bold}Permission needed{t.reset}",
                 f"    {t.dim}{consent.DESCRIBE.get(action, action)}{t.reset}",
                 ""]
        for key, value in rows:
            lines.append(f"    {t.dim}{str(key).ljust(14)}{t.reset}{value}")
        lines += ["", f"    {t.dim}tab allow   esc decline{t.reset}"]
        self._draw(lines)
        self.source.enter_raw()
        while True:
            key = self.source.read()
            if key in (K.TAB, K.ENTER, "y"):
                self._undraw()
                return True
            if key in (K.ESC, "q", "n", K.CTRL_C):
                self._undraw()
                return False

    def confirm_github(self, fetched, target: Path, cfg) -> bool:
        rows = [
            ("", f'"{fetched.title[:58]}"'),
            ("", f"{len(fetched.comments)} comment(s)"
                 + (f" · labels: {', '.join(fetched.labels[:3])}"
                    if fetched.labels else "")),
            ("clone", f"{fetched.clone_url} → {target}"),
            ("branch", (f"pr-{fetched.ref.number}" if fetched.ref.is_pr
                        else f"harness/issue-{fetched.ref.number}")),
            ("budget", f"{cfg.token_budget // 1000}k tokens · "
                       f"{cfg.time_budget // 60}m · "
                       f"{cfg.max_cycles} cycles"),
        ]
        return self.card(str(fetched.ref), rows, "tab start   esc back")

    def after_run(self, exit_code: int, cfg, report_path: Path,
                  github_ref=None, pr_hook=None) -> str:
        """Returns 'again' or 'quit'."""
        actions = ["diff", "report"]
        items = [
            Item("show the diff", "git diff in the target repository"),
            Item("open the full report", str(report_path),
                 enabled=report_path.is_file()),
        ]
        if pr_hook is not None:
            items.append(Item("commit, push and open a pull request",
                              "asks before anything leaves the machine",
                              enabled=exit_code in (0, 2)))
            actions.append("pr")
        # Only offer to comment when there is somewhere to comment on: an
        # item reading "post the report to None" is noise.
        if github_ref is not None:
            items.append(Item(f"post the report to {github_ref}",
                              "adds a comment",
                              enabled=exit_code in (0, 2)))
            actions.append("post")
        items += [Item("run another", ""), Item("quit", "")]
        actions += ["again", "quit"]

        at = 0
        while True:
            picked = self.menu("What next?", items,
                               "↑↓ move   tab choose   q quit", start=at)
            if picked is None:
                return "quit"
            at = picked
            action = actions[picked]
            if action == "quit":
                return "quit"
            if action == "again":
                return "again"
            if action == "diff":
                self._show_diff(cfg)
            elif action == "report":
                self._show_file(report_path)
            elif action == "post":
                self._post(github_ref, report_path, exit_code, cfg)
                items[picked].enabled = False
                at = 0
            elif action == "pr":
                result = pr_hook()
                self._note(result.render() if result else "nothing to push")
                items[picked].enabled = False
                at = 0

    # -- helpers -----------------------------------------------------------
    def _header(self, bootstrap) -> None:
        t = self.log.theme
        self.log.banner("2.1")
        if bootstrap is not None:
            self.log.kv("provider",
                        f"{bootstrap.provider.name} · {bootstrap.primary.id} "
                        f" {bootstrap.primary.tier}", t.bold)
        self.log.kv("repository", str(self.cfg.repo_path), t.dim)

    def _note(self, message: str) -> None:
        t = self.log.theme
        line = f"  {t.paint(self.log.g.WARN, t.warn)} {t.warn}{message}{t.reset}"
        if self.full:
            self.screen.append(line)
            self.screen.render()
            return
        self._w(line)
        self._flush()

    def _page(self, title: str, text: str) -> None:
        """Show long text inside the frame, scrollable.

        Writing it straight to the stream would land at whatever cursor
        position the frame happens to be at and be overwritten by the next
        redraw -- which looks like the menu moving on its own.
        """
        t = self.log.theme
        lines = (text or "(empty)").splitlines() or ["(empty)"]
        if not self.full:
            self._erase()
            for line in lines[:400]:
                self._w(line)
            self._flush()
            return

        rows = max(6, self.screen._rows - len(self.screen.header) - 6)
        top = 0
        while True:
            window = lines[top:top + rows]
            panel = ["", f"    {t.bold}{title}{t.reset}", ""]
            panel += [f"  {line}" for line in window]
            panel += ["", f"    {t.dim}↑↓ scroll   "
                          f"{top + 1}-{min(top + rows, len(lines))} "
                          f"of {len(lines)}   esc back{t.reset}"]
            self._draw(panel)
            key = self.source.read()
            if key in (K.ESC, "q", K.ENTER, K.TAB, K.CTRL_C):
                self._undraw()
                return
            if key == K.DOWN:
                top = min(max(0, len(lines) - rows), top + 1)
            elif key == K.UP:
                top = max(0, top - 1)

    def _show_diff(self, cfg) -> None:
        """Everything that changed, however it changed.

        A plain `git diff` shows only unstaged work, so anything staged --
        by a checkpoint, or on the way to a commit -- reads as "no changes"
        while the fix is sitting right there. Untracked files never appear
        in it at all, which hides a newly created file completely.
        """
        from .verify.runner import run

        def git(args):
            # Constant read-only git queries; no model text (deny list off).
            return run(f"git {args}", cfg.repo_path, timeout=30,
                       check_deny=False).stdout

        parts = []
        tracked = git("diff HEAD")          # staged and unstaged together
        if tracked.strip():
            parts.append(tracked)

        untracked = [ln.strip() for ln in
                     git("ls-files --others --exclude-standard").splitlines()
                     if ln.strip()]
        for path in untracked[:20]:
            body = git(f"diff --no-index -- /dev/null {path}")
            parts.append(body if body.strip() else f"new file: {path}")

        if not parts:
            # Changed, then committed: the work is in HEAD, not beside it.
            last = git("show --stat --oneline HEAD")
            if last.strip() and git("rev-list --count HEAD").strip() not in \
                    ("", "1"):
                parts.append("committed as:\n" + last)

        self._page("Diff", "\n".join(parts)[:40000] or "  (no changes)")

    def _show_file(self, path: Path) -> None:
        try:
            self._page(path.name, path.read_text("utf-8", errors="replace")
                       [:40000])
        except OSError as exc:
            self._note(f"could not read {path}: {exc}")

    def _post(self, ref, report_path: Path, exit_code: int, cfg) -> None:
        from . import publish
        try:
            body = report_path.read_text("utf-8", errors="replace")
        except OSError:
            self._note("no report to post")
            return

        class _S:
            cycles = "?"
        ok = publish.post_comment(ref, publish._preamble(exit_code, _S())
                                  + body, self.log)
        if not ok:
            self._note("could not post; the report is still on disk")


def interactive(cfg) -> bool:
    """Only when a human is plainly there, and never in an automated run."""
    if os.environ.get("HARNESS_NONINTERACTIVE", "").strip():
        return False
    if cfg.issue or cfg.replay:
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False

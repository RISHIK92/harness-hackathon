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

    def menu(self, title: str, items: list, hint: str = "") -> int | None:
        """Render a menu and return the chosen index, or None to go back."""
        t = self.log.theme
        self.source.enter_raw()      # ask_line() drops out of raw mode
        selected = 0
        enabled = [i for i, it in enumerate(items) if it.enabled]
        if not enabled:
            return None
        selected = enabled[0]

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
                self._undraw()
                return selected
            elif key.isdigit() and 1 <= int(key) <= len(items):
                idx = int(key) - 1
                if items[idx].enabled:
                    self._undraw()
                    return idx

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
            if len(key) == 1 and key.isprintable():
                lines[row] += key

        self._undraw()
        value = "\n".join(lines).strip()
        return value or None

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
        self._w(f"    {t.dim}finish with Ctrl-D on a blank line{t.reset}")
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
    def select_task(self, bootstrap=None) -> Choice:
        """Ask what to work on. Returns an empty Choice to quit."""
        self._header(bootstrap)
        past = self.history()
        items = [
            Item("paste an issue", "multi-line, Ctrl-D to finish"),
            Item("github issue or pull request", "owner/repo#123"),
            Item("a local repository", str(self.cfg.repo_path)),
            Item("recent", f"{len(past)} previous run(s)", enabled=bool(past)),
        ]
        picked = self.menu("What should I work on?", items)
        if picked is None:
            return Choice(quit=True)

        if picked == 0:
            text = self.ask_paste()
            return Choice(issue=text or "", quit=not text)

        if picked == 1:
            ref = self.ask_line(
                "GitHub issue or pull request",
                "owner/repo#123, or a full issue or pull-request URL")
            if not ref:
                return self.select_task(bootstrap)
            return Choice(issue=ref, github_ref=ref)

        if picked == 2:
            path = self.ask_line("Repository", "absolute or relative path",
                                 default=str(self.cfg.repo_path))
            if not path:
                return self.select_task(bootstrap)
            repo = Path(path).expanduser().resolve()
            if not repo.is_dir():
                self._note(f"no such directory: {repo}")
                return self.select_task(bootstrap)
            text = self.ask_paste()
            return Choice(issue=text or "", repo=repo, quit=not text)

        entries = [Item(e["issue"].splitlines()[0][:44],
                        f"{e['when']}  exit {e['exit']}") for e in past]
        which = self.menu("Recent runs", entries, "↑↓ move   tab rerun   esc back")
        if which is None:
            return self.select_task(bootstrap)
        chosen = past[which]
        return Choice(issue=chosen["issue"], repo=Path(chosen["repo"]))

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
                  github_ref=None) -> str:
        """Returns 'again' or 'quit'."""
        items = [
            Item("show the diff", "git diff in the target repository"),
            Item("open the full report", str(report_path),
                 enabled=report_path.is_file()),
            Item(f"post the report to {github_ref}", "adds a comment",
                 enabled=bool(github_ref) and exit_code in (0, 2)),
            Item("run another", ""),
            Item("quit", ""),
        ]
        while True:
            picked = self.menu("What next?", items,
                               "↑↓ move   tab choose   q quit")
            if picked is None or picked == 4:
                return "quit"
            if picked == 0:
                self._show_diff(cfg)
            elif picked == 1:
                self._show_file(report_path)
            elif picked == 2:
                self._post(github_ref, report_path, exit_code, cfg)
                items[2].enabled = False
            elif picked == 3:
                return "again"

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
        self._w(f"  {t.paint(self.log.g.WARN, t.warn)} {t.warn}{message}"
                f"{t.reset}")
        self._flush()

    def _show_diff(self, cfg) -> None:
        from .verify.runner import run
        result = run("git diff", cfg.repo_path, timeout=30, check_deny=False)
        self._w()
        self._w(result.stdout[:20000] or "  (no changes)")
        self._flush()

    def _show_file(self, path: Path) -> None:
        try:
            self._w()
            self._w(path.read_text("utf-8", errors="replace")[:20000])
        except OSError as exc:
            self._note(f"could not read {path}: {exc}")
        self._flush()

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

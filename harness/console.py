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
    _lines_drawn: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.stream = self.stream or sys.stdout
        if self.source is None:
            self.source = K.reader()

    # -- drawing -----------------------------------------------------------
    def _w(self, text: str = "") -> None:
        self.stream.write(text + "\n")
        self._lines_drawn += 1

    def _flush(self) -> None:
        self.stream.flush()

    def _erase(self) -> None:
        """Rub out what we drew, so a menu redraws in place."""
        if self._lines_drawn and getattr(self.log, "rich", False):
            self.stream.write(f"\x1b[{self._lines_drawn}A\x1b[0J")
        self._lines_drawn = 0

    def menu(self, title: str, items: list, hint: str = "") -> int | None:
        """Render a menu and return the chosen index, or None to go back."""
        t = self.log.theme
        selected = 0
        enabled = [i for i, it in enumerate(items) if it.enabled]
        if not enabled:
            return None
        selected = enabled[0]

        while True:
            self._erase()
            self._w()
            self._w(f"    {t.bold}{title}{t.reset}")
            self._w()
            for i, item in enumerate(items):
                mark = self.log.g.PHASE if i == selected else " "
                colour = t.accent if i == selected else ""
                body = item.label.ljust(30)
                if not item.enabled:
                    body = f"{t.dim}{body}{t.reset}"
                self._w(f"  {t.paint(mark, t.accent)} "
                        f"{t.paint(body, colour)}"
                        f"{t.dim}{item.hint}{t.reset}")
            self._w()
            self._w(f"    {t.dim}{hint or '↑↓ move   tab run   q quit'}"
                    f"{t.reset}")
            self._flush()

            key = self.source.read()
            if key in (K.CTRL_C, "q", K.ESC):
                self._erase()
                return None
            if key == K.UP:
                here = enabled.index(selected)
                selected = enabled[(here - 1) % len(enabled)]
            elif key == K.DOWN:
                here = enabled.index(selected)
                selected = enabled[(here + 1) % len(enabled)]
            elif key in (K.TAB, K.ENTER):
                self._erase()
                return selected
            elif key.isdigit() and 1 <= int(key) <= len(items):
                idx = int(key) - 1
                if items[idx].enabled:
                    self._erase()
                    return idx

    # -- input -------------------------------------------------------------
    def ask_line(self, title: str, hint: str = "",
                 default: str = "") -> str | None:
        """One line of text, with normal line editing."""
        t = self.log.theme
        self._w()
        self._w(f"    {t.bold}{title}{t.reset}")
        if hint:
            self._w(f"    {t.dim}{hint}{t.reset}")
        self._w()
        self._flush()
        close_raw = getattr(self.source, "close", None)
        if close_raw:
            self.source.close()          # normal line editing while typing
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
            enter_raw = getattr(self.source, "enter_raw", None)
            if enter_raw:
                enter_raw()
        return value or None

    def ask_paste(self) -> str | None:
        t = self.log.theme
        self._w()
        self._w(f"    {t.bold}Paste the issue{t.reset}")
        self._w(f"    {t.dim}finish with Ctrl-D on a blank line{t.reset}")
        self._w()
        self._flush()
        if getattr(self.source, "close", None):
            self.source.close()
        try:
            data = sys.stdin.read()
        except (EOFError, KeyboardInterrupt):
            return None
        finally:
            if getattr(self.source, "enter_raw", None):
                self.source.enter_raw()
        return data.strip() or None

    def card(self, title: str, rows: list, hint: str) -> bool:
        """A confirmation beat before something outward-facing."""
        t = self.log.theme
        self._erase()
        self._w()
        self._w(f"  {t.paint(self.log.g.COMPUTED, t.computed)} "
                f"{t.bold}{title}{t.reset}")
        for key, value in rows:
            if key:
                self._w(f"    {t.dim}{key.ljust(14)}{t.reset}{value}")
            else:
                self._w(f"    {t.dim}{value}{t.reset}")
        self._w()
        self._w(f"    {t.dim}{hint}{t.reset}")
        self._flush()
        while True:
            key = self.source.read()
            if key in (K.TAB, K.ENTER, "y"):
                self._erase()
                return True
            if key in (K.ESC, "q", "n", K.CTRL_C):
                self._erase()
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

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.parser.tree_sitter_parser import ParsedFile

from app.config import get_settings

settings = get_settings()

# Code is punctuation-dense, so whitespace-splitting undercounts real
# tokens by ~2x ("const x = foo(a, b);" is 5 whitespace words but ~12
# tokens). Measured on this repo's TypeScript: the word estimate said 737
# tokens for a span a char estimate puts at 1428. Under-counting makes
# chunks far larger than chunk_max_tokens intends, which is how a 144-line
# chunk happened. Chars-per-token is the standard approximation for code.
_CHARS_PER_TOKEN = 3.5


@dataclass
class Chunk:
    chunk_id: str          # "{repo_id}:{rel_path}:{index}"
    repo_id: str
    file_path: str         # relative path
    language: str
    start_line: int
    end_line: int
    text: str
    symbol_name: str = ""  # function/class name if chunk is a symbol
    metadata: dict = field(default_factory=dict)


def _token_estimate(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


# A chunk has to carry enough to be worth retrieving. Symbols carve their
# range out of a file and the parser usually ends a symbol on its last
# statement, so the closing brace falls into the leftover run BY ITSELF —
# producing real, embedded, retrievable chunks like:
#
#     quizController.ts:L217-L218      '  }\n};\n'
#     homeGroupController.ts:L415-L416 '  }\n};\n'
#
# Measured on the live corpus: 27 of 61,304 chunks (0.04%). Rare, but they
# sit INSIDE the controllers that get cited most, so they surface next to
# real hits and reach the evidence panel as "file x, 1 line, }".
#
# The old guard was `text.strip()`, which only rejects whitespace — "}" is
# not whitespace. Counting alphanumerics instead rejects any run that is
# pure punctuation while keeping genuinely small but real code: `def cli():
# pass` has 10, `MAX_RETRIES = 4` has 11, `}` and `});` have 0.
MIN_ALPHANUMERIC_CHARS = 6


def _has_content(text: str) -> bool:
    """Whether a chunk carries anything a search could meaningfully match."""
    return sum(c.isalnum() for c in text or "") >= MIN_ALPHANUMERIC_CHARS


def _contiguous_runs(numbers: list[int]) -> list[tuple[int, int]]:
    """Collapse a sorted list of line numbers into (start, end) runs of
    consecutive lines, so a chunk's line range always describes the text it
    actually holds."""
    runs: list[tuple[int, int]] = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return runs


def chunk_file(repo_id: str, rel_path: str, parsed: "ParsedFile", repo_root: str) -> list[Chunk]:
    """
    Split a parsed file into Chunks.
    - If the file has symbols (functions/classes), each symbol becomes its own chunk.
    - If a symbol is too large it is split by lines.
    - The remaining non-symbol lines are chunked by size.
    """
    max_tokens = settings.chunk_max_tokens
    chunks: list[Chunk] = []
    idx = 0

    lines = parsed.raw_text.splitlines(keepends=True)
    total_lines = len(lines)
    covered: set[int] = set()  # 1-based line numbers already included in a symbol chunk

    # ── Per-symbol chunks ────────────────────────────────────────────────────
    for sym in getattr(parsed, "symbols", []):
        sl, el = sym.start_line, sym.end_line
        if sl < 1 or el < sl:
            continue

        sym_lines = lines[sl - 1 : el]
        sym_text = "".join(sym_lines)

        if _token_estimate(sym_text) <= max_tokens:
            chunks.append(Chunk(
                chunk_id=f"{repo_id}:{rel_path}:{idx}",
                repo_id=repo_id,
                file_path=rel_path,
                language=parsed.language,
                start_line=sl,
                end_line=el,
                text=sym_text,
                symbol_name=sym.name,
                metadata={"kind": sym.kind, "docstring": sym.docstring or ""},
            ))
            idx += 1
            covered.update(range(sl, el + 1))
        else:
            # Split large symbol into line windows
            window: list[str] = []
            window_start = sl
            for lineno in range(sl, el + 1):
                window.append(lines[lineno - 1])
                if _token_estimate("".join(window)) >= max_tokens:
                    chunks.append(Chunk(
                        chunk_id=f"{repo_id}:{rel_path}:{idx}",
                        repo_id=repo_id,
                        file_path=rel_path,
                        language=parsed.language,
                        start_line=window_start,
                        end_line=lineno,
                        text="".join(window),
                        symbol_name=sym.name,
                    ))
                    idx += 1
                    covered.update(range(window_start, lineno + 1))
                    window = []
                    window_start = lineno + 1
            if window:
                chunks.append(Chunk(
                    chunk_id=f"{repo_id}:{rel_path}:{idx}",
                    repo_id=repo_id,
                    file_path=rel_path,
                    language=parsed.language,
                    start_line=window_start,
                    end_line=el,
                    text="".join(window),
                    symbol_name=sym.name,
                ))
                idx += 1
                covered.update(range(window_start, el + 1))

    # ── Non-symbol lines (file-level code, imports, comments) ────────────────
    # Uncovered lines are NOT contiguous — symbols carve holes out of the
    # middle of a file, leaving (say) the imports at the top and a bit of
    # wiring at the bottom. These must be chunked as SEPARATE RUNS: gathering
    # them into one chunk produced locators like "groupController.ts:L1-L1542"
    # holding 3439 characters, a range the snippet does not actually cover.
    # That is a fabricated locator, which the standing rule forbids.
    uncovered = [i for i in range(1, total_lines + 1) if i not in covered]
    for run_start, run_end in _contiguous_runs(uncovered):
        window_lines: list[str] = []
        window_start = run_start
        for lineno in range(run_start, run_end + 1):
            window_lines.append(lines[lineno - 1])
            if _token_estimate("".join(window_lines)) >= max_tokens:
                chunks.append(Chunk(
                    chunk_id=f"{repo_id}:{rel_path}:{idx}",
                    repo_id=repo_id,
                    file_path=rel_path,
                    language=parsed.language,
                    start_line=window_start,
                    end_line=lineno,
                    text="".join(window_lines),
                ))
                idx += 1
                window_lines = []
                window_start = lineno + 1
        if window_lines and _has_content("".join(window_lines)):
            chunks.append(Chunk(
                chunk_id=f"{repo_id}:{rel_path}:{idx}",
                repo_id=repo_id,
                file_path=rel_path,
                language=parsed.language,
                start_line=window_start,
                end_line=run_end,
                text="".join(window_lines),
            ))
            idx += 1

    return chunks

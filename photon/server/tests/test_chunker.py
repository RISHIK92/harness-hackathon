"""Chunker guards — both regressions here were found by ingesting a real
TypeScript repo (rishik92/adventa-backend) and measuring answer accuracy
against ground truth read from its source.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.embedding.chunker import _contiguous_runs, chunk_file


@dataclass
class _Sym:
    name: str
    kind: str
    start_line: int
    end_line: int
    docstring: str = ""


@dataclass
class _Parsed:
    raw_text: str
    language: str = "typescript"
    symbols: list = field(default_factory=list)


def test_contiguous_runs_splits_on_gaps():
    assert _contiguous_runs([1, 2, 3, 10, 11, 50]) == [(1, 3), (10, 11), (50, 50)]
    assert _contiguous_runs([]) == []


def test_every_chunk_line_range_matches_its_own_text():
    """The locator must describe the text it actually holds.

    A symbol in the middle of a file splits the non-symbol lines into two
    runs — the imports above it and the wiring below. Gathering both into
    one chunk produced locators like "groupController.ts:L1-L1542" carrying
    3439 characters: a range 1500 lines wide whose snippet covers a tiny
    fraction of it. Citing that to a user is a fabricated locator, which
    the standing rule forbids outright.
    """
    lines = [f"line {i}\n" for i in range(1, 201)]
    parsed = _Parsed(
        raw_text="".join(lines),
        symbols=[_Sym(name="middle", kind="function", start_line=50, end_line=150)],
    )
    chunks = chunk_file("repo1", "src/big.ts", parsed, "/tmp")
    assert chunks
    for c in chunks:
        claimed = c.end_line - c.start_line + 1
        actual = len(c.text.splitlines())
        assert actual == claimed, (
            f"{c.file_path}:L{c.start_line}-L{c.end_line} claims {claimed} lines "
            f"but holds {actual}"
        )
        # And the text must really be the lines it names.
        assert c.text == "".join(lines[c.start_line - 1 : c.end_line])


def test_no_chunk_spans_the_hole_a_symbol_left():
    lines = [f"line {i}\n" for i in range(1, 201)]
    parsed = _Parsed(
        raw_text="".join(lines),
        symbols=[_Sym(name="middle", kind="function", start_line=50, end_line=150)],
    )
    chunks = chunk_file("repo1", "src/big.ts", parsed, "/tmp")
    non_symbol = [c for c in chunks if not c.symbol_name]
    assert non_symbol, "expected chunks for the lines outside the symbol"
    # None may straddle the symbol: a chunk starting before 50 must end
    # before 50, never continue past 150.
    for c in non_symbol:
        assert not (c.start_line < 50 and c.end_line > 150)


def test_chunks_respect_the_token_budget():
    """The old whitespace-word estimate undercounted code tokens ~2x, which
    is how 144-line chunks got past a 512-token ceiling."""
    from app.config import get_settings

    budget = get_settings().chunk_max_tokens
    dense = "".join(
        f'const value{i} = compute({{ a: {i}, b: "x" }}).then((r) => r.y);\n'
        for i in range(1, 401)
    )
    chunks = chunk_file("repo1", "src/dense.ts", _Parsed(raw_text=dense), "/tmp")
    assert chunks
    for c in chunks:
        # Allow one line of overshoot: a window is closed after the line
        # that crosses the budget.
        assert len(c.text) / 3.5 <= budget * 1.3, (
            f"chunk {c.start_line}-{c.end_line} is {len(c.text)} chars, "
            f"well past a {budget}-token budget"
        )


# ── junk chunks ──────────────────────────────────────────────────────────
# A symbol's range usually ends on its last statement, so the closing brace
# falls into the leftover run BY ITSELF and became a real, embedded,
# retrievable chunk. Measured on the live corpus before this guard: 27 of
# 61,304, including quizController.ts:L217-L218 -> '  }\n};\n'. They sit
# inside the controllers cited most often, so they surfaced next to real
# hits and reached the evidence panel as "file x, 1 line, }".

from app.core.embedding.chunker import _has_content  # noqa: E402


def test_a_lone_closing_brace_is_not_a_chunk():
    for junk in ["  }\n};\n", "}\n", "});", "\n\n", "    ],\n)\n", "<a></a>\n"]:
        assert not _has_content(junk), junk


def test_small_but_real_code_survives():
    """The old guard was text.strip(), which only rejects whitespace — "}" is
    not whitespace. The new one must not overcorrect into dropping genuinely
    tiny code."""
    for real in ["def cli():\n    pass\n", "MAX_RETRIES = 4", "export default app;",
                 "const PORT = 3000;"]:
        assert _has_content(real), real


def test_the_retrieval_filter_matches_the_chunker():
    """A chunk the chunker would refuse to create today must not be returned
    from a corpus ingested before it refused — 61k chunks are already
    embedded and re-ingesting every repo to evict 27 is not worth it."""
    from app.tools.code import _has_content as read_time

    for text in ["  }\n};\n", "});", "def cli():\n    pass\n", "MAX_RETRIES = 4"]:
        assert read_time(text) == _has_content(text), text


# ── fabricated locators already in the corpus ────────────────────────────


def test_a_locator_claiming_far_more_than_it_holds_is_rejected():
    """Found live: drillController.ts:L1-L1315 holding 72 lines — a survivor
    of the chunker that predates _contiguous_runs, still in Qdrant because a
    re-ingest overwrites chunk ids 0..n and orphans the rest. The code panel
    reads the file back BY THAT RANGE, so it showed 1315 lines as "what the
    answer cited"."""
    from app.tools.code import _overclaims_its_range

    assert _overclaims_its_range({"start_line": 1, "end_line": 1315, "text": "x\n" * 72})


def test_a_normal_chunk_is_not_rejected():
    """Tolerant on purpose — a chunk whose text is a line or two short of its
    range is normal; only a gross mismatch is a fabricated locator."""
    from app.tools.code import _overclaims_its_range

    for chunk in (
        {"start_line": 393, "end_line": 506, "text": "x\n" * 114},
        {"start_line": 1, "end_line": 28, "text": "x\n" * 28},
        {"start_line": 5, "end_line": 30, "text": "x\n" * 5},  # short, but within tolerance
        {"start_line": None, "end_line": None, "text": "x"},
    ):
        assert not _overclaims_its_range(chunk), chunk

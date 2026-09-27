"""Memory across calls: what this client asked on a previous call.

The transcript rows a call already writes (models.TranscriptEntry, one row
per line, written by the call-agent orchestrator) are the memory. No second
store, no summarisation pass, no embedding pipeline — a transcript IS the
record of what was asked and what was answered, and anything derived from it
would be a paraphrase this tool would then have to cite as if it were the
words spoken.

That matters for the standing rule. Every item this returns quotes real rows
and its locator names the meeting and the minute (`call:abcd-efgh:2026-09-11T14:02:00Z`),
so a citation can be checked against `GET /api/meetings/{slug}/transcript.md`.
A summarised memory could not be.

Retrieval is lexical, not semantic, and that is a decision rather than a
shortcut. Embedding the transcript would mean an ingest pipeline that must
run before the memory works, so the first question after a call would find
nothing; and the questions this serves ("did we discuss the webhook retries
last time?") repeat the vocabulary of the call they are recalling, which is
exactly the case term overlap handles well and the case where a dense
embedding's advantage is smallest.
"""
from __future__ import annotations

import math
import re
from datetime import datetime

import structlog
from sqlmodel import select

from app.tools.evidence import make_evidence, tool_error, tool_result

log = structlog.get_logger()

TOOL_NAME = "search_past_calls"

# How many recent transcript lines are pulled before scoring. A call is a few
# dozen lines, so this is a few hundred calls' worth — far past the horizon
# anyone means by "last time", and bounded so a long-lived workspace cannot
# turn one tool call into a full table scan.
SCAN_LIMIT = 1500

_WORD_RE = re.compile(r"[a-z0-9_]+|[^\x00-\x7f]+")

# Words that appear in most questions and so carry no signal about WHICH
# past question is the match. Kept deliberately short: an aggressive stop
# list starts removing the domain words ("state", "set") that are the whole
# point of the match.
_STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how
    i if in into is it its me my no not of on or our so than that the their them then there
    these they this to too us was we were what when where which who why will with would you
    your""".split()
)


def _stem(word: str) -> str:
    """Crude suffix stripping, and it is not optional.

    Measured on the first real transcript this tool was pointed at: the query
    "webhook retries" scored ZERO against the line "why do the webhooks only
    retry three times" — the exact question it was recalling — because
    webhook/webhooks and retries/retry are different strings. Every term in
    that query was present and none of them matched.

    Plural and verb-form drift between how a thing is asked about and how it
    was asked about last time is the normal case, not an edge case, so
    lexical retrieval here is unusable without this. It is applied to both
    sides, so precision matters far less than consistency: "status" -> "statu"
    is fine, because the query's "status" becomes "statu" too.
    """
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _tokenise(text: str) -> set[str]:
    return {
        _stem(w)
        for w in _WORD_RE.findall((text or "").lower())
        if w not in _STOPWORDS and len(w) > 1
    }


def _score(query_tokens: set[str], text: str) -> float:
    """Overlap normalised by the length of the candidate.

    Dividing by sqrt(len) rather than by len: without any normalisation a
    long rambling turn wins every query by sheer surface area, and with full
    length normalisation a two-word line beats a precise sentence. The score
    is bounded to 1.0 so it reads on the same 0-1 scale as every other tool's
    score in the evidence panel.
    """
    candidate = _tokenise(text)
    if not query_tokens or not candidate:
        return 0.0
    overlap = len(query_tokens & candidate)
    if not overlap:
        return 0.0
    return min(1.0, overlap / math.sqrt(len(query_tokens) * len(candidate)))


def _pair_turns(rows: list[dict]) -> list[dict]:
    """Pair each human line with the agent reply that followed it.

    A question on its own is not useful memory — "did they ask about this
    before" is only half the answer, and the half nobody needs. Rows must
    arrive in chronological order within a meeting.

    A human line with no agent line after it is still kept, with `answer`
    empty: an unanswered question is real, often the most interesting thing
    in a transcript (it is the question the agent abstained on), and dropping
    it would quietly hide exactly that.
    """
    paired: list[dict] = []
    for i, row in enumerate(rows):
        if row["role"] != "human":
            continue
        # Only the IMMEDIATELY following line. Scanning further forward for
        # "the next agent line" would attach an answer across an intervening
        # question, i.e. report a reply that was given to something else.
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        answer = nxt["text"] if nxt and nxt["role"] == "agent" else ""
        paired.append({**row, "answer": answer})
    return paired


def _format_snippet(pair: dict, agent_name: str = "The agent") -> str:
    when = pair["created_at"]
    when_text = when.strftime("%Y-%m-%d") if isinstance(when, datetime) else str(when)
    head = f"On the call {pair['slug']} ({when_text}), {pair['speaker_name']} asked: {pair['text']}"
    if pair["answer"]:
        return f"{head}\n{agent_name} answered: {pair['answer']}"
    return f"{head}\n(no answer was recorded for this question)"


def _locator(pair: dict) -> str:
    when = pair["created_at"]
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(when, datetime) else str(when)
    return f"call:{pair['slug']}:{stamp}"


def _to_evidence(pair: dict, score: float, agent_name: str = "The agent") -> dict:
    return make_evidence("call", _locator(pair), _format_snippet(pair, agent_name), score)


def rank(query: str, pairs: list[dict], top_k: int) -> list[tuple[dict, float]]:
    """Scored on the QUESTION, not on the question plus the answer.

    Scoring the pair as one blob lets a long answer drag in a question that
    has nothing to do with the query — and it is the earlier question that
    the caller is trying to recall.
    """
    query_tokens = _tokenise(query)
    scored = [(p, _score(query_tokens, p["text"])) for p in pairs]
    return sorted([s for s in scored if s[1] > 0], key=lambda s: s[1], reverse=True)[:top_k]


async def _load_rows(workspace_id: str, exclude_meeting_slug: str | None) -> list[dict]:
    from app.database import AsyncSessionLocal
    from app.models import Meeting, TranscriptEntry

    async with AsyncSessionLocal() as session:
        stmt = (
            select(TranscriptEntry, Meeting)
            .join(Meeting, Meeting.id == TranscriptEntry.meeting_id)
            .where(Meeting.workspace_id == workspace_id)
            .order_by(TranscriptEntry.created_at.desc())
            .limit(SCAN_LIMIT)
        )
        if exclude_meeting_slug:
            # The call happening right now is conversation memory's job
            # (app.agent.history), not this tool's. Returning this call's own
            # lines here would cite the caller's own sentence back at them as
            # if it were established knowledge.
            stmt = stmt.where(Meeting.slug != exclude_meeting_slug)
        result = await session.execute(stmt)
        rows = result.all()

    ordered = [
        {
            "role": entry.role.value if hasattr(entry.role, "value") else str(entry.role),
            "text": entry.text,
            "speaker_name": entry.speaker_name,
            "created_at": entry.created_at,
            "slug": meeting.slug,
        }
        for entry, meeting in rows
    ]
    # Fetched newest-first so the LIMIT keeps recent calls; _pair_turns needs
    # chronological order to pair a question with the reply that followed it.
    ordered.reverse()
    return ordered


async def search_past_calls(
    query: str,
    top_k: int = 6,
    workspace_id: str | None = None,
    exclude_meeting_slug: str | None = None,
) -> dict:
    """What was asked and answered on this workspace's earlier calls."""
    if not workspace_id:
        return tool_result(TOOL_NAME, [], note="no workspace, so there are no past calls to search")
    try:
        rows = await _load_rows(workspace_id, exclude_meeting_slug)
    except Exception as exc:  # noqa: BLE001
        log.error("tool.search_past_calls_error", error=str(exc))
        return tool_error(TOOL_NAME, f"{TOOL_NAME} failed: {exc}")

    if not rows:
        return tool_result(TOOL_NAME, [], note="no earlier calls have been transcribed for this workspace")

    # Meetings are interleaved by the fetch order, so group before pairing —
    # otherwise the last line of one call pairs with the first line of
    # another and the memory reports an answer that was never given to that
    # question.
    by_meeting: dict[str, list[dict]] = {}
    for row in rows:
        by_meeting.setdefault(row["slug"], []).append(row)
    pairs: list[dict] = []
    for meeting_rows in by_meeting.values():
        pairs.extend(_pair_turns(meeting_rows))

    hits = rank(query, pairs, top_k)
    evidence = [_to_evidence(pair, score) for pair, score in hits]
    return tool_result(
        TOOL_NAME,
        evidence,
        note=None if evidence else f"nothing from an earlier call matched '{query}'",
    )

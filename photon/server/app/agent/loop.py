"""The agent loop: plan -> call tools -> compose -> verify -> abstain.
Plain function calling, no framework. Zero transport imports — callable
standalone with just a question string (see tests/test_agent_loop.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time

import structlog

from app.agent.events import EventSink, TurnTracer
from app.agent.history import format_history_block, normalise_history, resolve_query
from app.agent.llm import ANSWER_SCHEMA, PLAN_SCHEMA, extract_json, generate
from app.agent.personas import personas_prompt
from app.agent.prompts import build_compose_prompt, build_plan_prompt
from app.core.llm.vision import describe_screen
from app.agent.verifier import verify
from app.seed.loader import get_seed_repo_id
from app.services.workspace_repos import list_ready_repos
from app.tools.evidence import make_evidence
from app.tools.registry import TOOL_SCHEMAS, UnknownToolError, dispatch

log = structlog.get_logger()

MAX_ROUNDS = 2
COMPOSE_EVIDENCE_LIMIT = 6
MAX_TOOL_CALLS_TOTAL = 6
MAX_CALLS_PER_ROUND = 4

_REPO_ID_TOOLS = {t["name"] for t in TOOL_SCHEMAS if "repo_id" in t["parameters"]}

# Required arguments that are just natural language, and so can be filled
# from the question itself when the planner leaves them out.
#
# Measured: on "why does X behave this way" questions the planner emitted
# explain_why with args {} in 6 of 12 runs and search_code with args {} in
# 4 of 12, so the provenance chain (code -> commit -> PR -> Slack) silently
# never ran and the answer was composed from whatever else survived. A
# tightened plan prompt moved those numbers but cost more than it bought —
# warning the planner that a missing argument makes a tool fail made it
# risk-averse, and the docs-vs-code question went from planning
# check_conflict 3/3 times to planning nothing at all 3/3 times.
#
# So this is handled here instead, where it is deterministic rather than
# subject to model variance — the same posture already taken with repo_id.
# It is safe precisely because these arguments are free text: search_code's
# query is a semantic search string, check_conflict's claim is the assertion
# to check, and explain_why's symbol_or_path is self-located via search_code
# when it isn't a path (see provenance._locate_file). The question is a
# truthful value for each.
#
# Deliberately NOT extended to account_id, symbol or path. Those name a
# specific record, symbol or file; a question is not one, and filling one in
# would turn a clean "bad arguments" error into a confident lookup of the
# wrong thing.
_FREE_TEXT_ARGS = {"query", "claim", "symbol_or_path"}
_QUESTION_BACKFILL_ARGS = {
    t["name"]: tuple(
        k for k, v in t["parameters"].items() if v.get("required") and k in _FREE_TEXT_ARGS
    )
    for t in TOOL_SCHEMAS
    if any(v.get("required") and k in _FREE_TEXT_ARGS for k, v in t["parameters"].items())
}

# Tools that read tenant-owned data. workspace_id is FORCED by the loop and
# deliberately absent from the schema the planner sees: it is not a choice
# the model should be able to make, and a hallucinated workspace id would be
# a cross-tenant read rather than a merely wrong answer.
# Two different things, easy to confuse (they were, once — a merge left the
# connector injection unreachable and search_custom_docs silently returned
# "no connection" for a workspace that had documents indexed):
#
#   _WORKSPACE_ID_TOOLS       connector-backed tools that read TENANT data
#                             and are meaningless without a workspace.
#   _WORKSPACE_SEARCHABLE_TOOLS  repo tools that can search across all of a
#                             workspace's repos when no single repo was
#                             resolved (defined below, used inside the
#                             repo_id branch).
_WORKSPACE_ID_TOOLS = {
    "search_slack",
    "search_jira",
    "search_linear",
    "search_notion",
    "search_datadog",
    "search_custom_docs",
    "search_past_calls",
}

# The one tool that must know which call is happening NOW, and only so it
# can leave it out. Forced by the loop for the same reason workspace_id is:
# a planner free to choose it could quote this call's own transcript back
# as if it were something established on an earlier one.
_MEETING_SLUG_TOOLS = {"search_past_calls"}
# The only tools that can fall back to searching every repo in a workspace
# at once (plain vector search, filterable by workspace_id) rather than
# needing one specific repo (a Neo4j graph walk, a per-repo provenance
# chain, a file read).
_WORKSPACE_SEARCHABLE_TOOLS = {"search_code", "find_usages"}


async def _run_one_call(
    call: dict,
    repo_id: str | None,
    tracer: TurnTracer,
    call_id: str,
    round_num: int,
    workspace_id: str | None = None,
    known_repo_ids: set[str] | None = None,
    allowed_tools: set[str] | None = None,
    question: str = "",
    meeting_slug: str | None = None,
) -> dict:
    tool_name = call.get("tool")
    args = dict(call.get("args") or {})

    for arg_name in _QUESTION_BACKFILL_ARGS.get(tool_name, ()):
        if not args.get(arg_name):
            args[arg_name] = question
    if tool_name in _REPO_ID_TOOLS:
        if repo_id:
            # Single-repo mode: always force the loop's own resolved
            # repo_id, never trust the planner's guess — it doesn't
            # reliably know the real UUID and a wrong guess (e.g.
            # "meridian") silently empties out code evidence instead of
            # erroring, which is much worse (caught while testing).
            args["repo_id"] = repo_id
        else:
            # Multi-repo mode: no single repo was resolved up front, so
            # trust the planner's repo_id ONLY if it's one of this
            # workspace's actual repo ids (from the known_repos list in
            # the plan prompt) — same "never trust a guess" principle,
            # just extended to a set of valid ids instead of a single one.
            guessed = args.get("repo_id")
            if known_repo_ids and guessed in known_repo_ids:
                args["repo_id"] = guessed
            else:
                args.pop("repo_id", None)
            if workspace_id and tool_name in _WORKSPACE_SEARCHABLE_TOOLS:
                args["workspace_id"] = workspace_id

    if allowed_tools is not None and tool_name not in allowed_tools:
        # The planner should not have seen this tool at all; refusing here
        # too means a prompt-injection or a stale plan cannot reach a source
        # the call was configured to exclude.
        tracer.emit("tool.blocked", id=call_id, tool=tool_name, round=round_num)
        return {
            "tool": tool_name,
            "args": args,
            "result": {"tool": tool_name, "status": "error", "evidence": [],
                       "note": f"{tool_name} is not enabled for this call"},
            "ms": 0,
        }

    if tool_name in _MEETING_SLUG_TOOLS:
        args["exclude_meeting_slug"] = meeting_slug

    if tool_name in _WORKSPACE_ID_TOOLS:
        # Always overwrite, never default: a planner-supplied value here
        # would be a tenant boundary decided by an LLM. Deliberately outside
        # the repo_id branch above — connector tools have nothing to do with
        # repos, and nesting this under it is exactly how it went missing.
        args["workspace_id"] = workspace_id

    # Emitted BEFORE the await, so a client sees "search_code running…"
    # for the whole time it actually runs rather than only learning about
    # it once it's already finished.
    tracer.emit("tool.start", id=call_id, tool=tool_name, args=args, round=round_num)

    start = time.monotonic()
    try:
        result = await dispatch(tool_name, args)
    except UnknownToolError:
        result = {"tool": tool_name, "status": "error", "evidence": [], "note": f"unknown tool '{tool_name}'"}
    except TypeError as exc:
        result = {"tool": tool_name, "status": "error", "evidence": [], "note": f"bad arguments: {exc}"}
    except Exception as exc:  # noqa: BLE001 - a tool must never take the whole turn down
        log.error("agent.tool_call_error", tool=tool_name, error=str(exc))
        result = {"tool": tool_name, "status": "error", "evidence": [], "note": str(exc)}
    ms = int((time.monotonic() - start) * 1000)
    tracer.emit(
        "tool.done",
        id=call_id,
        tool=tool_name,
        ms=ms,
        status=result.get("status"),
        evidence_count=len(result.get("evidence", [])),
        note=result.get("note"),
    )

    return {"tool": tool_name, "args": args, "result": result, "ms": ms}


def _select_compose_evidence(tool_trace: list[dict], limit: int) -> list[dict]:
    """The evidence that actually goes into the compose prompt, capped.

    Round-robin across tools rather than taking the first N of a flat list:
    evidence is appended tool by tool, so a plain slice would hand the
    whole budget to whichever tool happened to run first and could drop
    the second tool's best item entirely — exactly the Slack message the
    S2 answer depends on. Each tool contributes its top result first, then
    its second, and so on.
    """
    per_tool = [t.get("evidence") or [] for t in tool_trace if t.get("evidence")]
    picked: list[dict] = []
    seen: set[str] = set()
    for rank in range(max((len(e) for e in per_tool), default=0)):
        for ev_list in per_tool:
            if rank >= len(ev_list):
                continue
            item = ev_list[rank]
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            picked.append(item)
            if len(picked) >= limit:
                return picked
    return picked


def _dedupe_evidence(evidence: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for e in evidence:
        seen.setdefault(e["id"], e)
    return list(seen.values())


# The abstention is built without an LLM (that's the point — it must be
# reliable when everything else failed), so a non-English caller can only
# be answered in their language if the sentence is pre-written. Keyed to
# the same BCP-47 codes the voice stack uses.
_ABSTENTION = {
    "en-IN": "I don't have evidence for that — nothing I searched turned up anything relevant.",
    "hi-IN": "मेरे पास इसका कोई प्रमाण नहीं है — मुझे कुछ भी प्रासंगिक नहीं मिला।",
    "te-IN": "దీనికి నా దగ్గర ఆధారం లేదు — సంబంధించిన సమాచారం ఏమీ దొరకలేదు.",
    "ta-IN": "இதற்கு என்னிடம் ஆதாரம் இல்லை — தொடர்புடைய தகவல் எதுவும் கிடைக்கவில்லை.",
}


def _no_evidence_abstention(tool_trace: list[dict], language: str | None = None) -> str:
    """Deliberately does NOT name the tools it tried.

    It used to ("I checked search_code, search_docs and found nothing"),
    which broke the voice rule "lead with the finding, not the method" —
    heard live, the agent read internal tool names aloud mid-sentence, in
    Telugu. The tools are already shown as pills in the evidence panel,
    where they belong. It also used to say "I checked any tools" when no
    tool ran at all, which was simply broken English.
    """
    return _ABSTENTION.get(language or "en-IN", _ABSTENTION["en-IN"])


async def _plan_round(
    question: str,
    context_log: list[str],
    screen_context: str | None,
    round_num: int,
    remaining: int,
    tracer,
    *,
    language: str | None,
    known_repos,
    allowed_tools,
    org_name,
    agent_name,
    history_block: str = "",
) -> list[dict]:
    """One planning round: ask the LLM which tools to call."""
    plan_prompt = build_plan_prompt(
        question,
        context_log,
        screen_context,
        is_first_round=(round_num == 0),
        language=language,
        known_repos=known_repos,
        # Both of these have been lost twice to concurrent edits of this
        # call. allowed_tools is what stops the planner even SEEING a
        # source the call excluded; without it only the execution-time
        # block catches it, which works but wastes a round-trip and
        # muddies the trace. org_name is who the agent says it is.
        allowed_tools=allowed_tools,
        org_name=org_name,
        agent_name=agent_name,
        history_block=history_block,
    )
    tracer.emit("plan.start", round=round_num + 1)
    plan_started = time.monotonic()
    raw_plan = await generate(plan_prompt, max_output_tokens=400, temperature=0.0, json_schema=PLAN_SCHEMA)
    plan = extract_json(raw_plan) or {}
    calls = (plan.get("calls") or [])[: min(MAX_CALLS_PER_ROUND, remaining)]

    if not calls and round_num == 0:
        # Should be rare now that the first-round prompt is directive about
        # calling something (see build_plan_prompt's nudge) — a safety net
        # for what still slips through. Retry once at a nonzero temperature
        # purely to escape a repeated deterministic empty output, not
        # because temperature fixes emptiness on its own (measured: it
        # doesn't — see build_plan_prompt's comment).
        log.warning("agent.empty_first_round_plan_retrying", question=question)
        tracer.emit("plan.retry", round=round_num + 1, reason="empty plan on the first round")
        raw_plan = await generate(plan_prompt, max_output_tokens=400, temperature=0.4, json_schema=PLAN_SCHEMA)
        plan = extract_json(raw_plan) or {}
        calls = (plan.get("calls") or [])[: min(MAX_CALLS_PER_ROUND, remaining)]

    tracer.emit(
        "plan.done",
        round=round_num + 1,
        ms=int((time.monotonic() - plan_started) * 1000),
        calls=[{"tool": c.get("tool"), "args": c.get("args") or {}} for c in calls],
    )
    return calls


# Everything a code-only workspace can offer. The fast path engages only
# when `allowed_tools` fits inside this set: the moment a call also exposes
# accounts, tickets, Slack or a connector, choosing between sources is a
# real decision and the planner earns its round-trip.
_CODE_TOOLS = {
    "search_code",
    "search_custom_docs",
    "search_docs",
    "explain_why",
    "trace_symbol",
    "find_usages",
    "read_file",
    # Memory of earlier calls is default-on for any workspace that has had
    # one (services/tool_availability.py), so leaving it out of this set
    # would have silently switched the fast path OFF for every code-only
    # workspace the moment it finished its first call — a ~1.8s regression
    # arriving days after the feature, with nothing to connect it to.
    # It is safe here because choosing it is not a judgement call: the rule
    # is written out in _RECALL_RE below, exactly as the plan prompt states
    # it in prose.
    "search_past_calls",
}

# "did we talk about this before" — the only shape where an earlier call is
# the source being asked for. Everything else on a code-only call is a
# question about the code, and searching the transcript archive too would
# just put stale paraphrase next to the source of truth.
_RECALL_RE = re.compile(
    r"\b(last time|previously|earlier call|last call|previous call|before this|"
    r"we discussed|we talked about|did (?:we|you|they) (?:discuss|talk|say|tell|mention)|"
    r"you (?:told|said|mentioned)|as (?:we|you) (?:discussed|said)|last week's call)\b",
    re.I,
)

# "why is it like this" — the one question shape where search_code alone is
# known to be wrong. explain_why must run WITH it, never instead of it: on
# its own it self-locates from the query text and confidently explained the
# wrong file (the documented Bangalore regression).
_WHY_RE = re.compile(
    r"\b(why|reason|rationale|how come|what for|purpose of)\b|కారణం|ஏன்|क्यों", re.I
)


def _fast_path_calls(
    question: str, allowed_tools: set[str] | None, retrieval_query: str | None = None
) -> list[dict] | None:
    """Tool calls to run INSTEAD of asking the planner, or None to plan.

    The plan call is a full OpenRouter round-trip. Measured: ~1.4-2.3s of a
    ~4.7s turn, while a trivial 20-token call to the same endpoint costs
    ~1.7s — so it is almost entirely provider latency, not thinking. For a
    code-only workspace the planner's decision is already written down as
    fixed rules in build_plan_prompt ("why does this exist" -> search_code
    AND explain_why; otherwise search). Applying those rules locally is the
    same decision for 0ms instead of ~1700ms.

    This is a LATENCY-FOR-ACCURACY trade and it is not free: measured on a
    real repo it cost ~6pp of answer accuracy. Off with AGENT_FAST_PATH=false.
    """
    from app.config import get_settings

    if not get_settings().agent_fast_path or not allowed_tools:
        return None
    allowed = set(allowed_tools)
    if not allowed.issubset(_CODE_TOOLS) or "search_code" not in allowed:
        return None

    # The RESOLVED query, not the raw utterance: on the fast path there is no
    # planner to notice that "why is that?" names nothing, so a follow-up
    # would otherwise be embedded as three pronouns and match nothing.
    query = retrieval_query or question
    calls = [{"tool": "search_code", "args": {"query": query}}]
    if _RECALL_RE.search(question) and "search_past_calls" in allowed:
        # Asked about an earlier call explicitly — search that too, and keep
        # search_code alongside it so an answer grounded only in a paraphrase
        # of an old answer can still be checked against the code.
        calls.append({"tool": "search_past_calls", "args": {"query": query}})
        return calls
    if _WHY_RE.search(question) and "explain_why" in allowed:
        # Mirrors the plan prompt's rule exactly. explain_why takes a symbol
        # or path, which we do not have before searching — the question text
        # is what the planner passes too, and the tool self-locates from it.
        calls.append({"tool": "explain_why", "args": {"symbol_or_path": query}})
    elif "search_custom_docs" in allowed:
        # Uploaded docs are cheap and often hold the prose answer that code
        # alone cannot give. Skipped for "why" turns, where explain_why is
        # the better second call and three tools bloats the compose prompt.
        calls.append({"tool": "search_custom_docs", "args": {"query": query}})
    return calls


async def answer_question(
    question: str,
    repo_id: str | None = None,
    screen_context: str | None = None,
    screen_image_bytes: bytes | None = None,
    on_event: EventSink | None = None,
    language: str | None = None,
    workspace_id: str | None = None,
    allowed_tools: set[str] | None = None,
    bot_types: list[str] | None = None,
    org_name: str | None = None,
    agent_name: str | None = None,
    history: list | None = None,
    meeting_slug: str | None = None,
) -> dict:
    """The Section 4 answer contract: {answer, claims, confidence, abstained,
    escalation, tool_trace}. Safe to call with no call/session in progress.

    screen_image_bytes: a JPEG screen-share frame, if the customer is
    sharing their screen and the question plausibly needs it (caller —
    call-agent's orchestrator — decides that, not this function). Analyzed
    once via Gemini vision (app.core.llm.gemini_vision) and folded into
    the evidence set as a citable "screen" source_type item, exactly like
    a tool result — NOT passed to the LLM as raw unverifiable prose, so
    the same "no uncited claim" rule applies to what the customer sees on
    screen as to everything else.

    on_event: optional sink for real-time stage/tool/latency events (see
    app.agent.events). Purely observational — the answer is byte-for-byte
    identical with or without it, and the loop never learns who's
    listening, so the transport-free rule still holds.

    history: the last few turns of THIS conversation, oldest first, as
    [{"role": "user"|"agent", "text": ...}] (app.agent.history). Without it
    a follow-up has no referent: "why is that?" reaches the planner as three
    words with no subject, and on the fast path those same three words are
    handed to search_code as a semantic query. Caller-supplied rather than
    read from the transcript table, because the two live consumers keep it
    already — the worker in TurnState.transcript, the browser in its turn
    list — and the text console writes no transcript rows at all.

    meeting_slug: which call this is, used ONLY to exclude it from
    search_past_calls. That tool's job is what was said on EARLIER calls;
    quoting the current one back would cite the caller's own sentence as
    established knowledge.

    workspace_id: only consulted when repo_id is omitted. With no repo_id
    and no workspace_id, falls back to the single seed repo (the original,
    pre-multi-tenant behavior). With a workspace_id and exactly one READY
    repo, that repo is resolved the same forced way a passed-in repo_id
    would be — no ambiguity, nothing changes. With 2+ READY repos, the
    loop switches to multi-repo mode: the plan prompt lists them
    (app.agent.prompts' known_repos block) so the planner can name one
    explicitly, and workspace-searchable tools fall back to searching
    every repo in the workspace when it doesn't.
    """
    turns = normalise_history(history)
    history_block = format_history_block(turns)
    # Two different strings from here on, and conflating them is the bug this
    # split exists to avoid: `question` is what the caller actually said and
    # is what the model reads (it resolves the reference itself, with the
    # history block in front of it), while `retrieval_query` is what the
    # search TOOLS get and must stand on its own with no conversation around
    # it. Identical whenever this is not a follow-up.
    retrieval_query, was_resolved = resolve_query(question, turns)

    known_repos: list[dict] = []
    if repo_id is None:
        if workspace_id:
            known_repos = await list_ready_repos(workspace_id)
            if len(known_repos) <= 1:
                repo_id = known_repos[0]["id"] if known_repos else None
                known_repos = []
        else:
            repo_id = get_seed_repo_id()
    known_repo_ids = {r["id"] for r in known_repos}

    tracer = TurnTracer(on_event)
    tracer.emit("turn.start", question=question)
    if turns:
        # Visible in the advanced panel. A follow-up that was silently
        # searched as-is looks exactly like a retrieval failure from the
        # outside, so the resolution has to be inspectable.
        tracer.emit(
            "history.used",
            turns=len(turns),
            resolved=was_resolved,
            query=retrieval_query if was_resolved else None,
        )

    all_evidence: list[dict] = []
    tool_trace: list[dict] = []
    context_log = ""
    total_calls = 0

    if screen_image_bytes:
        tracer.emit("vision.start", bytes=len(screen_image_bytes))
        start = time.monotonic()
        description = await describe_screen(screen_image_bytes, question)
        ms = int((time.monotonic() - start) * 1000)
        tracer.emit("vision.done", ms=ms, ok=bool(description))
        if description:
            frame_hash = hashlib.sha1(screen_image_bytes).hexdigest()[:10]
            screen_evidence = make_evidence("screen", f"screen:{frame_hash}", description, 1.0)
            all_evidence.append(screen_evidence)
            tool_trace.append({"tool": "describe_screen", "args": {}, "ms": ms, "evidence": [screen_evidence]})
            screen_context = description
        else:
            tool_trace.append(
                {"tool": "describe_screen", "args": {}, "ms": ms, "evidence": [], "note": "vision call failed"}
            )
            screen_context = None

    for round_num in range(MAX_ROUNDS):
        remaining = MAX_TOOL_CALLS_TOTAL - total_calls
        if remaining <= 0:
            break

        fast_calls = (
            _fast_path_calls(question, allowed_tools, retrieval_query) if round_num == 0 else None
        )
        if fast_calls is not None:
            calls = fast_calls[: min(MAX_CALLS_PER_ROUND, remaining)]
            tracer.emit(
                "plan.skipped",
                round=round_num + 1,
                reason="only search tools available — a plan cannot change the answer",
                calls=[{"tool": c["tool"], "args": c["args"]} for c in calls],
            )
        else:
            calls = await _plan_round(
                question,
                context_log,
                screen_context,
                round_num,
                remaining,
                tracer,
                language=language,
                known_repos=known_repos,
                allowed_tools=allowed_tools,
                org_name=org_name,
                agent_name=agent_name,
                history_block=history_block,
            )


        if not calls:
            break

        outcomes = await asyncio.gather(
            *[
                _run_one_call(
                    c,
                    repo_id,
                    tracer,
                    f"r{round_num + 1}c{i + 1}",
                    round_num + 1,
                    workspace_id=workspace_id,
                    known_repo_ids=known_repo_ids,
                    allowed_tools=allowed_tools,
                    # The backfill fills a tool's missing free-text argument,
                    # which is a search string — so it gets the resolved
                    # query for the same reason the fast path does.
                    question=retrieval_query,
                    meeting_slug=meeting_slug,
                )
                for i, c in enumerate(calls)
            ]
        )
        total_calls += len(outcomes)

        round_lines = [f"--- Round {round_num + 1} tool results ---"]
        for o in outcomes:
            r = o["result"]
            all_evidence.extend(r.get("evidence", []))
            # Evidence rides along on each trace entry (not a new top-level
            # answer key — Section 4's answer contract is fixed) so the
            # evidence panel (Phase 5) can build an ev_id -> evidence map for
            # every [ev_xxx] citation chip and render a real provenance
            # strip, instead of citations pointing at nothing once the
            # tool's raw result is discarded after composition.
            tool_trace.append(
                {"tool": o["tool"], "args": o["args"], "ms": o["ms"], "evidence": r.get("evidence", [])}
            )
            round_lines.append(
                f"{o['tool']}({o['args']}) -> status={r.get('status')} "
                f"evidence_count={len(r.get('evidence', []))} note={r.get('note')}"
            )
        context_log += "\n" + "\n".join(round_lines)

        if all_evidence:
            # A second planning round costs a full LLM round-trip (~1s) and
            # in practice returns "no further tools needed" nearly every
            # time once round 1 found anything — measured as pure latency
            # on the critical path of a live call. Round 2 still runs when
            # round 1 came back empty-handed, which is the case it exists
            # for.
            tracer.emit("plan.skipped", round=round_num + 2, reason="round 1 already gathered evidence")
            break

    dedup_evidence = _dedupe_evidence(all_evidence)

    tracer.emit("evidence.gathered", count=len(dedup_evidence), raw_count=len(all_evidence))

    if not dedup_evidence:
        result = {
            "answer": _no_evidence_abstention(tool_trace, language),
            "claims": [],
            "confidence": "low",
            "abstained": True,
            "escalation": None,
            "tool_trace": tool_trace,
        }
        tracer.emit("turn.done", ms=tracer.elapsed_ms, result=result, reason="no evidence")
        return result

    # Compose only ever cites a handful of items, but every extra one is
    # input tokens on the critical path (a 22-item set is ~8.2k chars of
    # prompt). Evidence arrives score-ordered per tool, so this keeps the
    # strongest and drops the tail. NOT a snippet-length cut — truncating
    # inside a snippet is what silently hid the S3 retry-policy paragraph
    # once before (see prompts._format_evidence).
    compose_evidence = _select_compose_evidence(tool_trace, COMPOSE_EVIDENCE_LIMIT) or dedup_evidence[
        :COMPOSE_EVIDENCE_LIMIT
    ]
    # The caller's language reaches ONLY compose. Planning stays in English
    # on purpose: tool names, the account directory and the corpus are all
    # English, and translating the planner's input buys nothing but a new
    # way for tool selection to go wrong.
    compose_prompt = build_compose_prompt(
        question,
        compose_evidence,
        language=language,
        persona_prompt=personas_prompt(bot_types),
        org_name=org_name,
        agent_name=agent_name,
        history_block=history_block,
    )
    tracer.emit("compose.start", evidence_count=len(compose_evidence), language=language or "en-IN")
    compose_started = time.monotonic()
    raw_composed = await generate(compose_prompt, max_output_tokens=700, temperature=0.1, json_schema=ANSWER_SCHEMA)
    parsed = extract_json(raw_composed)
    tracer.emit(
        "compose.done",
        ms=int((time.monotonic() - compose_started) * 1000),
        # A parse failure here is the already-documented DeepSeek JSON
        # flakiness, and it's exactly the kind of thing the advanced panel
        # exists to make visible instead of it silently becoming a
        # low-confidence abstention.
        parsed=parsed is not None,
    )
    composed = parsed or {
        "answer": "I wasn't able to compose a reliable answer from the evidence I found.",
        "claims": [],
        "abstained": True,
        "escalation": None,
    }

    valid_ids = {e["id"] for e in compose_evidence}
    verify_started = time.monotonic()
    result = verify(composed, valid_ids)
    result["tool_trace"] = tool_trace
    tracer.emit(
        "verify.done",
        ms=int((time.monotonic() - verify_started) * 1000),
        claims_in=len(composed.get("claims") or []),
        claims_kept=len(result.get("claims") or []),
        confidence=result.get("confidence"),
        abstained=result.get("abstained"),
    )
    tracer.emit("turn.done", ms=tracer.elapsed_ms, result=result)
    return result

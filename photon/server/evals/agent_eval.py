"""Accuracy eval for the agent loop — run this before trusting any change
to the model, the prompts, or the tool-selection guidance.

    cd server && .venv/bin/python evals/agent_eval.py <model> [trials]
    HARD=1 .venv/bin/python evals/agent_eval.py <model> [trials]

The model is passed in (not read from config) so two models can be A/B'd
on identical cases — that's how gemini-3.5-flash-lite was chosen over
deepseek-v4-flash. It runs the loop IN-PROCESS against the live stack, so
the brain-api does not need to be up, but Postgres/Qdrant/Neo4j do
(the tools are real).

Scored per case:
  tools   — did the planner call the tool(s) this question actually needs
            (and not a pile of extras)?
  content — does the answer contain the known ground truth from the seed
            corpus (every required group must hit), and none of the known
            wrong answers?
  abstain — did it abstain exactly when it should?
  cites   — is every [ev_xxx] marker in the answer a real id from this
            turn's evidence? (a fabricated locator is a build-breaking bug)
"""
import asyncio, os, re, statistics, sys, time
# Run from anywhere: this file lives in server/evals/.
_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER)
os.chdir(_SERVER)

from app.config import get_settings
from app.agent.loop import answer_question

MARKER = re.compile(r"\[(ev_[0-9a-f]+)\]")

CASES = [
    dict(id="S1-northwind", q="Northwind's webhooks are failing, what's going on?",
         want_tools={"get_account", "get_account_logs"}, any_of_tools=True, max_tools=3,
         need=[["401", "unauthor"], ["secret", "rotat"]], forbid=[], abstain=False),
    dict(id="S2-bangalore", q="why does pricing have a special case for Bangalore?",
         want_tools={"search_code", "explain_why"}, any_of_tools=False, max_tools=3,
         need=[["partner", "reseller", "referral"]],
         # the documented wrong answer this exact question produced before
         forbid=["isn't a special case", "not a special case", "five launch cities"],
         abstain=False),
    dict(id="S3-retries", q="how many times does Meridian retry a failed webhook?",
         want_tools={"search_docs", "search_code"}, any_of_tools=True, max_tools=3,
         need=[["3", "three"]], forbid=[], abstain=False),
    dict(id="accounts", q="which customer accounts do you know about?",
         want_tools={"list_accounts"}, any_of_tools=True, max_tools=2,
         need=[["northwind"], ["calico"], ["orion"]], forbid=[], abstain=False),
    dict(id="calico-rate", q="why does Calico Transit get a different rate?",
         want_tools={"get_account", "search_code", "explain_why", "search_slack"}, any_of_tools=True, max_tools=3,
         need=[["partner", "reseller", "bangalore"]], forbid=[], abstain=False),
    dict(id="docs-rotate", q="how does a customer rotate their webhook signing secret?",
         want_tools={"search_docs"}, any_of_tools=True, max_tools=3,
         need=[["secret", "rotat", "signing"]], forbid=[], abstain=False),
    dict(id="offtopic", q="what did you think of the football game last night?",
         want_tools=set(), any_of_tools=True, max_tools=1, need=[], forbid=[], abstain=True),
    dict(id="unknowable", q="what is Meridian's refund policy for annual plans?",
         want_tools=None, any_of_tools=True, max_tools=4, need=[], forbid=[], abstain=None),
]


HARD = [
    # Voice reality: Deepgram hands us lowercase, unpunctuated text. If tool
    # selection only works on tidy written questions it doesn't work at all.
    dict(id="asr-nopunct", q="why is bangalore pricing different from other cities",
         want_tools={"search_code", "explain_why"}, any_of_tools=True, max_tools=3,
         need=[["partner", "reseller", "referral"]],
         forbid=["isn't a special case", "not a special case"], abstain=False),
    dict(id="asr-mangled", q="uh so the webhooks for north wind are failing can you check",
         want_tools={"get_account", "get_account_logs"}, any_of_tools=True, max_tools=3,
         need=[["401", "unauthor", "secret", "rotat"]], forbid=[], abstain=False),
    # Indirect reference: never names the account, has to map Mumbai -> Northwind.
    dict(id="indirect-account", q="our Mumbai customer says their integration broke last week, why?",
         want_tools={"get_account", "get_account_logs", "get_incidents", "search_tickets"},
         any_of_tools=True, max_tools=4, need=[["secret", "rotat", "401"]], forbid=[], abstain=False),
    # Must NOT invent an account that doesn't exist in the corpus.
    dict(id="fake-account", q="how is the Acme Corporation account doing?",
         want_tools=None, any_of_tools=True, max_tools=4, need=[], forbid=["acme is", "acme's account is healthy"],
         abstain=True),
    # The S3 conflict from the docs side — docs claim 5 retries over 24h,
    # code does 3. Either answer is defensible; inventing a third is not.
    dict(id="conflict-docs", q="the docs say webhooks retry 5 times over 24 hours, is that right?",
         want_tools={"check_conflict", "search_docs", "search_code"}, any_of_tools=True, max_tools=3,
         need=[["3", "three", "conflict", "differ", "not", "actually"]], forbid=[], abstain=None),
    # Multi-hop provenance: the answer only exists in one Slack thread.
    dict(id="who-negotiated", q="who negotiated the Bangalore partner deal?",
         want_tools={"search_slack", "explain_why", "search_tickets"}, any_of_tools=True, max_tools=3,
         need=[["priya", "partnership", "blr", "legal"]], forbid=[], abstain=None),
    # Small talk mid-call (open mic means this WILL happen) — must not answer.
    dict(id="ambient", q="yeah sorry one second let me grab my coffee",
         want_tools=None, any_of_tools=True, max_tools=2, need=[], forbid=[], abstain=True),
    # Plausible-sounding but unsupported: nothing in the corpus about SLAs.
    dict(id="no-eviden-sla", q="what uptime SLA does Meridian guarantee enterprise customers?",
         want_tools=None, any_of_tools=True, max_tools=4, need=[], forbid=["99.9", "99.99"], abstain=None),
]

async def run_case(case):
    t0 = time.monotonic()
    res = await answer_question(case["q"])
    ms = int((time.monotonic() - t0) * 1000)
    called = {t["tool"] for t in res["tool_trace"]}
    answer = (res["answer"] or "").lower()

    if case["want_tools"] is None:
        tools_ok = True
    elif case["any_of_tools"]:
        tools_ok = bool(called & case["want_tools"]) if case["want_tools"] else not called
    else:
        tools_ok = case["want_tools"].issubset(called)
    tools_ok = tools_ok and len(res["tool_trace"]) <= case["max_tools"]

    content_ok = all(any(k in answer for k in group) for group in case["need"]) and not any(
        f in answer for f in case["forbid"])
    if res["abstained"] and case["abstain"] is False:
        content_ok = False

    abstain_ok = True if case["abstain"] is None else (res["abstained"] == case["abstain"])

    valid_ids = {e["id"] for t in res["tool_trace"] for e in t.get("evidence", [])}
    cites_ok = set(MARKER.findall(res["answer"] or "")).issubset(valid_ids)

    return dict(ms=ms, tools=sorted(called), tools_ok=tools_ok, content_ok=content_ok,
                abstain_ok=abstain_ok, cites_ok=cites_ok, abstained=res["abstained"],
                conf=res["confidence"], answer=res["answer"])

async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    model = sys.argv[1]
    trials = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    s = get_settings()
    s.openrouter_chat_model = model
    import app.core.llm.openrouter as orl
    orl.settings = s

    cases = HARD if os.environ.get("HARD") else CASES
    print(f"### {model}  ({trials} trials/case, {'HARD' if os.environ.get('HARD') else 'base'} set)")
    rows, times = [], []
    for case in cases:
        for i in range(trials):
            r = await run_case(case)
            rows.append((case["id"], r)); times.append(r["ms"])
            flags = "".join([" T" if not r["tools_ok"] else "  ", " C" if not r["content_ok"] else "  ",
                             " A" if not r["abstain_ok"] else "  ", " X" if not r["cites_ok"] else "  "])
            print(f"{case['id']:14s} #{i+1} {r['ms']:5d}ms [{flags}] {r['conf']:6s} "
                  f"{'abst' if r['abstained'] else '    '} tools={','.join(r['tools']) or '-'}")
            if not (r["tools_ok"] and r["content_ok"] and r["abstain_ok"] and r["cites_ok"]):
                print(f"                 -> {r['answer'][:180]}")
    n = len(rows)
    for key in ("tools_ok", "content_ok", "abstain_ok", "cites_ok"):
        ok = sum(1 for _, r in rows if r[key])
        print(f"{key:11s} {ok}/{n}  ({100*ok//n}%)")
    allok = sum(1 for _, r in rows if all(r[k] for k in ("tools_ok","content_ok","abstain_ok","cites_ok")))
    print(f"ALL PASS    {allok}/{n}  ({100*allok//n}%)")
    print(f"latency     median {int(statistics.median(times))}ms  p90 {sorted(times)[int(n*0.9)-1]}ms  max {max(times)}ms")

asyncio.run(main())

"""Does the planner pick tools from INTENT, and ONLY the tools it needs?

agent_eval.py checks whether answers are right. This checks something the
eval cannot: whether the planner reaches for the correct source from the
shape of the question, and whether it reaches for anything it did not need.

Precision matters as much as recall here. An irrelevant result does not sit
harmlessly in the evidence — it competes with the right answer during
composition and sometimes wins, on top of costing a round-trip.

    cd server && .venv/bin/python evals/intent_probe.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.agent.loop import answer_question

CASES = [
    ("why does the code do X",        "why does pricing have a special case for Bangalore?",      {"search_code","explain_why"}),
    ("is it a known issue",           "is the webhook 401 problem a known issue we're tracking?", {"search_jira","search_tickets","search_linear"}),
    ("what's our process",            "what's our process when a customer reports data loss?",    {"search_custom_docs"}),
    ("who said / decided",            "who decided we'd give Bangalore a partner rate?",          {"search_slack","explain_why"}),
    ("is it broken right now",        "is anything alerting right now that would explain this?",  {"search_datadog","get_incidents"}),
    ("customer state",                "is Northwind on the partner tier?",                        {"get_account","list_accounts"}),
    ("docs say what",                 "what do our docs say about webhook retries?",              {"search_docs"}),
    ("small talk",                    "thanks, that's all for now",                               set()),
]

async def main():
    ok = 0
    extras = []
    total = [0]
    for label, q, expected in CASES:
        # A workspace with Slack, custom docs and the seed corpus, so the
        # connector-backed intents have somewhere real to land.
        res = await answer_question(q, workspace_id=os.environ.get("PROBE_WORKSPACE", "60c023a4-6642-4f9b-b6cb-616ad3115d87"))
        used = {t["tool"] for t in res["tool_trace"]}
        total[0] += len(res["tool_trace"])
        hit = bool(used & expected) if expected else not used
        ok += hit
        extra = used - expected
        extras.append(len(extra))
        flag = "" if not extra else f"   +{len(extra)} extra: {','.join(sorted(extra))}"
        print(f"  {'OK ' if hit else 'MISS'} {label:22s} -> {','.join(sorted(used)) or '(none)'}{flag}")
    print(f"\nintent match: {ok}/{len(CASES)}   |   total tool calls: {total[0]}   |   unnecessary: {sum(extras)}")

asyncio.run(main())

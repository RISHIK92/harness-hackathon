"""Code-retrieval ranking eval — does search_code put the right chunk first?

Why this exists separately from agent_eval.py: agent_eval scores the whole
turn, and its code questions are easy enough that a mediocre ranking still
produces a correct answer. It measured 22-23/24 both with and without
reranking. So it cannot tell you whether retrieval got better or worse —
this file can.

Ground truth is a (file, line) the answer genuinely depends on. A chunk is
correct if it is from that file AND spans that line, so "found the right
file, wrong end of it" does not count as a hit.

    cd server && .venv/bin/python evals/retrieval_eval.py
    RERANK_ENABLED=false .venv/bin/python evals/retrieval_eval.py   # A/B

Baseline recorded 2026-09-12 on the seed repo (rerank-2.5-lite, pool 20):

    strategy      top1   top3    MRR
    dense only    2/8    5/8     0.473
    + rerank      6/8    8/8     0.854
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import get_settings  # noqa: E402
from app.seed.loader import get_seed_repo_id  # noqa: E402
from app.tools.code import search_code  # noqa: E402

# (question, gold file, a line that MUST fall inside the gold chunk)
CASES: list[tuple[str, str, int]] = [
    # Exact identifiers. Dense embeddings smear these — the classic failure
    # is ranking a docstring that MENTIONS the constant above the line that
    # DEFINES it, which is what RETRY_BACKOFF_SECONDS used to do.
    ("PARTNER_CITY_RATES", "app/pricing.py", 16),
    ("RETRY_BACKOFF_SECONDS", "app/webhooks.py", 16),
    ("compute_price", "app/pricing.py", 20),
    # Natural language. The trap here is tests/: they restate the question's
    # identifiers almost verbatim and so embed very close to it, while
    # almost never being the answer.
    ("why does pricing have a special case for Bangalore", "app/pricing.py", 24),
    ("what is the partner discount multiplier in bangalore", "app/pricing.py", 16),
    ("how many times do we retry a failed webhook", "app/webhooks.py", 16),
    ("webhook signature verification", "app/webhooks.py", 38),
    ("where is the hmac signature computed", "app/webhooks.py", 38),
]

TOP_K = 5


def _rank_of_gold(evidence: list[dict], gold_file: str, gold_line: int) -> int | None:
    for i, ev in enumerate(evidence):
        locator = ev.get("locator", "")
        if not locator.startswith(f"{gold_file}:L"):
            continue
        try:
            span = locator.split(":L", 1)[1]
            start, end = (int(x) for x in span.split("-L"))
        except (ValueError, IndexError):
            continue
        if start <= gold_line <= end:
            return i + 1
    return None


async def main() -> int:
    settings = get_settings()
    repo_id = get_seed_repo_id()
    print(f"repo={repo_id}  rerank={settings.rerank_enabled} "
          f"model={settings.rerank_model} pool={settings.rerank_pool}\n")

    top1 = top3 = 0
    mrr = 0.0
    elapsed: list[float] = []

    for question, gold_file, gold_line in CASES:
        started = time.time()
        result = await search_code(question, repo_id=repo_id, top_k=TOP_K)
        elapsed.append((time.time() - started) * 1000)
        rank = _rank_of_gold(result.get("evidence", []), gold_file, gold_line)
        if rank:
            mrr += 1 / rank
            top1 += rank == 1
            top3 += rank <= 3
        flag = " " if rank == 1 else ("." if rank and rank <= 3 else "X")
        print(f"  {flag} rank={rank or '-':<4} {gold_file}:{gold_line:<4} {question}")

    n = len(CASES)
    elapsed.sort()
    print(f"\ntop1 {top1}/{n}   top3 {top3}/{n}   MRR {mrr / n:.3f}   "
          f"median {elapsed[n // 2]:.0f}ms")
    # Guard the measured gain: below this, reranking is not earning its
    # latency and something regressed.
    ok = top1 >= 5 and mrr >= 0.75 if settings.rerank_enabled else True
    print("PASS" if ok else "FAIL — ranking regressed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

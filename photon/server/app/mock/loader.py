"""Mock data for a real workspace's own testing.

Deliberately a DIFFERENT fictional company ("Adventa", an exam-prep
platform for JEE/NEET students) from the "Meridian" seed corpus in
app/seed/ — that corpus is the eval harness's fixture and stays exactly
as-is. This module exists for the "Mock" button on the dashboard: a real
workspace with nothing connected yet can click it to see the agent answer
from *something*, without it being confused for a real GitHub/Slack/Jira
connection.

The repo excerpt (data/adventa_backend/) and the storyline below are
modelled on the REAL adventa-backend codebase — its actual Smart Mock Test
unlock rule (MIN_TESTS_FOR_SMART_MOCK = 3, with a Diagnostic Test fallback)
and Weakness Test topic selection (2 weakest topics per subject, gated on
totalAttempted >= 3) — condensed to a representative slice, not the whole
13k-line backend (auth, groups, AI tutor, video pipeline, etc. live
elsewhere in the real repo and aren't reproduced here).

Every item generated here is prefixed "[MOCK]" in its title/text and
carries a "mock:" locator prefix (applied by routers/mock.py, not here) —
see CLAUDE.md's "Removing the demo corpus from real workspaces" section for
why that distinction is load-bearing: showing fictional data
indistinguishably from real data is what caused a real bug there.

Each provider's items are indexed through the SAME sync/search code a real
connection uses (slack_sync.index_messages, jira_sync.index_issues,
connector_base.index_items) — so search_slack/search_jira/search_linear/
search_notion/search_datadog need no changes at all to pick these up; they
already query by workspace_id.
"""
from __future__ import annotations

import os
import time

MOCK_REPO_PATH = os.path.join(os.path.dirname(__file__), "data", "adventa_backend")


def mock_repo_path() -> str:
    return MOCK_REPO_PATH


# A shared reference point so the tickets/issues/messages/docs/monitors all
# tell one coherent (fictional) story, the same way Meridian's Bangalore
# rate does across its sources — makes a demo answer feel grounded rather
# than like disconnected fixture noise.
_NOW = time.time()


def mock_slack_messages() -> tuple[list[dict], dict[str, str]]:
    names = {"U_ANITA": "Anita Rao", "U_DEV": "Dev Kumar", "U_ARJUN": "Arjun Mehta"}
    base_ts = _NOW - 86400 * 3
    messages = [
        {
            "user": "U_ANITA", "ts": f"{base_ts:.6f}", "thread_ts": f"{base_ts:.6f}",
            "text": "[MOCK] Smart Mock Test only unlocks after 3 completed general mock "
                    "tests (MIN_TESTS_FOR_SMART_MOCK) — below that there isn't enough "
                    "topic-level accuracy data to personalize on, so we generate a "
                    "Diagnostic Test instead. Tracked as ADV-201.",
        },
        {
            "user": "U_DEV", "ts": f"{base_ts + 60:.6f}", "thread_ts": f"{base_ts:.6f}",
            "text": "[MOCK] Right — the Diagnostic Test is a fixed-shape general paper, "
                    "evenly weighted across subjects, since there's no per-topic accuracy "
                    "yet to weight by. Students never see a blocked screen, just a "
                    "non-personalized test until they hit the threshold.",
        },
        {
            "user": "U_ARJUN", "ts": f"{base_ts + 200:.6f}", "thread_ts": f"{base_ts:.6f}",
            "text": "[MOCK] Should the threshold be lower for JEE Advanced? It's more "
                    "calculation-heavy, students burn through fewer mocks before they'd "
                    "actually benefit from personalization.",
        },
        {
            "user": "U_ANITA", "ts": f"{base_ts + 260:.6f}", "thread_ts": f"{base_ts:.6f}",
            "text": "[MOCK] Good question — filed ADV-230 to look at a per-exam threshold "
                    "instead of one global MIN_TESTS_FOR_SMART_MOCK constant.",
        },
        {
            "user": "U_DEV", "ts": f"{_NOW - 3600:.6f}", "thread_ts": f"{_NOW - 3600:.6f}",
            "text": "[MOCK] Heads up, the Weakness Test's before/after accuracy comparison "
                    "can show a null delta if the student hasn't attempted anything on "
                    "that topic since the snapshot was taken — filed ADV-241.",
        },
    ]
    return messages, names


def mock_jira_issues() -> list[dict]:
    return [
        {
            "issue_key": "ADV-201", "summary": "[MOCK] Gate Smart Mock Test behind 3 completed mock tests",
            "status": "Done", "assignee": "Anita Rao",
            "text": "[MOCK] Implemented MIN_TESTS_FOR_SMART_MOCK = 3 in "
                    "createSmartMockTest. Below the threshold, generateDiagnosticTest() "
                    "returns a general fixed-shape paper instead of blocking the student.",
        },
        {
            "issue_key": "ADV-210", "summary": "[MOCK] Weakness Test should target the 2 weakest topics per subject",
            "status": "Done", "assignee": "Dev Kumar",
            "text": "[MOCK] generateWeaknessTest now selects the top 2 lowest-accuracy "
                    "topics per subject, requiring totalAttempted >= 3 so a single "
                    "unlucky question doesn't get flagged as a weakness.",
        },
        {
            "issue_key": "ADV-224", "summary": "[MOCK] Track diagnostic-vs-smart-mock funnel metric",
            "status": "Open", "assignee": "Arjun Mehta",
            "text": "[MOCK] We don't currently log how many students hit the Diagnostic "
                    "path vs go straight to a Smart Mock. Needed to know whether the "
                    "3-test threshold is actually the right number.",
        },
        {
            "issue_key": "ADV-230", "summary": "[MOCK] Consider a per-exam MIN_TESTS_FOR_SMART_MOCK threshold",
            "status": "In Progress", "assignee": "Anita Rao",
            "text": "[MOCK] JEE Advanced is more calculation-heavy than JEE Main or "
                    "NEET UG; a flat 3-test threshold across all exams may not be right. "
                    "Investigating a per-exam override on the same constant.",
        },
        {
            "issue_key": "ADV-241", "summary": "[MOCK] Weakness test accuracy comparison shows null delta sometimes",
            "status": "Open", "assignee": "Dev Kumar",
            "text": "[MOCK] getAccuracyComparison falls back to the pre-test snapshot "
                    "when there's no post-test attempt yet, which reads as a 0.00 delta "
                    "instead of 'not attempted yet' — confusing on the results screen.",
        },
    ]


def mock_linear_items() -> list[dict]:
    return [
        {"external_id": "ADV-1", "title": "[MOCK] Cache examDefaults.json lookup",
         "text": "[MOCK] examDefaults.json (duration/question count per exam) is read "
                 "from disk on every mock test generation. Should be cached in memory — "
                 "it changes maybe once a year.",
         "url": "https://linear.app/adventa/issue/ADV-1"},
        {"external_id": "ADV-2", "title": "[MOCK] AI video generation worker retries aren't idempotent",
         "text": "[MOCK] The Manim+Gemini video generation queue can produce a duplicate "
                 "YouTube upload if a worker retries after a partial success. Needs an "
                 "idempotency key keyed on the explanation request id.",
         "url": "https://linear.app/adventa/issue/ADV-2"},
        {"external_id": "ADV-3", "title": "[MOCK] Add index on UserTestAnswer(userId, isCorrect)",
         "text": "[MOCK] The weakness-topic query filters on userId + isCorrect=false "
                 "and is doing a sequential scan for students with a long attempt "
                 "history. Same root cause class as the Smart Mock eligibility query.",
         "url": "https://linear.app/adventa/issue/ADV-3"},
        {"external_id": "ADV-4", "title": "[MOCK] Expose GET /smart-mock/:examId/eligibility",
         "text": "[MOCK] Right now the only way to know 'you need 2 more mocks to "
                 "unlock Smart Mock' is to actually call createSmartMockTest and read "
                 "the DIAGNOSTIC-mode message. A read-only eligibility endpoint would "
                 "let the frontend show this without generating a test.",
         "url": "https://linear.app/adventa/issue/ADV-4"},
        {"external_id": "ADV-5", "title": "[MOCK] Move the AI tutor 'Ace' persona prompt out of source",
         "text": "[MOCK] COACH_PERSONA_PROMPT is a hardcoded string in "
                 "aiTutorController.ts. Product wants to iterate on tone without a "
                 "deploy — move it to a config table.",
         "url": "https://linear.app/adventa/issue/ADV-5"},
    ]


def mock_notion_items() -> list[dict]:
    return [
        {"external_id": "adventa-smart-mock-doc", "title": "[MOCK] Smart Mock Test — how personalization works",
         "text": "[MOCK] Unlocks after 3 completed general mock tests "
                 "(MIN_TESTS_FOR_SMART_MOCK). Below that, students get a Diagnostic "
                 "Test — a fixed-shape general paper — instead of a blocked screen. "
                 "See ADV-201 for the implementation.",
         "url": "https://notion.so/adventa/smart-mock-personalization"},
        {"external_id": "adventa-weakness-topic-doc", "title": "[MOCK] Weakness Test — topic selection algorithm",
         "text": "[MOCK] Selects the 2 lowest-accuracy topics per subject, requiring at "
                 "least 3 attempts on a topic before it can be flagged weak (avoids "
                 "false positives from a single wrong answer). Before/after accuracy is "
                 "tracked via a performance snapshot taken at test creation.",
         "url": "https://notion.so/adventa/weakness-test-topic-selection"},
        {"external_id": "adventa-exam-defaults-doc", "title": "[MOCK] Exam defaults reference",
         "text": "[MOCK] JEE Main: 180 min, 90 questions. JEE Advanced: 360 min, 108 "
                 "questions. NEET UG: 200 min, 180 questions. Source of truth is "
                 "examDefaults.json, read by both mock generation and the diagnostic "
                 "fallback.",
         "url": "https://notion.so/adventa/exam-defaults"},
        {"external_id": "adventa-video-pipeline-doc", "title": "[MOCK] AI video explanation pipeline runbook",
         "text": "[MOCK] A question's step-by-step explanation is scripted via Gemini, "
                 "rendered with Manim, and uploaded to YouTube by a BullMQ worker. A "
                 "stuck job usually means the Manim render step, not the upload — check "
                 "the queue depth first.",
         "url": "https://notion.so/adventa/video-pipeline-runbook"},
        {"external_id": "adventa-oncall-doc", "title": "[MOCK] On-call: video generation queue",
         "text": "[MOCK] Weekly rotation for the AI video generation queue "
                 "(BullMQ/Redis). Current week: Dev Kumar. Backup: Arjun Mehta.",
         "url": "https://notion.so/adventa/video-queue-oncall"},
    ]


def mock_datadog_items() -> list[dict]:
    return [
        {"external_id": "adventa-video-queue-backlog", "title": "[MOCK] video-generation-queue backlog",
         "text": "[MOCK] Monitor: queued-but-unprocessed AI video generation jobs "
                 "(BullMQ). Currently OK.",
         "meta": {"monitor_state": "OK"}},
        {"external_id": "adventa-ai-tutor-latency", "title": "[MOCK] AI tutor response latency (Gemini)",
         "text": "[MOCK] Monitor: p95 latency on the 'Ace' AI tutor's response, backed "
                 "by Gemini. Warn — elevated but under the alert threshold.",
         "meta": {"monitor_state": "Warn"}},
        {"external_id": "adventa-smart-mock-errors", "title": "[MOCK] smart-mock generation error rate",
         "text": "[MOCK] Monitor: error rate on createSmartMockTest, both DIAGNOSTIC "
                 "and SMART modes. Currently OK.",
         "meta": {"monitor_state": "OK"}},
        {"external_id": "adventa-db-connections", "title": "[MOCK] Postgres connections (Prisma pool)",
         "text": "[MOCK] Monitor: Prisma connection pool utilization above 80%. "
                 "Currently OK.",
         "meta": {"monitor_state": "OK"}},
        {"external_id": "adventa-youtube-upload-failures", "title": "[MOCK] youtube-upload failure rate",
         "text": "[MOCK] Monitor: failure rate on the video pipeline's YouTube upload "
                 "step. Alerting — correlates with the retry idempotency bug in ADV-2.",
         "meta": {"monitor_state": "Alert"}},
    ]

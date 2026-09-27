# Photon — TODOs

Status of every feature, honestly. Written 2026-09-12.

**Legend:** `[x]` done and verified · `[~]` half done · `[ ]` not started

**Score:** 45 done · 6 half · 30 not started

The done half is the hard half (grounded retrieval, citations, voice). The
missing half is mostly plumbing: whisper mode, distribution, a real customer
record, and the ticket layer.

---

## A. Answer engine

- [x] Planner picks which sources to search per question, runs them in parallel
- [x] 19 tools across code, docs, tickets, Slack, Jira, Notion, Linear, Datadog,
      past calls
- [x] Evidence contract — source type, locator, snippet, score on every item
- [x] Verifier strips any claim not backed by real evidence
- [x] Abstains instead of guessing
- [x] Confidence returned with every answer
- [x] `explain_why` — code → commit → ticket → PR → the Slack thread that decided it
- [x] `check_conflict` — flags docs disagreeing with code
- [x] Multi-repo disambiguation across a workspace
- [x] ~2s answers, measured (median 2065ms, p90 2181ms)
- [x] Accuracy eval harness — base 24/24, hard 15/16
- [x] Memory across turns in one conversation — follow-ups resolve against the
      last 6 turns (`app/agent/history.py`), on the voice and text paths both
- [x] Memory across calls — `search_past_calls` cites earlier calls' transcripts

## B. Voice and call presence

- [x] Joins a live call, hears the caller, speaks the answer
- [x] Multilingual voice — Telugu, Tamil, Hindi, English, detected per utterance
- [x] Small-talk gate — greetings 0ms, ambient chatter ignored
- [x] Poke-to-address on multi-party calls
- [x] Wake word as fallback
- [x] Reads the client's shared screen as citable evidence
- [x] Citation markers stripped from speech, kept in the UI

## C. Photon's own meeting (speak mode)

- [x] Shareable meeting codes
- [x] Waiting room with admit/deny, verified server-side
- [x] Video tiles, mic, camera, screen share
- [x] Captions split by speaker
- [x] Live evidence panel — citation chips, provenance strip, source cards
- [x] Live trace panel — tool running now, latency per step
- [x] Shared transcript per call, exportable as markdown
- [x] Text-input fallback if audio fails
- [x] Per-call source selection and persona
- [x] Refuses to join a call with no sources connected
- [ ] Google Calendar conferencing add-on (Photon in "Add conferencing")
- [ ] Outlook add-in
- [ ] Scheduling page ("book a support call")
- [ ] Dial-in fallback

## D. Whisper mode (works on any meeting platform)

- [~] Bot joins Meet / Teams / Zoom — paste a link on /whisper ("Send
      notetaker"); the bot joins as "<name>'s Photon (notes)", announces itself
      in the meeting chat, and its state (joining / waiting to be admitted /
      listening / ended) is shown with what to do. Request and payload shapes
      now match Recall's docs (the transcript is at `data.data.words`; the old
      parser read `data.words` and would have heard nothing). Remaining: a
      `RECALL_API_KEY` and a public `PUBLIC_BASE_URL` — refused up front with
      a clear message when either is missing
- [x] Whisper as a call mode on Photon's own call — every participant's mic
      gets its own STT stream (no AgentSession, so no one-linked-speaker
      limit), the agent never speaks, and everyone sees a "taking notes" banner
- [x] One ingest path per call — the browser caption bridge that doubled
      every line (and every suggestion) is gone
- [x] Suggestions run after the line is acknowledged, so a vendor webhook is
      never held open for an agent turn
- [x] Knows who is the client and who is us — `speaker_user_id` on Photon's
      own calls, name-vs-member matching everywhere else
- [x] "Whisper this call" — a side panel on the call, listening to the live
      captions of everyone except you and Photon (finals only, deduped by
      caption id). Limitation: LiveKit transcribes one linked participant at
      a time, so a multi-party room is only partly heard
- [x] Private Photon thread per team member, invisible to the client —
      enforced by `user_id` on the row, not by the UI
- [x] Unprompted suggested answers when the client asks something
- [x] Warnings on suggestions — internal-only, mixed sources, low confidence,
      abstained, escalation. *("not on the roadmap" is NOT implemented: it
      needs issue status in the evidence item, which the tool contract
      doesn't carry — see the known defect below)*
- [~] Slack as an alternative whisper surface — written and DMs the member by
      verified email, but **needs the `chat:write` scope**, which the app
      deliberately does not request today (the Slack connection is read-only)
- [x] No-bot whisper on Google Meet — Chrome extension (`extension/`): reads
      Meet's live captions, side panel with suggestions, paired by one-time
      code to a whisper-only, per-browser revocable token
- [ ] Desktop capture for Zoom / Teams desktop apps (Recall Desktop SDK)
- [x] Threads persist after the call — ending a session stops ingest and
      leaves every thread and message in place

### Whisper — needs you

1. [ ] **A meeting-bot vendor key** (`RECALL_API_KEY`) **and a public URL**
       the vendor can reach (`PUBLIC_BASE_URL` — ngrok is enough). That is
       the only thing between here and whisper working on a real Meet call;
       no code remains. `POST /api/whisper/sessions/{id}/bot` currently
       returns 501 naming exactly this.
2. [ ] **Slack `chat:write`** — re-install the Slack app with the scope
       added. Deliberately a decision, not a silent upgrade: it is the first
       write permission Photon would hold on your Slack, and the connection
       being read-only is a property a reviewer can check today.

### Whisper — next, in value order

1. [ ] **Issue status on connector evidence.** Add `status` to Jira/Linear
       evidence items; that is the one missing input for the "not on the
       roadmap" warning, which is deliberately not faked today.
2. [ ] **SSE instead of 2.5s polling** for the thread. A suggestion arriving
       2.5s late is survivable; during a live objection it is not. The trace
       panel already has the pattern to copy.
3. [x] **Chrome extension** — reads Meet's captions rather than capturing tab
       audio (no STT cost, speaker names for free); same ingest path.
4. [x] **Per-call whisper toggle in the pre-call screen** ("How it takes
       part: Speaks / Whispers").
5. [ ] **Suggestion feedback** — mark a suggestion used / wrong, so there is
       ever any data on whether the gate's looseness is right.

## E. Sources

- [x] GitHub App — private-org repos, user picks which to import
- [x] Ingest ~17s regardless of repo size, with time estimates
- [x] Slack, including thread replies
- [x] Jira
- [x] Notion, Linear, Datadog
- [x] Document upload
- [~] Customer record — **demo fixtures only**, no real CRM or billing source
      (`get_account`, `list_accounts`, `get_account_logs`, `get_incidents`)
- [ ] Help-desk history (Zendesk, Intercom, Freshdesk)
- [ ] Meeting transcripts from existing notetakers
- [ ] Sentry
- [ ] Microsoft Teams and Confluence (larger companies)

## F. Action layer

- [ ] Photon's own work identity in Jira / Linear / help desk
- [ ] Raises tickets from calls with transcript, citations and account attached
- [ ] Detects commitments ("we'll fix it by Friday") and turns them into work
- [ ] Assigns to the right owner from the org's project/component map
- [ ] Links to a similar existing issue instead of creating a duplicate
- [ ] Post-call follow-up email drafted with sources
- [ ] Notifies rep/client when the ticket resolves

## G. Knowledge that maintains itself

- [ ] Extracts questions and answers from calls into the knowledge base
- [ ] Human approval before an extracted answer becomes knowledge
- [ ] Gap report — questions Photon couldn't answer
- [~] Docs-vs-code drift alerts — engine exists (`check_conflict`), no digest
- [ ] "What changed this week that support should know" digest
- [ ] "Who knows this" expert routing

## H. Workspace, trust and control

- [x] Workspaces, invites, roles, individual vs team
- [x] Email/password and GitHub sign-in
- [x] Credentials encrypted at rest (Fernet)
- [x] Tenant isolation enforced in code, never chosen by the model
- [ ] Client-safe vs internal-only labels per source
- [ ] Access mirroring — can't reveal what the user couldn't open themselves
- [ ] Review log of everything Photon said out loud
- [ ] SSO and audit trail

---

## Known defects (from the `server/app/core/` review)

- [ ] **Deleting a repo leaves its chunks searchable.** `delete_repo`
      (`routers/repos.py:188`) removes only the Postgres row;
      `delete_repo_chunks` (`embedder.py:144`) is never called. Workspace-wide
      `search_code` keeps citing deleted repos. Neo4j nodes leak too.
- [ ] **Real relevance score is discarded.** `embedder.py:127` keeps only
      `hit.payload`; `tools/code.py:21` fakes a score from rank order. No
      threshold is possible, so a code question never comes back empty.
- [x] ~~**Re-ingest leaves stale chunks.**~~ Fixed — ingest now calls
      `delete_repo_chunks()` before writing, so a re-ingest is authoritative
      rather than additive. Found live: `drillController.ts` held 50 chunks
      with duplicate ranges and one claiming L1-L1315 while holding 72 lines,
      still being cited. Already-embedded ones are rejected at read time by
      `_overclaims_its_range()`.
- [ ] **Chunk overlap and misleading line ranges.** A class and its methods are
      both chunked; non-symbol chunks span line ranges they don't contain.
- [ ] **`find_usages` / `trace_symbol` don't do what they claim** — vector search
      over a phrase, and a file-level import graph, not a call graph.
- [ ] No Qdrant payload index on `repo_id` / `workspace_id`.
- [ ] `vector_search` with neither id set searches every tenant — guarded only by
      callers, not by core.
- [ ] `/api/query`'s no-evidence path tells the LLM to answer from general
      knowledge, contradicting the abstain rule (legacy console route only).
- [ ] `POST /api/agent/ask(/stream)` is still unauthenticated; `workspace_id` is
      client-asserted (documented demo-scope decision).
- [ ] **A suggestion cannot say "not on the roadmap".** The evidence item
      (Section 4) carries source_type, locator, snippet and score — but not an
      issue's STATE, so a Jira/Linear hit cannot be distinguished between
      shipped, in progress and rejected. Every other whisper warning is
      derived from the answer contract; this one would have to be inferred
      from prose, and a warning that is itself a guess trains people to
      ignore the label. Needs `status` on connector evidence first.
- [ ] **A source group added later is invisible to every existing call.**
      `Meeting.enabled_sources` freezes the toggles as they stood when the call
      was set up, and a stored list cannot distinguish "the user turned this
      off" from "it did not exist yet" — so a new group is silently off
      forever. Found live when `past_calls` was off for every pre-existing
      meeting; backfilled once in `database.py`, but the next source group hits
      the same wall. The fix is to record what was OFFERED, not just what was
      enabled.

## Cut list — remove so the feature set stops reading as a bluff

- [ ] Drop Datadog, Notion, Linear connectors (nobody in the target personas
      needs them; keep Jira)
- [ ] Drop the learning-path feature
- [ ] Keep the Meridian demo corpus for sales demos only, never in a real
      workspace (already gated off by default)

## Next five, in order

1. [~] Whisper — private thread, suggestions, warnings and persistence all
       done and tested; what remains is a vendor account so a bot can join
       Meet/Teams/Zoom, or a native capture app.
2. [x] Suggested answers in that thread when the client asks something.
3. [ ] Real customer record — one CRM or help-desk source replacing the fixtures.
4. [ ] Ticket layer — Photon's work identity, tickets raised from calls.
5. [ ] Calendar add-on and scheduling page, so Photon's own meeting link is easy
       to send for speak mode.

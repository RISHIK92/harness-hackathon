# Photon — Company Brain + Live Call Support Agent

Layout note: this repo uses `client/` (frontend, was `apps/console` in the
original plan), `server/` (backend, was `services/brain-api`), and
`call-agent/` (was `services/call-agent/`). All plan references to
`services/brain-api` mean `server/`; `apps/console` means `client/`;
`services/call-agent` means `call-agent/`. `apps/call` (the join page)
lives at `client/app/call/`.

## LLM providers — text vs vision split (resolved the Gemini quota blocker)

`GEMINI_API_KEY`'s free tier turned out to be capped at **20 requests/DAY**
for `gemini-2.5-*` (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) —
a hard daily cap, not a per-minute one retries can wait out. Each agent
turn burns 2-4 LLM calls (plan + compose, up to 2 rounds), so this was a
real blocker for Phase 3.

**Fix**: all TEXT generation moved to OpenRouter (`app/core/llm/
openrouter.py`, model `deepseek/deepseek-v4-flash-0731`) — the agent
loop's plan/compose calls, `check_conflict`'s judge call, and the web
console's `/api/query` streaming (`llm_orchestrator.py`). Gemini
(`gemini_vision_model = gemini-3.7-flash`) is now reserved exclusively for
image/screen-frame analysis, once Phase 4 needs it — not used by any code
yet. Both `OPENROUTER_API_KEY` and the model id were verified live against
OpenRouter's `/models` list before wiring anything up.

**New known constraint**: OpenRouter's routing to this model has highly
variable latency in testing — most calls land in ~1s, but one single-call
`httpx.ReadTimeout` was directly observed at 60s+ with no error, just a
slow upstream response. This is a real latency risk for the live-call demo
(Section 17's "gemini-2.5-pro is too slow for a live call" gotcha applies
here too, just for a different reason). `sync_chat` (`app/core/llm/
openrouter.py`) now retries `ReadTimeout`/`ConnectTimeout` once (capped at
2 total attempts, so a stuck call fails in a bounded ~90s rather than
compounding with the 429/5xx retry budget) — a mitigation, not a fix. If
this proves frequent during Phase 4 rehearsal, the real fix is a paid/
dedicated OpenRouter tier or a different model, not more retry tuning.

## Current phase

**Phase A, Slice 4 — multi-repo disambiguation.** Phase 0 (environment) is
complete — the manual two-person LiveKit test from its checklist is
superseded by the Phase 4 live verification (worker joined a real room,
real Deepgram STT/TTS). Phases 1-5 are complete — see their sections
below. Phase A (login, workspaces, real repo ingestion) is in progress:
Slices 1-4 (tenant boundary, client auth, GitHub App, multi-repo
disambiguation) are done — see their sections below.

### Phase 5 — evidence panel (done)

The plan's Section 11 says to adapt `QueryPanel.tsx`/`CodeViewer.tsx` from
`apps/console` — but per the Phase 0 scoping decision, this repo's
`client/` was never seeded with YASML's original frontend (no `apps/console`
equivalent exists here), so this was built fresh, integrated directly into
`client/app/call/page.tsx` rather than ported from a component that
doesn't exist in this repo.

**The real gap this phase had to close first**: `POST /api/agent/ask`'s
Section 4 answer contract only carries `claims[].evidence_ids` and
`tool_trace` (tool/args/ms) — the actual evidence items (locator, snippet,
score) aren't in the response at all once composition discards the raw
tool results. Without them there's nothing for a panel to render. Fixed
in `server/app/agent/loop.py`: each `tool_trace` entry now also carries
its own `evidence: [...]` array — enriches an existing key rather than
adding a new top-level one, so the fixed 6-key answer contract is intact.

- `client/lib/evidence.ts` — shared types + `buildEvidenceMap()` (flattens
  every tool_trace entry's evidence into one `ev_id -> Evidence` lookup —
  the only place a citation chip's data comes from) + `findProvenanceChain()`
  (pulls `explain_why`'s evidence array as-is, since `provenance.py`
  already builds it in exact hop order: code → commit → pr → slack...).
- `client/app/call/EvidencePanel.tsx` — renders each turn's answer with
  `[ev_xxx]` markers replaced by clickable source-type-icon chips
  (clicking scrolls to and briefly highlights the matching evidence card,
  via a ref map + `scrollIntoView`); confidence/abstained/escalation
  badges; tool-trace pills (`tool · Nms`); the provenance strip (rendered
  only when an `explain_why` call produced >1 evidence item, connected
  hop-to-hop with arrows); and the evidence card grid (source icon,
  locator, snippet, score).
- `client/app/call/AccountSummary.tsx` — the idle state: calls
  `POST /api/tools/list_accounts` directly and renders all 3 known
  accounts as cards. (Simpler than the plan's "account summary for
  whoever's on the call" — there's no real caller-to-account identity
  mapping in this build, so it shows all known accounts rather than
  picking one.)
- `client/app/call/page.tsx` — restructured to a bounded two-pane layout
  (call controls left, evidence panel right, each independently
  scrollable) so the panel doesn't just grow the whole page.

**Live-verified in a real browser** (Chrome via the `claude-in-chrome`
tools), not just compiled: idle state shows real account data fetched
live from the brain-api; a submitted question shows a real "thinking…"
state; a real S2 question ("why does pricing have a special case for
Bangalore?") first hit the already-documented DeepSeek compose-JSON-parse
flakiness (correctly rendered as `confidence: low` / `abstained` with 20
real evidence cards, not a UI crash), then on retry produced a fully
grounded high-confidence answer with a working `code → commit` provenance
strip and real evidence cards pulled straight from the corpus (actual
Slack messages from Dev Sharma/Priya Nair/Alex Rao in `#pricing`, correct
locators/scores). Multi-turn stacking (asking a second question) also
confirmed working.

**Known gap, honestly**: this evidence panel only reflects the
text-input path. The voice path (`call-agent/orchestrator.py`) gets the
same `AgentAnswer` from the brain-api and speaks it via TTS, but doesn't
yet broadcast the structured result back into the room for the browser to
render — building that (LiveKit data-channel publish from the adapter,
`TransportAdapter` doesn't currently have a 4th method for it) is real
additional plumbing beyond "evidence panel" itself, and the plan's own
cut order ranks tight voice/UI sync low ("tool trace pills, live — nice,
not load-bearing"). Noting it here rather than silently leaving the
question unaddressed.

### Pre-flight dialog before the GitHub hand-off

The user asked for a screen explaining what's about to happen — especially
for the private-org case — shown BEFORE Connect GitHub leaves for GitHub,
with an explicit confirmation.

Rather than static instructions the user has to map onto their own
situation, it reads the App's real state: `GET /api/integrations/github/app`
returns slug/owner/permissions/installations straight from GitHub's `/app`.

**`org_install_supported` is derived from OWNER TYPE, not a `public`
flag** — GitHub's `/app` response does not reliably carry `public` (it came
back absent here), but it always sends the owner. A User-owned private App
can only be installed on that user's account, so `owner_type ==
"Organization"` is the honest signal.

`client/app/dashboard/ConnectGithubDialog.tsx` then says something
specific: with the current App it renders "This app is owned by @RISHIK92
(a personal account) and is private, so GitHub will only offer that
account as an install target", the two ways out (transfer to the org, or
make public) with a deep link to the settings page, and the note that
personal-account installs work right now regardless. Plus the four steps,
the actual permissions read live from the App, and a plain statement that
selected code is cloned, chunked, sent to the embedding provider, and
sent to the LLM at query time — gated behind an "I understand" checkbox
that disables Continue until ticked.

Live-verified in the browser against the real App.

**Also confirmed working in that same screenshot**: GitHub sign-in now
succeeds end to end — the signed-in workspace is
`86944435+rishik92's workspace`, i.e. the noreply-email fallback added
after the `/user/emails` 403 is what created the account. That 403 is a
GitHub App concept trap worth remembering: a GitHub App's user token
derives access from the App's PERMISSIONS, not from OAuth scopes, so
`scope=user:email` in the authorize URL buys nothing and the call fails
for any user with a private email — which is the GitHub default.

## Code panel audit — four bugs, three of them in the data

Asked to check the meeting UI's code reference: is the flow right, is the code
fresh or stale, does code appear when nothing about code was asked, and where
do the one-line `}` fragments come from.

### 1. It showed every chunk RETRIEVED, not the ones the answer cited

`collectCitedFiles` walked `tool_trace[].evidence[]` with no reference to the
answer. On a code-only workspace the fast path calls `search_code` on EVERY
turn (`_fast_path_calls`), so "what is our refund policy" still comes back
with eight weakly-matching code chunks — and the panel rendered all of them.
That is the whole of "code shown when the question was not about code", and
it is also how a junk chunk reached the screen: the model never cites those,
the panel was showing them anyway.

Now filtered to ids that appear in the answer's `[ev_xxx]` markers or in a
verified claim. Measured live:

```
"what does the drill controller do?"      retrieved 8  cited 3   -> shows 3
"what is our refund policy?"              retrieved 8  cited 0   -> shows none
"who approves a discount over 20%?"       retrieved 8  cited 0   -> shows none
```

### 2. The `}` fragments — root cause in the chunker

A symbol's range ends on its last statement, so the closing brace falls into
the leftover run BY ITSELF and became a real, embedded, retrievable chunk:

```
quizController.ts:L217-L218       '  }\n};\n'
homeGroupController.ts:L415-L416  '  }\n};\n'
```

The guard was `text.strip()`, which only rejects whitespace — and `}` is not
whitespace. It now counts alphanumerics (`MIN_ALPHANUMERIC_CHARS = 6`), which
rejects pure punctuation while keeping genuinely small real code: `def cli():
pass` has 10, `MAX_RETRIES = 4` has 11, `}` and `});` have 0.

Scale, measured across the live corpus: **27 of 61,304 chunks (0.04%)** — rare,
but they sit inside the controllers cited most often, so they surface next to
real hits. The same threshold is applied again at READ time (`tools/code.py`),
before reranking so a junk chunk cannot occupy one of the reranker's slots:
61k chunks are already embedded and re-ingesting every repo to evict 27 is not
worth it.

### 3. Stale chunks, and a fabricated locator still being cited

`drillController.ts` held **50 chunks** with duplicate ranges (`L298-L340`
twice, `L346-L387` twice) and one claiming **L1-L1315 while holding 72
lines**. That is a fabricated locator, which the standing rule forbids
outright, and the panel reads the file back BY THAT RANGE — so it displayed
1315 lines as "what the answer cited".

Cause: chunk ids are position-based (`{repo_id}:{path}:{idx}`), so a re-ingest
overwrites 0..n and ORPHANS everything past n — plus everything whose
boundaries moved, which is most of them whenever the chunker itself changes.
That survivor predates `_contiguous_runs` and had been retrieved and cited
ever since. (TODOS.md listed "re-ingest leaves stale chunks" as a known
defect; this is its user-visible consequence.)

Ingest now calls `delete_repo_chunks()` first, making a re-ingest
authoritative rather than additive. The cost is a window where the repo has no
chunks — a question asked mid-ingest gets "nothing found" rather than
stale-but-present code, which is the better failure: an honest gap beats a
confident citation of a file that no longer looks like that.
`_overclaims_its_range()` also rejects these at read time, so the ones already
embedded stop being cited without re-ingesting anything.

### 4. Merging + the chunk fallback produced wrong line numbers

The panel's docstring says it never renders `evidence.snippet`, because a
chunk numbered from the locator's start prints confident wrong line numbers —
and then the code did exactly that as a fallback. Worse in multi-repo mode:
`repo_id` is read off the tool call, and the loop deliberately does NOT set it
there (it passes `workspace_id` instead), so `readFile` never ran and the
fallback was ALWAYS taken.

It now merges adjacent regions only when the file can actually be read back.
Unmerged, each chunk's locator still describes exactly the text it holds, so
the numbers stay true even on the fallback path.

Eval after all of this: base **24/24** across all four columns. 106 server
tests.

## Citation markers: stored always, displayed never

The rule this build already followed in two places and had quietly broken in
three. `[ev_xxx]` markers stay in the STORED answer — they are the grounding
contract, the verifier checks them, and the evidence panel turns them into
clickable chips. Every surface that shows PROSE strips them: TTS
(`call-agent/speech.py`, since Phase 5), and now the whisper thread, the call
chat and a Slack DM, none of which have anywhere to put a chip.

**The real bug underneath**: `client/lib/evidence.ts`'s `CITATION_RE` was
`/\[(ev_[0-9a-f]+)\]/g` — single id only. The compose model routinely emits
`[ev_a, ev_b]`, which that pattern matches **not at all**, so those brackets
rendered literally even in the evidence panel that exists to chip them. This
is the exact bug `verifier.py`'s `_MARKER_RE` had and had fixed server-side;
the client copy was never updated. It now matches the id LIST, and the panel
renders one chip per id — a single chip for two sources would hide one.

`stripCitations()` (client) and `strip_citations()` (whisper's Slack surface)
are the display-side equivalents of `for_speech()`. All three eat the space
before the bracket, so "platform [ev_x]." closes up to "platform." rather
than leaving "platform ." — and all three leave a non-citation bracket alone,
so "The field [webhook_url] is empty" is untouched.

Six tests cover it, including the one that matters most: the STORED message
still has its markers. Stripping leaking into storage would break the chips,
the verifier, and any later audit of what was actually cited.

## Whisper on the call itself — listening, and a side panel

Treating the Photon call as a stand-in for the client's own Meet/Zoom: Photon
listens over your shoulder instead of sitting in the meeting, and everything
it says lands in a panel only you can see.

**What gets captured is everyone EXCEPT you and Photon**, and the caption
stream already carries that distinction exactly, so the rule is not a
heuristic:

```ts
c.final && !c.isLocal && c.speaker !== "agent"
```

`isLocal` is you. `speaker === "agent"` is Photon — `CaptionsBridge` resolves
an undefined participant to the agent, because its own TTS transcript arrives
with no participant attached. What is left is the other side of the
conversation, which for whisper's purposes is the client.

Three details that are not obvious:

- **Only `final` segments.** LiveKit re-emits a segment under the same id as
  it grows (interim, interim, final); answering an interim is answering half a
  sentence, which is the endpointing bug in a different costume.
- **Deduped by caption id.** Without it one sentence is forwarded several
  times and answered several times — React re-renders on top of LiveKit's
  re-emission, so this is not a rare race.
- **`heardCount` is shown in the panel header.** "Listening" with no counter
  looks identical to a silently broken bridge, which is precisely the failure
  this feature had on its first live webhook run.

The panel is a tab beside Ask / Chat / Code rather than a second tab of the
browser: a rep watching a customer's face does not also have a spare window.
Suggestions render with citation markers stripped and the warning FIRST —
"✅ safe to say as-is" or "⚠️ Internal source only" — because the warning is
what decides whether the next sentence out of their mouth is safe.

**Verified end to end** with the exact payload the bridge sends:

```
heard: yeah                              -> no suggestion (backchannel)
heard: is there an AI tutor in this?     -> suggested
heard: and how does it pick questions?   -> suggested   (follow-up resolved)

  💬 Yeah, it's got Ace, an AI chat tutor that helps interpret student
     performance and guides them through next steps.     ✅ safe to say as-is
```

**The real limitation, unchanged and worth repeating**: LiveKit's
`AgentSession` transcribes ONE linked participant at a time
(`set_participant`, driven by poke). So on a multi-party call this hears
whoever is currently linked, not the whole room — the same constraint the
shared transcript has. For a one-to-one call with a client it is complete;
for a room of five it is not, and a genuine full-room whisper needs an STT
stream per participant or the vendor path.

## Whisper mode (Section D) — 5 of 7, and the two that need an account

Speak mode puts Photon in the room. Whisper keeps it out: the client hears
nothing, and every suggestion lands in a private thread belonging to ONE
workspace member.

**The transcript is source-agnostic, which is the design decision the whole
thing rests on.** Everything ends up POSTing the same line shape to
`/api/whisper/sessions/{id}/lines` — a meeting-bot vendor, a browser
extension, a desktop capture, the paste box in the UI, and Photon's own
worker. That is what stops whisper being welded to one vendor, and it means
**it works today with no external account at all**: `routers/meetings.py`
feeds a live whisper session bound to a Photon meeting, so a real call
already whispers.

### What is built

- `WhisperSession` / `WhisperLine` / `WhisperThread` / `WhisperMessage`.
  Deliberately NOT the same table as `Meeting`: a Meeting is Photon's own
  call (LiveKit room, slug, waiting room), while a whisper session is usually
  somebody else's meeting on somebody else's platform, where we have a
  transcript and nothing else.
- **Privacy is a row, not a UI choice.** A thread has a `user_id` and every
  read re-checks it against the caller's session. The check is not "is a
  member of this workspace" — a COLLEAGUE must not read your thread either,
  and a test asserts exactly that, with 404 rather than 403 so an id is never
  confirmed to exist.
- **The suggestion gate is looser than speak mode's, on purpose.** On a live
  call a wrong answer is heard by the customer; here a useless suggestion
  costs a glance, while a missed one costs the thing whisper is for. So it
  fires on question marks, interrogatives AND implicit asks ("we need SSO",
  "it's still broken", "send me the docs"), and skips only backchannel.
  Firing on every line would be one LLM turn per sentence of a meeting.
- **`is_client` is the other gate**, and it defaults to False everywhere it
  is unknown: answering your own colleague unprompted in front of a customer
  is worse than missing a suggestion. On Photon's own calls it is derived
  from `speaker_user_id`, which comes from a signed token.
- **Warnings** (`services/whisper/warnings.py`) are all derived from the
  answer contract — internal-only sources, mixed sources, low confidence,
  abstention, escalation — never inferred from the prose. `is_safe_to_read_aloud()`
  is the one-line version. The Slack case is the sharp one: the Bangalore
  pricing rationale is true, cited, and names a partner and a commission,
  so it is exactly what must not be read to a customer verbatim.
- Threads persist by construction: ending a session stops ingest (409 on
  further lines) and leaves every message in place.

**Verified end to end against the live stack**, in the workspace that has
real sources:

```
[us    ] thanks for making time today
[CLIENT] yeah no problem                              (backchannel — nothing)
[CLIENT] why do the webhook retries stop so early?    -> 1 thread
[us    ] good question, let me check                  (ours — nothing)
[CLIENT] is there an AI tutor in the product?         -> 1 thread

  💬 They're hardcoded to three tries [ev_e2dd8f0a] with a doubling delay
     starting at a second [ev_75c0b801], so it tops out pretty fast.
     evidence=9 (code)  ✅ no warnings — safe to say as-is
```

A workspace with nothing connected produces zero suggestions rather than a
guess — same rule as refusing to join a call with no sources.

### The vendor path, finished except for the key

`POST /api/whisper/sessions/{id}/bot` dispatches a bot;
`POST /api/whisper/webhooks/{session_id}/{secret}` receives the transcript.
A vendor-shaped event was driven end to end into a real cited suggestion, so
what remains for Meet/Zoom is a key and a public URL — not code.

- **The webhook is authenticated by a per-session secret in the path**, not a
  deployment-wide one. It is a bearer token in a URL, with the usual
  consequence that it can land in an access log — so it is scoped to ONE
  meeting and dies with that session, rather than being a standing key to
  every future call. Compared with `compare_digest`. A wrong secret and an
  unknown session both return 404, since distinct answers would make this an
  oracle for valid session ids. It is deliberately absent from
  `WhisperSessionRead`: a credential that rides along with every list
  response ends up in logs and screenshots, so it has its own endpoint.
- **Dispatch returns 501, not 500, when no vendor is configured** — nothing is
  broken, the capability is not set up, and the message names the env var.
- The webhook takes a vendor event OR a plain `{speaker_name, text}`, because
  the same URL is what you hand a hand-rolled capture script.

### Who is the client — and the bug the first live run found

The suggestion gate hangs entirely on `is_client`, and the two paths resolve
it in OPPOSITE directions on purpose. On Photon's own call `speaker_user_id`
comes from a token this API signed, so membership is authoritative. Anywhere
else there is a display name and nothing more, so the default inverts: a
speaker is the client UNLESS they match a workspace member, matched on email
and on the punctuation-stripped local part ("priya.nair@acme.com" → "Priya
Nair"). Never a substring test in either direction — "Sam" matching
"samantha@…" would silently swallow a real client's questions.

The inversion is only safe because of where a suggestion lands: a false
positive costs one unnecessary line in a private thread nobody else can see,
while a false negative costs whisper doing nothing on the calls it exists
for. It would not be safe if suggestions were spoken aloud, which is exactly
why speak mode's gate is the strict one.

**The bug**: `lines_from_webhook` set `is_client=False` whenever the vendor
sent no host/external flag — which looks like the cautious choice and is the
broken one. Most vendors send no flag, so every participant resolved as "us"
and a Meet call fired ZERO suggestions while the transcript flowed
perfectly — a silent failure with no symptom but absence. Caught on the first
live webhook run, not by reading the code. It now returns None and defers to
the name resolver:

```
86944435+rishik92    us     | thanks for joining              -> 0 threads
Dana Whitfield       CLIENT | yeah                            -> 0 threads  (backchannel)
Dana Whitfield       CLIENT | is there an AI tutor in this    -> 1 thread

  💬 Yeah, it's got an AI tutor named Ace [ev_54285641] that handles coaching
     and performance analysis.   confidence=high  evidence=16  no warnings
```

### What is NOT built, and why

- **Bot joins Meet/Teams/Zoom.** Needs a vendor (the adapter is written
  against Recall.ai because it covers all three with one integration) plus a
  webhook URL the vendor can reach; this deployment binds to localhost. It
  fails loudly with `BotProviderUnavailable` rather than returning a fake
  session id — a session that silently never receives a transcript looks
  exactly like a meeting where nobody spoke. `lines_from_webhook()` is a pure
  function precisely so the parsing, which is the part that will be wrong,
  is testable with no account.
- **Desktop capture** — "no bot visible in the client's meeting" cannot be
  done with a bot at all, by definition: a bot that joins a Meet call IS in
  the participant list. It needs capture on the rep's own machine, i.e. a
  native app this repo does not have.
- **Slack surface** is written and DMs the member by verified EMAIL (two
  people share a display name far more often than an email, and whispering
  one rep's thread to another is the failure this must not have) — but it
  needs `chat:write`, which the Slack app deliberately does not request. The
  connection being read-only is a property a reviewer approving the app can
  check, so enabling this has to be a deliberate re-install, not a silent
  upgrade. It reports exactly that instead of failing obscurely.
- **"Not on the roadmap"**, one of the TODO's own warning examples, is not
  implemented and should not be faked: the evidence item carries source_type,
  locator, snippet and score but not an issue's STATE, so a Jira hit cannot
  be told apart from shipped, in-progress or rejected. Every other warning is
  derived; this one would be a guess, and a warning that is itself a guess
  trains people to ignore the label. Recorded as a known defect — it needs
  `status` on connector evidence first.

14 whisper tests (85 server, 141 call-agent in total); `/whisper` serves 200.

## Human sounds and "checking, one moment" — at the speech layer

User request: thinking sounds ("ah", "hmm", "mmm"), a spoken "checking, please
wait" once a turn passes ~2s, and a beat after it so the answer does not tread
on its heels.

**The first of those is the thing that was measured as harmful** — licensing
the model to open with "Ah"/"Yeah" regressed accuracy 5/5, because a
conversational opener is chosen BEFORE the evidence is weighed and the rest of
the sentence gets written to justify it. So it is added in a different place:
`speech.with_thinking_sound()` glues the sound onto the string handed to
`adapter.speak()`, after the answer has been composed, verified and cited.
**The verdict is already fixed by then and nothing here can reach back and
change it.** The published answer and the transcript keep the clean text; only
the spoken copy carries the sound.

- The rotation contains EMPTY slots on purpose — a thinking sound on every
  single turn is its own tic, which is the problem it was added to solve. It
  lands on five turns in eight.
- It is skipped when the answer already opens with a particle ("No, …",
  "Yeah, …"), so "Hmm, no, I'm not seeing…" never happens.
- Indic languages get real spoken particles (`అలాగే`, `சரி`, `अच्छा`) rather
  than a transliterated "hmm": TTS handles a real word far more reliably than
  an onomatopoeic one, and a mangled filler is worse than none. **Untested
  against live Sarvam audio** — worth one listen before a demo.

**The filler**: `ACK_AFTER_SECONDS` is 1.5 and the wording says what it is
doing ("Hmm, checking — one moment", "Let me check that, please wait").

**The beat after it** (`POST_FILLER_PAUSE_SECONDS`, 1.0) is measured from when
the filler was SPOKEN, not from when the answer arrived — and that detail is
worth the extra state. A flat sleep-on-arrival delayed every answer by a
second including ones that came back long after the filler and already had
their gap; measured end to end through the real orchestrator, answers landed
at 4.2-5.6s. Relative to the filler they land at **3.0-3.5s** with the gap
intact.

```
 2.01s  Hmm, checking — one moment.
 3.54s  Hmm, it runs the booking and scheduling backend for Meridian…

 2.01s  Mmm, one sec while I look.
 3.48s  No, I'm not seeing any sign of it — the codebase has a notification
        service and webhooks, but no email handling.     <- no stacked sound
```

**Worth knowing**: a normal turn is ~2.5-3.5s, so at a 1.5s threshold the
filler fires on essentially every turn. That is deliberate — the silence is
what reads as a missed question — but if it proves chatty on a real call,
`AGENT_ACK_AFTER_SECONDS` is the dial (and `0` switches it off entirely). None of this touches the brain-api, so the agent eval is
unaffected by construction — no prompt changed.

## Planner JSON — a grammar, not a regex; and the prompt properly structured

### The eval's variance was one bug

Three eval runs scored 20, 21 and 23 out of 24, and the score tracked ONE
number: how often the planner emitted almost-valid JSON. Collected verbatim
from the logs, every failure had the same shape — a valid `calls` array with a
stray token welded on where the closing brace belonged:

```
…"webhook retry"}}]}}          …"rotate secret"}}]5}
…"webhook retry"}}] K}         …"webhook retry"}}]absolut}
…"annual plan refund"}}]uretics}    …"webhook retry"}}]      (truncated)
```

The turn then abstained with `tools=-` on a question it could answer, which
from the outside is indistinguishable from a retrieval failure.

**`json_mode` only ASKS for JSON; `response_format: json_schema` constrains
DECODING to a grammar.** The model advertises `structured_outputs`, so this is
available. Measured on the real plan prompt, 32 calls per arm:

```
json_object   malformed 2/32
json_schema   malformed 0/32
```

**`strict` is deliberately `False`.** Strict mode cannot express the planner's
free-form `args` — every tool takes different arguments — and a strict schema
emitted `"args": {}` on every call: syntactically perfect, semantically empty,
which is worse than malformed. Verified against the live endpoint before
choosing the shape.

The schema also fixed a second bug the measurement exposed by accident:
`json_object` sometimes omitted `args` entirely and hoisted the argument to the
top level (`{"tool": "search_docs", "query": …}`). The grammar makes that
impossible.

**`extract_json` is now the net under it rather than the fix.** The old greedy
`{.*}` regex ran to the LAST brace in the string and so swallowed exactly the
junk it needed to drop; it is replaced by a balanced scan that tracks strings
and escapes, plus a salvage path that recovers the `calls` array on its own.
That still earns its place because a grammar cannot prevent the one remaining
case — output truncated at `max_tokens`. Nine tests, built from the verbatim
failures above, including the two things it must NOT do: a brace inside a value
must not end the scan, and genuine nonsense must still return `None` rather
than invent a plan.

**Result across three runs**: `ALL PASS` **24/24, 24/24, 24/24**; JSON failures
0, 0, 2 (was 4-8); empty-plan retries 3, 3, 5 (was 6-7). Latency cost of
constrained decoding, measured with interleaved arms so provider drift hits
both equally: **+52ms median** on the plan call (1257 -> 1309ms) — so the
~300ms swing in eval medians is the usual OpenRouter variance, not the grammar.

**What is left, and why it is not worth more machinery**: the 2 remaining
failures in the third run were not malformed at all — the provider returned a
bare `{` and stopped. There is nothing to repair, the existing empty-plan retry
absorbs it, and all three runs scored 24/24 anyway. It is now logged as
`agent.llm_empty_generation` rather than as a parse failure, so the next person
reading the logs is not sent after a parsing bug that does not exist. Not
reproducible on demand (0/10 when probed directly).

### Structuring the prompt properly

The rules had grown by accretion, each carrying its measurement inline
("Measured: told simply to 'start with yes or no', this model opened with
'Yeah' … 6 times out of 6"). That is ~830 characters of meta-commentary shipped
to the model on every plan and compose call, on the critical path of a live
call — and it is a fact about this repo, not an instruction.

The measurements moved into a comment block above `_RULES`; the prompt kept
five clean sections (`# ROLE & OBJECTIVE`, `# GROUNDING RULES`,
`# PERSONALITY & TONE`, `# HOW TO ANSWER`, `# NEVER`). **1078 -> 706 tokens,
35% smaller, with every instruction preserved.**

**One thing the trim broke, and it is the interesting part.** The affirmation
guard went from 0/12 to **2/12** wrongly affirmed. The load-bearing text was
not the measurement provenance — it was the concrete COUNTER-EXAMPLE inside
it. Restoring just that, in instruction form rather than as a war story
("asked whether it sends email, do not answer about the video upload it found
instead"), put it back to **0/12**.

That matches what OpenAI's realtime guide says about sample phrases being
followed closely, and it is a useful rule for this prompt: **keep the example,
drop the provenance.** A rule stated abstractly is weaker than the same rule
with one concrete instance of getting it wrong.

Verified after the restructure: `agent_eval` base **24/24**, affirmation guard
**0/12**, and a 10-turn conversation with pronouns resolving across turns, no
canned closers, and openers that actually vary ("Nothing on pricing or billing
in the codebase" rather than a fourth "No, I'm not seeing any…"). 71 server
tests, 136 call-agent tests.

## Borrowing from the other voice agents — three landed, one was poison

Four ideas taken from the prompt structures surveyed above. Three shipped; the
one every guide recommends most loudly had to be thrown away, and that is the
part worth reading.

### 1. The transcript is a rough draft (shipped)

LiveKit's example puts it as "treat each transcript as a rough draft of what
was said — it may contain speech-to-text errors, so don't mention the
transcript or repeat its mistakes". Photon had nothing of the kind, despite a
documented live failure where Sarvam heard `ఇందుకు` for `ఎందుకు` and turned a
question into a statement. It now says to answer the INTENT when a word is
clearly wrong, never to quote the transcript back, and to ask for a repeat
only when the mis-hearing is what actually blocks the answer.

Verified with deliberately mis-transcribed questions — the shapes real ASR
produces, not typos:

```
"does it sent any emales anywhere"          -> answered about email, correctly, as "no"
"what about back ground jobs is there a cue" -> answered about the queue (BullMQ on Redis)
```

No "did you mean", no repeating "emales" back.

### 2. Vary the WORDING, never the VERDICT (shipped, after it backfired)

OpenAI's guide says to "add a Variety rule to reduce robotic phrasing", and
this build had earned one: banning the canned closing offer just grew a new
tic, with 6 of 6 answers opening on the identical phrase.

The plain version of that rule was actively dangerous. Measured on a question
whose honest answer is "no" (the repo has no mail library at all):

```
"vary your phrasing"        5/5 wrongly affirmed
same prompt, rule removed   0/5
```

**It varied the POLARITY.** The correct answer's natural opening — "No, I'm
not seeing any…" — is itself a repeated phrase, so an instruction to avoid
repeated phrasing pushed the model off it, and the nearest alternative opening
is "Yeah". This is the third time in this build that a purely stylistic
instruction has moved a verdict (after "start with yes or no", and the opener
licence below); the shape is always the same — anything that decides how a
sentence STARTS ends up deciding what it says.

The rule now separates the two explicitly: vary the wording, but "no" stays
"no" and "yes" stays "yes" however it is phrased. Back to 0/6.

### 3. Licensed disfluency (REVERTED — do not re-add)

This is the one every guide pushes hardest. LiveKit: "start responses with
real reactions (oh, hmm, ah) and fillers (um, uh, like)". OpenAI's metaprompt
has a Filler Words dial with four settings. Adding the equivalent here put the
email→YouTube bug straight back, and warning against affirming INSIDE the same
bullet did not save it.

The asymmetry is the lesson, and it is now recorded above `_RULES` so nobody
re-adds it: **those guides are written for task-flow agents** — take a
drive-thru order, book a table — where affirming is usually correct and a
stray "yeah" costs nothing. For a grounded answering agent the same warmth is
paid for in wrong answers. Register is still worth having here, but it is
bought with BANS on stiffness (no brochure verbs, no canned offers, no
"certainly") rather than with LICENCES to be chatty. Those measured clean.

### 4. No dead air (shipped)

"Before a tool call that takes a moment, give a brief spoken acknowledgment
first so there's no dead air." A turn is 2.1-2.9s of complete silence, which
on a call reads as the agent having missed the question — so people repeat
themselves and then talk over the answer.

`Orchestrator._filler_if_slow` speaks after `ACK_AFTER_SECONDS` (1.5s, set
`AGENT_ACK_AFTER_SECONDS=0` to disable). Details that matter:

- It **waits on the answer** rather than sleeping blindly, so a fast turn
  cancels it before it ever speaks, plus an `is_set()` check after the timeout
  to close the last sliver of race — a "one sec" spoken for a question that
  has already been answered is worse than the silence.
- It does **not** delay the answer: the filler is spoken while the brain-api
  call is still in flight.
- The vision path is excluded, because it already says something before its
  extra ~1.3s of work.
- New `LOOKUP_FILLERS` rather than reusing `ACKNOWLEDGEMENTS`, which contains
  "Sure, checking your screen" — true on a screen-share turn and a small lie
  on any other, which is exactly the class of thing the grounding rules exist
  to prevent. A test asserts no lookup filler mentions a screen.
- Rotates like the visual one, for the reason this whole section is about.

### What the measuring was worth

Three of these four changes were dangerous on first draft, and none of that
was visible by reading the output — every bad answer was fluent, cited and
confident. Two process notes:

- **An n=5 ablation is not a result.** Several of these ran 5/5 vs 0/5 (real),
  and others 2/6 vs 0/6 (noise I briefly believed). A `# FINAL CHECK` block
  added on the strength of one such reading measured 2/6 against 0/6 without
  it and was removed again.
- **Measure on the path the product actually runs.** An early harness omitted
  `bot_types`, so it exercised a compose prompt with no persona block — which
  no live call and no `/dev/ask` request ever gets. It disagreed with the real
  path and sent me chasing the persona as a culprit it turned out not to be.

Final state, n=12 on the shipped configuration with the persona applied:
**0/12 wrongly affirmed**. `agent_eval` base **24/24** (tools, content,
abstain and cites all 24/24) — the best run this build has recorded, on a run
with a mid-range 5 planner-JSON flakes, so not a quiet-run artefact. 62 server
tests, 136 call-agent tests.

## Prompt structure — labelled sections, and pronunciations as code

Taken from a read of how the well-known voice agents structure their prompts:
OpenAI's realtime guide (eight labelled sections: Role & Objective,
Personality & Tone, Context, Reference Pronunciations, Tools,
Instructions/Rules, Conversation Flow, Safety & Escalation), the
`openai-realtime-agents` metaprompt (Personality and Tone, with explicit
Filler Words and Pacing dials, then Instructions, then Conversation States),
LiveKit's own `examples/drive_thru` (Outcome / Voice & personality / How to
work / Hard constraints, marked with comments in the source), ElevenLabs
(Personality, Environment, Tone, Goal, Guardrails, Tools), and — for the
grounded-answering half — Perplexica and Onyx.

Photon's prompts were unlabelled prose plus a bullet list that had grown by
accretion. They now carry the same kind of headers: `# ROLE & OBJECTIVE`,
`# GROUNDING RULES`, `# PERSONALITY & TONE`, `# HOW TO ANSWER`, then
`# TOOLS` / `# KNOWN CUSTOMER ACCOUNTS` / `# QUESTION` /
`# HOW TO CHOOSE TOOLS` / `# OUTPUT` in the plan prompt and
`# QUESTION` / `# EVIDENCE` / `# OUTPUT` / `# EXAMPLES` in compose. The change
is content-preserving — the same sentences, grouped and labelled — so that any
movement could be attributed to structure alone.

**It did not move anything measurable, and the measurement is the point.**
Three `agent_eval` base runs:

```
                     ALL PASS   malformed plan JSON   empty first-round plans
before (tone work)     21/24            4                      6
after sections         23/24            4                      6
after sections         20/24            8                      7
```

The score tracks the PLANNER'S JSON failures, not the prompt structure. A run
where the planner emitted 8 unparseable plans scored 20; one with 4 scored 23;
the pre-change run with 4 scored 21. Every one of the failing cases in the
worst run shows `tools=-` — no tools ran at all, because the plan came back
empty or malformed (`{"calls": [...]}5}` and similar), so the agent abstained
on questions it can otherwise answer.

So: **the headers are kept for legibility, not because they were shown to
help** — and the honest read of this eval is that at 3 trials per case it has
a ±3 noise band, dominated by planner JSON reliability. That is now the
biggest lever on this number, and it is a decoding/parsing problem, not a
prompt-wording one.

### Reference Pronunciations live in `call-agent/speech.py`, not the prompt

Every guide above puts pronunciation guidance IN the prompt. That is right for
them and wrong here, for a structural reason: those are speech-to-speech
models, where the thing that writes the words is the thing that says them.
Photon composes text once and uses it twice — the browser renders it with
citation chips, and TTS reads it aloud. A prompt rule like "say four oh one
instead of 401" would change the displayed answer too, break `claims[].text`
as a verbatim substring of it, and break an eval case that asserts on the
literal string `401`. (`evals/agent_eval.py` really does: `need=[["401",
"unauthor"], …]`.)

`for_speech()` already existed as exactly this seam — it is where citation
markers are stripped — so the shaping goes there. It is deterministic, costs
no tokens, and cannot touch the published answer:

```
published:  It's handled by `src/controllers/drillController.ts` [ev_f852ac0c], but …
spoken:     It's handled by drill Controller, but …
```

Handled: backticks and markdown emphasis, `SCREAMING_SNAKE` and `snake_case`
(underscores are word breaks), `camelCase`, an acronym glued to a word
(`HTTPError` matches no camelCase pattern — there is no lowercase letter after
the first capital — so it needs its own pass), and path-like tokens with an
optional `:L42-L58` range, which collapse to the thing they name rather than
being read as "s r c slash controllers slash drill controller dot t s".

**Numbers are deliberately left alone.** Every TTS says "401" and "3 retries"
correctly, and rewriting them would only create a way for the spoken and
displayed answers to disagree.

Nine tests in `call-agent/tests/test_speech.py` (130 in that suite). One
existing test had to change: `test_unrelated_brackets_are_left_alone` asserted
`[webhook_url]` survived verbatim. Its intent — that a non-citation bracket is
not stripped — still holds and is still asserted; the underscore inside it is
now spoken as a space, which is the identifier being made sayable rather than
the bracket being touched.

**Not adopted**: OpenAI's Conversation States state machine, which is built for
task flows (take an order, book a table) rather than open support Q&A; and
everything Perplexica does about formatting, since it is a written answer
engine optimising for the opposite of voice — markdown headings, 2000-word
depth.

## Tone — it answered like a search result, not like someone on the call

The user's read was that the system prompt was too strict and every turn felt
like a Q&A bot. Driving a real conversation through `/dev/ask` made the tells
concrete, and both were things the prompt was explicitly asking for:

```
YOU:   what does this codebase do?
AGENT: Adventa is an exam-prep platform for competitive exams like JEE Main…
YOU:   what database does it use?
AGENT: Adventa uses Postgres via Prisma ORM with pgvector for semantic
       question search. Would you like more details on the database schema?
```

1. **It restated the subject's name every single turn.** Nobody in a
   conversation says "Adventa uses…" twice in a row; they say "it uses…".
   Nothing in the prompt had ever told it there WAS a conversation.
2. **Every answer ended with "Would you like more details?"** — which is
   exactly what the old rule "offer more detail rather than dumping everything
   you found" asks for, verbatim, on every turn.

The voice rules now cover register (contractions, a colleague's words), the
continuation ("once the subject is established, refer back to it the way a
person would"), a ban on the canned closing offer, and a ban on brochure verbs
("features", "provides", "supports", "handles" — "There's an AI tutor called
Ace" beats "It features an AI tutor named Ace"). The few-shots were rewritten
too, with a note that they are the TONE to match: few-shots move register far
more than rules do, and the old ones were clipped and written, not spoken. One
of them is now a FOLLOW-UP answer, because that shape had no example at all.

### The accuracy bug this nearly shipped

An early draft added "if they asked a yes/no question, start with yes or no".
Asked *"does it send emails anywhere?"* against a repo with **no mail library
and no `sendMail` anywhere** — verified in the clone, not inferred — it said:

> "Yeah, it sends them to YouTube."

The rule pushed it to commit to "yes" and then reach for the nearest retrieved
thing. Measured rather than eyeballed, 6 runs per arm against that ground
truth:

```
                              wrongly affirmed
with "start with yes or no"        6/6
without it                         3/6      <- the real bug is underneath
after the fix below                0/6
```

Removing the rule was not enough, which is the useful part: **the underlying
failure is pre-existing and belongs to compose, not to tone.** These tools
retrieve by similarity, so a question about something that does not exist
still returns the closest thing that does — ask about email, get the file that
uploads videos — and the model treated "six results came back" as evidence the
answer was yes. The compose prompt now says that outright, with a worked
example of the right answer when the thing asked about simply is not there
(say so, and mention the adjacent thing as an aside rather than as the
answer). The yes/no rule survives only in the form that names "no" and "I'm
not seeing any sign of it" as answers in their own right.

After: *"No, I'm not seeing any sign of emails sent anywhere. It does push
video outputs to YouTube, but there's nothing about email in the codebase."*

### Cost

`evals/agent_eval.py` base: **21/24, tools 23/24, content 21/24, abstain
21/24, cites 24/24** — identical in every column to the run before the tone
work, with the same `calico-rate` case abstaining (2 of 3, as before) plus one
planner-JSON flake (`}]5}`, the documented pre-existing issue). So the register
changed and nothing measurable was traded for it.

Checked over a 10-turn conversation, not just a pair: pronouns resolve across
turns, "why would they need a queue for that?" gets a causal answer rather
than a definition, and "you mentioned pgvector earlier" resolves correctly
**seven turns later** — which is conversation memory and the tone work paying
off together.

**Two nits left, not fixed**: a question like "where does the data for that
come from?" was read as *training* data and abstained where "Postgres, via
generated SQL" was available; and the model still reaches for "handles"
occasionally despite the ban. Both are mild, and tightening the prompt further
risks the over-affirmation this section is about.

## `/dev/ask` — asking as a real user, without a token

`POST /api/agent/ask` takes a client-asserted `workspace_id` and nothing
else, so exercising it by hand meant pasting a workspace uuid out of
Postgres — and even then it tested a configuration no real caller has, since
a real call's persona and `allowed_tools` come from the workspace's connected
sources, not from the payload.

`server/app/routers/dev_ask.py` resolves a person the way the product does
(email -> workspaces -> that workspace's real source groups -> the exact tool
list a call would get) and then calls the same `answer_question()` as
everything else. Nothing is special-cased.

```
GET  /dev/ask/whoami?as_email=…      who it would act as, and what that agent can reach
POST /dev/ask                        {question, as_email?, workspace?, session?, meeting_slug?}
DELETE /dev/ask/sessions             forget a stored conversation
```

- **It impersonates a user with no password and no token.** That is the point
  and the risk, so it is mounted only when `app_env != "production"`, the same
  gate as the other two `/dev` routers. `tests/test_dev_ask_gating.py` is a
  static AST check that the mount stays inside that block — it fails on the
  edit that would actually cause the problem (someone tidying the line out of
  the guard), which no runtime test in a dev environment would catch.
- **`session` keeps history server-side**, so a follow-up is its own curl
  instead of resending the conversation. In-memory and lost on reload,
  deliberately not a table: a real call's history comes from the worker or the
  browser, never from here.
- **`whoami` exists because nearly every confusing answer is really "it
  resolved a different workspace than you meant" or "that source isn't
  connected, so the tool was never offered to the planner"** — both invisible
  in the answer, both obvious there.
- Ambiguity is refused, not guessed: with several accounts it lists them
  rather than picking one, since picking wrong means answering from another
  tenant's sources. It hides the ~118 `@example.test` / `@example.com`
  accounts the test suites have left on this box (filter only — never
  auto-selected against), and `DEV_ASK_EMAIL` in `server/.env` skips the
  question entirely.

### The defect it found in its first real use

Asked `"what did we discuss about Ace on the last call?"` against a real
meeting, the agent answered *"I don't have records of our past calls"* — on a
workspace with six transcribed calls. `search_past_calls` was never offered.

**`Meeting.enabled_sources` is a frozen snapshot of the source toggles as they
stood when the call was set up**, and null (meaning "use the defaults") is not
what existing rows hold — they hold `["github", "custom_docs"]`. A source
group added later can therefore never appear in one, so cross-call memory was
silently off for **every call created before it shipped**, one commit after
shipping it.

Backfilled with a one-time idempotent data migration in `database.py` (the
only data migration there — the rest are `ALTER TABLE`), verified against the
real rows and re-run to confirm idempotency. Appending is right *specifically*
because the group is default-on and was never OFFERED to those calls: nobody
disabled it, it did not exist. **That reasoning does not generalise to an
opt-in source**, and a stored list of enabled keys still cannot distinguish
"turned off" from "did not exist yet" — so the next source group hits the same
wall. Recorded in TODOS.md's known defects; the real fix is to record what was
offered, not only what was enabled.

After the fix, the same question answers correctly and cites
`call:uhp2-3pqa:2026-09-12T15:18:00Z`, which matches the transcript row in
Postgres exactly.

**Noted, not fixed**: those transcript rows carry `speaker_name` as a raw
LiveKit identity (`user:5f027db4-…`) rather than a name, because nobody poked
on those calls and the orchestrator falls back to the identity. The snippet is
honest but noisy, and it is pre-existing data, not something this introduced.

## Memory — within a call, and across calls

Both of Section A's open TODOs. They sound like one feature and are two, with
nothing in common but the word: one is pronoun resolution on the critical path
of a live turn, the other is a retrieval source with a citation contract.

### Within a call — `server/app/agent/history.py`

`answer_question()` took a bare question string, so a follow-up had no
referent at all. "Why is that?" reached the planner as three words with no
subject, and on the fast path those same three words went to `search_code` as
a semantic query. The call-agent kept a transcript (`TurnState.transcript`) the
whole time and **nothing ever read it** — that was the entire bug.

**The split that matters**, because conflating the two halves is every way
this can go wrong:

| | what it is | who consumes it |
|---|---|---|
| `question` | what the caller actually said | the LLM, with the history block in front of it |
| `retrieval_query` | a string that stands on its own | the search TOOLS |

The model resolves the reference better than any regex, so it gets the raw
question plus the conversation and is told to work it out. The tools cannot —
an embedding of "why is that" matches nothing — and they are reached with no
LLM in between on the fast path, so `resolve_query()` is **deterministic and
0ms**: it prepends the last caller question when the utterance looks like a
follow-up. Concatenation, not a grammatical rewrite: `"why does pricing have a
special case for Bangalore why is that?"` embeds essentially where the tidy
sentence does, and an LLM rewrite would hand back the ~1.7s the fast path
exists to save.

Detection is allowed to be wrong in one direction only. A false positive makes
a search query noisier; a false negative is the bug itself. So it fires on
anaphora in a short utterance, on elliptical openers ("and Northwind?", "what
about the other one"), and on any very short interrogative — but deliberately
**not** on a complete question that merely opens with "why", which would blur
a perfectly good retrieval.

**Citation markers are stripped out of remembered agent text**, both in the
worker and again server-side. An `[ev_xxx]` id is valid only for the turn that
produced it; left in memory the compose model cheerfully reuses it the next
turn, where it names nothing, and `verifier.py` then strips a correct claim as
uncited — a right answer destroyed by its own memory.

**Verified live** against the running stack, on the seed corpus, and it is the
clearest before/after in this build:

```
Q1  why does pricing have a special case for Bangalore?
    -> partner commission rate agreed with BLR Mobility Partners [ev_80df968c]

Q2  who decided that?      WITH history  -> Priya Nair, Head of Partnerships,
                                            negotiated the BLR deal with Legal
Q2  who decided that?      NO history    -> "CEO Sam Okafor decided that"
```

Both cite real Slack evidence and both read as confident. The second is an
answer to a different question — which is what "no referent" looks like from
the outside, and why this was never a cosmetic gap.

History is caller-supplied (worker: `TurnState.history()`, browser: its own
turn list) rather than read back from the transcript table, because the text
console writes no transcript rows at all. It is client-asserted like
`workspace_id` on that still-unauthenticated route, but it is only ever read as
prose to resolve a pronoun against — it cannot widen which tools run or which
tenant is read, both of which are resolved server-side and forced onto every
call.

### Across calls — `search_past_calls` (`server/app/tools/memory.py`)

The transcript rows a call already writes ARE the memory. No second store and
no summarisation pass, specifically because of the standing rule: every item
quotes real rows and its locator (`call:abcd-efgh:2026-09-11T14:02:00Z`) can be
checked against `GET /api/meetings/{slug}/transcript.md`. A summarised memory
could not be cited that way — it would be the agent quoting its own paraphrase.

- **Lexical, not embedded**, so it works the moment a call ends rather than
  after an ingest, and because recalling a past call reuses that call's
  vocabulary — the case where a dense embedding's advantage is smallest.
- **The current call is excluded**, forced by the loop the same way
  `workspace_id` is (`_MEETING_SLUG_TOOLS`). Without it the tool quotes the
  caller's own sentence from ten seconds ago back at them as established fact.
- **A question is paired with the immediately following reply only.** Scanning
  forward for "the next agent line" attaches an answer across an intervening
  question — a real wrong citation, not a ranking miss. An unanswered question
  is kept with an empty answer: that is the turn the agent abstained on, which
  is often the most useful line in a transcript.
- Scored on the QUESTION, not the question plus the answer — the caller is
  recalling what they asked, and a long answer otherwise drags in an unrelated
  question.

**A defect the first live run found immediately**: the query `"webhook
retries"` scored **zero** against `"why do the webhooks only retry three
times"` — the exact question it was recalling. Every term was present and none
matched, because webhook/webhooks and retries/retry are different strings.
Plural and verb-form drift between how a thing is asked now and how it was
asked last time is the normal case, so lexical retrieval here is unusable
without the crude `_stem()` this added. Both sides are stemmed, so precision
barely matters ("status" -> "statu" is fine) — only consistency does.

### Keeping the ~1.8s fast path

`search_past_calls` is default-on for any workspace that has had a call, so
leaving it out of `_CODE_TOOLS` would have switched the fast path OFF for every
code-only workspace **the moment it finished its first call** — a latency
regression arriving days after the feature, with nothing to connect it to. It
is in the set instead, and `_RECALL_RE` applies the plan prompt's own rule
locally (the same shape as `_WHY_RE`), so an ordinary code question never pays
for a transcript search.

### Cost, stated plainly

With no history the compose prompt is **byte-for-byte** what it was before, and
the plan prompt differs by exactly one intent-mapping line (the one that names
the new tool, same as every other tool gets). The history block and its
guidance paragraph are appended only when there is a conversation to talk
about.

`evals/agent_eval.py` base came back **21/24** on the run after this, with all
three misses on `calico-rate`. That is below the documented 24/24, so it was
A/B'd directly rather than assumed: the same question, 6 runs with the new
prompt line and 6 without, in the same process against the same stack —
**5/6 both arms**. The case abstains intermittently on a thin single-tool
evidence set, which the earlier 22-23/24 variance note already describes; it is
not tool selection moving. `cites_ok` stayed 24/24, which is the check that
would catch a remembered `[ev_xxx]` leaking into a later turn.

## Code retrieval — reranking, and the fake score that hid the problem

The user asked whether code retrieval could be better. Traced the whole
path — `search_code` -> `vector_search` -> Qdrant `code_chunks` — and it
was **dense-only**: a Voyage embedding of the question against embeddings
of raw code text, top_k straight out, no lexical signal and no reranking
anywhere. Measured it against hand-labelled ground truth on the seed repo
rather than reasoning about it, and it was worse than it looked:
**top-1 2/8, top-3 5/8, MRR 0.473.**

**Two specific failure modes, both visible in the numbers:**

1. **It cannot tell a DEFINITION from a MENTION.** Querying
   `RETRY_BACKOFF_SECONDS` ranked `notification_service.send_webhook`
   first — which only references the constant in a docstring — above
   `app/webhooks.py:16`, the line that defines it. An embedding of a
   mention and an embedding of a definition sit in almost the same place.
2. **Tests outrank source.** A test restates the question's identifiers
   nearly verbatim, so it embeds closer to the query than the
   implementation does, while almost never being the answer. For "why does
   pricing have a special case for Bangalore" — the S2 demo question —
   ranks 2 and 3 were `tests/test_pricing.py` and the actual
   `PARTNER_CITY_RATES` branch **was not in the top 5 at all**. It was
   reaching the compose step only because `explain_why` runs alongside.

**The fix is overfetch-then-rerank** (`app/core/retrieval/rerank.py`):
pull a pool of 20 by vector similarity (cheap, recall-oriented), then let
a cross-encoder order them. A cross-encoder reads query and chunk
*together*, so it can make both distinctions above; bi-encoder similarity
structurally cannot. The document handed to it is
`file_path + symbol_name + text`, because the path is what separates
`tests/test_pricing.py` from `app/pricing.py` and that is invisible from
the code body alone.

```
                 top1   top3    MRR    median
dense only       2/8    5/8    0.473    397ms
+ rerank         6/8    8/8    0.854    750ms
```

`rerank-2.5-lite` beat full `rerank-2.5` (MRR 0.854 vs 0.812) and costs
the same — latency is network-bound, so it barely moves with model or pool
size. pool=20 was the sweet spot: 15 lost recall, 30 bought nothing.
**A hand-rolled lexical/exact-identifier boost was tried and dropped** —
it helped nothing on its own (MRR 0.469) and actively *hurt* on top of the
reranker (0.729 vs 0.812). The cross-encoder already does that job better.

**Fails open by design.** If the rerank call errors, the dense order is
returned unchanged and a warning is logged. This is on the critical path
of a live call: a degraded ranking is a worse answer, an exception is no
answer at all. `RERANK_ENABLED=false` disables it entirely.

**A real bug found on the way: the scores in the evidence panel were
fabricated.** `vector_search` discarded Qdrant's `hit.score`, so
`app/tools/code.py::_rank_score()` invented a descending ramp from rank
order (1.0, 0.94, 0.88…). Every result looked equally confident, nothing
downstream could threshold on relevance, and a reranker would have had
nothing to fuse with. The real score now rides on the payload as `_score`
(the reranker's relevance score replaces it when reranking ran, with the
dense score preserved as `_dense_score` for debugging). Real scores on the
seed repo spread 0.42-0.89 — the fake ramp was hiding that a rank-5 result
was often barely related. `_rank_score` survives only as the fallback for
unscored results (graph nodes).

**`evals/retrieval_eval.py` (new), because `agent_eval.py` cannot see any
of this.** The agent eval scores whole turns and measured 22-23/24 both
with and without reranking — its code questions are easy enough that a
mediocre ranking still yields a correct answer, so it is not evidence
either way. The retrieval eval scores rank-of-the-right-chunk directly,
with a regression floor (top1 >= 5, MRR >= 0.75), and A/Bs via
`RERANK_ENABLED=false`. **Run it after any change to chunking, the
embedding model, or the reranker.**

**Cost, stated plainly**: ~+350ms per `search_code`/`find_usages` call.
On the eval that moved the median turn ~2580 -> ~2660-2820ms, i.e. still
under 3s, because most questions call one search tool at most. A question
that calls two of them pays it twice.

**Not done, and worth knowing where the remaining headroom is**: chunks
are embedded as *raw code text only* — no file path, symbol name, kind or
docstring in the embedded string. Adding that header at ingest should lift
the dense pool's recall (which reranking cannot fix — it can only reorder
what Qdrant returns), but it needs a full re-ingest of every repo, so it
was left as a separate change rather than bundled in here. `find_usages`
is also still a plain vector search over a "usages and references of X"
string and never touches the Neo4j call graph, which actually knows the
answer.

## Tested against a real repo (rishik92/adventa-backend) — 3 defects

The user asked to run the retrieval work against a real repo and measure
accuracy continuously. Ingested `rishik92/adventa-backend` (Express +
Prisma + BullMQ + Manim/YouTube, 63 files, TypeScript) and wrote 18
retrieval cases and 16 answer cases whose ground truth was read FROM THE
SOURCE, so the agent is graded against the repo, not against itself.

**The headline: everything looked fine on the Meridian seed corpus and
fell over on a real repo**, because the fixture is small Python functions
(median chunk 193 chars) and a real TypeScript repo is not (median 2620).

**Defect 1 — non-symbol chunks fabricated their line range.** Symbols
carve holes out of a file, leaving the imports above and the wiring below.
Those leftover lines were gathered into ONE chunk spanning from the first
to the last, so `groupController.ts:L1-L1542` held 3439 characters — a
1542-line citation whose snippet covers a sliver of it. **That is a
fabricated locator, which the standing rule forbids outright.** Non-symbol
lines are now chunked as separate contiguous runs (`_contiguous_runs`).

**Defect 2 — the 800-char evidence cap threw away answers retrieval had
already found.** `make_evidence` truncated every snippet to 800 chars.
Measured: **80% of this repo's chunks lost text (vs 3% of the seed
corpus).** The symptom was precise — `search_code` ranked the chunk
defining `MAX_RETRIES = 4` FIRST, the constant is on line 23, the cap cut
at roughly line 20, and the agent abstained on a question it had retrieved
the answer to. Same for worker concurrency and the manim timeout. Now
`evidence_snippet_max_chars` (default 2400), and truncation appends an
explicit marker instead of happening silently.

**Defect 3 — the token estimate undercounted code by ~2x.**
`len(text.split()) / 0.75` counts whitespace words; code is punctuation
dense. Measured on this repo: 737 estimated vs ~1428 real for the same
span, which is how 144-line (and one 1542-line) chunks got past a
512-token ceiling. Now `len(text) / 3.5`.

After re-ingest: max chunk **1542 -> 80 lines**, median 2620 -> 1793
chars, chunks truncated by the cap **80% -> 4%**, and **zero** chunks
whose locator overclaims its text.

**Measured on adventa-backend** (16 answer cases x 3 rounds, scored on
stated fact + an LLM judge for closeness to the source-derived reference):

```
                              correct   closeness  cites
old chunking + rerank          69%       2.75/5    16/16
fixed chunking, no rerank      83%       4.00/5    48/48
fixed chunking + rerank        88%       4.23/5    48/48
```

Retrieval ranking on the same repo: top-1 **7/18 -> 13/18**, MRR 0.551 ->
0.796 with reranking.

**The lesson worth keeping**: reranking moved RANKING a lot (+6/18 top-1)
but final ANSWERS only +5pp, because compose sees 6-8 evidence items and
can recover from a rank-3 hit. **Chunking was the bigger lever for answer
quality** (69% -> 83%). Rank-1 accuracy is not the same objective as
answer accuracy, and optimising the first does not automatically buy the
second.

**Two hypotheses the measurement killed**, recorded so nobody re-runs
them: (a) the larger snippet cap was assumed to cost latency — it does
not, 800 chars measured SLOWER (4748ms) than 2400 (4290ms); the ~1s swing
seen that day was OpenRouter variance, as usual. (b) Once chunking is
fixed the cap barely matters (1600 and 2400 score the same), because most
chunks now fit under it — it mattered enormously only while chunks were
oversized.

**Still open, with evidence**: `"what port does the server listen on"`
failed BOTH with and without reranking. `src/index.ts` sits at dense rank
**35** (score 0.295), outside the pool of 20, so the reranker never sees
it — the whole 75-line file was one chunk whose embedding averaged 17
import lines against the two that matter. That is a RECALL failure and
reranking cannot fix it by construction. The fix is the one already
flagged: embed a context header (path, symbol, kind) with each chunk.
Post-chunking-fix the question now answers correctly, but the underlying
recall weakness is unaddressed.

`server/tests/test_chunker.py` guards defects 1 and 3 (4 tests, no network).
Seed-corpus regression after all of this: retrieval eval 6/8 top-1 / MRR
0.854 unchanged, `agent_eval` base 22/24 (within the 22-23/24 variance
measured that day), HARD **24/24**.

## Latency — the plan call was 1.7s of provider hop, not thinking

The user asked for a much faster turn and said to stay on OpenRouter. So
the first thing measured was whether the latency is even ours:

```
trivial 20-token call, one-line prompt, via OpenRouter
  google/gemini-3.5-flash-lite   1640ms     (current model)
  google/gemini-3.1-flash-lite   2221ms
  anthropic/claude-haiku-4.5     1294ms     (fastest available)
  openai/gpt-oss-120b            1650ms
  DIRECT Gemini API (free key)   2345-12530ms
```

**Every model costs 1.3-2.2s for a call that does no work.** That is the
provider hop. A live turn makes two sequential LLM calls, so ~3.4s of a
~5s turn is network round-trip before any prompt or evidence is
considered. This settles a target of "0.5s for the brain-api": it is not
reachable on OpenRouter with any model, at any prompt size. Direct Gemini
on the existing key is WORSE (heavily throttled now rather than quota
blocked), so it is not the escape hatch either.

**What that leaves: make two LLM calls into one.** `_fast_path_calls()` in
`app/agent/loop.py` skips the planning round entirely for a code-only
workspace, applying locally the rules `build_plan_prompt` already states
in prose — a "why does this exist" question gets `search_code` AND
`explain_why`; anything else gets `search_code` (plus `search_custom_docs`
when connected). Same decision, 0ms instead of ~1700ms.

**Measured on the live call's REAL tool set** (15 turns/arm for latency,
32 graded answers/arm for accuracy, adventa-backend ground truth):

```
                 correct  closeness   median   p90
fast path on       81%     3.62/5     2885ms   3564ms
fast path off      78%     3.41/5     4660ms   6224ms
```

**~1.8s faster and not less accurate** — slightly better, though 3pp at
n=32 is inside the noise; the honest claim is "no measurable accuracy
cost". Off with `AGENT_FAST_PATH=false`.

**A first attempt that WAS worse, kept here because the difference is the
whole lesson**: the original gate fired only when every tool was a plain
search and then called ALL of them. That cost 6pp of accuracy (75% vs
81%) AND barely helped latency, because firing two search tools gave back
most of the saved time in a fatter compose prompt. Encoding the planner's
actual rules beat calling everything — "skip the planner" is only safe if
something still makes the planner's decision.

**It is deliberately narrow.** It engages only when `allowed_tools` fits
inside the code-tool set; one account, ticket, Slack or connector tool and
the planner runs as before, because choosing between SOURCES is a real
decision. It also never lets `explain_why` run alone — that is exactly the
documented Bangalore regression. Two tests in
`server/tests/test_agent_loop.py` guard both properties.

**A process note worth keeping**: the first version of this was measured
at "-40% latency" against a hand-picked 2-tool set — and would have done
NOTHING on the real call, whose 6-tool set the gate rejected. Always
resolve `tools_for()` for the actual workspace before trusting a latency
number.

**Where the remaining ~2.9s goes** (and what is left): compose ~1.6-2.4s
(one unavoidable OpenRouter round-trip), `search_code` ~1.1-1.25s (Voyage
query embedding ~400ms + Qdrant + rerank ~310ms). Below ~2.5s needs either
a lower-latency LLM endpoint or dropping the reranker, which costs the
accuracy it buys. Also unrelated but real: a Deepgram TTS timeout added
17s to one live turn, and endpointing adds 0.3s (clean sentence) to 2.5s
(when the turn detector thinks you are not finished).

### Fix — endpointing truncated a live question mid-sentence

Heard on a real call: the caller said **"Oh, why do you"**, paused to
think, and the agent answered that fragment. It reads as the agent not
listening; it is actually a timing setting.

The log named the cause exactly:

```
eot prediction   probability=0.096  unlikely_threshold=0.56  endpointing_delay=2.5
user turn committed  ... delay_completed=true
orchestrator.speech_finalized  text='Oh, why do you'
```

The turn detector put the odds of the sentence being finished at **0.096
against a 0.56 threshold** — it KNEW they were still talking — and the
2.5s `max_delay` expired and committed anyway.

**Why raising it is close to free**, which is the part worth remembering:
the two delays cover DIFFERENT cases. When the detector is confident the
sentence ended, the turn commits after `min_delay`. `max_delay` applies
ONLY when the detector thinks the caller has not finished. So raising the
max buys time exactly where the alternative is answering half a question,
and costs nothing on fluent speech. `_ENDPOINTING` in
`adapters/livekit_adapter.py` is now `{"min_delay": 0.25, "max_delay":
6.0}` (stock streaming defaults are 0.3 / 2.5); the min is slightly lower
because a confidently-finished sentence should not wait at all on a call.

**API trap**: `AgentSession(min_endpointing_delay=..., max_endpointing_delay=...)`
is DEPRECATED in livekit-agents 1.7.0 and is a different API from
`turn_handling={"endpointing": {...}}`. Passing the wrong one fails
quietly — the session just keeps the defaults. Verified against the
installed SDK's own `_resolve_endpointing()` that the values are honoured,
and a test asserts it so an SDK upgrade cannot silently revert this. The
value is also logged at session start (`livekit_adapter.endpointing`),
because a wrong endpointing value is otherwise invisible — it surfaces as
the agent answering half a question, which looks like a model problem.

Two tests in `call-agent/tests/test_adapter_api.py`; 114 pass.

## Linear, Notion, Datadog — one generic connector instead of three more

Slack and Jira each earned a bespoke table and router: each has real
structure worth modelling (channels with history; projects with issues).
These three share one shape — credentials in, resources listed, resources
selected, sync queued — so they share ONE table
(`external_connections` + `connector_resources`), ONE router and ONE Celery
task. **A new provider is a module in `app/services/connectors/`, not
another table, router and migration.**

- Credentials live in a single Fernet-encrypted JSON blob: every provider
  needs a different set of secrets (one key, two keys, a token plus a
  site), and a column per vendor secret is a migration per vendor.
- Non-secret settings (Datadog's site) sit in a plain `config` JSON so they
  are inspectable without decrypting anything.
- `GET /providers` publishes what each one asks for, so the UI renders the
  right form and the API rejects a half-filled one *before* calling the
  vendor.
- One Qdrant collection filtered by `workspace_id` AND `provider` inside
  the query — a collection per vendor per tenant multiplies without bound
  for no retrieval benefit.

**Per-provider decisions that are not obvious:**
- **Linear** — personal API key, GraphQL. Comments are indexed with the
  issue: "we decided not to fix this because…" lives in a comment and
  nowhere else.
- **Notion** — internal integration token. Its permission model is the
  trap: an integration sees NOTHING until a page is explicitly shared with
  it, so "connected but empty" is the normal first state. The resources
  endpoint returns an explicit note saying so, because otherwise it reads
  as a broken integration when it is working exactly as Notion intends.
  Block flattening is depth-limited — pages nest arbitrarily and a runaway
  recursion would issue thousands of calls for text nobody reads.
- **Datadog** — API key + application key + SITE. Datadog is
  region-partitioned and the wrong host 403s, which looks exactly like a
  bad key, so site is part of the config rather than a guess. Indexes
  monitors and incidents, NOT metrics or raw logs: "is something on fire
  right now, and is it this?" is answerable from a monitor's name, message
  and state; time-series do not embed usefully and raw logs are expensive
  noise. A 403 on incidents is swallowed — Incident Management is a
  separate product and not having it is normal, not a sync failure.

`search_linear` / `search_notion` / `search_datadog` have **no seed
fallback** (same reasoning as `search_jira`): with no fixture equivalent, a
fallback would make a broken connection indistinguishable from a working
one. 17 tools registered now; eval held at 13-15/16, the two flags being
the known S2 tool-count threshold and the S3 empty-plan flake, not a new
regression from the larger tool list.

**Verified live**: `/providers` reports each form, a half-filled Datadog
form is rejected with 422 before any vendor call, a bad Linear key is
rejected with a real 400 from Linear's own API, and all three tools return
a clear "not connected" rather than fixture data.

## Jira — connected by API token, not OAuth

Same wall as Slack's public distribution: Atlassian's OAuth 3LO needs a
registered app with an HTTPS callback. An **API token** is created by any
user from their own Atlassian account in a minute, works on localhost, and
carries exactly that user's permissions — so the connection can never see
more of Jira than the person who made it.

- `POST /api/integrations/jira` verifies credentials against
  `/rest/api/3/myself` **before storing them**: an unverified token fails
  later, in a background sync, where nobody is watching. Token is Fernet-
  encrypted like Slack's.
- Projects are chosen explicitly — a Jira site holds plenty of projects
  that have nothing to do with support.
- Sync is incremental via JQL `updated >= …`; point ids are
  `uuid5(workspace:issue_key)` so an updated ticket REPLACES its old copy
  rather than leaving a stale one the agent could still cite.
- Descriptions and comments arrive as Atlassian Document Format (a nested
  node tree, not text); `_plain_text()` walks it for the text nodes.
- Only summary/description/status/assignee/comments are indexed. A Jira
  issue carries a lot of workflow metadata nobody asks support questions
  about, and embedding it dilutes the text that matters.

**`search_jira` has NO seed fallback**, deliberately unlike `search_slack`:
the demo corpus already exposes `tickets.jsonl` through `search_tickets`,
so a fallback here would serve fixture data under a Jira label and make it
impossible to tell whether a real connection works. A workspace with no
Jira gets a clear "nothing connected" note instead.

14 tools registered; eval unaffected (15-16/16, within the known variance).

## Slack — the first connector where the tools stop being fixtures

Built first among the connectors because the S2 demo scenario depends on
it: the Bangalore partner-rate reason exists ONLY in a Slack thread, so
this is the source that makes `search_slack` real rather than seeded.

**Setup**: `/dev/slack-app/new` renders a pre-filled app manifest. Slack has
no server-to-server manifest exchange like GitHub's (their manifest API
needs an app-configuration token created by hand), so this cannot be fully
automatic — but it removes every chance to mistype a scope or redirect URL.
Scopes are read-only (`channels:read/history`, `groups:read/history`,
`users:read`, `team:read`); the agent never posts to Slack, which a
reviewer approving the app can see. No event subscriptions: history is
pulled on demand, so there is no public URL for Slack to call — which is
also why this works on localhost.

**Tokens are encrypted at rest** (`app/core/crypto.py`, Fernet keyed from
`secret_key`). The GitHub App never needed this — it mints short-lived
tokens from a private key — but Slack OAuth returns a long-lived bot token
that reads every channel the app was added to. A read-only leak of one
table would otherwise be a full read of a customer's Slack. Rotating
`secret_key` invalidates stored tokens, and `decrypt()` returns "" rather
than raising so the app says "reconnect Slack" instead of 500ing.

**Ingestion** (`services/slack_sync.py` + `tasks/slack_ingest.py`):
- Thread REPLIES are fetched, not just the channel timeline — the S2 answer
  lives in a reply, not in the message that opened the thread.
- Point ids are `uuid5(workspace:channel:ts)`, so a re-sync UPDATES a
  message instead of duplicating it.
- Incremental by `last_synced_at`: re-embedding unchanged text costs real
  money.
- 429s honour `Retry-After` — ignoring Slack's rate limits gets the app
  throttled for everyone in the customer's workspace.
- One collection with a `workspace_id` payload filter, not a collection per
  tenant, and the filter is applied INSIDE the vector query: filtering
  post-hoc would let one tenant's messages occupy the top-k and silently
  starve another's.

**Channel selection is explicit** — connecting Slack must not quietly index
every channel a bot happens to be in. Deselecting stops future syncs but
does not retroactively purge, so a mis-click cannot destroy history.

**`search_slack` now prefers real data**, falling back to the seed corpus
when a workspace has none indexed — which keeps the demo scenarios working
on a fresh deployment and makes a just-connected-but-not-yet-synced
workspace degrade to "nothing found" rather than an error. Real hits use
the same `slack:#channel:ts` locator shape as the fixture, so the evidence
panel, citation rules and verifier treat them identically.

**`workspace_id` is forced by the loop, never planned** (`_WORKSPACE_ID_TOOLS`
in `loop.py`, same pattern as `repo_id`) and is deliberately absent from the
schema the planner sees: a hallucinated workspace id would be a
cross-tenant read, not merely a wrong answer.

**A real regression caught by the eval while doing this**: S1 dropped to
0/3 on content. The planner had started calling `get_account_logs` ALONE,
so the answer correctly reported "their endpoint returns 401" and never
reached "because the signing secret was rotated on Aug 14" — right, and
useless to the person on the call. The logs hold the SYMPTOM, the account
record holds the CAUSE. Fixed with an explicit plan rule to call both for
"why is <customer> broken", exactly like the earlier search_code +
explain_why fix. Eval back to 16/16.

**Not yet built**: the Slack UI (connect card, channel picker) — the
backend, encryption, sync and tool are done and the endpoints degrade
cleanly with a 503 until an app is created.

## Removing the demo corpus from real workspaces

The user asked why "Meridian" appeared everywhere. It was leaking in two
places, and one of them was dangerous:

1. **`tools_for()` appended SEED_TOOLS unconditionally** — so every real
   workspace's agent could answer from invented accounts, invented tickets
   and invented docs, confidently, with citations indistinguishable from
   real ones. The fictional corpus is now an ordinary source group
   (`demo_corpus`) gated on `enable_demo_corpus`, **off by default**, and
   dropped from the catalog entirely when disabled so it cannot be toggled
   on by accident.
2. **The call page's idle panel listed the fixture's customers.** Showing
   invented companies to a real user is worse than showing nothing — it
   implies the agent knows them, and the first question asked will be about
   a company that does not exist. Replaced with `WorkspaceSummary`, which
   lists what this workspace can actually answer from.

The seed REPO was already correctly gated (only used when no workspace is
given, which is the eval path), so it needed no change.

**Joining is now refused when a workspace has no sources** — 409 from the
API, and the setup screen disables Start with an explanation. A call where
every question meets an abstention looks broken to everyone on it, and the
fix (connect a source) is not discoverable from inside the call.

**Verified**: in a workspace holding only an uploaded document, "is
Northwind Logistics on the partner tier?" now ABSTAINS — it previously
answered with high confidence from the fixture — while a real question
about the uploaded process still answers with citations. A brand-new empty
workspace gets 409 on meeting creation. Eval 15-16/16 (unchanged variance),
13 tenancy/config tests pass.

**Worth knowing**: the planner sometimes names a tool it was never offered
(`get_account` here, `search_custom_docs` earlier) — the model recognises
the question shape and invents a plausible tool name. The execution-time
block catches it, which is exactly why hiding tools from the prompt was
never treated as sufficient on its own.

## Workspace types, and a waiting room on /call/[slug]

**Workspace kind (individual | team)** is asked at creation, not inferred,
because the answer changes what "connect Slack" MEANS: in a team workspace
it exposes that Slack to everyone ever admitted, and someone connecting a
personal account should know which of those they are doing. Inviting into
an individual workspace is refused with a 409 rather than silently
upgrading it — turning someone's private workspace into a shared one is not
a side effect of clicking Invite. Existing workspaces backfill to
INDIVIDUAL: a workspace nobody was ever invited to IS an individual one,
and defaulting the other way would relabel every existing one as shared.

**`/call/[slug]` — knock and admit.** A meeting code is shareable, which is
the point, but a link forwarded one hop too far should not put a stranger
into a live customer call. So the code gets you to the door and someone
already inside opens it.

- **Workspace members skip the queue entirely.** They already have access to
  everything the call can reach, and making colleagues queue teaches people
  to click Admit without reading it.
- A signed-in non-member does not have to retype their name — the API uses
  their account email. Only true strangers introduce themselves.
- **The token route verifies admission server-side.** The waiting room is
  only real if a join token cannot be obtained without passing through it,
  so `/api/livekit-token` now refuses a guest with no knock, and a guest
  whose knock is pending or denied. This closed a genuine hole: the route
  previously minted a token for anyone who knew the room name.
- Any member on the call can decide. Waiting for one specific person while
  a customer sits outside is worse than trusting the colleagues in the room.
- The waiter POLLS rather than subscribing: they are not in the room yet, so
  there is no room connection to listen on.
- `PhotonWaiting` reuses the landing page's photon streak and ring, but
  LOOPING rather than resolving — waiting is open-ended, and an animation
  that completes would imply something had been decided.

**Verified end to end**: a guest asked to join and saw the waiting screen;
admitting them from the other side moved them into the call automatically,
carrying the code, the admission proof and their name. `tests/
test_waiting_room.py` (5 tests) covers the denials: pending cannot mint a
token, no-knock cannot, denied cannot, and the waiting list is not public.

## Multi-party calls — poke to address, meeting codes, shared transcript

Decided with the user: **poke by button AND wake word**, guests treated as
viewers (workspace-scoped sources), **individual-scoped sources only serve
turns attributed to that individual**, and one **common** transcript per
call keyed by an 8-character code.

**The constraint that shapes all of it**: LiveKit's `AgentSession` listens
to exactly ONE participant — `RoomIO.linked_participant` /
`set_participant()` / `RoomInputOptions.participant_identity`. On a call
with 3 members and 2 external clients it hears one of them (whoever linked
first). That is why the earlier synthetic test only worked when the fake
caller joined before the browser.

So a poke is not merely an intent hint — **it is how we choose whose
microphone the agent is on**, which also makes attribution certain.

- `POKE_TOPIC = "photon.poke"` over the LiveKit data channel;
  `client/app/call/PokeButton.tsx` sends an EMPTY payload on purpose: the
  adapter reads the sender from the packet, which LiveKit authenticates.
  Anything in the body would be self-asserted.
- `_on_data_received` -> `set_participant(identity)` -> `on_poke` callback
  (4th method on `SessionCallbacks`).
- `POKE_WINDOW_SECONDS = 45`, and a poke is **consumed by the turn it
  triggers** so one poke answers one question rather than leaving the mic
  hot.
- Wake word is back as the fallback for clients not using our join page —
  with the honest limitation that it is only heard from the currently
  linked participant.

**Two bugs found on the way, both of which would have silently broken
per-speaker scoping:**
1. `_InterceptAgent` captured `speaker_id` ONCE at construction — as the
   AGENT's own identity. Every human utterance was attributed to the agent
   (`speech_finalized speaker_id=agent-AJ_...` in the logs). Now resolved
   per turn from the linked participant.
2. `/api/livekit-token` took `identity` from a **query parameter with no
   auth** — anyone could join as anyone. Harmless before; not once a turn's
   attribution decides whose private sources may answer it. Identity now
   comes from the verified session (checked against `/api/auth/me`, not
   decoded locally) and is signed into LiveKit token metadata. Guests get a
   random `guest:` identity.

**Meetings**: `Meeting.slug` is `abcd-efgh` from an alphabet with no
0/O/1/l/I — the first thing anyone does with a code is read it aloud. The
slug IS the LiveKit room name, so link, room and transcript are one
identifier. `normalise()` accepts "ABCD EFGH" and "abcdefgh".

**Transcript** is stored as ROWS and rendered to markdown on demand
(`GET /api/meetings/{slug}/transcript.md`), not appended to one text
column: several people and the agent write during a call and concurrent
read-modify-write loses lines silently. `GET /{slug}` is deliberately not
workspace-scoped (an external client joining by link is not a member), but
`transcript.md` **is** — the room is discoverable by code, what was said in
it is not.

**Verified** with a simulated 3-human + guest call: Alice poked and got an
answer (2.2s); Bob's side comment was ignored in 9ms with no API call; a
guest was ignored until they poked, then answered; and all six lines
including both agent replies rendered into one markdown transcript with
correct speaker names.

**Limitation to be honest about**: the transcript can only contain what the
agent HEARS, which is the linked (poked) participant. Un-poked side
conversation is not captured — the e2e above only showed Bob's line
because it called `on_speech` directly, bypassing LiveKit. A genuine
full-room transcript needs an STT stream per participant (~N x audio cost,
running whether or not anyone addresses the agent) or LiveKit's separate
transcription/egress. That was the cost trade flagged before building this.

## GitHub App — setup, and the three bugs that blocked it

The App exists and is live: **Photon-githubabcd**, app id `4684753`, owner
`@RISHIK92`, permissions `contents: read` + `metadata: read`. Verified by
signing an App JWT and calling GitHub's own `GET /app` -> 200.

**Operator setup (one time, per deployment):** visit
`/dev/github-app/new` (dev-only route, never linked from the product),
click "Create GitHub App" on GitHub, then paste the returned credentials
into `server/.env`. `public_base_url` must match what the App is
registered with.

Three bugs sat in the path, each of which failed in a way that pointed
somewhere unhelpful:

1. **Manifest rejected: "at least one callback URL is required."**
   The manifest set `request_oauth_on_install` but no `callback_urls` —
   which is a DIFFERENT field from `redirect_url` (that one only receives
   the one-time manifest-conversion code). Both real OAuth landing points
   must be listed, or the flows 404/mismatch at runtime:
   `/api/auth/github/callback` (sign-in, routers/auth.py) and
   `/api/integrations/github/callback` (install, routers/github_app.py).

2. **Callback 500'd AFTER the App was created — credentials lost.**
   `data.get("webhook_secret", "")` returns `None`, not `""`: a default
   only applies to a MISSING key, and GitHub returns the key present with
   a null value when the App has no webhook. `html.escape(None)` then
   threw, and because the manifest code is single-use, the client secret
   and PEM were unrecoverable — the App had to be re-keyed by hand from
   its settings page. Now null-tolerant.

3. **`Invalid symbol 92` when signing the JWT.** 92 is ASCII for
   backslash. A PEM is multi-line and `.env` is line-based, so the key is
   stored with literal `\n` — and nothing ever unescaped it, so the
   crypto library received actual backslashes. Fixed with a
   `field_validator` on `github_app_private_key` in `config.py`, so every
   consumer gets a real PEM; it tolerates escaped one-liners, quoted
   values, and already-real multi-line PEMs.

**Design correction while configuring it**: the install callback reads
only `installation_id` + `state` and never an OAuth `code` — it
authenticates as the App (JWT). So the right mechanism is the App's
**Setup URL**, not `request_oauth_on_install`. With OAuth-on-install the
post-install redirect goes to a *callback* URL, and with two registered
there is no guarantee GitHub picks the install one rather than the
sign-in one. The manifest now sets `setup_url` + `setup_on_update` and
leaves `request_oauth_on_install` false; sign-in is unaffected, since it
is its own flow through the callback URLs.

**Verified end to end** (server side): App JWT signs -> GitHub `/app`
200 -> `/api/auth/github/login` 307s to github.com/login/oauth/authorize
with the right redirect_uri -> `POST /api/integrations/github/connect`
returns a real `https://github.com/apps/photon-githubabcd/installations/new?state=…`
URL. `installations: 0` — nobody has installed it on an account yet,
which is the next step and needs a human to click Install.

## Phase A — login, workspaces, and real repo ingestion

Moving from a single-tenant demo to something a user logs into and points
at their own code. Decisions taken with the user: **JWT first then GitHub
App in the same phase**, **workspace-scoped from day one**, **user picks
which repos** rather than auto-ingesting a whole org.

### Slice 1 — the workspace tenant boundary (done)

The inherited YASML base already had more than expected: `User`,
email/password + JWT (`app/core/auth.py`), `Repo.owner_id`, per-user
list/get/delete, ZIP upload and clone-by-URL. What it did **not** have:
any org concept, GitHub auth, or an agent that respects any of it —
`app/agent/` and `app/tools/` contain **zero** references to users or
ownership, and `answer_question()` still falls back to
`get_seed_repo_id()`. The client had no login page at all.

- `Workspace` + `WorkspaceMember` models; `Repo.workspace_id`. A user is a
  login; a workspace is the thing that HAS data and can be shared.
- `app/core/workspace.py` — `get_current_workspace()` is the single choke
  point: resolves `X-Workspace-Id` (falling back to the caller's personal
  workspace) and verifies membership, so routers never re-derive
  ownership. A personal workspace is created on signup AND on login, so
  users predating this don't authenticate into nowhere.
- Non-members get **404, never 403** — a 403 confirms an id exists.
- `server/tests/test_tenancy.py` — 9 tests, two real users over real HTTP,
  checking DENIAL: can't list, get or delete another workspace's repo,
  can't borrow a workspace id, unauthenticated is rejected.

**Postgres, not SQLite** — the user asked whether we should migrate;
verified live, it's already `PostgreSQL 16.13` via `postgresql+asyncpg`
from the docker-compose `postgres:16-alpine`. Nothing to do. (The
`ADD COLUMN IF NOT EXISTS` migration pattern is Postgres-only anyway.)

**No Alembic**: `create_all()` creates missing TABLES but never adds a
column to an existing one, so every new column needs a line in
`create_db_and_tables()`. Fine for now; replace with Alembic before the
data matters.

### Slice 2 — client auth (done)

`client/lib/api.ts` (token + workspace header + 401 handling),
`/login` (sign in / create account), `/dashboard` (workspace switcher,
sources, repo list with live status), `AuthGuard`, and root now routes to
one or the other.

**Next 16 gotcha, caught by reading `node_modules/next/dist/docs/`**:
middleware is renamed to **`proxy.ts`**. A `middleware.ts` would have
silently done nothing. Not used in the end anyway — the token lives in
localStorage, which the server can't read, so the guard is client-side and
the real enforcement stays server-side (see the tenancy tests).

**Trade-off stated in the code**: localStorage is XSS-readable. The
hardened version is an httpOnly cookie set by a Next route handler
proxying the API. This build talks to the brain-api directly from the
browser, so the token must be reachable from JS.

### Slice 3 — GitHub App: OAuth login + private-org repo access (done)

User request: private orgs need real GitHub access, not a pasted public
URL + one shared static `GITHUB_TOKEN`. One GitHub App now covers both
"Sign in with GitHub" (an alternative to email/password, not a
replacement) and "Connect GitHub" (per-workspace installation, so an org
admin grants access to selected repos and the user then picks which of
those to actually import). No webhook in this pass — deliberately: this
deployment binds to localhost with no public URL for GitHub to reach, so
the repo picker computes the new-vs-already-imported diff live, on every
open, by calling GitHub's API directly. A real webhook is a documented
follow-up, not a gap in the feature as shipped.

**Backend** — `server/app/services/github_app_auth.py` (RS256 app-JWT via
the existing `python-jose[cryptography]`, no second JWT library; Redis-
cached installation tokens, `EX=3300`, 5min margin under GitHub's 60min
expiry), `server/app/routers/auth.py` (`GET /github/login` /
`GET /github/callback` — OAuth login, links to an existing `User` by
email or creates one with `hashed_password=None`), `server/app/routers/
github_app.py` (install + repo picker + diff-based import),
`server/app/routers/dev_github_setup.py` (one-time manifest-flow
bootstrap, mounted only outside production, never linked from product
UI). New `GitHubInstallation` table; `User.github_id`/`github_login`;
`Repo.github_repo_id`/`github_installation_id`. Migration: 7 idempotent
`ALTER TABLE` statements in `database.py`, verified applied via `psql`.

**Design correction made during implementation, not in the original
plan**: install-initiation can't be a plain `<a href>` — a top-level
browser navigation can't carry an `Authorization: Bearer` header, so
there'd be no way to know which user/workspace is installing. Fixed as
`POST /connect` (normal authenticated fetch, returns `{url}`) with the
client doing `window.location.href = url`; workspace binding flows
through a single-use Redis nonce (`gh:install_nonce:{nonce}`, TTL 600s,
passed to GitHub as `state`) instead of a token-bearing redirect. Also:
the OAuth token lands in the callback redirect as a URL **fragment**
(`#token=...`), never a query string, so it never hits server access
logs or a Referer header.

**Real pre-existing bug fixed along the way**: `repo_fetcher.py::
clone_github_repo` logged the token-embedded clone URL directly. Fixed
with a redaction regex before any log call — applies to both the static
`GITHUB_TOKEN` and installation tokens.

**Frontend**: `client/lib/api.ts` (`githubLoginUrl`, `startGithubInstall`,
`listGithubInstallations`, `listInstallationRepos`, `importGithubRepos`),
"Continue with GitHub" on `/login`, new `client/app/auth/callback/page.tsx`
(reads `#token=`, stores it, redirects to `/dashboard`), `/dashboard`'s
GitHub `SourceCard` replaced with a real "Connect GitHub" button and a
repo-picker section that opens automatically on `?installation=connected`
(fetches the workspace's installations, opens the most recently created
one — the redirect doesn't carry `installation_id`, so multiple
concurrent installations would all show the latest one's picker; a real
edge case, just not one this pass needed to solve).

**Verified**: server imports cleanly, live-reloads without error, `/dev/
github-app/new` returns 200, `POST /api/integrations/github/connect`
correctly 401s unauthenticated, existing email/password login regression-
tested (still works for the pre-existing test account), DB migration
columns/table confirmed via `psql`, `npx tsc --noEmit` clean on the
frontend. **Not verified by me**: the actual GitHub click-through (manifest
creation, installing the app on a real org) — that needs the user's own
GitHub account and can't be done headlessly.

### Slice 4 — multi-repo disambiguation (done)

User's question, paraphrased: once a workspace has ingested 10-15 repos,
how does the agent know *which* repo a question is about? Answer at the
time: it didn't — `app/agent/loop.py` force-injected a single `repo_id`
(from `get_seed_repo_id()`, the fictional Meridian corpus) into every
repo-scoped tool call, full stop. Any real multi-repo workspace would have
silently searched the wrong repo, or the seed repo, for every question.

**Two-part fix, mirroring the existing `known_accounts` pattern** (the
planner already resolves a customer name to an `account_id` the same way):

1. **Qdrant chunks are now tagged with `workspace_id` at ingest time**
   (`app/tasks/ingestion.py`, one line added to the existing per-chunk
   loop) alongside the `repo_id` tag they already had. `vector_search()`
   (`app/core/embedding/embedder.py`) gained a `workspace_id` param: pass
   a `repo_id` and it filters to that repo exactly as before (unchanged,
   zero behavior change for the single-repo/demo path); pass `workspace_id`
   with `repo_id=None` and it filters to every repo in that workspace at
   once, so relevance ranking across repos decides which repo's chunks
   actually answer the question. Repos ingested before this change have no
   `workspace_id` payload and won't be found this way — only affects the
   seed corpus, which is always addressed by explicit `repo_id` anyway.

2. **`answer_question()` (`app/agent/loop.py`) gained a `workspace_id`
   param**, only consulted when `repo_id` is omitted. Resolution:
   - no `repo_id`, no `workspace_id` -> unchanged, falls back to the seed
     repo (`get_seed_repo_id()`) — the original single-tenant demo path.
   - `workspace_id` given, workspace has 0 or 1 READY repos -> resolved
     the same forced way an explicit `repo_id` always was. No ambiguity,
     nothing new to reason about.
   - `workspace_id` given, 2+ READY repos -> **multi-repo mode**. The plan
     prompt (`app/agent/prompts.py`) grows a "Known repos in this
     workspace" block (id + name, same shape as `known_accounts`), and the
     planner is told: name a specific repo -> pass its exact id; ambiguous
     or no repo named -> omit `repo_id` entirely. `search_code` and
     `find_usages` (the two tools that are plain vector search under the
     hood) then search across the whole workspace via `workspace_id`.
     `trace_symbol`, `read_file`, `explain_why`, `check_conflict` can't —
     a Neo4j graph walk, a file read, and a provenance/conflict chain are
     inherently per-repo — so those four now return a clear tool error
     ("which repository? specify one of the known repos for this
     workspace") instead of crashing, when no repo could be resolved.
   - Same "never trust a guess" principle as the existing repo_id-override
     logic, just widened from one id to a set: a planner-supplied `repo_id`
     is only honored if it's actually in that workspace's known-repos list
     (`_run_one_call`'s `known_repo_ids` check); anything else is dropped
     rather than passed through, so a hallucinated repo id can't silently
     return empty results for the wrong repo.

**Deliberately out of scope, and said so explicitly rather than left
implicit**: `POST /api/agent/ask(/stream)` is still fully unauthenticated
(documented demo-scope decision, unchanged) — `workspace_id` on that
request is client-asserted, not verified against any session, same trust
boundary as everything else on that route today. `call-agent/orchestrator.py`
still has no workspace concept at all (hardcoded `photon` LiveKit room, no
login) — wiring a live call to a specific workspace is a separate, larger
"multi-tenant call agent" problem this pass didn't touch. The one reachable
integration point today is the web console's text-input fallback
(`client/app/call/page.tsx`), which now best-effort forwards
`getWorkspaceId()` from localStorage if the browser also has a dashboard
session.

**Verified live against the running stack** (Postgres/Qdrant/Neo4j, not
mocked): all 9 `test_agent_loop.py` + `test_verifier.py` tests and all 9
`test_tenancy.py` tests still pass unchanged. Directly exercised
`_run_one_call`'s new branching: a bogus planner-guessed repo id is
dropped and `workspace_id` is injected for `search_code`; a real
known-repo id is trusted through; `trace_symbol` with no resolvable repo
returns a clean tool error instead of crashing. Created a real workspace
with 2 READY repos + 1 still-INGESTING repo in Postgres and confirmed
`list_ready_repos()` returns only the 2 READY ones. Confirmed
`answer_question()`'s three resolution branches (0 repos, 1 repo, and the
existing seed-repo fallback) all complete without error. `npx tsc
--noEmit` clean.

### Ingest speed — 40.1s -> 16.5s, and it barely grows with repo size

The user pushed back that ~28s for 83 files was too slow (target: 5-15s
even for a big repo). Measured the phase breakdown rather than guessing:

```
+ 0.0s  clone starts      + 4.0s  clone done (already --depth 1)
+ 4.0s  manifest built    +14.0s  parse + import resolution done   (~10s)
+21.0s  first embedding batch                                      (~7s graph)
+40.0s  complete                                                   (~19s embedding)
```

Embedding was ~half the wall clock and **strictly sequential** — ~19
batches, each one network round-trip to the embedding API then Qdrant,
one after another. Replaced the loop with a `ThreadPoolExecutor`
(`settings.embedding_concurrency = 6`); the work is I/O-bound so the GIL
isn't the constraint. Per-batch error handling is unchanged, so one bad
batch still can't sink an ingest.

| repo | files | before | after |
|---|---|---|---|
| psf/requests | 83 | 40.1s | **16.5s** |
| httpie/cli | 236 | 56-62.8s | **17.3s** |

Zero `embed.batch_failed`, and verified the result is *correct*, not just
fast: `search_code` on the freshly ingested repo returns real chunks with
real locators. Note the new shape — **tripling the file count now adds
under a second**, because what's left is fixed cost (clone, parse, graph).
A `log.info("import.resolved")` firing 5,366 times per ingest was also
dropped to debug; measurably it was not the bottleneck.

### Estimated ingest time, calibrated from measurement

The user asked to show users how long importing N repos will take.
`app/services/estimate.py` + `POST /api/repos/estimate`:

- **Measured, not guessed** — seeds come from the runs above, and every
  ingest now records `Repo.ingest_seconds`, so after
  `MIN_SAMPLES_TO_CALIBRATE` (5) real imports the model re-fits to THIS
  deployment (least-squares on files -> seconds, with sanity guards for
  degenerate or nonsensical fits).
- **A range, never a single number** (0.7x-1.6x): ingest time varies with
  network and embedding-API latency, and false precision would be worse
  than a wide answer. Current output: 1 repo `10s-25s`, 5 repos
  `60s-2 min`, 20 repos `4 min-9 min`.
- The response carries `calibrated` and `sample_size` so the UI can say
  "estimated from N previous imports" rather than implying confidence it
  doesn't have.

**Honest limitation recorded in the module**: the two calibration repos
are close in size, so the slope is poorly determined and extrapolating to
a 5,000-file monorepo is not supported by this data.

**Measurement caveat**: a 5-repo concurrent calibration run gave much
worse per-repo numbers (40-81s) than solo runs, because the workers
contend. Estimates are built from solo measurements; concurrent imports
will be slower than predicted.

### Live — Sarvam switched on for the whole voice stack (STT + TTS)

`call-agent/.env` now sets `VOICE_STACK=sarvam` with the real
`SARVAM_API_KEY` (copied from `server/.env`, where the user had put it),
`SARVAM_TTS_MODEL=bulbul:v3`, `SARVAM_STT_MODEL=saaras:v3`. **The code
default is still deepgram** — this is a config switch, not a code change,
so removing those lines reverts everything.

**Measured TTS time-to-first-audio through the actual LiveKit plugin**
(WebSocket streaming, which is what a call pays — not REST synthesis).
Sarvam publishes no latency figures at all, so this is the only source:

| model | en-IN | hi-IN | te-IN | ta-IN |
|---|---|---|---|---|
| `bulbul:v3` | 1.06-1.31s | 1.09-1.11s | 1.25-1.43s | **1.90-2.11s** |
| `bulbul:v2` | 0.76s | 0.68-1.78s | 0.42-0.63s | 0.35-0.66s |

So v3 costs roughly **+0.5 to +1.4s of speaking latency** over v2, worst
in Tamil. On top of a ~2.2s turn that puts first audio at ~3.5-4.3s (v3)
vs ~2.6-3.0s (v2). `SARVAM_TTS_MODEL=bulbul:v2` is a one-line switch if
the demo needs the speed more than the prosody.

**Verified live, on a real LiveKit call** (synthesized the caller's Telugu
question with Sarvam TTS and published it as a real microphone track —
the only way to exercise Sarvam STT without a Telugu speaker):
`saaras:v3` transcribed **"బెంగళూరులో ధరలు ఎందుకు వేరుగా ఉన్నాయి? కారణం
చెప్పండి."** exactly, in native script, which is what makes the
script-based language detection work; `tts_language_switched language=te-IN`
fired; the answer came back spoken in Telugu.

**Two real defects that only a live call exposed:**

1. **The planner ignored non-English "why" questions.** Both the live
   Telugu turn and the earlier Tamil one fell back to a generic account
   lookup and answered "Bangalore is Calico Transit's home city" —
   grounded in real evidence, but not the question asked. The first
   language hint ("translate their intent") wasn't specific enough. It now
   gives an ordered procedure: translate to English FIRST, then apply the
   tool-matching rules to the English version, and it names the
   why-markers explicitly (`కారణం / ஏன் / क्यों`). After: all four
   languages pick `search_code + explain_why` and answer correctly, 2.1-2.9s.

2. **The abstention read internal tool names aloud.** Heard live, in
   Telugu: "నేను search_code, search_docs చూశాను..." — the agent
   pronouncing `search_code` mid-sentence. That breaks the voice rule
   "lead with the finding, not the method", which had only ever been
   applied to composed answers, never to the hand-written abstention. It
   no longer names tools in any language; the tool pills in the evidence
   panel are where that belongs.

English is unaffected: eval 16/16, median 2133ms. 99 call-agent tests pass.

**Known limit, unchanged**: STT accuracy is now the weakest link, not the
agent. One synthesized Telugu utterance came back with "ఎందుకు" (why)
transcribed as "ఇందుకు" (for this), turning the question into a statement
— the agent correctly abstained rather than guessing, but a mis-heard word
is a mis-answered question. `SARVAM_STT_LANGUAGE=te-IN` (instead of the
`unknown` auto-detect default) is the first thing to try if a specific
language dominates a call.

### Feature — multilingual voice (Telugu/Tamil/Hindi/English) behind `VOICE_STACK`

The user wants Indic language support and asked for the whole path behind
an env var. TTS was the easy third; the work is that **three things have
to line up — hear it, answer in it, speak it** — and the corpus is English
throughout, so the answer has to be composed in the caller's language
while citing English evidence.

**Env vars (all optional, defaults keep today's behaviour exactly):**

| var | default | meaning |
|---|---|---|
| `VOICE_STACK` | `deepgram` | `sarvam` swaps STT+TTS to Sarvam's Indic models |
| `SARVAM_API_KEY` | — | required only when `VOICE_STACK=sarvam` |
| `SARVAM_TTS_MODEL` | `bulbul:v3` | `bulbul:v2` is half the credits, fewer voices |
| `SARVAM_TTS_SPEAKER` | plugin default (`shubh`) | any v3 speaker |
| `SARVAM_STT_MODEL` | `saaras:v3` | with `language=unknown` it auto-detects |
| `AGENT_REPLY_LANGUAGE` | `auto` | pin to `te-IN`/`ta-IN`/`hi-IN`/`en-IN` to force one language |

**1. Hearing it** — `deepgram.STT(nova-3)` / `aura-2-thalia-en` (English
only) vs `sarvam.STT(saaras:v3, language="unknown")` /
`sarvam.TTS(bulbul:v3)`. Both plugin imports are lazy inside
`_build_stt`/`_build_tts`, so the default path doesn't need the package
installed. `livekit-plugins-sarvam` is in requirements.txt as optional.

**2. Knowing which language** — `call-agent/language.py`, by **Unicode
script**, not a model or an API call: Telugu, Tamil and Devanagari occupy
disjoint code-point blocks, so there is nothing to infer and it costs
nothing. It works identically behind either vendor, which is the point.
Code-mixed speech is the norm on Indian support calls, so the bar is a
*plurality* of letters (≥20%), not a majority — "sir webhook fail
అవుతోంది" is answered in Telugu, while one stray Indic word in a long
English sentence doesn't flip the reply. **Its one real limitation**: it
needs STT to emit native script. If an STT romanises Telugu as Latin
("meeru ela unnaru") every heuristic here sees English — that is what
`AGENT_REPLY_LANGUAGE` is for.

**3. Answering in it** — `build_compose_prompt(..., language=)` appends a
language block AFTER the few-shot examples (which are English and would
otherwise pull the answer back into English). It says: translate the
MEANING of the English evidence, but copy every `[ev_xxx]` marker and
every product/account identifier character for character, and keep each
claim's `text` a verbatim substring of the translated answer. Verified:
claims really do stay verbatim substrings in all four languages, so
`verifier.py` still strips uncited claims in-language.

**4. Speaking it** — `TransportAdapter.speak()` gained an optional
`language`. Sarvam's plugin exposes `update_options()`, so one
`AgentSession` retargets TTS per turn — a caller can switch from Hindi to
English mid-call without rebuilding the session. On Deepgram it is a
deliberate no-op (aura-2 is English-only): the answer still gets spoken,
just in an English voice, rather than erroring.

**Two bugs this work exposed, both language-independent:**
- `verifier.py`'s `_MARKER_RE` was `\[(ev_[0-9a-f]+)\]` — it matched
  nothing at all in `[ev_80abd768, ev_33f954d5]`, which the compose model
  routinely emits. On the no-structured-claims path a perfectly well-cited
  answer was therefore treated as having zero valid markers and **thrown
  away as an abstention**. Now matches ids anywhere; 5 new tests in
  `server/tests/test_verifier.py`.
- `_no_evidence_abstention()` said "I checked any tools" when no tool ran
  — broken English, shipped since Phase 3. That case now has its own
  sentence, and the whole abstention is localised (it is built without an
  LLM by design, so it can only be multilingual if pre-written).

**Also localised the 0ms fast path**: Indic greetings (నమస్కారం, வணக்கம்,
नमस्ते …) now hit the same instant canned reply English does — measured
**0ms**, versus 2.3s when it fell through to the full pipeline.

**A real failure caught while testing, worth keeping**: the Tamil version
of the Bangalore question first answered *"Bangalore is your home city"* —
grounded in real evidence, but the WRONG evidence. The planner was reading
Tamil and quietly fell back to a generic account lookup instead of
`search_code` + `explain_why`. Fixed with a plan-prompt hint (only added
for non-English turns) telling it the corpus and every tool argument are
English and to translate intent itself. **Planning deliberately stays in
English** — translating the planner's input buys nothing but a new way for
tool selection to go wrong.

**Verified end to end** through the real `Orchestrator` with a mock
adapter against the live brain-api, no Sarvam key needed for any of it:

```
   0ms  te-IN  నమస్కారం                    -> instant Telugu greeting
3619ms  te-IN  Telugu pricing question     -> correct, cited, in Telugu
2922ms  ta-IN  Tamil pricing question      -> correct, cited, in Tamil
2459ms  hi-IN  Hindi pricing question      -> correct, cited, in Hindi
2281ms  en-IN  English pricing question    -> unchanged
   1ms  --     "One sec, the phone."       -> silent
```

Citation markers stripped from every spoken line, present in every
published one. English path re-checked after the change: eval 16/16,
median 2053ms. 99 tests pass in `call-agent/tests/`, 9 in `server/tests/`.

**Untested, needs a key**: the Sarvam plugins themselves. Construction is
verified (both build, and `update_options` switches te-IN/ta-IN/hi-IN/en-IN
cleanly), but no audio has been synthesised — and **Sarvam publishes no
TTS latency figures**, which is the number that matters for a live call.
Benchmark v2 vs v3 TTFB before the demo.

### Fix — screen share was silently dead: `from_track()` is keyword-only

Found while the user live-tested screen share. They shared their screen and
asked "You please check that and help me open the search bar?" and got
"I don't have screen-sharing capabilities or visibility into your display."

The log said the track WAS subscribed
(`livekit_adapter.screen_share_subscribed participant=rishik`), the
utterance DID match `VISUAL_HINT_RE` ("help me open"), the small-talk gate
passed it through, Pillow was installed, and **no error appeared
anywhere** — worker log or server log.

**Root cause**: `rtc.VideoStream.from_track()` is **keyword-only**
(`(*, track, loop=None, format=None, capacity=0)`). The adapter called it
positionally, so it raised `TypeError` immediately — and that line sat
*outside* the pump's `try/except`, inside a task created with
`asyncio.create_task()` that nothing ever awaits. The exception was
therefore swallowed whole. Perfect silence: subscription logged, zero
frames, no traceback. This has been broken since Phase 4; the feature was
only ever tested by posting a frame straight to the brain-api, never
through a live LiveKit screen-share track, which is exactly the seam that
was wrong.

**Fixes:**
- `from_track(track=track, format=...)` — keyword.
- The stream is now created INSIDE the try, so any future failure is
  logged as `screen_pump_error` (with the exception type) instead of
  disappearing.
- New `screen_pump_started` and `screen_frames_flowing` (first frame, then
  every 30th) log lines. Silence used to be indistinguishable from
  "working fine, nobody asked a visual question yet."

**Regression guard** — `call-agent/tests/test_adapter_api.py`, static
checks that need no LiveKit room: the SDK signature really is keyword-only,
the adapter passes `track=`/`format=` as keywords with zero positional
args, and `from_track()` is lexically inside the pump's `try` block. 70
tests pass in that suite.

**A hypothesis the experiment killed**: the first suspicion was that
`AgentSession`'s RoomIO (`RoomInputOptions(video_enabled=True)`) was
consuming the track and starving our second `VideoStream`. Tested it
directly — published a real screenshare track into a scratch room,
subscribed, attached two `VideoStream`s to the same track: **both received
12 frames in 4s**. So two consumers are fine and `video_enabled` was left
alone. Worth recording, because "two readers on one track" is a plausible-
sounding explanation that would have sent the next person down the wrong
path.

### Fix — the agent was reading citation ids out loud

Caught by the user in a live call. The composed answer carries inline
`[ev_xxx]` markers (that's the grounding contract, and the evidence panel
renders them as chips), but TTS reads them literally, so Photon actually
said: *"Meridian is a B2B booking and scheduling platform, ev 20021cda."*

`call-agent/speech.py` (new) — `for_speech()` strips citation markers with
one regex, applied ONLY on the path to `adapter.speak()`. The structured
answer published to the browser keeps every marker, so citation chips,
claims and the verifier are all untouched — this is purely the split
between what's shown and what's spoken, which the voice rules already
assume ("never read a file path or line number aloud; it's shown on
screen instead").

The pattern handles the three shapes that actually occur: a lone marker,
several ids inside one bracket (`[ev_80abd768, ev_4879aa12]` — the compose
model really does emit these), and runs of adjacent markers. It also eats
the space *before* the bracket, so "platform [ev_x]." closes up to
"platform." rather than leaving "platform ." for the TTS to pause on. Non-
citation brackets are left alone (`The field [webhook_url] is empty`).

**Verified**: 9 new tests in `call-agent/tests/test_speech.py` (67 total
in that suite, all passing), plus end to end through the real
`Orchestrator` — spoken text "Bangalore has a reseller and referral
agreement in place for partner-tier accounts." vs the same turn's
published-to-UI text ending "...accounts [ev_80abd768]." Markers absent
from one, intact in the other.

### Fix — vision call 4.0s -> 1.3s, and a per-call TLS handshake tax on everything

The screen-share audit measured a visual turn at 7.4s, of which the vision
call alone was 4.0s. Benchmarked the same way as the text model: a real
frame with known strings drawn on it, **scored on whether the description
still READ the screen**, not just on speed.

| vision model | latency | read the screen? |
|---|---|---|
| `google/gemini-3.7-flash` (was) | 3.55s / 3.78s | 3/3 |
| `google/gemini-3.1-flash-lite` | 1.27s / 1.57s | 3/3 |
| **`google/gemini-3.5-flash-lite`** | **1.03s / 1.96s** | **3/3** |

`openrouter_vision_model` is now `gemini-3.5-flash-lite` — the same model
as the text path, so there is one model to reason about instead of two.

**Two things measured and deliberately NOT changed**: shrinking the frame
does not help (768px was no faster, and 512px was no faster AND dropped an
on-screen string — legibility is the whole point of a screen frame), and a
terser prompt capped at 120 output tokens was no faster than the careful
300-token one, so the prompt keeps its "do not guess at anything outside
the frame" wording. Both were plausible optimisations that the measurement
killed.

**Then a bigger find.** The same vision call took ~1.1s in the bench but
~2.0s through the agent. The difference: `openrouter.py` used
`httpx.post()`, which opens a **fresh TCP + TLS connection on every
call** — and a turn makes 2-3 of them, so the handshake was being paid 2-3
times per question, on the text path too. Replaced with one pooled
module-level `httpx.Client` (thread-safe, which matters because these run
in a thread executor; 300s keepalive).

**Results — visual turn 7.36s -> 3.25s:**

```
before:  vision 3997ms  plan 1363ms  compose 1954ms  = 7356ms
after:   vision 1257ms  plan 1034ms  compose  883ms  = 3250ms
```

**And the text path got faster for free**, mostly in the tail, because the
handshake tax is gone: median 2375 -> **2065ms**, p90 2976 -> **2181ms**,
max 3538 -> **2494ms**, and **12/12 under 3s** where it was 10/12 (the two
stragglers were exactly this).

**Accuracy re-checked after the change**, since a model swap is exactly
the kind of thing that trades correctness for speed: `evals/agent_eval.py`
base **24/24** (median 2013ms), HARD **15/16** (the same over-strict
tool-count threshold as before, not a wrong answer), and all 58
`call-agent/tests/` pass. One caveat seen in a separate 12-run sample: the
S3 retry-count question abstained once where it normally answers — the
eval hit it 3/3, so it reads as occasional compose variance rather than a
regression, but it is worth watching during a demo.

### Audit — screen share, code-level (2 real defects found and fixed)

The user asked for a code-only check of the screen-share path (no browser
test). Traced end to end: browser publishes SOURCE_SCREENSHARE ->
`livekit_adapter._on_track_subscribed` -> `_pump_screen_frames` (VideoStream
-> Pillow -> JPEG, 1024px cap, 1fps/0.3fps by speaking state) ->
`orchestrator.on_frame` (buffers only `source == "screen"`) ->
`_wants_visual_context` -> base64 -> `/api/agent/ask/stream` ->
`_decode_frame` -> `answer_question(screen_image_bytes=...)` ->
`describe_screen` -> a citable `source_type: "screen"` evidence item.
The plumbing itself was intact — including through the streaming refactor
(`_ask_brain` does forward `screen_image_base64`) and the small-talk gate
(every screen-shaped utterance classifies as ANSWER, verified in tests).

**Defect 1 — an already-active screen share was invisible.**
`track_subscribed` only fires for tracks arriving AFTER the handler is
attached, and `ctx.connect()` runs before it. So if the customer was
already sharing when the agent joined, no frame ever arrived — silently,
with the agent answering "I don't have access to your screen." **This is
the common case, not an edge case**: it happens on every worker restart
and every re-dispatch into a call already in progress (which happened
twice during this session alone). Fixed with `_attach_existing_screenshare()`,
sweeping `room.remote_participants` for a subscribed screenshare
publication at startup. Also added `_start_screen_pump()`, which cancels a
previous pump task before replacing it — stopping and restarting a share
mid-call used to leak the old task.

**Defect 2 — buffered frames never expired.** `latest_screen_frame_at`
was written and never read; `_wants_visual_context` only checked `is not
None`. When a share stops the pump ends but the last frame stays buffered
forever, so "what's on my screen?" twenty minutes later would confidently
describe a screen that no longer exists — real bytes, dead reality, and
the sort of thing the standing "never fabricate" rule exists to prevent.
Fixed with `SCREEN_FRAME_TTL_SECONDS = 30` (frames arrive at 0.3-1fps
during an active share, so anything older means sharing stopped); a stale
frame is dropped AND cleared.

**Also widened `VISUAL_HINT_RE`** for deictic phrasing — "what am I
looking at", "does this look right", "am I in the right place", "what does
this say", "where do I click". None contain the word "screen", so all were
previously missed. Safe to add because a frame is only ever attached when
one is genuinely fresh, i.e. the customer is sharing right now.

**Verified without a browser**: 58 tests in `call-agent/tests/` pass
(`test_screen_frame.py` covers camera-frames-are-not-screen-frames, fresh
frame used, stale frame dropped and cleared, no frame = not visual,
non-visual question never attaches one). Then the full path in one
process: a synthesized frame showing "Status: FAILING — last 47 deliveries
returned 401" pushed through the real `Orchestrator` against the live
brain-api produced "Your webhook delivery is failing with 401 errors
[ev_d66d6fb9]. The last forty-seven deliveries were rejected" — the "47"
appears nowhere in the seed corpus, so that is genuinely read off the
image, not recalled.

**Latency note**: a visual turn costs much more than a text one —
measured `vision 3997ms + plan 1363ms + tools 1ms + compose 1954ms =
7.4s`. The vision call alone is bigger than an entire normal turn. If
sub-3s matters for screen-share questions too, that call is the target
(smaller frame, or a faster vision model — the same measure-first
approach as `evals/agent_eval.py`).

**Untested by this audit** (needs a real browser, by definition): that
Chrome's screen-share track actually reaches the worker as
SOURCE_SCREENSHARE and that Pillow decodes those real frames. Every layer
above that is now exercised.

### Fix — a greeting cost 4.5s and a needless search (open-mic fallout)

Caught by the user in a live call, with the trace panel showing exactly
why: **"Hello. How are you?" → plan 2.4s → `search_docs` 660ms → compose
1.4s → 4.5s total**, to reply "Meridian is a B2B booking and scheduling
platform." Correct and cited, and completely unnecessary — nobody asked
what Meridian is. Root cause is the dropped wake word: with open mic,
EVERY finalized utterance became a full pipeline turn, so the agent both
burned latency on greetings and would talk over people who weren't
addressing it. This is the "lighter-weight local gate" the wake-word
removal note predicted would be needed.

**`call-agent/small_talk.py` (new)** — regex-only triage, no LLM, no
network, microseconds. Three outcomes: `GREETING` (instant canned line),
`IGNORE` (say nothing at all), `ANSWER` (the real pipeline).

The bias is deliberately asymmetric, and that's the whole design:
silently ignoring a real customer question is far worse than wasting a
turn on a greeting. So anything carrying a question mark, an interrogative,
or a product/account word (`webhook`, `northwind`, `pricing`, `401`, …)
goes to the pipeline no matter what else it matches — which is why
"northwind's webhooks are broken" (no interrogative at all) and "hi
Photon, why is Calico billed differently?" (starts with a greeting) both
still get answered. Filler is matched as CHAINED CLAUSES, not one
anchored alternation: real ambient speech is "yeah sorry", "One sec, the
phone." — the single-pattern version only caught one-word cases.

`orchestrator.py` calls it before anything else in `_handle_turn`, and
still publishes a `turn.fastpath` trace event so the advanced panel shows
a deliberate 0ms path rather than going blank as if the turn were missed
(`client/lib/trace.ts` renders it as "Greeting — answered locally" /
"Ambient speech — ignored"). The canned greeting makes no factual claim,
so the "no uncited claim" rule is untouched — there is nothing to cite.

**Verified**: `call-agent/tests/test_small_talk.py` — 37 cases, all
passing, built from real live-test utterances plus the voice-shaped cases
in `server/evals/agent_eval.py`'s HARD set. Then end to end through the
real `Orchestrator` against a mock adapter: greeting **0ms** + instant
reply, "One sec, the phone." **0ms** + silence, "yeah okay" **0ms** +
silence, and the Bangalore question still runs the full pipeline and
answers correctly.

`call-agent/.venv` now has pytest (dev-only, not in requirements.txt).

### Eval — is it still accurate after the latency work? (`server/evals/`)

The user pushed back on the latency result: fast is worthless if tool
selection got worse. Fair — the latency commit's evidence was 12
happy-path runs. So there is now a real eval harness,
**`server/evals/agent_eval.py`**, run it before trusting any change to
the model, the prompts, or the tool-selection guidance:

```
cd server && .venv/bin/python evals/agent_eval.py <model> [trials]
HARD=1 .venv/bin/python evals/agent_eval.py <model> [trials]
```

The model is a CLI arg rather than read from config specifically so two
models can be A/B'd on identical cases. Four things are scored per run,
independently: **tools** (did the planner call what the question needs,
and not a pile of extras), **content** (does the answer contain the known
ground truth from the seed corpus, and none of the known WRONG answers —
e.g. the documented "Bangalore isn't a special case" regression is a
literal `forbid` string), **abstain** (did it abstain exactly when it
should), **cites** (is every `[ev_xxx]` marker a real id from that turn —
a fabricated locator is a build-breaking bug, per the standing rule).

Two case sets. The base set is the demo scenarios. The HARD set exists
because the base set is written English and **a voice agent never gets
written English** — it includes unpunctuated lowercase ASR output, a
disfluent transcript ("uh so the webhooks for north wind are failing can
you check"), an account referenced only indirectly ("our Mumbai
customer"), a customer that doesn't exist (must abstain, not invent),
ambient small talk that open-mic will pick up mid-call, and a question
whose answer is nowhere in the corpus.

**Results — `gemini-3.5-flash-lite` (the new, fast model):**

| set | runs | tools | content | abstain | cites | all | median |
|---|---|---|---|---|---|---|---|
| base | 24 | 24/24 | 24/24 | 24/24 | 24/24 | **24/24** | 2216ms |
| hard | 16 | 15/16 | 16/16 | 16/16 | 16/16 | **15/16** | 2288ms |

The single flag was an over-strict threshold in the eval, not a bad
answer: one `conflict-docs` run called 4 tools where the case allowed 3.
Its answer was correct ("the code actually does three retries over a much
shorter window [ev_...] rather than five over 24 hours [ev_...]").

**A/B against the old `deepseek-v4-flash` on the identical base set**:
**0/8 runs passed** before I stopped it (2.9-54.9s per run — the run
itself was taking minutes). Failure modes were the ones already
documented above: empty first-round plans (S1 #2, S2 #1 called NO tools
at all and abstained), and "I wasn't able to compose a reliable answer
from the evidence I found" — the compose-JSON parse failure — on S2, S3
and even the trivial `list_accounts` case. The one run that did answer
(S1 #1, 18.1s) missed the secret-rotation half of the answer.

**Caveat on that A/B, stated plainly**: it is not a clean model-only
comparison. Both arms ran the NEW prompts (single-line JSON, 35-word cap,
shortest-substring claims), so some of deepseek's compose failures may be
that model reacting badly to the tightened output format rather than a
pre-existing weakness — though the empty-plan and compose-parse bugs were
both documented against it BEFORE any of this work. The defensible claim
is narrow and sufficient: **the current model+prompt combination is
accurate on this eval and the old one is not**, so the latency work did
not trade accuracy away.

**What this does NOT prove**: the corpus is a fixture, so these numbers
measure the loop's behaviour on known-answer questions, not open-domain
accuracy. Tool selection is stable across the 40 runs here; it is not
guaranteed for question shapes nobody has written a case for. Add a case
when a new failure mode shows up — that's what the file is for.

### Fix — turn latency: 39.2s -> 2.4s median (LLM was 97% of it)

The trace panel above immediately paid for itself: it showed a real S2
turn as **39.2s total = 2.4s of tools + 38.0s of LLM**, with a single
compose call costing 27.4s. The user asked to get the whole turn under
3s. Everything below was measured, not assumed — the bench used the
agent's OWN plan/compose prompts against a real 22-item evidence set.

**1. Model (the big one).** Measured per-turn LLM time (plan + compose):

| model | plan+compose | JSON |
|---|---|---|
| `deepseek/deepseek-v4-flash-0731` (was) | 11.3s / 16.6s | 1 of 2 runs failed to parse |
| `openai/gpt-oss-120b` | 16.8s / 18.3s | both failed |
| `google/gemini-3.7-flash` | 6.1s / 7.6s | compose failed both |
| `anthropic/claude-haiku-4.5` | 4.6s / 6.1s | both failed (hit token cap) |
| `google/gemini-3.1-flash-lite` | 3.7s / 9.7s | ok, unstable |
| **`google/gemini-3.5-flash-lite`** | **2.43s / 2.49s** | **clean both** |

`openrouter_chat_model` is now `google/gemini-3.5-flash-lite`. **Do not
swap it without re-running the same measurement** — vendor benchmarks
don't predict this; deepseek was chosen originally for exactly the same
"it's a flash model" reasoning and was the slowest thing tested.
(Vision still routes to `google/gemini-3.7-flash`, unchanged.)

**2. Dropped the second planner round when round 1 found evidence**
(`loop.py`). It cost a full LLM round-trip (~1s) on the critical path and
returned "no further tools needed" nearly every time. Round 2 still runs
when round 1 came back empty — the case it actually exists for. Emits a
`plan.skipped` trace event so the panel shows it wasn't silently lost.

**3. Output tokens, not prompt size, are the latency.** Measured: a 13%
smaller plan prompt changed nothing (770ms either way), but a
pretty-printed plan cost 1421ms vs 770ms compact, and compose at 257
output tokens cost 1468ms vs 814ms at 91. So:
- both prompts now demand single-line JSON, no indentation;
- the voice answer cap dropped 60 -> 35 words (a latency rule as much as
  a style one — and better for a live call anyway);
- `claims[].text` asks for the SHORTEST verbatim substring rather than
  the whole sentence, halving the duplicated text in the output. Still a
  real substring, so `verifier.py`'s strip-the-uncited-claim logic works
  unchanged (and more surgically);
- `max_output_tokens` 800 -> 400 (plan), 1500 -> 700 (compose).

**4. Compose evidence capped at 6 items** (`COMPOSE_EVIDENCE_LIMIT`),
selected **round-robin across tools** via `_select_compose_evidence()`,
not by slicing the flat list. A plain slice would hand the whole budget
to whichever tool ran first and could drop the second tool's best item —
which for S2 is the one Slack message the entire answer depends on. NOT
a snippet-length cut: truncating inside a snippet is what silently hid
the S3 retry paragraph once before.

**Measured after (12 runs, 4 questions, live stack):**

```
min 1864ms   median 2375ms   p90 2976ms   max 3538ms   under 3s: 10/12
```

All 12 returned `confidence: high` or a correct clean abstention; the 4
agent-loop pytest tests still pass; S1 (Northwind 401s), S2 (Bangalore
partner rate, still citing the Slack thread), S3 (3 retries) and the
off-topic abstention were each re-verified end to end.

**Honest caveats.** (a) Both >3s runs were plan-call jitter upstream
(1.8-2.2s vs a 0.8-1.0s norm), not our code — OpenRouter latency variance
is still the dominant risk, as it was for deepseek. (b) These totals
include ~0-0.5s of tool time because our tools are fast (`get_account` is
a cached JSON read; `search_code` is ~0.4s). **If a deployment's tools
really cost 2-2.5s, the floor is ~2.0s of LLM + that, i.e. ~4-4.5s — the
under-3s result depends on sub-second tools, not just on the model.**
(c) The remaining floor is two sequential LLM calls (~0.8s plan + ~1.0s
compose); getting materially below that needs a structural change, e.g.
streaming compose into TTS so time-to-first-audio beats time-to-full-
answer, which the verify step currently forbids (it needs the complete
composed JSON before anything may be spoken).

### Feature — live pipeline trace ("Advanced" panel beside the captions)

The user asked to see latency in real time, which tool is being called
internally, and the whole flow — in an advanced panel beside the
captions. The blocker: `answer_question()` only ever returned its
finished `tool_trace` at the end, so nothing at all was observable
*during* a turn (and turns run tens of seconds — see the DeepSeek latency
notes above). Every layer needed a way to report progress as it happened.

**Server — `server/app/agent/events.py` (new)**: `TurnTracer` stamps each
event with `t` (ms since turn start) and `seq`, and forwards it to a
plain callable sink. Deliberately a callable, not a queue/stream/socket:
`app/agent/` must stay transport-free (Section 5), so the loop never
learns who is listening. A tracer with no sink is a no-op, and a sink
that raises is caught — tracing can never break the turn it traces.

- `app/agent/loop.py` — `answer_question(..., on_event=None)`. Emits
  `turn.start`, `vision.start/done`, `plan.start/retry/done` (per round,
  with the chosen tools), `tool.start`/`tool.done` (per call, with ms /
  status / evidence count), `evidence.gathered`, `compose.start/done`
  (incl. `parsed:false` for the known DeepSeek JSON flakiness),
  `verify.done` (claims in vs kept), `turn.done`. `tool.start` fires
  BEFORE the await, so a tool shows as running for the whole time it
  actually runs. The answer is byte-for-byte identical with or without a
  sink.
- `app/routers/agent.py` — `POST /api/agent/ask/stream`, SSE. Distinct
  from the pre-existing `/ask?stream=true`, which runs the entire turn
  first and only then chunks the finished answer word by word — useless
  for latency, since nothing is sent until everything is over. A
  disconnecting client cancels the turn task rather than orphaning it.

**call-agent — the transport contract widened to 4 methods**: `speak` is
the answer for the human's ear; `publish_event` is everything *about* the
answer that belongs on a screen. `adapters/base.py` documents it as
optional-by-degradation (a platform with no data channel no-ops it and
loses only the panel). `livekit_adapter.py` implements it via
`publish_data(..., reliable=True, topic="photon.trace")`.
`orchestrator.py` now consumes `/ask/stream` instead of `/ask` and
republishes every event into the room, tagged `source: "voice"` and a
`turn_id`. **This closes the Phase 5 "known gap" above** for trace data
(the structured answer/evidence still isn't republished — only the
pipeline events are).

**Client** — one reducer, two feeds:
- `client/lib/trace.ts` — `applyTraceEvent()` folds flat events into
  `TurnTrace` {stages, tools, totals, status}. Dedupes by `seq` (a
  re-delivered data packet would otherwise duplicate a tool row) and
  marks any still-running stage as failed when the turn ends, so nothing
  spins forever.
- `client/app/call/TraceBridge.tsx` — voice feed, filters
  `RoomEvent.DataReceived` on the `photon.trace` topic.
- `client/app/call/page.tsx` — the text path now reads the SSE stream
  (with proper partial-frame buffering) instead of a plain POST, feeding
  the same reducer; both panels moved OUT of the connected-only branch,
  since the text fallback works without joining the call.
- `client/app/call/AdvancedPanel.tsx` — ticking elapsed clock (100ms,
  frozen on the server's authoritative total once done), a voice/text
  badge, per-stage rows with tool calls nested under the plan round that
  chose them, bars scaled to the slowest step *in that turn*, and a
  totals footer (total / tool ms / llm ms).

**Live-verified in a real browser**, text path end to end: the panel
showed `Plan · round 1` running, then "chose search_code, explain_why"
at 8.8s with both tools live in amber "running…", then each completing
at 1.2s, `Plan · round 2` "no further tools needed" 1.7s, `Evidence
deduped 9 unique of 10`, `Compose answer` 27.4s, `Verify citations
4 claim(s) kept` 1ms — footer `total 39.2s · 2 tool calls 2.4s · llm
38.0s · confidence high`, alongside a correct grounded answer.

**What that immediately exposed** (the point of building it): on a real
S2 question, tools are 2.4s of a 39.2s turn — **97% of the latency is
LLM time**, and 27.4s of it is the single compose call. Any future
latency work belongs there (smaller compose prompt, a faster model, or
streaming compose), not in tool optimization.

**Not verified live**: the voice half. The events are published over the
LiveKit data channel, but the running worker was started before these
changes and its prewarmed process still holds the old modules — **the
`worker.py dev` process must be restarted for voice turns to emit any
trace at all.** The user is running the live call tests.

### Change — captions split by speaker (caller vs Photon)

The user asked for the live captions to visibly separate the human from
the agent. Previously `CaptionsBridge.tsx` threw the attribution away —
its handler took only `segments` and pushed bare strings, so a human
question and Photon's spoken answer rendered as identical grey lines in
one undifferentiated list, and interim segments appended as duplicate
half-sentences (the old code sliced to the last 8 with no dedup by id).

- `client/lib/captions.ts` (new) — the `Caption` type (id, speaker,
  display name, isLocal, text, final, at) and `mergeCaption()`, which
  **upserts by LiveKit's segment id** rather than appending. Interim
  results arrive repeatedly under the same id with growing text and
  `final: false`, then once more `final: true` — appending blindly is
  what made one sentence render as a dozen fragments.
- `client/app/call/CaptionsBridge.tsx` — the `transcriptionReceived`
  handler now takes its second arg (`participant?: Participant`) and
  resolves who spoke: agent if `participant.kind === ParticipantKind.AGENT`
  (value 4 in `@livekit/protocol`'s `ParticipantInfo_Kind`), if the
  identity starts with `agent-`, **or if participant is undefined** —
  the agent's own TTS transcript arrives with no participant attached,
  so an undefined participant means the agent, not an unknown human.
  Local participant is labelled "You", other humans by name/identity,
  the agent as "Photon".
- `client/app/call/CaptionsPanel.tsx` (new) — chat-style transcript:
  caller left-aligned in slate with their initial as an avatar, Photon
  right-aligned in indigo with a "P" avatar, each bubble carrying a name
  label; non-final segments render dimmed/italic with a pulsing caret so
  it's visible which line is still being transcribed. Colour legend in
  the header, autoscrolls to the newest line.
- `client/app/call/page.tsx` — captions state is now `Caption[]` merged
  via `mergeCaption`; renders `<CaptionsPanel>` instead of the inline
  20px-tall grey `<p>` list.

**Verified**: `npx tsc --noEmit` clean, and live in a real browser —
joined the `photon` room, the worker joined and spoke its announcement,
which rendered correctly as a right-aligned indigo "Photon" bubble. The
caller-side (left, slate) rendering was **not** confirmed against real
STT: the user stopped the live test to run their own, and the synthetic-
speech harness kept losing the race with LiveKit's `RoomIO`, which binds
STT to the *first* remote participant that joins — with the browser
joining first, the fake caller's audio was never transcribed at all.
Worth knowing independently of this change: **whoever joins the room
first is the only person the agent listens to.**

### Feature — screen-share vision, actually wired up (was a stub since Phase 4)

The user asked to build this. It was previously a deliberate stub —
`_wants_visual_context` detected a visual question and set
`screen_context = "a screen frame was captured but visual analysis isn't
wired up yet"`, and the compose LLM would honestly say it had no screen
access. Now real:

- **`server/app/tools/evidence.py`** — added `"screen"` to
  `VALID_SOURCE_TYPES`. A screen-frame description is folded into the
  evidence set as a real citable item (`source_type: "screen"`,
  `locator: "screen:{sha1(image_bytes)[:10]}"` — content-addressed so two
  different frames never collide), exactly like a tool result. This was
  a deliberate design choice over passing the description as free-text
  "context": the same "no uncited claim" rule now applies to what's on
  screen as to everything else, and it gets a real `[ev_xxx]` chip in the
  evidence panel.
- **`server/app/agent/loop.py`** — `answer_question()` gained a
  `screen_image_bytes` param. If present, calls `describe_screen()` once
  up front, wraps a successful result as evidence (see above), and skips
  it entirely on failure — never fabricates a description. Also: the
  `if not dedup_evidence: abstain` early-return now naturally allows a
  screen-only answer through if the frame was the only evidence gathered.
- **`server/app/agent/prompts.py`** — `build_compose_prompt()` dropped
  its `screen_context` parameter entirely; passing the same description
  twice (once as free-floating "context", once as citable evidence)
  risked the compose LLM treating the screen half as not needing
  citation. It's evidence now, full stop.
- **`server/app/routers/agent.py`** — `AgentAskRequest` gained
  `screen_image_base64: Optional[str]`, decoded and passed through.
- **`call-agent/orchestrator.py`** — `_handle_turn` now base64-encodes
  `state.latest_screen_frame` and sends it as `screen_image_base64`
  instead of the old stub string. `VISUAL_HINT_RE` was also broadened —
  the ORIGINAL pattern missed a real utterance from testing ("check my
  screen and help me open the search bar?"), matching only "where do i" /
  "this screen" / "on my screen" style phrasing. Now also matches
  "check/look at/share/see (my/the) screen", "what's on (my/the) screen",
  and "help me find/open/see/locate".

**Real blocker hit and resolved while building this**: the obvious choice
was Gemini's direct API (`gemini_vision_model = gemini-3.7-flash`,
already configured, unused since Phase 3). Testing it directly hit the
**exact same free-tier quota wall** as Gemini text generation did in
Phase 3 — confirmed with `ResourceExhausted` after only a handful of
test calls (some earlier calls that looked like plain `DeadlineExceeded`
timeouts were very likely the same quota problem manifesting as a hang
rather than a clean rejection). Fixed the same way Phase 3 fixed it:
moved vision to OpenRouter too. `google/gemini-3.7-flash` is available
there — same model, verified directly to work (fast, accurate, correctly
read text out of a test screenshot) — just billed through OpenRouter
instead of hitting the Gemini API's free tier. `app/core/llm/
gemini_vision.py` was deleted; `app/core/llm/vision.py` replaces it,
calling the new `sync_chat_vision()` in `openrouter.py`. `config.py`'s
`gemini_api_key`/`gemini_chat_model` are now fully unused (kept only
because the key is already in `.env`) — **do not route anything through
Gemini's direct API again without a paid tier**, both the text and vision
models on that key are confirmed to hit the same 20-req/day wall.

**Verified end to end, live, not just unit-level**: `POST /api/agent/ask`
with a real screenshot correctly described the exact text in it (a
search box reading "webhook secret rotation"), the planner intelligently
also called `search_docs` and connected the screen content to real
Meridian documentation (signing-secret rotation), and the final answer
cited both the screen evidence and the doc evidence correctly —
`confidence: high`, not abstained. Then re-tested through
`orchestrator.py` directly (mock adapter, real frame bytes, real HTTP
call to the live brain-api) with the exact phrase that had failed before
("check my screen and help me open the search bar?") — now gets a real,
helpful, cited answer instead of "I don't have access to your screen."

### Fix — planner over-calling tools, plus a wrong-answer regression from fixing it

The user asked to check tool-calling latency, suspecting the planner was
calling tools it didn't need. Confirmed directly: a generic "I would like
to know about Meridian" question was calling `search_docs` AND
`search_code` AND `list_accounts` AND `get_incidents` — four tools for a
question that only needed one. Root cause in `app/agent/prompts.py`'s
plan prompt: "call at most 4 tools this round" is a ceiling with no
incentive toward economy, so the planner defaulted to "gather everything
plausible" rather than being selective.

Fixed by rewriting the plan-prompt guidance to explicitly say most
questions need exactly 1 tool, occasionally 2, with worked examples
mapping question shape to tool choice (account questions -> account
tools, generic product questions -> one search_docs call, not four
tools "to be safe"). Re-verified: the same Meridian question now calls
just `search_docs`; an account question calls only account-scoped tools.

**Caught a real regression from this fix while re-verifying S2**: with
the new "call only 1 tool" framing, the Bangalore pricing "why" question
started calling `explain_why` alone — and got a **wrong answer**:
"Bangalore isn't a special case — it's one of five launch cities with a
base fare table," flatly contradicting the actual `PARTNER_CITY_RATES`
branch verified repeatedly earlier in this build. Cause: `explain_why`
has to self-locate the relevant code from the query text alone, and
without `search_code` alongside it as an independent check, it locked
onto the wrong file (`rate_service.py`'s base-fare table instead of
`pricing.py`'s partner-rate override) and confidently explained the
wrong thing. Fixed by special-casing "why does this code/behavior exist"
questions to call `search_code` + `explain_why` together — re-verified:
correct, fully-grounded answer, still only 2 tool calls (down from the
original 4, not regressed back to it).

**Lesson worth keeping in mind**: minimizing tool calls trades off
against redundant cross-checking. Cutting straight to "1 tool always" is
unsafe for any question where a single tool can silently mis-locate its
target — worth watching for the same failure mode in other tools if
future prompt tightening happens.

### Change — dropped the "Photon" wake word, open mic by explicit request

After the HTTP-client fix above, the user re-tested and reported the
agent still only responded to the initial greeting, nothing else. The
actual log line: they said **"Hello. How are you?"** — no wake word.
`orchestrator.py`'s original design (build plan Phase 4: "explicit
address, not open mic") only forwarded a turn that literally started with
"Photon" — that gate was working exactly as designed, it just wasn't
what they expected in the moment.

The user then explicitly asked to drop the wake word entirely: "dont
need any wakeup word let it start the communication." Done —
`orchestrator.on_speech` now forwards every finalized turn to the brain-
api unconditionally, no gating. `WAKE_WORD_RE` and the wake-word-strip
logic are removed. The client header text (`client/app/call/page.tsx`)
updated to match ("no wake word needed" instead of "say Photon").

**Trade-off now in effect, worth knowing**: with no wake word, side
comments, talking to someone else on the call, or ambient chatter all get
sent to the agent as if they were real questions. The "abstain over
guess" rule still applies, so irrelevant chatter should produce a clean
abstention rather than a fabricated answer (verified: "Hello. How are
you?" through the open-mic path now correctly returns "I don't have
evidence for that..." rather than inventing small talk) — but it does
mean the agent evaluates every utterance, which costs a real API call
each time and could talk over a live conversation if someone's mid-
sentence about something unrelated. If that proves noisy in practice,
reintroducing a lighter-weight local gate (e.g. only calling the brain-api
for utterances that look like questions) would be the next place to look
— not done here since it wasn't asked for.

Also fixed while making this change: the still-running-from-the-previous-
fix-test worker had to be restarted and the agent re-dispatched into the
live `photon` room via LiveKit's Agent Dispatch API (same situation as
the previous fix — killing the worker process drops its agent
participant, and an already-active room doesn't automatically get a new
one), so the room the user is testing in was continuously fixed forward
rather than requiring them to leave and rejoin.

### Fix — the worker closed its own HTTP client right after startup

The user reported audio/TTS not working and asked to check the logs.
Investigated with a rigorous live test (not guessing): synthesized real
speech with macOS `say -o ... --data-format=LEI16@16000`, published it as
a fake microphone track into an isolated LiveKit room via `rtc.AudioSource`
+ `rtc.LocalAudioTrack` (paced in real time, not dumped as one blob), and
watched the worker's own logs.

**What the logs showed**: STT transcribed correctly, wake-word detection
fired, `orchestrator.speech_finalized` logged the right text — but then
`orchestrator.brain_api_error error='Cannot send a request, as the client
has been closed.'`, and the agent spoke its fallback error line instead
of a real answer. Root cause in `call-agent/worker.py`: `entrypoint()`
called `await adapter.start()`, which returns as soon as the
`AgentSession` is up and the announcement has played — it does **not**
block for the life of the call. Execution fell straight through to the
`finally` block, which closed the orchestrator's `httpx.AsyncClient`
while the session kept running in the background. Every real question
asked any time after startup hit a closed client.

Fixed by blocking `entrypoint()` on `ctx.room`'s `"disconnected"` event
before falling into `finally`, so the orchestrator (and its HTTP client)
now lives for the actual duration of the call.

**Re-verified with the identical synthetic-speech method after the fix**:
same question, this time producing a real, fully-grounded, cited answer —
"Bangalore has a special case because Meridian has a reseller/referral
agreement with a partner (BLR Mobility Partners) that gives partner-tier
accounts a 0.88x commission rate in Bangalore only [ev_80abd768,
ev_94f68467]. This is a deliberate business decision, not a bug
[ev_7fa701ec, ev_646abbce]." — confirmed spoken (`conversation_item_added
{role: assistant, ...}`), not just composed. Took ~80s end to end,
consistent with the documented DeepSeek latency variance, but completed
correctly.

**Process note**: while debugging, I killed the running worker to test
the fix, which also dropped the agent's connection to the `photon` room —
where a participant named `rishik` (very likely the user testing live)
was already present. Restarted the worker immediately after: their
existing browser tab should reconnect on its own via LiveKit's automatic
reconnection, but flagging this here in case anything looked like it
glitched mid-session.

### Fix — the join page didn't actually look like a meeting

The user caught a real miss after Phase 5 shipped: `client/app/call/`
never rendered any video. It attached remote *audio* to a hidden `<div>`
and called it done — no video tiles, no participant grid, no visible
screen-share viewer, despite the plan's Section 10 explicitly asking for
"video tiles". The transport itself was genuinely real LiveKit (verified
in Phase 4), but the UI read as a chat app with buttons, not a call.

Fixed by adopting `@livekit/components-react` + `@livekit/components-styles`
(installed, confirmed peer-dep compatible with the already-installed
`livekit-client@2.22.0`/React 19) instead of hand-rolling tile rendering:
`<LiveKitRoom serverUrl token connect audio>` wrapping the `<VideoConference>`
prefab gives real participant tiles, a proper control bar (mic/camera/
screen-share/chat/leave), and screen-share handling for free. A small
`CaptionsBridge` component (using `useRoomContext()` inside the
`LiveKitRoom` tree) forwards STT transcriptions back out to the page so
captions can sit outside `VideoConference`'s own layout. The evidence
panel stays a sibling next to it, not inside — matches the plan's "beside
the video, not inside it."

**Live-verified, not just compiled**: joined as a browser participant —
got a real named video tile ("TestUser") with mic/camera/share-screen/
chat/leave controls, confirmed via console logs as a genuine LiveKit
connection (`connected to Livekit Server ... region: India South`, real
`connecting -> connected` state transitions, not mocked). Then started
`call-agent/worker.py` and it joined the same room as a second tile
(`agent-AJ_...`), with live captions showing real STT transcription of
actual microphone audio in real time. This is now a genuine two-
participant meeting view, not a hidden-audio chat page.

### Phase 4 — live call transport (done)

New top-level service, `call-agent/`, with its **own venv** — same reason
as the CLAUDE.md gotcha above: `livekit-api` force-upgrades `protobuf` and
would break `server/.venv`'s Gemini/embedding stack if shared. `call-agent/`
and `server/` only ever talk over plain HTTP (`POST /api/agent/ask`).

- `call-agent/adapters/base.py` — the transport boundary contract: three
  methods (`speak`/`cancel_speech`/`announce`, implemented by a concrete
  adapter, called by the orchestrator) and two callbacks (`on_speech`/
  `on_frame`, implemented by the orchestrator as `SessionCallbacks`,
  called by the adapter). Split into two Protocols since the plan's single
  combined code block conflates "implemented by" and "called by" — see
  the module docstring for why.
- `call-agent/orchestrator.py` — `Orchestrator` (implements
  `SessionCallbacks`): explicit wake-word gating (only a turn starting
  with "Photon" triggers anything — see build plan Phase 4 for why this
  beats open-mic/turn-detection), rolling transcript, latest-screen-frame
  buffer (gated on `source == "screen"`, camera frames are dropped),
  `POST {BRAIN_API_URL}/api/agent/ask` on every addressed turn, hands the
  answer to `adapter.speak()`. Zero LiveKit imports — verified directly by
  running it against a `MockAdapter` with no transport in the loop at all
  (see verification below), same spirit as `server/tests/test_agent_loop.py`.
- `call-agent/adapters/livekit_adapter.py` — the one `TransportAdapter`
  implementation. `AgentSession(stt=deepgram.STT(model="nova-3"),
  tts=deepgram.TTS(model="aura-2-thalia-en"), vad=silero.VAD.load())`, a
  custom `Agent` subclass whose `on_user_turn_completed` hook forwards
  every turn to the orchestrator's `on_speech` and always raises
  `StopResponse()` — LiveKit's own (unconfigured) LLM node never runs;
  the orchestrator/brain-api is the only thing that ever composes an
  answer. Screen-share frames are pumped from a dedicated `track_subscribed`
  handler filtered to `rtc.TrackSource.SOURCE_SCREENSHARE` (never
  `SOURCE_CAMERA`), throttled to ~1fps speaking / ~0.3fps idle via
  `session.user_state`, resized/JPEG-encoded via Pillow, capped at 1024px.
- `call-agent/worker.py` — entrypoint; `python3 worker.py dev` for local
  testing, `start` for prod. Every API call/method used here
  (`AgentSession`, `Agent.on_user_turn_completed`, `StopResponse`,
  `RoomInputOptions`, `rtc.VideoStream.from_track`, `rtc.TrackSource`) was
  verified against the actually-installed `livekit-agents==1.7.0`'s real
  signatures via `inspect`, not assumed from training-data familiarity
  with older SDK versions — this API has moved a lot across majors.
- `client/app/api/livekit-token/route.ts` — mints a join token
  server-side (holds `LIVEKIT_API_SECRET`, never sent to the browser).
- `client/app/call/page.tsx` — the join page: mic/screen-share toggles via
  `livekit-client` (`setMicrophoneEnabled`/`setScreenShareEnabled`), live
  captions from `RoomEvent.TranscriptionReceived`, and a text-input
  fallback that calls `POST /api/agent/ask` directly (bypassing voice
  entirely) — satisfies the Definition of Done's "text-input fallback
  works if audio fails" without depending on the call-agent service at all.

**Live-verified, not just written and assumed correct** (this environment
has no real microphone, so audio content itself can't be tested, but
everything up to and around that boundary was):
1. `python3 worker.py dev` registered with LiveKit Cloud for real —
   `"registered worker" {"id": "AW_...", "url": "wss://photon-
   ter1p8y0.livekit.cloud", "region": "India South"}`.
2. A real second participant (a throwaway script using `livekit.rtc.Room`,
   playing the human) joined a real room and the worker was dispatched,
   joined as `agent-AJ_...`, and ran `entrypoint()` end to end with no
   errors.
3. Real Deepgram STT and TTS WebSocket connections were established
   (visible in the worker's own debug log).
4. `announce()` actually spoke: the log shows `conversation_item_added
   {role: assistant, text: "Hi, I'm Meridian's support agent..."}` —
   confirms `adapter.speak()` -> `session.say()` -> real TTS synthesis
   worked, not just that the call didn't throw.
5. Clean shutdown on participant disconnect, confirmed in the log.
6. `orchestrator.py` run standalone against a `MockAdapter` (no LiveKit
   object anywhere in the process): unaddressed speech correctly ignored,
   interim (non-final) speech correctly ignored, camera frames correctly
   never treated as screen frames, and an addressed question ("Photon,
   why does pricing have a special case for Bangalore?") correctly
   triggered a real HTTP call to the live brain-api and produced a
   fully-grounded S2 answer via `adapter.speak()`.
7. The Next.js `/call` page and `/api/livekit-token` route both compile
   and serve (200) under `next dev`; the token route mints a real,
   correctly-scoped JWT.

**Known gaps, honestly**: real audio in/out (a human speaking, hearing the
agent) is untested — this environment has no microphone/speaker to drive
that, so it needs an actual human on a real call, same as the Phase 0
LiveKit box that was never checked off. Screen-frame capture
(`_pump_screen_frames`) is written against a verified API but never
exercised with a live screen-share track — lowest priority per the plan's
own cut order ("Screen share / vision — First to go"). `RoomInputOptions`
logged a deprecation warning in favor of `RoomOptions` in 1.7.0 — still
functionally correct, not yet migrated.

Not built yet: the evidence panel (Phase 5) — the join page above is
functional but has no citation/evidence UI yet, just live captions and a
plain text Q&A fallback.

### Phase 3 — agent loop (done)

- `app/agent/llm.py` — `generate()` (OpenRouter via `app/core/llm/
  openrouter.py`, `json_mode` supported) + a tolerant `extract_json()`
  (strips markdown fences, falls back to regex-extracting the first
  `{...}` block).
- `app/agent/prompts.py` — `SYSTEM_RULES` (the three non-negotiable rules
  + voice rules for a live call), plan-prompt builder (includes the tool
  schema list and the known-accounts directory so the planner can map a
  customer name like "Northwind" to `acct_northwind` without a round-trip),
  compose-prompt builder with one grounded-answer few-shot and one clean-
  abstention few-shot.
- `app/agent/verifier.py` — deterministic, no LLM call: strips any claim
  whose `evidence_ids` aren't fully in the tool-verified set, forces full
  abstention if >50% of claims fail or zero evidence came back at all,
  downgrades confidence to `low` on any stripped claim.
- `app/agent/loop.py` — `answer_question(question, repo_id=None,
  screen_context=None)`: plan (max 4 calls/round) → execute tools in
  parallel via `asyncio.gather` → up to `MAX_ROUNDS=2` rounds, `
  MAX_TOOL_CALLS_TOTAL=6` → compose → verify. `repo_id` defaults to
  `get_seed_repo_id()` if not passed.
- `app/routers/agent.py` — `POST /api/agent/ask` (+ `?stream=true`, which
  chunks the already-verified answer word-by-word over SSE rather than a
  true token stream — verification needs the complete composed JSON first,
  so real mid-generation streaming isn't compatible with the verify step).
- `tests/test_agent_loop.py` — `test_agent_package_has_zero_transport_imports`
  is a static AST check (no LLM call) that nothing under `app/agent/`
  imports anything transport-shaped; the other three exercise S1, S2, and
  a clean-abstention question live against the real corpus/stack. All 4
  passing as of the last full run.

**Three real bugs caught and fixed while testing this against the live
stack** (all in git history, not just asserted fixed):
1. `_format_evidence()` in `prompts.py` re-truncated each evidence snippet
   to 300 chars on top of `make_evidence()`'s own 800-char cap. For a doc
   chunk where the relevant section isn't near the top of the file (e.g.
   `05-webhooks.md`'s retry-policy paragraph starts at char 458), the
   compose LLM never saw it — a real S3 answer went from correctly
   flagging the conflict to a false "I don't have evidence for that."
   Fixed by not re-truncating; `make_evidence()`'s cap is the only one now.
2. `loop.py`'s `_run_one_call` only auto-filled `repo_id` when the planner
   omitted it — a planner guess like `"repo_id": "meridian"` (plausible-
   looking, wrong) silently overrode the correct UUID and emptied out
   `search_code` results with no error. Fixed to always force the loop's
   own resolved `repo_id` for repo-scoped tools; also told the planner in
   the prompt not to bother guessing it.
3. The planner (`deepseek-v4-flash-0731`) sometimes returns an empty
   `"calls": []` plan on the very first round even for clearly-answerable
   questions — observed directly (a rerun of the exact S1 pytest failed
   with `abstained: True` and an empty `tool_trace` after passing cleanly
   moments earlier). Before any evidence exists, that's essentially always
   premature. Fixed with one retry (slightly higher temperature, a firmer
   nudge) when round 0 comes back empty — confirmed to resolve it on
   rerun, though given the model's variability this is a mitigation, not
   a guarantee; worth watching for during the demo.

Not built yet: `services/call-agent/` (Phase 4 — LiveKit transport
adapter) and the evidence panel UI (Phase 5).

### Phase 2 — tool layer (done)

- `app/seed/loader.py` — `ensure_repo_ingested()` runs the Meridian repo
  through the normal ingestion pipeline (Postgres `Repo`/`Job`, Neo4j module
  graph, `code_chunks` Qdrant collection) — idempotent, looks up by repo
  name `meridian-api` before re-ingesting. `embed_knowledge_base()` embeds
  `docs/` (12 files — **`CONFLICTS.md` is deliberately excluded**, see
  below), `tickets.jsonl`, `slack.jsonl` into three separate Qdrant
  collections (`kb_docs`, `kb_tickets`, `kb_slack`) via `embed_texts`.
  `load_accounts/commits/prs/tickets/logs/incidents/slack()` are plain
  cached JSON/JSONL readers for the deterministic tools.
  Run once per fresh stack: `python3 -c "from app.seed.loader import
  load_all; print(load_all())"` from `server/` (venv activated) — ingestion
  takes ~15s, KB embedding ~15s.
- `app/tools/evidence.py` — `make_evidence()`/`tool_result()`/`tool_error()`
  are the ONLY place the Section-4 evidence/tool-result shape is built. Every
  tool in `code.py`/`knowledge.py`/`tenant.py`/`provenance.py`/`conflict.py`
  goes through these — don't hand-build the dict shape elsewhere.
- `app/tools/code.py` — `search_code`, `trace_symbol`, `find_usages`,
  `read_file`, all wrapping the existing `vector_search`/`hybrid_retrieve`
  internals. Qdrant payloads don't carry a real cosine score (dropped in
  `context_assembler.py` too) — `_rank_score()` approximates a descending
  score from result order.
- `app/tools/knowledge.py` — `search_docs`, `search_tickets` (optional
  `account_id` filter), `search_slack` (optional `channel` filter).
- `app/tools/tenant.py` — `get_account` (secrets redacted to last 4 chars),
  `get_account_logs`, `get_incidents`, `list_accounts`. All deterministic
  JSON reads, no vector search — this is why S1 answers are exact.
  `get_incidents(account_id=...)` cross-references `related_tickets` against
  that account's tickets, then falls back to matching the account's first
  name-word or id in the incident text — matching the literal account_id or
  full account name against incident prose was too strict and silently
  returned nothing (caught and fixed while testing).
- `app/tools/provenance.py` — `explain_why(symbol_or_path, repo_id)`: code
  → commit (via `files` list) → ticket (`MER-\d+` regex on commit message)
  → PR (commit hash or ticket id in description) → Slack thread (keyword-
  scored match to find the root, then all messages sharing its
  `thread_ts`). Verified end-to-end on `app/pricing.py`: returns the full
  MER-412 chain down to all 12 messages in the `#pricing` thread. Any hop
  that can't be found stops the chain there with a `note` — never guesses.
- `app/tools/conflict.py` — `check_conflict(claim, repo_id)`: top-1
  `search_docs` + top-1 `search_code`, one narrow LLM judgment
  (agrees/conflicts/insufficient + <20-word reason), folded into `note`
  (the tool contract has no extra top-level keys). **Gotcha**: `gemini-2.5-
  flash` burns hidden reasoning tokens against `max_output_tokens` — 150 was
  silently truncating the answer mid-sentence; needed 1500 to reliably leave
  room for ~2 visible lines. Verified stable "conflicts" verdict on the
  retry-policy question and "agrees" on the secret-rotation question across
  repeated calls.
- `app/tools/registry.py` + `app/routers/tools.py` — `GET /api/tools` (schema
  list) and `POST /api/tools/{name}` (`{"args": {...}}` body), mounted
  unauthenticated at `/api/tools` in `main.py` per the plan's demo-scope
  auth decision. All 13 tools curl-tested individually, plus the unknown-
  tool (404) and missing-required-arg (422) error paths.

**Bug caught and fixed during testing**: `CONFLICTS.md` was initially
embedded into `kb_docs` like a real product doc. Since it accurately
narrates what the code does (that's its whole purpose), `check_conflict`
would sometimes retrieve it as the "docs" side and correctly report
"agrees" — silently defeating the S3 scenario. Fixed in `loader.py` by
excluding it from `embed_docs()`; stale embedded copy purged from Qdrant.
If `embed_docs(force=True)` is ever re-run, double check `CONFLICTS.md`
didn't sneak back in.

Not built yet: `app/agent/` (Phase 3 — the planning loop that calls these
tools and composes a cited answer) and `services/call-agent/` (Phase 4).

## Seed corpus (Phase 1)

The Meridian seed corpus is written at `server/app/seed/data/`:

- `repo/` — ~20-file FastAPI codebase (`meridian-api`). Load-bearing files:
  `app/pricing.py` (S2 — the unexplained Bangalore partner-rate branch),
  `app/webhooks.py` (S1 — signature verification + retry backoff that
  contradicts the docs for S3).
- `commits.jsonl` (30), `prs.jsonl` (10) — the commit touching `pricing.py`
  for the Bangalore rate references `MER-412`; PR #128 closes it and links
  the Slack thread.
- `slack.jsonl` (80 msgs / 6 channels: #pricing #eng #support #partnerships
  #incidents #general) — the `#pricing` thread starting at `t0` in
  `generate.py` is the ONLY place the Bangalore reason exists (BLR Mobility
  Partners reseller deal, signed by Legal, negotiated by Priya Nair /
  Partnerships). Not in docs, not in code comments — intentional per S2.
- `docs/` (12 pages + `CONFLICTS.md`) — `05-webhooks.md` claims "5 retries
  over 24h"; the actual code (`webhooks.py` `RETRY_BACKOFF_SECONDS`) does 3
  retries over ~12.5 min. This is the intentional S3 conflict;
  `CONFLICTS.md` documents it — don't "fix" it by editing one side to agree.
- `tickets.jsonl` (25), `accounts.json` (3: `acct_northwind` webhooks
  failing since the Aug 14 secret rotation — S1; `acct_calico` Bangalore
  partner tier — S2; `acct_orion` healthy control), `logs.jsonl` (138,
  includes the Northwind 401 spike since the rotation), `incidents.jsonl`
  (3, including the Northwind rotation-gap incident).
- `screens/` — 2 static HTML mockups (Integrations index, Signing/webhook
  settings page showing Northwind's stale-secret state) for the screen-share
  demo beat.
- `generate.py` — regenerates the volume data (commits/PRs/Slack/tickets/
  logs/incidents/accounts). Hand-authored load-bearing fixtures are inline
  in the script; re-running overwrites output, so diff before re-running if
  anything's been hand-edited since.

### Phase 0 checklist status
- [x] `GEMINI_API_KEY` and `VOYAGE_API_KEY` present in `server/.env`
- [x] LiveKit Cloud project + `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` in `server/.env` — verified live via a raw signed REST call to `ListRooms` (200 `{"rooms":[]}`). Do NOT `pip install livekit-api` (or any livekit SDK) into `server/.venv` — it force-upgrades `protobuf` to 7.x and breaks `google-ai-generativelanguage` (Gemini), which needs `protobuf<5`. The LiveKit adapter is a separate service (`services/call-agent/` in the plan) with its own venv/dependencies — that's where livekit SDKs belong.
- [x] `DEEPGRAM_API_KEY` in `server/.env` — verified live (200 from `/v1/projects`). Deepgram covers both STT (`nova-3`) and TTS (`aura-2-thalia-en`) per the user's chosen `AgentSession` config, so no separate TTS provider is needed.
- [x] `docker compose up -d` — postgres, redis, neo4j, qdrant all healthy (api/worker/frontend run locally, not in Docker — see `docker-compose.yml`)
- [x] `cd server && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000` → `GET /health` → 200
- [x] Celery worker running: `cd server && source .venv/bin/activate && celery -A app.tasks.celery_app worker --loglevel=info`
- [x] `cd client && npm run dev` → localhost:3000 loads
- [x] Ingested httpie/cli (236 files, 1066 functions) end to end — queued → cloning → parsing → graphing → embedding → READY, ~56s
- [x] `POST /api/query` streams a sensible answer with real cited chunks (file_path + line ranges) about that repo
- [ ] Two people in a LiveKit room, one sharing a screen, audio both ways — credentials verified server-side, but this specific box needs a real two-human microphone/screen-share test, which can't be run headlessly. Fastest path: open https://meet.livekit.io, paste in `LIVEKIT_URL` + a generated token (or your key/secret) from `server/.env`, and do it manually with a second person/device — that's the "stock quickstart" the plan means. Flip this box once done.

Phase 0 is otherwise complete — all keys are live-verified, ingestion and
query work end to end. Only the manual two-person transport smoke test is
outstanding.

Python note: pin the venv to **python3.12**, not 3.13 — `voyageai==0.3.2` has
no 3.13 wheel. Also `pip install greenlet` explicitly; it's a transitive
dependency of the async SQLAlchemy engine that isn't pinned in
`requirements.txt` but is required for `create_db_and_tables()` to work.

`REPOS_STORAGE_PATH` was moved from the default `/tmp/yasml-repos` to
`server/data/repos` (gitignored) — the default gets wiped on reboot.

## Tool contract (Section 4 of the plan)

Every tool returns:
```json
{
  "tool": "search_code",
  "status": "ok" | "empty" | "error",
  "evidence": [
    {
      "id": "ev_7a3f",
      "source_type": "code" | "docs" | "ticket" | "slack" | "account" | "log" | "commit",
      "locator": "backend/app/pricing.py:L42-L58",
      "snippet": "...",
      "score": 0.83,
      "retrieved_at": "2026-08-22T10:14:00Z"
    }
  ],
  "note": null
}
```

Every agent answer returns:
```json
{
  "answer": "... [ev_7a3f]",
  "claims": [{"text": "...", "evidence_ids": ["ev_7a3f"]}],
  "confidence": "high" | "medium" | "low",
  "abstained": false,
  "escalation": null,
  "tool_trace": [{"tool": "get_account", "args": {}, "ms": 240}]
}
```

Three rules the agent must never break: no uncited claim, abstain over guess,
never fabricate a locator.

## Transport boundary (Section 5)

`app/agent/` and `app/tools/` (to be created under `server/`) must contain
**zero transport imports** — no LiveKit types, no session objects, no room
handles. The agent loop must be callable from a plain unit test with no call
in progress. `services/call-agent/adapters/base.py`'s `TransportAdapter`
protocol (3 methods, 2 callbacks) is the only seam.

## Standing rule

**Never invent an evidence locator.** If a tool returns nothing, the agent
abstains. A fake file path, line number, ticket ID, or Slack timestamp is a
build-breaking bug, not a UI nit.

## File map (target — not all created yet)

```
server/app/
├── tools/          NEW — code.py, knowledge.py, tenant.py, provenance.py, conflict.py
├── agent/          NEW — loop.py, prompts.py, verifier.py (zero transport imports)
├── routers/tools.py, routers/agent.py   NEW
└── seed/           NEW — loader.py, data/ (fictional "Meridian" company corpus)

call-agent/          NEW top-level service — adapters/base.py, adapters/livekit_adapter.py, orchestrator.py, worker.py
client/app/call/     NEW — join page + evidence pane
```

See the full build plan (pasted into the conversation that created this repo
state) for Sections 6–18: seed corpus spec, tool layer detail, agent loop
design, LiveKit adapter, evidence panel, cut order, and demo script.

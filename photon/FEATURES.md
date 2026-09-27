# Photon — the engineer who never has to join the call

**Photon sits in your customer calls, listens, and answers product and
codebase questions out loud — grounded in your actual code, docs, tickets
and Slack, with a citation for every claim.**

It is not a chatbot bolted to a help centre. It joins the meeting, hears
the question, searches the same sources an engineer would, and answers in
the caller's language — in about two seconds, with the source on screen
next to the answer.

---

## The problem it exists for

### 1. Your best engineers are on sales calls

The person who can answer *"does your webhook retry, and how many times?"*
is the person who wrote the webhook. So they get pulled into the demo, the
onboarding call, the technical deep-dive, the escalation. A founder or
senior engineer loses a day a week to calls where they say perhaps six
sentences that nobody else in the company could have said.

**Photon says those six sentences.** It reads the retry code and answers
from it. The engineer stays in their editor.

### 2. Hiring support doesn't fix it for months

A support hire is weeks to find and months to become useful, because the
hard part is not the job — it is knowing the product. Until then every
non-trivial question routes back to engineering anyway, so you are paying
for the headcount *and* still paying the interrupt.

**Photon is useful on day one of a workspace**, because it learns the way
the product actually documents itself: the repo, the docs, the ticket
history, the Slack threads where decisions were made. Nobody has to write
it a training manual.

### 3. Your customers are not all in one language

Clients across India and abroad. A support team fluent in the languages
your buyers actually think in is a hiring problem most companies never
solve — so the call happens in everyone's second language, and precision
is the first casualty.

**Photon hears and answers in Telugu, Tamil, Hindi and English**, detects
the language per utterance, and can switch mid-call. The corpus stays
English; the answer is translated while the citations, product names and
identifiers are copied through character for character.

### 4. The call gets technical and the room runs out of answers

The salesperson is doing fine until the customer asks something specific.
Now it is "let me check and get back to you", or a scramble to pull an
engineer in. Either way the momentum is gone.

**Photon is already in the room.** Anyone can address it and get a
grounded answer without adding a person to the call.

### 5. Five minutes to find something that is in the docs

The answer exists. It is in `05-webhooks.md`, or a Jira comment, or a
Slack thread from March. Finding it live, while someone waits, is the
whole problem — and often the doc and the code disagree, which is worse
than not finding it.

**Photon searches all of it at once, in about two seconds** — and when the
docs and the code contradict each other, it says so rather than picking
one.

### 6. Knowledge transfer is a tax you pay forever

Every new hire, every team change, every handover: weeks of KT, most of it
re-explaining decisions that are already written down somewhere nobody can
find. And the reason behind a decision is usually in a Slack thread, not
in the code.

**Photon answers "why is this like this?" by walking the chain** — code →
commit → ticket → PR → the Slack thread where it was argued out — and
shows you every hop.

---

## What it actually does

### Answers with a citation, or does not answer

Every claim carries an evidence id you can click to see the exact source —
file and line range, ticket, Slack message, log line. Three rules the
agent cannot break:

- **no uncited claim** — a claim whose evidence does not check out is
  stripped from the answer before it is spoken
- **abstain over guess** — no evidence means "I don't have that", not a
  plausible sentence
- **never fabricate a locator** — a made-up file path or ticket id is
  treated as a build-breaking bug, not a cosmetic one

A verifier re-checks the composed answer against the evidence that was
actually retrieved, strips claims that fail, and downgrades confidence
when it has to. This is the difference between a demo and something you
let talk to a customer.

### Joins the call like a person

A meeting code (`abcd-efgh`) is the room, the link and the transcript. It
runs on real WebRTC — video tiles, screen share, live captions split by
speaker. Members walk straight in; anyone else knocks and is admitted by
someone already inside, verified server-side so a forwarded link cannot
put a stranger in a live customer call.

### Sees the screen

When someone shares their screen, Photon can read it — and answer about
what is on it, not just what is in the docs. It says "let me look" the
moment it starts, so the extra second of thinking does not read as silence.

A frame is only ever used while a share is genuinely live (30-second
freshness window), so it can never describe a screen that stopped existing
twenty minutes ago.

### Shows its work, live

A trace panel beside the captions shows the pipeline as it runs: which
tools were chosen, how long each took, evidence gathered, answer composed,
citations verified. When the answer is wrong you can see *where* it went
wrong instead of guessing.

The code sidebar shows every file the answer was grounded in, read from
the repo at the real line numbers — so someone can say "line 47" and
everyone lands in the same place.

### Reads what your company already wrote

18 tools across the sources a real answer lives in:

| source | what it answers |
|---|---|
| **GitHub** | what the code actually does, who changed it, why |
| **Slack** | the decision nobody wrote down, including thread replies |
| **Jira / Linear** | what was reported, what was decided, what shipped |
| **Notion** | the docs |
| **Datadog** | is something on fire right now, and is it this |
| **Uploaded docs** | contracts, runbooks, anything else |

Connected per workspace, scoped per workspace, and the agent is never
allowed to choose a workspace id — it is forced by the server, so a wrong
guess cannot become a cross-tenant read.

### Knows which repo you mean

A workspace with fifteen repos does not need you to say which one. Photon
resolves it from the question when it can, searches across all of them
when it cannot, and asks rather than guessing when the answer depends on
picking right.

---

## The shape of the bet

Every company past its first few customers ends up building a small human
cache in front of its own knowledge: the two people who know why things
are the way they are, interrupted all day. That cache is expensive, slow
to warm, speaks one or two languages, and leaves when they do.

Photon is that cache, built out of the sources the company already
maintains, available on every call at once, in the caller's language, with
the receipts attached.

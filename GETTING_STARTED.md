# Getting started

Everything here is copy-pasteable and verified. Five minutes to a working
run; the rest is for when you start changing things.

---

## 1. Run it — no API key needed

```bash
make setup                        # venv + optional deps + readiness report
python3 scripts/make_fixtures.py  # build the 11 practice repositories
scripts/demo.sh                   # fix a real bug in one of them
```

`scripts/demo.sh` runs the **real harness** against a real git repository
with a real test suite. Only the model is a scripted stand-in, so it is
offline and deterministic.

Try a few:

```bash
scripts/demo.sh py-offbyone        # the happy path
scripts/demo.sh py-red-baseline    # a repo with 2 tests already failing
scripts/demo.sh py-lint-debt       # a repo with 40 lint errors
scripts/demo.sh py-vague --dry     # a 1-line issue; investigation only
```

Reset a fixture after poking at it:

```bash
git -C tests/fixtures/py-offbyone checkout -- .
```

## 2. Run it with a real key

```bash
export AI_API_KEY="sk-..."         # any provider; detected from the prefix
make run ISSUE="parse_date crashes when the input has no separator"
```

By default it works on the current directory. To point it somewhere else:

```bash
REPO_PATH=~/code/some-project make run ISSUE="$(cat issue.txt)"
```

Nothing is hardcoded. `AI_API_KEY` is read from the environment only.

## 3. Prove it is reproducible

```bash
make test        # replays the last run from its trajectory, with NO api key
```

If no run has happened yet this falls through to the unit suite.

---

## What you should see

```
── HARNESS v2.1 ────────────────────────────────────────────────
provider        anthropic  (from key prefix sk-ant-api)      ← FR-6, no API call
models found    7 chat-capable of 9 listed                   ← FR-7, filtered
primary model   claude-opus-…    tier T2   ctx 200000        ← FR-8
tier profile    T2: bash-first action space, n=1, terse planning
test command    poetry run pytest -q     (from .github/workflows/ci.yml)
────────────────────────────────────────────────────────────────   ← all of this in <5s

[P0 TRIAGE     ] task type BUG_FIX
[P0 TRIAGE     ] baseline: 3 passed, 1 failed   ← the suite runs BEFORE any edit
[P1 INVESTIGATE] route ORACLE (oracle test: test name mentions parse_date)
[P1 INVESTIGATE] coverage localization:
                  src/dateparse/parser.py:11 (parse_date)  suspiciousness 0.707
[P1 INVESTIGATE] root cause: parse_date indexes parts[0] without checking …
[P2 SCOPE      ] plan: single_file, 1 file(s), ~4 lines
[P3 IMPLEMENT  ] applied: +2 -0 across 1 file(s)
[P4 VERIFY     ] lint: clean · oracle: PASS · tests: 1 now passing
[P5 CONFIDENCE ] C1 ok  C2 ok  C3 ok  C4 ok  C5 ok  C6 ok   (6/6)

── RESULT ──────────────────────────────────────────────────────
status            SUCCESS
=== DIFF ===
```

Three things worth noticing, because they are the whole point:

- **The suite runs before the first edit.** That is the only way to tell a
  regression you caused from a test that was already red.
- **`suspiciousness 0.707`** is spectrum-based fault localization, computed
  from that baseline run's coverage. Zero model calls.
- **6/6** are six conditions the harness computed from evidence. Five of them
  never consult a model at all.

Everything is written to `<repo>/.harness/run/` — start with
**`run_report.md`**, which is the narrative version of the above.

---

## When something goes wrong

```bash
HARNESS_LOG_LEVEL=debug make run ISSUE="..."     # verbose
HARNESS_DRY_RUN=1 make run ISSUE="..."           # P0-P2 only, writes nothing
```

| Symptom | Look at |
|---|---|
| Wrong file targeted | `.harness/run/rootcause.json` — what evidence did it cite? |
| Too large a diff | `.harness/run/scope.json` — `estimated_lines_changed` |
| Edit keeps failing | the `PATCH_NOT_APPLIED` lines — the format should de-escalate |
| Claims success wrongly | `.harness/run/confidence.json` — which of C1–C6 passed |
| Anything at all | `.harness/run/trajectory.jsonl` — every event, in order |

The harness never aborts on a missing tool. If `rg`, `coverage`, `ruff` or
tree-sitter is absent it logs `degraded: <reason>` and keeps going. A line
starting `degraded:` explains itself.

---

## The codebase in one screen

```
harness/
  __main__.py        entry point; exit codes
  orchestrator.py    the state machine and the fix-and-verify loop  ← start here
  records.py         every structure the phases pass each other
  config.py          all environment handling

  model/             BYOK: detect → discover → rank → tier → route
    detect.py          key prefix → provider, no API call
    rank.py            ranking table, tier inference, chat filter
    gateway.py         retries, budget, cache, failover
  repo/              search, snippets, style profile, git history, probes
  localize/          sbfl.py (coverage), oracle.py, router.py
  phases/            p0_triage … p5_confidence, one file each
  edit/              formats, parser, anchor ladder, validation, hygiene
  verify/            toolchain, baseline, lint gate, classification, judges
  recovery/          failure taxonomy, stuck detection, alternatives
  context/           event log, elision, prompt assembly
```

Read in this order: `orchestrator.py` → `records.py` → the phase you care
about. Each phase file is self-contained and its docstring names the PRD
requirements it satisfies.

---

## Changing things

```bash
.venv/bin/python -m pytest tests/unit -q       # 350 tests, ~90s
.venv/bin/python -m pytest tests/unit/test_wp5_edit.py -q      # one area
.venv/bin/python bench/runner.py               # all 11 fixtures, offline
scripts/clean_clone_gate.sh                    # the full acceptance run
```

**Run the gate before you push.** It clones *committed* state into a scratch
directory and builds from nothing — it has already caught two bugs that were
invisible in the working tree.

### Common tasks

**Add a provider** → `model/detect.py` (prefix + base URL) and
`model/rank.py` (ranking table). If it speaks the OpenAI wire, nothing else
is needed. Test: `tests/unit/test_wp1_detect_rank.py`.

**Support a new language** → `verify/toolchain.py` (test and lint commands),
`repo/snippets.py` (`DEF_PATTERNS`), `edit/validate.py` (syntax check).

**Add a fixture** → a function in `scripts/make_fixtures.py`, then
`python3 scripts/make_fixtures.py`. A `bench/tasks/*.yaml` makes it part of
the matrix.

**Change a prompt** → they live inline in `phases/p*.py`. Re-run
`bench/runner.py` before and after; if the numbers do not move, the change
is not an improvement.

### The rule for new features

An ENH feature ships only if `bench/runner.py` shows a gain in the **T0**
column. A feature that helps only a frontier model is helping the model, not
the harness.

---

## What is not built yet

All optional, all gated on the rule above. In priority order:

1. `git bisect` for regression-shaped issues — `SPEC.md` §21.3
2. Search subagent with a disposable context — §24
3. Git worktrees for parallel candidate evaluation — §25.1
4. Reproduction-test oracle (written to `.harness/`, never committed) — §25.2
5. Multi-candidate sampling with a normalized majority vote — §8.4
6. Template repair as a terminal fallback — §25.4
7. tree-sitter repo map with PageRank — §4.3 (**last**: the measurements
   suggest coverage-based localization already covers this ground)

Two fixtures are deliberately unresolved: `py-vague` (green baseline, no
anchors) and `py-cross-caller` (a feature needing two coordinated edits). In
both the harness reports PARTIAL rather than guessing. That is the intended
behaviour, not a TODO.

---

## The documents

| File | What it is |
|---|---|
| `PRD.md` | the requirements — 36 FRs and 6 NFRs |
| `SPEC.md` | the design, and why each decision was made |
| `IMPLEMENTATION.md` | the build plan, with a requirement-coverage matrix |
| `README.md` | the short version, for someone evaluating it |
| this file | for someone about to work on it |

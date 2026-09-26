# AI Coding Harness

An autonomous coding harness: it takes a GitHub issue as text, investigates
the repository, produces the smallest change that resolves the root cause,
verifies it against the repository's own tests, and reports what it did with
evidence.

Built for the LCC × DevClub AI Coding Harness Hackathon 2026.
See `PRD.md` for requirements, `SPEC.md` for the design, `IMPLEMENTATION.md`
for the build plan.

---

## Quick start

```bash
export AI_API_KEY="<your key>"
make setup
make run ISSUE="parse_date crashes when the input has no separator"
```

### From a GitHub issue or pull request

Hand it a reference and nothing else. The harness fetches the issue with its
comments, clones the repository, creates a branch, and works there:

```bash
make run ISSUE="owner/repo#123"
make run ISSUE="https://github.com/owner/repo/pull/456"
```

An issue gets a `harness/issue-123` branch; a pull request is checked out
through `pull/456/head`, so a fork works the same as a branch.

No `gh` needed -- the GitHub REST API is reached with the standard library,
the same way the model providers are. `gh` is used when it is already
installed, because it already holds your auth. For private repositories or
to lift the 60-request hourly rate limit, set `GITHUB_TOKEN`.

Reporting back is off by default, because it writes to someone else's issue
tracker:

```bash
HARNESS_POST=comment make run ISSUE="owner/repo#123"
```

That posts the run report as a comment, and only when the run produced a
verified fix. The harness never pushes code: `git push` stays on the deny
list, so you review the diff and open the PR yourself.

That is the whole interface. `make setup` creates a virtualenv and prints a
readiness report; `make run` executes the pipeline and prints the fix, the
verification results and a confidence report.

```bash
make test     # replays the last run offline, with no API key
make clean    # remove the venv and all harness artifacts
```

---

## What it does

```
issue text
   │
   ├─ P0  triage        anchors, task type, vagueness score
   ├─ ──  baseline      run the suite BEFORE editing, instrumented with coverage
   ├─ ──  localize      coverage (SBFL) + grep + structure + git history
   ├─ P1  investigate   hypotheses with checks the HARNESS executes
   ├─ P2  scope         a binding plan: files to change, files not to change
   ├─ P3  implement     smallest change, style matched to the file
   ├─ P4  verify        lint → scoped tests → full suite → cheap-model judges
   └─ P5  confidence    six conditions; any failure loops back, never submits
```

Key properties:

- **No code before the root cause.** Phase 1 is handed no write tool at all.
- **The harness decides what is true.** Every hypothesis carries a check the
  harness runs itself; the model proposes and interprets.
- **Pre-existing failures are documented, never fixed.** The suite is run
  before the first edit so a regression can be told from existing debt.
- **The lint gate is scoped to changed files** and diffed against the
  baseline, so a repository with lint debt does not deadlock every cycle.
- **It adapts to the model it is given.** The action space, edit format,
  sampling and context budget all change with the detected model tier.

---

## Configuration

Only `AI_API_KEY` is required. Everything else has a sensible default.

| Variable | Default | Purpose |
|---|---|---|
| `AI_API_KEY` | **required** | the only credential; read from the environment only |
| `AI_BASE_URL` | per provider | any OpenAI-compatible endpoint |
| `HARNESS_MODEL` / `HARNESS_CHEAP_MODEL` | auto | override model selection |
| `HARNESS_PROVIDER` / `HARNESS_TIER` | auto | force the adapter or the tier profile |
| `ISSUE` / `ISSUE_FILE` | stdin | the issue text; also accepted as `argv[1]` |
| `REPO_PATH` | `$PWD` | the repository to fix (ignored when `ISSUE` is a GitHub reference) |
| `GITHUB_TOKEN` / `GH_TOKEN` | — | private repositories, and 5000 instead of 60 API requests an hour |
| `HARNESS_POST` | `off` | `comment` posts the run report back to the issue or PR |
| `HARNESS_WORKSPACE` | `./.harness/workspace` | where cloned repositories land |
| `HARNESS_CLONE_DEPTH` | 0 (full) | shallow clone depth; full history is needed for FR-16 |
| `HARNESS_MAX_CYCLES` | 5 | fix-and-verify cycle cap |
| `HARNESS_TOKEN_BUDGET` | 900000 | global token cap |
| `HARNESS_TIME_BUDGET` | 1500 | seconds |
| `HARNESS_TEST_CMD` / `HARNESS_LINT_CMD` | discovered | override discovery |
| `HARNESS_DRY_RUN` | 0 | run P0–P2 and print the plan, writing nothing |
| `HARNESS_NO_CACHE` | 0 | disable the model-call cache |
| `HARNESS_LOG_LEVEL` | info | `info` or `debug` |

The provider is detected from the key prefix with no API call. Anthropic,
OpenAI, OpenRouter, Groq, xAI, Cerebras, Google, Together, Fireworks,
DeepSeek and Mistral are recognised; anything else works through
`AI_BASE_URL`.

---

## Output

Written to `.harness/run/` in the target repository, which is added to
`.git/info/exclude` so it can never appear in the diff:

| File | Contents |
|---|---|
| `run_report.md` | the evidence package: task, investigation, root cause, diff, what was **not** changed, verification, confidence, cost |
| `diff.patch` | the change as a patch |
| `rootcause.json`, `scope.json` | the structured records each phase produced |
| `baseline.json`, `verification.json`, `confidence.json` | test results before and after, and the six conditions |
| `trajectory.jsonl` | every event, replayable by `make test` |

Exit codes: `0` success, `2` partial (a fix is present but a condition
failed), `3` no fix, `4` configuration error, `5` internal error. Codes 4 and
5 restore the working tree.

---

## Modality

**Text only.** No image, audio or video code path exists anywhere in the
harness — not dormant, not behind a flag. The tool surface has no media type,
so the constraint is structural rather than a promise. `tests/unit/` asserts
this.

## Credentials

`AI_API_KEY` is read from the environment, injected only at the HTTP
boundary, and never placed in a prompt or in the environment of any command
run against the repository. Anything token-shaped is redacted before it is
written to disk. No credential appears anywhere in this repository; a
`.env.example` with an empty value is provided for local use.

---

## Development

```bash
python3 scripts/make_fixtures.py     # build the 11 fixture repositories
.venv/bin/python -m pytest tests -q  # the unit and integration suites
.venv/bin/python bench/runner.py     # the fixture matrix, offline
```

`bench/runner.py` runs every fixture against a scripted stand-in model and
records metrics to `bench/reports/`. An ENH feature ships only if the matrix
shows a gain in the **T0** column: a feature that helps only a frontier model
is helping the model, not the harness.

# Implementation Plan
## AI Coding Harness — LCC × DevClub Hackathon 2026

**Version:** 1.0
**Derived from:** `SPEC.md` v2.1 (Part I §1–20, Part II §21–37)
**Satisfies:** `PRD.md` v1.0 — all of FR-1…FR-36 and NFR-1…NFR-6 (coverage matrix in §14)
**Budget:** two days

---

## 0. How to use this document

Work is organised into **work packages** `WP0…WP10`, each containing numbered tasks. Every task carries:

| Field | Meaning |
|---|---|
| **ID** | `T<wp>.<n>` — cite this in commits and in the tracker |
| **Files** | what the task creates or edits |
| **Spec** | the `SPEC.md` section that defines the behaviour — read it before coding |
| **DoD** | definition of done: an observable, usually a command that must pass |
| **Ticks** | the PRD requirements this task satisfies |
| **Tier** | **CORE** = required to ship · **ENH** = only after CORE is green and only if §6.3's gate passes |

Work top-to-bottom. **WP0→WP7 is the critical path and ends at the shippable line.** WP8 raises the score, WP9 is optional, WP10 is mandatory and reserved.

---

## 1. Ground rules

1. **No task is done until its DoD command passes.** "It works on my branch" is not a DoD.
2. **Parsers and tables are written test-first.** Provider detection, model ranking, edit parsing, test-result parsing and the confidence truth table are pure functions with adversarial inputs — they are where silent failures live, and they are cheap to test.
3. **Every model call goes through `gateway.call()`.** No provider-specific code outside `model/adapters/`.
4. **Anything a program can compute is never a model call.** If a task's implementation contains a prompt that asks for a fact `git`, `grep`, or the AST could supply, it is wrong. (NFR-1)
5. **Every `except` either handles or re-raises a typed error.** No bare `except: pass` anywhere.
6. **Commit at every DoD.** The clean-clone gate (T10.1) depends on the repo being coherent at any point.
7. **If you fall behind, cut from WP9 first, then WP8. Never cut from WP0–WP7 or WP10.**

### 1.1 Parallel tracks

With 2–4 people, these run concurrently after WP0:

```
person A:  WP1 (model layer) ──────────▶ WP4 (phases 0–2) ─────▶ WP7 (confidence/loop)
person B:  WP2 (execution/baseline) ───▶ WP6 (verification) ───▶ WP8 (evidence/replay)
person C:  WP3 (context/repo intel) ───▶ WP5 (edit engine) ────▶ WP9 (ENH)
person D:  TB  (fixtures + bench) ─────▶ continuous ───────────▶ WP10 (gates)
```

`TB` (§13) starts immediately and never stops — without fixtures, nothing else can be checked.

---

## 2. WP0 — Skeleton and contract  *(D1 AM, ~1.5 h, CORE)*

Nothing else can be verified until `make setup && make run` executes end to end, even as a no-op.

| ID | Task | Files | Spec | DoD | Ticks |
|---|---|---|---|---|---|
| T0.1 | Package skeleton; `.env.example` containing only `AI_API_KEY=`; `.gitignore` excluding `.venv`, `.harness`, `__pycache__`; pinned `requirements-core.txt` / `requirements-optional.txt` | tree per §2.4 | §2.4, §32 | `python -c "import harness"` succeeds; a secret-pattern grep over the whole repo returns nothing | NFR-4 |
| T0.2 | Makefile: `setup` / `run` / `test` / `clean`; venv; **loud `AI_API_KEY` guard**; leading `-` on the optional install; no `sudo`, no `apt`, no `cargo` | `Makefile` | §13, §32 | `make run` with no key exits 1 in <1 s with a usage line; `make setup` succeeds with the optional install deliberately broken | NFR-3, NFR-4 |
| T0.3 | `Config` dataclass; env parsing with precedence **explicit > discovery > default**; unset variables stay unset (never `""`) | `config.py` | §12, §35 | unit: `AI_BASE_URL` absent → provider default used, not empty string | FR-9, FR-10 |
| T0.4 | Phase logger: `[Pn NAME]` prefixes, ANSI only when `isatty()`, secret redaction on every write | `logging_ui.py` | §11.1, §31 | unit: a string containing `sk-ant-api03-xxx` is written as `sk-ant-***` | FR-5, NFR-4 |
| T0.5 | `selfcheck`: Python version, git, `rg`, `ctags`, tree-sitter, `coverage`, write access — one line each, `ok` or `degraded: reason` | `selfcheck.py` | §13 | `make setup` ends with the readiness block; never exits non-zero for a missing optional | NFR-2, NFR-3 |
| T0.6 | Budgets: `TokenBudget`, `WallClock`, `StepCounter`; per-phase scoping; `BudgetExceeded` typed exception | `budget.py` | §10.3 | unit: exceeding a phase share raises, and the orchestrator converts it to a degraded completion | NFR-1 |
| T0.7 | Append-only event log: JSONL, blob refs for payloads >4 KB, all 12 event kinds | `context/events.py` | §6.1 | unit: 1000 events round-trip; blob refs resolve | NFR-5 |

**WP0 done when:** `make setup && make run` runs a no-op pipeline and exits 0 on a machine that has never built the project.

---

## 3. WP1 — Model layer / BYOK  *(D1 AM, ~2 h, CORE)*

This is the most visible engineering in the whole harness and the part the other teams most often skip. It is also six PRD requirements in one package.

| ID | Task | Files | Spec | DoD | Ticks |
|---|---|---|---|---|---|
| T1.1 | Prefix table, **longest-match-first**, 12 providers; `AMBIGUOUS` for bare `sk-`; ambiguity ladder (`AI_BASE_URL` → `HARNESS_PROVIDER` → host probe → OpenAI default). **No network call in detection.** | `model/detect.py` | §3.2 | unit over ≥40 real-shaped keys; `sk-or-v1-…` → openrouter, `sk-proj-…` → openai, `sk-ant-api03-…` → anthropic | **FR-6** |
| T1.2 | `GET <base>/models`, 2.5 s timeout + one retry; **chat-capability filter** (`NON_CHAT` list); cache to `.harness/cache/` | `model/discover.py` | §3.3 | unit: a listing containing `dall-e-3`, `text-embedding-3`, `whisper-1` returns none of them | **FR-7** |
| T1.3 | Ranking table per provider; `tier_of()` heuristics; `select_models()` returning `(primary, cheap)` under a **total sort order** | `model/rank.py` | §3.3 | unit over ~80 model ids; two runs on a shuffled input list give identical output | **FR-8** |
| T1.4 | `HARNESS_MODEL` / `HARNESS_CHEAP_MODEL` / `HARNESS_TIER` override, bypassing the discovery list | `model/rank.py`, `config.py` | §3.3 | unit: an override id absent from discovery is still selected | **FR-9** |
| T1.5 | Adapters: `openai_compat` (OpenAI/OpenRouter/Groq/xAI/Cerebras/DeepSeek/Mistral/Together/Fireworks/Ollama/vLLM/any `AI_BASE_URL`) and `anthropic` (Messages). stdlib `urllib` only. | `model/adapters/*.py` | §2.3, §3.2 | live smoke against ≥2 providers; `AI_BASE_URL` pointed at a local Ollama works unmodified | **FR-10** |
| T1.6 | Gateway: normalized `ModelReply`, backoff with `Retry-After`, budget accounting, call cache, trajectory trace, **primary→cheap failover** after 2 hard failures | `model/gateway.py` | §3.5 | fault-injection: 3×500 then success → one reply, 3 retries logged; 2 hard failures → failover logged | NFR-2 |
| T1.7 | Router: phase → tier → model, per §3.4's table; single-model degradation (`cheap == primary`, clamped) | `model/router.py` | §3.4 | unit: every phase resolves under 1-model, 2-model and override configurations | FR-8, NFR-1 |
| T1.8 | Startup block printed **before repository indexing**; budgets and detected toolchain included | `__main__.py` | §3.1 | `time make run` shows the block in <5 s on a 5 000-file repo | **FR-11, NFR-6** |
| T1.9 | Capability probe, **two attempts**, 8 s cap, cached; sets `tool_calling` / `strict_json`; failure ⇒ safest path | `model/probe.py` | §3.3 | unit: a model that fails one tool call then succeeds is recorded capable | NFR-2 · *ENH* |

**WP1 done when:** the startup block is correct against Anthropic, an OpenAI-compatible provider, and a local `AI_BASE_URL`, in under five seconds each.

---

## 4. WP2 — Execution and verification substrate  *(D1 midday, ~2.5 h, CORE)*

Built before the phases, because a loop that cannot tell a regression from a pre-existing failure is worse than no loop.

| ID | Task | Files | Spec | DoD | Ticks |
|---|---|---|---|---|---|
| T2.1 | Sandboxed runner: explicit `cwd`, scrubbed env (**no API key**), per-command timeout, 200 KB head + 50 KB tail capture, deny list, stateless invocations | `verify/runner.py` | §5.4, §31 | unit: `rm -rf /`, `sudo`, `git push`, a write outside the repo are all refused; a 10 s command with a 2 s timeout is killed | NFR-2, NFR-4 |
| T2.2 | Workspace: repo root resolution, git state, checkpoint via `git stash create`, restore, `.harness/` written to `.git/info/exclude`, non-git fallback | `repo/workspace.py` | §11.2, §15 | unit: checkpoint → mutate → restore returns a byte-identical tree; `.harness/` never appears in `git status` | FR-3 |
| T2.3 | Toolchain discovery: **CI workflows first**, then Makefile/tox/pre-commit, then manifests; `HARNESS_TEST_CMD`/`HARNESS_LINT_CMD` override; **never invent a linter the repo does not configure** | `verify/toolchain.py` | §4.2, §9.2 | correct test *and* lint command on all 13 fixtures; on `py-lint-debt` the discovered linter is the repo's own | FR-27, FR-28 |
| T2.4 | Result parsers: junit-xml, `go test -json`, cargo json, jest json, surefire, mocha; regex fallback with `parser_confidence` | `verify/parse_results.py` | §9.4 | unit against recorded fixtures for each framework; stable test ids across two runs | FR-29 |
| T2.5 | Baseline snapshot **with `coverage run`**; `mode` is one of `full`, `scoped`, `absent`; 300 s cap; emits `baseline.json` + `coverage.json` | `verify/baseline.py` | §9.2, §26.1 | `py-red-baseline` records exactly 2 failures; over-cap falls back to scoped and logs the degradation | **FR-29, FR-30** |
| T2.6 | Classification: new / pre-existing / fixed / unknown; **flake rerun once in isolation**; collection errors highest priority | `verify/classify.py` | §9.5 | truth-table unit; `py-flaky` yields zero blocking failures and one `flaky` note | **FR-29, FR-30** |

**WP2 done when:** on every fixture the harness can state, before touching any code, exactly which tests already fail.

---

## 5. WP3 — Context engine and repository intelligence  *(D1 midday, ~2 h, CORE)*

| ID | Task | Files | Spec | DoD | Ticks |
|---|---|---|---|---|---|
| T3.1 | Search ladder: `rg --json` → `git grep -n` → Python walker; capped at 80 hits; `file:line` + one line of context | `repo/search.py` | §4.4 | identical results across all three backends on a fixture; `rg` removed from `PATH` still works | NFR-2 |
| T3.2 | `read_window`; **`neighbours(file, symbol, k=3)`**; **`style_profile(file)`** — indent, quotes, line length, naming case, docstring convention, error idiom, import order, annotations | `repo/snippets.py` | §4.4 | unit: profile of a 4-space snake_case file with `raise` handling is reported exactly | **FR-22, FR-23** |
| T3.3 | Git history: `log -n 10`, `log -p -n 3` truncated to hunk headers ±3, `blame -L`, manifest log over 90 days | `repo/history.py` | §4.6 | unit on a fixture with a seeded recent commit; output under 600 tokens | **FR-16** |
| T3.4 | External probes: declared vs installed deps, recent manifest bumps, env-var requirements vs environment, runtime version, third-party contracts, missing config paths → `ExternalFactors` | `repo/external.py` | §4.6 | `py-env-missing` reports the missing var; `py-dep-bump` reports the skew; both **before** any classification | **FR-14** |
| T3.5 | Language census and file filtering (vendor, build, binaries, >1 MB, lockfiles) | `repo/lang.py` | §4.2 | census matches the startup block on all fixtures | FR-11 |
| T3.6 | Elision (stage 1): the seven deterministic rules, **never truncate mid-function** | `context/elide.py` | §6.3 | unit: a 50 KB assembly reduces below budget with pinned content intact | NFR-1 |
| T3.7 | Assembly: pinned / working set / recent-K / older; budget **derived from the real context window** × tier factor | `context/assemble.py` | §6.2, §6.3 | unit: an 8 k-window model never receives >2 800 tokens | NFR-1, NFR-2 |

**WP3 done when:** a T0 model with an 8 k window can be driven without a single overflow across all fixtures.

---

## 6. WP4 — Phases 0–2 and the routers  *(D1 PM, ~3 h, CORE)*

The PRD's headline goal (G1 — root cause before implementation) lives here, and it is enforced structurally: **the Phase-1 tool set contains no write tool.**

| ID | Task | Files | Spec | DoD | Ticks |
|---|---|---|---|---|---|
| T4.1 | Phase 0: deterministic anchor extraction (paths, symbols, frames, errors, versions, repro, expected/actual); **vagueness score** → `min_hypotheses`; cheap normalization call skipped when extraction suffices | `phases/p0_triage.py` | §7.1 | `py-vague` scores ≥0.5 and sets 3; a stack-trace issue scores <0.5 and makes zero model calls | FR-1, FR-15 |
| T4.2 | **Task-type router**: `BUG_FIX` / `FEATURE` / `REFACTOR` / `CONFIG` / `DEPENDENCY` / `UNKNOWN`; per-type pipeline adjustments; `UNKNOWN` → bug-fix + conservative | `phases/p0_triage.py` | §22 | unit over 20 labelled issue texts; a feature request relaxes the diff guard and lifts the new-file prohibition | FR-19 |
| T4.3 | **SBFL**: parse `coverage.json`, Ochiai per line, aggregate to symbol and file | `localize/sbfl.py` | §21.1 | on `py-offbyone` the buggy line ranks #1; zero model calls | FR-12 |
| T4.4 | **Oracle detection**: a baseline-failing test matching the anchors → `Config.oracle`, skip P1.5–P1.7 | `localize/oracle.py` | §21.2 | fixtures with a matching failing test take the ORACLE route and cut P1 tokens by >60 % | FR-12, NFR-1 |
| T4.5 | **Convergence router** over {SBFL, lexical, structural, historical}; **self-consistency n=5 @ 0.7** for the localization confidence number | `localize/router.py` | §21.5 | unit: 2-of-4 agreement → `DIRECT`; disagreement → `FULL`; `HARNESS_ROUTE` overrides | FR-12, NFR-1 |
| T4.6 | Phase 1: seed → external probes → symptom → **upstream call-path trace** → localization ladder → hypotheses **each with an executable `check`** → harness-executed elimination → synthesis → validation gate → **conservative path on low confidence** | `phases/p1_investigate.py` | §7.2 | `py-upstream` finds the cause 2 frames above the symptom; `py-env-missing` classifies `config`, not a code bug; **no write tool is reachable in this phase (unit-asserted)** | **FR-12, FR-13, FR-14, FR-15, FR-16, FR-17** |
| T4.7 | Phase 2: `ChangePlan`; harness **hardening** (test/lock/generated/vendor paths forced into the deny list); **symbol-level `callers_of`**; `kind` computed; estimate clamp | `phases/p2_scope.py` | §7.3 | `py-cross-caller` enumerates all 4 call sites and marks `cross_cutting`; a plan naming a lockfile has it moved to the deny list automatically | **FR-18, FR-19, FR-20, FR-21** |
| T4.8 | `HARNESS_DRY_RUN=1` — run P0–P2 and print the plan, writing nothing | `orchestrator.py` | §12 | `git status` clean after a dry run on every fixture | FR-12 |

**WP4 done when:** `HARNESS_DRY_RUN` produces a correct root cause and a hardened plan on ≥9 of 13 fixtures, with no file modified.

---

## 7. WP5 — Edit engine and Phase 3  *(D1 late, ~2.5 h, CORE)*

An edit that will not apply loses the entire cycle. This package is a reliability problem, not a formatting one.

| ID | Task | Files | Spec | DoD | Ticks |
|---|---|---|---|---|---|
| T5.1 | Three formats and `choose_format(tier, file_lines, conservative, failures)`; **unified diff is not implemented** | `edit/formats.py` | §8.2 | unit: T0→`LINE_RANGE`, T1/T2→`SEARCH_REPLACE`, small file after 2 failures→`WHOLE_FILE` | FR-26 |
| T5.2 | Fenced-block parser tolerant of stray prose; repair pass for unterminated blocks and mismatched markers | `edit/parse.py` | §8.3 | ≥95 % parse rate on the malformed corpus (T5.7) | FR-26 |
| T5.3 | Apply ladder: exact → whitespace-normalized → indentation-agnostic → difflib ≥0.92, **unique match required**; atomic multi-file; **staleness re-read before regenerating** | `edit/apply.py` | §8.3, §26.3 | unit: an ambiguous anchor is a typed failure, never a guess; a stale second hunk applies after the re-read | FR-26 |
| T5.4 | Validation: per-language syntax check, **diff-size guard at 1.3×**, **scope guard with automatic revert** | `edit/validate.py` | §8.3 | unit: a write to a deny-listed path is reverted, not warned about | **FR-24, FR-26** |
| T5.5 | Hygiene scan **on added lines only**: TODO/FIXME, debug output, commented code, unused imports, edit markers, indent mismatch | `edit/hygiene.py` | §8.3 | unit: pre-existing debt in a touched file produces zero flags | **FR-25** |
| T5.6 | Phase 3: deterministic context assembly (target + **3 neighbours** + **style profile** + imports + plan + **enumerated prohibitions**); **investigation history is excluded**; the §7.4 prompt | `phases/p3_implement.py` | §7.4 | unit asserts the trajectory is absent from the P3 message list; generated diffs on fixtures contain no explanatory comment and no defensive `try` | **FR-22, FR-23, FR-24, FR-25, FR-26** |
| T5.7 | Malformed-edit corpus: capture every unparseable/unappliable model output to `tests/fixtures/edits/` and run the ladder against it | `tests/`, `scripts/` | §17 C | corpus ≥40 samples; ladder reports an eventual apply rate | — |

**WP5 done when:** the apply rate on the corpus is ≥95 % across all three formats.

---

## 8. WP6 — Phase 4 verification  *(D2 AM, ~2 h, CORE)*

| ID | Task | Files | Spec | DoD | Ticks |
|---|---|---|---|---|---|
| T6.1 | **Lint gate scoped to changed files, diffed against the baseline**; no linter configured → no-op | `verify/lint_gate.py` | §9.2 | **`py-lint-debt` (40 pre-existing errors) does not block — this is the single most important test in the suite** | **FR-27** |
| T6.2 | Scoped test selection, five-step ladder: same-name → importing tests → `-k`/`-run`/`-t` on changed symbols → nearest test dir → skip with a note | `verify/scope_tests.py` | §9.3 | a scoped set is found on ≥11 of 13 fixtures | **FR-28** |
| T6.3 | Gate schedule: G1/G2/oracle/G4 every cycle; **G3 full suite once as the pre-submission gate**, at most twice per run | `phases/p4_verify.py` | §9.3, §26.2 | a 5-cycle run executes the full suite ≤2 times | **FR-29** |
| T6.4 | Judges on the **cheapest** model: G4 diff-sanity with the **anti-sycophancy rule**, G5 per-function best practices, **flag-only** | `verify/judges.py` | §9.6 | unit: a "YES" whose stated mechanism names no identifier present in the diff is downgraded to `inconclusive`; G5 never edits a file | **FR-31, FR-32** |
| T6.5 | Failure triage: extract the assertion, the failing test source and the stack frames intersecting our diff; attribute to a **specific hunk**; route that hunk only | `verify/triage.py` | §9.5 | on an induced regression the retry targets the offending hunk, not the whole file | FR-33 |

**WP6 done when:** classification is correct on `py-red-baseline`, `py-lint-debt` and `py-flaky`, and the full suite runs at most twice per run.

---

## 9. WP7 — Phase 5, loop control, recovery  *(D2 AM, ~2 h, CORE)* ← **SHIPPABLE LINE**

| ID | Task | Files | Spec | DoD | Ticks |
|---|---|---|---|---|---|
| T7.1 | Six conditions with their evidence sources; C2 accepts a `FIXED` test transition as overriding the judge | `phases/p5_confidence.py` | §10.1 | unit per condition against a constructed state | **FR-34** |
| T7.2 | `decide()` with **hard gates C3/C4/C5 checked first and no fall-through to SUBMIT** | `phases/p5_confidence.py` | §10.2 | **exhaustive test over all 64 condition combinations: no combination with a false hard gate returns `SUBMIT`** | **FR-35** |
| T7.3 | Orchestrator: transition **table** (not scattered conditionals), 5-cycle cap, attempt records, **best-attempt restore** by `(new_failures ↑, judge ↓, hygiene ↑, diff ↑)` | `orchestrator.py` | §9.7, §10.2 | unit: given attempts of 8/10 then 7/10 passing, the 8/10 tree is restored | **FR-33** |
| T7.4 | Failure taxonomy with deterministic recovery dispatch (11 types) | `recovery/taxonomy.py` | §27 | unit: each type routes to its deterministic action before any re-prompt | NFR-1, NFR-2 |
| T7.5 | Stuck detection: six signals from the event log; on trigger summarize, mark the failed assumption, escalate a level | `recovery/stuck.py` | §28 | unit: the same command with identical output 3× triggers escalation, not a retry | NFR-1 |
| T7.6 | **Rejected-alternatives recovery** — re-enter P1 with the next untried hypothesis rather than regenerating | `recovery/alternatives.py` | §29 | unit: cycle 3 on a 2-hypothesis fixture selects H2, not a re-prompt of H1 | FR-35 |
| T7.7 | Final report to stdout + `git diff`; exit codes 0/2/3/4/5 with tree restoration on 4 and 5 | `__main__.py`, `report.py` | §11.3, §11.4 | each exit path exercised; 4 and 5 leave `git status` clean | **FR-3, FR-4, FR-36** |

**WP7 done when:** all 13 fixtures run end to end, every PRD FR is exercised, and the harness never ships a worse tree than its best attempt.

> **This is the line. If the clock runs out here, you have a complete, PRD-compliant harness.**

---

## 10. WP8 — Evidence, replay, efficiency  *(D2 midday, ~2 h, CORE-for-score)*

Not required for correctness; this is where the "evidence" and "efficiency" criteria are won.

| ID | Task | Files | Spec | DoD | Ticks |
|---|---|---|---|---|---|
| T8.1 | `run_report.md` — the eight-section narrative, including **what was *not* changed and why** | `report.py` | §30.1 | generated on every run; section 5 surfaces `alternatives_rejected` | **FR-36** |
| T8.2 | **Replay mode wired to `make test`** — re-executes the recorded trajectory against the current repo, **requires no API key**; falls back to the unit suite when no run exists | `replay.py`, `Makefile` | §30.2 | `unset AI_API_KEY && make test` replays a prior run and prints a per-step pass/fail table | **NFR-5** |
| T8.3 | **Prompt caching**: freeze the stable prefix per phase, emit the breakpoint marker per adapter, log `cache_read`/`cache_write` | `context/assemble.py`, `model/adapters/*` | §23 | a 2-cycle run shows a cache-hit ratio >50 % on input tokens | NFR-1 |
| T8.4 | Cost: estimate printed **before** the run; per-call token line; totals and per-phase breakdown after | `budget.py`, `report.py` | §30.3 | `make run` output opens with a budget line and closes with a cost line | NFR-1 |
| T8.5 | **Trust boundary**: wrap issue text and every tool result in `<untrusted_content>`; the system-prompt clause; credential never in a prompt or a subprocess env | `context/assemble.py` | §31 | unit: a file containing "ignore previous instructions" reaches the model wrapped; a scope-violating edit is still reverted by C4 | NFR-4 |

---

## 11. WP9 — ENH, in priority order  *(D2 PM, ENH)*

**Each item ships only if §13.3's gate passes.** Build in this order and stop when time runs out.

| ID | Task | Spec | Gate |
|---|---|---|---|
| T9.1 | `git bisect` for regression-shaped issues, with all five guards | §21.3 | resolves `py-regression` with fewer P1 tokens |
| T9.2 | Search subagent with disposable context returning ≤1.5 k findings | §24 | removes context-overflow failures in the T0 column |
| T9.3 | Git worktrees + parallel candidate evaluation | §25.1 | wall-clock drop on multi-candidate fixtures |
| T9.4 | Reproduction-test oracle, written to `.harness/`, **never committed** | §25.2 | improves candidate ranking on ≥2 fixtures |
| T9.5 | Normalized vote + extended ranking incl. blast-radius containment | §8.4, §25.3 | T0 `resolved` improves |
| T9.6 | Template repair as the terminal fallback | §25.4 | resolves ≥1 otherwise-failed T0 run |
| T9.7 | Co-change mining | §21.4 | improves localization on `py-vague` |
| T9.8 | tree-sitter tags + PageRank repo map — **last**, and only if SBFL proved insufficient | §4.3 | improves `py-vague` / `py-upstream` in the T0 column |

---

## 12. WP10 — Gates  *(D2 final 2 h, MANDATORY, reserved)*

| ID | Task | DoD |
|---|---|---|
| T10.1 | **Clean-clone gate.** Fresh container, from a machine that never built this project: `git clone` → `export AI_API_KEY` → `make setup` → `make run` → `make test` | the full evaluator sequence passes cold, twice |
| T10.2 | Fault injection across §15 and §36: kill discovery, remove `rg`, break tree-sitter, remove `coverage`, 500-storm the provider, red baseline, 40-error lint debt, non-git directory, 8 k-context model | every row degrades as documented; none aborts the run |
| T10.3 | `README.md` (usage, env vars, what it does, text-only statement); commit the final `bench/reports/` matrix | a reader can run it without asking a question |

---

## 13. TB — Test and fixture track  *(continuous, starts immediately)*

Nothing in WP1–WP10 can be verified without this. It starts at hour one and runs in parallel throughout.

### 13.1 Fixtures

Each is a small git repo with a seeded bug, a failing test, and a known-good reference patch.

| Fixture | Lang | Exercises | Needed by |
|---|---|---|---|
| `py-offbyone` | python | happy path, SBFL ranking | T4.3, T5.6 |
| `py-none-guard` | python | scoped-test selection | T6.2 |
| `py-upstream` | python | cause 2 frames above the symptom | T4.6 |
| `py-vague` | python | 2-line issue → hypothesis elimination | T4.1, T4.6 |
| `py-env-missing` | python | **config/external — correct answer is not a code change** | T3.4, T4.6 |
| `py-dep-bump` | python | lockfile skew | T3.4 |
| `py-red-baseline` | python | 2 pre-existing failures | T2.5, T2.6 |
| `py-lint-debt` | python | 40 pre-existing lint errors | **T6.1** |
| `py-flaky` | python | one flaky test | T2.6 |
| `py-cross-caller` | python | signature change, 4 callers | T4.7 |
| `py-regression` | python | bug introduced by a known commit | T9.1 |
| `ts-type-narrow` | ts | non-python toolchain, tsc gate | T2.3 |
| `go-nil-map` | go | `go test -json` parsing | T2.4 |
| `js-large-file` | js | 2 000-line file → format selection | T5.1 |

### 13.2 Bench harness

```
bench/
├── tasks/*.yaml      repo · issue · setup · tests · success criteria
├── runner.py         matrix: fixtures × {T0, T1, T2}
├── evaluator.py      resolved? regressions? diff ratio?
├── metrics.py        emits §33.1's per-run JSON
└── reports/          one JSONL per matrix run, committed
```

### 13.3 The ENH gate

> **An ENH feature ships only if the matrix shows a gain in the T0 column.**
> A feature that helps only T2 is helping the model, not the harness. **A regression in the T0 column blocks a merge.**

### 13.4 Standing test suites

| Suite | Content |
|---|---|
| unit | provider detection · model selection · transition table · edit parsing · anchor matching · result parsers · classification truth table · budget arithmetic · redaction · **the 64-combination `decide()` test** |
| corpus | the malformed-edit corpus (T5.7) |
| replay | recorded runs replayed offline, asserting identical artifacts (T8.2) |
| matrix | fixtures × tiers, recorded to `bench/reports/` |

---

## 14. PRD coverage matrix

Every requirement in `PRD.md`, the task that delivers it, and how it is proven. **This table is the acceptance checklist.**

### 14.1 Functional requirements

| # | Requirement | Task(s) | Verified by |
|---|---|---|---|
| FR-1 | Accept a GitHub issue as a single text input | T0.3, T4.1 | `argv` / `ISSUE` / `ISSUE_FILE` / stdin all tested; non-TTY never blocks |
| FR-2 | Operate autonomously from receipt to submission | T7.3 | no interactive call exists in the loop — asserted by a source grep test |
| FR-3 | Produce a git diff or set of modified files | T2.2, T7.7 | working tree + `diff.patch` present on every fixture run |
| FR-4 | Exit with a clear status and reason | T7.7 | all five exit paths exercised; 4 and 5 leave the tree clean |
| FR-5 | Log each phase to stdout, human-readable | T0.4 | fixture runs produce the §11.1 transcript; ANSI-free when piped |
| FR-6 | Detect provider from key prefix **without an API call** | T1.1 | 40-key unit table; network disabled during the test |
| FR-7 | Query the provider's model listing endpoint | T1.2 | live smoke on ≥2 providers; non-chat models filtered out |
| FR-8 | Highest-ranked for primary, lowest for verification, from a hardcoded table | T1.3, T1.7 | 80-id unit; shuffled input gives identical output |
| FR-9 | `HARNESS_MODEL` / `HARNESS_CHEAP_MODEL` take precedence | T1.4 | override absent from discovery is still selected |
| FR-10 | Any OpenAI-compatible provider via `AI_BASE_URL` | T1.5 | local Ollama endpoint drives a full run unmodified |
| FR-11 | Print provider, available models, selected models at startup before work | T1.8 | startup block asserted; emitted before indexing |
| FR-12 | Investigation completes before implementation; **no code written first** | T4.6, T4.8 | **the P3 tool set is unreachable from P1 — unit-asserted**; `HARNESS_DRY_RUN` leaves `git status` clean |
| FR-13 | Structured root-cause report: statement, files, classification, confidence | T4.6 | `RootCauseRecord` schema validation on every run |
| FR-14 | Check external factors before concluding "code bug" | T3.4, T4.6 | `py-env-missing` classifies `config`; probes run **before** classification |
| FR-15 | ≥2 competing hypotheses for vague issues, eliminated with codebase evidence | T4.1, T4.6 | `py-vague` generates 3, each with an **executed** check and a recorded verdict |
| FR-16 | Examine git log for recent changes to relevant files | T3.3 | history appears in the P1 seed on every run |
| FR-17 | Low confidence → conservative minimal fix | T4.6 | forced-low-confidence run caps at 15 lines / 1 file and forbids signature changes |
| FR-18 | Explicit list of files to change and files that must not change | T4.7 | `ChangePlan` validated; test/lock/generated paths auto-denied |
| FR-19 | Classify single-file vs cross-cutting and adjust strategy | T4.2, T4.7 | `py-cross-caller` marked `cross_cutting` |
| FR-20 | Identify interface changes and **all callers requiring updates** | T4.7 | `py-cross-caller` enumerates all 4 call sites; symbol-level, not import-level |
| FR-21 | Plain-English description of the fix before implementation | T4.7 | `fix_description` present and non-empty before P3 starts |
| FR-22 | Read the three adjacent functions before writing code | T3.2, T5.6 | `neighbours(k=3)` injected unconditionally by code, not by prompt |
| FR-23 | Match indentation, naming, comment style, error handling | T3.2, T5.6 | style profile injected; style-delta check in the apply ladder |
| FR-24 | No new abstractions, dependencies, defensive code, or patterns | T5.4, T5.6 | enumerated prohibitions + new-dependency block + scope guard |
| FR-25 | No debug output, TODO, dead code, unused imports | T5.5 | hygiene scan on **added lines only**; slop audit over 20 diffs |
| FR-26 | Smallest diff; re-evaluate if >30 % over the estimate | T5.4 | diff guard at 1.3× triggers a rescope |
| FR-27 | Run the repo's linter before tests; lint errors block | T2.3, T6.1 | **`py-lint-debt` does not deadlock**; only new diagnostics block |
| FR-28 | Scoped tests before the full suite | T6.2 | scoped set found on ≥11 of 13 fixtures |
| FR-29 | Run the full suite; distinguish caused-by-fix from pre-existing | T2.5, T2.6, T6.3 | `py-red-baseline` reports 2 pre-existing, 0 new |
| FR-30 | Do not fix pre-existing failures; document and proceed | T2.6, T8.1 | pre-existing failures named in the report; never edited |
| FR-31 | Diff sanity check on the **cheapest** model; negative → re-investigate | T6.4 | judge routed to `cheap`; a `false` verdict routes to P1 |
| FR-32 | Best-practices check per modified function, cheapest model, **flag only** | T6.4 | G5 never writes a file — unit-asserted |
| FR-33 | Max five fix-and-verify cycles; exit with the best state and a reason | T6.5, T7.3 | 8/10 then 7/10 → the 8/10 tree is restored |
| FR-34 | Evaluate six conditions before declaring success | T7.1 | per-condition unit tests |
| FR-35 | Any failing condition loops back — **never a silent submission** | T7.2, T7.6 | **exhaustive 64-combination test** |
| FR-36 | Output the confidence report to stdout alongside the final diff | T7.7, T8.1 | §11.3 block + `git diff` on every run |

### 14.2 Non-functional requirements

| # | Requirement | Task(s) | Verified by |
|---|---|---|---|
| NFR-1 | Token efficiency; tool-executable work never delegated to a model | T0.6, T1.7, T3.6, T3.7, T4.3–T4.5, T7.4, T8.3 | primary model used only in P1/P2/P3; judges on `cheap`; a source review confirms no prompt asks for a fact `git`/`grep`/AST supplies; cache-hit ratio reported |
| NFR-2 | Graceful degradation; no phase fails silently | T0.5, T1.6, T2.1, T3.1, T10.2 | every row of §15 and §36 exercised by fault injection |
| NFR-3 | `make setup` succeeds on a clean Ubuntu 24 machine | T0.1, T0.2, T10.1 | **clean-container run, twice, from a machine that never built the project** |
| NFR-4 | No hardcoded secrets anywhere | T0.1, T0.4, T2.1, T8.5 | secret-pattern grep over the whole repo in CI; key absent from prompts and from subprocess environments; redaction unit test |
| NFR-5 | Reproducibility — deterministic harness logic | T0.7, T1.3, T7.3, T8.2 | **`make test` replays a recorded run offline with no API key and asserts identical artifacts**; temperature 0; `PYTHONHASHSEED=0`; total sort orders |
| NFR-6 | Startup logging within five seconds | T1.8 | `time make run` on a 5 000-file repo; block emitted before indexing |

### 14.3 PRD goals

| Goal | Where |
|---|---|
| G1 — root cause before implementation | T4.6 + the structural tool gate (T4.8) |
| G2 — minimal, correct diff | T5.4 diff guard · T5.6 prohibitions · C5 |
| G3 — zero AI slop | T3.2 style profile · T5.5 hygiene · T5.6 prohibitions · slop audit |
| G4 — multi-layer verification | WP6 in full |
| G5 — vague issue handling | T4.1 vagueness score · T4.6 hypothesis elimination · T4.6 conservative path |
| G6 — BYOK compatibility | WP1 in full |

---

## 15. Triage order if you fall behind

Cut in this order. Everything above a line stays.

```
CUT FIRST   WP9 entirely (T9.8 → T9.1, last-built first)
            T8.3 prompt caching · T8.4 cost detail
            T8.1 run_report.md  (the §11.3 stdout block already satisfies FR-36)
            T1.9 capability probe (assume no tool calling — the safe path)
            T3.6/T3.7 refinements (keep basic truncation)
─────────── never cut below this line ───────────
            WP0–WP7 in full          ← PRD compliance
            T8.2 replay              ← NFR-5 proof
            T8.5 trust boundary      ← 20 lines
            WP10 gates               ← the 2 reserved hours
```

Two things that look optional and are not: **T6.1** (a whole-repo lint gate deadlocks every cycle) and **T7.2**'s 64-combination test (a fall-through to `SUBMIT` violates FR-35 silently, which is the worst possible failure — it looks like success).

---

## 16. Acceptance run

The literal sequence the evaluator will execute. Run it end to end at T10.1, on a machine that has never built this project.

```bash
git clone <repo> && cd <repo>
export AI_API_KEY="<key>"
make setup                       # ≤3 min, no sudo, no network beyond pip
make run ISSUE="$(cat issue.txt)"
make test                        # replay — no API key required
```

**Pass criteria:**

- [ ] `make setup` exits 0 on a clean container, with the optional install deliberately broken
- [ ] the startup block appears within 5 s, naming provider, models, tier and toolchain — **FR-11, NFR-6**
- [ ] every phase logs to stdout as it runs — **FR-5**
- [ ] no code is written before the root-cause report — **FR-12**
- [ ] the final diff is proportional and touches only planned files — **FR-26, C4, C5**
- [ ] the full suite runs and pre-existing failures are named, not fixed — **FR-29, FR-30**
- [ ] the confidence report prints alongside the diff — **FR-34, FR-36**
- [ ] the exit code and reason are correct — **FR-4**
- [ ] `make test` replays offline with no API key — **NFR-5**
- [ ] `git grep` finds no credential anywhere in the repo — **NFR-4**

When all ten are checked on a cold machine, the harness is submittable.

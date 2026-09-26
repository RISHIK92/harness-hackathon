# Product Requirements Document
## AI Coding Harness — LCC × DevClub Hackathon 2026

**Version:** 1.0  
**Status:** Final  
**Audience:** Engineering team, evaluators

---

## 1. Purpose

This document defines the product requirements for an autonomous AI coding harness that accepts a GitHub issue as text input and produces correct, verified code changes against a real repository. The harness wraps a foundation model supplied at runtime via `AI_API_KEY` and is evaluated against other teams using the identical model under identical conditions. Competitive advantage derives entirely from harness engineering quality.

---

## 2. Problem Statement

Most AI coding agents fail in practice because they:

- Start writing code before understanding the root cause
- Dump irrelevant file content into context, degrading model output quality
- Produce a single attempt with no iteration or verification
- Write generic, style-inconsistent code that works syntactically but fails review
- Cannot handle vague issue descriptions that require inference
- Have no mechanism to detect whether a fix actually addresses the stated problem

The harness must solve all of these systematically, not incidentally.

---

## 3. Evaluation Context

### 3.1 Constraints from Submission Spec

- Entry point is `make setup` followed by `make run`
- API credential arrives as `AI_API_KEY` environment variable only — never hardcoded
- Input is text only — no image, audio, or video modalities required
- The prescribed model is the same for every team — competitive advantage is purely in harness engineering
- The evaluator will not modify source code, contact the team, or manually configure the project

### 3.2 Scoring Criteria (Inferred)

- **Correctness** — does the fix actually resolve the issue?
- **Verification** — do existing tests pass after the fix?
- **Code quality** — is the fix idiomatic, minimal, and clean?
- **Robustness** — does it handle vague, ambiguous, or underspecified issues?
- **Efficiency** — does it produce the fix without excessive token waste or unnecessary changes?

---

## 4. Goals

### 4.1 Primary Goals

**G1 — Root cause before implementation.** The harness must identify the true root cause of the issue — including whether it is a code bug, configuration problem, dependency issue, or external factor — before writing a single line of code.

**G2 — Minimal, correct diff.** The output must be the smallest possible change that fully resolves the root cause. No unrelated refactors, no defensive additions, no stylistic opinions imposed on the codebase.

**G3 — Zero AI slop.** Generated code must be indistinguishable in style from the surrounding codebase. The harness enforces style matching, naming convention adherence, and anti-pattern detection before accepting any generated code.

**G4 — Multi-layer verification.** Every fix must pass static analysis, unit-level verification of changed functions, and the full test suite before being submitted. Failures trigger targeted re-investigation and re-implementation, not blind retry.

**G5 — Vague issue handling.** The harness must produce a defensible fix even when the issue description provides minimal information, by generating and eliminating hypotheses using codebase evidence rather than guessing.

**G6 — BYOK compatibility.** The harness must detect the provider and available models from the supplied key automatically, select the best available model for each phase, and accept override via environment variables with zero code changes required.

### 4.2 Non-Goals

- Building a general-purpose chat interface
- Supporting image, audio, or video input
- Providing a GUI or web interface
- Storing persistent memory across separate evaluation runs
- Supporting parallel multi-issue execution

---

## 5. User Stories

**As an evaluator,** I run `make setup` once and `make run` once, supply the issue as text, and receive a correct code fix without any further interaction.

**As an evaluator,** I expect the fix to pass the existing test suite without me having to run anything manually after the harness exits.

**As an evaluator,** I expect the git diff to be proportional to the scope of the issue — a small bug produces a small diff.

**As an evaluator with a vague issue,** I expect the harness to make a reasonable, well-evidenced inference about the problem and produce a targeted fix rather than a speculative rewrite.

**As a developer on the team,** I can test the harness locally with my own API key and any supported provider without modifying source code.

---

## 6. Functional Requirements

### 6.1 Harness Lifecycle

**FR-1** The harness must accept a GitHub issue as a single text input at runtime.

**FR-2** The harness must operate autonomously from issue receipt to fix submission without human intervention.

**FR-3** The harness must produce a git diff or set of modified files as its output.

**FR-4** The harness must exit with a clear status indicating success or failure and the reason.

**FR-5** The harness must log each phase of execution to stdout in a human-readable format so the evaluator can follow the reasoning process.

### 6.2 Provider and Model Detection

**FR-6** The harness must detect the API provider from the key prefix without making an API call.

**FR-7** The harness must query the provider's model listing endpoint to discover available models.

**FR-8** The harness must select the highest-ranked available model for primary phases and the lowest-ranked for verification phases using a hardcoded ranking table per provider.

**FR-9** The harness must accept `HARNESS_MODEL` and `HARNESS_CHEAP_MODEL` environment variable overrides that take precedence over auto-detection.

**FR-10** The harness must support any OpenAI-compatible provider via `AI_BASE_URL` environment variable.

**FR-11** The harness must print the detected provider, available models, and selected models to stdout at startup before any work begins.

### 6.3 Phase 1 — Root Cause Investigation

**FR-12** The harness must complete root cause investigation before entering the implementation phase. There must be no code writing prior to this phase completing.

**FR-13** The investigation phase must produce a structured root cause report containing: a one-sentence root cause statement, the affected files, the bug classification (logic, null, type, config, external, race), and a confidence level.

**FR-14** The investigation phase must check for external factors — dependency version mismatches, environment variable absence, third-party API contract changes, runtime version differences — before concluding the root cause is a code bug.

**FR-15** The investigation phase must generate at least two competing hypotheses for vague issues and eliminate them using codebase evidence before selecting the most supported hypothesis.

**FR-16** The investigation phase must examine git log for recent changes to relevant files as part of its evidence gathering.

**FR-17** If confidence is low after exhausting investigation steps, the harness must attempt a conservative minimal fix rather than a speculative large change.

### 6.4 Phase 2 — Change Scoping

**FR-18** The scoping phase must produce an explicit list of files to change and files that must not change.

**FR-19** The scoping phase must classify the fix as single-file or cross-cutting and adjust the implementation strategy accordingly.

**FR-20** The scoping phase must identify whether any interfaces, function signatures, or exported APIs change, and if so must identify all callers that require updates.

**FR-21** The scoping phase must produce a plain-English description of the fix before implementation begins.

### 6.5 Phase 3 — Implementation

**FR-22** The implementation agent must read the three functions adjacent to the target function before writing any code, to extract style, naming conventions, and patterns.

**FR-23** Generated code must match the indentation, naming convention, comment style, and error handling pattern of the surrounding code in the same file.

**FR-24** The implementation must not introduce new abstractions, new dependencies, defensive code, or structural patterns not already present in the codebase unless the scoping phase explicitly required them.

**FR-25** The implementation must not leave debug output, TODO comments, dead code, or unused imports.

**FR-26** The implementation must produce the smallest diff that resolves the root cause. If the generated diff is more than 30% larger than what the scoping phase estimated, the implementation phase must re-evaluate before proceeding.

### 6.6 Phase 4 — Verification

**FR-27** The harness must run the repository's existing linter or static analysis tool before running any tests. Lint errors block test execution until resolved.

**FR-28** The harness must run tests scoped to changed files before running the full test suite, for fast feedback.

**FR-29** The harness must run the full existing test suite and distinguish between test failures caused by the fix and pre-existing failures.

**FR-30** The harness must not attempt to fix pre-existing test failures. It must document them and proceed.

**FR-31** The harness must perform a diff sanity check using the cheapest available model: given the root cause statement and the diff, does this diff address the stated root cause? A negative answer triggers re-investigation.

**FR-32** The harness must perform a best-practices check on each modified function using the cheapest available model, flagging but not automatically fixing style issues.

**FR-33** The harness must enforce a maximum of five fix-and-verify cycles before exiting with the best available state and a failure reason.

### 6.7 Phase 5 — Confidence Scoring

**FR-34** Before declaring success, the harness must evaluate six conditions: root cause identified with evidence, fix addresses root cause directly, all existing tests pass, no unintended file changes, diff size proportional to scope, external factors ruled out or addressed.

**FR-35** Any failing condition must trigger a loop back to the appropriate phase, not a silent submission.

**FR-36** The harness must output the confidence report to stdout alongside the final diff.

---

## 7. Non-Functional Requirements

**NFR-1 — Token efficiency.** Primary model calls are reserved for investigation, scoping, and implementation. Verification calls use the cheapest available model. Tool-executable tasks (linting, test running, grep, git) must never be delegated to a model call.

**NFR-2 — Graceful degradation.** If model detection fails, the harness defaults to OpenAI-compatible client and proceeds. If the cheapest model is unavailable, the primary model handles verification. No phase fails silently.

**NFR-3 — Clean environment.** `make setup` must succeed on a clean Ubuntu 24 machine with only standard system packages. All dependencies must be declared and installed by setup.

**NFR-4 — No hardcoded secrets.** No API keys, tokens, passwords, or credentials appear anywhere in source code, Makefile, configuration files, or documentation committed to the repository.

**NFR-5 — Reproducibility.** Given the same issue, same repository, and same model, the harness must produce equivalent fixes across runs. Non-determinism in model output is acceptable; non-determinism in harness logic is not.

**NFR-6 — Startup logging.** The harness must print provider, available models, selected models, and detected repository language within five seconds of `make run`.

---

## 8. Out of Scope

- GUI, web interface, or REST API
- Parallel issue processing
- Cross-run memory or persistent session state
- Multimodal input processing
- Generating new test cases (the harness runs existing tests only)
- Fixing pre-existing failures in the test suite

---

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Prescribed model is weaker than expected | Medium | High | Architecture compensates via decomposed prompts and more iteration cycles |
| Evaluation repo uses unsupported language | Low | Medium | Tree-sitter covers 50+ languages; fallback to grep-based navigation |
| Neo4j apt install fails on eval machine | Medium | Low | NetworkX in-memory graph is always the primary; Neo4j is optional persistence |
| Root cause investigation exceeds token budget | Medium | Medium | Hard cap on investigation steps; conservative fix at cap |
| Vague issue has no inferable root cause | Low | High | Document confidence level; produce minimal conservative fix with explanation |

---

## 10. Success Criteria

The harness is successful if:

- `make setup && make run` completes without error on a clean machine
- The fix resolves the stated issue as evidenced by test passage
- The git diff contains no unrelated changes
- The fix is style-consistent with the surrounding codebase
- The harness exits cleanly with a confidence report

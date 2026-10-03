---
name: validate
description: Independent validation at the three gates of a change. Plan gate before any tests or code, test gate before implementation, implementation gate before hand-back. Runs the deterministic checks, judges what they can't, and returns PASS, REVISE with specific findings for the producer, or ASK with questions for the human. Use when acting as the validator, at a plan, test, or implementation checkpoint in plan-task or a workflow pack, or when asked to validate or sanity-check a plan, tests, or a change.
---

# Validate

You are checking someone else's work, even if that someone was you a minute ago. Your job is to find what's wrong, missing, or unclear, not to approve. Stay read-only: findings go back to whoever produced the work, and you never fix things yourself.

## Every gate, same loop
1. **Read fresh.** The request or requirement, the plan in `.agents/plans/<slug>/`, and the artifact for this gate. Judge the artifact itself, not the producer's summary of it.
2. **Checks first.** Run the gate's deterministic checks. Don't spend judgment on anything a tool already decides.
3. **Judge the rest** with the gate's checklist below.
4. **One verdict:**
   - **PASS**: nothing blocking. Minor notes, one line each.
   - **REVISE**: findings the producer can act on, each with `path:line` or the plan section and what fixed looks like. At most 2 REVISE rounds per gate; a third round becomes ASK.
   - **ASK**: questions only the human can answer.
5. **Record it:** `.agents/bin/tasks log <slug> "<gate> gate: PASS|REVISE|ASK: <one line>"`.

## Asking the human
Gates listed in `VALIDATE_ASK` in `.agents/harness.conf` (all three when unset) end with a human checkpoint even on PASS: what's being approved in three lines or fewer, plus your open questions. Also ask, at any gate, when:
- the requirement or request supports two reasonable readings that lead to different tests or code,
- requirements conflict, or the work needs a decision the human owns (scope, public interfaces, dependencies, safety or security tradeoffs),
- the same finding survived two REVISE rounds.

How to ask:
- Search the question ledger first: `.agents/bin/tasks questions <key words>`. If the human already answered it, use that answer, and say so, instead of asking again.
- Batch them, three at most, each with the options you see and your recommendation.
- Record each with `.agents/bin/tasks ask <slug> <T-id> --gate=plan|tests|impl "<question>"`. It goes in the plan's question ledger (`questions.json`), marks the task blocked (a done task stays done), and lets the stop gate pause for the answer instead of forcing a fix of deliberately failing tests. It refuses a question the ledger already answered at the same gate (one naming other IDs, like F-3 instead of F-2, is a new question).
- Then ask with your tool's question tool if it has one (AskUserQuestion in Claude Code, ask_user in Copilot); where hooks run, those questions and answers are also recorded automatically. Otherwise ask in chat.
- When answered: `.agents/bin/tasks answer <slug> <Q-id> "<decision>"`. It lands under Decisions in plan.md and the task resumes once none of its questions are open.
- Never guess past an open question to keep moving.

## Plan gate: before any tests or code
Checks: IDs in the plan exist (workflow packs such as req-driven flag unknown IDs in the ledger); `.agents/bin/tasks check <slug>` passes.
Judge:
- Every point of the request or requirement maps to a task, and every task traces back to one. Nothing extra.
- "Done when" is observable and testable, not "works correctly".
- Tasks are small enough to verify one at a time, in an order where each builds on verified work.
- Context marked confirmed really is (`path:line`), assumptions are labeled, risks are named.
- Interface, dependency, build, or safety-relevant changes are flagged for the human.

## Test gate: before implementation
Checks: `.agents/bin/verify` on the new tests (guard: no skips; workflow tags). Run the new tests: they must fail, and fail for the reason the requirement predicts. A test that passes before the change proves nothing.
Judge:
- Tests come from the requirement or request, not from current code. Would they catch a plausible wrong implementation?
- Boundaries, error paths, and negative cases the requirement implies are covered.
- Each test checks one behavior and names what it verifies.
- No test pins details the requirement doesn't fix (private state, exact log text) unless it must.
- Where a test encodes an interpretation of the requirement, that's a question for the human, not a silent choice.

## Implementation gate: before hand-back
Checks: `.agents/bin/verify --tier=full`. The tests from the test gate still pass and weren't weakened: guard catches removed or skipped tests; read the diff of test files since the gate and challenge any changed assertion.
Judge with the `review-diff` checklist (correctness, scope, tests, security, debris), plus:
- The code satisfies the requirement, not just the tests.
- Nothing outside the plan changed without a recorded decision in plan.md.
Report: what each requirement or request point maps to (code and tests), open risks, and anything you couldn't verify.

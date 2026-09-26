---
name: req-driven
description: Requirements-driven changes. Every change starts from a requirement ID in the project's requirements source, tests name the requirement they verify, and code stays traceable back to it. Use in this repo whenever implementing, fixing, or changing behavior in the paths AGENTS.md says are requirements-driven, when a task or issue mentions a requirement ID, or when asked about traceability or which requirements are covered.
---

# Requirements-driven change

The requirements source is the authority, not the code and not the request wording. Where it lives, what IDs look like, and which paths are in scope are set in `.agents/harness.conf` (`REQ_SOURCE`, `REQ_ID_PATTERN`, `REQ_SCOPE`, `REQ_TESTS`). `verify` enforces the mechanical parts: IDs must exist, new tests must name one, and in-scope changes must reference one. This skill covers the judgment.

Three gates, each run with the `validate` skill: plan, tests, implementation. Gates listed in `VALIDATE_ASK` (all three by default) end with a check-in with the human.

## 1. Pin the requirement, then the plan gate
- Find the requirement(s) in `REQ_SOURCE`. Quote the ID and its text in the plan.
- Never invent an ID or reword a requirement to fit the code. If it's missing, ambiguous, or conflicts with another one, ask (`tasks ask`) and wait.
- Plan with `plan-task`, the ID at the start of each task, e.g. `tasks add <slug> "REQ-12: reject expired certificates" "REQ-12 tests pass; verify passes"`. A task in progress counts as the trace for the change.
- **Plan gate:** validate the plan. No tests or code until it passes and, if the gate is in `VALIDATE_ASK`, the human has agreed.

## 2. Tests from the requirement, then the test gate
- Derive acceptance criteria from the requirement text, not from the current implementation: stated behavior, boundaries, error cases.
- Name the requirement in the test name or a comment right above it (`TEST(CertStore, REQ_12_RejectsExpired)` or `// Verifies: REQ-12`). `REQ_12` counts as `REQ-12`.
- Run them and confirm they fail for the reason the requirement predicts.
- **Test gate:** validate the tests. If you stop here to ask the human, record it with `tasks ask`; that lets the stop gate pause while the tests are deliberately red.

## 3. Implement, then the implementation gate
- Make the tests pass without editing them. If a test turns out wrong, say why, fix it as its own step citing the requirement text, and expect the validator to challenge it.
- Reference the ID where the requirement is realized (a short comment at the function or block) when that helps a reader find it later. Don't sprinkle IDs on every line.
- Stay inside the requirement. Anything extra is scope creep; note it for the human instead.
- **Implementation gate:** validate the change. `.agents/bin/verify --tier=full` passes and regenerates `.agents/cache/req-trace.md`.

## 4. Report
Which requirement IDs the change implements, which tests verify each, and anything in the requirement you could not cover.

## Who does what
With roles: a planner writes the plan (no code), a tester writes the tagged tests (test paths only), an implementer makes them pass (tests locked), and a validator runs the gates (read-only). One writer at a time.
Without roles: if your tool can start a subagent, run each gate's `validate` in one with a fresh context, so the check isn't the author grading their own work. Otherwise run it as a separate pass: re-read the artifact from disk, not from memory.

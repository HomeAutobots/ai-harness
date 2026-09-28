---
name: feature-driven
description: Feature-Driven Development (FDD) with local artifacts. Model, feature list, plan by feature, design by feature, build by feature, with your approval at each check-in. Use in this repo when building or changing behavior in the paths AGENTS.md says are feature-driven, or when asked about the feature list, a feature's design, or feature progress.
---

# Feature-driven change

Everything FDD lives in `FDD_DIR`, set in `.agents/harness.conf` (default `.agents/fdd/`): `model.md`, `features.md`, `designs/<ID>.md`, and `approvals`. It's the developer's working material, local and never committed; the team's shared docs live elsewhere. `verify` enforces the mechanics (list format, trace, design before build, no private IDs in shared code). This skill covers the judgment. Each gate runs through the `validate` skill; gates in `FDD_ASK` end with a check-in, where the human records approval with `.agents/workflows/feature-driven/bin/fdd approve ...`. You can't run that command; ask with `tasks ask <slug> <T-id> --gate=<g>` and wait (`--gate=plan` for the list and design check-ins, `--gate=impl` for the inspection). Naming the command in your question is fine if the question is one line in single quotes with no backticks and starts with words, not the command, e.g. `tasks ask f-12-sale-total T1 --gate=plan 'Design ready. Please run: .agents/workflows/feature-driven/bin/fdd approve design F-12'`.

Start every session with `.agents/workflows/feature-driven/bin/fdd status` and `.agents/bin/tasks list`: they show the approved list, each feature's milestone, and the plan in progress. That's the state; pick up from there.

## 1. Model
Draft `model.md` from the repo and the developer's description: subject areas, the main entities, how they relate. A map, one screen, not a spec.

## 2. Feature list, then check-in 1
- Draft `features.md`: `## Subject area`, `### FS-<n> Feature set`, then `- F-<n> <action> the <result> <by|for|of|to> a(n) <object> [TICKET-1]`. The ticket key only when the feature has a real one.
- Keep features small: a few hours of your work. Split anything bigger.
- Validate the model and list, then ask for `fdd approve list`. After approval, never reword or add a feature, or revise `model.md`, without a new check-in; the approval covers both files.

## 3. Plan by feature
- One plan per feature or small group, slug in lowercase: `tasks new f-12-sale-total "Sale total"`. Every task description starts with the feature ID: `tasks add f-12-sale-total "F-12: add sale total"`.
- Set the task to `doing` before touching code in `FDD_SCOPE`; that's how `verify` knows which feature a change belongs to.
- Start a branch when `.agents/bin/gitflow status` suggests `gitflow start` (the repo protects its base branch), or when you're on another feature's branch. Then use the ticket key if the feature has one: `gitflow start PROJ-123 <summary>`. Otherwise keep working where you are.
- Pick the next feature by feature-set order and dependencies. `fdd status` shows where each one stands.

## 4. Design by feature, then check-in 2
Write `designs/F-12.md`: approach, entities and files touched, and a test plan (behavior, boundaries, error cases). Validate it, then ask for `fdd approve design F-12`. No code in `FDD_SCOPE` before that. Editing an approved design voids the approval; ask again.

## 5. Build by feature, then check-in 3
- Tests from the design's test plan first, then the code. `verify` passes.
- Never write a private feature ID in code, tests, docs, commit messages, branch summaries (`gitflow start PROJ-123 <summary>`), plan titles, or PR text; use the ticket key or nothing. `verify` and the commit hook catch code and messages; the rest is on you.
- Commit, record it (`tasks set <slug> <T-id> done <sha>`), validate the change, then ask for `fdd approve inspect F-12` (the task stays done while you wait). The feature is done after that.

## Report
Which features moved and to what milestone (`fdd status`), and anything in a design you couldn't build.

## Who does what
With roles: the planner does the model, list, plans, and designs (FDD's chief programmer); the implementer builds (the class owner); the validator runs every gate. One writer at a time.
Without roles: run each gate's `validate` in a fresh subagent if your tool can start one; otherwise re-read the artifact from disk as a separate pass.

# Changelog

Bump `VERSION` and add an entry for every change that projects will pick up on upgrade. Call out anything that changes agent behavior (core rules, built-in skills), since those land in every project at once.

## 0.3.0-dev (unreleased)
- **Workflow packs.** `install.sh --workflow <name>` installs a process skill plus checks that `verify` runs after the project's tier scripts for every pack in `WORKFLOWS`. First pack: `req-driven` (generic requirements traceability: unknown IDs, untagged new tests, untraced changes, trace report, optional untested-requirement gate).
- **Pack extension points.** A workflow pack can ship a `policy.conf.snippet` (appended once, keyed on its first-line marker comment, so deleted rules stay deleted), `seed/` files (created once, never overwritten), and `checks/commit-msg.sh` (run by gitflow wherever it checks commit messages, including `gitflow commit` with hooks off; exit 1 or 2 rejects, and a check that can't run blocks too). With `GIT_LOCAL_HOOKS=auto`, git hooks now also install when an installed pack ships a message check. No change for existing packs. Packs can also ship `bin/` commands and a `checks/state.sh` for verify's cache key.
- **Fix:** `install.sh` no longer copies `__pycache__` directories from a stack or workflow pack in a dev checkout, and `.agents/.gitignore` ignores them. Picked up on upgrade.
- **Fix:** `verify`'s cache now includes plan ledgers. Before, starting a task after a failed run could return the stale failure (req-driven's "task in progress" trace). Picked up on upgrade.
- **feature-driven workflow pack.** Classic FDD with local artifacts (`.agents/fdd/`, never committed): model, feature list, per-feature designs, and approvals you record with `fdd approve` (blocked for agents). Checks: list format, design before build, tracing through the plan ledger, no private feature IDs in shared code or commit messages, and FDD files that git tracks or doesn't ignore (`fdd-not-local`); the full tier writes a parking-lot progress report. Opt in with `install.sh --workflow feature-driven`. The checks need python3; once a feature list exists, commits block without it.
- **Cache key is whole-tree.** `verify` results now depend on the full working-tree state plus harness config and packs, so a change elsewhere (a header, a requirements export) invalidates a cached per-file result.
- **harness-tailor** fills workflow pack settings and adds one AGENTS.md line naming the workflow.
- **Validation gates.** New built-in `validate` skill: plan, test, and implementation gates with PASS / REVISE / ASK verdicts, a two-round feedback loop, and human check-ins at the gates in `VALIDATE_ASK` (new harness.conf setting, default all three). `plan-task` and `req-driven` run their gates through it; `review-diff` is its implementation-gate checklist.
- **Question ledger.** Per-plan `questions.json` (plus `_general/` outside plans). `tasks ask` records a question with its gate, blocks the task, and refuses questions the ledger already answered (`--force` to re-ask); `tasks answer` records the answer, adds it to plan.md Decisions, and resumes the task; `tasks questions` searches all questions and answers. The stop gate pauses while a task waits on an answer, so a test gate can stop on deliberately failing tests.
- **`questions` hook feature** (on by default in new installs; add it to `HOOKS` in existing ones): before a question-tool call, an earlier answer is shown instead (once per session); after it, question and answer are recorded; a new session starts with a reminder of open questions. Claude Code and Copilot; Cursor has no question tool to hook.
- **Core rules** mention that plan-task's gates use `validate` (one line).
- **Git workflow.** New `.agents/bin/gitflow` (start, commit, update, check, push, pr, review, merge, install-hooks) driven by `.agents/git.conf` over a personal `~/.config/ai-harness/git.conf`. Templates for branch names and commit subjects both generate and validate; trailers, protected branches, PR base and tool (gh, glab, none), merge method, and `GIT_AGENT_MAY` (which steps agents may take). Enforced by local `commit-msg` and `pre-push` hooks, the policy hook, and, when the project sets `GIT_AGENT_MAY`, native deny rules. New `git-workflow` skill; harness-tailor proposes git.conf from repo history.
- **Generic by default.** Nothing is protected unless configured (`GIT_PROTECTED` takes any names or globs, e.g. `dev/main release/*`, and `{base}` for the base branch). Local git hooks install only when git.conf has something to enforce (`GIT_LOCAL_HOOKS=auto`), and `sync` adds them once it does. A verify tier that can't run is reported by `gitflow check`, not treated as a failure. Placeholders with no data (no ticket, no linked plan) leave no gaps in the PR body. `tasks link` optionally ties a plan to its branch for `gitflow pr`. "Absence" tests cover a zero-config trunk repo, gitflow with no plan or ticket, a plan with no workflow, and a workflow with no git config.
- **Commit templates.** `GIT_COMMIT_TEMPLATE` (or the repo's local `commit.template`) defines the whole message: subject, sections (required unless the guidance line says `(optional)`), and trailers, with self-filling placeholders. A `prepare-commit-msg` hook prefills the editor for humans; `gitflow commit --section Label=text` fills it for agents; `gitflow template` describes it; the `commit-msg` hook validates every message (including section values like `Refs: {ticket}` against the branch's ticket) and drops optional sections left empty. With no template, nothing changes.
- **Development setup.** New `tests/lint.sh` (fast static gate, including checks that new CLIs and skills are wired into `install.sh` and `BUILTIN_SKILLS` and that the harness never gets installed into its own repo), `tests/all.sh` (smoke under every awk), `scripts/package.sh` (release zip plus SHA-256) and CI on Ubuntu and macOS bash 3.2. None of this ships in the release zip.
- **Fix (cpp-cmake):** the sanitizer tier no longer sets `detect_leaks=1`. Apple clang's ASan aborts every test at startup when it's set, so on macOS the full tier failed every test. Linux keeps leak checks, since ASan enables them by default there. Your own `ASAN_OPTIONS` still wins. Picked up on upgrade.
- **Behavior change:** the core rule "no git push, commit only when asked" becomes "follow this repo's git workflow; steps outside `GIT_AGENT_MAY` are the human's". The default `GIT_AGENT_MAY` is `branch commit`, so agents still don't push by default.
- **Upgrading:** new installs drop `deny-cmd git push` and `deny-cmd git rebase` from policy.conf because git.conf covers them. Existing policy.conf files are project-owned and keep those lines, which still win; remove them to let git.conf decide. Add `questions` to `HOOKS` in harness.conf to get the question hooks.

## 0.2.0
Changes agent behavior in every project: the core rules are rewritten and hooks are on by default.

- **Core rules trimmed** to ~20 lines of non-inferable directives. No version string in the managed block, so it stays cache-stable across upgrades.
- **Feedback layer.** `verify` is now a harness-owned orchestrator with edit, turn, and full tiers that run project-owned `.agents/checks/{edit,turn,full}.sh`. New `check` (edit tier) and `guard` (suppressions, skipped or deleted tests). Shaped output, logs on disk, budgets, caching by tree state, baselines, `--json`.
- **Hooks** for Claude Code, Copilot (CLI, cloud agent, VS Code), and Cursor: policy enforcement before tool calls, `check` after edits, and a bounded stop gate that runs `verify` on turns that changed files. `AGENTS_HOOKS=off` disables them.
- **Policy** in `.agents/policy.conf`, enforced by the hook and rendered into Claude deny rules and Codex execpolicy rules.
- **Adapter rendering** merges into existing `.claude/settings.json`, `.cursor/hooks.json`, and `.gemini/settings.json` without touching other entries. Default `ADAPTERS` is now `claude copilot cursor`.
- **Plan ledgers.** `plan-task` now uses `.agents/plans/<slug>/{plan.md,tasks.json,progress.log}` through the new `tasks` CLI. Old single-file plans aren't migrated.
- **Integrity.** `sync --check` also fails on changed pinned skills (`sync --lock-skill`) and invisible Unicode in instruction files.
- **Evals.** `eval new/run/report` replays real fixes across arms A (no harness), B (hooks off), C (full).
- **Stack packs.** `install.sh --stack <name>`; first pack: `cpp-cmake`.
- **Skills.** `harness-tailor` proposes facts-only tailoring under a line budget and fills the tier scripts; `plan-task` covers the ledger and session hygiene; `review-diff` focuses on what tools can't see.
- **Requires python3** for hooks and JSON rendering (degrades with a warning without it).

Migration from 0.1: the installer moves a tailored `.agents/bin/verify` into `.agents/checks/turn.sh` and `full.sh`. Review both (they now receive changed files as arguments), then re-run harness-tailor to split out an edit tier.

## 0.1.0
- AGENTS.md with two managed blocks: core rules and a generated skills index.
- `.agents/bin/sync` renders the blocks and maintains adapters. `--check` for CI.
- `.agents/bin/verify` contract: one "is it green" command per project.
- Built-in skills: `harness-tailor`, `plan-task`, `review-diff`.
- Adapters: `claude` (CLAUDE.md import plus `.claude/skills` mirror), `gemini` (context file setting).
- Symlink or copy mirroring, for Windows checkouts without symlink support.

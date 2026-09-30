---
name: harness-tailor
description: Proposes the per-repo tailoring of the ai-harness for human review. Fills AGENTS.md with facts an agent can't infer (commands, boundaries, non-obvious conventions), writes the tiered check scripts in .agents/checks/ and proves they run, folds legacy instruction files (CLAUDE.md, .cursorrules, copilot-instructions, GEMINI.md) into AGENTS.md, and baselines existing lint findings. Use right after installing or upgrading the harness, when AGENTS.md or .agents/checks still contain TODO(harness-tailor), or when asked to set up, refresh, or re-tailor the AI harness. It proposes; a human approves.
---

# Tailor the harness to this repo

You are producing a proposal, not a finished change. A human reviews it before anything is committed. In local mode (`HARNESS_MODE="local"` in `.agents/harness.conf`) the harness's files are hidden from git, so `git diff` won't show your work: your report lists every file you touched. Research on context files is clear: generic or model-written guidance adds cost and doesn't help. Only facts an agent couldn't work out from the repo earn a line.

## Rules for this job
- **Facts only.** Commands, paths, prohibitions, non-obvious conventions. No overviews, no directory tours, no "write clean code". If the README or the code already makes it obvious, leave it out.
- **Budget.** Add at most ~25 lines to AGENTS.md outside the managed blocks. Anything bigger goes in `.agents/context/<topic>.md` with a "read when" row.
- **Prove every command.** Anything you write down, run once. If it can't run here (credentials, hardware, target device), mark it `(unverified)`.
- **Stay inside the lines.** Never edit between `<!-- harness:*:start -->` and `<!-- harness:*:end -->`, or any harness-owned file (`.agents/bin`, `.agents/lib`, `.agents/hooks`, `.agents/core`, `.agents/builtin`, and `.agents/skills/`, which sync renders).
- **No app code changes.** You touch AGENTS.md, CLAUDE.md, `.agents/checks/`, `.agents/context/`, `.agents/baselines/`, and legacy instruction files only.
- **Local mode: nothing the project tracks.** Never edit a file git tracks (`git ls-files --error-unmatch <path>`) and don't create files outside `.agents/`: they would show up in `git status`. Tracked legacy files go in the report instead.
- **Ask last.** Collect questions the repo can't answer (off-limits areas, deploy rules, who owns what) and put them in your report.

## Steps

### 1. Survey (read only)
Stop once you have the picture:
- README, CONTRIBUTING, docs index.
- CI config (.github/workflows, .gitlab-ci.yml, Jenkinsfile, azure-pipelines.yml). CI defines what "passing" means; the full tier should mirror it.
- Build and package manifests (CMakeLists.txt, CMakePresets.json, Makefile, package.json, pyproject.toml, Cargo.toml, go.mod, ...), lint and format config, devcontainer.
- `git log --oneline -30` for commit conventions.
- If this is a C/C++ CMake repo and `cpp-cmake` isn't in `STACKS` in `.agents/harness.conf`, say so in the report: the human can run `install.sh --stack cpp-cmake`.

### 2. Legacy instructions
Read CLAUDE.md (and nested ones), .cursorrules, .cursor/rules/*.mdc, .github/copilot-instructions.md, .github/instructions/*.instructions.md, GEMINI.md, .windsurfrules, CONVENTIONS.md. Keep only durable, specific, still-true facts. Drop anything the harness core block already covers.

### 3. Check scripts (the part that matters most)
Deterministic checks beat prose rules, so put effort here. Replace the stubs in `.agents/checks/` (delete the `ai-harness:stub` line) using the helpers in `.agents/lib/feedback.sh` (`agents_step`, `agents_lint`, `agents_worst_rc`):
- `edit.sh`: per-file, well under EDIT_BUDGET: formatter in check mode, fast single-file lint or syntax check on `"$@"`.
- `turn.sh`: incremental build, tests affected by the change (all tests if they're fast), lint on changed files. Well under TURN_BUDGET.
- `full.sh`: everything CI runs, including slow analyzers and sanitizers.
Print findings as `path:line[:col]: severity: message` so the output shaper can dedupe and cap them. Then run `.agents/bin/verify --tier=full --no-cache`. If it fails on the untouched tree, don't fix code: if the failures are lint findings, record them with `.agents/bin/verify --tier=full --update-baseline` so only new findings count; otherwise note them under Gotchas and in your report.

### 3b. Workflow packs
If `WORKFLOWS` in `.agents/harness.conf` lists any, fill their settings there from what the repo actually has (for `req-driven`: `REQ_SOURCE`, `REQ_ID_PATTERN`, `REQ_SCOPE`, `REQ_TESTS`; for `feature-driven`: `FDD_SCOPE`, plus `FDD_ID_PATTERN` and `FDD_DIR` only if the developer wants something else), and prove them with `.agents/bin/verify --tier=full --no-cache`. The seeded `.gitignore` only covers `.agents/fdd/`. If you move `FDD_DIR` anywhere else in the repo, add a `.gitignore` there with `*` and `!.gitignore` (in local mode, add the directory to `.git/info/exclude` above the harness block instead; verify reports `fdd-not-local` until you do), or use an absolute path outside the repo. Add one line to AGENTS.md naming the workflow and where it applies, e.g. "Changes under src/ are requirements-driven: use the `req-driven` skill." For `feature-driven`: "Changes under src/ are feature-driven: use the `feature-driven` skill." Don't create the model or feature list while tailoring; that's the skill's first step, with the developer.

### 3c. Git workflow
Propose `.agents/git.conf` from evidence, uncommenting only what this repo actually requires: the default branch (`git symbolic-ref refs/remotes/origin/HEAD`), branch naming (`git branch -r`), commit subjects (`git log --format=%s -50`), CONTRIBUTING, existing PR templates (point `GIT_PR_TEMPLATE` at one if it exists), and any protected branches you can see. If the repo already has a commit template (a local `commit.template`, a `.gitmessage`, or one described in CONTRIBUTING), point `GIT_COMMIT_TEMPLATE` at it, or convert it to `.agents/git/commit.md` with placeholders. Personal preferences belong in `~/.config/ai-harness/git.conf`, not here. Check the result with `.agents/bin/gitflow config` and name the settings you guessed in your report.

### 4. AGENTS.md
If `.agents/AGENTS.local.md` exists (`HARNESS_MODE="local"` in harness.conf and the project tracks its own AGENTS.md), put these facts there instead, below the harness's blocks; leave the tracked AGENTS.md alone.
Replace every `TODO(harness-tailor)` and remove the "Not tailored yet" note. Fill only what applies, delete empty sections:
- **Title and one or two lines** on what matters most (for example "hard real-time, no heap after init"). This steers judgment calls.
- **Commands**: only ones an agent would get wrong: running a single test, codegen, required setup. The standard checks live in `verify`.
- **Boundaries**: concrete paths for never-edit (vendored, generated, third_party) and ask-first (public APIs, schemas, crypto and key handling, anything safety-relevant).
- **Conventions**: only what tooling doesn't enforce.
- **Context docs**: rows for any `.agents/context/<topic>.md` you create, each with a specific trigger ("changing anything under src/net/").

### 5. Legacy files
- CLAUDE.md: reduce to the harness stub (first line `@AGENTS.md`), keeping genuinely Claude-only notes below it. In local mode, only if git doesn't track it; a tracked one stays as it is (the harness uses `CLAUDE.local.md`).
- Everything else: don't delete. List them in the report with a recommendation.

### 6. Review hardening (recommend, don't apply)
In local mode (`HARNESS_MODE="local"` in harness.conf), skip this: nothing the harness added is tracked, so there's nothing yet for CODEOWNERS to cover.
Otherwise, recommend the human add CODEOWNERS entries so agent-steering files need review:
`AGENTS.md CLAUDE.md .agents/ .claude/ .cursor/ .github/hooks/ .github/agents/ .codex/ .gemini/`.
Point out anything in `.agents/policy.conf` that looks wrong for this repo.

### 7. Finish
- `.agents/bin/sync`, then `.agents/bin/sync --check`.
- `grep -rn "TODO(harness-tailor)" AGENTS.md .agents/checks` returns nothing.
- Report: lines added to AGENTS.md (target ≤25), each tier's commands and whether they ran, pre-existing failures and what was baselined, legacy files to clean up, the CODEOWNERS suggestion (team mode only), in local mode every file you touched, your batched questions, and up to 3 project-specific skills worth writing (name and one line each, for `.agents/library/skills/`; don't create them).

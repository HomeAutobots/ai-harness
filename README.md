# ai-harness

A provider-agnostic harness for AI coding agents. Install it into any repo, have an agent tailor it to that repo, and upgrade every project from this one place.

It gives every agent the same three things, whatever tool it runs in: a short set of instructions, deterministic feedback on its work, and hard limits on what it can touch.

## Why it's built this way

The design follows what the evidence says actually moves agent results:

- **Deterministic feedback beats prose rules.** The strongest gains come from putting compiler, test, lint, and sanitizer output in the loop, and from gating on it. So the core of the harness is a feedback layer (`check`, `verify`, `guard`) that hooks run automatically, not paragraphs asking the agent to be careful.
- **Context costs more than it helps unless it's non-inferable.** Studies of AGENTS.md-style files found little or no success gain and a real token cost, and model-written ones hurt. The always-on rules are about 20 lines, the tailored part has a ~25 line budget, and everything else loads on demand.
- **Tool output is context too.** Feedback is quiet on success, deduplicated and capped on failure, and the full log stays on disk. No timestamps in agent-visible output, so it doesn't churn the prompt cache.
- **Harness choices move cost more than success.** Measure on your own history before trusting any of this. `eval` replays real fixes from git and compares arms.
- **One source of truth.** `AGENTS.md` and `.agents/skills/` are open standards most agents read natively. Adapters exist only where a tool still needs one.
- **Upgrades that don't clobber.** Every file is harness-owned (replaced on upgrade) or project-owned (created once, never touched). Re-running the installer is the upgrade.

## Tool support

| Tool | Instructions | Skills | Hooks: policy / edit / turn | Native deny rules |
|---|---|---|---|---|
| Claude Code | `CLAUDE.md` with `@AGENTS.md` | `.claude/skills` mirror | all three, plus questions, `.claude/settings.json` | yes, `permissions.deny` |
| GitHub Copilot (CLI, cloud agent, VS Code) | native | native | all three, plus questions, `.github/hooks/harness.json` | no, the hook enforces |
| Cursor | native | native | policy and turn; edit findings arrive at the stop gate; no question tool to hook | no, the hook enforces |
| OpenAI Codex | native | native | not yet | `.codex/rules/harness.rules` (opt in) |
| Gemini CLI | `.gemini/settings.json` (opt in) | native | not yet | no |

Notes:
- **Why keep CLAUDE.md?** Claude Code reads AGENTS.md on its own only when no CLAUDE.md exists, and not on Bedrock, Vertex, or Foundry. The one-line import works everywhere.
- **Copilot CLI also reads `.claude/settings.json` hooks**, so with both adapters on, some hooks fire twice there. `verify` caches by tree state, so the second run costs nothing.
- **Cursor ignores `afterFileEdit` output.** The edit check still runs (and warms the cache), but Cursor sees findings when the stop gate sends them back as a follow-up.
- **Hooks are guardrails, not a sandbox.** Timeouts fail open, some tool versions don't run hooks for subagents, and a determined agent can write a script that does what a blocked command would. For unattended runs, use a sandboxed devcontainer with an egress allowlist as well.

## Quick start

```sh
git clone <your-remote>/ai-harness.git ~/code/ai-harness
~/code/ai-harness/install.sh ~/code/my-project
~/code/ai-harness/install.sh --stack cpp-cmake ~/code/my-cpp-project   # with a stack pack
~/code/ai-harness/install.sh --workflow req-driven ~/code/my-project    # with a workflow pack
```

Installs are local by default: everything the harness adds stays out of git in your clone, and files the project already tracks are never touched. Add `--team` to commit the harness instead, so the whole team and CI see it.

Then open the project in any agent and say:

> Use the harness-tailor skill to tailor the AI harness for this repo.

It proposes: AGENTS.md facts, the three tier scripts, and a baseline of existing lint findings. Review what it proposes; in team mode, commit the diff.

### Team mode

`install.sh --team <project-dir>` puts the harness under git, so the whole team and CI see the same thing once you commit it. Review the tailoring proposal (AGENTS.md, .agents/checks/, CLAUDE.md), then commit. In CI:

```sh
.agents/bin/sync --check && .agents/bin/verify --tier=full
```

Add CODEOWNERS for `AGENTS.md CLAUDE.md .agents/ .claude/ .cursor/ .github/hooks/ .github/agents/ .codex/ .gemini/`, so changes to what steers agents get reviewed.

Team mode commits what `sync` renders into `.agents/skills/` and `.claude/skills/`, except skills from your personal library: those render for your clone only, listed in the same marked block in `.git/info/exclude` that local mode uses (team mode writes it only when you have some, and removes it when you have none left). See [Libraries](#libraries).

### Local mode

Local (the default) puts the harness's files in the project as usual, but `sync` also writes a marked block to `.git/info/exclude` (works with worktrees and submodules) listing every harness path git doesn't track, so none of it shows up in `git status` or gets committed. That's everything the harness created, plus an untracked `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md`, or `.claude/settings.local.json` that was already there, since the harness writes into those (sync warns once about an `AGENTS.md` like that). Claude Code hooks and deny rules go in `.claude/settings.local.json` instead of the shared `.claude/settings.json`. If the project already tracks `AGENTS.md` or `CLAUDE.md`, those files are never touched: the harness's managed blocks go to `.agents/AGENTS.local.md` when `AGENTS.md` is tracked, and a personal `CLAUDE.local.md` takes over when `CLAUDE.md` is tracked, both excluded the same way. Copilot, Cursor, and Codex read only `AGENTS.md`, so with a tracked one they miss the harness's rules in this clone. Copilot and Cursor hooks still enforce checks and policy; Codex has no hooks yet, so only its native rule file (command blocking, not checks) applies. `verify`'s turn and full tiers add a `harness-tracked` finding, with the exact fix, if any of this gets committed by accident.

Those files are ignored by git in this clone. Most git commands leave them alone, a few don't:
- `git stash` and `git stash -u` are safe: they stash only the project's changes, and the harness keeps working while you're stashed.
- `git stash -a` (`--all`) stashes the harness too, so it's gone until you pop. Run `git stash pop` to get it back; don't re-run `install.sh` first, or the pop fails on the files it just recreated. `git clean -fdX` (or `-fdx`) deletes it (re-run `install.sh` afterwards): all of `.agents/`, including the project-owned parts (`policy.conf`, `checks/`, `context/`, `plans/`), and your tailoring in `AGENTS.md` or `.agents/AGENTS.local.md` (recoverable from the backup below). The default policy blocks agents from both.
- A checkout or pull that brings a tracked file at one of these paths (say a teammate commits an `AGENTS.md`) overwrites your local copy without asking.

To recover from a `git clean` or a checkout, `sync` keeps a backup inside the git dir, which git clean, stashes, and checkouts never touch. It lives in each worktree's own git dir, at `$(git rev-parse --git-path ai-harness)/backup` (`.git/ai-harness/backup/` in a plain clone); a subdirectory install uses `backup-<prefix>` instead, with `/` in the prefix turned into `_`. It leaves out `.agents/builtin/`, which `install.sh` rebuilds. It's refreshed on every `sync` and whenever `verify` runs on the turn or full tier; edits since then aren't in it. If `.agents/` goes missing, re-run `install.sh`: it restores your files from the backup instead of seeding blank ones. If a tracked `AGENTS.md` replaces your local one, the next `sync` saves your last local copy as `AGENTS.md.before-tracked` in the backup and tells you once. Switching to team mode deletes the backup, except a saved `AGENTS.md.before-tracked`, which `sync` keeps and points you to.

A new clone or worktree starts with none of this, so each one needs its own `install.sh` run. All worktrees of a clone share one `.git/info/exclude`, and so one harness block: install each worktree in the same mode. Switch a project with `install.sh --local` or `install.sh --team`; each tells you what changed and what to commit. Once a switch to local is committed, other clones lose `.agents/` on their next pull, so each developer re-runs `install.sh` (local by default) to get it back.

## The feedback loop

Three tiers, each a project-owned script in `.agents/checks/`, all run through one orchestrator:

| Tier | Runs | Typical content | Budget |
|---|---|---|---|
| edit | after every file edit (hook), `check <files>` | formatter check, per-file lint, syntax check | `EDIT_BUDGET` (15s) |
| turn | when the agent finishes a turn that changed files (stop hook), `verify` | incremental build, affected tests, lint on changed code | `TURN_BUDGET` (300s) |
| full | commit gate, CI, `verify --tier=full` | everything CI runs, sanitizers, slow analyzers | none |

- **Output contract.** `ok verify turn` on success. On failure: `path:line` findings (deduplicated, max 5 per file, 30 total), test failure markers, sanitizer reports trimmed to repo frames, and `full log: <path>`.
- **Exit codes.** 0 ok, 1 findings, 2 policy block, 3 tooling problem, 124 out of budget. Hooks block only on 1 and 2; anything else fails open with a note.
- **Stop gate.** Blocks at most `TURN_MAX_BLOCKS` (3) times per turn, then hands back to you instead of looping. Turns that didn't change the tree aren't gated.
- **Caching.** Results are keyed by tree state. Repeat calls with no changes return instantly.
- **Baselines.** `verify --tier=full --update-baseline` records current findings so only new ones count. Tier scripts opt in with `agents_lint <name> <cmd>`.

## Guard and policy

**Guard** scans only lines added in the working tree and blocks the ways to get green without fixing anything: new suppressions (NOLINT, cppcheck-suppress, pragma ignores, `-Wno-`, noqa, eslint-disable, ts-ignore, and friends), skipped or disabled tests, `.only`, deleted test files, and removed test cases. A human can approve an exception with `guard allow <file-glob> <text> <reason>`; the policy blocks agents from running that.

**Policy** lives in `.agents/policy.conf`: `deny-cmd` (command prefix, checked per segment of chains and pipelines, including inside `bash -c`), `deny-arg`, `deny-regex`, `deny-read` and `allow-read` (paths, also when named in a shell command). Defaults block `reset --hard`, `clean -f`, `git clean -x`/`-X`, `git stash -a`, history rewrites, `--no-verify`, piping downloads into a shell, `sudo`, and reads of `.env` files, key material, and credential directories. Git workflow rules (pushes, PRs, merges, branch and commit formats) live in `.agents/git.conf`.

## Validation gates

The built-in `validate` skill checks work at three gates: the plan before any tests or code, the tests before implementation, and the implementation before hand-back. Each gate runs the deterministic checks first, judges what they can't, and returns PASS, REVISE (specific findings back to the producer, at most two rounds), or ASK (questions only the human can answer). Gates listed in `VALIDATE_ASK` always end with a check-in with you.

Questions and answers live in a **question ledger** per plan (`.agents/plans/<slug>/questions.json`, or `_general/` outside a plan):

- `tasks ask` records a question (with its gate) and blocks the task (a done task stays done while it waits for your sign-off). It refuses a question the ledger already answered, so you don't get asked the same thing twice across sessions or tools.
- `tasks answer` records your answer, adds it to the plan's Decisions, and resumes the task once nothing is open.
- `tasks questions <words>` searches every question and answer across all plans.
- A task waiting on you pauses the stop gate, which matters at the test gate, where tests are deliberately red.

With the `questions` hook feature on (default), the hooks keep the ledger honest without relying on the agent to remember: before the agent asks through its question tool (AskUserQuestion in Claude Code, ask_user in Copilot), an earlier answer to the same question is shown to it instead, once per session; after you answer, the question and answer are recorded; and a new session starts with a reminder of anything still waiting on you. When roles land, `validate` becomes the validator role's instructions; until then, run it in a subagent with a fresh context where your tool supports one.

## Git workflow

Every repo's git workflow is described in config and carried out by one CLI, `.agents/bin/gitflow`, so it's generic in the harness and tailored per project:

- **Settings layer**, last wins: harness defaults, then your personal `~/.config/ai-harness/git.conf` (or `$AGENTS_PERSONAL_DIR/git.conf`; your workflow follows you across repos), then the project's `.agents/git.conf`. `gitflow config` shows the result.
- **Templates do double duty.** `GIT_BRANCH="{type}/{ticket}-{slug}"` and `GIT_COMMIT="{ticket}: {summary}"` both build names and messages (`gitflow start`, `gitflow commit`) and validate them. Extra rules go in `GIT_COMMIT_PATTERN`, required trailers in `GIT_COMMIT_TRAILERS`.
- **Commit templates.** A project can define the whole message in one file (`GIT_COMMIT_TEMPLATE`, or the repo's own `commit.template`): subject, sections like `Why:` and `Testing:`, and trailers. Sections are required unless marked `(optional)`. Humans get the editor prefilled (ticket and trailers already in), agents fill sections with `gitflow commit --section Why=...`, `gitflow template` shows what's expected, and the `commit-msg` hook checks everyone's messages against it.
- **The steps:** `start`, `commit`, `update` (merge or rebase the base in), `check`, `push` (runs a verify tier first), `pr` (gh, glab, or printed text; base forced to `GIT_BASE`; body from `.agents/git/pr.md`), `review`, `merge`.
- **Who does what:** `GIT_AGENT_MAY` lists the steps agents may take (default `branch commit`); the rest are yours.
- **Protected branches are the project's call.** Nothing is protected by default, which suits solo and trunk-based repos. Set any names or globs, such as `GIT_PROTECTED="dev/main release/*"`; `{base}` stands for whatever `GIT_BASE` is.
- **Plans link to branches only if you want.** `tasks link <slug>` records a plan's branch so `gitflow pr` can include it. Plans work without branches, and branches without plans.
- **Enforcement:**
  - Local git hooks (`commit-msg`, `pre-push`, and `prepare-commit-msg` when there's a commit template) apply to you and to every tool: message format, trailers, protected branches, force pushes. They're installed only once git.conf, or an installed workflow pack, has something for them to enforce (`sync` adds them when it does). An existing `core.hooksPath` (husky, pre-commit) is left alone, with the lines to add.
  - The policy hook checks agent commands before they run: protected pushes, force pushes, branch names, PR base, rebase vs merge, steps outside `GIT_AGENT_MAY`, and approving PRs, which agents never do.
  - When the project's git.conf sets `GIT_AGENT_MAY` explicitly, the forbidden steps are also rendered as native deny rules (Claude Code, Codex). Personal settings never land in committed files, so CI and every developer render the same thing.

**Safety is universal; process is opt-in.** Safety rules (no `reset --hard`, no `--no-verify`, no secrets, no force-pushing shared work) live in policy.conf and hold regardless of flow. Process rules (protected branches, branch and commit formats, trailers, dev workflows) apply only when configured. With nothing configured, each is a no-op, so a repo with no flow, or a developer who ignores the flow, still works. The smoke suite has "absence" tests that hold this in place: a zero-config trunk repo, gitflow with no plan or ticket, a plan with no workflow, and a workflow with no git config.

The `git-workflow` skill covers the judgment: commit granularity, PR descriptions worth reading, and handling every review comment (fix it, explain it, or ask the human). A `deny-cmd` in policy.conf always wins, if a project wants to forbid agent pushes outright.

## Plans that survive sessions and tools

`plan-task` keeps a ledger per piece of work in `.agents/plans/<slug>/`: `plan.md` for intent, questions, and decisions, `tasks.json` for steps, `progress.log` for session notes. Agents edit it only through `.agents/bin/tasks` (`new`, `add`, `next`, `set`, `ask`, `answer`, `log`), so it stays valid JSON. Any agent in any tool resumes with `tasks next <slug>` and `git log`.

## Evals

```sh
.agents/bin/eval new tls-expiry <fix-commit>     # then edit PROMPT and CHECK
.agents/bin/eval run --arms=A,B,C --runs=3
.agents/bin/eval report
```

Each task starts the agent at the commit before a real fix; success means that fix's own tests pass. Arm A has no harness, B has the harness with hooks off, C is the full harness. Token and turn numbers come from Claude Code's JSON output. The decision rule: adopt a change only if success doesn't drop, tokens per success stay within 1.1x, and wall time within 1.25x. Run evals in a disposable environment; the agent runs unattended.

## Libraries

Your own skills, workflows, and stacks live in libraries: plain directories shaped like the harness's own content. The harness looks them up by name on every run, so you write something once, every project picks it up, and upgrades never touch it.

```
<library>/
  skills/<name>/SKILL.md     a skill (Agent Skills format)
  workflows/<name>/          a workflow pack (see Workflow packs)
  stacks/<name>/             a stack pack (see Stack packs)
```

Search order; the first library with a name wins:

| # | Library | Where | Owner |
|---|---|---|---|
| 1 | Project | `.agents/library/` | the project; upgrades never touch it |
| 2 | Project-listed | `LIBRARIES` in `.agents/harness.conf`: paths relative to the project root (a submodule, say), `~/...`, or absolute | the team |
| 3 | Personal | `~/.config/ai-harness/` (`$XDG_CONFIG_HOME/ai-harness/`; `AGENTS_PERSONAL_DIR` overrides it for the library, its `harness.conf`, and `git.conf`), if it exists | you |
| 4 | Personal-listed | `LIBRARIES` in `~/.config/ai-harness/harness.conf`: absolute, `~/...`, or relative to that dir | you, or a team repo you clone |
| 5 | Built-ins | `.agents/builtin/` | the harness; replaced on upgrade |

- **Skills are always on.** `sync` renders every resolved skill into `.agents/skills/` (Copilot, Cursor, Codex, and Gemini read it natively) and mirrors it into `.claude/skills/`, and lists them in AGENTS.md's skills index. `.agents/skills/` is output now: a skill folder or link you put there by hand gets moved into `.agents/library/skills/`, with a notice. The one exception: in local mode, a skill the project itself tracks in `.agents/skills/` stays where it is.
- **Workflows and stacks are opt-in per project**, by name in `WORKFLOWS` / `STACKS` (or `install.sh --workflow <name>` / `--stack <name>`). They run in place from their library, so an edit there applies on the next `verify`. An active workflow's `skill/` renders as a skill named after the workflow, unless a library has a skill of that name. The skill comes from the pack that wins, the one whose checks run: if yours replaces a built-in pack and has no `skill/`, there's no skill, not the built-in one. `sync` warns about a listed name no library has: `verify` and `gitflow` skip a missing workflow, and tier scripts that source a missing stack's `.agents/stacks/<name>/lib.sh` stop with a tooling problem (exit 3). The exception is a missing project-listed library (below): then a name might be in it, so it's a tooling problem, not a skip.
- **Same name in two libraries:** the higher one wins and `sync` warns, naming both (for skills always, for workflows and stacks while they're active). That's how you replace a built-in; edits inside `.agents/builtin/` don't survive an upgrade.
- **Links or copies.** Each rendered skill links to its library (a relative link inside the repo, an absolute one outside it), so edits are live. With `LINK_MODE="copy"` it's a copy instead, refreshed by `sync`. Team mode commits the links for skills inside the repo and always copies a shared skill from a library outside it, since a link out of the repo can't be committed.
- **Personal stays personal in team mode.** Your personal skills render for your clone only (listed in the harness block in `.git/info/exclude`) and stay out of AGENTS.md's committed skills index. If one has the same name as a shared skill (project, project-listed, or built-in), team mode renders the shared one so the repo is the same for everyone, and `sync` says yours isn't used. `sync --lock-skill` pins the shared copy and refuses a personal-only skill. A personal workflow can be listed in a team repo's `WORKFLOWS`; teammates and CI without it skip its checks, and `sync` notes that. If yours replaces a built-in pack, its checks run in your clone, but team mode renders the built-in pack's skill (that's the committed one teammates and CI get, along with the built-in checks), and `sync` says so.
- **Only real items count.** A skill needs `SKILL.md`; a workflow needs at least one of `checks/` holding a file, `skill/SKILL.md`, `agents/`, `mcp/`, `bin/`, a non-empty `harness.conf.snippet` or `policy.conf.snippet`, or `seed/` holding a file (so a policy-only pack works); a stack needs `lib.sh` or `checks/` holding a file. Anything else with a name (an empty `~/.config/ai-harness/workflows/feature-driven/`, say) is ignored, so it can't shadow the working one further down, and `sync` warns: `<path> isn't a usable workflow (no checks/ with a file, skill/SKILL.md, agents/, mcp/, bin/, a snippet, or seed/ with a file); ignored`.
- **A project-listed library that isn't here** (missing, or an empty directory, which is what an uninitialized submodule looks like) is a tooling problem, not "no such library". `sync` keeps the renders that may have come from it instead of removing them as stale or pointing them at a built-in or a library listed after it, so nobody commits that change, and warns: `LIBRARIES lists vendor/team, which isn't here (an uninitialized submodule?); its skills keep their committed renders until it's back`. `sync --check` fails with `infra: LIBRARIES lists vendor/team, which isn't here`, so CI notices. While it's missing, an active workflow or stack that doesn't resolve might be in it: `verify` reports `infra: workflow '<name>' isn't available: LIBRARIES lists vendor/team, which isn't here` and exits 3, and `gitflow` treats that workflow's commit-message check the same way (exit 3, once per run in `gitflow check` and pre-push), so commits are blocked (the fail-closed rule for commit-message checks) until the library is checked out.
- **Nothing configured, nothing changes.** No `LIBRARIES` means none; no personal dir means none. A personal-listed directory that doesn't exist is skipped, and `sync` warns about it; teammates never have your personal libraries, so `verify` and `gitflow` stay quiet about them.
- **Agents render per tool.** `agents/<name>.md` in a library (yours, project-listed, personal, or an active workflow pack's own `agents/`) is a neutral agent: name, description, tools, a model tier, and effort, rendered by `sync` into each enabled adapter's own format (`.claude/agents`, `.github/agents`, `.cursor/agents`, `.codex/agents`, `.gemini/agents`). Write it once; every tool gets its own file.

  ```markdown
  ---
  name: reviewer
  description: Reviews a diff for correctness bugs. Use after implementing a change.
  tools: [read, search, shell]
  model: strong
  ---
  Body: the prompt.
  ```

  Model tiers and effort map per tool through `MODEL_<TIER>_<TOOL>` / `EFFORT_<TIER>_<TOOL>` in `.agents/harness.conf` (missing: inherit). Personal agents never land in a shared repo, same as personal skills. See `.agents/library/README.md` for the full format, including `native:` lines for a tool's own fields and the escalation warning.
- **Pack commands.** An active workflow pack's `bin/<name>` commands get a stable wrapper at `.agents/commands/<name>` that runs the pack from wherever its library resolves, so the path is the same on every machine (e.g. `.agents/commands/fdd` for feature-driven).

Example: a personal skill and workflow for every project:

```
~/.config/ai-harness/
  skills/sql-style/SKILL.md
  workflows/my-review/checks/turn.sh
  workflows/my-review/skill/SKILL.md
```

`.agents/bin/sync` in any project picks up `sql-style`. `install.sh --workflow my-review <project>` (or adding `my-review` to `WORKFLOWS`) turns the workflow on there.

`bash .agents/lib/libraries.sh resolve skills` shows what each name resolves to and from which library (`libraries` lists the search path, `shadows <kind>` the losers; `python3 .agents/lib/harness.py resolve <kind> [name]` does the same). Names are letters, digits, `.`, `_`, and `-`, not starting with `.` or `_`. `LIBRARIES` is space-separated, so library paths can't contain spaces. The resolver parses config without running it (git hooks use it), and your personal `harness.conf` is never run.

Pack scripts run from wherever the pack lives. Find the pack's own files from the script's path (`"$(dirname "$0")/../tool.py"`), never `.agents/workflows/<name>/`, and the project from `$AGENTS_ROOT`. `verify` and `gitflow` run pack checks with `bash`, so they don't need the exec bit. `verify`'s cache covers every file of each active pack wherever it lives, so an edit in your personal library counts. `sync` scans the skills that render and the active packs for invisible Unicode wherever they live (`.md`, `.conf`, `.json`, `.sh`, `.py`, and `.snippet` files), not only files in the repo.

## Stack packs

`install.sh --stack <name>` adds the stack to `STACKS` and seeds the tier scripts if they're still stubs. Tailored tier scripts are never replaced. The pack runs from its library (shipped ones from `.agents/builtin/stacks/<name>`); `.agents/stacks/<name>/lib.sh` is a small harness-owned shim that tier scripts source, which loads the pack from wherever it resolves.

- **cpp-cmake**: agent-owned build trees, syntax-only compiles with each file's real compile command, new warnings in changed files, clang-tidy on changed lines only, affected-test selection through the CMake file API, an ASan+UBSan tier, and cppcheck with baselines. See `stacks/cpp-cmake/README.md`.

## Workflow packs

Stack packs answer "how do we build and test this language." Workflow packs answer "in what order, and with what evidence, do we change things." They're independent, so a project can combine `cpp-cmake` with `req-driven`.

`install.sh --workflow <name>` adds the pack to `WORKFLOWS` and appends its settings to `.agents/harness.conf` once. Nothing is copied: the pack runs in place from its library (see [Libraries](#libraries)). `verify` runs its `checks/<tier>.sh` for each tier after the project's own tier scripts, so nothing in `.agents/checks/` has to change; `gitflow` runs its commit-message check; `sync` renders its `skill/` as a skill named after the pack.

A pack can also ship:
- `policy.conf.snippet`: rules appended to `.agents/policy.conf` once. Its first line is a marker comment; while the marker is there, reinstalls add nothing, so a rule you delete stays deleted.
- `seed/`: files copied into the project once and never overwritten (project-owned), e.g. `seed/.agents/<name>/.gitignore` to keep the pack's working files local. Harness-owned paths are skipped.
- `checks/commit-msg.sh <file>`: extra commit-message rules. `gitflow` runs it everywhere it checks messages (the `commit-msg` hook, `gitflow commit`, `check`, and pre-push). Exit 1 or 2 rejects the commit with its output. Any other failure (a missing tool, a crash) also blocks, since an unchecked message could carry what the check exists to stop, and so does an active workflow that doesn't resolve while a project-listed library isn't here (exit 3); a human can bypass with `git commit --no-verify`, which the policy denies to agents. A pack that prefers to let commits through handles its own missing tools and exits 0. It sees every message, including merge, fixup, and revert messages that the git.conf rules skip. Shipping one is enough for the git hooks to be installed.
- `bin/`: commands for people. Shipped packs' commands are made executable on install; in a library of your own, set the bit yourself.
- `checks/state.sh`: prints whatever the pack's checks read that git ignores (local working files), so `verify`'s cache notices when it changes. It runs on every `verify` call, since it feeds the cache key, so keep it fast and side-effect free. Plan ledgers are always included.

- **req-driven**: every change starts from a requirement ID in any exported requirements source (CSV, JSON, Markdown, text). Deterministic checks: IDs must exist, new tests must name the requirement they verify, changes in scope must reference one (in code, tests, or the plan task in progress), and the full tier writes a requirement to code to tests trace with optional untested-requirement gating. Standard-agnostic. See `workflows/req-driven/README.md`.
  The skill's phases (pin the requirement, tests first, implement, report) each end at a `validate` gate, so they map onto planner, tester, implementer, and validator roles when roles land.
- **feature-driven**: classic FDD for one developer. The agent drafts a domain model and feature list, then plans, designs, and builds one feature at a time; you approve the list, each design, and each finished feature with `fdd approve` (`.agents/commands/fdd`), which agents can't run. Checks: design before build, a task in progress for every in-scope change, no private feature IDs in shared code or commit messages, and a parking-lot progress report. All FDD files stay local. See `workflows/feature-driven/README.md`.

## Integrity

`sync --check` fails when:
- a managed block or adapter config drifted from what sync would render,
- a pinned third-party skill changed (`sync --lock-skill <name> <source> <ref>` pins by SHA-256 content hash),
- any instruction or config file an agent reads contains invisible Unicode (zero-width, bidi controls, tag characters), including the skills that render and the active packs in libraries outside the repo (`.md`, `.conf`, `.json`, `.sh`, `.py`, `.snippet` files there).

## What lands in a project

```
my-project/
├── AGENTS.md                    project-owned, except the two harness:* blocks
├── CLAUDE.md                    @AGENTS.md stub                          (claude)
├── .claude/settings.json        hooks + deny rules merged in; settings.local.json in local mode (claude)
├── .claude/skills/*             mirrors of .agents/skills/*              (claude)
├── .github/hooks/harness.json   hooks                                    (copilot)
├── .cursor/hooks.json           hooks merged in                          (cursor)
└── .agents/
    ├── core/                    harness: core rules, guard patterns
    ├── bin/                     harness: sync, verify, check, guard, tasks, eval
    ├── lib/, hooks/             harness: shell library, renderer, hook adapter
    ├── builtin/                 harness: built-in skills, workflow and stack packs (a library)
    ├── library/                 project: your skills, workflows, stacks (a library)
    ├── skills/                  sync: every resolved skill, rendered (links or copies)
    ├── stacks/<name>/lib.sh     harness: shims the tier scripts source
    ├── checks/{edit,turn,full}.sh   project: what each tier runs
    ├── harness.conf, policy.conf    project: adapters, hooks, budgets; limits
    ├── git.conf, git/               project: git workflow, PR template, commit template
    ├── guard.allow, baselines/      project: approved exceptions, known findings
    ├── context/, evals/             project: on-demand docs, eval tasks
    ├── plans/                   project: ledgers (gitignored by default)
    ├── generated.lock           sync: what it added to shared config files
    └── cache/                   local: logs, verify cache, hook state (gitignored)
```

Merged config files keep everything that isn't the harness's. Harness entries are recognized by their `.agents/hooks/` command path, and deny rules by `generated.lock`, so a re-render replaces exactly what the harness added.

## Upgrading projects

Change the harness here, bump `VERSION`, add a CHANGELOG entry, then per project:

```sh
~/code/ai-harness/install.sh ~/code/my-project
```

## Requirements

- bash 3.2+ (stock macOS works), git, POSIX tools (any awk: tested with gawk, mawk, one-true-awk).
- python3 3.8+ for hooks, JSON config rendering, evals, the cpp-cmake helpers, and workflow packs' checks. Without it, sync warns and hooks step aside rather than wedging the agent.

## Windows

Symlinked skills need Developer Mode plus `git config core.symlinks true`. Otherwise set `LINK_MODE="copy"` in `.agents/harness.conf`. Hooks need Git Bash on PATH.

## Working on the harness

Never install the harness into this repo; try changes in a scratch repo under /tmp instead.

```sh
bash tests/lint.sh       # seconds: syntax, shellcheck, portability, ownership lists, budgets, CODEOWNERS list, docs voice
bash tests/smoke.sh      # a few minutes, ~800 checks; the C++ section runs when cmake and a compiler exist
bash tests/all.sh        # lint, then smoke under every awk on the machine (the release gate)
bash scripts/package.sh  # dist/ai-harness-<version>.zip plus its SHA-256
```

CI (`.github/workflows/ci.yml`) runs lint and `tests/all.sh` on Ubuntu and on macOS with Apple's bash 3.2.

Keep `template/.agents/core/AGENTS.core.md` tight. Every line there loads in every session of every project, and lint fails past 25 lines.

## Known gaps

- Codex and Gemini CLI hooks aren't rendered yet; their formats need verifying first. Codex execpolicy rule syntax is also unverified against a live Codex.
- Copilot and Cursor have no documented personal hook location, so a tracked `.github/hooks/harness.json` or `.cursor/hooks.json` turns that adapter off in local mode (sync warns and leaves the tracked file alone); the Copilot cloud agent, which works from the remote repo, gets nothing in local mode either.
- Local mode's files are ignored by git in the clone, so `git clean -fdX` / `-fdx` and `git stash -a` take them away (`git stash -u` is fine), and a checkout that brings a tracked file at one of those paths overwrites the local copy. The backup in the git dir is refreshed on every `sync` and whenever `verify` runs on the turn or full tier; edits since then aren't in it. See [Local mode](#local-mode).
- Two installs in one repo (two subdirectories, local or team with personal skills) share one exclude block, and each `sync` rewrites it with only its own paths. The cpp-cmake stack's `/build-agent*/` lines are anchored at the repo top, not at a subdirectory install.
- Switching to local doesn't unshare a tracked `.gemini/settings.json`; its context entries stay, and local mode then leaves that tracked file alone.
- Without python3, `install.sh --local` can't strip or untrack tracked JSON configs (`.claude/settings.json`, `.cursor/hooks.json`, `.github/hooks/harness.json`, `.codex/rules/harness.rules`); only `AGENTS.md` and `CLAUDE.md` are handled without it.
- Hooks were tested with recorded payload shapes, not yet inside live Claude Code, Copilot, and Cursor sessions. Watch `.agents/cache/hook-events.log` on first use. The question-tool payloads (AskUserQuestion input and answers, Copilot ask_user) are the least certain; the capture falls back to recording the raw answer text.
- `gitflow` was tested against a local bare remote and a stand-in `gh`, not live GitHub, GitLab, or Jira. `gitflow review` lists all PR comments (inline ones as `path:line`), not only unresolved threads.
- Duplicate-question detection is word overlap with light stemming, not semantics. It catches rewordings of the same question; it can miss a paraphrase and, rarely, flag two different questions that share most words (`--force` overrides).
- No sandbox profile ships with the harness. Pair it with a devcontainer that allowlists egress for unattended runs.
- Policy rules block commands and reads, not writes. An agent can't run `guard allow` or `fdd approve`, but it could edit `.agents/guard.allow` or the local FDD `approvals` file directly. Command rules also match patterns, not intent: an agent that writes its own script to do the same thing is outside them.
- Tools that read `.agents/skills/` natively are assumed to follow the symlinks `sync` renders there, as Claude Code does in `.claude/skills/`. If one doesn't, set `LINK_MODE="copy"`.
- While a project-listed library isn't here, `sync` can't tell which marked copies came from it (a copy doesn't record its source), so with `LINK_MODE="copy"` or a missing library outside the repo it leaves every marked copy alone until the library is back. An active pack that resolves to a lower library in the meantime (a built-in the missing one would have shadowed) runs without a message.
- In team mode, personal skills aren't in AGENTS.md's skills index (a committed file); tools that only read the index don't see them.
- In team mode, an active workflow that's both built in and in your personal library renders the built-in skill, but `verify` and `gitflow` run the personal pack's checks, since runtime lookup keeps the plain search order. `sync` says so in one message ("your personal workflow 'x' runs its checks here, but team mode renders the skill from the builtin library").
- `eval`'s harness arms (B and C) still see your personal library, so personal skills and workflows can skew results between developers. Point `AGENTS_PERSONAL_DIR` at an empty directory for a clean run.
- Library paths can't contain spaces (`LIBRARIES` is space-separated).
- Skill renders are recognized as links into a library or marked copies in `.agents/skills/`, not by an entry in `.agents/generated.lock` the way agent renders are.
- feature-driven checks don't see every place a private feature ID can reach shared history: branch summaries (`gitflow start PROJ-123 <summary>`), plan titles that go into PR bodies, and code committed before a turn gate ran (`fdd-leak` only reads uncommitted changes).
- VS Code reads both `.claude/agents` and `.github/agents`; with the claude and copilot adapters on, it may list an agent twice.
- Codex loads project `.codex/` config only in a trusted project, and openai/codex#14579 reports project agents may not be callable by name.
- Copilot's cloud agent sees only committed agents; personal agents and local mode don't reach it.
- Cursor and Codex can limit an agent only to read-only; Codex effort values past high and Cursor's `[effort=...]` values beyond high aren't documented.
- Per-agent MCP servers: Claude takes server names; Codex and Cursor can't limit them (phase 3 renders MCP servers themselves).

## Roadmap

- **0.3** Workflow packs (req-driven first, in progress), Codex and Gemini hooks once verified, more stack packs (Python, TypeScript).
- **0.4** Roles: planner, tester, implementer, and validator (plus a read-only explorer), defined once and rendered per tool, with one writer at a time and the validator gating each phase.
- **0.5** MCP config rendered from one `.agents/mcp.json`, off by default.

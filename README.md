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
| Claude Code | `CLAUDE.md` with `@AGENTS.md` | `.claude/skills` mirror | all three (commands from `Bash`, `PowerShell`, `Monitor`), plus questions, `.claude/settings.json` | yes, `permissions.deny` |
| GitHub Copilot (CLI, cloud agent, VS Code) | native | native | all three, plus questions, `.github/hooks/harness.json` | no, the hook enforces |
| Cursor | native | native | policy and turn; edit findings arrive at the stop gate; no question tool to hook | no, the hook enforces |
| OpenAI Codex | native | native | all three (edits through `apply_patch`), `.codex/hooks.json` (opt in); no question tool to hook | `.codex/rules/harness.rules` (opt in) |
| Gemini CLI | `.gemini/settings.json` (opt in) | native | all three (shell, `read_file`, `read_many_files`, `grep_search`, `glob`, `write_file`, `replace`), `hooks` in `.gemini/settings.json` (opt in); no question tool to hook | no, the hook enforces |

Notes:
- **Why keep CLAUDE.md?** Claude Code reads AGENTS.md on its own only when no CLAUDE.md exists, and not on Bedrock, Vertex, or Foundry. The one-line import works everywhere.
- **Copilot CLI also reads `.claude/settings.json` hooks**, so with both adapters on, some hooks fire twice there. `verify` caches by tree state, so the second run costs nothing.
- **Codex and Gemini CLI hooks are opt in.** Add `codex` or `gemini` to `ADAPTERS` in `.agents/harness.conf` and run `sync`. Both tools load project hooks only in a trusted project or folder, and have you review them first (Codex: `/hooks`). Codex blocks through its JSON deny form, gets edit findings back after `apply_patch`, and is told to keep working by the stop gate; Gemini gets the same through `decision` and `additionalContext`. `HOOKS` picks which ones render, as for the other tools.
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

Add CODEOWNERS for `AGENTS.md CLAUDE.md .mcp.json .agents/ .claude/ .cursor/ .github/hooks/ .github/agents/ .codex/ .gemini/`, so changes to what steers agents get reviewed.

Team mode commits what `sync` renders into `.agents/skills/` and `.claude/skills/`, except skills from your personal library: those render for your clone only, listed in a marked block in `.git/info/exclude` like local mode's (team mode writes it only when you have some, and removes it when you have none left). See [Libraries](#libraries).

### Local mode

Local (the default) puts the harness's files in the project as usual, but `sync` also writes a marked block to `.git/info/exclude` (works with worktrees and submodules) listing every harness path git doesn't track, so none of it shows up in `git status` or gets committed. That's everything the harness created, plus an untracked `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md`, or `.claude/settings.local.json` that was already there, since the harness writes into those (sync warns once about an `AGENTS.md` like that). Claude Code hooks and deny rules go in `.claude/settings.local.json` instead of the shared `.claude/settings.json`. If the project already tracks `AGENTS.md` or `CLAUDE.md`, those files are never touched: the harness's managed blocks go to `.agents/AGENTS.local.md` when `AGENTS.md` is tracked, and a personal `CLAUDE.local.md` takes over when `CLAUDE.md` is tracked, both excluded the same way. Claude Code imports that file; the other tools don't read it, so `sync` gives each enabled one the rules in a file it reads on its own, untracked and excluded too:

| Tool | File | How the tool reads it |
|---|---|---|
| Copilot | `.github/instructions/ai-harness.instructions.md` | `applyTo: "**"`, alongside `AGENTS.md` |
| Cursor | `.cursor/rules/ai-harness.mdc` | `alwaysApply: true`, alongside `AGENTS.md` |
| Codex | `AGENTS.override.md` | in place of `AGENTS.md`, so it holds `AGENTS.md` as tracked, then the harness's file |
| Gemini CLI | `GEMINI.md` | alongside `AGENTS.md` (sync lists both in `context.fileName`); imports `.agents/AGENTS.local.md` |

Each holds a `<!-- ai-harness: generated by .agents/bin/sync ... -->` marker line (after the frontmatter for Copilot and Cursor). A file at one of those paths without it, one the project tracks, or a path through a symlinked folder (a shared `.cursor/rules`, say) isn't touched, and `sync` warns on every run that that tool won't see the harness rules; turning that adapter off is the only way to quiet it. They go away in team mode, when `AGENTS.md` stops being tracked, or when the adapter is turned off; Gemini's also goes when a tracked `.gemini/settings.json` sets `context.fileName` without `GEMINI.md`. They're copies, so they go stale when `AGENTS.md` changes (a pull) or you edit `.agents/AGENTS.local.md`: `verify`'s turn and full tiers report a stale one as `harness-stale`, `sync` rewrites it, and Codex's session-start hook (on with `questions` in `HOOKS`) tells it to follow `AGENTS.md` and `.agents/AGENTS.local.md` until then. `verify`'s turn and full tiers add a `harness-tracked` finding, with the exact fix, if any of this gets committed by accident, and `sync` in team mode warns about a committed copy.

Those files are ignored by git in this clone. Most git commands leave them alone, a few don't:
- `git stash` and `git stash -u` are safe: they stash only the project's changes, and the harness keeps working while you're stashed.
- `git stash -a` (`--all`) stashes the harness too, so it's gone until you pop. Run `git stash pop` to get it back; don't re-run `install.sh` first, or the pop fails on the files it just recreated. `git clean -fdX` (or `-fdx`) deletes it (re-run `install.sh` afterwards): all of `.agents/`, including the project-owned parts (`policy.conf`, `checks/`, `context/`, `plans/`), and your tailoring in `AGENTS.md` or `.agents/AGENTS.local.md` (recoverable from the backup below). The default policy blocks agents from both.
- A checkout or pull that brings a tracked file at one of these paths (say a teammate commits an `AGENTS.md`) overwrites your local copy without asking.

To recover from a `git clean` or a checkout, `sync` keeps a backup inside the git dir, which git clean, stashes, and checkouts never touch. It lives in each worktree's own git dir, at `$(git rev-parse --git-path ai-harness)/backup` (`.git/ai-harness/backup/` in a plain clone); a subdirectory install uses `backup-<prefix>` instead, with `/` in the prefix turned into `_`. It leaves out `.agents/builtin/`, which `install.sh` rebuilds. It's refreshed on every `sync` and whenever `verify` runs on the turn or full tier; edits since then aren't in it. If `.agents/` goes missing, re-run `install.sh`: it restores your files from the backup instead of seeding blank ones. If a tracked `AGENTS.md` replaces your local one, the next `sync` saves your last local copy as `AGENTS.md.before-tracked` in the backup and tells you once. Switching to team mode deletes the backup, except a saved `AGENTS.md.before-tracked`, which `sync` keeps and points you to.

A new clone or worktree starts with none of this, so each one needs its own `install.sh` run. All worktrees of a clone share one `.git/info/exclude`, so `sync` keys its block by worktree and install prefix (`# >>> ai-harness (local install; managed by .agents/bin/sync) [<worktree>][<prefix>]`, with `.` for the main worktree) and rewrites only its own. Two installs in different subdirectories get separate blocks the same way. A block whose worktree is gone is dropped by the next `sync` that writes the file, and a block from before the keys is taken over by the install whose paths it lists, if that install has no keyed block yet. Patterns in that file apply in every worktree, so install each worktree in the same mode: a team-mode worktree warns once when a local one at the same path hides its new harness files. Switch a project with `install.sh --local` or `install.sh --team`; each tells you what changed and what to commit. `--local` strips the harness out of the shared files the project keeps tracking (its blocks in `AGENTS.md`, its lines in `CLAUDE.md`, its hooks, deny rules, MCP servers, and Gemini context entries in the JSON configs; `AGENTS.md` stays in Gemini's context list while the project keeps a tracked `AGENTS.md` of its own) and untracks the ones left with nothing else. Stripping a JSON or TOML config needs python3; without it, `--local` stops with exit 3 before changing anything and names the files, unless none of them holds harness entries. Once a switch to local is committed, other clones lose `.agents/` on their next pull, so each developer re-runs `install.sh` (local by default) to get it back.

## The feedback loop

Three tiers, each a project-owned script in `.agents/checks/`, all run through one orchestrator:

| Tier | Runs | Typical content | Budget |
|---|---|---|---|
| edit | after every file edit (hook), `check <files>` | formatter check, per-file lint, syntax check | `EDIT_BUDGET` (15s) |
| turn | when the agent finishes a turn that changed files (stop hook), `verify` | incremental build, affected tests, lint on changed code | `TURN_BUDGET` (300s) |
| full | commit gate, CI, `verify --tier=full` | everything CI runs, sanitizers, slow analyzers | none |

- **Output contract.** `ok verify turn` on success. A workflow pack's `note: ` lines show under that status line, pass or fail, and in `--json` as `notes` (feature-driven says so when a simulated human is on). On failure: `path:line` findings (deduplicated, max 5 per file, 30 total), test failure markers, sanitizer reports trimmed to repo frames, and `full log: <path>`.
- **Exit codes.** 0 ok, 1 findings, 2 policy block, 3 tooling problem, 124 out of budget. Hooks block only on 1 and 2; anything else fails open with a note. `agents_step` (the tier scripts' helper in `.agents/lib/feedback.sh`) turns a command's exit 2 into 1, since for `make`, a test binary, or a linter 2 is just a failure; its `FAIL` line keeps the real code. A check that means a policy block exits 2 itself.
- **Stop gate.** Blocks at most `TURN_MAX_BLOCKS` (3) times per turn, then hands back to you instead of looping. Turns that didn't change the tree or a branch aren't gated. A turn that committed runs `verify` with `--since=<sha>` for `HEAD` and each branch tip the turn started from, so checks that read `AGENTS_SINCE` (the feature-driven gates) judge what was committed too, not just what's left uncommitted. If that stop pauses for a question or verify couldn't run, the range stays pending in `.agents/cache` and the next stop judges it from the same start; a give-up hands it to you (`verify --since=<sha>` in the message). A sign-off question on a done task doesn't pause the gate over commits made in that turn. The block message ends with how to pause when a red state is deliberate (`tasks ask <slug> <T-id> --gate=tests`). When `WORKFLOWS`, `STACKS`, or any `FDD_*` or `DEBUG_*` setting in `.agents/harness.conf` changed during the turn, the stop runs `verify` and adds a note with the old and new values, shown to you once when the stop is allowed (and in a block message, for the agent), and logged as `conf-changed` in `.agents/cache/hook-events.log`. A note, not a finding, since harness-tailor and your own requests change them too.
- **Hook log.** The hooks append a line per event worth keeping to `.agents/cache/hook-events.log` (local, gitignored): time, tool, event, decision, detail (a denied command, the edited files, a stop-gate result). A tool name they don't know as a shell, read, edit, or question tool goes there too, as `unknown-tool` with the name only (never the tool's input, cut to 64 characters), once per session and event (a payload with no session id counts as one long session; deleting the log starts over); only while the hook that would have checked it is on (`policy` for pre-tool, `edit` for post-edit). That's how an edit or shell tool the hooks miss shows up in a live session. MCP tools (`mcp__...`, `mcp_...`) and Copilot CLI's web, todo, and task tools aren't unknown. To list what turned up: `grep unknown-tool .agents/cache/hook-events.log | cut -f2,3,5 | sort -u`.
- **`--since=<rev>`.** Hands checks `AGENTS_SINCE`, that commit, and keys the cache on it. The project's tier scripts get the same file list as without it; a check that diffs can use it (feature-driven does).
- **Caching.** Results are keyed by tree state. Repeat calls with no changes return instantly.
- **Baselines.** `verify --tier=full --update-baseline` records current findings so only new ones count. Tier scripts opt in with `agents_lint <name> <cmd>`.

## Guard and policy

**Guard** scans only lines added in the working tree and blocks the ways to get green without fixing anything: new suppressions (NOLINT, cppcheck-suppress, pragma ignores, `-Wno-`, noqa, eslint-disable, ts-ignore, and friends), skipped or disabled tests, `.only`, deleted test files, and removed test cases. A human can approve an exception with `guard allow <file-glob> <text> <reason>`; the policy blocks agents from running that.

Guard also blocks likely secrets in added lines: private key headers, AWS access key IDs, GitHub, Slack, and Stripe live tokens, Slack webhooks, Google API keys, and credential literals like `db_password = "s3cr3t..."` or `export API_TOKEN=...` (a value of 12 or more characters mixing letters and digits). Secret rules scan docs too, and every finding redacts every secret on its line: `config.py:2: block: [secret] aws_key = "<redacted>"`. References and placeholders pass: `${TOKEN}`, `$(cat ...)`, `os.environ[...]`, `<your-key>`, `{{ secret }}`, Stripe test keys, and values containing `example`, `sample`, `changeme`, `replace_me`, `dummy`, `fake`, or `xxxx`. A fake key in a test fixture is the same human call as any other exception: `guard allow 'tests/fixtures/*' 'KEY = ' 'fake keys for parser tests'`. The text can be any part of the line; use a part that isn't the key, since `guard.allow` gets committed and reviewed. There's no inline marker, since an agent could write one. Add your own with a `secret` rule in `.agents/guard.patterns` (`secret<TAB><regex><TAB><fix hint>`); a leading `(?i)` ignores case, and `{n}` or `{n,}` after a bracket expression works on every awk.

**Policy** lives in `.agents/policy.conf`: `deny-cmd` (command prefix, checked per segment of chains and pipelines, including inside `bash -c`), `deny-arg`, `deny-regex`, `deny-read` and `allow-read` (paths, also when named in a shell command). Defaults block `reset --hard`, `clean -f`, `git clean -x`/`-X`, `git stash -a`, history rewrites, `--no-verify`, changing or removing files under `.git/hooks/` (`rm`, `mv`, `cp`, `chmod`, `sed -i`, `find -delete`, a `>` redirect, PowerShell's `Remove-Item` and `Set-Content`, and friends, with `/` or `\`; reading them with `cat` or `ls` is fine), piping downloads into a shell, `sudo`, and reads of `.env` files, key material, and credential directories. Two more ways to switch the commit hooks off are blocked by gitflow's agent check rather than policy.conf, since spotting them takes git's own argument rules: `git commit -n`, and setting `core.hooksPath` (`git -c core.hooksPath=...`, `--config-env`, or `git config` in any scope, including `--unset` and `git config set`/`unset`; reading it is fine). A person can still run any of them. Paths match however they're spelled, as written or with symlinks resolved, so a project under macOS `/tmp` (really `/private/tmp`), a symlinked home, or a link inside the repo to a `.env` file gets the same answer. Git workflow rules (pushes, PRs, merges, branch and commit formats) live in `.agents/git.conf`.

To see what the hook would do with a command or a read, and which rule decides it, run `policy test`. It uses the hook's own matching code, so its answer is the hook's answer:

```
$ .agents/bin/policy test "sudo apt install x"
blocked: `sudo` is blocked by policy: no privilege escalation
.agents/policy.conf:24: deny-cmd sudo  # no privilege escalation
$ .agents/bin/policy test --read config/.env.example
allowed (an exception: .agents/policy.conf:33: allow-read ./**/.env.example)
```

Exit 2 when blocked, 0 when allowed, 3 on a usage error or without python3. A block from the git workflow names `.agents/git.conf` instead of a line.

## Validation gates

The built-in `validate` skill checks work at three gates: the plan before any tests or code, the tests before implementation, and the implementation before hand-back. Each gate runs the deterministic checks first, judges what they can't, and returns PASS, REVISE (specific findings back to the producer, at most two rounds), or ASK (questions only the human can answer). Gates listed in `VALIDATE_ASK` always end with a check-in with you.

Questions and answers live in a **question ledger** per plan (`.agents/plans/<slug>/questions.json`, or `_general/` outside a plan):

- `tasks ask` records a question (with its gate) and blocks the task (a done task stays done while it waits for your sign-off). It refuses a question the ledger already answered, so you don't get asked the same thing twice across sessions or tools. IDs and ticket keys count (`approve design F-2` isn't `F-3`, `REQ-12` isn't `REQ-13`), and a check-in with `--gate` is compared only with answers at the same gate (and with questions that name IDs too, or name none), so an inspection request doesn't match a design rejection. A question without `--gate` is compared with every answer. IDs are capitals, a dash or underscore, and digits; `F12` or `#12` count as plain words, and so does any capitalized code like `UTF-8`, which can only make two questions look different.
- `tasks answer` records your answer, adds it to the plan's Decisions, and resumes the task once nothing is open.
- `tasks questions <words>` searches every question and answer across all plans.
- A task waiting on you pauses the stop gate, which matters at the test gate, where tests are deliberately red.

With the `questions` hook feature on (default), the hooks keep the ledger honest without relying on the agent to remember: before the agent asks through its question tool (AskUserQuestion in Claude Code, ask_user in Copilot), an earlier answer to the same question is shown to it instead, once per session; after you answer, the question and answer are recorded; and a new session starts with a reminder of anything still waiting on you. When roles land, `validate` becomes the validator role's instructions; until then, run it in a subagent with a fresh context where your tool supports one.

## Git workflow

Every repo's git workflow is described in config and carried out by one CLI, `.agents/bin/gitflow`, so it's generic in the harness and tailored per project:

- **Settings layer**, last wins: harness defaults, then your personal `~/.config/ai-harness/git.conf` (or `$AGENTS_PERSONAL_DIR/git.conf`; your workflow follows you across repos), then the project's `.agents/git.conf`. `gitflow config` shows the result.
- **Templates do double duty.** `GIT_BRANCH="{type}/{ticket}-{slug}"` and `GIT_COMMIT="{ticket}: {summary}"` both build names and messages (`gitflow start`, `gitflow commit`) and validate them. Extra rules go in `GIT_COMMIT_PATTERN`, required trailers in `GIT_COMMIT_TRAILERS`.
- **Commit templates.** A project can define the whole message in one file (`GIT_COMMIT_TEMPLATE`, or the repo's own `commit.template`): subject, sections like `Why:` and `Testing:`, and trailers. Sections are required unless marked `(optional)`. Humans get the editor prefilled (ticket and trailers already in), agents fill sections with `gitflow commit --section Why=...`, `gitflow template` shows what's expected, and the `commit-msg` hook checks everyone's messages against it.
- **The steps:** `start`, `commit`, `update` (merge or rebase the base in), `check`, `push` (runs a verify tier first), `pr` (gh, glab, or printed text; base forced to `GIT_BASE`; body from `.agents/git/pr.md`), `review`, `merge`. `gitflow <step> --help` (or `-h`) prints that step's usage and does nothing else. An unknown option (`gitflow push --force`, `gitflow commit -m x`), an argument a step doesn't take, or `--help` among other words (`gitflow commit fix -h parsing`) is refused with the usage and exit 3, before anything changes. Summary words that start with `-` go after `--`: `gitflow commit Handle -- -v`. The policy hook lets an agent run exactly `gitflow <step> --help` even for a step that's yours; native deny rules, where they're rendered, still block it.
- **Who does what:** `GIT_AGENT_MAY` lists the steps agents may take (default `branch commit`); the rest are yours.
- **Protected branches are the project's call.** Nothing is protected by default, which suits solo and trunk-based repos. Set any names or globs, such as `GIT_PROTECTED="dev/main release/*"`; `{base}` stands for whatever `GIT_BASE` is.
- **Plans link to branches only if you want.** `tasks link <slug>` records a plan's branch so `gitflow pr` can include it. Plans work without branches, and branches without plans.
- **Enforcement:**
  - Local git hooks (`commit-msg`, `pre-push`, and `prepare-commit-msg` when there's a commit template) apply to you and to every tool: message format, trailers, protected branches, force pushes. They're installed only once git.conf, or an installed workflow pack, has something for them to enforce (`sync` adds them when it does). An existing `core.hooksPath` (husky, pre-commit) is left alone, with the lines to add.
  - The policy hook checks agent commands before they run: protected pushes, force pushes, branch names, PR base, rebase vs merge, steps outside `GIT_AGENT_MAY`, approving PRs, which agents never do, and switching the commit hooks off (`git commit -n`, setting `core.hooksPath`), whatever the config.
  - When the project's git.conf sets `GIT_AGENT_MAY` explicitly, the forbidden steps are also rendered as native deny rules (Claude Code, Codex). Personal settings never land in committed files, so CI and every developer render the same thing.

**Safety is universal; process is opt-in.** Safety rules (no `reset --hard`, no `--no-verify`, no secrets, no force-pushing shared work) live in policy.conf and hold regardless of flow. Process rules (protected branches, branch and commit formats, trailers, dev workflows) apply only when configured. With nothing configured, each is a no-op, so a repo with no flow, or a developer who ignores the flow, still works. The smoke suite has "absence" tests that hold this in place: a zero-config trunk repo, gitflow with no plan or ticket, a plan with no workflow, and a workflow with no git config.

The `git-workflow` skill covers the judgment: commit granularity, PR descriptions worth reading, and handling every review comment (fix it, explain it, or ask the human). A `deny-cmd` in policy.conf always wins, if a project wants to forbid agent pushes outright.

## Plans that survive sessions and tools

`plan-task` keeps a ledger per piece of work in `.agents/plans/<slug>/`: `plan.md` for intent, questions, and decisions, `tasks.json` for steps, `progress.log` for session notes. Agents edit it only through `.agents/bin/tasks` (`new`, `add`, `next`, `set`, `ask`, `answer`, `log`), so it stays valid JSON. Any agent in any tool resumes with `tasks next <slug>` and `git log`. Like `gitflow`, `tasks <command> --help` prints that command's usage and writes nothing, and an unknown option or `--help` among other words is refused (exit 3) instead of being recorded as text; text that starts with `-` goes after `--`.

## Working files

Agents make files on the way to a change: repro scripts, logs, notes, review notes. They go in `.agents/work/`, seeded by `install.sh` and kept out of git by its own `.gitignore` (team mode too):

- `scratch/<slug>/` for one piece of work (the plan's slug, or a short name). When the plan's last task is done, `tasks` moves it to `scratch/_done/<slug>/`, and reopening the plan moves it back.
- `scripts/` for scripts worth reusing, `references/` for outside material, `reports/` for finished write-ups, `requirements/` for requirement exports and drafts.

A core rule tells agents to use it. When a turn leaves new untracked files elsewhere, the stop gate (after verify passes) names them once and asks the agent to move scratch into `.agents/work/` or say why each file belongs in the repo. That's one continuation, not a block, and it doesn't count toward the stop gate's tries. `WORK_REMIND="off"` in `.agents/harness.conf` turns it off. Anything the team needs gets promoted out: a script into a tracked folder, a report into your docs.

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
- **Links or copies.** Each rendered skill links to its library (a relative link inside the repo, an absolute one outside it), so edits are live. With `LINK_MODE="copy"` it's a copy instead, refreshed by `sync`, which records each copy's hash in `.agents/generated.lock` and warns ("was edited by hand; sync replaced it from ...") before replacing one edited in place. Edit the source in its library instead. Team mode commits the links for skills inside the repo and always copies a shared skill from a library outside it, since a link out of the repo can't be committed.
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
- **MCP servers render per tool.** `mcp/<name>.json` in a library (or an active workflow pack's own `mcp/`) is one MCP server, written once. `sync` merges it into each enabled adapter's config beside the servers you added by hand: `.mcp.json` (claude and copilot share it), `.cursor/mcp.json`, `.gemini/settings.json`, and a marked block at the end of `.codex/config.toml`. Secrets are only ever `${VAR}` references; a literal in a secret-looking `env` or header value, a key shape or a secret flag's literal value in `args` (`--api-key k8Hq...`), or a password or secret query parameter in `url` is an error and that server isn't rendered.

  ```json
  {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" },
    "tools": ["search_issues", "get_issue"]
  }
  ```

  sync owns only the server names it wrote (recorded in `generated.lock`); a hand-added server of the same name wins, with a warning, and every other key in those files stays as it is. Team mode never renders a personal server (add it to your tool's own user config); local mode never touches a tracked config. See `.agents/library/README.md` for the format and what each tool can't express.
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

A stack's settings go in `.agents/harness.conf` (every tier) or in a tier script (that tier). A value the tier script sets wins over `harness.conf`, which wins over the stack's default. A stack's `lib.sh` picks its keys up with `agents_conf_import <PREFIX>` (from `.agents/lib/feedback.sh`), since `verify` reads `harness.conf` without passing it on.

- **cpp-cmake**: agent-owned build trees, syntax-only compiles with each file's real compile command, new warnings in changed files, clang-tidy on changed lines only (skipped when it isn't installed; drop the guard in the tier scripts to require it), affected-test selection through the CMake file API, an ASan+UBSan tier, and cppcheck with baselines. When CTest has no tests registered, the test steps print `no tests ran` and exit 3 (`INFRA verify turn`), never a quiet `ok`; set `CPP_NO_TESTS=ok` for a project with no tests, or one whose tier scripts run its test binaries themselves. Settings are `CPP_*` (`CPP_BUILD_DIR`, `CPP_JOBS`, `CPP_TEST_TIMEOUT`, and so on). See `stacks/cpp-cmake/README.md`.
- **python**: runs the tools the project already uses, from its own environment (`PY_RUN`, else its virtualenv: `PY_VENV`, `.venv`, `venv`, an activated `$VIRTUAL_ENV`, or poetry's; else `PATH`, though pytest and mypy never come from `PATH` when the project has or expects an environment of its own). Edit: a format check (ruff format or black, only when the project formats with one; reported, never applied) and ruff lint on the edited files (only syntax errors and undefined names when the project has no ruff config), or a syntax check without ruff. Turn: the same on changed files, mypy or pyright for changed files when the project configures one, and only the pytest tests the change can reach, from a static import scan (anything it can't place runs the whole suite). Full: all of it over the whole project, every test. A tool the project configures but the machine lacks is `INFRA`; one it doesn't configure is skipped. When pytest collects nothing, the test steps say `no tests ran` and exit 3, like cpp-cmake; `PY_NO_TESTS=ok` turns that off. Settings are `PY_*` (`PY_RUN`, `PY_VENV`, `PY_FORMAT`, `PY_TYPECHECK`, `PY_PYTEST_ARGS`, and so on). See `stacks/python/README.md`.

## Workflow packs

Stack packs answer "how do we build and test this language." Workflow packs answer "in what order, and with what evidence, do we change things." They're independent, so a project can combine `cpp-cmake` with `req-driven`.

`install.sh --workflow <name>` adds the pack to `WORKFLOWS` and appends its settings to `.agents/harness.conf` once. Nothing is copied: the pack runs in place from its library (see [Libraries](#libraries)). `verify` runs its `checks/<tier>.sh` for each tier after the project's own tier scripts, so nothing in `.agents/checks/` has to change; `gitflow` runs its commit-message check; `sync` renders its `skill/` as a skill named after the pack.

A pack can also ship:
- `policy.conf.snippet`: rules appended to `.agents/policy.conf` once. Its first line is a marker comment; while the marker is there, reinstalls add nothing, so a rule you delete stays deleted.
- `seed/`: files copied into the project once and never overwritten (project-owned), e.g. `seed/.agents/<name>/.gitignore` to keep the pack's working files local. Harness-owned paths are skipped.
- `checks/commit-msg.sh <file>`: extra commit-message rules. `gitflow` runs it everywhere it checks messages (the `commit-msg` hook, `gitflow commit`, `check`, and pre-push). Exit 1 or 2 rejects the commit with its output. Any other failure (a missing tool, a crash) also blocks, since an unchecked message could carry what the check exists to stop, and so does an active workflow that doesn't resolve while a project-listed library isn't here (exit 3); a human can bypass with `git commit --no-verify`, which the policy denies to agents. A pack that prefers to let commits through handles its own missing tools and exits 0. It sees every message, including merge, fixup, and revert messages that the git.conf rules skip. Shipping one is enough for the git hooks to be installed.
- `bin/`: commands for people. Shipped packs' commands are made executable on install; in a library of your own, set the bit yourself.
- `human-gates`: one line naming the pack's record key, in lowercase letters, digits, and dashes, never `on` (feature-driven's is `fdd`, debug's is `debug`; `#` lines are comments). It says the pack has approvals only a person makes, recorded in `.git/ai-harness/<key>-approvals` through the harness's `.agents/lib/approvals.py`, which also refuses in an agent's shell and honors `install.sh --simulated-human`. `--simulated-human` needs at least one active pack with this file, and turns the switch on for all of them; a file that names no key fails it (exit 3) before anything changes.
- Notes: a line a pack's check prints that starts with `note: ` isn't a finding. `verify` shows it under the status line, pass or fail, and in `--json` as `notes`; it's left out of the shaped findings and the full log. Use it for state a person should see on every run, not for problems.
- `checks/state.sh`: prints whatever the pack's checks read that git ignores (local working files), so `verify`'s cache notices when it changes. It runs on every `verify` call, since it feeds the cache key, so keep it fast and side-effect free. Plan ledgers are always included.

- **req-driven**: every change starts from a requirement ID in any exported requirements source (CSV, JSON, Markdown, text). Deterministic checks: IDs must exist, new tests must name the requirement they verify, changes in scope must reference one (in code, tests, or the plan task in progress), and the full tier writes a requirement to code to tests trace with optional untested-requirement gating. Standard-agnostic. See `workflows/req-driven/README.md`.
  The skill's phases (pin the requirement, tests first, implement, report) each end at a `validate` gate, so they map onto planner, tester, implementer, and validator roles when roles land.
- **feature-driven**: classic FDD for one developer. The agent drafts a domain model and feature list, then plans, designs, and builds one feature at a time; you approve the list, each design, and each finished feature with `fdd approve` (`.agents/commands/fdd`) in your own terminal. The policy blocks the usual spellings for agents, `fdd approve` refuses in a shell Claude Code, Gemini CLI, or Cursor started, and only approvals it recorded in the git dir count (a line written into `approvals` any other way is a policy block, `fdd-approval-unrecorded`); see Known gaps for what's left. For flows where an agent plays you (a scratch repo, a pilot, a demo), `install.sh --simulated-human` turns on a per-clone switch for every pack with human gates: a shell that has the token it prints may approve, even an agent's, and every approval is marked simulated and counts only while the switch is on. Agents in the clone's own sessions don't have the token, so they're still refused. `fdd status`, `verify`, and the install summary say when it's on. Never use it in a real project. The stop gate also judges the commits an agent made during the turn, and keeps them pending through a pause until a stop passes. Checks: design before build, a task in progress for every in-scope change, an `FDD_SCOPE` that matches files in the repo (`fdd-scope-empty`), no new work on an inspected feature unless you reopen it by approving its design again (`fdd-inspected`), no feature task in progress with the feature list or `FDD_DIR` gone (`fdd-list-missing`), no feature task on another feature's branch (`fdd-wrong-branch`, from `tasks link` and, with `{ticket}` in `GIT_BRANCH`, the branch's ticket), no private feature IDs in shared code or commit messages, and a parking-lot progress report, where a feature counts as built only with a commit git has (`fdd approve inspect` refuses before that). All FDD files stay local. See `workflows/feature-driven/README.md`.
- **debug**: a debugging process the agent follows like a skeleton, filled in with the repo's own tools. It investigates and doesn't fix: a session ends at a root cause you approve, and the fix goes through the project's own process. Four workflows, each a steps file on one shared engine: bug reports (`debug start bug <ref>`), failing tests or CI (`test`), crashes and hangs (`crash`), and field or production issues (`field`, evidence before reproduction). Each goes from intake through reproduction attempts, evidence, hypotheses, and isolation to a `root-cause.md` you approve with `debug approve <slug>` in your own terminal, with the same approval rules and simulated human as feature-driven (`.agents/lib/approvals.py`, recorded in `.git/ai-harness/debug-approvals`). A project-owned playbook (`.agents/debug/playbook.md`, drafted by harness-tailor) binds each step to the repo's skills, commands, and docs. `debug run` captures every command as evidence, checks it against the policy first, masks what guard's secret rules match in what it captures, and stops a command past its time limit (`DEBUG_RUN_TIMEOUT`, 600 seconds; `--timeout=<sec>`), recording it with exit 124; a reproduction attempt is required, its outcome never blocks, and the root cause's confidence (`confirmed`, `reproduced`, `evidence-only`) is computed from the outcomes. Checks: no commits to project code during a session (`debug-committed`), experiments gone once the root cause is written (`debug-experiments-left`), `root-cause.md`'s headings and confidence (`debug-format`), cited evidence that `debug run` captured (`debug-evidence-missing`), the playbook's format, and approvals and closes only `debug` recorded (policy blocks). Sessions stay local. See `workflows/debug/README.md`.

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
├── .codex/hooks.json            hooks merged in                          (codex)
├── .gemini/settings.json        context file, hooks merged in            (gemini)
└── .agents/
    ├── core/                    harness: core rules, guard patterns
    ├── bin/                     harness: sync, verify, check, guard, policy, tasks, eval, gitflow
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
    ├── work/                    project: agents' working files, gitignored (scratch, scripts, references, reports, requirements)
    ├── generated.lock           sync: what it added to shared config files, hashes of agent renders and skill copies, MCP server names
    └── cache/                   local: logs, verify cache, hook state (gitignored)
```

Merged config files keep everything that isn't the harness's. Harness entries are recognized by their `.agents/hooks/` command path, and deny rules by `generated.lock`, so a re-render replaces exactly what the harness added. Agent renders are recognized by their marker line; the lock also keeps their hashes so sync can warn before rewriting one edited by hand. Skill copies (marked by a `.harness-copy` file) get the same: their hashes go under `skill_copies`, shared copies only in team mode, every copy in local mode. MCP servers are recognized by the names the lock records per file (`.mcp.json`, `.cursor/mcp.json`, `.gemini/settings.json`), and in `.codex/config.toml` by the `# >>> ai-harness mcp` block, which sync keeps at the end of the file.

## Upgrading projects

Change the harness here, bump `VERSION`, add a CHANGELOG entry, then per project:

```sh
~/code/ai-harness/install.sh ~/code/my-project
```

## Requirements

- bash 3.2+ (stock macOS works), git, POSIX tools (any awk: tested with gawk, mawk, one-true-awk).
- python3 3.8+ for hooks, JSON config rendering, evals, the cpp-cmake helpers, the python stack's affected-test scan, and workflow packs' checks. Without it, sync warns and hooks step aside rather than wedging the agent.

## Windows

Symlinked skills need Developer Mode plus `git config core.symlinks true`. Otherwise set `LINK_MODE="copy"` in `.agents/harness.conf`. Hooks need Git Bash on PATH.

## Working on the harness

Never install the harness into this repo; try changes in a scratch repo under /tmp instead. When an agent plays the person there (an orchestrating session driving `claude -p`, a feature-driven pilot), install with `--simulated-human` and approve with the token it prints (`AGENTS_SIMULATED_HUMAN=<token> .agents/commands/fdd approve ...`, or `AGENTS_SIMULATED_HUMAN=<token> .agents/commands/debug approve <slug>`), not with `env -u CLAUDECODE`.

```sh
bash tests/lint.sh       # seconds: syntax, shellcheck, portability, ownership lists, budgets, CODEOWNERS list, docs voice
bash tests/smoke.sh      # ~1,100 checks in parallel groups; the C++ section runs when cmake and a compiler exist
bash tests/all.sh        # lint, then smoke under every awk on the machine (the release gate)
bash scripts/package.sh  # dist/ai-harness-<version>.zip plus its SHA-256
```

CI (`.github/workflows/ci.yml`) runs lint and `tests/all.sh` on Ubuntu and on macOS with Apple's bash 3.2.

The smoke suite runs its sections in groups, one per CPU at a time, and prints them in file order. `SMOKE_JOBS=1` runs one group at a time; `SMOKE_TIMES=1` prints how long each group took. On a Mac, most of its time can go to macOS assessing each newly installed script on its first run (a second or more each, a few hundred per run). Listing the app you run it from (your terminal) under System Settings, Privacy & Security, Developer Tools should skip that check for what it starts.

To add a section, append it right before `echo "guards"` near the end: it runs in the foreground alongside the groups, as is. To run it in parallel instead, wrap it as a group (`grp_name() { ... }` then `group grp_name`) next to the others. A group sees what's set above it but nothing another group sets, so it makes its own repos, or starts with `wait_group <fn>` when it needs one from another group.

Keep `template/.agents/core/AGENTS.core.md` tight. Every line there loads in every session of every project, and lint fails past 25 lines.

## Known gaps

- Codex and Gemini CLI hooks follow the vendor docs (Oct 2026) and were tested with recorded payload shapes, not inside live sessions. Codex execpolicy rule syntax is also unverified against a live Codex.
- Codex loads project hooks only in a trusted project, after you review them once (`/hooks`). Open Codex issues report `apply_patch` not firing hooks and exit-2 blocks not enforced on some versions; the harness uses the JSON deny form. Codex runs hooks in the session's directory, so the hook command starts from the repo top (`git rev-parse --show-toplevel`). The policy hook matches Codex's shell tool as `Bash`, as its docs name it; a version that reports it under another name gets only the execpolicy rules (`deny-cmd`), not `deny-read` or `deny-regex`. In local mode an untracked `.codex/hooks.json` holding harness hooks is hidden from git, including hooks of yours merged into it.
- Gemini CLI turns hooks off in untrusted folders and fingerprints project hooks: a changed hook command asks for trust again, so an upgrade that changes the command line asks once. The policy hook covers `run_shell_command`, `read_file`, `read_many_files`, `grep_search` (and its older name `search_file_content`), and `glob`, checking `file_path`, `path`, `dir_path`, and each `include` entry (and `paths`, which older releases take; Gemini's docs name `path` on one page and `dir_path` on another, so both count). A glob in `include` (`**/*.txt`) is checked as written, not expanded, and `list_directory` (names only, no contents) isn't checked.
- No question-ledger hooks for Codex or Gemini CLI: neither has an ask-the-human tool a hook can see. Session start still reminds them of open questions.
- Copilot, Cursor, Codex, and Gemini CLI have no documented personal hook location in the project, so a tracked `.github/hooks/harness.json`, `.cursor/hooks.json`, `.codex/hooks.json`, or `.gemini/settings.json` turns that adapter's hooks off in local mode (sync warns and leaves the tracked file alone); the Copilot cloud agent, which works from the remote repo, gets nothing in local mode either.
- Local mode's files are ignored by git in the clone, so `git clean -fdX` / `-fdx` and `git stash -a` take them away (`git stash -u` is fine), and a checkout that brings a tracked file at one of those paths overwrites the local copy. The backup in the git dir is refreshed on every `sync` and whenever `verify` runs on the turn or full tier; edits since then aren't in it. See [Local mode](#local-mode).
- Worktrees of one repo share one exclude file: git reads only the common `info/exclude` and has no per-worktree one (the only per-worktree route, `core.excludesFile` under `extensions.worktreeConfig`, changes the repo's config and replaces your global excludes file). Each worktree gets a block of its own, but every pattern in the file applies in every worktree. So a local-mode worktree's `/.agents/` line also hides new harness files in a team-mode one at the same path; that `sync` warns once. Keep a repo's worktrees in one mode. A worktree whose directory is missing when another one syncs (an unmounted drive) loses its block until its own next `sync`. Once an install has a keyed block, it never takes over an unkeyed one again, so after a downgrade and re-upgrade, delete the leftover unkeyed block by hand.
- The cpp-cmake stack's exclude lines go outside the harness block and stay after you drop the stack. Older versions wrote `/build-agent*/` and `/compile_commands.json` at the repo top even for a subdirectory install; delete those lines by hand if you don't want them.
- Local mode with a tracked `AGENTS.md`: the rule files for the other tools follow the vendor docs (Oct 2026) and weren't tried in live sessions. Copilot attaches an `applyTo: "**"` instructions file when it works on a file in the repo, so a chat that touches no file may not include the harness rules (VS Code may still load it from its `description`). Codex reads `AGENTS.override.md` in place of `AGENTS.md`, so after a pull that changes `AGENTS.md`, Codex reads the old copy until the next `sync`; its session-start hook says so (only with `questions` in `HOOKS`), and `verify` flags it at the turn tier. A `GEMINI.md` or `AGENTS.override.md` the project tracks, or a tracked `.gemini/settings.json` whose `context.fileName` leaves out `GEMINI.md`, leaves that tool without the harness rules in the clone (sync names it). Copilot CLI and Cursor CLI also read a root `GEMINI.md` or `CLAUDE.md`; whether they follow the `@` imports in the ones sync writes isn't documented. The Copilot cloud agent and code review work from the remote repo and see none of it. The docs don't say whether a file git ignores still loads: Cursor's call rules "version-controlled", and Gemini's `context.fileFiltering.respectGitIgnore` doesn't say whether it covers `GEMINI.md` or its imports; if one of them skips ignored files, that tool misses the rules (hooks still enforce). Codex stops reading instruction files at `project_doc_max_bytes` (32 KiB by default), and `AGENTS.override.md` holds both files; `sync` warns past 32 KiB but can't see a limit you raised. In a subdirectory install, Copilot reads `.github/instructions/` only at the workspace root.
- Hooks were tested with recorded payload shapes, not yet inside live Claude Code, Copilot, and Cursor sessions. Watch `.agents/cache/hook-events.log` on first use: an `unknown-tool` line names a tool the hooks don't classify, and if it edits files or runs commands, the policy and edit hooks miss it. Copilot's hooks see every tool call, and VS Code's tool names aren't in its hooks docs, so expect its edit tools (`replace_string_in_file` and the like) to show up there. Claude Code, Codex, and Gemini CLI run the hooks only for the tools their matchers name, so a tool outside those never reaches the hook to be logged; an `unknown-tool` line under `claude` naming a tool Claude Code doesn't have most likely came from another tool running the Claude hooks (Copilot CLI does; VS Code may, and its docs say it ignores matchers). MCP tools named another way than `mcp_...` (Copilot CLI's naming isn't documented) show up as unknown too. The question-tool payloads (AskUserQuestion input and answers, Copilot ask_user) are the least certain; the capture falls back to recording the raw answer text.
- `gitflow` was tested against a local bare remote and a stand-in `gh`, not live GitHub, GitLab, or Jira. `gitflow review` lists all PR comments (inline ones as `path:line`), not only unresolved threads.
- Duplicate-question detection is word overlap with light stemming, not semantics. It catches rewordings of the same question; it can miss a paraphrase and, rarely, flag two different questions that share most words (`--force` overrides).
- Guard's secret rules go by shape, not entropy: a random token with no known prefix and no telling variable name gets through. Guard sees only lines added in the working tree, never history or ignored files, and slows a little on very large diffs (about 2x on a 20,000-line new file). Treat it as a tripwire; a key that reached a commit still needs revoking. A `guard allow` entry clears every rule on the lines it matches, so an approval for a suppression also hides a secret on that same line.
- No sandbox profile ships with the harness. Pair it with a devcontainer that allowlists egress for unattended runs.
- Policy rules block commands and reads, not writes. An agent can't run `guard allow`, but it could edit `.agents/guard.allow` directly. Command rules also match patterns, not intent: an agent that writes its own script, splices a word (`"appr""ove"`), or goes through a variable, `xargs`, or a python subprocess is outside them. A hook can't stop an agent that runs arbitrary shell, so the harness doesn't chase spellings with more patterns. The commit-hook rules catch the plain spellings only: `core.hooksPath` set through `GIT_CONFIG_*` environment variables, `git config --edit`, an `include.path` file, an edit to `.git/config`, or a value from a command substitution (`git config core.hooksPath $(mktemp -d)` reaches gitflow without its value); a hooks directory named another way (`cd .git/hooks`, `$(git rev-parse --git-path hooks)`); PowerShell cmdlets beyond the common file ones (`Remove-Item`, `Set-Content`, `del`, and friends); a husky-style hooks directory of the project's own; and Edit-tool writes all get through. `cp` out of `.git/hooks/`, or a search with the word `rm` in quotes, counts as a change (use `cat`).
- Copilot CLI's docs name its `grep` (or `rg`), `glob`, and `view` tools but not their arguments, so the policy hook reads the path under the names Claude Code uses (`path`, `file_path`); an argument named another way gets through unchecked. Its `PreToolUse` hooks report `rg` as `Grep`. In every tool, a search aimed at a denied directory itself (`~/.ssh`, not `~/.ssh/id_rsa`) isn't blocked: deny-read rules match the paths inside it.
- feature-driven approvals are tamper-evident, not tamper-proof. `fdd approve` refuses in an agent's shell only where the tool marks it (`CLAUDECODE` for Claude Code, `GEMINI_CLI` for Gemini CLI, `CURSOR_AGENT` for Cursor); Codex and Copilot document no marker, so there a spelling the policy doesn't catch still approves, and so does `env -u CLAUDECODE` anywhere (use `install.sh --simulated-human` where an agent is meant to play you). It also means you approve in your own terminal, not through Claude Code's `!` prefix. An approval counts only if `fdd approve` recorded it in the git dir (`.git/ai-harness/fdd-approvals`), so an Edit-tool or shell write to `approvals` doesn't count and is flagged; an agent that writes both files on purpose gets through, and nothing records which approvals were made inside an agent turn. Outside a git repo there's no record and every line counts. Each clone adopts the lines already in `approvals` once, the first time you run `install.sh` or `fdd approve` from your terminal (an upgrade from before the record, or a copied-in FDD dir), and lists them; after that, nothing is adopted. Before that first run, a line an agent wrote is adopted with yours, so read the list. The adopt step is `fdd_tools.py adopt`, which the policy blocks by the same patterns as `approve`, with the same limits.
- The simulated human (`install.sh --simulated-human`) is tamper-evident the same way, not tamper-proof. Its switch and the hash of its token live in the git dir, which an agent's shell can write. A switch written any other way (one file, or the wrong content), or one that can't be written to, is off and is a policy block (`fdd-simulated-human` in feature-driven). The hooks flag a switch that appears or changes during an agent turn (before the next tool call, and at the stop), and one whose token is in an agent session's environment (at session and turn start); a flagged switch stays off until you run `install.sh --simulated-human` again. An agent that writes the switch and its record on purpose, outside a turn the hooks see (or after editing the turn snapshot in `.agents/cache`), gets a working switch, but every approval it makes is still marked simulated, and `fdd status`, `verify`, and the install summary say the switch is on. The token is printed once by `install.sh` and stays in the transcript of the session that ran it (for Claude Code, `~/.claude/projects/`); an agent running as the same user that searches those files, or wherever else you keep the token, can approve. Set it only on the one command that approves, never in a shell that starts an agent. An approvals line copied with its marker removed is the same gap as a forged line: it needs the record too. The mid-turn checks run in the pre-tool hook and the stop gate, so they cover agent turns in every tool the hooks run in (Codex and Copilot too, though their shells carry no marker anyway), but not a process the agent leaves running past its turn, and not tools or tool calls the hooks don't see.
- debug approvals and closes have feature-driven's approval gaps, from the same `.agents/lib/approvals.py`: any process that can write the git dir can append to `.git/ai-harness/debug-approvals`, and `env -u CLAUDECODE` before `debug close` records a plain close that counts even while a root cause waits on you (`approve` and `reject` also have the policy patterns; `close` doesn't). debug evidence is an `E-<n>.md` with the lines `debug run` writes, but the check sees the entry, not who wrote it, so an agent can fake one. A session belongs to the branch it started on: after a switch its session checks go quiet there (`debug-committed` still watches commits made from its start), and renaming that branch or deleting the session dir hides its commits. In team mode, harness files sync merges outside `.agents/` (`.claude/settings.json`, `.cursor/hooks.json`) count as experiments until they're committed; set `DEBUG_SCOPE` to the code paths. With feature-driven or req-driven also active, an experiment in their scope draws `fdd-untraced` or `req-untraced` at the stop gate while the debug session is open. The stop gate runs verify only when a turn changed the project's tree, and session files are gitignored, so a turn that only writes them isn't gated; the check-in set still runs at `debug approve`. See `workflows/debug/README.md` for the rest.
- `debug run` masks secrets in captured output line by line, with guard's rules (a private key from its `BEGIN` line to its `END` line): a secret split across lines isn't caught, nor is anything guard's shapes don't know (a `set -x` trace of a value with no telling name or prefix), and the output is unmasked on disk while the command runs, and after a SIGKILL until the next `debug run` in that session; masking a huge log takes about a second per 12 MB, which an agent tool's timeout can cut short. A SIGKILLed `debug run` can't stop its command, `report.md` (a pasted CI log) isn't masked, and a rule anchored at a line's start misses text after a `\r`. Customer data is the playbook's scrubbing binding's job. See `workflows/debug/README.md`, Known gaps.
- The stop gate's settings note covers `WORKFLOWS`, `STACKS`, `FDD_*`, and `DEBUG_*` in `.agents/harness.conf`, not `HOOKS` or `AGENTS_HOOKS`: taking `turn` out of `HOOKS` during a turn skips the stop gate, note included. A setting changed in the environment instead of the file isn't noted either.
- feature-driven inspections can be undone by deleting their lines from `FDD_DIR/approvals`: the record in the git dir says an approval was made, but nothing reports one that went missing. `fdd-wrong-branch` reads only the project's `.agents/git.conf` (as the ticket check always has), so a `GIT_BRANCH` set only in a personal git.conf doesn't turn its ticket check on.
- The feature-driven stop gate judges the commits a turn made: what `HEAD` or any local branch has that none of the refs the turn started from (`HEAD` and every branch tip) had, minus merges, commits a remote-tracking branch has, and rebased copies with an unchanged patch. So a merge, pull, or rebase onto the base isn't the agent's work, and commits on a branch the turn then left still count. A commit no branch keeps (made on a detached `HEAD` that moved on, or on a branch the turn deleted) isn't judged, nor is one fetched from a URL rather than a remote. An amend that changes code is judged as the whole commit, earlier lines included. A commit made outside an agent turn (by you, or before the hooks were installed) isn't judged by the turn gates, `gitflow push` runs `verify` without `--since`, and a plain `verify` after a commit sees a clean tree (`verify --since=<rev>` judges a range by hand). Other checks don't read `AGENTS_SINCE`: guard (new suppressions, deleted tests) and the project's tier scripts still see only uncommitted lines, so a same-turn commit hides their findings. A first commit in a repo with no commits yet isn't judged (there's no starting commit). The turn's starting commit and the pending range live in `.agents/cache`, which an agent can write like any other file.
- Tools that read `.agents/skills/` natively are assumed to follow the symlinks `sync` renders there, as Claude Code does in `.claude/skills/`. If one doesn't, set `LINK_MODE="copy"`.
- While a project-listed library isn't here, `sync` can't tell which marked copies came from it (a copy doesn't record its source), so with `LINK_MODE="copy"` or a missing library outside the repo it leaves every marked copy alone until the library is back. An active pack that resolves to a lower library in the meantime (a built-in the missing one would have shadowed) runs without a message.
- In team mode, personal skills aren't in AGENTS.md's skills index (a committed file); tools that only read the index don't see them.
- In team mode, an active workflow that's both built in and in your personal library renders the built-in skill, but `verify` and `gitflow` run the personal pack's checks, since runtime lookup keeps the plain search order. `sync` says so in one message ("your personal workflow 'x' runs its checks here, but team mode renders the skill from the builtin library").
- `eval`'s harness arms (B and C) still see your personal library, so personal skills and workflows can skew results between developers. Point `AGENTS_PERSONAL_DIR` at an empty directory for a clean run.
- Library paths can't contain spaces (`LIBRARIES` is space-separated).
- sync warns before replacing a skill copy edited by hand only when python3 is there (it keeps the hashes), and not for a personal copy in team mode (the committed lock leaves those out). A hand-edited copy whose skill no longer resolves is removed as stale without a warning. A sync that ran without python3 or stopped partway leaves an old record, so the next source change can be called a hand edit once. Committed copies in `.claude/skills/` have no `eol=lf` pin (`.agents/skills/` does), so a Windows clone with `core.autocrlf=true` sees them as edited; use `LINK_MODE="copy"` with `core.autocrlf=input` or `false` there.
- feature-driven checks don't see every place a private feature ID can reach shared history: branch summaries (`gitflow start PROJ-123 <summary>`), plan titles that go into PR bodies, and code committed outside an agent turn (`fdd-leak` reads uncommitted changes, plus the turn's commits at the stop gate).
- VS Code reads both `.claude/agents` and `.github/agents`; with the claude and copilot adapters on, it may list an agent twice.
- Codex loads project `.codex/` config only in a trusted project, and openai/codex#14579 reports project agents may not be callable by name.
- Copilot's cloud agent sees only committed agents; personal agents and local mode don't reach it.
- Cursor and Codex can limit an agent only to read-only; Codex effort values past high and Cursor's `[effort=...]` values beyond high aren't documented.
- Per-agent MCP servers: Claude takes server names; Codex and Cursor can't limit an agent to some servers.
- `${VAR}` expansion in `.mcp.json` isn't documented for Copilot CLI or VS Code (VS Code documents `${env:VAR}` for its own files), and neither is `${VAR:-default}`.
- Copilot's cloud agent takes MCP servers only from the repo's settings on GitHub; sync can't render them there.
- Codex loads `.codex/config.toml` only in a trusted project, Gemini CLI connects stdio servers only in a trusted folder, and Claude Code asks before using project servers from `.mcp.json`.
- Claude Code blanks some credential variables (`ANTHROPIC_API_KEY`, `NPM_TOKEN`, and others) when it expands a remote server's `url` and `headers`; use another variable name there.
- Personal MCP servers aren't rendered in team mode; add them with your tool's user scope (`claude mcp add --scope local`, `~/.cursor/mcp.json`, `~/.codex/config.toml`, `~/.gemini/settings.json`, `~/.copilot/mcp-config.json`).
- Codex has no `${VAR}` expansion: an env value it can't pass by name (a renamed variable, text around a reference, a default) or a header other than `Bearer ${VAR}` / `${VAR}` is left out for codex with a warning, and a reference in `command`, `args`, `cwd`, or `url` keeps the server out of codex entirely. An `sse` server gets a plain `url` there.
- The MCP secrets check for `args` and `url` is a heuristic. These get through: a literal secret after a flag whose name doesn't end in a secret word (`--creds-value abc...`), a short non-password value, a value that looks like a path or file name, a bare positional value or a url username (`https://<token>@host`) that isn't a key shape guard knows, and a secret inside `command` that isn't one either. Review server files like any other code that runs commands.
- sync rewrites an MCP config it changes with its own JSON formatting (two-space indent); comments aren't JSON, so a config with them is reported as invalid and left alone.
- The workspace reminder needs a stop hook: in a tool without one, agents get only the core rule. In Cursor, which counts its own stop loops, a reminder uses one of the stop gate's 3 tries.

## Roadmap

- **0.3** Workflow packs (req-driven first, in progress), more stack packs (Python, TypeScript).
- **0.4** Roles: planner, tester, implementer, and validator (plus a read-only explorer), defined once and rendered per tool, with one writer at a time and the validator gating each phase.
- **0.5** More MCP: Copilot's cloud agent and VS Code's `.vscode/mcp.json`, once their formats and variable expansion are verified (library MCP servers already render, see Libraries).

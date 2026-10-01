# .agents

Installed by [ai-harness](https://github.com/). One source of truth for AI coding agents in this
repo, rendered into whatever each tool reads, plus deterministic feedback tools the agents use.

In local mode (`HARNESS_MODE=local` in `harness.conf`, the default), everything here stays out of git in this clone: an exclude block in `.git/info/exclude` hides it, along with the harness's other files (`AGENTS.md`, `CLAUDE.md` or `CLAUDE.local.md`, `.claude/settings.local.json`, skill mirrors) when the project doesn't track them. If the project tracks `AGENTS.md`, the managed blocks go to `AGENTS.local.md` instead; if it tracks `CLAUDE.md`, `CLAUDE.local.md` imports the harness. Claude Code hooks and deny rules live in `.claude/settings.local.json`. `git clean -fdX` and `git stash -a` take all of it away (`git stash -u` is fine). After `git stash -a`, run `git stash pop` (not `install.sh`, or the pop fails). After `git clean -fdX`, re-run `install.sh`: it restores from the backup `sync` keeps in this worktree's git dir (`$(git rev-parse --git-path ai-harness)/backup`, or `backup-<prefix>` for a subdirectory install). The backup is refreshed on every `sync` and whenever `verify` runs on the turn or full tier; edits since then aren't in it.

## Layout and ownership

| Path | Owner | What it is |
|---|---|---|
| `core/` | harness | Core rules rendered into AGENTS.md, guard patterns |
| `bin/sync` | harness | Renders AGENTS.md blocks and tool adapters; `--check` for CI |
| `bin/verify`, `bin/check` | harness | Feedback orchestrator: tiers, caching, output shaping |
| `bin/guard` | harness | Blocks new suppressions and skipped or deleted tests |
| `bin/tasks` | harness | Plan ledger CLI (`plans/<slug>/tasks.json`) |
| `bin/eval` | harness | Replays real fixes to measure whether the harness helps |
| `bin/gitflow` | harness | The repo's git workflow: branch, commit, update, push, PR, review, merge, checks |
| `lib/`, `hooks/` | harness | Shared shell library, JSON renderer, `agents_render.py` (per-tool agent renderer), hook adapter |
| `builtin/` | harness | The built-in library: the harness's skills and its workflow and stack packs (e.g. req-driven, cpp-cmake), rebuilt on every install |
| `library/` | project | Your library: `skills/`, `workflows/`, `stacks/`. Upgrades never touch it. Also searched: `LIBRARIES` in `harness.conf`, your personal `~/.config/ai-harness/` |
| `skills/` | sync | Every resolved skill, rendered (links, or copies with `LINK_MODE=copy`). Don't edit here; a skill added by hand moves to `library/skills/` |
| `commands/` | sync | One wrapper per `bin/<name>` command of each active workflow pack (e.g. `commands/fdd`); finds the pack at run time |
| `stacks/<name>/lib.sh` | harness | Shim the tier scripts source; loads the stack pack from its library |
| files a workflow pack seeds | project | Created once from the pack's `seed/` (e.g. a `.gitignore` for its local working files); a deleted one comes back on the next install |
| `checks/{edit,turn,full}.sh` | project | What each tier actually runs. Tailor these. |
| `harness.conf` | project | Adapters, hooks, budgets, libraries, active stacks and workflows |
| `git.conf`, `git/pr.md` | project | Git workflow settings (over your personal `~/.config/ai-harness/git.conf`) and PR template |
| `policy.conf` | project | Commands and paths agents may not touch |
| `guard.allow`, `baselines/` | project | Approved exceptions; pre-existing findings |
| `skills.lock` | project | Third-party skills pinned by content hash (`sync --lock-skill`) |
| `context/`, `plans/`, `evals/` | project | On-demand docs, plan ledgers (gitignored), eval tasks |
| `generated.lock` | sync | Tracks what sync added to shared config files, and hashes of agent renders and skill copies |
| `cache/` | local | Logs, verify cache, hook state (gitignored) |

Harness-owned files are replaced on upgrade (re-run `install.sh`), so don't customize them.

## Tiers
- **edit**: `check <files>` after each edit (hook). Seconds. Out of budget = skipped.
- **turn**: `verify` when the agent finishes a turn that changed files (stop hook). Blocks on
  failure, up to `TURN_MAX_BLOCKS` times, then hands back.
- **full**: `verify --tier=full` at commit gates and in CI.

Exit codes everywhere: 0 ok, 1 findings, 2 policy block, 3 tooling problem, 124 out of budget.

## Common tasks
- New skill: `.agents/library/skills/<name>/SKILL.md` (or `~/.config/ai-harness/skills/<name>/` for every project), then `.agents/bin/sync`.
- Third-party skill: copy it into `.agents/library/skills/`, review it, `.agents/bin/sync --lock-skill <name> <source> <ref>`.
- Change a built-in skill or pack: copy it to `.agents/library/skills/<name>/`, `workflows/<name>/`, or `stacks/<name>/` under the same name and edit it there (edits in `builtin/` are lost on upgrade).
- Where a skill, workflow, or stack comes from: `bash .agents/lib/libraries.sh resolve <skills|workflows|stacks>`.
- Pre-existing lint findings: `.agents/bin/verify --tier=full --update-baseline`.
- Approve a suppression (humans only): `.agents/bin/guard allow <file-glob> <text> <reason>`.
- Turn hooks off for a session: `AGENTS_HOOKS=off`.
- See this repo's git workflow: `.agents/bin/gitflow config`, then `gitflow status`.
- CI (team mode only; local mode keeps the harness out of CI): `.agents/bin/sync --check && .agents/bin/verify --tier=full`.

# .agents

Installed by [ai-harness](https://github.com/). One source of truth for AI coding agents in this
repo, rendered into whatever each tool reads, plus deterministic feedback tools the agents use.

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
| `lib/`, `hooks/` | harness | Shared shell library, JSON renderer, hook adapter |
| `stacks/<name>/` | harness | Stack packs (e.g. cpp-cmake), refreshed on upgrade |
| `workflows/<name>/` | harness | Workflow packs (e.g. req-driven): checks verify runs per tier; their skill lands in `skills/` |
| `skills/harness-tailor`, `plan-task`, `review-diff` | harness | Built-in skills |
| `checks/{edit,turn,full}.sh` | project | What each tier actually runs. Tailor these. |
| `harness.conf` | project | Adapters, hooks, budgets, stacks |
| `git.conf`, `git/pr.md` | project | Git workflow settings (over your personal `~/.config/ai-harness/git.conf`) and PR template |
| `policy.conf` | project | Commands and paths agents may not touch |
| `guard.allow`, `baselines/` | project | Approved exceptions; pre-existing findings |
| `skills/<other>` | project | Your skills; pin third-party ones in `skills.lock` |
| `context/`, `plans/`, `evals/` | project | On-demand docs, plan ledgers (gitignored), eval tasks |
| `generated.lock` | sync | Tracks what sync added to shared config files |
| `cache/` | local | Logs, verify cache, hook state (gitignored) |

Harness-owned files are replaced on upgrade (re-run `install.sh`), so don't customize them.

## Tiers
- **edit**: `check <files>` after each edit (hook). Seconds. Out of budget = skipped.
- **turn**: `verify` when the agent finishes a turn that changed files (stop hook). Blocks on
  failure, up to `TURN_MAX_BLOCKS` times, then hands back.
- **full**: `verify --tier=full` at commit gates and in CI.

Exit codes everywhere: 0 ok, 1 findings, 2 policy block, 3 tooling problem, 124 out of budget.

## Common tasks
- New skill: `.agents/skills/<name>/SKILL.md`, then `.agents/bin/sync`.
- Third-party skill: copy it in, review it, `.agents/bin/sync --lock-skill <name> <source> <ref>`.
- Pre-existing lint findings: `.agents/bin/verify --tier=full --update-baseline`.
- Approve a suppression (humans only): `.agents/bin/guard allow <file-glob> <text> <reason>`.
- Turn hooks off for a session: `AGENTS_HOOKS=off`.
- See this repo's git workflow: `.agents/bin/gitflow config`, then `gitflow status`.
- CI: `.agents/bin/sync --check && .agents/bin/verify --tier=full`.

# .agents/library

This project's own skills, workflows, and stacks. Project-owned: upgrades never touch it.

    skills/<name>/SKILL.md     a skill (Agent Skills format)
    workflows/<name>/          a workflow pack: checks/<tier>.sh, skill/SKILL.md, and optionally
                               checks/commit-msg.sh, checks/state.sh, harness.conf.snippet,
                               policy.conf.snippet, seed/, bin/
    stacks/<name>/             a stack pack: lib.sh, checks/<tier>.sh

After adding or changing something, run `.agents/bin/sync`. Skills are always on: they show up
in `.agents/skills/`, in each tool's skills dir, and in AGENTS.md's skills index. Workflows and
stacks turn on by name in `WORKFLOWS` / `STACKS` in `.agents/harness.conf` (or
`install.sh --workflow <name>`), and run from here.

Libraries are searched in this order; the first one with a name wins, and sync warns when one
shadows another: this directory, `LIBRARIES` in `.agents/harness.conf`, your personal library
`~/.config/ai-harness/`, `LIBRARIES` in `~/.config/ai-harness/harness.conf`, then the harness's
built-ins in `.agents/builtin/`. To change a built-in, put your version here under the same name.

Pack scripts run from wherever the pack lives, so find the pack's own files from the script's
path (`"$(dirname "$0")/../tool.py"`) and the project from `$AGENTS_ROOT`.

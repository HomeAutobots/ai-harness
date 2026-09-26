# Evals

Does the harness help on *this* repo, and what does it cost? Public benchmarks can't answer
that. Harness changes typically move success rates a few points while moving cost a lot, and
generic context files can hurt. So measure on your own history.

## Tasks
Each task replays a real fix. The agent starts at the commit before it; success means the fix's
own tests pass afterwards (the tests stay hidden until scoring).

    .agents/bin/eval new tls-expiry <fix-commit>

Then edit `.agents/evals/tasks/tls-expiry.task`:
- `PROMPT`: describe the problem the way a bug report would. Don't leak the fix.
- `CHECK`: the command that decides success, e.g. build then run the named tests.
- `TESTS`: files taken from the fix commit at scoring time (pre-filled from the commit).

Aim for 20-50 tasks you've actually done, across the kinds of work you hand to agents.

## Running
    .agents/bin/eval run --arms=A,B,C --runs=3
    .agents/bin/eval report

| Arm | Setup |
|---|---|
| A | no harness (baseline) |
| B | harness, hooks off (instructions and tools only) |
| C | full harness |

The agent runs unattended with edit permissions, so run evals in a disposable environment
(devcontainer, VM, CI runner), never on a machine with credentials you care about.
Set `EVAL_AGENT_CMD` in harness.conf to change the agent. The prompt is appended as the last
argument. Token and turn metrics are parsed from Claude Code's `--output-format json`; other
agents still get success, verify, guard, and wall-clock numbers.

## Deciding
Adopt a harness change only if, versus A: success is at least as high, tokens per success are
within 1.1x, and median wall time is within 1.25x. Re-run after model upgrades; results drift.
Results land in `.agents/evals/results/` (gitignored).

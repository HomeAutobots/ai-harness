---
name: plan-task
description: Plans multi-step work into a resumable ledger in .agents/plans/<slug>/ (plan.md for intent, tasks.json for steps, progress.log for session notes) and works through it one task at a time. Use for any task touching more than 2-3 files, changing an interface, schema, or dependency, with unclear requirements, or likely to span sessions or tools. Also use when asked to plan, scope, spec, or break down work, or to resume or hand off work in progress.
---

# Plan a task

The ledger (plan.md, tasks.json, questions.json, progress.log) is how work survives a context reset, a new session, or a switch from Claude Code to Copilot to Cursor. Keep it true and keep it short. Never hand-edit `tasks.json`: use `.agents/bin/tasks`, which only writes valid JSON.

## Start or resume
1. `.agents/bin/tasks list`. If a plan matches this work, resume it (step 4). Don't start a second plan for the same thing.
2. Investigate just enough: the code involved, its callers, its tests, and any context doc whose trigger matches. Separate what you confirmed from what you assume.
3. Create the ledger:
   - `.agents/bin/tasks new <slug> <title>` (slug: lowercase-dashes)
   - Fill `plan.md`: Request, Done when, Context (confirmed, with `path:line`), open questions, risks. Aim for under 40 lines.
   - If the work has its own branch, `tasks link <slug>` records it, so `gitflow pr` can put the plan in the PR. Optional: plans work without a branch, and branches without a plan.
   - One `.agents/bin/tasks add <slug> "<change>" "<acceptance>"` per step. A step is one reviewable change with its own check, e.g. `"Reject frames over 1500 bytes in parse()" "verify passes; FrameTest.RejectsOversize passes"`.
   - **Plan gate:** run the `validate` skill on the plan (in a subagent with fresh context if your tool has them). Questions for the human go through the question ledger (`tasks ask`, see `validate`); wait for answers. Then set `Status: in progress` in plan.md and continue.
4. Resume routine, every session: `tasks next <slug>`, `tasks questions --open`, `git log --oneline -10`, then `.agents/bin/verify` to see where the tree stands. Trust the ledger and the tree over your memory.

## Work the ledger
One task at a time:
1. `tasks set <slug> <id> doing`
2. Make the change. Run `.agents/bin/check` on touched files as you go.
3. `.agents/bin/verify` must pass. Where the workflow has a test or implementation gate, run `validate` for it before moving on.
4. Commit only if plan.md says `Commits: auto` and the repo's git workflow lets you (`commit` in `GIT_AGENT_MAY`), with `gitflow commit` (see `git-workflow`). With `Commits: ask` (the default), leave it for the human's commit gate.
5. `tasks set <slug> <id> done [commit-sha]`
6. `tasks log <slug> "<what changed, anything surprising, what's next>"`

If a task turns out wrong or too big, record the decision in plan.md and add or reshape tasks. Don't silently drift from the ledger.

## Session hygiene
- End sessions at task boundaries: ledger updated, verify green, then start fresh. A fresh session with a good ledger beats a long compacted one.
- Before stopping mid-task, `tasks log` exactly where things stand and the next concrete step.
- Waiting on the human: `tasks ask` records the question in the plan's question ledger and pauses the task; `tasks answer` records the decision and resumes it. `tasks questions <words>` searches every answer given so far, across all plans.
- When all tasks are done: `Status: done` in plan.md and a final log line.

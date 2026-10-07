# Working files

Where agents (and you) put the files made on the way to the real change, so the repo tree stays clean. Nothing here is committed: this folder's `.gitignore` keeps everything but this README out of git.

- `scratch/<slug>/`: working files for one piece of work: repro scripts, logs, notes, drafts, review notes. `<slug>` is the plan's slug (`.agents/plans/<slug>/`) or a short name when there's no plan.
- `scratch/_done/<slug>/`: where `tasks` moves a plan's folder when its last task is done. Reopening the plan (`tasks add`, or `tasks set` back to todo or doing) moves it back. Clear `_done/` by hand when you like.
- `scripts/`: scripts worth reusing across tasks. They're yours, not the team's.
- `references/`: material from outside: vendor docs, API dumps, ticket text, specs.
- `reports/`: finished write-ups for people: investigations, comparisons, pilot results.
- `requirements/`: requirement exports and drafts.

Promote what the team needs: a script into a tracked place in the repo (`scripts/`, `tools/`), a report into your docs or Confluence. Then it's reviewed and committed like any other change.

When a turn leaves new untracked files outside this folder, the stop gate names them (each once, at most one reminder a turn, never while verify has findings) and asks the agent to move any scratch here and say why the rest belong in the repo. `WORK_REMIND="off"` in `.agents/harness.conf` turns that off.

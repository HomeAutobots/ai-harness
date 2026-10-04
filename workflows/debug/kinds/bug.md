---
kind: bug
steps: intake reproduce gather-evidence hypothesize isolate root-cause check-in
bindable: intake reproduce gather-evidence isolate
---

# Bug report

Someone saw the software do the wrong thing. Go through the steps in order the first time; 3 to 5
loop until a hypothesis holds. For each step with a playbook section, read that section of the
playbook first and use its bindings; what's below is the fallback when the section is empty, and
the rule either way. The `debug` skill has the shared rules (evidence, experiments, the check-in).

| # | Step | Playbook section | Produces |
|---|---|---|---|
| 1 | intake | `intake` | `report.md`: the report as received, expected and actual pulled out |
| 2 | reproduce | `reproduce` | at least one attempt with its outcome recorded |
| 3 | gather-evidence | `gather-evidence` | evidence entries tied to the report's symptoms |
| 4 | hypothesize | (judgment) | `hypotheses.md` |
| 5 | isolate | `isolate` | each hypothesis confirmed or ruled out, citing E-ids |
| 6 | root-cause | (judgment) | `root-cause.md` |
| 7 | check-in | (none) | the human approves or rejects |

## 1. intake
- With a binding (a ticket CLI such as `gh issue view <n>`, `glab issue view <n>`, a Jira CLI, or
  an MCP server a `context:` doc names), fetch the ticket, comments included, and put it under
  "As received" unchanged. Run a command binding through `debug run intake -- ...` so the fetch is
  evidence too.
- If the binding fails (the tool is missing, you aren't logged in), don't install or log in: the
  failed run is the record. Ask the human to paste the report, and name the gap in your report.
- Without one, the text `debug start` saved under "As received" is the record. If there's none,
  ask the human to paste it.
- Fill in Expected and Actual, plus the version, environment, and steps the reporter gave.
  Anything unclear goes under "Unclear" and to the human
  (`.agents/bin/tasks ask <slug> T1 '<question>'`).

## 2. reproduce
- Follow the reporter's steps as literally as the repo allows: the same input through a test, the
  CLI with the same arguments, the app or simulator the playbook names, at the version reported if
  it differs from `HEAD` (say so if you can't get it).
- An old version: never check it out in place, since the session follows the branch. Use a
  worktree outside the repo: from the repo root, `git worktree add --detach ../repro-<ver> <ver>`,
  run it there with
  `debug run reproduce --attempt=reproduce -- bash -c 'cd "$(git rev-parse --show-toplevel)/../repro-<ver>" && <command>'`
  (`debug run` starts in the project root, which can be a subdirectory of the repo; then `cd` into
  that subdirectory of the old checkout too), and remove it after, from the repo root
  (`git worktree remove --force ../repro-<ver>`). The entry's `head:` is the branch's commit, so
  name `<ver>` in your notes.
- Record it: `debug run reproduce --attempt=reproduce -- <command>`, then
  `debug outcome E-<n> reproduced|partial|not-reproduced`. Partial means some of the symptoms.
- At least one attempt with its outcome recorded is required; a reproduction isn't. If it won't
  reproduce, note what differs from the report (environment, data, timing) and move on to evidence.

## 3. gather-evidence
- Collect what the symptoms point at: logs, the failing output, the code path from the entry point
  the report names, recent history of that code (`git log -L`, `git log -S`, `git blame`), config.
- Every item worth citing is a `debug run gather-evidence -- <command>` entry. Reading code is
  fine without one; cite it as `path:line` instead.
- Tie each entry to a symptom in report.md. Stop when you have enough to form hypotheses, not when
  you've read everything.

## 4. hypothesize
- Write `hypotheses.md`: every cause that fits the evidence, not just the first. For each, what
  observation would confirm it and what would rule it out, before you test it.
- Order them by how cheap they are to test and how well they fit.

## 5. isolate
- Test one hypothesis at a time against its confirm and rule-out conditions: a focused test, a
  bisect, temporary logging or an assert, a smaller input. Each result is an E-id; set the
  hypothesis's status with it.
- To bisect, with no tracked changes at all (`git status --untracked-files=no` prints nothing),
  run the whole bisect inside one entry, so HEAD is back on the branch when it ends:
  `debug run isolate -- bash -c 'git bisect start <bad> <good> && git bisect run <test>; rc=$?; git bisect reset; exit $rc'`.
  Keep `<test>` out of the project tree (a script in the session dir works: git ignores it and
  `DEBUG_SCOPE` leaves it out), and don't call harness tools from it: in team mode each step checks
  out that commit's `.agents/`, or none.
- Experiments are fine until `root-cause.md` exists; keep track of them so you can revert them.
  Don't put one in a file that already had uncommitted changes. If you must, undo your own lines
  by hand; never `git checkout` that file.
- A ruled-out hypothesis sends you back to step 3 or 4. When one holds, trigger the bug through
  that cause on purpose: `debug run isolate --attempt=confirm -- <command>` and its outcome. It
  raises confidence; it never blocks.

## 6. root-cause
- Revert your experiments first. `debug status` lists every uncommitted change in scope, yours or
  not: one that was there before the session is the human's, so ask them to commit or stash it,
  never revert it.
- Write `root-cause.md` with the fixed headings (the `debug` skill lists them), citing only E-ids
  and H-ids that exist, and the Confidence line `debug status` computes.
- Validate it with the `validate` skill before the check-in.

## 7. check-in
- With `rootcause` in `DEBUG_ASK`: ask the human to run `.agents/commands/debug approve <slug>`,
  with the `tasks ask` line the `debug` skill gives, and wait. A rejection reopens the session at
  hypothesize, with the reason at the end of hypotheses.md; go back to step 3 or 4.
- With `DEBUG_ASK` empty: `debug close <slug> reviewed` once the validate review passes.
- An approved session is closed. Don't edit root-cause.md after that: any change reopens the
  session for a new approval. The fix starts from the fix direction, in the repo's own process.

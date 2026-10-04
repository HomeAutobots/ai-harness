---
name: debug
description: Investigates a bug report to a root cause the human approves, following the debug workflow's steps with this repo's own tools from the debug playbook. Captures every run as evidence, records reproduction attempts and their outcomes, tests hypotheses, and writes root-cause.md for the check-in. Use when asked to investigate a reported bug, issue, or ticket, or to find its root cause. It investigates; it doesn't fix.
---

# Debug a reported bug

You investigate; you don't fix. A session ends at a root cause the human approves, and the fix goes through whatever process the repo uses, starting from your fix direction. `verify` enforces the mechanics (root-cause format, cited evidence exists, experiments cleaned up, no commits during a session, approvals only the human records). This skill covers the judgment.

## The session
- Start: `.agents/commands/debug start bug <ref>` with a ticket key (`PROJ-123`), `'#<n>'` for a GitHub or GitLab issue (quoted, or the shell reads a comment), or `-` with the report on stdin (or `--file=<path>`). It prints the session slug, `steps: <path>` (the steps file to follow), and a plan (`.agents/plans/<slug>`, task T1) to ask the human in.
- Where you are: `.agents/commands/debug status` (the step, the steps file, evidence, attempts and outcomes, computed confidence, hypotheses, experiments still in the tree, steps the playbook doesn't cover). Run it first whenever you pick up a session, and note any uncommitted changes already in the tree: they're the human's, not experiments. The current session is the newest open one started on this branch; starting another leaves the older one open, so finish it, or ask the human whether to close it.
- The steps: read the file `steps:` names. For each step, read its section of the playbook (`.agents/debug/playbook.md`, or `playbook.md` in `DEBUG_DIR`) first and use its bindings: `skill:` (use that skill), `run:` (fill in the `<placeholders>` and run it through `debug run`; a line with `&&`, `|`, `;` or a redirect goes inside `bash -c '<line>'`), `context:` (read it first). An empty section means the steps file's generic guidance. If the playbook lacked a binding you needed, say so in your report.
- Order: go through the steps in order the first time. Gather evidence, hypothesize, and isolate loop until a hypothesis holds; a ruled-out hypothesis sends you back for more evidence. The CLI tracks the step but doesn't force the order.

## Evidence
- Run everything that tells you something through `.agents/commands/debug run <step> -- <command>`. It records the command, its exit code, and the last 60 lines in `evidence/E-<n>.md`, keeps the full output in `E-<n>.log`, prints the entry ID and the tail, and exits with the command's exit code. Only what `debug run` captured counts as evidence: not something you describe, and not an entry you write by hand.
- The command gets no stdin, so nothing interactive. Shell syntax goes inside one argument: `debug run isolate -- bash -c 'make sim && ./sim --scenario s.txt'`. The policy still applies to the command.
- Reproduction: try at least once, `debug run reproduce --attempt=reproduce -- <command>`, then `debug outcome E-<n> reproduced|partial|not-reproduced`. The attempt and its recorded outcome are required; what the outcome is never blocks. Not reproduced is an answer: record it and go on with the evidence.
- Once a hypothesis looks right, try to trigger the bug through that cause on purpose (`debug run isolate --attempt=confirm -- <command>`) and record its outcome too.
- Confidence is computed from those outcomes: `confirmed` (a confirmation attempt reproduced it), `reproduced` (any attempt did), else `evidence-only`. Partial isn't a reproduction. Write the one `debug status` shows.

## Hypotheses and experiments
- `hypotheses.md`: one `## H-<n>: <claim>` section each, with `- would confirm:`, `- would rule out:`, and `- status: open`, `confirmed (E-<n>)`, or `ruled out (E-<n>)`. Decide what would confirm or rule a hypothesis out before you test it.
- Experiments (temporary logging, asserts, a tweak, a reproduction test) are fine until `root-cause.md` exists. After that, nothing uncommitted may stay in `DEBUG_SCOPE` outside `.agents/`: revert each of your experiments (`verify` says how, per file) and say in `root-cause.md` what it showed. Never revert a change that was there before the session: ask the human to commit or stash it. A reproduction test belongs in the fix, so describe it under Reproduction rather than keeping it. Don't put an experiment in a file that already had uncommitted changes. If you must, undo your own lines by hand; never `git checkout` that file.
- Never move HEAD off the branch: the session follows it. Bisect inside one `debug run`, and run an old version from a worktree outside the repo (the steps file says how).
- Never commit during a session. If the human wants the fix now, the session ends first (approve, or `debug close`), then the fix follows the repo's own process.

## Root cause and check-in
- Write `root-cause.md` in the session dir with these headings, in this order:
  - `## Summary`: one paragraph a ticket reader understands.
  - `## Cause`: what's wrong and why, with `path:line` for the code involved.
  - `## Evidence`: the E-ids that support it.
  - `## Reproduction`: the attempts by E-id, then `Confidence: confirmed|reproduced|evidence-only`.
  - `## Ruled out`: the H-ids you rejected, and why.
  - `## Fix direction`: what the fix should do, no code.
- Validate it with the `validate` skill: does the evidence support the cause, would the fix direction address the report, is anything claimed that no E-id shows?
- If `DEBUG_ASK` in `.agents/harness.conf` has `rootcause` (the default), the human approves. You can't (`debug approve` and `debug reject` refuse in your shell, and a line you write into `approvals` yourself doesn't count). Ask with one line in single quotes, no backticks, words first: `.agents/bin/tasks ask <slug> T1 --gate=impl --force 'Root cause ready. Please run: .agents/commands/debug approve <slug>'`, and wait. `--force` lets you ask again after a rejection.
- A rejection puts the reason at the end of `hypotheses.md`, sets the old root cause aside as `root-cause.rejected-<n>.md`, and sends the step back to hypothesize. Look again from there: experiments are allowed again, and confirmation attempts made before the rejection no longer count toward `confirmed`.
- If `DEBUG_ASK` is empty, the validate review is the check-in: `.agents/commands/debug close <slug> reviewed` runs the same checks the human's approve would.
- No root cause (the report is a duplicate, or the human drops it): revert your experiments, then `debug close <slug> abandoned|duplicate <note>`. Once a root cause waits on the human, only they can close the session.
- Questions only the human can answer (an unclear report, access, which environment): `.agents/bin/tasks ask <slug> T1 '<question>'`. Never guess.

## Report
The session slug, the root cause in two lines with its confidence, the evidence that carries it, and anything the playbook should have covered but didn't. If a simulated human is on (`debug status` or `verify` says so), another agent plays the person here; nothing changes for you, and its switch and token aren't yours to touch.

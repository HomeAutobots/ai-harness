# Workflow pack: debug

A debugging process the agent follows like a skeleton, filled in with this repo's own tools.
Installed with `install.sh --workflow debug <project>`. The harness owns the steps and the checks;
the project owns how each step works here (its simulator, its log locations, its ticket tool) in a
playbook.

The workflow investigates; it doesn't fix. A session ends at a root cause you approve, and the fix
goes through whatever process the repo uses (feature-driven, req-driven, or none), starting from
the root cause's fix direction. It ships four kinds of debugging, each its own workflow: bug
reports (`bug`), failing tests or CI (`test`), crashes and hangs (`crash`), and field or
production issues (`field`).

## What it adds
- **Skill** `debug` (harness-owned): the shared rules for evidence, experiments, and the check-in.
- **Steps** in `kinds/<kind>.md` (harness-owned): `bug.md`, `test.md`, `crash.md`, and
  `field.md`, each a skeleton with generic guidance for every step. `debug start` and
  `debug status` print the session's (`steps: ...`).
- **`debug`** at `.agents/commands/debug`: `start`, `run`, `outcome`, `status`, `close`, and
  `approve` / `reject` (people only, in their own terminal).
- **Checks** that `verify` runs after the project's own tier scripts.
- **Settings** appended to `.agents/harness.conf` (`DEBUG_*`).
- **Policy rules** appended to `.agents/policy.conf`: agents can't run `debug approve` or
  `debug reject`, or turn on the simulated human (`--simulated-human`,
  `.agents/lib/approvals.py simulated-human`).
- **`.agents/debug/playbook.md`** (project-owned, created once, harness-tailor drafts it) and
  **`.agents/debug/.gitignore`**, which keeps `sessions/` local.

## The playbook
One `## <step>` section per step that takes bindings (`intake`, `reproduce`, `gather-evidence`,
`isolate`), each a list:

```
## reproduce
- skill: run-device-sim
- run: make sim && ./sim --scenario <file>
- context: .agents/context/simulator.md
```

- `skill:` resolves the usual way (project, libraries, personal, built-in), so shared debugging
  skills can live in a library.
- `run:` is a command line whose `<placeholders>` the agent fills in. It runs through `debug run`,
  so the output is evidence. The checks never run it.
- `context:` is a doc to read first, repo-relative and inside the repo.

Plain lines are notes (say, why a command can't run here), and so is anything in a ``` or ~~~
fence. Every workflow reads the same sections, so a plain line like `For field issues:` above
some bindings says which workflow they're for, and an agent uses the bindings that fit its session's
workflow. A section with none that fit means the generic guidance in the session's kind file;
`debug status` lists the steps with no bindings. Like `.agents/context/`, the playbook is committed
in team mode and stays local in local mode.

## The workflows
| Kind | For | Steps | What's different |
|---|---|---|---|
| `bug` | someone saw the software do the wrong thing | intake, reproduce, gather-evidence, hypothesize, isolate, root-cause, check-in | the base workflow |
| `test` | a test fails, here or in CI | the same | intake pulls the failed CI log; reproduce runs the one test several times and counts (some failing is `partial`); isolate finds the last green and first red commits; "the test is wrong", "flaky, from this source", and "the environment" are root causes too |
| `crash` | it crashes or hangs | the same | a hang is reproduced with `--timeout` (exit 124 is a reproduced hang); evidence is a symbolized stack, a core file read in batch mode, or a thread dump; the root cause names the faulting `path:line` and the bad state |
| `field` | it went wrong where it runs for real, and that system can't be run here | intake, gather-evidence, reproduce, hypothesize, isolate, root-cause, check-in | evidence comes first, through the playbook's bindings (where scrubbing lives); `partial` and `not-reproduced` are the usual outcomes, `evidence-only` an accepted confidence, and the fix direction says what logging would confirm it |

The step names are shared, so one playbook binding serves every workflow that has the step. The
shared rules hold in all four: a reproduction attempt with its outcome is required and never
blocks, confidence comes from the attempts, and experiments are gone before the check-in.
`DEBUG_KINDS` turns workflows off.

## Sessions (in `DEBUG_DIR/sessions/<slug>/`, local, never committed)
| File | What |
|---|---|
| `report.md` | the report as received, then Expected, Actual, and Unclear |
| `evidence/E-<n>.md`, `E-<n>.log` | one per `debug run`: step, attempt and outcome, command, exit code, the commit it ran on, `timed out:` and `masked:` when they apply, the last 60 lines; the full output |
| `hypotheses.md` | `## H-<n>: <claim>` with what would confirm it, what would rule it out, and its status |
| `root-cause.md` | the check-in document, fixed headings: Summary, Cause, Evidence, Reproduction (with `Confidence:`), Ruled out, Fix direction |
| `root-cause.rejected-<n>.md` | a root cause you rejected, set aside |
| `state` | kind, ref, start commit, branch, `seq` (start order), step, status, close note, `confirm_after` |
| `approvals` | written by `debug approve`, `reject`, and `close`; each line also recorded in the git dir (`.git/ai-harness/debug-approvals`) |

`debug start` also records where the session started (its branch, `HEAD`, and the local branch
tips) in that same git-dir record, rather than in `state`, which the agent can edit. It opens a plan named after
the session (`.agents/plans/<slug>`, task T1 in progress), so the agent's questions and the
check-in request (`tasks ask`) pause the stop gate. `debug approve` answers the open check-in
question and sets T1 done.

## The command
- `debug start <kind> <ref | -> [--file=<path>]`: `kind` is one of the workflows `DEBUG_KINDS`
  turns on. `ref` is a ticket key (`GIT_TICKET` from the project's `.agents/git.conf`, else
  `PROJ-123` style), `'#<n>'` for a GitHub or GitLab issue (quoted, or the shell reads a comment),
  or `-` for a report on stdin. A failing test's id, a CI run's URL, or a crash log go in that way.
  `--file=<path>` reads the report from a file, relative to where you run it. The slug is
  `<kind>-proj-123`, `<kind>-42`, or the kind plus the report's first words, with `-2`, `-3` on a
  clash. Refused outside a git repo.
- `debug run <step> [--attempt=reproduce|confirm] [--timeout=<sec>] -- <command...>`: runs it from
  the project root with no stdin, in a process group of its own, captures it as the next evidence
  entry, prints `E-<n> (<step>, exit <code>): <path>`, any `timed out:` or `masked:` note, and the
  tail, and exits with the command's exit code. The step is any step of the session's kind. Shell
  syntax goes in one argument: `debug run isolate -- bash -c 'make sim && ./sim'`. Past
  `--timeout` (else `DEBUG_RUN_TIMEOUT`, 600 seconds; `0` means no limit) the whole group gets
  SIGTERM, then SIGKILL 3 seconds later; the entry is still recorded, with `exit: 124` and
  `timed out: after <n>s`, and `debug run` exits 124. Ctrl-C, SIGTERM, or SIGHUP to `debug run`
  stops the group the same way and records the entry (exit 128 plus the signal). A command still
  there 3 seconds after SIGKILL (stuck in the kernel) is left, and the entry says
  `still running: the command didn't stop`. A `--timeout` that isn't a whole number of seconds
  prints why and the usage and exits 2 before anything runs.
- `debug outcome E-<n> reproduced|partial|not-reproduced`: records an attempt's outcome (again to
  change it).
- `debug status [slug]`: kind, step, steps file, evidence, attempts and outcomes, computed
  confidence, hypotheses, the root cause, experiments still in the tree, unbound steps, and any
  `approvals` lines that don't count. With no current session, it names the open ones elsewhere.
- `debug approve <slug>` and `debug reject <slug> <why>`: yours. Approve runs the check-in set
  first and refuses on any finding (`debug: fix these before approving <slug>`). It records the
  hash of `root-cause.md`, so editing the file afterwards reopens the session for a new approval.
  Reject records the reason at the end of `hypotheses.md`, sets `root-cause.md` aside as
  `root-cause.rejected-<n>.md`, and sends the session back to hypothesize.
- `debug close <slug> abandoned|duplicate|reviewed [note]`: end a session without your approval.
  `abandoned` and `duplicate` are refused while experiments are in the tree. `reviewed` closes a
  root cause the agent reviewed with `validate`, only while `DEBUG_ASK` is empty, after the same
  check-in set approve runs. Once a root cause waits on you, a shell Claude Code, Gemini CLI, or
  Cursor started can't close it (Known gaps below). It waits on you only while `DEBUG_ASK` has
  `rootcause`: with `DEBUG_ASK` empty, the agent may close a rejected root cause too.
- Wrong arguments print the usage and exit 2. `--help` alone prints usage (`debug <command>
  --help` for one); an unknown option, or `--help` among other words, exits 3 before anything
  changes. Text for `reject` and `close` that starts with `-` goes after `--`.

The current session is the newest open one (by `seq` in `state`) started on the current branch (on
a detached HEAD, the newest started detached). The branch comes from the start record, never
`state`. Several open sessions are
fine, one per branch or ticket; starting a new one on the same branch says the older one is still
open. A session is approved or closed only by a line `debug` recorded; approved and closed
sessions are quiet.

## Confidence
Computed from the recorded attempts, and `root-cause.md` must state the same:
- `confirmed`: a confirmation attempt (`--attempt=confirm`) reproduced the problem through the stated cause.
- `reproduced`: an attempt of either kind reproduced it.
- `evidence-only`: neither; the cause rests on evidence and analysis. A partial reproduction counts here.

After a rejection, confirmation attempts made so far no longer count toward `confirmed` (they were
for the rejected cause); they still count as reproductions. Reject writes that cutoff to `state`
(`confirm_after`).

## Checks
"Check-in" is the set `debug approve` and `debug close reviewed` run before they record anything.
Everything below is quiet unless the current session is open, except the playbook check (judged
with or without a session: when it's edited, and on every full run), the approval checks (every session's `approvals`), and
`debug-committed` after a branch switch.

| Tier | Finding | Meaning |
|---|---|---|
| check-in, full | `debug-no-repro-attempt` | `root-cause.md` exists, but no reproduction or confirmation attempt has its outcome recorded. Attempts from before a rejection count. |
| edit (`root-cause.md`), turn, full, check-in | `debug-format` | headings missing (one finding names them all), no `Confidence:` line under `## Reproduction`, or one the attempts don't support; any other `Confidence:` line that disagrees (up to 3); a fence that never closes is named |
| edit (`root-cause.md`, `hypotheses.md`), turn, full, check-in | `debug-evidence-missing` | `root-cause.md` cites an E-id `debug run` didn't capture or an H-id `hypotheses.md` doesn't have, or a `## H-<n>` section cites a missing E-id |
| turn, full, check-in, once `root-cause.md` exists | `debug-experiments-left` | an uncommitted change (staged, unstaged, or a new file git doesn't ignore) in `DEBUG_SCOPE`, outside `.agents/` and `DEBUG_DIR`; the fix says how to revert each file |
| turn, full (with `--since`) | `debug-committed` | a commit in the turn that changes files in `DEBUG_SCOPE`, outside `.agents/` and `DEBUG_DIR`, while the session is open |
| edit (playbook), full | `debug-playbook-format` | a section that isn't a step, a list item that isn't a binding, an empty binding, a `context:` outside the repo or missing, a `skill:` that doesn't resolve, a fence that never closes |
| edit (that `approvals`), turn, full | `debug-approval-unrecorded` | an `approvals` line `debug approve`, `reject`, or `close` didn't record, or another session's (in another worktree, or an earlier session with the same slug); it doesn't count, and it's a policy block (exit 2) |
| edit (that `approvals`), turn, full | `debug-approval-simulated` | a line a simulated human made, while the switch is off; it doesn't count, and it's a policy block |
| turn, full, while there are sessions | `debug-simulated-human` | a simulated-human switch that doesn't count (written by hand, or flagged by the hooks); a policy block |

Details:
- **Evidence** is an `E-<n>.md` with the `command:` and `exit:` lines `debug run` writes. A note
  written by hand without them isn't evidence, and neither is a symlink.
- **Citations** in fenced code don't count; ids in code spans do. In `hypotheses.md`, only the
  `## H-<n>` sections cite, so your reject reasons and other notes are never findings. One finding
  per missing id per file, at the first line that cites it, with the others listed.
- **Caps.** `verify` shows 5 findings per file, so past 5 the fifth names the rest. The checks cap
  the list themselves, so `debug approve` prints the same one. `debug-committed` stops looking after 13 commits that change
  project code.
- **Headings** in `root-cause.md` match with any case, extra spaces, or a trailing colon
  (`## Ruled out:`), up to 3 leading spaces. In the playbook, sections are exact lowercase step
  names.
- **Experiments sync made.** What sync writes outside `.agents/` during a session isn't an
  experiment: the agent and skill renders `.agents/generated.lock` lists, `.claude/skills/` link
  mirrors, the files it renders whole (`.github/hooks/harness.json`, `.codex/rules/harness.rules`),
  and `AGENTS.md` or `CLAUDE.md` when only its own parts changed. Everything else
  is judged, including configs it merges into yours (`.claude/settings.json` and the like).
- **Commits after a branch switch.** With no current session, a commit still counts when it
  descends from where an open session started and the turn started on that session's branch, so
  branching off mid-session doesn't hide the work. The finding names the session.
- **The playbook's skills.** A `skill:` that doesn't resolve while a library `LIBRARIES` lists
  isn't here is a tooling problem (exit 3), not a finding, since the skill may be in it.

## Settings
| Key | Default | What |
|---|---|---|
| `DEBUG_DIR` | `.agents/debug` | the playbook and `sessions/`, repo-relative or absolute; empty means the default |
| `DEBUG_KINDS` | `bug test crash field` | the workflows `debug start` accepts; empty means it starts none (open sessions keep running and being checked) |
| `DEBUG_SCOPE` | `**` | where experiments must be gone before the check-in, and where commits count (space-separated globs); empty turns both checks off |
| `DEBUG_ASK` | `rootcause` | check-ins that need you; empty means the agent's `validate` review is the check-in |
| `DEBUG_RUN_TIMEOUT` | `600` | seconds `debug run` gives a command before it stops it; `0` means no limit, empty means the default; `debug run --timeout=<sec>` wins for one run |

A missing key means its default, so an install from before a key behaves the same. An install
from before the test, crash, and field workflows keeps the `DEBUG_KINDS="bug"` line it got then
(harness.conf is yours, never rewritten): add `test crash field` to it to turn them on. A value
this pack doesn't know (`DEBUG_KINDS="perf"`, `DEBUG_ASK="design"`, `DEBUG_RUN_TIMEOUT="soon"`) is
a tooling problem (`infra:`, exit 3). Settings come from `.agents/harness.conf` only: `DEBUG_*` environment variables are
ignored, unlike `FDD_*`. A change to `DEBUG_*` during an agent turn gets a note from the stop gate,
like `WORKFLOWS` and `FDD_*`. A `DEBUG_DIR` other than the default doesn't get the seeded
`.gitignore`, so keep its `sessions/` out of git yourself.

## Notes
- **Nothing configured, nothing happens.** No session, no findings. An empty playbook means the
  generic steps. No intake tool means the pasted report is the record.
- **It needs git.** The start commit, the experiments check, and the approval record all come from
  git, so `debug start` refuses outside a repo.
- **Approve in your own terminal.** The same rules as `fdd approve`, from the harness's shared
  `.agents/lib/approvals.py`, with a record of its own (`.git/ai-harness/debug-approvals`): `debug approve` and `reject` refuse in a shell Claude Code,
  Gemini CLI, or Cursor started, the policy blocks the usual spellings for agents, and only lines
  they recorded in the git dir count. Each line names the session it was made for (its slug,
  worktree, and session dir) and counts only when `debug` recorded it after that session started,
  so a line copied from another session approves nothing. `install.sh --simulated-human` works
  here as it does for feature-driven: a shell with its token may approve, approvals are marked
  simulated, `debug status` starts with `simulated human: on (...)`, and `verify` adds a note while
  a session is open. With feature-driven also installed, a switch that doesn't count is reported
  by both packs (`fdd-simulated-human`, and `debug-simulated-human` while debug has sessions); the
  fix is the same.
- **Closes are recorded too.** `debug close` writes its line like an approval, so editing `state`
  closes nothing. An agent's `abandoned` or `duplicate` close is recorded as `close-agent` and
  stops counting if its root cause later waits on you (`DEBUG_ASK` has `rootcause`, and
  `root-cause.md` exists, or you rejected it, or edited it after approving). `close` refuses an
  agent by the same rule. A `reviewed` close counts only while `DEBUG_ASK` lacks `rootcause`.
- **Experiments end at the root cause.** Until `root-cause.md` exists, the agent may change code
  to learn something. After that, nothing uncommitted may stay in `DEBUG_SCOPE`, your own
  uncommitted work included: commit or stash it (`git stash -u`) before the check-in. If the tree
  has generated files git doesn't ignore, narrow `DEBUG_SCOPE` (`DEBUG_SCOPE="src/** tests/**"`).
- **`debug run` keeps the policy.** The command is checked against `.agents/policy.conf` first
  (`.agents/bin/policy test`), so wrapping a command doesn't get around a `deny-cmd`: a blocked one
  isn't run (exit 2, `debug: not run: ...`). `AGENTS_HOOKS` set on the `debug` command doesn't turn
  that check off; `HOOKS` in `.agents/harness.conf` without `policy` does. A policy test that's
  missing or fails is a tooling problem and the command doesn't run. `E-<n>.log` keeps all of the
  output, uncut. Since the exit code is the command's, a 2 or 3 can come from it too: a run that
  happened prints its `E-<n> (...)` line first.
- **Secrets in captured output are masked.** Before `debug run` writes the entry, it replaces
  what guard's secret rules match (`.agents/core/guard.patterns` and your `.agents/guard.patterns`,
  the `secret` lines) with `[masked]`, in the log, the tail, and the command line (each argument
  on its own: `env 'DB_PASSWORD=[masked]' make test`), the way guard redacts a line it prints:
  `export API_TOKEN=[masked]`. A private key is masked from its `BEGIN` line through its `END`
  line, or from `BEGIN` to `END` when it's on one line (JSON's `\n`), as one secret. The entry then
  says `masked: <n> possible secrets`, and so does `debug run`. Placeholders guard skips (`${TOKEN}`,
  `your-key`) stay. With nothing matched, the log is byte for byte what the command wrote. A
  rule in your `.agents/guard.patterns` that can't be turned into a Python regex (`[[:punct:]]`,
  say) isn't used for masking, and the entry and `debug run` say so:
  `not masked: 1 secret rule debug run can't use (<what>)`.
  Customer data (ids, emails) isn't a secret shape: scrub it in the playbook's `gather-evidence`
  binding (`run: scripts/pull-logs.sh <id> 2>&1 | scripts/scrub`, run as
  `bash -c 'set -o pipefail; <line>'` so errors are scrubbed and a failed pull isn't exit 0). A
  harness without `.agents/lib/guard_shapes.py` or without secret rules in
  `.agents/core/guard.patterns` (re-run `install.sh`) makes `debug run` refuse (exit 3) rather than
  capture unmasked output. A raw `E-<n>.log` with no `E-<n>.md` (a `debug run` killed with SIGKILL
  before it masked it) and a stale `E-<n>.log.masking` are deleted by the next `debug run` in that
  session, unless another `debug run` there is still going (`evidence/.lock`).
- **A time limit on every run.** A long bisect needs `--timeout=0` (the steps files say so): a
  limit that stops it mid-way also stops it before `git bisect reset`, and HEAD stays mid-bisect
  until you run `git bisect reset`. Keep a limit under the agent tool's own command timeout
  (Claude Code's Bash tool stops a command after 2 minutes unless asked for longer), so `debug run`
  stops and records the command before the tool gives up on `debug run`. The command has no
  controlling terminal (its own session), so anything that prompts on `/dev/tty` fails rather than
  waiting. Processes it leaves running when it exits normally keep running; what they write later
  isn't in the log.
- **Old versions and bisect stay off the branch.** The session follows the branch, so the steps
  file has the agent bisect inside one `debug run` and run an old version from a worktree outside
  the repo.
- **With other workflows.** feature-driven and req-driven judge every change in their scope, so
  an experiment in `src/` during a debug session also draws `fdd-untraced` or `req-untraced` at the
  stop gate. Keep experiments small and revert them as you go, or pause with `tasks ask` while one
  is in place.
- **Patterns are Python regexes.** `GIT_TICKET` is read from the project's `.agents/git.conf`
  only (not a personal one) and must match the whole ref. An invalid one is a tooling problem
  when `debug start` needs it; `#<n>` and `-` still work.
- **Moving the clone** keeps its sessions: the record names worktrees and session dirs relative to
  the repo.
- **Known gaps.**
  - Masking works line by line, so a secret split across a line break isn't caught (a private
    key is the exception: everything from its `BEGIN` line to its `END` line is masked, and a key
    with no `END` masks the rest of the log). It knows only the shapes guard knows, so a `set -x`
    trace, a debug dump of an HTTP request (`Authorization: Bearer ...`), or a URL with a password
    in it gets through unless the value has a shape guard knows. The output sits unmasked in
    `E-<n>.log` while the command runs, until `debug run` rewrites it; a `debug run` killed before
    then (SIGKILL) leaves it that way until the next `debug run` in that session deletes it.
  - Like guard, a value after a key name counts as a secret only when it mixes letters and
    digits, so `DB_PASSWORD=correcthorsebatterystaple` or an all-digit PIN isn't masked.
  - Masking a huge log takes time after the command ends (about 1 second per 12 MB, slower with
    text that isn't ASCII). An agent tool whose own timeout kills `debug run` while it masks
    leaves the raw log (as SIGKILL does, above). `E-<n>.log` has no size cap yet (roadmap row 66).
  - A `debug run` killed with SIGKILL can't stop its command, which keeps running in its own
    process group; SIGINT, SIGTERM, and SIGHUP are passed on. An agent tool that kills its whole
    process group on its own timeout reaches `debug run` but not the command. A process that leaves
    the group (`setsid`, a container) isn't stopped by the time limit either.
  - Masking goes by newline. A rule anchored at a line's start doesn't see text a progress bar's
    `\r` puts there, though the entry's tail shows it as its own line. And `report.md` holds the
    report as pasted, so a CI log pasted into `debug start test -` isn't masked.
  - An evidence file with `command:` and `exit:` lines counts whether `debug run` wrote it or the
    agent did; the check sees the entry, not who made it.
  - Approvals are tamper-evident, not tamper-proof (README, Known gaps): any process that can
    write the git dir can append to `.git/ai-harness/debug-approvals`. `env -u CLAUDECODE` (or
    `CLAUDECODE=`) before `debug close` records a plain close that counts even while a root cause
    waits on you; `approve` and `reject` also have the policy patterns, `close` doesn't.
  - A session belongs to the branch it started on. After a switch, its session checks go quiet
    there (`debug-committed` still watches commits made from its start, as above). Renaming that
    branch (`git branch -m`) or deleting the session dir hides its commits and silences its checks.
    A turn that starts on a brand-new branch with no commits of its own yet looks like a turn on
    the session's branch, so the session watches its commits.
  - A session dir made by hand has no start record, so it's never current; `debug status` shows it
    as `no start record`. Which of a branch's open sessions is newest comes from `seq` in `state`,
    so editing it changes which one is current.
  - In team mode, harness files outside `.agents/` that sync merges or writes and that aren't
    committed yet (`.claude/settings.json`, `.cursor/hooks.json`, a new `AGENTS.md` or
    `CLAUDE.md`) count as experiments until committed. Set `DEBUG_SCOPE` to the project's code paths.
  - During a merge, a file both sides added (conflicted, not in `HEAD`) gets
    `git checkout HEAD -- <path>` as its fix, which fails. Finish or abort the merge first.
  - `confirm_after` sits in `state`, which the agent can edit. That's accepted: the outcomes it
    judges are agent-recorded too.
  - The stop gate runs `verify` only when a turn changed the project's tree. Session files are
    gitignored (and all of `.agents/` is hidden in local mode), so a turn that only writes them,
    with its experiments reverted, stops without a `verify` run and without the "Paused for your
    input" note for the check-in. `debug-format`, `debug-evidence-missing`, and
    `debug-no-repro-attempt` then run when the agent runs `verify` (`--tier=full` for
    `debug-no-repro-attempt`), and always at `debug approve` and `close reviewed`, which refuse on
    any finding.
- **python3.** The checks and the command need it; a missing python3 is a tooling problem.
  `debug run` also needs a POSIX system (process groups): on a Python without them (native
  Windows) it refuses with exit 3, and the checks and the other commands still work.

---
kind: test
steps: intake reproduce gather-evidence hypothesize isolate root-cause check-in
bindable: intake reproduce gather-evidence isolate
---

# Failing test or CI

A test fails, here or in CI. Go through the steps in order the first time; 3 to 5 loop until a
hypothesis holds. For each step with a playbook section, read that section first: the playbook is
shared by every workflow, so use the bindings that fit this one (a plain line like
`For field issues:` above some says which workflow they're for); what's below is the fallback when
none fit, and the rule either way. The `debug` skill has the shared rules (evidence, experiments,
the check-in).

| # | Step | Playbook section | Produces |
|---|---|---|---|
| 1 | intake | `intake` | `report.md`: the failing test's id and the CI job or run; the failed job's log as evidence (`debug run intake`) |
| 2 | reproduce | `reproduce` | the one test run here, several times, with its pass and fail count |
| 3 | gather-evidence | `gather-evidence` | the CI and local environments side by side, and the failure itself |
| 4 | hypothesize | (judgment) | `hypotheses.md` |
| 5 | isolate | `isolate` | the last green and first red commits; each hypothesis confirmed or ruled out |
| 6 | root-cause | (judgment) | `root-cause.md`: the code, the test, a flaky source, or the environment |
| 7 | check-in | (none) | the human approves or rejects |

## 1. intake
- The failure goes into `report.md` through the ref: a ticket key, `'#<n>'` for an issue (quoted,
  or the shell reads a comment), or `-` with the failing test's id and the CI run's URL on stdin or
  in a file (`--file=<path>`). Not the whole CI log: `report.md` is kept as given, unmasked, and CI
  logs often print tokens.
- With a binding (`gh run view <run-id> --log-failed`, or the CI tool the playbook names), fetch
  the failed job's log through `debug run intake -- ...`, so the log is evidence and what guard's
  secret rules match in it is masked. If the binding fails (the tool is missing, you aren't logged
  in), don't install or log in: the failed run is the record. Ask the human for the log as a file
  and record it with `debug run intake -- cat <file>`, and name the gap in your report.
- Pull out, under "As received" or next to it: the test's id exactly as the runner names it, the
  CI job or run, the commit it ran on, and the failure message. Expected is that it passes;
  Actual is how it fails. Anything unclear goes under "Unclear" and to the human
  (`.agents/bin/tasks ask <slug> T1 '<question>'`).

## 2. reproduce
- Run that one test here, the way CI runs it (the same runner, flags, and selection):
  `debug run reproduce --attempt=reproduce -- <command>`, then
  `debug outcome E-<n> reproduced|partial|not-reproduced`.
- Once proves little for a test that might be flaky. Run it several times inside one entry (the
  number the playbook gives, else 20) and count:
  `debug run reproduce --attempt=reproduce -- bash -c 'p=0; f=0; for i in $(seq 20); do if <command>; then p=$((p+1)); echo "run $i: pass"; else f=$((f+1)); echo "run $i: fail"; fi; done; echo "passed $p, failed $f"; [ "$f" -eq 0 ]'`.
  Every run failing is `reproduced`, some is `partial` (an intermittent failure), none is
  `not-reproduced`. Give it a `--timeout` of about 20 times one run, and ask your tool for a
  longer command timeout than that; if it still hits the limit, the `run <i>:` lines say how far it
  got.
- A pass here is a finding in itself: the difference between CI and here is the next place to
  look. Record it and go on to evidence.
- A test that can hang gets a limit: `--timeout=<sec>` on `debug run` (it exits 124, and the entry
  says `timed out:`).

## 3. gather-evidence
- Record both environments, CI's from its log and config, this machine's with
  `debug run gather-evidence -- ...`: language and tool versions, the OS, the environment variables
  the tests read, parallelism (workers, shards), the test order and the random seed if the runner
  shuffles, the time zone and locale.
- The failure itself: the assertion, the stack, anything the test printed. Its history: when it
  started failing (the CI history, `gh run list --workflow <name>`), and whether other tests fail
  with it.
- Tie each entry to the failure. Stop when you have enough to form hypotheses, not when you've
  read everything.

## 4. hypothesize
- Write `hypotheses.md`: every cause that fits the evidence, not just the first. For a failing
  test that's usually one of: the code changed and broke it, the test is wrong (it always was, or
  it changed), it's flaky (timing, test order, shared state, randomness, the network), or the
  environment differs (a version, a variable, a resource CI lacks). For each, what would confirm
  it and what would rule it out, before you test it.
- Order them by how cheap they are to test and how well they fit.

## 5. isolate
- Find the last green and first red commits: from the CI history, or by running the test at a few
  older commits from a worktree outside the repo. Never check one out in place, since the session
  follows the branch. From the repo root, `git worktree add --detach ../repro-<ver> <ver>`, run it
  there with
  `debug run isolate -- bash -c 'cd "$(git rev-parse --show-toplevel)/../repro-<ver>" && <command>'`
  (`debug run` starts in the project root, which can be a subdirectory of the repo; then `cd` into
  that subdirectory of the old checkout too), and remove it after, from the repo root
  (`git worktree remove --force ../repro-<ver>`).
- Then bisect between them, with no tracked changes at all (`git status --untracked-files=no`
  prints nothing), and run the whole bisect inside one entry, so HEAD is back on the branch when it
  ends:
  `debug run isolate --timeout=0 -- bash -c 'git bisect start <first-red> <last-green> && git bisect run <test>; rc=$?; git bisect reset; exit $rc'`.
  Keep `<test>` out of the project tree (a script in the session dir works: git ignores it and
  `DEBUG_SCOPE` leaves it out), and don't call harness tools from it: in team mode each step checks
  out that commit's `.agents/`, or none. For a flaky test, `<test>` runs it several times and fails
  if any run fails, or the bisect lands on noise. `git bisect run` gives up on an exit code of 128
  or more (a crash), so `<test>` turns any failure into 1, and a test that can hang gets its own
  limit:
  `timeout -k 5 60 <command>; rc=$?; [ "$rc" -lt 125 ] || [ "$rc" -gt 127 ] || exit 255; [ "$rc" -eq 0 ] || exit 1`
  (`gtimeout` on macOS with Homebrew's coreutils). 125 to 127 mean `timeout` failed or couldn't
  find or run the command; 255 stops the bisect, where 1 would call every commit bad. A limit on
  the whole `debug run` stops it before `git bisect reset`. If HEAD is ever left mid-bisect
  (`git status` says so), run `git bisect reset` first.
- At the first red commit, check what changed: the test, the code it tests, or neither (a
  dependency, the CI config): `debug run isolate -- git show --stat <commit>`.
- Order and shared state: run the test alone, then with the tests that ran before it in CI, and
  halve that list until the pair that fails together is left.
- Experiments (a fixed seed, a sleep, an assert, a retry) are fine until `root-cause.md` exists;
  keep track of them so you can revert them. Don't put one in a file that already had uncommitted
  changes. If you must, undo your own lines by hand; never `git checkout` that file.
- A ruled-out hypothesis sends you back to step 3 or 4. When one holds, make the test fail through
  that cause on purpose: `debug run isolate --attempt=confirm -- <command>` and its outcome. It
  raises confidence; it never blocks.

## 6. root-cause
- Revert your experiments first. `debug status` lists every uncommitted change in scope, yours or
  not: one that was there before the session is the human's, so ask them to commit or stash it,
  never revert it.
- Any of these is a root cause: the code is wrong (the test caught a bug), the test is wrong (it
  asserts what the code was never meant to do, or depends on something it shouldn't), it's flaky
  and you name the source (timing, test order, shared state, randomness), or it's the environment,
  not the code (a version, a variable, a resource CI lacks). `## Cause` says which, with
  `path:line`; `## Fix direction` says what to change: the code, the test, its isolation, or the
  environment. A retry isn't a fix direction unless the source is outside the project.
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

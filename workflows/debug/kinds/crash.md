---
kind: crash
steps: intake reproduce gather-evidence hypothesize isolate root-cause check-in
bindable: intake reproduce gather-evidence isolate
---

# Crash or hang

The program dies (a segfault, an abort, an uncaught exception, a sanitizer report) or stops making
progress (a hang, a deadlock, a busy loop). Go through the steps in order the first time; 3 to 5
loop until a hypothesis holds. For each step with a playbook section, read that section first: the
playbook is shared by every workflow, so use the bindings that fit this one (a plain line like
`For field issues:` above some says which workflow they're for); what's below is the fallback when
none fit, and the rule either way. The `debug` skill has the shared rules (evidence, experiments,
the check-in).

| # | Step | Playbook section | Produces |
|---|---|---|---|
| 1 | intake | `intake` | `report.md`: the crash report, stack trace, or sanitizer output; for a hang, what stops and where |
| 2 | reproduce | `reproduce` | at least one attempt with its outcome recorded; a hang tried with a time limit |
| 3 | gather-evidence | `gather-evidence` | a symbolized stack, a core file read in batch mode, or a thread dump |
| 4 | hypothesize | (judgment) | `hypotheses.md` |
| 5 | isolate | `isolate` | each hypothesis confirmed or ruled out, citing E-ids |
| 6 | root-cause | (judgment) | `root-cause.md`: the faulting `path:line` and the bad state that reached it |
| 7 | check-in | (none) | the human approves or rejects |

## 1. intake
- Put under "As received", unchanged: the crash report, the stack trace, sanitizer output, the
  signal or exception, and where a core file is (its path, not its contents). For a hang: what
  stops responding, what it was doing, how long it was left, and whether it used CPU meanwhile.
- With a binding (a ticket CLI, a crash reporter's CLI), fetch it through
  `debug run intake -- ...`, so the fetch is evidence too. If the binding fails (the tool is
  missing, you aren't logged in), don't install or log in: ask the human to paste it, and name the
  gap in your report.
- Fill in Expected and Actual, the version or commit, the platform, the build (release, debug, a
  sanitizer build), and the input or steps that lead there. Anything unclear goes under "Unclear"
  and to the human (`.agents/bin/tasks ask <slug> T1 '<question>'`).

## 2. reproduce
- Run what crashed, with the same input and build:
  `debug run reproduce --attempt=reproduce -- <command>`, then
  `debug outcome E-<n> reproduced|partial|not-reproduced`. A crash shows in the exit code (128
  plus the signal: 139 is a segfault, 134 an abort) and the output's tail.
- A hang always gets a time limit, well past how long the run normally takes:
  `debug run reproduce --attempt=reproduce --timeout=60 -- <command>`. Exit 124, with
  `timed out: after 60s` in the entry, is a reproduced hang; finishing in time is not.
- Output to a file is block-buffered, so a program stopped at the limit, or one that crashes, can
  lose what it printed last. Run it unbuffered where you can (`python3 -u`,
  `env PYTHONUNBUFFERED=1 <command>`, `stdbuf -oL -eL <command>` on Linux, `gstdbuf` on macOS with
  Homebrew's coreutils).
- Memory errors: a sanitizer build (AddressSanitizer, UndefinedBehaviorSanitizer) stops at the
  first bad access, with a stack, where a plain build crashes later or not at all. Use the one the
  playbook binds (a cpp-cmake project has an ASan+UBSan build for its full tier); don't set one up
  from scratch without asking.
- A crash that depends on timing: run it several times inside one entry and count:
  `debug run reproduce --attempt=reproduce -- bash -c 'f=0; for i in $(seq 20); do <command>; rc=$?; echo "run $i: exit $rc"; [ "$rc" -eq 0 ] || f=$((f+1)); done; echo "failed $f of 20"; [ "$f" -eq 0 ]'`.
  Every run crashing is `reproduced`, some is `partial`, none is `not-reproduced`.
- If it won't reproduce, note what differs from the report (input, platform, build flags, load)
  and move on to evidence.

## 3. gather-evidence
- Symbolize the stack: a trace of addresses means little until it names functions and lines
  (`addr2line -e <binary> <address>` on Linux, `atos -o <binary> -l <load address> <address>` on
  macOS, `llvm-symbolizer`, the language's own trace). If the binary has no debug info, say so and
  use a build that has it.
- A core file: read it with a debugger in batch mode, since `debug run` gives the command no stdin:
  `debug run gather-evidence -- gdb -batch -ex bt <binary> <core>`, or
  `debug run gather-evidence -- lldb --batch -c <core> -o 'bt all' <binary>`. In gdb,
  `-ex 'thread apply all bt'` shows every thread.
- A hang: a thread dump taken while it hangs, from the same entry that runs it:
  `debug run gather-evidence --timeout=90 -- bash -c '<command> & pid=$!; sleep 20; <dump>; rc=$?; kill -KILL $pid; exit $rc'`,
  where `<command>` is the program itself (behind `make run` or a pipeline, `$!` is a shell, not
  the program) and `<dump>` is the playbook's thread-dump binding with `$pid` for its `<pid>`
  (`py-spy dump --pid $pid`, `jstack $pid`, `gdb -batch -ex "thread apply all bt" -p $pid`,
  `lldb --batch -o "bt all" -p $pid`). The entry's exit code is the dump's.
- Attaching may be refused. On Linux with ptrace scope 1 (common) a debugger may attach only to its
  own children, and the dump above runs beside the program, not above it; macOS may want developer
  mode. Then launch the program under the tool, so the tool is its parent:
  `debug run gather-evidence --timeout=90 -- bash -c 'gdb -batch -ex run -ex "thread apply all bt" --args <binary> <args> & sleep 20; pkill -INT -P $!; wait $!'`
  (SIGINT stops the program inside gdb, which then prints every thread), or
  `py-spy record --duration 20 --format raw -o <file> -- <command>` for Python. If that's refused
  too, say so rather than working around it.
- Every item worth citing is a `debug run gather-evidence -- <command>` entry. Reading code is fine
  without one; cite it as `path:line`. Stop when you have enough to form hypotheses.

## 4. hypothesize
- Write `hypotheses.md`: every cause that fits the stack and the state, not just the first. For a
  crash: a null or dangling pointer, a use after free, an index out of bounds, an unchecked error,
  input reaching code that trusts it, an unhandled exception. For a hang: two locks taken in
  opposite orders, a wait nobody signals, a loop whose exit never comes, a blocking call with no
  timeout. For each, what would confirm it and what would rule it out, before you test it.
- The frame that crashed is where the bad state was noticed, not always where it was made.
  Hypotheses about where it came from count too.

## 5. isolate
- Test one hypothesis at a time against its confirm and rule-out conditions: a smaller input, an
  assert or a log line where the bad value appears, a sanitizer build, a debugger run in batch
  mode (`gdb -batch -ex run -ex bt --args <binary> <args>`), a bisect. Each result is an E-id; set
  the hypothesis's status with it.
- To bisect, with no tracked changes at all (`git status --untracked-files=no` prints nothing), run
  the whole bisect inside one entry, so HEAD is back on the branch when it ends:
  `debug run isolate --timeout=0 -- bash -c 'git bisect start <bad> <good> && git bisect run <test>; rc=$?; git bisect reset; exit $rc'`.
  Keep `<test>` out of the project tree (a script in the session dir works: git ignores it and
  `DEBUG_SCOPE` leaves it out), and don't call harness tools from it: in team mode each step checks
  out that commit's `.agents/`, or none. `git bisect run` gives up on an exit code of 128 or more,
  which is what a crash exits with (139 for a segfault, 134 for an abort), and on a hang it waits
  forever. So `<test>` turns any failure into 1 and gives the program its own limit:
  `timeout -k 5 60 <command>; rc=$?; [ "$rc" -lt 125 ] || [ "$rc" -gt 127 ] || exit 255; [ "$rc" -eq 0 ] || exit 1`
  (`gtimeout` on macOS with Homebrew's coreutils; `-k 5` kills it 5 seconds after the limit if it
  ignores SIGTERM). 125 to 127 mean `timeout` failed or couldn't find or run the command; 255 stops
  the bisect, where 1 would call every commit bad. A limit on the whole `debug run` stops it before
  `git bisect reset`. If HEAD is ever left mid-bisect (`git status` says so), run
  `git bisect reset` first.
- An old version: never check it out in place, since the session follows the branch. From the repo
  root, `git worktree add --detach ../repro-<ver> <ver>`, run it there with
  `debug run isolate -- bash -c 'cd "$(git rev-parse --show-toplevel)/../repro-<ver>" && <command>'`
  (`debug run` starts in the project root, which can be a subdirectory of the repo; then `cd` into
  that subdirectory of the old checkout too), and remove it after, from the repo root
  (`git worktree remove --force ../repro-<ver>`).
- Experiments (an assert, a log line, a smaller buffer, a lock taken earlier) are fine until
  `root-cause.md` exists; keep track of them so you can revert them. Don't put one in a file that
  already had uncommitted changes. If you must, undo your own lines by hand; never `git checkout`
  that file.
- A ruled-out hypothesis sends you back to step 3 or 4. When one holds, trigger the crash or hang
  through that cause on purpose: `debug run isolate --attempt=confirm -- <command>` and its
  outcome. It raises confidence; it never blocks.

## 6. root-cause
- Revert your experiments first. `debug status` lists every uncommitted change in scope, yours or
  not: one that was there before the session is the human's, so ask them to commit or stash it,
  never revert it.
- `## Cause` names the faulting `path:line` and the bad state that reached it: which value, made
  where, and how it got there. For a hang, the lock order, the wait, or the loop, with `path:line`
  for each side.
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

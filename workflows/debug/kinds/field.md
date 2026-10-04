---
kind: field
steps: intake gather-evidence reproduce hypothesize isolate root-cause check-in
bindable: intake gather-evidence reproduce isolate
---

# Field or production issue

Something went wrong where the software runs for real (production, a customer's site, a device in
the field), and that system can't be run here. So evidence comes before reproduction: logs,
telemetry, and config from there first, then a local attempt built from what they show. Go through
the steps in order the first time; 2 to 5 loop until a hypothesis holds. For each step with a
playbook section, read that section first: the playbook is shared by every workflow, so use
the bindings that fit this one (a plain line like `For field issues:` above some says which
workflow they're for); what's below is the fallback when none fit, and the rule either way. The
`debug` skill has the shared rules (evidence, experiments, the check-in).

| # | Step | Playbook section | Produces |
|---|---|---|---|
| 1 | intake | `intake` | `report.md`: what happened, where, when, which version, and ids to search by |
| 2 | gather-evidence | `gather-evidence` | logs, telemetry, and config from the field, narrowed, plus what's missing |
| 3 | reproduce | `reproduce` | a local attempt built from the evidence, with its outcome recorded |
| 4 | hypothesize | (judgment) | `hypotheses.md` |
| 5 | isolate | `isolate` | each hypothesis confirmed or ruled out, citing E-ids |
| 6 | root-cause | (judgment) | `root-cause.md`, often `evidence-only`, saying what would confirm it |
| 7 | check-in | (none) | the human approves or rejects |

## 1. intake
- With a binding (the ticket tool, an incident tool's CLI), fetch the report, comments included,
  through `debug run intake -- ...`, and put it under "As received" unchanged. If the binding
  fails (the tool is missing, you aren't logged in), don't install or log in: ask the human to
  paste it, and name the gap in your report.
- Pull out: what happened and what should have, when (with the time zone), where (environment,
  region, host, device), which version was running, how many were affected, and ids you can search
  by (a request id, a trace id, an order number). Anything unclear goes under "Unclear" and to the
  human (`.agents/bin/tasks ask <slug> T1 '<question>'`).
- `report.md` stays local, but the root cause and the ticket don't: from here on, refer to
  customers and their data by scrubbed ids only.

## 2. gather-evidence
- Pull logs, telemetry (metrics, traces, error reports), and config through the playbook's
  bindings, each one a `debug run gather-evidence -- ...` entry. The binding is also where the
  project's scrubbing lives (`run: scripts/pull-logs.sh <id> 2>&1 | scripts/scrub`): run the whole
  line, as `bash -c 'set -o pipefail; <line>'`, never the pull without the scrub. The `2>&1` sends
  the pull's errors through the scrubber too (`debug run` records stderr), and `pipefail` keeps a
  failed pull from reading as exit 0. `debug run` masks secrets guard knows, like keys and tokens,
  but not customer data; that's the scrubber's job.
- Narrow first: the time window, the request or trace id, the version, the host. A day of logs
  from every host is noise.
- Note what's missing as well as what's there: no logs at the level you need, data rotated away, a
  host nobody can reach. It goes in the root cause.
- Without a binding, ask the human for the logs or an export (`tasks ask`, as above). Don't log in
  to production systems yourself, and don't widen your own access.
- Tie each entry to the report. Stop when you have enough to form hypotheses.

## 3. reproduce
- Build a local attempt from the evidence: the version that was running, the inputs and config you
  could reconstruct, the same sequence of requests. Record it:
  `debug run reproduce --attempt=reproduce -- <command>`, then
  `debug outcome E-<n> reproduced|partial|not-reproduced`.
- `partial` and `not-reproduced` are the usual outcomes here, and fine: the attempt is what's
  required. Say what you couldn't reconstruct (the data, the load, the hardware, the other
  services).
- A version other than `HEAD`: never check it out in place, since the session follows the branch.
  From the repo root, `git worktree add --detach ../repro-<ver> <ver>`, run it there with
  `debug run reproduce --attempt=reproduce -- bash -c 'cd "$(git rev-parse --show-toplevel)/../repro-<ver>" && <command>'`
  (`debug run` starts in the project root, which can be a subdirectory of the repo; then `cd` into
  that subdirectory of the old checkout too), and remove it after, from the repo root
  (`git worktree remove --force ../repro-<ver>`). The entry's `head:` is the branch's commit, so
  name `<ver>` in your notes.

## 4. hypothesize
- Write `hypotheses.md`: every cause that fits the evidence, not just the first. Field issues add
  their own: config or data that differs from what runs here, load and timing, another service or
  the network, a deploy or migration near the time it started. For each, what would confirm it and
  what would rule it out, including which evidence from the field would settle it.
- Order them by how cheap they are to test and how well they fit.

## 5. isolate
- Test against the evidence first: does the hypothesis explain every log line in the window, and
  the requests that didn't fail? Then here: a test with the reconstructed input, a smaller case, a
  bisect between the version that worked and the one that didn't.
- To bisect, with no tracked changes at all (`git status --untracked-files=no` prints nothing),
  run the whole bisect inside one entry, so HEAD is back on the branch when it ends:
  `debug run isolate --timeout=0 -- bash -c 'git bisect start <bad> <good> && git bisect run <test>; rc=$?; git bisect reset; exit $rc'`.
  Keep `<test>` out of the project tree (a script in the session dir works: git ignores it and
  `DEBUG_SCOPE` leaves it out), and don't call harness tools from it: in team mode each step checks
  out that commit's `.agents/`, or none. `git bisect run` gives up on an exit code of 128 or
  more (a crash), so `<test>` turns any failure into 1, and one that can hang gets its own limit:
  `timeout -k 5 60 <command>; rc=$?; [ "$rc" -eq 0 ] || exit 1` (`gtimeout` on macOS with
  Homebrew's coreutils). A limit on the whole `debug run` stops it before `git bisect reset`. If
  HEAD is ever left mid-bisect (`git status` says so), run `git bisect reset` first.
- Experiments happen here, in this repo, never on the field system. They're fine until
  `root-cause.md` exists; keep track of them so you can revert them. Don't put one in a file that
  already had uncommitted changes. If you must, undo your own lines by hand; never `git checkout`
  that file.
- A ruled-out hypothesis sends you back to step 2 or 4. When one holds and you can trigger it here,
  do it on purpose: `debug run isolate --attempt=confirm -- <command>` and its outcome. It raises
  confidence; it never blocks.

## 6. root-cause
- Revert your experiments first. `debug status` lists every uncommitted change in scope, yours or
  not: one that was there before the session is the human's, so ask them to commit or stash it,
  never revert it.
- Confidence will often be `evidence-only`: nothing reproduced here, and the cause rests on the
  field evidence. That's acceptable. `## Fix direction` then also says what logging or telemetry
  would confirm it next time (a log line on that path, a metric, a trace span), along with the fix,
  and names the evidence that was missing.
- Raw customer data never goes in `root-cause.md` or the ticket: only scrubbed ids. Quote a log
  line only after it went through the scrubber.
- Write `root-cause.md` with the fixed headings (the `debug` skill lists them), citing only E-ids
  and H-ids that exist, and the Confidence line `debug status` computes.
- Validate it with the `validate` skill before the check-in.

## 7. check-in
- With `rootcause` in `DEBUG_ASK`: ask the human to run `.agents/commands/debug approve <slug>`,
  with the `tasks ask` line the `debug` skill gives, and wait. A rejection reopens the session at
  hypothesize, with the reason at the end of hypotheses.md; go back to step 2 or 4.
- With `DEBUG_ASK` empty: `debug close <slug> reviewed` once the validate review passes.
- An approved session is closed. Don't edit root-cause.md after that: any change reopens the
  session for a new approval. The fix starts from the fix direction, in the repo's own process.

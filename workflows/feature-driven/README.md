# Workflow pack: feature-driven

Classic Feature-Driven Development (De Luca) for one developer and their agent. Installed with
`install.sh --workflow feature-driven <project>`.

The agent drafts a domain model and a feature list, plans and designs one feature at a time, and
builds it. You approve at the check-ins. Everything FDD produces stays local: it's your working
material, not a version-controlled document. Shared designs belong in your team's document
repository (Confluence or similar). The only FDD-related thing that reaches shared history is a
real ticket key.

## What it adds
- **Skill** `feature-driven` (harness-owned): the five FDD processes and three check-ins.
- **`fdd`** at `.agents/commands/fdd`: `approve list | design <ID> | inspect <ID>`
  (people only, in their own terminal) and `status [ID]`.
- **Checks** that `verify` runs after the project's own tier scripts, and a commit-message check.
- **Settings** appended to `.agents/harness.conf` (`FDD_*`), filled in by harness-tailor.
- **`.agents/fdd/.gitignore`**, so the artifacts stay out of git (the ignore file itself is committed).

## Files (in `FDD_DIR`, default `.agents/fdd/`)
| File | What |
|---|---|
| `model.md` | subject areas, main entities, relationships |
| `features.md` | `## Subject area`, `### FS-1 Feature set`, `- F-12 Calculate the total of a sale [PROJ-123]` |
| `designs/F-12.md` | approach, files touched, test plan |
| `approvals` | written by `fdd approve`; one line per approval, each also recorded in the git dir (`.git/ai-harness/fdd-approvals`) |

The list approval holds a hash of `model.md` and `features.md` together, so editing either one
voids it, not just `features.md`. A design approval holds a hash of that one design file. An
inspection records the commit and is final.

## Checks
| Tier | Finding | Meaning |
|---|---|---|
| edit, turn, full | `fdd-leak` | a feature ID from your list appears in a changed line of shared code, tests, or docs |
| edit (when the list is edited), full | `fdd-format` | duplicate ID, feature outside a set, name or ticket in the wrong shape |
| edit (when `approvals` is edited), turn, full | `fdd-approval-unrecorded` | a line in `approvals` that `fdd approve` didn't record; it doesn't count, and it's a policy block (exit 2) |
| edit (when `approvals` is edited), turn, full | `fdd-approval-simulated` | an approval a simulated human made, while the switch is off; it doesn't count, and it's a policy block |
| turn, full | `fdd-simulated-human` | a simulated-human switch that `install.sh --simulated-human` didn't write, or that appeared or changed during an agent turn; it's off, and it's a policy block |
| turn, full | `fdd-not-local` | `FDD_DIR` is in the repo but git tracks files in it or doesn't ignore it |
| turn, full | `fdd-list-missing` | the approved list was deleted |
| turn, full (when a change outside `FDD_DIR` and `.agents/` is judged) | `fdd-scope-empty` | `FDD_SCOPE` matches no tracked file and no new one git doesn't ignore, so every gate below would stay quiet; set it to where the code is |
| turn, full | `fdd-list-unapproved` | in-scope code changed while the list isn't approved, or changed since |
| turn, full | `fdd-unknown` | a task in progress names a feature that isn't in the list |
| turn, full | `fdd-untraced` | in-scope code changed with no task in progress naming a feature |
| turn, full | `fdd-no-design` | building a feature without its design, or before you approved it |
| commit message | leak | a feature ID from your list anywhere in the message; use the ticket key |

The full tier writes `.agents/cache/fdd-progress.md`, FDD's parking lot: each feature's milestone
(designed 41%, design approved 44%, built 89%, inspected 100%) and each feature set's average.
Built needs a `done` task whose recorded commit is a SHA git has (`git cat-file -e <sha>^{commit}`);
a made-up SHA, a ref name, or a commit since lost stays at 44% as `built (commit not found)`
(`tasks set <slug> <T-id> done HEAD` records the SHA `HEAD` names, so that works). Any real commit
counts, though, even one that doesn't touch `FDD_SCOPE` or belongs to another feature; review
`fdd status` before you approve an inspection.
`fdd status` shows the same milestones.

## Notes
- **Nothing configured, nothing happens.** Until `features.md` exists, every check is quiet and commits pass.
  After that, an `FDD_SCOPE` that matches nothing is a finding (`fdd-scope-empty`, exit 1), not a
  quiet pass: the default `src/**` matches nothing in a repo without `src/`, and an untailored
  scope would otherwise turn every gate off.
- **Tracing is local.** A change belongs to the feature whose plan task is `doing`, or, for a commit
  made in the turn, the feature whose task is `done` with that commit recorded; nothing in the code says so.
- **Gates.** `FDD_ASK` picks the check-ins that need your approval; the others get an agent review only.
- **Approve in your own terminal.** `fdd approve` refuses in a shell an agent tool started
  (`CLAUDECODE`, `GEMINI_CLI`, or `CURSOR_AGENT` set), so Claude Code's `!` prefix won't do. Each
  approval is also recorded in the git dir; a line in `approvals` without that record, say one an
  agent wrote with its Edit tool or `echo`, doesn't count, and `verify` reports it as
  `fdd-approval-unrecorded` (exit 2) until you delete it. `fdd status` lists such lines as "not
  counted". The lines already there when a clone first runs `install.sh` or `fdd approve` from your
  terminal (an upgrade from before the record, a copied-in FDD dir) are recorded once and listed.
- **When an agent plays you.** In a scratch repo, a pilot, or a demo, the "person" at the check-ins
  can itself be an agent, say a Claude Code session driving `claude -p` sessions in the repo. Install
  with `install.sh --workflow feature-driven --simulated-human <repo>`. It turns on a switch for
  that clone (`.git/ai-harness/simulated-human`) and prints a token once:
  `install: simulated human: to approve from an agent's shell, set AGENTS_SIMULATED_HUMAN=<token> on
  that one command`. With the token set, `fdd approve` works in an agent's shell
  (`AGENTS_SIMULATED_HUMAN=<token> .agents/commands/fdd approve design F-1`), and so does install's
  adoption. The agents working in the repo don't have the token, so they're refused exactly as
  without the switch. Keep it that way: never export the token in a shell that starts an agent.
  The hooks see it in that agent's environment and turn the switch off. Every approval
  made or adopted while it's on is marked simulated: `approved design F-1 (simulated human)`, a
  who field ending in `(simulated human)` in `approvals`, or a `#simulated` line in the record for
  adopted ones. `fdd status` starts with `simulated human: on (...)`, `verify` adds a `note:` line,
  and every `install.sh` run says so. Simulated approvals count only while the switch is on: delete
  the file to turn it off, and they become `fdd-approval-simulated`. Running `install.sh
  --simulated-human` again writes a new token; it's the only way to turn the switch back on after
  the hooks flag it (it appeared or changed during an agent turn, or its token was in an agent's
  environment). Set `AGENTS_SIMULATED_HUMAN` yourself (16 characters or more) before that run to
  pick the token. Never use it in a real project.
- **Commits in the same turn.** The stop gate runs `verify --since=<sha>` for `HEAD` and each
  branch tip the turn started from, so the trace, design, and leak checks see what an agent
  committed during the turn, whether through `git commit`, `gitflow commit`, skipped git hooks, or
  an amend, and on whichever branch. Merges, commits a remote has, and rebased copies aren't
  counted as the turn's. A task that's `done` with one of those commits traces the files that
  commit touched. A range left by a pause stays pending until a stop passes.
- **Known gaps.** This makes self-approval visible, not impossible. Codex and Copilot mark no agent
  shell, so there an approve spelled in a way the policy doesn't match still runs, and anywhere an
  agent can unset the marker (`env -u CLAUDECODE`) or write both `approvals` and the record on
  purpose. The same goes for the simulated-human switch: one written by hand, or that appears during
  an agent turn, is off and reported, but an agent that writes the switch and its record on purpose
  outside a hooked turn gets one that works (its approvals are still marked simulated, and status
  says the switch is on). Review `fdd status` before trusting it. Commits made outside an agent turn aren't judged
  by the turn gates; `verify --since=<rev>` judges a range by hand.
  Private IDs can also reach shared places the checks don't read: a branch summary
  (`gitflow start PROJ-123 <summary>`), a plan title that ends up in a PR body, or code committed
  outside an agent turn (`fdd-leak` diffs against `HEAD`, or the commit the turn started from at the stop gate).
- **Patterns are Python regexes.** `FDD_ID_PATTERN`, `FDD_NAME_PATTERN`, and `GIT_TICKET` (from
  `.agents/git.conf`, used to check ticket keys) must all be valid Python regexes. An invalid one is
  reported as a tooling problem (`infra:`, exit 3).
- **Matching.** Feature IDs match case-sensitively: with the default pattern, `f-12` isn't `F-12`.
- **Environment overrides.** `FDD_*` environment variables override `.agents/harness.conf`, but they
  aren't part of `verify`'s cache key. Run `verify --no-cache` after changing one.
- **python3.** The checks need it (a missing python3 is reported as a tooling problem). The commit check
  blocks without it once a feature list exists.

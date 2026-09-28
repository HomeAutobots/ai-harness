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
- **`fdd`** at `.agents/workflows/feature-driven/bin/fdd`: `approve list | design <ID> | inspect <ID>`
  (people only; the policy blocks agents) and `status [ID]`.
- **Checks** that `verify` runs after the project's own tier scripts, and a commit-message check.
- **Settings** appended to `.agents/harness.conf` (`FDD_*`), filled in by harness-tailor.
- **`.agents/fdd/.gitignore`**, so the artifacts stay out of git (the ignore file itself is committed).

## Files (in `FDD_DIR`, default `.agents/fdd/`)
| File | What |
|---|---|
| `model.md` | subject areas, main entities, relationships |
| `features.md` | `## Subject area`, `### FS-1 Feature set`, `- F-12 Calculate the total of a sale [PROJ-123]` |
| `designs/F-12.md` | approach, files touched, test plan |
| `approvals` | written by `fdd approve`; one line per approval |

The list approval holds a hash of `model.md` and `features.md` together, so editing either one
voids it, not just `features.md`. A design approval holds a hash of that one design file. An
inspection records the commit and is final.

## Checks
| Tier | Finding | Meaning |
|---|---|---|
| edit, turn, full | `fdd-leak` | a feature ID from your list appears in a changed line of shared code, tests, or docs |
| edit (when the list is edited), full | `fdd-format` | duplicate ID, feature outside a set, name or ticket in the wrong shape |
| turn, full | `fdd-list-missing` | the approved list was deleted |
| turn, full | `fdd-list-unapproved` | in-scope code changed while the list isn't approved, or changed since |
| turn, full | `fdd-unknown` | a task in progress names a feature that isn't in the list |
| turn, full | `fdd-untraced` | in-scope code changed with no task in progress naming a feature |
| turn, full | `fdd-no-design` | building a feature without its design, or before you approved it |
| commit message | leak | a feature ID from your list anywhere in the message; use the ticket key |

The full tier writes `.agents/cache/fdd-progress.md`, FDD's parking lot: each feature's milestone
(designed 41%, design approved 44%, built 89%, inspected 100%) and each feature set's average.

## Notes
- **Nothing configured, nothing happens.** Until `features.md` exists, every check is quiet and commits pass.
- **Tracing is local.** A change belongs to the feature whose plan task is `doing`; nothing in the code says so.
- **Gates.** `FDD_ASK` picks the check-ins that need your approval; the others get an agent review only.
- **Known gap.** Policy blocks the approve command, not file writes, so an agent could still edit
  `approvals` by hand. It's local, so review `fdd status` before trusting it.
- **python3.** The checks need it (a missing python3 is reported as a tooling problem). The commit check
  blocks without it once a feature list exists.

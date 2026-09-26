# Workflow pack: req-driven

Requirements-driven development, generic: any requirements source you can export to CSV, JSON,
Markdown, or plain text, and any ID format you can write as a regex. Installed with
`install.sh --workflow req-driven <project>`.

A workflow pack answers "in what order, and with what evidence, do we change things." It's
independent of stack packs, so a project can combine `cpp-cmake` with `req-driven`.

## What it adds
- **Skill** `req-driven` (harness-owned): pin the requirement, tests from the requirement first,
  implement without bending the tests, close the loop with a trace.
- **Checks** that `verify` runs after the project's own tier scripts, for every workflow in
  `WORKFLOWS` in `.agents/harness.conf`. Nothing in `.agents/checks/` changes.
- **Settings** appended to `.agents/harness.conf` (`REQ_*`), filled in by harness-tailor.

| Tier | Finding | Meaning |
|---|---|---|
| edit, turn, full | `req-unknown` | an ID in the change isn't in `REQ_SOURCE` (typo or invented) |
| edit, turn, full | `req-untagged-test` | a new test doesn't name the requirement it verifies |
| turn, full | `req-untraced` | the change touches `REQ_SCOPE` and references no requirement anywhere: code, tests, or the plan task in progress |
| full | `req-unknown` repo-wide, and `req-untested` with `REQ_REQUIRE_TESTED=1` | baselinable with `verify --tier=full --update-baseline` |

The full tier also writes `.agents/cache/req-trace.md`: every requirement with the code and
tests that reference it.

## Sources
- **CSV**: IDs from a column named `id`, `req id`, `requirement id`, `key`, or `requirement`;
  otherwise any cell matching the pattern. Most requirements tools export this.
- **JSON**: `id`-like fields anywhere in the structure.
- **Markdown or text**: every match of the pattern, e.g. a `requirements.md` with one heading per
  requirement.
- **A directory** of any of these.

## Notes
- **Identifiers.** If the pattern contains `-` but no `_`, `REQ_12` counts as `REQ-12`, so IDs
  work in C++, Python, and Java test names.
- **Granularity.** Trace is per change, not per file: one reference anywhere in the change (or the
  plan task in progress) satisfies `req-untraced`. Tighten with project checks if you need more.
- **No waivers.** A change with no applicable requirement fails the turn tier on purpose; the
  skill tells the agent to stop and ask rather than invent one.

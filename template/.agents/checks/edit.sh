#!/usr/bin/env bash
# ai-harness:stub  (harness-tailor replaces this; delete this line once tailored)
# Edit tier. Project-owned. Runs after every file edit (post-edit hook) and via `check <files>`,
# with the edited files as arguments. Budget: EDIT_BUDGET seconds (harness.conf); if it runs
# out, the result is "skipped", never a block.
# Keep it per-file and fast: formatter in check mode, per-file lint, syntax check.
# Print findings as path:line[:col]: severity: message. Exit 0 clean, 1 findings, 3 tool missing.
. "$AGENTS_ROOT/.agents/lib/feedback.sh"
[ $# -gt 0 ] || exit 0
rc=0
# TODO(harness-tailor): per-file checks for the edited files ("$@"). Examples:
#   agents_step format prettier --check "$@" || rc=$?
#   agents_lint ruff ruff check --output-format=concise "$@" || rc=$(agents_worst_rc $rc $?)
exit $rc

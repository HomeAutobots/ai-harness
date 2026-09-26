#!/usr/bin/env bash
# ai-harness:stub  (harness-tailor replaces this; delete this line once tailored)
# Turn tier. Project-owned. The stop hook runs this (via verify) when the agent tries to
# finish; it's also the default for `.agents/bin/verify`. Arguments: changed files.
# Budget: TURN_BUDGET seconds. Aim for build + tests affected by the change + lint on
# changed code, well under the budget. Exit 0 clean, 1 findings, 3 tool missing.
. "$AGENTS_ROOT/.agents/lib/feedback.sh"
# TODO(harness-tailor): the project's incremental build, affected tests, and changed-code lint.
# Examples:
#   agents_step build npm run build || exit $?
#   agents_step tests npm test -- --changedSince=HEAD || rc=$?
#   agents_lint eslint npx eslint --format unix "$@" || rc=$(agents_worst_rc $rc $?)
echo "infra: .agents/checks/turn.sh is not configured yet. Run the harness-tailor skill."
exit 3

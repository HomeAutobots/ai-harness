#!/usr/bin/env bash
# ai-harness:stub  (harness-tailor replaces this; delete this line once tailored)
# Full tier. Project-owned. The commit gate and CI: `.agents/bin/verify --tier=full`.
# No budget by default. Mirror CI: full build, all tests, slow analyzers, sanitizers.
# Exit 0 clean, 1 findings, 3 tool missing.
. "$AGENTS_ROOT/.agents/lib/feedback.sh"
# TODO(harness-tailor): everything CI checks.
echo "infra: .agents/checks/full.sh is not configured yet. Run the harness-tailor skill."
exit 3

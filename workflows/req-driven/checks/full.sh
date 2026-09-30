#!/usr/bin/env bash
# req-driven workflow, full tier. Harness-owned: replaced on upgrade.
# verify runs this after the project's own .agents/checks/full.sh. Settings: .agents/harness.conf (REQ_*).
command -v python3 >/dev/null 2>&1 || { echo "infra: python3 not found (req-driven workflow)"; exit 3; }
T="$(dirname "$0")/../req_tools.py"
. "$AGENTS_ROOT/.agents/lib/feedback.sh"
rc=0
python3 "$T" diff "$AGENTS_ROOT" "$@" || rc=$?
python3 "$T" trace "$AGENTS_ROOT" || rc=$(agents_worst_rc "$rc" $?)
exit "$rc"

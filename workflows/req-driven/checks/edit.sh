#!/usr/bin/env bash
# req-driven workflow, edit tier. Harness-owned: replaced on upgrade.
# verify runs this after the project's own .agents/checks/edit.sh. Settings: .agents/harness.conf (REQ_*).
command -v python3 >/dev/null 2>&1 || { echo "infra: python3 not found (req-driven workflow)"; exit 3; }
T="$(dirname "$0")/../req_tools.py"
exec python3 "$T" diff --edit "$AGENTS_ROOT" "$@"

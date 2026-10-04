#!/usr/bin/env bash
# debug workflow, full tier: turn checks, plus the reproduction attempt and the playbook. Harness-owned: replaced on upgrade.
# verify runs this after the project's own .agents/checks/full.sh. Settings: .agents/harness.conf (DEBUG_*).
command -v python3 >/dev/null 2>&1 || { echo "infra: python3 not found (debug workflow)"; exit 3; }
exec python3 "$(dirname "$0")/../debug_tools.py" check full "$AGENTS_ROOT" "$@"

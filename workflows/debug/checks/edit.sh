#!/usr/bin/env bash
# debug workflow, edit tier: root-cause.md, hypotheses.md, and the playbook when they're edited. Harness-owned: replaced on upgrade.
# verify runs this after the project's own .agents/checks/edit.sh. Settings: .agents/harness.conf (DEBUG_*).
command -v python3 >/dev/null 2>&1 || { echo "infra: python3 not found (debug workflow)"; exit 3; }
exec python3 "$(dirname "$0")/../debug_tools.py" check edit "$AGENTS_ROOT" "$@"

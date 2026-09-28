#!/usr/bin/env bash
# feature-driven workflow, turn tier. Harness-owned: replaced on upgrade.
# verify runs this after the project's own .agents/checks/turn.sh. Settings: .agents/harness.conf (FDD_*).
command -v python3 >/dev/null 2>&1 || { echo "infra: python3 not found (feature-driven workflow)"; exit 3; }
exec python3 "$AGENTS_ROOT/.agents/workflows/feature-driven/fdd_tools.py" check turn "$AGENTS_ROOT" "$@"

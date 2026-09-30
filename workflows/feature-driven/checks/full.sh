#!/usr/bin/env bash
# feature-driven workflow, full tier: turn checks, list format, and .agents/cache/fdd-progress.md.
# Harness-owned: replaced on upgrade. Settings: .agents/harness.conf (FDD_*).
command -v python3 >/dev/null 2>&1 || { echo "infra: python3 not found (feature-driven workflow)"; exit 3; }
exec python3 "$(dirname "$0")/../fdd_tools.py" check full "$AGENTS_ROOT" "$@"

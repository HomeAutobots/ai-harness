#!/usr/bin/env bash
# feature-driven workflow: commit messages carry no private feature IDs. gitflow runs this with the
# message file. Harness-owned: replaced on upgrade.
if ! command -v python3 >/dev/null 2>&1; then
  d="$(sed -n "s/^[[:space:]]*FDD_DIR=[\"']\{0,1\}\([^\"'#]*\)[\"']\{0,1\}.*/\1/p" "$AGENTS_ROOT/.agents/harness.conf" 2>/dev/null | tail -1 | sed 's/[[:space:]]*$//')"
  d="${d:-.agents/fdd}"
  case "$d" in /*) ;; *) d="$AGENTS_ROOT/$d" ;; esac
  [ -f "$d/features.md" ] || exit 0   # no feature list yet, nothing to leak
  echo "infra: python3 not found (feature-driven workflow)"
  exit 3
fi
exec python3 "$(dirname "$0")/../fdd_tools.py" msg "$AGENTS_ROOT" "$1"

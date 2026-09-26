#!/usr/bin/env bash
# The full gate: lint, then the smoke suite with the default awk and every other POSIX awk
# on this machine (gawk, mawk, one-true-awk), since awk differences are the most common
# portability break in this codebase.
#   bash tests/all.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
bash "$ROOT/tests/lint.sh" || exit 1
rc=0
run() {  # run <label> [awk path]
  local d out
  d="$(mktemp -d)"
  [ -n "${2:-}" ] && ln -s "$2" "$d/awk"
  if out="$(PATH="$d:$PATH" bash "$ROOT/tests/smoke.sh" 2>&1)"; then
    printf 'smoke %-14s %s\n' "$1" "$(printf '%s\n' "$out" | tail -1)"
  else
    printf 'smoke %-14s FAILED\n' "$1"
    printf '%s\n' "$out" | grep -E '^  FAIL' | sed 's/^/  /'
    rc=1
  fi
  rm -rf "$d"
}
run "(default awk)"
for a in gawk mawk original-awk nawk; do
  p="$(command -v "$a" 2>/dev/null)" && run "$a" "$p"
done
exit $rc

#!/usr/bin/env bash
# feature-driven workflow: FDD_DIR contents for verify's cache key (git ignores the directory).
# Harness-owned: replaced on upgrade.
d="$(sed -n "s/^[[:space:]]*FDD_DIR=[\"']\{0,1\}\([^\"'#]*\)[\"']\{0,1\}.*/\1/p" "$AGENTS_ROOT/.agents/harness.conf" 2>/dev/null | tail -1 | sed 's/[[:space:]]*$//')"
d="${d:-.agents/fdd}"
case "$d" in /*) ;; *) d="$AGENTS_ROOT/$d" ;; esac
[ -d "$d" ] || exit 0
find "$d" -type f | LC_ALL=C sort | while IFS= read -r f; do
  printf '%s\n' "$f"
  cat "$f"
done

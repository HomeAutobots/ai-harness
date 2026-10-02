#!/usr/bin/env bash
# feature-driven workflow: FDD_DIR contents for verify's cache key (git ignores the directory).
# Harness-owned: replaced on upgrade.
d="$(sed -n "s/^[[:space:]]*FDD_DIR=[\"']\{0,1\}\([^\"'#]*\)[\"']\{0,1\}.*/\1/p" "$AGENTS_ROOT/.agents/harness.conf" 2>/dev/null | tail -1 | sed 's/[[:space:]]*$//')"
d="${d:-.agents/fdd}"
case "$d" in /*) ;; *) d="$AGENTS_ROOT/$d" ;; esac
# The record of what fdd approve wrote, in the git dir: an approval counts only when it's there.
gd="$(git -C "$AGENTS_ROOT" rev-parse --git-common-dir 2>/dev/null)"
case "$gd" in
  "") ;;
  /*) cat "$gd/ai-harness/fdd-approvals" 2>/dev/null ;;
  *) cat "$AGENTS_ROOT/$gd/ai-harness/fdd-approvals" 2>/dev/null ;;
esac
[ -d "$d" ] || exit 0
find "$d" -type f | LC_ALL=C sort | while IFS= read -r f; do
  printf '%s\n' "$f"
  cat "$f"
done

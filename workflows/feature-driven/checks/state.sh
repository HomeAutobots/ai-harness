#!/usr/bin/env bash
# feature-driven workflow: FDD_DIR contents for verify's cache key (git ignores the directory).
# Harness-owned: replaced on upgrade.
d="$(sed -n "s/^[[:space:]]*FDD_DIR=[\"']\{0,1\}\([^\"'#]*\)[\"']\{0,1\}.*/\1/p" "$AGENTS_ROOT/.agents/harness.conf" 2>/dev/null | tail -1 | sed 's/[[:space:]]*$//')"
d="${d:-.agents/fdd}"
case "$d" in /*) ;; *) d="$AGENTS_ROOT/$d" ;; esac
# The records in the git dir (fdd approve writes fdd-approvals; an approval counts only when it's
# there) and the simulated-human switch next to them (install.sh --simulated-human), which counts
# when any pack's record holds its hash, so every record goes in.
gd="$(git -C "$AGENTS_ROOT" rev-parse --git-common-dir 2>/dev/null)"
case "$gd" in
  "") ;;
  /*) cat "$gd"/ai-harness/*-approvals "$gd/ai-harness/simulated-human" 2>/dev/null ;;
  *) cat "$AGENTS_ROOT/$gd"/ai-harness/*-approvals "$AGENTS_ROOT/$gd/ai-harness/simulated-human" 2>/dev/null ;;
esac
# The wrong-branch check reads the current branch, each plan's Branch: line (tasks link), and git.conf.
git -C "$AGENTS_ROOT" symbolic-ref -q HEAD 2>/dev/null
grep -H '^Branch:' "$AGENTS_ROOT"/.agents/plans/*/plan.md 2>/dev/null
cat "$AGENTS_ROOT/.agents/git.conf" 2>/dev/null
[ -d "$d" ] || exit 0
find "$d" -type f | LC_ALL=C sort | while IFS= read -r f; do
  printf '%s\n' "$f"
  cat "$f"
done

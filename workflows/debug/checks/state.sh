#!/usr/bin/env bash
# debug workflow: what its checks read that git ignores, for verify's cache key. Harness-owned:
# replaced on upgrade.
d="$(sed -n "s/^[[:space:]]*DEBUG_DIR=[\"']\{0,1\}\([^\"'#]*\)[\"']\{0,1\}.*/\1/p" "$AGENTS_ROOT/.agents/harness.conf" 2>/dev/null | tail -1 | sed 's/[[:space:]]*$//')"
d="${d:-.agents/debug}"
case "$d" in /*) ;; *) d="$AGENTS_ROOT/$d" ;; esac
# The records in the git dir (debug approve and reject write debug-approvals; an approval counts
# only when it's there) and the simulated-human switch next to them (install.sh --simulated-human),
# which counts when any pack's record holds its hash, so every record goes in, each under its name
# (a line moved from one record to another changes the key).
gd="$(git -C "$AGENTS_ROOT" rev-parse --git-common-dir 2>/dev/null)"
case "$gd" in
  "") ;;
  /*) ;;
  *) gd="$AGENTS_ROOT/$gd" ;;
esac
if [ -n "$gd" ]; then
  for f in "$gd"/ai-harness/*-approvals; do
    [ -f "$f" ] && { printf '%s\n' "${f##*/}"; cat "$f"; }
  done
  cat "$gd/ai-harness/simulated-human" 2>/dev/null
fi
# The current session is the newest open one on this branch.
git -C "$AGENTS_ROOT" symbolic-ref -q HEAD 2>/dev/null
[ -d "$d" ] || exit 0
find "$d" -type f ! -name '*.log' | LC_ALL=C sort | while IFS= read -r f; do
  printf '%s\n' "$f"
  cat "$f"
done
# The playbook's skill: and context: lines resolve against the libraries and the repo.
if [ -f "$d/playbook.md" ]; then
  sed -n 's/^[[:space:]]*[-*][[:space:]]*context:[[:space:]]*//p' "$d/playbook.md" | while IFS= read -r c; do
    case "$c" in /*) p="$c" ;; *) p="$AGENTS_ROOT/$c" ;; esac
    if [ -e "$p" ]; then echo "context $c: here"; else echo "context $c: missing"; fi
  done
  if grep -q '^[[:space:]]*[-*][[:space:]]*skill:' "$d/playbook.md"; then
    bash "$AGENTS_ROOT/.agents/lib/libraries.sh" resolve skills 2>/dev/null | cut -f1   # AGENTS_ROOT: verify exports it
  fi
fi

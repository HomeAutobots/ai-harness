# shellcheck shell=bash
# ai-harness: the harness block in the clone's exclude file. Harness-owned: replaced on upgrade.
# Sourced by sync (writes it) and verify (reads it). Run from the install root.
#
# Every worktree of a clone reads one info/exclude (git has no per-worktree exclude file), and its
# patterns apply in all of them. So each install keeps a block of its own, keyed by worktree and
# install prefix, and rewrites only that one:
#   # >>> ai-harness (local install; managed by .agents/bin/sync) [<worktree>][<prefix>]
#   /<prefix>.agents/
#   # <<< ai-harness [<worktree>][<prefix>]
# <worktree> is the worktree's top relative to the directory that holds the common git dir ("."
# for the main worktree), or absolute when it's somewhere else; <prefix> is git rev-parse
# --show-prefix. Entries are relative to the repo top, the prefix escaped for gitignore.
# The end line carries the key too: a sync from before the keys drops every bare end line it
# sees, and would leave keyed blocks open. A start line inside a block also ends that block, so a
# damaged file can't swallow the lines after it.
# A block from before the keys (bare marker lines) is adopted by the install whose paths it lists
# (same prefix, and a harness path among them), only while that install has no keyed block: once
# it has one, a bare block is another worktree's, still on an older version.

AGENTS_EXCL_MARK="# >>> ai-harness (local install; managed by .agents/bin/sync)"
AGENTS_EXCL_END="# <<< ai-harness"

# Shared awk: block starts and ends, and whether a block from before the keys (leg[1..n]) is this
# install's. Needs L (the bare marker), E (the bare end), K (this install's start line), P ("/"
# plus the escaped prefix), and A (1: a bare block may be adopted). KE is this install's end line.
AGENTS_EXCL_AWK='
function excl_start(s) { return s == L || index(s, L " [") == 1 }
function excl_end(s) { return s == E || index(s, E " [") == 1 }
function excl_ours(n,   i, r, any) {
  if (!A) return 0
  for (i = 1; i <= n; i++) {
    if (index(leg[i], P) != 1) return 0
    r = substr(leg[i], length(P) + 1)
    if (r ~ /^\.(agents|claude|github|cursor|codex|gemini)\// || r ~ /^(AGENTS\.md|CLAUDE\.md|CLAUDE\.local\.md|\.mcp\.json)$/) any = 1
  }
  return n == 0 || any
}
BEGIN { L = ENVIRON["AGENTS_EXCL_MARK"]; E = ENVIRON["AGENTS_EXCL_END"]; K = ENVIRON["AGENTS_EXCL_K"]; P = ENVIRON["AGENTS_EXCL_PRE"]
        A = (ENVIRON["AGENTS_EXCL_ADOPT"] == "1"); KE = E substr(K, length(L) + 1) }
'
export AGENTS_EXCL_MARK AGENTS_EXCL_END

# agents_excl_file: the clone's exclude file (shared by its worktrees)
agents_excl_file() { git rev-parse --git-path info/exclude 2>/dev/null; }

# agents_excl_pre: this install's prefix, escaped the way block entries carry it
agents_excl_pre() { git rev-parse --show-prefix 2>/dev/null | sed 's/[[*?\\]/\\&/g' || true; }

# agents_excl_base: the directory holding the common git dir, physically
agents_excl_base() {
  local c
  c="$(git rev-parse --git-common-dir 2>/dev/null)" && [ -n "$c" ] || return 1
  (cd "$c" 2>/dev/null && cd .. && pwd -P)
}

# agents_excl_wt <base> <dir>: the key of the worktree whose top is <dir>
agents_excl_wt() {
  local d
  d="$(cd "$2" 2>/dev/null && pwd -P)" && [ -n "$d" ] || return 1
  case "$d" in
    "$1") printf '.\n' ;;
    "$1"/*) printf '%s\n' "${d#"$1"/}" ;;
    *) printf '%s\n' "$d" ;;
  esac
}

# agents_excl_begin: this install's begin marker
agents_excl_begin() {
  local base top wt
  base="$(agents_excl_base)" || return 1
  top="$(git rev-parse --show-toplevel 2>/dev/null)" && [ -n "$top" ] || return 1
  wt="$(agents_excl_wt "$base" "$top")" || return 1
  printf '%s [%s][%s]\n' "$AGENTS_EXCL_MARK" "$wt" "$(git rev-parse --show-prefix 2>/dev/null || true)"
}

# agents_excl_live: the key of every worktree still on disk, one per line
agents_excl_live() {
  local base line
  base="$(agents_excl_base)" || return 0
  git worktree list --porcelain 2>/dev/null | while IFS= read -r line; do
    case "$line" in "worktree "*) agents_excl_wt "$base" "${line#worktree }" || true ;; esac
  done
}

# agents_excl_adopt <file> <begin>: 1 when a block from before the keys may be this install's
# (it has no keyed block yet), else 0
agents_excl_adopt() {
  if [ -f "$1" ] && grep -qxF -- "$2" "$1"; then echo 0; else echo 1; fi
}

# agents_excl_own <file> <begin>: the entries of this install's block, plus those of a block from
# before the keys that's this install's
agents_excl_own() {
  [ -f "$1" ] || return 0
  AGENTS_EXCL_K="$2" AGENTS_EXCL_PRE="/$(agents_excl_pre)" AGENTS_EXCL_ADOPT="$(agents_excl_adopt "$1" "$2")" \
    awk "$AGENTS_EXCL_AWK"'
    function done_blk(   i) { if (g && excl_ours(n)) for (i = 1; i <= n; i++) print leg[i]; f = 0; g = 0 }
    excl_start($0) { done_blk(); f = ($0 == K); g = ($0 == L); n = 0; next }
    excl_end($0) { done_blk(); next }
    f { print; next }
    g { leg[++n] = $0 }
    END { done_blk() }' "$1"
}

# agents_excl_rewrite <file> <begin> <entries> [<live>]: the exclude file with this install's block
# holding the lines of <entries> (none: no block), where its old block was (or the block from
# before the keys it adopts), else at the end. With <live> (agents_excl_live), the blocks of
# worktrees that are gone go too. Everything else stays as it was, in order.
agents_excl_rewrite() {
  local f="$1"
  [ -f "$f" ] || f=/dev/null
  AGENTS_EXCL_K="$2" AGENTS_EXCL_PRE="/$(agents_excl_pre)" AGENTS_EXCL_NEW="$3" AGENTS_EXCL_LIVE="${4:-}" \
    AGENTS_EXCL_ADOPT="$(agents_excl_adopt "$f" "$2")" awk "$AGENTS_EXCL_AWK"'
    function put(   i) { if (done) return; done = 1; if (!m) return
                         print K; for (i = 1; i <= m; i++) print new[i]; print KE }
    function keep(   i) { print hdr; for (i = 1; i <= n; i++) print leg[i] }
    function alive(   i) { if (!nl) return 1
                           for (i = 1; i <= nl; i++) if (index(hdr, L " [" live[i] "][") == 1) return 1
                           return 0 }
    # done_blk <end line>: the block that just ended (at its end line, at the next start line, or
    # at the end of the file, where <end line> is empty). Ours is replaced, an adopted one too;
    # the block of another install is kept (and closed) unless its worktree is gone.
    function done_blk(endl) {
      if (st == 1 || (st == 2 && excl_ours(n))) put()
      else if (st == 2 || alive()) { keep(); print (endl != "" ? endl : E substr(hdr, length(L) + 1)) }
      st = 0 }
    BEGIN { while ((getline line < ENVIRON["AGENTS_EXCL_NEW"]) > 0) if (!(line in seen)) { seen[line] = 1; new[++m] = line }
            if (ENVIRON["AGENTS_EXCL_LIVE"] != "")
              while ((getline line < ENVIRON["AGENTS_EXCL_LIVE"]) > 0) if (line != "") live[++nl] = line }
    excl_start($0) { if (st) done_blk(""); st = ($0 == K) ? 1 : (($0 == L) ? 2 : 3); hdr = $0; n = 0; next }
    !st { print; next }
    excl_end($0) { done_blk($0); next }
    { leg[++n] = $0 }
    END { if (st) done_blk(""); put() }' "$f"
}

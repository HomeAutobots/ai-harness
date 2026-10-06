#!/usr/bin/env bash
# ai-harness installer. Installs or upgrades the harness in a project. Safe to re-run.
#
#   ./install.sh [--local | --team] [--stack <name>]... [--workflow <name>]... [--simulated-human] <project-dir>
#
# --simulated-human is for flows where an agent plays the person (a scratch repo, a pilot, a demo):
# a shell holding the token it prints may approve the gates of every workflow pack with human gates
# (feature-driven, debug) in this clone, even an agent's shell, and every approval is marked
# simulated. Never in a real project. It's kept in the git dir, so it's per clone; delete
# .git/ai-harness/simulated-human to turn it off.
#
# Harness-owned files are replaced on every run. Project-owned files are only created when
# missing, so re-running is the upgrade and never touches tailoring. What the harness ships
# (built-in skills, stack packs in stacks/, workflow packs in workflows/) lands in
# .agents/builtin/, one library among several (.agents/library/, LIBRARIES, ~/.config/ai-harness/).
# Packs are turned on by name in STACKS / WORKFLOWS in .agents/harness.conf and run in place from
# whichever library has them.
set -euo pipefail

HARNESS="$(cd "$(dirname "$0")" && pwd)"
SRC="$HARNESS/template"
VERSION="$(cat "$HARNESS/VERSION")"

usage() {
  echo "usage: $0 [--local | --team] [--stack <name>]... [--workflow <name>]... [--simulated-human] <project-dir>" >&2
  echo "  stacks: $(ls "$HARNESS/stacks" | tr '\n' ' ')  workflows: $(ls "$HARNESS/workflows" | tr '\n' ' ')" >&2
  exit 2
}
NEW_STACKS=""
NEW_WORKFLOWS=""
NEW_MODE=""
SIMULATED_HUMAN=0
DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) [ $# -ge 2 ] || usage; NEW_STACKS="$NEW_STACKS $2"; shift 2 ;;
    --stack=*) NEW_STACKS="$NEW_STACKS ${1#--stack=}"; shift ;;
    --workflow) [ $# -ge 2 ] || usage; NEW_WORKFLOWS="$NEW_WORKFLOWS $2"; shift 2 ;;
    --workflow=*) NEW_WORKFLOWS="$NEW_WORKFLOWS ${1#--workflow=}"; shift ;;
    --local) NEW_MODE=local; shift ;;
    --team) NEW_MODE=team; shift ;;
    --simulated-human) SIMULATED_HUMAN=1; shift ;;
    -h|--help) usage ;;
    -*) echo "install: unknown option $1" >&2; usage ;;
    *) [ -z "$DEST" ] || usage; DEST="$1"; shift ;;
  esac
done
[ -n "$DEST" ] || usage
[ -d "$DEST" ] || { echo "install: not a directory: $DEST" >&2; exit 2; }
DEST="$(cd "$DEST" && pwd)"
[ "$DEST" != "$HARNESS" ] || { echo "install: that's the harness repo itself" >&2; exit 2; }
# The resolver, from this harness (so it works on a first install too), pointed at the project.
export AGENTS_ROOT="$DEST"
unset AGENTS_LIBS_PINNED AGENTS_LIBS_PIN_ROOT   # a scan pinned by a caller (verify) predates what this run builds
# shellcheck source=template/.agents/lib/libraries.sh
. "$SRC/.agents/lib/libraries.sh"
say() { printf 'install: %s\n' "$*"; }

if git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1; then
  [ -z "$(git -C "$DEST" status --porcelain)" ] \
    || say "note: $DEST has uncommitted changes, so review the harness diff carefully"
else
  say "note: $DEST isn't a git repo. The feedback tools and hooks need git; please init one."
fi
command -v python3 >/dev/null 2>&1 \
  || say "warning: python3 not found. Hooks and permission rules won't be rendered until it is installed."

# Local mode recovery: .agents/ is gone (git clean -X, a checkout) but sync kept a
# backup in the git dir, where none of those reach. Restore it before seeding anything, so the
# project's tailoring comes back instead of blank templates. Same path as agents_backup_dir
# (prefixes a/b and a_b collide there too).
if [ ! -d "$DEST/.agents" ] && git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1; then
  bk="$(git -C "$DEST" rev-parse --git-path ai-harness)"
  case "$bk" in /*) ;; *) bk="$DEST/$bk" ;; esac
  pfx="$(git -C "$DEST" rev-parse --show-prefix)"; pfx="${pfx%/}"
  bk="$bk/backup${pfx:+-$(printf '%s' "$pfx" | tr '/' '_')}"
  if [ -d "$bk/.agents" ]; then
    cp -R "$bk/.agents" "$DEST/.agents"
    for f in AGENTS.md CLAUDE.md CLAUDE.local.md; do
      if [ -f "$bk/$f" ] && [ ! -e "$DEST/$f" ]; then cp "$bk/$f" "$DEST/$f"; fi
    done
    say "restored your local harness files from $bk (.agents/ was missing)"
  fi
fi

# Pack names on the command line: after the restore, so a pack in a restored .agents/library/ counts.
known_pack() {  # known_pack <workflows|stacks> <name>: shipped here, or in a library the project sees
  agents_valid_name "$2" || return 1
  [ -d "$HARNESS/$1/$2" ] || agents_resolve "$1" "$2" >/dev/null && return 0
  # A hand-made pack still in the old layout (moved to .agents/library/ below); a stack shim isn't one.
  case "$1" in
    workflows) [ -d "$DEST/.agents/workflows/$2" ] ;;
    stacks) [ -f "$DEST/.agents/stacks/$2/lib.sh" ] && ! grep -q 'ai-harness: stack shim' "$DEST/.agents/stacks/$2/lib.sh" ;;
  esac
}
for s in $NEW_STACKS; do
  known_pack stacks "$s" || { echo "install: unknown stack '$s' (not shipped with the harness or in a library)" >&2; usage; }
done
for w in $NEW_WORKFLOWS; do
  known_pack workflows "$w" || { echo "install: unknown workflow '$w' (not shipped with the harness or in a library)" >&2; usage; }
done
# --simulated-human: checked before anything changes. It only affects the approvals of workflow
# packs with human gates (a pack with a human-gates file) and lives in the git dir. A token the
# caller sets must be long enough that an agent can't guess it.
gate_key() {  # gate_key <pack dir>: the record key its human-gates file names (the first line that's a key, not "on"); empty without one
  [ -f "$1/human-gates" ] || return 0
  sed -n '/^[[:space:]]*\([a-z0-9][a-z0-9-]*\)[[:space:]]*$/{s//\1/;/^on$/d;p;q;}' "$1/human-gates"
}
gated_pack() {  # gated_pack <name>: the pack that will run has human gates (a library's copy, else the one shipped here)
  local p
  p="$(agents_resolve workflows "$1" 2>/dev/null)" || p=""
  case "$p" in ""|"$DEST/.agents/builtin/"*) p="$HARNESS/workflows/$1" ;; esac   # builtin/ is rebuilt below
  [ -f "$p/human-gates" ] || return 1
  if [ -z "$(gate_key "$p")" ]; then
    echo "install: $p/human-gates names no record key (a line of lowercase letters, digits, and dashes, not 'on'); fix the pack" >&2
    exit 3
  fi
}
if [ "$SIMULATED_HUMAN" -eq 1 ]; then
  git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1 \
    || { echo "install: --simulated-human needs a git repo (it's kept in the git dir, per clone)" >&2; exit 2; }
  command -v python3 >/dev/null 2>&1 || { echo "install: --simulated-human needs python3" >&2; exit 3; }
  gated=""
  for w in $(sed -n 's/^WORKFLOWS="\{0,1\}\([^"#]*\)"\{0,1\}.*/\1/p' "$DEST/.agents/harness.conf" 2>/dev/null | head -1) $NEW_WORKFLOWS; do
    if gated_pack "$w"; then gated="$gated $w"; fi
  done
  if [ -z "$gated" ]; then
    names=""
    for g in "$HARNESS"/workflows/*/human-gates; do
      if [ -f "$g" ]; then names="${names:+$names, }$(basename "$(dirname "$g")")"; fi
    done
    echo "install: --simulated-human only changes approvals in workflow packs with human gates ($names); add one with --workflow" >&2
    exit 2
  fi
  if [ -n "${AGENTS_SIMULATED_HUMAN:-}" ] && [ "${#AGENTS_SIMULATED_HUMAN}" -lt 16 ]; then
    echo "install: AGENTS_SIMULATED_HUMAN is shorter than 16 characters; unset it to get a generated token" >&2; exit 2
  fi
fi

PREV="$(cat "$DEST/.agents/HARNESS_VERSION" 2>/dev/null || true)"

# Without python3, a switch to local can still unshare AGENTS.md, CLAUDE.md, and the files that
# only leave the index, but it can't strip the harness out of a tracked config the project keeps.
# So --local on a team install (or one whose harness is still tracked) stops here, before the
# upgrade changes anything, when such a config holds harness entries: a hook command under
# .agents/hooks/, servers or deny rules generated.lock records, the Gemini context entry, or the
# Codex MCP block.
if [ "$NEW_MODE" = local ] && [ -n "$PREV" ] && ! command -v python3 >/dev/null 2>&1 \
   && git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1 \
   && { [ "$(sed -n 's/^HARNESS_MODE="\{0,1\}\([a-z]*\).*/\1/p' "$DEST/.agents/harness.conf" 2>/dev/null | head -n 1)" != local ] \
        || git -C "$DEST" ls-files --error-unmatch -- .agents/harness.conf >/dev/null 2>&1; }; then
  needs=""; lock="$DEST/.agents/generated.lock"
  # A tracked AGENTS.md the project tailored stays tracked, and so does its Gemini context entry.
  keep_agents=0
  git -C "$DEST" ls-files --error-unmatch -- AGENTS.md >/dev/null 2>&1 && [ -f "$DEST/AGENTS.md" ] \
    && ! grep -q '^> \*\*Not tailored yet\.\*\*' "$DEST/AGENTS.md" && keep_agents=1
  for p in .claude/settings.json .cursor/hooks.json .codex/hooks.json .gemini/settings.json .mcp.json .cursor/mcp.json .codex/config.toml; do
    f="$DEST/$p"
    { [ -f "$f" ] && [ ! -L "$f" ] && git -C "$DEST" ls-files --error-unmatch -- "$p" >/dev/null 2>&1; } || continue
    case "$p" in
      .codex/config.toml) grep -qxF '# >>> ai-harness mcp (managed by .agents/bin/sync)' "$f" || continue ;;
      *) grep -qF '.agents/hooks/' "$f" || grep -qF "\"$p\": [" "$lock" 2>/dev/null \
           || { [ "$p" = .claude/settings.json ] && grep -q '"claude_deny": \[ *$' "$lock" 2>/dev/null; } \
           || { [ "$p" = .gemini/settings.json ] && [ "$keep_agents" -eq 0 ] && grep -qF '"AGENTS.md"' "$f"; } || continue ;;
    esac
    needs="$needs $p"
  done
  if [ -n "$needs" ]; then
    say "error: python3 not found. Switching to local needs it to take the harness out of these tracked files:$needs" >&2
    say "nothing was changed. Install python3 and re-run install.sh --local, or keep team mode." >&2
    exit 3
  fi
fi

# same_copy <shipped> <installed>: the same files, contents, and executable bits, and no links. A
# Python cache from running the harness doesn't count (Python checks it against the source).
same_copy() {
  if [ -f "$1" ]; then
    [ -f "$2" ] && [ ! -L "$2" ] && cmp -s "$1" "$2" || return 1
    if [ -x "$1" ]; then [ -x "$2" ]; else [ ! -x "$2" ]; fi
    return
  fi
  [ -d "$2" ] && [ ! -L "$2" ] && [ -z "$(find "$2" -type l | head -n 1)" ] || return 1
  diff -r -x __pycache__ "$1" "$2" >/dev/null 2>&1 || return 1
  [ "$(cd "$1" && find . -type f -perm -u+x | sort)" = "$(cd "$2" && find . -type f -perm -u+x | sort)" ]
}

# Harness-owned: replaced every run. One that already matches what ships stays as it is, so a
# re-run rewrites only what changed (and macOS doesn't assess the unchanged scripts again).
replace() {
  same_copy "$SRC/$1" "$DEST/$1" && return 0
  rm -rf "${DEST:?}/$1"
  mkdir -p "$(dirname "$DEST/$1")"
  cp -R "$SRC/$1" "$DEST/$1"
}

# Project-owned: created once, never overwritten.
seed() {
  [ -e "$DEST/$1" ] && return 0
  mkdir -p "$(dirname "$DEST/$1")"
  cp -R "$SRC/$1" "$DEST/$1"
  say "created $1"
}

# --- migrate 0.1.x: verify was project-owned; its checks move to the tier scripts ------------
if [ -f "$DEST/.agents/bin/verify" ] && ! grep -q 'ai-harness: verify (orchestrator)' "$DEST/.agents/bin/verify"; then
  mkdir -p "$DEST/.agents/checks"
  for tier in turn full; do
    if [ ! -e "$DEST/.agents/checks/$tier.sh" ]; then
      cp "$DEST/.agents/bin/verify" "$DEST/.agents/checks/$tier.sh"
      say "migrated your 0.1 .agents/bin/verify to .agents/checks/$tier.sh (review it: tier scripts get changed files as arguments)"
    fi
  done
  rm -f "$DEST/.agents/bin/verify"
fi

replace .agents/core
replace .agents/lib
replace .agents/hooks
replace .agents/README.md
replace .agents/.gitattributes
replace .agents/.gitignore
for tool in sync verify check guard policy tasks eval gitflow; do
  replace ".agents/bin/$tool"
done
# An upgrade from before libraries: any link in .agents/skills/ was made by hand then. A local
# install restored from its backup lacks .agents/builtin/ too, but has .agents/library/.
OLD_LAYOUT=0
if [ -n "$PREV" ] && [ ! -d "$DEST/.agents/builtin" ] && [ ! -d "$DEST/.agents/library" ]; then OLD_LAYOUT=1; fi

# Built-in library: every skill, workflow pack, and stack pack this harness ships, active or not.
# Replaced wholesale on every run; other libraries shadow it by name.
build_builtin() {
  local b="$DEST/.agents/builtin" kind
  rm -rf "${b:?}"
  for kind in skills workflows stacks; do mkdir -p "$b/$kind"; done
  cp -R "$SRC/.agents/skills/." "$b/skills/"
  cp -R "$HARNESS/workflows/." "$b/workflows/"
  cp -R "$HARNESS/stacks/." "$b/stacks/"
  find "$b" -name __pycache__ -type d -prune -exec rm -rf {} +   # from a dev checkout
  find "$b" -type f \( -name '*.sh' -o -path '*/bin/*' \) -exec chmod +x {} +
}
# One-time note: cpp-cmake used to ignore its CPP_* settings in harness.conf (all but CPP_NO_TESTS,
# and export lines, which verify passed on), and now applies them. Said while the installed copy
# is one from before that, so only once; only when cpp-cmake is in STACKS.
for f in "$DEST/.agents/builtin/stacks/cpp-cmake/lib.sh" "$DEST/.agents/stacks/cpp-cmake/lib.sh"; do
  if [ ! -f "$f" ] || grep -qF 'ai-harness: stack shim' "$f"; then continue; fi
  if grep -qF 'agents_conf_import CPP_' "$f"; then break; fi
  case " $(agents_conf_get "$DEST/.agents/harness.conf" STACKS) " in *" cpp-cmake "*) ;; *) break ;; esac
  keys="$(sed -n 's/^[[:space:]]*\(CPP_[A-Za-z0-9_]*\)=.*/\1/p' "$DEST/.agents/harness.conf" 2>/dev/null \
    | grep -vx CPP_NO_TESTS | LC_ALL=C sort -u | tr '\n' ' ' | sed 's/ $//' || true)"
  [ -n "$keys" ] && say "note: .agents/harness.conf sets $keys, which the cpp-cmake stack used to ignore there; they apply now (a value the tier scripts set still wins). Review those lines."
  break
done
build_builtin

seed AGENTS.md
seed .agents/harness.conf
seed .agents/policy.conf
seed .agents/git.conf
seed .agents/git
seed .agents/context
seed .agents/plans
seed .agents/evals
seed .agents/library
seed .agents/work
for d in scratch scripts references reports requirements; do
  mkdir -p "$DEST/.agents/work/$d"
done
for tier in edit turn full; do
  seed ".agents/checks/$tier.sh"
done

# --- packs --------------------------------------------------------------------------------------
CONF="$DEST/.agents/harness.conf"
conf_list() {  # conf_list <VAR>: current value of a space-separated list in harness.conf
  sed -n "s/^$1=\"\{0,1\}\([^\"#]*\)\"\{0,1\}.*/\1/p" "$CONF" | head -1 | sed 's/[[:space:]]*$//'
}
conf_list_set() {  # conf_list_set <VAR> <value>: set (or add) the list in harness.conf
  if grep -q "^$1=" "$CONF"; then
    tmp="$(mktemp)"
    awk -v k="$1" -v v="$2" '$0 ~ "^" k "=" { print k "=\"" v "\""; next } { print }' "$CONF" > "$tmp" && cat "$tmp" > "$CONF" && rm -f "$tmp"
  else
    printf '%s="%s"\n' "$1" "$2" >> "$CONF"
  fi
}
merge_list() {  # merge_list <existing> <new...>: union, order kept
  local out="$1" x
  shift
  for x in "$@"; do case " $out " in *" $x "*) ;; *) out="${out:+$out }$x" ;; esac; done
  printf '%s' "$out"
}

# --- mode: local (hidden from git in this clone) or team (committed) ---------------------------
# A fresh install defaults to local; an upgrade keeps the recorded mode, and a missing line
# (installs from before this setting) means team. Only --local or --team switches.
OLD_MODE="$(conf_list HARNESS_MODE)"
if [ -n "$NEW_MODE" ]; then MODE="$NEW_MODE"
elif [ -z "$PREV" ]; then MODE=local
else MODE="${OLD_MODE:-team}"; fi
SWITCH=""
[ -n "$PREV" ] && [ "${OLD_MODE:-team}" != "$MODE" ] && SWITCH="$MODE"
# An explicit --local finishes a switch that stopped partway (mode recorded, harness still tracked).
if [ "$NEW_MODE" = local ] && [ -n "$PREV" ] && [ -z "$SWITCH" ] \
   && git -C "$DEST" ls-files --error-unmatch -- .agents/harness.conf >/dev/null 2>&1; then SWITCH=local; fi
conf_list_set HARNESS_MODE "$MODE"
say "mode: $MODE$([ "$MODE" = local ] && echo ' (hidden from git in this clone; install.sh --team to commit it instead)')"

# --- migrate to libraries --------------------------------------------------------------------
# One time and idempotent: every step checks what's on disk, so an interrupted run finishes on
# the next. Copies of what this harness ships go (they run from .agents/builtin/ now); anything
# the project made moves into its own library, .agents/library/. Team mode lists the moves to
# commit; local mode keeps quiet.
MIGRATED=""
migrated() { MIGRATED="$MIGRATED
  $*"; }
repath() {  # repath <dir> <old> <new>: point whole-name mentions of path <old> in text files at <new>
  local o f
  o="$(printf '%s' "$2" | sed 's/[].[\*^$|]/\\&/g')"
  { LC_ALL=C grep -rlIF -- "$2" "$1" 2>/dev/null || true; } | while IFS= read -r f; do
    LC_ALL=C sed -e "s|$o\([^A-Za-z0-9._-]\)|$3\1|g" -e "s|$o\$|$3|" "$f" > "$f.harness-tmp" && cat "$f.harness-tmp" > "$f"
    rm -f "$f.harness-tmp"
  done
  return 0
}
# shipped_copy <shipped> <copy> [diff args]: true if <copy> is an unchanged copy of what this
# harness ships (a Python cache from running it doesn't count as a change)
shipped_copy() {
  local a="$1" b="$2"
  shift 2
  diff -rq -x __pycache__ "$@" "$a" "$b" >/dev/null 2>&1
}
# set_aside <kind> <path>: an old copy of something the harness ships that differs from it
# (edited, or just from an older version) is kept, never deleted, where no library looks and git
# never commits it: the worktree's git dir, next to local mode's backup (not inside it: team mode
# removes the backup). Outside git, .agents/library/.migrated/. One summary line at the end.
SET_ASIDE=0
SET_ASIDE_DIR=""
set_aside() {
  local n dest i=1
  if [ -z "$SET_ASIDE_DIR" ]; then
    if SET_ASIDE_DIR="$(git -C "$DEST" rev-parse --git-path ai-harness 2>/dev/null)" && [ -n "$SET_ASIDE_DIR" ]; then
      case "$SET_ASIDE_DIR" in /*) ;; *) SET_ASIDE_DIR="$DEST/$SET_ASIDE_DIR" ;; esac
      pfx="$(git -C "$DEST" rev-parse --show-prefix 2>/dev/null || true)"; pfx="${pfx%/}"
      SET_ASIDE_DIR="$SET_ASIDE_DIR/migrated${pfx:+-$(printf '%s' "$pfx" | tr '/' '_')}"   # per install, like the backup
    else
      SET_ASIDE_DIR="$DEST/.agents/library/.migrated"
    fi
  fi
  n="$(basename "$2")"
  dest="$SET_ASIDE_DIR/$1/$n"
  while [ -e "$dest" ] || [ -L "$dest" ]; do dest="$SET_ASIDE_DIR/$1/$n.$i"; i=$((i + 1)); done
  mkdir -p "$(dirname "$dest")"
  mv "$2" "$dest"
  SET_ASIDE=$((SET_ASIDE + 1))
}
STACK_SHIM_MARK="ai-harness: stack shim"
write_stack_shim() {  # write_stack_shim <name>: .agents/stacks/<name>/lib.sh forwards to the resolved pack
  local d="$DEST/.agents/stacks/$1"
  rm -rf "${d:?}"   # callers make sure it's missing, a shim, or a copy of a shipped pack
  mkdir -p "$d"
  cat > "$d/lib.sh" <<EOF
# shellcheck shell=bash
# $STACK_SHIM_MARK. Harness-owned: install.sh rewrites it. Tier scripts source this path; it
# loads the $1 stack pack from whichever library has it (.agents/builtin/stacks/$1 unless a
# library of yours shadows it).
. "\$AGENTS_ROOT/.agents/lib/libraries.sh"
if ! _agents_stack="\$(agents_resolve stacks '$1')"; then
  echo "infra: stack '$1' isn't in any library (STACKS in .agents/harness.conf)"
  exit 3
fi
. "\$_agents_stack/lib.sh"
EOF
}

# local_tracked <path>: local mode never moves, rewrites, or deletes what the project tracks, except
# on a switch from team mode, where it's the harness's own files (untracked below).
local_tracked() {
  { [ "$MODE" = local ] && [ "$SWITCH" != local ]; } || return 1
  [ -n "$(git -C "$DEST" ls-files -- "$1" 2>/dev/null | head -n 1)" ]
}
# stack_refs <name> <now-in>: once .agents/stacks/<name>/ keeps only the shim, each line elsewhere in
# the project that points at another file in it (a tier script, a CI config) gets a path:line warning.
stack_refs() {
  local s=".agents/stacks/$1/"
  {
    if git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1; then
      # the whole repo (:/), so a subdirectory install sees the CI configs at the top too
      git -C "$DEST" grep -nIF --untracked -e "$s" -- ':/' 2>/dev/null || true
    else
      (cd "$DEST" && grep -rnIF --exclude-dir=.git -e "$s" . 2>/dev/null | sed 's|^\./||') || true
    fi
    # local mode hides .agents/ from git, so its project-owned files are read directly
    (cd "$DEST" && grep -nIF -e "$s" .agents/checks/*.sh .agents/*.conf /dev/null 2>/dev/null) || true
    (cd "$DEST" && grep -rnIF -e "$s" .agents/library .agents/context 2>/dev/null) || true
  } | awk -v s="$s" '{
      p = index($0, ":"); path = substr($0, 1, p - 1); rest = substr($0, p + 1)
      q = index(rest, ":"); num = substr(rest, 1, q - 1); c = substr(rest, q + 1)
      t = "/" path   # a sibling install in another subdirectory counts the same as this one
      if (t ~ /\/\.agents\/(builtin|stacks|cache)\// || t ~ /\/\.agents\/library\/\.migrated\//) next
      while ((i = index(c, s)) > 0) {
        c = substr(c, i + length(s))
        if (!(substr(c, 1, 6) == "lib.sh" && substr(c, 7, 1) !~ /[A-Za-z0-9._-]/)) { print path ":" num; next }
      }
    }' | sort -t: -k1,1 -k2,2n -u | while IFS= read -r ref; do
    say "warning: $ref: points into .agents/stacks/$1/, which only keeps lib.sh now; the stack's files are in $2/"
  done
}

if [ -d "$DEST/.agents/workflows" ]; then
  for d in "$DEST"/.agents/workflows/*/; do
    [ -d "$d" ] || continue
    w="$(basename "$d")"
    if ! agents_valid_name "$w" || [ -L "${d%/}" ]; then
      say "warning: .agents/workflows/$w isn't a plain pack directory with a valid name; left in place, move it into .agents/library/workflows/ yourself"
    elif local_tracked ".agents/workflows/$w"; then
      say "warning: the project tracks files in .agents/workflows/$w; local mode leaves it in place (move it into .agents/library/workflows/ yourself, in a commit)"
    elif [ -d "$HARNESS/workflows/$w" ]; then
      # The old layout copied the pack without its skill, seed files, and config snippets.
      if shipped_copy "$HARNESS/workflows/$w" "${d%/}" -x skill -x seed -x harness.conf.snippet -x policy.conf.snippet; then
        rm -rf "${DEST:?}/.agents/workflows/$w"
        migrated "removed .agents/workflows/$w (the pack runs from .agents/builtin/workflows/$w)"
      else
        set_aside workflows "$DEST/.agents/workflows/$w"
      fi
    elif [ -e "$DEST/.agents/library/workflows/$w" ] || [ -L "$DEST/.agents/library/workflows/$w" ]; then
      say "warning: .agents/workflows/$w and .agents/library/workflows/$w both exist; keep the one you want in .agents/library/workflows/ and delete .agents/workflows/$w"
    else
      # Paths first, then the move: a run stopped in between still finishes (repath is idempotent).
      repath "$DEST/.agents/workflows/$w" ".agents/workflows/$w" ".agents/library/workflows/$w"
      mkdir -p "$DEST/.agents/library/workflows"
      mv "$DEST/.agents/workflows/$w" "$DEST/.agents/library/workflows/$w"
      migrated "moved .agents/workflows/$w to .agents/library/workflows/$w"
      if awk -v s=".agents/workflows/$w/" 'index($0, s) { f = 1 } END { exit !f }' "$DEST/.agents/policy.conf" "$DEST/.agents/git.conf" "$DEST"/.agents/checks/*.sh 2>/dev/null; then
        say "warning: .agents/policy.conf, git.conf, or checks/ mention .agents/workflows/$w/; it's in .agents/library/workflows/$w/ now, so update them"
      fi
    fi
  done
  rmdir "$DEST/.agents/workflows" 2>/dev/null || true
fi
# A hand-made pack's skill joins it as skill/ (this also finishes a move that stopped halfway).
# Only for active packs, the ones whose skill the old layout copied in: a project skill that
# merely shares a name with an inactive library pack stays where it is.
for d in "$DEST"/.agents/library/workflows/*/; do
  [ -d "$d" ] || continue
  w="$(basename "$d")"; s="$DEST/.agents/skills/$w"
  case " $(conf_list WORKFLOWS) $NEW_WORKFLOWS " in *" $w "*) ;; *) continue ;; esac
  if [ -f "$s/SKILL.md" ] && [ ! -L "$s" ] && [ ! -f "$s/.harness-copy" ] && [ ! -e "${d}skill" ]; then
    if local_tracked ".agents/skills/$w"; then
      say "warning: the project tracks .agents/skills/$w; local mode leaves it in place instead of moving it to .agents/library/workflows/$w/skill"
      continue
    fi
    repath "$s" ".agents/workflows/$w" ".agents/library/workflows/$w"
    mv "$s" "${d}skill"
    migrated "moved .agents/skills/$w to .agents/library/workflows/$w/skill"
  fi
done
if [ -d "$DEST/.agents/stacks" ]; then
  for d in "$DEST"/.agents/stacks/*/; do
    [ -d "$d" ] || continue
    s="$(basename "$d")"
    if grep -qF "$STACK_SHIM_MARK" "${d}lib.sh" 2>/dev/null; then continue; fi
    if ! agents_valid_name "$s" || [ -L "${d%/}" ]; then
      say "warning: .agents/stacks/$s isn't a plain pack directory with a valid name; left in place, move it into .agents/library/stacks/ yourself"
    elif local_tracked ".agents/stacks/$s"; then
      say "warning: the project tracks files in .agents/stacks/$s; local mode leaves it in place (move it into .agents/library/stacks/ yourself, in a commit)"
    elif [ -d "$HARNESS/stacks/$s" ]; then
      shipped_copy "$HARNESS/stacks/$s" "${d%/}" || set_aside stacks "$DEST/.agents/stacks/$s"
      write_stack_shim "$s"
      migrated "replaced .agents/stacks/$s with a shim (the pack runs from .agents/builtin/stacks/$s)"
      stack_refs "$s" ".agents/builtin/stacks/$s"
    elif [ -e "$DEST/.agents/library/stacks/$s" ] || [ -L "$DEST/.agents/library/stacks/$s" ]; then
      say "warning: .agents/stacks/$s and .agents/library/stacks/$s both exist; keep the one you want in .agents/library/stacks/ and delete .agents/stacks/$s"
    else
      repath "$DEST/.agents/stacks/$s" ".agents/stacks/$s" ".agents/library/stacks/$s"
      repath "$DEST/.agents/stacks/$s" ".agents/library/stacks/$s/lib.sh" ".agents/stacks/$s/lib.sh"   # tier scripts keep the shim path
      mkdir -p "$DEST/.agents/library/stacks"
      mv "$DEST/.agents/stacks/$s" "$DEST/.agents/library/stacks/$s"
      write_stack_shim "$s"
      migrated "moved .agents/stacks/$s to .agents/library/stacks/$s (.agents/stacks/$s/lib.sh forwards to it)"
      stack_refs "$s" ".agents/library/stacks/$s"
    fi
  done
fi
# .agents/skills/ is rendered by sync now. Copies of shipped skills (and of shipped packs' skills)
# go; the project's own skills move to .agents/library/skills/. Before this layout nothing in
# .agents/skills/ was a link unless someone made it, so an old install's links move too.
# Marked copies (.harness-copy) are renders: sync replaces or removes them.
if [ -d "$DEST/.agents/skills" ]; then
  for d in "$DEST"/.agents/skills/*; do
    { [ -e "$d" ] || [ -L "$d" ]; } || continue
    n="$(basename "$d")"
    [ -f "$d/SKILL.md" ] || continue
    agents_valid_name "$n" || continue
    lib="$DEST/.agents/library/skills/$n"
    # Local mode never touches what the project tracks (a repo may keep its own skills here),
    # except on a switch from team mode, where it's the harness's own files, untracked below.
    if [ "$MODE" = local ] && [ "$SWITCH" != local ] && git -C "$DEST" ls-files --error-unmatch -- ".agents/skills/$n" >/dev/null 2>&1; then continue; fi
    if [ -L "$d" ]; then
      [ "$OLD_LAYOUT" -eq 1 ] || continue   # a render
      tp="$(cd "$d" && pwd -P)"
      case "$tp" in "$(cd "$DEST" && pwd -P)/.agents/builtin/"*) continue ;; esac   # a render too
      if [ -e "$lib" ] || [ -L "$lib" ]; then
        say "warning: .agents/skills/$n and .agents/library/skills/$n both exist; keep the one you want in .agents/library/skills/ and delete .agents/skills/$n"
        continue
      fi
      # Same target: relative (from its new place) inside the project, as it was outside it.
      t="$(readlink "$d")"; rp="$(cd "$DEST" && pwd -P)"
      case "$tp" in "$rp"/*) t="../../../${tp#"$rp"/}" ;; *) case "$t" in /*) ;; *) t="$tp" ;; esac ;; esac
      mkdir -p "$DEST/.agents/library/skills"
      ln -s "$t" "$lib"
      rm -f "$d"
      migrated "moved your link .agents/skills/$n to .agents/library/skills/$n"
      continue
    fi
    [ -f "$d/.harness-copy" ] && continue   # a render
    shipped=""
    if [ -d "$SRC/.agents/skills/$n" ]; then shipped="$SRC/.agents/skills/$n"; at=".agents/builtin/skills/$n"
    elif [ -d "$HARNESS/workflows/$n/skill" ]; then shipped="$HARNESS/workflows/$n/skill"; at=".agents/builtin/workflows/$n/skill"; fi
    if [ -n "$shipped" ]; then
      if shipped_copy "$shipped" "$d"; then
        rm -rf "${d:?}"
        migrated "removed .agents/skills/$n (built in: $at)"
      else
        set_aside skills "$d"
      fi
    elif [ -e "$lib" ] || [ -L "$lib" ]; then
      say "warning: .agents/skills/$n and .agents/library/skills/$n both exist; keep the one you want in .agents/library/skills/ and delete .agents/skills/$n"
    else
      mkdir -p "$DEST/.agents/library/skills"
      mv "$d" "$lib"
      migrated "moved .agents/skills/$n to .agents/library/skills/$n"
    fi
  done
fi
# The feature-driven approve rule names the fdd command, which lives in .agents/builtin/ now.
if [ -f "$DEST/.agents/policy.conf" ] && grep -q '^deny-cmd \.agents/workflows/feature-driven/bin/fdd approve' "$DEST/.agents/policy.conf"; then
  tmp="$(mktemp)"
  sed 's|^deny-cmd \.agents/workflows/feature-driven/bin/fdd approve|deny-cmd .agents/builtin/workflows/feature-driven/bin/fdd approve|' "$DEST/.agents/policy.conf" > "$tmp"
  cat "$tmp" > "$DEST/.agents/policy.conf"; rm -f "$tmp"
  migrated "pointed the fdd approve rule in .agents/policy.conf at .agents/builtin/workflows/feature-driven/bin/fdd"
fi
# sync's .agents/commands/fdd runs the same command, so it gets the same native deny rule. Keyed on
# the builtin rule, so a project that deleted the fdd rules on purpose doesn't get one back.
if [ -f "$DEST/.agents/policy.conf" ] \
   && grep -q '^deny-cmd \.agents/builtin/workflows/feature-driven/bin/fdd approve' "$DEST/.agents/policy.conf" \
   && ! grep -q '^deny-cmd \.agents/commands/fdd approve' "$DEST/.agents/policy.conf"; then
  tmp="$(mktemp)"
  awk '{ print } /^deny-cmd \.agents\/builtin\/workflows\/feature-driven\/bin\/fdd approve/ && !done {
         print "deny-cmd .agents/commands/fdd approve   # approving FDD gates is a human decision"; done = 1 }' \
    "$DEST/.agents/policy.conf" > "$tmp"
  cat "$tmp" > "$DEST/.agents/policy.conf"; rm -f "$tmp"
  migrated "added a deny rule for .agents/commands/fdd approve to .agents/policy.conf"
fi

if [ "$SET_ASIDE" -gt 0 ]; then
  case "$SET_ASIDE_DIR" in "$DEST"/*) kept_in="${SET_ASIDE_DIR#"$DEST"/}" ;; *) kept_in="$SET_ASIDE_DIR" ;; esac
  if [ "$SET_ASIDE" -eq 1 ]; then
    say "kept 1 old copy that differs from the shipped version in $kept_in; review and delete it when done"
  else
    say "kept $SET_ASIDE old copies that differ from the shipped versions in $kept_in; review and delete them when done"
  fi
fi

# --- packs: turned on by name, run in place from whichever library has them ------------------
STACKS="$(merge_list "$(conf_list STACKS)" $NEW_STACKS)"
conf_list_set STACKS "$STACKS"
for s in $STACKS; do
  if ! sp="$(agents_resolve stacks "$s")"; then
    say "warning: STACKS lists '$s' but no library has it; skipped"
    continue
  fi
  if [ -e "$DEST/.agents/stacks/$s" ] && ! grep -qF "$STACK_SHIM_MARK" "$DEST/.agents/stacks/$s/lib.sh" 2>/dev/null; then
    continue   # the migration above warned: both .agents/stacks/$s and the library copy exist
  fi
  write_stack_shim "$s"
  # A stack's tier scripts replace the generic stubs, never tailored scripts.
  for tier in edit turn full; do
    t="$DEST/.agents/checks/$tier.sh"
    [ -f "$sp/checks/$tier.sh" ] || continue
    if [ ! -e "$t" ] || grep -q 'ai-harness:stub' "$t"; then
      cp "$sp/checks/$tier.sh" "$t"
      say "seeded .agents/checks/$tier.sh from stack $s"
    fi
  done
done

WORKFLOWS="$(merge_list "$(conf_list WORKFLOWS)" $NEW_WORKFLOWS)"
conf_list_set WORKFLOWS "$WORKFLOWS"
for w in $WORKFLOWS; do
  if ! wp="$(agents_resolve workflows "$w")"; then
    say "warning: WORKFLOWS lists '$w' but no library has it; skipped"
    continue
  fi
  # Settings are project-owned: appended once, never overwritten.
  snip="$wp/harness.conf.snippet"
  first_var=""
  [ -f "$snip" ] && first_var="$(grep -m1 -o '^[A-Z_]*=' "$snip" | tr -d '=' || true)"
  if [ -n "$first_var" ] && ! grep -q "^$first_var=" "$CONF"; then
    cat "$snip" >> "$CONF"
    say "added $w settings to .agents/harness.conf (harness-tailor fills them in)"
  fi
  # Policy rules are project-owned too: appended once, keyed on the snippet's first line (a marker
  # comment), so a rule the project deletes on purpose stays deleted while the marker stays.
  snip="$wp/policy.conf.snippet"
  if [ -f "$snip" ]; then
    marker="$(sed -n '1p' "$snip")"
    if [ -n "$marker" ] && ! grep -qxF -- "$marker" "$DEST/.agents/policy.conf"; then
      { echo; cat "$snip"; } >> "$DEST/.agents/policy.conf"
      say "added $w rules to .agents/policy.conf"
    fi
  fi
  # Seed files (e.g. a .gitignore for the pack's local working files): project-owned, created once.
  if [ -d "$wp/seed" ]; then
    (cd "$wp/seed" && find . -type f) | while IFS= read -r f; do
      f="${f#./}"
      case "$f" in .agents/bin/*|.agents/lib/*|.agents/hooks/*|.agents/core/*|.agents/builtin/*|.agents/skills/*|.agents/workflows/*|.agents/stacks/*)
        say "warning: $w seeds $f, which is harness-owned; skipped"; continue ;;
      esac
      [ -e "$DEST/$f" ] && continue
      mkdir -p "$(dirname "$DEST/$f")"
      cp "$wp/seed/$f" "$DEST/$f"
      say "created $f"
    done
  fi
done

printf '%s\n' "$VERSION" > "$DEST/.agents/HARNESS_VERSION"
chmod +x "$DEST"/.agents/bin/* "$DEST"/.agents/hooks/run "$DEST"/.agents/checks/*.sh

# --- switching modes -------------------------------------------------------------------------
# --local on a team install: the harness's own files leave the index (they stay on disk), and
# tracked shared files lose only the harness's blocks and entries. --team needs nothing here:
# sync in team mode drops the exclude block (keeping only your personal skill renders) and moves
# the Claude settings back.
IN_GIT=0; git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1 && IN_GIT=1
if [ "$SWITCH" = local ] && [ "$IN_GIT" -eq 1 ]; then
  unshare_out="$(cd "$DEST" && bash .agents/bin/sync --unshare)" \
    || { say "error: couldn't take the harness out of shared files; fix the error above and re-run with --local"; exit 3; }
  untrack=".agents"
  for p in "$DEST"/.claude/skills/*; do
    { [ -L "$p" ] && case "$(readlink "$p")" in ../../.agents/skills/*) true ;; *) false ;; esac; } \
      || [ -f "$p/.harness-copy" ] || continue
    untrack="$untrack .claude/skills/$(basename "$p")"
  done
  while IFS= read -r line; do
    case "$line" in
      "untrack "*) untrack="$untrack ${line#untrack }" ;;
      "stripped "*) say "removed the harness entries from ${line#stripped }" ;;
    esac
  done <<EOF
$unshare_out
EOF
  # -f: files stay on disk either way; it only lets staged-but-uncommitted changes go too.
  for p in $untrack; do git -C "$DEST" rm -r -q -f --cached --ignore-unmatch -- "$p"; done
fi

# The scripts just copied run through bash, not exec'd: macOS assesses a newly written executable
# on its first exec, which can take seconds. Same interpreter their shebang picks.
bash "$DEST/.agents/bin/sync"
# Local git hooks (commit-msg, pre-push) so the git workflow holds for humans and every tool.
if [ "$IN_GIT" -eq 1 ]; then
  (cd "$DEST" && bash .agents/bin/gitflow install-hooks) | sed 's/^/install: /'
fi
# Packs with human gates (a human-gates file naming the pack's record key): an approval counts only
# when the pack's CLI recorded it in the git dir. The simulated human (--simulated-human) goes on
# first, for every such pack at once (.agents/lib/approvals.py), so this run can adopt with its
# token; on every run, the summary says when a switch is there. A --simulated-human that couldn't
# be turned on fails the install (exit 3, at the end), so a script that relies on it doesn't go on
# without it. Then feature-driven records the approvals already in FDD_DIR, once per clone (0.3.0):
# the first person-run install (or fdd approve) in a clone records what's there, and lists it.
SIM_FAILED=""
HUMAN_GATE_KEYS=""
for w in $WORKFLOWS; do
  wp="$(agents_resolve workflows "$w" 2>/dev/null)" || continue
  k="$(gate_key "$wp")"
  [ -z "$k" ] || HUMAN_GATE_KEYS="$HUMAN_GATE_KEYS $k"
done
if [ -n "$HUMAN_GATE_KEYS" ] && [ "$IN_GIT" -eq 1 ] && command -v python3 >/dev/null 2>&1; then
  sim_on=""
  if [ "$SIMULATED_HUMAN" -eq 1 ]; then
    [ -n "${AGENTS_SIMULATED_HUMAN:-}" ] \
      || AGENTS_SIMULATED_HUMAN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
    export AGENTS_SIMULATED_HUMAN
    sim_on=on
  fi
  sim_rc=0
  # shellcheck disable=SC2086  # one key per word
  sim_out="$(python3 "$DEST/.agents/lib/approvals.py" simulated-human "$DEST" $sim_on $HUMAN_GATE_KEYS 2>&1)" || sim_rc=$?
  [ -z "$sim_out" ] || printf '%s\n' "$sim_out" | sed 's/^/install: /'
  [ "$sim_rc" -eq 0 ] || [ "$SIMULATED_HUMAN" -eq 0 ] || SIM_FAILED="--simulated-human didn't turn the simulated human on (see above)"
elif [ "$SIMULATED_HUMAN" -eq 1 ]; then
  SIM_FAILED="no active workflow pack resolved with human gates (a human-gates file), so the simulated human is off"
fi
case " $WORKFLOWS " in *" feature-driven "*)
  if [ "$IN_GIT" -eq 1 ] && command -v python3 >/dev/null 2>&1 && wp="$(agents_resolve workflows feature-driven)" \
     && grep -q '^def cmd_adopt' "$wp/fdd_tools.py" 2>/dev/null; then   # an older personal pack has no adopt
    { python3 "$wp/fdd_tools.py" adopt "$DEST" 2>&1 || true; } | sed 's/^/install: /'
  fi ;;
esac

if { [ -n "$MIGRATED" ] || [ "$SET_ASIDE" -gt 0 ]; } && [ "$MODE" = team ]; then
  say "moved the harness to the library layout (see CHANGELOG.md). Commit what git status shows:$MIGRATED"
fi

if [ "$SWITCH" = local ] && [ "$IN_GIT" -eq 1 ]; then
  say "switched to local mode. Commit what git status shows (git add the modified files, or git commit -a): the harness leaves the repo (your files stay on disk)."
  say "after that commit, other clones lose .agents/ on pull; each developer re-runs install.sh (local by default)"
  git -C "$DEST" status --short | awk '/^D  \.agents\// { n++; next } { print "  " $0 }
    END { if (n) print "  D  .agents/ (" n " files)" }'
elif [ "$SWITCH" = team ]; then
  say "switched to team mode. Review and commit the harness: git add -A && git commit"
  if [ -f "$DEST/.agents/AGENTS.local.md" ]; then
    say "note: .agents/AGENTS.local.md stays; move any facts in it into AGENTS.md, then delete it (git add -A would otherwise commit it)"
  fi
fi

if [ -z "$PREV" ] && [ "$MODE" = local ]; then
  cat <<EOF

ai-harness $VERSION installed in $DEST (local mode)

Next:
  1. Open the project in any agent and say:
       "Use the harness-tailor skill to tailor the AI harness for this repo."
  2. Review what it proposes. Everything stays out of git in this clone; teammates see nothing.
  3. A new clone or worktree needs its own install.sh run.
  git stash -u is fine. After git stash -a, run git stash pop (not install.sh) to get them back.
  After git clean -fdX, re-run install.sh: it restores them from the backup in the git dir, as of
  the last sync or turn/full-tier verify.
  To share the harness with the team instead: install.sh --team $DEST
EOF
elif [ -z "$PREV" ]; then
  cat <<EOF

ai-harness $VERSION installed in $DEST (team mode)

Next:
  1. Open the project in any agent and say:
       "Use the harness-tailor skill to tailor the AI harness for this repo."
  2. Review the proposal (AGENTS.md, .agents/checks/, CLAUDE.md), then commit.
  3. CI: run '.agents/bin/sync --check && .agents/bin/verify --tier=full'.
  4. Consider CODEOWNERS for AGENTS.md, CLAUDE.md, .mcp.json, .agents/, .claude/, .cursor/,
     .github/hooks/, .github/agents/, .codex/, .gemini/.
EOF
elif [ "$PREV" != "$VERSION" ] && [ "$MODE" = local ]; then
  say "upgraded $PREV -> $VERSION. Check CHANGELOG.md in the harness repo. Local mode: nothing goes into git."
elif [ "$PREV" != "$VERSION" ]; then
  say "upgraded $PREV -> $VERSION. Check CHANGELOG.md in the harness repo, review the diff, commit."
else
  say "reinstalled $VERSION, no version change"
fi
if [ -n "$SIM_FAILED" ]; then
  say "error: $SIM_FAILED" >&2
  exit 3
fi

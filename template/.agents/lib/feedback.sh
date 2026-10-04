# shellcheck shell=bash
# ai-harness: feedback library. Harness-owned: replaced on upgrade.
# Sourced by verify, check, guard, and project check scripts (.agents/checks/*.sh).
#
# The contract every feedback tool follows:
#   quiet on success (one line), capped and deduplicated findings on failure,
#   full raw output on disk in .agents/cache/logs/, and these exit codes:
#     0 ok or skipped   1 findings to fix   2 policy block   3 tooling problem   124 out of budget
# Agent-visible output never contains timestamps or durations, so it stays byte-stable
# across runs and doesn't churn the model's prompt cache.

if [ -z "${AGENTS_ROOT:-}" ]; then
  AGENTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
export AGENTS_ROOT
AGENTS_CACHE="$AGENTS_ROOT/.agents/cache"
# The resolver: verify finds workflow packs with it, and the stack shims source it.
if [ -f "$AGENTS_ROOT/.agents/lib/libraries.sh" ]; then
  # shellcheck source=libraries.sh
  . "$AGENTS_ROOT/.agents/lib/libraries.sh"
fi

# shellcheck disable=SC2034  # read by the scripts that source this
agents_load_conf() {
  FEEDBACK_MAX_LINES=30
  FEEDBACK_MAX_PER_FILE=5
  EDIT_BUDGET=15
  TURN_BUDGET=300
  FULL_BUDGET=0
  # shellcheck source=/dev/null
  [ -f "$AGENTS_ROOT/.agents/harness.conf" ] && . "$AGENTS_ROOT/.agents/harness.conf"
  return 0
}

# agents_conf_import <PREFIX>: a stack's settings from .agents/harness.conf. verify loads that file
# but doesn't export it, so a stack's lib.sh calls this (agents_conf_import CPP_) before its defaults.
# The file is sourced in a subshell, as verify does, and each <PREFIX>* variable that sourcing
# sets is copied here with its value, unless the caller already has it set: a value set in the
# tier script before sourcing the lib, or one in its environment, wins. Set after sourcing, it
# wins too. Not exported. Sourced rather than parsed so a value means what it means to verify.
agents_conf_import() {
  case "${1:-}" in
    ""|[!A-Za-z]*|*[!A-Za-z0-9_]*) echo "infra: agents_conf_import: bad prefix '${1:-}'" >&2; return 3 ;;
  esac
  [ -f "$AGENTS_ROOT/.agents/harness.conf" ] || return 0
  eval "$(
    set +eu; unset IFS   # a tier script's strict mode or IFS must not change what's split here
    _ac_had=" $(compgen -v "$1" 2>/dev/null | tr '\n' ' ')"
    # shellcheck source=/dev/null
    . "$AGENTS_ROOT/.agents/harness.conf" >/dev/null 2>&1
    unset IFS
    for _ac_k in $(compgen -v "$1" 2>/dev/null); do
      [ "${_ac_had#* "$_ac_k" }" = "$_ac_had" ] || continue   # set before: no case here (bash 3.2 misparses one in $())
      printf '%s=%q\n' "$_ac_k" "${!_ac_k}"
    done
    exit 0
  )"
}

# agents_backup_dir: where local mode keeps its backup, inside this worktree's git dir
# (git rev-parse --git-path ai-harness), where git clean, git stash -a, and checkouts never reach.
# One per install prefix (backup-<prefix>, / turned into _), so subdirectory installs don't
# collide. Known limit: prefixes a/b and a_b map to the same directory.
# install.sh computes the same path; keep the two in step.
agents_backup_dir() {
  local gd pfx
  gd="$(git -C "$AGENTS_ROOT" rev-parse --git-path ai-harness 2>/dev/null)" || return 1
  [ -n "$gd" ] || return 1
  case "$gd" in /*) ;; *) gd="$AGENTS_ROOT/$gd" ;; esac
  pfx="$(git -C "$AGENTS_ROOT" rev-parse --show-prefix 2>/dev/null || true)"
  pfx="${pfx%/}"
  printf '%s/backup%s\n' "$gd" "${pfx:+-$(printf '%s' "$pfx" | tr '/' '_')}"
}

# agents_backup [quiet]: local mode only. Copies .agents/ (minus the cache, eval results, and
# builtin/, which install.sh rebuilds on every run, restores included) and the untracked root
# instruction files into agents_backup_dir, replacing the previous copy.
# A local AGENTS.md the project has since started tracking is kept aside once, as
# AGENTS.md.before-tracked, with a warning. Only sync does that: with quiet (verify), the old
# backup's AGENTS.md is carried forward unchanged and nothing is printed, so the one-time
# warning isn't lost in verify's output. Always returns 0: a backup must never fail its caller,
# so any copy problem (say tar reporting a file that changed while read) skips this refresh
# silently and leaves the previous backup in place.
agents_backup() {
  [ "${HARNESS_MODE:-team}" = local ] || return 0
  [ -d "$AGENTS_ROOT/.agents" ] || return 0
  local quiet="${1:-}" dest tmp f o
  dest="$(agents_backup_dir)" || return 0
  # A run killed mid-swap leaves only $dest.old.*: put it back before cleaning up.
  if [ ! -e "$dest" ]; then
    for o in "$dest".old.*; do [ -d "$o/.agents" ] && mv "$o" "$dest" 2>/dev/null && break; done
  fi
  rm -rf "$dest".new.* "$dest".old.* 2>/dev/null || true
  tmp="$dest.new.$$"
  mkdir -p "$tmp" 2>/dev/null || return 0
  if ! (cd "$AGENTS_ROOT" && tar -cf - --exclude .agents/cache --exclude .agents/evals/results --exclude .agents/builtin .agents) 2>/dev/null \
       | tar -C "$tmp" -xf - 2>/dev/null || [ ! -d "$tmp/.agents" ]; then
    rm -rf "$tmp"; return 0
  fi
  for f in AGENTS.md CLAUDE.md CLAUDE.local.md; do
    if git -C "$AGENTS_ROOT" ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
      if [ -f "$dest/$f.before-tracked" ]; then
        cp "$dest/$f.before-tracked" "$tmp/" 2>/dev/null || true
      elif [ "$f" != AGENTS.md ] || [ ! -f "$dest/$f" ]; then
        :
      elif [ "$quiet" = quiet ]; then
        cp "$dest/$f" "$tmp/$f" 2>/dev/null || true
      elif ! cmp -s "$dest/$f" "$AGENTS_ROOT/$f" && cp "$dest/$f" "$tmp/$f.before-tracked" 2>/dev/null; then
        echo "sync: warning: the project now tracks AGENTS.md; your last local copy is in $dest/AGENTS.md.before-tracked; move its facts into .agents/AGENTS.local.md" >&2
      fi
    elif [ -f "$AGENTS_ROOT/$f" ]; then
      cp "$AGENTS_ROOT/$f" "$tmp/$f" 2>/dev/null || true
    fi
  done
  # Swap in the new copy so a whole backup stays on disk (as $dest or $dest.old.*) if killed midway.
  if [ -e "$dest" ] && ! mv "$dest" "$dest.old.$$" 2>/dev/null; then rm -rf "$tmp"; return 0; fi
  if mv "$tmp" "$dest" 2>/dev/null; then
    rm -rf "$dest.old.$$"
  else
    rm -rf "$tmp"; mv "$dest.old.$$" "$dest" 2>/dev/null || true
  fi
  return 0
}

# agents_hash: content hash of stdin (git's blob hash; git is the one tool we can count on)
agents_hash() { git hash-object --stdin; }

# agents_changed_files: repo-relative paths added/modified/renamed vs HEAD, plus untracked
agents_changed_files() {
  (
    cd "$AGENTS_ROOT" || exit 0
    if git rev-parse -q --verify HEAD >/dev/null 2>&1; then
      git diff --name-only --diff-filter=ACMR HEAD --
    else
      git ls-files -m
    fi
    git ls-files -o --exclude-standard
  ) | LC_ALL=C sort -u
}

# agents_state_key [files...]: hash of everything a check result can depend on: the file list
# asked about, HEAD, the whole working-tree diff, untracked files, harness config, the library
# search path, and every active pack as resolved (in the repo or not).
# Whole-tree on purpose: a result for one file can depend on others (headers, a requirements export).
# Gitignored inputs count too: plan ledgers, and whatever a workflow pack's checks/state.sh prints.
agents_state_key() {
  (
    cd "$AGENTS_ROOT" || exit 0
    printf '%s\n' "$@"
    git rev-parse -q --verify HEAD 2>/dev/null || echo "no-head"
    git diff --binary HEAD 2>/dev/null
    git ls-files -o --exclude-standard -z | xargs -0 git hash-object -- 2>/dev/null
    cat .agents/harness.conf .agents/checks/* .agents/baselines/* .agents/guard.allow .agents/stacks/*/* 2>/dev/null
    agents_libraries 2>/dev/null
    agents_pack_state 2>/dev/null
    cat .agents/plans/*/tasks.json 2>/dev/null
    # local mode: the instruction copies verify compares (git ignores them), and their source
    cat .agents/AGENTS.local.md AGENTS.override.md GEMINI.md .cursor/rules/ai-harness.mdc .github/instructions/ai-harness.instructions.md 2>/dev/null
    for w in $(agents_conf_get .agents/harness.conf WORKFLOWS 2>/dev/null); do
      d="$(agents_resolve workflows "$w" 2>/dev/null)" || continue
      if [ -f "$d/checks/state.sh" ]; then bash "$d/checks/state.sh" 2>/dev/null; fi
    done
    true
  ) | agents_hash
}

# agents_pack_state: each active workflow and stack as resolved (name, path, every file, read
# through symlinks), so an edit to a pack in any library counts for the cache key
agents_pack_state() {
  local k kind n p f
  for k in WORKFLOWS STACKS; do
    kind=workflows; [ "$k" = STACKS ] && kind=stacks
    for n in $(agents_conf_get "$AGENTS_ROOT/.agents/harness.conf" "$k"); do
      p="$(agents_resolve "$kind" "$n")" || { printf '%s %s unresolved\n' "$k" "$n"; continue; }
      printf '%s %s %s\n' "$k" "$n" "$p"
      find -H "$p" \( -type f -o -type l \) ! -path '*/__pycache__/*' 2>/dev/null | LC_ALL=C sort | while IFS= read -r f; do
        printf '%s\n' "$f"
        cat "$f" 2>/dev/null
      done
    done
  done
  return 0
}

# agents_budget_run <seconds> <outfile> <cmd...>: run cmd with stdout+stderr to outfile.
# 0 seconds means no limit. Returns the command's exit code, or 124 when out of budget.
agents_budget_run() {
  local budget="$1" out="$2" rc=0 t pid wd
  shift 2
  if [ "${budget:-0}" -le 0 ] 2>/dev/null; then
    "$@" >"$out" 2>&1 || rc=$?
    return $rc
  fi
  t="$(command -v timeout || command -v gtimeout || true)"
  if [ -n "$t" ]; then
    "$t" -k 5 "$budget" "$@" >"$out" 2>&1 || rc=$?
    return $rc
  fi
  "$@" >"$out" 2>&1 &
  pid=$!
  ( sleep "$budget"; kill -TERM "$pid" 2>/dev/null; sleep 5; kill -KILL "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  wd=$!
  wait "$pid" || rc=$?
  kill "$wd" 2>/dev/null
  wait "$wd" 2>/dev/null
  if [ "$rc" -eq 143 ] || [ "$rc" -eq 137 ]; then rc=124; fi
  return $rc
}

# agents_worst_rc <rc...>: combine exit codes, worst first: 2 > 1 > 3 > 124 > 0
agents_worst_rc() {
  local best=0 r
  for r in "$@"; do
    case "$r" in
      0) ;;
      2) best=2 ;;
      1) [ "$best" = 2 ] || best=1 ;;
      3) case "$best" in 0|124) best=3 ;; esac ;;
      124) [ "$best" = 0 ] && best=124 ;;
      *) case "$best" in 0|124|3) best=1 ;; esac ;;
    esac
  done
  echo "$best"
}

# agents_log <label> <rawfile>: copy raw output into the log store, print its repo-relative path.
# Named by content hash, so identical output lands at the identical path.
agents_log() {
  local label="$1" raw="$2" h
  mkdir -p "$AGENTS_CACHE/logs"
  h="$(agents_hash < "$raw" | cut -c1-8)"
  cp "$raw" "$AGENTS_CACHE/logs/$label-$h.log"
  ( cd "$AGENTS_CACHE/logs" && ls -t 2>/dev/null | tail -n +41 | while IFS= read -r f; do rm -f "$f"; done )
  echo ".agents/cache/logs/$label-$h.log"
}

# agents_shape <rawfile>: print the findings an agent needs, nothing else.
# Keeps path:line diagnostics (deduplicated, capped per file), test failure markers, sanitizer
# reports with a few frames, and short continuation lines. Drops source excerpts, carets,
# include chains, and progress noise. Falls back to the tail when nothing is recognizable.
agents_shape() {
  awk -v root="$AGENTS_ROOT" -v maxl="${FEEDBACK_MAX_LINES:-30}" -v maxpf="${FEEDBACK_MAX_PER_FILE:-5}" '
    function rel(p) { if (index(p, root "/") == 1) p = substr(p, length(root) + 2); sub(/^(\.\/)+/, "", p); return p }
    function emit(s) { if (n < maxl + 0) out[++n] = s; else dropped++ }
    function unroot(s,   i) { while ((i = index(s, root "/")) > 0) s = substr(s, 1, i - 1) substr(s, i + length(root) + 1); return s }
    function frame(s) { return s ~ /^[ \t]*#[0-9]+ 0x[0-9a-fA-F]+ / }
    { sub(/\r$/, ""); line = $0 }
    line ~ /(ERROR|WARNING): [A-Za-z]+Sanitizer|^SUMMARY: [A-Za-z]+Sanitizer/ {
      if (!(line in seenm)) { seenm[line] = 1; emit(line) }
      frames = 4; cont = 0; prevfind = 0; next
    }
    frame(line) && (frames > 0 || cont > 0) {
      if (index(line, root "/") == 0 || line ~ / in (_start|__libc_start)/) next   # runtime frames
      if (frames > 0) frames--; else cont--
      l = unroot(line); sub(/ 0x[0-9a-fA-F]+ in /, " in ", l)   # addresses change every run
      emit(l); next
    }
    match(line, /^[^ \t:]*[.\/][^ \t:]*:[0-9]+(:[0-9]+)?:/) {
      loc = substr(line, 1, RLENGTH - 1); msg = substr(line, RLENGTH + 1); sub(/^ +/, "", msg)
      split(loc, parts, ":"); path = rel(parts[1]); ln = parts[2]
      if (msg ~ /^note:/) {
        if (!prevfind || notes >= 1) { cont = 0; next }
        notes++
      } else { notes = 0 }
      key = path ":" ln ":" msg
      if (key in seen) { cont = 0; next }
      seen[key] = 1
      if (++perfile[path] > maxpf + 0) { dropped++; cont = 0; next }
      emit(path substr(line, length(parts[1]) + 1))
      cont = 4; prevfind = 1; next
    }
    line ~ /^(FAIL|FAILED|BLOCK|ERROR|error:|fatal:|Error:|infra:|CMake Error|ninja: build stopped|The following tests FAILED:)/ ||
    line ~ /^[ \t]+[0-9]+ - .* \((Failed|SEGFAULT|Timeout|Subprocess aborted|Exception|Not Run|ILLEGAL|Child aborted|OTHER_FAULT)\)/ {
      if (!(line in seenm)) { seenm[line] = 1; emit(line) }
      cont = 0; prevfind = 0; next
    }
    cont > 0 {
      if (line ~ /^[ \t]*$/ || line ~ /^[ \t]*[0-9]* *\| / || line ~ /^[ \t|]*[~^]+[~^ ]*$/ || line ~ /^In file included from/) next
      if (line ~ /^[ \t]+(Start +[0-9]+:|[0-9]+\/[0-9]+ Test)/) next   # ctest progress
      if (line ~ /^[ \t]/ || line ~ /^(Expected|Which is|Value of|Actual)/) { cont--; emit(unroot(line)); next }
      cont = 0
    }
    { if (line != "") tail[++t] = line; prevfind = 0 }
    END {
      if (n == 0) {
        start = t - 14; if (start < 1) start = 1
        for (i = start; i <= t; i++) print tail[i]
      } else {
        for (i = 1; i <= n; i++) print out[i]
        if (dropped > 0) print "(" dropped " more findings in the log)"
      }
    }
  ' "$1"
}

# agents_lint <name> <cmd...>: run a linter, keep only findings not in the baseline.
# For check scripts. Baseline: .agents/baselines/<name>.txt, keyed by path|rule|message
# (no line numbers, so it survives edits). `verify --update-baseline` rewrites baselines.
# Returns 0 clean, 1 new findings (printed), 3 tool missing.
agents_lint() {
  local name="$1" raw base rc=0
  shift
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "infra: $1 not found (needed for $name)"
    return 3
  fi
  raw="$(mktemp)"
  base="$AGENTS_ROOT/.agents/baselines/$name.txt"
  "$@" >"$raw" 2>&1 || rc=$?
  awk -v root="$AGENTS_ROOT" '
    { sub(/\r$/, "") }
    match($0, /^[^ \t:][^:]*:[0-9]+(:[0-9]+)?: (warning|error|style|performance|portability|information): /) {
      split($0, parts, ":"); path = parts[1]
      if (index(path, root "/") == 1) path = substr(path, length(root) + 2)
      msg = substr($0, RLENGTH + 1); rule = ""
      if (match(msg, /\[[^]]+\]$/)) { rule = substr(msg, RSTART + 1, RLENGTH - 2); msg = substr(msg, 1, RSTART - 2) }
      else if (match(msg, /^\[[^]]+\] /)) { rule = substr(msg, 2, RLENGTH - 3); msg = substr(msg, RLENGTH + 1) }
      gsub(/[0-9]+/, "N", msg)
      print path "|" rule "|" msg "\t" path substr($0, length(parts[1]) + 1)
    }
  ' "$raw" | LC_ALL=C sort -t "$(printf '\t')" -k1,1 -u > "$raw.f"
  if [ "${AGENTS_UPDATE_BASELINE:-0}" = 1 ]; then
    mkdir -p "$(dirname "$base")"
    cut -f1 "$raw.f" > "$base"
    echo "baseline $name: $(wc -l < "$base" | tr -d ' ') findings recorded"
    rm -f "$raw" "$raw.f"
    return 0
  fi
  if [ -f "$base" ]; then
    awk -F '\t' 'NR == FNR { b[$0] = 1; next } !($1 in b) { print $2 }' "$base" "$raw.f" > "$raw.new"
  else
    cut -f2- "$raw.f" > "$raw.new"
  fi
  if [ -s "$raw.new" ]; then
    cat "$raw.new"
    rm -f "$raw" "$raw.f" "$raw.new"
    return 1
  fi
  if [ "$rc" -ne 0 ] && [ ! -s "$raw.f" ]; then
    echo "FAIL $name (exit $rc)"
    tail -n 15 "$raw"
    rm -f "$raw" "$raw.f" "$raw.new"
    return 1
  fi
  rm -f "$raw" "$raw.f" "$raw.new"
  return 0
}

# agents_step <name> <cmd...>: run a command quietly; on failure print a FAIL line and its output.
# Returns the command's exit code, except 2 comes back as 1: verify reads 2 as a policy block, and
# for a test binary, make, or a linter, 2 is just a failure. A check that means a policy block
# returns 2 itself, never through agents_step.
agents_step() {
  local name="$1" raw rc=0
  shift
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "infra: $1 not found (needed for $name)"
    return 3
  fi
  raw="$(mktemp)"
  "$@" >"$raw" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "FAIL $name (exit $rc)"
    cat "$raw"
  fi
  rm -f "$raw"
  [ "$rc" -eq 2 ] && rc=1
  return $rc
}

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
# asked about, HEAD, the whole working-tree diff, untracked files, and harness config and packs.
# Whole-tree on purpose: a result for one file can depend on others (headers, a requirements export).
agents_state_key() {
  (
    cd "$AGENTS_ROOT" || exit 0
    printf '%s\n' "$@"
    git rev-parse -q --verify HEAD 2>/dev/null || echo "no-head"
    git diff --binary HEAD 2>/dev/null
    git ls-files -o --exclude-standard -z | xargs -0 git hash-object -- 2>/dev/null
    cat .agents/harness.conf .agents/checks/* .agents/baselines/* .agents/guard.allow \
      .agents/workflows/*/* .agents/workflows/*/checks/* .agents/stacks/*/* 2>/dev/null
  ) | agents_hash
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
  return $rc
}

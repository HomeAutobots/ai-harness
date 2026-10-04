# shellcheck shell=bash
# ai-harness stack pack: python. Harness-owned: ships in .agents/builtin/stacks/python/ and is
# replaced on upgrade. The project's .agents/checks/*.sh (project-owned) source it through the
# shim at .agents/stacks/python/lib.sh.
#
# Deterministic Python feedback from the tools the project already uses: ruff (or black) for
# format and lint, mypy or pyright for types, pytest for tests. Every function follows the
# feedback contract: silent on success, path:line findings on failure, exit 0 clean / 1 findings /
# 3 a tool the project expects is missing, or no tests ran.
#
# Which tools run (each PY_* setting below overrides its guess):
#   format  ruff format when the project configures ruff and formats with it (a [tool.ruff.format]
#           or ruff.toml [format] section, or `ruff format` / ruff-format in pre-commit, a Makefile,
#           justfile, tox, nox, pyproject.toml, or a GitHub workflow); black when pyproject.toml
#           has [tool.black]; otherwise none.
#   lint    ruff with the project's config when it has one (ruff.toml, .ruff.toml, [tool.ruff]).
#           Without one, an installed ruff checks only syntax errors and undefined names
#           (E9, F63, F7, F82, ignoring any user-level config), so the result doesn't depend on
#           whose machine it runs on; with no ruff at all, a syntax check with Python.
#   types   mypy when configured (mypy.ini, .mypy.ini, [tool.mypy], setup.cfg [mypy]); pyright
#           when configured (pyrightconfig.json, [tool.pyright]); otherwise none.
#   tests   pytest.
# A tool the project configures but this machine lacks is a tooling problem (exit 3); one it
# doesn't configure is skipped quietly.
#
# Tools come from the project's environment: PY_RUN when set; else its virtualenv (PY_VENV, else
# $UV_PROJECT_ENVIRONMENT, .venv, venv, $VIRTUAL_ENV, or poetry's), else PATH. pytest, mypy, and
# python import the project, so when it has a virtualenv they must come from it, and a project
# with a lock file (uv.lock, poetry.lock, pdm.lock) but no environment yet is reported, not run
# with whatever is on PATH. ruff, black, and pyright may come from PATH.
#
# Settings: the PY_* below. Put them in .agents/harness.conf, or in a check script (before or
# after sourcing this file, before calling). The check script wins, then harness.conf, then the
# defaults here. A variable already in the environment counts as set by the script, except that
# verify loads harness.conf over it first.

. "$AGENTS_ROOT/.agents/lib/feedback.sh"
PY_PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # wherever this pack's library is
# An older install's feedback.sh (with a newer copy of this pack in a library) has no import, so
# only PY_NO_TESTS comes from harness.conf there.
if command -v agents_conf_import >/dev/null 2>&1; then
  agents_conf_import PY_
elif [ -z "${PY_NO_TESTS+x}" ] && command -v agents_conf_get >/dev/null 2>&1; then
  PY_NO_TESTS="$(agents_conf_get "$AGENTS_ROOT/.agents/harness.conf" PY_NO_TESTS)"
fi

: "${PY_RUN:=}"            # command prefix for the project's tools, e.g. "uv run --frozen" or "hatch run"
: "${PY_VENV:=}"           # the project's virtualenv; empty: $UV_PROJECT_ENVIRONMENT, .venv, venv, $VIRTUAL_ENV, or poetry's
: "${PY_FORMAT:=}"         # ruff, black, or off; empty: what the project uses (see above)
: "${PY_LINT:=}"           # ruff or off; empty: see above. off also drops the syntax check
: "${PY_TYPECHECK:=}"      # mypy, pyright, both ("mypy pyright"), or off; empty: what the project configures
: "${PY_RUFF_ARGS:=}"      # extra args for ruff check, e.g. --select=E,F,B (split on spaces)
: "${PY_MYPY_ARGS:=}"      # extra args for mypy (split on spaces)
: "${PY_PYRIGHT_ARGS:=}"   # extra args for pyright (split on spaces)
: "${PY_PYTEST_ARGS:=}"    # extra args for pytest, read like a command line: -x -m 'not slow'
# PY_NO_TESTS: when pytest collects no tests (exit 5), or pytest isn't there to run them, the test
# steps report it as a tooling problem (exit 3), never a silent ok. Set it to ok when the project
# has no tests, or runs them from the tier scripts itself (agents_step tests py_run ...). Unset:
# reported.

py_py() { python3 "$PY_PACK/py_tools.py" "$@"; }

# py_filter <files...>: the Python sources and stubs among the given files that exist
py_filter() {
  local f
  for f in "$@"; do
    case "$f" in
      *.py|*.pyi) [ -f "$AGENTS_ROOT/$f" ] && printf '%s\n' "$f" ;;
    esac
  done
  return 0
}

# --- the project's environment and tools ---------------------------------------------------

_py_is_venv() { [ -f "$1/pyvenv.cfg" ] || [ -x "$1/bin/python" ] || [ -f "$1/Scripts/python.exe" ]; }
_py_abs() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$AGENTS_ROOT" "$1" ;; esac; }

# _py_env: the bin directory of the project's virtualenv in _PY_BIN, or empty. When the project
# should have one and doesn't (a lock file, a PY_VENV that isn't one), why in _PY_ENV_WHY.
# Looked up once per run. poetry keeps its envs outside the project unless told otherwise, so
# poetry is asked where (only when the project has a poetry.lock).
_py_env() {
  [ -n "${_PY_ENV_DONE:-}" ] && return 0
  _PY_ENV_DONE=1 _PY_BIN="" _PY_ENV_WHY=""
  local d="" c lock
  if [ -n "$PY_VENV" ]; then
    d="$(_py_abs "$PY_VENV")"
    _py_is_venv "$d" || { _PY_ENV_WHY="PY_VENV=$PY_VENV isn't a virtualenv"; return 0; }
  else
    for c in "${UV_PROJECT_ENVIRONMENT:-}" .venv venv "${VIRTUAL_ENV:-}"; do
      [ -n "$c" ] || continue
      c="$(_py_abs "$c")"
      if _py_is_venv "$c"; then d="$c"; break; fi
    done
    if [ -z "$d" ] && [ -f "$AGENTS_ROOT/poetry.lock" ] && command -v poetry >/dev/null 2>&1; then
      d="$(cd "$AGENTS_ROOT" && poetry env info -p 2>/dev/null)" || d=""
    fi
  fi
  if [ -z "$d" ]; then
    for lock in uv.lock poetry.lock pdm.lock; do
      [ -f "$AGENTS_ROOT/$lock" ] || continue
      case "$lock" in
        uv.lock) c="uv sync" ;;
        poetry.lock) c="poetry install" ;;
        pdm.lock) c="pdm install" ;;
      esac
      _PY_ENV_WHY="the project has $lock but no environment yet; $c makes one"
      break
    done
    return 0
  fi
  if [ -d "$d/bin" ]; then _PY_BIN="$d/bin"; elif [ -d "$d/Scripts" ]; then _PY_BIN="$d/Scripts"; fi
  return 0
}

# _py_direct <tool>: the tool from the project's virtualenv, else PATH, in PY_CMD (never PY_RUN).
_py_direct() {
  _py_env
  PY_CMD=()
  if [ -n "$_PY_BIN" ] && [ -x "$_PY_BIN/$1" ]; then PY_CMD=("$_PY_BIN/$1")
  elif [ -n "$_PY_BIN" ] && [ -x "$_PY_BIN/$1.exe" ]; then PY_CMD=("$_PY_BIN/$1.exe")
  elif command -v "$1" >/dev/null 2>&1; then PY_CMD=("$1")
  else return 1
  fi
}

# _py_run_has <tool>: whether PY_RUN's environment has the tool. One probe per run, through the
# runner's own Python (python3, else python), since a runner that can't find a tool fails in its
# own way (uv exits 2). A runner that can't start Python at all leaves its error in _PY_RUN_ERR.
_py_run_has() {
  local err out py rc probe='import shutil, sys
print(" ".join(t for t in sys.argv[1:] if shutil.which(t)))'
  if [ -z "${_PY_RUN_DONE:-}" ]; then
    _PY_RUN_DONE=1 _PY_RUN_TOOLS="" _PY_RUN_ERR=""
    err="$(mktemp)"
    for py in python3 python; do
      rc=0
      # shellcheck disable=SC2086
      out="$(cd "$AGENTS_ROOT" && set -f && $PY_RUN "$py" -c "$probe" ruff black mypy pyright pytest python python3 2>"$err")" || rc=$?
      _PY_RUN_TOOLS="$(printf '%s\n' "$out" | tail -n 1)"
      if [ "$rc" -eq 0 ]; then _PY_RUN_ERR=""; break; fi
      _PY_RUN_ERR="failed (exit $rc): $(grep -v '^[[:space:]]*$' "$err" | tail -n 1)"
      _PY_RUN_TOOLS=""
    done
    rm -f "$err"
    _PY_RUN_TOOLS=" $_PY_RUN_TOOLS "
  fi
  case "$_PY_RUN_TOOLS" in *" $1 "*) return 0 ;; esac
  return 1
}

# _py_find <tool>: how to run the tool, in PY_CMD, or 1 with why not in _PY_WHY. Through PY_RUN
# when it's set; else _py_direct, except that pytest, mypy, and python never come from PATH when
# the project has (or should have) an environment of its own.
_py_find() {
  _PY_WHY=""
  if [ -n "$PY_RUN" ]; then
    # shellcheck disable=SC2206
    PY_CMD=($PY_RUN "$1")
    _py_run_has "$1" && return 0
    if [ -n "$_PY_RUN_ERR" ]; then _PY_WHY="PY_RUN ($PY_RUN) $_PY_RUN_ERR"
    else _PY_WHY="PY_RUN ($PY_RUN) doesn't find it"
    fi
    return 1
  fi
  _py_env
  case "$1" in
    pytest|mypy|python|python3)
      if [ -n "$_PY_BIN" ]; then
        { [ -x "$_PY_BIN/$1" ] || [ -x "$_PY_BIN/$1.exe" ]; } && { _py_direct "$1"; return 0; }
        _PY_WHY="not in the project's environment, ${_PY_BIN%/*}"
        return 1
      fi
      [ -n "$_PY_ENV_WHY" ] && { _PY_WHY="$_PY_ENV_WHY"; return 1; } ;;
  esac
  _py_direct "$1" && return 0
  _PY_WHY="${_PY_ENV_WHY:-not on PATH${_PY_BIN:+ or in ${_PY_BIN%/*}}}"
  return 1
}

# _py_python: the project's Python in PY_CMD (python3 before python outside an environment, since
# python can still be Python 2 there)
_py_python() {
  if [ -n "$PY_RUN" ]; then _py_find python3 || _py_find python; return $?; fi
  _py_env
  if [ -n "$_PY_BIN" ]; then _py_find python || _py_find python3; return $?; fi
  _py_find python3 || _py_find python
}

# py_run <tool> [args...]: run one of the project's tools the way the stack does, from the project
# root. A tool's or runner's exit 2 comes back as 1: verify reads 2 as a policy block. For tier
# scripts: agents_step tests py_run python -m unittest discover -s tests
py_run() {
  local tool="$1" rc=0
  shift
  if [ "$tool" = python ]; then _py_python; else _py_find "$tool"; fi \
    || { echo "infra: $tool not found${_PY_WHY:+: $_PY_WHY}"; return 3; }
  (cd "$AGENTS_ROOT" && "${PY_CMD[@]}" "$@") || rc=$?
  [ "$rc" -eq 2 ] && rc=1
  return "$rc"
}

# _py_missing <tool> <what expects it> <setting>: a tool the project expects isn't here (exit 3)
_py_missing() {
  echo "infra: $1 not found${_PY_WHY:+ ($_PY_WHY)}, and $2. Install it in the project's environment, point PY_VENV or PY_RUN at the one that has it, or set $3=off"
  return 3
}

# --- what the project configures ----------------------------------------------------------

_py_pyproject_has() { [ -f "$AGENTS_ROOT/pyproject.toml" ] && grep -Eq "$1" "$AGENTS_ROOT/pyproject.toml"; }

# _py_conf_key <file> <section> <key>: the TOML or INI file sets key in [section] itself
_py_conf_key() {
  [ -f "$AGENTS_ROOT/$1" ] || return 1
  awk -v s="$2" -v k="$3" '
    /^[ \t]*\[/ { sec = $0; sub(/^[ \t]*\[+[ \t]*/, "", sec); sub(/[ \t]*\].*$/, "", sec); next }
    sec == s { l = $0; sub(/^[ \t]+/, "", l); if (index(l, k) == 1) { r = substr(l, length(k) + 1); if (r ~ /^[ \t]*=/) f = 1 } }
    END { exit !f }' "$AGENTS_ROOT/$1"
}

# _py_ruff_conf: where the project configures ruff (prints it), or returns 1
_py_ruff_conf() {
  local f
  for f in ruff.toml .ruff.toml; do [ -f "$AGENTS_ROOT/$f" ] && { echo "$f"; return 0; }; done
  _py_pyproject_has '^[[:space:]]*\[tool\.ruff[].]' && { echo pyproject.toml; return 0; }
  return 1
}

# _py_ruff_formats: the project formats with ruff (a format section, or ruff format in its tooling)
_py_ruff_formats() {
  local f
  _py_pyproject_has '^[[:space:]]*\[tool\.ruff\.format\]' && return 0
  for f in ruff.toml .ruff.toml; do
    [ -f "$AGENTS_ROOT/$f" ] && grep -Eq '^[[:space:]]*\[format\]' "$AGENTS_ROOT/$f" && return 0
  done
  for f in .pre-commit-config.yaml Makefile makefile justfile tox.ini noxfile.py pyproject.toml .github/workflows/*.yml .github/workflows/*.yaml; do
    [ -f "$AGENTS_ROOT/$f" ] || continue
    grep -Eq 'ruff[[:space:]]+format|ruff-format' "$AGENTS_ROOT/$f" && return 0
  done
  return 1
}

# _py_format_tool: ruff, black, or off (PY_FORMAT, else what the project uses)
_py_format_tool() {
  case "$PY_FORMAT" in
    ruff|black|off) echo "$PY_FORMAT"; return 0 ;;
    "") ;;
    *) echo "infra: PY_FORMAT='$PY_FORMAT' isn't ruff, black, or off" >&2; echo off; return 3 ;;
  esac
  if _py_ruff_conf >/dev/null && _py_ruff_formats; then echo ruff
  elif _py_pyproject_has '^[[:space:]]*\[tool\.black\]'; then echo black
  else echo off
  fi
}

# _py_mypy_conf: the file holding the project's mypy config (prints it), or returns 1
_py_mypy_conf() {
  local f
  for f in mypy.ini .mypy.ini; do
    [ -f "$AGENTS_ROOT/$f" ] && grep -Eq '^[[:space:]]*\[mypy\]' "$AGENTS_ROOT/$f" && { echo "$f"; return 0; }
  done
  _py_pyproject_has '^[[:space:]]*\[tool\.mypy[].]' && { echo pyproject.toml; return 0; }
  [ -f "$AGENTS_ROOT/setup.cfg" ] && grep -Eq '^[[:space:]]*\[mypy[]-]' "$AGENTS_ROOT/setup.cfg" && { echo setup.cfg; return 0; }
  return 1
}

# _py_pyright_conf: likewise for pyright
_py_pyright_conf() {
  [ -f "$AGENTS_ROOT/pyrightconfig.json" ] && { echo pyrightconfig.json; return 0; }
  _py_pyproject_has '^[[:space:]]*\[tool\.pyright\]' && { echo pyproject.toml; return 0; }
  return 1
}

# _py_type_scope <tool>: how the project's config scopes the checker, from the one config file the
# checker reads: files (mypy's files =; pyright's include or exclude, its own discovery applies
# both), exclude (mypy's exclude = alone: discovery from the root applies it), or nothing. A
# scoped checker runs on its scope, so changed files outside it aren't held to its settings
# (neither checker applies an exclude to files named on its command line).
_py_type_scope() {
  local f s
  case "$1" in
    mypy)
      f="$(_py_mypy_conf)" || return 0
      s=mypy; [ "$f" = pyproject.toml ] && s=tool.mypy
      if _py_conf_key "$f" "$s" files; then echo files
      elif _py_conf_key "$f" "$s" exclude; then echo exclude
      fi ;;
    pyright)
      f="$(_py_pyright_conf)" || return 0
      if [ "$f" = pyrightconfig.json ]; then
        grep -Eq '"(include|exclude)"' "$AGENTS_ROOT/$f" && echo files
      elif _py_conf_key "$f" tool.pyright include || _py_conf_key "$f" tool.pyright exclude; then
        echo files
      fi ;;
  esac
  return 0
}

# _py_type_tools: mypy and/or pyright, or nothing (PY_TYPECHECK, else what the project configures)
_py_type_tools() {
  local t out=""
  case "$PY_TYPECHECK" in
    off) return 0 ;;
    "") ;;
    *)
      for t in $PY_TYPECHECK; do
        case "$t" in
          mypy|pyright) out="$out $t" ;;
          *) echo "infra: PY_TYPECHECK='$PY_TYPECHECK' isn't mypy, pyright, or off" >&2; return 3 ;;
        esac
      done
      echo "$out"; return 0 ;;
  esac
  _py_mypy_conf >/dev/null && out="$out mypy"
  _py_pyright_conf >/dev/null && out="$out pyright"
  echo "$out"
}

# --- format -------------------------------------------------------------------------------

# _py_fmt_run <ruff|black> <files...>: the formatter's diff turned into one finding per file, at
# the first line it would change (a notebook's cell is named in the message). Parse errors are
# left to the lint step.
_py_fmt_run() {
  local tool="$1" raw rc=0
  shift
  raw="$(mktemp)"
  if [ "$tool" = ruff ]; then
    (cd "$AGENTS_ROOT" && "${PY_CMD[@]}" format --check --diff --force-exclude "$@") >"$raw" 2>&1 || rc=$?
  else
    (cd "$AGENTS_ROOT" && "${PY_CMD[@]}" --check --diff --quiet "$@") >"$raw" 2>&1 || rc=$?
  fi
  # Hunk lengths tell header lines from removed lines that happen to start with "-- ".
  awk -v tool="$tool" '
    function hunk_done() { return old <= 0 && new <= 0 }
    hunk_done() && /^--- / {
      f = substr($0, 5); sub(/\t.*$/, "", f); cell = ""
      if ((i = index(f, ":cell ")) > 0) { cell = substr(f, i + 1) ": "; f = substr(f, 1, i - 1) }
      want = 1; next
    }
    hunk_done() && /^\+\+\+ / { next }
    hunk_done() && /^@@ -[0-9]+/ {
      split($2, a, ","); split($3, b, ",")
      ln = -a[1]; old = (2 in a) ? a[2] + 0 : 1; new = (2 in b) ? b[2] + 0 : 1
      next
    }
    !hunk_done() {
      c = substr($0, 1, 1)
      if (c == "\\") next
      if (c == " ") { old--; new--; ln++; next }
      if (c == "-") old--; else if (c == "+") new--
      if (want) {
        if (ln < 1) ln = 1
        cmd = (tool == "ruff") ? "ruff format" : "black"
        printf "%s:%d: error: %snot formatted the way %s formats it (run: %s %s) [%s-format]\n", f, ln, cell, tool, cmd, f, tool
        want = 0; found = 1
      }
      next
    }
    /^error: (Failed to parse |cannot format .*: Cannot parse)/ { next }
    /^(error|Error|ruff failed|  Cause:)/ { other = 1; print }
    END { exit found ? 1 : (other ? 2 : 0) }
  ' "$raw" || { rc=$?; rm -f "$raw"; return "$rc"; }
  rm -f "$raw"
  return 0
}

# py_format_check <files...>: formatter findings for the given files (agents_lint baseline name:
# ruff-format or black-format), only when the project uses a formatter
py_format_check() {
  local files=() f
  while IFS= read -r f; do files+=("$f"); done < <(py_filter "$@")
  [ ${#files[@]} -gt 0 ] || return 0
  _py_format_checked "${files[@]}"
}

# py_format_check_all: the same for the whole project (the formatter's own file discovery). black
# reads .gitignore but not .git/info/exclude, where local mode hides the harness's own files, so
# its findings for files git ignores are dropped (_py_fmt_seen); ruff reads both.
py_format_check_all() { _py_format_checked .; }

# _py_fmt_seen <ruff|black> .: _py_fmt_run on the whole project, minus findings for files git
# ignores; outside git, all of them
_py_fmt_seen() {
  local tool="$1" seen out rc=0
  shift
  seen="$(mktemp)"
  if ! (cd "$AGENTS_ROOT" && git ls-files -co --exclude-standard) > "$seen" 2>/dev/null; then
    rm -f "$seen"
    _py_fmt_run "$tool" "$@"
    return $?
  fi
  out="$(_py_fmt_run "$tool" "$@")" || rc=$?
  # The list goes in with -v, not as a first file: an empty one would swallow every finding. black
  # names files by absolute path (through symlinks, so /tmp may come out as /private/tmp).
  printf '%s\n' "$out" | r1="$AGENTS_ROOT/" r2="$(cd "$AGENTS_ROOT" && pwd -P)/" awk -v seen="$seen" -v rc="$rc" '
    BEGIN { while ((getline l < seen) > 0) ok[l] = 1 }
    match($0, /^[^ \t:][^:]*:[0-9]+: error: /) {
      f = $0; sub(/:[0-9]+: error: .*/, "", f); rest = substr($0, length(f) + 1)
      if (index(f, ENVIRON["r1"]) == 1) f = substr(f, length(ENVIRON["r1"]) + 1)
      else if (index(f, ENVIRON["r2"]) == 1) f = substr(f, length(ENVIRON["r2"]) + 1)
      sub(/^\.\//, "", f)
      if (!(f in ok)) next
      $0 = f rest; found = 1
    }
    $0 != "" { print; other = other || !/^[^ \t:][^:]*:[0-9]+: error: / }
    END { exit found ? 1 : (rc == 1 ? (other ? 2 : 0) : rc) }'
  rc=$?
  rm -f "$seen"
  return "$rc"
}

_py_format_checked() {
  local tool rc=0 why
  tool="$(_py_format_tool)" || rc=$?
  [ "$rc" -eq 0 ] || return "$rc"
  case "$tool" in
    off) return 0 ;;
    ruff) why="the project formats with it ($(_py_ruff_conf))" ;;
    black) why="pyproject.toml configures it ([tool.black])" ;;
  esac
  [ -n "$PY_FORMAT" ] && why="PY_FORMAT=$PY_FORMAT asks for it"
  _py_find "$tool" || { _py_missing "$tool" "$why" PY_FORMAT; return $?; }
  if [ "$tool" = black ] && [ "$*" = . ]; then
    agents_lint "$tool-format" _py_fmt_seen "$tool" "$@"
  else
    agents_lint "$tool-format" _py_fmt_run "$tool" "$@"
  fi
}

# --- lint ---------------------------------------------------------------------------------

# _py_ruff_ok: ruff in PY_CMD is new enough for --output-format=concise (0.5), or says it isn't (3)
_py_ruff_ok() {
  local v
  v="$(cd "$AGENTS_ROOT" && "${PY_CMD[@]}" --version 2>/dev/null | awk '{ print $2 }')"
  case "$v" in
    0.[0-4].*) echo "infra: ruff $v is older than 0.5, which the python stack needs (--output-format=concise). Upgrade it in the project's environment, or set PY_LINT=off"; return 3 ;;
  esac
  return 0
}

# _py_ruff_run <files...>: ruff check, findings as path:line:col: error: message [CODE]. Never
# fixes anything, whatever the project's config says; respects its excludes. Without the project
# having a ruff config (_PY_RUFF_OWN empty), only syntax errors and undefined names.
_py_ruff_run() {
  local raw rc=0 own=() sel="--select=E9,F63,F7,F82"
  raw="$(mktemp)"
  [ -n "${_PY_RUFF_OWN:-}" ] || own=(--isolated "$sel")
  # shellcheck disable=SC2086
  (cd "$AGENTS_ROOT" && set -f && "${PY_CMD[@]}" check --no-fix --force-exclude --output-format=concise ${own[@]+"${own[@]}"} $PY_RUFF_ARGS "$@") >"$raw" 2>&1 || rc=$?
  awk '
    match($0, /^[^ \t:][^:]*:cell [0-9]+:/) {   # a notebook: nb.ipynb:cell 2:1:8: ...
      pre = substr($0, 1, RLENGTH - 1); i = index(pre, ":cell ")
      cell = substr(pre, i + 1) ": "; $0 = substr(pre, 1, i - 1) substr($0, RLENGTH)
    }
    match($0, /^[^ \t:][^:]*:[0-9]+:[0-9]+: /) {
      loc = substr($0, 1, RLENGTH - 2); rest = substr($0, RLENGTH + 1)
      if (rest ~ /^(invalid-syntax|SyntaxError|E999):? /) { code = "syntax"; sub(/^[^ ]+ /, "", rest) }
      else { code = rest; sub(/ .*/, "", code); sub(/^[^ ]+ /, "", rest); sub(/^\[\*\] /, "", rest) }
      sub(/:$/, "", code)
      print loc ": error: " cell rest " [" code "]"; cell = ""; next
    }
    /^Found [0-9]+ error/ || /^\[\*\] / || /^No fixes available/ || /^All checks passed/ { next }
    { print }
  ' "$raw"
  rm -f "$raw"
  return "$rc"
}

# _py_syntax <files...>: a syntax check with the project's Python, when ruff isn't there to do it
_py_syntax() {
  local rc=0
  # It imports nothing of the project's, so any Python 3 will do when the project's isn't there.
  _py_python || _py_direct python3 || return 0
  (cd "$AGENTS_ROOT" && "${PY_CMD[@]}" -c '
import ast, sys
rc = 0
for f in sys.argv[1:]:
    try:
        with open(f, "rb") as h:
            ast.parse(h.read(), f)
    except SyntaxError as e:
        print("%s:%s:%s: error: %s [syntax]" % (f, e.lineno or 1, e.offset or 1, e.msg))
        rc = 1
sys.exit(rc)
' "$@") 2>&1 || rc=$?
  case "$rc" in
    0|1) return "$rc" ;;
    *) echo "infra: syntax check: ${PY_CMD[*]} exited $rc"; return 3 ;;
  esac
}

# py_lint <files...>: ruff on the given files (new findings only; baseline name: ruff), or a syntax
# check when there's no ruff
py_lint() {
  local files=() f
  while IFS= read -r f; do files+=("$f"); done < <(py_filter "$@")
  [ ${#files[@]} -gt 0 ] || return 0
  _py_linted "${files[@]}"
}

# py_lint_all: ruff over the whole project (its own file discovery, gitignore and excludes)
py_lint_all() { _py_linted .; }

_py_linted() {
  local conf
  _PY_RUFF_OWN=""
  case "$PY_LINT" in
    off) return 0 ;;
    ruff|"") ;;
    *) echo "infra: PY_LINT='$PY_LINT' isn't ruff or off"; return 3 ;;
  esac
  if conf="$(_py_ruff_conf)" || [ "$PY_LINT" = ruff ]; then
    _py_find ruff || { _py_missing ruff "the project configures it (${conf:-PY_LINT=ruff})" PY_LINT; return $?; }
    _PY_RUFF_OWN=1
    _py_ruff_ok || return $?
  elif ! _py_direct ruff || ! _py_ruff_ok >/dev/null; then   # no ruff the project chose: Python's syntax check
    [ "$1" = . ] && return 0
    _py_syntax "$@"
    return $?
  fi
  agents_lint ruff _py_ruff_run "$@"
}

# --- types --------------------------------------------------------------------------------

# _py_keep <output file> <files...>: the checker's lines about the given files (and lines that
# aren't about a file); exit 1 when an error is among them
_py_keep() {
  local out="$1"
  shift
  printf '%s\n' "$@" | awk 'NR == FNR { want[$0] = 1; next }
    match($0, /^[^ \t:][^:]*:[0-9]+(:[0-9]+)?: (error|note): /) {
      split($0, p, ":"); f = p[1]; sub(/^\.\//, "", f)
      if (f in want) { print; if ($0 ~ /: error: /) n++ }
      next
    }
    { print }
    END { exit n ? 1 : 0 }' - "$out"
}

# _py_mypy_run [files...]: mypy, keeping only errors in the given files (mypy follows imports and
# reports on them too). When the project's config scopes mypy (files =, or exclude =), mypy runs
# on that scope, so a changed file outside it isn't held to the project's mypy settings. No files:
# the scope, or the project root.
_py_mypy_run() {
  local raw rc=0 args=("$@")
  raw="$(mktemp)"
  case "$(_py_type_scope mypy)" in
    files) args=() ;;
    exclude) args=(.) ;;
    *) [ $# -eq 0 ] && args=(.) ;;
  esac
  # shellcheck disable=SC2086
  (cd "$AGENTS_ROOT" && set -f && "${PY_CMD[@]}" --no-pretty --no-color-output --no-error-summary $PY_MYPY_ARGS ${args[@]+"${args[@]}"}) >"$raw" 2>&1 || rc=$?
  if [ "$rc" -ne 1 ] || [ $# -eq 0 ]; then
    cat "$raw"; rm -f "$raw"; return "$rc"
  fi
  _py_keep "$raw" "$@" && rc=0
  rm -f "$raw"
  return "$rc"
}

# _py_pyright_run [files...]: pyright's errors as path:line:col: error: message [rule], in the
# given files only (warnings don't fail pyright, so they don't fail this). When the project's
# config scopes pyright (include or exclude), it runs on that scope. Anything else pyright says goes through
# when it fails outright.
_py_pyright_run() {
  local raw rc=0 r=0 proot args=("$@")
  raw="$(mktemp)"
  proot="$(cd "$AGENTS_ROOT" && pwd -P)"
  [ -n "$(_py_type_scope pyright)" ] && args=()
  # shellcheck disable=SC2086
  (cd "$AGENTS_ROOT" && set -f && "${PY_CMD[@]}" $PY_PYRIGHT_ARGS ${args[@]+"${args[@]}"}) >"$raw" 2>&1 || rc=$?
  # A message's later lines are indented with spaces and no-break spaces (bytes 302 240), so
  # awk runs on bytes.
  printf '%s\n' "$@" | r1="$AGENTS_ROOT/" r2="$proot/" LC_ALL=C awk -v rc="$rc" '
    function rel(p) {
      if (index(p, ENVIRON["r1"]) == 1) return substr(p, length(ENVIRON["r1"]) + 1)
      if (index(p, ENVIRON["r2"]) == 1) return substr(p, length(ENVIRON["r2"]) + 1)
      return p
    }
    function flush() {
      if (msg == "") return
      rule = ""
      if (match(msg, / \([A-Za-z]+\)$/)) { rule = substr(msg, RSTART + 2, RLENGTH - 3); msg = substr(msg, 1, RSTART - 1) }
      if (sev == "error" && (!any || (f in want))) { print f ":" ln ": error: " msg (rule != "" ? " [" rule "]" : ""); n++ }
      msg = ""
    }
    NR == FNR { if ($0 != "") { want[$0] = 1; any = 1 } next }
    /^ +[^ ].*:[0-9]+:[0-9]+ - (error|warning|information): / {
      flush()
      line = $0; sub(/^ +/, "", line)
      i = index(line, " - "); loc = substr(line, 1, i - 1); rest = substr(line, i + 3)
      sev = rest; sub(/:.*/, "", sev); msg = substr(rest, length(sev) + 3)
      j = match(loc, /:[0-9]+:[0-9]+$/); f = rel(substr(loc, 1, j - 1)); ln = substr(loc, j + 1)
      next
    }
    msg != "" && /^  / {
      l = $0; nb = "\302\240"
      while (substr(l, 1, 1) == " " || substr(l, 1, 2) == nb) l = substr(l, (substr(l, 1, 1) == " ") ? 2 : 3)
      if (l != "") msg = msg "; " l
      next
    }
    { flush(); if (rc != 0 && rc != 1) print }
    END { flush(); exit n ? 1 : 0 }
  ' - "$raw" || r=$?
  rm -f "$raw"
  case "$rc" in 0|1) return "$r" ;; esac
  return "$rc"
}

# py_typecheck <files...>: the project's type checker on the given files (new findings only;
# baseline names: mypy, pyright)
py_typecheck() {
  local files=() f
  while IFS= read -r f; do files+=("$f"); done < <(py_filter "$@")
  [ ${#files[@]} -gt 0 ] || return 0
  _py_typechecked "${files[@]}"
}

# py_typecheck_all: the type checker over what the project's config names (mypy: its files
# setting, else the project root; pyright: its include)
py_typecheck_all() { _py_typechecked; }

_py_typechecked() {
  local tools t rc=0 r conf
  tools="$(_py_type_tools)" || return 3
  for t in $tools; do
    r=0
    if ! _py_find "$t"; then
      if [ -n "$PY_TYPECHECK" ]; then conf="PY_TYPECHECK=$PY_TYPECHECK asks for it"
      else conf="the project configures it ($("_py_${t}_conf"))"
      fi
      _py_missing "$t" "$conf" PY_TYPECHECK || r=$?
    else
      agents_lint "$t" "_py_${t}_run" ${@+"$@"} || r=$?
    fi
    rc="$(agents_worst_rc "$rc" "$r")"
  done
  return "$rc"
}

# --- tests --------------------------------------------------------------------------------

# _py_test_count: how many files match pytest's default names (test_*.py, *_test.py) among the
# files git sees (tracked, or untracked and not ignored); 0 when git can't list them either
_py_test_count() {
  (cd "$AGENTS_ROOT" && git ls-files -co --exclude-standard -- '*.py' 2>/dev/null) \
    | awk '{ n = split($0, p, "/"); b = p[n] } b ~ /^test_.*\.py$/ || b ~ /_test\.py$/ { c++ } END { print c + 0 }'
}

# _py_no_tests <why>: says no tests ran and returns 3, or returns 1 to skip quietly (PY_NO_TESTS=ok)
_py_no_tests() {
  [ "${PY_NO_TESTS:-}" = ok ] && return 1
  echo "infra: tests: no tests ran: $1. Add tests, or run them from .agents/checks/*.sh (agents_step tests py_run ...) and set PY_NO_TESTS=ok there; if the project has no tests, a person sets PY_NO_TESTS=ok in .agents/harness.conf"
  return 3
}

# _py_tests_ready: pytest in PY_CMD and 0, or why not: 3 (said), or 1 to skip quietly. Without
# pytest, PY_NO_TESTS=ok skips too: the project runs its tests some other way.
_py_tests_ready() {
  _py_find pytest && return 0
  [ "${PY_NO_TESTS:-}" = ok ] && return 1
  if [ "$(_py_test_count)" = 0 ]; then
    _py_no_tests "no test_*.py or *_test.py files, and no pytest${_PY_WHY:+ ($_PY_WHY)}"
    return $?
  fi
  echo "infra: pytest not found${_PY_WHY:+ ($_PY_WHY)}, and the project has test files. Install it in the project's environment, or point PY_VENV or PY_RUN at the one that has it; tests that run another way go in .agents/checks/*.sh with PY_NO_TESTS=ok"
  return 3
}

# _py_pytest_run [args...]: pytest from the project root, its output with timings taken out (they
# change every run), traceback frames outside the project dropped (pytest names them by absolute
# path: the standard library, site-packages), and assertion detail kept under the failing line
_py_pytest_run() {
  local raw rc=0
  raw="$(mktemp)"
  # shellcheck disable=SC2086
  (cd "$AGENTS_ROOT" && eval '"${PY_CMD[@]}" -q --tb=short --color=no '"$PY_PYTEST_ARGS"' "$@"') >"$raw" 2>&1 || rc=$?
  awk '
    /^[0-9.]+s (call|setup|teardown) / { next }
    /^=* *slowest .*durations/ { next }
    / in [0-9.]+s( \([0-9:]+\))? *=*$/ { sub(/ in [0-9.]+s( \([0-9:]+\))?/, "") }
    /^\/[^:]*:[0-9]+: in / { skip = 1; next }
    skip && /^    / && !/^E   / { next }
    { skip = 0 }
    /^E   / { print "    " $0; next }
    { print }
  ' "$raw"
  rm -f "$raw"
  return "$rc"
}

# _py_pytest <step> [args...]: run pytest as a step. 0 passed; 1 failures, collection errors, or a
# conftest that doesn't load; 3 pytest itself failed; 5 nothing collected (the caller decides).
_py_pytest() {
  local step="$1" out rc=0
  shift
  out="$(mktemp)"
  _py_pytest_run "$@" >"$out" 2>&1 || rc=$?
  # pytest exits 4 (usage) when a conftest it loads first raises: the code's problem, not pytest's
  if [ "$rc" -eq 4 ] && grep -Eq 'ImportError while loading conftest|conftest\.py:[0-9]+' "$out"; then rc=1; fi
  case "$rc" in
    0|5) ;;
    1|2) echo "FAIL $step (exit $rc)"; cat "$out"; rc=1 ;;
    *) echo "infra: $step: pytest exit $rc (internal or usage error; check PY_PYTEST_ARGS)"; cat "$out"; rc=3 ;;
  esac
  rm -f "$out"
  return "$rc"
}

# _py_tests_all <step>: every test pytest collects; none at all is "no tests ran"
_py_tests_all() {
  local rc=0
  _py_pytest "$1" || rc=$?
  [ "$rc" -eq 5 ] || return "$rc"
  _py_no_tests "pytest collected none" && return 0
  rc=$?
  [ "$rc" -eq 1 ] && return 0
  return "$rc"
}

# py_test_affected <files...>: only the test files the change can reach (py_tools.py affected:
# a changed test file runs itself, a changed module the tests that import it, and anything the
# import scan can't place runs everything). No python3 for the scan: everything.
py_test_affected() {
  local rc=0 sel f list=()
  _py_tests_ready || { rc=$?; [ "$rc" -eq 1 ] && return 0; return "$rc"; }
  sel="$(py_py affected "$AGENTS_ROOT" ${@+"$@"} 2>/dev/null)" || sel=ALL
  [ -n "$sel" ] || sel=ALL
  case "$sel" in
    NONE) return 0 ;;
    ALL) _py_tests_all tests; return $? ;;
  esac
  while IFS= read -r f; do [ -n "$f" ] && list+=("$f"); done <<EOF
$sel
EOF
  _py_pytest tests "${list[@]}" || rc=$?
  if [ "$rc" -eq 5 ]; then   # the selected files hold no tests after all: run them all
    _py_tests_all tests; return $?
  fi
  return "$rc"
}

# py_test_all: every test pytest collects
py_test_all() {
  local rc=0
  _py_tests_ready || { rc=$?; [ "$rc" -eq 1 ] && return 0; return "$rc"; }
  _py_tests_all tests
}

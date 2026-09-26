# shellcheck shell=bash
# ai-harness stack pack: cpp-cmake. Harness-owned: lives in .agents/stacks/cpp-cmake/ and is
# replaced on upgrade. Sourced by the project's .agents/checks/*.sh, which stay project-owned.
#
# Deterministic C/C++ feedback built from the compiler, CMake, CTest, and static analyzers.
# Every function follows the feedback contract: silent on success, path:line findings on
# failure, exit 0 clean / 1 findings / 3 tool missing. Override the settings below in the
# check scripts (before calling) or in .agents/harness.conf.

. "$AGENTS_ROOT/.agents/lib/feedback.sh"
CPP_PACK="$AGENTS_ROOT/.agents/stacks/cpp-cmake"

: "${CPP_BUILD_DIR:=build-agent}"          # agent-owned build tree (never your own build dir)
: "${CPP_SAN_DIR:=build-agent-asan}"       # sanitizer build tree
: "${CPP_BUILD_TYPE:=Debug}"
: "${CPP_CMAKE_ARGS:=}"                    # extra configure args, e.g. -DBUILD_TESTING=ON
: "${CPP_JOBS:=}"                          # parallelism; empty = let the generator decide
: "${CPP_TIDY_CONFIG:=}"                   # empty: repo .clang-tidy if present, else the pack's curated profile
: "${CPP_TIDY_ARGS:=}"                     # e.g. --extra-arg=-Wno-unknown-warning-option
: "${CPP_SANITIZERS:=address,undefined}"
: "${CPP_TEST_TIMEOUT:=120}"               # per-test timeout, seconds
: "${CPP_CPPCHECK_ARGS:=}"                 # e.g. --addon=misra (MISRA needs the rule texts file)

cpp_py() { python3 "$CPP_PACK/cpp_tools.py" "$@"; }

cpp_nproc() { getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2; }

# cpp_filter <files...>: the C/C++ sources and headers among the given files
cpp_filter() {
  local f
  for f in "$@"; do
    case "$f" in
      *.c|*.cc|*.cpp|*.cxx|*.c++|*.h|*.hh|*.hpp|*.hxx|*.h++|*.inl|*.ipp|*.tpp)
        [ -f "$AGENTS_ROOT/$f" ] && printf '%s\n' "$f" ;;
    esac
  done
}

# Keep the agent build trees out of `git status` without touching the project's .gitignore.
_cpp_exclude() {
  local ex
  ex="$(git -C "$AGENTS_ROOT" rev-parse --git-path info/exclude 2>/dev/null)" || return 0
  case "$ex" in /*) ;; *) ex="$AGENTS_ROOT/$ex" ;; esac
  mkdir -p "$(dirname "$ex")"
  grep -qxF '/build-agent*/' "$ex" 2>/dev/null || printf '%s\n' '/build-agent*/' >> "$ex"
  grep -qxF '/compile_commands.json' "$ex" 2>/dev/null || printf '%s\n' '/compile_commands.json' >> "$ex"
}

# cpp_configure <dir> [cmake args...]: configure once; later builds re-run CMake themselves.
# Also asks CMake's file API for the codemodel, which affected-test selection reads.
cpp_configure() {
  local dir="$1" b
  shift
  command -v cmake >/dev/null 2>&1 || { echo "infra: cmake not found"; return 3; }
  _cpp_exclude
  b="$AGENTS_ROOT/$dir"
  mkdir -p "$b/.cmake/api/v1/query"
  if [ ! -f "$b/.cmake/api/v1/query/codemodel-v2" ]; then
    : > "$b/.cmake/api/v1/query/codemodel-v2"
    [ -f "$b/CMakeCache.txt" ] && cmake "$b" >/dev/null 2>&1
  fi
  [ -f "$b/CMakeCache.txt" ] && return 0
  local args=(-S "$AGENTS_ROOT" -B "$b" -DCMAKE_BUILD_TYPE="$CPP_BUILD_TYPE" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON)
  command -v ninja >/dev/null 2>&1 && args+=(-G Ninja)
  command -v ccache >/dev/null 2>&1 && args+=(-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
  # shellcheck disable=SC2206
  [ -n "$CPP_CMAKE_ARGS" ] && args+=($CPP_CMAKE_ARGS)
  agents_step configure cmake "${args[@]}" "$@" || { rm -f "$b/CMakeCache.txt"; return 1; }
  # clangd and other tools look for compile_commands.json at the root
  if [ "$dir" = "$CPP_BUILD_DIR" ] && [ ! -e "$AGENTS_ROOT/compile_commands.json" ]; then
    ln -s "$dir/compile_commands.json" "$AGENTS_ROOT/compile_commands.json" 2>/dev/null || true
  fi
}

# cpp_format_check <files...>: clang-format violations, only if the repo defines a style
cpp_format_check() {
  local files=() f
  while IFS= read -r f; do files+=("$AGENTS_ROOT/$f"); done < <(cpp_filter "$@")
  [ ${#files[@]} -gt 0 ] || return 0
  [ -f "$AGENTS_ROOT/.clang-format" ] || [ -f "$AGENTS_ROOT/_clang-format" ] || return 0
  command -v clang-format >/dev/null 2>&1 || { echo "infra: clang-format not found"; return 3; }
  local out rc=0
  out="$(clang-format --dry-run --Werror "${files[@]}" 2>&1)" || rc=$?
  [ "$rc" -eq 0 ] && return 0
  printf '%s\n' "$out" | grep -E '^[^ ].*:[0-9]+:[0-9]+: (error|warning):' | sed 's/$/  (run clang-format -i on the file)/'
  return 1
}

# cpp_diagnose <files...>: -fsyntax-only compile of the changed sources with their exact
# compile_commands.json entry (respects cross compilers and flags). Errors fail; so do new
# warnings in the changed files (baseline name: build-warnings). Independent of build state, so
# a warning keeps showing until it's fixed, not just on the build that recompiled the file.
# Header-only edits are covered by the build. Skipped until the agent build tree exists.
cpp_diagnose() {
  local db="$AGENTS_ROOT/$CPP_BUILD_DIR/compile_commands.json"
  [ -f "$db" ] || return 0
  local srcs=() f raw rc=0
  while IFS= read -r f; do
    case "$f" in *.c|*.cc|*.cpp|*.cxx|*.c++) srcs+=("$f") ;; esac
  done < <(cpp_filter "$@")
  [ ${#srcs[@]} -gt 0 ] || return 0
  raw="$(mktemp)"
  cpp_py syntax "$AGENTS_ROOT" "$AGENTS_ROOT/$CPP_BUILD_DIR" "${srcs[@]}" >"$raw" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    cat "$raw"
    rm -f "$raw"
    return "$rc"
  fi
  cpp_filter "$@" > "$raw.changed"
  awk -v root="$AGENTS_ROOT" 'NR == FNR { c[$0] = 1; next }
    match($0, /^[^ :][^:]*:[0-9]+:[0-9]+: warning: /) {
      split($0, p, ":"); f = p[1]; if (index(f, root "/") == 1) f = substr(f, length(root) + 2)
      if (f in c) print
    }' "$raw.changed" "$raw" > "$raw.warn"
  agents_lint build-warnings cat "$raw.warn" || rc=$?
  rm -f "$raw" "$raw.changed" "$raw.warn"
  return "$rc"
}

# cpp_build: incremental build of the agent build tree. Fails on errors.
cpp_build() {
  cpp_configure "$CPP_BUILD_DIR" || return $?
  agents_step build cmake --build "$AGENTS_ROOT/$CPP_BUILD_DIR" -j "${CPP_JOBS:-$(cpp_nproc)}"
}

# cpp_tidy_changed <files...>: clang-tidy on changed lines only (line filter from git diff)
cpp_tidy_changed() {
  local db="$AGENTS_ROOT/$CPP_BUILD_DIR"
  [ -f "$db/compile_commands.json" ] || return 0
  local all=() srcs=() f
  while IFS= read -r f; do
    all+=("$f")
    case "$f" in *.c|*.cc|*.cpp|*.cxx|*.c++) srcs+=("$AGENTS_ROOT/$f") ;; esac
  done < <(cpp_filter "$@")
  [ ${#srcs[@]} -gt 0 ] || return 0
  command -v clang-tidy >/dev/null 2>&1 || { echo "infra: clang-tidy not found"; return 3; }
  local cfg=()
  if [ -n "$CPP_TIDY_CONFIG" ]; then cfg=(--config-file="$CPP_TIDY_CONFIG")
  elif [ ! -f "$AGENTS_ROOT/.clang-tidy" ]; then cfg=(--config-file="$CPP_PACK/clang-tidy.agent")
  fi
  local filter
  filter="$(cpp_py line-filter "$AGENTS_ROOT" "${all[@]}")"
  # shellcheck disable=SC2086
  agents_lint clang-tidy clang-tidy -p "$db" --quiet --header-filter='.*' --line-filter="$filter" \
    ${cfg[@]+"${cfg[@]}"} $CPP_TIDY_ARGS "${srcs[@]}"
}

_cpp_ctest() {  # _cpp_ctest <build dir> [ctest args...]
  local b="$1"
  shift
  (cd "$b" && ctest --output-on-failure --timeout "$CPP_TEST_TIMEOUT" -j "${CPP_JOBS:-$(cpp_nproc)}" "$@")
}

# cpp_test_affected <files...>: run only the tests whose targets depend on the changed sources.
# Falls back to all tests for header, CMake, or unknown changes, and when the codemodel is missing.
cpp_test_affected() {
  local b="$AGENTS_ROOT/$CPP_BUILD_DIR" sel
  [ -f "$b/CTestTestfile.cmake" ] || return 0
  sel="$(cpp_py affected "$AGENTS_ROOT" "$b" "$@")" || sel=ALL
  case "$sel" in
    NONE) return 0 ;;
    ALL) agents_step tests _cpp_ctest "$b" ;;
    *) agents_step tests _cpp_ctest "$b" -R "$sel" ;;
  esac
}

# cpp_test_all: every test in the agent build tree
cpp_test_all() {
  local b="$AGENTS_ROOT/$CPP_BUILD_DIR"
  [ -f "$b/CTestTestfile.cmake" ] || return 0
  agents_step tests _cpp_ctest "$b"
}

# cpp_sanitize: separate ASan+UBSan build, all tests. Findings arrive as sanitizer reports
# with the faulting file:line, which is what repair loops need.
cpp_sanitize() {
  local f="-fsanitize=$CPP_SANITIZERS -fno-omit-frame-pointer -fno-sanitize-recover=all"
  cpp_configure "$CPP_SAN_DIR" "-DCMAKE_C_FLAGS=$f" "-DCMAKE_CXX_FLAGS=$f" \
    "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=$CPP_SANITIZERS" "-DCMAKE_SHARED_LINKER_FLAGS=-fsanitize=$CPP_SANITIZERS" || return $?
  local b="$AGENTS_ROOT/$CPP_SAN_DIR"
  agents_step sanitizer-build cmake --build "$b" -j "${CPP_JOBS:-$(cpp_nproc)}" || return 1
  [ -f "$b/CTestTestfile.cmake" ] || return 0
  # No detect_leaks=1: ASan already enables leak checks where LeakSanitizer exists (Linux), and
  # Apple clang aborts every binary at startup when it's set ("not supported on this platform").
  ASAN_OPTIONS="${ASAN_OPTIONS:-abort_on_error=1}" \
  UBSAN_OPTIONS="${UBSAN_OPTIONS:-print_stacktrace=1:halt_on_error=1}" \
    agents_step sanitizer-tests _cpp_ctest "$b"
}

# cpp_cppcheck: cppcheck over the compile database, new findings only (baseline: cppcheck)
cpp_cppcheck() {
  cpp_configure "$CPP_BUILD_DIR" || return $?
  local b="$AGENTS_ROOT/$CPP_BUILD_DIR"
  mkdir -p "$b/cppcheck"
  # shellcheck disable=SC2086
  agents_lint cppcheck cppcheck --project="$b/compile_commands.json" --cppcheck-build-dir="$b/cppcheck" \
    --enable=warning,style,performance,portability --inline-suppr --quiet \
    --suppress=missingIncludeSystem --suppress=unmatchedSuppression -i"$b" \
    --template='{file}:{line}:{column}: {severity}: {message} [{id}]' $CPP_CPPCHECK_ARGS
}

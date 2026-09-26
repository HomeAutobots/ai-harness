#!/usr/bin/env bash
# Edit tier (cpp-cmake). Project-owned: tune freely. Runs after each edit with the edited files.
# clang-format check (only when the repo has a .clang-format) and a syntax-only compile of
# edited sources with their real compile command: errors, plus new warnings in edited files.
# Budget: EDIT_BUDGET.
. "$AGENTS_ROOT/.agents/stacks/cpp-cmake/lib.sh"
rc=0
cpp_format_check "$@" || rc=$?
cpp_diagnose "$@" || rc=$(agents_worst_rc "$rc" $?)
exit "$rc"

#!/usr/bin/env bash
# Turn tier (cpp-cmake). Project-owned: tune freely. The stop hook runs this via verify.
# Incremental build, new warnings in changed files, clang-tidy on changed lines,
# and the tests whose targets depend on what changed. Budget: TURN_BUDGET.
. "$AGENTS_ROOT/.agents/stacks/cpp-cmake/lib.sh"
# CPP_CMAKE_ARGS="-DBUILD_TESTING=ON"      # whatever your tests need to be configured
# Tests CTest doesn't know about: run them below, and set CPP_NO_TESTS=ok so "no tests ran" goes quiet.
# CPP_NO_TESTS=ok
rc=0
cpp_build || exit $?                       # nothing else means much if it doesn't build
cpp_diagnose "$@" || rc=$?                 # new warnings in changed files
cpp_tidy_changed "$@" || rc=$(agents_worst_rc "$rc" $?)
cpp_test_affected "$@" || rc=$(agents_worst_rc "$rc" $?)
# agents_step tests "$AGENTS_ROOT/$CPP_BUILD_DIR/my_tests" || rc=$(agents_worst_rc "$rc" $?)
exit "$rc"

#!/usr/bin/env bash
# Full tier (cpp-cmake). Project-owned: tune freely. Commit gate and CI: verify --tier=full.
# Everything in the turn tier, all tests, an ASan+UBSan build with all tests, and cppcheck.
. "$AGENTS_ROOT/.agents/stacks/cpp-cmake/lib.sh"
# CPP_CMAKE_ARGS="-DBUILD_TESTING=ON"
# CPP_CPPCHECK_ARGS="--addon=misra"        # MISRA C 2012 via cppcheck (bring your own rule texts)
# Tests CTest doesn't know about: run them below (from both build trees), and set CPP_NO_TESTS=ok
# so "no tests ran" goes quiet.
# CPP_NO_TESTS=ok
rc=0
cpp_build || exit $?
cpp_diagnose "$@" || rc=$?
cpp_tidy_changed "$@" || rc=$(agents_worst_rc "$rc" $?)
cpp_test_all || rc=$(agents_worst_rc "$rc" $?)
# agents_step tests "$AGENTS_ROOT/$CPP_BUILD_DIR/my_tests" || rc=$(agents_worst_rc "$rc" $?)
cpp_sanitize || rc=$(agents_worst_rc "$rc" $?)
# agents_step sanitizer-tests "$AGENTS_ROOT/$CPP_SAN_DIR/my_tests" || rc=$(agents_worst_rc "$rc" $?)
# Drop the guard to make cppcheck mandatory.
if command -v cppcheck >/dev/null 2>&1; then
  cpp_cppcheck || rc=$(agents_worst_rc "$rc" $?)
fi
exit "$rc"

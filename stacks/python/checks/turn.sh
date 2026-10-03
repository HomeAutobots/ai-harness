#!/usr/bin/env bash
# Turn tier (python). Project-owned: tune freely. The stop hook runs this via verify.
# Format and lint on changed files, the type checker the project configures on changed files,
# and the pytest tests the change can reach. Budget: TURN_BUDGET.
. "$AGENTS_ROOT/.agents/stacks/python/lib.sh"
# PY_PYTEST_ARGS="-x"                      # or markers: PY_PYTEST_ARGS="-m 'not slow'"
# Tests pytest doesn't run (unittest without pytest, a custom runner): run them below, and set
# PY_NO_TESTS=ok so "no tests ran" goes quiet.
# PY_NO_TESTS=ok
rc=0
py_format_check "$@" || rc=$?
py_lint "$@" || rc=$(agents_worst_rc "$rc" $?)
py_typecheck "$@" || rc=$(agents_worst_rc "$rc" $?)
py_test_affected "$@" || rc=$(agents_worst_rc "$rc" $?)
# agents_step tests py_run python -m unittest discover -s tests || rc=$(agents_worst_rc "$rc" $?)
exit "$rc"

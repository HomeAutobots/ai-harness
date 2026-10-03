#!/usr/bin/env bash
# Full tier (python). Project-owned: tune freely. Commit gate and CI: verify --tier=full.
# Format and lint over the whole project, the type checker over what its config names, and
# every test.
. "$AGENTS_ROOT/.agents/stacks/python/lib.sh"
# PY_PYTEST_ARGS="-x"
# Tests pytest doesn't run (unittest without pytest, a custom runner): run them below, and set
# PY_NO_TESTS=ok so "no tests ran" goes quiet.
# PY_NO_TESTS=ok
rc=0
py_format_check_all || rc=$?
py_lint_all || rc=$(agents_worst_rc "$rc" $?)
py_typecheck_all || rc=$(agents_worst_rc "$rc" $?)
py_test_all || rc=$(agents_worst_rc "$rc" $?)
# agents_step tests py_run python -m unittest discover -s tests || rc=$(agents_worst_rc "$rc" $?)
exit "$rc"

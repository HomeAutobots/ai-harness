#!/usr/bin/env bash
# Edit tier (python). Project-owned: tune freely. Runs after each edit with the edited files.
# Format check (ruff format or black, when the project formats with one) and ruff lint on the
# edited files; without ruff, a syntax check with the project's Python. Budget: EDIT_BUDGET.
. "$AGENTS_ROOT/.agents/stacks/python/lib.sh"
rc=0
py_format_check "$@" || rc=$?
py_lint "$@" || rc=$(agents_worst_rc "$rc" $?)
exit "$rc"

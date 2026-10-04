# Stack pack: python

Deterministic Python feedback from the tools the project already uses, wired into the harness
tiers. Installed with `install.sh --stack python <project>`; the pack runs from
`.agents/builtin/stacks/python/` (harness-owned) and the tier scripts in `.agents/checks/`
(project-owned, tune them freely).

| Tier | When | What runs |
|---|---|---|
| edit | after every edit (hook) | format check (ruff format or black, when the project formats with one) and ruff lint on the edited files; without ruff, a syntax check with Python |
| turn | stop gate, `verify` | the above on changed files, the project's type checker (mypy or pyright) for changed files, and the pytest tests the change can reach |
| full | commit gate, CI | format and lint over the whole project, the type checker over what its config names, every test |

## How it works
- **The project's tools, from the project's environment.** Tools run through `PY_RUN` when it's
  set (`uv run --frozen`, `hatch run`, `pdm run`), else from the project's virtualenv
  (`PY_VENV`, else `$UV_PROJECT_ENVIRONMENT`, `.venv`, `venv`, an activated `$VIRTUAL_ENV`, or
  the one `poetry env info -p` names when there's a `poetry.lock`), else from `PATH`. A uv
  project's environment is its `.venv`, so the pack runs from there directly: same tools,
  without `uv run` syncing the environment first. Everything runs from the install root.
  - pytest, mypy, and python import the project, so when the project has a virtualenv they must
    come from it: a global pytest can't import the project's dependencies and would turn every
    turn into a wall of import errors. A project with a lock file (`uv.lock`, `poetry.lock`,
    `pdm.lock`) and no environment yet gets `infra: ... the project has uv.lock but no
    environment yet; uv sync makes one`. ruff, black, and pyright don't import the project and
    may come from `PATH`. CI that installs into the system Python while the lock file is
    committed (`uv pip install --system`) sets `PY_RUN="env"`: every tool then comes from
    `PATH`, on purpose.
  - With `PY_RUN`, one probe per run asks the runner's Python which tools it can find, so a
    tool the runner lacks is reported as missing (exit 3), not as the runner's own error.
- **What it runs follows the project's config.** Nothing the project hasn't chosen gets imposed:
  - Format: ruff format when the project configures ruff and formats with it (a
    `[tool.ruff.format]` or ruff.toml `[format]` section, or `ruff format` / `ruff-format` in
    `.pre-commit-config.yaml`, a Makefile, justfile, tox, nox, `pyproject.toml`, or a GitHub
    workflow); black when `pyproject.toml` has `[tool.black]`; otherwise none. On the full tier
    the formatter finds the files itself. black reads `.gitignore` but not `.git/info/exclude`,
    where local mode hides the harness, so its findings for files git ignores are dropped.
  - Lint: ruff with the project's config when it has one (`ruff.toml`, `.ruff.toml`,
    `[tool.ruff]`). Without one, an installed ruff checks only syntax errors and undefined names
    (`--isolated --select=E9,F63,F7,F82`), so the findings don't depend on whose ruff, or whose
    user-level ruff config, happens to be there. No ruff at all: a syntax check with Python.
  - Types: mypy when configured (`mypy.ini`, `.mypy.ini`, `[tool.mypy]`, `setup.cfg [mypy]`),
    pyright when configured (`pyrightconfig.json`, `[tool.pyright]`), otherwise none. When the
    config scopes the checker (mypy `files =` or `exclude =`, pyright `include` or `exclude`,
    read from the one config file the checker itself uses), the turn tier runs it on that
    scope and keeps the changed files' errors. So a changed file outside the scope (a test,
    when only `src` is type-checked) isn't held to the project's strict settings, which naming
    it on the command line would do: neither checker applies an exclude to files named there.
    Without
    a scope it runs on the changed files.
  - Tests: pytest.
- **Missing tools.** A tool the project configures but this machine lacks is a tooling problem:
  `infra: mypy not found (not on PATH), and the project configures it (pyproject.toml)...`,
  exit 3, so verify says `INFRA` (the stop gate lets it through; CI on full fails). A tool it
  doesn't configure is skipped quietly, so an optional tool never fails every turn.
- **Format is checked, not applied.** Like clang-format in cpp-cmake, the edit tier reports
  `path:line: error: not formatted the way ruff formats it (run: ruff format <file>)` at the
  first line the formatter would change, and leaves the file alone. A hook that rewrites a
  file right after the agent's edit leaves the agent with a stale copy (some tools refuse the
  next edit until it re-reads), and it would hide the change from the diff the agent reviews.
  Running the formatter is one command the finding names. `ruff check` always runs with
  `--no-fix`, even when the project's config says `fix = true`.
- **Findings are `path:line`.** ruff's concise output, mypy, and pyright are turned into
  `path:line:col: error: message [rule]`, so the output shaper dedupes and caps them and
  baselines work. A notebook's findings name the cell in the message
  (`nb.ipynb:1:8: error: cell 2: ...`). mypy follows imports and reports errors in them too;
  only errors in the files being checked count. pyright warnings don't fail pyright, so they
  don't fail this. pytest runs with `-q --tb=short`, its timings taken out (they change every
  run), traceback frames outside the project (the standard library, site-packages) dropped,
  and its `E ` assertion lines kept under the failing line.
- **Affected tests.** A static import scan (python3's `ast`, cached in
  `.agents/cache/py-imports.json`) over the Python files git sees:
  - a changed test file (`test_*.py`, `*_test.py`) runs itself;
  - a changed module runs every test file that imports it, directly or through other modules,
    and every test under a `conftest.py` that does; relative imports, src layouts,
    `importlib.import_module("x")`, and `pytest_plugins` count;
  - a package's `__init__.py` passes a change on only through the names it re-exports from the
    changed module, so `from pkg import a` doesn't run because `pkg/__init__.py` also imports
    `b`; a changed `__init__.py` runs whatever imports anything in its package;
  - a changed `conftest.py` runs the tests under its directory;
  - docs, images, and `.pyi` stubs run nothing, unless they sit in a directory with tests
    (golden files, fixtures); the harness's own files run nothing;
  - everything else runs the whole suite: packaging and test config (`pyproject.toml`,
    `setup.cfg`, `tox.ini`, ...), requirements and constraints files, other non-Python files,
    a deleted module, a module no test reaches (it may be loaded dynamically or run in a
    subprocess), custom `python_files` patterns or doctests in the pytest config, and no
    python3 for the scan. Conservative on purpose.
- **No tests is never a quiet pass.** When pytest collects nothing (exit 5), or there are no
  `test_*.py` / `*_test.py` files and no pytest, the test steps print
  `infra: tests: no tests ran: ...` and exit 3, so verify says `INFRA`, not `ok`. Test files
  without pytest to run them is `infra: pytest not found`. Two ways to settle it:
  - Tests pytest doesn't run (unittest without pytest installed, a custom runner): run them
    from the tier scripts with the commented `agents_step tests py_run python -m unittest
    discover -s tests` line, and set `PY_NO_TESTS=ok` there. `py_run` runs a tool the way the
    stack does, and turns a tool's exit 2 into 1, since verify reads 2 as a policy block.
  - A project with no tests at all: `PY_NO_TESTS=ok` in `.agents/harness.conf`. That's a
    person's call, not an agent's.
- **pytest exit codes.** 1 (failures) and 2 (collection errors, interrupted) are findings, and
  so is 4 when a conftest pytest loads first fails to import; 3 and other 4s (internal or
  usage error) are tooling problems; 5 is "no tests ran". A selection whose files hold no
  tests runs the whole suite instead.
- **Baselines.** `verify --tier=full --update-baseline` records current ruff, ruff-format (or
  black-format), mypy, and pyright findings in `.agents/baselines/`; after that only new
  findings fail.
- **Nothing to hide from git.** ruff, mypy, and pytest write their caches with their own
  `.gitignore`; the import scan's cache lives in `.agents/cache/`. Python's `__pycache__` is the
  project's `.gitignore` business, as it is when you run the tests yourself.

## Settings
`PY_RUN`, `PY_VENV`, `PY_FORMAT`, `PY_LINT`, `PY_TYPECHECK`, `PY_RUFF_ARGS`, `PY_MYPY_ARGS`,
`PY_PYRIGHT_ARGS`, `PY_PYTEST_ARGS`, `PY_NO_TESTS`. See the top of `lib.sh`. Empty means
"follow the project": set one only to override what the pack finds.

| Setting | Values |
|---|---|
| `PY_RUN` | a command prefix for every tool, e.g. `uv run --frozen`, `hatch run`, `pdm run` |
| `PY_VENV` | the project's virtualenv, relative to the install root or absolute |
| `PY_FORMAT` | `ruff`, `black`, or `off` |
| `PY_LINT` | `ruff` (with the project's config) or `off` (`off` also drops the syntax check) |
| `PY_TYPECHECK` | `mypy`, `pyright`, `mypy pyright`, or `off` |
| `PY_RUFF_ARGS`, `PY_MYPY_ARGS`, `PY_PYRIGHT_ARGS` | extra arguments, split on spaces |
| `PY_PYTEST_ARGS` | extra pytest arguments, read like a shell command line: `-x -m 'not slow'` |
| `PY_NO_TESTS` | `ok` turns "no tests ran" off (and "pytest not found"); unset means reported |

Set them in `.agents/harness.conf` or in the check scripts:

```sh
# .agents/harness.conf: every tier
PY_RUN="hatch run"
PY_TYPECHECK="off"

# .agents/checks/full.sh: this tier only, after sourcing lib.sh
PY_PYTEST_ARGS="-m 'not slow' -x"
```

Which one wins:
1. A value the check script sets, before or after it sources `lib.sh`.
2. `.agents/harness.conf`. `lib.sh` reads it when sourced, the same way `verify` does.
3. The default in `lib.sh`.

A variable in your shell's environment counts as set by the check script when you run that
script directly. Under `verify` it doesn't beat `harness.conf`, since `verify` loads the file
over it first, as it does for every `harness.conf` setting.

## Notes
- **ruff 0.5 or newer** for `--output-format=concise`. An older one the project configures is
  reported (exit 3); an older one it didn't choose is passed over for the syntax check.
- **Monorepos**: config is read from the install root. Install the harness in the package's
  directory, or set the `PY_*` overrides.
- **Tests in a subprocess or loaded by name** (CLI tests, plugin registries) aren't seen by the
  import scan. A module no test imports runs the whole suite, so these are still covered; a
  module some test imports runs only those tests. Imports a package's `__init__.py` makes
  only for their side effects (registering handlers, say) pass nothing on. When that matters,
  call `py_test_all` in `turn.sh` instead of `py_test_affected`.
- **Django** and other frameworks whose tests need a plugin (pytest-django) or their own runner
  (`manage.py test`): install the plugin in the project's environment, or run the framework's
  runner from the tier scripts with `PY_NO_TESTS=ok`.
- **pyright** findings are read from its text output (`file:line:col - error: message
  (rule)`, later lines of a message indented with no-break spaces), checked against pyright
  1.1.414.

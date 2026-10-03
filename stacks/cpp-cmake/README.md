# Stack pack: cpp-cmake

Deterministic C and C++ feedback for CMake projects, wired into the harness tiers. Installed
with `install.sh --stack cpp-cmake <project>`; the pack runs from `.agents/builtin/stacks/cpp-cmake/`
(harness-owned) and the tier scripts in `.agents/checks/` (project-owned, tune them freely).

| Tier | When | What runs |
|---|---|---|
| edit | after every edit (hook) | clang-format check (if the repo has `.clang-format`), `-fsyntax-only` compile of edited sources with their real compile command: errors plus new warnings |
| turn | stop gate, `verify` | incremental build, new warnings in changed files, clang-tidy on changed lines only (when installed), tests affected by the change |
| full | commit gate, CI | the above, all tests, ASan+UBSan build with all tests, cppcheck (new findings only, when installed) |

Why this shape: execution and compiler feedback in the loop is where the evidence for agent
gains is strongest (sanitizer-guided C/C++ repair especially), and findings limited to what
the agent changed keep it from wandering into pre-existing debt.

## How it works
- **Own build trees.** `build-agent/` and `build-agent-asan/` by default (`CPP_BUILD_DIR`,
  `CPP_SAN_DIR`; Ninja and ccache when present, compile database exported). Your own build
  directories are never touched, as long as these don't name one. Both are added to
  `.git/info/exclude` (under the install's subdirectory when the harness lives in one), and
  `compile_commands.json` is symlinked at the install root for clangd.
- **Changed lines only.** clang-tidy gets a `--line-filter` built from `git diff -U0`, so
  existing findings elsewhere in a file don't show up.
- **Optional analyzers.** The seeded tier scripts run clang-tidy and cppcheck only when they're
  installed (`if command -v ...`), so a machine without them skips those steps instead of
  failing every turn that touches a source with `infra: clang-tidy not found`. To make one
  mandatory, drop its guard: then a missing tool is a tooling problem (exit 3), as before.
- **Affected tests.** CMake's file API maps changed sources to targets, then to everything
  that depends on them, then to the CTest tests those executables run. Header, CMake, or
  unknown changes run everything; so does a missing codemodel, or a selection that matches no
  test CTest lists. Conservative on purpose.
- **No tests is never a quiet pass.** When the build tree has no `CTestTestfile.cmake`, or
  `ctest -N` lists `Total Tests: 0`, the test steps (`cpp_test_affected`, `cpp_test_all`, and
  the sanitizer tests in `cpp_sanitize`) print `infra: tests: no tests ran: ...` and exit 3,
  so verify says `INFRA`, not `ok`. The count comes from the listing, not ctest's exit code,
  which for "No tests were found!!!" is 0 or 8 depending on the version and
  `CTEST_NO_TESTS_ACTION`. The stop gate lets exit 3 through, so agents aren't blocked; CI on
  `verify --tier=full` fails until it's settled. Two ways to settle it:
  - Tests that are plain executables CTest doesn't know about (`add_executable` with no
    `add_test`): register them, or run them from the tier scripts and set `CPP_NO_TESTS=ok`
    there. The seeded scripts have commented lines for both:
    `agents_step tests "$AGENTS_ROOT/$CPP_BUILD_DIR/<binary>"` in turn and full, and
    `agents_step sanitizer-tests "$AGENTS_ROOT/$CPP_SAN_DIR/<binary>"` in full. Wire both:
    `CPP_NO_TESTS=ok` quiets the sanitizer step too, so leaving the second one out means the
    ASan build runs no tests.
  - A project with no tests at all: `CPP_NO_TESTS=ok` in `.agents/harness.conf`. That's a
    person's call, not an agent's.
- **Warnings don't depend on build state.** New-warning detection uses a syntax-only pass over
  the changed sources, so a warning keeps showing until it's fixed, not just on the build that
  happened to recompile the file.
- **Baselines.** `verify --tier=full --update-baseline` records current clang-tidy, cppcheck,
  and build-warning findings in `.agents/baselines/`; after that only new findings fail.
- **Profile.** Without a repo `.clang-tidy`, the curated `clang-tidy.agent` profile applies:
  bug-finding checks, no style checks.

## Settings
`CPP_BUILD_DIR`, `CPP_SAN_DIR`, `CPP_BUILD_TYPE`, `CPP_CMAKE_ARGS`, `CPP_JOBS`,
`CPP_TIDY_CONFIG`, `CPP_TIDY_ARGS`, `CPP_SANITIZERS`, `CPP_TEST_TIMEOUT`, `CPP_CPPCHECK_ARGS`,
`CPP_NO_TESTS`. See the top of `lib.sh` for defaults. Set them in `.agents/harness.conf` or in
the check scripts:

```sh
# .agents/harness.conf: every tier
CPP_JOBS="8"
CPP_TEST_TIMEOUT="300"

# .agents/checks/full.sh: this tier only, after sourcing lib.sh
CPP_TEST_TIMEOUT=900
```

Which one wins:
1. A value the check script sets, before or after it sources `lib.sh`.
2. `.agents/harness.conf`. `lib.sh` reads it when sourced, the same way `verify` does.
3. The default in `lib.sh`.

A variable in your shell's environment counts as set by the check script when you run that
script directly. Under `verify` it doesn't beat `harness.conf`, since `verify` loads the file
over it first, as it does for every `harness.conf` setting.

`CPP_NO_TESTS` unset means "no tests ran" is reported; `ok` turns that off. A `CPP_BUILD_DIR` or
`CPP_SAN_DIR` outside `build-agent*` gets its own line in `.git/info/exclude` the first time it's
configured.

## Notes
- **Cross compiling** (embedded targets): the syntax check uses each file's real compile
  command, so cross compilers work. Tests and sanitizers need a host build; point
  `CPP_CMAKE_ARGS` at a host toolchain or drop those calls from the scripts.
- **clang-tidy on a GCC compile database** may choke on GCC-only flags. Add
  `CPP_TIDY_ARGS="--extra-arg=-Wno-unknown-warning-option"` or strip flags with a `.clangd`-style
  wrapper.
- **MISRA**: `CPP_CPPCHECK_ARGS="--addon=misra"` (you supply the rule texts file).
- **Semantic navigation**: for Claude Code, the clangd LSP plugin gives go-to-definition and
  find-references on the same compile database (`/plugin` and search for clangd). Useful for
  big codebases; the evidence that it raises success rates for frontier models is weak, so
  treat it as optional.

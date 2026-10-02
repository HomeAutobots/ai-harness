# Stack pack: cpp-cmake

Deterministic C and C++ feedback for CMake projects, wired into the harness tiers. Installed
with `install.sh --stack cpp-cmake <project>`; the pack runs from `.agents/builtin/stacks/cpp-cmake/`
(harness-owned) and the tier scripts in `.agents/checks/` (project-owned, tune them freely).

| Tier | When | What runs |
|---|---|---|
| edit | after every edit (hook) | clang-format check (if the repo has `.clang-format`), `-fsyntax-only` compile of edited sources with their real compile command: errors plus new warnings |
| turn | stop gate, `verify` | incremental build, new warnings in changed files, clang-tidy on changed lines only, tests affected by the change |
| full | commit gate, CI | the above, all tests, ASan+UBSan build with all tests, cppcheck (new findings only) |

Why this shape: execution and compiler feedback in the loop is where the evidence for agent
gains is strongest (sanitizer-guided C/C++ repair especially), and findings limited to what
the agent changed keep it from wandering into pre-existing debt.

## How it works
- **Own build trees.** `build-agent/` and `build-agent-asan/` (Ninja and ccache when present,
  compile database exported). Your own build directories are never touched. Both are added to
  `.git/info/exclude` (under the install's subdirectory when the harness lives in one), and
  `compile_commands.json` is symlinked at the install root for clangd.
- **Changed lines only.** clang-tidy gets a `--line-filter` built from `git diff -U0`, so
  existing findings elsewhere in a file don't show up.
- **Affected tests.** CMake's file API maps changed sources to targets, then to everything
  that depends on them, then to the CTest tests those executables run. Header, CMake, or
  unknown changes run everything; so does a missing codemodel. Conservative on purpose.
- **Warnings don't depend on build state.** New-warning detection uses a syntax-only pass over
  the changed sources, so a warning keeps showing until it's fixed, not just on the build that
  happened to recompile the file.
- **Baselines.** `verify --tier=full --update-baseline` records current clang-tidy, cppcheck,
  and build-warning findings in `.agents/baselines/`; after that only new findings fail.
- **Profile.** Without a repo `.clang-tidy`, the curated `clang-tidy.agent` profile applies:
  bug-finding checks, no style checks.

## Settings
Set in the check scripts (or `.agents/harness.conf`): `CPP_BUILD_DIR`, `CPP_SAN_DIR`,
`CPP_BUILD_TYPE`, `CPP_CMAKE_ARGS`, `CPP_JOBS`, `CPP_TIDY_CONFIG`, `CPP_TIDY_ARGS`,
`CPP_SANITIZERS`, `CPP_TEST_TIMEOUT`, `CPP_CPPCHECK_ARGS`. See the top of `lib.sh`.

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

# PROJECT_NAME

> **Not tailored yet.** Ask your agent: "Use the harness-tailor skill to tailor the AI harness for this repo."
> Budget: this whole file under ~80 lines. Every line loads into every session and costs tokens on every task.

TODO(harness-tailor): 1-2 sentences on what this is and what matters most here (safety, determinism, API stability). Skip anything the README or code already makes obvious.

<!-- harness:core:start -->
<!-- harness:core:end -->

## Commands
Only commands an agent would get wrong or couldn't guess. `.agents/bin/verify` covers the standard checks.
- TODO(harness-tailor): e.g. run one test, regenerate code, required setup step

## Boundaries
- Never edit: TODO(harness-tailor) (vendored code, generated files, applied migrations)
- Ask first: TODO(harness-tailor) (public APIs, schemas, crypto, anything safety-relevant)

## Conventions
TODO(harness-tailor): only what tooling doesn't enforce and a newcomer would get wrong. Delete if none.

## Context docs
Read one only when the task touches its area. They live in `.agents/context/`.

| Doc | Read when |
|---|---|
| _none yet_ | |

<!-- harness:skills:start -->
<!-- harness:skills:end -->

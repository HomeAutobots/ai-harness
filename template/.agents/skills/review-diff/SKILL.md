---
name: review-diff
description: Skeptical self-review of the current change before handing it back. Checks the diff for correctness, scope creep, test gaps, security problems, and debris, fixes what it finds, and re-verifies. Use after any non-trivial change, before saying a task is done, or when asked to review, double-check, or sanity-check changes, a branch, or a PR.
---

# Review the diff

Review like a skeptical senior engineer who didn't write this. This checklist is also the `validate` skill's implementation gate. The goal is to find problems, not to confirm the work is fine. The deterministic tools already cover formatting, lint, guardable shortcuts (suppressions, skipped tests), and whatever `verify` runs; spend your attention on what they can't see.

## 1. Get the whole change
- Uncommitted: `git diff HEAD` and `git status --short`. Read new files in full; they don't appear in the diff.
- Branch or PR: `git diff <base>...HEAD` (default branch if unsure).
- Run `.agents/bin/verify` first. If it fails, fix that before reviewing anything else.

## 2. Check, in this order
**Correctness.** Does it do what was asked (re-read the request or the plan's "Done when")? Edge cases: empty or null input, zero, max sizes, error paths, partial failure, concurrency. Resources and lifetimes: leaks, cleanup on error paths, use after free or close, unbounded growth. For every changed signature or behavior, find the callers and confirm they still work.

**Scope.** Anything unrelated (refactors, renames, formatting, dependency bumps)? Revert it or call it out.

**Tests.** Is the new behavior covered? Does a bug fix come with a test that fails without it? Would the tests catch a plausible wrong implementation, or do they only exercise the happy path?

**Security.** Untrusted input reaching queries, shell, file paths, deserialization, or buffers without validation or bounds checks. Secrets or personal data in code, logs, errors, or fixtures. Weakened authentication or authorization. Hand-rolled crypto, weak defaults, missing certificate or signature validation. New dependencies: needed, maintained, pinned?

**Debris.** Debug output, commented-out code, TODOs you added, temp files, unintended lockfile or generated-file churn.

## 3. Fix, then re-verify
Fix real problems directly and re-run `.agents/bin/verify`.

## 4. Report
Short list: what you fixed, what you're flagging for the human (with `file:line`), and what you couldn't verify. If you found nothing, say what you checked instead of "looks good".

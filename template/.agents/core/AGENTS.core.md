## Harness rules

### Non-negotiables
- Done means `.agents/bin/verify` exits 0. Where your tool supports hooks it runs when you try to finish; otherwise run it yourself. On failure, fix the findings. Never claim success without it.
- Never delete, skip, or weaken a test, and never add a suppression (NOLINT, noqa, eslint-disable, pragma ignores) to get green. If one is truly warranted, stop and ask.
- Ask before adding or upgrading dependencies, changing build or CI config, or changing a public interface.
- Never read, print, or commit secrets or key material (.env files, *.pem, *.key, *.p12, *.pfx, credentials).
- Git: follow this repo's workflow with the `git-workflow` skill and `.agents/bin/gitflow`. Steps outside `GIT_AGENT_MAY` are the human's. Never force-push shared branches, `reset --hard`, `clean -f`, or use `--no-verify`.
- Stop and ask when requirements conflict, a step is destructive, or the same check fails 3 times.

### Feedback tools
Quiet on success. Exit 0 ok, 1 findings, 2 policy block, 3 tooling problem.
- `.agents/bin/check <files>`: fast per-file checks. Runs after each edit where hooks exist.
- `.agents/bin/verify`: guard, build, affected tests, lint on changed code. `--tier=full` is the commit gate.
- On failure the last line points to the full log. Open it only if the summary isn't enough.

### Work that spans sessions
- More than a few steps, or likely to outlive this session: use the `plan-task` skill. Its gates use the `validate` skill.
- Resuming: `.agents/bin/tasks list`, then `.agents/bin/tasks next <slug>` and `git log --oneline -10`.

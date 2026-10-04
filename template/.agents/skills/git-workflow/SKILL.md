---
name: git-workflow
description: This repo's git workflow as configured in .agents/git.conf. Branching from the right base with the right name, commit message format, keeping the branch current, pushing, opening the PR or MR, handling review comments, and merging, all through .agents/bin/gitflow. Use whenever work will be committed, before creating a branch, committing, pushing, opening or updating a pull request, responding to review comments, or when asked how this repo handles branches, commits, or PRs.
---

# Git workflow

Start with `.agents/bin/gitflow config`. It shows this repo's rules: base branch, protected branches, naming, commit format, PR target, and which steps are yours (`GIT_AGENT_MAY`). A step not listed there is the human's: get the work to that point, then say it's ready and what they should do next.

Use gitflow for the mechanics. It builds names and messages that pass the checks, so you never hand-format them.

1. **Start.** `gitflow start <ticket> "<summary>"` branches from the base with the configured name. Never commit on a protected branch.
2. **Commit.** Stage the change, then `gitflow commit "<summary>"` (add `--type=<t>` if the format has a type). If `gitflow template` shows a commit template, fill each section it asks for with `--section "<Label>=<text>"`: real content, not filler; a section with nothing true to say is a sign to ask. One commit per ledger task, or per review round. The summary says what changed, in the imperative, specific enough to read in `git log`. If the plan says `Commits: ask`, wait for the human.
3. **Stay current.** `gitflow update` merges or rebases the base in, per `GIT_UPDATE`. Resolve conflicts carefully, then run `verify`.
4. **Push.** `gitflow push` runs the required verify tier first. After a rebase, `gitflow push --lease` if `GIT_FORCE` allows it. Never force-push anything else.
5. **PR.** `gitflow pr` opens it against the base with the template filled in: ticket link, commits, verify result. Then make the summary worth reading: what changed, why, how it was verified, and what deserves a close look. With `GIT_PR_TOOL=none`, hand the printed title and body to the human.
6. **Review.** `gitflow review` lists the comments. Address every one: fix it, or explain why not. Never skip one silently. A comment that conflicts with the requirement, the plan, or another reviewer is a question for the human (`tasks ask`). Fixes go in as new commits unless the project rebases, pass the `validate` implementation gate, and get a reply per thread saying what changed.
7. **Merge.** Only if `merge` is in `GIT_AGENT_MAY`, the PR is approved, and checks are green: `gitflow merge`. Never approve your own PR.

When a git hook or the policy hook rejects a command, read the reason: it names the rule and the fix. Don't route around it with `--no-verify`, `core.hooksPath`, a different branch name, or raw git.

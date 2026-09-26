# Plans

Plan ledgers, one directory per piece of work, managed with `.agents/bin/tasks`:

    .agents/plans/<slug>/
      plan.md        intent: request, done-when, confirmed context, decisions, risks
      tasks.json     one task per line: id, status (todo|doing|done|blocked), commit, desc, acceptance
      questions.json the question ledger: what was asked, at which gate, and what the human answered
      progress.log   append-only, one dated line per session or task

    .agents/plans/_general/questions.json   questions asked outside any plan

This is the handoff between sessions, agents, and tools. Any agent resumes with
`.agents/bin/tasks next <slug>` plus `git log`. `tasks questions <words>` searches every answer
given so far, and the question hooks record questions asked through a tool's question tool. Agents never hand-edit tasks.json; the CLI keeps
it valid JSON.

Ledgers are gitignored by default because they churn. To share one with the team, commit it
deliberately (`git add -f`), or remove the ignore rule in `.agents/plans/.gitignore`.

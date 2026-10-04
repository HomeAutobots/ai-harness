# Debug playbook

How each debugging step works in this repo. The `debug` skill reads a step's section when it gets
there; an empty or missing section means the workflow's generic guidance. harness-tailor drafts
this file; edit it like any project doc. `.agents/commands/debug status` lists the steps with
nothing here yet, and `verify` checks the format (`debug-playbook-format`).

Each list item in a section is one binding:
  skill: <name>       a skill for the step (this project's, a library's, or a built-in)
  run: <command>      a command line; fill in its <placeholders> and run it with
                      .agents/commands/debug run <step> -- <command>, so the output is evidence
  context: <path>     a doc to read first, repo-relative
Plain lines are notes, e.g. why a command couldn't be tried here. For example, under reproduce:
"- run: make sim && ./sim --scenario <file>".

## intake

## reproduce

## gather-evidence

## isolate

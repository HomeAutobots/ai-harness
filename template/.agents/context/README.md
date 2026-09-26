# Context docs

On-demand knowledge for agents, loaded only when a task touches the area. One topic per file.

Write each one for an agent that's about to change code in that area: key concepts, invariants, where things live, what not to do. Link to existing docs instead of copying them.

Every doc here needs a row in the AGENTS.md "Context docs" table with a specific "Read when" trigger (for example "changing anything under src/net/"). Without that row, agents won't find it.

Delete docs that go stale. A wrong doc is worse than none.

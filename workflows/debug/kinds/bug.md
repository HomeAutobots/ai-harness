---
kind: bug
steps: intake reproduce gather-evidence hypothesize isolate root-cause check-in
bindable: intake reproduce gather-evidence isolate
---

# Bug report

Someone saw the software do the wrong thing. Seven steps; 3 to 5 loop until a hypothesis holds.

| # | Step | Playbook section | Produces |
|---|---|---|---|
| 1 | intake | `intake` | `report.md`: the report as received, expected and actual pulled out |
| 2 | reproduce | `reproduce` | at least one attempt with its outcome recorded |
| 3 | gather-evidence | `gather-evidence` | evidence entries tied to the report's symptoms |
| 4 | hypothesize | (judgment) | `hypotheses.md` |
| 5 | isolate | `isolate` | each hypothesis confirmed or ruled out, citing E-ids |
| 6 | root-cause | (judgment) | `root-cause.md` |
| 7 | check-in | (none) | the human approves or rejects |

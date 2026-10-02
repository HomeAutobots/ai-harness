# .agents/library

This project's own skills, workflows, and stacks. Project-owned: upgrades never touch it.

    skills/<name>/SKILL.md     a skill (Agent Skills format)
    agents/<name>.md           an agent, in the neutral format (see Agents below)
    mcp/<name>.json            an MCP server, rendered into each tool's config (see MCP servers)
    workflows/<name>/          a workflow pack: checks/<tier>.sh, skill/SKILL.md, and optionally
                               checks/commit-msg.sh, checks/state.sh, harness.conf.snippet,
                               policy.conf.snippet, seed/, agents/, mcp/, bin/
    stacks/<name>/             a stack pack: lib.sh, checks/<tier>.sh

After adding or changing something, run `.agents/bin/sync`. Skills are always on: they show up
in `.agents/skills/`, in each tool's skills dir, and in AGENTS.md's skills index. Workflows and
stacks turn on by name in `WORKFLOWS` / `STACKS` in `.agents/harness.conf` (or
`install.sh --workflow <name>`), and run from here.

Libraries are searched in this order; the first one with a name wins, and sync warns when one
shadows another: this directory, `LIBRARIES` in `.agents/harness.conf`, your personal library
`~/.config/ai-harness/`, `LIBRARIES` in `~/.config/ai-harness/harness.conf`, then the harness's
built-ins in `.agents/builtin/`. To change a built-in, put your version here under the same name.
Only a real item counts: a skill needs SKILL.md, a workflow needs checks/ with a file, skill/SKILL.md,
agents/, mcp/, or bin/, and a stack needs lib.sh or checks/ with a file. An empty directory is ignored (sync
warns), so it never hides the one it's named after.

Pack scripts run from wherever the pack lives, so find the pack's own files from the script's
path (`"$(dirname "$0")/../tool.py"`) and the project from `$AGENTS_ROOT`. verify and gitflow run
checks with bash; a command in a pack's `bin/` needs `chmod +x` from you.

Names are letters, digits, `.`, `_`, and `-`, and don't start with `.` or `_`. See where each name
resolves from with `bash .agents/lib/libraries.sh resolve <skills|workflows|stacks>`.

## Agents

`agents/<name>.md` is a neutral agent: write it once, and `sync` renders it into whatever each
enabled adapter reads (`.claude/agents`, `.github/agents`, `.cursor/agents`, `.codex/agents`,
`.gemini/agents`). The same search order as skills applies: a library's own `agents/`, plus an
active workflow pack's `agents/*.md`, joining in after every library's own (a library agent of the
same name wins, with a shadow warning).

```markdown
---
name: reviewer                 # optional; must equal the file name
description: Reviews a diff for correctness bugs. Use after implementing a change.
tools: [read, search, shell]   # read, search, edit, shell, web, mcp:<server>; omitted = every tool
model: strong                  # fast | standard | strong | inherit (default)
effort: high                   # low | medium | high | max | inherit (default)
skills: [review-diff]          # Claude preloads; other tools: warning
mcp: [github]                  # server names; adds that server's tools when tools is set
max_turns: 30                  # Claude maxTurns, Gemini max_turns; others: warning
targets: [claude, codex]       # optional; default every enabled adapter
native:                        # per-tool lines in that tool's own syntax, copied verbatim
  claude:
    color: blue
  codex:
    model_reasoning_effort = "high"
---
Body: the prompt.
```

The frontmatter is a small YAML subset (`harness.py` parses it with the standard library, no
PyYAML): plain or quoted scalars, `>`/`|` folded text, `[a, b]` or `- a` lists, and the `native:`
block. Fields: `name` (optional, must match the file name), `description` (required, every tool
needs one to know when to use the agent), `tools`, `model` (a tier, not a model id), `effort`,
`skills` (Claude only), `mcp` (server names), `max_turns`, `targets` (which adapters get this
agent; default all enabled ones), and `native`. An unknown field warns and is ignored; put a
tool's own fields under `native:` instead.

Where each render lands and what it drops:
- **claude** `.claude/agents/<name>.md`: everything maps, including skills, MCP server names
  (`mcp__<server>`), and max turns.
- **copilot** `.github/agents/<name>.agent.md`: drops skills, effort, and max turns (not
  supported); always rendered, since the CLI and cloud agent read nothing else.
- **cursor** `.cursor/agents/<name>.md`: `tools` can only coarsen to `readonly: true` (no edit, no
  shell in the list) or the full set; a finer list warns and the agent gets every tool anyway.
  Effort rides on the model id (`<model>[effort=X]`), so it's dropped unless a
  `MODEL_<TIER>_CURSOR` is set. Drops skills, max turns, and per-agent MCP limits (MCP is
  inherited, not scoped).
- **codex** `.codex/agents/<name>.toml`: the same readonly-or-everything limit as Cursor
  (`sandbox_mode = "read-only"` or omitted, meaning inherit). `effort: max` becomes `high`
  (Codex's documented ceiling), noted every sync. Drops skills, max turns, and MCP server limits
  (the servers themselves render from `mcp/`, see MCP servers below).
- **gemini** `.gemini/agents/<name>.md`: drops skills and effort; max turns is supported.

A field a tool can't express gets one warning naming the field and the tools that drop it; the
agent still renders for them, just without it. `--check` doesn't fail on these.

Model tiers and effort map through `.agents/harness.conf`: `MODEL_<TIER>_<TOOL>` and
`EFFORT_<TIER>_<TOOL>` (tiers `fast standard strong`; tools `CLAUDE COPILOT CURSOR CODEX GEMINI`,
uppercase). A missing key, or `model: inherit` / `effort: inherit`, leaves the field out, so the
tool's own default applies. An agent's own `effort:` beats its tier's `EFFORT_*` default.

`native.<tool>` holds raw lines in that tool's own syntax (YAML frontmatter for the Markdown
tools, TOML for Codex); `sync` copies them verbatim and doesn't interpret them, except for the
escalation check below. A native line overrides sync's own rendered line for that key, so
`native.claude: model: claude-opus-5-5` pins an exact model past the tier mapping. A native line
that grants more than the harness default (Claude `permissionMode:
bypassPermissions` or `dontAsk`; Codex `sandbox_mode = "danger-full-access"` or `approval_policy =
"never"`) still renders as written, but warns on every sync, naming the source file and line; the
policy hook still checks every tool call either way, so that's your call to make, not a bypass of
it.

Every render starts with a marker comment. A file at a render path without one isn't sync's: it's
never changed or removed, and a library agent of that name just skips that tool, with a warning.
An agent file with errors (bad frontmatter, no description, a `name:` that doesn't match the file
name) isn't rendered, but a render it had from before a good run stays as it is. A committed
render also stays as is while a project-listed library it may belong to isn't here.

In team mode a personal agent's renders stay out of git, listed in the clone's exclude block like
personal skills, and out of `generated.lock`; local mode hides every render from git the same way
it hides the rest of the harness. A personal agent never lands in a shared repo.

A pack's `bin/<name>` commands (not agents) get their own stable path: `.agents/commands/<name>`,
written by `sync` for every command of each active workflow pack, execing the pack's `bin/<name>`
from wherever its library resolves (e.g. `.agents/commands/fdd` for feature-driven).

## MCP servers

`mcp/<name>.json` is one MCP server, written once: `sync` merges it into each enabled adapter's
config, beside servers added by hand. An active workflow pack's `mcp/*.json` join after every
library's own, as its agents do.

```json
{
  "type": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}", "LOG_LEVEL": "debug" },
  "tools": ["search_issues", "get_issue"],
  "targets": ["claude", "copilot", "cursor"]
}
```

A remote server is `"type": "http"` (streamable HTTP) or `"sse"`, with `url` and `headers`
(`"Authorization": "Bearer ${LINEAR_TOKEN}"`). `type` defaults to stdio when there's a `command`.
Optional: `cwd` (stdio), `tools` (an allowlist), `targets` (default: every enabled adapter), and
`native: {"<tool>": {...}}`, keys merged into that tool's entry as written (for codex, a value of
`null` takes sync's key out). The file name is the server name.

Secrets: an `env` or header value whose name looks like a secret (TOKEN, KEY, SECRET, PASSWORD,
PASS, AUTH, CREDENTIAL, COOKIE in any case, plus `Authorization` and `Proxy-Authorization` headers)
must use `${VAR}` references, with fixed text around them if needed (`Bearer ${X}`). A literal there
is an error: `mcp/github.json: env.GITHUB_TOKEN is a literal; use ${VAR} so the secret stays out
of the repo`, and that server isn't rendered. `${VAR:-default}` works in `.mcp.json`; the other
tools get `${VAR}` without the default, with a warning. Check a file with
`python3 .agents/lib/mcp_render.py check <file>`; see one tool's entry with
`python3 .agents/lib/mcp_render.py render <tool> <file>`.

Where each server lands:
- **claude, copilot** `.mcp.json` (one file for both): `type`, `command`, `args`, `env`, `cwd`,
  `url`, `headers` as written, plus `tools` when copilot is on (Copilot CLI reads it; Claude has
  no per-server allowlist, so with copilot off `tools` warns).
- **cursor** `.cursor/mcp.json`: references become `${env:VAR}`; a remote server has no `type`.
  Drops `cwd` and `tools`.
- **gemini** `.gemini/settings.json`: `url` for sse, `httpUrl` for http, `tools` as `includeTools`.
- **codex** a `[mcp_servers.<name>]` table in a block at the end of `.codex/config.toml`, between
  `# >>> ai-harness mcp (managed by .agents/bin/sync)` and `# <<< ai-harness mcp`. Codex has no
  `${VAR}` expansion, so `"K": "${K}"` becomes `env_vars = ["K"]`, `Authorization: Bearer ${X}`
  becomes `bearer_token_env_var = "X"`, a header `"H": "${X}"` becomes `env_http_headers`, plain
  values go in `env` / `http_headers`, and `tools` becomes `enabled_tools`. Anything else (a
  renamed variable, a default) is left out for codex with a warning. Keep your own codex settings
  above the block; sync moves the block back to the end if lines follow it.

sync owns only the names it wrote, recorded per file in `generated.lock`. A re-render replaces
those, adds new ones, and removes ones whose server is gone; everything else in the file stays. A
server you added by hand with the same name wins, with a warning. A config that ends up holding
nothing is removed. A config that isn't valid JSON is an error and is left alone. An agent's
`mcp: [x]` naming a server no library or config here has gets a warning.

Team mode commits these configs, so a personal server isn't rendered; sync names your tool's user
scope instead (`claude mcp add --scope local`, `~/.cursor/mcp.json`, and so on). Local mode never
touches a config the project tracks, and hides the ones it writes in the clone's exclude block.

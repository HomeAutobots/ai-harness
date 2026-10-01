#!/usr/bin/env python3
"""ai-harness agent renderer. Harness-owned: replaced on upgrade.

Turns neutral agents (agents/<name>.md in a library or an active workflow pack) into each tool's
own agent file. harness.py agents runs it for sync. The frontmatter is a small YAML subset, read
with the standard library: key: value scalars (plain or quoted, > or | folded), lists as [a, b] or
"- a" lines, and native:, a block per tool of lines in that tool's own syntax, copied verbatim.

  agents_render.py parse <file>      print the parsed agent as JSON (a debugging aid)
  agents_render.py render <tool> <file> [KEY=VALUE ...]   print one render (a debugging aid)
"""
import json
import re
import sys


class AgentError(Exception):
    def __init__(self, line, msg):
        Exception.__init__(self, msg)
        self.line = line
        self.msg = msg


def strip_comment(v):
    """The value without a trailing # comment (a # inside quotes stays)."""
    v = v.strip()
    if v[:1] in ("'", '"'):
        q = v[0]
        j = v.find(q, 1)
        while q == '"' and j > 0 and v[j - 1] == "\\":
            j = v.find(q, j + 1)
        return v[:j + 1] if j > 0 else v
    m = re.search(r"\s#", v)
    return v[:m.start()].rstrip() if m else v


def scalar(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        if v[0] == '"':
            try:
                return json.loads(v)
            except ValueError:
                return v[1:-1]
        return v[1:-1].replace("''", "'")
    return v


def _native(lines, i, end, native):
    """Reads native:'s block from line index i; returns the index after it."""
    base = inner = tool = None
    while i < end:
        raw = lines[i]
        if raw.strip() and raw[0] not in " \t":
            break
        if not raw.strip():
            i += 1
            continue
        ind = len(raw) - len(raw.lstrip())
        if base is None:
            base = ind
        if ind == base:
            m = re.match(r"^\s*([a-z]+)\s*:\s*(#.*)?$", raw)
            if not m:
                raise AgentError(i + 1, "native: expects '<tool>:' lines, each with that tool's lines indented under it")
            tool, inner = m.group(1), None
            native.setdefault(tool, [])
        elif ind < base:
            raise AgentError(i + 1, "native: lines don't line up")
        else:
            if inner is None:
                inner = ind
            if ind < inner:
                raise AgentError(i + 1, "native.%s: lines don't line up" % tool)
            native[tool].append((raw[inner:].rstrip(), i + 1))
        i += 1
    return i


def parse(path):
    """(fields, native, body). fields: key -> (value, line); native: tool -> [(text, line)]."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except (OSError, UnicodeDecodeError) as e:
        raise AgentError(1, "can't read it (%s)" % e)
    if not lines or lines[0].rstrip() != "---":
        raise AgentError(1, "no frontmatter (the file must start with a --- line)")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        raise AgentError(1, "the frontmatter isn't closed with a --- line")
    fields, native = {}, {}
    i = 1
    while i < end:
        raw, n = lines[i], i + 1
        s = raw.rstrip()
        if not s.strip() or s.lstrip().startswith("#"):
            i += 1
            continue
        if raw[0] in " \t":
            raise AgentError(n, "unexpected indented line")
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", s)
        if not m:
            raise AgentError(n, "expected 'key: value'")
        key, val = m.group(1), strip_comment(m.group(2))
        i += 1
        if key in fields:
            raise AgentError(n, "'%s' appears twice" % key)
        if key == "native":
            if val:
                raise AgentError(n, "native: takes an indented block per tool")
            i = _native(lines, i, end, native)
            continue
        if val in (">", "|", ">-", "|-"):
            parts = []
            while i < end and (not lines[i].strip() or lines[i][0] in " \t"):
                parts.append(lines[i].strip())
                i += 1
            if val.startswith(">"):
                fields[key] = (" ".join(p for p in parts if p), n)
            else:
                fields[key] = ("\n".join(parts).strip("\n"), n)
            continue
        if val == "":
            items = []
            while i < end and re.match(r"^\s+-\s", lines[i] + " "):
                items.append(scalar(strip_comment(re.sub(r"^\s+-\s*", "", lines[i].rstrip()))))
                i += 1
            fields[key] = (items, n) if items else ("", n)
            continue
        if val.startswith("["):
            if not val.endswith("]"):
                raise AgentError(n, "a [list] must close on the same line")
            inner = val[1:-1].strip()
            fields[key] = ([scalar(x.strip()) for x in inner.split(",")] if inner else [], n)
            continue
        fields[key] = (scalar(val), n)
    body = "\n".join(lines[end + 1:]).strip("\n")
    return fields, native, body


TOOLS = ("claude", "copilot", "cursor", "codex", "gemini")
NEUTRAL = ("read", "search", "edit", "shell", "web")
TIERS = ("fast", "standard", "strong")
EFFORTS = ("low", "medium", "high", "max")
KNOWN = ("name", "description", "tools", "model", "effort", "skills", "mcp", "max_turns", "targets")
MARK = "# generated by .agents/bin/sync from "
PATHS = {"claude": ".claude/agents/%s.md", "copilot": ".github/agents/%s.agent.md",
         "cursor": ".cursor/agents/%s.md", "codex": ".codex/agents/%s.toml", "gemini": ".gemini/agents/%s.md"}
TOOL_NAMES = {
    "claude": {"read": ["Read"], "search": ["Grep", "Glob"], "edit": ["Edit", "Write", "NotebookEdit"],
               "shell": ["Bash"], "web": ["WebFetch", "WebSearch"]},
    "copilot": {"read": ["read"], "search": ["search"], "edit": ["edit"], "shell": ["execute"], "web": ["web"]},
    "gemini": {"read": ["read_file", "list_directory"], "search": ["glob", "grep_search"],
               "edit": ["write_file", "replace"], "shell": ["run_shell_command"],
               "web": ["web_fetch", "google_web_search"]},
}
MCP_NAME = {"claude": "mcp__%s", "copilot": "%s/*", "gemini": "mcp_%s_*"}
ESCALATION = {
    "claude": re.compile(r"^\s*permissionMode\s*:\s*[\"']?(bypassPermissions|dontAsk)\b"),
    "codex": re.compile(r"^\s*(sandbox_mode\s*=\s*[\"']danger-full-access|approval_policy\s*=\s*[\"']never)"),
}
GEMINI_NAME = re.compile(r"^[a-z0-9_-]+$")


class Agent(object):
    """A neutral agent, checked. errors: (line, msg) that stop it rendering; warnings: (line, msg)."""

    def __init__(self, name, path, shown):
        self.name, self.path, self.shown = name, path, shown
        self.errors, self.warnings = [], []
        self.description = self.body = None
        self.tools = self.mcp = self.skills = self.targets = None
        self.model = self.effort = self.max_turns = None
        self.model_line = 1
        self.native = {}
        try:
            fields, native, body = parse(path)
        except AgentError as e:
            self.errors.append((e.line, e.msg))
            return
        get = lambda k: fields.get(k, (None, 1))
        for k, (_, n) in sorted(fields.items(), key=lambda kv: kv[1][1]):
            if k not in KNOWN:
                self.warnings.append((n, "unknown field '%s', ignored (put a tool's own fields under native:)" % k))
        v, n = get("name")
        if v not in (None, "") and v != name:
            self.errors.append((n, "name '%s' doesn't match the file name '%s'" % (v, name)))
        v, n = get("description")
        if not isinstance(v, str) or not v.strip():
            self.errors.append((1, "no description (every tool needs one to know when to use the agent)"))
        else:
            self.description = v.strip()
        if not body.strip():
            self.errors.append((1, "no prompt (the text after the frontmatter)"))
        self.body = body
        v, n = get("tools")
        if v is not None:
            if not isinstance(v, list):
                v = [v]
            self.tools, self.mcp = [], []
            for t in v:
                if t in NEUTRAL:
                    self.tools.append(t)
                elif t.startswith("mcp:") and len(t) > 4:
                    self.mcp.append(t[4:])
                else:
                    self.warnings.append((n, "tools: '%s' isn't read, search, edit, shell, web, or mcp:<server>; "
                                             "ignored" % t))
        v, n = get("mcp")
        if v is not None:
            for s in (v if isinstance(v, list) else [v]):
                if self.mcp is None:
                    self.mcp = []
                if s not in self.mcp:
                    self.mcp.append(s)
        v, n = get("model")
        self.model_line = n
        if v is not None and v != "inherit":
            if v in TIERS:
                self.model = v
            else:
                self.warnings.append((n, "model: '%s' isn't fast, standard, strong, or inherit; inherits (pin a "
                                         "model with a native: line)" % v))
        v, n = get("effort")
        if v is not None and v != "inherit":
            if v in EFFORTS:
                self.effort = v
            else:
                self.warnings.append((n, "effort: '%s' isn't low, medium, high, max, or inherit; ignored" % v))
        v, n = get("skills")
        if v:
            self.skills = v if isinstance(v, list) else [v]
        v, n = get("max_turns")
        if v not in (None, ""):
            if re.match(r"^[0-9]+$", str(v)):
                self.max_turns = int(v)
            else:
                self.warnings.append((n, "max_turns: '%s' isn't a number; ignored" % v))
        v, n = get("targets")
        if v is not None:
            v = v if isinstance(v, list) else [v]
            self.targets = [t for t in v if t in TOOLS]
            for t in v:
                if t not in TOOLS:
                    self.warnings.append((n, "targets: unknown tool '%s' (claude, copilot, cursor, codex, gemini)" % t))
        for tool, ls in sorted(native.items()):
            if tool not in TOOLS:
                self.warnings.append((ls[0][1] - 1 if ls else 1, "native: unknown tool '%s'" % tool))
                continue
            self.native[tool] = ls
            pat = ESCALATION.get(tool)
            for text, ln in ls:
                if pat and pat.match(text):
                    self.warnings.append((ln, "native.%s grants more than the harness default (%s); rendered as "
                                              "written, and the policy hook still checks every tool call"
                                              % (tool, text.strip())))

    def wants(self, tool):
        return self.targets is None or tool in self.targets


def q(v):
    return json.dumps(v, ensure_ascii=False)


def qlist(xs):
    return "[" + ", ".join(q(x) for x in xs) + "]"


def _conf_value(conf, key):
    v = (conf.get(key) or "").strip()
    return None if v in ("", "inherit") else v


def model_for(agent, tool, conf):
    return _conf_value(conf, "MODEL_%s_%s" % (agent.model.upper(), tool.upper())) if agent.model else None


def effort_for(agent, tool, conf, warn):
    if agent.effort:
        return agent.effort
    if not agent.model:
        return None
    key = "EFFORT_%s_%s" % (agent.model.upper(), tool.upper())
    v = _conf_value(conf, key)
    if v and v not in EFFORTS:
        warn(".agents/harness.conf: %s=%s isn't low, medium, high, or max; ignored" % (key, v))
        return None
    return v


def coarse(agent):
    """(read_only, exact) for tools that can only be read-only or have everything (Cursor, Codex)."""
    if agent.tools is None:
        return False, True
    t = set(agent.tools)
    if not (t & set(["edit", "shell"])):
        return True, True
    return False, set(NEUTRAL) <= t


def mapped_tools(agent, tool):
    out = []
    for t in agent.tools:
        for x in TOOL_NAMES[tool][t]:
            if x not in out:
                out.append(x)
    for s in agent.mcp or []:
        out.append(MCP_NAME[tool] % s)
    return out


def _native_keys(lines, toml):
    pat = r"^([A-Za-z_][\w.-]*)\s*=" if toml else r"^([A-Za-z_][\w-]*)\s*:"
    return set(m.group(1) for m in (re.match(pat, t) for t, _ in lines) if m)


def render(agent, tool, conf, source):
    """(text, missing, notes): the file for that tool, the fields it can't express, other warnings."""
    missing, notes = [], []
    model = model_for(agent, tool, conf)
    effort = effort_for(agent, tool, conf, notes.append)
    keys = [("name", q(agent.name)), ("description", q(agent.description))]
    if tool in ("claude", "copilot", "gemini"):
        if agent.tools is not None:
            keys.append(("tools", qlist(mapped_tools(agent, tool))))
    elif tool in ("cursor", "codex"):
        ro, exact = coarse(agent)
        if not exact:
            missing.append("tools (it can only limit an agent to read-only, so this one gets every tool)")
        if agent.mcp and agent.tools is not None:
            missing.append("mcp (it can't limit an agent to some MCP servers)")
    if tool == "cursor" and effort and model:
        model = "%s[effort=%s]" % (model, effort)
    elif tool == "cursor" and effort:
        missing.append("effort (it goes in the model id, and no MODEL_%s_CURSOR is set)" % (agent.model or "<tier>").upper())
    if tool == "codex" and effort == "max":
        notes.append("effort: max becomes high for codex (its highest documented level)")
        effort = "high"
    if model and tool != "codex":
        keys.append(("model", q(model)))
    if tool == "claude":
        if effort:
            keys.append(("effort", q(effort)))
        if agent.skills:
            keys.append(("skills", qlist(agent.skills)))
        if agent.mcp:
            keys.append(("mcpServers", qlist(agent.mcp)))
        if agent.max_turns is not None:
            keys.append(("maxTurns", str(agent.max_turns)))
    if tool == "cursor" and coarse(agent)[0]:
        keys.append(("readonly", "true"))
    if tool == "gemini" and agent.max_turns is not None:
        keys.append(("max_turns", str(agent.max_turns)))
    if tool in ("copilot", "gemini") and effort:
        missing.append("effort")
    if tool != "claude" and agent.skills:
        missing.append("skills")
    if tool in ("copilot", "cursor", "codex") and agent.max_turns is not None:
        missing.append("max_turns")
    native = [t for t, _ in agent.native.get(tool, [])]
    head = MARK + source + "; edit the source, not this file"
    if tool == "codex":
        nk = _native_keys(agent.native.get(tool, []), True)
        out = [head]
        tk = [("name", q(agent.name)), ("description", q(agent.description))]
        if model:
            tk.append(("model", q(model)))
        if effort:
            tk.append(("model_reasoning_effort", q(effort)))
        if coarse(agent)[0]:
            tk.append(("sandbox_mode", q("read-only")))
        out += ["%s = %s" % (k, v) for k, v in tk if k not in nk]
        if "developer_instructions" not in nk:
            if "'''" in agent.body or agent.body.endswith("'"):
                out.append("developer_instructions = " + q(agent.body))
            else:
                out.append("developer_instructions = '''\n" + agent.body + "\n'''")
        return "\n".join(out + native) + "\n", missing, notes
    nk = _native_keys(agent.native.get(tool, []), False)
    out = ["---", head] + ["%s: %s" % (k, v) for k, v in keys if k not in nk] + native + ["---", agent.body]
    return "\n".join(out) + "\n", missing, notes


def _main(argv):
    if len(argv) == 3 and argv[1] == "parse":
        try:
            fields, native, body = parse(argv[2])
        except AgentError as e:
            print("%s:%d: %s" % (argv[2], e.line, e.msg))
            return 1
        out = dict((k, v[0]) for k, v in fields.items())
        out["native"] = dict((t, [x[0] for x in ls]) for t, ls in native.items())
        out["body"] = body
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return 0
    if len(argv) >= 4 and argv[1] == "render" and argv[2] in TOOLS:
        import os
        path = argv[3]
        name = os.path.basename(path)[:-3] if path.endswith(".md") else os.path.basename(path)
        conf = dict(a.split("=", 1) for a in argv[4:] if "=" in a)
        agent = Agent(name, path, path)
        for ln, msg in agent.warnings:
            print("warning: %s:%d: %s" % (path, ln, msg), file=sys.stderr)
        if agent.errors:
            for ln, msg in agent.errors:
                print("%s:%d: %s" % (path, ln, msg))
            return 1
        text, missing, notes = render(agent, argv[2], conf, path)
        for m in missing:
            print("warning: %s: %s: not supported by %s" % (path, m, argv[2]), file=sys.stderr)
        for m in notes:
            print("warning: %s: %s" % (path, m), file=sys.stderr)
        sys.stdout.write(text)
        return 0
    print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

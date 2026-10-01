#!/usr/bin/env python3
"""ai-harness agent renderer. Harness-owned: replaced on upgrade.

Turns neutral agents (agents/<name>.md in a library or an active workflow pack) into each tool's
own agent file. harness.py agents runs it for sync. The frontmatter is a small YAML subset, read
with the standard library: key: value scalars (plain or quoted, > or | folded, or plain lines
indented under the key), lists as [a, b] or "- a" lines, and native:, a block per tool of lines in
that tool's own syntax, copied verbatim. Limits of the subset: a | block loses its lines' relative
indentation, and a [a, b] item can't contain a comma (use "- a" lines for that).

  agents_render.py parse <file>      print the parsed agent as JSON (a debugging aid)
  agents_render.py render <tool> <file> [KEY=VALUE ...]   print one render (a debugging aid)
"""
import hashlib
import json
import os
import re
import sys


class AgentError(Exception):
    def __init__(self, line, msg):
        Exception.__init__(self, msg)
        self.line = line
        self.msg = msg


def strip_comment(v):
    """The value without a # comment (a # inside quotes stays; a value that is only a comment is empty)."""
    v = v.strip()
    if v[:1] in ("'", '"'):
        q = v[0]
        j = v.find(q, 1)
        while j > 0:
            if q == "'" and v[j + 1:j + 2] == "'":
                j = v.find(q, j + 2)
                continue
            if q == '"':
                k = j - 1
                while k > 0 and v[k] == "\\":
                    k -= 1
                if (j - 1 - k) % 2:
                    j = v.find(q, j + 1)
                    continue
            break
        return v[:j + 1] if j > 0 else v
    m = re.search(r"(^|\s)#", v)
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


def _skip(raw):
    """A blank line or a comment-only line."""
    s = raw.strip()
    return not s or s.startswith("#")


def _native(lines, i, end, native, native_at):
    """Reads native:'s block from line index i; returns the index after it."""
    base = inner = tool = None
    while i < end:
        raw = lines[i]
        if _skip(raw):
            i += 1
            continue
        if raw[0] not in " \t":
            break
        ind = len(raw) - len(raw.lstrip())
        if base is None:
            base = ind
        if ind == base:
            m = re.match(r"^\s*([a-z]+)\s*:\s*(#.*)?$", raw)
            if not m:
                raise AgentError(i + 1, "native: expects '<tool>:' lines, each with that tool's lines indented under it")
            tool, inner = m.group(1), None
            native.setdefault(tool, [])
            native_at.setdefault(tool, i + 1)
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
    """(fields, native, native_at, body). fields: key -> (value, line), value None when the key is
    there but empty; native: tool -> [(text, line)]; native_at: tool -> the line of its '<tool>:'."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            lines = fh.read().split("\n")
    except (OSError, UnicodeDecodeError) as e:
        raise AgentError(1, "can't read it (%s)" % e)
    if not lines or lines[0].rstrip() != "---":
        raise AgentError(1, "no frontmatter (the file must start with a --- line)")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        raise AgentError(1, "the frontmatter isn't closed with a --- line")
    fields, native, native_at = {}, {}, {}
    i = 1
    while i < end:
        raw, n = lines[i], i + 1
        s = raw.rstrip()
        if _skip(s):
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
            i = _native(lines, i, end, native, native_at)
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
            items, j = [], i
            while j < end:
                if _skip(lines[j]):
                    j += 1
                    continue
                if not re.match(r"^\s*-(\s|$)", lines[j]):
                    break
                items.append(scalar(strip_comment(re.sub(r"^\s*-", "", lines[j].rstrip()))))
                j += 1
                i = j
            if items:
                fields[key] = (items, n)
                continue
            parts = []
            while i < end and (_skip(lines[i]) or lines[i][0] in " \t"):
                if not _skip(lines[i]):
                    parts.append(strip_comment(lines[i]))
                i += 1
            if len(parts) == 1:
                fields[key] = (scalar(parts[0]), n)
            else:
                fields[key] = (" ".join(parts) if parts else None, n)
            continue
        if val.startswith("["):
            if not val.endswith("]"):
                raise AgentError(n, "a [list] must close on the same line")
            inner = val[1:-1].strip()
            fields[key] = ([scalar(x.strip()) for x in inner.split(",")] if inner else [], n)
            continue
        fields[key] = (scalar(val), n)
    body = "\n".join(lines[end + 1:]).strip("\n")
    return fields, native, native_at, body


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
    "codex": re.compile(r"^\s*(sandbox_mode\s*=\s*[\"']danger-full-access|approval_policy\s*=\s*[\"']never[\"'])"),
}
# Gemini agent names must match this; the caller (sync) skips gemini for an agent whose name doesn't.
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
            fields, native, native_at, body = parse(path)
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
            self.errors.append((n, "no description (every tool needs one to know when to use the agent)"))
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
                self.warnings.append((native_at.get(tool, 1), "native: unknown tool '%s'" % tool))
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


def tq(v):
    """A TOML basic string (JSON's escapes are valid TOML; DEL isn't allowed raw)."""
    return q(v).replace("\x7f", "\\u007f")


TOML_UNSAFE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|\r(?!\n)")


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
    """The top-level keys a tool's native: lines set (for TOML, the ones before the first [table])."""
    pat = r"^([A-Za-z_][\w.-]*)\s*=" if toml else r"^([A-Za-z_][\w-]*)\s*:"
    out = set()
    for t, _ in lines:
        if toml and t.startswith("["):
            break
        m = re.match(pat, t)
        if m:
            out.add(m.group(1))
    return out


def render(agent, tool, conf, source):
    """(text, missing, notes): the file for that tool, the fields it can't express, other warnings."""
    missing, notes = [], []
    nk = _native_keys(agent.native.get(tool, []), tool == "codex")
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
        if agent.mcp and (tool == "codex" or agent.tools is not None):
            missing.append("mcp (it can't limit an agent to some MCP servers)")
    if tool == "cursor" and effort and model:
        model = "%s[effort=%s]" % (model, effort)
    elif tool == "cursor" and effort and "model" not in nk:
        missing.append("effort (it goes in the model id, and no MODEL_%s_CURSOR is set)" % (agent.model or "<tier>").upper())
    if tool == "codex" and effort == "max" and "model_reasoning_effort" not in nk:
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
        out = [head]
        tk = [("name", tq(agent.name)), ("description", tq(agent.description))]
        if model:
            tk.append(("model", tq(model)))
        if effort:
            tk.append(("model_reasoning_effort", tq(effort)))
        if coarse(agent)[0]:
            tk.append(("sandbox_mode", tq("read-only")))
        out += ["%s = %s" % (k, v) for k, v in tk if k not in nk]
        if "developer_instructions" not in nk:
            b = agent.body
            if "'''" in b or b.endswith("'") or TOML_UNSAFE.search(b):
                out.append("developer_instructions = " + tq(b))
            else:
                out.append("developer_instructions = '''\n" + agent.body + "\n'''")
        return "\n".join(out + native) + "\n", missing, notes
    out = ["---", head] + ["%s: %s" % (k, v) for k, v in keys if k not in nk] + native + ["---", agent.body]
    return "\n".join(out) + "\n", missing, notes


def marked(text):
    """True if sync wrote it: the marker in its first lines."""
    return any(l.startswith(MARK) for l in text.split("\n")[:3])


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def marked_renders(root):
    """Every file in the agent render dirs that sync wrote, as repo-relative paths."""
    out = []
    for tool in TOOLS:
        d = os.path.dirname(PATHS[tool])
        suffix = PATHS[tool].split("%s", 1)[1]
        full = os.path.join(root, d)
        if not os.path.isdir(full):
            continue
        for f in sorted(os.listdir(full)):
            rel = os.path.join(d, f)
            if f.endswith(suffix) and os.path.isfile(os.path.join(root, rel)) and marked(_read(os.path.join(root, rel)) or ""):
                out.append(rel)
    return out


def source_label(root, path, lib):
    """Where an agent came from, as the marker shows it: a repo path, or library:agents/<file> for
    one outside the repo (no home directories in committed files)."""
    ap = os.path.abspath(path)
    if ap.startswith(root + os.sep):
        return os.path.relpath(ap, root)
    parts = ap.split(os.sep)
    tail = os.sep.join(parts[-4:]) if len(parts) > 4 and parts[-4] == "workflows" else os.sep.join(parts[-2:])
    return "%s library: %s" % (lib, tail)


def sync_agents(root, conf, rows, check, tracked, old_lock, team):
    """Renders every agent for every enabled adapter.
    rows: (name, path, library) from sync, winners only, in sync's order.
    tracked(rel) -> bool. old_lock: {rel: sha256}. team: bool.
    Returns dict: wrote, drift, warnings, errors (lists of str), lock ({rel: sha}), renders
    ([(rel, personal)])."""
    adapters = [a for a in TOOLS if a in (conf.get("ADAPTERS") or "").split()]
    local = not team
    res = {"wrote": [], "drift": [], "warnings": [], "errors": [], "lock": {}, "renders": []}
    want = {}
    for name, path, lib in rows:
        shown = os.path.relpath(path, root) if os.path.abspath(path).startswith(root + os.sep) else path
        agent = Agent(name, path, shown)
        for ln, msg in agent.warnings:
            res["warnings"].append("%s:%d: %s" % (shown, ln, msg))
        if agent.errors:
            for ln, msg in agent.errors:
                res["errors"].append("%s:%d: %s; agent not rendered" % (shown, ln, msg))
            continue
        personal = lib in ("personal", "personal-listed")
        missing = {}
        for tool in adapters:
            if not agent.wants(tool):
                continue
            if tool == "gemini" and not GEMINI_NAME.match(name):
                res["warnings"].append("%s: gemini needs a name of lowercase letters, digits, - and _; not rendered "
                                       "for gemini" % shown)
                continue
            text, miss, notes = render(agent, tool, conf, source_label(root, path, lib))
            for m in miss:
                missing.setdefault(m, []).append(tool)
            for m in notes:
                res["warnings"].append("%s: %s" % (shown, m))
            want[PATHS[tool] % name] = (text, personal, shown, tool)
        for m, ts in sorted(missing.items()):
            res["warnings"].append("%s: %s: not supported by %s; they get the agent without it"
                                   % (shown, m, ", ".join(ts)))
    for rel in sorted(want):
        text, personal, shown, tool = want[rel]
        full = os.path.join(root, rel)
        have = _read(full) if os.path.exists(full) else None
        if local and tracked(rel):
            res["warnings"].append("%s is tracked by the project; local mode leaves it alone" % rel)
            continue
        if os.path.exists(full) and (have is None or not marked(have)):
            res["warnings"].append("%s wasn't made by sync, leaving it alone (the agent from %s isn't rendered for "
                                   "%s)" % (rel, shown, tool))
            continue
        if team and personal and tracked(rel):
            res["warnings"].append("your personal agent in %s isn't rendered for %s: the project tracks %s"
                                   % (shown, tool, rel))
            continue
        res["renders"].append((rel, personal))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if team and not personal:
            res["lock"][rel] = digest
        if have == text:
            continue
        if check:
            res["drift"].append(rel)
            continue
        if have is not None and rel in old_lock and hashlib.sha256(have.encode("utf-8")).hexdigest() != old_lock[rel]:
            res["warnings"].append("%s was edited by hand; sync rewrote it from %s (edit the source instead)" % (rel, shown))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        res["wrote"].append("wrote " + rel)
    for rel in marked_renders(root):
        if rel in want:
            continue
        if local and tracked(rel):
            continue
        if check:
            res["drift"].append(rel + " (stale)")
            continue
        os.remove(os.path.join(root, rel))
        res["wrote"].append("removed stale " + rel)
    return res


def _main(argv):
    if len(argv) == 3 and argv[1] == "parse":
        try:
            fields, native, _, body = parse(argv[2])
        except AgentError as e:
            print("%s:%d: %s" % (argv[2], e.line, e.msg))
            return 1
        out = dict((k, v[0]) for k, v in fields.items())
        out["native"] = dict((t, [x[0] for x in ls]) for t, ls in native.items())
        out["body"] = body
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return 0
    if len(argv) >= 4 and argv[1] == "render" and argv[2] in TOOLS:
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

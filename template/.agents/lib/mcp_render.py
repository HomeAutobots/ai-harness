#!/usr/bin/env python3
"""ai-harness MCP server renderer. Harness-owned: replaced on upgrade.

Turns neutral MCP servers (mcp/<name>.json in a library or an active workflow pack) into each
tool's own config entry. harness.py mcp runs it for sync. A server file is a JSON object:
type (stdio, http, sse; stdio when there's a command), command, args, env, cwd for stdio; url,
headers for http and sse; and optionally tools (an allowlist), targets (which adapters get it),
and native ({<tool>: {...}}, keys merged into that tool's entry as written).

Secrets are only ever ${VAR} references: an env or header value whose name looks like a secret
must hold a reference (fixed text around it is fine, as in "Bearer ${TOKEN}"). ${VAR:-default}
gives a default where the tool supports one.

  mcp_render.py check <file>     check a server file: errors on stdout, warnings on stderr
  mcp_render.py render <tool> <file> [ADAPTERS="..."]   print one tool's entry (a debugging aid)
"""
import json
import os
import re
import sys

sys.dont_write_bytecode = True

TOOLS = ("claude", "copilot", "cursor", "codex", "gemini")
TYPES = ("stdio", "http", "sse")
KNOWN = ("type", "command", "args", "env", "cwd", "url", "headers", "tools", "targets", "native")
STDIO_KEYS = ("command", "args", "env", "cwd")
REMOTE_KEYS = ("url", "headers")
SECRET_WORDS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASS", "AUTH", "CREDENTIAL", "COOKIE")
SECRET_HEADERS = ("authorization", "proxy-authorization")
# ${VAR} or ${VAR:-default}
REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def secret_name(name, header):
    up = name.upper()
    if header and name.lower() in SECRET_HEADERS:
        return True
    return any(w in up for w in SECRET_WORDS)


def refs(value):
    """[(name, default or None)] for each ${VAR} reference in a string."""
    return [(m.group(1), m.group(2)) for m in REF.finditer(value)]


def _strings(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


class Server(object):
    """A neutral MCP server, checked. errors: (line or None, msg) that stop it rendering;
    warnings: (line or None, msg)."""

    def __init__(self, name, path, shown):
        self.name, self.path, self.shown = name, path, shown
        self.errors, self.warnings = [], []
        self.type = None
        self.data = {}
        self.tools = self.targets = None
        self.native = {}
        try:
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            self.errors.append((None, "can't read it (%s)" % e))
            return
        try:
            data = json.loads(text)
        except ValueError as e:
            self.errors.append((getattr(e, "lineno", None), "not valid JSON (%s)" % getattr(e, "msg", e)))
            return
        if not isinstance(data, dict):
            self.errors.append((None, "expected a JSON object ({\"command\": ...} or {\"type\": \"http\", ...})"))
            return
        self._check(data)

    def _err(self, msg):
        self.errors.append((None, msg))

    def _warn(self, msg):
        self.warnings.append((None, msg))

    def _check(self, data):
        for k in data:
            if k not in KNOWN:
                self._warn("unknown key '%s', ignored (put a tool's own keys under native)" % k)
        t = data.get("type")
        if t is None:
            if "command" in data:
                t = "stdio"
            elif "url" in data:
                self._err('type: a server with a url needs "type": "http" or "sse"')
                return
            else:
                self._err('no command or url (a stdio server needs "command"; a remote one "type" and "url")')
                return
        if t not in TYPES:
            self._err("type: '%s' isn't stdio, http, or sse" % t)
            return
        self.type = t
        mine, other = (STDIO_KEYS, REMOTE_KEYS) if t == "stdio" else (REMOTE_KEYS, STDIO_KEYS)
        for k in other:
            if k in data:
                self._warn("%s: not used by %s %s server; ignored" % (k, "a" if t == "stdio" else "an", t))
        if t == "stdio":
            if not isinstance(data.get("command"), str) or not data["command"].strip():
                self._err("command: required for a stdio server (a string)")
            if "args" in data and not _strings(data["args"]):
                self._err("args: expected a list of strings")
            if "cwd" in data and not isinstance(data["cwd"], str):
                self._err("cwd: expected a string")
        else:
            if not isinstance(data.get("url"), str) or not data["url"].strip():
                self._err("url: required for an %s server (a string)" % t)
        for k in ("env", "headers"):
            if k not in mine or k not in data:
                continue
            v = data[k]
            if not isinstance(v, dict):
                self._err("%s: expected an object of strings" % k)
                continue
            for name, val in v.items():
                if not isinstance(val, str):
                    self._err("%s.%s: expected a string" % (k, name))
                elif secret_name(name, k == "headers") and val != "" and not refs(val):
                    self._err("%s.%s is a literal; use ${VAR} so the secret stays out of the repo" % (k, name))
        for k in mine:
            if k in data:
                self.data[k] = data[k]
        if "tools" in data:
            if not _strings(data["tools"]) or not all(x.strip() for x in data["tools"]):
                self._err("tools: expected a list of strings (tool names)")
            else:
                self.tools = list(data["tools"])
        if "targets" in data:
            v = data["targets"]
            if not _strings(v):
                self._err("targets: expected a list of tool names")
            else:
                self.targets = [x for x in v if x in TOOLS]
                for x in v:
                    if x not in TOOLS:
                        self._warn("targets: unknown tool '%s' (claude, copilot, cursor, codex, gemini)" % x)
        if "native" in data:
            v = data["native"]
            if not isinstance(v, dict):
                self._err("native: expected an object of {<tool>: {...}}")
            else:
                for tool, block in sorted(v.items()):
                    if tool not in TOOLS:
                        self._warn("native: unknown tool '%s'" % tool)
                    elif not isinstance(block, dict):
                        self._err("native.%s: expected an object" % tool)
                    else:
                        self.native[tool] = block
                        if tool == "gemini" and block.get("trust") is True:
                            self._warn("native.gemini: trust: true skips gemini's confirmation for every tool this "
                                       "server has; rendered as written")

    def wants(self, tool):
        return self.targets is None or tool in self.targets

    def messages(self):
        """(errors, warnings) as 'path[:line]: msg' strings."""
        def fmt(ls):
            return ["%s%s: %s" % (self.shown, ":%d" % ln if ln else "", m) for ln, m in ls]
        return fmt(self.errors), fmt(self.warnings)


# ------------------------------------------------------------------ per-tool entries

FILES = {"claude": ".mcp.json", "copilot": ".mcp.json", "cursor": os.path.join(".cursor", "mcp.json"),
         "gemini": os.path.join(".gemini", "settings.json"), "codex": os.path.join(".codex", "config.toml")}
BARE = re.compile(r"^[A-Za-z0-9_-]+$")
RAW_UNSAFE = re.compile("[\x7f-\x9f￾￿]")
WHOLE_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
BEARER_REF = re.compile(r"^Bearer \$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def tq(v):
    """A TOML basic string (JSON's escapes are TOML's too; DEL and C1 controls escaped as well)."""
    return RAW_UNSAFE.sub(lambda m: "\\u%04x" % ord(m.group()), json.dumps(v, ensure_ascii=False))


def tkey(k):
    return k if BARE.match(k) else tq(k)


def tvalue(v):
    """A JSON value as TOML: strings, numbers, booleans, arrays, objects as inline tables (a null
    member is left out)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, str):
        return tq(v)
    if isinstance(v, list):
        return "[" + ", ".join(tvalue(x) for x in v) + "]"
    if isinstance(v, dict):
        items = ["%s = %s" % (tkey(k), tvalue(x)) for k, x in v.items() if x is not None]
        return "{ " + ", ".join(items) + " }" if items else "{}"
    raise ValueError("no TOML for %r" % (v,))


def _subst(value, style, dropped):
    """value with each ${VAR} written the tool's way: as is (claude), ${env:VAR} (cursor), or ${VAR}
    without a default (gemini). dropped gets an item when a default was left out."""
    def rep(m):
        if style == "claude":
            return m.group(0)
        if m.group(2) is not None:
            dropped.append(m.group(1))
        return ("${env:%s}" if style == "cursor" else "${%s}") % m.group(1)
    return REF.sub(rep, value)


def _json_entry(srv, style, keys):
    """(entry, a default was dropped) for a JSON tool; keys is [(entry key, source key)] in order."""
    out, dropped = {}, []
    for ek, sk in keys:
        if sk not in srv.data:
            continue
        v = srv.data[sk]
        if isinstance(v, str):
            v = _subst(v, style, dropped)
        elif isinstance(v, list):
            v = [_subst(x, style, dropped) for x in v]
        elif isinstance(v, dict):
            v = dict((k, _subst(x, style, dropped)) for k, x in v.items())
        out[ek] = v
    return out, bool(dropped)


def _merge_native(entry, srv, tools):
    for t in tools:
        for k, v in srv.native.get(t, {}).items():
            entry[k] = v
    return entry


def render(srv, tool, adapters):
    """(entry, missing, notes) for one tool. entry: a dict for the JSON tools, the [mcp_servers.<n>]
    table text for codex, None when the tool gets nothing; missing: fields it can't express; notes:
    other warnings. claude and copilot share .mcp.json: either gives the entry for both."""
    missing, notes = [], []
    if not srv.wants(tool):
        return None, missing, notes
    if tool in ("claude", "copilot"):
        on = [t for t in ("claude", "copilot") if t in adapters and srv.wants(t)] or [tool]
        keys = [("command", "command"), ("args", "args"), ("env", "env"), ("cwd", "cwd"),
                ("url", "url"), ("headers", "headers")]
        entry, _ = _json_entry(srv, "claude", keys)
        entry = dict([("type", srv.type)] + list(entry.items()))
        if srv.tools is not None:
            if "copilot" in on:
                entry["tools"] = list(srv.tools)   # Copilot CLI reads it; Claude has no per-server list
            else:
                missing.append("tools")
        return _merge_native(entry, srv, on), missing, notes
    if tool == "cursor":
        if srv.type == "stdio":
            entry, dropped = _json_entry(srv, "cursor", [("command", "command"), ("args", "args"), ("env", "env")])
            entry = dict([("type", "stdio")] + list(entry.items()))
            if "cwd" in srv.data:
                missing.append("cwd")
        else:
            entry, dropped = _json_entry(srv, "cursor", [("url", "url"), ("headers", "headers")])
        if srv.tools is not None:
            missing.append("tools")
        if dropped:
            missing.append("defaults in ${VAR:-default}")
        return _merge_native(entry, srv, ["cursor"]), missing, notes
    if tool == "gemini":
        if srv.type == "stdio":
            keys = [("command", "command"), ("args", "args"), ("env", "env"), ("cwd", "cwd")]
        else:
            keys = [("httpUrl" if srv.type == "http" else "url", "url"), ("headers", "headers")]
        entry, dropped = _json_entry(srv, "gemini", keys)
        if srv.tools is not None:
            entry["includeTools"] = list(srv.tools)
        if dropped:
            missing.append("defaults in ${VAR:-default}")
        return _merge_native(entry, srv, ["gemini"]), missing, notes
    if tool == "codex":
        return _codex(srv, notes), missing, notes
    return None, missing, notes


def _codex(srv, notes):
    """The [mcp_servers.<name>] table. Codex has no ${VAR} expansion: it passes env vars by name
    (env_vars), reads a bearer token from one (bearer_token_env_var), and header values from others
    (env_http_headers)."""
    d = srv.data
    for k in ("command", "cwd", "url"):
        if isinstance(d.get(k), str) and refs(d[k]):
            notes.append("codex can't expand ${VAR} in %s; not rendered for codex" % k)
            return None
    if any(refs(a) for a in d.get("args", [])):
        notes.append("codex can't expand ${VAR} in args; not rendered for codex")
        return None
    t = []
    if srv.type == "stdio":
        t.append(("command", d["command"]))
        if "args" in d:
            t.append(("args", list(d["args"])))
        env, env_vars = {}, []
        for k, v in d.get("env", {}).items():
            if not refs(v):
                env[k] = v
            elif v == "${%s}" % k:
                env_vars.append(k)
            else:
                notes.append('env.%s: codex passes variables by name only ("K": "${K}"); left out for codex' % k)
        if env:
            t.append(("env", env))
        if env_vars:
            t.append(("env_vars", env_vars))
        if "cwd" in d:
            t.append(("cwd", d["cwd"]))
    else:
        t.append(("url", d["url"]))
        bearer, lit, envh = None, {}, {}
        for k, v in d.get("headers", {}).items():
            m = BEARER_REF.match(v)
            if k.lower() == "authorization" and m and bearer is None:
                bearer = m.group(1)
            elif not refs(v):
                lit[k] = v
            elif WHOLE_REF.match(v):
                envh[k] = WHOLE_REF.match(v).group(1)
            else:
                notes.append('headers.%s: codex can\'t express this (only "Bearer ${VAR}" for Authorization, or a '
                             'whole "${VAR}" with no default); left out for codex' % k)
        if bearer:
            t.append(("bearer_token_env_var", bearer))
        if lit:
            t.append(("http_headers", lit))
        if envh:
            t.append(("env_http_headers", envh))
    if srv.tools is not None:
        t.append(("enabled_tools", list(srv.tools)))
    table = dict(t)
    for k, v in srv.native.get("codex", {}).items():
        if v is None:
            table.pop(k, None)   # null takes sync's key out
        else:
            table[k] = v
    lines = ["[mcp_servers.%s]" % tkey(srv.name)]
    for k, v in table.items():
        try:
            lines.append("%s = %s" % (tkey(k), tvalue(v)))
        except ValueError:
            notes.append("native.codex.%s: no TOML for that value; left out" % k)
    return "\n".join(lines) + "\n"


def tool_messages(srv, missing_by_field):
    """One warning per field some tools can't express, naming them."""
    return ["%s: %s: not supported by %s; they get the server without it" % (srv.shown, m, ", ".join(ts))
            for m, ts in sorted(missing_by_field.items())]


def _main(argv):
    if len(argv) == 3 and argv[1] == "check":
        path = argv[2]
        name = os.path.basename(path)
        name = name[:-5] if name.endswith(".json") else name
        srv = Server(name, path, path)
        errors, warnings = srv.messages()
        for w in warnings:
            print("warning: " + w, file=sys.stderr)
        for e in errors:
            print(e)
        return 1 if errors else 0
    if len(argv) >= 4 and argv[1] == "render" and argv[2] in TOOLS:
        path = argv[3]
        name = os.path.basename(path)
        name = name[:-5] if name.endswith(".json") else name
        conf = dict(a.split("=", 1) for a in argv[4:] if "=" in a)
        adapters = (conf.get("ADAPTERS") or argv[2]).split()
        srv = Server(name, path, path)
        errors, warnings = srv.messages()
        for w in warnings:
            print("warning: " + w, file=sys.stderr)
        if errors:
            for e in errors:
                print(e)
            return 1
        entry, missing, notes = render(srv, argv[2], adapters)
        for m in tool_messages(srv, dict((m, [argv[2]]) for m in missing)):
            print("warning: " + m, file=sys.stderr)
        for n in notes:
            print("warning: %s: %s" % (path, n), file=sys.stderr)
        if entry is not None:
            sys.stdout.write(entry if isinstance(entry, str) else json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
        return 0
    print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

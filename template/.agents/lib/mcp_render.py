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
    print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

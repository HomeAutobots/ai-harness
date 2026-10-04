#!/usr/bin/env python3
"""ai-harness MCP server renderer. Harness-owned: replaced on upgrade.

Turns neutral MCP servers (mcp/<name>.json in a library or an active workflow pack) into each
tool's own config entry. harness.py mcp runs it for sync. A server file is a JSON object:
type (stdio, http, sse; stdio when there's a command), command, args, env, cwd for stdio; url,
headers for http and sse; and optionally tools (an allowlist), targets (which adapters get it),
and native ({<tool>: {...}}, keys merged into that tool's entry as written).

Secrets are only ever ${VAR} references: an env or header value whose name looks like a secret
must hold a reference (fixed text around it is fine, as in "Bearer ${TOKEN}"), and no default may
look like a credential. No command, cwd, env, header, args, or url value may hold a key shape
guard knows (its secret rules). args: no literal credential after a secret-looking flag (--token=..., --api-key <value>) or in a
secret header given to --header. url: no user:pass@, no secret-looking query or fragment
parameter. ${VAR:-default} gives a default where the tool supports one.

  mcp_render.py check <file>     check a server file: errors on stdout, warnings on stderr
  mcp_render.py render <tool> <file> [ADAPTERS="..."]   print one tool's entry (a debugging aid)
"""
import json
import os
import re
import sys
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True   # before the import below: no .agents/lib/__pycache__ left in the project
from guard_shapes import PLACEHOLDER, key_shape   # guard's secret rules, shared with the debug pack

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


# Literal secrets in args and url. A flag or query parameter counts as secret when its name's last
# word is one of these (so --api-key, --githubToken, and ?access_token do; --key-file and
# --auth-mode don't). A key whose word before it says it isn't a credential (--cache-key) doesn't.
SECRET_PARTS = ("token", "key", "secret", "password", "passwd", "auth", "credential", "credentials",
                "apikey", "accesstoken", "authtoken", "clientsecret", "secretkey", "accesskey", "privatekey")
NOT_SECRET_KEYS = ("cache", "sort", "public", "partition", "primary", "foreign", "idempotency", "dedup",
                   "routing", "group", "ssh")
PASSWORD_PARTS = ("password", "passwd")   # any literal value counts, however short
HEADER_FLAGS = ("header", "headers", "h")   # --header "Authorization: Bearer ..." (mcp-remote and others)
FLAG_EQ = re.compile(r"^(--?[A-Za-z][A-Za-z0-9_.-]*)=(.*)$", re.S)
FLAG = re.compile(r"^--?([A-Za-z][A-Za-z0-9_.-]*)$")
HEADER_LINE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.*)$", re.S)
# A value that names a file rather than holding a secret: an absolute, home, or relative path, a
# drive letter, or a short file extension at the end.
PATHLIKE = re.compile(r"^(/|\./|\.\./|~|[A-Za-z]:[\\/])|\.[A-Za-z][A-Za-z0-9]{0,4}$")
SECRET_TAIL = "; use ${VAR} so the secret stays out of the repo"


def _secret_word(name):
    """'password' for a password-like name, True for another secret-looking one, else None."""
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)   # camelCase: githubToken is github_token
    parts = [p for p in re.split(r"[^a-z0-9]+", name.lower()) if p]
    if not parts or parts[-1] not in SECRET_PARTS:
        return None
    if parts[-1] == "key" and len(parts) > 1 and parts[-2] in NOT_SECRET_KEYS:
        return None
    return "password" if parts[-1] in PASSWORD_PARTS else True


def credential(value):
    """True when a literal looks like a real credential: a known key shape, or 12+ characters mixing
    letters and digits with no space that isn't a path or a placeholder."""
    if not value or PLACEHOLDER.search(value):
        return False
    if key_shape(value):
        return True
    return (len(value) >= 12 and bool(re.search("[A-Za-z]", value)) and bool(re.search("[0-9]", value))
            and not re.search(r"\s", value) and not PATHLIKE.search(value))


def _literal_parts(value):
    """The literal text in value: each ${VAR:-default}'s default, and what's left around the
    references (with no references, the value itself)."""
    out = [d for _, d in refs(value) if d]
    rest = REF.sub(" ", value).strip()
    return out + ([rest] if rest else [])


def literal_secret(name, value):
    """True when value, given under a secret-looking flag or parameter name, holds a real
    credential outside its ${VAR} references: any literal for a password, else credential()."""
    kind = _secret_word(name)
    if not kind:
        return False
    for part in _literal_parts(value):
        if kind == "password" and not PLACEHOLDER.search(part) and not PATHLIKE.search(part):
            return True
        if credential(part):
            return True
    return False


def hidden_default(value):
    """True when a ${VAR:-default} in value defaults to something that looks like a credential."""
    return any(credential(d) for _, d in refs(value) if d)


def _no_constant(name):
    raise ValueError("%s isn't JSON" % name)   # NaN and Infinity: Python takes them, JSON and TOML don't


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
            data = json.loads(text, parse_constant=_no_constant)
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
            self._secret_map(k, v, k == "headers")
        for k in ("command", "cwd"):
            if k in mine and isinstance(data.get(k), str):
                self._secret_shape(k, data[k])
        if "args" in mine and _strings(data.get("args")):
            self._secret_args("", data["args"])
        if "url" in mine and isinstance(data.get("url"), str):
            self._secret_url("url", data["url"])
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
                        self._secrets("native.%s." % tool, block)
                        if tool == "gemini" and block.get("trust") is True:
                            self._warn("native.gemini: trust: true skips gemini's confirmation for every tool this "
                                       "server has; rendered as written")

    def _secrets(self, prefix, block):
        """The secrets rule for a native block, as for the neutral keys."""
        for k in ("env", "headers", "http_headers"):
            if isinstance(block.get(k), dict):
                self._secret_map(prefix + k, block[k], k != "env")
        for k in ("command", "cwd"):
            if isinstance(block.get(k), str):
                self._secret_shape(prefix + k, block[k])
        if _strings(block.get("args")):
            self._secret_args(prefix, block["args"])
        for k in ("url", "httpUrl", "serverUrl"):
            if isinstance(block.get(k), str):
                self._secret_url(prefix + k, block[k])

    def _secret_map(self, where, v, header):
        """env or header values: a secret-looking name needs a ${VAR} reference with no credential
        as its default; any value with a key shape guard knows is an error too."""
        for name, val in v.items():
            if not isinstance(val, str):
                continue
            if secret_name(name, header) and val != "" and not refs(val):
                self._err("%s.%s is a literal%s" % (where, name, SECRET_TAIL))
            elif hidden_default(val):
                self._err("%s.%s has a literal secret as its ${VAR:-default}%s" % (where, name, SECRET_TAIL))
            else:
                self._secret_shape("%s.%s" % (where, name), val)

    def _secret_shape(self, where, value):
        """A key shape guard knows anywhere in value. True when it found one."""
        shape = key_shape(value)
        if shape:
            self._err("%s holds a literal secret (%s)%s" % (where, shape, SECRET_TAIL))
        return bool(shape)

    def _secret_args(self, prefix, args):
        """A literal credential in args: a key shape anywhere, the value of a secret-looking flag
        (--token=..., or --api-key then the value as the next item), or a secret header given
        to a header flag (--header "Authorization: Bearer ...")."""
        for i, a in enumerate(args):
            where = "%sargs[%d]" % (prefix, i)
            if self._secret_shape(where, a):
                continue
            m = FLAG_EQ.match(a)
            prev = FLAG.match(args[i - 1]) if i > 0 and not a.startswith("-") else None
            if m:
                flag, val = m.group(1), m.group(2)
            elif prev:
                flag, val = args[i - 1], a
            else:
                continue
            name = flag.lstrip("-")
            if literal_secret(name, val):
                self._err("%s is a literal %s value%s" % (where, flag, SECRET_TAIL))
            elif name.lower() in HEADER_FLAGS:
                h = HEADER_LINE.match(val)
                if h and secret_name(h.group(1), True) and h.group(2).strip() and not refs(h.group(2)):
                    self._err("%s is a literal %s header%s" % (where, h.group(1), SECRET_TAIL))
                elif h and hidden_default(h.group(2)):
                    self._err("%s has a literal secret as its ${VAR:-default}%s" % (where, SECRET_TAIL))

    def _secret_url(self, where, url):
        """A literal credential in a url: a password (user:pass@host), a secret-looking query or
        fragment parameter, or a key shape anywhere in it."""
        rest, _, fragment = url.partition("#")
        query = rest.partition("?")[2]
        try:
            password = urlsplit(url).password
        except ValueError:   # a broken [host]: no userinfo to read, the rest still gets checked
            password = None
        if password and _literal_parts(password):
            self._err("%s has a password in it (user:pass@host)%s" % (where, SECRET_TAIL))
            return
        if self._secret_shape(where, url):
            return
        # By hand, not parse_qsl: a + in a token is a +, not a space.
        for kind, text in (("query", query), ("fragment", fragment)):
            for pair in text.split("&"):
                name, eq, val = pair.partition("=")
                if eq and literal_secret(unquote(name), unquote(val)):
                    self._err("%s: %s parameter %s is a literal%s" % (where, kind, unquote(name), SECRET_TAIL))
                    return

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
            # Copilot CLI reads it (and reads .mcp.json whatever targets says); Claude has no per-server list
            if "copilot" in on or "copilot" in adapters:
                entry["tools"] = list(srv.tools)
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


# ------------------------------------------------------------------ merging into the tools' files

BEGIN = "# >>> ai-harness mcp (managed by .agents/bin/sync)"
END = "# <<< ai-harness mcp"
PERSONAL = ("personal", "personal-listed")
USER_SCOPE = {"claude": "claude: claude mcp add --scope local", "copilot": "copilot: ~/.copilot/mcp-config.json",
              "cursor": "cursor: ~/.cursor/mcp.json", "codex": "codex: ~/.codex/config.toml",
              "gemini": "gemini: ~/.gemini/settings.json"}
# A [mcp_servers.<name>] table (or a subtable of one); the name bare or quoted.
TABLE = re.compile(r"""^\s*\[\s*mcp_servers\s*\.\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([A-Za-z0-9_-]+))""")
JSON_FILES = (".mcp.json", FILES["cursor"], FILES["gemini"])


def in_repo(root, path):
    """path relative to the repo, or None if it's outside (real paths on both sides)."""
    rp, rr = os.path.realpath(path), os.path.realpath(root)
    return os.path.relpath(rp, rr) if rp.startswith(rr + os.sep) else None


def dump_json(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _table_name(line):
    m = TABLE.match(line)
    if not m:
        return None
    if m.group(1) is not None:
        try:
            return json.loads('"%s"' % m.group(1))
        except ValueError:
            return m.group(1)
    return m.group(2) if m.group(2) is not None else m.group(3)


# A plain key at the start of a line (bare or quoted), then = or a dot.
KEY = re.compile(r"""^\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([A-Za-z0-9_-]+))\s*([.=])""")


def _key(m):
    if m.group(1) is not None:
        try:
            return json.loads('"%s"' % m.group(1))
        except ValueError:
            return m.group(1)
    return m.group(2) if m.group(2) is not None else m.group(3)


def project_names(lines):
    """The MCP servers a config.toml's own lines define: [mcp_servers.<n>] tables (and subtables),
    <n>.x = / <n> = under [mcp_servers], and mcp_servers.<n>.x = at the top level."""
    out, table = set(), None
    for ln in lines:
        s = ln.strip()
        if s.startswith("["):
            n = _table_name(ln)
            if n is not None:
                out.add(n)
            table = re.sub(r"\s+", "", s.split("#", 1)[0])
            continue
        m = KEY.match(ln)
        if not m:
            continue
        if table == "[mcp_servers]":
            out.add(_key(m))
        elif table is None and _key(m) == "mcp_servers" and m.group(4) == ".":
            m2 = KEY.match(ln[m.end():])
            if m2:
                out.add(_key(m2))
    return out


def file_tools(rel, adapters):
    return [t for t in adapters if FILES[t] == rel]


def toml_block(text):
    """(outside lines, {name: table text} inside the block, has block) for a config.toml's text.
    Raises ValueError when the markers are broken."""
    lines = text.split("\n")
    begins = [i for i, ln in enumerate(lines) if ln.rstrip("\r") == BEGIN]
    ends = [i for i, ln in enumerate(lines) if ln.rstrip("\r") == END]
    if not begins and not ends:
        return lines, {}, False
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise ValueError("its ai-harness mcp block markers are broken (one '%s' line, then one '%s' line)"
                         % (BEGIN, END))
    b, e = begins[0], ends[0]
    chunks, name = {}, None
    for ln in lines[b + 1:e]:
        n = _table_name(ln) if ln.startswith("[") else None
        if n is not None and not ln.lstrip().startswith("[[") and n != name:
            name = n
            chunks.setdefault(name, []).append(ln)
        elif name is not None:
            chunks[name].append(ln)
    sep = 1 if b > 0 and not lines[b - 1].strip() else 0   # the blank line sync puts before the block
    return lines[:b - sep] + lines[e + 1:], dict((k, "\n".join(v).rstrip("\n").rstrip()) for k, v in chunks.items()), True


def sync_mcp(root, conf, rows, check, tracked, old_lock, team, library_missing=False):
    """Renders every server into each enabled tool's config, merged beside what's there.
    rows: (name, path, library) from sync, winners only. old_lock: {file: [names sync owns]}.
    Returns dict: wrote, drift, warnings, errors (lists of str), lock ({file: [names]}), files
    (files holding entries sync owns), seen (every server name the files hold)."""
    adapters = [a for a in TOOLS if a in (conf.get("ADAPTERS") or "").split()]
    res = {"wrote": [], "drift": [], "warnings": [], "errors": [], "lock": {}, "files": [], "seen": set()}
    want = {}   # file -> {name: (entry, shown)}
    held = set()   # names of servers with errors: whatever sync wrote for them last stays
    for name, path, lib in rows:
        shown = in_repo(root, path) or path
        srv = Server(name, path, shown)
        errs, warns = srv.messages()
        res["warnings"] += warns
        if errs:
            res["errors"] += [e + "; server not rendered" for e in errs]
            held.add(name)
            continue
        tools = [t for t in adapters if srv.wants(t)]
        if team and lib in PERSONAL:
            if tools:
                res["warnings"].append("%s: your personal server '%s' isn't rendered in team mode (the repo commits "
                                       "these configs); add it to your own instead: %s"
                                       % (shown, name, "; ".join(USER_SCOPE[t] for t in tools)))
            continue
        if srv.targets is not None and "claude" in adapters and "claude" not in tools and "copilot" in tools:
            res["warnings"].append("%s: claude reads .mcp.json too, so it gets this server along with copilot" % shown)
        if srv.targets is not None and "copilot" in adapters and "copilot" not in tools and "claude" in tools:
            res["warnings"].append("%s: copilot reads .mcp.json too, so it gets this server along with claude" % shown)
        missing = {}
        for tool in tools:
            entry, miss, notes = render(srv, tool, adapters)
            for m in miss:
                missing.setdefault(m, []).append(tool)
            for n in notes:
                res["warnings"].append("%s: %s" % (shown, n))
            if entry is not None:
                want.setdefault(FILES[tool], {})[name] = (entry, shown)
        res["warnings"] += tool_messages(srv, missing)
    for rel in sorted(set(want) | set(old_lock)):
        if rel == FILES["codex"]:
            _settle_toml(root, rel, want.get(rel, {}), set(old_lock.get(rel, [])), held, check, tracked, team,
                         library_missing, adapters, res)
        elif rel in JSON_FILES:
            _settle_json(root, rel, want.get(rel, {}), set(old_lock.get(rel, [])), held, check, tracked, team,
                         library_missing, adapters, res)
    for rel in JSON_FILES + (FILES["codex"],):   # names an agent's mcp: may point at, wherever they are
        if not os.path.isfile(os.path.join(root, rel)):
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                text = fh.read()
            if rel == FILES["codex"]:
                outside, chunks, _ = toml_block(text)
                res["seen"].update(project_names(outside) | set(chunks))
            else:
                res["seen"].update(((json.loads(text) or {}).get("mcpServers") or {}).keys())
        except (OSError, ValueError, AttributeError, UnicodeDecodeError):
            pass
    return res


def _skip(root, rel, ws, old, tracked, team, adapters, res):
    """True (with a warning) when sync mustn't touch the file: a link, or tracked in local mode.
    What sync recorded for it stays recorded."""
    full = os.path.join(root, rel)
    d = os.path.dirname(rel)
    why = None
    if os.path.islink(full):
        why = "%s is a link; sync leaves it alone" % rel
    elif d and os.path.realpath(os.path.join(root, d)) != os.path.join(os.path.realpath(root), d):
        why = "%s is a link (or inside one); sync leaves %s alone" % (d, rel)
    elif not team and tracked(rel):
        why = ("the project tracks %s; local mode leaves it alone, so these servers aren't rendered for %s"
               % (rel, ", ".join(file_tools(rel, adapters)) or "it"))
    if why is None:
        return False
    if ws or old:
        res["warnings"].append(why)
    if old:
        res["lock"][rel] = sorted(old)
    return True


def _settle(root, rel, old_text, new_text, check, res):
    """Writes (or, for --check, reports) the new text; None removes the file."""
    if new_text == old_text:
        return
    full = os.path.join(root, rel)
    if check:
        res["drift"].append(rel + (" (remove)" if new_text is None else ""))
        return
    if new_text is None:
        os.remove(full)
        res["wrote"].append("removed " + rel)
        return
    d = os.path.dirname(full)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = full + ".ai-harness.tmp"   # the file is the project's too: never leave it half-written
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.replace(tmp, full)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    res["wrote"].append("wrote " + rel)


def _settle_json(root, rel, ws, old, held, check, tracked, team, library_missing, adapters, res):
    if _skip(root, rel, ws, old, tracked, team, adapters, res):
        return
    full = os.path.join(root, rel)
    text = obj = None
    if os.path.exists(full):
        try:
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
            obj = json.loads(text) if text.strip() else {}
            if not isinstance(obj, dict) or not isinstance(obj.get("mcpServers", {}), dict):
                raise ValueError("expected an object, with mcpServers an object")
        except (OSError, ValueError, UnicodeDecodeError) as e:
            res["errors"].append("%s is not valid JSON (%s); fix or remove it, then re-run sync" % (rel, e))
            if old:
                res["lock"][rel] = sorted(old)
            return
    servers = (obj or {}).get("mcpServers") or {}
    new, owned = {}, set()
    for n, v in servers.items():
        if n in old and n not in ws:
            if n in held:
                owned.add(n)
            elif library_missing:
                res["warnings"].append("%s: server '%s' stays as it is: a library LIBRARIES lists isn't here, and it "
                                       "may come from there" % (rel, n))
                owned.add(n)
            else:
                continue   # its server is gone
        new[n] = v
    for n in sorted(ws):
        entry, shown = ws[n]
        if n in servers and n not in old and servers[n] != entry:   # one just like sync's is sync's
            res["warnings"].append("%s: server '%s' was added by hand; it stays, and the one from %s isn't rendered "
                                   "for %s" % (rel, n, shown, ", ".join(file_tools(rel, adapters))))
            continue
        new[n] = entry
        owned.add(n)
    if obj is None:
        new_obj = {"mcpServers": new} if new else None
    else:
        new_obj = dict(obj)
        if new:
            new_obj["mcpServers"] = new
        elif servers:
            new_obj.pop("mcpServers", None)   # it held only sync's entries
    if owned:
        res["lock"][rel] = sorted(owned)
        res["files"].append(rel)
    if new_obj == obj:
        return
    _settle(root, rel, text, dump_json(new_obj) if new_obj else None, check, res)


def _settle_toml(root, rel, ws, old, held, check, tracked, team, library_missing, adapters, res):
    """.codex/config.toml: sync's tables live in a marked block at the end; the rest is the project's."""
    if _skip(root, rel, ws, old, tracked, team, adapters, res):
        return
    full = os.path.join(root, rel)
    text = None
    if os.path.exists(full):
        try:
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            res["errors"].append("%s can't be read (%s); fix or remove it, then re-run sync" % (rel, e))
            if old:
                res["lock"][rel] = sorted(old)
            return
    try:
        outside, chunks, has_block = toml_block(text or "")
    except ValueError as e:
        res["errors"].append("%s: %s; fix them, then re-run sync" % (rel, e))
        if old:
            res["lock"][rel] = sorted(old)
        return
    if not has_block and not ws:
        return   # nothing of sync's there, and nothing to add: untouched
    project = project_names(outside)
    final = {}
    for n, chunk in chunks.items():   # the block is sync's: what's in it is sync's, recorded or not
        if n in ws or n in project:
            continue
        if n in held:
            final[n] = chunk
        elif library_missing:
            res["warnings"].append("%s: server '%s' stays as it is: a library LIBRARIES lists isn't here, and it "
                                   "may come from there" % (rel, n))
            final[n] = chunk
    for n in sorted(ws):
        entry, shown = ws[n]
        if n in project:
            res["warnings"].append("%s: server '%s' was added by hand; it stays, and the one from %s isn't rendered "
                                   "for codex" % (rel, n, shown))
            continue
        final[n] = entry.rstrip("\n")
    while outside and not outside[-1].strip():
        outside.pop()
    body = "\n".join(outside)
    if final:
        block = "\n\n".join([BEGIN + "\n" + final[n] if i == 0 else final[n] for i, n in enumerate(sorted(final))])
        new_text = (body + "\n\n" if body else "") + block + "\n" + END + "\n"
        res["lock"][rel] = sorted(final)
        res["files"].append(rel)
    else:
        new_text = body + "\n" if body else None
    _settle(root, rel, text, new_text, check, res)


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

#!/usr/bin/env python3
"""ai-harness agent renderer. Harness-owned: replaced on upgrade.

Turns neutral agents (agents/<name>.md in a library or an active workflow pack) into each tool's
own agent file. harness.py agents runs it for sync. The frontmatter is a small YAML subset, read
with the standard library: key: value scalars (plain or quoted, > or | folded), lists as [a, b] or
"- a" lines, and native:, a block per tool of lines in that tool's own syntax, copied verbatim.

  agents_render.py parse <file>      print the parsed agent as JSON (a debugging aid)
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
    print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

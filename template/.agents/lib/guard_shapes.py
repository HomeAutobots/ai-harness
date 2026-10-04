#!/usr/bin/env python3
"""ai-harness: guard's secret rules, for Python callers. Harness-owned: replaced on upgrade.

The "secret" lines of .agents/core/guard.patterns and the project's own .agents/guard.patterns,
each ERE turned into a Python regex, plus guard's filter for references and placeholders (bin/guard's
plausible()), mirrored. mcp_render.py refuses literal secrets in MCP server files with it. Import it
with this directory on sys.path; it has no command line.
"""
import os
import re

# Guard's filter for references and placeholders (bin/guard, plausible()), mirrored.
PLACEHOLDER = re.compile(r"\$\{|\$\(|\{\{|%\(|<[A-Za-z0-9_ .-]*>|example|sample|changeme|change_me|replace_?me|"
                         r"placeholder|your[_-]|dummy|fake|redacted|xxxx|\*\*\*\*|\.\.\.|[sp]k_test_", re.I)
POSIX_CLASSES = (("[:space:]", r"\s"), ("[:blank:]", r" \t"), ("[:alpha:]", "A-Za-z"), ("[:digit:]", "0-9"),
                 ("[:alnum:]", "A-Za-z0-9"), ("[:upper:]", "A-Z"), ("[:lower:]", "a-z"),
                 ("[:xdigit:]", "0-9A-Fa-f"))
EDGE = "\"'`()[]{}<>,;:=. \t/\\@#?&+!|*$%^~"   # what guard's plausible() trims: not [A-Za-z0-9_-]
_SHAPES = []


def shapes():
    """[(regex, what)] for guard's secret rules: .agents/core/guard.patterns, then the project's own
    .agents/guard.patterns, from the .agents/ this file is in. Read once, each ERE turned into a
    Python regex; one that doesn't translate is skipped (guard still has it)."""
    if _SHAPES:
        return _SHAPES[0]
    out = []
    agents = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (os.path.join(agents, "core", "guard.patterns"), os.path.join(agents, "guard.patterns")):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            f = line.split("\t")
            if line.startswith("#") or len(f) < 2 or f[0] != "secret" or not f[1]:
                continue
            ere = f[1]
            for posix, py in POSIX_CLASSES:
                ere = ere.replace(posix, py)
            if "[:" in ere:
                continue
            try:
                rx = re.compile(ere)
            except re.error:
                continue
            what = (f[2] if len(f) > 2 else "").split(":")[0].strip() or "secret"
            out.append((rx, what))
    _SHAPES.append(out)
    return out


def plausible(m):
    """guard's plausible() for one match: not a reference or placeholder, and an assignment's value
    mixes letters and digits."""
    m = m.strip(EDGE)
    if not m:
        return False
    op = re.search(r"(:=|=>|=|:)\s*[\"']?", m)
    raw = m[op.end():] if op else m
    if PLACEHOLDER.search(raw):
        return False
    if op:
        v = raw.lstrip(EDGE)
        return bool(re.search("[0-9]", v) and re.search("[A-Za-z]", v))
    return True


def key_shape(value):
    """What guard calls the first plausible secret in value (an AWS key, a GitHub token, ...), or
    None. Like guard, a match that's a placeholder doesn't hide one that starts inside it."""
    for rx, what in shapes():
        pos = n = 0
        while pos <= len(value) and n < 50:
            n += 1
            m = rx.search(value, pos)
            if not m:
                break
            if m.end() > m.start() and plausible(m.group(0)):
                return what
            pos = m.start() + 1
    return None

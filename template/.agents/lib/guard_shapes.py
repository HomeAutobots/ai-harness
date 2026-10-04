#!/usr/bin/env python3
"""ai-harness: guard's secret rules, for Python callers. Harness-owned: replaced on upgrade.

The "secret" lines of .agents/core/guard.patterns and the project's own .agents/guard.patterns,
each ERE turned into a Python regex, plus guard's filter for references and placeholders (bin/guard's
plausible()), mirrored. mcp_render.py refuses literal secrets in MCP server files with it, and the
debug pack's debug run masks them in the output it captures. Import it with this directory on
sys.path; it has no command line.
"""
import bisect
import os
import re

# Guard's filter for references and placeholders (bin/guard, plausible()), mirrored.
PLACEHOLDER = re.compile(r"\$\{|\$\(|\{\{|%\(|<[A-Za-z0-9_ .-]*>|example|sample|changeme|change_me|replace_?me|"
                         r"placeholder|your[_-]|dummy|fake|redacted|xxxx|\*\*\*\*|\.\.\.|[sp]k_test_", re.I)
POSIX_CLASSES = (("[:space:]", r"\s"), ("[:blank:]", r" \t"), ("[:alpha:]", "A-Za-z"), ("[:digit:]", "0-9"),
                 ("[:alnum:]", "A-Za-z0-9"), ("[:upper:]", "A-Z"), ("[:lower:]", "a-z"),
                 ("[:xdigit:]", "0-9A-Fa-f"))
EDGE = "\"'`()[]{}<>,;:=. \t/\\@#?&+!|*$%^~"   # what guard's plausible() trims: not [A-Za-z0-9_-]
WORD = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")   # what guard's redact() keeps
_SHAPES = []
SKIPPED = []   # what each secret rule shapes() couldn't turn into a Python regex is called (guard still has them)
_LINES = []   # each rule's looser twins (_prefilters), for finding the lines worth a look


def shapes():
    """[(regex, what)] for guard's secret rules: .agents/core/guard.patterns, then the project's own
    .agents/guard.patterns, from the .agents/ this file is in. Read once, each ERE turned into a
    Python regex; one that doesn't translate is skipped (guard still has it) and named in SKIPPED."""
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
            what = (f[2] if len(f) > 2 else "").split(":")[0].strip() or "secret"
            for posix, py in POSIX_CLASSES:
                ere = ere.replace(posix, py)
            try:
                if "[:" in ere:
                    raise re.error("a POSIX class with no Python twin")
                rx = re.compile(ere)
            except re.error:
                SKIPPED.append(what)
                continue
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


def _value(text, start, end):
    """The part of the match text[start:end] that guard's redact() replaces: what follows an
    assignment operator when there is one, with characters outside [A-Za-z0-9_-] trimmed from both
    ends, so a key name, quotes, and the line ending stay. (start, end)."""
    while start < end and text[start] not in WORD:
        start += 1
    while end > start and text[end - 1] not in WORD:
        end -= 1
    op = re.search(r"(:=|=>|=|:)\s*[\"']?", text[start:end])
    if op:
        start += op.end()
        while start < end and text[start] not in WORD:
            start += 1
    return start, end


def mask(text, mark="[masked]"):
    """(text, n): text with every plausible secret replaced by mark, the way guard's redact() does it
    when it prints a line ('export API_TOKEN=[masked]'), and how many it replaced. Meant for one line
    at a time: a secret split across a line break isn't one, and neither is a match that takes in a
    mark an earlier rule left. Like guard's find(), a rule gives up after 50 matches that weren't
    plausible; replacements have no limit."""
    n = 0
    for rx, _ in shapes():
        out, last, pos, misses = [], 0, 0, 0
        while pos <= len(text) and misses < 50:
            m = rx.search(text, pos)
            if not m:
                break
            if m.end() > m.start() and mark not in m.group(0) and plausible(m.group(0)):
                s, e = _value(text, m.start(), m.end())
                if e > s:
                    out += [text[last:s], mark]
                    last = pos = e
                    n += 1
                    continue
            misses += 1
            pos = m.start() + 1
        text = "".join(out) + text[last:]
    return text, n


def _line_local(p):
    """p with the newline taken out of what can match it: \\s, and a class that's only \\s, become
    [^\\S\\n], and a negated class gets \\n. A line has no newline, so in one line it matches just
    what p does; across many, it can't run from one line into the next. Without it, ^[\\s]* under
    re.M backtracks over every blank line from every line start, which is quadratic."""
    out, i = [], 0
    while i < len(p):
        if p.startswith("[\\s]", i) or p.startswith("\\s", i):
            out.append("[^\\S\\n]")
            i += 4 if p[i] == "[" else 2
        elif p[i] == "\\":
            out.append(p[i:i + 2])
            i += 2
        elif p[i] == "[":
            j = i + 1 + (p[i + 1:i + 2] == "^")
            j += p[j:j + 1] == "]"   # a ] first is part of the class
            while j < len(p) and p[j] != "]":
                j += 2 if p[j] == "\\" else 1
            cls = p[i:j + 1]
            out.append(cls[:-1] + "\\n]" if cls.startswith("[^") else cls)
            i = j + 1
        else:
            out.append(p[i])
            i += 1
    return "".join(out)


def _prefilters():
    """[(twin, lowered twin or None)] for each rule: looser regexes with re.M that find every line the
    rule matches in, quickly. A leading (^|[...]) boundary is dropped (Python's re scans slowly
    without a literal start) and newlines are taken out of what can match (_line_local). A (?i) rule
    also gets a lowered twin, for lowered ASCII text, as guard lowers it, unless it has an escape
    lowering would change (\\S, \\W). A twin that doesn't compile is the rule itself."""
    if not _LINES:
        out = []
        for rx, _ in shapes():
            base = re.sub(r"^\(\^\|\[\^?\]?[^]]*\]\)", "", rx.pattern)
            try:
                twin = re.compile(_line_local(base), re.M)
            except re.error:
                twin = re.compile(rx.pattern, re.M)
            low = None
            if base.startswith("(?i)") and not re.search(r"\\[A-Z]", base):
                try:
                    low = re.compile(_line_local(base[4:].lower()), re.M)
                except re.error:
                    pass
            out.append((twin, low))
        _LINES.append(out)
    return _LINES[0]


def mask_lines(text, mark="[masked]"):
    """mask() on each line of text (split on newlines only), as (text, n). Fast on text with few
    secrets: each rule's twin (_prefilters) searches the whole text once, and only the lines a match
    touches are looked at one by one. The lowered twins run only on ASCII text, where lower() maps
    each character to one character and re.I agrees with it."""
    hit, nl = set(), None
    low = text.lower() if text.isascii() else None
    for twin, lowered in _prefilters():
        rx, t = (lowered, low) if lowered is not None and low is not None else (twin, text)
        for m in rx.finditer(t):
            if nl is None:
                nl = [0] + [x.end() for x in re.finditer("\n", text)]
            hit.update(range(bisect.bisect_right(nl, m.start()) - 1, bisect.bisect_right(nl, m.end())))
    if not hit:
        return text, 0
    lines, n = text.split("\n"), 0
    for i in sorted(hit):
        if i < len(lines):
            lines[i], k = mask(lines[i], mark)
            n += k
    return "\n".join(lines), n

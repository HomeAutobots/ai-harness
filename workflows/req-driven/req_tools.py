#!/usr/bin/env python3
"""ai-harness workflow pack: req-driven. Harness-owned: replaced on upgrade.

Deterministic traceability checks, independent of any particular standard or tool. Settings
come from .agents/harness.conf (environment variables override):

  REQ_SOURCE          requirements export: .csv, .json, .md/.txt, or a directory of them
  REQ_ID_PATTERN      what an ID looks like, as a Python regex (default REQ-[0-9]+)
  REQ_SCOPE           globs where a change must reference a requirement (default src/**)
  REQ_TESTS           globs that identify test files
  REQ_REQUIRE_TESTED  1 = in the full tier, every requirement needs at least one test

  req_tools.py diff [--edit] <root> [files...]   check what the current change adds
  req_tools.py trace <root>                      repo-wide: unknown IDs, untested requirements,
                                                 and a trace report in .agents/cache/req-trace.md
Exit: 0 clean, 1 findings, 3 not configured.
"""
import csv
import fnmatch
import glob
import json
import os
import re
import subprocess
import sys

HARNESS_PATHS = (".agents/", ".claude/", ".cursor/", ".github/hooks/", ".codex/", ".gemini/")
HARNESS_FILES = {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}
DEFAULT_TESTS = "test/** tests/** **/test/** **/tests/** **/*_test.* **/*Test.* **/*Tests.* **/test_*.py **/*.test.* **/*.spec.*"
TEST_DECL = re.compile(
    r"^\s*(TEST|TEST_F|TEST_P|TYPED_TEST|TEST_CASE|SCENARIO)\s*\(|^\s*(async\s+)?def\s+test_|"
    r"^\s*(it|test)\s*\(\s*[\"'`]|@Test\b|#\[test\]|^\s*fn\s+test_|^\s*func\s+Test[A-Z_]")
TEXT_EXT = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp", ".py", ".js", ".ts",
            ".jsx", ".tsx", ".java", ".kt", ".rs", ".go", ".cs", ".rb", ".swift", ".m", ".mm", ".sh",
            ".cmake", ".txt", ".md", ".rst", ".yaml", ".yml", ".json", ".toml", ".proto", ".xml"}


# ------------------------------------------------------------------ config

def load_conf(root):
    conf = {"REQ_SOURCE": "", "REQ_ID_PATTERN": "REQ-[0-9]+", "REQ_SCOPE": "src/**",
            "REQ_TESTS": DEFAULT_TESTS, "REQ_REQUIRE_TESTED": "0"}
    try:
        with open(os.path.join(root, ".agents", "harness.conf"), encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'^\s*(REQ_[A-Z_]+)=(?:"([^"]*)"|\'([^\']*)\'|([^\s#]*))', line)
                if m:
                    conf[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
    except OSError:
        pass
    for k in list(conf):
        if os.environ.get(k):
            conf[k] = os.environ[k]
    return conf


class Ids:
    """Finds requirement IDs in text. If the pattern uses '-' but not '_', REQ_12 counts as
    REQ-12, so IDs survive in identifiers such as C++ test names."""

    def __init__(self, pattern):
        self.loose = "-" in pattern and "_" not in pattern
        self.rx = re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % pattern)

    def find(self, text):
        out = [m.group(0) for m in self.rx.finditer(text)]
        if self.loose and "_" in text:
            out += [m.group(0) for m in self.rx.finditer(text.replace("_", "-"))]
        seen, uniq = set(), []
        for i in out:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
        return uniq


def glob_match(path, pat):
    if fnmatch.fnmatchcase(path, pat):
        return True
    if pat.startswith("**/") and glob_match(path, pat[3:]):
        return True
    if "/**/" in pat and glob_match(path, pat.replace("/**/", "/", 1)):
        return True
    return False


def matches_any(path, globs):
    return any(glob_match(path, g) for g in globs.split())


def harness_path(path):
    return path.startswith(HARNESS_PATHS) or path in HARNESS_FILES


# ------------------------------------------------------------------ requirements source

def load_known(root, source, ids):
    """Returns {id: (relpath, line)} for every requirement in the source."""
    path = os.path.join(root, source)
    files = []
    if os.path.isdir(path):
        for base, dirs, names in os.walk(path):
            dirs.sort()
            files += [os.path.join(base, n) for n in sorted(names)]
    elif os.path.isfile(path):
        files = [path]
    known = {}
    for f in files:
        rel = os.path.relpath(f, root)
        ext = os.path.splitext(f)[1].lower()
        try:
            with open(f, encoding="utf-8", errors="replace", newline="") as fh:
                text = fh.read()
        except OSError:
            continue
        if ext == ".csv":
            rows = list(csv.reader(text.splitlines()))
            col = None
            if rows:
                heads = [re.sub(r"[^a-z]", "", h.lower()) for h in rows[0]]
                for want in ("id", "reqid", "requirementid", "key", "requirement"):
                    if want in heads:
                        col = heads.index(want)
                        break
            for n, row in enumerate(rows, 1):
                cells = [row[col]] if col is not None and col < len(row) else row
                for c in cells:
                    for i in ids.find(c):
                        known.setdefault(i, (rel, n))
        elif ext == ".json":
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            found = set()

            def walk(x):
                if isinstance(x, dict):
                    for k in ("id", "ID", "key", "requirement_id", "reqId"):
                        if isinstance(x.get(k), str):
                            found.update(ids.find(x[k]))
                    for v in x.values():
                        walk(v)
                elif isinstance(x, list):
                    for v in x:
                        walk(v)
                elif isinstance(x, str) and ids.rx.fullmatch(x):
                    found.add(x)
            walk(data)
            for n, line in enumerate(text.splitlines(), 1):
                for i in ids.find(line):
                    if i in found:
                        known.setdefault(i, (rel, n))
        else:
            for n, line in enumerate(text.splitlines(), 1):
                for i in ids.find(line):
                    known.setdefault(i, (rel, n))
    return known


# ------------------------------------------------------------------ git helpers

def git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True,
                          errors="replace").stdout


def added_lines(root, files):
    """{path: {line_no: text}} for lines added in the working tree vs HEAD, untracked included."""
    out = {}
    has_head = subprocess.run(["git", "-C", root, "rev-parse", "-q", "--verify", "HEAD"],
                              capture_output=True).returncode == 0
    if has_head:
        path, ln = None, 0
        for line in git(root, "diff", "-U0", "--no-color", "--no-ext-diff", "HEAD", "--", *files).splitlines():
            if line.startswith("+++ "):
                path = line[6:] if line.startswith("+++ b/") else None
            elif line.startswith("@@"):
                m = re.match(r"@@ -\S+ \+(\d+)", line)
                ln = int(m.group(1)) if m else 0
            elif line.startswith("+") and path:
                out.setdefault(path, {})[ln] = line[1:]
                ln += 1
    for f in git(root, "ls-files", "-o", "--exclude-standard", "--", *files).splitlines():
        try:
            with open(os.path.join(root, f), encoding="utf-8") as fh:
                out[f] = {n: t for n, t in enumerate(fh.read().splitlines(), 1)}
        except (OSError, UnicodeDecodeError):
            pass
    return out


def file_lines(root, path):
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


COMMENT = re.compile(r"^\s*(//|#|/\*|\*|--|@|\[)")


def test_window(full, n, decl):
    """The declaration line, the comment or annotation lines directly above it (up to 3, stopping
    at code or another test), and for annotation-style declarations the line below."""
    window = [decl]
    i = n - 2  # 0-based index of the line above
    while i >= 0 and len(window) < 4:
        line = full[i]
        if TEST_DECL.search(line) or not (COMMENT.match(line) or not line.strip()):
            break
        window.append(line)
        i -= 1
    if re.search(r"@Test\b|#\[test\]", decl) and n < len(full):
        window.append(full[n])
    return window


def plan_refs(root, ids):
    """IDs named by plan tasks currently in progress: the ledger can carry the trace."""
    refs = []
    for tj in sorted(glob.glob(os.path.join(root, ".agents", "plans", "*", "tasks.json"))):
        rel = os.path.relpath(tj, root)
        for n, line in enumerate(file_lines(root, rel), 1):
            if '"status":"doing"' in line:
                refs += [(rel, n, i) for i in ids.find(line)]
    return refs


# ------------------------------------------------------------------ commands

def cmd_diff(root, files, edit_only):
    conf = load_conf(root)
    if not conf["REQ_SOURCE"]:
        print("infra: REQ_SOURCE is not set in .agents/harness.conf (req-driven workflow). Run harness-tailor.")
        return 3
    ids = Ids(conf["REQ_ID_PATTERN"])
    known = load_known(root, conf["REQ_SOURCE"], ids)
    if not known:
        print("infra: no requirement IDs matching %s found in %s" % (conf["REQ_ID_PATTERN"], conf["REQ_SOURCE"]))
        return 3
    source = conf["REQ_SOURCE"].rstrip("/")
    findings, referenced = [], False
    added = added_lines(root, files)
    for path in sorted(added):
        if harness_path(path) or path == source or path.startswith(source + "/"):
            continue
        lines = added[path]
        is_test = matches_any(path, conf["REQ_TESTS"])
        full = file_lines(root, path) if is_test else []
        for n in sorted(lines):
            for i in ids.find(lines[n]):
                referenced = True
                if i not in known:
                    findings.append("%s:%d: error: [req-unknown] %s is not in %s\n"
                                    "  fix: use an ID that exists there; if the requirement is missing, stop and ask"
                                    % (path, n, i, source))
            if is_test and TEST_DECL.search(lines[n]):
                if not any(ids.find(w) for w in test_window(full or [], n, lines[n])):
                    findings.append("%s:%d: error: [req-untagged-test] new test names no requirement\n"
                                    "  fix: put the ID it verifies in the test name or a comment right above it"
                                    % (path, n))
    if not edit_only:
        for rel, n, i in plan_refs(root, ids):
            referenced = True
            if i not in known:
                findings.append("%s:%d: error: [req-unknown] %s is not in %s" % (rel, n, i, source))
        scoped = [p for p in sorted(added) if not harness_path(p) and matches_any(p, conf["REQ_SCOPE"])
                  and not matches_any(p, conf["REQ_TESTS"])]
        if scoped and not referenced:
            findings.append("%s:1: error: [req-untraced] this change touches %s but references no requirement\n"
                            "  fix: tag the code or its tests with the requirement it implements, or name it in the "
                            "plan task in progress; if none applies, stop and ask" % (scoped[0], conf["REQ_SCOPE"]))
    for f in findings:
        print(f)
    return 1 if findings else 0


def cmd_trace(root):
    conf = load_conf(root)
    if not conf["REQ_SOURCE"]:
        print("infra: REQ_SOURCE is not set in .agents/harness.conf (req-driven workflow). Run harness-tailor.")
        return 3
    ids = Ids(conf["REQ_ID_PATTERN"])
    known = load_known(root, conf["REQ_SOURCE"], ids)
    source = conf["REQ_SOURCE"].rstrip("/")
    code, tests, findings = {}, {}, []
    for path in git(root, "ls-files", "-co", "--exclude-standard").splitlines():
        if harness_path(path) or path == source or path.startswith(source + "/"):
            continue
        if os.path.splitext(path)[1].lower() not in TEXT_EXT and os.path.basename(path) != "CMakeLists.txt":
            continue
        is_test = matches_any(path, conf["REQ_TESTS"])
        for n, line in enumerate(file_lines(root, path), 1):
            for i in ids.find(line):
                (tests if is_test else code).setdefault(i, []).append("%s:%d" % (path, n))
                if i not in known:
                    findings.append(("%s:%d: error: [req-unknown] %s is not in %s" % (path, n, i, source),
                                     "%s|req-unknown|%s" % (path, i)))
    if conf["REQ_REQUIRE_TESTED"] == "1":
        for i in sorted(known):
            if i not in tests:
                rel, n = known[i]
                findings.append(("%s:%d: error: [req-untested] %s has no test that references it" % (rel, n, i),
                                 "%s|req-untested|%s" % (rel, i)))
    # Report: one row per requirement, newest reader-friendly shape.
    cache = os.path.join(root, ".agents", "cache")
    os.makedirs(cache, exist_ok=True)
    with open(os.path.join(cache, "req-trace.md"), "w", encoding="utf-8") as fh:
        fh.write("# Requirements trace\n\nSource: `%s`. %d requirements, %d referenced in code, %d with tests.\n\n"
                 % (source, len(known), sum(1 for i in known if i in code), sum(1 for i in known if i in tests)))
        fh.write("| Requirement | Code | Tests |\n| --- | --- | --- |\n")
        for i in sorted(known):
            fh.write("| %s | %s | %s |\n" % (i, ", ".join(code.get(i, [])[:5]) or "none",
                                            ", ".join(tests.get(i, [])[:5]) or "none"))
    baseline = os.path.join(root, ".agents", "baselines", "req-trace.txt")
    if os.environ.get("AGENTS_UPDATE_BASELINE") == "1":
        os.makedirs(os.path.dirname(baseline), exist_ok=True)
        with open(baseline, "w", encoding="utf-8") as fh:
            fh.writelines(k + "\n" for _, k in sorted(set(findings), key=lambda x: x[1]))
        print("baseline req-trace: %d findings recorded" % len(findings))
        return 0
    base = set()
    if os.path.exists(baseline):
        base = set(open(baseline, encoding="utf-8").read().split("\n"))
    new = [msg for msg, key in findings if key not in base]
    for m in new:
        print(m)
    return 1 if new else 0


def main(argv):
    if len(argv) >= 3 and argv[1] == "diff":
        edit = argv[2] == "--edit"
        rest = argv[3:] if edit else argv[2:]
        if not rest:
            return 2
        root = os.path.abspath(rest[0])
        return cmd_diff(root, rest[1:], edit)
    if len(argv) == 3 and argv[1] == "trace":
        return cmd_trace(os.path.abspath(argv[2]))
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""python stack helpers. Harness-owned: replaced on upgrade. Standard library only, Python 3.8+.

  py_tools.py affected <root> <files...>    the test files a change can reach, one per line, or ALL / NONE

Affected tests come from a static import scan of the Python files git sees under <root>:
  - a changed test file (test_*.py, *_test.py) runs itself;
  - a changed module runs every test file that imports it, directly or through other modules in
    the repo, and every test under a conftest.py that does;
  - a package's __init__.py passes a change on only through the names it re-exports from the
    changed module (so `from pkg import a` doesn't run because pkg/__init__.py also imports b);
    a changed __init__.py reaches everything that imports anything in its package;
  - a changed conftest.py runs the tests under its directory;
  - docs, images, stubs, compiled files, and the harness's own files run nothing, unless they sit
    in a directory below the top that holds tests (golden files, fixtures);
  - anything else runs ALL: packaging and test config, requirements and constraints files, other
    non-Python files, a deleted module, a module no test reaches (it may be loaded dynamically or
    run in a subprocess), custom python_files patterns or doctests in the pytest config, no test
    files at all, or git unable to list files.
A file this interpreter can't parse (newer syntax) is scanned for import lines instead.
"""
import ast
import json
import os
import re
import subprocess
import sys

IGNORED = (".md", ".markdown", ".rst", ".txt", ".png", ".svg", ".jpg", ".jpeg", ".gif", ".ico",
           ".pdf", ".pyi", ".pyc", ".pyo")
IGNORED_NAMES = (".gitignore", ".gitattributes", ".editorconfig", ".pre-commit-config.yaml",
                 "LICENSE", "LICENSE.txt", "COPYING", "py.typed")
HARNESS = (".agents/", ".claude/", ".cursor/", ".github/", ".codex/", ".gemini/", "AGENTS.md",
           "CLAUDE.md", "GEMINI.md")
TEST_CONFIGS = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
CACHE_VERSION = 2
IMPORT_LINE = re.compile(r"^\s*import\s+([\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)")
FROM_LINE = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+\(?\s*([\w\s,*]*)")


def is_test(path):
    b = os.path.basename(path)
    return b.endswith(".py") and (b.startswith("test_") or b.endswith("_test.py"))


def is_init(path):
    return os.path.basename(path) == "__init__.py"


def git_lines(root, *args):
    out = subprocess.run(["git", "-C", root] + list(args), stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, check=True).stdout
    return [ln for ln in out.decode("utf-8", "surrogateescape").splitlines() if ln]


def py_files(root):
    """Python files git sees under root (tracked, or untracked and not ignored), minus the harness's."""
    files = git_lines(root, "ls-files", "-co", "--exclude-standard", "--", "*.py")
    return sorted(set(f for f in files if not f.startswith(HARNESS) and os.path.isfile(os.path.join(root, f))))


def candidates(path):
    """Every name the file may be imported as: each suffix of its dotted path (src layouts, tests
    that put their own directory on sys.path, and so on). Over-matching only runs more tests."""
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return set(".".join(parts[i:]) for i in range(len(parts)))


def resolve_from(path, level, module):
    """The absolute dotted name `from <dots><module>` means in this file (its path from the root)."""
    if not level:
        return module
    base = path[:-3].split("/")[:-1]   # the file's package: for a/b/c.py and a/b/__init__.py, a.b
    if level > 1:
        base = base[:-(level - 1)] if level - 1 <= len(base) else []
    return ".".join(base + ([module] if module else []))


def const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def scan_ast(path, tree):
    """{"p": names imported whole, "f": [module, name, name it's bound to] for each from-import}"""
    plain, frm = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                plain.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = resolve_from(path, node.level or 0, node.module or "")
            for a in node.names:
                frm.add((mod, a.name, a.asname or a.name))
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if fname in ("import_module", "__import__") and node.args:
                s = const_str(node.args[0])
                if s and not s.startswith("."):
                    plain.add(s)
        elif isinstance(node, ast.Assign):
            # pytest_plugins = ["pkg.fixtures"]: loaded like an import
            if any(getattr(t, "id", "") == "pytest_plugins" for t in node.targets):
                v = node.value
                for e in (v.elts if isinstance(v, (ast.List, ast.Tuple)) else [v]):
                    s = const_str(e)
                    if s:
                        plain.add(s)
    return {"p": sorted(plain), "f": sorted(list(t) for t in frm)}


def scan_text(path, text):
    plain, frm = set(), set()
    for line in text.splitlines():
        m = IMPORT_LINE.match(line)
        if m:
            plain.update(n.split()[0] for n in m.group(1).split(",") if n.strip())
            continue
        m = FROM_LINE.match(line)
        if m:
            mod = resolve_from(path, len(m.group(1)), m.group(2))
            for n in m.group(3).split(","):
                w = n.split()
                if w:
                    frm.add((mod, w[0], w[2] if len(w) == 3 and w[1] == "as" else w[0]))
    return {"p": sorted(plain), "f": sorted(list(t) for t in frm)}


def imports_of(root, files, cache_path):
    """What each file imports, cached by size and mtime."""
    cache = {}
    try:
        with open(cache_path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") == CACHE_VERSION:
            cache = data.get("files", {})
    except (OSError, ValueError):
        cache = {}
    out, fresh = {}, {}
    for f in files:
        p = os.path.join(root, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        stamp = [st.st_size, st.st_mtime_ns]
        hit = cache.get(f)
        if hit and hit[0] == stamp:
            found = hit[1]
        else:
            try:
                with open(p, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            try:
                found = scan_ast(f, ast.parse(raw, f))
            except (SyntaxError, ValueError):
                found = scan_text(f, raw.decode("utf-8", "replace"))
        out[f] = found
        fresh[f] = [stamp, found]
    if fresh != cache:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp = cache_path + ".tmp.%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": CACHE_VERSION, "files": fresh}, fh)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    return out


class Graph:
    """Who imports what, by module name:
      whole[m]  files that depend on module m entirely: `import m`, or `from p import x` naming
                submodule m = p.x, or a string import of m
      named[m]  (file, name) for each `from m import name`
      under[m]  files importing something inside package m (a submodule's import runs m/__init__.py)
    """

    def __init__(self, imports):
        self.imports = imports
        self.whole, self.named, self.under = {}, {}, {}
        for g, d in imports.items():
            for n in d["p"]:
                self.whole.setdefault(n, set()).add(g)
                self._under(n, g)
            for mod, name, _ in d["f"]:
                if not mod:   # `from . import x` at the top of the tree
                    self.whole.setdefault(name, set()).add(g)
                    continue
                self.named.setdefault(mod, []).append((g, name))
                self._under(mod, g, include_self=True)
                if name != "*":
                    self.whole.setdefault(mod + "." + name, set()).add(g)

    def _under(self, name, g, include_self=False):
        parts = name.split(".")
        for i in range(1, len(parts) + (1 if include_self else 0)):
            self.under.setdefault(".".join(parts[:i]), set()).add(g)

    def exports(self, init, cands):
        """The names package file init passes on from a module known by cands, or None for all."""
        names = set()
        d = self.imports.get(init, {"p": [], "f": []})
        for n in d["p"]:
            if n in cands:
                names.add(n.split(".")[-1])
        for mod, name, bound in d["f"]:
            if mod in cands:
                if name == "*":
                    return None
                names.add(bound)
            elif (mod + "." + name if mod else name) in cands:
                names.add(bound)
        return names

    def reach(self, start):
        """Every file a change to start reaches: {file: names it passes on (None: all)}."""
        seen = {start: None}
        todo = [start]
        while todo:
            f = todo.pop()
            via = seen[f]
            cands = candidates(f)
            hits = {}

            def hit(g, new):
                if g in hits:
                    new = None if hits[g] is None or new is None else (hits[g] | new)
                hits[g] = new

            for c in cands:
                for g in self.whole.get(c, ()):
                    hit(g, self.exports(g, cands) if is_init(g) else None)
                for g, name in self.named.get(c, ()):
                    if not is_init(f) or via is None or name == "*" or name in via:
                        hit(g, self.exports(g, cands) if is_init(g) else None)
                if f == start and is_init(f):
                    for g in self.under.get(c, ()):  # importing anything in the package runs it
                        hit(g, None)
            for g, new in hits.items():
                if g == f:
                    continue
                if g in seen:
                    old = seen[g]
                    if old is None or (new is not None and new <= old):
                        continue
                    new = None if new is None else (old | new)
                seen[g] = new
                todo.append(g)
        del seen[start]
        return seen


def unsure_config(root):
    """pytest finds tests by other file names, or runs doctests in modules: the scan can't follow."""
    for c in TEST_CONFIGS:
        try:
            with open(os.path.join(root, c), encoding="utf-8", errors="replace") as fh:
                if re.search(r"^\s*python_files\s*=|--doctest", fh.read(), re.M):
                    return True
        except OSError:
            pass
    return False


def ignorable(path, test_dirs):
    """Docs, images, and the like: unless they sit with the tests (golden files, fixtures)."""
    base = os.path.basename(path)
    if path.startswith(HARNESS) or "__pycache__/" in path:
        return True
    if re.match(r"(requirements|constraints).*\.(txt|in)$", base) or re.search(r"(^|/)(requirements|constraints)/", path):
        return False
    if not (path.lower().endswith(IGNORED) or base in IGNORED_NAMES):
        return False
    return not any(under_dir(path, d) for d in test_dirs)


def under_dir(path, d):
    return d == "" or path.startswith(d + "/")


def cmd_affected(root, changed):
    try:
        files = py_files(root)
        deleted = git_lines(root, "diff", "--relative", "--name-only", "--no-renames", "--diff-filter=D", "HEAD", "--")
    except (OSError, subprocess.CalledProcessError):
        return print("ALL") or 0
    tests = [f for f in files if is_test(f)]
    if not tests or unsure_config(root):
        return print("ALL") or 0
    if any(d.endswith(".py") and not d.startswith(HARNESS) for d in deleted):
        return print("ALL") or 0
    test_dirs = set(os.path.dirname(t) for t in tests) - {""}   # tests at the top don't make every doc a fixture
    selected, modules, conftests = set(), [], []
    for f in changed:
        base = os.path.basename(f)
        if ignorable(f, test_dirs):
            continue
        if not f.endswith(".py"):
            return print("ALL") or 0  # packaging and test config, data files, unknown: be conservative
        if is_test(f):
            selected.add(f)
        elif base == "conftest.py":
            conftests.append(f)
        else:
            modules.append(f)
    if modules or conftests:
        graph = Graph(imports_of(root, files, os.path.join(root, ".agents", "cache", "py-imports.json")))
        for m in modules:
            reached = graph.reach(m)
            hit = [t for t in reached if is_test(t)]
            confs = [c for c in reached if os.path.basename(c) == "conftest.py"]
            if not hit and not confs:
                return print("ALL") or 0  # nothing reaches it statically: dynamic loading, subprocess, ...
            selected.update(hit)
            conftests.extend(confs)
        for c in conftests:
            d = os.path.dirname(c)
            selected.update(t for t in tests if under_dir(t, d))
            selected.update(t for t in graph.reach(c) if is_test(t))  # a test that imports conftest, rarely
    selected = set(t for t in selected if os.path.isfile(os.path.join(root, t)))
    if not selected:
        return print("NONE") or 0
    if set(tests) <= selected:
        return print("ALL") or 0
    for t in sorted(selected):
        print(t)
    return 0


def main(argv):
    if len(argv) >= 3 and argv[1] == "affected":
        return cmd_affected(argv[2], argv[3:])
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

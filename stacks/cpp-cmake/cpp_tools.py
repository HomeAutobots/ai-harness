#!/usr/bin/env python3
"""cpp-cmake stack helpers. Harness-owned: replaced on upgrade.

  cpp_tools.py syntax <root> <build> <files...>     -fsyntax-only using each file's compile command
  cpp_tools.py line-filter <root> <files...>         clang-tidy --line-filter JSON for changed lines
  cpp_tools.py affected <root> <build> <files...>    ctest -R regex for affected tests, or ALL / NONE
"""
import glob
import json
import os
import re
import shlex
import subprocess
import sys

SOURCES = (".c", ".cc", ".cpp", ".cxx", ".c++")
HEADERS = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp", ".tpp")
IGNORED = (".md", ".markdown", ".rst", ".txt", ".png", ".svg")
HARNESS = (".agents/", ".claude/", ".cursor/", ".github/", ".codex/", ".gemini/", "AGENTS.md", "CLAUDE.md", "GEMINI.md")
DROP_WITH_ARG = {"-o", "-MF", "-MT", "-MQ"}
DROP = {"-c", "-MD", "-MMD", "-MP"}


def load_db(build):
    with open(os.path.join(build, "compile_commands.json"), encoding="utf-8") as fh:
        return json.load(fh)


def entry_args(e):
    return list(e["arguments"]) if e.get("arguments") else shlex.split(e["command"])


def cmd_syntax(root, build, files):
    index = {}
    for e in load_db(build):
        index[os.path.normpath(os.path.join(e["directory"], e["file"]))] = e
    rc = 0
    for f in files:
        e = index.get(os.path.normpath(os.path.join(root, f)))
        if not e:
            continue  # not in the build yet; the turn tier's build covers it
        args, out, skip = entry_args(e), [], False
        for a in args:
            if skip:
                skip = False
                continue
            if a in DROP_WITH_ARG:
                skip = True
                continue
            if a in DROP or a.startswith("-o") and len(a) > 2 and not a.startswith("-openmp"):
                continue
            out.append(a)
        out.append("-fsyntax-only")
        try:
            p = subprocess.run(out, cwd=e["directory"], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as ex:
            print("infra: syntax check for %s failed to run: %s" % (f, ex))
            rc = max(rc, 3)
            continue
        sys.stdout.write(p.stdout + p.stderr)  # warnings matter even when it compiles
        if p.returncode != 0:
            rc = 1
    return rc


def git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True).stdout


def cmd_line_filter(root, files):
    entries = []
    for f in files:
        path = os.path.join(root, f)
        tracked = subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch", "--", f],
                                 capture_output=True).returncode == 0
        if not tracked:
            entries.append({"name": path})
            continue
        ranges = []
        for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", git(root, "diff", "-U0", "HEAD", "--", f), re.M):
            start, count = int(m.group(1)), int(m.group(2) or 1)
            if count > 0:
                ranges.append([start, start + count - 1])
        if ranges:
            entries.append({"name": path, "lines": ranges})
    if not entries:
        entries.append({"name": "ai-harness-no-changed-lines"})  # filters everything out
    print(json.dumps(entries))
    return 0


def load_codemodel(build):
    reply = os.path.join(build, ".cmake", "api", "v1", "reply")
    idx = sorted(glob.glob(os.path.join(reply, "index-*.json")))
    if not idx:
        return None
    with open(idx[-1], encoding="utf-8") as fh:
        index = json.load(fh)
    cm = next((o for o in index.get("objects", []) if o.get("kind") == "codemodel"), None)
    if not cm:
        return None
    with open(os.path.join(reply, cm["jsonFile"]), encoding="utf-8") as fh:
        codemodel = json.load(fh)
    src_root = codemodel["paths"]["source"]
    targets = {}
    for t in codemodel["configurations"][0]["targets"]:
        with open(os.path.join(reply, t["jsonFile"]), encoding="utf-8") as fh:
            targets[t["id"]] = json.load(fh)
    return src_root, targets


def cmd_affected(root, build, files):
    if not files:
        return print("NONE") or 0
    cm = load_codemodel(build)
    if not cm:
        return print("ALL") or 0
    src_root, targets = cm
    by_source = {}
    for tid, t in targets.items():
        for s in t.get("sources", []):
            p = s["path"] if os.path.isabs(s["path"]) else os.path.join(src_root, s["path"])
            by_source.setdefault(os.path.normpath(p), set()).add(tid)
    hit = set()
    for f in files:
        p = os.path.normpath(os.path.join(root, f))
        low = f.lower()
        if f.startswith(HARNESS) or (low.endswith(IGNORED) and os.path.basename(f) != "CMakeLists.txt"):
            continue
        if low.endswith(SOURCES) and p in by_source:
            hit |= by_source[p]
            continue
        return print("ALL") or 0  # headers, CMake, data files, unknown: be conservative
    # expand to everything that depends on a hit target
    rdeps = {}
    for tid, t in targets.items():
        for d in t.get("dependencies", []):
            rdeps.setdefault(d["id"], set()).add(tid)
    todo = list(hit)
    while todo:
        for dep in rdeps.get(todo.pop(), ()):
            if dep not in hit:
                hit.add(dep)
                todo.append(dep)
    artifacts = {}
    for tid, t in targets.items():
        for a in t.get("artifacts", []):
            ap = a["path"] if os.path.isabs(a["path"]) else os.path.join(build, a["path"])
            artifacts[os.path.normpath(ap)] = tid
    try:
        out = subprocess.run(["ctest", "--show-only=json-v1"], cwd=build, capture_output=True, text=True).stdout
        tests = json.loads(out).get("tests", [])
    except (OSError, ValueError):
        return print("ALL") or 0
    chosen = []
    for t in tests:
        cmd = t.get("command") or []
        exe = os.path.normpath(cmd[0]) if cmd else ""
        tid = artifacts.get(exe)
        if tid is None or tid in hit:  # unknown executables (scripts, wrappers) always run
            chosen.append(t["name"])
    if not chosen:
        print("NONE")
    elif len(chosen) == len(tests):
        print("ALL")
    else:
        print("^(" + "|".join(re.escape(n) for n in chosen) + ")$")
    return 0


def main(argv):
    if len(argv) >= 4 and argv[1] == "syntax":
        return cmd_syntax(argv[2], argv[3], argv[4:])
    if len(argv) >= 3 and argv[1] == "line-filter":
        return cmd_line_filter(argv[2], argv[3:])
    if len(argv) >= 4 and argv[1] == "affected":
        return cmd_affected(argv[2], argv[3], argv[4:])
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

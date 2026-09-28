#!/usr/bin/env python3
"""ai-harness workflow pack: feature-driven. Harness-owned: replaced on upgrade.

Classic Feature-Driven Development with local artifacts that are never committed. They live in
FDD_DIR: model.md, features.md, designs/<ID>.md, and approvals (written only by `fdd approve`).
Settings come from .agents/harness.conf (environment variables override):

  FDD_DIR           where the artifacts live, repo-relative or absolute (default .agents/fdd)
  FDD_ID_PATTERN    what a private feature ID looks like, as a Python regex (default F-[0-9]+)
  FDD_SCOPE         globs where a change needs a feature in progress and its design (default src/**)
  FDD_NAME_PATTERN  what a feature name looks like, as a Python regex; empty skips the check
  FDD_ASK           gates that need a human approval: any of list, design, inspect

  fdd_tools.py check <edit|turn|full> <root> [files...]   the verify checks (full also writes the report)
  fdd_tools.py msg <root> <message-file>                  commit messages carry no private feature IDs
  fdd_tools.py approve <root> list | design <ID> | inspect <ID>   record a human approval
  fdd_tools.py status <root> [ID]                         approvals and milestones, no dates
Exit: 0 clean, 1 findings, 2 usage.
"""
import datetime
import fnmatch
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

DEFAULT_NAME = r"^\S+ .+ (by|for|of|to|from|in|on|with|into) .+$"
DEFAULT_TICKET = r"[A-Z][A-Z0-9]+-[0-9]+"
# FDD's milestone weights, cumulative: walkthrough + design, design inspection, code, code inspection + promote.
MILESTONES = ((41, "designed"), (44, "design approved"), (89, "built"), (100, "inspected"))
APPROVE = ".agents/workflows/feature-driven/bin/fdd approve"


# ------------------------------------------------------------------ config

def read_conf(path, prefix):
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'^\s*(%s[A-Z_]+)=(?:"([^"]*)"|\'([^\']*)\'|([^\s#]*))' % prefix, line)
                if m:
                    out[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
    except OSError:
        pass
    return out


def load_conf(root):
    conf = {"FDD_DIR": ".agents/fdd", "FDD_ID_PATTERN": "F-[0-9]+", "FDD_SCOPE": "src/**",
            "FDD_NAME_PATTERN": DEFAULT_NAME, "FDD_ASK": "list design inspect"}
    conf.update(read_conf(os.path.join(root, ".agents", "harness.conf"), "FDD_"))
    for k in list(conf):
        if k in os.environ:
            conf[k] = os.environ[k]
    conf["ticket"] = read_conf(os.path.join(root, ".agents", "git.conf"), "GIT_").get("GIT_TICKET") or DEFAULT_TICKET
    return conf


def fdd_dir(root, conf):
    d = conf["FDD_DIR"]
    return os.path.normpath(d if os.path.isabs(d) else os.path.join(root, d))


def shown(root, path):
    """Repo-relative inside the repo, absolute outside it."""
    rel = os.path.relpath(path, root)
    return path if rel.startswith("..") else rel


def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def finding(path, line, kind, msg, fix):
    return "%s:%d: error: [%s] %s\n  fix: %s" % (path, line, kind, msg, fix)


def emit(found):
    for f in found:
        print(f)
    return 1 if found else 0


class Ids:
    """Finds feature IDs in text. If the pattern uses '-' but not '_', F_12 counts as F-12."""

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


# ------------------------------------------------------------------ git

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


# ------------------------------------------------------------------ feature list

class Feature:
    def __init__(self, fid, name, ticket, fset, line):
        self.id, self.name, self.ticket, self.set, self.line = fid, name, ticket, fset, line


ITEM = re.compile(r"^\s*[-*]\s+(\S+)\s*(.*?)\s*$")
TICKET = re.compile(r"\s*\[([^\]]*)\]$")


def parse_features(root, conf, path):
    """({id: Feature} in list order, fdd-format findings). ## starts a subject area, ### a feature set."""
    feats, found = {}, []
    rel = shown(root, path)
    name_rx = re.compile(conf["FDD_NAME_PATTERN"]) if conf["FDD_NAME_PATTERN"] else None
    fset = None
    for n, line in enumerate(read_lines(path), 1):
        if line.startswith("### "):
            fset = line[4:].strip()
            continue
        if line.startswith("## "):
            fset = None
            continue
        m = ITEM.match(line)
        if not m:
            continue
        fid, rest = m.group(1), m.group(2)
        if not re.fullmatch(conf["FDD_ID_PATTERN"], fid):
            found.append(finding(rel, n, "fdd-format", "'%s' isn't a feature ID (%s)" % (fid, conf["FDD_ID_PATTERN"]),
                                 "start each feature with its ID, e.g. '- F-12 Calculate the total of a sale'"))
            continue
        ticket = ""
        t = TICKET.search(rest)
        if t:
            ticket, rest = t.group(1).strip(), rest[:t.start()].rstrip()
            if not re.fullmatch(conf["ticket"], ticket):
                found.append(finding(rel, n, "fdd-format", "[%s] isn't a ticket key (%s)" % (ticket, conf["ticket"]),
                                     "use the real ticket key, or leave the brackets off"))
                ticket = ""
        if fid in feats:
            found.append(finding(rel, n, "fdd-format", "%s is already used on line %d" % (fid, feats[fid].line),
                                 "give each feature its own ID"))
            continue
        if fset is None:
            found.append(finding(rel, n, "fdd-format", "%s isn't in a feature set" % fid,
                                 "put it under a '### FS-<n> <feature set>' heading"))
        if name_rx and not name_rx.search(rest):
            found.append(finding(rel, n, "fdd-format", "'%s' doesn't read like an FDD feature name" % rest,
                                 "<action> the <result> <by|for|of|to> a(n) <object>, "
                                 "e.g. 'Calculate the total of a sale'"))
        feats[fid] = Feature(fid, rest, ticket, fset, n)
    return feats, found


# ------------------------------------------------------------------ commands (filled in by later tasks)

def cmd_check(tier, root, files):
    conf = load_conf(root)
    d = fdd_dir(root, conf)
    fpath = os.path.join(d, "features.md")
    out = []
    if not os.path.isfile(fpath):
        return emit(out)
    feats, fmt = parse_features(root, conf, fpath)
    edited = {os.path.normpath(os.path.join(root, f)) for f in files}
    if tier == "full" or (tier == "edit" and fpath in edited):
        out += fmt
    return emit(out)


def cmd_msg(root, path):
    conf = load_conf(root)
    if not os.path.isfile(os.path.join(fdd_dir(root, conf), "features.md")):
        return 0
    return 0


def cmd_status(root, only):
    conf = load_conf(root)
    fpath = os.path.join(fdd_dir(root, conf), "features.md")
    if not os.path.isfile(fpath):
        print("list: none yet (%s)" % shown(root, fpath))
        return 0
    return 0


def cmd_approve(root, args):
    print("usage: fdd approve list | design <ID> | inspect <ID>", file=sys.stderr)
    return 2


def main(argv):
    a = argv[1:]
    if len(a) >= 3 and a[0] == "check" and a[1] in ("edit", "turn", "full"):
        return cmd_check(a[1], os.path.abspath(a[2]), a[3:])
    if len(a) == 3 and a[0] == "msg":
        return cmd_msg(os.path.abspath(a[1]), a[2])
    if len(a) >= 2 and a[0] == "approve":
        return cmd_approve(os.path.abspath(a[1]), a[2:])
    if len(a) in (2, 3) and a[0] == "status":
        return cmd_status(os.path.abspath(a[1]), a[2] if len(a) == 3 else None)
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

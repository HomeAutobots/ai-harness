#!/usr/bin/env python3
"""ai-harness workflow pack: feature-driven. Harness-owned: replaced on upgrade.

Classic Feature-Driven Development with local artifacts that are never committed. They live in
FDD_DIR: model.md, features.md, designs/<ID>.md, and approvals (written only by `fdd approve`).
`fdd approve` also records each line it writes in the git dir (ai-harness/fdd-approvals); a line in
approvals without that record doesn't count and is a finding. It refuses in a shell an agent tool
started (CLAUDECODE, GEMINI_CLI, CURSOR_AGENT), unless install.sh --simulated-human turned on the
simulated human for this clone and the shell has its token (AGENTS_SIMULATED_HUMAN); approvals made
then are marked simulated and count only while it's on. The record, the refusal, and the simulated
human come from the harness's .agents/lib/approvals.py (record key fdd, from human-gates), shared
with every pack that has human gates. Settings come from .agents/harness.conf (environment
variables override):

  FDD_DIR           where the artifacts live, repo-relative or absolute (default .agents/fdd)
  FDD_ID_PATTERN    what a private feature ID looks like, as a Python regex (default F-[0-9]+)
  FDD_SCOPE         globs where a change needs a feature in progress and its design (default src/**)
  FDD_NAME_PATTERN  what a feature name looks like, as a Python regex; empty skips the check
  FDD_ASK           gates that need a human approval: any of list, design, inspect

  fdd_tools.py check <edit|turn|full> <root> [files...]   the verify checks (full also writes the report);
                                                          with AGENTS_SINCE (verify --since), turn and
                                                          full also judge what was committed since then
  fdd_tools.py msg <root> <message-file>                  commit messages carry no private feature IDs
  fdd_tools.py approve <root> list | design <ID> | inspect <ID>   record a human approval
  fdd_tools.py status <root> [ID]                         approvals and milestones, no dates
  fdd_tools.py adopt <root>                               install.sh: record the approvals already in
                                                          FDD_DIR while the git dir has none for it
  fdd_tools.py simulated-human <root> [on]                install.sh: say whether the simulated human
                                                          is on; 'on' (--simulated-human) turns it on
Exit: 0 clean, 1 findings, 2 usage or an approval fdd approve didn't record, 3 tooling problem (e.g.
a pattern that isn't a valid regex).
"""
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
PACK = os.path.dirname(os.path.abspath(__file__))


def gate_key():
    """The pack's record key, from its human-gates file (the one install.sh reads; the first line
    that's a key, not "on"), so the two can't drift; fdd when the file can't be read or names none."""
    try:
        with open(os.path.join(PACK, "human-gates"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.fullmatch(r"\s*([a-z0-9][a-z0-9-]*)\s*", line)
                if m and m.group(1) != "on":
                    return m.group(1)
    except OSError:
        pass
    return "fdd"


KEY = gate_key()   # the record in the git dir, ai-harness/<KEY>-approvals
ap = None          # the harness's .agents/lib/approvals.py, loaded by use_lib() once the project is known


def use_lib(root):
    """The shared approvals library, from the project this runs for: .agents/lib/ is harness-owned
    and in every install, wherever this pack's library is."""
    global ap
    if ap is None:
        sys.dont_write_bytecode = True   # no .agents/lib/__pycache__ left in the project
        sys.path.insert(0, os.path.join(root, ".agents", "lib"))
        try:
            import approvals
        except ImportError:
            raise ConfError("this project's harness has no .agents/lib/approvals.py; re-run install.sh")
        ap = approvals
    return ap


def approve_cmd(root):
    """How a person runs fdd approve (.agents/commands/fdd when sync wrote it)."""
    return ap.human_cmd(root, PACK, "fdd") + " approve"


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
    conf["FDD_DIR"] = conf["FDD_DIR"] or ".agents/fdd"  # empty means the default, as in checks/state.sh
    conf["ticket"] = read_conf(os.path.join(root, ".agents", "git.conf"), "GIT_").get("GIT_TICKET") or DEFAULT_TICKET
    for key, name in (("FDD_ID_PATTERN", "FDD_ID_PATTERN"), ("FDD_NAME_PATTERN", "FDD_NAME_PATTERN"),
                      ("ticket", "GIT_TICKET")):
        try:
            re.compile(conf[key])
        except re.error as e:
            raise ConfError("%s isn't a valid Python regex: %s" % (name, e))
    return conf


class ConfError(Exception):
    pass


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


def emit(found, blocked=()):
    """Print findings; exit 2 (a policy block) when any is a forged approval, else 1 or 0."""
    for f in found:
        print(f)
    return 2 if blocked else 1 if found else 0


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


def scope_matches(root, globs):
    """True when a file git tracks, or a new one it doesn't ignore, matches one of the globs."""
    files = git(root, "ls-files", "-z", "--full-name", "--cached", "--others", "--exclude-standard").split("\0")
    return any(f and matches_any(f, globs) for f in files)


# ------------------------------------------------------------------ git

def git(root, *args):
    return subprocess.run(["git", "-C", root, "-c", "core.quotePath=false"] + list(args), capture_output=True,
                          text=True, errors="replace").stdout


def in_repo(root, path):
    return not os.path.relpath(os.path.join(root, path), root).startswith("..")


def since_commits(root):
    """Commits made since AGENTS_SINCE (verify --since; the stop gate passes HEAD and the branch
    tips its turn started from): what HEAD or a local branch has now that none of those had,
    leaving out merges, anything a remote has (a pull of the base), and copies with the same patch
    as a commit they had (a rebase, or an amend of the message only). Empty without any."""
    since = os.environ.get("AGENTS_SINCE", "").split()
    if not since:
        return []
    new = git(root, "rev-list", "--no-merges", "HEAD", "--branches", "--not", *since, "--remotes", "--").split()
    gone = git(root, "rev-list", "--no-merges", *since, "--not", "HEAD", "--branches", "--").split() if new else []
    if gone:
        old = set(patch_ids(root, gone).values())
        ids = patch_ids(root, new)
        new = [c for c in new if ids.get(c) not in old]
    return new


def patch_ids(root, commits):
    """{commit: stable patch id} (git patch-id), for telling a rebased copy from new work."""
    try:
        show = subprocess.run(["git", "-C", root, "show", "--no-color", "--no-ext-diff"] + list(commits),
                              capture_output=True).stdout
        out = subprocess.run(["git", "-C", root, "patch-id", "--stable"], input=show,
                             capture_output=True).stdout.decode(errors="replace")
    except OSError:
        return {}
    return {c: p for p, c in (l.split()[:2] for l in out.splitlines() if len(l.split()) >= 2)}


def parse_patch(text, out):
    """Add the '+' lines of a unified diff (-U0) to out, {path: {line_no: text}}. Lines two patches
    add at the same number are joined, so neither is lost."""
    path, ln, header = None, 0, False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("+++ "):
            p = line[4:]
            p = p[:-1] if p.endswith("\t") else p
            path = p[2:] if p.startswith("b/") else None
        elif line.startswith("@@"):
            header = False
            m = re.match(r"@@ -\S+ \+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and path and not header:
            lines = out.setdefault(path, {})
            lines[ln] = line[1:] if ln not in lines else lines[ln] + "\n" + line[1:]
            ln += 1


def added_lines(root, files, commits=()):
    """{path: {line_no: text}} for lines added in the working tree vs HEAD, untracked included,
    plus every line the given commits added (whole commits, whatever the file list), so committing
    doesn't hide a change. A committed line's number is the one in its commit."""
    out = {}
    if commits:
        parse_patch(git(root, "show", "-U0", "--no-color", "--no-ext-diff", "--format=", *commits), out)
    if files:
        files = [f for f in files if in_repo(root, f)]  # git rejects the whole call for one outside path
        if not files:
            return out
    has_head = subprocess.run(["git", "-C", root, "rev-parse", "-q", "--verify", "HEAD"],
                              capture_output=True).returncode == 0
    if has_head:
        parse_patch(git(root, "diff", "-U0", "--no-color", "--no-ext-diff", "HEAD", "--", *files), out)
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


# ------------------------------------------------------------------ approvals, ledger, milestones

def record_file(root):
    """Where fdd approve records the lines it writes (.agents/lib/approvals.py, key fdd)."""
    return ap.record_file(root, KEY)


def recorded(root):
    """The approvals lines fdd approve recorded, or None outside git."""
    return ap.recorded(root, KEY)


def read_approvals(root, d, switch=None):
    """({(kind, id): value}, [(line no, kind, id)], [(line no, kind, id)]): the latest recorded
    line for each wins. Lines fdd approve didn't record (written by hand or by an agent) don't
    count and come back second; simulated ones don't count while the switch is off and come back
    third. Latest means the line fdd approve recorded last in the git dir (each line's last place
    there: approving the same thing again, even on the same day, moves it), so reordering or
    copying lines in approvals changes nothing; outside git, the last one in approvals. A design
    approved after the feature's inspection reopens it: that inspection stops counting, and
    ("reopened", ID) is set."""
    counted, unrecorded, simulated = ap.classify(root, KEY, os.path.join(d, "approvals"), switch)
    out, at = {}, {}
    for _, parts, p in counted:
        key = (parts[0], parts[1])
        if key not in at or p >= at[key]:
            out[key], at[key] = parts[4], p
    for kind, fid in list(at):
        if kind == "inspect" and ("design", fid) in at and at[("design", fid)] > at[("inspect", fid)]:
            del out[("inspect", fid)]
            out[("reopened", fid)] = "1"
    return out, [(n, p[0], p[1]) for n, p in unrecorded], [(n, p[0], p[1]) for n, p in simulated]


def what(kind, fid):
    return kind if fid == "-" else "%s %s" % (kind, fid)


def unrecorded_findings(root, d, unrecorded, simulated=()):
    path = shown(root, os.path.join(d, "approvals"))
    return [finding(path, n, "fdd-approval-unrecorded",
                    "this %s approval wasn't written by fdd approve, so it doesn't count" % what(kind, fid),
                    "only the human approves: ask them to run %s %s, and delete this line if they didn't write it"
                    % (approve_cmd(root), what(kind, fid)))
            for n, kind, fid in unrecorded] + \
           [finding(path, n, "fdd-approval-simulated",
                    "this %s approval was made by a simulated human (install.sh --simulated-human), and that "
                    "switch is off in this clone, so it doesn't count" % what(kind, fid),
                    "only the human approves: ask them to run %s %s" % (approve_cmd(root), what(kind, fid)))
            for n, kind, fid in simulated]


def switch_findings(root, switch):
    if switch.state != "void":
        return []
    return [finding(shown(root, switch.path), 1, "fdd-simulated-human",
                    "this simulated-human switch %s, so it's off" % switch.why,
                    "only a person turns it on, with install.sh --simulated-human between agent turns; "
                    "ask them, and delete this file if they didn't")]


def sha(*paths):
    h = hashlib.sha256()
    for p in paths:
        try:
            with open(p, "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
        h.update(b"\0")
    return h.hexdigest()


def list_hash(d):
    return sha(os.path.join(d, "model.md"), os.path.join(d, "features.md"))


def design_path(d, fid):
    return os.path.join(d, "designs", fid + ".md")


def approval(approvals, kind, fid, current=None):
    """'current', 'stale', or 'missing'. Inspections are final, so they never go stale."""
    v = approvals.get((kind, fid))
    if v is None:
        return "missing"
    return "current" if kind == "inspect" or v == current else "stale"


def list_state(d, approvals):
    return {"current": "approved", "stale": "changed since it was approved", "missing": "not approved"}[
        approval(approvals, "list", "-", list_hash(d))]


def ledger(root, conf):
    """[(tasks.json path, line, status, commit, feature ID, task ID)] for plan tasks whose description
    starts with an ID."""
    start = re.compile(r"\s*(%s)(?![A-Za-z0-9])" % conf["FDD_ID_PATTERN"])
    rows = []
    for tj in sorted(glob.glob(os.path.join(root, ".agents", "plans", "*", "tasks.json"))):
        rel = os.path.relpath(tj, root)
        for n, line in enumerate(read_lines(tj), 1):
            s = line.strip().rstrip(",")
            if not s.startswith("{"):
                continue
            try:
                task = json.loads(s)
            except ValueError:
                continue
            desc = task.get("desc", "")
            if not isinstance(desc, str):
                continue
            m = start.match(desc)
            if m:
                rows.append((rel, n, task.get("status", ""), task.get("commit", ""), m.group(1), task.get("id", "")))
    return rows


_COMMITS = {}


def commit_found(root, c):
    """True when c is a commit in this repo (git cat-file -e <c>^{commit}): a full or abbreviated
    hex SHA of 7 or more, as the same-turn trace needs, not a ref name, which moves, or other text."""
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", c or ""):
        return False
    if (root, c) not in _COMMITS:
        _COMMITS[(root, c)] = subprocess.run(["git", "-C", root, "cat-file", "-e", c + "^{commit}"],
                                             capture_output=True).returncode == 0
    return _COMMITS[(root, c)]


def milestone(root, d, fid, conf, approvals, rows):
    """(percent, label), cumulative: each milestone counts only once the ones before it are reached.
    Built needs a done task with a commit git has; a recorded commit it can't find stops there."""
    ask = conf["FDD_ASK"].split()
    dp = design_path(d, fid)
    done = [r[3] for r in rows if r[4] == fid and r[2] == "done" and r[3]]
    built = any(commit_found(root, c) for c in done)
    reached = (
        os.path.isfile(dp),
        "design" not in ask or approval(approvals, "design", fid, sha(dp)) == "current",
        built,
        "inspect" not in ask or approval(approvals, "inspect", fid) == "current",
    )
    pct, label = 0, "not started"
    for i, (ok, (weight, name)) in enumerate(zip(reached, MILESTONES)):
        if not ok:
            if i == 2 and done:
                label = "built (commit not found)"
            break
        pct, label = weight, name
    if ("reopened", fid) in approvals:   # its design was approved again after the inspection
        label += " (reopened)"
    return pct, label


def progress_lines(root, d, conf, feats, approvals, rows, only=None):
    sets = {}
    for f in feats.values():
        sets.setdefault(f.set or "(no feature set)", []).append(f)
    out = []
    for fset, fs in sets.items():
        pcts = [milestone(root, d, f.id, conf, approvals, rows) for f in fs]
        if only is None:
            out.append("## %s: %d%%" % (fset, int(round(sum(p for p, _ in pcts) / float(len(pcts))))))
        for f, (p, label) in zip(fs, pcts):
            if only in (None, f.id):
                out.append("- %s %s: %d%% %s%s" % (f.id, f.name, p, label, " [%s]" % f.ticket if f.ticket else ""))
    return out


# ------------------------------------------------------------------ commands (filled in by later tasks)

def cmd_check(tier, root, files):
    conf = load_conf(root)
    d = fdd_dir(root, conf)
    fpath = os.path.join(d, "features.md")
    switch = ap.Switch(root)
    approvals, unrecorded, simulated = read_approvals(root, d, switch)
    ask = conf["FDD_ASK"].split()
    edited = {os.path.normpath(os.path.join(root, f)) for f in files}
    # Forged approvals first, on every tier that looks: the edit tier when approvals itself was edited.
    blocked = unrecorded_findings(root, d, unrecorded, simulated) \
        if tier != "edit" or os.path.join(d, "approvals") in edited else []
    if tier != "edit":
        blocked = switch_findings(root, switch) + blocked
        if switch.state == "on":   # verify shows a pack's note lines even when it passes
            print("note: simulated human is on in this clone (%s): a shell with its token can approve FDD "
                  "gates, and each approval made here is marked simulated" % switch.how)
    out = []
    if not os.path.isfile(fpath):
        if tier != "edit" and ("list", "-") in approvals:
            out.append(finding(shown(root, fpath), 1, "fdd-list-missing", "the feature list was approved but is gone",
                               "restore it; to stop using the workflow, remove feature-driven from WORKFLOWS instead"))
        if tier != "edit":   # a moved or deleted FDD_DIR turns every gate off, while the ledger says otherwise
            gone = "" if os.path.isdir(d) else " (FDD_DIR %s doesn't exist)" % shown(root, d)
            for rel, n, _, _, fid, _ in [r for r in ledger(root, conf) if r[2] == "doing"]:
                out.append(finding(rel, n, "fdd-list-missing", "%s's task is doing, but there's no feature list at "
                                   "%s%s, so no feature gate runs" % (fid, shown(root, fpath), gone),
                                   "put the list back where FDD_DIR says; never move it, or change FDD_* or "
                                   "WORKFLOWS, to get past a gate. No list written yet: write it first. The human "
                                   "stopped using the workflow: set this task done"))
        return emit(blocked + out, blocked)
    feats, fmt = parse_features(root, conf, fpath)
    if tier != "edit":
        out += not_local(root, d, fpath)
    if tier == "full" or (tier == "edit" and fpath in edited):
        out += fmt
    inside = d + os.sep
    commits = since_commits(root) if tier != "edit" else []
    added = added_lines(root, files, commits)
    ids = Ids(conf["FDD_ID_PATTERN"])
    for path in sorted(added):
        if os.path.join(root, path).startswith(inside):
            continue
        for n in sorted(added[path]):
            for i in ids.find(added[path][n]):
                if i in feats:
                    tk = feats[i].ticket
                    out.append(finding(path, n, "fdd-leak", "%s is a private feature ID from your local feature list" % i,
                                       "remove it; use the ticket key %s instead" % tk if tk else
                                       "remove it; shared code, tests, and docs don't mention local feature IDs"))
    if tier == "edit":
        return emit(blocked + out, blocked)
    rows = ledger(root, conf)
    doing = [r for r in rows if r[2] == "doing"]
    for rel, n, _, _, fid, _ in doing:
        if fid not in feats:
            out.append(finding(rel, n, "fdd-unknown", "%s is not in %s" % (fid, shown(root, fpath)),
                               "name a feature from the approved list; a new feature needs a new list approval"))
        elif ("inspect", fid) in approvals:
            out.append(finding(rel, n, "fdd-inspected", "%s is inspected; new work needs a new feature in the list"
                               % fid, "inspections are final: add a feature for this work and ask for a list "
                               "check-in. If it really is more of %s, ask the human to reopen it by approving its "
                               "design again (%s design %s). If this task's work is finished, set it done"
                               % (fid, approve_cmd(root), fid)))
    out += branch_findings(root, conf, feats, rows, doing)
    scoped = [p for p in sorted(added)
              if not os.path.join(root, p).startswith(inside) and matches_any(p, conf["FDD_SCOPE"])]
    # A scope that matches nothing turns every gate below off. Said once there's a change outside
    # FDD_DIR and .agents/ to judge, so a repo with a list and no code yet isn't held up.
    if not scoped:
        other = [p for p in added if not os.path.join(root, p).startswith(inside) and "/.agents/" not in "/" + p]
        if other and not scope_matches(root, conf["FDD_SCOPE"]):
            conf_rel = os.path.join(".agents", "harness.conf")
            n = ([i for i, l in enumerate(read_lines(os.path.join(root, conf_rel)), 1)
                  if re.match(r"\s*FDD_SCOPE=", l)] or [1])[-1]   # the last one is the one in effect
            out.append(finding(conf_rel, n, "fdd-scope-empty",
                               "FDD_SCOPE (%s) matches no file in the repo, so no change needs a feature or "
                               "a design" % (conf["FDD_SCOPE"] or "empty"),
                               "FDD_SCOPE is where the features' code lives (space-separated globs); "
                               "harness-tailor fills it in, otherwise ask the human (tasks ask). Never point it "
                               "at files the features don't change. If that code doesn't exist yet, the first "
                               "file in scope settles this"))
    if scoped:
        if "list" in ask:
            state = approval(approvals, "list", "-", list_hash(d))
            if state != "current":
                out.append(finding(shown(root, fpath), 1, "fdd-list-unapproved", "the feature list is %s"
                                   % ("not approved" if state == "missing" else "changed since it was approved"),
                                   "validate the model and list, then ask the human to run %s list" % approve_cmd(root)))
        first = min(added[scoped[0]] or [1])  # point at the first changed line, not the top of the file
        # A feature is in progress while its task is doing. Commits made since AGENTS_SINCE can also
        # belong to a feature whose task is done with that commit recorded: that traces the files
        # those commits touched, nothing else.
        built, traced = set(), set()
        for r in rows:
            mine = [c for c in commits if len(r[3]) >= 7 and c.startswith(r[3])] if r[2] == "done" else []
            if mine and r[4] in feats:
                built.add(r[4])
                traced |= {p for p in git(root, "show", "--format=", "--name-only", "--no-renames", *mine).splitlines() if p}
        doing_feats = {r[4] for r in doing if r[4] in feats}
        active = sorted(doing_feats | built)
        untraced = [] if doing_feats else [p for p in scoped if p not in traced]
        if untraced:
            first_u = min(added[untraced[0]] or [1])
            since = (" (counting %d commit%s since %s)" % (len(commits), "" if len(commits) == 1 else "s",
                                                           os.environ["AGENTS_SINCE"][:7]) if commits else "")
            out.append(finding(untraced[0], first_u, "fdd-untraced",
                               "this change%s touches %s but no plan task in progress names a feature"
                               % (since, conf["FDD_SCOPE"]),
                               "set the feature's task to doing (tasks set <slug> <T-id> doing), its description "
                               "starting with the feature ID; if no feature fits, stop and ask"))
        for fid in active:
            dp = design_path(d, fid)
            why = None
            if not os.path.isfile(dp):
                why = "it has no design (%s)" % shown(root, dp)
            elif "design" in ask:
                state = approval(approvals, "design", fid, sha(dp))
                if state == "missing":
                    why = "its design isn't approved"
                elif state == "stale":
                    why = "its design changed since it was approved"
            if why:
                out.append(finding(scoped[0], first, "fdd-no-design", "building %s, but %s" % (fid, why),
                                   "write the design, validate it, and ask the human to run %s design %s; "
                                   "no code in %s until then" % (approve_cmd(root), fid, conf["FDD_SCOPE"])))
    if tier == "full":
        write_report(root, d, conf, feats, approvals, rows)
    return emit(blocked + out, blocked)


def plan_branch(root, rel):
    """(branch, line no) from the 'Branch:' line tasks link wrote in the plan of tasks.json rel, or
    ('', 0) when it isn't linked."""
    for n, l in enumerate(read_lines(os.path.join(root, os.path.dirname(rel), "plan.md")), 1):
        if l.startswith("Branch:"):   # the first: the header line tasks link writes after Commits:
            return l[len("Branch:"):].strip(), n
    return "", 0


def base_branch(root, gconf):
    """gitflow's base branch: GIT_BASE, else the remote's default branch, else main, else master."""
    if gconf.get("GIT_BASE"):
        return gconf["GIT_BASE"]
    remote = gconf.get("GIT_REMOTE") or "origin"
    b = git(root, "symbolic-ref", "-q", "--short", "refs/remotes/%s/HEAD" % remote).strip()
    if b.startswith(remote + "/"):
        return b[len(remote) + 1:]
    if subprocess.run(["git", "-C", root, "show-ref", "-q", "--verify", "refs/heads/main"]).returncode != 0 and \
            subprocess.run(["git", "-C", root, "show-ref", "-q", "--verify", "refs/heads/master"]).returncode == 0:
        return "master"
    return "main"


def branch_findings(root, conf, feats, rows, doing):
    """fdd-wrong-branch for a doing task: its plan is linked (tasks link) to another branch than the
    current one; or this branch is linked to another plan whose tasks name only other features
    (the base branch aside); or, when GIT_BRANCH in .agents/git.conf puts the ticket in branch
    names, this branch's ticket isn't the feature's. Nothing on a detached HEAD, with no links,
    or without that template."""
    cur = git(root, "symbolic-ref", "-q", "--short", "HEAD").strip()
    if not cur or not doing:
        return []
    plans = {}   # plan dir -> ((branch, line), {feature IDs its tasks name})
    for r in rows:
        dr = os.path.dirname(r[0])
        if dr not in plans:
            plans[dr] = (plan_branch(root, r[0]), set())
        plans[dr][1].add(r[4])
    gconf = read_conf(os.path.join(root, ".agents", "git.conf"), "GIT_")
    tickets = "{ticket}" in gconf.get("GIT_BRANCH", "")
    bt = re.search(conf["ticket"], cur) if tickets else None
    bt = bt.group(0) if bt else ""
    base, out, seen = None, [], set()
    for rel, n, _, _, fid, tid in doing:
        dr = os.path.dirname(rel)
        slug = os.path.basename(dr)
        paused = ". If this work is paused, set its task back to todo (tasks set %s %s todo)" % (slug, tid)
        (linked, ln), _ = plans[dr]
        if linked and linked != cur:
            if dr not in seen:
                seen.add(dr)
                out.append(finding(os.path.join(dr, "plan.md"), ln, "fdd-wrong-branch",
                                   "%s's plan %s is linked to branch %s, but this is %s" % (fid, slug, linked, cur),
                                   "switch to %s for this work (git switch %s). If the human moved the work here, "
                                   "tasks link %s%s" % (linked, linked, slug, paused)))
            continue
        if fid not in feats:
            continue
        others = sorted(p for p, ((b, _), fs) in plans.items() if p != dr and b == cur and fid not in fs)
        if linked != cur and others:
            base = base or base_branch(root, gconf)
            if cur != base:
                names = sorted(set().union(*(plans[p][1] for p in others)))
                out.append(finding(rel, n, "fdd-wrong-branch", "%s's task is doing on %s, the branch of plan %s (%s)"
                                   % (fid, cur, ", ".join(os.path.basename(p) for p in others), ", ".join(names)),
                                   "one feature per branch: start %s's own (gitflow start, then tasks link %s), "
                                   "or switch to it. If the human wants them to share it, tasks link %s here%s"
                                   % (fid, slug, slug, paused)))
                continue
        ft = feats[fid].ticket
        owner = [f.id for f in feats.values() if bt and f.ticket == bt]
        if bt and bt != ft and (ft or owner):
            out.append(finding(rel, n, "fdd-wrong-branch", "%s's task is doing on %s, whose ticket %s %s"
                               % (fid, cur, bt, "is %s's" % owner[0] if owner else "isn't %s's (%s)" % (fid, ft)),
                               ("switch to %s's branch, or start it: gitflow start %s <summary>" % (fid, ft) if ft
                                else "start a branch for %s; ask the human which ticket it goes under" % fid)
                               + paused))
    return out


def not_local(root, d, fpath):
    """fdd-not-local findings when FDD_DIR is inside the repo and git tracks or doesn't ignore it."""
    rel = os.path.relpath(d, root)
    if rel.startswith(".."):
        return []
    fix = ("add a .gitignore with '*' and '!.gitignore' to %s (or set FDD_DIR to .agents/fdd), "
           "and git rm --cached anything already tracked" % rel)
    out = []
    keep = os.path.normpath(os.path.join(rel, ".gitignore"))
    for f in git(root, "ls-files", "-z", "--", rel).split("\0"):
        if f and os.path.normpath(f) != keep:
            out.append(finding(f, 1, "fdd-not-local", "FDD files must stay local, but git tracks this one", fix))
    frel = os.path.relpath(fpath, root)
    if subprocess.run(["git", "-C", root, "check-ignore", "-q", "--no-index", frel],
                      capture_output=True).returncode == 1:
        out.append(finding(frel, 1, "fdd-not-local", "FDD files must stay local, but git doesn't ignore %s" % rel, fix))
    return out


def write_report(root, d, conf, feats, approvals, rows):
    cache = os.path.join(root, ".agents", "cache")
    os.makedirs(cache, exist_ok=True)
    weights = ", ".join("%s %d%%" % (label, pct) for pct, label in MILESTONES)
    head = ["# Feature progress", "",
            "From `%s`, with FDD's milestone weights: %s."
            % (shown(root, os.path.join(d, "features.md")), weights), ""]
    with open(os.path.join(cache, "fdd-progress.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(head + progress_lines(root, d, conf, feats, approvals, rows)) + "\n")


def cmd_msg(root, path):
    conf = load_conf(root)
    fpath = os.path.join(fdd_dir(root, conf), "features.md")
    if not os.path.isfile(fpath):
        return 0
    feats, _ = parse_features(root, conf, fpath)
    ids = Ids(conf["FDD_ID_PATTERN"])
    out = []
    # No skip for '#' lines: git only strips those on an edited (no -m/-F) commit, and gitflow
    # always commits with -F, so a '#'-prefixed line lands in history like any other.
    for n, line in enumerate(read_lines(path), 1):
        for i in ids.find(line):
            if i in feats:
                tk = feats[i].ticket
                out.append("commit message line %d: %s is a private feature ID from your local feature list; %s"
                           % (n, i, "use the ticket key %s" % tk if tk else "leave it out"))
    return emit(out)


def cmd_status(root, only):
    conf = load_conf(root)
    d = fdd_dir(root, conf)
    fpath = os.path.join(d, "features.md")
    switch = ap.Switch(root)
    if ap.switch_line(root, switch):
        print(ap.switch_line(root, switch))
    if not os.path.isfile(fpath):
        print("list: none yet (%s)" % shown(root, fpath))
        return 0
    feats, _ = parse_features(root, conf, fpath)
    if only and only not in feats:
        print("fdd: %s is not in %s" % (only, shown(root, fpath)), file=sys.stderr)
        return 1
    approvals, unrecorded, simulated = read_approvals(root, d, switch)
    print("list: %s" % list_state(d, approvals))
    for line in progress_lines(root, d, conf, feats, approvals, ledger(root, conf), only):
        print(line)
    for label, rows in (("not written by fdd approve", unrecorded),
                        ("made by a simulated human while the switch is off", simulated)):
        if rows:
            print("not counted, %s: %s" % (label, ", ".join(
                "%s:%d %s" % (shown(root, os.path.join(d, "approvals")), n, what(kind, fid))
                for n, kind, fid in rows)))
    return 0


def cmd_approve(root, args):
    kinds = {"list": 0, "design": 1, "inspect": 1}
    if not args or args[0] not in kinds or len(args) != 1 + kinds[args[0]]:
        print("usage: fdd approve list | design <ID> | inspect <ID>", file=sys.stderr)
        return 2
    switch = ap.Switch(root)
    if ap.refused("fdd", "approving", switch):
        return 2
    conf = load_conf(root)
    d = fdd_dir(root, conf)
    fpath = os.path.join(d, "features.md")
    if not os.path.isfile(fpath):
        print("fdd: no feature list at %s" % shown(root, fpath), file=sys.stderr)
        return 1
    feats, fmt = parse_features(root, conf, fpath)
    kind, fid = args[0], (args[1] if len(args) == 2 else "-")
    if kind == "list":
        if fmt:
            print("\n".join(fmt), file=sys.stderr)
            print("fdd: fix the list before approving it", file=sys.stderr)
            return 1
        value = list_hash(d)
    elif fid not in feats:
        print("fdd: %s is not in %s" % (fid, shown(root, fpath)), file=sys.stderr)
        return 1
    elif kind == "design":
        dp = design_path(d, fid)
        if not os.path.isfile(dp):
            print("fdd: no design at %s" % shown(root, dp), file=sys.stderr)
            return 1
        value = sha(dp)
    else:
        why = not_built(root, conf, fid)
        if why:
            print("fdd: %s isn't built yet, so there's nothing to inspect: %s" % (fid, why), file=sys.stderr)
            return 1
        value = git(root, "rev-parse", "-q", "--verify", "HEAD").strip()
        if not value:
            print("fdd: nothing is committed yet; inspect after the feature's commit", file=sys.stderr)
            return 1
    line, sim = ap.new_line(root, kind, fid, value, switch)
    earlier = adopt(root, d, sim)  # an upgrade's approvals, if install.sh couldn't take them in
    ap.record(root, KEY, [line])   # first, so a line in approvals is never left without its record
    with open(os.path.join(d, "approvals"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print("approved %s%s" % (what(kind, fid), ap.SIMULATED if sim else ""))
    if earlier:
        print("also " + earlier)
    return 0


def not_built(root, conf, fid):
    """Why fdd approve inspect refuses, for the person at the terminal; '' once a plan task for the
    feature is done with a commit git finds (the built milestone)."""
    done = [r for r in ledger(root, conf) if r[4] == fid and r[2] == "done" and r[3]]
    if any(commit_found(root, r[3]) for r in done):
        return ""
    if done:
        def safe(t, n=64):   # ledger text, shown in a person's terminal
            return re.sub(r"[^\w.-]", "?", str(t))[:n]
        rel, _, _, c, _, tid = done[0]
        slug, tid = safe(os.path.basename(os.path.dirname(rel))), safe(tid)
        return ("its done task (%s %s) records commit %s, which isn't a commit in this repo, so fdd status "
                "shows it as 'built (commit not found)'. Have the agent record the real commit (tasks set %s %s "
                "done <sha>), then run this again." % (slug, tid, safe(c, 16), slug, tid))
    return ("no plan task for it is done with its commit recorded (.agents/bin/tasks list shows the plans). "
            "Run this again once the code is committed and the agent has set the task done with that commit.")


def adopt(root, d, simulated=False):
    """The first time a person runs fdd approve or install.sh in this clone: approvals written
    before fdd kept a record (an upgrade), or copied in with the directory, are recorded as they
    are, or as simulated while the simulated human is on (ap.adopt). Returns a sentence listing
    them for the human, or ''."""
    lines = ap.adopt(root, KEY, os.path.join(d, "approvals"), simulated)
    if not lines:
        return ""
    return ("recorded the %d FDD approvals already in %s as %s: %s. Delete any line you didn't approve."
            % (len(lines), shown(root, os.path.join(d, "approvals")),
               "simulated (the simulated human is on)" if simulated else "yours",
               ", ".join(what(*l.split("\t")[:2]) for l in lines)))


def cmd_adopt(root):
    """install.sh: adopt() earlier approvals, never in an agent's shell unless it has the
    simulated human's token. With no approvals file yet there's nothing to adopt, so the clone is
    marked adopted: a line an agent writes before your first fdd approve can't ride along with it."""
    conf = load_conf(root)
    d = fdd_dir(root, conf)
    if not os.path.isfile(os.path.join(d, "approvals")):
        ap.adopt(root, KEY, os.path.join(d, "approvals"))   # no lines: only marks the clone adopted
        return 0
    switch = ap.Switch(root)
    shell = ap.blocked_shell(switch)
    if shell:
        path = record_file(root)
        n = len(read_approvals(root, d, switch)[1])
        if path is not None and ap.ADOPTED not in read_lines(path) and n:
            print("the %d FDD approvals in %s aren't recorded yet, so they don't count, and this shell was started "
                  "by %s; run install.sh again from your own terminal to keep them"
                  % (n, shown(root, os.path.join(d, "approvals")), shell[1]))
        return 0
    msg = adopt(root, d, switch.state == "on")
    if msg:
        print(msg)
    return 0


# Kept as a thin wrapper over the shared switch: tests, scripts, and the CHANGELOG name this subcommand.
def cmd_simulated_human(root, turn_on):
    """The shared switch (ap.cmd_simulated_human) for this pack's record alone; install.sh turns it
    on for every pack with human gates at once."""
    return ap.cmd_simulated_human(root, turn_on, [KEY])


def main(argv):
    try:
        return dispatch(argv[1:])
    except ConfError as e:
        print("infra: %s" % e)
    except re.error as e:
        print("infra: a FDD_* or GIT_TICKET pattern isn't a valid Python regex: %s" % e)
    except Exception as e:  # a crash is a tooling problem, not findings
        print("infra: fdd_tools failed: %s" % e)
    return 3


def dispatch(a):
    if len(a) == 3 and a[0] == "msg":   # the commit-msg check needs no approvals library
        return cmd_msg(os.path.abspath(a[1]), a[2])
    if len(a) >= 3 and a[0] == "check" and a[1] in ("edit", "turn", "full"):
        root, run = a[2], lambda r: cmd_check(a[1], r, a[3:])
    elif len(a) >= 2 and a[0] == "approve":
        root, run = a[1], lambda r: cmd_approve(r, a[2:])
    elif len(a) in (2, 3) and a[0] == "status":
        root, run = a[1], lambda r: cmd_status(r, a[2] if len(a) == 3 else None)
    elif len(a) == 2 and a[0] == "adopt":
        root, run = a[1], cmd_adopt
    elif len(a) in (2, 3) and a[0] == "simulated-human" and a[2:] in ([], ["on"]):
        root, run = a[1], lambda r: cmd_simulated_human(r, a[2:] == ["on"])
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    root = os.path.abspath(root)
    use_lib(root)   # once a command matched, so a bad one still gets the usage
    return run(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

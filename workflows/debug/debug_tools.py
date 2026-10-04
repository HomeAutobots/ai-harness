#!/usr/bin/env python3
"""ai-harness workflow pack: debug. Harness-owned: replaced on upgrade.

A debugging process the agent follows like a skeleton (kinds/<kind>.md), filled in with the
project's own tools through its playbook (DEBUG_DIR/playbook.md). It investigates and stops at a
root cause the human approves; the fix goes through the project's own process. Each session lives
in DEBUG_DIR/sessions/<slug>/, local and never committed: report.md, evidence/E-<n>.md and .log
(written by debug run), hypotheses.md, root-cause.md, state, and approvals (written only by debug
approve and reject; each line is also recorded in the git dir, .git/ai-harness/debug-approvals,
through the harness's .agents/lib/approvals.py). Settings come from .agents/harness.conf
(environment variables override); a missing key means its default:

  DEBUG_DIR     the playbook and sessions/, repo-relative or absolute (default .agents/debug)
  DEBUG_KINDS   the workflows that are on (default bug)
  DEBUG_SCOPE   globs where experiments must be gone before the check-in (default **)
  DEBUG_ASK     check-ins that need the human: rootcause (default); empty means agent review only

  debug_tools.py cli <root> <command> [args...]             the debug command (bin/debug)
  debug_tools.py check <edit|turn|full> <root> [files...]   the verify checks; with AGENTS_SINCE
                                                            (verify --since), turn and full also
                                                            judge what was committed since then
Exit: 0 clean, 1 findings, 2 usage or a policy block (an approval debug didn't record), 3 tooling
problem or an unknown option. debug run exits with its command's exit code.
"""
import fnmatch
import hashlib
import os
import re
import shlex
import subprocess
import sys

PACK = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {"DEBUG_DIR": ".agents/debug", "DEBUG_KINDS": "bug", "DEBUG_SCOPE": "**", "DEBUG_ASK": "rootcause"}
DEFAULT_TICKET = r"[A-Z][A-Z0-9]+-[0-9]+"
HEADINGS = ("Summary", "Cause", "Evidence", "Reproduction", "Ruled out", "Fix direction")
ATTEMPTS = ("reproduce", "confirm")
OUTCOMES = ("reproduced", "partial", "not-reproduced")
BINDINGS = ("skill", "run", "context")
CLOSE_REASONS = ("abandoned", "duplicate", "reviewed")
TAIL = 60   # lines of a command's output kept in its evidence entry
STATE_KEYS = ("kind", "ref", "start", "branch", "seq", "step", "status", "note")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
EID = re.compile(r"(?<![A-Za-z0-9-])E-([0-9]+)(?![0-9])")
HID = re.compile(r"(?<![A-Za-z0-9-])H-([0-9]+)(?![0-9])")
BLOCKING = ("debug-approval-unrecorded", "debug-approval-simulated", "debug-simulated-human")


class ConfError(Exception):
    pass


def gate_key():
    """The pack's record key, from its human-gates file (the one install.sh reads; the first line
    that's a key, not "on"), so the two can't drift; debug when the file can't be read or names none."""
    try:
        with open(os.path.join(PACK, "human-gates"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.fullmatch(r"\s*([a-z0-9][a-z0-9-]*)\s*", line)
                if m and m.group(1) != "on":
                    return m.group(1)
    except OSError:
        pass
    return "debug"


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


# ------------------------------------------------------------------ config and files

def read_conf(path, prefix):
    out = {}
    for line in read_lines(path):
        m = re.match(r'^\s*(%s[A-Z_]+)=(?:"([^"]*)"|\'([^\']*)\'|([^\s#]*))' % prefix, line)
        if m:
            out[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
    return out


def load_conf(root):
    """DEBUG_* from .agents/harness.conf over the defaults (an install from before a key has no
    line for it), then the environment. An empty DEBUG_DIR means the default, as in checks/state.sh."""
    conf = dict(DEFAULTS)
    conf.update(read_conf(os.path.join(root, ".agents", "harness.conf"), "DEBUG_"))
    for k in DEFAULTS:
        if k in os.environ:
            conf[k] = os.environ[k]
    conf["DEBUG_DIR"] = conf["DEBUG_DIR"] or DEFAULTS["DEBUG_DIR"]
    return conf


def git(root, *args):
    return subprocess.run(["git", "-C", root, "-c", "core.quotePath=false"] + list(args), capture_output=True,
                          text=True, errors="replace").stdout


def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def append_line(path, line):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def one_line(text):
    return " ".join(str(text).split())


def shown(root, path):
    """Repo-relative inside the repo, absolute outside it."""
    rel = os.path.relpath(path, root)
    return path if rel.startswith("..") else rel


def sha(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def finding(path, line, kind, msg, fix):
    """(kind, text): the text in the harness's finding format."""
    return kind, "%s:%d: error: [%s] %s\n  fix: %s" % (path, line, kind, msg, fix)


def emit(found, blocked=()):
    """Print findings; exit 2 (a policy block) when any is a forged approval, else 1 or 0."""
    for f in list(blocked) + list(found):
        print(f)
    return 2 if blocked else 1 if found else 0


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


def debug_dir(root, conf):
    d = conf["DEBUG_DIR"]
    return os.path.normpath(d if os.path.isabs(d) else os.path.join(root, d))


def sessions_dir(root, conf):
    return os.path.join(debug_dir(root, conf), "sessions")


def playbook_path(root, conf):
    return os.path.join(debug_dir(root, conf), "playbook.md")


def debug_cmd(root):
    """How to run the debug command here (.agents/commands/debug when sync wrote it)."""
    return ap.human_cmd(root, PACK, "debug")


# ------------------------------------------------------------------ kinds

def kind_file(kind):
    return os.path.join(PACK, "kinds", kind + ".md")


def read_kind(kind):
    """{'steps': [...], 'bindable': [...]} from a kind file's front matter, or None when the pack has
    no such kind. Steps run in order; bindable ones have a playbook section."""
    if not SLUG.fullmatch(kind or ""):
        return None
    lines = read_lines(kind_file(kind))
    meta = {}
    if lines[:1] == ["---"]:
        for l in lines[1:]:
            if l == "---":
                break
            k, sep, v = l.partition(":")
            if sep:
                meta[k.strip()] = v.split()
    if not meta.get("steps"):
        return None
    meta.setdefault("bindable", [])
    return meta


def shipped_kinds():
    try:
        names = sorted(os.listdir(os.path.join(PACK, "kinds")))
    except OSError:
        return []
    return [n[:-3] for n in names if n.endswith(".md") and read_kind(n[:-3])]


# ------------------------------------------------------------------ sessions

def read_state(sdir):
    st = {}
    for line in read_lines(os.path.join(sdir, "state")):
        k, sep, v = line.partition(": ")
        if sep and k in STATE_KEYS:
            st[k] = v
    return st


def write_state(sdir, st):
    write_text(os.path.join(sdir, "state"),
               "".join("%s: %s\n" % (k, one_line(st[k])) for k in STATE_KEYS if st.get(k, "") != ""))


def seq_of(st):
    s = st.get("seq", "")
    return int(s) if s.isdigit() else 0


def all_sessions(root, conf):
    """[(slug, dir, state)] for every session, oldest first."""
    sd = sessions_dir(root, conf)
    try:
        names = os.listdir(sd)
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(sd, n)
        if SLUG.fullmatch(n) and os.path.isfile(os.path.join(p, "state")):
            out.append((n, p, read_state(p)))
    return sorted(out, key=lambda s: (seq_of(s[2]), s[0]))


def find_session(root, conf, slug):
    for s in all_sessions(root, conf):
        if s[0] == slug:
            return s
    return None


def current_branch(root):
    return git(root, "symbolic-ref", "-q", "--short", "HEAD").strip()


def approvals_path(sdir):
    return os.path.join(sdir, "approvals")


def verdict(root, slug, sdir, switch):
    """'approved', 'rejected', or '': the session's counted approvals line that debug approve or
    reject recorded last (its place in the git-dir record) decides."""
    counted, _, _ = ap.classify(root, KEY, approvals_path(sdir), switch)
    best, at = "", None
    for _, parts, p in counted:
        if parts[1] == slug and parts[0] in ("rootcause", "reject") and (at is None or p >= at):
            best, at = ("approved" if parts[0] == "rootcause" else "rejected"), p
    return best


def not_open(root, slug, sdir, st, switch):
    """Why a session isn't open ('approved', 'closed (abandoned)'), or '' while it is. Approved
    comes only from a recorded approval, never from the state file alone."""
    if st.get("status") == "closed":
        return "closed (%s)" % (st.get("note") or "no reason given")
    if verdict(root, slug, sdir, switch) == "approved":
        return "approved"
    return ""


def current_session(root, conf, switch):
    """(slug, dir, state) of the agent's current session: the newest open one started on this branch
    (on a detached HEAD, the newest started detached); None when there's none."""
    br, found = current_branch(root), None
    for slug, sdir, st in all_sessions(root, conf):
        if st.get("branch", "") == br and not not_open(root, slug, sdir, st, switch):
            found = (slug, sdir, st)
    return found


# ------------------------------------------------------------------ checks

CHECKS = []   # each takes a Ctx and returns [(kind, finding text)]; run in this order


def check(fn):
    CHECKS.append(fn)
    return fn


class Ctx:
    """What one check run looks at: the tier ('edit', 'turn', 'full', or 'checkin' for the set
    debug approve runs), the files verify passed, and the session (the current one by default)."""

    def __init__(self, root, tier, files, session=None):
        self.root, self.tier = root, tier
        self.conf = load_conf(root)
        self.edited = {os.path.normpath(os.path.join(root, f)) for f in files}
        self.switch = ap.Switch(root)
        cur = session or current_session(root, self.conf, self.switch)
        self.slug, self.sdir, self.state = cur if cur else (None, None, {})

    def path(self, name):
        return os.path.join(self.sdir, name)

    def judged(self, path):
        """The file exists and this tier looks at it: the edit tier only when it was edited."""
        return os.path.isfile(path) and (self.tier != "edit" or os.path.normpath(path) in self.edited)


def run_checks(ctx):
    """(findings, policy blocks), each a list of finding texts."""
    found, blocked = [], []
    for fn in CHECKS:
        for kind, text in fn(ctx):
            (blocked if kind in BLOCKING else found).append(text)
    return found, blocked


def cmd_check(tier, root, files):
    ctx = Ctx(root, tier, files)
    found, blocked = run_checks(ctx)
    if tier != "edit" and ctx.slug and ctx.switch.state == "on":   # verify shows note lines even on a pass
        print("note: simulated human is on in this clone (%s): a shell with its token can approve debug "
              "check-ins, and each approval made here is marked simulated" % ctx.switch.how)
    return emit(found, blocked)


# ------------------------------------------------------------------ the debug command

CLI = {}   # name -> (function, usage line, options it takes), in the order usage lists them


def command(name, usage, options=()):
    def wrap(fn):
        CLI[name] = (fn, usage, options)
        return fn
    return wrap


def usage_text(name=None):
    lines = [CLI[name][1]] if name else \
        ["debug <command> [args...]   (debug <command> --help for one)"] + [u for _, u, _ in CLI.values()]
    return "usage: " + "\n       ".join(lines)


def bad(name):
    print(usage_text(name), file=sys.stderr)
    return 2


def no_session(root):
    print("debug: no open session on this branch; start one with %s start <kind> <ref>, or see %s status"
          % (debug_cmd(root), debug_cmd(root)), file=sys.stderr)
    return 2


def cli(root, a):
    """Parse a debug command line. --help alone prints usage; --help among other words, or an
    option the command doesn't take, is refused (exit 3) before anything changes. Words after --
    are the command (run) or text (reject, close), never options."""
    if a in (["-h"], ["--help"]):
        print(usage_text())
        return 0
    if not a or a[0] not in CLI:
        if a and a[0] in ("-h", "--help"):
            print("debug: -h or --help with other arguments; nothing was done", file=sys.stderr)
            print(usage_text(), file=sys.stderr)
            return 3
        return bad(None)
    name, rest = a[0], a[1:]
    fn, usage, options = CLI[name]
    words, opts, after = [], {}, None
    for i, w in enumerate(rest):
        if w == "--":
            after = rest[i + 1:]
            break
        if w in ("-h", "--help"):
            if len(rest) == 1:
                print(usage_text(name))
                return 0
            print("debug: %s: -h or --help with other arguments; nothing was done" % name, file=sys.stderr)
            print(usage_text(name), file=sys.stderr)
            return 3
        if w.startswith("-") and w != "-":
            k = w.split("=", 1)[0] + ("=" if "=" in w else "")
            if k not in options:
                print("debug: %s: unknown option '%s' (text that starts with - goes after --)" % (name, w),
                      file=sys.stderr)
                print(usage_text(name), file=sys.stderr)
                return 3
            opts[k] = w.split("=", 1)[1] if "=" in w else ""
            continue
        words.append(w)
    return fn(root, words, opts, after)


# ------------------------------------------------------------------ main

def main(argv):
    try:
        return dispatch(argv[1:])
    except ConfError as e:
        print("infra: %s" % e)
    except re.error as e:
        print("infra: GIT_TICKET in .agents/git.conf isn't a valid Python regex: %s" % e)
    except Exception as e:  # a crash is a tooling problem, not findings
        print("infra: debug_tools failed: %s" % e)
    return 3


def dispatch(a):
    at = 2 if a[:1] == ["check"] else 1   # where the project root is in the arguments
    if len(a) > at:
        use_lib(os.path.abspath(a[at]))
    if len(a) >= 3 and a[0] == "check" and a[1] in ("edit", "turn", "full"):
        return cmd_check(a[1], os.path.abspath(a[2]), a[3:])
    if len(a) >= 2 and a[0] == "cli":
        return cli(os.path.abspath(a[1]), a[2:])
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

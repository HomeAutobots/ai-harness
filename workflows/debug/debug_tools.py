#!/usr/bin/env python3
"""ai-harness workflow pack: debug. Harness-owned: replaced on upgrade.

A debugging process the agent follows like a skeleton (kinds/<kind>.md), filled in with the
project's own tools through its playbook (DEBUG_DIR/playbook.md). It investigates and stops at a
root cause the human approves; the fix goes through the project's own process. Each session lives
in DEBUG_DIR/sessions/<slug>/, local and never committed: report.md, evidence/E-<n>.md and .log
(written by debug run), hypotheses.md, root-cause.md, state, and approvals (written only by debug
approve and reject; each line is also recorded in the git dir, .git/ai-harness/debug-approvals,
through the harness's .agents/lib/approvals.py). Settings come from .agents/harness.conf only,
never the environment (checks/state.sh and the stop gate read just the file); a missing key means
its default, and a value this pack doesn't know is a tooling problem (exit 3):

  DEBUG_DIR     the playbook and sessions/, repo-relative or absolute (default .agents/debug)
  DEBUG_KINDS   the workflows that are on (default bug)
  DEBUG_SCOPE   globs where experiments must be gone before the check-in (default **)
  DEBUG_ASK     check-ins that need the human: rootcause (default); empty means agent review only

  debug_tools.py cli <root> <command> [args...]             the debug command (bin/debug)
  debug_tools.py check <edit|turn|full> <root> [files...]   the verify checks; with AGENTS_SINCE
                                                            (verify --since), turn and full also
                                                            judge what was committed since then
Exit: 0 clean, 1 findings, 2 usage or a policy block (an approval debug didn't record, or a
command .agents/policy.conf blocks), 3 tooling problem or an unknown option. debug run exits with
its command's exit code, so its 2 or 3 can come from the command too: a run that happened prints its
"E-<n> (...)" line first. Its command gets no stdin (/dev/null) and has no time limit, and E-<n>.log
keeps all of its output with no size cap; only the entry (E-<n>.md) is cut to the last lines.
"""
import fnmatch
import hashlib
import os
import re
import shlex
import shutil
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
TAIL = 60   # lines of a command's output kept in its evidence entry (E-<n>.md). Checks must never
            # read E-<n>.log: checks/state.sh leaves *.log out of verify's cache key.
STATE_KEYS = ("kind", "ref", "start", "branch", "seq", "step", "status", "note")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
EID = re.compile(r"(?<![A-Za-z0-9-])E-([0-9]+)(?![0-9])")
HID = re.compile(r"(?<![A-Za-z0-9-])H-([0-9]+)(?![0-9])")
# A check that emits a policy-block kind must list it here, or it exits 1.
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
    line for it). Never the environment: checks/state.sh (verify's cache key) and the stop gate's
    settings note read only the file, and DEBUG_* is a common name. An empty DEBUG_DIR means the
    default, as in checks/state.sh. A check-in or workflow this pack doesn't know raises ConfError."""
    conf = dict(DEFAULTS)
    conf.update(read_conf(os.path.join(root, ".agents", "harness.conf"), "DEBUG_"))
    conf["DEBUG_DIR"] = conf["DEBUG_DIR"] or DEFAULTS["DEBUG_DIR"]
    for w in conf["DEBUG_ASK"].split():
        if w != "rootcause":
            raise ConfError("DEBUG_ASK: unknown check-in '%s' (rootcause)" % w)
    kinds = shipped_kinds()
    for w in conf["DEBUG_KINDS"].split():
        if w not in kinds:
            raise ConfError("DEBUG_KINDS: unknown workflow '%s' (%s)" % (w, " ".join(kinds)))
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
        if a and a[0].startswith("-") and a[0] != "-":
            if a[0] in ("-h", "--help"):
                print("debug: -h or --help with other arguments; nothing was done", file=sys.stderr)
            else:
                print("debug: unknown option '%s' (a command's options go after it)" % a[0], file=sys.stderr)
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
    use_lib(root)   # once the line parsed, so usage and refusals work without the approvals library
    return fn(root, words, opts, after)


# ------------------------------------------------------------------ the playbook and starting

def read_playbook(path):
    """({step: [(line no, key, value)]}, [(line no, heading)], [(line no, text)]): the bindings in
    each '## <step>' section, every section heading, and every list item in a section that isn't a
    '<key>: <value>' binding. Lines before the first section, and other lines, are notes."""
    sections, heads, odd, cur = {}, [], [], None
    for n, line in enumerate(read_lines(path), 1):
        if line.startswith("## "):
            cur = line[3:].strip()
            heads.append((n, cur))
            sections.setdefault(cur, [])
            continue
        m = re.match(r"^\s*[-*]\s+(.*?)\s*$", line) if cur is not None else None
        if not m:
            continue
        b = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", m.group(1))
        if b:
            sections[cur].append((n, b.group(1), b.group(2).strip().strip("`").strip()))
        else:
            odd.append((n, m.group(1)))
    return sections, heads, odd


def unbound_steps(root, conf, kind):
    """The kind's bindable steps whose playbook section is missing or has no binding: a known key
    (BINDINGS) with a value."""
    meta = read_kind(kind) or {"bindable": []}
    sections = read_playbook(playbook_path(root, conf))[0]
    return [s for s in meta["bindable"] if not any(k in BINDINGS and v for _, k, v in sections.get(s, []))]


def ticket_pattern(root):
    """GIT_TICKET from .agents/git.conf (the default when unset), compiled; ConfError when it isn't
    a valid regex, so the message names the key and the file."""
    ticket = read_conf(os.path.join(root, ".agents", "git.conf"), "GIT_").get("GIT_TICKET") or DEFAULT_TICKET
    try:
        return re.compile(ticket)
    except re.error as e:
        raise ConfError("GIT_TICKET in .agents/git.conf isn't a valid Python regex: %s" % e)


def new_slug(root, conf, kind, ref, text):
    """<kind>-<ref> in lowercase (bug-proj-123, bug-42), or <kind>-<first words of the report> for a
    pasted one; -2, -3... when a session or plan already has that name."""
    if ref == "-":
        first = next((l for l in text.splitlines() if l.strip()), "")
        base = "-".join(re.findall(r"[a-z0-9]+", first.lower())[:6])[:40].strip("-") or "report"
    else:
        base = re.sub(r"[^a-z0-9]+", "-", ref.lower()).strip("-")
    slug, n = "%s-%s" % (kind, base), 2
    while os.path.exists(os.path.join(sessions_dir(root, conf), slug)) or \
            os.path.exists(os.path.join(root, ".agents", "plans", slug)):
        slug, n = "%s-%s-%d" % (kind, base, n), n + 1
    return slug


def tasks_path(root):
    return os.path.join(root, ".agents", "bin", "tasks")


def tasks_cli(root, *args):
    """Run .agents/bin/tasks: '' when it worked, else what it said on stderr (never empty)."""
    r = subprocess.run(["bash", tasks_path(root)] + list(args), cwd=root, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, errors="replace")
    return "" if r.returncode == 0 else (one_line(r.stderr) or "tasks exited %d" % r.returncode)


def open_plan(root, slug, kind, ref):
    """A plan ledger named after the session with one task in progress, so a question to the human
    (tasks ask) or the check-in pauses the stop gate. Its path, or '' when there's no tasks CLI (an
    older install) or a step failed (said on stderr; the session stands without a plan)."""
    if not os.path.isfile(tasks_path(root)):
        return ""
    what = "the pasted report" if ref == "-" else ref
    for step in (("new", slug, "Debug %s %s" % (kind, what)),
                 ("add", slug, "Find the root cause of %s (debug session %s)" % (what, slug)),
                 ("set", slug, "T1", "doing")):
        err = tasks_cli(root, *step)
        if err:
            print("debug: couldn't open the plan: %s" % err, file=sys.stderr)
            return ""
    return os.path.join(".agents", "plans", slug)


REPORT = """# Report: %s

Kind: %s. "As received" is the report exactly as it came; Expected and Actual are pulled out of it.

## As received

%s

## Expected

## Actual

## Unclear
"""

HYPOTHESES = """# Hypotheses

One section per hypothesis: `## H-<n>: <claim>`, then `- would confirm: ...`, `- would rule out: ...`,
and `- status: open`, `- status: confirmed (E-<n>)`, or `- status: ruled out (E-<n>)`.
"""


@command("start", "debug start <kind> <ref | -> [--file=<path>]", ("--file=",))
def cmd_start(root, words, opts, after):
    if len(words) != 2 or after is not None:
        return bad("start")
    kind, ref = words
    if opts.get("--file=", None) == "":
        return bad("start")
    conf = load_conf(root)
    meta = read_kind(kind)
    if not meta:
        print("debug: no such workflow: %s (shipped: %s)" % (kind[:40], " ".join(shipped_kinds())),
              file=sys.stderr)
        return 2
    on = conf["DEBUG_KINDS"].split()
    if kind not in on:
        print("debug: the %s workflow isn't on here (DEBUG_KINDS in .agents/harness.conf: %s)"
              % (kind[:40], " ".join(on) or "empty"), file=sys.stderr)
        return 2
    if ref != "-" and not re.fullmatch(r"#[0-9]+", ref):
        ticket = ticket_pattern(root)   # only here, so a broken GIT_TICKET doesn't block #<n> or -
        if not ticket.fullmatch(ref):
            print("debug: %s isn't a ref: give a ticket key (%s), #<n> for a GitHub or GitLab issue, or - "
                  "to read the report from stdin or --file" % (ref[:40], ticket.pattern), file=sys.stderr)
            return 2
    text = ""
    if "--file=" in opts:
        typed = opts["--file="]   # from the caller's working directory: bin/debug doesn't cd
        try:
            with open(os.path.abspath(os.path.expanduser(typed)), "rb") as fh:
                text = fh.read().decode("utf-8", "replace")
        except OSError as e:
            print("debug: can't read %s: %s" % (typed, e.strerror), file=sys.stderr)
            return 2
    elif ref == "-":
        if sys.stdin.isatty():
            print("debug: paste the report, then Ctrl-D", file=sys.stderr)
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
    text = text.strip("\n")
    if ref == "-" and not text.strip():
        print("debug: the report is empty: pipe it in, or pass --file=<path>", file=sys.stderr)
        return 2
    older = current_session(root, conf, ap.Switch(root))
    slug = new_slug(root, conf, kind, ref, text)
    sdir = os.path.join(sessions_dir(root, conf), slug)
    got = text or ("(not fetched yet: run the playbook's intake binding for %s, or ask the human to paste "
                   "the report)" % ref)
    state = {"kind": kind, "ref": ref, "branch": current_branch(root),
             "start": git(root, "rev-parse", "-q", "--verify", "HEAD").strip() or "none",
             "seq": str(1 + max([seq_of(st) for _, _, st in all_sessions(root, conf)] + [0])),
             "step": meta["steps"][0], "status": "open"}
    try:
        os.makedirs(sessions_dir(root, conf), exist_ok=True)
        os.mkdir(sdir)   # fails when it exists, so the cleanup below only ever removes this new one
    except OSError as e:
        raise ConfError("can't write the session %s: %s" % (shown(root, sdir), e.strerror or e))
    try:   # state last: a session counts only once it exists, and a failed write leaves no dir behind
        os.mkdir(os.path.join(sdir, "evidence"))
        write_text(os.path.join(sdir, "report.md"), REPORT % ("pasted" if ref == "-" else ref, kind, got))
        write_text(os.path.join(sdir, "hypotheses.md"), HYPOTHESES)
        write_state(sdir, state)
    except Exception as e:   # not only OSError: a report that can't be encoded must not leave a half-made dir
        shutil.rmtree(sdir, ignore_errors=True)
        raise ConfError("can't write the session %s: %s" % (shown(root, sdir), getattr(e, "strerror", None) or e))
    plan = open_plan(root, slug, kind, ref)
    print("started %s (%s, %s): %s" % (slug, kind, "pasted report" if ref == "-" else ref, shown(root, sdir)))
    if older:
        print("note: %s is still open %s; %s is now current"
              % (older[0], "on this branch" if older[2].get("branch") else "here (detached HEAD)", slug))
    print("steps: %s" % shown(root, kind_file(kind)))
    if plan:
        print("plan: %s, task T1 in progress; ask the human with .agents/bin/tasks ask %s T1 '<question>'"
              % (plan, slug))
    if "intake" not in unbound_steps(root, conf, kind):
        print("next: intake: use the playbook's intake bindings (%s)" % shown(root, playbook_path(root, conf)))
    elif text:
        print("next: intake: no playbook binding, so the report as received is the starting record; pull "
              "Expected and Actual out of it in report.md")
    else:
        print("next: intake: no playbook binding and no report text; ask the human to paste the report")
    return 0


# ------------------------------------------------------------------ evidence

def evidence(sdir):
    """{n: {step, attempt, outcome, command, exit, head}} from the header of each evidence/E-<n>.md
    (the lines before its first '## ')."""
    ed = os.path.join(sdir, "evidence")
    try:
        names = os.listdir(ed)
    except OSError:
        return {}
    out = {}
    for name in names:
        m = re.fullmatch(r"E-([0-9]+)\.md", name)
        if not m:
            continue
        meta = {}
        for l in read_lines(os.path.join(ed, name)):
            if l.startswith("## "):
                break
            k, sep, v = l.partition(": ")
            if sep and k in ("step", "attempt", "outcome", "command", "exit", "head"):
                meta[k] = v
        out[int(m.group(1))] = meta
    return out


def policy_block(root, cmdline):
    """What the policy hook would say about this command line (.agents/bin/policy test), so debug
    run never gets around .agents/policy.conf: (why, the rule line policy test names, or ''), or
    None when it's allowed or harness.conf turns the policy hook off. AGENTS_HOOKS is left out of
    the test's environment: set inline on the debug command, it would turn the check off for that
    one call while the hook stays on. A policy test that's missing or fails is a tooling problem
    (ConfError), never a pass."""
    pol = os.path.join(root, ".agents", "bin", "policy")
    if not os.path.isfile(pol):
        raise ConfError("this project's harness has no .agents/bin/policy to check the command against, "
                        "so it didn't run; re-run install.sh")
    env = dict(os.environ)
    env.pop("AGENTS_HOOKS", None)
    p = subprocess.run(["bash", pol, "test", cmdline], cwd=root, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, errors="replace")
    if p.returncode == 0:
        return None
    if p.returncode != 2:
        raise ConfError("couldn't check the command against .agents/policy.conf, so it didn't run: %s"
                        % ((p.stderr.strip().splitlines() or [""])[-1][:300] or "policy test exited %d" % p.returncode))
    out = p.stdout.strip().splitlines()
    if any(l.startswith("note: the policy hook is off") for l in out):
        return None
    first = (out or ["blocked"])[0]
    rule = next((l for l in out[1:] if not l.startswith("note: ")), "")
    return (first[len("blocked: "):] if first.startswith("blocked: ") else first), rule


def tail_lines(path, n=TAIL):
    """The last n lines of a file, as a terminal would show them: split on newlines only, each line
    the text after its last carriage return (progress bars), cut at 1000 characters. Reads at most
    its last MiB, so a huge log costs no more than a small one."""
    window = 1 << 20
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - window - 1))
            lines = fh.read().decode("utf-8", errors="replace").split("\n")
    except OSError:
        return []
    if size > window + 1:
        lines = lines[1:]   # it started before the part read (or is the empty text before a newline)
    if lines and lines[-1] == "":
        lines.pop()
    lines = [l[:-1] if l.endswith("\r") else l for l in lines[-n:]]
    lines = [l.rsplit("\r", 1)[-1] for l in lines]
    return [l if len(l) <= 1000 else l[:1000] + " ..." for l in lines]


def new_entry(sdir):
    """(n, fd of evidence/E-<n>.log, created with O_EXCL): the next number after every E-<n>.md and
    E-<n>.log there, reserved before the command runs, so two runs at once never share one. A log
    with no .md is a run in progress (or one that died); evidence() counts only the .md entries."""
    ed = os.path.join(sdir, "evidence")
    os.makedirs(ed, exist_ok=True)
    nums = [int(m.group(1)) for m in (re.fullmatch(r"E-([0-9]+)\.(?:md|log)", x) for x in os.listdir(ed)) if m]
    n = max(nums) + 1 if nums else 1
    while True:
        try:
            return n, os.open(os.path.join(ed, "E-%d.log" % n), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            n += 1


@command("run", "debug run <step> [--attempt=reproduce|confirm] -- <command...>", ("--attempt=",))
def cmd_run(root, words, opts, after):
    attempt = opts.get("--attempt=")
    if len(words) != 1 or not after or (attempt is not None and attempt not in ATTEMPTS):
        return bad("run")
    conf = load_conf(root)
    cur = current_session(root, conf, ap.Switch(root))
    if not cur:
        return no_session(root)
    slug, sdir, st = cur
    meta, step = read_kind(st.get("kind", "")), words[0]
    if not meta or step not in meta["steps"]:
        print("debug: %s isn't a step of the %s workflow (%s)" % (step[:40], st.get("kind", "?"),
              " ".join(meta["steps"]) if meta else "its kind file is gone"), file=sys.stderr)
        return 2
    cmdline = shlex.join(after)
    blocked = policy_block(root, cmdline)
    if blocked:
        print("debug: not run: %s" % blocked[0], file=sys.stderr)
        if blocked[1]:
            print(blocked[1], file=sys.stderr)
        return 2
    head = git(root, "rev-parse", "-q", "--verify", "HEAD").strip()[:12] or "none"   # what it ran on
    if git(root, "status", "--porcelain").strip():
        head += ", with uncommitted changes"
    n, fd = new_entry(sdir)
    md, log = (os.path.join(sdir, "evidence", "E-%d%s" % (n, ext)) for ext in (".md", ".log"))
    with os.fdopen(fd, "wb") as fh:
        try:
            rc = subprocess.run(after, cwd=root, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL).returncode
        except OSError as e:   # as a shell says it: 127 not found, 126 found but it can't run
            fh.write(("debug: couldn't run %s: %s\n" % (after[0], e.strerror or e)).encode("utf-8", "replace"))
            rc = 127 if isinstance(e, FileNotFoundError) else 126
        except KeyboardInterrupt:
            rc = 130
    rc = rc if rc >= 0 else 128 - rc   # killed by a signal
    tail = tail_lines(log)
    shown_cmd = one_line(cmdline).encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    header = ["# E-%d" % n, "step: " + step] + (["attempt: " + attempt] if attempt else []) + \
             ["command: " + shown_cmd, "exit: %d" % rc, "head: " + head]
    write_text(md, "\n".join(header + ["", "## Output (last %d lines)" % TAIL, ""] + tail +
                             ["", "Full output: E-%d.log" % n]) + "\n")
    st = read_state(sdir)   # fresh: the session may have been closed while the command ran
    if st.get("status") == "open":
        st["step"] = step
        write_state(sdir, st)
    print("E-%d (%s%s, exit %d): %s" % (n, step, ", %s attempt" % attempt if attempt else "", rc, shown(root, md)))
    for l in tail:
        print(l)
    if attempt:
        print("record the outcome: %s outcome E-%d reproduced|partial|not-reproduced" % (debug_cmd(root), n))
    return rc


@command("outcome", "debug outcome <E-n> reproduced|partial|not-reproduced")
def cmd_outcome(root, words, opts, after):
    m = re.fullmatch(r"E-([0-9]+)", words[0]) if len(words) == 2 else None
    if not m or words[1] not in OUTCOMES or after is not None:
        return bad("outcome")
    conf = load_conf(root)
    cur = current_session(root, conf, ap.Switch(root))
    if not cur:
        return no_session(root)
    slug, sdir, _ = cur
    n = int(m.group(1))
    meta = evidence(sdir).get(n)
    if meta is None:
        print("debug: session %s has no E-%d" % (slug, n), file=sys.stderr)
        return 1
    if meta.get("attempt") not in ATTEMPTS:
        print("debug: E-%d isn't an attempt; run one with debug run <step> --attempt=reproduce -- <command>" % n,
              file=sys.stderr)
        return 1
    path = os.path.join(sdir, "evidence", "E-%d.md" % n)
    lines = read_lines(path)
    end = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
    header = [l for l in lines[:end] if not l.startswith("outcome: ")]
    at = next(i for i, l in enumerate(header) if l.startswith("attempt: "))
    write_text(path, "\n".join(header[:at + 1] + ["outcome: " + words[1]] + header[at + 1:] + lines[end:]) + "\n")
    print("E-%d: %s (%s attempt)" % (n, words[1], meta["attempt"]))
    return 0


# ------------------------------------------------------------------ main

def main(argv):
    try:
        return dispatch(argv[1:])
    except ConfError as e:
        print("infra: %s" % e)
    except Exception as e:  # a crash is a tooling problem, not findings
        print("infra: debug_tools failed: %s" % e)
    return 3


def dispatch(a):
    if len(a) >= 3 and a[0] == "check" and a[1] in ("edit", "turn", "full"):
        root = os.path.abspath(a[2])
        use_lib(root)   # once a command matched, so a bad one still gets the usage
        return cmd_check(a[1], root, a[3:])
    if len(a) >= 2 and a[0] == "cli":
        return cli(os.path.abspath(a[1]), a[2:])   # cli() loads the library once the line parses
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

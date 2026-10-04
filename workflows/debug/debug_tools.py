#!/usr/bin/env python3
"""ai-harness workflow pack: debug. Harness-owned: replaced on upgrade.

A debugging process the agent follows like a skeleton (kinds/<kind>.md), filled in with the
project's own tools through its playbook (DEBUG_DIR/playbook.md). It investigates and stops at a
root cause the human approves; the fix goes through the project's own process. Each session lives
in DEBUG_DIR/sessions/<slug>/, local and never committed: report.md, evidence/E-<n>.md and .log
(written by debug run), hypotheses.md, root-cause.md, state, and approvals (written only by debug
approve, reject, and close; each line is also recorded in the git dir, .git/ai-harness/debug-approvals,
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
import json
import os
import re
import shlex
import shutil
import stat
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
STATE_KEYS = ("kind", "ref", "start", "branch", "seq", "step", "status", "note", "confirm_after")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
EID = re.compile(r"(?<![A-Za-z0-9-])E-([0-9]+)(?![A-Za-z0-9])")   # h_sections() ends a heading's id the same way
HID = re.compile(r"(?<![A-Za-z0-9-])H-([0-9]+)(?![A-Za-z0-9])")
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
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
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


def last_line(root, slug, sdir, switch, kinds):
    """The fields of the session's counted approvals line of one of these kinds that debug recorded
    last (its place in the git-dir record), or None."""
    counted, _, _ = ap.classify(root, KEY, approvals_path(sdir), switch)
    best, at = None, None
    for _, parts, p in counted:
        if parts[1] == slug and parts[0] in kinds and (at is None or p >= at):
            best, at = parts, p
    return best


def verdict(root, slug, sdir, switch):
    """'approved', 'rejected', 'changed', or '': the session's counted approvals line that debug
    approve or reject recorded last decides. An approval binds the hash of root-cause.md as it was:
    once the file differs (or is gone), it's 'changed', and the session is open again. An approval
    with no hash binds nothing, so it's 'changed' too."""
    best = last_line(root, slug, sdir, switch, ("rootcause", "reject"))
    if best is None:
        return ""
    if best[0] == "reject":
        return "rejected"
    return "approved" if best[4] and best[4] == sha(os.path.join(sdir, "root-cause.md")) else "changed"


VERDICT_SAID = {"rejected": "root cause rejected", "changed": "root cause changed since its approval"}


def reviewed_void(conf, close):
    """True when a recorded close is a reviewed one that doesn't count: DEBUG_ASK has rootcause now,
    so the human approves the root cause."""
    return close is not None and close[4].split(":", 1)[0] == "reviewed" and "rootcause" in conf["DEBUG_ASK"].split()


CLOSES = ("close", "close-agent")


class OFF:   # a simulated-human switch that's off, for lines that never need the human
    state = "off"   # close-agent: made in an agent's shell without the simulated human's token


def agent_close_void(conf, close, sdir, v):
    """True when a recorded close is an agent's abandoned or duplicate one that doesn't count: the
    root cause waits on the human now (DEBUG_ASK has rootcause, and root-cause.md exists or the
    verdict v is rejected or changed), however it was when the agent closed it."""
    return close is not None and close[0] == "close-agent" and \
        close[4].split(":", 1)[0] in ("abandoned", "duplicate") and "rootcause" in conf["DEBUG_ASK"].split() and \
        (os.path.isfile(os.path.join(sdir, "root-cause.md")) or v in ("rejected", "changed"))


def void_close(conf, close, sdir, v):
    """Why a recorded close doesn't count, as status says it, or '' when it counts (or there's none)."""
    if reviewed_void(conf, close):
        return "closed as reviewed but DEBUG_ASK has rootcause"
    if agent_close_void(conf, close, sdir, v):
        return "closed by an agent but its root cause waits on you"
    return ""


def not_open(root, conf, slug, sdir, st, switch):
    """Why a session isn't open ('approved', 'closed (abandoned)'), or '' while it is. Both come
    only from lines debug recorded (approve, close), never from the state file, which an agent can
    edit. A close counts unless void_close() says why not."""
    v = verdict(root, slug, sdir, switch)
    close = last_line(root, slug, sdir, switch, CLOSES)
    if close is not None and not void_close(conf, close, sdir, v):
        return "closed (%s)" % (close[4] or "no reason given")
    return "approved" if v == "approved" else ""


def current_session(root, conf, switch):
    """(slug, dir, state) of the agent's current session: the newest open one started on this branch
    (on a detached HEAD, the newest started detached); None when there's none."""
    br, found = current_branch(root), None
    for slug, sdir, st in all_sessions(root, conf):
        if st.get("branch", "") == br and not not_open(root, conf, slug, sdir, st, switch):
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


@check
def no_repro_attempt(ctx):
    """debug-no-repro-attempt (check-in, full): root-cause.md exists, but no reproduction attempt
    has its outcome recorded. A confirmation attempt counts too (it's a reproduction as well). The
    outcome itself never blocks: not-reproduced is a fine answer. Attempts made before a rejected
    root cause still count (confirm_after only limits `confirmed`)."""
    rc = ctx.path("root-cause.md") if ctx.slug else ""
    if ctx.tier not in ("checkin", "full") or not rc or not os.path.isfile(rc):
        return []
    ev = evidence(ctx.sdir)
    tried = sorted(n for n, e in ev.items() if e.get("attempt") in ATTEMPTS)
    if any(ev[n].get("outcome") in OUTCOMES for n in tried):
        return []
    cmd = debug_cmd(ctx.root)
    if tried:
        n = tried[-1]
        said = ev[n].get("outcome", "")
        return [finding(shown(ctx.root, rc), 1, "debug-no-repro-attempt",
                        "E-%d is a %s attempt, but %s"
                        % (n, "reproduction" if ev[n]["attempt"] == "reproduce" else "confirmation",
                           "its outcome '%s' isn't one of %s" % (said[:40], "|".join(OUTCOMES)) if said
                           else "its outcome isn't recorded"),
                        "record it: %s outcome E-%d reproduced|partial|not-reproduced" % (cmd, n))]
    return [finding(shown(ctx.root, rc), 1, "debug-no-repro-attempt",
                    "root-cause.md is written, but session %s has no reproduction attempt" % ctx.slug,
                    "try to reproduce it once: %s run reproduce --attempt=reproduce -- <command>, then record "
                    "the outcome (%s outcome E-<n> ...). Not reproduced is a fine answer; the attempt is "
                    "what's required" % (cmd, cmd))]


def heading(line):
    """A level 1 or 2 markdown heading as (level, name), the name normalized for matching: spaces
    collapsed, a trailing colon (and a space before it) and closing hashes dropped, lowercased
    ('  ## Ruled  Out : ##' -> 'ruled out'). None for any other line ('###', '#12 says', '##Cause')."""
    m = re.match(r"^ {0,3}(#{1,2})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$", line)
    return (len(m.group(1)), " ".join(m.group(2).split()).rstrip(":").strip().lower()) if m else None


CONF_LINE = re.compile(r"^\s*(?:[-*+]\s+)?[*_`]*confidence[*_`]*\s*:[\s*_`]*([A-Za-z-]*)", re.I)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def unfenced(lines):
    """([(line no, line)] for the lines outside ``` and ~~~ fences, the line of a fence still open at
    the end or 0). A backtick opener whose info string has a backtick isn't a fence, and a fence
    closes only on a bare run of its own character at least as long as the opener."""
    out, fence, at = [], None, 0
    for n, line in enumerate(lines, 1):
        f = FENCE.match(line)
        if fence is None:
            if f and not (f.group(1)[0] == "`" and "`" in f.group(2)):
                fence, at = f.group(1), n
                continue
        else:
            if f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence) and not f.group(2).strip():
                fence, at = None, 0
            continue
        out.append((n, line))
    return out, at


def root_cause(path):
    """(headings, the required Confidence line, the other Confidence lines, open fence) for
    root-cause.md: {name: line no} of its '## ' headings (names as heading() gives them); (line
    no, value) of the first 'Confidence:' line under ## Reproduction, or None; [(line no, value)]
    of every other one, in any section; the line of a fence still open at the end, or 0. A '# '
    heading ends a section, '###' and deeper stay inside it. A Confidence line may be a list item
    or have *, _ or backticks around its parts ('**Confidence:** `reproduced`'); values are
    lowercased. Lines inside ``` or ~~~ fences are skipped (unfenced())."""
    heads, conf, others, sec = {}, None, [], None
    lines, at = unfenced(read_lines(path))
    for n, line in lines:
        h = heading(line)
        if h:
            sec = h[1] if h[0] == 2 else None
            if sec is not None:
                heads.setdefault(sec, n)
            continue
        m = CONF_LINE.match(line)
        if m:
            if sec == "reproduction" and conf is None:
                conf = (n, m.group(1).lower())
            else:
                others.append((n, m.group(1).lower()))
    return heads, conf, others, at


@check
def rc_format(ctx):
    """debug-format (edit when root-cause.md is edited, turn, full, check-in): a fixed heading is
    missing (one finding names them all), the Confidence: line under ## Reproduction is missing
    or isn't what the recorded attempts support (confidence(), which honors confirm_after), or
    another Confidence line anywhere says something else. With none under ## Reproduction, the
    first one elsewhere is named as being in the wrong place."""
    rc = ctx.path("root-cause.md") if ctx.slug else ""
    if not rc or not ctx.judged(rc):
        return []
    heads, stated, others, fence = root_cause(rc)
    rel, out = shown(ctx.root, rc), []
    code = " (the fence at line %d never closes, so the rest of the file is code)" % fence if fence else ""
    missing = [h for h in HEADINGS if h.lower() not in heads]
    if missing:
        out.append(finding(rel, 1, "debug-format", "root-cause.md has no %s section%s%s"
                           % (", ".join("'## %s'" % h for h in missing), "s" if len(missing) > 1 else "", code),
                           "use the fixed headings, in order: %s" % ", ".join("## " + x for x in HEADINGS)))
    want = confidence(evidence(ctx.sdir), ctx.state)
    after = confirm_after(ctx.state)
    if stated is None and others:   # one elsewhere and none under ## Reproduction: it's in the wrong place
        n, v = others.pop(0)
        out.append(finding(rel, n, "debug-format",
                           "a Confidence line outside ## Reproduction (Confidence: %s); move it there%s"
                           % (v[:40] or "(empty)", code),
                           "move it under ## Reproduction%s" % ("" if v == want else ", and make it 'Confidence: "
                                                               "%s', what the recorded attempts support" % want)))
    elif stated is None:
        out.append(finding(rel, heads.get("reproduction", 1), "debug-format",
                           "no 'Confidence:' line under ## Reproduction%s" % code,
                           "add 'Confidence: %s', what the recorded attempts support" % want))
    elif stated[1] != want:
        out.append(finding(rel, stated[0], "debug-format",
                           "Confidence: %s, but the recorded attempts support %s" % (stated[1][:40] or "(empty)", want),
                           "confirmed needs a confirmation attempt that reproduced the bug through the cause "
                           "(%s run isolate --attempt=confirm -- ...)%s, reproduced needs a reproduction "
                           "attempt that reproduced it, anything else is evidence-only. Say what the attempts "
                           "show, or record the attempt"
                           % (debug_cmd(ctx.root), " (confirmation attempts up to E-%d were for a rejected root "
                              "cause and don't count)" % after if after else "")))
    for n, v in [o for o in others if o[1] != want][:3]:   # with the two above, at most 5 per run
        out.append(finding(rel, n, "debug-format",
                           "a second Confidence line (Confidence: %s); keep one, under ## Reproduction"
                           % (v[:40] or "(empty)"),
                           "drop this line; the one under ## Reproduction says what the recorded attempts "
                           "support (%s)" % want))
    return out


def citations(path, kinds, only=None):
    """[(kind, n, [line nos])] of the ids a file cites outside fences (on the lines in only, when
    given), kind 'E' or 'H' (only those in kinds), in the order they're first cited; each line once
    per id. An id is E- or H- in capitals, then digits, with no letter, digit or hyphen before it
    and no letter or digit after it ('E-1.', '(E-2, E-3)', '_E-4_'; not 'XE-5', 'H-7x' or 'e-7'),
    so E-10 is never E-1. Ids in code spans and URLs count ('`E-9`'); fenced ones don't."""
    found = {}
    for n, line in unfenced(read_lines(path))[0]:
        if only is not None and n not in only:
            continue
        hits = sorted((m.start(), k, int(m.group(1))) for k, rx in (("E", EID), ("H", HID)) if k in kinds
                      for m in rx.finditer(line))
        for _, k, i in hits:
            ls = found.setdefault((k, i), [])
            if not ls or ls[-1] != n:
                ls.append(n)
    return [(k, i, ls) for (k, i), ls in found.items()]   # dicts keep insertion order (3.7+)


@check
def evidence_missing(ctx):
    """debug-evidence-missing (turn, full, check-in; edit when root-cause.md or hypotheses.md is
    edited): root-cause.md cites an E-id debug run didn't capture in this session (evidence())
    or an H-id hypotheses.md doesn't have, or a hypothesis in hypotheses.md cites a missing E-id.
    In hypotheses.md only the '## H-<n>' sections count, so the human's reject reason and other
    notes there are never findings. When only hypotheses.md was edited (the edit tier), root-cause.md
    is judged for its H-ids alone, since dropping a hypothesis breaks those and nothing else.
    Fenced code doesn't count (citations()). One finding per id per file, at the line that first
    cites it; past 5 in a file, the fifth names the rest."""
    if not ctx.slug:
        return []
    rc, hy = ctx.path("root-cause.md"), ctx.path("hypotheses.md")
    todo = [(rc, "EH" if ctx.judged(rc) else "H", None)] if os.path.isfile(rc) and (ctx.judged(rc) or
                                                                                   ctx.judged(hy)) else []
    secs, fence = h_sections(hy)
    todo += [(hy, "E", set().union(*[h[4] for h in secs]))] if ctx.judged(hy) else []
    if not todo:
        return []
    ev, hs, cmd, out = evidence(ctx.sdir), {h[0] for h in secs}, debug_cmd(ctx.root), []
    no_h = ": there's no hypotheses.md" if not os.path.isfile(hy) else \
        " (the fence at line %d in hypotheses.md never closes)" % fence if fence else ""
    kind = "debug-evidence-missing"
    for p, kinds, only in todo:
        rel = shown(ctx.root, p)
        bad = [c for c in citations(p, kinds, only) if c[1] not in (ev if c[0] == "E" else hs)]
        # verify shows at most 5 findings per file (FEEDBACK_MAX_PER_FILE), counting debug-format's on
        # root-cause.md, and says how many more are in its log. Capping here keeps a long list from
        # crowding those out, and the fifth names the rest (up to 8); debug approve's output isn't capped.
        for k, i, ls in (bad[:4] if len(bad) > 5 else bad):
            also = "" if len(ls) < 2 else " (also cited on line%s %s%s)" % (
                "s" if len(ls) > 2 else "", ", ".join(str(x) for x in ls[1:4]), ", ..." if len(ls) > 4 else "")
            if k == "H":
                out.append(finding(rel, ls[0], kind, "H-%d isn't in hypotheses.md%s%s" % (i, no_h, also),
                                   "cite a hypothesis hypotheses.md has (a '## H-<n>: <claim>' section), or add "
                                   "it there first"))
            elif os.path.lexists(os.path.join(ctx.sdir, "evidence", "E-%d.md" % i)):
                out.append(finding(rel, ls[0], kind, "E-%d isn't in this session's evidence: evidence/E-%d.md "
                                   "isn't an entry debug run captured%s" % (i, i, also),
                                   "capture it with %s run <step> -- <command> and cite the E-id it prints; an "
                                   "entry written by hand isn't evidence" % cmd))
            else:
                out.append(finding(rel, ls[0], kind, "E-%d isn't in this session's evidence: there's no "
                                   "evidence/E-%d.md%s" % (i, i, also),
                                   "cite only what debug run captured (%s status lists it); evidence you only "
                                   "describe doesn't count" % cmd))
        if len(bad) > 5:
            rest = bad[4:]
            out.append(finding(rel, rest[0][2][0], kind, "%d more cited ids are missing: %s%s" % (
                len(rest), ", ".join("%s-%d (line %d)" % (k, i, ls[0]) for k, i, ls in rest[:8]),
                ", ..." if len(rest) > 8 else ""),
                "each is missing like the ones above: cite only E-ids debug run captured (%s status lists "
                "them) and H-ids hypotheses.md has" % cmd))
    return out


EXPERIMENT = {"head": "", "deleted": ": the file is deleted", "staged": ": a new file, staged",
              "untracked": ": a new file"}


def undo(p, origin):
    """How to revert one experiment, by changed_lines()'s origin. checkout HEAD, not the index, so a
    staged change is undone too."""
    q = shlex.quote(p)
    if origin in ("head", "deleted"):
        return "restore it with git checkout HEAD -- %s" % q
    if origin == "staged":
        return "unstage it with git rm -q --cached -- %s, then delete the file" % q
    return "delete the file"


@check
def experiments_left(ctx):
    """debug-experiments-left (check-in; turn and full once root-cause.md exists): uncommitted changes
    to files in DEBUG_SCOPE, outside .agents/ and DEBUG_DIR, that sync didn't make (changed_in_scope()),
    one finding per file at its first changed line, saying how to revert it. Past 5 files, the fifth
    finding names the rest. Experiments end when the root cause is written."""
    if not ctx.slug or ctx.tier == "edit" or not os.path.isfile(ctx.path("root-cause.md")):
        return []
    left, kind, scope = changed_in_scope(ctx.root, ctx.conf), "debug-experiments-left", ctx.conf["DEBUG_SCOPE"]
    human = ("If it's the human's own work, ask them to commit it or stash it (git stash -u) before the "
             "check-in")
    out = [finding(p, n, kind, "an uncommitted change in DEBUG_SCOPE (%s) while %s's root-cause.md exists%s"
                   % (scope, ctx.slug, EXPERIMENT[o]),
                   "experiments end when the root cause is written: %s, and say what it showed in "
                   "root-cause.md. %s" % (undo(p, o), human))
           for p, n, o in (left[:4] if len(left) > 5 else left)]
    if len(left) > 5:
        rest = left[4:]
        out.append(finding(rest[0][0], rest[0][1], kind, "%d more files have uncommitted changes in DEBUG_SCOPE "
                           "(%s): %s%s" % (len(rest), scope, ", ".join("%s:%d" % x[:2] for x in rest[:8]),
                                           ", ..." if len(rest) > 8 else ""),
                           "revert each like the ones above (git checkout HEAD -- <path> for a file HEAD has; "
                           "git rm -q --cached -- <path> and then delete it for a new staged file; delete a new "
                           "untracked one; %s status lists them all). %s" % (debug_cmd(ctx.root), human)))
    return out


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
    print("debug: no open session %s; start one with %s start <kind> <ref>, or see %s status"
          % (where(root), debug_cmd(root), debug_cmd(root)), file=sys.stderr)
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


def tasks_run(root, args, tool=None):
    """Run .agents/bin/tasks: (exit code, stdout, stderr). tool sets AGENTS_TOOL, which progress.log
    credits (the tasks CLI says agent when it's unset)."""
    env = dict(os.environ)
    if tool:
        env["AGENTS_TOOL"] = tool
    r = subprocess.run(["bash", tasks_path(root)] + list(args), cwd=root, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, errors="replace")
    return r.returncode, r.stdout, r.stderr


def tasks_cli(root, *args, **kw):
    """Run .agents/bin/tasks: '' when it worked, else what it said on stderr (never empty)."""
    rc, _, err = tasks_run(root, args, kw.get("tool"))
    return "" if rc == 0 else (one_line(err) or "tasks exited %d" % rc)


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
    if git(root, "rev-parse", "--is-inside-work-tree").strip() != "true":   # start commits, diffs against HEAD
        print("debug: the debug workflow needs a git repository", file=sys.stderr)
        return 2
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
    (the lines before its first '## '), values stripped. Only regular files, and only entries whose
    header has the command: and exit: lines debug run always writes: a hand-written note isn't
    evidence."""
    ed = os.path.join(sdir, "evidence")
    try:
        names = os.listdir(ed)
    except OSError:
        return {}
    out = {}
    for name in names:
        m = re.fullmatch(r"E-([1-9][0-9]*)\.md", name)
        if not m:
            continue
        path = os.path.join(ed, name)
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):
                continue
        except OSError:
            continue
        meta = {}
        for l in read_lines(path):
            if l.startswith("## "):
                break
            k, sep, v = l.partition(":")
            if sep and k.strip() in ("step", "attempt", "outcome", "command", "exit", "head"):
                meta[k.strip()] = v.strip()
        if "command" in meta and "exit" in meta:
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
    if lines and lines[-1] == "":
        lines.pop()
    if size > window + 1 and len(lines) > 1:   # a lone last line longer than the window stays
        lines = lines[1:]   # it started before the part read (or is the empty text before a newline)
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
    switch = ap.Switch(root)
    cur = current_session(root, conf, switch)
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
    if not not_open(root, conf, slug, sdir, st, switch):   # a recorded close or approval freezes the step
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
    header = [l for l in lines[:end] if not re.match(r"\s*outcome\s*:", l)]
    at = next(i for i, l in enumerate(header) if re.match(r"\s*attempt\s*:", l))
    write_text(path, "\n".join(header[:at + 1] + ["outcome: " + words[1]] + header[at + 1:] + lines[end:]) + "\n")
    print("E-%d: %s (%s attempt)" % (n, words[1], meta["attempt"]))
    return 0


# ------------------------------------------------------------------ where a session stands

H_STATUSES = ("open", "confirmed", "ruled")


def h_sections(path):
    """([[n, line no, status, status seen, {line nos in the section}]], the line of a fence still
    open at the end or 0) for hypotheses.md: what hypotheses() returns, plus the lines each section
    holds, its heading included."""
    out, cur = [], None
    lines, at = unfenced(read_lines(path))
    for n, line in lines:
        m = re.match(r"^## H-([0-9]+)(?![A-Za-z0-9])", line)   # ends like HID, so citations agree
        if m:
            cur = [int(m.group(1)), n, "open", False, {n}]
            out.append(cur)
            continue
        if re.match(r"#{1,6}\s", line):
            cur = None
            continue
        if cur is None:
            continue
        cur[4].add(n)
        s = re.match(r"^\s*[-*]\s+[*_`]*status[*_`]*\s*:(.*)$", line, re.I)
        if s and not cur[3]:
            w = re.match(r"[\s*_`]*([A-Za-z]+)", s.group(1))
            word = w.group(1).lower() if w else ""
            cur[2], cur[3] = (word if word in H_STATUSES else "unclear"), True
    return out, at


def hypotheses(path):
    """[(n, line no, status)] for each '## H-<n>: <claim>' section in hypotheses.md, in file order (an
    id written twice is there twice). status is the first word of the section's first '- status:'
    line, any case, with *, _ or backticks around it allowed: open, confirmed, ruled (ruled out), or
    unclear for any other word or none; open when there's no status line. Any other markdown heading
    (1 to 6 #, then a space) ends a section; a line like '#12 says' or '#include' doesn't. Lines
    inside ``` or ~~~ fences are code, skipped (unfenced())."""
    return [tuple(h[:3]) for h in h_sections(path)[0]]


def confirm_after(st):
    """The state's confirm_after: confirmation attempts up to this E-number came before a rejected
    root cause, so they don't confirm the current one. 0 when it's missing or not a number."""
    v = (st or {}).get("confirm_after", "").strip()
    return int(v) if v.isdigit() else 0


def confidence(ev, st):
    """What the recorded attempts support: confirmed when a confirmation attempt after the state's
    confirm_after triggered the bug through the stated cause, reproduced when any attempt
    reproduced it (a confirmation attempt is a reproduction too), else evidence-only. debug reject
    writes confirm_after; it sits in the state file an agent can edit, which is accepted, since the
    outcomes it judges are agent-recorded too."""
    after = confirm_after(st)
    hits = [(n, e.get("attempt")) for n, e in ev.items() if e.get("outcome") == "reproduced"]
    if any(a == "confirm" and n > after for n, a in hits):
        return "confirmed"
    if any(a in ATTEMPTS for _, a in hits):
        return "reproduced"
    return "evidence-only"


C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def c_unquote(text):
    """The string a C-style quoted token at the start of text stands for (git quotes a path with a
    tab, newline, quote or backslash in it), or None when the token doesn't close."""
    out, i = bytearray(), 1
    while i < len(text):
        c = text[i]
        if c == '"':
            return out.decode("utf-8", "replace")
        if c == "\\" and text[i + 1:i + 2] in C_ESCAPES:
            out.append(C_ESCAPES[text[i + 1]])
            i += 2
            continue
        if c == "\\" and re.fullmatch(r"[0-3][0-7]{2}", text[i + 1:i + 4]):
            out.append(int(text[i + 1:i + 4], 8))
            i += 4
            continue
        out += c.encode("utf-8")
        i += 1
    return None


def diff_path(rest):
    """The path in a 'diff --git <rest>' line, or None. With --no-renames and fixed prefixes both
    sides name the same path, 'a/P b/P', each side C-quoted when P needs it."""
    if rest.startswith('"'):
        p = c_unquote(rest)
        return p[2:] if p and p.startswith("a/") else None
    p = rest[2:2 + (len(rest) - 5) // 2]
    return p if p and rest == "a/%s b/%s" % (p, p) else None


def git_run(root, *args):
    """git's (exit code, stdout as text, stderr), newlines untouched (a carriage return in a path or
    a line stays one). ConfError when git can't run at all."""
    try:
        p = subprocess.run(["git", "-C", root, "-c", "core.quotePath=false"] + list(args), capture_output=True)
    except OSError as e:
        raise ConfError("can't run git: %s" % (e.strerror or e))
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def git_raw(root, *args):
    """git's stdout as text, newlines untouched; ConfError when git fails, so a broken repo is a
    tooling problem, never a clean tree."""
    rc, out, err = git_run(root, *args)
    if rc != 0:
        raise ConfError("git %s failed: %s" % (args[0], one_line(err)[:300] or "exit %d" % rc))
    return out


DIFF_OPTS = ("--no-color", "--no-ext-diff", "--no-textconv", "--relative", "--no-renames")


def diff_first_lines(root, *rev):
    """{path: (first changed line, status letter)} for one 'git diff' (rev: 'HEAD', '--cached', ...).
    The paths and their status (A added, D deleted, M, T, ...) come from 'git diff --name-status -z',
    so a file the patch parser can't name still counts (line 1); the line is the new side's start of
    the file's first hunk in 'git diff -U0', 1 for a change with no hunk (binary, mode, an empty or
    deleted file). Paths are relative to root; the user's diff settings (external tools, textconv,
    renames, prefixes) can't change the output."""
    raw = git_raw(root, "diff", "--name-status", "-z", *(DIFF_OPTS + rev)).split("\0")
    names = [(p, s[:1]) for s, p in zip(raw[0::2], raw[1::2]) if p]
    first, cur = {}, None
    text = git_raw(root, "diff", "-U0", "--src-prefix=a/", "--dst-prefix=b/", *(DIFF_OPTS + rev))
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            cur = diff_path(line[len("diff --git "):])
            if cur is not None and cur not in first:
                first[cur] = 0
        elif cur is not None and first[cur] == 0 and line.startswith("@@ "):
            m = re.match(r"@@ -\S+ \+(\d+)", line)
            first[cur] = max(int(m.group(1)), 1) if m else 1
    return {p: (first.get(p) or 1, s) for p, s in names}


def changed_lines(root):
    """{path: (first changed line, origin)} for every uncommitted change, relative to root: the working
    tree and the index against HEAD (a change only staged counts too; the working tree's line wins),
    and new files git doesn't ignore (line 1). In a repo with no commits yet, every staged file counts.
    origin says how to undo it: 'head' (HEAD has the file), 'deleted' (HEAD has it, the working tree
    doesn't), 'staged' (a new file in the index), 'untracked' (a new file git doesn't know). Call it
    once per run: up to seven git processes, however many files changed. ConfError when git can't read
    the repo."""
    rc, _, err = git_run(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        raise ConfError("git can't read the repo at %s: %s" % (root, one_line(err)[:300] or "exit %d" % rc))
    got, said = {}, {}
    revs = (("--cached", "HEAD"), ("HEAD",)) if git(root, "rev-parse", "-q", "--verify", "HEAD").strip() \
        else (("--cached",),)   # no commits: the index against the empty tree, every file added
    for rev in revs:
        for p, (n, s) in diff_first_lines(root, *rev).items():
            got[p] = n
            said.setdefault(p, set()).add(s)
    for p in git_raw(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if p:
            got.setdefault(p, 1)
    out = {}
    for p, n in got.items():
        s = said.get(p, set())
        if s - {"A"}:   # a diff against HEAD that isn't an add: HEAD has the file
            out[p] = (n, "head" if os.path.lexists(os.path.join(root, p)) else "deleted")
        else:
            out[p] = (n, "staged" if s else "untracked")
    return out


def in_scope(root, conf, path):
    """True for project code an experiment or commit can touch: a path (relative to root) in
    DEBUG_SCOPE, outside .agents/ and DEBUG_DIR."""
    return not path.startswith(".agents/") and matches_any(path, conf["DEBUG_SCOPE"]) and \
        not os.path.normpath(os.path.join(root, path)).startswith(debug_dir(root, conf) + os.sep)


def changed_in_scope(root, conf):
    """[(path, first changed line, origin)] for the uncommitted changes (changed_lines) to files in
    DEBUG_SCOPE, leaving out .agents/, DEBUG_DIR, and changes sync made (harness_made): the
    experiments a session leaves in the tree. Empty when DEBUG_SCOPE is."""
    if not conf["DEBUG_SCOPE"].split():
        return []
    left = [(p, n, o) for p, (n, o) in sorted(changed_lines(root).items()) if in_scope(root, conf, p)]
    made = harness_made(root, [x[0] for x in left], ("HEAD", ":", None))
    return [x for x in left if x[0] not in made]


# Files outside .agents/ that sync writes, so an upgrade or a sync during a session changes them.
# They're the harness's, not experiments or project code (decision 19), but only what's sync's
# beyond doubt is left out: a file the project edits by hand would hide a real experiment.
HARNESS_WHOLE = (".github/hooks/harness.json", ".codex/rules/harness.rules")   # harness.py renders these whole
RENDER_DIR = re.compile(r"\.(?:claude|github|cursor|codex|gemini)/(?:agents|skills)/")
RENDER = re.compile(r"\.(?:claude|github|cursor|codex|gemini)/(?:agents|skills)/[^/]+")   # one render
MIRROR = re.compile(r"\.claude/skills/([^/]+)")   # a link mirror points at ../../.agents/skills/<name>
BLOCK = re.compile(r"[ \t]*<!-- harness:([a-z0-9-]+):(start|end) -->[ \t\r]*")
CLAUDE_MARK = "<!-- Shared instructions live in AGENTS.md"


def side_entry(root, side, path):
    """(kind, text) of path (relative to root) on one side of a change: None is the working tree,
    ':' the index, anything else a commit. kind is 'link' (text: its target), 'file', or 'other';
    (None, None) when that side doesn't have it."""
    if side is None:
        full = os.path.join(root, path)
        try:
            if os.path.islink(full):
                return "link", os.readlink(full)
            if os.path.isfile(full):
                with open(full, "rb") as fh:
                    return "file", fh.read().decode("utf-8", "replace")
        except OSError:
            return None, None
        return ("other", None) if os.path.lexists(full) else (None, None)
    if side == ":":
        rc, out, _ = git_run(root, "--literal-pathspecs", "ls-files", "-s", "-z", "--", path)
        m = re.match(r"(\d+) ([0-9a-f]+) 0\t([^\0]*)\0", out)   # stage 0: a conflicted file isn't sync's
        typ = "blob"
    else:
        rc, out, _ = git_run(root, "--literal-pathspecs", "ls-tree", "-z", side, "--", path)
        m = re.match(r"(\d+) (\w+) ([0-9a-f]+)\t([^\0]*)\0", out)
        typ = m.group(2) if m else ""
    if rc != 0 or not m or m.groups()[-1] != path:   # a dir in the index lists the files under it
        return None, None
    if typ != "blob":
        return "other", None
    rc, text, _ = git_run(root, "cat-file", "blob", m.groups()[-2])
    mode = m.group(1)
    return ("link" if mode == "120000" else "file" if mode.startswith("100") else "other"), text


def outside_blocks(text):
    """AGENTS.md's lines outside the harness's managed blocks (sync's render_block), blank lines left
    out; None when a block doesn't close (not a file sync rendered)."""
    out, inside = [], None
    for line in text.splitlines():
        m = BLOCK.fullmatch(line)
        if m and inside is None and m.group(2) == "start":
            inside = m.group(1)
        elif m and inside == m.group(1) and m.group(2) == "end":
            inside = None
        elif inside is None and line.strip():
            out.append(line)
    return None if inside is not None else out


def claude_lines(text):
    """CLAUDE.md's lines minus what sync writes there (its strip_claude): the @AGENTS.md and
    @.agents/AGENTS.local.md imports, the marker comment, and blank lines."""
    return [l for l in text.splitlines() if l.strip() and not l.startswith(CLAUDE_MARK)
            and l.rstrip(" \t\r") not in ("@AGENTS.md", "@.agents/AGENTS.local.md")]


INSTRUCTIONS = {"AGENTS.md": outside_blocks, "CLAUDE.md": claude_lines}


def harness_made(root, paths, sides):
    """The paths (relative to root) whose change is sync's, comparing these sides (side_entry's):
    a file harness.py renders whole (HARNESS_WHOLE); a render .agents/generated.lock records on any
    side (agents, skill_copies), inside a tool's agents/ or skills/ dir; a link mirror of
    .agents/skills/; and AGENTS.md or CLAUDE.md when only the harness's parts differ (the managed
    blocks; sync's imports and marker line). Never a config sync merges into the project's own
    (.claude/settings.json, .mcp.json, ...): telling its entries apart takes harness.py's rules,
    which a pack from another library can't count on. No git calls unless a path could be one."""
    cand = [p for p in paths if p in HARNESS_WHOLE or p in INSTRUCTIONS or RENDER_DIR.match(p)]
    renders, out = set(), set()
    if any(RENDER_DIR.match(p) for p in cand):
        for s in sides:
            kind, text = side_entry(root, s, ".agents/generated.lock")
            try:
                lock = json.loads(text) if kind == "file" else {}
            except ValueError:
                lock = {}
            for key in ("agents", "skill_copies"):
                got = lock.get(key) if isinstance(lock, dict) else None
                if isinstance(got, dict):
                    renders |= {r for r in got if isinstance(r, str) and RENDER.fullmatch(r)}
    for p in cand:
        if p in HARNESS_WHOLE or any(p == r or p.startswith(r + "/") for r in renders):
            out.add(p)
            continue
        m, norm = MIRROR.fullmatch(p), INSTRUCTIONS.get(p)
        if not m and not norm:
            continue
        got = [side_entry(root, s, p) for s in sides]
        if m and any(k for k, _ in got) and \
                all(k is None or (k == "link" and t == "../../.agents/skills/" + m.group(1)) for k, t in got):
            out.add(p)
        elif norm and all(k == "file" for k, _ in got):
            seen = [norm(t) for _, t in got]
            if None not in seen and all(x == seen[0] for x in seen):
                out.add(p)
    return out


def listed(left, most=8):
    """'a:1, b:3' for changed_in_scope()'s list: the first most, then 'and N more'."""
    text = ", ".join("%s:%d" % x[:2] for x in left[:most])
    return text + (" and %d more" % (len(left) - most) if len(left) > most else "")


def where(root):
    """'on <branch>', 'here (detached HEAD)' when there's no branch, or 'here' outside git."""
    br = current_branch(root)
    if br:
        return "on " + br
    return "here (detached HEAD)" if git(root, "rev-parse", "-q", "--verify", "HEAD").strip() else "here"


def print_status(root, conf, switch, session):
    slug, sdir, st = session
    kind = st.get("kind", "?")
    why = not_open(root, conf, slug, sdir, st, switch)
    v = verdict(root, slug, sdir, switch)
    if why.startswith("closed"):
        state = why + (", " + VERDICT_SAID[v] if v in VERDICT_SAID else "")
    elif why:
        state = why
    else:
        void = void_close(conf, last_line(root, slug, sdir, switch, CLOSES), sdir, v)
        state = "open" + (", " + void if void else "") + (", " + VERDICT_SAID[v] if v in VERDICT_SAID else "")
    print("session: %s (%s, %s), %s" % (slug, kind, "pasted report" if st.get("ref") == "-" else st.get("ref", "?"),
                                        state))
    print("step: %s" % st.get("step", "?"))
    print("steps: %s" % (shown(root, kind_file(kind)) if read_kind(kind) else "none, the pack has no %s workflow"
                         % one_line(kind)[:40]))
    ev = evidence(sdir)
    print("evidence: %d%s" % (len(ev), " (%s)" % ", ".join("E-%d" % n for n in sorted(ev)) if ev else ""))
    tries = ["E-%d %s: %s" % (n, ev[n]["attempt"], ev[n].get("outcome", "outcome not recorded"))
             for n in sorted(ev) if ev[n].get("attempt") in ATTEMPTS]
    print("attempts: %s" % ("; ".join(tries) if tries else "none yet"))
    print("confidence: %s" % confidence(ev, st))
    by = {}
    for n, _, s in hypotheses(os.path.join(sdir, "hypotheses.md")):
        if n not in by.setdefault(s, []):
            by[s].append(n)

    def ids(s):
        return " (%s)" % ", ".join("H-%d" % n for n in by[s]) if by.get(s) else ""
    print("hypotheses: %d open%s, %d confirmed, %d ruled out%s"
          % (len(by.get("open", [])), ids("open"), len(by.get("confirmed", [])), len(by.get("ruled", [])),
             ", %d unclear%s" % (len(by["unclear"]), ids("unclear")) if by.get("unclear") else ""))
    rc = os.path.join(sdir, "root-cause.md")
    print("root cause: %s" % (shown(root, rc) if os.path.isfile(rc) else "not written yet"))
    if not conf["DEBUG_SCOPE"].split():
        print("experiments: not checked (DEBUG_SCOPE is empty)")
    else:
        left = changed_in_scope(root, conf)
        print("experiments in the tree: %s" % (listed(left) if left else "none"))
    gaps = unbound_steps(root, conf, kind)
    if gaps:
        print("steps with no playbook bindings: %s (%s)" % (", ".join(gaps), shown(root, playbook_path(root, conf))))
    _, unrecorded, simulated = ap.classify(root, KEY, approvals_path(sdir), switch)
    for label, rows in (("not written by debug approve, reject, or close", unrecorded),
                        ("made by a simulated human while the switch is off", simulated)):
        if rows:
            print("not counted, %s: %s" % (label, ", ".join(
                "%s:%d %s" % (shown(root, approvals_path(sdir)), n, p[0]) for n, p in rows)))


@command("status", "debug status [slug]")
def cmd_status(root, words, opts, after):
    if len(words) > 1 or after is not None:
        return bad("status")
    conf = load_conf(root)
    switch = ap.Switch(root)
    if ap.switch_line(root, switch):
        print(ap.switch_line(root, switch))
    if words:
        session = find_session(root, conf, words[0])
        if not session:
            print("debug: no session %s" % words[0][:64], file=sys.stderr)
            return 1
    else:
        session = current_session(root, conf, switch)
    if not session:
        elsewhere = ["%s (%s)" % (s, st.get("branch") or "detached HEAD") for s, d, st in all_sessions(root, conf)
                     if not not_open(root, conf, s, d, st, switch)]
        print("no open session %s%s" % (where(root), "; open elsewhere: " + ", ".join(elsewhere) if elsewhere else
                                        " (start one: %s start <kind> <ref>)" % debug_cmd(root)))
        return 0
    print_status(root, conf, switch, session)
    return 0


# ------------------------------------------------------------------ the check-in

def checkin(root, session, prog):
    """The check-in set debug approve (and close reviewed) runs on a session's root cause: True when
    it passes; otherwise the findings go to stderr."""
    found, blocked = run_checks(Ctx(root, "checkin", [], session))
    if not found and not blocked:
        return True
    for f in blocked + found:
        print(f, file=sys.stderr)
    print("debug: fix these before %s %s" % (prog, session[0]), file=sys.stderr)
    return False


def checkin_questions(root, slug):
    """The Q-ids of the plan's open check-in questions: on T1, at any gate or none, naming debug
    approve <slug>. Other questions on T1 are the agent's and stay open."""
    rc, out, _ = tasks_run(root, ["questions", "--open", "--plan=" + slug])
    if rc != 0:
        return []
    asks = re.compile(r"debug approve %s(?![A-Za-z0-9-])" % re.escape(slug))
    got = []
    for line in out.splitlines():
        m = re.match(r"^(\S+) (Q[0-9]+) \[open\] \(T1(?:, [^)]* gate)?\) (.*)$", line)
        if m and m.group(1) == slug and asks.search(m.group(3)):
            got.append(m.group(2))
    return got


def finish_plan(root, slug, answer, done, tool="human"):
    """The session's plan: answer its open check-in questions, and set T1 done when the session is
    over, or back in progress when it isn't. Credited to the person (AGENTS_TOOL=human) by default;
    tool=None leaves AGENTS_TOOL as the caller set it (debug close, which an agent may run). A
    missing plan changes nothing; a step that fails on a plan that's there is a note on stderr."""
    plan = os.path.join(".agents", "plans", slug)
    if not os.path.isfile(tasks_path(root)) or not os.path.isdir(os.path.join(root, plan)):
        return
    steps = [("answer", slug, q, answer) for q in checkin_questions(root, slug)]
    for step in steps + [("set", slug, "T1", "done" if done else "doing")]:
        err = tasks_cli(root, *step, tool=tool)
        if err:
            print("note: couldn't update the plan %s: %s" % (plan, err), file=sys.stderr)
            return


SESSION_FILES = ("state", "hypotheses.md", "approvals", "root-cause.md")


def open_session(root, conf, switch, slug, verb):
    """(slug, dir, state) of an open session, or the exit code after saying why: 1 when there's no
    such session or it isn't open, 2 when the session dir or one of its files is a symlink."""
    session = find_session(root, conf, slug)
    if not session:
        print("debug: no session %s (%s status lists the open one)" % (slug[:64], debug_cmd(root)), file=sys.stderr)
        return 1
    links = [n for n in ("",) + SESSION_FILES if os.path.islink(os.path.join(session[1], n) if n else session[1])]
    if links:
        print("debug: %s: %s is a symlink, so it won't %s it; make it a plain file" % (
            slug, ", ".join(n or "the session dir" for n in links), verb), file=sys.stderr)
        return 2
    why = not_open(root, conf, session[0], session[1], session[2], switch)
    if why:
        print("debug: %s is %s" % (slug, why), file=sys.stderr)
        return 1
    return session


def judged_session(root, conf, switch, slug, verb):
    """(slug, dir, state) of an open session with a root cause, or the exit code after saying why
    (open_session's, or 1 when there's no root-cause.md to judge)."""
    session = open_session(root, conf, switch, slug, verb)
    if isinstance(session, int):
        return session
    if not os.path.isfile(os.path.join(session[1], "root-cause.md")):
        print("debug: %s has no root-cause.md yet, so there's nothing to %s" % (slug, verb), file=sys.stderr)
        return 1
    return session


def last_entry(sdir):
    """The highest E-number in the session's evidence/, counting a run still in progress (its .log
    is there before its .md); 0 when there's none."""
    try:
        names = os.listdir(os.path.join(sdir, "evidence"))
    except OSError:
        return 0
    return max([int(m.group(1)) for m in (re.fullmatch(r"E-([0-9]+)\.(?:md|log)", x) for x in names) if m] + [0])


def last_step(st, fallback):
    meta = read_kind(st.get("kind", ""))
    return meta["steps"][-1] if meta else fallback


@command("approve", "debug approve <slug>")
def cmd_approve(root, words, opts, after):
    if len(words) != 1 or after is not None:
        return bad("approve")
    switch = ap.Switch(root)
    if ap.refused("debug", "approving a root cause", switch):
        return 2
    conf = load_conf(root)
    session = judged_session(root, conf, switch, words[0], "approve")
    if isinstance(session, int):
        return session
    slug, sdir, st = session
    rc = os.path.join(sdir, "root-cause.md")
    seen = sha(rc)
    if not seen:   # an approval binds the file's hash: nothing to bind when it can't be read
        print("debug: can't read %s, so there's nothing to approve; make it readable" % shown(root, rc),
              file=sys.stderr)
        return 1
    if not checkin(root, session, "approving"):
        return 1
    if sha(rc) != seen:   # the approval binds what the check-in judged
        print("debug: root-cause.md changed while checking; run approve again", file=sys.stderr)
        return 1
    line, sim = ap.new_line(root, "rootcause", slug, seen, switch)
    ap.record(root, KEY, [line])   # first, so a line in approvals is never left without its record
    append_line(approvals_path(sdir), line)
    st.update(status="approved", step=last_step(st, st.get("step", "")))
    write_state(sdir, st)
    finish_plan(root, slug, "approved", True)
    print("approved %s%s" % (slug, ap.SIMULATED if sim else ""))
    return 0


@command("reject", "debug reject <slug> <why...>")
def cmd_reject(root, words, opts, after):
    why = one_line(" ".join(words[1:] + (after or [])))
    if not words or not why:
        return bad("reject")
    switch = ap.Switch(root)
    if ap.refused("debug", "rejecting a root cause", switch):
        return 2
    conf = load_conf(root)
    session = judged_session(root, conf, switch, words[0], "reject")
    if isinstance(session, int):
        return session
    slug, sdir, st = session
    rc = os.path.join(sdir, "root-cause.md")
    line, sim = ap.new_line(root, "reject", slug, sha(rc), switch)
    ap.record(root, KEY, [line])   # first, so a line in approvals is never left without its record
    append_line(approvals_path(sdir), line)
    k = 1
    while os.path.exists(os.path.join(sdir, "root-cause.rejected-%d.md" % k)):
        k += 1
    os.rename(rc, os.path.join(sdir, "root-cause.rejected-%d.md" % k))   # experiments are allowed again
    # Confirmation attempts made so far were for the rejected cause: they no longer confirm.
    st.update(status="open", step="hypothesize", confirm_after=str(max(last_entry(sdir), confirm_after(st))))
    write_state(sdir, st)
    with open(os.path.join(sdir, "hypotheses.md"), "a", encoding="utf-8") as fh:
        fh.write("\n## Check-in: root cause rejected (root-cause.rejected-%d.md)\n- why: %s\n"
                 "- next: look again: more evidence, new or revised hypotheses, then a new root-cause.md\n" % (k, why))
    finish_plan(root, slug, "rejected: " + why, False)
    print("rejected %s%s: root-cause.md is now root-cause.rejected-%d.md, and the reason is in hypotheses.md"
          % (slug, ap.SIMULATED if sim else "", k))
    return 0


@command("close", "debug close <slug> abandoned|duplicate|reviewed [note...]")
def cmd_close(root, words, opts, after):
    """End a session, recorded like an approval (a close line in approvals and the git-dir record),
    so a state file edited by hand closes nothing. abandoned or duplicate: refused while experiments
    are in the tree, and in an agent's shell while the root cause waits on the human (DEBUG_ASK has
    rootcause and root-cause.md exists, or it was rejected or changed since its approval). reviewed:
    a root cause the agent reviewed, only when DEBUG_ASK lacks rootcause, after the check-in set
    passes; it stops counting if rootcause goes back into DEBUG_ASK. Otherwise an agent may run it;
    an agent's abandoned or duplicate close (close-agent) stops counting once the root cause waits on
    the human, so moving root-cause.md aside or emptying DEBUG_ASK for a moment gets it nothing."""
    if len(words) < 2 or words[1] not in CLOSE_REASONS:
        return bad("close")
    reason, note = words[1], one_line(" ".join(words[2:] + (after or [])))
    conf = load_conf(root)
    switch = ap.Switch(root)
    session = open_session(root, conf, switch, words[0], "close")
    if isinstance(session, int):
        return session
    slug, sdir, st = session
    rc = os.path.join(sdir, "root-cause.md")
    asks, waits = "rootcause" in conf["DEBUG_ASK"].split(), False
    if reason == "reviewed":
        if asks:
            print("debug: DEBUG_ASK has rootcause, so the human approves this root cause: ask them to run %s "
                  "approve %s" % (debug_cmd(root), slug), file=sys.stderr)
            return 1
        if not os.path.isfile(rc):
            print("debug: %s has no root-cause.md yet, so there's nothing to review" % slug, file=sys.stderr)
            return 1
        if not checkin(root, session, "closing"):
            return 1
    else:
        waits = (asks and os.path.isfile(rc)) or verdict(root, slug, sdir, switch) in ("rejected", "changed")
        if waits and ap.refused("debug", "closing a session whose root cause waits on you", switch):
            return 2
        left = changed_in_scope(root, conf)
        if left:
            print("debug: revert the experiments before closing %s: %s (if they're the human's own work, ask "
                  "them to commit or stash it)" % (slug, listed(left)), file=sys.stderr)
            return 1
    text = reason + (": " + note if note else "")
    # Marked simulated only when it took the human (the token got it past refused); otherwise an
    # agent's close would reopen once the switch goes off. Any other close in an agent's shell, with
    # the token or without, is a close-agent line, which stops counting if the root cause comes to
    # wait on the human.
    by_agent = ap.agent_shell() is not None and not waits
    kind = "close-agent" if by_agent else "close"
    line, sim = ap.new_line(root, kind, slug, text, switch if waits and ap.agent_shell() else OFF)
    ap.record(root, KEY, [line])   # first, so a line in approvals is never left without its record
    append_line(approvals_path(sdir), line)
    st.update(status="closed", note=text)
    write_state(sdir, st)
    finish_plan(root, slug, "closed: " + text, True, tool=None)
    print("closed %s (%s)%s" % (slug, text, ap.SIMULATED if sim else ""))
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

#!/usr/bin/env python3
"""ai-harness hook adapter. Harness-owned: replaced on upgrade.

Translates each tool's hook protocol into the harness's tool-agnostic checks:

  pre-tool      enforce .agents/policy.conf on shell commands and file reads; before a question
                to the human, check the question ledger for an earlier answer
  post-edit     run .agents/bin/check on edited files and feed findings back to the agent;
                after a question to the human, record the question and answer in the ledger
  session-start remind the agent of questions still waiting on the human; tell Codex when the
                AGENTS.override.md sync wrote in local mode is out of date
  turn-start    snapshot the working tree when a prompt arrives
  stop-gate     run .agents/bin/verify when the agent tries to finish, if this turn changed
                anything; block with the findings until it passes (bounded retries)

Usage: hook.py <event> --tool=<claude|copilot|cursor|codex|gemini>   (JSON payload on stdin)

A crashing hook must never wedge the agent: every path ends in a valid, permissive
response unless policy says otherwise.
"""
import fnmatch
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CACHE = os.path.join(ROOT, ".agents", "cache")
EDIT_TOOLS = {
    "edit", "write", "multiedit", "create", "str_replace_editor", "str_replace_based_edit_tool",
    "apply_patch", "notebookedit", "replace", "write_file",
}
SHELL_TOOLS = {"bash", "shell", "run_shell_command", "powershell", "terminal", "run_in_terminal"}
READ_TOOLS = {"read", "view", "read_file", "grep", "glob", "search"}
QUESTION_TOOLS = {"askuserquestion", "ask_user", "askuser"}


# ---------------------------------------------------------------- config

def load_conf():
    conf = {
        "HOOKS": "policy edit turn questions", "EDIT_BUDGET": "15", "TURN_BUDGET": "300",
        "TURN_MAX_BLOCKS": "3", "POLICY_FAIL_CLOSED": "0",
    }
    path = os.path.join(ROOT, ".agents", "harness.conf")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'^\s*([A-Z_][A-Z0-9_]*)=(?:"([^"]*)"|\'([^\']*)\'|([^\s#]*))', line)
                if m:
                    conf[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
    except OSError:
        pass
    return conf


POLICY = os.path.join(".agents", "policy.conf")


def load_policy():
    """Rules as (kind, pattern, reason, line number in policy.conf)."""
    rules = []
    try:
        with open(os.path.join(ROOT, POLICY), encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                reason = ""
                if " #" in line:
                    line, reason = line.split(" #", 1)
                    line, reason = line.strip(), reason.strip()
                parts = line.split(None, 1)
                if len(parts) == 2:
                    rules.append((parts[0], parts[1].strip(), reason, n))
    except OSError:
        pass
    return rules


# ---------------------------------------------------------------- helpers

def log_event(tool, event, decision, detail=""):
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(os.path.join(CACHE, "hook-events.log"), "a", encoding="utf-8") as fh:
            fh.write("%d\t%s\t%s\t%s\t%s\n" % (time.time(), tool, event, decision, detail[:200].replace("\n", " ")))
    except OSError:
        pass


def as_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"input": value}
        except ValueError:
            return {"input": value}
    return {}


def tool_name(data):
    return str(data.get("tool_name") or data.get("toolName") or "")


def tool_args(data):
    return as_dict(data.get("tool_input", data.get("toolArgs", {})))


def session_key(data):
    sid = str(data.get("session_id") or data.get("sessionId") or data.get("conversation_id") or "default")
    return hashlib.sha1(sid.encode()).hexdigest()[:12]


def harness_cmd(name, *args):
    """Command line for one of the harness's own bash scripts in .agents/bin. Through bash when it's
    executable (what its shebang runs anyway): macOS assesses a newly written executable on its
    first exec, which can take longer than a hook's budget. Not executable: exec'd as before."""
    path = os.path.join(ROOT, ".agents", "bin", name)
    return (["bash", path] if os.access(path, os.X_OK) else [path]) + list(args)


def git(*args):
    try:
        return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return b""


def tree_state():
    h = hashlib.sha1()
    h.update(git("rev-parse", "-q", "--verify", "HEAD"))
    h.update(git("status", "--porcelain=v1", "-z"))
    h.update(git("diff", "--binary", "HEAD"))
    untracked = git("ls-files", "-o", "--exclude-standard")
    if untracked.strip():
        try:
            h.update(subprocess.run(["git", "hash-object", "--stdin-paths"], cwd=ROOT, input=untracked,
                                    capture_output=True, timeout=30).stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    return h.hexdigest()


def read_file(path, default=""):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return default


def write_file(path, text):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass


# A project can be reached through a symlink (macOS /tmp is /private/tmp; a linked home or
# checkout), and a tool may send either spelling, for cwd or for the file. Paths are compared as
# written and with symlinks resolved; when nothing is a symlink the two are the same.

def full_path(path, cwd=None):
    """Absolute, but not normalized: in link/../x the .. applies to where link points, which only
    realpath (in spellings) gets right."""
    p = os.path.expanduser(str(path))
    if not os.path.isabs(p):
        p = os.path.join(cwd or ROOT, p)
    return p


def spellings(p):
    """p (absolute) as written, then with symlinks resolved (realpath resolves the part that
    exists and keeps the rest, so a file about to be written works too)."""
    out = [os.path.abspath(p)]
    real = os.path.realpath(p)
    if real not in out:
        out.append(real)
    return out


def rel_under(p, base):
    """p relative to base, or None when p is outside it."""
    rel = os.path.relpath(p, base)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return None
    return rel


def rel_to_root(path, cwd=None):
    if not path:
        return None
    for p in spellings(full_path(path, cwd)):
        for base in spellings(ROOT):
            rel = rel_under(p, base)
            if rel is not None:
                return rel
    return None


# ---------------------------------------------------------------- policy

def glob_match(rel, pat):
    if fnmatch.fnmatchcase(rel, pat):
        return True
    if pat.startswith("**/") and glob_match(rel, pat[3:]):
        return True
    if "/**/" in pat and glob_match(rel, pat.replace("/**/", "/", 1)):
        return True
    return False


def path_matches(p, pattern, resolve_lead=False):
    """Does p (one absolute spelling) match pattern? ./x is repo-relative, ~/x is home-relative,
    /x absolute, bare x matches a basename. The repo and home match in either spelling. With
    resolve_lead (deny-read only) the pattern's literal start is resolved too, so a deny naming a
    link covers the real path; an allow-read never is, or a link the agent makes at an allowed
    path (fixtures -> ~) would carry the exception to whatever it points at."""
    if pattern.startswith("./"):
        anchor, pat = ROOT, pattern[2:]
    elif pattern.startswith("~/"):
        anchor, pat = os.path.expanduser("~"), pattern[2:]
    elif pattern.startswith("/"):
        anchor, pat = "/", pattern[1:]
    else:
        return fnmatch.fnmatchcase(os.path.basename(p), pattern)
    parts = pat.split("/")
    n = 0
    while n < len(parts) and parts[n] and not any(c in parts[n] for c in "*?["):
        n += 1
    lead = "/".join(parts[:n])  # the literal start, e.g. .ssh in ~/.ssh/**, or all of ~/.netrc
    if resolve_lead:
        bases = spellings(os.path.join(anchor, lead))
    else:
        bases = [os.path.join(b, lead) for b in spellings(anchor)]
    for base in bases:
        rel = rel_under(p, base)
        if rel is None:
            continue
        if lead:
            rel = lead if rel == os.curdir else lead + "/" + rel
        if glob_match(rel, pat):
            return True
    return False


def read_match(path, rules, cwd=None):
    """(message, rule): the deny-read rule a read of path hits, or (None, the allow-read exception
    that let it through), or (None, None). Each spelling of the path (as written, symlinks
    resolved) is checked on its own: a read is blocked when any spelling hits a deny-read that no
    allow-read excepts for that same spelling, so a link to a secret doesn't borrow an exception."""
    if not path:
        return None, None
    excepted = None
    for p in spellings(full_path(path, cwd)):
        deny = next((r for r in rules if r[0] == "deny-read" and path_matches(p, r[1], True)), None)
        if deny is None:
            continue
        allow = next((r for r in rules if r[0] == "allow-read" and path_matches(p, r[1])), None)
        if allow is not None:
            excepted = excepted or allow
            continue
        pat, reason = deny[1], deny[2]
        return "reading %s is blocked by policy (%s)%s" % (path, pat, ": " + reason if reason else ""), deny
    return None, excepted


def read_denied(path, rules, cwd=None):
    return read_match(path, rules, cwd)[0]


def repo_command(seg):
    """A segment run by absolute path from inside the repo, made repo-relative: /repo/.agents/bin/x
    becomes .agents/bin/x, whichever spelling of the repo the path uses."""
    for root in spellings(ROOT):
        if seg.startswith(root + "/"):
            return seg[len(root) + 1:]
    if not seg.startswith("/"):
        return seg
    first, sep, rest = seg.partition(" ")
    rel = rel_to_root(first)  # as written first, then resolved (a link to a repo command counts too)
    if rel and rel != os.curdir:
        return rel + sep + rest
    return seg


def segments(cmd):
    for seg in re.split(r"&&|\|\||[;|\n&]|\$\(|`", cmd):
        seg = " ".join(seg.split())
        # strip leading env assignments and transparent wrappers
        while True:
            m = re.match(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*|command|exec|env|nohup|time|builtin)\s+", seg)
            if not m:
                break
            seg = seg[m.end():]
        seg = re.sub(r"^(?:bash|sh|zsh)\s+(?=[^-\s])", "", seg.strip("() "))
        seg = repo_command(seg)
        while seg.startswith("./"):
            seg = seg[2:]
        if seg:
            yield seg


def shell_match(cmd, rules, cwd=None, parent=None):
    """(message, rule) for what a shell command hits. rule is the policy.conf rule, or None for a
    block from the repo's git workflow (gitflow check-cmd). (None, None) when it's allowed."""
    if not cmd:
        return None, None
    try:
        tokens = shlex.split(cmd, comments=False, posix=True)
    except ValueError:
        tokens = cmd.split()
    for rule in rules:
        kind, pat, reason = rule[:3]
        why = ": " + reason if reason else ""
        if kind == "deny-cmd":
            for seg in segments(cmd):
                if seg == pat or seg.startswith(pat + " "):
                    return "`%s` is blocked by policy%s" % (pat, why), rule
        elif kind == "deny-arg":
            if any(t == pat or t.startswith(pat + "=") for t in tokens):
                return "`%s` is blocked by policy%s" % (pat, why), rule
        elif kind == "deny-regex":
            if parent is not None and cmd in parent:
                continue  # already matched against the whole command, where this argument appears verbatim
            try:
                if re.search(pat, cmd):
                    # The reason says what's blocked; the raw regex only helps when there is none.
                    return ("this command is blocked by policy%s" % why if reason
                            else "this command matches a blocked pattern (%s)" % pat), rule
            except re.error:
                pass
    gitflow = os.path.join(ROOT, ".agents", "bin", "gitflow")
    if os.path.exists(gitflow):
        for seg in segments(cmd):
            if re.match(r"^(git|gh|glab|\.agents/bin/gitflow|gitflow)(\s|$)", seg):
                try:
                    p = subprocess.run(harness_cmd("gitflow", "check-cmd", seg), cwd=ROOT, capture_output=True, text=True, timeout=15)
                except (OSError, subprocess.SubprocessError):
                    continue
                if p.returncode == 2:
                    return (p.stdout.strip() or "blocked by this repo's git workflow") + " (.agents/git.conf)", None
    for t in tokens:
        if " " in t.strip() and t.strip() != cmd.strip():
            inner = shell_match(t, rules, cwd, parent=cmd)  # e.g. bash -c "git push"
            if inner[0]:
                return inner
            continue
        if t.startswith("-") or any(c in t for c in "*?[]$"):
            continue
        t = t.lstrip("<>")
        denied = read_match(t, rules, cwd)
        if denied[0]:
            return denied
    return None, None


def shell_denied(cmd, rules, cwd=None, parent=None):
    return shell_match(cmd, rules, cwd, parent)[0]


def policy_test(argv):
    """`.agents/bin/policy test`: what the pre-tool hook would say about a command or a read,
    using the same matching code. Exit 2 blocked, 0 allowed, 3 usage."""
    usage = ("usage: policy test \"<shell command>\"\n"
             "       policy test --read <path>\n")
    if len(argv) < 2 or argv[0] != "test" or (argv[1] == "--read" and len(argv) != 3) \
            or (argv[1] != "--read" and len(argv) != 2):
        if argv and argv[0] in ("-h", "--help"):
            sys.stdout.write(usage)
            return 0
        sys.stderr.write(usage)
        return 3
    rules = load_policy()
    cwd = os.getcwd()  # the physical path; matching takes either spelling, as for a hook payload
    if argv[1] == "--read":
        msg, rule = read_match(argv[2], rules, cwd)
    else:
        msg, rule = shell_match(argv[1], rules, cwd)
    where = ""
    if rule:
        kind, pat, reason, n = rule
        where = "%s:%d: %s %s%s" % (POLICY, n, kind, pat, "  # " + reason if reason else "")
    if msg:
        print("blocked: " + msg)
        if where:
            print(where)
        if "policy" not in load_conf().get("HOOKS", "").split() or os.environ.get("AGENTS_HOOKS", "on") == "off":
            print("note: the policy hook is off here (HOOKS in .agents/harness.conf, or AGENTS_HOOKS=off), so it isn't enforced")
        return 2
    print("allowed" + (" (an exception: %s)" % where if where else ": no policy rule matches"))
    return 0


def pre_tool(tool, data, conf):
    feats = conf.get("HOOKS", "").split()
    if is_question_tool(data):
        return question_pre(tool, data) if "questions" in feats else allow(tool, "pre-tool")
    if "policy" not in feats:
        return allow(tool, "pre-tool")
    rules = load_policy()
    cwd = data.get("cwd") or ROOT
    kind, value = None, ""
    if tool == "cursor":
        ev = data.get("hook_event_name", "")
        if ev == "beforeShellExecution":
            kind, value = "shell", data.get("command", "")
        elif ev == "beforeReadFile":
            kind, value = "read", data.get("file_path", "")
    else:
        name = tool_name(data).lower()
        args = tool_args(data)
        if name in SHELL_TOOLS:
            kind, value = "shell", args.get("command") or args.get("input") or ""
            # Where the command runs, when the tool says (Codex workdir, Gemini dir_path / directory)
            wd = args.get("workdir") or args.get("dir_path") or args.get("directory")
            if isinstance(wd, str) and wd:
                cwd = os.path.join(cwd, wd)
            if isinstance(value, list):   # an argv list, as in ["bash", "-lc", "<script>"]
                value = " ".join(shlex.quote(str(v)) for v in value)
        elif name in READ_TOOLS:
            kind, value = "read", (args.get("file_path") or args.get("path") or args.get("filePath")
                                   or args.get("absolute_path") or "")
    reason = None
    if kind == "shell":
        reason = shell_denied(str(value), rules, cwd)
    elif kind == "read":
        reason = read_denied(str(value), rules, cwd)
    if reason:
        log_event(tool, "pre-tool", "deny", str(value))
        return deny(tool, reason + ". Ask the human if this is really needed.")
    return allow(tool, "pre-tool")


def deny(tool, reason):
    if tool == "claude":
        sys.stderr.write(reason + "\n")
        return 2
    if tool == "copilot":
        print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": reason}))
        return 2
    if tool == "cursor":
        print(json.dumps({"continue": True, "permission": "deny", "user_message": reason,
                          "agent_message": reason, "userMessage": reason, "agentMessage": reason}))
        return 2
    if tool == "codex":   # the JSON form: open Codex issues report exit 2 not enforced on some versions
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
        return 0
    if tool == "gemini":
        print(json.dumps({"decision": "deny", "reason": reason}))
        return 0
    sys.stderr.write(reason + "\n")
    return 2


def allow(tool, event):
    if tool == "cursor" and event in ("pre-tool", "turn-start"):
        print(json.dumps({"continue": True, "permission": "allow"}))
    return 0


# ---------------------------------------------------------------- edit and stop

def edited_files(tool, data):
    files = []
    if tool == "cursor":
        files.append(data.get("file_path"))
    else:
        if tool_name(data).lower() not in EDIT_TOOLS:
            return []
        args = tool_args(data)
        for k in ("file_path", "path", "notebook_path", "filePath"):
            if args.get(k):
                files.append(args[k])
        for e in args.get("edits") or []:
            if isinstance(e, dict) and e.get("file_path"):
                files.append(e["file_path"])
        # apply_patch: Codex sends the patch text as tool_input.command, others as input or patch
        patch = args.get("patch") or args.get("input") or args.get("command") or ""
        if isinstance(patch, str):
            files += re.findall(r"^\*\*\* (?:Update|Add) File: (.+?)\s*$", patch, re.M)
            files += re.findall(r"^\*\*\* Move to: (.+?)\s*$", patch, re.M)
            files += re.findall(r"^\+\+\+ b/(.+)$", patch, re.M)
    out = []
    for f in files:
        r = rel_to_root(f, data.get("cwd"))
        if r and os.path.isfile(os.path.join(ROOT, r)) and not r.startswith(".agents" + os.sep + "cache"):
            if r not in out:
                out.append(r)
    return out


def run_tool(args, budget):
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=budget)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "out of budget"
    except OSError as e:
        return 3, str(e)


def post_edit(tool, data, conf):
    feats = conf.get("HOOKS", "").split()
    if is_question_tool(data):
        return question_post(tool, data) if "questions" in feats else 0
    if "edit" not in feats:
        return 0
    files = edited_files(tool, data)
    if not files:
        return 0
    budget = int(conf.get("EDIT_BUDGET", "15") or 15) + 10
    rc, out = run_tool(harness_cmd("check", *files), budget)
    log_event(tool, "post-edit", str(rc), " ".join(files))
    if rc not in (1, 2):
        return 0
    msg = out + "\nFix these before moving on."
    if tool == "claude":
        sys.stderr.write(msg + "\n")
        return 2
    if tool == "copilot":
        print(json.dumps({"additionalContext": msg}))
        return 0
    if tool in ("codex", "gemini"):   # added to what the agent sees; the edit and the tool's own output stay
        ev = "PostToolUse" if tool == "codex" else "AfterTool"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": ev, "additionalContext": msg}}))
        return 0
    return 0  # cursor ignores afterFileEdit output; the stop gate reports instead


def turn_start(tool, data, conf):
    key = session_key(data)
    # Codex and Gemini continue a blocked stop as a new prompt, which may fire this hook again; a fresh
    # snapshot then would let the next stop through with verify still failing. The counter goes back
    # to 0 on a pass, a pause, or giving up, so the next real prompt snapshots as usual.
    if tool in ("codex", "gemini") and int(read_file(os.path.join(CACHE, "stop-" + key), "0") or 0) > 0:
        return allow(tool, "turn-start")
    write_file(os.path.join(CACHE, "turn-" + key), tree_state())
    return allow(tool, "turn-start")


def stop_gate(tool, data, conf):
    key = session_key(data)
    counter = os.path.join(CACHE, "stop-" + key)
    max_blocks = int(conf.get("TURN_MAX_BLOCKS", "3") or 3)

    # Only gate turns that changed the tree. Without a snapshot, gate any dirty tree.
    before = read_file(os.path.join(CACHE, "turn-" + key))
    now = tree_state()
    dirty = bool(git("status", "--porcelain").strip())
    if (before and before == now) or (not before and not dirty):
        write_file(counter, "0")
        return finish_allow(tool)

    # A task paused for the human (tasks ask) is a legitimate place to stop, even mid red phase.
    waiting = paused_for_human()
    if waiting:
        write_file(counter, "0")
        log_event(tool, "stop-gate", "paused", waiting)
        return finish_allow(tool, "Paused for your input: open questions in %s" % waiting)

    blocks = int(read_file(counter, "0") or 0)
    if tool == "cursor" and isinstance(data.get("loop_count"), int):
        blocks = max(blocks, data["loop_count"])
    if blocks >= max_blocks:
        write_file(counter, "0")
        log_event(tool, "stop-gate", "give-up")
        return finish_allow(tool, "Stop gate: verify still failing after %d attempts. Handing back; "
                                  "run .agents/bin/verify to see what is left." % blocks)

    budget = int(conf.get("TURN_BUDGET", "300") or 300) + 30
    rc, out = run_tool(harness_cmd("verify", "--tier=turn"), budget)
    log_event(tool, "stop-gate", str(rc))
    if rc == 0:
        write_file(counter, "0")
        write_file(os.path.join(CACHE, "turn-" + key), tree_state())
        return finish_allow(tool)
    if rc not in (1, 2):
        write_file(counter, "0")
        return finish_allow(tool, "Stop gate could not verify (exit %d): %s" % (rc, out.splitlines()[-1] if out else ""))

    write_file(counter, str(blocks + 1))
    reason = ("The stop gate ran .agents/bin/verify and it failed. Fix these, then finish:\n\n%s\n\n"
              "(Attempt %d of %d. If a finding is wrong or out of scope, say so plainly instead of "
              "suppressing it.)" % (out, blocks + 1, max_blocks))
    if tool == "claude":
        sys.stderr.write(reason + "\n")
        return 2
    if tool == "copilot":
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    if tool == "cursor":
        print(json.dumps({"followup_message": reason}))
        return 0
    if tool in ("codex", "gemini"):   # Stop / AfterAgent: keep working, with the reason as the next prompt
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    sys.stderr.write(reason + "\n")
    return 2


# ---------------------------------------------------------------- question ledger

def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def open_questions():
    """[(slug, question row)] for every open question in every plan's ledger."""
    import glob
    out = []
    for qj in sorted(glob.glob(os.path.join(ROOT, ".agents", "plans", "*", "questions.json"))):
        for q in load_json(qj) or []:
            if isinstance(q, dict) and q.get("status") == "open":
                out.append((os.path.basename(os.path.dirname(qj)), q))
    return out


def paused_for_human():
    """A plan whose blocked (or done, awaiting sign-off) task has an open question (tasks ask):
    a legitimate place to stop."""
    clean = None
    for slug, q in open_questions():
        tasks = load_json(os.path.join(ROOT, ".agents", "plans", slug, "tasks.json")) or []
        # A done task awaiting sign-off pauses only while nothing is uncommitted (new files included,
        # the ledgers aside); new edits get checked.
        if clean is None and any(isinstance(t, dict) and t.get("status") == "done" for t in tasks):
            clean = not git("status", "--porcelain", "--", ".", ":(exclude).agents/plans").strip()
        if any(isinstance(t, dict) and t.get("id") == q.get("task") and (
                t.get("status") == "blocked" or (t.get("status") == "done" and clean)) for t in tasks):
            return ".agents/plans/%s/questions.json" % slug
    return ""


def is_question_tool(data):
    return tool_name(data).lower() in QUESTION_TOOLS


def question_texts(args):
    out = []
    qs = args.get("questions")
    if isinstance(qs, list):
        for q in qs:
            if isinstance(q, dict) and isinstance(q.get("question"), str):
                out.append(q["question"])
            elif isinstance(q, str):
                out.append(q)
    for k in ("question", "prompt", "message"):
        if isinstance(args.get(k), str):
            out.append(args[k])
    return [q.strip() for q in out if q.strip()]


def answer_texts(data, questions):
    resp = data.get("tool_response", data.get("tool_result", data.get("toolResult")))
    if isinstance(resp, dict) and isinstance(resp.get("answers"), dict):
        answers = resp["answers"]
        return [str(answers.get(q, "")) for q in questions]
    text = resp
    if isinstance(resp, dict):
        text = (resp.get("text_result_for_llm") or resp.get("textResultForLlm") or resp.get("content")
                or resp.get("result") or "")
    if isinstance(text, list):
        text = " ".join(t.get("text", "") if isinstance(t, dict) else str(t) for t in text)
    text = " ".join(str(text or "").split())[:500]
    return [text] * len(questions)


def tasks_cli(*args):
    try:
        p = subprocess.run(harness_cmd("tasks", *args), cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 3, ""


def question_pre(tool, data):
    """Before the agent asks the human: surface an earlier answer once per session."""
    seen_file = os.path.join(CACHE, "asked-" + session_key(data))
    seen = set(read_file(seen_file).split())
    hits = []
    for q in question_texts(tool_args(data)):
        h = hashlib.sha1(q.lower().encode()).hexdigest()[:12]
        if h in seen:
            continue  # already shown the earlier answer; asking again is the agent's call
        rc, out = tasks_cli("similar", q)
        best = [l.split("\t") for l in out.splitlines() if l.strip()]
        best = [b for b in best if len(b) >= 6 and float(b[0]) >= 0.7]
        if best:
            seen.add(h)
            b = best[0]
            hits.append("- \"%s\" was answered in %s %s (%s): %s" % (b[4], b[1], b[2], b[3], b[5]))
    if not hits:
        return allow(tool, "pre-tool")
    write_file(seen_file, "\n".join(sorted(seen)))
    log_event(tool, "pre-tool", "question-dedupe", " ".join(hits)[:200])
    return deny(tool, "The question ledger already has an answer:\n%s\nUse it. If something changed since, "
                      "ask again and say what changed." % "\n".join(hits))


def question_post(tool, data):
    """After the agent asked the human: record question and answer in the ledger."""
    qs = question_texts(tool_args(data))
    for q, a in zip(qs, answer_texts(data, qs)):
        tasks_cli("record", "--source=hook-" + tool, q, a)
    if qs:
        log_event(tool, "post-edit", "question-recorded", str(len(qs)))
    return 0


def codex_copy_stale(conf):
    """Local mode: sync's AGENTS.override.md (lib/rules_copies.sh) no longer holds AGENTS.md then
    .agents/AGENTS.local.md as they are now, say after a pull. Codex reads it in place of AGENTS.md."""
    def text(rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8", newline="") as f:
            t = f.read()
        return t + "\n" if t and not t.endswith("\n") else t
    path = os.path.join(ROOT, "AGENTS.override.md")
    if conf.get("HARNESS_MODE") != "local" or os.path.islink(path) or not os.path.isfile(path):
        return False
    try:
        copy = text("AGENTS.override.md")
        if not copy.startswith("<!-- ai-harness: generated by .agents/bin/sync"):
            return False
        want = "\n" + text("AGENTS.md") + "\n" + text(os.path.join(".agents", "AGENTS.local.md"))
    except (OSError, ValueError):
        return False
    if copy.split("\n", 1)[-1] == want:
        return False
    # A committed copy isn't sync's to rewrite (sync warns about it instead).
    return not git("ls-files", "--", "AGENTS.override.md").strip()


def session_start(tool, data, conf):
    waiting = open_questions()
    lines = []
    if waiting:
        lines.append("Questions still waiting on the human (question ledger):")
        for slug, q in waiting[:5]:
            lines.append("- %s %s%s: %s" % (slug, q.get("id"), " (%s)" % q["task"] if q.get("task") else "", q.get("question")))
        if len(waiting) > 5:
            lines.append("- ...and %d more: .agents/bin/tasks questions --open" % (len(waiting) - 5))
        lines.append("Ask the human about these before continuing that work. Record answers with "
                     ".agents/bin/tasks answer <slug> <Q-id> \"<answer>\".")
    if tool == "codex" and codex_copy_stale(conf):
        lines.append("AGENTS.override.md, which you read in place of AGENTS.md, is out of date with AGENTS.md "
                     "or .agents/AGENTS.local.md. Follow those two files, and run .agents/bin/sync to refresh it.")
    if not lines:
        return 0
    text = "\n".join(lines)
    if tool == "copilot":
        print(json.dumps({"additionalContext": text}))
    elif tool in ("codex", "gemini"):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    else:
        print(text)
    return 0


def finish_allow(tool, note=""):
    if note:
        if tool in ("claude", "codex", "gemini"):
            print(json.dumps({"systemMessage": note}))
        else:
            sys.stderr.write(note + "\n")
    if tool == "cursor":
        print(json.dumps({}))
    return 0


# ---------------------------------------------------------------- main

# pre-tool and post-edit check their own features (policy/edit vs questions) per tool call.
EVENTS = {"pre-tool": (None, pre_tool), "post-edit": (None, post_edit),
          "session-start": ("questions", session_start),
          "turn-start": ("turn", turn_start), "stop-gate": ("turn", stop_gate)}


def main(argv):
    tool = "claude"
    for a in argv[2:]:
        if a.startswith("--tool="):
            tool = a.split("=", 1)[1]
    if tool != "gemini":
        return run_event(argv, tool)
    # Gemini CLI parses stdout as one JSON object: exactly one goes out, {} when there's nothing to say.
    out, real = io.StringIO(), sys.stdout
    sys.stdout = out
    try:
        rc = run_event(argv, tool)
    finally:
        sys.stdout = real
    text = out.getvalue().strip()
    print(text if text else "{}")
    return rc


def run_event(argv, tool):
    event = argv[1] if len(argv) > 1 else ""
    conf = load_conf()
    if event not in EVENTS:
        sys.stderr.write("hook.py: unknown event %r\n" % event)
        return 0
    feature, handler = EVENTS[event]
    if os.environ.get("AGENTS_HOOKS", "on") == "off" or (feature and feature not in conf.get("HOOKS", "").split()):
        return allow(tool, event)
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        data = {}
    try:
        return handler(tool, data, conf)
    except Exception as e:  # never wedge the agent
        log_event(tool, event, "error", repr(e))
        if event == "pre-tool" and conf.get("POLICY_FAIL_CLOSED") == "1":
            return deny(tool, "policy hook failed (%s); failing closed" % e)
        return allow(tool, event)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

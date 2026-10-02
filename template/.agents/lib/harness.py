#!/usr/bin/env python3
"""ai-harness adapter renderer and skills lock. Harness-owned: replaced on upgrade.

Called by .agents/bin/sync. Renders the harness hooks and policy into each tool's native
config, merging into files the project may already own. Harness entries are recognized by
their command path (.agents/hooks/) or, for permission rules, by .agents/generated.lock, so a
re-render replaces exactly what the harness added and never touches anything else.

  harness.py render [--check]                        write (or just diff) adapter configs
  harness.py resolve <kind> [name]                   what the libraries resolve to (.agents/lib/libraries.sh)
  harness.py lock-skill <name> <source> <ref>        pin a third-party skill by content hash
  harness.py check-skills [<skill-set>]              verify pinned skills are unchanged
  harness.py unshare                                 strip harness entries from tracked configs
  harness.py agents [--check] <agent-set> <renders-out>     render library agents for each tool
                                                     (0 ok, 1 drift, 5 agent-file errors, 3 failed)
  harness.py mcp [--check] <mcp-set> <files-out> [<agent-set>]   merge library MCP servers into each tool's config
                                                     (0 ok, 1 drift, 5 server-file or config errors, 3 failed)
"""
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

sys.dont_write_bytecode = True   # it imports agents_render: no __pycache__ left in the project

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LOCK = os.path.join(ROOT, ".agents", "generated.lock")
SKILLS_LOCK = os.path.join(ROOT, ".agents", "skills.lock")
PERSONAL = ("personal", "personal-listed")   # library sources that are one person's own
LIBRARIES = os.path.join(ROOT, ".agents", "lib", "libraries.sh")


def load_conf():
    conf = {"ADAPTERS": "claude", "HOOKS": "policy edit turn questions", "EDIT_BUDGET": "15", "TURN_BUDGET": "300",
            "HARNESS_MODE": "team"}
    try:
        with open(os.path.join(ROOT, ".agents", "harness.conf"), encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'^\s*([A-Z_][A-Z0-9_]*)=(?:"([^"]*)"|\'([^\']*)\'|([^\s#]*))', line)
                if m:
                    conf[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
    except OSError:
        pass
    return conf


def tracked(rel):
    """True if the project's git index has this path (local mode never modifies such files)."""
    try:
        return subprocess.run(["git", "-C", ROOT, "ls-files", "--error-unmatch", "--", rel],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def resolve(kind, name=None, command="resolve"):
    """What the libraries resolve to, as (name, path, library) tuples. The resolver is
    .agents/lib/libraries.sh (bash, so verify and git hooks work without python3); this asks it.
    command="items" lists every library's copy instead of the winners."""
    cmd = ["bash", LIBRARIES, command, kind] + ([name] if name else [])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, AGENTS_ROOT=ROOT)).stdout
    except OSError:
        return []
    return [tuple(line.split("\t")) for line in out.splitlines() if line.count("\t") == 2]


def load_policy():
    rules = []
    try:
        with open(os.path.join(ROOT, ".agents", "policy.conf"), encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                reason = ""
                if " #" in line:
                    line, reason = [x.strip() for x in line.split(" #", 1)]
                parts = line.split(None, 1)
                if len(parts) == 2:
                    rules.append((parts[0], parts[1].strip(), reason))
    except OSError:
        pass
    return rules


GIT_STEP_RULES = {
    "push": [["git", "push"], [".agents/bin/gitflow", "push"]],
    "pr": [["gh", "pr", "create"], ["glab", "mr", "create"], [".agents/bin/gitflow", "pr"]],
    "merge": [["gh", "pr", "merge"], ["glab", "mr", "merge"], [".agents/bin/gitflow", "merge"]],
}


def git_denied_prefixes():
    """Command prefixes for git steps the project's own git.conf keeps from agents. Only an
    explicit GIT_AGENT_MAY in .agents/git.conf counts: personal settings (~/.config) never land
    in committed files, so CI and every developer render the same thing. Everything else about
    the git workflow is enforced by the policy hook and the git hooks."""
    may = None
    try:
        with open(os.path.join(ROOT, ".agents", "git.conf"), encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'^\s*GIT_AGENT_MAY=(?:"([^"]*)"|\'([^\']*)\'|(\S*))', line)
                if m:
                    may = (m.group(1) or m.group(2) or m.group(3) or "").split()
    except OSError:
        pass
    out = [["glab", "mr", "approve"]]
    if may is not None:
        for step, prefixes in GIT_STEP_RULES.items():
            if step not in may:
                out += prefixes
    return out


def ours(cmd):
    return isinstance(cmd, str) and ".agents/hooks/" in cmd


def hook_cmd(tool, event):
    if tool == "claude":
        return '"$CLAUDE_PROJECT_DIR"/.agents/hooks/run %s --tool=claude' % event
    return ".agents/hooks/run %s --tool=%s" % (event, tool)


def budgets(conf):
    def num(k, d):
        try:
            return int(conf.get(k, d))
        except ValueError:
            return d
    return num("EDIT_BUDGET", 15) + 20, num("TURN_BUDGET", 300) + 60


# ------------------------------------------------------------------ per-tool renderers

def claude_render(existing, conf, rules, enabled, prev_deny):
    feats = set(conf.get("HOOKS", "").split()) if enabled else set()
    edit_t, turn_t = budgets(conf)
    want = {}

    def group(event, timeout, matcher=None):
        g = {"hooks": [{"type": "command", "command": hook_cmd("claude", event), "timeout": timeout}]}
        if matcher:
            g = {"matcher": matcher, **g}
        return g
    pre = (["Bash|Read|Grep|Glob"] if "policy" in feats else []) + (["AskUserQuestion"] if "questions" in feats else [])
    post = (["Edit|Write|MultiEdit|NotebookEdit"] if "edit" in feats else []) + (["AskUserQuestion"] if "questions" in feats else [])
    if pre:
        want["PreToolUse"] = [group("pre-tool", 10, "|".join(pre))]
    if post:
        want["PostToolUse"] = [group("post-edit", edit_t, "|".join(post))]
    if "questions" in feats:
        want["SessionStart"] = [group("session-start", 10)]
    if "turn" in feats:
        want["UserPromptSubmit"] = [group("turn-start", 10)]
        want["Stop"] = [group("stop-gate", turn_t)]
    deny = []
    if enabled:
        deny += ["Bash(%s:*)" % " ".join(p) for p in git_denied_prefixes()]
        allows = [p for k, p, _ in rules if k == "allow-read"]
        for kind, pat, _ in rules:
            if kind == "deny-cmd":
                deny.append("Bash(%s:*)" % pat)
            elif kind == "deny-read":
                # Claude's deny rules can't carve out exceptions, so a deny that an allow-read
                # overlaps (./**/.env* vs ./**/.env.example) is left to the pre-tool hook.
                if not any(fnmatch.fnmatchcase(a, pat) for a in allows):
                    deny.append("Read(%s)" % pat)

    obj = dict(existing or {})
    hooks = {}
    for name, groups in (obj.get("hooks") or {}).items():
        kept = []
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, dict):
                kept.append(g)
                continue
            inner = g.get("hooks") or []
            left = [h for h in inner if not (isinstance(h, dict) and ours(h.get("command")))]
            if left:
                kept.append(dict(g, hooks=left))
            elif not inner:
                kept.append(g)
        if kept:
            hooks[name] = kept
    for name, groups in want.items():
        hooks.setdefault(name, []).extend(groups)
    if hooks:
        obj["hooks"] = hooks
    else:
        obj.pop("hooks", None)

    perms = dict(obj.get("permissions") or {})
    rules_now = [d for d in perms.get("deny", []) if d not in prev_deny]
    rules_now += [d for d in deny if d not in rules_now]
    if rules_now:
        perms["deny"] = rules_now
    else:
        perms.pop("deny", None)
    if perms:
        obj["permissions"] = perms
    else:
        obj.pop("permissions", None)
    return obj, deny


def copilot_render(conf, enabled):
    if not enabled:
        return None
    feats = set(conf.get("HOOKS", "").split())
    edit_t, turn_t = budgets(conf)

    def entry(event, timeout):
        return [{"type": "command", "bash": hook_cmd("copilot", event), "cwd": ".", "timeoutSec": timeout}]
    hooks = {}
    if "policy" in feats or "questions" in feats:
        hooks["PreToolUse"] = entry("pre-tool", 10)
    if "edit" in feats or "questions" in feats:
        hooks["PostToolUse"] = entry("post-edit", edit_t)
    if "questions" in feats:
        hooks["SessionStart"] = entry("session-start", 10)
    if "turn" in feats:
        hooks["UserPromptSubmit"] = entry("turn-start", 10)
        hooks["Stop"] = entry("stop-gate", turn_t)
    return {"version": 1, "hooks": hooks} if hooks else None


def cursor_render(existing, conf, enabled):
    feats = set(conf.get("HOOKS", "").split()) if enabled else set()
    edit_t, turn_t = budgets(conf)

    def entry(event, timeout):
        return [{"command": hook_cmd("cursor", event), "timeout": timeout}]
    want = {}
    if "policy" in feats:
        want["beforeShellExecution"] = entry("pre-tool", 10)
        want["beforeReadFile"] = entry("pre-tool", 10)
    if "edit" in feats:
        want["afterFileEdit"] = entry("post-edit", edit_t)
    if "turn" in feats:
        want["beforeSubmitPrompt"] = entry("turn-start", 10)
        want["stop"] = entry("stop-gate", turn_t)
    obj = dict(existing or {})
    hooks = {}
    for name, entries in (obj.get("hooks") or {}).items():
        kept = [e for e in (entries or []) if not (isinstance(e, dict) and ours(e.get("command")))]
        if kept:
            hooks[name] = kept
    for name, entries in want.items():
        hooks.setdefault(name, []).extend(entries)
    if not hooks and set(obj) <= {"version", "hooks"}:
        return None
    obj.setdefault("version", 1)
    obj["hooks"] = hooks
    return obj


def gemini_render(existing, enabled):
    obj = dict(existing or {})
    if not enabled:
        return obj if existing is not None else None
    ctx = dict(obj.get("context") or {})
    fn = ctx.get("fileName")
    names = [fn] if isinstance(fn, str) else list(fn or [])
    if "AGENTS.md" not in names:
        names.insert(0, "AGENTS.md")
    if "GEMINI.md" not in names:
        names.append("GEMINI.md")
    ctx["fileName"] = names
    obj["context"] = ctx
    return obj


def codex_rules(rules, enabled):
    if not enabled:
        return None
    lines = [
        "# Generated by ai-harness from .agents/policy.conf. Don't edit: change policy.conf,",
        "# then run .agents/bin/sync. Codex loads project rules only for trusted projects.",
        "",
    ]
    n = 0
    git_rules = [("deny-cmd", " ".join(p), "blocked by this repo's git workflow (.agents/git.conf)")
                 for p in git_denied_prefixes()]
    for kind, pat, reason in list(rules) + git_rules:
        if kind != "deny-cmd":
            continue
        try:
            toks = shlex.split(pat)
        except ValueError:
            continue
        n += 1
        lines += ["prefix_rule(",
                  "    pattern = [%s]," % ", ".join(json.dumps(t) for t in toks),
                  '    decision = "forbidden",',
                  "    justification = %s," % json.dumps(reason or "blocked by ai-harness policy"),
                  ")", ""]
    return "\n".join(lines) if n else None


# ------------------------------------------------------------------ io

def read_json(path):
    """Returns (obj, error). obj is None when the file doesn't exist."""
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        return (json.loads(text) if text.strip() else {}), None
    except (OSError, ValueError) as e:
        return None, str(e)


def dump_json(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def render(check):
    conf = load_conf()
    rules = load_policy()
    adapters = set(conf.get("ADAPTERS", "").split())
    lock, _ = read_json(LOCK)
    lock = lock or {}
    prev_deny = lock.get("claude_deny", [])
    drift, errors, wrote = [], [], []
    new_lock = dict(lock)   # keys other commands own (agents) pass through
    new_lock["claude_deny"] = prev_deny
    local = conf.get("HARNESS_MODE", "team") == "local"
    notes = []

    def skip_tracked(rel):
        if local and tracked(rel):
            notes.append("%s is tracked by the project; local mode leaves it alone (that adapter is off in this "
                          "clone)" % rel)
            return True
        return False

    def settle_json(rel, new_obj, old_obj):
        path = os.path.join(ROOT, rel)
        if new_obj is None:
            if old_obj is not None and os.path.exists(path):
                if check:
                    drift.append(rel + " (remove)")
                else:
                    os.remove(path)
                    wrote.append("removed " + rel)
            return
        if old_obj == new_obj:
            return
        if check:
            drift.append(rel)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(dump_json(new_obj))
        wrote.append("wrote " + rel)

    # Claude Code: shared settings.json in team mode, personal settings.local.json in local mode.
    # The other file keeps no harness entries, unless it's tracked in local mode (never touched).
    rel = os.path.join(".claude", "settings.local.json" if local else "settings.json")
    other = os.path.join(".claude", "settings.json" if local else "settings.local.json")
    if not skip_tracked(rel):
        old, err = read_json(os.path.join(ROOT, rel))
        if err:
            errors.append("%s is not valid JSON (%s); fix or remove it, then re-run sync" % (rel, err))
        elif old is not None or "claude" in adapters:
            new, deny = claude_render(old, conf, rules, "claude" in adapters, prev_deny)
            if old is None and not new:
                new = None
            settle_json(rel, new, old)
            new_lock["claude_deny"] = deny
    if not (local and tracked(other)):
        o_old, o_err = read_json(os.path.join(ROOT, other))
        if o_old is not None and not o_err:
            o_new, _ = claude_render(o_old, conf, rules, False, prev_deny)
            # Only a file that held harness entries changes; one left empty by stripping goes.
            if o_new != o_old:
                settle_json(other, o_new or None, o_old)
        elif o_err:
            errors.append("%s is not valid JSON (%s); fix or remove it, then re-run sync" % (other, o_err))

    # GitHub Copilot (CLI, cloud agent, VS Code): a file the harness owns outright
    rel = os.path.join(".github", "hooks", "harness.json")
    if not skip_tracked(rel):
        old, err = read_json(os.path.join(ROOT, rel))
        settle_json(rel, copilot_render(conf, "copilot" in adapters), old if not err else {"invalid": True})

    # Cursor
    rel = os.path.join(".cursor", "hooks.json")
    if not skip_tracked(rel):
        old, err = read_json(os.path.join(ROOT, rel))
        if err:
            errors.append("%s is not valid JSON (%s); fix or remove it, then re-run sync" % (rel, err))
        elif old is not None or "cursor" in adapters:
            settle_json(rel, cursor_render(old, conf, "cursor" in adapters), old)

    # Gemini CLI (context file only; hooks not rendered yet)
    rel = os.path.join(".gemini", "settings.json")
    if not skip_tracked(rel):
        old, err = read_json(os.path.join(ROOT, rel))
        if err:
            errors.append("%s is not valid JSON (%s); fix or remove it, then re-run sync" % (rel, err))
        elif old is not None or "gemini" in adapters:
            settle_json(rel, gemini_render(old, "gemini" in adapters), old)

    # Codex execpolicy rules: a file the harness owns outright
    rel = os.path.join(".codex", "rules", "harness.rules")
    if not skip_tracked(rel):
        path = os.path.join(ROOT, rel)
        want = codex_rules(rules, "codex" in adapters)
        have = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if want is None and have is not None:
            if check:
                drift.append(rel + " (remove)")
            else:
                os.remove(path)
                wrote.append("removed " + rel)
        elif want is not None and want != have:
            if check:
                drift.append(rel)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(want)
                wrote.append("wrote " + rel)

    # Lock
    if new_lock != lock:
        if check:
            drift.append(os.path.relpath(LOCK, ROOT))
        else:
            with open(LOCK, "w", encoding="utf-8") as fh:
                fh.write(dump_json(new_lock))
            wrote.append("wrote " + os.path.relpath(LOCK, ROOT))

    for w in wrote:
        print("sync: " + w)
    for d in drift:
        print("sync: out of date: " + d)
    if not check:
        for n in notes:
            print("sync: warning: " + n, file=sys.stderr)
    for e in errors:
        print("sync: error: " + e, file=sys.stderr)
    if errors:
        return 2
    return 1 if drift else 0


def unshare():
    """install.sh --local on a team install: harness entries leave tracked shared configs.
    Prints "untrack <path>" when nothing but harness entries remain, else strips in place."""
    conf = load_conf()
    rules = load_policy()
    lock, _ = read_json(LOCK)
    prev = (lock or {}).get("claude_deny", [])
    for rel in (os.path.join(".claude", "settings.json"), os.path.join(".cursor", "hooks.json")):
        path = os.path.join(ROOT, rel)
        if not tracked(rel):
            continue
        old, err = read_json(path)
        if old is None or err:
            continue
        if rel.startswith(".claude"):
            new, _ = claude_render(old, conf, rules, False, prev)
        else:
            new = cursor_render(old, conf, False)
        if not new:
            print("untrack " + rel)
        elif new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(dump_json(new))
            print("stripped " + rel)
    for rel in (os.path.join(".github", "hooks", "harness.json"), os.path.join(".codex", "rules", "harness.rules")):
        if tracked(rel):
            print("untrack " + rel)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import agents_render
    for rel in agents_render.marked_renders(ROOT):
        if tracked(rel) and not os.path.islink(os.path.join(ROOT, rel)):
            print("untrack " + rel)
    return 0


def agents(check, set_path, out_path):
    """Library agents, rendered per tool (.agents/lib/agents_render.py). Writes "personal<TAB>path" /
    "shared<TAB>path" lines to out_path for sync's exclude block."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import agents_render
    conf = load_conf()
    rows = []
    try:
        with open(set_path, encoding="utf-8") as fh:
            rows = [tuple(l.rstrip("\n").split("\t")) for l in fh if l.count("\t") == 2]
    except OSError:
        pass
    lock, _ = read_json(LOCK)
    lock = lock or {}
    team = conf.get("HARNESS_MODE", "team") != "local"
    res = agents_render.sync_agents(ROOT, conf, rows, check, tracked, lock.get("agents", {}), team,
                                    os.environ.get("AGENTS_LIBRARY_MISSING", "0") == "1")
    with open(out_path, "w", encoding="utf-8") as fh:
        for rel, personal in res["renders"]:
            fh.write("%s\t%s\n" % ("personal" if personal else "shared", rel))
    new_lock = dict(lock)
    if res["lock"]:
        new_lock["agents"] = res["lock"]
    else:
        new_lock.pop("agents", None)
    if new_lock != lock:
        if check:
            res["drift"].append(os.path.relpath(LOCK, ROOT))
        else:
            with open(LOCK, "w", encoding="utf-8") as fh:
                fh.write(dump_json(new_lock))
            res["wrote"].append("wrote " + os.path.relpath(LOCK, ROOT))
    for w in res["wrote"]:
        print("sync: " + w)
    for d in res["drift"]:
        print("sync: out of date: " + d)
    for w in res["warnings"]:
        print("sync: warning: " + w, file=sys.stderr)
    for e in res["errors"]:
        print("sync: error: " + e, file=sys.stderr)
    if res["errors"]:
        return 5   # sync can't fix a broken agent file; drift lines above still count
    return 1 if res["drift"] else 0


def read_rows(path):
    """name<TAB>path<TAB>library rows from one of sync's set files."""
    try:
        with open(path, encoding="utf-8") as fh:
            return [tuple(l.rstrip("\n").split("\t")) for l in fh if l.count("\t") == 2]
    except OSError:
        return []


def mcp_lock(lock):
    """The lock's mcp key, {file: [names]}, with anything malformed left out."""
    old = (lock or {}).get("mcp")
    if not isinstance(old, dict):
        return {}
    return dict((k, [n for n in v if isinstance(n, str)]) for k, v in old.items() if isinstance(v, list))


def mcp(check, set_path, out_path, agent_set=None):
    """Library MCP servers, merged into each tool's config (.agents/lib/mcp_render.py). Writes the
    files that hold sync's entries to out_path, one per line, for sync's exclude block. With the
    agent set, warns about an agent's mcp: naming a server no library or config here has."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mcp_render
    conf = load_conf()
    rows = read_rows(set_path)
    lock, _ = read_json(LOCK)
    lock = lock or {}
    team = conf.get("HARNESS_MODE", "team") != "local"
    missing = os.environ.get("AGENTS_LIBRARY_MISSING", "0") == "1"
    res = mcp_render.sync_mcp(ROOT, conf, rows, check, tracked, mcp_lock(lock), team, missing)
    if agent_set and not missing:
        import agents_render
        known = set(r[0] for r in rows) | res["seen"]
        for name, path, _ in read_rows(agent_set):
            agent = agents_render.Agent(name, path, agents_render.in_repo(ROOT, path) or path)
            for s in [] if agent.errors else (agent.mcp or []):
                if s not in known:
                    res["warnings"].append("%s: mcp: no library has a server named '%s', and no MCP config here "
                                           "lists it (add mcp/%s.json to a library, or ignore this if it's in "
                                           "your own tool config)" % (agent.shown, s, s))
    with open(out_path, "w", encoding="utf-8") as fh:
        for rel in res["files"]:
            fh.write(rel + "\n")
    new_lock = dict(lock)
    if res["lock"]:
        new_lock["mcp"] = res["lock"]
    else:
        new_lock.pop("mcp", None)
    if new_lock != lock:
        if check:
            res["drift"].append(os.path.relpath(LOCK, ROOT))
        else:
            with open(LOCK, "w", encoding="utf-8") as fh:
                fh.write(dump_json(new_lock))
            res["wrote"].append("wrote " + os.path.relpath(LOCK, ROOT))
    for w in res["wrote"]:
        print("sync: " + w)
    for d in res["drift"]:
        print("sync: out of date: " + d)
    for w in res["warnings"]:
        print("sync: warning: " + w, file=sys.stderr)
    for e in res["errors"]:
        print("sync: error: " + e, file=sys.stderr)
    if res["errors"]:
        return 5   # a broken server file or config sync can't fix; drift lines above still count
    return 1 if res["drift"] else 0


# ------------------------------------------------------------------ skills lock

def dir_hash(path):
    h = hashlib.sha256()
    for base, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            if name == ".harness-copy":
                continue
            full = os.path.join(base, name)
            h.update(os.path.relpath(full, path).replace(os.sep, "/").encode() + b"\0")
            with open(full, "rb") as fh:
                h.update(fh.read())
            h.update(b"\0")
    return h.hexdigest()


def read_skills_lock():
    entries = {}
    if os.path.exists(SKILLS_LOCK):
        with open(SKILLS_LOCK, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 4 and not line.startswith("#"):
                    entries[parts[0]] = (parts[1], parts[2], parts[3])
    return entries


def lock_skill(name, source, ref):
    team = load_conf().get("HARNESS_MODE", "team") != "local"
    found = [(p, lib) for n, p, lib in resolve("skills", "team" if team else None, command="items") if n == name]
    if team:
        # Team mode renders (and checks) the shared copy, as sync does; skills.lock is committed.
        found = [f for f in found if f[1] not in PERSONAL] + [f for f in found if f[1] in PERSONAL]
        if found and found[0][1] in PERSONAL:
            print("skill %s comes from your personal library, and team mode commits skills.lock: pins are for "
                  "skills the project shares (put it in .agents/library/skills/ first)" % name, file=sys.stderr)
            return 1
    if not found or not os.path.isfile(os.path.join(found[0][0], "SKILL.md")):
        print("no skill named %s in any library (a project's own go in .agents/library/skills/)" % name, file=sys.stderr)
        return 1
    path = found[0][0]
    entries = read_skills_lock()
    entries[name] = (dir_hash(path), source, ref)
    with open(SKILLS_LOCK, "w", encoding="utf-8") as fh:
        fh.write("# ai-harness skills lock: third-party skills pinned by content hash.\n")
        fh.write("# sync --check fails if a pinned skill changes. Re-pin after reviewing an update:\n")
        fh.write("#   .agents/bin/sync --lock-skill <name> <source> <ref>\n")
        fh.write("# <name> <sha256> <source> <ref>\n")
        for n in sorted(entries):
            fh.write("%s %s %s %s\n" % (n, entries[n][0], entries[n][1], entries[n][2]))
    print("locked %s at %s (%s)" % (name, ref, entries[name][0][:12]))
    return 0


def skill_set(set_path=None):
    """{name: (path, library)} for the skills sync renders: sync's list when given, else the resolver's."""
    rows = []
    if set_path:
        try:
            with open(set_path, encoding="utf-8") as fh:
                rows = [tuple(line.rstrip("\n").split("\t")) for line in fh]
        except OSError:
            rows = []
    else:
        rows = resolve("skills")
    return {r[0]: (r[1], r[2]) for r in rows if len(r) == 3}


def check_skills(set_path=None):
    entries = read_skills_lock()
    skills = skill_set(set_path)
    bad = 0
    for name, (digest, source, ref) in sorted(entries.items()):
        if name not in skills:
            print("sync: pinned skill '%s' is missing" % name)
            bad = 1
        elif dir_hash(skills[name][0]) != digest:
            print("sync: skill '%s' changed since it was pinned (%s @ %s). Review the diff, then "
                  "re-pin with: .agents/bin/sync --lock-skill %s %s <ref>" % (name, source, ref, name, source))
            bad = 1
    for name, (path, lib) in sorted(skills.items()):
        # Built-ins ship with the harness and a personal library is your own; pins are for
        # skills that came into the project from somewhere else.
        if name in entries or lib == "builtin" or lib in PERSONAL or not os.path.isdir(path):
            continue
        scripts = [f for b, _, fs in os.walk(path) for f in fs
                   if f.endswith((".sh", ".py", ".js", ".ts", ".rb", ".pl")) or
                   os.access(os.path.join(b, f), os.X_OK)]
        if scripts:
            print("sync: warning: skill '%s' ships scripts but isn't pinned in .agents/skills.lock. "
                  "If it came from outside this repo, pin it." % name, file=sys.stderr)
    return bad


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "render":
        return render("--check" in argv[2:])
    if cmd == "lock-skill" and len(argv) == 5:
        return lock_skill(argv[2], argv[3], argv[4])
    if cmd == "check-skills":
        return check_skills(argv[2] if len(argv) > 2 else None)
    if cmd == "unshare":
        return unshare()
    if cmd == "agents" and len(argv) in (4, 5):
        a = argv[2:]
        chk = a[0] == "--check"
        a = a[1:] if chk else a
        if len(a) == 2:
            try:
                return agents(chk, a[0], a[1])
            except Exception:   # sync keeps the old exclude entries when this didn't finish
                import traceback
                traceback.print_exc()
                return 3
    if cmd == "mcp" and len(argv) in (4, 5, 6):
        a = argv[2:]
        chk = a[0] == "--check"
        a = a[1:] if chk else a
        if len(a) in (2, 3):
            try:
                return mcp(chk, *a)
            except Exception:   # sync keeps the old exclude entries when this didn't finish
                import traceback
                traceback.print_exc()
                return 3
    if cmd == "resolve" and len(argv) in (3, 4):
        # The resolver is bash (verify and git hooks run without python3); this just asks it.
        return subprocess.call(["bash", LIBRARIES, "resolve"] + argv[2:], env=dict(os.environ, AGENTS_ROOT=ROOT))
    print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

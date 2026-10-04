#!/usr/bin/env python3
"""ai-harness: approvals only a person records. Harness-owned: replaced on upgrade.

Shared by the workflow packs with human gates. A pack has them when it ships a human-gates file
naming its record key (feature-driven: fdd, debug: debug); packs import this from the project's
.agents/lib/, never from each other. A pack's CLI writes each approval as a line of five
tab-separated fields (kind, id, who, date, value) to the pack's own approvals file and to the
record in the git dir every worktree shares (ai-harness/<key>-approvals). A line without its
record doesn't count. Approving refuses in a shell an agent tool started (CLAUDECODE, GEMINI_CLI,
CURSOR_AGENT), unless install.sh --simulated-human turned on the simulated human for this clone
and the shell has its token (AGENTS_SIMULATED_HUMAN); approvals made then are marked simulated and
count only while it's on.

  approvals.py simulated-human <root> [on] [key...]   install.sh: say whether the simulated human
                                                      is on; 'on' (--simulated-human) turns it on
                                                      and records its hash in each key's record
Exit: 0 ok, 2 usage, 3 the switch didn't turn on, or a crash.
"""
import datetime
import hashlib
import os
import re
import subprocess
import sys

# Shells an agent tool starts carry a marker its docs name: Claude Code sets CLAUDECODE=1 for its
# Bash tool, Gemini CLI sets GEMINI_CLI=1 for run_shell_command, Cursor sets CURSOR_AGENT. Codex
# and Copilot document none (README, Known gaps).
AGENT_SHELLS = (("CLAUDECODE", "Claude Code"), ("GEMINI_CLI", "Gemini CLI"), ("CURSOR_AGENT", "Cursor"))
KEY = re.compile(r"[a-z0-9][a-z0-9-]*")   # a record key; never "on", which main() reads as the switch


def valid_key(k):
    return bool(KEY.fullmatch(k)) and k != "on"


def agent_shell():
    """(variable, tool) when this runs in a shell an agent tool started, else None."""
    for var, name in AGENT_SHELLS:
        if os.environ.get(var):
            return var, name
    return None


def git(root, *args):
    return subprocess.run(["git", "-C", root, "-c", "core.quotePath=false"] + list(args), capture_output=True,
                          text=True, errors="replace").stdout


def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def shown(root, path):
    """Repo-relative inside the repo, absolute outside it."""
    rel = os.path.relpath(path, root)
    return path if rel.startswith("..") else rel


# ------------------------------------------------------------------ the record

RECORD_DIRS = {}   # root -> record_dir(root), worked out once per run: a pack reads many approvals files


def record_dir(root):
    """<git dir every worktree shares>/ai-harness: outside the working tree and every pack's own
    files. None outside git: nothing to record in, so every line counts."""
    if root not in RECORD_DIRS:
        gd = git(root, "rev-parse", "--git-common-dir").strip()
        gd = gd if not gd or os.path.isabs(gd) else os.path.join(root, gd)
        RECORD_DIRS[root] = os.path.join(os.path.normpath(gd), "ai-harness") if gd else None
    return RECORD_DIRS[root]


def record_file(root, key):
    """Where a pack's CLI records the lines it writes. Not keyed by where the pack keeps its own
    approvals file: a line binds a hash of what was approved, so the same line in a moved or copied
    file approves the same thing."""
    d = record_dir(root)
    return None if d is None else os.path.join(d, key + "-approvals")


def recorded(root, key):
    """The lines the pack's CLI recorded, or None outside git."""
    path = record_file(root, key)
    return None if path is None else set(read_lines(path))


def record(root, key, lines):
    """Append approvals lines to the pack's record (no-op outside git). Only approvals lines: five
    tab-separated fields on one line (no break of any kind str.splitlines() knows, which is what
    read_lines() splits on), not starting with "#". The record's markers (adopted, simulated, the
    switch's hash) are this library's to write, so a pack can't write one by mistake. Raises
    ValueError, writing nothing, for any other line."""
    for l in lines:
        if l.splitlines() != [l] or l.startswith("#") or len(l.split("\t")) != 5:
            raise ValueError("not an approvals line (five tab-separated fields, not a # marker): %r" % l)
    _append(root, key, lines)


def _append(root, key, lines):
    """Append lines, markers included, to the pack's record (no-op outside git). This library only."""
    path = record_file(root, key)
    if path is None:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for l in lines:
            fh.write(l + "\n")


# ------------------------------------------------------------------ the simulated human

# For flows where an agent plays the person (a scratch repo, a pilot, a demo). install.sh
# --simulated-human writes SWITCH next to the records, holding a hash of a token, and records that
# hash in each active gated pack's record. While it's on, a shell that has the token
# (AGENTS_SIMULATED_HUMAN) may approve even if an agent tool started it, and every approval made or
# adopted is marked simulated: the who field ends in SIMULATED, or the record holds
# "#simulated<TAB><line>". A simulated approval counts only while the switch is on. A switch whose
# hash no record holds, that the hooks flagged (it appeared during an agent turn, or its token was
# in an agent's environment), or that can't be written to (so it can't be flagged) is off, and
# each pack's checks report it.
SWITCH = "simulated-human"
SIMULATED = " (simulated human)"
SIM_REC = "#simulated\t"
SWITCH_REC = "#simulated-human\t"
TOKEN_VAR = "AGENTS_SIMULATED_HUMAN"


def switch_recorded(root, h):
    """True when a pack's record holds the switch's hash. install.sh writes it into the record of
    every active pack with human gates; a clone switched on before this library has it in
    fdd-approvals only, and that counts for every pack."""
    d = record_dir(root)
    try:
        names = sorted(os.listdir(d)) if d else []
    except OSError:
        names = []
    return any(n.endswith("-approvals") and SWITCH_REC + h in read_lines(os.path.join(d, n)) for n in names)


class Switch:
    """The simulated-human switch: state 'off' (no file), 'on', or 'void' (a file that doesn't
    count, why says so)."""

    def __init__(self, root):
        self.state, self.how, self.why, self.hash = "off", "", "", ""
        d = record_dir(root)
        self.path = os.path.join(d, SWITCH) if d else None
        if not self.path or not os.path.isfile(self.path):
            return
        lines = read_lines(self.path)
        ons = [l.split("\t") for l in lines if l.startswith("on\t")]
        flags = [l.split("\t", 1)[1] for l in lines if l.startswith("flagged\t")]
        if len(ons) != 1 or len(ons[0]) != 3 or not switch_recorded(root, ons[0][1]):
            self.state, self.why = "void", "wasn't written by install.sh --simulated-human"
        elif flags:
            self.state, self.why = "void", "was " + flags[0]
        elif not os.access(self.path, os.W_OK):   # the stop gate couldn't flag it
            self.state, self.why = "void", "isn't writable, so the hooks can't flag it"
        else:
            self.state, self.hash, self.how = "on", ons[0][1], ons[0][2]

    def token_ok(self):
        tok = os.environ.get(TOKEN_VAR, "")
        return self.state == "on" and bool(tok) and token_hash(tok) == self.hash


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_simulated(line, rec):
    return line.split("\t")[2].endswith(SIMULATED) or (rec is not None and SIM_REC + line in rec)


def switch_line(root, switch):
    """How a pack's status and install.sh describe the switch; '' when there's none."""
    if switch.state == "on":
        return ("simulated human: on (%s); approvals made here are marked simulated, and count only while "
                "it's on" % switch.how)
    if switch.state == "void":
        return "simulated human: off, %s %s" % (shown(root, switch.path), switch.why)
    return ""


# ------------------------------------------------------------------ approving

def classify(root, key, path, switch=None):
    """The lines of an approvals file, sorted out: ([(line no, fields, place)] that count,
    [(line no, fields)] the pack's CLI didn't record, [(line no, fields)] a simulated human made
    while the switch is off). Lines without five fields are skipped. place is the line's last place
    in the record (approving the same thing again moves it, even on the same day), so reordering or
    copying lines in the file changes nothing; outside git, every line counts and place is its line
    number."""
    rec = recorded(root, key)
    on = (switch or Switch(root)).state == "on"
    pos = {}
    if rec is not None:
        for i, l in enumerate(read_lines(record_file(root, key))):
            pos[l[len(SIM_REC):] if l.startswith(SIM_REC) else l] = i
    counted, unrecorded, simulated = [], [], []
    for n, line in enumerate(read_lines(path), 1):
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        if rec is not None and line not in rec and SIM_REC + line not in rec:
            unrecorded.append((n, parts))
            continue
        if not on and is_simulated(line, rec):
            simulated.append((n, parts))
            continue
        counted.append((n, parts, pos.get(line, -1) if rec is not None else n))
    return counted, unrecorded, simulated


def blocked_shell(switch):
    """The (variable, tool) of the agent shell this runs in when that keeps it from taking a human's
    step; None when it may (a person's terminal, or an agent's shell with the simulated human's
    token)."""
    shell = agent_shell()
    return None if not shell or switch.token_ok() else shell


def refused(prog, doing, switch):
    """True, after saying why on stderr, when this runs in a shell an agent tool started and the
    shell doesn't have the simulated human's token. doing names the step, e.g. 'approving'."""
    shell = blocked_shell(switch)
    if not shell:
        return False
    print("%s: %s is the human's step, and this shell was started by %s (%s is set). "
          "Run it in your own terminal." % (prog, doing, shell[1], shell[0]), file=sys.stderr)
    if os.environ.get(TOKEN_VAR):   # only someone who set it hears why it didn't help
        print("%s: %s doesn't help: %s" % (prog, TOKEN_VAR, {
            "on": "it doesn't match this clone's simulated-human token",
            "off": "this clone has no simulated human (install.sh --simulated-human)",
            "void": "this clone's simulated-human switch %s, so it's off" % switch.why}[switch.state]),
            file=sys.stderr)
    return True


def new_line(root, kind, ident, value, switch):
    """(approvals line, simulated?). who is git's user.name, else $USER, and ends in SIMULATED while
    the simulated human is on: in the line itself, so a copy of the file carries it anywhere."""
    def field(t):   # one field: no tab or line break (any that read_lines() splits on) can split the line
        return " ".join(t.splitlines()).replace("\t", " ")
    who = field(git(root, "config", "user.name").strip() or os.environ.get("USER", "unknown"))
    sim = switch.state == "on"
    if sim:
        who += SIMULATED
    return "\t".join((field(kind), field(ident), who, datetime.date.today().isoformat(), field(value))), sim


def human_cmd(root, pack, name):
    """How a person runs a pack's bin/<name>: .agents/commands/<name> when sync wrote it (the same
    path on every machine), else the pack's own bin/<name>, relative to the project when the pack
    sits inside it (the built-in library), else its full path (a personal library)."""
    wrapper = os.path.join(root, ".agents", "commands", name)
    if os.path.isfile(wrapper) and not os.path.islink(wrapper):
        try:
            with open(wrapper, encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
        except OSError:
            head = ""
        if "# generated by .agents/bin/sync: runs bin/%s " % name in head:
            return os.path.join(".agents", "commands", name)
    cmd = os.path.join(pack, "bin", name)
    rel = os.path.relpath(cmd, root)
    return cmd if rel.startswith("..") else rel


ADOPTED = "#adopted"   # in a record: this clone has taken in the pack's earlier approvals, once


def adopt(root, key, path, simulated=False):
    """The first time a person runs the pack's CLI or install.sh in this clone: the lines already in
    path (written before the pack kept a record, or copied in) are recorded as they are, or as
    simulated while the simulated human is on. Never again after that, so a line written later
    can't be adopted. Returns the lines adopted ([] for none, or when it already ran)."""
    rec = recorded(root, key)
    if rec is None or ADOPTED in rec:
        return []
    lines = [l for l in read_lines(path) if len(l.split("\t")) == 5 and l not in rec and SIM_REC + l not in rec]
    _append(root, key, [ADOPTED] + [SIM_REC + l if simulated else l for l in lines])
    return lines


def cmd_simulated_human(root, turn_on, keys):
    """install.sh: with turn_on (install.sh --simulated-human), turn the simulated human on for this
    clone with the token in AGENTS_SIMULATED_HUMAN, replacing any earlier switch, and record its
    hash in each key's record. Either way, say when a switch is there, for the install summary."""
    if turn_on:
        token = os.environ.get(TOKEN_VAR, "")
        d = record_dir(root)
        if d is None or len(token) < 16:
            print("error: --simulated-human needs a git repo and a token of 16 or more characters", file=sys.stderr)
            return 2
        if not keys or not all(valid_key(k) for k in keys):
            print("error: --simulated-human needs the record key of a workflow pack with human gates", file=sys.stderr)
            return 2
        shell = agent_shell()
        h = token_hash(token)
        for k in keys:
            _append(root, k, [SWITCH_REC + h])   # first, so the switch is never left without its record
        sw = os.path.join(d, SWITCH)
        if os.path.lexists(sw):   # a fresh file: not through a link, and writable again
            os.remove(sw)
        with open(sw, "w", encoding="utf-8") as fh:
            fh.write("# ai-harness simulated human, for flows where an agent plays the person. Written by "
                     "install.sh --simulated-human;\n# delete this file to turn it off. Edited or written any "
                     "other way, it's off and verify reports it.\n")
            fh.write("on\t%s\tinstall.sh --simulated-human, run from %s\n"
                     % (h, "a %s shell" % shell[1] if shell else "a terminal"))
    switch = Switch(root)
    if switch_line(root, switch):
        print(switch_line(root, switch))
    if turn_on and switch.state != "on":
        return 3
    if turn_on:
        print("simulated human: to approve from an agent's shell, set %s=%s on that one command; never "
              "export it where an agent starts, or the hooks turn the switch off. Never use this in a "
              "real project." % (TOKEN_VAR, os.environ[TOKEN_VAR]))
    return 0


def main(argv):
    a = argv[1:]
    if len(a) >= 2 and a[0] == "simulated-human":
        on = a[2:3] == ["on"]
        try:
            return cmd_simulated_human(os.path.abspath(a[1]), on, a[3:] if on else a[2:])
        except Exception as e:  # a crash is a tooling problem
            print("infra: approvals failed: %s" % e)
            return 3
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

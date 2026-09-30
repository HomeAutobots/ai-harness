#!/usr/bin/env bash
# ai-harness installer. Installs or upgrades the harness in a project. Safe to re-run.
#
#   ./install.sh [--local | --team] [--stack <name>]... [--workflow <name>]... <project-dir>
#
# Harness-owned files are replaced on every run. Project-owned files are only created when
# missing, so re-running is the upgrade and never touches tailoring. Stack packs (stacks/<name>)
# add language tooling; workflow packs (workflows/<name>) add a process skill and its checks.
# Once installed, packs are listed in STACKS / WORKFLOWS in .agents/harness.conf and refreshed
# on every upgrade.
set -euo pipefail

HARNESS="$(cd "$(dirname "$0")" && pwd)"
SRC="$HARNESS/template"
VERSION="$(cat "$HARNESS/VERSION")"

usage() {
  echo "usage: $0 [--local | --team] [--stack <name>]... [--workflow <name>]... <project-dir>" >&2
  echo "  stacks: $(ls "$HARNESS/stacks" | tr '\n' ' ')  workflows: $(ls "$HARNESS/workflows" | tr '\n' ' ')" >&2
  exit 2
}
NEW_STACKS=""
NEW_WORKFLOWS=""
NEW_MODE=""
DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) [ $# -ge 2 ] || usage; NEW_STACKS="$NEW_STACKS $2"; shift 2 ;;
    --stack=*) NEW_STACKS="$NEW_STACKS ${1#--stack=}"; shift ;;
    --workflow) [ $# -ge 2 ] || usage; NEW_WORKFLOWS="$NEW_WORKFLOWS $2"; shift 2 ;;
    --workflow=*) NEW_WORKFLOWS="$NEW_WORKFLOWS ${1#--workflow=}"; shift ;;
    --local) NEW_MODE=local; shift ;;
    --team) NEW_MODE=team; shift ;;
    -h|--help) usage ;;
    -*) echo "install: unknown option $1" >&2; usage ;;
    *) [ -z "$DEST" ] || usage; DEST="$1"; shift ;;
  esac
done
[ -n "$DEST" ] || usage
[ -d "$DEST" ] || { echo "install: not a directory: $DEST" >&2; exit 2; }
DEST="$(cd "$DEST" && pwd)"
[ "$DEST" != "$HARNESS" ] || { echo "install: that's the harness repo itself" >&2; exit 2; }
for s in $NEW_STACKS; do
  [ -d "$HARNESS/stacks/$s" ] || { echo "install: unknown stack '$s'" >&2; usage; }
done
for w in $NEW_WORKFLOWS; do
  [ -d "$HARNESS/workflows/$w" ] || { echo "install: unknown workflow '$w'" >&2; usage; }
done

say() { printf 'install: %s\n' "$*"; }

if git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1; then
  [ -z "$(git -C "$DEST" status --porcelain)" ] \
    || say "note: $DEST has uncommitted changes, so review the harness diff carefully"
else
  say "note: $DEST isn't a git repo. The feedback tools and hooks need git; please init one."
fi
command -v python3 >/dev/null 2>&1 \
  || say "warning: python3 not found. Hooks and permission rules won't be rendered until it is installed."

PREV="$(cat "$DEST/.agents/HARNESS_VERSION" 2>/dev/null || true)"

# Harness-owned: replaced every run.
replace() {
  rm -rf "${DEST:?}/$1"
  mkdir -p "$(dirname "$DEST/$1")"
  cp -R "$SRC/$1" "$DEST/$1"
}

# Project-owned: created once, never overwritten.
seed() {
  [ -e "$DEST/$1" ] && return 0
  mkdir -p "$(dirname "$DEST/$1")"
  cp -R "$SRC/$1" "$DEST/$1"
  say "created $1"
}

# --- migrate 0.1.x: verify was project-owned; its checks move to the tier scripts ------------
if [ -f "$DEST/.agents/bin/verify" ] && ! grep -q 'ai-harness: verify (orchestrator)' "$DEST/.agents/bin/verify"; then
  mkdir -p "$DEST/.agents/checks"
  for tier in turn full; do
    if [ ! -e "$DEST/.agents/checks/$tier.sh" ]; then
      cp "$DEST/.agents/bin/verify" "$DEST/.agents/checks/$tier.sh"
      say "migrated your 0.1 .agents/bin/verify to .agents/checks/$tier.sh (review it: tier scripts get changed files as arguments)"
    fi
  done
  rm -f "$DEST/.agents/bin/verify"
fi

replace .agents/core
replace .agents/lib
replace .agents/hooks
replace .agents/README.md
replace .agents/.gitattributes
replace .agents/.gitignore
for tool in sync verify check guard tasks eval gitflow; do
  replace ".agents/bin/$tool"
done
for skill in "$SRC"/.agents/skills/*/; do
  replace ".agents/skills/$(basename "$skill")"
done

seed AGENTS.md
seed .agents/harness.conf
seed .agents/policy.conf
seed .agents/git.conf
seed .agents/git
seed .agents/context
seed .agents/plans
seed .agents/evals
for tier in edit turn full; do
  seed ".agents/checks/$tier.sh"
done

# --- packs --------------------------------------------------------------------------------------
CONF="$DEST/.agents/harness.conf"
conf_list() {  # conf_list <VAR>: current value of a space-separated list in harness.conf
  sed -n "s/^$1=\"\{0,1\}\([^\"#]*\)\"\{0,1\}.*/\1/p" "$CONF" | head -1 | sed 's/[[:space:]]*$//'
}
conf_list_set() {  # conf_list_set <VAR> <value>: set (or add) the list in harness.conf
  if grep -q "^$1=" "$CONF"; then
    tmp="$(mktemp)"
    awk -v k="$1" -v v="$2" '$0 ~ "^" k "=" { print k "=\"" v "\""; next } { print }' "$CONF" > "$tmp" && cat "$tmp" > "$CONF" && rm -f "$tmp"
  else
    printf '%s="%s"\n' "$1" "$2" >> "$CONF"
  fi
}
merge_list() {  # merge_list <existing> <new...>: union, order kept
  local out="$1" x
  shift
  for x in "$@"; do case " $out " in *" $x "*) ;; *) out="${out:+$out }$x" ;; esac; done
  printf '%s' "$out"
}

# --- mode: local (hidden from git in this clone) or team (committed) ---------------------------
# A fresh install defaults to local; an upgrade keeps the recorded mode, and a missing line
# (installs from before this setting) means team. Only --local or --team switches.
OLD_MODE="$(conf_list HARNESS_MODE)"
if [ -n "$NEW_MODE" ]; then MODE="$NEW_MODE"
elif [ -z "$PREV" ]; then MODE=local
else MODE="${OLD_MODE:-team}"; fi
SWITCH=""
[ -n "$PREV" ] && [ "${OLD_MODE:-team}" != "$MODE" ] && SWITCH="$MODE"
# An explicit --local finishes a switch that stopped partway (mode recorded, harness still tracked).
if [ "$NEW_MODE" = local ] && [ -n "$PREV" ] && [ -z "$SWITCH" ] \
   && git -C "$DEST" ls-files --error-unmatch -- .agents/harness.conf >/dev/null 2>&1; then SWITCH=local; fi
conf_list_set HARNESS_MODE "$MODE"
say "mode: $MODE$([ "$MODE" = local ] && echo ' (hidden from git in this clone; install.sh --team to commit it instead)')"

STACKS="$(merge_list "$(conf_list STACKS)" $NEW_STACKS)"
conf_list_set STACKS "$STACKS"
for s in $STACKS; do
  if [ ! -d "$HARNESS/stacks/$s" ]; then
    say "warning: STACKS lists '$s' but this harness has no such pack; skipped"
    continue
  fi
  rm -rf "${DEST:?}/.agents/stacks/$s"
  mkdir -p "$DEST/.agents/stacks"
  cp -R "$HARNESS/stacks/$s" "$DEST/.agents/stacks/$s"
  find "$DEST/.agents/stacks/$s" -name __pycache__ -type d -prune -exec rm -rf {} +   # from a dev checkout
  # A stack's tier scripts replace the generic stubs, never tailored scripts.
  for tier in edit turn full; do
    t="$DEST/.agents/checks/$tier.sh"
    if [ ! -e "$t" ] || grep -q 'ai-harness:stub' "$t"; then
      cp "$HARNESS/stacks/$s/checks/$tier.sh" "$t"
      say "seeded .agents/checks/$tier.sh from stack $s"
    fi
  done
done

WORKFLOWS="$(merge_list "$(conf_list WORKFLOWS)" $NEW_WORKFLOWS)"
conf_list_set WORKFLOWS "$WORKFLOWS"
for w in $WORKFLOWS; do
  if [ ! -d "$HARNESS/workflows/$w" ]; then
    say "warning: WORKFLOWS lists '$w' but this harness has no such pack; skipped"
    continue
  fi
  rm -rf "${DEST:?}/.agents/workflows/$w" "${DEST:?}/.agents/skills/$w"
  mkdir -p "$DEST/.agents/workflows" "$DEST/.agents/skills"
  cp -R "$HARNESS/workflows/$w" "$DEST/.agents/workflows/$w"
  find "$DEST/.agents/workflows/$w" -name __pycache__ -type d -prune -exec rm -rf {} +   # from a dev checkout
  mv "$DEST/.agents/workflows/$w/skill" "$DEST/.agents/skills/$w"
  # Settings are project-owned: appended once, never overwritten.
  snip="$HARNESS/workflows/$w/harness.conf.snippet"
  first_var=""
  [ -f "$snip" ] && first_var="$(grep -m1 -o '^[A-Z_]*=' "$snip" | tr -d '=' || true)"
  if [ -n "$first_var" ] && ! grep -q "^$first_var=" "$CONF"; then
    cat "$snip" >> "$CONF"
    say "added $w settings to .agents/harness.conf (harness-tailor fills them in)"
  fi
  rm -f "$DEST/.agents/workflows/$w/harness.conf.snippet"
  # Policy rules are project-owned too: appended once, keyed on the snippet's first line (a marker
  # comment), so a rule the project deletes on purpose stays deleted while the marker stays.
  snip="$HARNESS/workflows/$w/policy.conf.snippet"
  if [ -f "$snip" ]; then
    marker="$(sed -n '1p' "$snip")"
    if [ -n "$marker" ] && ! grep -qxF -- "$marker" "$DEST/.agents/policy.conf"; then
      { echo; cat "$snip"; } >> "$DEST/.agents/policy.conf"
      say "added $w rules to .agents/policy.conf"
    fi
  fi
  rm -f "$DEST/.agents/workflows/$w/policy.conf.snippet"
  # Seed files (e.g. a .gitignore for the pack's local working files): project-owned, created once.
  if [ -d "$HARNESS/workflows/$w/seed" ]; then
    (cd "$HARNESS/workflows/$w/seed" && find . -type f) | while IFS= read -r f; do
      f="${f#./}"
      case "$f" in .agents/bin/*|.agents/lib/*|.agents/hooks/*|.agents/core/*|.agents/skills/*|.agents/workflows/*|.agents/stacks/*)
        say "warning: $w seeds $f, which is harness-owned; skipped"; continue ;;
      esac
      [ -e "$DEST/$f" ] && continue
      mkdir -p "$(dirname "$DEST/$f")"
      cp "$HARNESS/workflows/$w/seed/$f" "$DEST/$f"
      say "created $f"
    done
  fi
  rm -rf "${DEST:?}/.agents/workflows/$w/seed"
done

printf '%s\n' "$VERSION" > "$DEST/.agents/HARNESS_VERSION"
chmod +x "$DEST"/.agents/bin/* "$DEST"/.agents/hooks/run "$DEST"/.agents/checks/*.sh
for w in $WORKFLOWS; do
  if [ -d "$DEST/.agents/workflows/$w/checks" ]; then
    find "$DEST/.agents/workflows/$w/checks" -type f -name '*.sh' -exec chmod +x {} +
  fi
  if [ -d "$DEST/.agents/workflows/$w/bin" ]; then
    find "$DEST/.agents/workflows/$w/bin" -type f -exec chmod +x {} +
  fi
done

# --- switching modes -------------------------------------------------------------------------
# --local on a team install: the harness's own files leave the index (they stay on disk), and
# tracked shared files lose only the harness's blocks and entries. --team needs nothing here:
# sync in team mode drops the exclude block and moves the Claude settings back.
IN_GIT=0; git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1 && IN_GIT=1
if [ "$SWITCH" = local ] && [ "$IN_GIT" -eq 1 ]; then
  unshare_out="$(cd "$DEST" && .agents/bin/sync --unshare)" \
    || { say "error: couldn't take the harness out of shared files; fix the error above and re-run with --local"; exit 3; }
  untrack=".agents"
  for p in "$DEST"/.claude/skills/*; do
    { [ -L "$p" ] && case "$(readlink "$p")" in ../../.agents/skills/*) true ;; *) false ;; esac; } \
      || [ -f "$p/.harness-copy" ] || continue
    untrack="$untrack .claude/skills/$(basename "$p")"
  done
  while IFS= read -r line; do
    case "$line" in
      "untrack "*) untrack="$untrack ${line#untrack }" ;;
      "stripped "*) say "removed the harness entries from ${line#stripped }" ;;
    esac
  done <<EOF
$unshare_out
EOF
  # -f: files stay on disk either way; it only lets staged-but-uncommitted changes go too.
  for p in $untrack; do git -C "$DEST" rm -r -q -f --cached --ignore-unmatch -- "$p"; done
fi

"$DEST/.agents/bin/sync"
# Local git hooks (commit-msg, pre-push) so the git workflow holds for humans and every tool.
if [ "$IN_GIT" -eq 1 ]; then
  (cd "$DEST" && .agents/bin/gitflow install-hooks) | sed 's/^/install: /'
fi

if [ "$SWITCH" = local ] && [ "$IN_GIT" -eq 1 ]; then
  say "switched to local mode. Commit what git status shows (git add the modified files, or git commit -a): the harness leaves the repo (your files stay on disk)."
  say "after that commit, other clones lose .agents/ on pull; each developer re-runs install.sh (local by default)"
  git -C "$DEST" status --short | awk '/^D  \.agents\// { n++; next } { print "  " $0 }
    END { if (n) print "  D  .agents/ (" n " files)" }'
elif [ "$SWITCH" = team ]; then
  say "switched to team mode. Review and commit the harness: git add -A && git commit"
  if [ -f "$DEST/.agents/AGENTS.local.md" ]; then
    say "note: .agents/AGENTS.local.md stays; move any facts in it into AGENTS.md, then delete it (git add -A would otherwise commit it)"
  fi
fi

if [ -z "$PREV" ] && [ "$MODE" = local ]; then
  cat <<EOF

ai-harness $VERSION installed in $DEST (local mode)

Next:
  1. Open the project in any agent and say:
       "Use the harness-tailor skill to tailor the AI harness for this repo."
  2. Review what it proposes. Everything stays out of git in this clone; teammates see nothing.
  3. A new clone or worktree needs its own install.sh run.
  To share the harness with the team instead: install.sh --team $DEST
EOF
elif [ -z "$PREV" ]; then
  cat <<EOF

ai-harness $VERSION installed in $DEST (team mode)

Next:
  1. Open the project in any agent and say:
       "Use the harness-tailor skill to tailor the AI harness for this repo."
  2. Review the proposal (AGENTS.md, .agents/checks/, CLAUDE.md), then commit.
  3. CI: run '.agents/bin/sync --check && .agents/bin/verify --tier=full'.
  4. Consider CODEOWNERS for AGENTS.md, CLAUDE.md, .agents/, .claude/, .cursor/, .github/hooks/,
     .github/agents/, .codex/, .gemini/.
EOF
elif [ "$PREV" != "$VERSION" ] && [ "$MODE" = local ]; then
  say "upgraded $PREV -> $VERSION. Check CHANGELOG.md in the harness repo. Local mode: nothing goes into git."
elif [ "$PREV" != "$VERSION" ]; then
  say "upgraded $PREV -> $VERSION. Check CHANGELOG.md in the harness repo, review the diff, commit."
else
  say "reinstalled $VERSION, no version change"
fi

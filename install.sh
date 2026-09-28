#!/usr/bin/env bash
# ai-harness installer. Installs or upgrades the harness in a project. Safe to re-run.
#
#   ./install.sh [--stack <name>]... [--workflow <name>]... <project-dir>
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
  echo "usage: $0 [--stack <name>]... [--workflow <name>]... <project-dir>" >&2
  echo "  stacks: $(ls "$HARNESS/stacks" | tr '\n' ' ')  workflows: $(ls "$HARNESS/workflows" | tr '\n' ' ')" >&2
  exit 2
}
NEW_STACKS=""
NEW_WORKFLOWS=""
DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) [ $# -ge 2 ] || usage; NEW_STACKS="$NEW_STACKS $2"; shift 2 ;;
    --stack=*) NEW_STACKS="$NEW_STACKS ${1#--stack=}"; shift ;;
    --workflow) [ $# -ge 2 ] || usage; NEW_WORKFLOWS="$NEW_WORKFLOWS $2"; shift 2 ;;
    --workflow=*) NEW_WORKFLOWS="$NEW_WORKFLOWS ${1#--workflow=}"; shift ;;
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
  [ -d "$DEST/.agents/workflows/$w/checks" ] && chmod +x "$DEST/.agents/workflows/$w/checks/"*.sh
  [ -d "$DEST/.agents/workflows/$w/bin" ] && chmod +x "$DEST/.agents/workflows/$w/bin/"*
done

"$DEST/.agents/bin/sync"
# Local git hooks (commit-msg, pre-push) so the git workflow holds for humans and every tool.
if git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1; then
  (cd "$DEST" && .agents/bin/gitflow install-hooks) | sed 's/^/install: /'
fi

if [ -z "$PREV" ]; then
  cat <<EOF

ai-harness $VERSION installed in $DEST

Next:
  1. Open the project in any agent and say:
       "Use the harness-tailor skill to tailor the AI harness for this repo."
  2. Review the proposal (AGENTS.md, .agents/checks/, CLAUDE.md), then commit.
  3. CI: run '.agents/bin/sync --check && .agents/bin/verify --tier=full'.
  4. Consider CODEOWNERS for AGENTS.md, CLAUDE.md, .agents/, .claude/, .cursor/, .github/hooks/.
EOF
elif [ "$PREV" != "$VERSION" ]; then
  say "upgraded $PREV -> $VERSION. Check CHANGELOG.md in the harness repo, review the diff, commit."
else
  say "reinstalled $VERSION, no version change"
fi

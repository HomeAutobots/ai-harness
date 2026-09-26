#!/usr/bin/env bash
# Fast static checks for the ai-harness source repo. Seconds, no installs.
#   bash tests/lint.sh
# Findings print as path:line: error: [rule] message. Exit 0 clean, 1 findings.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 3
export LC_ALL=C
OUT="$(mktemp)"
trap 'rm -f "$OUT" "$OUT".*' EXIT
finding() { printf '%s: error: [%s] %s\n' "$1" "$2" "$3" >> "$OUT"; }

files() {  # every file in the repo that we own, minus build output and vcs
  find . \( -path ./.git -o -path ./dist -o -name __pycache__ \) -prune -o -type f -print | sed 's|^\./||' | sort
}
files > "$OUT.all"

# Shell scripts: *.sh, anything with a sh/bash shebang, and the shellcheck-annotated library.
while IFS= read -r f; do
  case "$f" in
    *.sh) echo "$f" ;;
    *.py|*.md|*.json|*.conf|*.txt|*.csv|*.patterns|*.agent|*.snippet|*.yml|*.yaml) ;;
    *) head -1 "$f" 2>/dev/null | grep -Eq '^#!.*(bash|/sh)' && echo "$f" ;;
  esac
done < "$OUT.all" > "$OUT.sh"
grep -E '\.py$' "$OUT.all" > "$OUT.py"

# 1. Syntax
while IFS= read -r f; do
  err="$(bash -n "$f" 2>&1)" || finding "$f:1" syntax "$(printf '%s' "$err" | head -1)"
done < "$OUT.sh"
if command -v python3 >/dev/null 2>&1; then
  xargs python3 -c '
import ast, sys
for f in sys.argv[1:]:
    try:
        ast.parse(open(f, encoding="utf-8").read(), f)
    except SyntaxError as e:
        print("%s:%s: error: [syntax] %s" % (f, e.lineno or 1, e.msg))
' < "$OUT.py" >> "$OUT"
else
  echo "note: python3 not found; Python syntax not checked"
fi

# 2. shellcheck
if command -v shellcheck >/dev/null 2>&1; then
  xargs shellcheck -S warning -f gcc < "$OUT.sh" | sed 's/: warning: /: error: [shellcheck] /; s/: error: \([^[]\)/: error: [shellcheck] \1/' >> "$OUT"
else
  echo "note: shellcheck not found; skipped (install it: apt install shellcheck / brew install shellcheck)"
fi

# 3. bash 3.2 portability (this file lists the constructs, so it's excluded)
grep -v '^tests/lint.sh$' "$OUT.sh" | xargs grep -nE 'declare -A|(^|[^a-zA-Z_])(mapfile|readarray|coproc)[[:space:]]|\$\{[A-Za-z_]+(,,|\^\^)\}|\|&|&>>|local -n |declare -n ' \
  | while IFS= read -r l; do finding "${l%%:*}:$(echo "$l" | cut -d: -f2)" bash32 "bash 4+ construct; keep scripts bash 3.2 compatible"; done

# 4. Python 3.8 portability (common 3.9+ features)
xargs grep -nE '\.removeprefix\(|\.removesuffix\(|^[[:space:]]*match [^=]*:$|: (list|dict|tuple|set)\[' < "$OUT.py" \
  | while IFS= read -r l; do finding "${l%%:*}:$(echo "$l" | cut -d: -f2)" py38 "Python 3.9+ feature; keep to 3.8"; done

# 5. Ownership lists: every bin tool installed, every template entry placed, every built-in skill known
for f in template/.agents/bin/*; do
  n="$(basename "$f")"
  grep -Eq "for tool in ([^;]*[[:space:]])?$n([[:space:]]|;)" install.sh \
    || finding "install.sh:1" ownership "template/.agents/bin/$n isn't in install.sh's 'for tool in' list, so installs won't get it"
done
for f in template/.agents/* template/.agents/.[a-z]*; do
  [ -e "$f" ] || continue
  n="$(basename "$f")"
  case "$n" in bin|skills|checks|HARNESS_VERSION) continue ;; esac
  grep -Fq ".agents/$n" install.sh || finding "install.sh:1" ownership "template/.agents/$n is neither replaced nor seeded by install.sh"
done
for d in template/.agents/skills/*/; do
  n="$(basename "$d")"
  grep -q "BUILTIN_SKILLS = .*\"$n\"" template/.agents/lib/harness.py \
    || finding "template/.agents/lib/harness.py:1" ownership "built-in skill '$n' is missing from BUILTIN_SKILLS"
done

# 6. Budgets
n="$(wc -l < template/.agents/core/AGENTS.core.md | tr -d ' ')"
[ "$n" -le 25 ] || finding "template/.agents/core/AGENTS.core.md:$n" budget "core rules are $n lines; keep them near 20 (max 25): every line loads in every session"

# 7. Never install the harness into its own repo
[ -e .agents ] && finding ".agents:1" self-install "the harness is installed into its own repo; remove .agents/ (try it in a scratch repo under /tmp)"
[ -e AGENTS.md ] && finding "AGENTS.md:1" self-install "AGENTS.md at the repo root means the harness got installed here; remove it"

# 8. Release hygiene
v="$(tr -d '[:space:]' < VERSION)"
grep -q "^## $v\( \|$\)" CHANGELOG.md || finding "CHANGELOG.md:1" version "VERSION is $v but CHANGELOG.md has no '## $v' heading"

# 9. Docs voice and hidden characters
grep -E '\.(md|conf|patterns|snippet|agent)$' "$OUT.all" | xargs grep -nF "$(printf '\342\200\224')" 2>/dev/null \
  | while IFS= read -r l; do finding "${l%%:*}:$(echo "$l" | cut -d: -f2)" em-dash "no em dashes in docs or templates; use a colon, comma, or new sentence"; done
printf '\342\200\213\n\342\200\214\n\342\200\215\n\342\200\216\n\342\200\217\n\342\200\252\n\342\200\253\n\342\200\254\n\342\200\255\n\342\200\256\n\342\201\240\n\342\201\246\n\342\201\247\n\342\201\250\n\342\201\251\n\357\273\277\n\363\240\200\n\363\240\201\n' > "$OUT.hidden"
tr '\n' '\0' < "$OUT.all" | xargs -0 grep -lF -f "$OUT.hidden" 2>/dev/null \
  | while IFS= read -r f; do finding "$f:1" hidden-unicode "invisible Unicode (zero-width, bidi, BOM, or tag characters)"; done

if [ -s "$OUT" ]; then
  sort -u "$OUT"
  echo "FAIL lint ($(sort -u "$OUT" | wc -l | tr -d ' ') findings)"
  exit 1
fi
echo "ok lint"

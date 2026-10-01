# shellcheck shell=bash
# ai-harness: libraries and the one resolver. Harness-owned: replaced on upgrade.
#
# A library is a directory holding any of: skills/<name>/SKILL.md, workflows/<name>/,
# stacks/<name>/, agents/<name>.md, mcp/<name>.json. For each kind, the first library in this
# order that has a name wins:
#   project          .agents/library/
#   project-listed   LIBRARIES in .agents/harness.conf (absolute, ~/..., or relative to the project root)
#   personal         ~/.config/ai-harness/ ($XDG_CONFIG_HOME/ai-harness; AGENTS_PERSONAL_DIR overrides)
#   personal-listed  LIBRARIES in <personal>/harness.conf (absolute, ~/..., or relative to that dir)
#   builtin          .agents/builtin/, what the harness ships (AGENTS_BUILTIN_DIR overrides)
# Directories that don't exist are skipped, and each directory counts once. An entry counts only
# when it's a real item of its kind (agents_item_ok): an empty directory never shadows anything. Workflows and stacks
# are used only when named in WORKFLOWS / STACKS in .agents/harness.conf; an active workflow's
# skill/ joins the skills under the workflow's name, and its agents/*.md join the agents by file
# name, both from the winning pack only, after every library's own skills and agents.
#
# Sourced by sync, verify and check scripts (through feedback.sh), gitflow, the stack shims, and
# install.sh. As a command (harness.py resolve runs it):
#   bash .agents/lib/libraries.sh libraries               source<TAB>path, in search order
#   bash .agents/lib/libraries.sh resolve <kind> [name]   name<TAB>path<TAB>source, winners only
#   bash .agents/lib/libraries.sh items <kind> [team]     every item in search order, shadowed too
#   bash .agents/lib/libraries.sh shadows <kind>          name, winner source and path, shadowed source and path
# Kinds: skills workflows stacks agents mcp. Config files are parsed, never sourced (git hooks
# run this), and LIBRARIES is space-separated, so a library path can't contain spaces.
# bash 3.2 and POSIX tools only, never python3: verify and git hooks work without it.

if [ -z "${AGENTS_ROOT:-}" ]; then
  AGENTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
AGENTS_TAB="$(printf '\t')"

# agents_personal_dir: the personal library, which also holds the personal harness.conf and git.conf
agents_personal_dir() {
  printf '%s\n' "${AGENTS_PERSONAL_DIR:-${XDG_CONFIG_HOME:-${HOME:-}/.config}/ai-harness}"
}

# agents_conf_get <file> <KEY>: a KEY=value setting, quoted or not. The last one wins, as when
# the file is sourced. Parsed, never run.
agents_conf_get() {
  [ -f "$1" ] || return 0
  sed -n "s/^[[:space:]]*$2=[\"']\{0,1\}\([^\"'#]*\)[\"']\{0,1\}.*/\1/p" "$1" | tail -n 1 | sed 's/[[:space:]]*$//'
  return 0
}

# agents_valid_name <name>: a plain item name, never a path and never hidden
agents_valid_name() {
  case "$1" in ""|.*|_*|*[!A-Za-z0-9._-]*) return 1 ;; esac
  return 0
}

_agents_lib_candidates() {  # source<TAB>path for every library that might exist, in order
  local pd p
  # LIBRARIES is split unquoted on whitespace below; noglob keeps a metacharacter in a path from
  # matching files in the caller's cwd instead of being taken literally. Always run inside a
  # subshell (process substitution in _agents_libraries_scan), so this never leaks to a caller.
  set -f
  pd="$(agents_personal_dir)"
  printf 'project\t%s\n' "$AGENTS_ROOT/.agents/library"
  for p in $(agents_conf_get "$AGENTS_ROOT/.agents/harness.conf" LIBRARIES); do
    case "$p" in /*) ;; \~/*) p="${HOME:-}/${p#\~/}" ;; *) p="$AGENTS_ROOT/$p" ;; esac
    printf 'project-listed\t%s\n' "$p"
  done
  printf 'personal\t%s\n' "$pd"
  for p in $(agents_conf_get "$pd/harness.conf" LIBRARIES); do
    case "$p" in /*) ;; \~/*) p="${HOME:-}/${p#\~/}" ;; *) p="$pd/$p" ;; esac
    printf 'personal-listed\t%s\n' "$p"
  done
  printf 'builtin\t%s\n' "${AGENTS_BUILTIN_DIR:-$AGENTS_ROOT/.agents/builtin}"
  return 0
}

_agents_libraries_scan() {  # the ones that exist, each directory once (compared physically, printed as reached)
  local src p logical physical seen="|"
  while IFS="$AGENTS_TAB" read -r src p; do
    if [ -z "$p" ] || [ ! -d "$p" ]; then continue; fi
    logical="$(cd "$p" 2>/dev/null && pwd)" || continue
    physical="$(cd "$p" 2>/dev/null && pwd -P)" || continue
    case "$seen" in *"|$physical|"*) continue ;; esac
    seen="$seen$physical|"
    printf '%s\t%s\n' "$src" "$logical"
  done < <(_agents_lib_candidates)
  return 0
}

# agents_library_candidates: source<TAB>path for every library the config names, existing or not
agents_library_candidates() {
  _agents_lib_candidates
}

# agents_library_paths: every directory the resolver would search, one per line, whether or not
# it exists (sync uses it to tell its own links from ones made by hand)
agents_library_paths() {
  _agents_lib_candidates | cut -f2
}

# agents_libraries_pin: scan once for this process and its children (verify does: it resolves
# several packs per run). Tied to the project root, so a run for another project scans its own.
agents_libraries_pin() {
  AGENTS_LIBS_PINNED="$(_agents_libraries_scan)"
  AGENTS_LIBS_PIN_ROOT="$AGENTS_ROOT"
  export AGENTS_LIBS_PINNED AGENTS_LIBS_PIN_ROOT
}

# agents_libraries: the search path, source<TAB>path per existing library, in order
agents_libraries() {
  if [ "${AGENTS_LIBS_PIN_ROOT:-}" = "$AGENTS_ROOT" ]; then
    if [ -n "${AGENTS_LIBS_PINNED:-}" ]; then printf '%s\n' "$AGENTS_LIBS_PINNED"; fi
    return 0
  fi
  _agents_libraries_scan
}

# agents_item_ok <kind> <path>: it's a real item of that kind, not just a name. A directory that
# isn't (an empty one, say) never counts, so it can't shadow a working item further down.
#   skill     <name>/SKILL.md
#   workflow  any of checks/ holding a file, skill/SKILL.md, agents/, mcp/, bin/
#   stack     lib.sh or checks/ holding a file
#   agent     <name>.md, mcp <name>.json: a file that isn't empty
_agents_has_file() { [ -n "$(find -L "$1" -mindepth 1 -maxdepth 1 -type f 2>/dev/null | head -n 1)" ]; }
agents_item_ok() {
  case "$1" in
    skills) [ -f "$2/SKILL.md" ] ;;
    workflows)
      [ -d "$2" ] || return 1
      if [ -f "$2/skill/SKILL.md" ] || [ -d "$2/agents" ] || [ -d "$2/mcp" ] || [ -d "$2/bin" ] \
         || [ -s "$2/harness.conf.snippet" ] || [ -s "$2/policy.conf.snippet" ]; then return 0; fi
      _agents_has_file "$2/checks" || _agents_has_file "$2/seed" ;;
    stacks) [ -d "$2" ] && { [ -f "$2/lib.sh" ] || _agents_has_file "$2/checks"; } ;;
    agents|mcp) [ -f "$2" ] && [ -s "$2" ] ;;
    *) return 1 ;;
  esac
}

# _agents_lib_entries <kind> <ok|unusable>: name<TAB>path<TAB>source for every entry with an item's
# shape (a directory, or a .md / .json file) in every library, in search order: the usable ones,
# or the ones agents_item_ok turns down
_agents_lib_entries() {
  local kind="$1" want="$2" ext="" src lib p n
  case "$kind" in skills|workflows|stacks) ;; agents) ext=md ;; mcp) ext=json ;; *) return 2 ;; esac
  while IFS="$AGENTS_TAB" read -r src lib; do
    [ -d "$lib/$kind" ] || continue
    while IFS= read -r p; do
      n="${p##*/}"
      if [ -n "$ext" ]; then
        case "$n" in *."$ext") n="${n%."$ext"}" ;; *) continue ;; esac
      else
        [ -d "$p" ] || continue
      fi
      agents_valid_name "$n" || continue
      if agents_item_ok "$kind" "$p"; then
        [ "$want" = ok ] || continue
      else
        [ "$want" = unusable ] || continue
      fi
      printf '%s\t%s\t%s\n' "$n" "$p" "$src"
    done < <(find -H "$lib/$kind" -mindepth 1 -maxdepth 1 2>/dev/null | LC_ALL=C sort)
  done < <(agents_libraries)
  return 0
}

# agents_lib_items <kind>: name<TAB>path<TAB>source for every item in every library, in search order
agents_lib_items() {
  _agents_lib_entries "$1" ok
}

# agents_unusable <kind>: name<TAB>path<TAB>source for what looks like an item but isn't one (an
# empty pack directory, a skill without SKILL.md, an empty agent file). Ignored; sync warns.
agents_unusable() {
  _agents_lib_entries "$1" unusable
}

# agents_missing_listed: each LIBRARIES entry in .agents/harness.conf that isn't here, one path per
# line: no directory, or an empty one (an uninitialized submodule is an empty directory). While one is missing, "no library has it" can't be known,
# so callers treat a name that doesn't resolve as a tooling problem. Personal-listed libraries are
# never counted: they're personal, and teammates never have them.
agents_missing_listed() {
  local src p
  while IFS="$AGENTS_TAB" read -r src p; do
    [ "$src" = project-listed ] || continue
    if [ ! -d "$p" ] || [ -z "$(ls -A "$p" 2>/dev/null)" ]; then printf '%s\n' "$p"; fi
  done < <(_agents_lib_candidates)
  return 0
}

# agents_missing_note: "LIBRARIES lists <path>[, <path>], which isn't here" (paths as written
# relative to the project), or nothing when every project-listed library is here
agents_missing_note() {
  local p list=""
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in "$AGENTS_ROOT"/*) p="${p#"$AGENTS_ROOT"/}" ;; esac
    list="${list:+$list, }$p"
  done < <(agents_missing_listed)
  if [ -n "$list" ]; then printf 'LIBRARIES lists %s, which isn'"'"'t here\n' "$list"; fi
  return 0
}

# agents_shared_workflow <name>: source<TAB>path of the first shared (not personal) library that
# has that workflow, the pack teammates and CI resolve to; 1 if none
agents_shared_workflow() {
  local src lib
  while IFS="$AGENTS_TAB" read -r src lib; do
    case "$src" in personal|personal-listed) continue ;; esac
    if agents_item_ok workflows "$lib/workflows/$1"; then printf '%s\t%s\n' "$src" "$lib/workflows/$1"; return 0; fi
  done < <(agents_libraries)
  return 1
}

# agents_items <kind> [team]: agents_lib_items, plus for skills each active workflow's skill/ and
# for agents each active workflow's agents/*.md, after all the libraries' own items. They come
# from the pack that wins for that workflow (the one whose checks run), never from a pack it
# shadows. With "team", a personal pack that wins also brings the items of the shared pack it
# shadows (team mode renders those: they're committed).
_agents_pack_items() {  # _agents_pack_items <kind> <workflow> <pack path> <source>
  local f n
  case "$1" in
    skills) if [ -f "$3/skill/SKILL.md" ]; then printf '%s\t%s\t%s\n' "$2" "$3/skill" "$4"; fi ;;
    agents)
      [ -d "$3/agents" ] || return 0
      find -H "$3/agents" -mindepth 1 -maxdepth 1 -name '*.md' 2>/dev/null | LC_ALL=C sort | while IFS= read -r f; do
        n="${f##*/}"; n="${n%.md}"
        agents_valid_name "$n" && agents_item_ok agents "$f" || continue
        printf '%s\t%s\t%s\n' "$n" "$f" "$4"
      done ;;
  esac
}
agents_items() {
  local w line wpath wsrc shared restoreglob=0
  agents_lib_items "$1" || return $?
  case "$1" in skills|agents) ;; *) return 0 ;; esac
  # WORKFLOWS is split unquoted below; noglob until it's done, restored only if we turned it on
  # (this runs in the caller's shell, unlike _agents_lib_candidates, so it must not stay changed).
  case $- in *f*) ;; *) set -f; restoreglob=1 ;; esac
  for w in $(agents_conf_get "$AGENTS_ROOT/.agents/harness.conf" WORKFLOWS); do
    agents_valid_name "$w" || continue
    line="$(agents_lookup workflows "$w")" || continue
    wsrc="${line##*"$AGENTS_TAB"}"; wpath="${line#*"$AGENTS_TAB"}"; wpath="${wpath%"$AGENTS_TAB"*}"
    _agents_pack_items "$1" "$w" "$wpath" "$wsrc"
    [ "${2:-}" = team ] || continue
    case "$wsrc" in personal|personal-listed) ;; *) continue ;; esac
    shared="$(agents_shared_workflow "$w")" || continue
    _agents_pack_items "$1" "$w" "${shared#*"$AGENTS_TAB"}" "${shared%%"$AGENTS_TAB"*}"
  done
  [ "$restoreglob" = 1 ] && set +f
  return 0
}

# agents_resolve_all <kind>: the winners, name<TAB>path<TAB>source, by name
agents_resolve_all() {
  agents_items "$1" | awk -F '\t' '!seen[$1]++' | LC_ALL=C sort
}

# agents_shadows <kind>: name<TAB>winner source<TAB>winner path<TAB>shadowed source<TAB>shadowed path
agents_shadows() {
  agents_items "$1" | awk -F '\t' '
    ($1 in wp) { printf "%s\t%s\t%s\t%s\t%s\n", $1, ws[$1], wp[$1], $3, $2; next }
    { wp[$1] = $2; ws[$1] = $3 }'
}

# agents_lookup <kind> <name>: that name's winning line; 1 if no library has it, 2 for a bad kind
# (agents: the libraries' own only; an active pack's agents come from agents_items, as sync uses)
agents_lookup() {
  local kind="$1" name="$2" src lib p line
  agents_valid_name "$name" || return 1
  case "$kind" in
    skills)
      line="$(agents_resolve_all skills | awk -F '\t' -v n="$name" '$1 == n')"
      [ -n "$line" ] || return 1
      printf '%s\n' "$line"
      return 0 ;;
    workflows|stacks|agents|mcp) ;;
    *) return 2 ;;
  esac
  while IFS="$AGENTS_TAB" read -r src lib; do
    case "$kind" in
      workflows|stacks) p="$lib/$kind/$name" ;;
      agents) p="$lib/agents/$name.md" ;;
      mcp) p="$lib/mcp/$name.json" ;;
    esac
    agents_item_ok "$kind" "$p" || continue
    printf '%s\t%s\t%s\n' "$name" "$p" "$src"
    return 0
  done < <(agents_libraries)
  return 1
}

# agents_resolve <kind> <name>: the winning path
agents_resolve() {
  local line
  line="$(agents_lookup "$1" "$2")" || return $?
  line="${line#*"$AGENTS_TAB"}"
  printf '%s\n' "${line%"$AGENTS_TAB"*}"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    libraries) agents_libraries ;;
    resolve|items|shadows)
      case "${2:-}" in
        skills|workflows|stacks|agents|mcp) ;;
        *) echo "libraries.sh: unknown kind '${2:-}' (skills, workflows, stacks, agents, mcp)" >&2; exit 2 ;;
      esac
      case "$1" in
        items) agents_items "$2" "${3:-}" ;;
        shadows) agents_shadows "$2" ;;
        *) if [ -n "${3:-}" ]; then agents_lookup "$2" "$3"; else agents_resolve_all "$2"; fi ;;
      esac ;;
    *) echo "usage: libraries.sh libraries | resolve <kind> [name] | items <kind> [team] | shadows <kind>" >&2; exit 2 ;;
  esac
fi

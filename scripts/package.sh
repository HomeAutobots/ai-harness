#!/usr/bin/env bash
# Build dist/ai-harness-<VERSION>.zip, the thing users install from. Dev-only files stay out:
# .git, .claude, CLAUDE.md, .github, docs, scripts, dist.
#   bash scripts/package.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
V="$(tr -d '[:space:]' < "$ROOT/VERSION")"
OUT="$ROOT/dist/ai-harness-$V.zip"
mkdir -p "$ROOT/dist"
rm -f "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir "$TMP/ai-harness"
(cd "$ROOT" && tar -cf - --exclude ./.git --exclude ./.claude --exclude ./CLAUDE.md --exclude ./.github \
  --exclude ./docs --exclude ./scripts --exclude ./dist --exclude __pycache__ --exclude '*.pyc' .) \
  | tar -C "$TMP/ai-harness" -xf -
(cd "$TMP" && zip -qr "$OUT" ai-harness)
if command -v sha256sum >/dev/null 2>&1; then SUM="$(sha256sum "$OUT")"; else SUM="$(shasum -a 256 "$OUT")"; fi
echo "${OUT#"$ROOT"/}  sha256 ${SUM%% *}"

#!/usr/bin/env bash
# Instala a skill /yt-sintese no Claude Code da máquina atual.
# Uso: bash scripts/install-skill.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_SKILL="$REPO_ROOT/skills/yt-sintese/SKILL.md"

if [[ ! -f "$SRC_SKILL" ]]; then
    echo "Skill source não encontrada: $SRC_SKILL" >&2
    exit 1
fi

DST_DIR="$HOME/.claude/skills/yt-sintese"
DST_SKILL="$DST_DIR/SKILL.md"

mkdir -p "$DST_DIR"
cp -f "$SRC_SKILL" "$DST_SKILL"

echo "Skill instalada em $DST_SKILL"
echo "Disponível como /yt-sintese em qualquer sessão Claude Code."

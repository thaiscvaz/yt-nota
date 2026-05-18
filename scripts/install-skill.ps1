# Instala a skill /yt-sintese no Claude Code da máquina atual.
# Uso: pwsh scripts/install-skill.ps1

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SrcSkill = Join-Path $RepoRoot "skills/yt-sintese/SKILL.md"

if (-not (Test-Path $SrcSkill)) {
    Write-Error "Skill source não encontrada: $SrcSkill"
}

$DstDir = Join-Path $HOME ".claude/skills/yt-sintese"
$DstSkill = Join-Path $DstDir "SKILL.md"

if (-not (Test-Path $DstDir)) {
    New-Item -ItemType Directory -Path $DstDir -Force | Out-Null
}

Copy-Item -Path $SrcSkill -Destination $DstSkill -Force
Write-Host "Skill instalada em $DstSkill"
Write-Host "Disponível como /yt-sintese em qualquer sessão Claude Code."

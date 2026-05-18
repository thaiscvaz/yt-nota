# Changelog

Tudo que muda nesse projeto vai aqui. Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [0.2.1] - 2026-05-18

### Adicionado
- Testes pra `vault.py` (26 testes): `_yaml_quote`, `_as_str`, `write_draft`, round-trip `_parse_draft`, `finalize_draft`, channel card create/append, edge cases (sem transcript, sem channel card, título com caracteres problemáticos)
- Cópia da skill `/yt-sintese` em `skills/yt-sintese/SKILL.md` dentro do repo (canônico, versionado)
- Script `scripts/install-skill.ps1` (Windows) e `scripts/install-skill.sh` (Unix) pra instalar a skill no `~/.claude/skills/` em outra máquina
- Seção "Setup em outra máquina" no README

### Corrigido
- Modo `--dry-run` agora reporta corretamente "N/N preview(s) gerado(s)" em vez de "N/N drafts criados"

## [0.2.0] - 2026-05-18

### Alterado
- **Remoção da dependência Anthropic API.** Síntese agora acontece via skill `/yt-sintese` no Claude Code, sem custo de API
- Pipeline em 2 passos: `yt-nota <url>` extrai e cria draft → `/yt-sintese` no Claude Code processa e finaliza

### Adicionado
- Skill global `/yt-sintese` em `~/.claude/skills/yt-sintese/SKILL.md`
- Subcomando `yt-nota --finalize <draft>` (chamado pela skill) pra montar a nota final + transcript + channel card
- Subcomando `yt-nota --list` pra listar drafts pendentes
- `write_draft` em `vault.py` que escreve em `<vault>/30-Recursos/Literatura/_drafts/`
- `_as_str` helper pra normalizar `datetime.date` retornado pelo YAML parser

### Removido
- `src/yt_nota/synthesizer.py` (chamava Anthropic SDK)
- `src/yt_nota/prompts/synthesis.md` (movido pra skill `/yt-sintese`)
- Dependências `anthropic` e `python-frontmatter`

### Adicionado (deps)
- `PyYAML` pra parsing do frontmatter dos drafts

## [0.1.0] - 2026-05-16

### Adicionado
- Scaffold inicial: CLI Python com extração yt-dlp + síntese Anthropic SDK + escrita no vault Obsidian
- Suporte a URL única, múltiplas, playlist, arquivo, stdin
- Parser VTT robusto com dedup de auto-captions overlapping (16 testes)
- Channel card auto-criado em `30-Recursos/Notas/<Canal>.md`
- Flag `--with-cookies` pra cookies do Chrome (vídeos restritos)
- Flag `--tema` pra atualização opcional de MOC temático
- Repo `https://github.com/thaiscvaz/yt-nota`

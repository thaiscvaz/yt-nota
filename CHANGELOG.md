# Changelog

Tudo que muda nesse projeto vai aqui. Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [0.2.4] - 2026-05-26

### Adicionado
- **`--sleep N`**: segundos de espera entre vídeos no batch (default 0). Espalha as chamadas no tempo. Sugestão: 15-30s pra batch >5.
- **Detecção de rate limit 429 + parada precoce**: ao 2º HTTP 429 consecutivo do endpoint `timedtext`, a wave para imediatamente. URLs restantes são salvas em `<queue>.pending.txt`. Exit code 2 sinaliza parada por rate limit.
- **`--retry-pending FILE`**: retoma uma wave parcial a partir de `.pending.txt`. Se completar tudo, o pending é apagado; se parar de novo por 429, atualiza com as URLs restantes.
- Nova exceção `RateLimitError(ExtractError)` em `extractor.py`, propagada em `_fetch_vtt` quando status_code é 429.

### Why
Operação real no canal "A Cara da Riqueza" (554 vídeos) bateu 429 logo no início. Sem parada precoce, gastei requisições inúteis em 540 URLs (todas com 429). Com sleep + parada precoce + retry-pending, dá pra rodar waves de 8 com pausa segura e retomar limpo após reset do rate limit (1-24h).

## [0.2.3] - 2026-05-24

### Alterado
- **Transcripts brutos agora ficam em subpasta `transcripts/`** dentro de cada canal, separados das notas síntese. Estrutura nova: `<vault>/30-Recursos/Literatura/<Canal>/3-<id>-<slug>.md` + `<Canal>/transcripts/3-<id>-<slug>.transcript.md`. Wikilinks continuam funcionando (Obsidian resolve por nome).
- **Skill `/yt-sintese` reescrita pra notas mais densas:** parágrafos curtos (3-5 linhas), uso obrigatório de tabelas pra comparação numérica, sub-headers `###` dentro de "O que defende" pra pontos multifacetados, bullets pra valores/exemplos. Dicionário expandido (5-8 termos com valor de referência + analogia). Notas permanentes sobem pra 2-4. Cada ponto de "O que defende" agora cobre 8-20 linhas (vs 2-4 antes).

### Adicionado
- Teste de regressão `test_finalize_puts_transcript_in_subfolder` em `test_vault.py`.
- Idempotência (`is_video_already_processed`) agora também varre `<Canal>/transcripts/` pra detectar processamento prévio.

### Migração feita no vault
- Transcripts existentes em `Bau-de-Experiencias-3D/`, `STLFLIX-BR-Impressao-3D/` e `Matheus-Battisti-Hora-de-Codar/` movidos pros subfolders `transcripts/`.

## [0.2.2] - 2026-05-24

### Adicionado
- **Idempotência por `video_id`**: nova função `vault.is_video_already_processed` e check em `cli._process_single`. Se uma nota final ou draft pendente já tem o `video_id`, o pipeline pula com log informativo. Permite rodar a mesma queue várias vezes sem duplicar.
- Flag `--force` no CLI pra sobrescrever a dedup quando realmente quiser reprocessar.
- Testes de regressão pra dedup (5 novos em `test_vault.py`, total 47).
- **Skill `/yt-sintese` agora preserva configs técnicas literalmente**: nova seção "Preservação de configurações técnicas" obriga capturar valores numéricos exatos (temperatura em °C, velocidade em mm/s, flow %, pressure advance, layer height em mm). Vale pra notas onde a Thais vai usar como receita prática (impressão 3D, etc).
- **Queues curadas pro canal "Bau de Experiencias 3D"**: `queues/bau-wave1.txt` (5 vídeos pra validar estilo), `bau-wave2.txt` (13 do currículo numerado), `bau-wave3.txt` (6 de monetização).

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

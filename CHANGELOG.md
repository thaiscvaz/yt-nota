# Changelog

Tudo que muda nesse projeto vai aqui. Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [0.4.0] - 2026-06-04

### Mudado
- **Drafts migram pra `Pipeline/_processar/`** (Fase A+B do refatoramento do vault Obsidian). Path antigo `30-Recursos/Literatura/_drafts/` movido pra `30-Recursos/Literatura/Pipeline/_processar/`. Constante `DRAFTS_DIR` renomeada pra `PROCESSAR_DIR` em `config.py`; propagado em `vault.py`, `cli.py` e fixtures de teste.
- Notas finais agora seguem 7 domínios hierárquicos: `30-Recursos/Literatura/<dominio>/<canal>/` (ex: `IA-Engenharia/Akita/`, `Financas/REDACTED-CHANNEL/`). Mapping canal → domínio em `config/channel_domains.yaml`.
- **Channel cards migram pra `30-Recursos/Notas/Cards-de-Pessoa/<canal>.md`** (era `Notas/<canal>.md` flat). Nova constante `CARDS_DE_PESSOA_DIR` em `config.py`.
- Fixtures de teste (`test_vault.py`, `test_whisper_fallback.py`) atualizadas pra usar `PROCESSAR_DIR` e novo path.
- README, `docs/plan.md`, `skills/yt-sintese/SKILL.md`, `CLAUDE.md` atualizados.

### Adicionado
- **`src/yt_nota/domain.py`**: módulo novo de resolução de domínio em cascata: flag `--dominio` (override CLI) → frontmatter `dominio:` do draft → lookup em `config/channel_domains.yaml` → `DomainResolutionError` com instrução clara se nada bater. `validate()` rejeita domínios fora dos 7 da REGRAS-VAULT.
- **Constantes em `config.py`**: `VALID_DOMINIOS` (frozenset com os 7 válidos), `DOMAINS_CONFIG_PATH`, `CARDS_DE_PESSOA_DIR`.
- **Kwarg `dominio` em `write_draft`** e **`dominio_override` em `finalize_draft`**: permitem skill `/yt-sintese` ou CLI passar override explícito. Frontmatter do draft ganha campo `dominio:` opcional.
- **`is_video_already_processed` agora varre TODOS os subdomínios** de `Literatura/` pra dedup (cobre notas legadas em path flat anteriores à migração).

### Compatibilidade
- **Quebra silenciosa pra canais não mapeados.** Se canal não está em `config/channel_domains.yaml` E ninguém passou `--dominio`, o `finalize` aborta com `DomainResolutionError`. Solução: 1 linha no YAML, ou `--dominio X`.
- Notas legadas em `Literatura/<canal>/` flat continuam funcionando pra dedup. Não migra automático; a Thais decide se move manualmente.
- Channel cards legados em `Notas/<canal>.md` ficam órfãos. Primeira execução pós-upgrade cria card novo em `Notas/Cards-de-Pessoa/<canal>.md`.

### Why
Refatoramento do vault Obsidian (2026-06-04) hierarquizou `30-Recursos/Literatura/` em 7 domínios (Mestrado, Saude, Financas, IA-Engenharia, Carreira, Impressao-3D, Metodo) + sub-pasta `Pipeline/` pros drafts em transito. Documentação canônica em `30-Recursos/Sistema/REGRAS-VAULT.md` e `MIGRACAO-PROJETOS.md`. Pasta `_drafts/` flat foi substituída por `Pipeline/_processar/` (rename de constante alinhado com nome da pasta). Cards-de-Pessoa subdomínio criado na Fase B pra reduzir entropia de `Notas/`.

## [0.3.0] - 2026-05-31

### Adicionado
- **Fallback Whisper local quando YouTube responde 429 nas legendas.** Novo módulo `whisper_fallback.py` baixa só o áudio (formato 139, m4a 49 kbps, ~5 MB por 13 min) via yt-dlp programático e transcreve com `faster-whisper` em CPU int8. O endpoint de áudio do YouTube não está rate-limited (só o `timedtext`), então o fallback funciona mesmo quando legendas falham. Modelo default `small` (244 MB) processa a ~0.3x realtime em CPU. 13 min de vídeo viram 4 min de processo.
- **Optional dependency `[whisper]`**: `pip install yt-nota[whisper]` instala `faster-whisper>=1.0.0`. Sem essa instalação, o fallback é silenciosamente skip e o comportamento da v0.2.4 (parada precoce + `.pending.txt`) é preservado.
- **Flags CLI novas**:
  - `--whisper-fallback` / `--no-whisper-fallback`: liga/desliga (default ligado)
  - `--whisper-model {tiny,base,small,medium,large,large-v2,large-v3}`: escolha o modelo (default `small`)
- **Env vars**:
  - `YT_NOTA_WHISPER_FALLBACK=0` desativa
  - `YT_NOTA_WHISPER_MODEL=base` muda o modelo default
- **Frontmatter do draft**: campo `transcript_origem` agora aceita `whisper-local` (além de `auto`/`manual`). A skill `/yt-sintese` lê esse campo e pode aplicar lógica diferente se quiser (atualmente todos seguem o mesmo fluxo de síntese).
- Função `extract_transcript()` ganhou kwargs `whisper_fallback`, `whisper_model`, `with_cookies`. Quando captação por VTT falha com 429 e `whisper_fallback=True`, cai automático no Whisper.

### Why
Em 2025 o endpoint `timedtext` do YouTube passou a rate-limitar muito mais agressivo. Em cenário real de batch (vários vídeos longos, em PT-BR técnico), nem backoff exponencial até 45s nem cookies do browser nem atualização pro yt-dlp pré-release foram suficientes. Todas as URLs retornavam 429. O endpoint de áudio (`googlevideo`) continuou livre. Whisper local resolveu definitivamente, sem dependência do servidor de legendas. Esse cenário virou comum o suficiente em 2026 que justifica embutir o fallback no CLI.

### Compatibilidade
- **Retrocompatível**: comportamento default mudou de "para no 429 com pending.txt" pra "tenta Whisper, se Whisper indisponível mantém parada precoce". Quem não tem `faster-whisper` instalado vê o mesmo comportamento da v0.2.4. Quem quer o comportamento antigo explícito: `--no-whisper-fallback`.
- Campo `is_auto` (bool) do dict retornado por `extract_transcript()` ainda existe; só foi adicionado um campo `origin` (str) que diferencia `manual` / `auto` / `whisper-local`.

## [0.2.4] - 2026-05-26

### Adicionado
- **`--sleep N`**: segundos de espera entre vídeos no batch (default 0). Espalha as chamadas no tempo. Sugestão: 15-30s pra batch >5.
- **Detecção de rate limit 429 + parada precoce**: ao 2º HTTP 429 consecutivo do endpoint `timedtext`, a wave para imediatamente. URLs restantes são salvas em `<queue>.pending.txt`. Exit code 2 sinaliza parada por rate limit.
- **`--retry-pending FILE`**: retoma uma wave parcial a partir de `.pending.txt`. Se completar tudo, o pending é apagado; se parar de novo por 429, atualiza com as URLs restantes.
- Nova exceção `RateLimitError(ExtractError)` em `extractor.py`, propagada em `_fetch_vtt` quando status_code é 429.

### Why
Operação real no canal "A REDACTED-CHANNEL" (554 vídeos) bateu 429 logo no início. Sem parada precoce, gastei requisições inúteis em 540 URLs (todas com 429). Com sleep + parada precoce + retry-pending, dá pra rodar waves de 8 com pausa segura e retomar limpo após reset do rate limit (1-24h).

## [0.2.3] - 2026-05-24

### Alterado
- **Transcripts brutos agora ficam em subpasta `transcripts/`** dentro de cada canal, separados das notas síntese. Estrutura nova: `<vault>/30-Recursos/Literatura/<Canal>/3-<id>-<slug>.md` + `<Canal>/transcripts/3-<id>-<slug>.transcript.md`. Wikilinks continuam funcionando (Obsidian resolve por nome).
- **Skill `/yt-sintese` reescrita pra notas mais densas:** parágrafos curtos (3-5 linhas), uso obrigatório de tabelas pra comparação numérica, sub-headers `###` dentro de "O que defende" pra pontos multifacetados, bullets pra valores/exemplos. Dicionário expandido (5-8 termos com valor de referência + analogia). Notas permanentes sobem pra 2-4. Cada ponto de "O que defende" agora cobre 8-20 linhas (vs 2-4 antes).

### Adicionado
- Teste de regressão `test_finalize_puts_transcript_in_subfolder` em `test_vault.py`.
- Idempotência (`is_video_already_processed`) agora também varre `<Canal>/transcripts/` pra detectar processamento prévio.

### Migração feita no vault
- Transcripts existentes em `REDACTED-CHANNEL/`, `REDACTED-CHANNEL/` e `REDACTED-CHANNEL/` movidos pros subfolders `transcripts/`.

## [0.2.2] - 2026-05-24

### Adicionado
- **Idempotência por `video_id`**: nova função `vault.is_video_already_processed` e check em `cli._process_single`. Se uma nota final ou draft pendente já tem o `video_id`, o pipeline pula com log informativo. Permite rodar a mesma queue várias vezes sem duplicar.
- Flag `--force` no CLI pra sobrescrever a dedup quando realmente quiser reprocessar.
- Testes de regressão pra dedup (5 novos em `test_vault.py`, total 47).
- **Skill `/yt-sintese` agora preserva configs técnicas literalmente**: nova seção "Preservação de configurações técnicas" obriga capturar valores numéricos exatos (temperatura em °C, velocidade em mm/s, flow %, pressure advance, layer height em mm). Vale pra notas onde a User vai usar como receita prática (impressão 3D, etc).
- **Queues curadas pro canal "REDACTED-CHANNEL"**: `queues/redacted-wave1.txt` (5 vídeos pra validar estilo), `redacted-wave2.txt` (13 do currículo numerado), `redacted-wave3.txt` (6 de monetização).

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
- Repo `https://github.com/user-handle/yt-nota`

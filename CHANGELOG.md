# Changelog

Tudo que muda nesse projeto vai aqui. Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [Unreleased]

### Adicionado
- **Registro de eventos no `registry.db`** (Projeto Buma B-09, entrega 1). Tabela `events`, só de acréscimo, com dois tipos de linha: `transition`, gravada pelo próprio registro na mesma transação de cada mudança de status (falha ao gravar o evento desfaz a mudança), e `activity`, gravada pela etapa que faz o trabalho, com ciclo de vida no vocabulário XES (`start`, `complete`, `ate_abort`, `schedule`, `withdraw`). Cada linha separa a hora do acontecimento (`occurred_at`) da hora do registro (`recorded_at`), traz `run_id` (de `YT_NOTA_RUN_ID` ou criado e exportado pelo processo), `attempt_id`, `actor`, `decided_by` e uma chave única que ignora regravação. A extração grava a tentativa de transcrição (`transcribe`: início, fim com origem, idioma, segmentos e modelo do Whisper, ou `ate_abort` por rate limit). Esquema passa a `2` com migração idempotente: `videos` não muda e `meta` guarda `events_d1_since`, o início da cobertura. `--registry events-export --events-out <csv>` exporta com `case_id, activity, lifecycle, timestamp` primeiro. O backfill marca suas transições como retrospectivas. Nada disso muda a saída do CLI; falha ao gravar um evento de atividade vira warning.
- **Eventos ligados a objetos (OCEL 2.0).** Tabela `event_objects` (`event_id`, `object_type`, `object_id`, `qualifier`); todo evento liga a `source` do caso, e a extração liga também a `transcript` (id com o hash do texto) e o `draft`. Tipos reservados para a próxima entrega: `note`, `profile`, `concept_page`. `--registry events-export --events-format ocel2` grava OCEL 2.0 JSON, conferido com `pm4py.read_ocel2_json`.
- **Medidas e versões nos eventos:** hash e número de palavras da transcrição e do draft, idioma, segmentos, versão do yt-nota lida da fonte (`__version__`, não do metadado instalado, que estava em 0.3.0 com o código em 0.5.0), commit e se a árvore estava suja. A leitura de metadados (`fetch_metadata`) também vira tentativa; vídeo indisponível, que antes falhava sem rastro, fica como `ate_abort` com o erro.
- **Correções da revisão de 18/10 (antes do primeiro commit).** A chave do evento identifica a operação, não o relógio: `op_id` opcional (reenvio da mesma operação não duplica), senão um id novo por chamada, então duas decisões no mesmo segundo viram dois eventos; reenvio com conteúdo diferente levanta `EventConflict` (cada evento guarda `payload_sha256`). Leitura do status e escrita na mesma transação (`BEGIN IMMEDIATE`), para o `from` da transição ser o status de fato substituído. `mark_downloaded(evidence=...)` guarda caminho, hash e mtime da evidência sem transformar o mtime em hora do download. `open_registry(readonly=True)` abre em `mode=ro`, sem criar esquema nem migrar. O Whisper devolve o modelo que carregou, e o evento separa `whisper_model_requested` de `whisper_model_effective`. Saídas antes sem evento ganham `route`: domínio não resolvido (`ate_abort`), já processado e playlist não expandida (`withdraw`), e `route complete` com o domínio escolhido.
- **Correções da terceira revisão.** Tabela `operations` registra todo `op_id` aplicado, mude ou não o status, então o reenvio de uma operação que não gerou transição nunca desfaz um estado posterior. Simulações (`--dry-run`) e as ações de leitura do `--registry` (`stats`, `list`, `filter`, `events-export`) abrem o banco só para leitura: não criam o arquivo, não migram, não gravam; escrita num registro só leitura levanta erro.
- Testes: 28 novos em `tests/test_events.py` (273 no total; os 245 anteriores sem mudança).
- **Pendências da quarta revisão (P1, P2).** `--registry events-export` num banco ainda v1 (sem a tabela `events`) exporta o log vazio, CSV só com o cabeçalho ou OCEL sem eventos, avisa que a cobertura de eventos não começou e sai com 0; antes levantava `no such table: events`. `--registry mark --dry-run` é recusado antes de abrir o banco, com mensagem que aponta `--registry filter`; antes falhava na primeira escrita com `registry opened read-only`. Quatro testes novos (277 no total).

### Mudado
- **A nota final segue o frontmatter mínimo do vault.** `moc: "[[MOC-<Fonte>]]"` substitui `up:` (deprecado), entram `created`, `updated`, `video_id`, `aliases` (o título real do vídeo, pra nota ser achada pelo título e não só pelo id do arquivo) e a tag do domínio. O título da nota é `# <título>`, sem o id na frente.
- **Toda pasta de fonte ganha um hub.** O `--finalize` usa o `MOC-*.md` que já existe na pasta ou cria `MOC-<Fonte>.md` junto com a primeira nota, e sempre adiciona a nota nova em `## Literatura` (exceto em MOC com Dataview). Antes só atualizava MOC quando o draft tinha `tema:`.
- **Sem travessão nos textos gerados pelo CLI**: título da nota, cabeçalho do draft e do transcript, linha do card do canal (agora `[[nota|Título]] (data)`).

### Corrigido
- **Erro de extração diz o que aconteceu** (Projeto Buma B-12). `ExtractError` ganha `kind`, tirado da mensagem do yt-dlp: `rate_limited`, `members_only`, `age_restricted`, `private`, `removed`, `geo_blocked`, `live_or_upcoming`, `no_formats`, `unavailable` (o YouTube não deu motivo) ou `unknown`. A mensagem traz o tipo em português, e o evento `fetch_metadata` `ate_abort` grava `error_kind`. Antes, vídeo privado, removido, exclusivo de membros e limite de requisições apareciam todos como "yt-dlp falhou". 22 testes novos (302 no total), um deles com a mensagem real de um id inexistente.
- **Vídeo público dado como "not available".** Sem runtime de JavaScript, o cliente padrão do yt-dlp recebe `UNPLAYABLE` em alguns vídeos curtos e levanta "This video is not available" por falta de formatos, embora título e duração tenham vindo. O extrator só precisa de metadados e legendas, então liga `ignore_no_formats_error`; erros de vídeo removido ou privado continuam levantando. Três shorts da A Cara da Riqueza ficaram uma semana em `descoberto` assim, e toda rotina terminou com código 1. Três testes novos (280 no total).
- **Dedup pulava vídeo citado na descrição de outro.** O índice dos drafts casava `v=<id>` no arquivo inteiro, e a descrição do vídeo fica no draft; canal que linka os próprios vídeos perdia esses vídeos, contados como "já processados". Em drafts o índice agora lê só o frontmatter.

### Adicionado
- **Registro de qualidade por nota no `--finalize`** (`quality.py`, `data/finalize-log.jsonl`). Duas checagens determinísticas que nunca bloqueiam: cada citação em blockquote é procurada no transcript (literal, parcial, não encontrada; transcript em outro idioma sai como traduzida) e, se `YT_NOTA_WRITING_GATE` aponta pra um arquivo Python com `gate_text(text)`, o resultado do gate de estilo entra na mesma linha.
- Testes: 17 novos em `tests/test_quality.py` e `tests/test_vault_compliance.py` (221 no total).

### Mudado (anterior)
- **Taxonomia de domínios agora é configurável** (era hardcoded no source). `config.py` deixou de declarar `VALID_DOMINIOS` como frozenset fixo; passou a carregar `config/domains.yaml` (pessoal, não versionado) com fallback pra `config/domains.example.yaml` (genérico, versionado), via `get_valid_dominios()` + `reload_dominios()` — mesma cascata do `channel_domains.yaml`. `domain.py` e `vault.py` chamam a função em runtime. Objetivo: o repo deixa de carregar a taxonomia pessoal do usuário e vira ferramenta genérica/forkável (padrão FrankMD). Migração: copie `domains.example.yaml` pra `domains.yaml` e edite com seus domínios.

### Adicionado
- **O CLI agora escreve no registro por conta própria.** Antes só a automação gravava, via `--registry mark`; extração e `--finalize` não tocavam o SQLite, então um vídeo baixado direto pelo CLI ficava invisível pro registro. A extração grava `baixado` com título, canal, data, duração e origem do transcript; o `--finalize` grava `curado` com o veredicto (`--verdict`, default `FICHAMENTO`) e os paths da nota. Um reprocesso com `--force` nunca rebaixa um vídeo já curado. Falha de SQLite vira warning: o vault continua sendo a rede de segurança.
- **Dedup consulta o registro antes do vault.** Vídeo `baixado` ou `curado` é pulado sem rede e sem varrer o vault. Fecha o buraco do descarte: um vídeo com veredicto `DESCARTE` não deixa nota, então só o registro sabe que ele já foi visto. `descoberto` e `historico` continuam passando pelo scan do vault.
- **`yt-nota --triage`** lista os drafts pendentes em TSV (`video_id`, canal, duração, data, título, arquivo) lendo só o frontmatter. Serve pra decidir pelo título o que merece síntese antes de gastar a leitura do transcript inteiro.
- **`yt-nota --discard <draft>... --reason "..."`** descarta drafts sem sintetizar: grava `DESCARTE` com a razão no registro e apaga o draft. Recusa rodar sem `--reason`, e preserva o draft se o registro falhar ou se o draft não tiver `video_id`, senão o vídeo voltaria a ser baixado.
- **`yt-nota --registry adopt-drafts`** registra como `baixado` os drafts pendentes que o registro não conhece (aceita `--dry-run`). Migração única pra quem já tinha drafts antes desta versão.
- Testes: 14 novos em `tests/test_cli_registry.py` (204 no total); fixture autouse isola a suíte do `data/registry.db` real.
- **Registro durável do que já passou pelo pipeline** (`registry.py`, SQLite em `data/registry.db`). Substitui o `processados.json` do vault, que era um dicionário `{canal: [ids]}` sem semântica — o próprio `_comment` dele admitia misturar "baixado, fichado ou pulado de propósito", então não dava pra responder "isso já foi curado?" nem "qual foi o veredicto?". O registro tem ciclo de vida explícito (`descoberto` → `baixado` → `curado`/`erro`) e guarda o veredicto do portão de curadoria (`DESCARTE`/`PROPAGA`/`ATOMICA`/`FICHAMENTO`, Fase 3) com a razão e os paths tocados no vault. A idempotência é estrutural, não convencionada: `mark_discovered` usa `INSERT OR IGNORE` e devolve se a linha é nova, então redescobrir um vídeo pelo RSS nunca rebaixa o status de um já curado — a regressão de 2026-07-21 vira impossível por schema, não por disciplina.
- **`yt-nota --registry <stats|list|filter|mark|backfill>`** — inspeção e administração do registro. `list` filtra por `--channel`/`--status`/`--verdict`/`--limit`; `backfill` importa o `processados.json` legado (aceita `--dry-run` e `--from-file`). Path do banco sobrescrevível com `--db`.
- **`filter` e `mark`, os verbos que a skill `/curadoria-incremental` consome.** `filter` imprime só os ids desconhecidos (consulta pura: não cria linha como efeito colateral, senão o próprio diff envenenaria o estado). `mark` grava a transição de status e **se recusa a marcar `curado` sem `--verdict`, ou `erro` sem `--reason`** — curar sem dizer o quê era exatamente o buraco do `processados.json`. Ambos aceitam `--ids` ou `--from-file`, e o arquivo pode trazer `videoId<TAB>título`: o título já vem no RSS, jogá-lo fora obrigaria a rebuscar depois.
- **`Registry.enrich()`** — preenche metadado que faltava sem sobrescrever o que já existe (`COALESCE`). Como `mark_discovered` usa `INSERT OR IGNORE`, sem isso um título que chega numa run posterior (linha importada pelo backfill, que só tinha o id) nunca entraria.
- **O backfill importa como `historico`, nunca como `curado`.** O JSON legado não distingue o que foi fichado do que foi pulado; marcar tudo como curado seria inventar procedência que o dado não tem. `historico` significa exatamente "o pipeline antigo já viu esse id" — suficiente pra dedup, honesto sobre o resto. Executado em 2026-07-25: 5.007 ids de 21 canais.
- `config/domains.example.yaml` (template genérico) + `get_valid_dominios()`/`reload_dominios()` em `config.py`.
- `tests/conftest.py` (fixture autouse desacopla a suíte da taxonomia pessoal) + `tests/test_config_dominios.py` (cobre a cascata personal → example).
- Testes: 47 novos (188 no total). `tests/test_registry.py` cobre ciclo de vida, a regressão de 2026-07-21, veredicto inválido, `filter_new` acima do teto de variáveis do SQLite (1.200 ids), `enrich` e a semântica do backfill (não sobrescreve linha real, ignora entradas malformadas, idempotente). `tests/test_registry_cli.py` cobre o contrato de linha de comando que a skill consome — separado de propósito: lá é a camada de dados, aqui é a interface.

## [0.5.0] - 2026-06-09

### Corrigido
- **Bug do Whisper "traduzindo" áudio EN pra PT (W33 vid 2).** Causa raiz: não era auto-detect — `PREFERRED_LANGS` escolhia a auto-caption pt-BR (tradução de máquina do YouTube) pra vídeos EN, e no 429 o idioma dessa legenda virava hint do Whisper, forçando `language=pt` em áudio EN (= tradução). Agora o hint vem do idioma ORIGINAL do áudio (`info["language"]` do yt-dlp, novo campo `language` em `normalize_video_info`); auto-captions traduzidas nunca viram hint (`_whisper_lang_hint`). Sem candidato confiável, deixa o Whisper auto-detectar.
- **429 com Whisper indisponível voltou a parar a wave.** Desde a v0.3.0, sem `faster-whisper` instalado o 429 degradava silenciosamente: drafts SEM transcript pra wave inteira, queimando a queue. Agora, se o fallback é acionado e falha (não instalado, áudio falhou), o `RateLimitError` propaga — parada precoce + pending.txt como prometido na v0.3.0.
- **Off-by-one no pending.txt:** a PRIMEIRA URL do streak de 429 era descartada do arquivo de retomada (perdida pra sempre — nunca virou draft e a dedup não acusava). O pending agora começa na primeira URL do streak.
- **`httpx` declarado em `dependencies`** (era usado em `extractor.py` mas não declarado — instalação limpa quebrava com ImportError).

### Adicionado
- **Dedup ANTES da rede**: `video_id` é parseado direto da URL (`video_id_from_url`) e buscado em todo o vault (`vault.find_video_anywhere`) antes do `extract_info`. Re-rodar uma queue já processada vira no-op instantâneo: zero chamadas de rede, zero sleep, zero exposição a rate limit.
- **Índice de dedup em memória** (`vault._dedup_index`): cada diretório do vault é varrido UMA vez por execução, em vez de reler todos os arquivos do canal a cada vídeo do batch. `write_draft` registra o draft novo no índice (mesma URL 2x na mesma queue continua dedupando); `finalize_draft` invalida o índice.
- **Sleep inteligente**: `--sleep` só dorme antes de vídeos que realmente vão à rede. Vídeos pulados por dedup não custam mais N segundos cada.
- **`<queue>.failed.txt`**: URLs com falha individual (erro de extração, 429 isolado absorvido pelo streak-reset) eram perdidas em silêncio — só o exit code 1 sinalizava. Agora são salvas num arquivo retomável com `--retry-pending`.
- **Guard do finalize**: valida que o body contém as 7 seções da skill `/yt-sintese` ANTES de deletar o draft (proteção contra body truncado/fora de formato). Bypass com `--skip-body-check`. Automatiza a validação manual que segurou a sessão de 01/06 em 0 erros.
- **Preferência por track original nas auto-captions**: pra vídeo cujo idioma original é conhecido, a track `-orig` (ou código do idioma do áudio) vence traduções de máquina em `PREFERRED_LANGS`. Legendas manuais continuam vencendo tudo.
- Testes: 24 novos (134 total) — `test_extractor.py` novo (video_id_from_url, _pick_subtitle, _whisper_lang_hint, re-raise no 429), loop do `_cmd_extract` (off-by-one, failed.txt, dedup pré-rede, --force), guard do finalize, índice de dedup.

### Compatibilidade
- `_try_whisper_fallback` mudou kwarg `preferred_lang` → `lang_hint` (interno, sem impacto externo).
- Vídeos EN que antes geravam transcript via auto-caption pt-BR traduzida agora geram transcript EN original (a síntese pra PT-BR acontece na skill, como sempre). Comportamento pra vídeos PT inalterado.
- `--whisper-model` agora deriva as choices de `SUPPORTED_MODELS` (mesma lista, fonte única).

## [0.4.0] - 2026-06-04

### Mudado
- **Drafts migram pra `Pipeline/_processar/`** (Fase A+B do refatoramento do vault Obsidian). Path antigo `30-Recursos/Literatura/_drafts/` movido pra `30-Recursos/Literatura/Pipeline/_processar/`. Constante `DRAFTS_DIR` renomeada pra `PROCESSAR_DIR` em `config.py`; propagado em `vault.py`, `cli.py` e fixtures de teste.
- Notas finais agora seguem domínios hierárquicos: `30-Recursos/Literatura/<dominio>/<canal>/` (ex: `<Dominio>/<Canal>/`). Mapping canal → domínio em `config/channel_domains.yaml`.
- **Channel cards migram pra `30-Recursos/Notas/Cards-de-Pessoa/<canal>.md`** (era `Notas/<canal>.md` flat). Nova constante `CARDS_DE_PESSOA_DIR` em `config.py`.
- Fixtures de teste (`test_vault.py`, `test_whisper_fallback.py`) atualizadas pra usar `PROCESSAR_DIR` e novo path.
- README, `docs/plan.md`, `skills/yt-sintese/SKILL.md`, `CLAUDE.md` atualizados.

### Adicionado
- **`src/yt_nota/domain.py`**: módulo novo de resolução de domínio em cascata: flag `--dominio` (override CLI) → frontmatter `dominio:` do draft → lookup em `config/channel_domains.yaml` → `DomainResolutionError` com instrução clara se nada bater. `validate()` rejeita domínios fora da taxonomia configurada.
- **Constantes em `config.py`**: `VALID_DOMINIOS` (frozenset com os 7 válidos), `DOMAINS_CONFIG_PATH`, `CARDS_DE_PESSOA_DIR`.
- **Kwarg `dominio` em `write_draft`** e **`dominio_override` em `finalize_draft`**: permitem skill `/yt-sintese` ou CLI passar override explícito. Frontmatter do draft ganha campo `dominio:` opcional.
- **`is_video_already_processed` agora varre TODOS os subdomínios** de `Literatura/` pra dedup (cobre notas legadas em path flat anteriores à migração).

### Compatibilidade
- **Quebra silenciosa pra canais não mapeados.** Se canal não está em `config/channel_domains.yaml` E ninguém passou `--dominio`, o `finalize` aborta com `DomainResolutionError`. Solução: 1 linha no YAML, ou `--dominio X`.
- Notas legadas em `Literatura/<canal>/` flat continuam funcionando pra dedup. Não migra automático; o usuário move manualmente.
- Channel cards legados em `Notas/<canal>.md` ficam órfãos. Primeira execução pós-upgrade cria card novo em `Notas/Cards-de-Pessoa/<canal>.md`.

### Why
Refatoramento do vault Obsidian (2026-06-04) hierarquizou `30-Recursos/Literatura/` em domínios temáticos + sub-pasta `Pipeline/` pros drafts em transito. Documentação canônica em `30-Recursos/Sistema/REGRAS-VAULT.md` e `MIGRACAO-PROJETOS.md`. Pasta `_drafts/` flat foi substituída por `Pipeline/_processar/` (rename de constante alinhado com nome da pasta). Cards-de-Pessoa subdomínio criado na Fase B pra reduzir entropia de `Notas/`.

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

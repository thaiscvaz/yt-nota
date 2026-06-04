# yt-nota

CLI que extrai transcripts do YouTube e prepara drafts pra síntese no Claude Code, gerando notas profundas no vault Obsidian. **Sem custo de API.**

## Por que existe

Consumir YouTube como fonte de aprendizado vira fricção em batch. Manual: abrir vídeo, "mais", "Mostrar transcrição", copiar tudo, colar no Claude, pedir nota. Tudo bem pra 1 vídeo, irrita em 10.

`yt-nota` automatiza a parte chata (extração + estruturação) e deixa a síntese acontecer na sua sessão Claude Code via skill `/yt-sintese` — usa a assinatura que você já paga, zero custo extra.

## Como funciona (fluxo em 2 passos)

```
┌─────────────────────┐         ┌────────────────────────┐
│  Terminal           │         │  Claude Code           │
│  yt-nota <url>      │ ──────▶ │  /yt-sintese           │
│  (extrai, escreve   │         │  (lê drafts, gera 7    │
│   draft no vault)   │         │   seções, finaliza)    │
└─────────────────────┘         └────────────────────────┘
```

**Passo 1 (terminal):** `yt-nota <url>` extrai metadata + transcript via yt-dlp e escreve um **draft** em `<vault>/30-Recursos/Literatura/Pipeline/_processar/`.

**Passo 2 (Claude Code):** invoca `/yt-sintese`. A skill lê todos os drafts pendentes, gera o body da nota (7 seções), chama `yt-nota --finalize` que monta a nota final + transcript bruto + atualiza channel card, e deleta o draft.

## Instalação

```bash
cd <user-home>\00_projetos\yt-nota
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Não precisa de `.env` por padrão (vault path tem default). Se o seu vault está em outro lugar, copie `.env.example` pra `.env` e ajuste.

### Setup em outra máquina

A skill `/yt-sintese` vive em `~/.claude/skills/yt-sintese/` (fora do repo, no global do Claude Code). O repo guarda uma cópia canônica em `skills/yt-sintese/SKILL.md` e um instalador:

**Windows (PowerShell):**
```powershell
pwsh scripts/install-skill.ps1
```

**Mac / Linux:**
```bash
bash scripts/install-skill.sh
```

Roda uma vez por máquina depois do clone. Quando atualizar a skill (edita `skills/yt-sintese/SKILL.md`), roda o instalador de novo pra propagar.

## Uso

```bash
# Um vídeo
yt-nota https://www.youtube.com/watch?v=jYZ6RQay4QY

# Vários
yt-nota url1 url2 url3

# Playlist (expande automaticamente)
yt-nota --playlist https://www.youtube.com/playlist?list=PLxxx

# Arquivo com URLs (uma por linha)
yt-nota --file queue.txt

# Stdin (cole, Ctrl+Z + Enter no Windows)
yt-nota --stdin

# Listar drafts pendentes
yt-nota --list

# Preview sem escrever
yt-nota --dry-run <url>
```

Flags úteis:
- `--tema "IA-e-Programacao"` salva o tema no draft; o finalize atualiza o MOC correspondente
- `--with-cookies` usa cookies do Chrome (Chrome precisa estar FECHADO no Windows). Pra vídeos restritos por idade/região.
- `-v` verbose
- `--no-whisper-fallback` desliga o fallback Whisper se quiser o comportamento antigo (parar no 429 e salvar `.pending.txt`)
- `--whisper-model base` escolhe um modelo diferente (default `small`)

Depois de criar um ou mais drafts, abre o Claude Code e digita:
```
/yt-sintese
```

A skill processa tudo. Pra um draft específico:
```
/yt-sintese <user-home>\<cloud-storage>\...\Pipeline\_processar\<arquivo>.draft.md
```

## O que sai

Por vídeo processado:

```
<vault>/30-Recursos/Literatura/<dominio>/<Canal>/
├── 3-<timestamp>-<slug>.md                       ← síntese (frontmatter + 7 seções)
└── transcripts/3-<timestamp>-<slug>.transcript.md ← transcript com timestamps

<vault>/30-Recursos/Notas/Cards-de-Pessoa/<Canal>.md  ← channel card (criado/atualizado)
```

`<dominio>` é um dos 7 da REGRAS-VAULT (IA-Engenharia, Financas, Saude, Carreira, Impressao-3D, Metodo, Mestrado). Resolvido em cascata: flag `--dominio` → frontmatter do draft → lookup em `config/channel_domains.yaml`. Canal novo precisa entrar no YAML antes do finalize, ou o CLI aborta com instrução clara.

A nota síntese tem: `em uma frase`, `o que defende`, `o que mais me marcou` (com timestamp), `o que isso muda pra mim`, `dicionário` (4-7 termos), `notas permanentes a criar`, `referência`.

## Vídeos sem transcript

Música, alguns shorts e lives sem captions cobrem essa categoria. O draft é criado mesmo assim, com `transcript: indisponivel`. A síntese usa só metadata + descrição.

## Fallback Whisper local (v0.3.0+)

Desde 2025 o endpoint de legendas do YouTube (`timedtext`) ficou muito mais agressivo no rate limit (HTTP 429). Backoff exponencial não resolve mais. O endpoint de **áudio** continua livre, então `yt-nota` cai automaticamente em Whisper local quando legendas falham.

### Instalação

```bash
pip install yt-nota[whisper]
```

Isso adiciona `faster-whisper>=1.0.0` (250 MB de deps + 244 MB do modelo `small` baixado no primeiro uso pro cache do Hugging Face). Sem essa instalação extra, o fallback é silenciosamente skip e o comportamento da v0.2.4 (parada precoce + `.pending.txt`) é preservado.

### Como funciona

Quando o YouTube retorna 429 nas legendas:

1. yt-dlp baixa o **áudio** do vídeo (formato 139, m4a 49 kbps, ~5 MB por 13 min) — endpoint diferente, sem rate limit
2. faster-whisper transcreve em CPU int8 (~0.3x realtime: 13 min de vídeo = 4 min de processo)
3. Os segments produzidos seguem o mesmo formato `Segment(t, text)` dos VTTs, então o draft é idêntico
4. O frontmatter ganha `transcript_origem: whisper-local` (em vez de `auto`/`manual`)

### Configuração

```bash
# Liga/desliga (default ligado)
yt-nota <url> --whisper-fallback         # explícito
yt-nota <url> --no-whisper-fallback      # mantém comportamento v0.2.4

# Escolha do modelo (default small)
yt-nota <url> --whisper-model base       # 74 MB, ~2x mais rápido, qualidade aceitável
yt-nota <url> --whisper-model medium     # 769 MB, ~1x realtime, qualidade máxima sem GPU

# Via env (para automação)
YT_NOTA_WHISPER_FALLBACK=0 yt-nota <url>
YT_NOTA_WHISPER_MODEL=base yt-nota <url>
```

| Modelo | Tamanho | Velocidade | Qualidade PT-BR técnico |
|---|---|---|---|
| `tiny` | 39 MB | ~0.08x realtime | Ruim (só preview) |
| `base` | 74 MB | ~0.15x realtime | Aceitável |
| `small` (default) | 244 MB | ~0.3x realtime | **Excelente** |
| `medium` | 769 MB | ~1x realtime | Máxima sem GPU |
| `large-v3` | 1.5 GB | ~2x realtime | Máxima absoluta (GPU recomendado) |

## Testes

```bash
pip install -e ".[dev]"
pytest
```

## Roadmap (não no escopo atual)

- Watch folder: processa URLs adicionadas a um `.txt` automaticamente
- ~~Whisper local pra vídeos sem captions~~ ✅ adicionado em v0.3.0
- Modo `--api` opcional (back to Anthropic SDK) se quiser automação total um dia
- Suporte GPU pra Whisper (CUDA/Metal) — atualmente roda CPU int8 que é suficiente pra batches normais

## Licença

MIT.

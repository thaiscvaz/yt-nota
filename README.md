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

**Passo 1 (terminal):** `yt-nota <url>` extrai metadata + transcript via yt-dlp e escreve um **draft** em `<vault>/30-Recursos/Literatura/_drafts/`.

**Passo 2 (Claude Code):** invoca `/yt-sintese`. A skill lê todos os drafts pendentes, gera o body da nota (7 seções), chama `yt-nota --finalize` que monta a nota final + transcript bruto + atualiza channel card, e deleta o draft.

## Instalação

```bash
cd C:\Users\thais\00_projetos\yt-nota
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Não precisa de `.env` por padrão (vault path tem default). Se o seu vault está em outro lugar, copie `.env.example` pra `.env` e ajuste.

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

Depois de criar um ou mais drafts, abre o Claude Code e digita:
```
/yt-sintese
```

A skill processa tudo. Pra um draft específico:
```
/yt-sintese C:\Users\thais\OneDrive\...\_drafts\<arquivo>.draft.md
```

## O que sai

Por vídeo processado:

```
<vault>/30-Recursos/Literatura/<Canal>/
├── 3-<timestamp>-<slug>.md              ← síntese (frontmatter + 7 seções)
└── 3-<timestamp>-<slug>.transcript.md   ← transcript com timestamps

<vault>/30-Recursos/Notas/<Canal>.md     ← channel card (criado/atualizado)
```

A nota síntese tem: `em uma frase`, `o que defende`, `o que mais me marcou` (com timestamp), `o que isso muda pra mim`, `dicionário` (4-7 termos), `notas permanentes a criar`, `referência`.

## Vídeos sem transcript

Música, alguns shorts e lives sem captions cobrem essa categoria. O draft é criado mesmo assim, com `transcript: indisponivel`. A síntese usa só metadata + descrição.

## Testes

```bash
pip install -e ".[dev]"
pytest
```

## Roadmap (não no escopo atual)

- Watch folder: processa URLs adicionadas a um `.txt` automaticamente
- Whisper local pra vídeos sem captions
- Modo `--api` opcional (back to Anthropic SDK) se quiser automação total um dia

## Licença

MIT.

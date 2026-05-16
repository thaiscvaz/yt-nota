# Plano: `yt-nota` — CLI Python para virar transcripts do YouTube em notas Obsidian

## Contexto

Thais consome YouTube como fonte de aprendizado e hoje faz manualmente: abre vídeo → "mais" → "Mostrar transcrição" → seleciona tudo → cola no Claude → pede nota. Funciona pra 1 vídeo, vira fricção pra 10. Já confia na qualidade da síntese (faz há tempo) e usa o vault Obsidian com estrutura PARA (`30-Recursos/Literatura/<Canal>/`) e channel cards em `30-Recursos/Notas/`.

A dor real é **a extração manual + falta de batch**. Não é qualidade de síntese, não é onde a nota vai, é o tempo de copy-paste vezes N.

Solução: **CLI Python standalone** que automatiza extração (yt-dlp), chama a API Anthropic pra síntese, e escreve as notas no vault no mesmo padrão que ela já usa. Suporta single, lista, playlist, arquivo.

## Decisões fechadas

| Decisão | Escolha |
|---|---|
| Interface | CLI Python standalone (`yt-nota` comando) |
| Extração | `yt-dlp` (sem cookies por default; flag `--with-cookies` ativa `--cookies-from-browser chrome` quando precisar — vídeos restritos por idade/região) |
| Síntese | Anthropic SDK Python, modelo `claude-opus-4-7` default, `--fast` pra sonnet-4-6 |
| Input | URL única, múltiplas URLs, playlist, arquivo `.txt`, stdin |
| Transcript bruto | Arquivo irmão `3-<id>-<slug>.transcript.md` |
| Síntese | Frontmatter + 7 seções (em uma frase / o que defende / o que marcou / o que muda / dicionário / conexões / notas permanentes / referência) |
| Channel card | Auto-criado/atualizado em `30-Recursos/Notas/<Canal>.md` |
| MOC temático | Só com `--tema X` (não auto-detecta) |
| Vídeo sem transcript | Nota só com metadata + descrição |
| Idioma | Transcript em idioma original, síntese sempre PT-BR. `--translate` traduz transcript |
| Volume alvo | 1-3 esporádico (default), mas batch funciona até playlist inteira |

## Repositório

- **Remote:** `https://github.com/thaiscvaz/yt-nota.git` (público, recém-criado, vazio)
- **Local:** `C:\Users\thais\00_projetos\yt-nota\`
- **Tudo versionado:** código, testes, prompts, plano (`docs/plan.md`), changelog, README. Nada fica solto fora do repo.

## Arquitetura

```
C:\Users\thais\00_projetos\yt-nota\
├── .git/                     # remote: github.com/thaiscvaz/yt-nota
├── docs/
│   └── plan.md               # cópia viva deste plano, evolui com o projeto
├── pyproject.toml            # package metadata, dependencies, entry point
├── README.md                 # uso, exemplos, troubleshooting
├── .env.example              # ANTHROPIC_API_KEY
├── .gitignore                # .venv, .env, __pycache__
├── src/yt_nota/
│   ├── __init__.py
│   ├── __main__.py           # python -m yt_nota
│   ├── cli.py                # argparse: single/multi/playlist/file/stdin
│   ├── extractor.py          # yt-dlp wrapper (metadata + subs)
│   ├── transcript.py         # VTT parse, dedup auto-captions, timestamps mm:ss
│   ├── synthesizer.py        # Anthropic API call + prompt template
│   ├── vault.py              # write 2 files (note + transcript), update channel card
│   ├── slug.py               # title → slug ASCII
│   ├── config.py             # vault path, model defaults (lê de env/config)
│   └── prompts/
│       └── synthesis.md      # template Markdown editável (Jinja2 vars)
└── tests/
    ├── test_extractor.py     # mock yt-dlp response
    ├── test_transcript.py    # VTT parsing edge cases
    ├── test_vault.py         # frontmatter, file writes
    └── fixtures/
        └── sample_vtt.vtt
```

## Dependências

| Pacote | Por quê |
|---|---|
| `yt-dlp` | extração transcript+metadata (já instalado) |
| `anthropic` | SDK pra síntese via API |
| `python-frontmatter` | manipular frontmatter YAML em md |
| `python-dotenv` | carregar `.env` |
| `httpx` | retry/timeout robusto (já vem com anthropic SDK) |
| dev: `pytest`, `pytest-mock` | testes |

Sem requests (httpx é melhor), sem click (argparse é suficiente pra esse escopo), sem rich (output simples é OK pra CLI).

## Comandos

```bash
# Single
yt-nota https://youtube.com/watch?v=xxx

# Multi (args)
yt-nota url1 url2 url3

# Playlist (yt-dlp expande)
yt-nota --playlist https://youtube.com/playlist?list=PLxxx

# Arquivo de fila
yt-nota --file C:/Users/thais/Desktop/queue.txt

# Stdin (cola URLs, Ctrl+Z+Enter no Windows)
yt-nota --stdin

# Opções globais
--tema "IA-e-Programacao"    # atualiza MOC específico
--translate                  # traduz transcript pra PT-BR (default: mantém)
--with-cookies               # usa cookies do Chrome (Chrome precisa estar fechado)
--model opus|sonnet          # default opus
--dry-run                    # extrai e mostra preview, não escreve
--no-channel-card            # pula update do channel card
--vault PATH                 # override path do vault
-v / --verbose               # log detalhado
```

## Fluxo por vídeo

1. **Extrair metadata** via `yt-dlp -j --skip-download <url>` (+ `--cookies-from-browser chrome` se `--with-cookies`)
   - Captura: id, title, channel, channel_url, upload_date, duration, description, tags
2. **Extrair transcript** via `yt-dlp --write-subs --write-auto-subs --sub-langs pt,pt-BR,en,en-US --sub-format vtt --skip-download <url>` (+ cookies se flag)
   - Prefere manual subs > auto. Prefere pt > en > any.
3. **Parsear VTT** em `transcript.py`:
   - Strip header, tags inline (`<00:00:01.000>`, `<c>`)
   - Dedup overlapping cues (problema clássico de auto-captions)
   - Output: `[{t: "mm:ss", text: "..."}]`
4. **Gerar slug**: `<title>` → ASCII lowercase, hífens, max 6 palavras
5. **Sintetizar nota** em `synthesizer.py`:
   - Carrega template `prompts/synthesis.md`
   - Injeta: título, canal, data, duração, descrição, transcript completo
   - Chama Anthropic API com modelo escolhido
   - Recebe Markdown com frontmatter + 7 seções (formato `/ps-nota`)
6. **Escrever arquivos** em `vault.py`:
   - `<vault>/30-Recursos/Literatura/<Canal>/3-<id_temporal>-<slug>.md` (síntese)
   - `<vault>/30-Recursos/Literatura/<Canal>/3-<id_temporal>-<slug>.transcript.md` (transcript)
   - Frontmatter da síntese: `transcript_file: "[[3-<id>-<slug>.transcript]]"`
7. **Atualizar channel card** `<vault>/30-Recursos/Notas/<Canal>.md`:
   - Se não existe: cria com template (`tipo: card-vivo`, seção "Vídeos processados")
   - Se existe: append linha em "Vídeos processados", atualiza `updated:` no frontmatter

## Modelo de síntese (essência)

Prompt instrui Claude a gerar nota com:

- **Frontmatter** alinhado com `/ps-nota`: ID, tipo: literatura, subtipo: vídeo, título, autores (canal), ano, fonte: YouTube, url, canal, canal_url, duração, data-publicação, data-leitura, idioma_original, status: lido, tags, up: `[[<Canal>]]`, transcript_file: `[[<transcript>]]`
- **Em uma frase** — tese central
- **O que defende** — 3-5 pontos com nuance, citações pontuais
- **O que mais me marcou** — blockquote com timestamp `[mm:ss]`
- **O que isso muda pra mim** — aplicação à carreira/projetos da Thais
- **Dicionário** — 4-7 termos técnicos explicados com analogia, ordem de aparição
- **Conexões** — links a outras notas do vault (pode ficar vazio se não houver pista)
- **Notas permanentes a criar** — 1-3 títulos atômicos pra extrair depois
- **Referência** — formato CANAL. Título. YouTube, DD/MM/YYYY. URL.

**Estilo (inegociável, vem do CLAUDE.md e feedback memories):**
- Sem travessão "—"
- Tom pessoal, não formal
- Sem palavrão
- Sem markers de IA ("vamos explorar", "esse vídeo traz")
- PT-BR

## Output do CLI

Por vídeo, modo verbose:
```
[1/3] https://youtube.com/watch?v=xxx
  ✓ Metadata extraído (Fabio Akita · 1h 23m · 2026-04-12)
  ✓ Transcript pt-BR auto (1842 segmentos)
  → Sintetizando com claude-opus-4-7...
  ✓ Nota: 30-Recursos/Literatura/Fabio-Akita/3-20260516-akita-ia-projetos.md
  ✓ Transcript: 3-20260516-akita-ia-projetos.transcript.md
  ✓ Channel card atualizado
```

Modo silent (default): só os caminhos de arquivos criados, ou erros.

## Tratamento de falhas

| Falha | Ação |
|---|---|
| URL inválida | Skip, log warning, continua batch |
| Vídeo privado/removido | Skip, log warning, continua batch |
| Transcript indisponível | Cria nota só com metadata + descrição, marca `transcript: indisponivel` |
| API Anthropic timeout/rate limit | Retry com backoff exponencial 3x (httpx) |
| Slug colide com nota existente | Append sufixo timestamp |
| Vault path não existe | Aborta com erro claro |
| Vídeo restrito (idade/região) sem flag --with-cookies | Mensagem clara sugerindo rodar com --with-cookies (e fechar Chrome antes) |
| Chrome aberto com --with-cookies (file lock) | Erro claro: "Feche o Chrome e rode de novo, ou rode sem --with-cookies" |

## Setup pra Thais (uma vez)

```bash
cd C:/Users/thais/00_projetos
git init yt-nota && cd yt-nota
# (Eu crio os arquivos)
python -m venv .venv
.venv\Scripts\activate
pip install -e .
cp .env.example .env
# Editar .env: ANTHROPIC_API_KEY=sk-ant-...
```

Depois: `yt-nota <url>` funciona globalmente (dentro do venv). Opcional: alias no PowerShell profile.

## Verificação (smoke test)

1. `yt-nota https://www.youtube.com/watch?v=jYZ6RQay4QY` (URL teste que você passou)
2. Verificar:
   - Arquivo `30-Recursos/Literatura/<Canal>/3-<ts>-<slug>.md` criado com frontmatter completo + 7 seções
   - Arquivo `.transcript.md` separado com timestamps `[mm:ss]`
   - Channel card criado/atualizado em `30-Recursos/Notas/`
   - Síntese em PT-BR, sem travessão, sem markers de IA
3. `yt-nota url1 url2 url3` — verificar 3 notas geradas em paralelo
4. `yt-nota --playlist <link>` — verificar expansão e processamento
5. `yt-nota --dry-run <url>` — verificar preview sem escrita
6. Edge case: vídeo sem transcript → verificar nota fallback com metadata

## Fase 2 (não vai entrar agora)

- Browser MCP integration pra puxar "Curtidos" do YouTube logado
- Watch folder (processa URLs adicionadas a um .txt automaticamente)
- Skill `/yt-nota` no Claude Code que envolve o CLI (combo CLI + skill)
- Whisper local pra vídeos sem captions (música, alguns shorts)

## Arquivos críticos a criar

| Caminho | Função |
|---|---|
| `C:\Users\thais\00_projetos\yt-nota\pyproject.toml` | package + entry point `yt-nota` |
| `C:\Users\thais\00_projetos\yt-nota\src\yt_nota\cli.py` | argparse, orquestração |
| `C:\Users\thais\00_projetos\yt-nota\src\yt_nota\extractor.py` | yt-dlp wrapper |
| `C:\Users\thais\00_projetos\yt-nota\src\yt_nota\transcript.py` | VTT parser robusto |
| `C:\Users\thais\00_projetos\yt-nota\src\yt_nota\synthesizer.py` | Anthropic SDK call |
| `C:\Users\thais\00_projetos\yt-nota\src\yt_nota\vault.py` | escrita no Obsidian |
| `C:\Users\thais\00_projetos\yt-nota\src\yt_nota\prompts\synthesis.md` | template editável |
| `C:\Users\thais\00_projetos\yt-nota\tests\test_transcript.py` | mínimo de regressão pra VTT |

Sem documentação intermediária. README minimalista. Sem comentários supérfluos no código.

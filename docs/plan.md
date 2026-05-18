# yt-nota — arquitetura e estado

> **Status:** v0.2.0 funcionando fim a fim. Sem dependência de API paga.

## Contexto

Thais consome YouTube como fonte de aprendizado. O fluxo manual hoje (abrir vídeo → "Mostrar transcrição" → copiar → colar no Claude → pedir nota) funciona pra um vídeo e vira fricção em batch.

A dor é **a extração manual + falta de batch**, não a qualidade da síntese (que ela já confia, fazendo via Claude). Objetivo: automatizar a extração e estruturar a síntese, **sem custo de API**.

## Solução em 2 passos

1. **Terminal:** `yt-nota <url>` extrai metadata + transcript via yt-dlp e grava um draft em `<vault>/30-Recursos/Literatura/_drafts/`.
2. **Claude Code:** `/yt-sintese` lê drafts pendentes, gera o body (7 seções), chama `yt-nota --finalize` que monta a nota final + transcript + channel card e deleta o draft.

A síntese acontece dentro da sua sessão Claude Code (sem chamar API Anthropic externa). Custo extra: zero.

## Decisões fechadas

| Decisão | Escolha |
|---|---|
| Interface | CLI Python `yt-nota` + Claude Code skill `/yt-sintese` |
| Extração | `yt-dlp` (Python API). Sem cookies por default. `--with-cookies` ativa `--cookies-from-browser chrome` para vídeos restritos. |
| Síntese | Claude Code skill (zero custo); processa todos os drafts pendentes |
| Input | URL única, múltiplas, `--playlist`, `--file`, `--stdin` |
| Transcript bruto | Arquivo irmão `3-<id>-<slug>.transcript.md` |
| Nota síntese | 7 seções: em uma frase, o que defende, o que mais me marcou (citação + `[mm:ss]`), o que isso muda pra mim, dicionário (4-7 termos), notas permanentes a criar, referência |
| Channel card | Auto-criado/atualizado em `30-Recursos/Notas/<Canal>.md` |
| MOC temático | Só com `--tema X` (não auto-detecta) |
| Vídeo sem transcript | Draft com metadata + descrição; síntese fica mais curta |
| Idioma | Transcript fica no original. Síntese sempre PT-BR. |
| Volume alvo | 1-3 esporádico (default); batch funciona até playlist inteira |

## Repositório

- **Remote:** `https://github.com/thaiscvaz/yt-nota.git`
- **Local:** `C:\Users\thais\00_projetos\yt-nota\`

## Arquitetura

```
yt-nota/
├── .git/
├── docs/plan.md             # este arquivo
├── pyproject.toml           # deps: yt-dlp, python-dotenv, PyYAML
├── README.md
├── .env.example             # YT_NOTA_VAULT opcional
├── .gitignore
├── src/yt_nota/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py               # argparse com 3 modos: extract (default), --finalize, --list
│   ├── extractor.py         # yt-dlp wrapper (metadata + subs via httpx)
│   ├── transcript.py        # VTT parser + dedup de auto-captions
│   ├── vault.py             # write_draft, finalize_draft, channel card, MOC
│   ├── slug.py              # title/canal → slug ASCII
│   └── config.py            # vault path, dirs derivados
└── tests/
    ├── test_transcript.py   # regressão do parser
    ├── test_slug.py         # regressão dos slugs
    └── fixtures/*.vtt
```

Skill global: `C:\Users\thais\.claude\skills\yt-sintese\SKILL.md` (não vive no repo do projeto, vive no `~/.claude` global).

## Dependências (runtime)

| Pacote | Por quê |
|---|---|
| `yt-dlp` | extração metadata + sub URLs |
| `httpx` | baixa o VTT direto do CDN (vem com SDK Anthropic; aqui é usado direto) |
| `python-dotenv` | carrega `.env` opcional pra `YT_NOTA_VAULT` |
| `PyYAML` | parsing do frontmatter do draft no finalize |

Dev: `pytest`, `pytest-mock`.

**Removido:** `anthropic` SDK (não chama API mais), `python-frontmatter` (PyYAML é suficiente).

## Comandos

```bash
# Extração (modo default)
yt-nota <url>
yt-nota url1 url2 url3
yt-nota --playlist <playlist_url>
yt-nota --file queue.txt
yt-nota --stdin

# Flags
--tema X            # salva tema no draft, finalize atualiza MOC
--with-cookies      # cookies do Chrome (Chrome precisa estar FECHADO)
--dry-run           # preview sem escrever draft
-v / --verbose

# Operações
yt-nota --list                                  # drafts pendentes
yt-nota --finalize <draft> --body-file <body>   # chamado pela skill
```

## Fluxo end-to-end por vídeo

1. `yt-nota <url>` chama `extract_info()` (yt-dlp Python API). Captura: id, title, channel, channel_url, upload_date, duration, description, tags, lista de subtitle URLs.
2. `extract_transcript()` escolhe melhor sub (manual > auto, pt > en > qualquer), baixa VTT via httpx, parseia com `parse_vtt()`.
3. `write_draft()` escreve em `_drafts/<id>-<slug>.draft.md` com frontmatter completo + descrição + transcript com `[mm:ss]`.
4. Thais invoca `/yt-sintese` no Claude Code.
5. Skill lista drafts (`yt-nota --list`), lê cada um, gera body (7 seções).
6. Skill escreve body em arquivo temporário (`.venv/tmp_body_<id>.md`).
7. Skill chama `yt-nota --finalize <draft> --body-file <tmp>`.
8. `finalize_draft()` parseia frontmatter do draft, monta frontmatter final + header + body, escreve `3-<id>-<slug>.md`. Escreve transcript file. Cria/atualiza channel card. Deleta draft.
9. Skill apaga o tmp body.

## Tratamento de falhas

| Falha | Ação |
|---|---|
| URL inválida | Skip, log warning, continua batch |
| Vídeo privado/removido | Skip, log warning, continua batch |
| Transcript indisponível | Draft com `transcript: indisponivel`. Síntese usa descrição. |
| Slug colide com nota existente | Sufixo numérico (`-2`, `-3`, ...) |
| Vault path não existe | Aborta com erro claro |
| Vídeo restrito sem `--with-cookies` | Mensagem clara sugerindo rodar com `--with-cookies` |
| Chrome aberto com `--with-cookies` (file lock) | Erro claro: feche Chrome ou rode sem |

## Setup (uma vez)

```bash
git clone https://github.com/thaiscvaz/yt-nota.git C:/Users/thais/00_projetos/yt-nota
cd C:/Users/thais/00_projetos/yt-nota
python -m venv .venv
.venv/Scripts/activate
pip install -e .
```

Skill já está em `C:\Users\thais\.claude\skills\yt-sintese\`. Disponível em qualquer sessão Claude Code.

## Verificação (smoke test)

Executado em 2026-05-18 com URL `https://www.youtube.com/watch?v=jYZ6RQay4QY`. Resultado:

- Draft criado: `_drafts/20260518164518-o-nicho-que-fez-esse-casal.draft.md`
- Skill `/yt-sintese` processou em ~30s
- Saída no vault:
  - `30-Recursos/Literatura/STLFLIX-BR-Impressao-3D/3-20260518164518-o-nicho-que-fez-esse-casal.md`
  - `3-20260518164518-o-nicho-que-fez-esse-casal.transcript.md`
  - `30-Recursos/Notas/STLFLIX-BR-Impressao-3D.md` (novo card)
- Draft auto-deletado

16/16 testes passando.

## Fase 2 (não no escopo atual)

- Watch folder (processa URLs adicionadas a um `.txt` automaticamente)
- Whisper local para vídeos sem captions (música, alguns shorts)
- Flag `--api` opcional pra reativar Anthropic SDK se quiser automação total
- Browser MCP pra puxar "Curtidos" / "Assistir mais tarde" do YouTube logado

## Histórico

- v0.1.0 (2026-05-16): scaffold inicial com integração Anthropic API direta. Pipeline rodou até a chamada da API mas barrou em billing da conta da Thais.
- v0.2.0 (2026-05-18): refatorado pra modo draft + skill `/yt-sintese`. Sem dependência de API paga. Smoke test passou fim a fim.

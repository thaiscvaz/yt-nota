# yt-nota

> Contexto pra IAs (Claude Code, Cursor, etc.) editando este repositório.

## Escopo

CLI Python + skill Claude Code `/yt-sintese` pra transformar vídeos do YouTube em notas Obsidian estruturadas. Whisper local fallback quando o YouTube limita as legendas (429). Zero custo de API (síntese acontece dentro da sessão Claude Code).

## Onde o yt-nota escreve

O CLI **escreve no vault Obsidian apontado por `YT_NOTA_VAULT`** (variável de ambiente, obrigatória para extract/finalize/list; sem ela o CLI aborta com instrução). A estrutura esperada:

```
<vault>/
  30-Recursos/
    Pipeline/_processar/                      ← drafts pendentes de síntese
    <dominio>/<canal>/                        ← notas finais após /yt-sintese
      MOC-<canal>.md                          ← hub da fonte (criado/atualizado pelo finalize)
      3-<id>-<slug>.md
      transcripts/3-<id>-<slug>.transcript.md
    Pessoas/<canal>.md                        ← card vivo do canal
```

Onde `<dominio>` é um valor da taxonomia configurável do vault (definida em `config/domains.yaml`, copiada de `config/domains.example.yaml`), resolvido via:
1. Flag `--dominio` (override explícito)
2. Frontmatter `dominio:` do draft
3. Lookup em `config/channel_domains.yaml` (configuração pessoal, não versionada — copie de `config/channel_domains.example.yaml`)

Sem mapping nem override, o CLI aborta com `DomainResolutionError`.

## Convenções importantes

- **Sem dependências do Anthropic SDK em runtime.** A síntese roda dentro da skill `/yt-sintese`, não na CLI. CLI extrai e prepara; Claude Code sintetiza.
- **Drafts são sempre intermediários.** A skill consome e deleta o draft após produzir a nota final + transcript + atualizar o channel card.
- **Domain mapping é opcional mas recomendado.** Sem `config/channel_domains.yaml` o usuário precisa passar `--dominio` em cada execução.

## Stack

Python ≥ 3.10, `yt-dlp`, `httpx`, `faster-whisper` (opcional, pra fallback), `pyyaml`, `python-dotenv`. Testes: `pytest`. Sem CI integrado por enquanto.

## Mais

Veja `README.md` pra setup, `docs/plan.md` pra arquitetura, `CHANGELOG.md` pra histórico de versões.

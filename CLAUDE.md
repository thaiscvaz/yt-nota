# yt-nota

> Este arquivo foi criado em 2026-06-04 pra registrar a regra de integração com o vault Obsidian. Adicione contexto específico do projeto conforme necessário (memória em `~/.claude/memory/project_yt_nota.md`).

## Escopo do projeto

CLI + skill `/yt-sintese`, sem custo de API. v0.3.0 (31/05/2026) com fallback Whisper local quando YouTube retorna 429. 89 testes passando. Produto open source.

## Integração com vault Obsidian

Este projeto **escreve no vault Obsidian** quando você roda `yt-nota <url>`:
- Drafts em `C:\Users\thais\OneDrive\Documentos\Obsidian\30-Recursos\Literatura\Pipeline\_processar\` (a partir de 2026-06-04, era `_drafts/` antes)

Skill `/yt-sintese` processa os drafts e produz notas finais em:
- `C:\Users\thais\OneDrive\Documentos\Obsidian\30-Recursos\Literatura\<dominio>\<canal>\` (segue 7 domínios do ROTEAMENTO)

Quando criar, mover, renomear ou deletar nota no vault, respeitar as regras invariantes:

1. **Ler antes:** `C:\Users\thais\OneDrive\Documentos\Obsidian\30-Recursos\Sistema\REGRAS-VAULT.md` — perfil cognitivo TDAH, princípio Zettelkasten, 7 domínios, regras anti-entropia, regra dos 3 cliques.
2. **Aplicar destino:** `C:\Users\thais\OneDrive\Documentos\Obsidian\30-Recursos\Sistema\ROTEAMENTO.md` — Tabela A (curadoria de fontes) decide o domínio do canal.

Em conflito entre instrução pontual e estas regras, perguntar à Thais antes de quebrar. Para validar drift, rodar `/vault-checkup --validar` sob demanda.

## Histórico de migração

- **2026-06-04 (v0.4.0):** Realinhamento completo com vault Fase A+B. Drafts em `Pipeline/_processar/`, notas em `Literatura/<dominio>/<canal>/`, cards em `Notas/Cards-de-Pessoa/<canal>.md`. Mapping em `config/channel_domains.yaml`. Resolução de domínio em cascata via `src/yt_nota/domain.py`. Ver CHANGELOG `[0.4.0]`.

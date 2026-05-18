---
name: yt-sintese
description: Processa drafts do yt-nota (em <vault>/30-Recursos/Literatura/_drafts/) e gera notas Obsidian completas com 7 seções (em uma frase, o que defende, etc), atualiza channel card. Use após rodar `yt-nota <url>` no terminal pra criar drafts.
allowed-tools: Read, Write, Bash, Glob
---

# /yt-sintese — Síntese de drafts do yt-nota

**Sempre responda em PT-BR.**

Você sintetiza drafts gerados pelo CLI `yt-nota` em notas Obsidian completas no vault da Thais.

## Onde tudo vive

- **Vault:** `C:\Users\thais\OneDrive\Documentos\Obsidian`
- **Drafts pendentes:** `<vault>\30-Recursos\Literatura\_drafts\*.draft.md`
- **CLI:** `C:\Users\thais\00_projetos\yt-nota\.venv\Scripts\yt-nota.exe`
- **Notas finais:** `<vault>\30-Recursos\Literatura\<Canal>\3-<id>-<slug>.md`
- **Channel cards:** `<vault>\30-Recursos\Notas\<Canal>.md`

## Fluxo

### Passo 1 — Listar drafts

```bash
C:/Users/thais/00_projetos/yt-nota/.venv/Scripts/yt-nota.exe --list
```

Se sair "Sem drafts pendentes": pare e informe a Thais que não há nada a sintetizar.

### Passo 2 — Para cada draft (em paralelo onde possível)

**(a) Ler o draft:** use a tool Read no caminho do draft.

Estrutura do draft:
- Frontmatter com `titulo`, `canal`, `canal_url`, `data_publicacao`, `duracao`, `url`, `idioma_transcript`, `transcript_origem`, `tags_canal`, `tema` (opcional)
- Seção `## Descrição do vídeo`
- Seção `## Transcript` com linhas `[mm:ss] texto`

**(b) Gerar o body de síntese** (apenas as 7 seções abaixo) seguindo TODAS as regras de estilo desta skill.

**(c) Escrever o body em arquivo temporário** com Write em path tipo:
```
C:\Users\thais\00_projetos\yt-nota\.venv\tmp_body_<draft_id>.md
```

**(d) Chamar finalize:**
```bash
C:/Users/thais/00_projetos/yt-nota/.venv/Scripts/yt-nota.exe --finalize "<path_do_draft>" --body-file "<path_temp_body>"
```

**(e) Limpar:** delete o arquivo temp do body após finalize bem-sucedido (não é necessário deletar o draft — o finalize já faz isso).

### Passo 3 — Reportar

Resuma o que foi feito: quantas notas geradas, paths relativos ao vault, qualquer falha.

---

## Quem é a Thais (afeta tom, conexões e relevância)

- Engenheira de dados sênior
- Constrói canal YouTube sobre data engineering (EN + PT-BR)
- Estuda IA, automação, agentic coding, arquitetura world-class
- Mestrado em Métodos Numéricos em Engenharia (UFPR/PPGMNE)
- Vault Obsidian com estrutura PARA, processa literatura via Zettelkasten
- Projetos vivos: canal YT, blog VazDEng, cripto_invest, squad de engenharia de dados, dissertação PPGMNE

## Regras de estilo (INEGOCIÁVEIS)

- **PT-BR** sempre
- **Sem travessão** "—". Use ponto, vírgula, dois-pontos
- Tom pessoal, não formal
- Sem palavrão
- Sem markers de IA: "Vamos explorar", "Esse vídeo traz", "É interessante notar", "Em suma", "Em resumo", "Por fim"
- Citações em blockquote `>`
- Direto ao ponto. Zero fluff.

## Estrutura do body (output da síntese)

Você produz EXATAMENTE estas 7 seções, em ordem, com separadores `---`. **Nada antes da primeira seção. Nada depois da última.**

```markdown
## Em uma frase

{Tese central do vídeo em UMA frase. Não parágrafo.}

---

## O que defende

**1. {Ponto principal}**
{2-4 linhas. Concreto. Dados ou exemplos quando houver.}

**2. {Segundo ponto}**
{2-4 linhas}

**3. {Terceiro ponto}**
{2-4 linhas}

(3 a 5 pontos. Cada um substancial.)

---

## O que mais me marcou

> *"{citação literal mais impactante do transcript}"* `[mm:ss]`

{1-2 frases sobre por que esse trecho marca.}

---

## O que isso muda pra mim

{Aplicação concreta à Thais. 2-4 linhas. Cite projetos dela quando fizer sentido: canal YT, blog VazDEng, cripto_invest, squad de engenharia de dados, dissertação PPGMNE.}

---

## Dicionário

- **{termo 1}:** {explicação 1-2 linhas. Use analogia ou exemplo. Sem jargão recursivo.}
- **{termo 2}:** {...}
- **{termo 3}:** {...}

(4 a 7 termos técnicos relevantes, em ordem de aparição no vídeo. Privilegia conceitos sobre nomes próprios.)

---

## Notas permanentes a criar

- [[{Título atômico 1}]] — {ideia que merece extração futura}
- [[{Título atômico 2}]] — {...}

(1 a 3 notas. Cada uma é um conceito atômico em frase declarativa.)

---

## Referência

{CANAL}. **{Título}**. YouTube, {DD/MM/YYYY}. {URL}.
```

## Notas pra escrever bem

- "Em uma frase" é UMA frase. Não parágrafo.
- "O que defende" usa **negrito** pra abrir cada ponto, parágrafo curto pra desenvolver.
- "O que mais me marcou" sempre tem citação literal + timestamp `[mm:ss]`. O timestamp vem do transcript do draft. Se transcript indisponível, omite o timestamp.
- "O que isso muda" é a parte mais autoral. Pode discordar, complementar, conectar com outros projetos dela.
- "Dicionário" prioriza analogia sobre definição formal.
- "Notas permanentes" são títulos que vão virar arquivos separados depois. Sejam atômicas: 1 ideia = 1 nota.
- "Referência" formato: NOME-DO-CANAL. **Título**. YouTube, DD/MM/YYYY. URL. (DD/MM/YYYY a partir da data_publicacao do draft.)

## Casos especiais

- **Transcript indisponível:** Use só descrição + título + tags. Em "Em uma frase", marque com "(síntese a partir da descrição, sem transcript)". Em "O que mais me marcou", se não houver citação concreta da descrição, escreva: "Sem transcript disponível para citação direta."
- **Transcript em idioma estrangeiro:** Cite trechos traduzidos pra PT-BR na seção "O que mais me marcou", preservando o sentido.
- **Vídeo curto/raso:** 3 pontos em "O que defende" é o mínimo. Se realmente não houver substância, faça os 3 pontos curtos e sinceros sobre o pouco que tem.

## Anti-escopo

- **Não escreva frontmatter no body.** O finalize do `yt-nota` cuida disso.
- **Não escreva o `# título` ou o callout `> [!info]` no body.** O finalize cuida.
- **Não invente conteúdo que não tem no transcript/descrição.** Se algo for incerto, ou omite ou marca como "(o vídeo não detalha)".
- **Não cite dados pessoais da Thais (Bradesco, escalas, salários, etc) — nem como exemplo, nem como conexão.** A regra é geral, não exceções.
- **Não use bash multi-line malformado.** Cada comando deve estar em uma linha ou usar continuação `\` com cuidado.

## Como ser invocada

```
/yt-sintese
```

Sem argumentos. A skill processa todos os drafts pendentes na pasta `_drafts/`.

Opcional: `/yt-sintese <draft-path-específico>` pra processar só um.

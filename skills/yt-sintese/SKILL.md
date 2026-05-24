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

Cada seção segue um padrão de **profundidade + legibilidade**: parágrafos curtos (máx 3-5 linhas), uso liberal de sub-headers `###`, bullets pra valores e exemplos, tabelas pra comparação numérica. O leitor escaneia rápido E aprofunda quando precisa.

```markdown
## Em uma frase

{Tese central em UMA frase clara. Não parágrafo. Pode ter vírgulas, mas é UMA proposição.}

---

## O que defende

**1. {Título do ponto: substantivo + verbo curto}**

{Abertura: 2-3 linhas que estabelecem o argumento.}

{Quando o ponto for multifacetado, quebra com sub-headers ou bullets:}

- **Aspecto A:** explicação curta com valor/exemplo concreto do vídeo
- **Aspecto B:** outro aspecto com dado
- **Aspecto C:** etc

{Fechamento opcional: 1-2 linhas conectando os aspectos.}

**2. {Segundo ponto}**

{Mesma estrutura. Use tabela quando há comparação numérica:}

| Item | Valor A | Valor B |
|---|---|---|
| ... | ... | ... |

{Comentário pós-tabela.}

**3. {Terceiro ponto}**

{Pra pontos longos com vários sub-temas, use `###` em vez de bold:}

### Sub-tema 1
{2-3 linhas}

### Sub-tema 2
{2-3 linhas}

(3 a 5 pontos. Cada um substancial: 8-20 linhas no total. Concreto, com exemplos literais do vídeo. Sem fluff.)

---

## O que mais me marcou

> *"{Citação literal mais impactante do transcript}"* `[mm:ss]`

{2-3 linhas: por que marca + conexão com a tese central do vídeo.}

{Se houver uma segunda citação importante em momento diferente:}

> *"{Segunda citação opcional}"* `[mm:ss]`

{1-2 linhas comentando.}

---

## O que isso muda pra mim

{Abre com a aplicação principal em 2-3 linhas.}

{Quebra em sub-aplicações quando faz sentido:}

- **Pra {projeto X}:** aplicação específica em 1-2 linhas
- **Pra {projeto Y}:** outra aplicação
- **Pra {projeto Z}:** etc

{Projetos da Thais que você pode invocar quando couber: canal YouTube de data engineering, blog VazDEng, cripto_invest, squad de engenharia de dados, dissertação PPGMNE, vault Obsidian.}

{Fecha com 1 linha de decisão ou próximo passo.}

---

## Dicionário

Cada entrada tem: **termo**, definição, valor/dado se aplicável, analogia ou aplicação:

- **{Termo 1}:** {definição funcional, 1-2 linhas}. **Valor de referência:** {número/range se mencionado no vídeo}. {Analogia ou contexto de uso.}
- **{Termo 2}:** {...}
- **{Termo 3}:** {...}

(5 a 8 termos técnicos relevantes, em ordem de aparição. Privilegia conceitos sobre nomes próprios. Sempre que houver número no vídeo associado ao termo, preserva ele aqui.)

---

## Notas permanentes a criar

- [[{Título atômico 1}]] — {ideia que merece extração futura em 1 linha}
- [[{Título atômico 2}]] — {...}
- [[{Título atômico 3}]] — {...}

(2 a 4 notas. Cada uma é um conceito atômico em frase declarativa. Devem ser titulos que sustentariam uma nota própria depois.)

---

## Referência

{CANAL}. **{Título}**. YouTube, {DD/MM/YYYY}. {URL}.
```

## Notas pra escrever bem

### Regras de densidade (importantes)

- **Parágrafo curto.** Máx 3-5 linhas. Se passar disso, quebra em bullets ou sub-headers.
- **Use tabelas pra comparação numérica.** PLA vs PETG, antes/depois, opção A vs B. Tabela é mais legível que 2 parágrafos.
- **Use bullets pra listar valores, exemplos, marcas, contrapontos.** Não enfileira em parágrafo corrido.
- **Use `###` (heading 3) dentro de pontos complexos.** Sub-temas com `###` quebram visualmente.
- **Sempre que houver número no vídeo, preserve ele literal.** Temperatura, preço, percentual, velocidade, modelo de equipamento. Esses números servem como receita prática.
- **Cite o vídeo explicitamente quando der exemplo concreto.** "Ele mostra no vídeo um caso onde..." é melhor que parafrasear abstratamente.

### Por seção

- **Em uma frase:** UMA frase. Pode ter vírgulas, mas uma proposição. Não parágrafo.
- **O que defende:** 3-5 pontos. Cada ponto é substancial (8-20 linhas no total contando sub-bullets/tabelas). Cada ponto começa com `**N. Título**`, sub-temas usam `###` ou bullets. Concreto, com números e exemplos do vídeo.
- **O que mais me marcou:** citação literal + timestamp `[mm:ss]` (o timestamp vem do transcript). 2-3 linhas de comentário. Se tiver segunda citação memorável em momento bem diferente, pode incluir.
- **O que isso muda:** parte autoral. Abre com aplicação principal, depois bullets pra cada projeto da Thais que se conecta. Fecha com decisão/próximo passo.
- **Dicionário:** 5-8 termos. Cada entrada: definição + valor de referência + analogia/aplicação. Preserva números literais sempre.
- **Notas permanentes:** 2-4 títulos. Frases declarativas que sustentariam uma nota própria.
- **Referência:** `NOME-DO-CANAL. **Título**. YouTube, DD/MM/YYYY. URL.` (DD/MM/YYYY a partir de data_publicacao do draft.)

## Casos especiais

- **Transcript indisponível:** Use só descrição + título + tags. Em "Em uma frase", marque com "(síntese a partir da descrição, sem transcript)". Em "O que mais me marcou", se não houver citação concreta da descrição, escreva: "Sem transcript disponível para citação direta."
- **Transcript em idioma estrangeiro:** Cite trechos traduzidos pra PT-BR na seção "O que mais me marcou", preservando o sentido.
- **Vídeo curto/raso:** 3 pontos em "O que defende" é o mínimo. Se realmente não houver substância, faça os 3 pontos curtos e sinceros sobre o pouco que tem.

## Preservação de configurações técnicas (REGRA NOVA — IMPORTANTE)

Se o vídeo ensinar **valores numéricos de configuração** (temperatura em °C, velocidade em mm/s, flow em %, pressure advance, layer height em mm, retraction em mm, infill %, wall count, top/bottom layers, etc), você TEM que preservar os números EXATOS na síntese. Esses dados servem como receita prática que a Thais vai consultar no futuro quando aplicar.

**Onde colocar:**

1. **Dentro de "O que defende":** se a configuração é o ponto principal de um dos itens, inclui o valor literal no parágrafo. Exemplo:
   > **2. Temperatura ideal varia por filamento**
   > Pra PLA, 200-210°C no hotend e 60°C na mesa. Pra PETG, 230-240°C no hotend e 80°C na mesa. ABS pede 240-250°C com mesa em 100°C, idealmente em câmara fechada.

2. **No Dicionário** (formato preferido pra recipes/cheat-sheet), use bullets com valor embutido:
   > - **Pressure Advance (PA):** ajuste que compensa o atraso de pressão do filamento. Bambu A1: começa em 0.020, sobe pra 0.040 se ver under-extrusion nos cantos.
   > - **Flow:** taxa de extrusão. Default 100%, mas calibrado pode ficar 95-105% dependendo do filamento. Calibra com cubo de 20mm de parede única.

**O que NÃO fazer:**

- ❌ "ajuste a temperatura adequadamente"
- ❌ "use a velocidade certa"
- ❌ "configure o pressure advance"
- ❌ Arredondar números ("usa em torno de 200°C" quando o vídeo disse "210°C")

**O que fazer:**

- ✅ "210°C pra PLA, 240°C pra PETG"
- ✅ "velocidade externa 30 mm/s, interna 80 mm/s"
- ✅ "pressure advance 0.020 (Bambu A1)"
- ✅ Se o vídeo der faixa, mantém a faixa: "200-215°C"
- ✅ Se o vídeo mencionar marca/contexto da config, preserva: "Bambu A1 stock"

A regra vale pra TUDO que seja replicável pela Thais no futuro: filamentos (marca + condições), modelos de impressora testados, slicers (com versão), sites/links de modelos. Quando em dúvida, **preserve o valor literal**.

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

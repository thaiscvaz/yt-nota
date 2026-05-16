# yt-nota

CLI que transforma transcripts do YouTube em notas profundas no vault Obsidian.

## Por que existe

Consumo de YouTube como fonte de aprendizado vira fricção quando vira batch. Manual: abrir vídeo, clicar "mais", "mostrar transcrição", copiar, colar no Claude, pedir nota. Funciona pra 1 vídeo, irrita em 10.

`yt-nota` automatiza isso. Você passa URL (ou lista, ou playlist), ele extrai transcript, chama o Claude pra sintetizar uma nota com a estrutura que você já usa no vault, e escreve no lugar certo.

## Instalação

```bash
cd C:\Users\thais\00_projetos\yt-nota
python -m venv .venv
.venv\Scripts\activate
pip install -e .

cp .env.example .env
# Editar .env: ANTHROPIC_API_KEY=sk-ant-...
```

## Uso

```bash
# Um vídeo
yt-nota https://www.youtube.com/watch?v=jYZ6RQay4QY

# Vários
yt-nota url1 url2 url3

# Playlist
yt-nota --playlist https://www.youtube.com/playlist?list=PLxxx

# Arquivo com URLs (uma por linha)
yt-nota --file queue.txt

# Stdin (cole, Ctrl+Z + Enter no Windows)
yt-nota --stdin

# Flags úteis
yt-nota --tema "IA-e-Programacao" <url>     # atualiza MOC específico
yt-nota --translate <url>                    # traduz transcript pra PT-BR
yt-nota --with-cookies <url>                 # vídeos restritos (Chrome precisa estar fechado)
yt-nota --model sonnet <url>                 # sonnet em vez de opus
yt-nota --dry-run <url>                      # preview sem escrever
```

## O que sai

Por vídeo, dois arquivos no vault:

```
30-Recursos/Literatura/<Canal>/
├── 3-<timestamp>-<slug>.md              ← síntese profunda, 7 seções
└── 3-<timestamp>-<slug>.transcript.md   ← transcript bruto com timestamps
```

Mais um update no channel card `30-Recursos/Notas/<Canal>.md`.

A nota síntese vem com: frontmatter completo, "em uma frase", "o que defende", "o que mais me marcou" (com timestamp), "o que isso muda pra mim", dicionário (4-7 termos), conexões, notas permanentes a criar, referência.

## Vídeos restritos

Se um vídeo falhar por idade/região, feche o Chrome e rode com `--with-cookies`. yt-dlp pega cookies do seu perfil logado.

## Testes

```bash
pip install -e ".[dev]"
pytest
```

## Licença

MIT.

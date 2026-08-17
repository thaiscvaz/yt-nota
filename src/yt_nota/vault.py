"""Escrita no vault Obsidian (estrutura Fase A+B, 2026-06-04).

Dois modos:
- `write_draft`: chamado pelo CLI principal. Escreve um draft com metadata + transcript
  + `dominio` na pasta `Pipeline/_processar/`. A síntese acontece depois via skill
  `/yt-sintese` no Claude Code.
- `finalize`: chamado pelo subcomando `yt-nota finalize`. Recebe o body sintetizado,
  monta a nota final em `30-Recursos/<dominio>/<canal>/` + transcript + channel card
  em `30-Recursos/Pessoas/<canal>.md` + (opcional) MOC tema.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

from . import config
from .config import (
    CARDS_DE_PESSOA_DIR,
    PROCESSAR_DIR,
    RECURSOS_DIR,
    VAULT_PATH,
)
from .slug import channel_slug, title_slug
from .transcript import Segment, segments_to_markdown, segments_to_plain


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _today_br() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def _as_str(value) -> str:
    """YAML pode entregar date/datetime ou string. Normaliza pra ISO string."""
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _yaml_quote(value: str) -> str:
    if not value:
        return '""'
    if any(c in value for c in ':#"\'\n[]{}|>&%@`'):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return value


# ---------------------------------------------------------------------------
# Draft writer (modo padrão do CLI)
# ---------------------------------------------------------------------------

def write_draft(
    video: dict,
    segments: Optional[list[Segment]],
    transcript_info: Optional[dict],
    *,
    tema: Optional[str] = None,
    dominio: Optional[str] = None,
) -> Path:
    """Escreve draft em Pipeline/_processar/ aguardando síntese.

    `dominio` (opcional): se passado, é gravado no frontmatter pra usar no finalize.
    Se None aqui, o finalize precisa resolver via cli (--dominio) ou lookup YAML.

    Retorna o path do draft criado.
    """
    PROCESSAR_DIR.mkdir(parents=True, exist_ok=True)

    canal = video["channel"] or "Canal-Desconhecido"
    ts_id = _now_id()
    slug = title_slug(video["title"])

    draft_path = _unique_path(PROCESSAR_DIR / f"{ts_id}-{slug}.draft.md")

    fm = [
        "---",
        "tipo: yt-nota-draft",
        "status: pendente-sintese",
        f"draft_id: {ts_id}",
        f"url: {video['url']}",
        f"video_id: {video['video_id']}",
        f"titulo: {_yaml_quote(video['title'])}",
        f"canal: {_yaml_quote(canal)}",
    ]
    if video.get("channel_url"):
        fm.append(f"canal_url: {video['channel_url']}")
    if video.get("upload_date_iso"):
        fm.append(f"data_publicacao: {video['upload_date_iso']}")
    if video.get("duration_human"):
        fm.append(f"duracao: {video['duration_human']}")
    if transcript_info:
        fm.append(f"idioma_transcript: {transcript_info['language']}")
        # `origin` é preferido (suporta 'whisper-local'); fallback no is_auto pra retrocompat
        origem = transcript_info.get("origin") or (
            "auto" if transcript_info.get("is_auto") else "manual"
        )
        fm.append(f"transcript_origem: {origem}")
    else:
        fm.append("transcript: indisponivel")
    if video.get("tags"):
        fm.append("tags_canal:")
        for tag in video["tags"][:12]:
            fm.append(f"  - {_yaml_quote(tag)}")
    if tema:
        fm.append(f"tema: {_yaml_quote(tema)}")
    if dominio:
        fm.append(f"dominio: {dominio}")
    fm.append(f"created: {_today_iso()}")
    fm.append("---")

    body_parts = [
        "\n".join(fm),
        "",
        f"# DRAFT — {video['title']}",
        "",
        "> [!warning] Síntese pendente",
        "> Esse arquivo aguarda processamento via skill `/yt-sintese` no Claude Code.",
        "> Quando processado, vira nota final em `30-Recursos/<dominio>/<canal>/`,",
        "> com transcript em arquivo irmão e atualização do channel card. Este draft é deletado.",
        "",
    ]

    if video.get("description"):
        body_parts.append("## Descrição do vídeo")
        body_parts.append("")
        body_parts.append(video["description"].strip())
        body_parts.append("")

    if segments:
        body_parts.append("## Transcript")
        body_parts.append("")
        body_parts.append(segments_to_plain(segments, with_timestamps=True))
        body_parts.append("")
    else:
        body_parts.append("## Transcript")
        body_parts.append("")
        body_parts.append("_Transcript indisponível para esse vídeo._")
        body_parts.append("")

    draft_path.write_text("\n".join(body_parts), encoding="utf-8")
    _register_draft_in_index(video.get("video_id") or "", draft_path)
    return draft_path


# ---------------------------------------------------------------------------
# Finalize (chamado pela skill após síntese)
# ---------------------------------------------------------------------------

def _parse_draft(draft_path: Path) -> tuple[dict, list[Segment], Optional[str]]:
    """Lê um draft e devolve (metadata_dict, segments, descricao)."""
    text = draft_path.read_text(encoding="utf-8")

    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        raise ValueError(f"Draft sem frontmatter válido: {draft_path}")
    meta = yaml.safe_load(m.group(1)) or {}
    body = text[m.end():]

    descricao = None
    desc_match = re.search(
        r"## Descrição do vídeo\s*\n+(.*?)(?=\n##\s|\Z)", body, re.DOTALL
    )
    if desc_match:
        descricao = desc_match.group(1).strip()

    segments: list[Segment] = []
    transcript_match = re.search(r"## Transcript\s*\n+(.*?)\Z", body, re.DOTALL)
    if transcript_match:
        for line in transcript_match.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("_"):
                continue
            seg_match = re.match(r"\[([\d:]+)\]\s+(.+)$", line)
            if seg_match:
                segments.append(Segment(t=seg_match.group(1), text=seg_match.group(2)))

    return meta, segments, descricao


def finalize_draft(
    draft_path: Path,
    body_markdown: str,
    *,
    no_channel_card: bool = False,
    delete_draft: bool = True,
    dominio_override: Optional[str] = None,
) -> dict:
    """Lê o draft, junta com o body sintetizado, escreve nota final + transcript + card.

    `body_markdown` é o output da síntese (as 7 seções).
    `dominio_override`: força um domínio específico (CLI flag `--dominio`). Se None,
    tenta ler do frontmatter do draft; se nem isso, faz lookup no YAML; se falhar,
    propaga DomainResolutionError.
    Retorna paths dos arquivos criados/atualizados.
    """
    from .domain import resolve as resolve_dominio

    meta, segments, _ = _parse_draft(draft_path)

    canal = meta.get("canal") or "Canal-Desconhecido"
    canal_slug_str = channel_slug(canal)
    titulo = meta.get("titulo") or "Sem título"
    slug = title_slug(titulo)
    ts_id = meta.get("draft_id") or _now_id()
    transcript_lang = meta.get("idioma_transcript")
    transcript_origem = meta.get("transcript_origem")

    dominio = resolve_dominio(canal_slug_str, override=dominio_override or meta.get("dominio"))

    canal_dir = RECURSOS_DIR / dominio / canal_slug_str
    canal_dir.mkdir(parents=True, exist_ok=True)

    note_path = _unique_path(canal_dir / f"3-{ts_id}-{slug}.md")
    transcript_path: Optional[Path] = None
    transcript_link: Optional[str] = None

    if segments and transcript_lang:
        transcripts_dir = canal_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcripts_dir / f"{note_path.stem}.transcript.md"
        transcript_link = transcript_path.stem

    fm = _build_final_frontmatter(meta, canal_slug_str, transcript_link, ts_id)
    header = _build_header(meta, ts_id)
    note_path.write_text(fm + "\n" + header + body_markdown.strip() + "\n", encoding="utf-8")

    if transcript_path is not None and segments:
        transcript_content = _build_transcript_file(
            meta, segments, ts_id, slug, transcript_lang, transcript_origem
        )
        transcript_path.write_text(transcript_content, encoding="utf-8")

    card_path: Optional[Path] = None
    if not no_channel_card:
        card_path = _update_channel_card(canal, canal_slug_str, meta, ts_id, slug, note_path.stem)

    moc_path: Optional[Path] = None
    if meta.get("tema"):
        moc_path = _update_moc(meta["tema"], meta, note_path.stem)

    if delete_draft:
        try:
            draft_path.unlink()
        except OSError:
            pass

    # O finalize move conteúdo (draft → nota), então qualquer índice de dedup
    # construído antes ficou stale. Reset é barato; o próximo check re-escaneia.
    reset_dedup_index()

    return {
        "note_path": note_path,
        "transcript_path": transcript_path,
        "channel_card_path": card_path,
        "moc_path": moc_path,
        "draft_deleted": delete_draft,
    }


def _build_final_frontmatter(
    meta: dict,
    canal_slug_str: str,
    transcript_link: Optional[str],
    ts_id: str,
) -> str:
    lines = ["---"]
    lines.append(f"ID: {ts_id}")
    lines.append("tipo: literatura")
    lines.append("subtipo: vídeo")
    lines.append(f"titulo: {_yaml_quote(_as_str(meta.get('titulo')))}")
    lines.append(f"autores: {_yaml_quote(_as_str(meta.get('canal')))}")
    pub = _as_str(meta.get("data_publicacao"))
    if pub:
        lines.append(f"ano: {pub[:4]}")
    lines.append("fonte: YouTube")
    lines.append(f"url: {_as_str(meta.get('url'))}")
    lines.append(f"canal: {_yaml_quote(_as_str(meta.get('canal')))}")
    if meta.get("canal_url"):
        lines.append(f"canal_url: {_as_str(meta['canal_url'])}")
    if meta.get("duracao"):
        lines.append(f"duracao: {_as_str(meta['duracao'])}")
    if pub:
        lines.append(f"data-publicacao: {pub}")
    lines.append(f"data-leitura: {_today_br()}")
    if meta.get("idioma_transcript"):
        lines.append(f"idioma_original: {meta['idioma_transcript']}")
        lines.append(f"transcript_origem: {meta.get('transcript_origem', 'auto')}")
    else:
        lines.append("transcript: indisponivel")
    lines.append("status: lido")
    lines.append("tags:")
    lines.append("  - literatura")
    lines.append("  - youtube")
    lines.append(f'up: "[[{canal_slug_str}]]"')
    if transcript_link:
        lines.append(f'transcript_file: "[[{transcript_link}]]"')
    lines.append("---")
    return "\n".join(lines)


def _build_header(meta: dict, ts_id: str) -> str:
    pub = _as_str(meta.get("data_publicacao")) or "data desconhecida"
    dur = _as_str(meta.get("duracao"))
    if meta.get("idioma_transcript"):
        lang_label = f"{meta['idioma_transcript']} ({meta.get('transcript_origem', 'auto')})"
    else:
        lang_label = "transcript indisponível"
    return (
        f"\n# {ts_id} — {_as_str(meta.get('titulo'))}\n\n"
        f"> [!info] Vídeo de {_as_str(meta.get('canal'))}\n"
        f"> **Publicado:** {pub} | **Duração:** {dur} | **Idioma:** {lang_label}\n\n"
    )


def _build_transcript_file(
    meta: dict,
    segments: list[Segment],
    ts_id: str,
    slug: str,
    lang: Optional[str],
    origem: Optional[str],
) -> str:
    fm = [
        "---",
        "tipo: transcript-bruto",
        f'parent: "[[3-{ts_id}-{slug}]]"',
        f"fonte_video: {_as_str(meta.get('url'))}",
        f"canal: {_yaml_quote(_as_str(meta.get('canal')))}",
    ]
    if lang:
        fm.append(f"idioma: {lang}")
    if origem:
        fm.append(f"origem: {origem}")
    fm.append(f"segmentos: {len(segments)}")
    fm.append("---")
    return "\n".join(fm) + f"\n\n# Transcript — {_as_str(meta.get('titulo'))}\n\nVídeo: {_as_str(meta.get('url'))}\n" + segments_to_markdown(segments)


def _update_channel_card(
    canal: str,
    canal_slug_str: str,
    meta: dict,
    ts_id: str,
    slug: str,
    note_stem: str,
) -> Path:
    CARDS_DE_PESSOA_DIR.mkdir(parents=True, exist_ok=True)
    card_path = CARDS_DE_PESSOA_DIR / f"{canal_slug_str}.md"
    pub = _as_str(meta.get("data_publicacao")) or "sem data"
    line = f"- [[{note_stem}]] — {_as_str(meta.get('titulo'))} ({pub})"

    if not card_path.exists():
        fm = ["---", "tipo: card-vivo", f"canal: {_yaml_quote(canal)}"]
        if meta.get("canal_url"):
            fm.append(f"canal_url: {meta['canal_url']}")
        fm.extend(
            [
                f"created: {_today_iso()}",
                f"updated: {_today_iso()}",
                "tags:",
                "  - card-vivo",
                "  - youtube",
                f"  - {canal_slug_str.lower()}",
                "---",
            ]
        )
        content = (
            "\n".join(fm)
            + "\n\n"
            + f"# {canal}\n\n"
            + "Card vivo do canal. Vídeos processados pelo `yt-nota` listados abaixo.\n\n"
            + "## Vídeos processados\n\n"
            + line
            + "\n"
        )
        card_path.write_text(content, encoding="utf-8")
        return card_path

    existing = card_path.read_text(encoding="utf-8")
    existing = re.sub(
        r"^updated:.*$", f"updated: {_today_iso()}", existing, count=1, flags=re.MULTILINE
    )
    if "## Vídeos processados" in existing:
        existing = existing.replace(
            "## Vídeos processados\n",
            f"## Vídeos processados\n\n{line}\n",
            1,
        )
        existing = existing.replace(f"\n\n{line}\n\n\n", f"\n\n{line}\n\n", 1)
    else:
        existing = existing.rstrip() + "\n\n## Vídeos processados\n\n" + line + "\n"
    card_path.write_text(existing, encoding="utf-8")
    return card_path


def _update_moc(tema: str, meta: dict, note_stem: str) -> Optional[Path]:
    dominio = _as_str(meta.get("dominio") or "")
    candidates = []
    if dominio:
        candidates.append(
            VAULT_PATH / "30-Recursos" / dominio / tema / f"MOC-{tema}.md"
        )
    candidates += [
        VAULT_PATH / "30-Recursos" / tema / f"MOC-{tema}.md",
        VAULT_PATH / "30-Recursos" / tema / f"{tema}.md",
        VAULT_PATH / "20-Areas" / tema / f"{tema}.md",
    ]
    # A bare "30-Recursos/<tema>.md" candidate is forbidden: on Windows the
    # lookup is case-insensitive and tema "Claude" resolves to the CLAUDE.md
    # instruction file (incident 2026-07, repeated 2026-08).
    candidates = [c for c in candidates if c.name.lower() != "claude.md"]
    moc_path = next((c for c in candidates if c.exists()), None)
    if moc_path is None:
        return None
    line = f"- [[{note_stem}|{_as_str(meta.get('titulo'))}]]"
    content = moc_path.read_text(encoding="utf-8")
    if "```dataview" in content:
        # MOC auto-lists notes via Dataview; manual link lines would duplicate.
        return moc_path
    if note_stem in content:
        return moc_path
    if "## Literatura" in content:
        content = content.replace(
            "## Literatura\n", f"## Literatura\n\n{line}\n", 1
        )
        content = content.replace(f"\n\n{line}\n\n\n", f"\n\n{line}\n\n", 1)
    else:
        content = content.rstrip() + "\n\n## Literatura\n\n" + line + "\n"
    moc_path.write_text(content, encoding="utf-8")
    return moc_path


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    i = 2
    while True:
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def list_pending_drafts() -> list[Path]:
    if not PROCESSAR_DIR.exists():
        return []
    return sorted(PROCESSAR_DIR.glob("*.draft.md"))


# ---------------------------------------------------------------------------
# Dedup index
# ---------------------------------------------------------------------------
# Cache em memória por diretório: {dir: {video_id: evidence_path}}. Cada diretório
# é varrido UMA vez por execução (antes: releitura completa de todos os arquivos
# a cada vídeo do batch). write_draft registra o draft novo no índice, então a
# mesma URL duplicada numa queue continua dedupando dentro da mesma run.

_dedup_index: dict[Path, dict[str, Path]] = {}

_VIDEO_ID_CONTENT_RES = (
    re.compile(r"^video_id:\s*(\S+)", re.MULTILINE),
    re.compile(r"[?&]v=([A-Za-z0-9_-]+)"),
)


def reset_dedup_index() -> None:
    """Invalida o índice (testes ou processos longos que editam o vault por fora)."""
    _dedup_index.clear()


def _index_dir(directory: Path, pattern: str) -> dict[str, Path]:
    cached = _dedup_index.get(directory)
    if cached is not None:
        return cached
    index: dict[str, Path] = {}
    if directory.exists():
        for f in sorted(directory.glob(pattern)):
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for rx in _VIDEO_ID_CONTENT_RES:
                for vid in rx.findall(content):
                    index.setdefault(vid, f)
    _dedup_index[directory] = index
    return index


def _register_draft_in_index(video_id: str, draft_path: Path) -> None:
    idx = _dedup_index.get(PROCESSAR_DIR)
    if idx is not None and video_id:
        idx.setdefault(video_id, draft_path)


def _check_canal_dir(canal_dir: Path, video_id: str) -> Optional[Path]:
    hit = _index_dir(canal_dir, "3-*.md").get(video_id)
    if hit is not None:
        return hit
    return _index_dir(canal_dir / "transcripts", "*.transcript.md").get(video_id)


def is_video_already_processed(
    video_id: str,
    channel_slug: str,
    dominio: Optional[str] = None,
) -> tuple[bool, Optional[Path]]:
    """Verifica se um video_id já tem nota final OU draft pendente no vault.

    `dominio` (opcional): se passado, prioriza `<dominio>/<canal>/`; caso contrário
    varre TODOS os subdomínios pra catch posicionamento legado.
    Retorna (já_processado, path_da_evidencia_ou_None).
    """
    if not video_id:
        return False, None

    # Candidatos: novo (<dominio>/<canal>) + todos os <dominio>/<canal> existentes
    # (cobre canais espalhados em mais de um domínio se houver overlap futuro).
    canal_dirs: list[Path] = []
    if dominio:
        canal_dirs.append(RECURSOS_DIR / dominio / channel_slug)
    for dom in config.get_valid_dominios():
        cand = RECURSOS_DIR / dom / channel_slug
        if cand.is_dir() and cand not in canal_dirs:
            canal_dirs.append(cand)

    for canal_dir in canal_dirs:
        hit = _check_canal_dir(canal_dir, video_id)
        if hit is not None:
            return True, hit

    hit = _index_dir(PROCESSAR_DIR, "*.draft.md").get(video_id)
    if hit is not None:
        return True, hit

    return False, None


def find_video_anywhere(video_id: str) -> Optional[Path]:
    """Busca video_id em todos os domínios de 30-Recursos + drafts pendentes, sem saber o canal.

    Usado pelo CLI pra dedup ANTES do extract_info (o canal só é conhecido após
    a chamada de rede). Varre `30-Recursos/<dominio>/<canal>/` nos domínios válidos,
    reusando o mesmo índice por diretório.
    """
    if not video_id:
        return None

    hit = _index_dir(PROCESSAR_DIR, "*.draft.md").get(video_id)
    if hit is not None:
        return hit

    for dom in sorted(config.get_valid_dominios()):
        dominio_dir = RECURSOS_DIR / dom
        if not dominio_dir.is_dir():
            continue
        for canal_dir in sorted(dominio_dir.iterdir()):
            if not canal_dir.is_dir():
                continue
            hit = _check_canal_dir(canal_dir, video_id)
            if hit is not None:
                return hit
    return None

"""Escrita no vault Obsidian.

Dois modos:
- `write_draft`: chamado pelo CLI principal. Escreve um draft com metadata + transcript
  na pasta `_drafts/`. A síntese acontece depois via skill `/yt-sintese` no Claude Code.
- `finalize`: chamado pelo subcomando `yt-nota finalize`. Recebe o body sintetizado,
  monta a nota final + transcript + channel card + (opcional) MOC tema.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

from .config import DRAFTS_DIR, LITERATURA_DIR, NOTAS_DIR, VAULT_PATH
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
) -> Path:
    """Escreve draft em _drafts/ aguardando síntese.

    Retorna o path do draft criado.
    """
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

    canal = video["channel"] or "Canal-Desconhecido"
    ts_id = _now_id()
    slug = title_slug(video["title"])

    draft_path = _unique_path(DRAFTS_DIR / f"{ts_id}-{slug}.draft.md")

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
        fm.append(f"transcript_origem: {'auto' if transcript_info['is_auto'] else 'manual'}")
    else:
        fm.append("transcript: indisponivel")
    if video.get("tags"):
        fm.append("tags_canal:")
        for tag in video["tags"][:12]:
            fm.append(f"  - {_yaml_quote(tag)}")
    if tema:
        fm.append(f"tema: {_yaml_quote(tema)}")
    fm.append(f"created: {_today_iso()}")
    fm.append("---")

    body_parts = [
        "\n".join(fm),
        "",
        f"# DRAFT — {video['title']}",
        "",
        "> [!warning] Síntese pendente",
        "> Esse arquivo aguarda processamento via skill `/yt-sintese` no Claude Code.",
        "> Quando processado, vira nota final em `30-Recursos/Literatura/<Canal>/`,",
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
) -> dict:
    """Lê o draft, junta com o body sintetizado, escreve nota final + transcript + card.

    `body_markdown` é o output da síntese (as 7 seções).
    Retorna paths dos arquivos criados/atualizados.
    """
    meta, segments, _ = _parse_draft(draft_path)

    canal = meta.get("canal") or "Canal-Desconhecido"
    canal_slug_str = channel_slug(canal)
    titulo = meta.get("titulo") or "Sem título"
    slug = title_slug(titulo)
    ts_id = meta.get("draft_id") or _now_id()
    transcript_lang = meta.get("idioma_transcript")
    transcript_origem = meta.get("transcript_origem")

    canal_dir = LITERATURA_DIR / canal_slug_str
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
    NOTAS_DIR.mkdir(parents=True, exist_ok=True)
    card_path = NOTAS_DIR / f"{canal_slug_str}.md"
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
    candidates = [
        VAULT_PATH / "30-Recursos" / f"{tema}.md",
        VAULT_PATH / "20-Áreas" / tema / f"{tema}.md",
    ]
    moc_path = next((c for c in candidates if c.exists()), None)
    if moc_path is None:
        return None
    line = f"- [[{note_stem}|{_as_str(meta.get('titulo'))}]]"
    content = moc_path.read_text(encoding="utf-8")
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
    if not DRAFTS_DIR.exists():
        return []
    return sorted(DRAFTS_DIR.glob("*.draft.md"))


def is_video_already_processed(video_id: str, channel_slug: str) -> tuple[bool, Optional[Path]]:
    """Verifica se um video_id já tem nota final OU draft pendente no vault.

    Retorna (já_processado, path_da_evidencia_ou_None).
    Path serve pra log informativo.
    """
    if not video_id:
        return False, None

    canal_dir = LITERATURA_DIR / channel_slug
    if canal_dir.exists():
        for note in canal_dir.glob("3-*.md"):
            if note.name.endswith(".transcript.md"):
                continue
            try:
                content = note.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if f"video_id: {video_id}" in content or f"v={video_id}" in content:
                return True, note
        transcripts_dir = canal_dir / "transcripts"
        if transcripts_dir.exists():
            for tr in transcripts_dir.glob("*.transcript.md"):
                try:
                    content = tr.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if f"v={video_id}" in content or f"video_id: {video_id}" in content:
                    return True, tr

    if DRAFTS_DIR.exists():
        for draft in DRAFTS_DIR.glob("*.draft.md"):
            try:
                content = draft.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if f"video_id: {video_id}" in content:
                return True, draft

    return False, None

"""Escrita das notas no vault Obsidian.

Gera dois arquivos por vídeo: a nota síntese e o transcript bruto.
Atualiza (ou cria) o channel card. Opcionalmente atualiza um MOC temático.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import LITERATURA_DIR, NOTAS_DIR, VAULT_PATH
from .slug import channel_slug, title_slug
from .transcript import Segment, segments_to_markdown


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _today_br() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def _yaml_str(value: str) -> str:
    """Escapa string pra YAML (frontmatter)."""
    if not value:
        return '""'
    if any(c in value for c in ':#"\'\n[]{}|>&%@`'):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return value


def _build_frontmatter(
    video: dict,
    transcript_info: Optional[dict],
    transcript_link: Optional[str],
    canal_slug_str: str,
    ts_id: str,
) -> str:
    lines: list[str] = ["---"]
    lines.append(f"ID: {ts_id}")
    lines.append("tipo: literatura")
    lines.append("subtipo: vídeo")
    lines.append(f"titulo: {_yaml_str(video['title'])}")
    lines.append(f"autores: {_yaml_str(video['channel'])}")

    pub = video.get("upload_date_iso") or ""
    if pub:
        lines.append(f"ano: {pub[:4]}")
    lines.append("fonte: YouTube")
    lines.append(f"url: {video['url']}")
    lines.append(f"canal: {_yaml_str(video['channel'])}")
    if video.get("channel_url"):
        lines.append(f"canal_url: {video['channel_url']}")
    if video.get("duration_human"):
        lines.append(f"duracao: {video['duration_human']}")
    if pub:
        lines.append(f"data-publicacao: {pub}")
    lines.append(f"data-leitura: {_today_br()}")

    if transcript_info:
        lines.append(f"idioma_original: {transcript_info['language']}")
        lines.append(
            f"transcript_origem: {'auto' if transcript_info['is_auto'] else 'manual'}"
        )
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


def _build_header(video: dict, transcript_info: Optional[dict], ts_id: str) -> str:
    pub = video.get("upload_date_iso") or "data desconhecida"
    dur = video.get("duration_human") or ""
    if transcript_info:
        lang_label = f"{transcript_info['language']} ({'auto' if transcript_info['is_auto'] else 'manual'})"
    else:
        lang_label = "transcript indisponível"

    return (
        f"\n# {ts_id} — {video['title']}\n\n"
        f"> [!info] Vídeo de {video['channel']}\n"
        f"> **Publicado:** {pub} | **Duração:** {dur} | **Idioma:** {lang_label}\n\n"
    )


def _build_transcript_file(
    video: dict, segments: list[Segment], transcript_info: dict, parent_id: str, parent_slug: str
) -> str:
    fm = [
        "---",
        "tipo: transcript-bruto",
        f'parent: "[[3-{parent_id}-{parent_slug}]]"',
        f"fonte_video: {video['url']}",
        f"canal: {_yaml_str(video['channel'])}",
        f"idioma: {transcript_info['language']}",
        f"origem: {'auto' if transcript_info['is_auto'] else 'manual'}",
        f"segmentos: {len(segments)}",
        "---",
        "",
        f"# Transcript bruto — {video['title']}",
        "",
        f"Vídeo: {video['url']}",
    ]
    return "\n".join(fm) + "\n" + segments_to_markdown(segments)


def _unique_path(path: Path) -> Path:
    """Se path já existe, anexa sufixo numérico antes da extensão."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 2
    while True:
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def write_note(
    video: dict,
    body: str,
    segments: Optional[list[Segment]],
    transcript_info: Optional[dict],
    *,
    no_channel_card: bool = False,
    tema: Optional[str] = None,
) -> dict:
    """Escreve nota síntese + (opcionalmente) transcript + channel card + tema MOC.

    Retorna dict com paths absolutos dos arquivos tocados.
    """
    canal = video["channel"] or "Canal-Desconhecido"
    canal_slug_str = channel_slug(canal)
    ts_id = _now_id()
    slug = title_slug(video["title"])

    canal_dir = LITERATURA_DIR / canal_slug_str
    canal_dir.mkdir(parents=True, exist_ok=True)

    note_path = _unique_path(canal_dir / f"3-{ts_id}-{slug}.md")
    transcript_path: Optional[Path] = None

    if segments and transcript_info:
        transcript_path = canal_dir / f"{note_path.stem}.transcript.md"
        transcript_link = transcript_path.stem
    else:
        transcript_link = None

    fm = _build_frontmatter(video, transcript_info, transcript_link, canal_slug_str, ts_id)
    header = _build_header(video, transcript_info, ts_id)
    note_path.write_text(fm + "\n" + header + body.strip() + "\n", encoding="utf-8")

    if segments and transcript_info and transcript_path is not None:
        transcript_path.write_text(
            _build_transcript_file(video, segments, transcript_info, ts_id, slug),
            encoding="utf-8",
        )

    card_path: Optional[Path] = None
    if not no_channel_card:
        card_path = _update_channel_card(canal, canal_slug_str, video, ts_id, slug, note_path.stem)

    moc_path: Optional[Path] = None
    if tema:
        moc_path = _update_moc(tema, video, ts_id, slug, note_path.stem)

    return {
        "note_path": note_path,
        "transcript_path": transcript_path,
        "channel_card_path": card_path,
        "moc_path": moc_path,
    }


def _update_channel_card(
    canal: str, canal_slug_str: str, video: dict, ts_id: str, slug: str, note_stem: str
) -> Path:
    NOTAS_DIR.mkdir(parents=True, exist_ok=True)
    card_path = NOTAS_DIR / f"{canal_slug_str}.md"

    line = (
        f"- [[{note_stem}]] — {video['title']} "
        f"({video.get('upload_date_iso', 'sem data')})"
    )

    if not card_path.exists():
        fm = [
            "---",
            "tipo: card-vivo",
            f"canal: {_yaml_str(canal)}",
        ]
        if video.get("channel_url"):
            fm.append(f"canal_url: {video['channel_url']}")
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
            + "Card vivo do canal. Vídeos processados por `yt-nota` listados abaixo.\n\n"
            + "## Vídeos processados\n\n"
            + line
            + "\n"
        )
        card_path.write_text(content, encoding="utf-8")
        return card_path

    existing = card_path.read_text(encoding="utf-8")
    existing = re.sub(
        r"^updated:.*$",
        f"updated: {_today_iso()}",
        existing,
        count=1,
        flags=re.MULTILINE,
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


def _update_moc(tema: str, video: dict, ts_id: str, slug: str, note_stem: str) -> Optional[Path]:
    """Update um MOC temático. Procura em 30-Recursos/{tema}.md primeiro."""
    candidates = [
        VAULT_PATH / "30-Recursos" / f"{tema}.md",
        VAULT_PATH / "20-Áreas" / tema / f"{tema}.md",
    ]
    moc_path = next((c for c in candidates if c.exists()), None)
    if moc_path is None:
        return None

    line = f"- [[{note_stem}|{video['title']}]]"
    content = moc_path.read_text(encoding="utf-8")
    if note_stem in content:
        return moc_path

    if "## Literatura" in content:
        content = content.replace(
            "## Literatura\n",
            f"## Literatura\n\n{line}\n",
            1,
        )
        content = content.replace(f"\n\n{line}\n\n\n", f"\n\n{line}\n\n", 1)
    else:
        content = content.rstrip() + "\n\n## Literatura\n\n" + line + "\n"

    moc_path.write_text(content, encoding="utf-8")
    return moc_path

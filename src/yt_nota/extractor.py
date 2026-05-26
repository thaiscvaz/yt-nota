"""Wrapper do yt-dlp pra extrair metadata + transcript do YouTube.

Usa a API Python do yt-dlp diretamente. Subtitles vêm como lista de URLs por idioma;
busca a versão VTT da preferência mais alta (manual > auto, pt > en > qualquer).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
import yt_dlp

from .transcript import Segment, parse_vtt

log = logging.getLogger(__name__)


class ExtractError(Exception):
    pass


class RateLimitError(ExtractError):
    """YouTube respondeu 429 (Too Many Requests).

    Sinaliza pro CLI que continuar processando vai dar 429 também — rate limit
    é por janela, não por URL. Parada precoce evita desperdiçar URLs do queue.
    """


PREFERRED_LANGS = [
    "pt-BR",
    "pt",
    "pt-orig",
    "en",
    "en-US",
    "en-GB",
    "en-orig",
]


def _ydl_opts(with_cookies: bool, flat_playlist: bool = False) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noprogress": True,
    }
    if flat_playlist:
        opts["extract_flat"] = "in_playlist"
    if with_cookies:
        opts["cookiesfrombrowser"] = ("chrome",)
    return opts


def extract_info(url: str, *, with_cookies: bool = False, flat_playlist: bool = False) -> dict:
    opts = _ydl_opts(with_cookies, flat_playlist=flat_playlist)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "cookies" in msg.lower() and with_cookies:
            raise ExtractError(
                "Falha lendo cookies do Chrome. Feche o Chrome e rode de novo, "
                "ou rode sem --with-cookies."
            ) from e
        raise ExtractError(f"yt-dlp falhou: {msg}") from e

    if not info:
        raise ExtractError(f"Sem info para {url}")
    return info


def is_playlist(info: dict) -> bool:
    return info.get("_type") == "playlist" or "entries" in info


def playlist_video_urls(info: dict) -> list[str]:
    urls: list[str] = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        vid = entry.get("id")
        if vid:
            urls.append(f"https://www.youtube.com/watch?v={vid}")
        elif entry.get("url"):
            urls.append(entry["url"])
    return urls


def normalize_video_info(info: dict) -> dict:
    """Converte info crua do yt-dlp em dict limpo pra downstream."""
    upload_date = info.get("upload_date") or ""
    iso_date = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
        if len(upload_date) == 8 and upload_date.isdigit()
        else ""
    )

    duration = int(info.get("duration") or 0)
    h, rem = divmod(duration, 3600)
    m, s = divmod(rem, 60)
    if h:
        duration_human = f"{h}h {m}m"
    elif m:
        duration_human = f"{m}m {s}s"
    else:
        duration_human = f"{s}s"

    return {
        "url": info.get("webpage_url") or info.get("original_url") or "",
        "video_id": info.get("id") or "",
        "title": info.get("title") or "",
        "channel": info.get("uploader") or info.get("channel") or "",
        "channel_url": info.get("uploader_url") or info.get("channel_url") or "",
        "channel_id": info.get("channel_id") or "",
        "upload_date_iso": iso_date,
        "duration_seconds": duration,
        "duration_human": duration_human,
        "description": info.get("description") or "",
        "tags": info.get("tags") or [],
        "thumbnail": info.get("thumbnail") or "",
        "_raw_subs": info.get("subtitles") or {},
        "_raw_auto": info.get("automatic_captions") or {},
    }


def _pick_subtitle(info_subs: dict, info_auto: dict) -> Optional[tuple[str, bool, list]]:
    """Escolhe a melhor combinação (idioma, manual_ou_auto, lista_de_urls).

    Ordem: manual em preferred langs > manual pt.* > manual en.* > auto em preferred langs
    > auto pt.* > auto en.* > qualquer manual > qualquer auto.
    """
    for lang in PREFERRED_LANGS:
        if lang in info_subs:
            return (lang, False, info_subs[lang])

    for k, v in info_subs.items():
        if k.lower().startswith("pt"):
            return (k, False, v)
    for k, v in info_subs.items():
        if k.lower().startswith("en"):
            return (k, False, v)

    for lang in PREFERRED_LANGS:
        if lang in info_auto:
            return (lang, True, info_auto[lang])

    for k, v in info_auto.items():
        if k.lower().startswith("pt"):
            return (k, True, v)
    for k, v in info_auto.items():
        if k.lower().startswith("en"):
            return (k, True, v)

    if info_subs:
        k = next(iter(info_subs))
        return (k, False, info_subs[k])
    if info_auto:
        k = next(iter(info_auto))
        return (k, True, info_auto[k])
    return None


def _fetch_vtt(sub_entries: list) -> str:
    vtt = next((e for e in sub_entries if e.get("ext") == "vtt"), None)
    if vtt is None:
        if not sub_entries:
            raise ExtractError("Sem entradas de subtitle")
        vtt = sub_entries[0]
    try:
        r = httpx.get(vtt["url"], timeout=30, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ExtractError(f"Falha baixando subtitle: {e}") from e
    if r.status_code == 429:
        raise RateLimitError("YouTube respondeu 429 (rate limit). Tente novamente em algumas horas.")
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise ExtractError(f"Falha baixando subtitle: {e}") from e
    return r.text


def extract_transcript(video: dict) -> Optional[dict]:
    """Recebe video normalizado (com _raw_subs e _raw_auto). Retorna transcript ou None."""
    pick = _pick_subtitle(video.get("_raw_subs", {}), video.get("_raw_auto", {}))
    if pick is None:
        return None

    lang, is_auto, entries = pick
    try:
        vtt_text = _fetch_vtt(entries)
    except RateLimitError:
        raise
    except ExtractError as e:
        log.warning("Falha buscando transcript em %s: %s", lang, e)
        return None

    segments = parse_vtt(vtt_text)
    if not segments:
        return None

    return {
        "language": lang,
        "is_auto": is_auto,
        "segments": segments,
    }

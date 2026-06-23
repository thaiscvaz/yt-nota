"""Wrapper do yt-dlp pra extrair metadata + transcript do YouTube.

Usa a API Python do yt-dlp diretamente. Subtitles vêm como lista de URLs por idioma;
busca a versão VTT da preferência mais alta (manual > auto, pt > en > qualquer).
"""

from __future__ import annotations

import logging
import re
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

# Idiomas aceitos como hint pro Whisper (prefixo de 2 letras)
WHISPER_HINT_LANGS = {"pt", "en", "es", "fr", "de", "it", "ja", "zh"}


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


_VIDEO_ID_URL_RE = re.compile(
    r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})"
)


def video_id_from_url(url: str) -> Optional[str]:
    """Extrai o video_id direto da URL, sem rede. None se o formato não for reconhecido.

    Usado pelo CLI pra dedup ANTES do extract_info — vídeo já processado não gasta
    chamada de rede nem sleep. URLs de playlist/canal retornam None (caller decide).
    """
    m = _VIDEO_ID_URL_RE.search(url)
    return m.group(1) if m else None


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
        # Idioma ORIGINAL do áudio (yt-dlp). Crítico pro hint do Whisper: o idioma
        # da legenda escolhida pode ser tradução de máquina e não reflete o áudio.
        "language": info.get("language") or "",
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


def _pick_subtitle(
    info_subs: dict, info_auto: dict, original_lang: Optional[str] = None
) -> Optional[tuple[str, bool, list]]:
    """Escolhe a melhor combinação (idioma, manual_ou_auto, lista_de_urls).

    Ordem: manual em preferred langs > manual pt.* > manual en.* > auto no idioma
    ORIGINAL do áudio > auto em preferred langs > auto pt.* > auto en.* >
    qualquer manual > qualquer auto.

    Auto-captions traduzidas (ex: pt-BR pra áudio EN) são tradução de máquina —
    dupla degradação na síntese. A track original (sufixo -orig ou código do
    idioma do áudio) é sempre mais fiel, por isso vence as preferred langs.
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

    if original_lang:
        prefix = original_lang.split("-")[0].lower()
        for key in (f"{prefix}-orig", original_lang, prefix):
            if key in info_auto:
                return (key, True, info_auto[key])
        for k, v in info_auto.items():
            if k.lower().startswith(prefix):
                return (k, True, v)

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


def _whisper_lang_hint(
    original_lang: Optional[str],
    picked_lang: Optional[str],
    picked_is_auto: bool,
) -> Optional[str]:
    """Hint de idioma pro Whisper. Prioriza o idioma ORIGINAL do áudio.

    O idioma da legenda escolhida pode ser tradução de máquina (auto-caption pt-BR
    pra áudio EN). Forçar esse idioma faz o Whisper TRADUZIR em vez de transcrever.
    Auto-captions traduzidas nunca viram hint; legenda manual ou track -orig só
    entram na ausência do idioma original. Sem candidato confiável → None
    (auto-detect do Whisper).
    """
    candidates = []
    if original_lang:
        candidates.append(original_lang)
    if picked_lang and (not picked_is_auto or picked_lang.endswith("-orig")):
        candidates.append(picked_lang)
    for cand in candidates:
        prefix = cand.split("-")[0].lower()
        if prefix in WHISPER_HINT_LANGS:
            return prefix
    return None


def extract_transcript(
    video: dict,
    *,
    whisper_fallback: bool = False,
    whisper_model: str = "small",
    with_cookies: bool = False,
) -> Optional[dict]:
    """Recebe video normalizado (com _raw_subs e _raw_auto). Retorna transcript ou None.

    Se `whisper_fallback=True` e o YouTube responder 429 nas legendas, baixa o áudio
    e transcreve localmente com faster-whisper (precisa `pip install yt-nota[whisper]`).
    Se o fallback estiver indisponível ou falhar, o RateLimitError é propagado —
    preserva a semântica de parada precoce + pending.txt da v0.2.4.
    """
    original_lang = (video.get("language") or "").strip() or None
    pick = _pick_subtitle(
        video.get("_raw_subs", {}), video.get("_raw_auto", {}), original_lang=original_lang
    )

    if pick is not None:
        lang, is_auto, entries = pick
        try:
            vtt_text = _fetch_vtt(entries)
            segments = parse_vtt(vtt_text)
            if segments:
                return {
                    "language": lang,
                    "is_auto": is_auto,
                    "origin": "auto" if is_auto else "manual",
                    "segments": segments,
                }
        except RateLimitError:
            if not whisper_fallback:
                raise
            log.warning(
                "Rate limit 429 nas legendas. Caindo no Whisper local (modelo %s)...",
                whisper_model,
            )
            result = _try_whisper_fallback(
                video,
                model=whisper_model,
                lang_hint=_whisper_lang_hint(original_lang, lang, is_auto),
                with_cookies=with_cookies,
            )
            if result is None:
                raise
            return result
        except ExtractError as e:
            log.warning("Falha buscando transcript em %s: %s", lang, e)

    # Sem legenda disponível no YouTube. Whisper fallback opcional aqui também
    # quando o usuário quer transcript pra vídeos que não têm captions.
    if whisper_fallback:
        log.info("Sem legenda disponível. Tentando Whisper local (modelo %s)...", whisper_model)
        return _try_whisper_fallback(
            video,
            model=whisper_model,
            lang_hint=_whisper_lang_hint(original_lang, None, False),
            with_cookies=with_cookies,
        )

    return None


def _try_whisper_fallback(
    video: dict, *, model: str, lang_hint: Optional[str], with_cookies: bool
) -> Optional[dict]:
    """Wrapper isolado pra import tardio do módulo whisper_fallback (optional dep).

    `lang_hint` já vem normalizado por `_whisper_lang_hint` (prefixo 2 letras ou None).
    """
    try:
        from . import whisper_fallback
    except ImportError:
        log.warning("Whisper fallback indisponível (import falhou).")
        return None

    if not whisper_fallback.is_available():
        log.warning(
            "Whisper fallback solicitado mas faster-whisper não está instalado. "
            "Instale com: pip install yt-nota[whisper]"
        )
        return None

    url = video.get("url") or ""
    if not url:
        log.warning("Whisper fallback: video sem URL, abortando.")
        return None

    return whisper_fallback.transcribe_from_video(
        url, model_size=model, language=lang_hint, with_cookies=with_cookies
    )

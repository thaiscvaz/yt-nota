"""Fallback de transcrição via Whisper local quando o YouTube rate-limita legendas.

Em 2025 o endpoint `timedtext` do YouTube passou a devolver HTTP 429 muito mais
agressivo, mesmo com backoff exponencial. O endpoint de áudio (mesmo do vídeo)
continua livre. Esse módulo aproveita isso: baixa só o áudio (formato 139, m4a
49 kbps, ~5 MB por 13 min) via yt-dlp e transcreve localmente com faster-whisper.

Optional dependency: `pip install yt-nota[whisper]` instala `faster-whisper`.
Sem essa instalação extra, `is_available()` devolve False e o fallback é skip.

Output compatível com `extractor.extract_transcript()`:
    {"language": str, "is_auto": True, "origin": "whisper-local", "segments": [Segment...]}
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import yt_dlp

from .transcript import Segment

log = logging.getLogger(__name__)


# Modelo default. "small" oferece boa qualidade em PT-BR técnico em CPU int8
# (~0.3x realtime). Trocar com env `YT_NOTA_WHISPER_MODEL` ou flag `--whisper-model`.
DEFAULT_MODEL = "small"
SUPPORTED_MODELS = {"tiny", "base", "small", "medium", "large", "large-v2", "large-v3"}

# Cache local de modelos baixados (~244 MB pro small). Reusa entre execuções.
_MODEL_CACHE: dict[str, object] = {}


def is_available() -> bool:
    """True se faster-whisper estiver instalado nesse venv."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _seconds_to_label(total_seconds: float) -> str:
    """Compat com transcript.Segment: 'MM:SS' ou 'H:MM:SS'."""
    total = int(total_seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _download_audio(video_url: str, out_dir: Path, *, with_cookies: bool = False) -> Optional[Path]:
    """Baixa só o áudio (m4a low-bitrate) via yt-dlp programaticamente.

    Retorna o path do arquivo baixado, ou None se falhar.
    """
    out_template = str(out_dir / "audio.%(ext)s")
    opts: dict = {
        "format": "139/140/bestaudio[ext=m4a]/bestaudio",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": False,
        "noplaylist": True,
    }
    if with_cookies:
        opts["cookiesfrombrowser"] = ("chrome",)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([video_url])
    except yt_dlp.utils.DownloadError as e:
        log.warning("Whisper fallback: falha baixando áudio de %s: %s", video_url, e)
        return None

    # yt-dlp pode usar .m4a, .webm, .opus dependendo do formato disponível
    for ext in ("m4a", "webm", "opus", "mp3"):
        candidate = out_dir / f"audio.{ext}"
        if candidate.exists() and candidate.stat().st_size > 10_000:
            return candidate
    log.warning("Whisper fallback: yt-dlp completou mas nenhum áudio foi gravado em %s", out_dir)
    return None


def _get_model(model_size: str):
    """Carrega/cacheia o WhisperModel. Reusa instância entre chamadas."""
    if model_size in _MODEL_CACHE:
        return _MODEL_CACHE[model_size]
    from faster_whisper import WhisperModel  # import tardio (optional dep)
    log.info("Whisper fallback: carregando modelo '%s' (primeira vez baixa do HF Hub)...", model_size)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    _MODEL_CACHE[model_size] = model
    return model


def _transcribe_audio(audio_path: Path, *, model_size: str, language: Optional[str]) -> Optional[dict]:
    """Roda faster-whisper no áudio e devolve dict no formato do extract_transcript.

    `language` é uma dica (ex: "pt"). Se None, deixa o Whisper detectar.
    """
    model = _get_model(model_size)
    try:
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5,
        )
    except Exception as e:  # erro genérico do ctranslate2 / cpu / arquivo corrupto
        log.warning("Whisper fallback: transcrição falhou em %s: %s", audio_path, e)
        return None

    segments: list[Segment] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(Segment(t=_seconds_to_label(seg.start), text=text))

    if not segments:
        return None

    detected_lang = info.language or language or "pt"
    return {
        "language": detected_lang,
        "is_auto": True,
        "origin": "whisper-local",
        "segments": segments,
    }


def transcribe_from_video(
    video_url: str,
    *,
    model_size: str = DEFAULT_MODEL,
    language: Optional[str] = None,
    with_cookies: bool = False,
) -> Optional[dict]:
    """Pipeline completo: baixa áudio + transcreve. Retorna dict compatível ou None."""
    if not is_available():
        log.warning(
            "Whisper fallback solicitado mas faster-whisper não está instalado. "
            "Instale com: pip install yt-nota[whisper]"
        )
        return None

    if model_size not in SUPPORTED_MODELS:
        log.warning("Whisper fallback: modelo '%s' inválido. Usando '%s'.", model_size, DEFAULT_MODEL)
        model_size = DEFAULT_MODEL

    with tempfile.TemporaryDirectory(prefix="yt-nota-whisper-") as td:
        audio_path = _download_audio(video_url, Path(td), with_cookies=with_cookies)
        if audio_path is None:
            return None
        return _transcribe_audio(audio_path, model_size=model_size, language=language)


def resolve_model_from_env(cli_value: Optional[str] = None) -> str:
    """Resolução em cascata: --whisper-model > YT_NOTA_WHISPER_MODEL > default."""
    if cli_value:
        return cli_value
    return os.environ.get("YT_NOTA_WHISPER_MODEL", DEFAULT_MODEL)


def resolve_enabled_from_env(cli_value: Optional[bool] = None) -> bool:
    """Resolução: --no-whisper-fallback override > YT_NOTA_WHISPER_FALLBACK > default True.

    Env values: "0", "false", "no" desligam. Qualquer outro ativa.
    """
    if cli_value is not None:
        return cli_value
    raw = os.environ.get("YT_NOTA_WHISPER_FALLBACK", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}

"""Testes de regressão pra extractor.py (v0.5.0).

Foca em:
- video_id_from_url: parse sem rede pros formatos comuns de URL
- _pick_subtitle: track original do áudio vence tradução de máquina (bug W33 vid 2)
- _whisper_lang_hint: hint correto pro Whisper (nunca idioma de tradução automática)
- extract_transcript: 429 + Whisper indisponível propaga RateLimitError
  (preserva parada precoce + pending.txt da v0.2.4)
"""

import pytest

from yt_nota import extractor
from yt_nota.extractor import (
    RateLimitError,
    _pick_subtitle,
    _whisper_lang_hint,
    video_id_from_url,
)


# ---------------------------------------------------------------------------
# video_id_from_url
# ---------------------------------------------------------------------------

class TestVideoIdFromUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?list=PLx&v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?t=42",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
        ],
    )
    def test_recognized_formats(self, url):
        assert video_id_from_url(url) == "dQw4w9WgXcQ"

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/playlist?list=PLxyz",
            "https://www.youtube.com/@canalteste",
            "not-a-url",
        ],
    )
    def test_unrecognized_returns_none(self, url):
        assert video_id_from_url(url) is None


# ---------------------------------------------------------------------------
# _pick_subtitle — track original vence tradução de máquina
# ---------------------------------------------------------------------------

def _entries(name):
    return [{"ext": "vtt", "url": f"https://fake/{name}.vtt"}]


class TestPickSubtitle:
    def test_manual_pt_still_wins(self):
        """Legenda manual continua vencendo tudo (tradução humana é confiável)."""
        pick = _pick_subtitle(
            {"pt-BR": _entries("manual-pt")},
            {"en-orig": _entries("auto-en")},
            original_lang="en",
        )
        assert pick == ("pt-BR", False, _entries("manual-pt"))

    def test_auto_original_lang_beats_translated_preferred(self):
        """Vídeo EN com auto-caption traduzida pt-BR: a track original EN vence.

        Regressão do bug W33 vid 2 — antes o pt-BR (tradução de máquina) era
        escolhido por estar primeiro em PREFERRED_LANGS.
        """
        pick = _pick_subtitle(
            {},
            {"pt-BR": _entries("translated"), "en-orig": _entries("original")},
            original_lang="en",
        )
        assert pick == ("en-orig", True, _entries("original"))

    def test_auto_original_plain_code_when_no_orig_track(self):
        pick = _pick_subtitle(
            {},
            {"pt-BR": _entries("translated"), "en": _entries("original")},
            original_lang="en",
        )
        assert pick == ("en", True, _entries("original"))

    def test_pt_video_unchanged_behavior(self):
        """Vídeo PT: comportamento idêntico ao anterior (pt-BR escolhido)."""
        pick = _pick_subtitle({}, {"pt-BR": _entries("auto-pt")}, original_lang="pt")
        assert pick == ("pt-BR", True, _entries("auto-pt"))

    def test_without_original_lang_falls_back_to_preferred_order(self):
        pick = _pick_subtitle(
            {},
            {"pt-BR": _entries("a"), "en-orig": _entries("b")},
            original_lang=None,
        )
        assert pick == ("pt-BR", True, _entries("a"))

    def test_no_subs_returns_none(self):
        assert _pick_subtitle({}, {}, original_lang="en") is None


# ---------------------------------------------------------------------------
# _whisper_lang_hint
# ---------------------------------------------------------------------------

class TestWhisperLangHint:
    def test_original_lang_wins_over_translated_pick(self):
        """Áudio EN + caption pt-BR (tradução): hint deve ser 'en', nunca 'pt'.

        Regressão do bug W33 vid 2: hint 'pt' forçava o Whisper a TRADUZIR
        áudio EN em vez de transcrever.
        """
        assert _whisper_lang_hint("en", "pt-BR", True) == "en"

    def test_translated_auto_caption_never_becomes_hint(self):
        """Sem idioma original conhecido, auto-caption traduzida não vira hint."""
        assert _whisper_lang_hint(None, "pt-BR", True) is None

    def test_orig_track_is_trusted(self):
        assert _whisper_lang_hint(None, "en-orig", True) == "en"

    def test_manual_caption_is_trusted(self):
        assert _whisper_lang_hint(None, "pt-BR", False) == "pt"

    def test_normalizes_region_suffix(self):
        assert _whisper_lang_hint("pt-BR", None, False) == "pt"

    def test_unknown_lang_returns_none(self):
        assert _whisper_lang_hint("xx", None, False) is None

    def test_nothing_known_returns_none(self):
        assert _whisper_lang_hint(None, None, False) is None


# ---------------------------------------------------------------------------
# extract_transcript — 429 com Whisper indisponível/falho propaga RateLimitError
# ---------------------------------------------------------------------------

def _video_with_auto_pt():
    return {
        "url": "https://youtube.com/watch?v=abc",
        "language": "en",
        "_raw_subs": {},
        "_raw_auto": {"pt": [{"ext": "vtt", "url": "https://fake/sub.vtt"}]},
    }


class TestRateLimitWhisperUnavailable:
    def test_whisper_returning_none_reraises_rate_limit(self, monkeypatch):
        """faster-whisper ausente (ou áudio falhou) → RateLimitError propaga.

        Sem isso, o batch inteiro degradava silenciosamente pra drafts SEM
        transcript em vez de parar e salvar o pending.txt (regressão v0.3.0).
        """
        def fake_fetch(entries):
            raise RateLimitError("429")

        monkeypatch.setattr(extractor, "_fetch_vtt", fake_fetch)
        monkeypatch.setattr(
            extractor, "_try_whisper_fallback", lambda video, **kw: None
        )

        with pytest.raises(RateLimitError):
            extractor.extract_transcript(_video_with_auto_pt(), whisper_fallback=True)

    def test_whisper_success_passes_original_lang_hint(self, monkeypatch):
        """O hint repassado ao Whisper é o idioma ORIGINAL do áudio."""
        def fake_fetch(entries):
            raise RateLimitError("429")

        captured = {}

        def fake_whisper(video, **kw):
            captured.update(kw)
            return {"language": "en", "is_auto": True, "origin": "whisper-local",
                    "segments": [object()]}

        monkeypatch.setattr(extractor, "_fetch_vtt", fake_fetch)
        monkeypatch.setattr(extractor, "_try_whisper_fallback", fake_whisper)

        result = extractor.extract_transcript(_video_with_auto_pt(), whisper_fallback=True)
        assert result is not None
        assert captured["lang_hint"] == "en"

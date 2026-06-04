"""Testes de regressão pro fallback Whisper.

Foca em:
- Resolução de modelo via env var + CLI override
- Resolução do enabled/disabled via env var + CLI override
- is_available() consistente com import faster_whisper
- transcribe_from_video() com WhisperModel mockado (não baixa modelo real)
- extract_transcript() invoca o fallback no RateLimitError quando flag ativa
- extract_transcript() respeita --no-whisper-fallback (propaga RateLimitError)
- Frontmatter do draft escreve transcript_origem: whisper-local corretamente
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from yt_nota import whisper_fallback
from yt_nota.transcript import Segment


# ---------------------------------------------------------------------------
# Resolução de env vars
# ---------------------------------------------------------------------------

class TestResolveModel:
    def test_cli_value_wins(self, monkeypatch):
        monkeypatch.setenv("YT_NOTA_WHISPER_MODEL", "medium")
        assert whisper_fallback.resolve_model_from_env("base") == "base"

    def test_env_when_no_cli(self, monkeypatch):
        monkeypatch.setenv("YT_NOTA_WHISPER_MODEL", "tiny")
        assert whisper_fallback.resolve_model_from_env(None) == "tiny"

    def test_default_when_no_env_no_cli(self, monkeypatch):
        monkeypatch.delenv("YT_NOTA_WHISPER_MODEL", raising=False)
        assert whisper_fallback.resolve_model_from_env(None) == "small"


class TestResolveEnabled:
    def test_cli_false_wins(self, monkeypatch):
        monkeypatch.setenv("YT_NOTA_WHISPER_FALLBACK", "1")
        assert whisper_fallback.resolve_enabled_from_env(False) is False

    def test_cli_true_wins(self, monkeypatch):
        monkeypatch.setenv("YT_NOTA_WHISPER_FALLBACK", "0")
        assert whisper_fallback.resolve_enabled_from_env(True) is True

    @pytest.mark.parametrize("raw", ["0", "false", "False", "FALSE", "no", "off"])
    def test_env_disables(self, monkeypatch, raw):
        monkeypatch.setenv("YT_NOTA_WHISPER_FALLBACK", raw)
        assert whisper_fallback.resolve_enabled_from_env(None) is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", ""])
    def test_env_enables(self, monkeypatch, raw):
        monkeypatch.setenv("YT_NOTA_WHISPER_FALLBACK", raw)
        assert whisper_fallback.resolve_enabled_from_env(None) is True

    def test_default_enabled_when_no_env(self, monkeypatch):
        monkeypatch.delenv("YT_NOTA_WHISPER_FALLBACK", raising=False)
        assert whisper_fallback.resolve_enabled_from_env(None) is True


# ---------------------------------------------------------------------------
# Segment formatting
# ---------------------------------------------------------------------------

class TestSecondsToLabel:
    @pytest.mark.parametrize("seconds,expected", [
        (0, "0:00"),
        (5, "0:05"),
        (65, "1:05"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (7325.5, "2:02:05"),  # float input
    ])
    def test_formatting(self, seconds, expected):
        assert whisper_fallback._seconds_to_label(seconds) == expected


# ---------------------------------------------------------------------------
# transcribe_from_video pipeline (com mocks)
# ---------------------------------------------------------------------------

class FakeWhisperSegment:
    """Stand-in pro segment do faster-whisper."""
    def __init__(self, start, text):
        self.start = start
        self.text = text
        self.end = start + 5.0


class FakeTranscribeInfo:
    def __init__(self, language="pt", duration=120.0):
        self.language = language
        self.duration = duration


def _fake_whisper_model(segments_data, *, language="pt"):
    """Cria um WhisperModel falso com transcribe() mockado."""
    fake = MagicMock()
    segs = [FakeWhisperSegment(start, text) for start, text in segments_data]
    fake.transcribe.return_value = (iter(segs), FakeTranscribeInfo(language=language))
    return fake


class TestTranscribePipeline:
    def test_transcribe_audio_happy_path(self, tmp_path, monkeypatch):
        # Mock o WhisperModel cacheado
        fake = _fake_whisper_model([(0.0, "Olá pessoal"), (5.5, "hoje vamos falar de Python")])
        monkeypatch.setattr(whisper_fallback, "_MODEL_CACHE", {"small": fake})

        # Cria audio fake só pra existir
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"x" * 50_000)

        result = whisper_fallback._transcribe_audio(audio, model_size="small", language="pt")
        assert result is not None
        assert result["language"] == "pt"
        assert result["is_auto"] is True
        assert result["origin"] == "whisper-local"
        assert len(result["segments"]) == 2
        assert result["segments"][0] == Segment(t="0:00", text="Olá pessoal")
        assert result["segments"][1] == Segment(t="0:05", text="hoje vamos falar de Python")

    def test_transcribe_audio_empty_segments_returns_none(self, tmp_path, monkeypatch):
        fake = _fake_whisper_model([])
        monkeypatch.setattr(whisper_fallback, "_MODEL_CACHE", {"small": fake})

        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"x" * 50_000)

        assert whisper_fallback._transcribe_audio(audio, model_size="small", language="pt") is None

    def test_transcribe_audio_handles_exception(self, tmp_path, monkeypatch):
        fake = MagicMock()
        fake.transcribe.side_effect = RuntimeError("ctranslate2 boom")
        monkeypatch.setattr(whisper_fallback, "_MODEL_CACHE", {"small": fake})

        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"x" * 50_000)

        assert whisper_fallback._transcribe_audio(audio, model_size="small", language="pt") is None

    def test_transcribe_from_video_skips_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(whisper_fallback, "is_available", lambda: False)
        result = whisper_fallback.transcribe_from_video("https://youtube.com/watch?v=abc")
        assert result is None

    def test_transcribe_from_video_skips_when_download_fails(self, monkeypatch):
        monkeypatch.setattr(whisper_fallback, "is_available", lambda: True)
        monkeypatch.setattr(whisper_fallback, "_download_audio", lambda url, td, **kw: None)
        result = whisper_fallback.transcribe_from_video("https://youtube.com/watch?v=abc")
        assert result is None

    def test_transcribe_from_video_falls_back_default_model_on_invalid(self, tmp_path, monkeypatch):
        """Modelo inválido cai pro default 'small'."""
        monkeypatch.setattr(whisper_fallback, "is_available", lambda: True)

        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"x" * 50_000)
        monkeypatch.setattr(whisper_fallback, "_download_audio", lambda url, td, **kw: audio)

        fake = _fake_whisper_model([(0.0, "test")])
        monkeypatch.setattr(whisper_fallback, "_MODEL_CACHE", {"small": fake})

        result = whisper_fallback.transcribe_from_video(
            "https://youtube.com/watch?v=abc", model_size="nonsense"
        )
        assert result is not None
        assert result["origin"] == "whisper-local"


# ---------------------------------------------------------------------------
# Integração com extract_transcript()
# ---------------------------------------------------------------------------

class TestExtractTranscriptFallback:
    def test_rate_limit_with_fallback_invokes_whisper(self, monkeypatch):
        from yt_nota import extractor

        # Mock _fetch_vtt pra levantar RateLimitError
        def fake_fetch(entries):
            raise extractor.RateLimitError("429")

        monkeypatch.setattr(extractor, "_fetch_vtt", fake_fetch)

        # Mock o _try_whisper_fallback pra retornar transcript fake
        fake_transcript = {
            "language": "pt",
            "is_auto": True,
            "origin": "whisper-local",
            "segments": [Segment(t="0:00", text="conteúdo")],
        }
        monkeypatch.setattr(
            extractor, "_try_whisper_fallback",
            lambda video, **kw: fake_transcript,
        )

        video = {
            "url": "https://youtube.com/watch?v=abc",
            "_raw_subs": {},
            "_raw_auto": {"pt": [{"ext": "vtt", "url": "https://fake/sub.vtt"}]},
        }
        result = extractor.extract_transcript(video, whisper_fallback=True, whisper_model="small")
        assert result == fake_transcript

    def test_rate_limit_without_fallback_raises(self, monkeypatch):
        from yt_nota import extractor

        def fake_fetch(entries):
            raise extractor.RateLimitError("429")

        monkeypatch.setattr(extractor, "_fetch_vtt", fake_fetch)

        video = {
            "url": "https://youtube.com/watch?v=abc",
            "_raw_subs": {},
            "_raw_auto": {"pt": [{"ext": "vtt", "url": "https://fake/sub.vtt"}]},
        }
        with pytest.raises(extractor.RateLimitError):
            extractor.extract_transcript(video, whisper_fallback=False)

    def test_no_subtitle_with_fallback_invokes_whisper(self, monkeypatch):
        """Vídeos sem caption: fallback ativo tenta Whisper mesmo assim."""
        from yt_nota import extractor

        fake_transcript = {
            "language": "pt",
            "is_auto": True,
            "origin": "whisper-local",
            "segments": [Segment(t="0:00", text="só áudio")],
        }
        monkeypatch.setattr(
            extractor, "_try_whisper_fallback",
            lambda video, **kw: fake_transcript,
        )

        video = {
            "url": "https://youtube.com/watch?v=abc",
            "_raw_subs": {},
            "_raw_auto": {},  # nenhuma caption
        }
        result = extractor.extract_transcript(video, whisper_fallback=True)
        assert result == fake_transcript

    def test_no_subtitle_no_fallback_returns_none(self, monkeypatch):
        from yt_nota import extractor

        video = {
            "url": "https://youtube.com/watch?v=abc",
            "_raw_subs": {},
            "_raw_auto": {},
        }
        assert extractor.extract_transcript(video, whisper_fallback=False) is None


# ---------------------------------------------------------------------------
# Integração com vault.write_draft()
# ---------------------------------------------------------------------------

class TestVaultWritesWhisperOrigin:
    def test_origin_whisper_local_in_frontmatter(self, tmp_path, monkeypatch):
        """write_draft escreve transcript_origem: whisper-local quando dict tem origin."""
        from yt_nota import vault

        literatura = tmp_path / "30-Recursos" / "Literatura"
        drafts = literatura / "_drafts"
        drafts.mkdir(parents=True)
        monkeypatch.setattr(vault, "DRAFTS_DIR", drafts)
        monkeypatch.setattr(vault, "LITERATURA_DIR", literatura)
        monkeypatch.setattr(vault, "NOTAS_DIR", tmp_path / "30-Recursos" / "Notas")
        monkeypatch.setattr(vault, "VAULT_PATH", tmp_path)

        video = {
            "url": "https://youtube.com/watch?v=abc",
            "video_id": "abc",
            "title": "Aula teste",
            "channel": "Canal Teste",
            "channel_url": "",
            "upload_date_iso": "2026-05-31",
            "duration_human": "10m",
            "description": "",
            "tags": [],
            "thumbnail": "",
        }
        segments = [Segment(t="0:00", text="hello")]
        transcript_info = {
            "language": "pt",
            "is_auto": True,
            "origin": "whisper-local",
            "segments": segments,
        }
        draft_path = vault.write_draft(video, segments, transcript_info)
        text = draft_path.read_text(encoding="utf-8")
        assert "transcript_origem: whisper-local" in text
        assert "idioma_transcript: pt" in text

    def test_origin_auto_backwards_compat(self, tmp_path, monkeypatch):
        """Sem campo `origin`, cai no is_auto bool (retrocompat com extractor v0.2.x)."""
        from yt_nota import vault

        literatura = tmp_path / "30-Recursos" / "Literatura"
        drafts = literatura / "_drafts"
        drafts.mkdir(parents=True)
        monkeypatch.setattr(vault, "DRAFTS_DIR", drafts)
        monkeypatch.setattr(vault, "LITERATURA_DIR", literatura)
        monkeypatch.setattr(vault, "NOTAS_DIR", tmp_path / "30-Recursos" / "Notas")
        monkeypatch.setattr(vault, "VAULT_PATH", tmp_path)

        video = {
            "url": "https://youtube.com/watch?v=abc",
            "video_id": "abc",
            "title": "Aula teste",
            "channel": "Canal Teste",
            "channel_url": "",
            "upload_date_iso": "2026-05-31",
            "duration_human": "10m",
            "description": "",
            "tags": [],
            "thumbnail": "",
        }
        segments = [Segment(t="0:00", text="hello")]
        transcript_info = {"language": "pt", "is_auto": True, "segments": segments}  # sem origin
        draft_path = vault.write_draft(video, segments, transcript_info)
        text = draft_path.read_text(encoding="utf-8")
        assert "transcript_origem: auto" in text

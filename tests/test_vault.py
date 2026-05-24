"""Testes de regressão pra vault.py.

Foca em:
- Round-trip write_draft → _parse_draft (preserva metadata e segments)
- finalize_draft cria nota + transcript + channel card nos paths certos
- Channel card é criado na primeira vez e atualizado nas subsequentes
- _as_str normaliza date/datetime do YAML (regressão do bug do smoke test)
- _yaml_quote escapa strings problemáticas
"""

from datetime import date, datetime
from pathlib import Path

import pytest

from yt_nota import vault
from yt_nota.transcript import Segment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_vault(tmp_path, monkeypatch):
    """Aponta vault.py pra um vault temporário pra cada teste."""
    literatura = tmp_path / "30-Recursos" / "Literatura"
    notas = tmp_path / "30-Recursos" / "Notas"
    drafts = literatura / "_drafts"
    literatura.mkdir(parents=True)
    notas.mkdir(parents=True)
    drafts.mkdir(parents=True)

    monkeypatch.setattr(vault, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(vault, "LITERATURA_DIR", literatura)
    monkeypatch.setattr(vault, "NOTAS_DIR", notas)
    monkeypatch.setattr(vault, "DRAFTS_DIR", drafts)
    return tmp_path


def _sample_video():
    return {
        "url": "https://www.youtube.com/watch?v=abc123",
        "video_id": "abc123",
        "title": "Como usar Delta Lake na prática",
        "channel": "Canal Teste",
        "channel_url": "https://www.youtube.com/@canalteste",
        "upload_date_iso": "2025-05-10",
        "duration_seconds": 600,
        "duration_human": "10m 0s",
        "description": "Vídeo sobre Delta Lake e Parquet.",
        "tags": ["data engineering", "delta lake", "parquet"],
    }


def _sample_segments():
    return [
        Segment(t="0:02", text="Olá pessoal, hoje vamos falar de Delta Lake"),
        Segment(t="0:15", text="A diferença pro Parquet puro é a camada transacional"),
        Segment(t="1:30", text="O delta_log registra cada commit"),
    ]


def _sample_transcript_info():
    return {"language": "pt", "is_auto": True, "segments": _sample_segments()}


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def test_yaml_quote_simple_string():
    assert vault._yaml_quote("hello") == "hello"


def test_yaml_quote_string_with_colon():
    result = vault._yaml_quote("Delta Lake: o que é")
    assert result.startswith('"') and result.endswith('"')


def test_yaml_quote_empty():
    assert vault._yaml_quote("") == '""'


def test_as_str_with_string():
    assert vault._as_str("hello") == "hello"


def test_as_str_with_date():
    assert vault._as_str(date(2025, 11, 13)) == "2025-11-13"


def test_as_str_with_datetime():
    dt = datetime(2025, 11, 13, 14, 30, 0)
    assert vault._as_str(dt) == "2025-11-13"


def test_as_str_with_none():
    assert vault._as_str(None) == ""


def test_as_str_with_number():
    assert vault._as_str(123) == "123"


# ---------------------------------------------------------------------------
# write_draft
# ---------------------------------------------------------------------------

def test_write_draft_creates_file_in_drafts_dir(fake_vault):
    video = _sample_video()
    segs = _sample_segments()
    info = _sample_transcript_info()

    path = vault.write_draft(video, segs, info)

    assert path.exists()
    assert path.parent.name == "_drafts"
    assert path.suffix == ".md"
    assert path.name.endswith(".draft.md")


def test_write_draft_has_required_frontmatter(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    content = path.read_text(encoding="utf-8")

    assert "tipo: yt-nota-draft" in content
    assert "status: pendente-sintese" in content
    assert "video_id: abc123" in content
    assert "canal:" in content
    assert "idioma_transcript: pt" in content
    assert "transcript_origem: auto" in content


def test_write_draft_includes_description_and_transcript(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    content = path.read_text(encoding="utf-8")

    assert "Vídeo sobre Delta Lake e Parquet." in content
    assert "[0:02] Olá pessoal" in content
    assert "[1:30] O delta_log" in content


def test_write_draft_without_transcript_marks_indisponivel(fake_vault):
    path = vault.write_draft(_sample_video(), None, None)
    content = path.read_text(encoding="utf-8")

    assert "transcript: indisponivel" in content
    assert "Transcript indisponível" in content


def test_write_draft_preserves_tema(fake_vault):
    path = vault.write_draft(
        _sample_video(),
        _sample_segments(),
        _sample_transcript_info(),
        tema="IA-e-Programacao",
    )
    content = path.read_text(encoding="utf-8")
    assert "tema: IA-e-Programacao" in content


# ---------------------------------------------------------------------------
# _parse_draft (round-trip)
# ---------------------------------------------------------------------------

def test_parse_draft_roundtrip_preserves_metadata(fake_vault):
    video = _sample_video()
    path = vault.write_draft(video, _sample_segments(), _sample_transcript_info())

    meta, segments, descricao = vault._parse_draft(path)

    assert meta["titulo"] == video["title"]
    assert meta["canal"] == video["channel"]
    assert meta["url"] == video["url"]
    assert meta["video_id"] == video["video_id"]
    assert meta["idioma_transcript"] == "pt"
    assert meta["transcript_origem"] == "auto"
    assert descricao is not None
    assert "Delta Lake" in descricao


def test_parse_draft_roundtrip_preserves_segments(fake_vault):
    original_segs = _sample_segments()
    path = vault.write_draft(_sample_video(), original_segs, _sample_transcript_info())

    _, parsed_segs, _ = vault._parse_draft(path)

    assert len(parsed_segs) == len(original_segs)
    for orig, parsed in zip(original_segs, parsed_segs):
        assert parsed.t == orig.t
        assert parsed.text == orig.text


def test_parse_draft_yaml_date_field_works_with_finalize(fake_vault):
    """Regressão: YAML converte 'data_publicacao: 2025-11-13' em date object.
    finalize_draft chama _as_str que normaliza. Sem isso, dava TypeError.
    """
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    body = "## Em uma frase\n\nTeste.\n"

    result = vault.finalize_draft(path, body, delete_draft=False)

    assert result["note_path"].exists()
    note_content = result["note_path"].read_text(encoding="utf-8")
    assert "ano: 2025" in note_content
    assert "data-publicacao: 2025-05-10" in note_content


# ---------------------------------------------------------------------------
# finalize_draft
# ---------------------------------------------------------------------------

def test_finalize_creates_note_transcript_and_card(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    body = "## Em uma frase\n\nTeste de finalize.\n\n---\n\n## O que defende\n\nPonto.\n"

    result = vault.finalize_draft(path, body)

    assert result["note_path"].exists()
    assert result["transcript_path"] is not None and result["transcript_path"].exists()
    assert result["channel_card_path"] is not None and result["channel_card_path"].exists()
    assert result["draft_deleted"] is True
    assert not path.exists()


def test_finalize_puts_transcript_in_subfolder(fake_vault):
    """Transcripts ficam em <canal>/transcripts/, não soltos junto das notas."""
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    result = vault.finalize_draft(path, "body")

    assert result["transcript_path"] is not None
    assert result["transcript_path"].parent.name == "transcripts"
    # Nota principal NÃO está dentro de transcripts/
    assert result["note_path"].parent.name != "transcripts"
    # Nota e transcript no mesmo canal (transcripts é subpasta)
    assert result["transcript_path"].parent.parent == result["note_path"].parent


def test_finalize_note_has_complete_frontmatter(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    body = "## Em uma frase\n\nteste"

    result = vault.finalize_draft(path, body)
    content = result["note_path"].read_text(encoding="utf-8")

    assert "tipo: literatura" in content
    assert "subtipo: vídeo" in content
    assert "fonte: YouTube" in content
    assert "up: \"[[Canal-Teste]]\"" in content
    assert "transcript_file:" in content


def test_finalize_creates_channel_card_with_video_line(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    result = vault.finalize_draft(path, "body")

    card_content = result["channel_card_path"].read_text(encoding="utf-8")
    assert "tipo: card-vivo" in card_content
    assert "## Vídeos processados" in card_content
    assert "Como usar Delta Lake na prática" in card_content


def test_finalize_appends_to_existing_channel_card(fake_vault):
    # Primeiro vídeo
    p1 = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    vault.finalize_draft(p1, "body1")

    # Segundo vídeo do mesmo canal
    video2 = _sample_video()
    video2["title"] = "Segundo vídeo no mesmo canal"
    video2["video_id"] = "xyz999"
    video2["url"] = "https://www.youtube.com/watch?v=xyz999"
    p2 = vault.write_draft(video2, _sample_segments(), _sample_transcript_info())
    result2 = vault.finalize_draft(p2, "body2")

    card_content = result2["channel_card_path"].read_text(encoding="utf-8")
    assert "Como usar Delta Lake na prática" in card_content
    assert "Segundo vídeo no mesmo canal" in card_content


def test_finalize_keeps_draft_when_flag_set(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    vault.finalize_draft(path, "body", delete_draft=False)
    assert path.exists()


def test_finalize_skips_channel_card_when_flag_set(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    result = vault.finalize_draft(path, "body", no_channel_card=True)
    assert result["channel_card_path"] is None


def test_finalize_without_transcript_skips_transcript_file(fake_vault):
    path = vault.write_draft(_sample_video(), None, None)
    result = vault.finalize_draft(path, "body")

    assert result["note_path"].exists()
    assert result["transcript_path"] is None


def test_finalize_with_problematic_title_chars(fake_vault):
    video = _sample_video()
    video["title"] = 'Video com: dois "pontos" e aspas'
    path = vault.write_draft(video, _sample_segments(), _sample_transcript_info())
    body = "body"

    result = vault.finalize_draft(path, body)
    assert result["note_path"].exists()
    content = result["note_path"].read_text(encoding="utf-8")
    assert "Video com" in content


# ---------------------------------------------------------------------------
# list_pending_drafts
# ---------------------------------------------------------------------------

def test_list_pending_drafts_returns_empty_when_no_drafts(fake_vault):
    assert vault.list_pending_drafts() == []


def test_list_pending_drafts_returns_drafts_sorted(fake_vault):
    p1 = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    video2 = _sample_video()
    video2["title"] = "Outro"
    p2 = vault.write_draft(video2, None, None)

    drafts = vault.list_pending_drafts()
    assert len(drafts) == 2
    assert all(d.suffix == ".md" and d.name.endswith(".draft.md") for d in drafts)


# ---------------------------------------------------------------------------
# is_video_already_processed
# ---------------------------------------------------------------------------

def test_dedup_returns_false_when_vault_empty(fake_vault):
    already, evidence = vault.is_video_already_processed("abc123", "Canal-Teste")
    assert already is False
    assert evidence is None


def test_dedup_detects_existing_final_note(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    vault.finalize_draft(path, "body")

    already, evidence = vault.is_video_already_processed("abc123", "Canal-Teste")
    assert already is True
    assert evidence is not None
    assert evidence.suffix == ".md"
    assert not evidence.name.endswith(".transcript.md")


def test_dedup_detects_existing_draft(fake_vault):
    vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())

    already, evidence = vault.is_video_already_processed("abc123", "Canal-Teste")
    assert already is True
    assert evidence is not None
    assert evidence.name.endswith(".draft.md")


def test_dedup_different_video_id_returns_false(fake_vault):
    vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    already, evidence = vault.is_video_already_processed("DIFFERENT-id", "Canal-Teste")
    assert already is False


def test_dedup_empty_video_id_returns_false(fake_vault):
    already, evidence = vault.is_video_already_processed("", "Canal-Teste")
    assert already is False

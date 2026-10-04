"""Finalize output against the vault rules: title, source MOC, dedup scope."""

from yt_nota import vault

from .test_vault import (  # noqa: F401  (fake_vault is a fixture)
    _sample_segments,
    _sample_transcript_info,
    _sample_video,
    fake_vault,
)

EM_DASH = "—"


def _second_video():
    video = _sample_video()
    video["title"] = "Segundo vídeo no mesmo canal"
    video["video_id"] = "xyz999"
    video["url"] = "https://www.youtube.com/watch?v=xyz999"
    return video


def test_finalize_title_has_no_id_or_em_dash(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    result = vault.finalize_draft(path, "body")

    assert "\n# Como usar Delta Lake na prática\n" in result["note_path"].read_text(encoding="utf-8")
    for key in ("note_path", "channel_card_path", "transcript_path", "moc_path"):
        assert EM_DASH not in result[key].read_text(encoding="utf-8"), key


def test_draft_header_has_no_em_dash(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    assert EM_DASH not in path.read_text(encoding="utf-8")


def test_finalize_creates_source_moc_with_first_note(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    result = vault.finalize_draft(path, "body")

    moc = result["note_path"].parent / "MOC-Canal-Teste.md"
    assert result["moc_path"] == moc
    content = moc.read_text(encoding="utf-8")
    assert "tipo: moc" in content
    assert 'moc: "[[MOC-Tech]]"' in content
    assert f"[[{result['note_path'].stem}|Como usar Delta Lake na prática]]" in content


def test_finalize_appends_to_existing_source_moc(fake_vault):
    p1 = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    r1 = vault.finalize_draft(p1, "body1")
    p2 = vault.write_draft(_second_video(), _sample_segments(), _sample_transcript_info())
    r2 = vault.finalize_draft(p2, "body2")

    content = r2["moc_path"].read_text(encoding="utf-8")
    assert r1["note_path"].stem in content and r2["note_path"].stem in content
    assert content.count("## Literatura") == 1


def test_finalize_reuses_existing_moc_with_other_name(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    canal_dir = vault.RECURSOS_DIR / "Tech" / "Canal-Teste"
    canal_dir.mkdir(parents=True)
    hub = canal_dir / "MOC-Hub-Antigo.md"
    hub.write_text("---\ntipo: moc\nupdated: 2026-01-01\n---\n\n# Hub\n", encoding="utf-8")

    result = vault.finalize_draft(path, "body")

    assert result["moc_path"] == hub
    assert not (canal_dir / "MOC-Canal-Teste.md").exists()
    assert 'moc: "[[MOC-Hub-Antigo]]"' in result["note_path"].read_text(encoding="utf-8")
    assert result["note_path"].stem in hub.read_text(encoding="utf-8")


def test_dataview_moc_gets_no_manual_link(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    canal_dir = vault.RECURSOS_DIR / "Tech" / "Canal-Teste"
    canal_dir.mkdir(parents=True)
    hub = canal_dir / "MOC-Canal-Teste.md"
    original = "---\ntipo: moc\n---\n\n```dataview\nLIST\n```\n"
    hub.write_text(original, encoding="utf-8")

    vault.finalize_draft(path, "body")

    assert hub.read_text(encoding="utf-8") == original


def test_video_cited_in_another_draft_description_is_not_deduped(fake_vault):
    """A channel that links its own videos in descriptions must not lose them."""
    video = _sample_video()
    video["description"] = "Veja também https://www.youtube.com/watch?v=OUTRO_VIDEO1"
    vault.write_draft(video, _sample_segments(), _sample_transcript_info())
    vault.reset_dedup_index()

    assert vault.find_video_anywhere("OUTRO_VIDEO1") is None
    assert vault.find_video_anywhere("abc123") is not None


def test_finalize_returns_quality_report(fake_vault):
    path = vault.write_draft(_sample_video(), _sample_segments(), _sample_transcript_info())
    result = vault.finalize_draft(path, "body")

    assert result["quality"]["quotes"]["total"] == 0
    assert result["quality"]["style"] is None

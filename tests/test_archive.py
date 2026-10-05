"""archive_draft: a draft leaves `_processar/` only as a verified copy."""

from pathlib import Path

import pytest

from yt_nota import archive
from yt_nota.archive import ArchiveError, archive_draft


def _draft(tmp_path: Path, text: str = "conteúdo do draft\n", name: str = "x.draft.md") -> Path:
    draft = tmp_path / name
    draft.write_text(text, encoding="utf-8")
    return draft


def test_copy_is_byte_identical_and_original_is_removed(tmp_path, isolated_archive):
    draft = _draft(tmp_path)
    original = draft.read_bytes()

    target = archive_draft(draft, "PROPAGA")

    assert target == isolated_archive / "PROPAGA" / "x.draft.md"
    assert target.read_bytes() == original
    assert not draft.exists()


def test_one_folder_per_verdict(tmp_path, isolated_archive):
    a = archive_draft(_draft(tmp_path, name="a.draft.md"), "DESCARTE")
    b = archive_draft(_draft(tmp_path, name="b.draft.md"), "FICHAMENTO")
    assert a.parent.name == "DESCARTE"
    assert b.parent.name == "FICHAMENTO"


def test_existing_different_copy_is_never_overwritten(tmp_path, isolated_archive):
    first = archive_draft(_draft(tmp_path, "versão um\n"), "DESCARTE")
    second = archive_draft(_draft(tmp_path, "versão dois\n"), "DESCARTE")

    assert first.read_text(encoding="utf-8") == "versão um\n"
    assert second.name == "x.draft-2.md"
    assert second.read_text(encoding="utf-8") == "versão dois\n"


def test_identical_existing_copy_is_reused(tmp_path, isolated_archive):
    first = archive_draft(_draft(tmp_path), "DESCARTE")
    second = archive_draft(_draft(tmp_path), "DESCARTE")
    assert second == first
    assert len(list(first.parent.iterdir())) == 1


def test_missing_draft_raises(tmp_path, isolated_archive):
    with pytest.raises(ArchiveError):
        archive_draft(tmp_path / "nao-existe.draft.md", "DESCARTE")


def test_failed_verification_keeps_the_draft(tmp_path, isolated_archive, monkeypatch):
    draft = _draft(tmp_path)
    real = archive._sha256
    calls = {"n": 0}

    def flaky(path: Path) -> str:
        calls["n"] += 1
        # First call hashes the source; the verification of the copy disagrees.
        return real(path) if calls["n"] == 1 else "0" * 64

    monkeypatch.setattr(archive, "_sha256", flaky)
    with pytest.raises(ArchiveError):
        archive_draft(draft, "PROPAGA")
    assert draft.exists()


def test_unwritable_archive_keeps_the_draft(tmp_path, monkeypatch):
    from yt_nota import config

    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is expected", encoding="utf-8")
    monkeypatch.setattr(config, "ARCHIVE_DIR", blocker)
    draft = _draft(tmp_path)
    with pytest.raises(ArchiveError):
        archive_draft(draft, "PROPAGA")
    assert draft.exists()

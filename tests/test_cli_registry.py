"""CLI ↔ registro durável: extração, finalize, triage, discard e adopt-drafts."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from yt_nota import cli
from yt_nota.registry import open_registry

URL = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
VID = "AAAAAAAAAAA"


def _args(**overrides) -> argparse.Namespace:
    base = dict(
        urls=[URL],
        playlist=None,
        file=None,
        retry_pending=None,
        stdin=False,
        tema=None,
        dominio=None,
        sleep=0,
        with_cookies=False,
        dry_run=False,
        force=False,
        db=None,
        reason=None,
        verdict=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _draft(tmp_path: Path, video_id: str = VID, name: str = "x.draft.md") -> Path:
    draft = tmp_path / name
    draft.write_text(
        "---\n"
        "tipo: yt-nota-draft\n"
        f"video_id: {video_id}\n"
        'titulo: "Um título: com dois-pontos"\n'
        "canal: Some Channel\n"
        "duracao: 12m 3s\n"
        "data_publicacao: 2026-09-01\n"
        "transcript_origem: auto\n"
        "---\n\n# DRAFT\n\n## Transcript\n\n[00:00] oi\n",
        encoding="utf-8",
    )
    return draft


def _get(db: Path, video_id: str = VID):
    with open_registry(db) as reg:
        return reg.get(video_id)


def test_record_downloaded_creates_row_with_metadata(isolated_registry):
    cli._registry_record(
        _args(),
        VID,
        "Some-Channel",
        title="T",
        published_at="2026-09-01",
        duration_s=600,
        status=cli.STATUS_BAIXADO,
        transcript_source="auto",
    )
    row = _get(isolated_registry)
    assert row.status == "baixado"
    assert (row.title, row.published_at, row.duration_s) == ("T", "2026-09-01", 600)
    assert row.transcript_source == "auto"


def test_record_downloaded_never_demotes_curated(isolated_registry):
    args = _args()
    cli._registry_record(args, VID, "C", status=cli.STATUS_CURADO, verdict="FICHAMENTO")
    cli._registry_record(args, VID, "C", status=cli.STATUS_BAIXADO)
    assert _get(isolated_registry).status == "curado"


def test_record_swallows_sqlite_errors(monkeypatch):
    def boom(_path):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(cli, "open_registry", boom)
    cli._registry_record(_args(), VID, "C", status=cli.STATUS_BAIXADO)  # must not raise
    assert cli._registry_done_ids(_args()) == set()


def test_extract_skips_video_the_registry_already_curated(tmp_path, monkeypatch):
    """A discarded video leaves no note in the vault; only the registry can dedupe it."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "find_video_anywhere", lambda vid: None)
    monkeypatch.setattr(cli, "list_pending_drafts", lambda: [])
    cli._registry_record(
        _args(), VID, "C", status=cli.STATUS_CURADO, verdict="DESCARTE", reason="off-topic"
    )
    calls = []
    monkeypatch.setattr(cli, "_process_single", lambda *a: calls.append(a) or "ok")

    assert cli._cmd_extract(_args()) == 0
    assert calls == []


def test_extract_still_downloads_discovered_only_video(tmp_path, monkeypatch):
    """`descoberto` means seen in the RSS, not downloaded: it must not be skipped."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "find_video_anywhere", lambda vid: None)
    monkeypatch.setattr(cli, "list_pending_drafts", lambda: [])
    cli._registry_record(_args(), VID, "C")
    calls = []
    monkeypatch.setattr(cli, "_process_single", lambda *a: calls.append(a) or "ok")

    assert cli._cmd_extract(_args()) == 0
    assert len(calls) == 1


def test_force_bypasses_registry_dedup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "find_video_anywhere", lambda vid: None)
    monkeypatch.setattr(cli, "list_pending_drafts", lambda: [])
    cli._registry_record(_args(), VID, "C", status=cli.STATUS_BAIXADO)
    calls = []
    monkeypatch.setattr(cli, "_process_single", lambda *a: calls.append(a) or "ok")

    assert cli._cmd_extract(_args(force=True)) == 0
    assert len(calls) == 1


def test_finalize_marks_curated_and_archives_draft(
    tmp_path, monkeypatch, isolated_registry, isolated_archive
):
    draft = _draft(tmp_path)
    body = tmp_path / "body.md"
    body.write_text(
        "\n\n".join(f"{s}\n\nconteúdo" for s in cli.EXPECTED_BODY_SECTIONS), encoding="utf-8"
    )
    note = tmp_path / "nota.md"
    monkeypatch.setattr(
        cli,
        "finalize_draft",
        lambda *a, **kw: {
            "note_path": note,
            "transcript_path": None,
            "channel_card_path": None,
            "moc_path": None,
            "draft_deleted": True,
            "video_id": VID,
            "canal_slug": "Some-Channel",
            "titulo": "T",
        },
    )
    args = argparse.Namespace(
        finalize=str(draft),
        body_file=str(body),
        no_channel_card=False,
        keep_draft=False,
        dominio=None,
        skip_body_check=False,
        verdict="FICHAMENTO",
        reason="fonte primária longa",
        db=None,
    )
    original = draft.read_bytes()
    assert cli._cmd_finalize(args) == 0
    assert not draft.exists()
    assert (isolated_archive / "FICHAMENTO" / draft.name).read_bytes() == original
    row = _get(isolated_registry)
    assert (row.status, row.verdict, row.channel_name) == ("curado", "FICHAMENTO", "Some-Channel")
    assert row.vault_paths == (str(note),)


def test_discard_requires_reason_and_keeps_draft(tmp_path):
    draft = _draft(tmp_path)
    assert cli._cmd_discard(_args(discard=[str(draft)])) == 1
    assert draft.exists()


def test_discard_marks_descarte_and_archives_draft(tmp_path, isolated_registry, isolated_archive):
    draft = _draft(tmp_path)
    original = draft.read_bytes()
    rc = cli._cmd_discard(_args(discard=[str(draft)], reason="fora do escopo"))
    assert rc == 0
    assert not draft.exists()
    assert (isolated_archive / "DESCARTE" / draft.name).read_bytes() == original
    row = _get(isolated_registry)
    assert (row.status, row.verdict, row.verdict_reason) == ("curado", "DESCARTE", "fora do escopo")
    assert row.title == "Um título: com dois-pontos"


def test_discard_keeps_draft_when_registry_fails(tmp_path, monkeypatch):
    draft = _draft(tmp_path)

    def boom(_path):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(cli, "open_registry", boom)
    assert cli._cmd_discard(_args(discard=[str(draft)], reason="x")) == 1
    assert draft.exists()


def test_discard_keeps_draft_without_video_id(tmp_path):
    draft = tmp_path / "broken.draft.md"
    draft.write_text("---\ntipo: yt-nota-draft\n---\n", encoding="utf-8")
    assert cli._cmd_discard(_args(discard=[str(draft)], reason="x")) == 1
    assert draft.exists()


def test_triage_prints_one_tsv_row_per_draft(tmp_path, monkeypatch, capsys):
    draft = _draft(tmp_path)
    monkeypatch.setattr(cli, "list_pending_drafts", lambda: [draft])
    assert cli._cmd_triage(_args()) == 0
    header, row = capsys.readouterr().out.strip().splitlines()
    assert header.split("\t")[0] == "video_id"
    assert row.split("\t") == [
        VID,
        "Some Channel",
        "12m 3s",
        "2026-09-01",
        "Um título: com dois-pontos",
        "x.draft.md",
    ]


def test_adopt_drafts_registers_unknown_drafts_as_downloaded(
    tmp_path, monkeypatch, isolated_registry
):
    known = _draft(tmp_path, video_id="BBBBBBBBBBB", name="known.draft.md")
    new = _draft(tmp_path)
    cli._registry_record(_args(), "BBBBBBBBBBB", "C", status=cli.STATUS_BAIXADO)
    monkeypatch.setattr(cli, "list_pending_drafts", lambda: [known, new])
    monkeypatch.setattr(cli.config, "VAULT_CONFIGURED", True)

    args = _args(registry="adopt-drafts")
    assert cli._cmd_registry(args) == 0
    row = _get(isolated_registry)
    assert (row.status, row.channel_name, row.transcript_source) == (
        "baixado",
        "Some-Channel",
        "auto",
    )
    assert row.published_at == "2026-09-01"


def test_adopt_drafts_dry_run_writes_nothing(tmp_path, monkeypatch, isolated_registry):
    monkeypatch.setattr(cli, "list_pending_drafts", lambda: [_draft(tmp_path)])
    monkeypatch.setattr(cli.config, "VAULT_CONFIGURED", True)
    assert cli._cmd_registry(_args(registry="adopt-drafts", dry_run=True)) == 0
    assert _get(isolated_registry) is None

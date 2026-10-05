"""Curation gate at the CLI: mandatory verdict, `--close`, archive after registry."""

import argparse
import sqlite3
from pathlib import Path

import pytest

from yt_nota import cli, vault
from yt_nota.registry import open_registry

VID = "dQw4w9WgXcQ"
BODY = "\n\n".join(f"{s}\n\nconteúdo" for s in cli.EXPECTED_BODY_SECTIONS)


@pytest.fixture
def fake_vault(tmp_path, monkeypatch):
    recursos = tmp_path / "30-Recursos"
    drafts = recursos / "Pipeline" / "_processar"
    cards = recursos / "Pessoas"
    for d in (drafts, cards):
        d.mkdir(parents=True)
    for mod in (vault, cli):
        monkeypatch.setattr(mod, "VAULT_PATH", tmp_path)
        monkeypatch.setattr(mod, "PROCESSAR_DIR", drafts)
    monkeypatch.setattr(vault, "RECURSOS_DIR", recursos)
    monkeypatch.setattr(vault, "CARDS_DE_PESSOA_DIR", cards)
    vault.reset_dedup_index()
    return tmp_path


def _draft(root: Path, *, video_id: str | None = VID, name: str = "20260901120000-um-video.draft.md") -> Path:
    draft = root / "30-Recursos" / "Pipeline" / "_processar" / name
    lines = ["---", "tipo: yt-nota-draft", "draft_id: 20260901120000"]
    if video_id:
        lines.append(f"video_id: {video_id}")
        lines.append(f"url: https://www.youtube.com/watch?v={video_id}")
    lines += [
        'titulo: "Um vídeo de teste"',
        "canal: Some Channel",
        "data_publicacao: 2026-09-01",
        "idioma_transcript: pt",
        "transcript_origem: auto",
        "dominio: Tech",
        "---",
        "",
        "# DRAFT: Um vídeo de teste",
        "",
        "## Transcript",
        "",
        "[00:00] primeira fala",
        "[00:12] segunda fala",
        "",
    ]
    draft.write_text("\n".join(lines), encoding="utf-8")
    return draft


def _args(**overrides) -> argparse.Namespace:
    base = dict(
        close=None, finalize=None, discard=None, body_file=None, paths=None,
        verdict=None, reason=None, dominio=None, db=None, keep_draft=False,
        no_channel_card=False, skip_body_check=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _row(db: Path):
    with open_registry(db) as reg:
        return reg.get(VID)


PAGES = ["Obsidian-Principal/30-Recursos/Tech/Conceito-A.md", "Conceito-B"]


# --- AC-13: a verdict is mandatory ------------------------------------------

@pytest.mark.parametrize("verdict,reason", [
    (None, "motivo"), ("FICHAMENTO", None), ("PROPAGA", "motivo"), ("DESCARTE", "motivo"),
])
def test_finalize_without_fichamento_verdict_and_reason_keeps_draft(
    fake_vault, tmp_path, isolated_registry, verdict, reason
):
    draft = _draft(fake_vault)
    body = tmp_path / "body.md"
    body.write_text(BODY, encoding="utf-8")
    rc = cli._cmd_finalize(_args(finalize=str(draft), body_file=str(body),
                                 verdict=verdict, reason=reason))
    assert rc == 1
    assert draft.exists()
    assert _row(isolated_registry) is None
    assert not list((fake_vault / "30-Recursos" / "Tech").glob("**/3-*.md"))


@pytest.mark.parametrize("verdict,reason,paths", [
    (None, "motivo", PAGES), ("PROPAGA", None, PAGES), ("PROPAGA", "motivo", None),
    ("FICHAMENTO", "motivo", PAGES), ("DESCARTE", "motivo", PAGES),
])
def test_close_without_verdict_reason_or_paths_keeps_draft(
    fake_vault, isolated_registry, verdict, reason, paths
):
    draft = _draft(fake_vault)
    rc = cli._cmd_close(_args(close=str(draft), verdict=verdict, reason=reason, paths=paths))
    assert rc == 1
    assert draft.exists()
    assert _row(isolated_registry) is None


def test_close_keeps_draft_without_video_id(fake_vault, isolated_registry):
    draft = _draft(fake_vault, video_id=None)
    rc = cli._cmd_close(_args(close=str(draft), verdict="PROPAGA", reason="x", paths=PAGES))
    assert rc == 1
    assert draft.exists()


# --- AC-14 / AC-16: close writes evidence, records, then archives ------------

def test_close_propaga_writes_transcript_card_registry_and_archives(
    fake_vault, isolated_registry, isolated_archive
):
    draft = _draft(fake_vault)
    original = draft.read_bytes()

    rc = cli._cmd_close(_args(close=str(draft), verdict="PROPAGA",
                              reason="refina Conceito-A e Conceito-B", paths=PAGES))

    assert rc == 0
    canal_dir = fake_vault / "30-Recursos" / "Tech" / "Some-Channel"
    # No fichamento is created for a PROPAGA.
    assert not list(canal_dir.glob("3-*.md"))
    transcript = canal_dir / "transcripts" / "3-20260901120000-um-video-de-teste.transcript.md"
    text = transcript.read_text(encoding="utf-8")
    assert "veredicto: PROPAGA" in text
    assert '  - "[[Conceito-A]]"' in text and '  - "[[Conceito-B]]"' in text
    assert "parent:" not in text
    assert "primeira fala" in text

    card = (fake_vault / "30-Recursos" / "Pessoas" / "Some-Channel.md").read_text(encoding="utf-8")
    assert ("- Um vídeo de teste (2026-09-01) · PROPAGA → [[Conceito-A]], [[Conceito-B]] · "
            f"[vídeo](https://www.youtube.com/watch?v={VID})") in card

    row = _row(isolated_registry)
    assert (row.status, row.verdict, row.verdict_reason) == (
        "curado", "PROPAGA", "refina Conceito-A e Conceito-B")
    assert row.vault_paths[:2] == tuple(PAGES)
    assert row.vault_paths[2].endswith(".transcript.md")

    assert not draft.exists()
    assert (isolated_archive / "PROPAGA" / draft.name).read_bytes() == original


def test_close_atomica_uses_its_own_archive_folder(fake_vault, isolated_registry, isolated_archive):
    draft = _draft(fake_vault)
    rc = cli._cmd_close(_args(close=draft.name, verdict="ATOMICA",
                              reason="conceito novo", paths=["Conceito-Novo"]))
    assert rc == 0
    assert _row(isolated_registry).verdict == "ATOMICA"
    assert (isolated_archive / "ATOMICA" / draft.name).exists()


def test_closed_video_is_deduplicated_by_the_vault_scan(fake_vault, isolated_registry):
    draft = _draft(fake_vault)
    cli._cmd_close(_args(close=str(draft), verdict="PROPAGA", reason="x", paths=PAGES))
    hit = vault.find_video_anywhere(VID)
    assert hit is not None and hit.name.endswith(".transcript.md")


def test_close_keeps_draft_when_registry_fails(fake_vault, monkeypatch, isolated_archive):
    draft = _draft(fake_vault)

    def boom(_path):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(cli, "open_registry", boom)
    rc = cli._cmd_close(_args(close=str(draft), verdict="PROPAGA", reason="x", paths=PAGES))
    assert rc == 1
    assert draft.exists()
    assert not list(isolated_archive.rglob("*.draft.md"))


def test_rerun_after_failure_does_not_duplicate_the_card_line(
    fake_vault, monkeypatch, isolated_registry
):
    draft = _draft(fake_vault)
    args = _args(close=str(draft), verdict="PROPAGA", reason="x", paths=PAGES)
    real_open = cli.open_registry

    def boom(_path):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(cli, "open_registry", boom)
    assert cli._cmd_close(args) == 1
    monkeypatch.setattr(cli, "open_registry", real_open)
    assert cli._cmd_close(args) == 0

    card = (fake_vault / "30-Recursos" / "Pessoas" / "Some-Channel.md").read_text(encoding="utf-8")
    assert card.count("· PROPAGA →") == 1


# --- AC-18: FICHAMENTO keeps today's output, plus the archive ----------------

def test_finalize_fichamento_end_to_end(fake_vault, tmp_path, isolated_registry, isolated_archive):
    draft = _draft(fake_vault)
    original = draft.read_bytes()
    body = tmp_path / "body.md"
    body.write_text(BODY, encoding="utf-8")

    rc = cli._cmd_finalize(_args(finalize=str(draft), body_file=str(body),
                                 verdict="FICHAMENTO", reason="palestra longa, vale citar"))

    assert rc == 0
    canal_dir = fake_vault / "30-Recursos" / "Tech" / "Some-Channel"
    assert len(list(canal_dir.glob("3-*.md"))) == 1
    assert len(list((canal_dir / "transcripts").glob("*.transcript.md"))) == 1
    row = _row(isolated_registry)
    assert (row.verdict, row.verdict_reason) == ("FICHAMENTO", "palestra longa, vale citar")
    assert not draft.exists()
    assert (isolated_archive / "FICHAMENTO" / draft.name).read_bytes() == original


def test_finalize_keep_draft_leaves_it_in_place(fake_vault, tmp_path, isolated_archive):
    draft = _draft(fake_vault)
    body = tmp_path / "body.md"
    body.write_text(BODY, encoding="utf-8")
    rc = cli._cmd_finalize(_args(finalize=str(draft), body_file=str(body), keep_draft=True,
                                 verdict="FICHAMENTO", reason="x"))
    assert rc == 0
    assert draft.exists()
    assert not list(isolated_archive.rglob("*.draft.md"))

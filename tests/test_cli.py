"""Testes do CLI: pending file (v0.2.4), loop de extração e guard do finalize (v0.5.0)."""
from __future__ import annotations

import argparse
from pathlib import Path

from yt_nota import cli
from yt_nota.cli import _failed_path_for, _pending_path_for, _save_pending
from yt_nota.extractor import RateLimitError


def test_pending_path_for_none_returns_default():
    assert _pending_path_for(None) == Path("yt-nota.pending.txt")


def test_pending_path_for_queue_appends_suffix():
    result = _pending_path_for("queues/redacted-channel-wave01-fii.txt")
    assert result == Path("queues/redacted-channel-wave01-fii.txt.pending.txt")


def test_pending_path_for_pending_file_is_idempotent():
    src = "queues/redacted-channel-wave01-fii.txt.pending.txt"
    assert _pending_path_for(src) == Path(src)


def test_save_pending_writes_urls_with_header(tmp_path: Path):
    pending = tmp_path / "wave.pending.txt"
    urls = ["https://youtu.be/aaa", "https://youtu.be/bbb"]
    _save_pending(pending, urls, "rate limit 429")
    content = pending.read_text(encoding="utf-8")
    assert "# Wave interrompida: rate limit 429" in content
    assert "2 URLs restantes" in content
    assert "yt-nota --retry-pending wave.pending.txt" in content
    assert "https://youtu.be/aaa" in content
    assert "https://youtu.be/bbb" in content


def test_save_pending_empty_list(tmp_path: Path):
    pending = tmp_path / "wave.pending.txt"
    _save_pending(pending, [], "rate limit 429")
    content = pending.read_text(encoding="utf-8")
    assert "0 URLs restantes" in content


def test_failed_path_for_default():
    assert _failed_path_for(None) == Path("yt-nota.failed.txt")


def test_failed_path_for_queue():
    assert _failed_path_for("queues/wave.txt") == Path("queues/wave.txt.failed.txt")


# ---------------------------------------------------------------------------
# Loop de extração (_cmd_extract)
# ---------------------------------------------------------------------------

URLS = [
    "https://www.youtube.com/watch?v=AAAAAAAAAAA",
    "https://www.youtube.com/watch?v=BBBBBBBBBBB",
    "https://www.youtube.com/watch?v=CCCCCCCCCCC",
    "https://www.youtube.com/watch?v=DDDDDDDDDDD",
]


def _extract_args(**overrides) -> argparse.Namespace:
    base = dict(
        urls=list(URLS),
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
        whisper_fallback=None,
        whisper_model=None,
        whisper_fallback_enabled=False,
        whisper_model_resolved="small",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _quiet_vault(monkeypatch):
    monkeypatch.setattr(cli, "find_video_anywhere", lambda vid: None)
    monkeypatch.setattr(cli, "list_pending_drafts", lambda: [])


def test_429_stop_keeps_first_failed_url_in_pending(tmp_path, monkeypatch):
    """Regressão do off-by-one: a 1ª URL do streak de 429 ia sumir do pending.

    Wave: ok, 429, 429 → para. O pending precisa conter as URLs 2, 3 E 4
    (as duas que deram 429 nunca viraram draft).
    """
    monkeypatch.chdir(tmp_path)
    _quiet_vault(monkeypatch)

    def fake_process(url, args, idx, total):
        if url == URLS[0]:
            return "ok"
        raise RateLimitError("429")

    monkeypatch.setattr(cli, "_process_single", fake_process)

    rc = cli._cmd_extract(_extract_args())
    assert rc == 2

    pending = (tmp_path / "yt-nota.pending.txt").read_text(encoding="utf-8")
    assert URLS[0] not in pending
    assert URLS[1] in pending  # primeira do streak — era perdida antes do fix
    assert URLS[2] in pending
    assert URLS[3] in pending


def test_isolated_429_url_lands_in_failed_file(tmp_path, monkeypatch):
    """429 isolado (streak resetado por sucesso) não pode sumir em silêncio."""
    monkeypatch.chdir(tmp_path)
    _quiet_vault(monkeypatch)

    def fake_process(url, args, idx, total):
        if url == URLS[1]:
            raise RateLimitError("429")
        return "ok"

    monkeypatch.setattr(cli, "_process_single", fake_process)

    rc = cli._cmd_extract(_extract_args())
    assert rc == 1  # nem todas viraram draft
    failed = (tmp_path / "yt-nota.failed.txt").read_text(encoding="utf-8")
    assert URLS[1] in failed
    assert URLS[0] not in failed


def test_individual_failures_saved_to_failed_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _quiet_vault(monkeypatch)

    def fake_process(url, args, idx, total):
        return "fail" if url == URLS[2] else "ok"

    monkeypatch.setattr(cli, "_process_single", fake_process)

    rc = cli._cmd_extract(_extract_args())
    assert rc == 1
    failed = (tmp_path / "yt-nota.failed.txt").read_text(encoding="utf-8")
    assert URLS[2] in failed


def test_preprocessed_urls_skip_without_network(tmp_path, monkeypatch):
    """Dedup pré-rede: URL já no vault não chama _process_single nem dorme."""
    monkeypatch.chdir(tmp_path)
    evidence = tmp_path / "nota.md"
    evidence.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli, "find_video_anywhere", lambda vid: evidence)
    monkeypatch.setattr(cli, "list_pending_drafts", lambda: [])

    calls = []

    def fake_process(url, args, idx, total):
        calls.append(url)
        return "ok"

    monkeypatch.setattr(cli, "_process_single", fake_process)

    rc = cli._cmd_extract(_extract_args())
    assert rc == 0
    assert calls == []  # nenhuma chamada de rede


def test_force_bypasses_pre_network_dedup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    evidence = tmp_path / "nota.md"
    evidence.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli, "find_video_anywhere", lambda vid: evidence)
    monkeypatch.setattr(cli, "list_pending_drafts", lambda: [])

    calls = []

    def fake_process(url, args, idx, total):
        calls.append(url)
        return "ok"

    monkeypatch.setattr(cli, "_process_single", fake_process)

    rc = cli._cmd_extract(_extract_args(force=True))
    assert rc == 0
    assert calls == URLS


# ---------------------------------------------------------------------------
# Guard do finalize (valida as 7 seções antes de deletar o draft)
# ---------------------------------------------------------------------------

def _finalize_args(draft: Path, body: Path, **overrides) -> argparse.Namespace:
    base = dict(
        finalize=str(draft),
        body_file=str(body),
        no_channel_card=False,
        keep_draft=False,
        dominio=None,
        skip_body_check=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


COMPLETE_BODY = "\n\n---\n\n".join(f"{s}\n\nconteúdo" for s in cli.EXPECTED_BODY_SECTIONS)


def test_finalize_guard_blocks_incomplete_body_and_keeps_draft(tmp_path):
    draft = tmp_path / "x.draft.md"
    draft.write_text("---\ntipo: yt-nota-draft\n---\n", encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text("## Em uma frase\n\nsó isso", encoding="utf-8")

    rc = cli._cmd_finalize(_finalize_args(draft, body))
    assert rc == 1
    assert draft.exists()


def test_finalize_guard_accepts_complete_body(tmp_path, monkeypatch):
    draft = tmp_path / "x.draft.md"
    draft.write_text("---\ntipo: yt-nota-draft\n---\n", encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text(COMPLETE_BODY, encoding="utf-8")

    monkeypatch.setattr(
        cli,
        "finalize_draft",
        lambda *a, **kw: {
            "note_path": tmp_path / "nota.md",
            "transcript_path": None,
            "channel_card_path": None,
            "moc_path": None,
            "draft_deleted": True,
        },
    )
    rc = cli._cmd_finalize(_finalize_args(draft, body))
    assert rc == 0


def test_finalize_skip_body_check_bypasses_guard(tmp_path, monkeypatch):
    draft = tmp_path / "x.draft.md"
    draft.write_text("---\ntipo: yt-nota-draft\n---\n", encoding="utf-8")
    body = tmp_path / "body.md"
    body.write_text("corpo fora do formato", encoding="utf-8")

    monkeypatch.setattr(
        cli,
        "finalize_draft",
        lambda *a, **kw: {
            "note_path": tmp_path / "nota.md",
            "transcript_path": None,
            "channel_card_path": None,
            "moc_path": None,
            "draft_deleted": True,
        },
    )
    rc = cli._cmd_finalize(_finalize_args(draft, body, skip_body_check=True))
    assert rc == 0

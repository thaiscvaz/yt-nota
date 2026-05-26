"""Testes pras helpers de pending file (v0.2.4)."""
from __future__ import annotations

from pathlib import Path

from yt_nota.cli import _pending_path_for, _save_pending


def test_pending_path_for_none_returns_default():
    assert _pending_path_for(None) == Path("yt-nota.pending.txt")


def test_pending_path_for_queue_appends_suffix():
    result = _pending_path_for("queues/cara-riqueza-wave01-fii.txt")
    assert result == Path("queues/cara-riqueza-wave01-fii.txt.pending.txt")


def test_pending_path_for_pending_file_is_idempotent():
    src = "queues/cara-riqueza-wave01-fii.txt.pending.txt"
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

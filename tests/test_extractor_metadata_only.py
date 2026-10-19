"""Metadata extraction must not depend on media formats (2026-10-18).

Without a JS runtime, yt-dlp's default client gets UNPLAYABLE for some short
videos and raises "This video is not available" for lack of formats, although
title and duration were returned. Three public videos sat at `descoberto` for
a week this way, and every scheduled run ended with exit code 1.
"""
from __future__ import annotations

import pytest
import yt_dlp

from yt_nota import extractor


class _FakeYDL:
    """Behaves like yt-dlp on those videos: metadata only when formats may be missing."""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if not self.opts.get("ignore_no_formats_error"):
            raise yt_dlp.utils.DownloadError("ERROR: [youtube] x: This video is not available")
        return {"id": "byPo6nFVSFs", "title": "curto", "duration": 33, "formats": []}


def test_extract_info_returns_metadata_when_no_format_is_playable(monkeypatch):
    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", _FakeYDL)
    info = extractor.extract_info("https://www.youtube.com/watch?v=byPo6nFVSFs")
    assert info["duration"] == 33


def test_metadata_options_never_download_media():
    for flat in (False, True):
        opts = extractor._ydl_opts(False, flat_playlist=flat)
        assert opts["skip_download"] is True and opts["ignore_no_formats_error"] is True


def test_other_extraction_errors_still_raise(monkeypatch):
    class _Gone(_FakeYDL):
        def extract_info(self, url, download=False):
            raise yt_dlp.utils.DownloadError("ERROR: [youtube] x: Video unavailable. Removed")

    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", _Gone)
    with pytest.raises(extractor.ExtractError):
        extractor.extract_info("https://www.youtube.com/watch?v=x")

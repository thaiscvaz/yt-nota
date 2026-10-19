"""Extraction errors say what happened (Projeto Buma B-12, 2026-10-19).

Each message is the text yt-dlp raises: YouTube's playability reason after
"ERROR: [youtube] <id>: ", plus what yt-dlp appends (login hint, rate-limit
advice). The bare "This video is not available" is the one observed live on
2026-10-18 for three public shorts.
"""
from __future__ import annotations

import pytest
import yt_dlp

from yt_nota import cli, extractor

LOGIN_HINT = ("Use --cookies-from-browser or --cookies for the authentication. See  "
              "https://github.com/yt-dlp/yt-dlp/wiki/FAQ for how to manually pass cookies")

CASES = [
    ("Private video. Sign in if you've been granted access to this video. " + LOGIN_HINT,
     "private"),
    ("Sign in to confirm you’re not a bot. " + LOGIN_HINT, "rate_limited"),
    ("Sign in to confirm your age. This video may be inappropriate for some users. "
     + LOGIN_HINT, "age_restricted"),
    ("Join this channel to get access to members-only content like this video, and other "
     "exclusive perks.", "members_only"),
    ("Video unavailable. This video has been removed by the uploader", "removed"),
    ("Video unavailable. This video is no longer available because the YouTube account "
     "associated with this video has been terminated.", "removed"),
    ("This video has been removed for violating YouTube's Terms of Service", "removed"),
    ("The uploader has not made this video available in your country", "geo_blocked"),
    ("This video is not available from your location due to geo restriction", "geo_blocked"),
    ("This live event will begin in 3 hours.", "live_or_upcoming"),
    ("Premieres in 2 hours", "live_or_upcoming"),
    ("Video unavailable. This content isn't available, try again later. The current session "
     "has been rate-limited by YouTube for up to an hour.", "rate_limited"),
    ("HTTP Error 429: Too Many Requests", "rate_limited"),
    ("Requested format is not available. Use --list-formats for a list of available formats",
     "no_formats"),
    ("This video is not available", "unavailable"),
    ("Video unavailable", "unavailable"),
    ("This video is unavailable", "unavailable"),  # live, for a nonexistent id, 2026-10-19
    ("Unsupported URL: https://example.com/x", "unknown"),
]


@pytest.mark.parametrize("reason,kind", CASES)
def test_kind_comes_from_the_message(reason, kind):
    assert extractor.classify_error(f"ERROR: [youtube] abc123DEF45: {reason}") == kind


def test_every_kind_has_a_plain_text():
    kinds = {k for k, _ in extractor.ERROR_KINDS} | {"unknown"}
    assert kinds == set(extractor.ERROR_KIND_TEXT)


def test_extract_error_carries_the_kind_and_says_it(monkeypatch):
    class _Removed:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            raise yt_dlp.utils.DownloadError(
                "ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader")

    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", _Removed)
    with pytest.raises(extractor.ExtractError) as err:
        extractor.extract_info("https://www.youtube.com/watch?v=x")
    assert err.value.kind == "removed"
    assert "removed" in str(err.value) and "removido" in str(err.value)


def test_rate_limit_error_keeps_working_without_a_kind():
    e = extractor.RateLimitError("429")
    assert isinstance(e, extractor.ExtractError) and e.kind == "unknown"


def test_aborted_metadata_event_records_the_kind(monkeypatch, isolated_registry):
    import argparse

    from yt_nota.registry import open_registry

    def fail(url, **kw):
        raise extractor.ExtractError("yt-dlp falhou (private: ...): Private video", kind="private")

    monkeypatch.setattr(cli, "extract_info", fail)
    args = argparse.Namespace(with_cookies=False, dominio=None, force=True, dry_run=False,
                              tema=None, db=None, whisper_fallback_enabled=False,
                              whisper_model_resolved="small")
    assert cli._process_single("https://www.youtube.com/watch?v=abc123DEF45", args, 1, 1) == "fail"
    with open_registry(isolated_registry) as r:
        ev = [e for e in r.list_events("abc123DEF45") if e["lifecycle"] == "ate_abort"]
    assert ev and ev[0]["activity"] == "fetch_metadata", ev
    assert ev[0]["attrs"]["error_kind"] == "private", ev

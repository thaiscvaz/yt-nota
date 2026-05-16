from pathlib import Path

from yt_nota.transcript import (
    Segment,
    parse_vtt,
    parse_vtt_file,
    segments_to_plain,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_manual_vtt_keeps_all_cues():
    segs = parse_vtt_file(FIXTURES / "sample_manual.vtt")
    assert len(segs) == 3
    assert segs[0].t == "0:01"
    assert segs[0].text == "Hello world"
    assert segs[1].t == "0:05"
    assert segs[1].text == "This is a longer line that wraps in the file"
    assert segs[2].t == "1:10"
    assert segs[2].text == "After one minute we say goodbye"


def test_parse_auto_vtt_dedups_overlap():
    segs = parse_vtt_file(FIXTURES / "sample_auto.vtt")
    # Should produce 3 segments with rolling-buffer overlap removed
    assert len(segs) == 3
    assert segs[0].text == "ola tudo"
    assert segs[1].text == "bem com voces"
    assert segs[2].text == "hoje"


def test_segments_to_plain_with_timestamps():
    segs = [Segment(t="0:00", text="hello"), Segment(t="0:05", text="world")]
    result = segments_to_plain(segs)
    assert "[0:00] hello" in result
    assert "[0:05] world" in result


def test_empty_vtt_returns_empty():
    segs = parse_vtt("WEBVTT\n\n")
    assert segs == []


def test_vtt_with_inline_tags_strips_them():
    raw = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "<00:00:01.000><c>hello world</c>\n"
    )
    segs = parse_vtt(raw)
    assert len(segs) == 1
    assert segs[0].text == "hello world"


def test_vtt_with_hours():
    raw = (
        "WEBVTT\n\n"
        "01:02:03.000 --> 01:02:06.000\n"
        "after one hour\n"
    )
    segs = parse_vtt(raw)
    assert len(segs) == 1
    assert segs[0].t == "1:02:03"

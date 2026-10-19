"""Event log of the registry (Projeto Buma B-09, delivery 1)."""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

import pytest

from yt_nota import cli, registry as regmod
from yt_nota.extractor import RateLimitError
from yt_nota.registry import (
    COVERAGE_KEY_D1,
    SCHEMA,
    STATUS_BAIXADO,
    STATUS_CURADO,
    STATUS_DESCOBERTO,
    VERDICT_DESCARTE,
    VERDICT_FICHAMENTO,
    VERDICT_PROPAGA,
    Registry,
    backfill_from_processados,
    open_registry,
)


@pytest.fixture
def reg():
    with open_registry(":memory:") as r:
        yield r


def _transitions(reg: Registry, vid: str) -> list[dict]:
    return [e for e in reg.list_events(vid) if e["kind"] == "transition"]


# -- migration -----------------------------------------------------------------


def _v1_db(path: Path) -> None:
    """A database as schema version 1 left it, with real-looking rows."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
    conn.executemany(
        "INSERT INTO videos (video_id, channel_name, first_seen_at, status, verdict, "
        "vault_paths, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [("v1", "C", "2026-10-01T00:00:00+00:00", "curado", "DESCARTE", "[]",
          "2026-10-02T00:00:00+00:00"),
         ("v2", "C", "2026-10-03T00:00:00+00:00", "descoberto", None, None,
          "2026-10-03T00:00:00+00:00")],
    )
    conn.commit()
    conn.close()


def _dump(path: Path) -> tuple:
    conn = sqlite3.connect(path)
    videos = conn.execute("SELECT * FROM videos ORDER BY video_id").fetchall()
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    return videos, meta, events


def test_migration_from_v1_keeps_videos_and_is_idempotent(tmp_path):
    db = tmp_path / "registry.db"
    _v1_db(db)
    conn = sqlite3.connect(db)
    before = conn.execute("SELECT * FROM videos ORDER BY video_id").fetchall()
    conn.close()

    with open_registry(db):
        pass
    videos1, meta1, events1 = _dump(db)
    with open_registry(db):
        pass
    videos2, meta2, events2 = _dump(db)

    assert videos1 == before == videos2  # `videos` untouched by the migration
    assert meta1["schema_version"] == "2"
    assert meta1 == meta2  # coverage start kept from the first open
    assert COVERAGE_KEY_D1 in meta1
    assert events1 == events2 == 0  # migration writes no event


# -- transitions -----------------------------------------------------------------


def test_new_video_writes_one_transition(reg: Registry):
    assert reg.mark_discovered("v1", "C") is True
    assert reg.mark_discovered("v1", "C") is False  # re-discovery: no new row, no event
    (t,) = _transitions(reg, "v1")
    assert (t["attrs"]["from"], t["attrs"]["to"]) == (None, STATUS_DESCOBERTO)
    assert t["activity"] == "status" and t["lifecycle"] == "complete"


def test_each_status_change_writes_exactly_one_transition(reg: Registry):
    reg.mark_discovered("v1", "C")
    reg.mark_downloaded("v1", transcript_source="auto")
    reg.mark_downloaded("v1", transcript_source="auto")  # same status: no transition
    reg.mark_curated("v1", VERDICT_PROPAGA, reason="adds a number", vault_paths=["a.md"])
    reg.mark_curated("v1", VERDICT_DESCARTE, reason="re-decided")  # verdict rewritten
    steps = [(t["attrs"]["from"], t["attrs"]["to"], t["attrs"].get("verdict"))
             for t in _transitions(reg, "v1")]
    assert steps == [
        (None, STATUS_DESCOBERTO, None),
        (STATUS_DESCOBERTO, STATUS_BAIXADO, None),
        (STATUS_BAIXADO, STATUS_CURADO, VERDICT_PROPAGA),
        (STATUS_CURADO, STATUS_CURADO, VERDICT_DESCARTE),
    ]
    assert _transitions(reg, "v1")[1]["attrs"]["transcript_source"] == "auto"
    assert _transitions(reg, "v1")[2]["attrs"]["vault_paths"] == ["a.md"]


def test_decisions_in_the_same_second_all_reach_the_history(reg: Registry, monkeypatch):
    # Thais's review reproduced FICHAMENTO -> PROPAGA -> DESCARTE in one second
    # leaving only two events: the key depended on the clock. Freeze the clock.
    monkeypatch.setattr(regmod, "_now", lambda: "2026-10-18T12:00:00+00:00")
    reg.mark_discovered("v1", "C")
    for verdict in (VERDICT_FICHAMENTO, VERDICT_PROPAGA, VERDICT_DESCARTE):
        reg.mark_curated("v1", verdict, reason=verdict.lower())
    verdicts = [t["attrs"].get("verdict") for t in _transitions(reg, "v1")][1:]
    assert verdicts == [VERDICT_FICHAMENTO, VERDICT_PROPAGA, VERDICT_DESCARTE]
    assert reg.get("v1").verdict == VERDICT_DESCARTE  # state and history agree


def test_replayed_operation_dedupes_and_a_changed_replay_conflicts(reg: Registry):
    reg.mark_discovered("v1", "C")
    reg.mark_curated("v1", VERDICT_PROPAGA, reason="r", op_id="op-1")
    reg.mark_curated("v1", VERDICT_PROPAGA, reason="r", op_id="op-1")  # same op, resent
    assert len(_transitions(reg, "v1")) == 2  # discovery + one curation
    with pytest.raises(regmod.EventConflict):
        reg.mark_curated("v1", VERDICT_DESCARTE, reason="other", op_id="op-1")
    assert reg.get("v1").verdict == VERDICT_PROPAGA  # the conflicting write rolled back
    assert reg.record_event("v1", "screen", "complete", attempt_id="a1",
                            attrs={"reason": "kept"}) is True
    with pytest.raises(regmod.EventConflict):
        reg.record_event("v1", "screen", "complete", attempt_id="a1",
                         attrs={"reason": "short"})


def test_resend_of_an_operation_that_changed_nothing_never_undoes_a_later_state(reg: Registry):
    # Thais's third review: op B asked for `baixado` when the video was already
    # there, so it wrote no transition and left no trace; after `curado`, a
    # resend of B put the video back to `baixado`.
    reg.mark_discovered("v1", "C")
    reg.mark_downloaded("v1", op_id="A")
    reg.mark_downloaded("v1", op_id="B")  # no state change, still recorded
    reg.mark_curated("v1", VERDICT_PROPAGA, reason="r", op_id="C")
    reg.mark_downloaded("v1", op_id="B")  # resend of B
    assert reg.get("v1").status == STATUS_CURADO
    assert [t["attrs"]["to"] for t in _transitions(reg, "v1")] == [
        STATUS_DESCOBERTO, STATUS_BAIXADO, STATUS_CURADO]
    with pytest.raises(regmod.EventConflict):  # same op id, other content
        reg.mark_downloaded("v1", op_id="B", transcript_source="auto")
    assert reg.get("v1").status == STATUS_CURADO


def test_concurrent_writer_cannot_change_the_state_between_read_and_write(tmp_path):
    # Review: B changed descoberto -> baixado between A's read and A's write, and
    # A recorded `from: descoberto`. Now A holds the write lock before reading,
    # so B cannot write in between, and A's `from` is what A replaced.
    db = tmp_path / "r.db"
    with open_registry(db) as r:
        r.mark_discovered("v1", "C")
    blocked: list[str] = []

    class Interleaved(Registry):
        def _current_status(self, video_id):
            other = sqlite3.connect(db, timeout=0.2)
            try:
                other.execute("UPDATE videos SET status = 'baixado' WHERE video_id = ?",
                              (video_id,))
                other.commit()
            except sqlite3.OperationalError as e:
                blocked.append(str(e))
            finally:
                other.close()
            return super()._current_status(video_id)

    a = Interleaved(sqlite3.connect(db))
    try:
        a.mark_curated("v1", VERDICT_DESCARTE, reason="x")
    finally:
        a.close()
    assert blocked and "locked" in blocked[0]
    with open_registry(db) as r:
        last = _transitions(r, "v1")[-1]
        assert (last["attrs"]["from"], last["attrs"]["to"]) == (STATUS_DESCOBERTO, STATUS_CURADO)
        assert r.get("v1").status == STATUS_CURADO


def test_readonly_open_never_migrates_or_writes(tmp_path):
    db = tmp_path / "registry.db"
    _v1_db(db)
    before = db.read_bytes()
    with open_registry(db, readonly=True) as r:
        assert r.is_known("v1") and not r.is_known("zz")
        with pytest.raises(sqlite3.OperationalError):
            r.record_event("v1", "screen", "complete")
        with pytest.raises(sqlite3.OperationalError):
            r.mark_discovered("v3", "C")
    assert db.read_bytes() == before  # still schema v1, byte-identical


def test_cli_dry_runs_and_read_actions_never_touch_the_database(tmp_path):
    # Thais's third review: `--registry backfill --dry-run` on a v1 database
    # exited 0 but migrated it to v2 and stamped the coverage start.
    import json
    db = tmp_path / "registry.db"
    _v1_db(db)
    legacy = tmp_path / "processados.json"
    legacy.write_text(json.dumps({"C": ["n1", "v1"]}), encoding="utf-8")
    before = db.read_bytes()
    base = dict(db=str(db), channel=None, status=None, verdict=None, limit=None,
                ids=None, from_file=str(legacy), reason=None, events_out=None,
                events_format="csv")
    for action, dry in (("backfill", True), ("adopt-drafts", True), ("stats", False),
                        ("list", False), ("filter", False)):
        args = argparse.Namespace(registry=action, dry_run=dry, **base)
        cli._cmd_registry(args)
        assert db.read_bytes() == before, action
    # extraction dry run: the dedup lookup reads the registry read-only
    assert cli._registry_done_ids(argparse.Namespace(db=str(db), dry_run=True)) == {"v1"}
    assert db.read_bytes() == before
    # a missing database is not created by a read-only open
    missing = tmp_path / "none.db"
    assert cli._registry_done_ids(argparse.Namespace(db=str(missing), dry_run=True)) == set()
    with open_registry(missing, readonly=True) as r:
        assert r.total() == 0
        with pytest.raises(sqlite3.OperationalError):
            r.mark_discovered("x", "C")
    assert not missing.exists()


def _registry_args(db: Path, action: str, *, dry_run: bool = False, **over) -> argparse.Namespace:
    base = dict(db=str(db), channel=None, status=None, verdict=None, limit=None, ids=None,
                from_file=None, reason=None, events_out=None, events_format="csv")
    base.update(over)
    return argparse.Namespace(registry=action, dry_run=dry_run, **base)


@pytest.mark.parametrize("fmt", ["csv", "ocel2"])
def test_events_export_on_v1_database_exports_empty_log(tmp_path, caplog, fmt):
    # Thais's fourth review (P1): on a v1 database the read-only export raised
    # `no such table: events`.
    import json
    db = tmp_path / "registry.db"
    _v1_db(db)
    before = db.read_bytes()
    out = tmp_path / f"events.{fmt}"
    with caplog.at_level("WARNING"):
        rc = cli._cmd_registry(_registry_args(db, "events-export", events_out=str(out),
                                              events_format=fmt))
    assert rc == 0
    assert "Cobertura de eventos ainda não iniciada" in caplog.text
    if fmt == "csv":
        with open(out, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0][:4] == ["case_id", "activity", "lifecycle", "timestamp"] and len(rows) == 1
    else:
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["events"] == [] and doc["objects"] == []
    assert db.read_bytes() == before  # still v1, byte-identical


def test_events_export_with_coverage_started_does_not_warn(tmp_path, caplog):
    db = tmp_path / "registry.db"
    with open_registry(db) as r:
        r.mark_discovered("v1", "C")
    with caplog.at_level("WARNING"):
        rc = cli._cmd_registry(_registry_args(db, "events-export",
                                              events_out=str(tmp_path / "e.csv")))
    assert rc == 0 and "Cobertura de eventos" not in caplog.text


def test_mark_dry_run_is_refused_before_opening_the_database(tmp_path, caplog):
    # Thais's fourth review (P2): `mark --dry-run` failed on its first write with
    # `registry opened read-only` instead of refusing clearly.
    db = tmp_path / "registry.db"
    _v1_db(db)
    before = db.read_bytes()
    args = _registry_args(db, "mark", dry_run=True, channel="C", ids=["n1"])
    with caplog.at_level("ERROR"):
        assert cli._cmd_registry(args) == 1
    assert "não tem modo de simulação" in caplog.text
    assert db.read_bytes() == before
    missing = tmp_path / "none.db"
    assert cli._cmd_registry(_registry_args(missing, "mark", dry_run=True, channel="C",
                                            ids=["n1"])) == 1
    assert not missing.exists()


def test_reconcile_evidence_is_kept_as_evidence_not_as_time(reg: Registry):
    reg.mark_discovered("v1", "C")
    ev = {"path": "x.md", "sha256": "abc", "mtime": "2026-01-01T00:00:00+00:00",
          "basis": "file_mtime_is_not_the_download_time"}
    reg.mark_downloaded("v1", evidence=ev)
    t = _transitions(reg, "v1")[-1]
    assert t["attrs"]["evidence"] == ev
    assert t["occurred_at"] != ev["mtime"]  # observed now, not the file time


def test_failed_event_write_rolls_back_the_state_change(reg: Registry, monkeypatch):
    reg.mark_discovered("v1", "C")

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(reg, "_insert_transition", boom)
    with pytest.raises(sqlite3.OperationalError):
        reg.mark_downloaded("v1")
    assert reg.get("v1").status == STATUS_DESCOBERTO
    with pytest.raises(sqlite3.OperationalError):
        reg.mark_discovered("v2", "C")
    assert reg.get("v2") is None


def test_occurred_and_recorded_times_are_separate(reg: Registry):
    reg.mark_discovered("v1", "C")
    reg.mark_downloaded("v1", occurred_at="2026-01-01T00:00:00+00:00")
    events = _transitions(reg, "v1")
    (t,) = [e for e in events if e["attrs"]["to"] == STATUS_BAIXADO]
    assert t["occurred_at"] == "2026-01-01T00:00:00+00:00"
    assert t["recorded_at"] > t["occurred_at"]
    # The log orders by when things happened, not by when they were written.
    assert events[0] is t


def test_backfill_transitions_are_marked_retrospective(reg: Registry):
    backfill_from_processados(reg, {"C": ["v9"]})
    ts = _transitions(reg, "v9")
    assert ts and all(t["attrs"].get("retrospective") is True for t in ts)
    assert all(t["actor"] == "backfill" for t in ts)
    reg.mark_discovered("v10", "C")  # after the backfill, back to normal
    (t,) = _transitions(reg, "v10")
    assert "retrospective" not in t["attrs"] and t["actor"] == "yt-nota"


# -- activities --------------------------------------------------------------------


def test_record_event_dedupes_by_key(reg: Registry):
    assert reg.record_event("v1", "transcribe", "start", attempt_id="att-1") is True
    assert reg.record_event("v1", "transcribe", "start", attempt_id="att-1") is False
    assert len(reg.list_events("v1")) == 1


def test_record_event_validates_vocabulary(reg: Registry):
    with pytest.raises(ValueError):
        reg.record_event("v1", "screen", "done")
    with pytest.raises(ValueError):
        reg.record_event("v1", "screen", "complete", decided_by="someone")
    with pytest.raises(ValueError):
        reg.record_event("v1", "screen", "complete", kind="other")


def test_run_id_comes_from_env_and_actor_from_open(monkeypatch):
    monkeypatch.setenv(regmod.RUN_ID_ENV, "run-fixed")
    with open_registry(":memory:", actor="fetch_new") as r:
        r.record_event("v1", "screen", "withdraw", decided_by="rule", attrs={"reason": "short"})
        (e,) = r.list_events("v1")
    assert (e["run_id"], e["actor"], e["decided_by"]) == ("run-fixed", "fetch_new", "rule")


def test_current_run_id_is_created_once_and_exported(monkeypatch):
    monkeypatch.delenv(regmod.RUN_ID_ENV, raising=False)
    first = regmod.current_run_id()
    assert first.startswith("run-")
    assert regmod.current_run_id() == first
    import os
    assert os.environ[regmod.RUN_ID_ENV] == first


def test_export_csv_has_process_mining_columns_first(reg: Registry, tmp_path):
    reg.mark_discovered("v1", "C")
    reg.record_event("v1", "transcribe", "start", attempt_id="att-1")
    out = tmp_path / "events.csv"
    assert reg.export_events_csv(out) == 2
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0][:4] == ["case_id", "activity", "lifecycle", "timestamp"]
    assert len(rows) == 3 and rows[1][0] == "v1"


def test_every_event_links_its_source_and_extra_objects(reg: Registry):
    reg.mark_discovered("v1", "C")
    reg.record_event("v1", "transcribe", "complete", attempt_id="a1",
                     objects=[("transcript", "v1:abc", "produced")])
    t, act = reg.list_events("v1")
    assert t["objects"] == [("source", "v1", "case")]
    assert act["objects"] == [("source", "v1", "case"), ("transcript", "v1:abc", "produced")]
    with pytest.raises(ValueError):
        reg.record_event("v1", "x", "complete", objects=[("video", "v1", "case")])


def test_ocel2_export_structure(reg: Registry, tmp_path):
    import json
    reg.mark_discovered("v1", "C")
    reg.record_event("v1", "transcribe", "complete", attempt_id="a1",
                     attrs={"transcript_words": 10, "software": {"commit": "abc"}},
                     objects=[("transcript", "v1:abc", "produced")])
    out = tmp_path / "log.json"
    assert reg.export_events_ocel2_json(out) == {"events": 2, "objects": 2}
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert set(doc) == {"objectTypes", "eventTypes", "objects", "events"}
    assert {e["type"] for e in doc["events"]} == {"status:descoberto", "transcribe:complete"}
    ev = [e for e in doc["events"] if e["type"] == "transcribe:complete"][0]
    assert {r["objectId"] for r in ev["relationships"]} == {"source:v1", "transcript:v1:abc"}
    attrs = {a["name"]: a["value"] for a in ev["attributes"]}
    assert attrs["attr_transcript_words"] == 10 and json.loads(attrs["attr_software"]) == {"commit": "abc"}
    assert {o["id"] for o in doc["objects"]} == {"source:v1", "transcript:v1:abc"}


def test_rollback_on_a_file_database(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    with open_registry(db) as r:
        r.mark_discovered("v1", "C")
        monkeypatch.setattr(r, "_insert_transition",
                            lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
        with pytest.raises(sqlite3.OperationalError):
            r.mark_downloaded("v1")
    with open_registry(db) as r:  # a fresh connection sees the committed state only
        assert r.get("v1").status == STATUS_DESCOBERTO
        assert len([e for e in r.list_events("v1") if e["kind"] == "transition"]) == 1


# -- CLI: transcription attempts -----------------------------------------------------

URL_A = "https://www.youtube.com/watch?v=AAAAAAAAAAA"


def _extract_args(**over) -> argparse.Namespace:
    base = dict(with_cookies=False, dominio=None, force=True, dry_run=False, tema=None,
                db=None, whisper_fallback_enabled=False, whisper_model_resolved="small")
    base.update(over)
    return argparse.Namespace(**base)


def _stub_pipeline(monkeypatch, transcript_fn):
    video = {"video_id": "AAAAAAAAAAA", "channel": "Some Channel", "title": "t",
             "duration_human": "10m", "upload_date_iso": "2026-10-01",
             "channel_id": "UC1", "duration_seconds": 600}
    monkeypatch.setattr(cli, "extract_info", lambda url, with_cookies=False: {})
    monkeypatch.setattr(cli, "is_playlist", lambda info: False)
    monkeypatch.setattr(cli, "normalize_video_info", lambda info: dict(video))
    monkeypatch.setattr(cli, "resolve_dominio", lambda slug, override=None: "Tech")
    monkeypatch.setattr(cli, "extract_transcript", transcript_fn)
    monkeypatch.setattr(cli, "write_draft", lambda *a, **k: Path("draft.md"))


def _events(db: Path) -> list[dict]:
    with open_registry(db) as r:
        return [e for e in r.list_events("AAAAAAAAAAA") if e["kind"] == "activity"]


def test_transcribe_start_and_complete_share_an_attempt(monkeypatch, isolated_registry):
    _stub_pipeline(monkeypatch, lambda v, **k: {"origin": "whisper-local", "language": "pt",
                                                "segments": [1, 2, 3]})
    assert cli._process_single(URL_A, _extract_args(), 1, 1) == "ok"
    evs = _events(isolated_registry)
    start, end = [e for e in evs if e["activity"] == "transcribe"]
    assert (start["lifecycle"], end["lifecycle"]) == ("start", "complete")
    assert start["attempt_id"] == end["attempt_id"] and start["attempt_id"]
    a = end["attrs"]
    assert (a["origin"], a["language"], a["segments"]) == ("whisper-local", "pt", 3)
    # requested from the CLI; effective only from the transcriber (absent here)
    assert (a["whisper_model_requested"], a["whisper_model_effective"]) == ("small", None)
    assert len(a["transcript_sha256"]) == 64 and a["transcript_words"] == 3
    assert set(a["software"]) == {"yt_nota", "commit", "dirty"}
    from yt_nota import __version__
    assert a["software"]["yt_nota"] == __version__  # the source, not stale metadata
    # objects: the case's source plus the transcript it produced
    types = {(t, q) for t, _, q in end["objects"]}
    assert types == {("source", "case"), ("transcript", "produced")}
    (draft_ev,) = [e for e in evs if e["activity"] == "write_draft"]
    dtypes = {(t, q) for t, _, q in draft_ev["objects"]}
    assert dtypes == {("source", "case"), ("transcript", "input"), ("draft", "produced")}
    assert [e["activity"] for e in evs][:2] == ["fetch_metadata", "fetch_metadata"]


def test_transcribe_rate_limit_is_an_aborted_attempt(monkeypatch, isolated_registry):
    def limited(v, **k):
        raise RateLimitError("429")

    _stub_pipeline(monkeypatch, limited)
    with pytest.raises(RateLimitError):
        cli._process_single(URL_A, _extract_args(), 1, 1)
    start, end = [e for e in _events(isolated_registry) if e["activity"] == "transcribe"]
    assert end["lifecycle"] == "ate_abort" and end["attrs"]["reason"] == "rate_limit"
    assert start["attempt_id"] == end["attempt_id"]


def test_unresolved_domain_and_already_processed_leave_events(monkeypatch, isolated_registry):
    from yt_nota.domain import DomainResolutionError

    _stub_pipeline(monkeypatch, lambda v, **k: None)

    def no_domain(slug, override=None):
        raise DomainResolutionError("canal sem domínio")

    monkeypatch.setattr(cli, "resolve_dominio", no_domain)
    assert cli._process_single(URL_A, _extract_args(), 1, 1) == "fail"
    route = [e for e in _events(isolated_registry) if e["activity"] == "route"]
    assert [(e["lifecycle"], e["attrs"]["reason"]) for e in route] == [
        ("ate_abort", "domain_unresolved")]

    _stub_pipeline(monkeypatch, lambda v, **k: None)
    monkeypatch.setattr(cli, "is_video_already_processed",
                        lambda vid, slug, dominio=None: (True, Path("x.md")))
    assert cli._process_single(URL_A, _extract_args(force=False), 1, 1) == "skip"
    route = [e for e in _events(isolated_registry) if e["activity"] == "route"]
    assert [(e["lifecycle"], e["attrs"].get("reason")) for e in route][-2:] == [
        ("complete", None), ("withdraw", "already_processed")]


def test_unavailable_video_is_an_aborted_metadata_fetch(monkeypatch, isolated_registry):
    # The cause of the recurring exit 1: the video fails at metadata, before any
    # transcription, and used to leave no trace (B-09).
    from yt_nota.extractor import ExtractError

    def unavailable(url, with_cookies=False):
        raise ExtractError("ERROR: [youtube] AAAAAAAAAAA: This video is not available")

    monkeypatch.setattr(cli, "extract_info", unavailable)
    url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    assert cli._process_single(url, _extract_args(), 1, 1) == "fail"
    start, end = _events(isolated_registry)
    assert (start["activity"], start["lifecycle"]) == ("fetch_metadata", "start")
    assert (end["lifecycle"], end["attrs"]["reason"]) == ("ate_abort", "extract_error")
    assert "not available" in end["attrs"]["error"]


def test_dry_run_writes_no_event(monkeypatch, isolated_registry):
    _stub_pipeline(monkeypatch, lambda v, **k: None)
    cli._process_single(URL_A, _extract_args(dry_run=True), 1, 1)
    assert _events(isolated_registry) == []


def test_event_write_failure_never_stops_extraction(monkeypatch, isolated_registry):
    _stub_pipeline(monkeypatch, lambda v, **k: None)

    def broken(*a, **k):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(Registry, "record_event", broken)
    assert cli._process_single(URL_A, _extract_args(), 1, 1) == "ok"


def test_events_export_cli(monkeypatch, isolated_registry, tmp_path):
    with open_registry(isolated_registry) as r:
        r.mark_discovered("v1", "C")
    out = tmp_path / "e.csv"
    args = argparse.Namespace(registry="events-export", db=str(isolated_registry),
                              events_out=str(out))
    assert cli._cmd_registry(args) == 0
    assert out.read_text(encoding="utf-8").startswith("case_id,activity,lifecycle,timestamp")
    args.events_out = None
    assert cli._cmd_registry(args) == 1

"""Registro durável do que já passou pelo pipeline.

Substitui o `processados.json` que morava no vault (`Claude/automacoes/curadoria-incremental/data/`)
e guardava só uma lista de video_ids por canal — sem veredicto, sem timestamp, sem destino,
sem registro de erro.

Três propriedades que o JSON não dava:

1. **Idempotência por construção.** A linha é escrita na DESCOBERTA, não na conclusão. Se a
   sessão headless morre entre o download e a escrita do estado (bug real de 2026-07-21), o
   vídeo continua marcado — não é rebaixado nem re-baixado.

2. **Veredicto auditável.** Cada vídeo carrega o que o portão de curadoria decidiu e por quê.
   Curadoria agressiva demais fica visível e ajustável, em vez de silenciosa.

3. **Crash-safe.** Uma transação por vídeo. Sem arquivo meio-escrito.

O schema é criado sob demanda; `open_registry()` é idempotente e serve tanto pro banco real
quanto pra `:memory:` nos testes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import PACKAGE_ROOT

DEFAULT_DB_PATH = PACKAGE_ROOT / "data" / "registry.db"

# Ciclo de vida de um vídeo. A linha nasce em `descoberto` e só avança.
STATUS_DESCOBERTO = "descoberto"  # visto no RSS, ainda não baixado
STATUS_BAIXADO = "baixado"  # transcript extraído, aguardando o portão
STATUS_CURADO = "curado"  # portão decidiu, veredicto gravado
STATUS_ERRO = "erro"  # falhou; `error` explica
STATUS_HISTORICO = "historico"  # migrado do processados.json, procedência incerta

VALID_STATUS = frozenset(
    {STATUS_DESCOBERTO, STATUS_BAIXADO, STATUS_CURADO, STATUS_ERRO, STATUS_HISTORICO}
)

# Veredictos do portão de curadoria (Fase 3 do plano Curadoria-YouTube-v2).
VERDICT_DESCARTE = "DESCARTE"  # nada escrito no vault
VERDICT_PROPAGA = "PROPAGA"  # só `## Atualizações` de atômicas já existentes
VERDICT_ATOMICA = "ATOMICA"  # cria/atualiza uma nota de conceito
VERDICT_FICHAMENTO = "FICHAMENTO"  # fichamento completo (fonte densa; exceção)

VALID_VERDICTS = frozenset(
    {VERDICT_DESCARTE, VERDICT_PROPAGA, VERDICT_ATOMICA, VERDICT_FICHAMENTO}
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id          TEXT PRIMARY KEY,
    channel_id        TEXT,
    channel_name      TEXT NOT NULL,
    title             TEXT,
    published_at      TEXT,
    duration_s        INTEGER,
    first_seen_at     TEXT NOT NULL,
    status            TEXT NOT NULL,
    verdict           TEXT,
    verdict_reason    TEXT,
    vault_paths       TEXT,
    transcript_source TEXT,
    error             TEXT,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_name, published_at);
CREATE INDEX IF NOT EXISTS idx_videos_status  ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_verdict ON videos(verdict);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SCHEMA_VERSION = "2"

# Event log (Projeto Buma B-09, delivery 1). Append-only. Two kinds of rows:
# `activity` rows are work done by a step, with an XES lifecycle (start,
# complete, ate_abort, schedule, withdraw); `transition` rows are status changes
# of a video, written by this module in the SAME transaction as the change, so a
# state and its transition never disagree. `occurred_at` is when it happened
# (for reconciliation, the time of the disk evidence); `recorded_at` is when the
# row was written. `event_key` is unique, so a retried write is ignored.
EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key   TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    case_id     TEXT NOT NULL,
    activity    TEXT NOT NULL,
    lifecycle   TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    run_id      TEXT,
    attempt_id  TEXT,
    actor       TEXT NOT NULL,
    decided_by  TEXT,
    attrs       TEXT NOT NULL DEFAULT '{}',
    payload_sha256 TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_case     ON events(case_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_activity ON events(activity, lifecycle);

-- Object-centric links (OCEL 2.0 event-to-object relationship): one event can
-- touch several typed objects, each with a qualifier naming its role.
CREATE TABLE IF NOT EXISTS event_objects (
    event_id    INTEGER NOT NULL REFERENCES events(event_id),
    object_type TEXT NOT NULL,
    object_id   TEXT NOT NULL,
    qualifier   TEXT NOT NULL,
    PRIMARY KEY (event_id, object_type, object_id, qualifier)
);
CREATE INDEX IF NOT EXISTS idx_event_objects_object ON event_objects(object_type, object_id);

-- Operations applied with an op_id, recorded whether or not they changed the
-- state, so a resend is recognized even when the first apply wrote no
-- transition (it must never undo a later state).
CREATE TABLE IF NOT EXISTS operations (
    op_key         TEXT PRIMARY KEY,
    case_id        TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    applied_at     TEXT NOT NULL,
    run_id         TEXT,
    changed_state  INTEGER NOT NULL
);
"""

# Object types of the curation process (B-09). `source` is the video; a
# `transcript` id carries the hash of its text, so a re-transcription is a new
# object; `draft`, `note` (final fichamento), `profile` and `concept_page` are
# vault files identified by their repo-relative path.
OBJECT_TYPES = frozenset({"source", "transcript", "draft", "note", "profile", "concept_page"})

EVENT_KINDS = frozenset({"activity", "transition"})
LIFECYCLES = frozenset({"schedule", "start", "complete", "ate_abort", "withdraw"})
DECIDERS = frozenset({"rule", "model", "human"})
EVENT_COLUMNS = ("event_id", "event_key", "kind", "case_id", "activity", "lifecycle",
                 "occurred_at", "recorded_at", "run_id", "attempt_id", "actor",
                 "decided_by", "attrs")
# Coverage start of each delivery, kept in `meta` (B-09: opening the issue does
# not start the history; switching on the producers does).
COVERAGE_KEY_D1 = "events_d1_since"

RUN_ID_ENV = "YT_NOTA_RUN_ID"
ACTOR_ENV = "YT_NOTA_ACTOR"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_run_id() -> str:
    """The run id of this process, shared with child processes.

    Taken from YT_NOTA_RUN_ID when a wrapper set it; otherwise created once and
    exported, so `fetch_new.py` and the `yt-nota` it starts carry the same id."""
    rid = os.environ.get(RUN_ID_ENV)
    if not rid:
        rid = f"run-{uuid4().hex[:12]}"
        os.environ[RUN_ID_ENV] = rid
    return rid


def new_attempt_id() -> str:
    """One attempt of one activity on one case; links its start to its end."""
    return f"att-{uuid4().hex[:12]}"


def text_measures(text: str) -> dict:
    """Hash and size of a text, so an event says WHICH version it touched."""
    import hashlib
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "words": len(text.split())}


def software_version() -> dict:
    """yt-nota version and git commit of the running code, when available."""
    out: dict = {"yt_nota": None, "commit": None, "dirty": None}
    try:
        # The source's own version, not importlib.metadata: an editable install
        # keeps the metadata of the version it was installed at (measured on
        # 2026-10-18: metadata said 0.3.0 while the code was 0.5.0).
        from . import __version__
        out["yt_nota"] = __version__
    except Exception:  # noqa: BLE001 - leave unknown, never guess
        pass
    try:
        import subprocess
        root = Path(__file__).resolve().parents[2]
        out["commit"] = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5).stdout.strip() or None
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", "src"],
            capture_output=True, text=True, timeout=5).stdout
        out["dirty"] = bool(status.strip()) if out["commit"] else None
    except Exception:  # noqa: BLE001 - git absent: leave unknown, never guess
        pass
    return out


@dataclass(frozen=True)
class VideoRecord:
    """Uma linha do registro, já desserializada."""

    video_id: str
    channel_name: str
    channel_id: str | None = None
    title: str | None = None
    published_at: str | None = None
    duration_s: int | None = None
    first_seen_at: str = ""
    status: str = STATUS_DESCOBERTO
    verdict: str | None = None
    verdict_reason: str | None = None
    vault_paths: tuple[str, ...] = ()
    transcript_source: str | None = None
    error: str | None = None
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> VideoRecord:
        raw_paths = row["vault_paths"]
        return cls(
            video_id=row["video_id"],
            channel_name=row["channel_name"],
            channel_id=row["channel_id"],
            title=row["title"],
            published_at=row["published_at"],
            duration_s=row["duration_s"],
            first_seen_at=row["first_seen_at"],
            status=row["status"],
            verdict=row["verdict"],
            verdict_reason=row["verdict_reason"],
            vault_paths=tuple(json.loads(raw_paths)) if raw_paths else (),
            transcript_source=row["transcript_source"],
            error=row["error"],
            updated_at=row["updated_at"],
        )


class EventConflict(ValueError):
    """An event key already exists with different content: the same operation
    was replayed with a different payload. Never silently kept or dropped."""


class Registry:
    """Fachada fina sobre o SQLite. Uma instância = uma conexão."""

    def __init__(self, conn: sqlite3.Connection, *, actor: str | None = None,
                 readonly: bool = False) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self.actor = actor or os.environ.get(ACTOR_ENV) or "yt-nota"
        self.run_id = current_run_id()
        self._retrospective = False  # set by the backfill: rows describe the past
        self.readonly = readonly
        if readonly:
            # Read-only opens (dry runs) never create, migrate or stamp anything.
            return
        self._conn.executescript(SCHEMA)
        self._conn.executescript(EVENTS_SCHEMA)
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(events)")}
        if "payload_sha256" not in cols:  # events table from an earlier dev build
            self._conn.execute(
                "ALTER TABLE events ADD COLUMN payload_sha256 TEXT NOT NULL DEFAULT ''")
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        # Idempotent migration 1 -> 2: the events table above is additive and
        # `videos` is untouched, so only the version marker moves.
        self._conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version' "
            "AND CAST(value AS INTEGER) < CAST(? AS INTEGER)",
            (SCHEMA_VERSION, SCHEMA_VERSION),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            (COVERAGE_KEY_D1, _now()),
        )
        self._conn.commit()

    # -- eventos (B-09) ------------------------------------------------------

    def record_event(
        self,
        case_id: str,
        activity: str,
        lifecycle: str,
        *,
        kind: str = "activity",
        occurred_at: str | None = None,
        attempt_id: str | None = None,
        decided_by: str | None = None,
        attrs: dict | None = None,
        actor: str | None = None,
        objects: Iterable[tuple[str, str, str]] = (),
        op_id: str | None = None,
    ) -> bool:
        """Append one event and commit. Returns False when the same operation was
        already recorded with the same content; raises EventConflict when the key
        exists with different content.

        `objects` are extra (object_type, object_id, qualifier) links; the case's
        `source` object is always linked with qualifier `case`."""
        if self.readonly:
            raise sqlite3.OperationalError("registry opened read-only")
        try:
            inserted = self._insert_event(
                case_id, activity, lifecycle, kind=kind, occurred_at=occurred_at,
                attempt_id=attempt_id, decided_by=decided_by, attrs=attrs, actor=actor,
                objects=objects, op_id=op_id,
            )
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        return inserted

    def _insert_event(
        self,
        case_id: str,
        activity: str,
        lifecycle: str,
        *,
        kind: str,
        occurred_at: str | None,
        attempt_id: str | None,
        decided_by: str | None,
        attrs: dict | None,
        actor: str | None,
        objects: Iterable[tuple[str, str, str]] = (),
        op_id: str | None = None,
    ) -> bool:
        """INSERT without commit, so callers can keep it in their transaction.

        The key identifies the OPERATION, never the clock: `op_id` when the
        caller has one (a replay of the same operation dedupes), else the
        attempt and lifecycle for activities, else a fresh id, so two decisions
        in the same second are two events. A replayed key with a different
        payload raises EventConflict."""
        links = [("source", case_id, "case"), *objects]
        for otype, oid, qual in links:
            if otype not in OBJECT_TYPES or not oid or not qual:
                raise ValueError(f"objeto inválido: {(otype, oid, qual)!r}")
        if kind not in EVENT_KINDS:
            raise ValueError(f"kind inválido: {kind!r}")
        if lifecycle not in LIFECYCLES:
            raise ValueError(f"lifecycle inválido: {lifecycle!r}")
        if decided_by is not None and decided_by not in DECIDERS:
            raise ValueError(f"decided_by inválido: {decided_by!r}")
        occurred = occurred_at or _now()
        attrs = dict(attrs or {})
        if self._retrospective:
            attrs["retrospective"] = True
        who = actor or ("backfill" if self._retrospective else self.actor)
        if op_id:
            key = f"{kind}|{case_id}|op:{op_id}"
        elif attempt_id:
            key = f"{kind}|{case_id}|{activity}|{lifecycle}|{attempt_id}"
        else:
            key = f"{kind}|{case_id}|{uuid4().hex}"
        attrs_json = json.dumps(attrs, ensure_ascii=False, sort_keys=True)
        # Content of the operation, without the times a replay would recompute.
        payload = json.dumps([kind, case_id, activity, lifecycle, decided_by, attrs,
                              sorted(map(list, links))], ensure_ascii=False, sort_keys=True)
        payload_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO events (event_key, kind, case_id, activity, lifecycle, "
            "occurred_at, recorded_at, run_id, attempt_id, actor, decided_by, attrs, "
            "payload_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, kind, case_id, activity, lifecycle, occurred, _now(), self.run_id,
             attempt_id, who, decided_by, attrs_json, payload_sha),
        )
        if cur.rowcount == 0:
            stored = self._conn.execute(
                "SELECT payload_sha256 FROM events WHERE event_key = ?", (key,)).fetchone()
            if stored is not None and stored["payload_sha256"] != payload_sha:
                raise EventConflict(f"event key {key!r} replayed with different content")
            return False
        self._conn.executemany(
            "INSERT OR IGNORE INTO event_objects (event_id, object_type, object_id, qualifier) "
            "VALUES (?, ?, ?, ?)",
            [(cur.lastrowid, t, i, q) for t, i, q in links],
        )
        return True

    def _insert_transition(self, case_id: str, frm: str | None, to: str,
                           occurred_at: str | None, extra: dict,
                           op_id: str | None = None) -> None:
        attrs = {"from": frm, "to": to, **{k: v for k, v in extra.items() if v is not None}}
        self._insert_event(case_id, "status", "complete", kind="transition",
                           occurred_at=occurred_at, attempt_id=None, decided_by=None,
                           attrs=attrs, actor=None, op_id=op_id)

    def _begin_write(self) -> None:
        """Take the write lock BEFORE reading the state a write depends on, so no
        other process can change it in between (B-09: a stale `from`)."""
        if self.readonly:
            raise sqlite3.OperationalError("registry opened read-only")
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _op_payload(kind: str, content: dict) -> str:
        """Content of an operation, independent of the state it found."""
        clean = {k: v for k, v in content.items() if v is not None}
        return hashlib.sha256(json.dumps([kind, clean], ensure_ascii=False, sort_keys=True)
                              .encode("utf-8")).hexdigest()

    def _op_seen(self, video_id: str, op_id: str, payload_sha: str) -> bool:
        """True when this op_id was already applied with the same content: a
        resend is a no-op, never an undo. Same id, other content: EventConflict."""
        row = self._conn.execute(
            "SELECT payload_sha256 FROM operations WHERE op_key = ?",
            (f"{video_id}|{op_id}",)).fetchone()
        if row is None:
            return False
        if row["payload_sha256"] != payload_sha:
            raise EventConflict(f"operation {op_id!r} on {video_id!r} replayed with "
                                f"different content")
        return True

    def _op_record(self, video_id: str, op_id: str, payload_sha: str, changed: bool) -> None:
        self._conn.execute(
            "INSERT INTO operations (op_key, case_id, payload_sha256, applied_at, run_id, "
            "changed_state) VALUES (?, ?, ?, ?, ?, ?)",
            (f"{video_id}|{op_id}", video_id, payload_sha, _now(), self.run_id, int(changed)))

    def _current_status(self, video_id: str):
        return self._conn.execute(
            "SELECT status FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()

    def _objects_of(self, event_id: int) -> list[tuple[str, str, str]]:
        return [tuple(r) for r in self._conn.execute(
            "SELECT object_type, object_id, qualifier FROM event_objects "
            "WHERE event_id = ? ORDER BY object_type, object_id, qualifier", (event_id,))]

    def list_events(self, case_id: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM events", ()
        if case_id is not None:
            sql, params = sql + " WHERE case_id = ?", (case_id,)
        rows = self._conn.execute(sql + " ORDER BY occurred_at, event_id", params).fetchall()
        return [{**dict(r), "attrs": json.loads(r["attrs"]),
                 "objects": self._objects_of(r["event_id"])} for r in rows]

    def export_events_ocel2_json(self, out_path: Path | str) -> dict:
        """Write the log as OCEL 2.0 JSON (objectTypes, eventTypes, objects,
        events, each event with its object relationships). The event type is
        `activity:lifecycle` (transitions: `status:<to>`), so start and complete
        stay distinct events. Event attributes keep the scalar fields; nested
        attrs are serialized as JSON strings. Returns counts."""
        events = self.list_events()
        objects: dict[tuple[str, str], None] = {}
        etypes: dict[str, set[str]] = {}
        out_events = []
        for e in events:
            etype = (f"status:{e['attrs'].get('to')}" if e["kind"] == "transition"
                     else f"{e['activity']}:{e['lifecycle']}")
            attrs = {"kind": e["kind"], "case_id": e["case_id"], "run_id": e["run_id"],
                     "attempt_id": e["attempt_id"], "actor": e["actor"],
                     "decided_by": e["decided_by"], "recorded_at": e["recorded_at"]}
            for k, v in e["attrs"].items():
                attrs[f"attr_{k}"] = v if isinstance(v, (str, int, float, bool)) or v is None \
                    else json.dumps(v, ensure_ascii=False)
            attrs = {k: v for k, v in attrs.items() if v is not None}
            etypes.setdefault(etype, set()).update(attrs)
            rels = [{"objectId": f"{t}:{i}", "qualifier": q} for t, i, q in e["objects"]]
            for t, i, _ in e["objects"]:
                objects[(t, i)] = None
            out_events.append({
                "id": f"e{e['event_id']}", "type": etype, "time": e["occurred_at"],
                "attributes": [{"name": k, "value": v} for k, v in attrs.items()],
                "relationships": rels,
            })

        def _atype(v) -> str:
            return "integer" if isinstance(v, int) and not isinstance(v, bool) else \
                "float" if isinstance(v, float) else "boolean" if isinstance(v, bool) else "string"

        types_seen: dict[str, dict[str, str]] = {}
        for ev in out_events:
            for a in ev["attributes"]:
                types_seen.setdefault(ev["type"], {}).setdefault(a["name"], _atype(a["value"]))
        doc = {
            "objectTypes": [{"name": t, "attributes": []} for t in sorted(OBJECT_TYPES)],
            "eventTypes": [{"name": et, "attributes": [{"name": n, "type": ty}
                                                       for n, ty in sorted(types_seen.get(et, {}).items())]}
                           for et in sorted(etypes)],
            "objects": [{"id": f"{t}:{i}", "type": t, "attributes": [], "relationships": []}
                        for t, i in objects],
            "events": out_events,
        }
        Path(out_path).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"events": len(out_events), "objects": len(objects)}

    def export_events_csv(self, out_path: Path | str) -> int:
        """Write every event as CSV, process-mining columns first. Returns the count."""
        rows = self._conn.execute("SELECT * FROM events ORDER BY occurred_at, event_id")
        head = ["case_id", "activity", "lifecycle", "timestamp"] + [
            c for c in EVENT_COLUMNS if c not in ("case_id", "activity", "lifecycle")]
        n = 0
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(head)
            for r in rows.fetchall():
                w.writerow([r["case_id"], r["activity"], r["lifecycle"], r["occurred_at"]]
                           + [r[c] for c in head[4:]])
                n += 1
        return n

    # -- escrita ------------------------------------------------------------

    def mark_discovered(
        self,
        video_id: str,
        channel_name: str,
        *,
        channel_id: str | None = None,
        title: str | None = None,
        published_at: str | None = None,
        duration_s: int | None = None,
        occurred_at: str | None = None,
        op_id: str | None = None,
    ) -> bool:
        """Registra um vídeo visto no RSS. Retorna True se era novo.

        É a operação que torna a idempotência estrutural: chamada ANTES de qualquer
        download. Re-rodar uma fila inteira vira no-op, mesmo se a run anterior morreu
        no meio.

        A new row also writes its first transition (none -> descoberto) in the same
        transaction (B-09).
        """
        now = _now()
        try:
            self._begin_write()
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO videos (
                    video_id, channel_id, channel_name, title, published_at,
                    duration_s, first_seen_at, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    channel_id,
                    channel_name,
                    title,
                    published_at,
                    duration_s,
                    now,
                    STATUS_DESCOBERTO,
                    now,
                ),
            )
            created = cur.rowcount > 0
            if created:
                self._insert_transition(video_id, None, STATUS_DESCOBERTO,
                                        occurred_at or now, {"channel": channel_name},
                                        op_id=op_id)
            if op_id:
                op_sha = self._op_payload("discover", {"channel": channel_name})
                if not self._op_seen(video_id, op_id, op_sha):
                    self._op_record(video_id, op_id, op_sha, created)
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        return created

    def enrich(
        self,
        video_id: str,
        *,
        channel_id: str | None = None,
        title: str | None = None,
        published_at: str | None = None,
        duration_s: int | None = None,
    ) -> None:
        """Preenche metadado que falta, sem sobrescrever o que já existe.

        `mark_discovered` usa INSERT OR IGNORE, então metadado que chega depois da
        primeira vez (título vindo do RSS pra uma linha importada pelo backfill, que
        só tinha o id) nunca entraria. Isso resolve. COALESCE garante a assimetria:
        preenche buraco, nunca reescreve dado bom com um None.
        """
        sets = {
            k: v
            for k, v in (
                ("channel_id", channel_id),
                ("title", title),
                ("published_at", published_at),
                ("duration_s", duration_s),
            )
            if v is not None
        }
        if not sets:
            return
        assigns = ", ".join(f"{k} = COALESCE({k}, ?)" for k in sets)
        cur = self._conn.execute(
            f"UPDATE videos SET {assigns}, updated_at = ? WHERE video_id = ?",
            (*sets.values(), _now(), video_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"video_id não registrado: {video_id}")
        self._conn.commit()

    def mark_downloaded(
        self,
        video_id: str,
        *,
        transcript_source: str | None = None,
        occurred_at: str | None = None,
        evidence: dict | None = None,
        op_id: str | None = None,
    ) -> None:
        """`evidence` (path, hash, mtime of the file that proves the download)
        goes into the transition as evidence; it never becomes `occurred_at`,
        because a file's mtime can be an edit, a copy or a restore."""
        self._update(video_id, occurred_at=occurred_at, op_id=op_id,
                     extra_attrs={"evidence": evidence} if evidence else None,
                     status=STATUS_BAIXADO, transcript_source=transcript_source)

    def mark_curated(
        self,
        video_id: str,
        verdict: str,
        *,
        reason: str | None = None,
        vault_paths: Iterable[str] = (),
        occurred_at: str | None = None,
        op_id: str | None = None,
    ) -> None:
        if verdict not in VALID_VERDICTS:
            raise ValueError(
                f"Veredicto inválido: {verdict!r}. Esperado um de {sorted(VALID_VERDICTS)}"
            )
        self._update(
            video_id,
            occurred_at=occurred_at,
            op_id=op_id,
            status=STATUS_CURADO,
            verdict=verdict,
            verdict_reason=reason,
            vault_paths=json.dumps(list(vault_paths), ensure_ascii=False),
        )

    def mark_error(self, video_id: str, error: str, *, occurred_at: str | None = None,
                   op_id: str | None = None) -> None:
        self._update(video_id, occurred_at=occurred_at, op_id=op_id, status=STATUS_ERRO,
                     error=error)

    # Fields copied into a transition's attrs when the update sets them.
    _TRANSITION_FIELDS = ("verdict", "verdict_reason", "vault_paths", "transcript_source", "error")

    def _update(self, video_id: str, *, occurred_at: str | None = None,
                op_id: str | None = None, extra_attrs: dict | None = None,
                **fields: object) -> None:
        """UPDATE parcial. Campos None são ignorados, exceto os explicitamente setáveis.

        When the status changes, or a verdict is (re)written, the transition is
        appended in the same transaction; a failure rolls both back (B-09). The
        write lock is taken BEFORE the current status is read, so the `from` of
        the transition is the status this update actually replaced."""
        sets = {k: v for k, v in fields.items() if v is not None}
        if not sets:
            return
        try:
            self._begin_write()
            row = self._current_status(video_id)
            if row is None:
                raise KeyError(f"video_id não registrado: {video_id}")
            op_sha = self._op_payload("update", {**sets, **(extra_attrs or {})}) if op_id else None
            if op_id and self._op_seen(video_id, op_id, op_sha):
                self._conn.commit()  # a resend of an operation already applied
                return
            sets["updated_at"] = _now()
            assigns = ", ".join(f"{k} = ?" for k in sets)
            self._conn.execute(
                f"UPDATE videos SET {assigns} WHERE video_id = ?",
                (*sets.values(), video_id),
            )
            new_status = sets.get("status")
            changed = bool(new_status and (new_status != row["status"] or "verdict" in sets))
            if changed:
                extra = {k: sets.get(k) for k in self._TRANSITION_FIELDS}
                if extra.get("vault_paths"):
                    extra["vault_paths"] = json.loads(extra["vault_paths"])
                extra.update(extra_attrs or {})
                self._insert_transition(video_id, row["status"], new_status,
                                        occurred_at, extra, op_id=op_id)
            if op_id:  # recorded even when the state did not change
                self._op_record(video_id, op_id, op_sha, changed)
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    # -- leitura ------------------------------------------------------------

    def is_known(self, video_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row is not None

    def known_ids(self, channel_name: str | None = None) -> set[str]:
        if channel_name is None:
            rows = self._conn.execute("SELECT video_id FROM videos")
        else:
            rows = self._conn.execute(
                "SELECT video_id FROM videos WHERE channel_name = ?", (channel_name,)
            )
        return {r["video_id"] for r in rows}

    def get(self, video_id: str) -> VideoRecord | None:
        row = self._conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return VideoRecord.from_row(row) if row else None

    def filter_new(self, video_ids: Iterable[str]) -> list[str]:
        """Dos IDs dados, os que o registro ainda não conhece (ordem preservada)."""
        ids = list(video_ids)
        if not ids:
            return []
        known = set()
        # SQLite tem teto de variáveis por statement (999 no default histórico).
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start : chunk_start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT video_id FROM videos WHERE video_id IN ({placeholders})", chunk
            )
            known.update(r["video_id"] for r in rows)
        return [v for v in ids if v not in known]

    def counts_by_channel(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT channel_name, COUNT(*) AS n FROM videos GROUP BY channel_name ORDER BY n DESC"
        )
        return {r["channel_name"]: r["n"] for r in rows}

    def counts_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM videos GROUP BY status ORDER BY n DESC"
        )
        return {r["status"]: r["n"] for r in rows}

    def counts_by_verdict(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT verdict, COUNT(*) AS n FROM videos "
            "WHERE verdict IS NOT NULL GROUP BY verdict ORDER BY n DESC"
        )
        return {r["verdict"]: r["n"] for r in rows}

    def list_videos(
        self,
        *,
        channel_name: str | None = None,
        status: str | None = None,
        verdict: str | None = None,
        limit: int | None = None,
    ) -> list[VideoRecord]:
        where, params = [], []
        if channel_name:
            where.append("channel_name = ?")
            params.append(channel_name)
        if status:
            where.append("status = ?")
            params.append(status)
        if verdict:
            where.append("verdict = ?")
            params.append(verdict)
        sql = "SELECT * FROM videos"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(published_at, first_seen_at) DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [VideoRecord.from_row(r) for r in self._conn.execute(sql, params)]

    def total(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone()["n"]

    def close(self) -> None:
        self._conn.close()


@contextmanager
def open_registry(db_path: Path | str | None = None, *, actor: str | None = None,
                  readonly: bool = False) -> Iterator[Registry]:
    """Abre (e cria, se preciso) o registro. Fecha ao sair do bloco.

    `":memory:"` é aceito para testes. `actor` names the step writing events.
    `readonly=True` opens the file in SQLite read-only mode and skips schema
    creation and migration, so a dry run leaves the database byte-identical.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    if readonly and str(db_path) != ":memory:" and not Path(db_path).exists():
        # A read-only open must never create the file: read an empty in-memory
        # registry instead, and keep it refusing writes.
        registry = Registry(sqlite3.connect(":memory:"), actor=actor)
        registry.readonly = True
    elif readonly:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
        registry = Registry(conn, actor=actor, readonly=True)
    else:
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        registry = Registry(conn, actor=actor)
    try:
        yield registry
    finally:
        registry.close()


def backfill_from_processados(
    registry: Registry, processados: dict[str, object]
) -> tuple[int, int]:
    """Importa o `processados.json` legado. Retorna (inseridos, ignorados).

    O JSON só tem `{canal: [video_id, ...]}` — nenhum veredicto, nenhuma data, e o
    próprio comentário dele admite misturar "baixado, fichado ou pulado de propósito".
    Por isso tudo entra como `historico`, e NÃO como `curado`: afirmar que foram
    curados seria inventar procedência que o dado não tem.

    Idempotente — rodar duas vezes não duplica nem sobrescreve linhas reais.
    """
    inserted = skipped = 0
    registry._retrospective = True  # B-09: these transitions describe the past
    try:
        for channel_name, video_ids in processados.items():
            if channel_name.startswith("_") or not isinstance(video_ids, list):
                continue  # `_comment` e afins
            for video_id in video_ids:
                if not isinstance(video_id, str) or not video_id:
                    continue
                if registry.is_known(video_id):
                    skipped += 1
                    continue
                registry.mark_discovered(video_id, channel_name)
                registry._update(video_id, status=STATUS_HISTORICO)
                inserted += 1
    finally:
        registry._retrospective = False
    return inserted, skipped

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

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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

SCHEMA_VERSION = "1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


class Registry:
    """Fachada fina sobre o SQLite. Uma instância = uma conexão."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self._conn.commit()

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
    ) -> bool:
        """Registra um vídeo visto no RSS. Retorna True se era novo.

        É a operação que torna a idempotência estrutural: chamada ANTES de qualquer
        download. Re-rodar uma fila inteira vira no-op, mesmo se a run anterior morreu
        no meio.
        """
        now = _now()
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
        self._conn.commit()
        return cur.rowcount > 0

    def mark_downloaded(self, video_id: str, *, transcript_source: str | None = None) -> None:
        self._update(video_id, status=STATUS_BAIXADO, transcript_source=transcript_source)

    def mark_curated(
        self,
        video_id: str,
        verdict: str,
        *,
        reason: str | None = None,
        vault_paths: Iterable[str] = (),
    ) -> None:
        if verdict not in VALID_VERDICTS:
            raise ValueError(
                f"Veredicto inválido: {verdict!r}. Esperado um de {sorted(VALID_VERDICTS)}"
            )
        self._update(
            video_id,
            status=STATUS_CURADO,
            verdict=verdict,
            verdict_reason=reason,
            vault_paths=json.dumps(list(vault_paths), ensure_ascii=False),
        )

    def mark_error(self, video_id: str, error: str) -> None:
        self._update(video_id, status=STATUS_ERRO, error=error)

    def _update(self, video_id: str, **fields: object) -> None:
        """UPDATE parcial. Campos None são ignorados, exceto os explicitamente setáveis."""
        sets = {k: v for k, v in fields.items() if v is not None}
        if not sets:
            return
        sets["updated_at"] = _now()
        assigns = ", ".join(f"{k} = ?" for k in sets)
        cur = self._conn.execute(
            f"UPDATE videos SET {assigns} WHERE video_id = ?",
            (*sets.values(), video_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"video_id não registrado: {video_id}")
        self._conn.commit()

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
def open_registry(db_path: Path | str | None = None) -> Iterator[Registry]:
    """Abre (e cria, se preciso) o registro. Fecha ao sair do bloco.

    `":memory:"` é aceito para testes.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    registry = Registry(conn)
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
    return inserted, skipped

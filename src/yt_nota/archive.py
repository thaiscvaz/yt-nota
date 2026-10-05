"""Arquivo de drafts que saem de `_processar/`.

Um draft nunca é apagado: quando recebe veredicto, é copiado para
`<ARCHIVE_DIR>/<VEREDICTO>/`, a cópia é conferida por hash e só então o original
sai do vault. Qualquer falha no caminho deixa o draft onde estava.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import config


class ArchiveError(OSError):
    """A cópia não pôde ser criada ou conferida; o draft original foi preservado."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_draft(draft: Path, verdict: str) -> Path:
    """Move `draft` para o arquivo do veredicto e devolve o path da cópia.

    Ordem fixa: copiar, conferir o hash da cópia, remover o original. Um destino
    que já existe com outro conteúdo nunca é sobrescrito: a cópia ganha sufixo.
    """
    if not draft.is_file():
        raise ArchiveError(f"Draft não encontrado: {draft}")
    source_hash = _sha256(draft)
    target_dir = config.ARCHIVE_DIR / verdict
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / draft.name
        n = 2
        while target.exists() and _sha256(target) != source_hash:
            target = target_dir / f"{draft.name.removesuffix('.md')}-{n}.md"
            n += 1
        if not target.exists():
            target.write_bytes(draft.read_bytes())
        if _sha256(target) != source_hash:
            raise ArchiveError(f"Cópia não confere com o original: {target}")
    except ArchiveError:
        raise
    except OSError as e:
        raise ArchiveError(f"Falha ao arquivar {draft.name}: {e}") from e
    # Only now does the draft leave the vault.
    draft.unlink()
    return target

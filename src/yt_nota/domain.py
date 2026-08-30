"""Resolução de domínio do vault (7 domínios temáticos) pra cada canal.

Lookup carrega `config/channel_domains.yaml` (pessoal, não versionado).
Se não existe, fallback pra `channel_domains.example.yaml` (versionado, só
canais públicos amplamente conhecidos).
Override programático via CLI flag `--dominio` ou frontmatter `dominio:` do draft.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

import yaml

from . import config
from .config import DOMAINS_CONFIG_PATH


class DomainResolutionError(Exception):
    """Canal sem domínio mapeado e sem override explícito."""


@lru_cache(maxsize=1)
def _load_mapping() -> dict[str, str]:
    # Prioriza o YAML pessoal do usuário (não versionado); cai no .example
    # (versionado, só canais neutros) se o primeiro não existe.
    candidates = [
        DOMAINS_CONFIG_PATH,
        DOMAINS_CONFIG_PATH.with_name(DOMAINS_CONFIG_PATH.stem + ".example.yaml"),
    ]
    for path in candidates:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # Index both the original slug and a lowercase alias so the lookup
            # tolerates yt-dlp returning the channel in a different case
            # (e.g. mapping has 'PasquaDev', yt-dlp yields 'pasquadev').
            mapping: dict[str, str] = {}
            for k, v in data.items():
                mapping[str(k)] = str(v)
                mapping.setdefault(str(k).lower(), str(v))
            return mapping
    return {}


def validate(dominio: str) -> str:
    valid = config.get_valid_dominios()
    if dominio not in valid:
        raise DomainResolutionError(
            f"Domínio inválido: '{dominio}'. "
            f"Válidos: {sorted(valid)}"
        )
    return dominio


def resolve(canal_slug: str, override: Optional[str] = None) -> str:
    """Resolve domínio em cascata: override > YAML lookup > erro.

    A skill `/yt-sintese` ou `cli.write_draft` pode passar override (vindo de
    flag `--dominio` ou frontmatter já preenchido). Sem override, lookup no YAML.
    Se nada bater, dispara DomainResolutionError com instrução clara.
    """
    if override:
        return validate(override)

    mapping = _load_mapping()
    dominio = mapping.get(canal_slug) or mapping.get(canal_slug.lower())
    if dominio:
        return validate(dominio)

    raise DomainResolutionError(
        f"Canal '{canal_slug}' não tem domínio mapeado em "
        f"{DOMAINS_CONFIG_PATH.name}. Solução:\n"
        f"  1. Adicionar '{canal_slug}: <dominio>' em config/channel_domains.yaml, ou\n"
        f"  2. Rodar novamente com --dominio <DOMINIO>\n"
        f"Domínios válidos: {sorted(config.get_valid_dominios())}"
    )


def reload_mapping() -> None:
    """Limpa cache do mapping (útil em testes ou após edição manual do YAML)."""
    # Defensivo: testes podem monkeypatchar `_load_mapping` por uma função plain
    # (sem o decorator lru_cache), nesse caso `cache_clear` não existe.
    clear = getattr(_load_mapping, "cache_clear", None)
    if clear is not None:
        clear()

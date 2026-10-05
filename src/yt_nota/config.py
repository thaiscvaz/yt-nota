import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

# No default: a silent fallback path would write drafts somewhere unexpected.
# The CLI refuses vault-touching modes while this is unset (see cli.main).
VAULT_ENV_VAR = "YT_NOTA_VAULT"
VAULT_CONFIGURED = bool(os.environ.get(VAULT_ENV_VAR, "").strip())
VAULT_PATH = Path(os.environ.get(VAULT_ENV_VAR, "").strip() or ".")

RECURSOS_DIR = VAULT_PATH / "30-Recursos"

# Drafts pendentes de síntese (Pipeline/_processar/)
PROCESSAR_DIR = RECURSOS_DIR / "Pipeline" / "_processar"

# Cards vivos por canal (domínio Pessoas/, estrutura domain-first 2026-06)
CARDS_DE_PESSOA_DIR = RECURSOS_DIR / "Pessoas"

# Legacy processados.json, read only by `--registry backfill`. Its default lives
# inside the vault; override when the automation folder sits elsewhere (e.g. a
# second vault).
LEGACY_PROCESSADOS_PATH = Path(
    os.environ.get("YT_NOTA_PROCESSADOS", "").strip()
    or VAULT_PATH / "Claude" / "automacoes" / "curadoria-incremental" / "data" / "processados.json"
)

# Paths resolvidos em runtime via PACKAGE_ROOT.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent

# Drafts que saem de _processar/ com veredicto são arquivados aqui, fora do
# vault, e nunca apagados (ver archive.py). Uma subpasta por veredicto.
ARCHIVE_ENV_VAR = "YT_NOTA_ARCHIVE"
ARCHIVE_DIR = Path(
    os.environ.get(ARCHIVE_ENV_VAR, "").strip() or PACKAGE_ROOT / "_archive" / "drafts"
)

# YAML com a taxonomia de domínios válidos. Pessoal (não versionado); cai no
# .example.yaml (genérico, versionado) se o pessoal não existe.
DOMINIOS_CONFIG_PATH = PACKAGE_ROOT / "config" / "domains.yaml"

# YAML com mapping canal_slug → domínio. Path resolvido em runtime via PACKAGE_ROOT.
DOMAINS_CONFIG_PATH = PACKAGE_ROOT / "config" / "channel_domains.yaml"

# YAML com aliases de canal: slug derivado do nome no YouTube → slug canônico
# da pasta no vault. Permite renomear pasta no vault sem depender do nome do canal.
ALIASES_CONFIG_PATH = PACKAGE_ROOT / "config" / "channel_aliases.yaml"


@lru_cache(maxsize=1)
def get_valid_dominios() -> frozenset[str]:
    """Conjunto de domínios válidos do vault.

    Cascata (espelha domain._load_mapping):
      1. config/domains.yaml — pessoal, não versionado (taxonomia real do usuário).
      2. config/domains.example.yaml — versionado, genérico (fallback).
    Formato esperado: lista YAML de strings. Cacheado; use reload_dominios()
    após editar o YAML em runtime (ou em testes).
    """
    candidates = [
        DOMINIOS_CONFIG_PATH,
        DOMINIOS_CONFIG_PATH.with_name("domains.example.yaml"),
    ]
    for path in candidates:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or []
            return frozenset(str(d) for d in data)
    return frozenset()


def reload_dominios() -> None:
    """Limpa o cache de get_valid_dominios (útil em testes ou após editar o YAML)."""
    get_valid_dominios.cache_clear()

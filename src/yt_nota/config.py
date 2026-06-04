import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VAULT_PATH = Path(
    os.environ.get(
        "YT_NOTA_VAULT",
        r"<user-home>\<cloud-storage>\Documentos\Obsidian",
    )
)

LITERATURA_DIR = VAULT_PATH / "30-Recursos" / "Literatura"
NOTAS_DIR = VAULT_PATH / "30-Recursos" / "Notas"

# Drafts pendentes de síntese (vault Obsidian Fase A+B, 2026-06-04)
PROCESSAR_DIR = LITERATURA_DIR / "Pipeline" / "_processar"

# Cards vivos por canal (subdomínio Notas/Cards-de-Pessoa/)
CARDS_DE_PESSOA_DIR = NOTAS_DIR / "Cards-de-Pessoa"

# Domínios válidos (REGRAS-VAULT seção 3)
VALID_DOMINIOS = frozenset({
    "IA-Engenharia",
    "Financas",
    "Saude",
    "Carreira",
    "Impressao-3D",
    "Metodo",
    "Mestrado",
})

# YAML com mapping canal_slug → domínio. Path resolvido em runtime via PACKAGE_ROOT.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
DOMAINS_CONFIG_PATH = PACKAGE_ROOT / "config" / "channel_domains.yaml"

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VAULT_PATH = Path(
    os.environ.get(
        "YT_NOTA_VAULT",
        r"C:\Users\thais\OneDrive\Documentos\Obsidian",
    )
)

LITERATURA_DIR = VAULT_PATH / "30-Recursos" / "Literatura"
NOTAS_DIR = VAULT_PATH / "30-Recursos" / "Notas"
DRAFTS_DIR = LITERATURA_DIR / "_drafts"

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

MODELS = {
    "opus": "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
}

DEFAULT_MODEL_ALIAS = "opus"
DEFAULT_MODEL = os.environ.get("YT_NOTA_MODEL", MODELS[DEFAULT_MODEL_ALIAS])


def get_anthropic_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY não está configurado. "
            "Copie .env.example pra .env e cole sua chave da Anthropic."
        )
    return key


def resolve_model(alias_or_id: str) -> str:
    return MODELS.get(alias_or_id, alias_or_id)

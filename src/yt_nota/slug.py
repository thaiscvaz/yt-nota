import re
import unicodedata
from functools import lru_cache

import yaml

from .config import ALIASES_CONFIG_PATH


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)
    )


@lru_cache(maxsize=1)
def _load_aliases() -> dict[str, str]:
    """Optional mapping derived-slug → canonical vault folder slug.

    Mirrors domain._load_mapping: personal YAML first, .example fallback.
    Missing files mean no aliases — channel_slug stays pure derivation.
    """
    candidates = [
        ALIASES_CONFIG_PATH,
        ALIASES_CONFIG_PATH.with_name(ALIASES_CONFIG_PATH.stem + ".example.yaml"),
    ]
    for path in candidates:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return {str(k): str(v) for k, v in data.items()}
    return {}


def channel_slug(name: str) -> str:
    """Channel display name → folder/file name. Preserves capitalization.

    Applies channel_aliases.yaml at the end so a vault folder rename does not
    depend on the channel display name on YouTube.
    """
    if not name:
        return "Unknown"
    s = _strip_accents(name).strip()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    s = s or "Unknown"
    return _load_aliases().get(s, s)


def title_slug(title: str, max_words: int = 6) -> str:
    """Title → URL-safe slug. Lowercase, hyphens, max N words."""
    if not title:
        return "sem-titulo"
    s = _strip_accents(title).lower().strip()
    s = re.sub(r"[^\w\s-]", " ", s, flags=re.UNICODE)
    words = [w for w in re.split(r"\s+", s) if w][:max_words]
    return "-".join(words) or "sem-titulo"

import re
import unicodedata


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)
    )


def channel_slug(name: str) -> str:
    """Channel display name → folder/file name. Preserves capitalization."""
    if not name:
        return "Unknown"
    s = _strip_accents(name).strip()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    return s or "Unknown"


def title_slug(title: str, max_words: int = 6) -> str:
    """Title → URL-safe slug. Lowercase, hyphens, max N words."""
    if not title:
        return "sem-titulo"
    s = _strip_accents(title).lower().strip()
    s = re.sub(r"[^\w\s-]", " ", s, flags=re.UNICODE)
    words = [w for w in re.split(r"\s+", s) if w][:max_words]
    return "-".join(words) or "sem-titulo"

import yt_nota.slug as slug_module
from yt_nota.slug import channel_slug, title_slug


def test_channel_slug_basic():
    assert channel_slug("Tech Reviewer") == "Tech-Reviewer"


def test_channel_slug_with_accents():
    assert channel_slug("REDACTED-CHANNEL") == "REDACTED-CHANNEL"
    assert channel_slug("Educação Financeira") == "Educacao-Financeira"


def test_channel_slug_special_chars():
    assert channel_slug("@1MinuteAI") == "1MinuteAI"
    assert channel_slug("SampleChannel - YouTube") == "SampleChannel-YouTube"


def test_channel_slug_empty():
    assert channel_slug("") == "Unknown"
    assert channel_slug(None) == "Unknown"


def test_title_slug_basic():
    assert title_slug("Hello World") == "hello-world"


def test_title_slug_max_words():
    assert title_slug("um dois tres quatro cinco seis sete oito", max_words=4) == "um-dois-tres-quatro"


def test_title_slug_strips_accents():
    assert title_slug("Programação Funcional") == "programacao-funcional"


def test_title_slug_special_chars():
    assert title_slug("AI: The Future?!") == "ai-the-future"


def test_title_slug_empty():
    assert title_slug("") == "sem-titulo"
    assert title_slug("   ") == "sem-titulo"


def test_title_slug_only_special():
    assert title_slug("!!!???") == "sem-titulo"


def test_channel_slug_applies_alias(monkeypatch):
    monkeypatch.setattr(
        slug_module, "_load_aliases", lambda: {"Maker-Lab-Robotica-3D": "Maker-Lab"}
    )
    assert channel_slug("Maker Lab Robótica 3D") == "Maker-Lab"


def test_channel_slug_without_alias_keeps_derivation(monkeypatch):
    monkeypatch.setattr(slug_module, "_load_aliases", lambda: {})
    assert channel_slug("Maker Lab Robótica 3D") == "Maker-Lab-Robotica-3D"


def test_load_aliases_missing_files_returns_empty(monkeypatch, tmp_path):
    slug_module._load_aliases.cache_clear()
    monkeypatch.setattr(
        slug_module, "ALIASES_CONFIG_PATH", tmp_path / "channel_aliases.yaml"
    )
    try:
        assert slug_module._load_aliases() == {}
    finally:
        slug_module._load_aliases.cache_clear()

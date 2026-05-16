from yt_nota.slug import channel_slug, title_slug


def test_channel_slug_basic():
    assert channel_slug("Fabio Akita") == "Fabio-Akita"


def test_channel_slug_with_accents():
    assert channel_slug("Cara da Riqueza") == "Cara-da-Riqueza"
    assert channel_slug("Educação Financeira") == "Educacao-Financeira"


def test_channel_slug_special_chars():
    assert channel_slug("@1MinuteAI") == "1MinuteAI"
    assert channel_slug("AkitaOnRails - YouTube") == "AkitaOnRails-YouTube"


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

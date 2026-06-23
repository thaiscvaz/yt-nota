"""Shared test fixtures.

Decouples the whole suite from the user's personal vault taxonomy: an autouse
fixture points `config.DOMINIOS_CONFIG_PATH` at a temp file holding a neutral
domain set, so tests exercise the real `get_valid_dominios()` cascade without
depending on `config/domains.yaml` (personal) or `config/domains.example.yaml`.
"""

import pytest

from yt_nota import config

# Neutral domains used across the suite. Includes the values asserted in
# test_vault.py ("Tech", "Career").
TEST_DOMINIOS = (
    "Tech",
    "Finance",
    "Health",
    "Career",
    "Hobby",
    "Productivity",
    "Research",
)


@pytest.fixture(autouse=True)
def stub_valid_dominios(tmp_path_factory, monkeypatch):
    """Point the domain loader at a temp file with neutral domains for every test."""
    cfg = tmp_path_factory.mktemp("domcfg") / "domains.yaml"
    cfg.write_text(
        "\n".join(f"- {d}" for d in TEST_DOMINIOS) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "DOMINIOS_CONFIG_PATH", cfg)
    config.reload_dominios()
    yield
    config.reload_dominios()

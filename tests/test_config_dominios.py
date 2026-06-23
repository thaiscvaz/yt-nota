"""Tests for the configurable domain taxonomy loader (config.get_valid_dominios).

Cascade: personal `domains.yaml` -> committed `domains.example.yaml`.
"""

from yt_nota import config


def _write(path, domains):
    path.write_text("\n".join(f"- {d}" for d in domains) + "\n", encoding="utf-8")


def test_loads_personal_domains_yaml(tmp_path, monkeypatch):
    personal = tmp_path / "domains.yaml"
    _write(personal, ["Alpha", "Beta"])
    monkeypatch.setattr(config, "DOMINIOS_CONFIG_PATH", personal)
    config.reload_dominios()

    assert config.get_valid_dominios() == frozenset({"Alpha", "Beta"})


def test_falls_back_to_example_when_personal_absent(tmp_path, monkeypatch):
    personal = tmp_path / "domains.yaml"  # not created
    example = tmp_path / "domains.example.yaml"
    _write(example, ["Generic1", "Generic2", "Generic3"])
    monkeypatch.setattr(config, "DOMINIOS_CONFIG_PATH", personal)
    config.reload_dominios()

    assert config.get_valid_dominios() == frozenset({"Generic1", "Generic2", "Generic3"})


def test_returns_empty_when_no_config_present(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOMINIOS_CONFIG_PATH", tmp_path / "domains.yaml")
    config.reload_dominios()

    assert config.get_valid_dominios() == frozenset()


def test_reload_clears_cache(tmp_path, monkeypatch):
    personal = tmp_path / "domains.yaml"
    _write(personal, ["One"])
    monkeypatch.setattr(config, "DOMINIOS_CONFIG_PATH", personal)
    config.reload_dominios()
    assert config.get_valid_dominios() == frozenset({"One"})

    _write(personal, ["One", "Two"])
    assert config.get_valid_dominios() == frozenset({"One"})  # still cached
    config.reload_dominios()
    assert config.get_valid_dominios() == frozenset({"One", "Two"})

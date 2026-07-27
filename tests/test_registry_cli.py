"""Testes dos verbos `--registry` do CLI.

Separado de `test_registry.py` de propósito: lá é a camada de dados, aqui é o
contrato de linha de comando que a skill `/curadoria-incremental` consome.
"""

from __future__ import annotations

import argparse

from yt_nota import cli
from yt_nota.registry import (
    STATUS_BAIXADO,
    STATUS_CURADO,
    STATUS_DESCOBERTO,
    STATUS_ERRO,
    VERDICT_DESCARTE,
    open_registry,
)


def _args(db, **overrides) -> argparse.Namespace:
    base = dict(
        registry=None,
        db=str(db),
        channel=None,
        status=None,
        verdict=None,
        reason=None,
        ids=None,
        limit=None,
        from_file=None,
        dry_run=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# -- filter ------------------------------------------------------------------


def test_filter_imprime_so_os_desconhecidos(tmp_path, capsys):
    db = tmp_path / "r.db"
    with open_registry(db) as reg:
        reg.mark_discovered("b", "Canal")

    rc = cli._cmd_registry(_args(db, registry="filter", ids=["a", "b", "c"]))

    assert rc == 0
    assert capsys.readouterr().out.split() == ["a", "c"]


def test_filter_aceita_ids_separados_por_virgula(tmp_path, capsys):
    db = tmp_path / "r.db"
    rc = cli._cmd_registry(_args(db, registry="filter", ids=["a,b", "c"]))
    assert rc == 0
    assert capsys.readouterr().out.split() == ["a", "b", "c"]


def test_filter_nao_registra_nada(tmp_path):
    """`filter` é consulta pura: não pode criar linha como efeito colateral."""
    db = tmp_path / "r.db"
    cli._cmd_registry(_args(db, registry="filter", ids=["a", "b"]))
    with open_registry(db) as reg:
        assert reg.total() == 0


def test_filter_sem_entrada_falha(tmp_path):
    assert cli._cmd_registry(_args(tmp_path / "r.db", registry="filter")) == 1


# -- mark --------------------------------------------------------------------


def test_mark_default_e_descoberto(tmp_path):
    db = tmp_path / "r.db"
    rc = cli._cmd_registry(_args(db, registry="mark", channel="Anthropic", ids=["a"]))
    assert rc == 0
    with open_registry(db) as reg:
        assert reg.get("a").status == STATUS_DESCOBERTO
        assert reg.get("a").channel_name == "Anthropic"


def test_mark_cria_a_linha_se_nao_existir(tmp_path):
    """A skill marca `baixado` sem ter marcado `descoberto` antes — não pode dar KeyError."""
    db = tmp_path / "r.db"
    rc = cli._cmd_registry(
        _args(db, registry="mark", channel="Claude", status=STATUS_BAIXADO, ids=["novo"])
    )
    assert rc == 0
    with open_registry(db) as reg:
        assert reg.get("novo").status == STATUS_BAIXADO


def test_mark_curado_exige_veredicto(tmp_path):
    """O buraco do processados.json era exatamente esse: curar sem dizer o quê."""
    db = tmp_path / "r.db"
    rc = cli._cmd_registry(
        _args(db, registry="mark", channel="C", status=STATUS_CURADO, ids=["a"])
    )
    assert rc == 1
    with open_registry(db) as reg:
        assert reg.total() == 0  # falhou antes de escrever


def test_mark_descarte_grava_a_razao(tmp_path):
    db = tmp_path / "r.db"
    rc = cli._cmd_registry(
        _args(
            db,
            registry="mark",
            channel="Canal-Exemplo",
            status=STATUS_CURADO,
            verdict=VERDICT_DESCARTE,
            reason="abaixo de min_minutes",
            ids=["a"],
        )
    )
    assert rc == 0
    with open_registry(db) as reg:
        rec = reg.get("a")
        assert rec.status == STATUS_CURADO
        assert rec.verdict == VERDICT_DESCARTE
        assert rec.verdict_reason == "abaixo de min_minutes"


def test_mark_erro_exige_razao(tmp_path):
    db = tmp_path / "r.db"
    rc = cli._cmd_registry(
        _args(db, registry="mark", channel="C", status=STATUS_ERRO, ids=["a"])
    )
    assert rc == 1


def test_mark_erro_com_razao(tmp_path):
    db = tmp_path / "r.db"
    rc = cli._cmd_registry(
        _args(
            db,
            registry="mark",
            channel="C",
            status=STATUS_ERRO,
            reason="429 depois do fallback",
            ids=["a"],
        )
    )
    assert rc == 0
    with open_registry(db) as reg:
        assert reg.get("a").error == "429 depois do fallback"


def test_mark_exige_channel(tmp_path):
    assert cli._cmd_registry(_args(tmp_path / "r.db", registry="mark", ids=["a"])) == 1


def test_mark_sem_entrada_falha(tmp_path):
    assert cli._cmd_registry(_args(tmp_path / "r.db", registry="mark", channel="C")) == 1


def test_mark_e_idempotente(tmp_path):
    db = tmp_path / "r.db"
    for _ in range(2):
        cli._cmd_registry(
            _args(db, registry="mark", channel="C", status=STATUS_BAIXADO, ids=["a"])
        )
    with open_registry(db) as reg:
        assert reg.total() == 1
        assert reg.get("a").status == STATUS_BAIXADO


def test_mark_nao_rebaixa_curado_para_descoberto(tmp_path):
    """Re-marcar `descoberto` um vídeo já curado não pode reverter o status."""
    db = tmp_path / "r.db"
    cli._cmd_registry(
        _args(
            db,
            registry="mark",
            channel="C",
            status=STATUS_CURADO,
            verdict=VERDICT_DESCARTE,
            reason="shorts",
            ids=["a"],
        )
    )
    cli._cmd_registry(_args(db, registry="mark", channel="C", ids=["a"]))
    with open_registry(db) as reg:
        assert reg.get("a").status == STATUS_CURADO


# -- --from-file -------------------------------------------------------------


def test_from_file_com_titulo_tabulado(tmp_path):
    db = tmp_path / "r.db"
    entrada = tmp_path / "ids.txt"
    entrada.write_text(
        "# comentário\n\na\tKeynote de abertura\nb\n", encoding="utf-8"
    )

    rc = cli._cmd_registry(
        _args(db, registry="mark", channel="Anthropic", from_file=str(entrada))
    )

    assert rc == 0
    with open_registry(db) as reg:
        assert reg.get("a").title == "Keynote de abertura"
        assert reg.get("b").title is None
        assert reg.total() == 2


def test_from_file_titulo_preenche_linha_ja_existente(tmp_path):
    """Linha veio do backfill sem título; o RSS traz o título numa run seguinte."""
    db = tmp_path / "r.db"
    with open_registry(db) as reg:
        reg.mark_discovered("a", "Anthropic")

    entrada = tmp_path / "ids.txt"
    entrada.write_text("a\tTítulo que faltava\n", encoding="utf-8")
    cli._cmd_registry(
        _args(db, registry="mark", channel="Anthropic", from_file=str(entrada))
    )

    with open_registry(db) as reg:
        assert reg.get("a").title == "Título que faltava"


def test_filter_aceita_from_file(tmp_path, capsys):
    db = tmp_path / "r.db"
    with open_registry(db) as reg:
        reg.mark_discovered("b", "C")
    entrada = tmp_path / "ids.txt"
    entrada.write_text("a\tT1\nb\tT2\nc\n", encoding="utf-8")

    cli._cmd_registry(_args(db, registry="filter", from_file=str(entrada)))

    assert capsys.readouterr().out.split() == ["a", "c"]

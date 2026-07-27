"""Testes do registro durável (registry.py)."""

from __future__ import annotations

import json

import pytest

from yt_nota.registry import (
    STATUS_BAIXADO,
    STATUS_CURADO,
    STATUS_DESCOBERTO,
    STATUS_ERRO,
    STATUS_HISTORICO,
    VERDICT_DESCARTE,
    VERDICT_FICHAMENTO,
    VERDICT_PROPAGA,
    Registry,
    backfill_from_processados,
    open_registry,
)


@pytest.fixture
def reg():
    with open_registry(":memory:") as r:
        yield r


# -- ciclo de vida -----------------------------------------------------------


def test_mark_discovered_cria_linha_em_descoberto(reg: Registry):
    assert reg.mark_discovered("abc123", "Anthropic") is True
    rec = reg.get("abc123")
    assert rec is not None
    assert rec.status == STATUS_DESCOBERTO
    assert rec.channel_name == "Anthropic"
    assert rec.first_seen_at  # carimbado
    assert rec.verdict is None


def test_mark_discovered_e_idempotente(reg: Registry):
    assert reg.mark_discovered("abc123", "Anthropic") is True
    assert reg.mark_discovered("abc123", "Anthropic") is False
    assert reg.total() == 1


def test_discovered_nao_e_rebaixado_por_redescoberta(reg: Registry):
    """O bug de 2026-07-21: sessão morre depois do download, run seguinte re-baixa.

    Redescobrir um vídeo já curado não pode reverter o status.
    """
    reg.mark_discovered("abc123", "Anthropic")
    reg.mark_curated("abc123", VERDICT_FICHAMENTO, reason="keynote densa")

    reg.mark_discovered("abc123", "Anthropic")  # RSS traz de novo

    rec = reg.get("abc123")
    assert rec.status == STATUS_CURADO
    assert rec.verdict == VERDICT_FICHAMENTO


def test_fluxo_completo_descoberto_baixado_curado(reg: Registry):
    reg.mark_discovered("v1", "Claude", title="Título", duration_s=900)
    reg.mark_downloaded("v1", transcript_source="youtube_captions")
    assert reg.get("v1").status == STATUS_BAIXADO
    assert reg.get("v1").transcript_source == "youtube_captions"

    reg.mark_curated(
        "v1", VERDICT_PROPAGA, reason="refina nota existente", vault_paths=["a.md", "b.md"]
    )
    rec = reg.get("v1")
    assert rec.status == STATUS_CURADO
    assert rec.verdict == VERDICT_PROPAGA
    assert rec.verdict_reason == "refina nota existente"
    assert rec.vault_paths == ("a.md", "b.md")


def test_mark_error_registra_motivo(reg: Registry):
    reg.mark_discovered("v1", "NVIDIA")
    reg.mark_error("v1", "429 rate limit")
    rec = reg.get("v1")
    assert rec.status == STATUS_ERRO
    assert rec.error == "429 rate limit"


def test_update_em_video_desconhecido_falha(reg: Registry):
    with pytest.raises(KeyError):
        reg.mark_downloaded("nunca-visto")


def test_veredicto_invalido_e_rejeitado(reg: Registry):
    reg.mark_discovered("v1", "Claude")
    with pytest.raises(ValueError, match="Veredicto inválido"):
        reg.mark_curated("v1", "TALVEZ")


# -- consultas ---------------------------------------------------------------


def test_filter_new_preserva_ordem_e_remove_conhecidos(reg: Registry):
    reg.mark_discovered("b", "Canal")
    assert reg.filter_new(["a", "b", "c"]) == ["a", "c"]


def test_filter_new_vazio(reg: Registry):
    assert reg.filter_new([]) == []


def test_filter_new_acima_do_teto_de_variaveis(reg: Registry):
    """SQLite limita variáveis por statement; filter_new fatia em blocos de 500."""
    ids = [f"v{i:04d}" for i in range(1200)]
    for vid in ids[:600]:
        reg.mark_discovered(vid, "Canal")
    novos = reg.filter_new(ids)
    assert novos == ids[600:]
    assert len(novos) == 600


def test_known_ids_por_canal(reg: Registry):
    reg.mark_discovered("a", "Anthropic")
    reg.mark_discovered("b", "Claude")
    assert reg.known_ids("Anthropic") == {"a"}
    assert reg.known_ids() == {"a", "b"}


def test_is_known(reg: Registry):
    reg.mark_discovered("a", "Anthropic")
    assert reg.is_known("a") is True
    assert reg.is_known("z") is False


def test_contadores(reg: Registry):
    reg.mark_discovered("a", "Anthropic")
    reg.mark_discovered("b", "Anthropic")
    reg.mark_discovered("c", "Claude")
    reg.mark_curated("a", VERDICT_DESCARTE, reason="promocional")

    assert reg.counts_by_channel() == {"Anthropic": 2, "Claude": 1}
    assert reg.counts_by_status()[STATUS_DESCOBERTO] == 2
    assert reg.counts_by_status()[STATUS_CURADO] == 1
    assert reg.counts_by_verdict() == {VERDICT_DESCARTE: 1}


def test_list_videos_filtra_por_veredicto(reg: Registry):
    reg.mark_discovered("a", "Anthropic", published_at="2026-07-01")
    reg.mark_discovered("b", "Anthropic", published_at="2026-07-02")
    reg.mark_curated("a", VERDICT_DESCARTE, reason="promo")
    reg.mark_curated("b", VERDICT_FICHAMENTO, reason="densa")

    descartes = reg.list_videos(verdict=VERDICT_DESCARTE)
    assert [v.video_id for v in descartes] == ["a"]
    assert descartes[0].verdict_reason == "promo"


def test_list_videos_ordena_por_publicacao_desc(reg: Registry):
    reg.mark_discovered("velho", "C", published_at="2026-01-01")
    reg.mark_discovered("novo", "C", published_at="2026-07-01")
    assert [v.video_id for v in reg.list_videos()] == ["novo", "velho"]


def test_list_videos_respeita_limit(reg: Registry):
    for i in range(5):
        reg.mark_discovered(f"v{i}", "C", published_at=f"2026-07-0{i + 1}")
    assert len(reg.list_videos(limit=2)) == 2


# -- backfill do processados.json --------------------------------------------


def test_backfill_marca_como_historico_nao_como_curado(reg: Registry):
    """O JSON legado mistura baixado/fichado/pulado — não dá pra afirmar `curado`."""
    inserted, skipped = backfill_from_processados(
        reg, {"_comment": "ruído", "Anthropic": ["a", "b"], "Claude": ["c"]}
    )
    assert (inserted, skipped) == (3, 0)
    assert reg.get("a").status == STATUS_HISTORICO
    assert reg.get("a").verdict is None
    assert reg.get("a").channel_name == "Anthropic"


def test_backfill_ignora_chaves_de_metadata(reg: Registry):
    backfill_from_processados(reg, {"_comment": "texto solto", "Claude": ["c"]})
    assert reg.total() == 1


def test_backfill_e_idempotente(reg: Registry):
    data = {"Anthropic": ["a", "b"]}
    assert backfill_from_processados(reg, data) == (2, 0)
    assert backfill_from_processados(reg, data) == (0, 2)
    assert reg.total() == 2


def test_backfill_nao_sobrescreve_linha_real(reg: Registry):
    """Um vídeo já curado de verdade não pode ser rebaixado a `historico`."""
    reg.mark_discovered("a", "Anthropic")
    reg.mark_curated("a", VERDICT_FICHAMENTO, reason="densa")

    backfill_from_processados(reg, {"Anthropic": ["a", "b"]})

    assert reg.get("a").status == STATUS_CURADO
    assert reg.get("a").verdict == VERDICT_FICHAMENTO
    assert reg.get("b").status == STATUS_HISTORICO


def test_backfill_ignora_entradas_malformadas(reg: Registry):
    inserted, _ = backfill_from_processados(
        reg, {"Canal": ["ok", "", None, 123, "ok2"]}
    )
    assert inserted == 2
    assert reg.known_ids() == {"ok", "ok2"}


# -- persistência ------------------------------------------------------------


def test_registro_persiste_em_disco(tmp_path):
    db = tmp_path / "sub" / "registry.db"  # diretório ainda não existe
    with open_registry(db) as r:
        r.mark_discovered("a", "Anthropic", title="T")
        r.mark_curated("a", VERDICT_FICHAMENTO, reason="densa", vault_paths=["x.md"])

    assert db.exists()
    with open_registry(db) as r2:
        rec = r2.get("a")
        assert rec.verdict == VERDICT_FICHAMENTO
        assert rec.vault_paths == ("x.md",)


def test_abrir_duas_vezes_nao_recria_schema(tmp_path):
    db = tmp_path / "registry.db"
    with open_registry(db) as r:
        r.mark_discovered("a", "C")
    with open_registry(db) as r:
        assert r.total() == 1


def test_vault_paths_serializa_acentos(reg: Registry):
    reg.mark_discovered("a", "C")
    reg.mark_curated("a", VERDICT_PROPAGA, vault_paths=["Método/Atenção.md"])
    assert reg.get("a").vault_paths == ("Método/Atenção.md",)


# -- enrich ------------------------------------------------------------------


def test_enrich_preenche_metadado_que_faltava(reg: Registry):
    """Linha veio do backfill só com o id; o RSS traz o título depois."""
    reg.mark_discovered("a", "Anthropic")
    reg.enrich("a", title="Keynote 2026", published_at="2026-07-01", duration_s=3600)
    rec = reg.get("a")
    assert rec.title == "Keynote 2026"
    assert rec.published_at == "2026-07-01"
    assert rec.duration_s == 3600


def test_enrich_nao_sobrescreve_dado_existente(reg: Registry):
    reg.mark_discovered("a", "Anthropic", title="Título bom", duration_s=900)
    reg.enrich("a", title="Título pior", duration_s=1)
    rec = reg.get("a")
    assert rec.title == "Título bom"
    assert rec.duration_s == 900


def test_enrich_sem_campos_e_noop(reg: Registry):
    reg.mark_discovered("a", "Anthropic", title="T")
    reg.enrich("a")
    assert reg.get("a").title == "T"


def test_enrich_em_video_desconhecido_falha(reg: Registry):
    with pytest.raises(KeyError):
        reg.enrich("nunca-visto", title="T")


def test_enrich_nao_muda_status(reg: Registry):
    reg.mark_discovered("a", "C")
    reg.mark_curated("a", VERDICT_FICHAMENTO, reason="densa")
    reg.enrich("a", title="T")
    rec = reg.get("a")
    assert rec.status == STATUS_CURADO
    assert rec.verdict == VERDICT_FICHAMENTO
    assert rec.title == "T"


def test_backfill_de_arquivo_real_do_vault(reg: Registry, tmp_path):
    """Formato exato do processados.json em produção."""
    payload = {
        "_comment": "videoIds already handled ...",
        "NVIDIA": ["U-jd4DdZyXw", "0B62mzkASs8"],
        "Karpathy": ["EWvNQjAaOHw"],
    }
    path = tmp_path / "processados.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    data = json.loads(path.read_text(encoding="utf-8"))
    inserted, skipped = backfill_from_processados(reg, data)

    assert (inserted, skipped) == (3, 0)
    assert reg.counts_by_channel() == {"NVIDIA": 2, "Karpathy": 1}

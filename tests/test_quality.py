"""Finalize quality checks: quotes against the transcript, external gate, log."""

import json

from yt_nota import quality
from yt_nota.transcript import Segment

SEGMENTS = [
    Segment(t="00:10", text="o modelo de dados precisa vir antes do modelo de ia"),
    Segment(t="00:20", text="sem dado preparado nao existe treinamento que funcione"),
]


def _body(quote: str) -> str:
    return f'## O que mais me marcou\n\n> *"{quote}"* `[00:10]`\n\nComentário.'


def test_literal_quote_is_found_across_segments():
    res = quality.check_quotes(
        _body("O modelo de dados precisa vir antes do modelo de IA, sem dado preparado não existe treinamento"),
        SEGMENTS,
        transcript_lang="pt",
    )
    assert res["total"] == 1 and res["literal"] == 1 and not res["problems"]


def test_invented_quote_is_reported():
    res = quality.check_quotes(
        _body("A nuvem é o único caminho possível para qualquer empresa moderna"),
        SEGMENTS,
        transcript_lang="pt",
    )
    assert res["nao_encontrada"] == 1
    assert res["problems"][0]["quote"].startswith("A nuvem")


def test_foreign_transcript_quotes_are_marked_translated():
    res = quality.check_quotes(
        _body("Uma frase traduzida do inglês pela síntese"), SEGMENTS, transcript_lang="en-orig"
    )
    assert res["traduzida"] == 1 and res["nao_encontrada"] == 0


def test_quote_without_transcript_is_not_found():
    res = quality.check_quotes(_body("Uma citação longa o bastante para contar"), [], transcript_lang="pt")
    assert res["nao_encontrada"] == 1


def test_short_quotes_are_ignored():
    assert quality.check_quotes(_body("Curta demais"), SEGMENTS, transcript_lang="pt")["total"] == 0


def test_style_gate_absent_returns_none():
    assert quality.run_style_gate("texto") is None


def test_style_gate_is_loaded_from_env(tmp_path, monkeypatch):
    gate = tmp_path / "gate.py"
    gate.write_text(
        "def gate_text(text):\n"
        "    bad = 'PROIBIDO' in text\n"
        "    return {'ok': not bad, 'summary': '1 findings' if bad else '0 findings',\n"
        "            'findings': [('anti-ai', 'palavra proibida')] if bad else []}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(quality.GATE_ENV_VAR, str(gate))

    assert quality.run_style_gate("limpo")["ok"] is True
    res = quality.run_style_gate("texto PROIBIDO")
    assert res["ok"] is False and res["findings"] == ["palavra proibida"]


def test_broken_gate_never_raises(tmp_path, monkeypatch):
    gate = tmp_path / "gate.py"
    gate.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    monkeypatch.setenv(quality.GATE_ENV_VAR, str(gate))
    assert quality.run_style_gate("texto") is None


def test_log_quality_appends_one_json_line_per_note(tmp_path):
    path = tmp_path / "log.jsonl"
    report = quality.quality_report("nota", _body("Curta"), SEGMENTS, transcript_lang="pt")
    quality.log_quality(report, video_id="abc", note="a.md", log_path=path)
    quality.log_quality(report, video_id="def", note="b.md", log_path=path)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [e["video_id"] for e in lines] == ["abc", "def"]
    assert lines[0]["quotes"]["total"] == 0 and lines[0]["style"] is None

"""Checagens de qualidade da nota no finalize. Nunca bloqueiam: viram log.

Duas checagens, ambas determinísticas:

- citações: cada citação em blockquote do body é procurada no transcript. O
  transcript é a única evidência do que foi dito; citação que não aparece nele
  foi parafraseada ou inventada pela síntese.
- estilo: gate externo opcional (`YT_NOTA_WRITING_GATE`), um arquivo Python que
  expõe `gate_text(text) -> {"ok": bool, "findings": [...], "summary": str}`.

O resultado vai pra `data/finalize-log.jsonl`, uma linha por nota.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import PACKAGE_ROOT
from .transcript import Segment

log = logging.getLogger("yt-nota")

DEFAULT_LOG_PATH = PACKAGE_ROOT / "data" / "finalize-log.jsonl"
GATE_ENV_VAR = "YT_NOTA_WRITING_GATE"

# Share of the quote's 4-word windows found in the transcript.
LITERAL_THRESHOLD = 0.8
PARTIAL_THRESHOLD = 0.4

_QUOTE_RE = re.compile(r'^>\s*\*?["“](.+?)["”]\*?', re.MULTILINE)


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]+", " ", text).split()


def check_quotes(body: str, segments: list[Segment], *, transcript_lang: str = "") -> dict:
    """Classifica cada citação do body contra o transcript.

    Transcript em outro idioma: as citações são traduzidas pela síntese, então a
    comparação literal não se aplica e elas saem como `traduzida`.
    """
    quotes = [q for q in _QUOTE_RE.findall(body) if len(_words(q)) >= 5]
    result = {"total": len(quotes), "literal": 0, "parcial": 0, "nao_encontrada": 0, "traduzida": 0}
    problems: list[dict] = []
    if not quotes:
        return {**result, "problems": problems}
    if not segments:
        result["nao_encontrada"] = len(quotes)
        return {**result, "problems": [{"quote": q[:120], "hit": 0.0} for q in quotes]}
    if transcript_lang and not transcript_lang.lower().startswith("pt"):
        result["traduzida"] = len(quotes)
        return {**result, "problems": problems}

    haystack = " ".join(_words(" ".join(s.text for s in segments)))
    for quote in quotes:
        words = _words(quote)
        grams = [" ".join(words[i : i + 4]) for i in range(len(words) - 3)]
        hit = sum(g in haystack for g in grams) / len(grams)
        if hit >= LITERAL_THRESHOLD:
            result["literal"] += 1
        elif hit >= PARTIAL_THRESHOLD:
            result["parcial"] += 1
        else:
            result["nao_encontrada"] += 1
            problems.append({"quote": quote[:120], "hit": round(hit, 2)})
    return {**result, "problems": problems}


def run_style_gate(text: str) -> Optional[dict]:
    """Roda o gate externo, se configurado. Qualquer falha vira None (sem gate)."""
    gate_path = os.environ.get(GATE_ENV_VAR, "").strip()
    if not gate_path:
        return None
    try:
        spec = importlib.util.spec_from_file_location("yt_nota_writing_gate", gate_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        res = module.gate_text(text)
        return {
            "ok": bool(res.get("ok")),
            "summary": str(res.get("summary", "")),
            "findings": [str(f[1] if isinstance(f, (list, tuple)) else f) for f in res.get("findings", [])],
        }
    except Exception as e:  # the gate is someone else's code; never break a finalize on it
        log.debug("Gate de escrita indisponível (%s): %s", gate_path, e)
        return None


def quality_report(
    note_text: str,
    body: str,
    segments: list[Segment],
    *,
    transcript_lang: str = "",
) -> dict:
    return {
        "quotes": check_quotes(body, segments, transcript_lang=transcript_lang),
        "style": run_style_gate(note_text),
    }


def log_quality(report: dict, *, video_id: str, note: str, log_path: Optional[Path] = None) -> None:
    """Appenda uma linha JSON por nota. Falha de escrita não derruba o finalize."""
    path = log_path or DEFAULT_LOG_PATH
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "video_id": video_id,
        "note": note,
        **report,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("Log de qualidade não gravado (%s): %s", path, e)

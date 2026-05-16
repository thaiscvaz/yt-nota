"""Parser robusto de WebVTT (.vtt) com dedup de auto-captions.

YouTube auto-captions emitem cues sobrepostos onde cada cue contém o texto
acumulado anterior + palavras novas. Esse parser detecta o padrão, mantém só
o conteúdo novo de cada cue, e produz uma lista limpa de segmentos com timestamps.
"""

import re
from dataclasses import dataclass


@dataclass
class Segment:
    t: str  # "MM:SS" ou "H:MM:SS"
    text: str

    def to_dict(self) -> dict:
        return {"t": self.t, "text": self.text}


_TS_RE = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?\s+-->\s+"
    r"(?:(?:\d+):)?(?:\d{1,2}):(?:\d{2})(?:\.\d+)?"
)
_INLINE_TAG_RE = re.compile(r"<[^>]+>")


def _seconds_to_label(total_seconds: int) -> str:
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_timestamp(line: str) -> int | None:
    m = _TS_RE.match(line.strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    mm = int(m.group(2))
    ss = int(m.group(3))
    return h * 3600 + mm * 60 + ss


def _clean_text(line: str) -> str:
    line = _INLINE_TAG_RE.sub("", line)
    return line.strip()


def _dedup_within_cue(lines: list[str]) -> list[str]:
    """Se uma linha estende outra (mesmo prefixo), mantém só a mais completa.

    Padrão de auto-caption:
        line 1: "hello"
        line 2: "hello and welcome"
    → resultado: ["hello and welcome"]
    """
    result: list[str] = []
    for line in lines:
        if not line:
            continue
        if result and (line.startswith(result[-1] + " ") or line.startswith(result[-1])):
            result[-1] = line
        else:
            result.append(line)
    return result


def _parse_cues(raw: str) -> list[Segment]:
    """Split VTT em cues, retorna lista preliminar de Segment (sem dedup entre cues)."""
    cues: list[Segment] = []
    current_ts: int | None = None
    current_lines: list[str] = []

    for line in raw.splitlines():
        stripped = line.strip()

        if not stripped:
            if current_ts is not None and current_lines:
                cleaned = [_clean_text(l) for l in current_lines]
                deduped = _dedup_within_cue(cleaned)
                text = " ".join(deduped).strip()
                if text:
                    cues.append(Segment(t=_seconds_to_label(current_ts), text=text))
            current_ts = None
            current_lines = []
            continue

        if stripped.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue

        ts = _parse_timestamp(stripped)
        if ts is not None:
            current_ts = ts
            current_lines = []
            continue

        if current_ts is not None:
            current_lines.append(stripped)

    if current_ts is not None and current_lines:
        cleaned = [_clean_text(l) for l in current_lines]
        deduped = _dedup_within_cue(cleaned)
        text = " ".join(deduped).strip()
        if text:
            cues.append(Segment(t=_seconds_to_label(current_ts), text=text))

    return cues


def _dedup_between_cues(cues: list[Segment]) -> list[Segment]:
    """Remove sobreposição entre cues consecutivos.

    Quando cue N começa com o texto de cue N-1 (auto-caption rolling buffer),
    extrai só a parte nova.
    """
    if not cues:
        return []
    result = [cues[0]]
    prev_text = cues[0].text

    for cur in cues[1:]:
        cur_text = cur.text
        if cur_text == prev_text:
            continue

        if cur_text.startswith(prev_text + " "):
            new_text = cur_text[len(prev_text) :].strip()
            if new_text:
                result.append(Segment(t=cur.t, text=new_text))
                prev_text = cur_text
            continue

        overlap = 0
        max_check = min(len(prev_text), len(cur_text))
        for i in range(max_check, 0, -1):
            if prev_text.endswith(cur_text[:i]):
                if i == len(cur_text) or cur_text[i] == " ":
                    overlap = i
                    break

        if overlap:
            new_text = cur_text[overlap:].strip()
            if new_text:
                result.append(Segment(t=cur.t, text=new_text))
                prev_text = cur_text
        else:
            result.append(cur)
            prev_text = cur_text

    return result


def parse_vtt(raw: str) -> list[Segment]:
    """Parse VTT em string, retorna lista de Segment limpa e deduplicada."""
    cues = _parse_cues(raw)
    return _dedup_between_cues(cues)


def parse_vtt_file(path) -> list[Segment]:
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_vtt(text)


def segments_to_plain(segments: list[Segment], with_timestamps: bool = True) -> str:
    """Renderiza segments como texto pra prompt."""
    if with_timestamps:
        return "\n".join(f"[{s.t}] {s.text}" for s in segments)
    return "\n".join(s.text for s in segments)


def segments_to_markdown(segments: list[Segment]) -> str:
    """Renderiza segments pro arquivo .transcript.md."""
    lines = ["", "## Transcript", ""]
    for s in segments:
        lines.append(f"`[{s.t}]` {s.text}")
        lines.append("")
    return "\n".join(lines)

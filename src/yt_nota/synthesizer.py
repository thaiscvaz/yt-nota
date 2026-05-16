"""Chama Claude pra sintetizar o body da nota a partir de metadata + transcript."""

from __future__ import annotations

import logging
from importlib.resources import files
from typing import Optional

from anthropic import Anthropic

from .config import get_anthropic_key, resolve_model
from .transcript import Segment, segments_to_plain

log = logging.getLogger(__name__)


def _load_prompt() -> str:
    return (files("yt_nota.prompts") / "synthesis.md").read_text(encoding="utf-8")


def _build_user_message(
    video: dict,
    segments: Optional[list[Segment]],
    *,
    translate: bool,
    tema: Optional[str],
) -> str:
    parts: list[str] = []
    parts.append("# Metadata do vídeo\n")
    parts.append(f"- **Título:** {video['title']}")
    parts.append(f"- **Canal:** {video['channel']}")
    parts.append(f"- **Data:** {video.get('upload_date_iso') or 'desconhecida'}")
    parts.append(f"- **Duração:** {video['duration_human']}")
    parts.append(f"- **URL:** {video['url']}")
    if video.get("tags"):
        parts.append(f"- **Tags do canal:** {', '.join(video['tags'][:12])}")

    if video.get("description"):
        desc = video["description"][:2000]
        truncated = "..." if len(video["description"]) > 2000 else ""
        parts.append(f"\n## Descrição do vídeo\n\n{desc}{truncated}")

    if segments:
        plain = segments_to_plain(segments, with_timestamps=True)
        note = ""
        if translate:
            note = (
                "\n**IMPORTANTE:** Esse transcript está em idioma estrangeiro. "
                "Cite trechos traduzidos pra PT-BR na seção 'O que mais me marcou'.\n"
            )
        parts.append(f"\n## Transcript completo com timestamps{note}\n\n{plain}")
    else:
        parts.append(
            "\n**Atenção:** Transcript indisponível para esse vídeo. "
            "Use apenas título + descrição + tags. Marque a limitação em 'Em uma frase'."
        )

    if tema:
        parts.append(
            f"\n## Tema indicado pela Thais\n\n"
            f"A Thais indicou que essa nota pertence ao tema **{tema}**. "
            f"Use isso pra orientar tags relacionadas, conexões prováveis e relevância pessoal."
        )

    parts.append(
        "\n---\n\n"
        "**Agora produz a nota completa, seguindo EXATAMENTE as 7 seções e regras de estilo. "
        "Nada antes da primeira seção, nada depois da última.**"
    )
    return "\n".join(parts)


def synthesize(
    video: dict,
    segments: Optional[list[Segment]],
    *,
    model_alias: str = "opus",
    translate: bool = False,
    tema: Optional[str] = None,
    max_tokens: int = 4096,
) -> str:
    client = Anthropic(api_key=get_anthropic_key())
    model = resolve_model(model_alias)
    system = _load_prompt()
    user = _build_user_message(video, segments, translate=translate, tema=tema)

    log.debug("Chamando %s (max_tokens=%d)", model, max_tokens)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )

    text_parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    return "\n".join(text_parts).strip()

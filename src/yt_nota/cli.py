"""CLI principal do yt-nota."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_MODEL_ALIAS, MODELS, VAULT_PATH
from .extractor import (
    ExtractError,
    extract_info,
    extract_transcript,
    is_playlist,
    normalize_video_info,
    playlist_video_urls,
)
from .synthesizer import synthesize
from .vault import write_note

log = logging.getLogger("yt-nota")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )


def _collect_urls(args: argparse.Namespace) -> list[str]:
    urls: list[str] = list(args.urls or [])
    if args.playlist:
        urls.append(args.playlist)
    if args.file:
        path = Path(args.file)
        if not path.exists():
            raise SystemExit(f"Arquivo não encontrado: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    if args.stdin:
        for line in sys.stdin:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def _looks_like_playlist(url: str) -> bool:
    return "list=" in url or "/playlist" in url


def _expand_playlists(urls: list[str], with_cookies: bool) -> list[str]:
    expanded: list[str] = []
    for url in urls:
        if _looks_like_playlist(url):
            log.info("Expandindo playlist: %s", url)
            try:
                info = extract_info(url, with_cookies=with_cookies, flat_playlist=True)
                if is_playlist(info):
                    vids = playlist_video_urls(info)
                    log.info("  %d vídeos na playlist", len(vids))
                    expanded.extend(vids)
                    continue
            except ExtractError as e:
                log.warning("Falha expandindo playlist %s: %s", url, e)
        expanded.append(url)
    return expanded


def _process_single(url: str, args: argparse.Namespace, idx: int, total: int) -> bool:
    log.info("[%d/%d] %s", idx, total, url)
    try:
        info = extract_info(url, with_cookies=args.with_cookies)
    except ExtractError as e:
        log.error("  Falha: %s", e)
        return False

    if is_playlist(info):
        log.error("  URL parece playlist mas não foi expandida. Pulando.")
        return False

    video = normalize_video_info(info)
    log.info(
        "  Metadata: %s · %s · %s",
        video["channel"] or "canal desconhecido",
        video["duration_human"],
        video.get("upload_date_iso") or "sem data",
    )

    transcript = extract_transcript(video)
    if transcript:
        log.info(
            "  Transcript: %s (%s, %d segmentos)",
            transcript["language"],
            "auto" if transcript["is_auto"] else "manual",
            len(transcript["segments"]),
        )
    else:
        log.info("  Transcript indisponível, sintetizando só com metadata")

    log.info("  Sintetizando com %s...", args.model)
    try:
        body = synthesize(
            video,
            transcript["segments"] if transcript else None,
            model_alias=args.model,
            translate=args.translate,
            tema=args.tema,
        )
    except Exception as e:
        log.error("  Síntese falhou: %s", e)
        return False

    if args.dry_run:
        sys.stdout.write("\n=== PREVIEW (dry-run) ===\n\n")
        sys.stdout.write(body)
        sys.stdout.write("\n\n=== fim do preview ===\n\n")
        return True

    result = write_note(
        video,
        body,
        transcript["segments"] if transcript else None,
        transcript,
        no_channel_card=args.no_channel_card,
        tema=args.tema,
    )

    def _rel(p):
        try:
            return p.relative_to(VAULT_PATH)
        except Exception:
            return p

    log.info("  Nota: %s", _rel(result["note_path"]))
    if result["transcript_path"]:
        log.info("  Transcript: %s", _rel(result["transcript_path"]))
    if result["channel_card_path"]:
        log.info("  Channel card: %s", _rel(result["channel_card_path"]))
    if result["moc_path"]:
        log.info("  MOC tema: %s", _rel(result["moc_path"]))
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yt-nota",
        description="Transforma transcripts do YouTube em notas Obsidian profundas.",
    )
    parser.add_argument("urls", nargs="*", help="URLs do YouTube")
    parser.add_argument("--playlist", help="URL de playlist (expande pra vídeos)")
    parser.add_argument("--file", help="Arquivo .txt com uma URL por linha")
    parser.add_argument("--stdin", action="store_true", help="Lê URLs do stdin")
    parser.add_argument("--tema", help="MOC temático (atualiza além do channel card)")
    parser.add_argument("--translate", action="store_true", help="Pede tradução do transcript pra PT-BR na síntese")
    parser.add_argument(
        "--with-cookies",
        action="store_true",
        help="Usa cookies do Chrome (precisa estar FECHADO no Windows)",
    )
    parser.add_argument(
        "--model",
        choices=list(MODELS.keys()),
        default=DEFAULT_MODEL_ALIAS,
        help="Modelo Claude (default: %(default)s)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Não escreve, só mostra preview")
    parser.add_argument("--no-channel-card", action="store_true", help="Pula atualização do channel card")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"yt-nota {__version__}")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    urls = _collect_urls(args)
    if not urls:
        parser.print_help()
        sys.exit(1)

    urls = _expand_playlists(urls, args.with_cookies)
    log.info("Processando %d vídeo(s)...\n", len(urls))

    successes = 0
    for i, url in enumerate(urls, 1):
        if _process_single(url, args, i, len(urls)):
            successes += 1

    log.info("\n%d/%d processados com sucesso.", successes, len(urls))
    sys.exit(0 if successes == len(urls) else 1)


if __name__ == "__main__":
    main()

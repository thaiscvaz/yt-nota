"""CLI do yt-nota.

Três modos, determinados por flags:

- (default) `yt-nota <url>...` extrai metadata + transcript e escreve draft em
  `<vault>/30-Recursos/Literatura/_drafts/`. A síntese acontece via skill
  `/yt-sintese` no Claude Code (zero custo de API).

- `yt-nota --finalize <draft.md>` lê o draft + body sintetizado (via --body-file
  ou stdin), escreve nota final + transcript + channel card no vault, deleta o draft.
  Chamado pela skill.

- `yt-nota --list` lista drafts pendentes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import DRAFTS_DIR, VAULT_PATH
from .extractor import (
    ExtractError,
    extract_info,
    extract_transcript,
    is_playlist,
    normalize_video_info,
    playlist_video_urls,
)
from .vault import finalize_draft, list_pending_drafts, write_draft

log = logging.getLogger("yt-nota")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    if not verbose:
        for noisy in ("httpx", "httpcore", "yt_dlp"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _rel(p: Path | None) -> str:
    if p is None:
        return ""
    try:
        return str(p.relative_to(VAULT_PATH))
    except Exception:
        return str(p)


# ---------------------------------------------------------------------------
# Modo extração
# ---------------------------------------------------------------------------

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
        "  %s · %s · %s",
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
        log.info("  Transcript indisponível")

    if args.dry_run:
        sys.stdout.write(f"\n=== PREVIEW {video['title']} ===\n\n")
        sys.stdout.write(f"Canal: {video['channel']}\n")
        sys.stdout.write(f"Duração: {video['duration_human']}\n")
        if transcript:
            sys.stdout.write(f"Transcript: {len(transcript['segments'])} segmentos\n")
            for s in transcript["segments"][:5]:
                sys.stdout.write(f"  [{s.t}] {s.text[:80]}\n")
        sys.stdout.write("\n")
        return True

    draft_path = write_draft(
        video,
        transcript["segments"] if transcript else None,
        transcript,
        tema=args.tema,
    )
    log.info("  Draft: %s", _rel(draft_path))
    return True


def _cmd_extract(args: argparse.Namespace) -> int:
    urls = _collect_urls(args)
    if not urls:
        log.error("Sem URLs. Use `yt-nota <url>` ou --playlist/--file/--stdin.")
        return 1
    urls = _expand_playlists(urls, args.with_cookies)
    log.info("Processando %d vídeo(s)...", len(urls))

    successes = 0
    for i, url in enumerate(urls, 1):
        if _process_single(url, args, i, len(urls)):
            successes += 1

    if args.dry_run:
        log.info("\n%d/%d preview(s) gerado(s) (dry-run, nada escrito).", successes, len(urls))
    else:
        log.info("\n%d/%d drafts criados.", successes, len(urls))
        if successes:
            pending = list_pending_drafts()
            log.info(
                "Drafts pendentes em %s (%d total). Invoque `/yt-sintese` no Claude Code pra processar.",
                _rel(DRAFTS_DIR),
                len(pending),
            )
    return 0 if successes == len(urls) else 1


# ---------------------------------------------------------------------------
# Modo finalize
# ---------------------------------------------------------------------------

def _cmd_finalize(args: argparse.Namespace) -> int:
    draft = Path(args.finalize)
    if not draft.exists():
        log.error("Draft não encontrado: %s", draft)
        return 1

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    else:
        body = sys.stdin.read()

    if not body.strip():
        log.error("Body de síntese vazio. Passe via --body-file ou stdin.")
        return 1

    result = finalize_draft(
        draft,
        body,
        no_channel_card=args.no_channel_card,
        delete_draft=not args.keep_draft,
    )
    log.info("Nota: %s", _rel(result["note_path"]))
    if result["transcript_path"]:
        log.info("Transcript: %s", _rel(result["transcript_path"]))
    if result["channel_card_path"]:
        log.info("Channel card: %s", _rel(result["channel_card_path"]))
    if result["moc_path"]:
        log.info("MOC: %s", _rel(result["moc_path"]))
    if result["draft_deleted"]:
        log.info("Draft deletado.")
    return 0


# ---------------------------------------------------------------------------
# Modo list
# ---------------------------------------------------------------------------

def _cmd_list(_args: argparse.Namespace) -> int:
    drafts = list_pending_drafts()
    if not drafts:
        log.info("Sem drafts pendentes.")
        return 0
    log.info("%d draft(s) pendente(s):", len(drafts))
    for d in drafts:
        sys.stdout.write(f"{d}\n")
    return 0


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yt-nota",
        description="Extrai transcripts do YouTube. Síntese acontece via skill /yt-sintese no Claude Code.",
    )
    parser.add_argument("urls", nargs="*", help="URLs do YouTube (modo extração)")
    parser.add_argument("--playlist", help="URL de playlist (expande pra vídeos)")
    parser.add_argument("--file", help="Arquivo .txt com uma URL por linha")
    parser.add_argument("--stdin", action="store_true", help="Lê URLs do stdin")
    parser.add_argument("--tema", help="Tema (MOC) — usado pelo finalize depois")
    parser.add_argument(
        "--with-cookies",
        action="store_true",
        help="Usa cookies do Chrome (Chrome precisa estar FECHADO)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview sem escrever draft")

    parser.add_argument("--finalize", metavar="DRAFT", help="Finalize um draft (precisa --body-file ou stdin)")
    parser.add_argument("--body-file", help="Arquivo com body sintetizado (modo finalize)")
    parser.add_argument("--no-channel-card", action="store_true", help="Pula channel card (modo finalize)")
    parser.add_argument("--keep-draft", action="store_true", help="Não deleta o draft (modo finalize)")

    parser.add_argument("--list", action="store_true", help="Lista drafts pendentes")

    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"yt-nota {__version__}")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    if args.list:
        sys.exit(_cmd_list(args))
    if args.finalize:
        sys.exit(_cmd_finalize(args))
    sys.exit(_cmd_extract(args))


if __name__ == "__main__":
    main()

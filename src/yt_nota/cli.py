"""CLI do yt-nota.

Três modos, determinados por flags:

- (default) `yt-nota <url>...` extrai metadata + transcript e escreve draft em
  `<vault>/30-Recursos/Literatura/Pipeline/_processar/`. A síntese acontece via skill
  `/yt-sintese` no Claude Code (zero custo de API).

- `yt-nota --finalize <draft.md>` lê o draft + body sintetizado (via --body-file
  ou stdin), escreve nota final + transcript + channel card no vault, deleta o draft.
  Chamado pela skill.

- `yt-nota --list` lista drafts pendentes.

- `yt-nota --registry <acao>` consulta/administra o registro durável do que já passou
  pelo pipeline (stats | list | backfill).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .config import PROCESSAR_DIR, VAULT_PATH
from .domain import DomainResolutionError, resolve as resolve_dominio
from .extractor import (
    ExtractError,
    RateLimitError,
    extract_info,
    extract_transcript,
    is_playlist,
    normalize_video_info,
    playlist_video_urls,
    video_id_from_url,
)
from .registry import (
    DEFAULT_DB_PATH,
    STATUS_BAIXADO,
    STATUS_CURADO,
    STATUS_DESCOBERTO,
    STATUS_ERRO,
    backfill_from_processados,
    open_registry,
)
from .slug import channel_slug
from .vault import (
    finalize_draft,
    find_video_anywhere,
    is_video_already_processed,
    list_pending_drafts,
    write_draft,
)
from .whisper_fallback import (
    SUPPORTED_MODELS,
    resolve_enabled_from_env,
    resolve_model_from_env,
)

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
    file_path = args.retry_pending or args.file
    if file_path:
        path = Path(file_path)
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
    return "list=" in url or "/playlist" in url or "/@" in url or "/channel/" in url or "/c/" in url


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


def _process_single(url: str, args: argparse.Namespace, idx: int, total: int) -> str:
    """Processa 1 vídeo. Retorna "ok" | "skip" | "fail".

    Propaga RateLimitError (caller decide parar a wave).
    """
    log.info("[%d/%d] %s", idx, total, url)
    try:
        info = extract_info(url, with_cookies=args.with_cookies)
    except ExtractError as e:
        log.error("  Falha: %s", e)
        return "fail"

    if is_playlist(info):
        log.error("  URL parece playlist mas não foi expandida. Pulando.")
        return "fail"

    video = normalize_video_info(info)
    log.info(
        "  %s · %s · %s",
        video["channel"] or "canal desconhecido",
        video["duration_human"],
        video.get("upload_date_iso") or "sem data",
    )

    canal_slug_str = channel_slug(video["channel"] or "Canal-Desconhecido")

    # Resolução de domínio em cascata: --dominio > lookup channel_domains.yaml.
    # Sem domínio resolvido, aborta esse vídeo (regra do vault exige hierarquia).
    try:
        dominio = resolve_dominio(canal_slug_str, override=args.dominio)
    except DomainResolutionError as e:
        log.error("  %s", e)
        return "fail"

    if not args.force:
        already, evidence = is_video_already_processed(
            video["video_id"], canal_slug_str, dominio=dominio
        )
        if already:
            log.info("  Já processado, pulando: %s", _rel(evidence) if evidence else "")
            return "skip"

    try:
        transcript = extract_transcript(
            video,
            whisper_fallback=args.whisper_fallback_enabled,
            whisper_model=args.whisper_model_resolved,
            with_cookies=args.with_cookies,
        )
    except RateLimitError:
        raise
    if transcript:
        origin = transcript.get("origin") or ("auto" if transcript.get("is_auto") else "manual")
        log.info(
            "  Transcript: %s (%s, %d segmentos)",
            transcript["language"],
            origin,
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
        return "ok"

    draft_path = write_draft(
        video,
        transcript["segments"] if transcript else None,
        transcript,
        tema=args.tema,
        dominio=dominio,
    )
    log.info("  Draft: %s", _rel(draft_path))
    return "ok"


def _pending_path_for(source: str | None) -> Path:
    """Path do .pending.txt baseado em --file ou --retry-pending."""
    if source:
        src = Path(source)
        if src.name.endswith(".pending.txt"):
            return src
        return src.with_suffix(src.suffix + ".pending.txt") if src.suffix else src.with_name(src.name + ".pending.txt")
    return Path("yt-nota.pending.txt")


def _failed_path_for(source: str | None) -> Path:
    """Path do .failed.txt (URLs com falha individual, fora da parada por 429)."""
    base = _pending_path_for(source)
    return base.with_name(base.name.replace(".pending.txt", ".failed.txt"))


def _save_pending(path: Path, remaining: list[str], reason: str) -> None:
    header = (
        f"# Wave interrompida: {reason}\n"
        f"# {len(remaining)} URLs restantes. Retomar com: yt-nota --retry-pending {path.name}\n"
        f"\n"
    )
    path.write_text(header + "\n".join(remaining) + "\n", encoding="utf-8")


def _cmd_extract(args: argparse.Namespace) -> int:
    urls = _collect_urls(args)
    if not urls:
        log.error("Sem URLs. Use `yt-nota <url>` ou --playlist/--file/--stdin.")
        return 1
    urls = _expand_playlists(urls, args.with_cookies)
    log.info("Processando %d vídeo(s)...", len(urls))
    if args.sleep > 0:
        log.info("Sleep entre vídeos: %ds", args.sleep)

    successes = 0
    rate_limit_streak = 0
    first_429_idx = 0  # 1-based: primeira URL do streak de 429 (entra no pending)
    stopped_early = False
    failures: list[str] = []
    did_network = False
    source = args.retry_pending or args.file
    for i, url in enumerate(urls, 1):
        # Dedup sem rede: video_id parseado da URL. Vídeo já processado não gasta
        # extract_info nem sleep — re-rodar uma queue grande vira no-op instantâneo.
        if not args.force:
            vid = video_id_from_url(url)
            if vid:
                evidence = find_video_anywhere(vid)
                if evidence is not None:
                    log.info("[%d/%d] Já processado, pulando: %s", i, len(urls), _rel(evidence))
                    successes += 1
                    continue

        if did_network and args.sleep > 0:
            time.sleep(args.sleep)
        did_network = True
        try:
            status = _process_single(url, args, i, len(urls))
            if status in ("ok", "skip"):
                successes += 1
            else:
                failures.append(url)
            rate_limit_streak = 0
        except RateLimitError as e:
            log.error("  %s", e)
            rate_limit_streak += 1
            if rate_limit_streak == 1:
                first_429_idx = i
                failures.append(url)  # removido depois se entrar no pending
                log.warning("  (continuando, pode ser flutuação. Próximo erro 429 vai parar.)")
                continue
            log.error(
                "Parada precoce: 2 rate limits consecutivos. "
                "Próximas URLs vão dar 429 também."
            )
            stopped_early = True
            break

    remaining: list[str] = []
    if stopped_early:
        # Retoma a partir da PRIMEIRA URL do streak (as duas que deram 429 nunca
        # viraram draft — descartá-las perderia vídeos silenciosamente).
        remaining = urls[first_429_idx - 1:]
        failures = [u for u in failures if u not in set(remaining)]
        pending_path = _pending_path_for(source)
        _save_pending(pending_path, remaining, "rate limit 429")
        log.error(
            "\n%d URLs restantes salvas em %s. "
            "Retome com: yt-nota --retry-pending %s",
            len(remaining),
            pending_path,
            pending_path.name,
        )

    if failures and not args.dry_run:
        failed_path = _failed_path_for(source)
        _save_pending(failed_path, failures, "falhas individuais")
        log.warning(
            "%d URL(s) com falha salvas em %s. Reprocesse com: yt-nota --retry-pending %s",
            len(failures),
            failed_path,
            failed_path.name,
        )

    if args.dry_run:
        log.info("\n%d/%d preview(s) gerado(s) (dry-run, nada escrito).", successes, len(urls))
    else:
        log.info("\n%d/%d drafts criados.", successes, len(urls))
        if successes:
            pending = list_pending_drafts()
            log.info(
                "Drafts pendentes em %s (%d total). Invoque `/yt-sintese` no Claude Code pra processar.",
                _rel(PROCESSAR_DIR),
                len(pending),
            )

    # Se rodou tudo sem parada precoce E veio de --retry-pending, deleta o pending
    if not stopped_early and args.retry_pending:
        pending_path = Path(args.retry_pending)
        if pending_path.exists():
            pending_path.unlink()
            log.info("Pending file consumido: %s", pending_path.name)

    if stopped_early:
        return 2  # exit code dedicado pra rate limit
    return 0 if successes == len(urls) else 1


# ---------------------------------------------------------------------------
# Modo finalize
# ---------------------------------------------------------------------------

# As 7 seções que a skill /yt-sintese produz. O finalize deleta o draft após
# escrever a nota — validar o body ANTES protege contra perder o draft pra um
# body truncado ou fora do formato.
EXPECTED_BODY_SECTIONS = (
    "## Em uma frase",
    "## O que defende",
    "## O que mais me marcou",
    "## O que isso muda pra mim",
    "## Dicionário",
    "## Notas permanentes a criar",
    "## Referência",
)


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

    if not args.skip_body_check:
        missing = [s for s in EXPECTED_BODY_SECTIONS if s not in body]
        if missing:
            log.error(
                "Body de síntese incompleto — draft preservado. Seções ausentes: %s. "
                "Corrija o body ou use --skip-body-check pra forçar.",
                ", ".join(missing),
            )
            return 1

    result = finalize_draft(
        draft,
        body,
        no_channel_card=args.no_channel_card,
        delete_draft=not args.keep_draft,
        dominio_override=args.dominio,
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
# Modo registry
# ---------------------------------------------------------------------------

# Onde o processados.json legado mora no vault. Só é lido no backfill.
LEGACY_PROCESSADOS = (
    VAULT_PATH / "Claude" / "automacoes" / "curadoria-incremental" / "data" / "processados.json"
)


def _print_counts(title: str, counts: dict[str, int]) -> None:
    sys.stdout.write(f"\n{title}\n")
    if not counts:
        sys.stdout.write("  (vazio)\n")
        return
    width = max(len(k) for k in counts)
    for key, n in counts.items():
        sys.stdout.write(f"  {key:<{width}}  {n:>6}\n")


def _registry_items(args: argparse.Namespace) -> list[tuple[str, str | None]]:
    """Resolve --ids / --from-file numa lista de (video_id, título|None).

    `--from-file` aceita `videoId` ou `videoId<TAB>título` por linha: o RSS já traz o
    título, e jogá-lo fora obrigaria a rebuscar depois. Linhas vazias e `#` são ignoradas.
    """
    items: list[tuple[str, str | None]] = []
    if args.ids:
        for chunk in args.ids:
            for raw in chunk.replace(",", " ").split():
                items.append((raw, None))
    if args.from_file:
        for line in Path(args.from_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            vid, _, title = line.partition("\t")
            items.append((vid.strip(), title.strip() or None))
    return items


def _cmd_registry(args: argparse.Namespace) -> int:
    action = args.registry
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH

    with open_registry(db_path) as reg:
        if action == "stats":
            sys.stdout.write(f"Registro: {db_path}\n")
            sys.stdout.write(f"Total de vídeos: {reg.total()}\n")
            _print_counts("Por status:", reg.counts_by_status())
            _print_counts("Por veredicto:", reg.counts_by_verdict())
            _print_counts("Por canal:", reg.counts_by_channel())
            return 0

        if action == "list":
            videos = reg.list_videos(
                channel_name=args.channel,
                status=args.status,
                verdict=args.verdict,
                limit=args.limit,
            )
            if not videos:
                log.info("Nenhum vídeo bate com o filtro.")
                return 0
            for v in videos:
                published = v.published_at or "sem data"
                extra = f" [{v.verdict}]" if v.verdict else ""
                reason = f" — {v.verdict_reason}" if v.verdict_reason else ""
                sys.stdout.write(
                    f"{v.video_id}  {published:<12} {v.status:<11}{extra} "
                    f"{v.channel_name} · {v.title or ''}{reason}\n"
                )
            log.info("\n%d vídeo(s).", len(videos))
            return 0

        if action == "filter":
            items = _registry_items(args)
            if not items:
                log.error("Nada a filtrar: informe --ids ou --from-file.")
                return 1
            novos = reg.filter_new([vid for vid, _ in items])
            for vid in novos:
                sys.stdout.write(f"{vid}\n")
            log.info("%d de %d são novos.", len(novos), len(items))
            return 0

        if action == "mark":
            items = _registry_items(args)
            if not items:
                log.error("Nada a marcar: informe --ids ou --from-file.")
                return 1
            if not args.channel:
                log.error("--channel é obrigatório em --registry mark.")
                return 1
            alvo = args.status or STATUS_DESCOBERTO
            if alvo == STATUS_CURADO and not args.verdict:
                log.error(
                    "--status curado exige --verdict. Curar sem veredicto é exatamente o "
                    "buraco do processados.json que o registro existe pra fechar."
                )
                return 1
            if alvo == STATUS_ERRO and not args.reason:
                log.error("--status erro exige --reason (o motivo é o dado útil).")
                return 1

            for vid, title in items:
                reg.mark_discovered(vid, args.channel, title=title)
                if title:
                    reg.enrich(vid, title=title)
                if alvo == STATUS_BAIXADO:
                    reg.mark_downloaded(vid)
                elif alvo == STATUS_CURADO:
                    reg.mark_curated(vid, args.verdict, reason=args.reason)
                elif alvo == STATUS_ERRO:
                    reg.mark_error(vid, args.reason)

            detalhe = f" [{args.verdict}]" if args.verdict else ""
            log.info(
                "%d vídeo(s) de %s marcados como `%s`%s.",
                len(items),
                args.channel,
                alvo,
                detalhe,
            )
            return 0

        if action == "backfill":
            source = Path(args.from_file) if args.from_file else LEGACY_PROCESSADOS
            if not source.exists():
                log.error("Arquivo de origem não encontrado: %s", source)
                return 1
            data = json.loads(source.read_text(encoding="utf-8"))
            if args.dry_run:
                total = sum(
                    len(v)
                    for k, v in data.items()
                    if not k.startswith("_") and isinstance(v, list)
                )
                novos = len(reg.filter_new(
                    [
                        vid
                        for k, v in data.items()
                        if not k.startswith("_") and isinstance(v, list)
                        for vid in v
                        if isinstance(vid, str) and vid
                    ]
                ))
                log.info(
                    "Dry-run: %s tem %d ids; %d entrariam como `historico`, %d já conhecidos.",
                    source.name,
                    total,
                    novos,
                    total - novos,
                )
                return 0
            inserted, skipped = backfill_from_processados(reg, data)
            log.info(
                "Backfill de %s: %d inseridos como `historico`, %d já conhecidos. Total: %d.",
                source.name,
                inserted,
                skipped,
                reg.total(),
            )
            return 0

    log.error("Ação desconhecida: %s", action)
    return 1


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
    parser.add_argument(
        "--retry-pending",
        help="Retoma a partir de <wave>.pending.txt salvo após parada precoce por 429",
    )
    parser.add_argument("--stdin", action="store_true", help="Lê URLs do stdin")
    parser.add_argument("--tema", help="Tema (MOC) — usado pelo finalize depois")
    parser.add_argument(
        "--dominio",
        help=(
            "Domínio do vault (ver config/domains.yaml). "
            "Override do lookup em config/channel_domains.yaml."
        ),
    )
    parser.add_argument(
        "--sleep",
        type=int,
        default=0,
        metavar="N",
        help="Segundos de espera entre vídeos (default 0; 60 validado como estável pra waves longas)",
    )
    parser.add_argument(
        "--with-cookies",
        action="store_true",
        help="Usa cookies do Chrome (Chrome precisa estar FECHADO)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview sem escrever draft")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocessa mesmo se video_id já tem nota/draft no vault (sobrescreve dedup)",
    )

    parser.add_argument(
        "--whisper-fallback",
        dest="whisper_fallback",
        action="store_true",
        default=None,
        help="Força usar Whisper local quando 429. Default: ativado (override com --no-whisper-fallback ou YT_NOTA_WHISPER_FALLBACK=0)",
    )
    parser.add_argument(
        "--no-whisper-fallback",
        dest="whisper_fallback",
        action="store_false",
        help="Desativa o fallback Whisper (mantém parada precoce no 429)",
    )
    parser.add_argument(
        "--whisper-model",
        choices=sorted(SUPPORTED_MODELS),
        default=None,
        help="Modelo Whisper pro fallback. Default: 'small' (244 MB, ótimo PT-BR). Override com YT_NOTA_WHISPER_MODEL.",
    )

    parser.add_argument("--finalize", metavar="DRAFT", help="Finalize um draft (precisa --body-file ou stdin)")
    parser.add_argument("--body-file", help="Arquivo com body sintetizado (modo finalize)")
    parser.add_argument("--no-channel-card", action="store_true", help="Pula channel card (modo finalize)")
    parser.add_argument("--keep-draft", action="store_true", help="Não deleta o draft (modo finalize)")
    parser.add_argument(
        "--skip-body-check",
        action="store_true",
        help="Pula validação das 7 seções do body antes de deletar o draft (modo finalize)",
    )

    parser.add_argument("--list", action="store_true", help="Lista drafts pendentes")

    reg_group = parser.add_argument_group("registro durável")
    reg_group.add_argument(
        "--registry",
        choices=("stats", "list", "filter", "mark", "backfill"),
        help=(
            "Consulta/administra o registro do que já passou pelo pipeline. "
            "filter = imprime só os ids desconhecidos; mark = grava transição de status"
        ),
    )
    reg_group.add_argument("--db", help=f"Path do registro (default: {DEFAULT_DB_PATH})")
    reg_group.add_argument("--channel", help="Canal (filtro em `list`, obrigatório em `mark`)")
    reg_group.add_argument(
        "--status",
        choices=("descoberto", "baixado", "curado", "erro", "historico"),
        help="Filtro em `list`; status-alvo em `mark` (default: descoberto)",
    )
    reg_group.add_argument(
        "--verdict",
        choices=("DESCARTE", "PROPAGA", "ATOMICA", "FICHAMENTO"),
        help="Veredicto do portão de curadoria. Filtro em `list`; obrigatório em `mark --status curado`",
    )
    reg_group.add_argument(
        "--reason",
        help="Razão do veredicto (`mark --status curado`) ou do erro (`mark --status erro`)",
    )
    reg_group.add_argument(
        "--ids",
        nargs="+",
        help="video_ids separados por espaço ou vírgula (`filter`/`mark`)",
    )
    reg_group.add_argument("--limit", type=int, help="Teto de linhas (--registry list)")
    reg_group.add_argument(
        "--from-file",
        dest="from_file",
        help=(
            "Arquivo de entrada. Em `backfill`: JSON legado (default: processados.json "
            "no vault). Em `filter`/`mark`: um `videoId` ou `videoId<TAB>título` por linha"
        ),
    )

    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"yt-nota {__version__}")
    args = parser.parse_args()

    # Resolve Whisper opts em cascata: CLI flag > env var > default
    args.whisper_fallback_enabled = resolve_enabled_from_env(args.whisper_fallback)
    args.whisper_model_resolved = resolve_model_from_env(args.whisper_model)

    _setup_logging(args.verbose)

    if args.registry:
        sys.exit(_cmd_registry(args))
    if args.list:
        sys.exit(_cmd_list(args))
    if args.finalize:
        sys.exit(_cmd_finalize(args))
    sys.exit(_cmd_extract(args))


if __name__ == "__main__":
    main()

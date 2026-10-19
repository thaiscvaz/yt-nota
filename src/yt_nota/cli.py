"""CLI do yt-nota.

Três modos, determinados por flags:

- (default) `yt-nota <url>...` extrai metadata + transcript e escreve draft em
  `<vault>/30-Recursos/Literatura/Pipeline/_processar/`. A síntese acontece via skill
  `/yt-sintese` no Claude Code (zero custo de API).

- `yt-nota --finalize <draft.md> --verdict FICHAMENTO --reason "..."` lê o draft +
  body sintetizado (via --body-file ou stdin), escreve nota final + transcript +
  channel card no vault, grava o veredicto no registro e arquiva o draft.

- `yt-nota --close <draft.md> --verdict PROPAGA|ATOMICA --reason "..." --paths ...`
  fecha um draft cujo conteúdo foi para páginas da camada por assunto: grava o
  transcript e a linha no card, registra o veredicto e arquiva o draft.

- `yt-nota --discard <draft.md> --reason "..."` grava DESCARTE e arquiva o draft.

Um draft nunca é apagado: sai de `_processar/` para `_archive/drafts/<VEREDICTO>/`
depois de o registro ser gravado e a cópia conferida.

- `yt-nota --list` lista drafts pendentes.

- `yt-nota --registry <acao>` consulta/administra o registro durável do que já passou
  pelo pipeline (stats | list | backfill).
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

from . import __version__
from . import config
from .archive import ArchiveError, archive_draft
from .config import LEGACY_PROCESSADOS_PATH, PROCESSAR_DIR, VAULT_ENV_VAR, VAULT_PATH
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
    VERDICT_ATOMICA,
    VERDICT_DESCARTE,
    VERDICT_FICHAMENTO,
    VERDICT_PROPAGA,
    backfill_from_processados,
    new_attempt_id,
    open_registry,
    software_version,
    text_measures,
)
from .slug import channel_slug
from .quality import log_quality
from .vault import (
    close_without_note,
    finalize_draft,
    find_video_anywhere,
    is_video_already_processed,
    list_pending_drafts,
    read_draft_meta,
    reset_dedup_index,
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
# Registro durável (escrita pelo próprio CLI)
# ---------------------------------------------------------------------------
# O registro é a fonte do dedup; o vault é a rede de segurança. Uma falha de
# SQLite nunca derruba a extração nem o finalize: vira warning e o scan do vault
# continua valendo.

def _registry_db(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "db", None) or DEFAULT_DB_PATH)


def _open_for(args: argparse.Namespace, db_path: Path, *, readonly: bool):
    """open_registry, read-only when asked. The keyword is passed only for
    read-only opens, so a normal open keeps exactly its previous call."""
    return open_registry(db_path, readonly=True) if readonly else open_registry(db_path)


def _registry_done_ids(args: argparse.Namespace) -> set[str]:
    """video_ids que o registro já dá por baixados ou curados.

    `descoberto` fica de fora de propósito: é vídeo visto no RSS que ainda precisa
    ser baixado. `historico` também: procedência incerta, o vault decide.
    """
    try:
        # A dry run only reads: open read-only, so it never creates or migrates.
        with _open_for(args, _registry_db(args), readonly=bool(getattr(args, "dry_run", False))) as reg:
            return {
                v.video_id
                for status in (STATUS_BAIXADO, STATUS_CURADO)
                for v in reg.list_videos(status=status)
            }
    except sqlite3.Error as e:
        log.warning("Registro indisponível (%s); dedup só pelo vault.", e)
        return set()


def _registry_record(
    args: argparse.Namespace,
    video_id: str,
    channel_name: str,
    *,
    title: str | None = None,
    channel_id: str | None = None,
    published_at: str | None = None,
    duration_s: int | None = None,
    status: str | None = None,
    transcript_source: str | None = None,
    verdict: str | None = None,
    reason: str | None = None,
    vault_paths: tuple[str, ...] = (),
) -> bool:
    """Garante a linha do vídeo e aplica a transição pedida. Nunca levanta.

    Devolve True quando o registro foi gravado: quem arquiva um draft depende
    disso para só tirá-lo do vault depois de o veredicto estar a salvo.
    """
    if not video_id:
        return False
    meta = dict(
        title=title or None,
        channel_id=channel_id or None,
        published_at=published_at or None,
        duration_s=duration_s or None,
    )
    try:
        with open_registry(_registry_db(args)) as reg:
            reg.mark_discovered(video_id, channel_name, **meta)
            reg.enrich(video_id, **meta)
            if status == STATUS_BAIXADO:
                # Nunca rebaixa um vídeo já curado (reprocesso com --force).
                current = reg.get(video_id)
                if current is None or current.status != STATUS_CURADO:
                    reg.mark_downloaded(video_id, transcript_source=transcript_source)
            elif status == STATUS_CURADO and verdict:
                reg.mark_curated(video_id, verdict, reason=reason, vault_paths=vault_paths)
            elif status == STATUS_ERRO and reason:
                reg.mark_error(video_id, reason)
    except sqlite3.Error as e:
        log.warning("  Registro não atualizado para %s: %s", video_id, e)
        return False
    return True


def _registry_event(
    args: argparse.Namespace,
    case_id: str,
    activity: str,
    lifecycle: str,
    *,
    attempt_id: str | None = None,
    decided_by: str | None = None,
    attrs: dict | None = None,
    objects: tuple = (),
) -> None:
    """Append one activity event (B-09). Never raises and writes nothing on a
    dry run: the event log must never stop or change a run."""
    if not case_id or getattr(args, "dry_run", False):
        return
    try:
        with open_registry(_registry_db(args)) as reg:
            reg.record_event(case_id, activity, lifecycle, attempt_id=attempt_id,
                             decided_by=decided_by, attrs=attrs, objects=objects)
    except (sqlite3.Error, ValueError) as e:
        log.warning("  Evento não registrado para %s: %s", case_id, e)


_SOFTWARE: dict | None = None


def _software_version() -> dict:
    """yt-nota version and commit, computed once per process (B-09)."""
    global _SOFTWARE
    if _SOFTWARE is None:
        _SOFTWARE = software_version()
    return _SOFTWARE


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
    # B-09: metadata fetch is where unavailable videos fail; record that attempt.
    url_vid = video_id_from_url(url) or ""
    meta_attempt = new_attempt_id()
    _registry_event(args, url_vid, "fetch_metadata", "start", attempt_id=meta_attempt)
    try:
        info = extract_info(url, with_cookies=args.with_cookies)
    except ExtractError as e:
        log.error("  Falha: %s", e)
        _registry_event(args, url_vid, "fetch_metadata", "ate_abort", attempt_id=meta_attempt,
                        attrs={"reason": "extract_error", "error_kind": getattr(e, "kind", "unknown"),
                               "error": str(e)[:500]})
        return "fail"
    _registry_event(args, url_vid, "fetch_metadata", "complete", attempt_id=meta_attempt)

    if is_playlist(info):
        log.error("  URL parece playlist mas não foi expandida. Pulando.")
        _registry_event(args, url_vid, "route", "withdraw", decided_by="rule",
                        attrs={"reason": "playlist_not_expanded"})
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
    # B-09: every exit of this function leaves an event, so a video never stops
    # silently between metadata and transcription.
    try:
        dominio = resolve_dominio(canal_slug_str, override=args.dominio)
    except DomainResolutionError as e:
        log.error("  %s", e)
        _registry_event(args, video["video_id"], "route", "ate_abort",
                        attrs={"reason": "domain_unresolved", "channel": canal_slug_str,
                               "error": str(e)[:500]})
        return "fail"
    _registry_event(args, video["video_id"], "route", "complete", decided_by="rule",
                    attrs={"channel": canal_slug_str, "dominio": dominio,
                           "override": bool(args.dominio)})

    if not args.force:
        already, evidence = is_video_already_processed(
            video["video_id"], canal_slug_str, dominio=dominio
        )
        if already:
            log.info("  Já processado, pulando: %s", _rel(evidence) if evidence else "")
            _registry_event(args, video["video_id"], "route", "withdraw", decided_by="rule",
                            attrs={"reason": "already_processed",
                                   "evidence": str(_rel(evidence)) if evidence else None})
            return "skip"

    # B-09: one attempt of the `transcribe` activity, start and end linked by id.
    attempt = new_attempt_id()
    _registry_event(args, video["video_id"], "transcribe", "start", attempt_id=attempt,
                    attrs={"whisper_fallback_enabled": bool(args.whisper_fallback_enabled)})
    try:
        transcript = extract_transcript(
            video,
            whisper_fallback=args.whisper_fallback_enabled,
            whisper_model=args.whisper_model_resolved,
            with_cookies=args.with_cookies,
        )
    except RateLimitError as e:
        _registry_event(args, video["video_id"], "transcribe", "ate_abort",
                        attempt_id=attempt, attrs={"reason": "rate_limit", "error": str(e)})
        raise
    end_attrs: dict = {"origin": None, "language": None, "segments": 0,
                       "software": _software_version()}
    transcript_obj: tuple = ()
    if transcript:
        origin = transcript.get("origin")
        segments = transcript.get("segments") or []
        measures = text_measures("\n".join(getattr(s, "text", str(s)) for s in segments))
        end_attrs.update({"origin": origin, "language": transcript.get("language"),
                          "segments": len(segments), "transcript_sha256": measures["sha256"],
                          "transcript_words": measures["words"]})
        if origin == "whisper-local":
            # Requested and effective apart: the transcriber may fall back to its
            # default; the effective one is only what it reports (null if absent).
            end_attrs["whisper_model_requested"] = args.whisper_model_resolved
            end_attrs["whisper_model_effective"] = transcript.get("model")
        transcript_obj = (("transcript", f"{video['video_id']}:{measures['sha256'][:16]}",
                           "produced"),)
    _registry_event(args, video["video_id"], "transcribe", "complete", attempt_id=attempt,
                    attrs=end_attrs, objects=transcript_obj)
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
    draft_attrs: dict = {"path": _rel(draft_path)}
    try:
        draft_attrs.update({f"draft_{k}": v for k, v in text_measures(
            Path(draft_path).read_text(encoding="utf-8", errors="replace")).items()})
    except OSError:
        pass  # unreadable draft: measures stay absent, never guessed
    _registry_event(args, video["video_id"], "write_draft", "complete", attrs=draft_attrs,
                    objects=(*[(t, i, "input") for t, i, _ in transcript_obj],
                             ("draft", str(_rel(draft_path)), "produced")))
    _registry_record(
        args,
        video["video_id"],
        canal_slug_str,
        title=video.get("title"),
        channel_id=video.get("channel_id"),
        published_at=video.get("upload_date_iso"),
        duration_s=video.get("duration_seconds"),
        status=STATUS_BAIXADO,
        transcript_source=(transcript.get("origin") if transcript else None),
    )
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
    registry_done = set() if args.force else _registry_done_ids(args)
    for i, url in enumerate(urls, 1):
        # Dedup sem rede: video_id parseado da URL. Vídeo já processado não gasta
        # extract_info nem sleep — re-rodar uma queue grande vira no-op instantâneo.
        if not args.force:
            vid = video_id_from_url(url)
            if vid:
                # O registro vem primeiro: cobre o que o vault não prova (vídeo
                # descartado na curadoria não deixa nota).
                if vid in registry_done:
                    log.info("[%d/%d] Já no registro, pulando: %s", i, len(urls), vid)
                    successes += 1
                    continue
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


def _report_quality(result: dict) -> None:
    """Registra citações e estilo da nota. Informativo: nunca falha o finalize."""
    quality = result.get("quality") or {}
    quotes = quality.get("quotes") or {}
    if quotes.get("total"):
        log.info(
            "Citações: %d literais, %d parciais, %d não encontradas no transcript, %d traduzidas.",
            quotes["literal"],
            quotes["parcial"],
            quotes["nao_encontrada"],
            quotes["traduzida"],
        )
        for p in quotes.get("problems", []):
            log.warning("  Citação não encontrada no transcript: \"%s\"", p["quote"])
    style = quality.get("style")
    if style is not None:
        log.info("Estilo: %s", style["summary"] or ("limpo" if style["ok"] else "com achados"))
        for finding in style["findings"]:
            log.warning("  %s", finding)
    log_quality(quality, video_id=result.get("video_id") or "", note=_rel(result["note_path"]))


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

    # AC-13: no silent default. A fichamento is the exception of the curation
    # gate, so whoever finalizes states the verdict and why.
    verdict = getattr(args, "verdict", None)
    reason = getattr(args, "reason", None)
    if verdict != VERDICT_FICHAMENTO or not reason:
        log.error(
            "--finalize exige --verdict FICHAMENTO e --reason. Draft preservado. "
            "Para PROPAGA ou ATOMICA use --close; para DESCARTE use --discard."
        )
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
        delete_draft=False,  # archived below, after the registry write
        dominio_override=args.dominio,
    )
    log.info("Nota: %s", _rel(result["note_path"]))
    if result["transcript_path"]:
        log.info("Transcript: %s", _rel(result["transcript_path"]))
    if result["channel_card_path"]:
        log.info("Channel card: %s", _rel(result["channel_card_path"]))
    if result["moc_path"]:
        log.info("MOC: %s", _rel(result["moc_path"]))
    _report_quality(result)

    # Fecha o ciclo no registro: sem isso o veredicto dependia de a skill lembrar
    # de chamar `--registry mark` depois do finalize.
    recorded = True
    if result.get("video_id"):
        paths = tuple(
            _rel(result[k]) for k in ("note_path", "transcript_path") if result.get(k)
        )
        recorded = _registry_record(
            args,
            result["video_id"],
            result.get("canal_slug") or "Canal-Desconhecido",
            title=result.get("titulo"),
            status=STATUS_CURADO,
            verdict=verdict,
            reason=reason,
            vault_paths=paths,
        )
    if args.keep_draft:
        return 0
    return _archive_after_registry(draft, verdict, recorded)


def _archive_after_registry(draft: Path, verdict: str, recorded: bool) -> int:
    """AC-14: the draft leaves `_processar/` only with the verdict recorded and
    a verified copy in the archive. Otherwise it stays and the command fails."""
    if not recorded:
        log.error("Registro não gravado; draft preservado em _processar: %s", draft.name)
        return 1
    try:
        archived = archive_draft(draft, verdict)
    except ArchiveError as e:
        log.error("Draft preservado em _processar (%s).", e)
        return 1
    reset_dedup_index()
    log.info("Draft arquivado: %s", archived)
    return 0


# ---------------------------------------------------------------------------
# Modo close (vereditos sem fichamento)
# ---------------------------------------------------------------------------

def _cmd_close(args: argparse.Namespace) -> int:
    """Fecha um draft como PROPAGA ou ATOMICA: o conteúdo já está em páginas da
    camada por assunto; aqui ficam transcript, linha no card, registro e arquivo."""
    draft = Path(args.close)
    if not draft.exists():
        draft = PROCESSAR_DIR / args.close
    if not draft.exists():
        log.error("Draft não encontrado: %s", args.close)
        return 1
    verdict = getattr(args, "verdict", None)
    if verdict not in (VERDICT_PROPAGA, VERDICT_ATOMICA) or not args.reason:
        log.error(
            "--close exige --verdict PROPAGA ou ATOMICA e --reason. Draft preservado."
        )
        return 1
    pages = list(args.paths or [])
    if not pages:
        log.error("--close exige --paths com as páginas escritas. Draft preservado.")
        return 1
    video_id = str(read_draft_meta(draft).get("video_id") or "")
    if not video_id:
        log.error("Draft sem video_id, preservado: %s", draft.name)
        return 1

    result = close_without_note(draft, verdict, pages, dominio_override=args.dominio)
    if result["transcript_path"]:
        log.info("Transcript: %s", _rel(result["transcript_path"]))
    log.info("Channel card: %s", _rel(result["channel_card_path"]))

    vault_paths = tuple(pages) + tuple(
        _rel(result[k]) for k in ("transcript_path",) if result.get(k)
    )
    recorded = _registry_record(
        args,
        video_id,
        result["canal_slug"],
        title=result["titulo"],
        status=STATUS_CURADO,
        verdict=verdict,
        reason=args.reason,
        vault_paths=vault_paths,
    )
    return _archive_after_registry(draft, verdict, recorded)


# ---------------------------------------------------------------------------
# Modo list / triage / discard
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


def _cmd_triage(_args: argparse.Namespace) -> int:
    """Lista os drafts pendentes só com metadado, uma linha TSV por draft.

    Serve à triagem barata: decidir pelo título o que merece síntese custa uma
    fração de ler cada transcript inteiro.
    """
    drafts = list_pending_drafts()
    if not drafts:
        log.info("Sem drafts pendentes.")
        return 0
    sys.stdout.write("video_id\tcanal\tduracao\tpublicado\ttitulo\tdraft\n")
    for d in drafts:
        meta = read_draft_meta(d)
        sys.stdout.write(
            "\t".join(
                str(meta.get(k) or "")
                for k in ("video_id", "canal", "duracao", "data_publicacao", "titulo")
            )
            + f"\t{d.name}\n"
        )
    log.info("%d draft(s) pendente(s).", len(drafts))
    return 0


def _cmd_discard(args: argparse.Namespace) -> int:
    """Descarta drafts sem sintetizar: grava DESCARTE no registro e arquiva o draft."""
    if not args.reason:
        log.error("--discard exige --reason (o motivo é o que torna o descarte auditável).")
        return 1
    failures = 0
    for raw in args.discard:
        draft = Path(raw)
        if not draft.exists():
            draft = PROCESSAR_DIR / raw
        if not draft.exists():
            log.error("Draft não encontrado: %s", raw)
            failures += 1
            continue
        meta = read_draft_meta(draft)
        video_id = str(meta.get("video_id") or "")
        if not video_id:
            log.error("Draft sem video_id, preservado: %s", draft.name)
            failures += 1
            continue
        canal_slug_str = channel_slug(str(meta.get("canal") or "Canal-Desconhecido"))
        try:
            with open_registry(_registry_db(args)) as reg:
                reg.mark_discovered(
                    video_id, canal_slug_str, title=str(meta.get("titulo") or "") or None
                )
                reg.mark_curated(video_id, VERDICT_DESCARTE, reason=args.reason)
        except sqlite3.Error as e:
            # Sem o registro o descarte seria silencioso e o vídeo voltaria a ser
            # baixado; melhor manter o draft.
            log.error("Registro não atualizado, draft preservado (%s): %s", draft.name, e)
            failures += 1
            continue
        try:
            archived = archive_draft(draft, VERDICT_DESCARTE)
        except ArchiveError as e:
            log.error("Veredicto gravado, mas o draft ficou em _processar (%s).", e)
            failures += 1
            continue
        log.info("Descartado: %s (%s), arquivado em %s", draft.name, video_id, archived)
    reset_dedup_index()
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Modo registry
# ---------------------------------------------------------------------------

# Onde o processados.json legado mora. Só é lido no backfill; override via
# YT_NOTA_PROCESSADOS quando não estiver dentro do YT_NOTA_VAULT.
LEGACY_PROCESSADOS = LEGACY_PROCESSADOS_PATH


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


_READ_ONLY_REGISTRY_ACTIONS = frozenset({"stats", "list", "filter", "events-export"})


def _cmd_registry(args: argparse.Namespace) -> int:
    action = args.registry
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH
    # Reading actions and every dry run open read-only: they never create the
    # file, migrate the schema or stamp the coverage start (B-09).
    readonly = action in _READ_ONLY_REGISTRY_ACTIONS or bool(getattr(args, "dry_run", False))
    if action == "mark" and getattr(args, "dry_run", False):
        # `mark` has no simulation mode; refuse before opening anything.
        log.error("--registry mark não tem modo de simulação (--dry-run); nada foi gravado. "
                  "Para ver quais ids são novos, use --registry filter.")
        return 1

    with _open_for(args, db_path, readonly=readonly) as reg:
        if action == "stats":
            sys.stdout.write(f"Registro: {db_path}\n")
            sys.stdout.write(f"Total de vídeos: {reg.total()}\n")
            _print_counts("Por status:", reg.counts_by_status())
            _print_counts("Por veredicto:", reg.counts_by_verdict())
            _print_counts("Por canal:", reg.counts_by_channel())
            return 0

        if action == "events-export":
            if not args.events_out:
                log.error("--registry events-export exige --events-out <arquivo.csv>.")
                return 1
            if not reg.has_event_log():
                log.warning("Cobertura de eventos ainda não iniciada: %s não tem a tabela "
                            "`events` (o código da B-09 ainda não abriu este banco para "
                            "escrita). O arquivo sai com o log vazio.", db_path)
            if getattr(args, "events_format", "csv") == "ocel2":
                counts = reg.export_events_ocel2_json(args.events_out)
                log.info("%d evento(s) e %d objeto(s) exportados em OCEL 2.0 para %s.",
                         counts["events"], counts["objects"], args.events_out)
                return 0
            n = reg.export_events_csv(args.events_out)
            log.info("%d evento(s) exportados para %s.", n, args.events_out)
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

        if action == "adopt-drafts":
            # Drafts baixados antes de o CLI escrever no registro (waves antigas)
            # existem no vault mas não aqui. Sem adotá-los, o dedup depende só do scan.
            if not config.VAULT_CONFIGURED:
                log.error("%s não definido; adopt-drafts precisa do vault.", VAULT_ENV_VAR)
                return 1
            adopted = known = skipped = 0
            for draft in list_pending_drafts():
                meta = read_draft_meta(draft)
                video_id = str(meta.get("video_id") or "")
                if not video_id:
                    skipped += 1
                    continue
                existing = reg.get(video_id)
                if existing is not None and existing.status in (STATUS_BAIXADO, STATUS_CURADO):
                    known += 1
                    continue
                if args.dry_run:
                    adopted += 1
                    continue
                canal = channel_slug(str(meta.get("canal") or "Canal-Desconhecido"))
                titulo = str(meta.get("titulo") or "") or None
                publicado = str(meta.get("data_publicacao") or "") or None
                reg.mark_discovered(video_id, canal, title=titulo, published_at=publicado)
                reg.enrich(video_id, title=titulo, published_at=publicado)
                reg.mark_downloaded(
                    video_id, transcript_source=str(meta.get("transcript_origem") or "") or None
                )
                adopted += 1
            log.info(
                "%s%d draft(s) adotados como `baixado`, %d já conhecidos, %d sem video_id.",
                "Dry-run: " if args.dry_run else "",
                adopted,
                known,
                skipped,
            )
            return 0

        if action == "backfill":
            source = Path(args.from_file) if args.from_file else LEGACY_PROCESSADOS
            if not source.exists():
                log.error(
                    "Arquivo de origem não encontrado: %s. "
                    "Passe --from-file <processados.json> ou defina YT_NOTA_PROCESSADOS.",
                    source,
                )
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
    parser.add_argument("--keep-draft", action="store_true", help="Deixa o draft em _processar (modo finalize)")
    parser.add_argument(
        "--skip-body-check",
        action="store_true",
        help="Pula validação das 7 seções do body antes de arquivar o draft (modo finalize)",
    )

    parser.add_argument("--list", action="store_true", help="Lista drafts pendentes")
    parser.add_argument(
        "--triage",
        action="store_true",
        help="Lista drafts pendentes em TSV (video_id, canal, duração, data, título) pra triagem por título",
    )
    parser.add_argument(
        "--discard",
        nargs="+",
        metavar="DRAFT",
        help="Descarta drafts sem sintetizar: grava DESCARTE no registro e arquiva o draft (exige --reason)",
    )
    parser.add_argument(
        "--close",
        metavar="DRAFT",
        help=(
            "Fecha um draft sem fichamento (exige --verdict PROPAGA|ATOMICA, --reason e "
            "--paths): grava transcript, linha no card e registro, e arquiva o draft"
        ),
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        metavar="PAGINA",
        help="Páginas da camada por assunto escritas a partir do draft (modo close)",
    )

    reg_group = parser.add_argument_group("registro durável")
    reg_group.add_argument(
        "--registry",
        choices=("stats", "list", "filter", "mark", "backfill", "adopt-drafts", "events-export"),
        help=(
            "Consulta/administra o registro do que já passou pelo pipeline. "
            "filter = imprime só os ids desconhecidos; mark = grava transição de status; "
            "adopt-drafts = registra como `baixado` os drafts pendentes que o registro não conhece; "
            "events-export = grava o log de eventos em CSV (--events-out)"
        ),
    )
    reg_group.add_argument("--db", help=f"Path do registro (default: {DEFAULT_DB_PATH})")
    reg_group.add_argument("--events-out", help="Arquivo de saída de `--registry events-export`")
    reg_group.add_argument("--events-format", choices=("csv", "ocel2"), default="csv",
                           help="csv (plano, uma linha por evento) ou ocel2 (OCEL 2.0 JSON)")
    reg_group.add_argument("--channel", help="Canal (filtro em `list`, obrigatório em `mark`)")
    reg_group.add_argument(
        "--status",
        choices=("descoberto", "baixado", "curado", "erro", "historico"),
        help="Filtro em `list`; status-alvo em `mark` (default: descoberto)",
    )
    reg_group.add_argument(
        "--verdict",
        choices=("DESCARTE", "PROPAGA", "ATOMICA", "FICHAMENTO"),
        help=(
            "Veredicto do portão de curadoria. Filtro em `list`; obrigatório em "
            "`mark --status curado`; obrigatório em --finalize (FICHAMENTO) e em "
            "--close (PROPAGA ou ATOMICA)"
        ),
    )
    reg_group.add_argument(
        "--reason",
        help=(
            "Razão do veredicto (`mark --status curado`, --finalize, --close, --discard) "
            "ou do erro (`mark --status erro`)"
        ),
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
    if not config.VAULT_CONFIGURED:
        log.error(
            "%s não definido. Aponte para a raiz do seu vault Obsidian no .env "
            "(copie .env.example) ou na variável de ambiente.",
            VAULT_ENV_VAR,
        )
        sys.exit(1)
    if args.list:
        sys.exit(_cmd_list(args))
    if args.triage:
        sys.exit(_cmd_triage(args))
    if args.discard:
        sys.exit(_cmd_discard(args))
    if args.close:
        sys.exit(_cmd_close(args))
    if args.finalize:
        sys.exit(_cmd_finalize(args))
    sys.exit(_cmd_extract(args))


if __name__ == "__main__":
    main()

"""Wrapper do yt-dlp. **Único** módulo do pacote que importa ``yt_dlp``.

Regras que este módulo respeita (e que tornam a Web UI gratuita depois):
nunca imprime, nunca chama ``sys.exit``, não descobre caminhos sozinho, e
nenhuma exceção vinda do callback derruba o download.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadError

from .models import (
    DownloadOptions,
    DownloadReport,
    EventKind,
    ProgressCallback,
    ProgressEvent,
    Status,
    TrackResult,
)

log = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: O outtmpl termina em " [id]"; tiramos isso para exibir um título limpo.
_ID_SUFFIX = re.compile(r"\s*\[[A-Za-z0-9_\-]{3,}\]$")
#: progress_hooks dispara por chunk — centenas de vezes por segundo. Sem este
#: teto, um stream SSE derreteria e o terminal piscaria.
_EMIT_INTERVAL = 0.25


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _first_line(text: str) -> str:
    """Erros do yt-dlp são multi-linha e coloridos — parecem crash para leigo."""
    line = _strip_ansi(str(text)).strip().splitlines()
    clean = line[0].strip() if line else "erro desconhecido"
    for prefix in ("ERROR: ", "ERRO: "):
        if clean.startswith(prefix):
            clean = clean[len(prefix) :]
    return clean


def _title_from_path(path: Path) -> str:
    return _ID_SUFFIX.sub("", path.stem).strip() or path.stem


class _YdlLogger:
    """Captura as linhas de ERROR do yt-dlp.

    Necessário porque com ``ignoreerrors=True`` o ``extract_info`` devolve
    ``None`` em vez de levantar, e o texto do erro vai **apenas** para o logger.
    """

    def __init__(self) -> None:
        self.errors: list[str] = []

    def reset(self) -> None:
        self.errors.clear()

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug("%s", msg)

    def info(self, msg: str) -> None:
        log.debug("%s", msg)

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp aviso: %s", _strip_ansi(msg))

    def error(self, msg: str) -> None:
        self.errors.append(_first_line(msg))
        log.debug("yt-dlp erro: %s", _strip_ansi(msg))


@dataclass(slots=True)
class _State:
    """Estado por URL, capturado por closure pelos hooks."""

    url: str = ""
    index: int = 0
    total: int = 0
    last_emit: float = 0.0
    titles: dict[str, str] = field(default_factory=dict)

    def begin(self, url: str, index: int, total: int) -> None:
        self.url, self.index, self.total = url, index, total
        self.last_emit = 0.0


def _make_emitter(on_progress: ProgressCallback | None):
    """Blinda o callback: um consumidor morto não pode matar o download."""

    def emit(event: ProgressEvent) -> None:
        if on_progress is None:
            return
        try:
            on_progress(event)
        except Exception:  # noqa: BLE001 - callback de terceiro, nunca propaga
            log.debug("callback de progresso levantou exceção", exc_info=True)

    return emit


def _make_progress_hook(emit, state: _State):
    def hook(data: dict) -> None:
        status = data.get("status")
        info = data.get("info_dict") or {}

        if status == "downloading":
            now = time.monotonic()
            if now - state.last_emit < _EMIT_INTERVAL:
                return
            state.last_emit = now
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            done = data.get("downloaded_bytes") or 0
            emit(
                ProgressEvent(
                    kind=EventKind.DOWNLOADING,
                    url=state.url,
                    title=info.get("title"),
                    index=state.index,
                    total=state.total,
                    percent=(done / total * 100) if total else None,
                    downloaded_bytes=done,
                    total_bytes=total,
                    speed=data.get("speed"),
                    eta=data.get("eta"),
                )
            )
        elif status == "finished":
            state.last_emit = 0.0
            emit(
                ProgressEvent(
                    kind=EventKind.POSTPROCESSING,
                    url=state.url,
                    title=info.get("title"),
                    index=state.index,
                    total=state.total,
                    message="convertendo",
                )
            )

    return hook


_PP_LABELS = {
    "FFmpegExtractAudio": "convertendo para MP3",
    "FFmpegMetadata": "gravando metadados",
    "EmbedThumbnail": "embutindo capa",
    "FFmpegThumbnailsConvertor": "preparando capa",
}


def _make_pp_hook(emit, state: _State):
    def hook(data: dict) -> None:
        info = data.get("info_dict") or {}
        status = data.get("status")

        # Best-effort: guarda o título associado ao .mp3 final. O diff de
        # diretório continua sendo a fonte da verdade sobre *quais* arquivos
        # existem; isto só melhora o texto exibido.
        if status == "finished" and data.get("postprocessor") == "FFmpegExtractAudio":
            filepath = info.get("filepath")
            if filepath and info.get("title"):
                state.titles[os.path.basename(filepath)] = info["title"]

        if status != "started":
            return
        label = _PP_LABELS.get(data.get("postprocessor", ""))
        if label:
            emit(
                ProgressEvent(
                    kind=EventKind.POSTPROCESSING,
                    url=state.url,
                    title=info.get("title"),
                    index=state.index,
                    total=state.total,
                    message=label,
                )
            )

    return hook


def _build_ydl_opts(
    dest_dir: Path,
    opt: DownloadOptions,
    logger: _YdlLogger,
    progress_hook,
    pp_hook,
) -> dict:
    postprocessors: list[dict] = []

    if opt.embed_thumbnail:
        # Converte webp/png -> jpg ANTES do download terminar, para o embedder
        # sempre receber um formato que o ID3 aceita. O `when` coloca este PP
        # em outro bucket, então a posição na lista é irrelevante.
        postprocessors.append(
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"}
        )

    # FFmpegExtractAudio PRECISA vir primeiro: tudo depois dele opera no .mp3,
    # não no .webm de origem.
    postprocessors.append(
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": opt.audio_format,
            "preferredquality": opt.bitrate,
            "nopostoverwrites": False,
        }
    )
    if opt.embed_metadata:
        postprocessors.append(
            {
                "key": "FFmpegMetadata",
                "add_metadata": True,
                "add_chapters": False,
                "add_infojson": False,
            }
        )
    if opt.embed_thumbnail:
        # `already_have_thumbnail` é invertido em relação ao nome: True MANTÉM
        # o .jpg no disco. Queremos False, que apaga depois de embutir.
        postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

    cache_root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")

    return {
        # --- seleção ---------------------------------------------------
        "format": "bestaudio/best",
        "noplaylist": opt.no_playlist,
        # --- saída -----------------------------------------------------
        "paths": {"home": str(dest_dir), "temp": str(dest_dir)},
        "outtmpl": {
            "default": "%(title).180B [%(id)s].%(ext)s",
            # Sem isto, uma playlist também baixa a capa DA PLAYLIST como
            # arquivo solto no diretório de trabalho.
            "pl_thumbnail": "",
        },
        # Só remove o que é ilegal no Windows (: ? " < > | *). Acentos ficam:
        # `restrictfilenames` transformaria "Coração" em "Coracao".
        "windowsfilenames": True,
        "restrictfilenames": False,
        "overwrites": False,
        # Obrigatório: EmbedThumbnail EMBUTE um arquivo, não o baixa. A CLI do
        # yt-dlp liga esta flag por você; a API Python não.
        "writethumbnail": opt.embed_thumbnail,
        # --- pós-processamento ------------------------------------------
        "postprocessors": postprocessors,
        "prefer_ffmpeg": True,
        "keepvideo": False,
        # --- resiliência -------------------------------------------------
        "ignoreerrors": True,  # uma entrada morta não mata a playlist inteira
        "retries": opt.retries,
        "fragment_retries": opt.retries,
        "extractor_retries": 3,
        "socket_timeout": opt.socket_timeout,
        "sleep_interval": opt.sleep_interval,
        "concurrent_fragment_downloads": 4,
        # --- saída estruturada -------------------------------------------
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,  # a renderização é nossa
        "consoletitle": False,
        "color": {"stdout": "no_color", "stderr": "no_color"},
        "logger": logger,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [pp_hook],
        # --- diversos ------------------------------------------------------
        "cachedir": os.path.join(cache_root, "yt-dlp"),
        "cookiefile": str(opt.cookiefile) if opt.cookiefile else None,
        "simulate": opt.simulate,
        "skip_download": opt.simulate,
    }


def _snapshot(directory: Path, suffix: str) -> set[Path]:
    return {
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() == f".{suffix.lower()}"
    }


def _flatten(info: dict | None) -> list[dict]:
    """Achata o retorno do extract_info em uma lista de entradas reais.

    Com ``ignoreerrors=True`` as entradas que falharam viram ``None`` dentro de
    ``entries``, e ``entries`` pode ser um gerador preguiçoso.
    """
    if not info:
        return []
    if info.get("_type") == "playlist":
        return [entry for entry in (info.get("entries") or []) if entry]
    return [info]


def download_audio(
    urls: Sequence[str],
    dest_dir: Path,
    options: DownloadOptions | None = None,
    on_progress: ProgressCallback | None = None,
) -> DownloadReport:
    """Baixa ``urls`` como áudio dentro de ``dest_dir``.

    ``dest_dir`` deve ser um diretório de trabalho recém-criado e vazio: a
    detecção de quais arquivos cada URL gerou é feita por **diff de diretório**.

    ``on_progress`` pode ser chamado de qualquer thread (ver ``ProgressCallback``).
    """
    options = options or DownloadOptions()
    dest_dir.mkdir(parents=True, exist_ok=True)

    emit = _make_emitter(on_progress)
    state = _State()
    logger = _YdlLogger()
    report = DownloadReport()
    total = len(urls)

    ydl_opts = _build_ydl_opts(
        dest_dir,
        options,
        logger,
        _make_progress_hook(emit, state),
        _make_pp_hook(emit, state),
    )

    emit(ProgressEvent(kind=EventKind.JOB_START, total=total))

    # Uma única instância para todas as URLs: reusa conexões e o cache de
    # extractor, que é uma diferença grande em lotes grandes.
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for index, url in enumerate(urls, 1):
            state.begin(url, index, total)
            logger.reset()
            emit(ProgressEvent(kind=EventKind.URL_START, url=url, index=index, total=total))

            before = _snapshot(dest_dir, options.audio_format)
            failure: str | None = None
            info: dict | None = None

            try:
                info = ydl.extract_info(url, download=not options.simulate)
            except DownloadError as exc:
                failure = _first_line(exc)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - extractor de terceiro
                failure = f"{type(exc).__name__}: {exc}"
                log.debug("falha inesperada em %s", url, exc_info=True)

            if options.simulate:
                _record_simulation(report, url, index, total, info, logger, failure, emit)
                continue

            produced = sorted(_snapshot(dest_dir, options.audio_format) - before)

            if produced:
                for path in produced:
                    title = state.titles.get(path.name) or _title_from_path(path)
                    size = path.stat().st_size
                    report.results.append(
                        TrackResult(
                            url=url, status=Status.OK, title=title, path=path, size=size
                        )
                    )
                    emit(
                        ProgressEvent(
                            kind=EventKind.TRACK_DONE,
                            url=url,
                            title=title,
                            index=index,
                            total=total,
                            total_bytes=size,
                        )
                    )
                # Playlist parcialmente falha: registra os erros das entradas
                # mortas, mas NÃO marca a URL como reprocessável — um retry
                # rebaixaria a playlist inteira.
                for message in logger.errors:
                    report.results.append(
                        TrackResult(url=url, status=Status.FAILED, error=message)
                    )
                    emit(
                        ProgressEvent(
                            kind=EventKind.URL_FAILED,
                            url=url,
                            index=index,
                            total=total,
                            message=message,
                        )
                    )
            else:
                message = failure or (
                    logger.errors[0] if logger.errors else "nenhum áudio foi produzido"
                )
                report.results.append(
                    TrackResult(url=url, status=Status.FAILED, error=message)
                )
                report.failed_urls.append(url)
                emit(
                    ProgressEvent(
                        kind=EventKind.URL_FAILED,
                        url=url,
                        index=index,
                        total=total,
                        message=message,
                    )
                )

    emit(ProgressEvent(kind=EventKind.JOB_DONE, total=total))
    return report


def _record_simulation(
    report: DownloadReport,
    url: str,
    index: int,
    total: int,
    info: dict | None,
    logger: _YdlLogger,
    failure: str | None,
    emit,
) -> None:
    """No modo simulação nada vai para o disco, então o diff de diretório não
    serve: os títulos vêm do próprio info_dict."""
    entries = _flatten(info)
    if entries:
        for entry in entries:
            title = entry.get("title") or entry.get("id") or url
            report.results.append(TrackResult(url=url, status=Status.OK, title=title))
            emit(
                ProgressEvent(
                    kind=EventKind.TRACK_DONE,
                    url=url,
                    title=title,
                    index=index,
                    total=total,
                )
            )
        return

    message = failure or (logger.errors[0] if logger.errors else "nada encontrado")
    report.results.append(TrackResult(url=url, status=Status.FAILED, error=message))
    report.failed_urls.append(url)
    emit(
        ProgressEvent(
            kind=EventKind.URL_FAILED, url=url, index=index, total=total, message=message
        )
    )

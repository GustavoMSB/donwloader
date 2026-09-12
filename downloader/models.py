"""Tipos de dados compartilhados entre o núcleo e as interfaces.

Este módulo é deliberadamente livre de dependências: não importa ``yt_dlp``,
não faz I/O e não conhece a CLI. Isso permite que um futuro ``api.py``
(FastAPI) serialize estes objetos diretamente com ``dataclasses.asdict()``,
sem uma camada paralela de DTOs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    """Resultado de uma faixa. `str`-Enum => serializável em JSON de graça."""

    OK = "ok"
    FAILED = "failed"


class EventKind(str, Enum):
    """Tipo de evento de progresso emitido pelo núcleo."""

    JOB_START = "job_start"
    URL_START = "url_start"
    DOWNLOADING = "downloading"
    POSTPROCESSING = "postprocessing"
    TRACK_DONE = "track_done"
    URL_FAILED = "url_failed"
    ZIPPING = "zipping"
    JOB_DONE = "job_done"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """Um instante do progresso.

    O núcleo emite estes objetos; quem renderiza decide o que fazer com eles.
    A CLI desenha uma barra no terminal, uma futura API os empurra por SSE.
    """

    kind: EventKind
    url: str | None = None
    title: str | None = None
    index: int | None = None  # posição (1-based) da URL na lista de entrada
    total: int | None = None  # total de URLs de entrada
    percent: float | None = None  # 0..100; None quando o tamanho é desconhecido
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed: float | None = None  # bytes/s
    eta: int | None = None  # segundos
    message: str | None = None


#: Assinatura do callback de progresso.
#:
#: IMPORTANTE: pode ser chamado de **qualquer thread**. O yt-dlp é bloqueante,
#: então o núcleo permanece síncrono e um futuro layer async o roda via
#: ``asyncio.to_thread`` — o callback precisará de ``loop.call_soon_threadsafe``.
ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True, slots=True)
class DownloadOptions:
    """Como baixar. Independente de interface."""

    audio_format: str = "mp3"
    bitrate: str = "192"
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    no_playlist: bool = False
    cookiefile: Path | None = None
    retries: int = 5
    socket_timeout: int = 30
    sleep_interval: float = 0.0
    simulate: bool = False


@dataclass(slots=True)
class TrackResult:
    """Uma faixa baixada (ou uma URL que falhou)."""

    url: str
    status: Status
    title: str | None = None
    path: Path | None = None
    size: int | None = None
    error: str | None = None


@dataclass(slots=True)
class DownloadReport:
    """O que aconteceu com um lote de URLs."""

    results: list[TrackResult] = field(default_factory=list)
    #: URLs que não produziram *nenhum* arquivo — as únicas seguras de
    #: realimentar no programa (uma playlist parcialmente falha não entra aqui,
    #: senão o retry baixaria tudo de novo).
    failed_urls: list[str] = field(default_factory=list)

    @property
    def ok(self) -> list[TrackResult]:
        return [r for r in self.results if r.status is Status.OK]

    @property
    def failed(self) -> list[TrackResult]:
        return [r for r in self.results if r.status is Status.FAILED]

    @property
    def files(self) -> list[Path]:
        return [r.path for r in self.results if r.path is not None]


@dataclass(slots=True)
class JobResult:
    """O resultado completo de uma execução: relatório + o .zip gerado."""

    report: DownloadReport
    zip_path: Path | None = None
    zip_size: int | None = None
    started_at: str = ""
    finished_at: str = ""
    duplicates_ignored: int = 0
    work_dir_kept: Path | None = None

    @property
    def exit_code(self) -> int:
        if not self.report.ok:
            return 3  # nenhum sucesso -> nenhum zip
        if self.report.failed:
            return 1  # parcial
        return 0

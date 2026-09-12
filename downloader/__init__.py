"""MP3 Downloader — baixa áudio de URLs/playlists e entrega um único .zip.

O núcleo (``core``/``pipeline``) é independente de interface: a CLI de hoje e
uma Web UI amanhã chamam as mesmas funções.
"""

from .models import (
    DownloadOptions,
    DownloadReport,
    EventKind,
    JobResult,
    ProgressEvent,
    Status,
    TrackResult,
)

__version__ = "0.1.0"

__all__ = [
    "DownloadOptions",
    "DownloadReport",
    "EventKind",
    "JobResult",
    "ProgressEvent",
    "Status",
    "TrackResult",
    "__version__",
]

"""Exceções tipadas.

O núcleo nunca chama ``sys.exit``: ele levanta uma destas. A ``cli.py`` mapeia
cada uma para um exit code; uma futura ``api.py`` mapeia as mesmas para status
HTTP. Esse é o motivo de elas existirem.
"""

from __future__ import annotations


class DownloaderError(Exception):
    """Base de tudo que este pacote levanta intencionalmente."""


class NoInputError(DownloaderError):
    """Entrada ausente, inexistente ou sem nenhuma URL válida. -> exit 4 / HTTP 400."""


class EnvironmentProblem(DownloaderError):
    """Ambiente inadequado: ffmpeg ausente, diretório não gravável. -> exit 5 / HTTP 500."""

"""Resolve os tokens de entrada em uma lista de URLs.

Um token é uma URL ou o caminho de um arquivo ``.txt`` com uma URL por linha.
Funções puras (só leitura de arquivo), fáceis de testar.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from .errors import NoInputError

log = logging.getLogger(__name__)


def is_url(token: str) -> bool:
    return token.strip().lower().startswith(("http://", "https://"))


def _resolve_path(token: str, input_dir: Path | None) -> Path | None:
    """Caminho literal primeiro, depois relativo a ``input_dir``.

    É o que faz ``lista.txt`` e ``input/lista.txt`` funcionarem igual, sem o
    usuário precisar pensar em caminhos ``/app/`` dentro do container.
    """
    candidate = Path(token).expanduser()
    if candidate.is_file():
        return candidate
    if input_dir is not None:
        candidate = (input_dir / token).expanduser()
        if candidate.is_file():
            return candidate
    return None


def read_url_file(path: Path) -> list[str]:
    """Lê um .txt de URLs.

    ``utf-8-sig`` remove o BOM que o Bloco de Notas do Windows insere — sem
    isso a primeira URL do arquivo viria com um caractere invisível grudado.
    Linhas vazias e comentários ``#`` são ignorados; linhas que não parecem
    URL geram aviso e são puladas (um erro de digitação não deve abortar um
    arquivo de 200 linhas).
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise NoInputError(f"não foi possível ler '{path}': {exc}") from exc

    urls: list[str] = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not is_url(line):
            log.warning("%s:%d ignorada (não parece uma URL): %s", path.name, lineno, line)
            continue
        urls.append(line)
    return urls


def resolve_inputs(
    tokens: Sequence[str],
    input_dir: Path | None = None,
) -> tuple[list[str], int]:
    """Expande tokens em URLs únicas, preservando a ordem.

    Retorna ``(urls, duplicadas_ignoradas)``.
    """
    if not tokens:
        raise NoInputError("nenhuma URL ou arquivo informado")

    collected: list[str] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if is_url(token):
            collected.append(token)
            continue

        path = _resolve_path(token, input_dir)
        if path is None:
            extra = f" (procurei também em {input_dir}/)" if input_dir else ""
            raise NoInputError(
                f"'{token}' não é uma URL nem um arquivo existente{extra}"
            )
        collected.extend(read_url_file(path))

    seen: set[str] = set()
    urls: list[str] = []
    for url in collected:
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    if not urls:
        raise NoInputError("nenhuma URL válida encontrada na entrada")

    return urls, len(collected) - len(urls)

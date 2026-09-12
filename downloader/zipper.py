"""Empacota os arquivos baixados em um único .zip."""

from __future__ import annotations

import itertools
import logging
import os
import zipfile
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger(__name__)


def unique_arcname(name: str, taken: set[str]) -> str:
    """Desempata nomes repetidos com ' (2)', ' (3)', ...

    A comparação é ``casefold()`` porque Windows e macOS tratam nomes como
    case-insensitive: ``Musica.mp3`` e ``MUSICA.mp3`` colidiriam na extração.
    """
    if name.casefold() not in taken:
        taken.add(name.casefold())
        return name

    stem, dot, ext = name.rpartition(".")
    if not stem:  # nome sem extensão
        stem, dot, ext = name, "", ""
    for counter in itertools.count(2):
        candidate = f"{stem} ({counter}){dot}{ext}"
        if candidate.casefold() not in taken:
            taken.add(candidate.casefold())
            return candidate
    raise AssertionError("inalcançável")  # pragma: no cover


def create_zip(files: Sequence[Path], dest_dir: Path, name: str) -> Path:
    """Escreve ``dest_dir/name.zip`` contendo ``files``.

    Grava primeiro em um arquivo temporário no mesmo diretório e faz
    ``os.replace``, que é atômico dentro do mesmo filesystem: um Ctrl-C nunca
    deixa um .zip pela metade com cara de completo.

    ``ZIP_STORED`` e não ``ZIP_DEFLATED``: MP3 já é entropy-coded, deflate
    ganharia 0–2% queimando CPU em cada byte.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"{name}.zip"
    tmp = dest_dir / f".{name}.zip.tmp"
    taken: set[str] = set()

    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path in files:
                arcname = unique_arcname(path.name, taken)
                log.debug("zip += %s", arcname)
                zf.write(path, arcname=arcname)
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    return final

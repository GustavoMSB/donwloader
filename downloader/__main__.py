"""Permite `python -m downloader` — é o ENTRYPOINT da imagem Docker."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

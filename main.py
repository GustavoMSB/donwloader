#!/usr/bin/env python3
"""MP3 Downloader — ponto de entrada.

Use `./dl.sh` para rodar dentro do Docker (recomendado: o container traz o
ffmpeg). Este arquivo serve para rodar direto, se você já tiver ffmpeg e
yt-dlp instalados.
"""

import sys

from downloader.cli import main

if __name__ == "__main__":
    sys.exit(main())

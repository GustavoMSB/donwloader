# syntax=docker/dockerfile:1
#
# Base Debian slim, não Alpine: o ffmpeg do apt Debian é a build de referência
# (todos os codecs que o yt-dlp pode invocar) e Alpine é musl — o extra
# yt-dlp[default] puxa pycryptodomex/brotli, e qualquer adição futura sem
# wheel musl viraria compilação C em todo build.
FROM python:3.12-slim-bookworm

ARG HOST_UID=1000
ARG HOST_GID=1000
# Vazio = instala a última versão estável. Preencha para builds reprodutíveis.
ARG YTDLP_VERSION=
# YTDLP_PRE=1 instala o nightly (fix já mergeado mas ainda sem release).
ARG YTDLP_PRE=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/app \
    XDG_CACHE_HOME=/app/.cache \
    INPUT_DIR=/app/input \
    OUTPUT_DIR=/app/output

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Usuário com o MESMO uid/gid do host: sem isso, os arquivos que o container
# escreve no bind mount saem como root e o usuário só apaga com sudo.
RUN groupadd -g ${HOST_GID} app 2>/dev/null || true \
 && useradd -u ${HOST_UID} -g ${HOST_GID} -M -d /app -s /usr/sbin/nologin app 2>/dev/null || true

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ -n "${YTDLP_VERSION}" ]; then \
        pip install --no-cache-dir --upgrade "yt-dlp[default]==${YTDLP_VERSION}"; \
    elif [ -n "${YTDLP_PRE}" ]; then \
        pip install --no-cache-dir --pre --upgrade "yt-dlp[default]"; \
    fi \
 && echo "yt-dlp $(yt-dlp --version)" \
 && ffmpeg -version | head -n1

COPY --chown=${HOST_UID}:${HOST_GID} downloader/ ./downloader/
COPY --chown=${HOST_UID}:${HOST_GID} main.py ./

# Pré-criar e chownar: um volume nomeado herda dono e modo do diretório da
# imagem no ponto de montagem, então o cache do yt-dlp nasce com o dono certo.
RUN mkdir -p /app/input /app/output /app/.cache \
 && chown -R ${HOST_UID}:${HOST_GID} /app/input /app/output /app/.cache

USER ${HOST_UID}:${HOST_GID}

ENTRYPOINT ["python", "-m", "downloader"]
CMD ["--help"]

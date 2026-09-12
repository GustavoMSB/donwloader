#!/usr/bin/env bash
#
# Comando do dia a dia.
#
#   ./dl.sh 'https://www.youtube.com/playlist?list=PLxxxx'
#   ./dl.sh minhas_musicas.txt
#   ./dl.sh --dry-run lista.txt
#
set -euo pipefail

# readlink -f: funciona chamado de outro diretório ou por symlink em ~/bin.
cd "$(dirname "$(readlink -f "$0")")"

# Antes de qualquer `docker compose`: se o diretório do bind mount não existir,
# o daemon do Docker o cria como root e o container (uid 1000) não escreve nele.
mkdir -p input output

# HOST_UID, nunca UID: UID é readonly no bash e `export UID=...` mataria o
# script sob `set -e`. E o compose não enxerga o $UID do shell de qualquer
# forma (o bash define mas não exporta), por isso gravamos no .env.
if [[ ! -f .env ]]; then
  printf 'HOST_UID=%s\nHOST_GID=%s\nTZ=%s\nYTDLP_VERSION=\n' \
    "$(id -u)" "$(id -g)" "${TZ:-America/Sao_Paulo}" > .env
  echo ">> .env criado (uid=$(id -u) gid=$(id -g))"
fi

if [[ -z "$(docker images -q mp3dl:local 2>/dev/null)" ]]; then
  echo ">> primeira execução: construindo a imagem (leva alguns minutos)..."
  docker compose build
fi

# --rm não é opcional: sem ele o `docker compose run` acumula containers
# parados para sempre.
exec docker compose run --rm downloader "$@"

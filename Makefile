SHELL    := /bin/bash
HOST_UID := $(shell id -u)
HOST_GID := $(shell id -g)
COMPOSE  := docker compose
RUN      := $(COMPOSE) run --rm downloader

.DEFAULT_GOAL := help
.PHONY: help setup build rebuild dl batch update update-nightly shell version clean

help:  ## mostra esta ajuda
	@echo "MP3 Downloader"
	@echo ""
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  No dia a dia use ./dl.sh — o make trunca URLs que contenham '#'."

setup:
	@mkdir -p input output
	@[ -f .env ] || { \
	  printf 'HOST_UID=%s\nHOST_GID=%s\nTZ=America/Sao_Paulo\nYTDLP_VERSION=\n' \
	    "$(HOST_UID)" "$(HOST_GID)" > .env; \
	  echo ">> .env criado"; }

build: setup  ## constrói a imagem
	$(COMPOSE) build

rebuild: setup  ## reconstrói do zero (sem cache)
	$(COMPOSE) build --no-cache --pull

dl: setup  ## baixa uma URL -> make dl URL='https://...'
	@[ -n "$(URL)" ] || { echo "uso: make dl URL='https://...'"; exit 2; }
	$(RUN) "$(URL)"

batch: setup  ## baixa de um .txt -> make batch FILE=lista.txt
	@[ -n "$(FILE)" ] || { echo "uso: make batch FILE=lista.txt"; exit 2; }
	$(RUN) "$(FILE)"

update: setup  ## fixa/atualiza o yt-dlp -> make update YTDLP_VERSION=2025.12.01
	$(COMPOSE) build --build-arg YTDLP_VERSION=$(YTDLP_VERSION)

update-nightly: setup  ## usa o yt-dlp nightly (quando o fix ainda não saiu em release)
	$(COMPOSE) build --build-arg YTDLP_VERSION= --build-arg YTDLP_PRE=1

version: setup  ## mostra as versões dentro da imagem
	$(COMPOSE) run --rm --entrypoint sh downloader -c 'yt-dlp --version; ffmpeg -version | head -n1'

shell: setup  ## abre um shell dentro do container
	$(COMPOSE) run --rm --entrypoint bash downloader

clean:  ## remove os .zip gerados em output/
	rm -f output/*.zip

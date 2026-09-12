"""Interface de linha de comando.

Este é o **único** módulo do pacote que imprime e que chama ``sys.exit``.
Todo o resto emite eventos e levanta exceções — é o que permite plugar uma
Web UI depois sem refatorar o núcleo.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import sys
from pathlib import Path

from .errors import DownloaderError, EnvironmentProblem, NoInputError
from .models import DownloadOptions, EventKind, JobResult, ProgressEvent
from .pipeline import human_size, run_job

log = logging.getLogger("downloader")

RULE = "─"
OK_MARK = "✓"
FAIL_MARK = "✗"


def _term_width() -> int:
    return max(40, min(shutil.get_terminal_size((80, 24)).columns, 100))


def _display(path: Path) -> str:
    """Dentro do container o zip está em ``/app/output/``; no host, o mesmo
    arquivo está em ``./output/``. Mostrar o caminho do container confundiria,
    então o compose informa o rótulo do host em ``HOST_OUTPUT_DIR``."""
    label = os.environ.get("HOST_OUTPUT_DIR")
    if not label:
        return str(path)
    return f"{label.rstrip('/')}/{path.name}"


def _eta(seconds: float | None) -> str:
    if not seconds:
        return "--:--"
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class Renderer:
    """Desenha o progresso no terminal.

    Em TTY usa ``\\r`` para atualizar a linha no lugar. Sem TTY (``-T``, CI,
    pipe) degrada para uma linha a cada 10% — o fallback não pode gerar
    milhares de linhas de log.
    """

    def __init__(self, stream=sys.stdout) -> None:
        self.stream = stream
        self.tty = stream.isatty()
        self._dirty = False  # há uma linha transitória para limpar?
        self._last_decile = -1

    def _clear(self) -> None:
        if self._dirty and self.tty:
            self.stream.write("\r" + " " * (_term_width() - 1) + "\r")
        self._dirty = False

    def _transient(self, text: str) -> None:
        if self.tty:
            self.stream.write("\r" + text[: _term_width() - 1].ljust(_term_width() - 1))
            self.stream.flush()
            self._dirty = True

    def line(self, text: str = "") -> None:
        self._clear()
        self.stream.write(text + "\n")
        self.stream.flush()

    def __call__(self, event: ProgressEvent) -> None:
        kind = event.kind
        tag = f"[{event.index:>2}/{event.total}]" if event.index else ""

        if kind is EventKind.JOB_START:
            self.line(f"MP3 Downloader — {event.total} URL(s)")
            self.line(RULE * _term_width())

        elif kind is EventKind.URL_START:
            self._last_decile = -1
            self._transient(f"{tag}   preparando: {event.url}")

        elif kind is EventKind.DOWNLOADING:
            self._render_download(tag, event)

        elif kind is EventKind.POSTPROCESSING:
            label = event.title or event.url or ""
            self._transient(f"{tag}   {event.message}: {label}")

        elif kind is EventKind.TRACK_DONE:
            # Sem total_bytes (--dry-run) não há tamanho a mostrar.
            size = human_size(event.total_bytes).rjust(9) if event.total_bytes else ""
            title = event.title or "(sem título)"
            room = _term_width() - len(tag) - len(size) - 6
            self.line(f"{tag} {OK_MARK} {title[:room].ljust(room)} {size}".rstrip())

        elif kind is EventKind.URL_FAILED:
            self.line(f"{tag} {FAIL_MARK} {event.url}")
            self.line(f"{' ' * (len(tag) + 3)}{event.message}")

        elif kind is EventKind.ZIPPING:
            self._transient("Compactando...")

        elif kind is EventKind.JOB_DONE:
            self._clear()

    def _render_download(self, tag: str, event: ProgressEvent) -> None:
        title = event.title or event.url or ""
        percent = event.percent
        speed = f"{human_size(event.speed)}/s" if event.speed else ""

        if not self.tty:
            # Uma linha a cada 10%, e só isso.
            decile = int((percent or 0) // 10)
            if decile <= self._last_decile:
                return
            self._last_decile = decile
            self.line(f"{tag}   {title[:50]} {percent or 0:.0f}%")
            return

        if percent is None:
            self._transient(f"{tag}   {title} — {human_size(event.downloaded_bytes)} {speed}")
            return

        width = 20
        filled = int(width * percent / 100)
        bar = "█" * filled + "░" * (width - filled)
        head = f"{tag}   {title}"
        tail = f"{bar} {percent:3.0f}%  {speed}  eta {_eta(event.eta)}"
        room = _term_width() - len(tail) - 4
        self._transient(f"{head[:room].ljust(room)}  {tail}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mp3dl",
        description="Baixa áudio de URLs ou playlists e entrega um único .zip de MP3.",
        epilog=(
            "Exemplos:\n"
            "  ./dl.sh 'https://www.youtube.com/playlist?list=PLxxxx'\n"
            "  ./dl.sh minhas_musicas.txt\n"
            "  ./dl.sh --dry-run lista.txt\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        metavar="URL|ARQUIVO.txt",
        help="uma ou mais URLs, e/ou arquivos .txt com uma URL por linha",
    )
    parser.add_argument("-b", "--bitrate", default="192", help="kbps do MP3 (padrão: 192)")
    parser.add_argument(
        "--no-playlist",
        action="store_true",
        help="baixa só o vídeo do link, mesmo que ele faça parte de uma playlist",
    )
    parser.add_argument(
        "--no-thumbnail", action="store_true", help="não embute a capa no MP3"
    )
    parser.add_argument(
        "--no-metadata", action="store_true", help="não grava título/artista/álbum"
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="lista o que seria baixado e sai"
    )
    parser.add_argument(
        "--keep-files",
        action="store_true",
        help="mantém os .mp3 soltos no diretório de trabalho (depuração)",
    )
    parser.add_argument(
        "--cookies",
        metavar="ARQUIVO",
        help="cookies.txt de um navegador logado (contorna o 'confirm you are not a bot')",
    )
    parser.add_argument(
        "--sleep", type=float, default=0.0, metavar="SEG", help="pausa entre downloads"
    )
    parser.add_argument("--retries", type=int, default=5, help="tentativas por faixa (padrão: 5)")
    parser.add_argument("--name", help="nome do .zip (sem extensão)")
    parser.add_argument(
        "-o",
        "--output",
        default=os.environ.get("OUTPUT_DIR", "output"),
        metavar="DIR",
        help="onde gravar o .zip (padrão: output/)",
    )
    parser.add_argument(
        "--input-dir",
        default=os.environ.get("INPUT_DIR", "input"),
        metavar="DIR",
        help="onde procurar arquivos .txt informados por nome (padrão: input/)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    return parser


def _summary(renderer: Renderer, result: JobResult, dry_run: bool) -> None:
    report = result.report
    renderer.line(RULE * _term_width())

    if dry_run:
        renderer.line(
            f"Simulação: {len(report.ok)} faixa(s) seriam baixadas, "
            f"{len(report.failed)} falha(s). Nenhum arquivo foi gravado."
        )
    elif result.zip_path is not None:
        plural = "s" if len(report.ok) != 1 else ""
        renderer.line(
            f"Concluído: {len(report.ok)} faixa{plural}, {len(report.failed)} falha(s)"
        )
        renderer.line(
            f"Arquivo: {_display(result.zip_path)}  ({human_size(result.zip_size)})"
        )
    else:
        renderer.line("Nenhum download concluído — nenhum arquivo .zip foi gerado.")

    if result.duplicates_ignored:
        renderer.line(f"URLs duplicadas ignoradas: {result.duplicates_ignored}")

    if report.failed:
        renderer.line()
        renderer.line("Falhas:")
        for track in report.failed:
            renderer.line(f"  {track.url}")
            renderer.line(f"    -> {track.error}")
        if result.zip_path is not None and report.failed_urls:
            renderer.line()
            renderer.line("As URLs que falharam por completo estão em _falhas.txt dentro do zip.")

    if result.work_dir_kept is not None:
        renderer.line(f"Arquivos mantidos em: {_display(result.work_dir_kept)}")


def _on_sigterm(signum, frame) -> None:  # noqa: ARG001
    """`docker stop` manda SIGTERM. Convertendo em KeyboardInterrupt, o mesmo
    `finally` do pipeline limpa o diretório de trabalho nos dois casos."""
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not args.inputs:
        parser.print_help()
        return 4

    signal.signal(signal.SIGTERM, _on_sigterm)

    options = DownloadOptions(
        bitrate=args.bitrate,
        embed_thumbnail=not args.no_thumbnail,
        embed_metadata=not args.no_metadata,
        no_playlist=args.no_playlist,
        cookiefile=Path(args.cookies) if args.cookies else None,
        retries=args.retries,
        sleep_interval=args.sleep,
        simulate=args.dry_run,
    )

    renderer = Renderer()

    try:
        result = run_job(
            inputs=args.inputs,
            output_dir=Path(args.output),
            options=options,
            on_progress=renderer,
            keep_files=args.keep_files,
            input_dir=Path(args.input_dir),
            zip_name=args.name,
        )
    except NoInputError as exc:
        renderer.line(f"Entrada inválida: {exc}")
        return 4
    except EnvironmentProblem as exc:
        renderer.line(f"Ambiente: {exc}")
        return 5
    except KeyboardInterrupt:
        renderer.line()
        renderer.line("Interrompido. Arquivos parciais foram removidos.")
        return 130
    except DownloaderError as exc:  # pragma: no cover - rede de segurança
        renderer.line(f"Erro: {exc}")
        return 5

    _summary(renderer, result, args.dry_run)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

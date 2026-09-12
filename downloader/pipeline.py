"""Orquestração: resolve a entrada -> baixa -> empacota -> limpa.

Este é o ponto de entrada que tanto a CLI quanto uma futura API chamam.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .core import download_audio
from .errors import EnvironmentProblem
from .models import (
    DownloadOptions,
    DownloadReport,
    EventKind,
    JobResult,
    ProgressCallback,
    ProgressEvent,
)
from .sources import resolve_inputs
from .zipper import create_zip

log = logging.getLogger(__name__)

REPORT_NAME = "_relatorio.txt"
FAILURES_NAME = "_falhas.txt"
#: Ficar sem espaço no meio do lote produz erros de ffmpeg bem confusos.
LOW_DISK_BYTES = 1 * 1024**3


def human_size(num: float | None) -> str:
    if not num:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{num:.0f} B"
        num /= 1024
    return f"{num:.1f} GB"  # pragma: no cover


def preflight(output_dir: Path) -> None:
    """Falha cedo e com mensagem clara, em vez de deixar o yt-dlp falhar
    obscuramente no primeiro postprocessor."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise EnvironmentProblem(
            "ffmpeg/ffprobe não encontrados. Rode pelo Docker (./dl.sh) ou "
            "instale o ffmpeg no sistema."
        )

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EnvironmentProblem(f"não consegui criar '{output_dir}': {exc}") from exc

    probe = output_dir / ".escrita-ok"
    try:
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise EnvironmentProblem(
            f"sem permissão de escrita em '{output_dir}': {exc}"
        ) from exc

    free = shutil.disk_usage(output_dir).free
    if free < LOW_DISK_BYTES:
        log.warning("pouco espaço livre em %s: %s", output_dir, human_size(free))


def _write_manifests(
    work_dir: Path, report: DownloadReport, started: datetime, duplicates: int
) -> list[Path]:
    """Relatório e lista de falhas vão DENTRO do zip — ``output/`` fica só com
    o .zip, como pedido."""
    written: list[Path] = []

    lines = [
        "MP3 Downloader",
        f"Gerado em: {started.strftime('%d/%m/%Y %H:%M:%S')}",
        f"Faixas baixadas: {len(report.ok)}",
        f"Falhas: {len(report.failed)}",
    ]
    if duplicates:
        lines.append(f"URLs duplicadas ignoradas: {duplicates}")
    lines.append("")
    lines.append("-" * 60)

    for position, track in enumerate(report.ok, 1):
        lines.append(f"{position:3d}. {track.title}")
        lines.append(f"     {human_size(track.size)}  |  {track.url}")

    if report.failed:
        lines += ["", "-" * 60, "FALHAS", ""]
        for track in report.failed:
            lines.append(f"  {track.url}")
            lines.append(f"    -> {track.error}")

    report_path = work_dir / REPORT_NAME
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    written.append(report_path)

    if report.failed_urls:
        failures_path = work_dir / FAILURES_NAME
        failures_path.write_text(
            "# URLs que falharam por completo — extraia este arquivo e rode:\n"
            "#   ./dl.sh _falhas.txt\n" + "\n".join(report.failed_urls) + "\n",
            encoding="utf-8",
        )
        written.append(failures_path)

    return written


def run_job(
    inputs: Sequence[str],
    output_dir: Path,
    options: DownloadOptions | None = None,
    on_progress: ProgressCallback | None = None,
    keep_files: bool = False,
    input_dir: Path | None = None,
    zip_name: str | None = None,
) -> JobResult:
    """Executa um trabalho completo e devolve o resultado.

    Não imprime nada e não chama ``sys.exit``: levanta ``NoInputError`` /
    ``EnvironmentProblem`` e deixa a interface decidir o que fazer.
    """
    options = options or DownloadOptions()
    output_dir = output_dir.expanduser()

    preflight(output_dir)
    urls, duplicates = resolve_inputs(inputs, input_dir)

    started = datetime.now()
    stamp = started.strftime("%Y-%m-%d_%H-%M-%S")

    # Diretório de trabalho dedicado: "apagar os mp3 soltos" vira um rmtree de
    # algo que eu mesmo criei, nunca um glob em output/*.mp3 — que um dia
    # apagaria um arquivo do usuário. Fica no mesmo filesystem do zip final,
    # o que torna o os.replace do zipper atômico.
    work_dir = output_dir / f".trabalho-{stamp}"
    work_dir.mkdir(parents=True)

    try:
        report = download_audio(urls, work_dir, options, on_progress)

        result = JobResult(
            report=report,
            started_at=started.isoformat(timespec="seconds"),
            duplicates_ignored=duplicates,
            work_dir_kept=work_dir if keep_files else None,
        )

        if options.simulate or not report.ok:
            result.finished_at = datetime.now().isoformat(timespec="seconds")
            return result

        if on_progress is not None:
            try:
                on_progress(ProgressEvent(kind=EventKind.ZIPPING))
            except Exception:  # noqa: BLE001
                log.debug("callback de progresso levantou exceção", exc_info=True)

        manifests = _write_manifests(work_dir, report, started, duplicates)
        zip_path = create_zip(
            [*report.files, *manifests], output_dir, zip_name or f"mp3_{stamp}"
        )

        result.zip_path = zip_path
        result.zip_size = zip_path.stat().st_size
        result.finished_at = datetime.now().isoformat(timespec="seconds")
        return result
    finally:
        # Roda também no Ctrl-C e no `docker stop`: nenhum .mp3 solto e nenhum
        # .part sobrevive em output/, que fica só com o .zip.
        if keep_files:
            log.info("arquivos mantidos em %s", work_dir)
        else:
            shutil.rmtree(work_dir, ignore_errors=True)

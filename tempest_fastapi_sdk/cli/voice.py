"""``tempest voice`` — diarize and transcribe from the command line.

Tuning a diarization pipeline is a loop of *change a setting, listen to
the result*, and going through the service for each round is the slow
way. These commands close it:

    tempest voice models                       # fetch the models
    tempest voice diarize reuniao.wav -n 2     # who spoke when
    tempest voice transcribe reuniao.wav -n 2  # who said what

``diarize`` runs without Whisper, which makes it the fast way to check
whether the speaker count and threshold are right before paying for
transcription.

Needs the ``[genai-diarization]`` extra; ``transcribe`` also needs
``[genai-audio]``. A missing extra exits 2 with the install line, never
a traceback.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

voice_app: typer.Typer = typer.Typer(
    name="voice",
    help="Diarize and transcribe conversations.",
    no_args_is_help=True,
)


def _fail(message: str) -> None:
    """Print an error and exit with the CLI's validation code.

    Args:
        message (str): What went wrong and how to fix it.

    Raises:
        typer.Exit: Always, with code 2.
    """
    typer.secho(f"error: {message}", fg="red", err=True)
    raise typer.Exit(2)


def _require(extra: str, module: str) -> None:
    """Exit with the install line when an extra is missing.

    Args:
        extra (str): The extra's name.
        module (str): Module that proves it is installed.

    Raises:
        typer.Exit: With code 2 when the import fails.
    """
    import importlib.util

    if importlib.util.find_spec(module) is None:
        _fail(
            f"this command needs the [{extra}] extra: "
            f'uv add "tempest-fastapi-sdk[{extra}]"',
        )


def _check_audio(path: Path) -> None:
    """Refuse a path that is not a readable file.

    Args:
        path (Path): The recording.

    Raises:
        typer.Exit: With code 2 when the file is missing.
    """
    if not path.is_file():
        _fail(f"{path} not found")


@voice_app.command("models")
def models(
    cache_dir: Annotated[
        Path | None,
        typer.Option("--cache-dir", help="Where to store them."),
    ] = None,
) -> None:
    """Download the diarization models into the cache.

    Run this at build time. Leaving it to the first request makes one
    user pay a 46 MB download inside their timeout.

    Args:
        cache_dir (Path | None): Override the cache location.

    Raises:
        typer.Exit: With code 2 when the extra is missing or a download
            fails.
    """
    _require("genai-diarization", "sherpa_onnx")
    from tempest_fastapi_sdk.genai.audio.diarization import (
        default_cache_dir,
        ensure_models,
    )

    target = cache_dir or default_cache_dir()
    typer.echo(f"fetching models into {target}")
    try:
        resolved = ensure_models(cache_dir)
    except OSError as exc:
        _fail(str(exc))
    for name, path in resolved.items():
        size = path.stat().st_size / (1024 * 1024)
        typer.echo(f"  {name:40} {size:7.1f} MB")
    typer.secho("models ready", fg="green")


@voice_app.command("diarize")
def diarize(
    audio: Annotated[Path, typer.Argument(help="The recording.")],
    num_speakers: Annotated[
        int | None,
        typer.Option("--num-speakers", "-n", help="Exact count, when known."),
    ] = None,
    threshold: Annotated[
        float | None,
        typer.Option("--threshold", help="Clustering threshold when the count is not."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable.")] = False,
) -> None:
    """Show who spoke when, without transcribing.

    Skips Whisper entirely, which makes it the quick way to check the
    speaker count and threshold before paying for transcription.

    Args:
        audio (Path): The recording.
        num_speakers (int | None): Exact speaker count.
        threshold (float | None): Clustering threshold.
        as_json (bool): Emit JSON instead of a table.

    Raises:
        typer.Exit: With code 2 when the extra is missing or the file is
            unreadable.
    """
    _require("genai-diarization", "sherpa_onnx")
    _check_audio(audio)
    from tempest_fastapi_sdk.genai.audio.diarization import (
        DEFAULT_CLUSTERING_THRESHOLD,
        SpeakerDiarizer,
    )

    diarizer = SpeakerDiarizer(
        num_speakers=num_speakers,
        threshold=threshold if threshold is not None else DEFAULT_CLUSTERING_THRESHOLD,
    )
    turns = asyncio.run(diarizer.diarize(audio))
    if as_json:
        typer.echo(
            json.dumps([turn.model_dump() for turn in turns], indent=2, default=str),
        )
        return
    if not turns:
        typer.secho("no speech detected", fg="yellow")
        return
    for turn in turns:
        typer.echo(
            f"  {turn.start:7.2f} → {turn.end:7.2f}  "
            f"({turn.duration:5.2f}s)  falante {turn.speaker}",
        )
    speakers = sorted({turn.speaker for turn in turns})
    typer.secho(
        f"{len(turns)} turnos, {len(speakers)} falantes: {speakers}", fg="green"
    )


@voice_app.command("transcribe")
def transcribe(
    audio: Annotated[Path, typer.Argument(help="The recording.")],
    num_speakers: Annotated[
        int | None,
        typer.Option("--num-speakers", "-n", help="Exact count, when known."),
    ] = None,
    language: Annotated[
        str | None,
        typer.Option("--language", "-l", help="Force a language instead of detecting."),
    ] = None,
    model_size: Annotated[
        str,
        typer.Option("--model", help="Whisper size: tiny/base/small/medium/large-v3."),
    ] = "small",
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable.")] = False,
) -> None:
    """Transcribe a conversation with each line attributed to a speaker.

    Args:
        audio (Path): The recording.
        num_speakers (int | None): Exact speaker count.
        language (str | None): Force a language.
        model_size (str): Whisper model size.
        as_json (bool): Emit JSON instead of labelled lines.

    Raises:
        typer.Exit: With code 2 when an extra is missing or the file is
            unreadable.
    """
    _require("genai-diarization", "sherpa_onnx")
    _require("genai-audio", "faster_whisper")
    _check_audio(audio)
    from tempest_fastapi_sdk.genai.audio.conversation import ConversationTranscriber
    from tempest_fastapi_sdk.genai.audio.diarization import SpeakerDiarizer
    from tempest_fastapi_sdk.genai.audio.stt import SpeechToText

    transcriber = ConversationTranscriber(
        stt=SpeechToText(model_size=model_size),
        diarizer=SpeakerDiarizer(num_speakers=num_speakers),
    )
    result = asyncio.run(transcriber.transcribe(audio, language=language))
    if as_json:
        typer.echo(json.dumps(result.model_dump(), indent=2, default=str))
        return
    typer.echo(result.transcript())
    typer.secho(
        f"{result.num_speakers} falantes, {result.duration:.1f}s, "
        f"idioma {result.language or 'desconhecido'}",
        fg="green",
    )


__all__: list[str] = [
    "voice_app",
]

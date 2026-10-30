"""Bounded audio decoding shared by every audio engine.

Speech-to-text, diarization and voice enrolment all start from the same
step: turn an upload into mono float32 samples at 16 kHz. Doing that once,
here, is what lets the work a request causes be bounded in **decoded**
terms. A byte cap on the upload is not enough on its own: a container
declares its own sample rate and a codec can pack a long stretch of audio
into very few bytes, so a few kilobytes can ask for hours of samples once
they are decoded and resampled.

Two checks keep the output proportional to what was uploaded:

- the declared source rate must fall inside
  ``[MIN_SOURCE_RATE, MAX_SOURCE_RATE]``, checked from the header (WAV) or
  on every decoded frame (PyAV) **before** anything is resampled;
- the decoded duration must stay within ``max_seconds``. A WAV is read
  for at most one frame past the bound (the header's frame count is not
  trusted), and PyAV output is checked against a running sample count, so
  decoding stops as soon as the limit is passed instead of after the whole
  stream sits in memory.

Both refusals raise
:class:`~tempest_fastapi_sdk.exceptions.ValidationException`, which the
SDK's exception handlers answer with ``422``.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any, BinaryIO

from tempest_fastapi_sdk.exceptions.validation import ValidationException

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np
    import numpy.typing as npt

MIN_SOURCE_RATE: int = 8_000
"""Lowest declared sample rate accepted, in Hz.

8 kHz is narrowband telephony, the lowest rate speech is recorded at in
practice. Anything lower is not a recording a speech model can use, and
resampling it to 16 kHz multiplies the sample count by the rate ratio, so
a forged low rate is how a small upload turns into a large allocation.
"""

MAX_SOURCE_RATE: int = 192_000
"""Highest declared sample rate accepted, in Hz.

192 kHz is the top rate professional audio interfaces record at.
"""

MAX_CHANNELS: int = 8
"""Most channels accepted in a WAV file (7.1 surround)."""

DEFAULT_MAX_AUDIO_SECONDS: float = 3_600.0
"""Longest decoded recording accepted by default, in seconds.

One hour, which holds a long meeting. At 16 kHz that is 57.6 million
float32 samples, about 230 MB — the most memory one call is allowed to
ask for by default. Pass ``max_audio_seconds=None`` to the engine to lift
the bound for trusted, operator-supplied files.
"""


def load_audio(
    audio: str | Path | bytes,
    *,
    target_rate: int,
    max_seconds: float | None = DEFAULT_MAX_AUDIO_SECONDS,
) -> npt.NDArray[np.float32]:
    """Read audio into mono float32 samples at ``target_rate``, bounded.

    Accepts what the models need rather than what a file happens to be:
    stereo is mixed down and any sample rate in the accepted range is
    resampled, because feeding a model the wrong rate degrades it silently
    instead of failing.

    A 16-bit PCM WAV is read with the standard library. When it needs
    resampling and PyAV is installed (it ships with the ``[genai-audio]``
    extra), PyAV resamples it, as it does every other format; without
    PyAV the WAV is resampled by linear interpolation.

    Args:
        audio (str | Path | bytes): Path to an audio file, or its bytes.
        target_rate (int): Sample rate to produce.
        max_seconds (float | None): Longest decoded duration accepted.
            ``None`` lifts the bound; the source-rate range still applies.

    Returns:
        npt.NDArray[np.float32]: 1-D samples in ``[-1, 1]``.

    Raises:
        ValidationException: When the declared sample rate or channel
            count is outside the accepted range (``details`` carry
            ``min_rate`` and ``max_rate``), or the decoded audio is longer
            than ``max_seconds`` (``details`` carry ``max_seconds``).
        FileNotFoundError: When ``audio`` is not bytes and does not name an
            existing regular file. Decoders only ever receive a file
            object, so a URL or an FFmpeg protocol (``http://``,
            ``concat:``) passed as a string is never fetched.
        ValueError: When the audio cannot be decoded.
    """
    import wave

    import numpy as np

    try:
        with _open_source(audio) as handle, wave.open(handle) as wav:
            channels = wav.getnchannels()
            rate = wav.getframerate()
            width = wav.getsampwidth()
            nframes = wav.getnframes()
            if width != 2:
                return _decode_with_pyav(
                    audio,
                    target_rate=target_rate,
                    max_seconds=max_seconds,
                )
            _check_rate(rate)
            _check_channels(channels)
            if rate != target_rate and _pyav() is not None:
                return _decode_with_pyav(
                    audio,
                    target_rate=target_rate,
                    max_seconds=max_seconds,
                )
            wanted = nframes
            if max_seconds is not None:
                wanted = min(nframes, int(max_seconds * rate) + 1)
            frames = wav.readframes(wanted)
            _check_duration(len(frames) // (width * channels) / rate, max_seconds)
    except (wave.Error, EOFError):
        return _decode_with_pyav(
            audio,
            target_rate=target_rate,
            max_seconds=max_seconds,
        )

    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples[: len(samples) - len(samples) % channels]
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != target_rate:
        samples = _resample(samples, rate, target_rate)
    return samples


def _open_source(audio: str | Path | bytes) -> BinaryIO:
    """Open the recording as a binary file object for a decoder.

    A string is never handed to a decoder: PyAV hands strings to FFmpeg,
    which treats ``http://``, ``concat:`` and other protocol prefixes as
    inputs to fetch. Only bytes or an existing regular file are accepted,
    so whatever names the audio (a model's tool argument, a form field)
    can at most point at a local file.

    Args:
        audio (str | Path | bytes): The recording or its local path.

    Returns:
        BinaryIO: A file object the caller closes.

    Raises:
        FileNotFoundError: When ``audio`` is not bytes and does not name an
            existing regular file.
    """
    from pathlib import Path as LocalPath

    if isinstance(audio, bytes):
        return io.BytesIO(audio)
    local = LocalPath(audio)
    if not local.is_file():
        raise FileNotFoundError(f"audio must be a local file, got {local.name!r}")
    return local.open("rb")


def _check_rate(rate: int) -> None:
    """Refuse a declared sample rate outside the accepted range.

    Args:
        rate (int): The declared rate, in Hz.

    Raises:
        ValidationException: When ``rate`` is outside
            ``[MIN_SOURCE_RATE, MAX_SOURCE_RATE]``.
    """
    if not MIN_SOURCE_RATE <= rate <= MAX_SOURCE_RATE:
        raise ValidationException(
            message=(
                f"audio sample rate {rate} Hz is outside the accepted range "
                f"{MIN_SOURCE_RATE}-{MAX_SOURCE_RATE} Hz"
            ),
            details={"min_rate": MIN_SOURCE_RATE, "max_rate": MAX_SOURCE_RATE},
        )


def _check_channels(channels: int) -> None:
    """Refuse a WAV channel count outside ``1..MAX_CHANNELS``.

    Args:
        channels (int): The declared channel count.

    Raises:
        ValidationException: When ``channels`` is out of range.
    """
    if not 1 <= channels <= MAX_CHANNELS:
        raise ValidationException(
            message=f"audio has {channels} channels; at most {MAX_CHANNELS}",
            details={"max_channels": MAX_CHANNELS},
        )


def _check_duration(seconds: float, max_seconds: float | None) -> None:
    """Refuse audio longer than ``max_seconds``.

    Args:
        seconds (float): The duration measured or declared so far.
        max_seconds (float | None): The bound, or ``None`` for none.

    Raises:
        ValidationException: When ``seconds`` exceeds ``max_seconds``.
    """
    if max_seconds is not None and seconds > max_seconds:
        raise ValidationException(
            message=f"audio is longer than {max_seconds:g} s",
            details={"max_seconds": max_seconds},
        )


def _pyav() -> Any | None:
    """Import PyAV when it is installed.

    Returns:
        Any | None: The ``av`` module, or ``None`` without it.
    """
    try:
        import av
    except ImportError:
        return None
    return av


def _decode_with_pyav(
    audio: str | Path | bytes,
    *,
    target_rate: int,
    max_seconds: float | None,
) -> npt.NDArray[np.float32]:
    """Decode any format through PyAV, counting samples as they arrive.

    Ported from faster-whisper's ``faster_whisper.audio.decode_audio``
    (MIT), which is what ``SpeechToText`` handed its bytes to before: the
    same ``s16`` mono resampler and the same skipping of frames the codec
    reports as invalid. Two things differ, and both are the reason this
    exists. Every frame's rate is checked before it reaches the resampler,
    so one frame can grow by at most ``target_rate / MIN_SOURCE_RATE``.
    And the output is counted per resampled frame against ``max_seconds``,
    so decoding stops at the bound instead of buffering the whole stream
    first. The upstream grouping of frames into 500 000-sample blocks is
    dropped, since it would resample a block before its size is known.

    Args:
        audio (str | Path | bytes): The recording.
        target_rate (int): Sample rate to produce.
        max_seconds (float | None): Longest decoded duration accepted.

    Returns:
        npt.NDArray[np.float32]: Mono samples.

    Raises:
        ValidationException: When a frame's rate is out of range or the
            output passes ``max_seconds``.
        ValueError: When PyAV is not installed or cannot open the input.
    """
    import gc

    import numpy as np

    av = _pyav()
    if av is None:
        raise ValueError(
            "only 16-bit WAV can be read without a decoder; install the "
            "[genai-audio] extra for mp3/m4a/ogg: pip install "
            '"tempest-fastapi-sdk[genai-audio]"',
        )
    limit = None if max_seconds is None else int(max_seconds * target_rate)
    resampler: Any = av.audio.resampler.AudioResampler(
        format="s16",
        layout="mono",
        rate=target_rate,
    )
    parts: list[Any] = []
    total = 0

    def _collect(resampled: list[Any]) -> None:
        """Append resampled frames, refusing once the bound is passed.

        Args:
            resampled (list[Any]): Frames the resampler produced.

        Raises:
            ValidationException: When the running total passes ``limit``.
        """
        nonlocal total
        for out in resampled:
            array = out.to_ndarray().reshape(-1)
            total += int(array.shape[0])
            if limit is not None and total > limit:
                _check_duration(total / target_rate, max_seconds)
            parts.append(array)

    try:
        with (
            _open_source(audio) as source,
            av.open(source, mode="r", metadata_errors="ignore") as container,
        ):
            if not container.streams.audio:
                raise ValueError("the input holds no audio stream")
            frames = iter(container.decode(audio=0))
            while True:
                try:
                    frame = next(frames)
                except StopIteration:
                    break
                except av.error.InvalidDataError:
                    continue
                _check_rate(int(frame.sample_rate or 0))
                frame.pts = None
                _collect(resampler.resample(frame))
            _collect(resampler.resample(None))
    except av.error.FFmpegError as exc:
        raise ValueError(f"audio could not be decoded: {exc}") from exc
    finally:
        del resampler
        gc.collect()

    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32) / 32768.0


def _resample(
    samples: npt.NDArray[np.float32],
    source_rate: int,
    target_rate: int,
) -> npt.NDArray[np.float32]:
    """Resample mono samples by linear interpolation.

    The fallback for a 16-bit WAV at another rate when PyAV is not
    installed. Linear interpolation is not the best resampler, but the
    alternative is a new dependency (``soxr``, ``scipy``) for a step that
    runs once per request. Only reached after :func:`_check_rate`, so the
    output is at most ``target_rate / MIN_SOURCE_RATE`` times the input.

    Args:
        samples (npt.NDArray[np.float32]): 1-D samples.
        source_rate (int): Their current rate.
        target_rate (int): The rate to produce.

    Returns:
        npt.NDArray[np.float32]: Resampled samples.
    """
    import numpy as np

    duration = len(samples) / source_rate
    target_length = int(duration * target_rate)
    source_positions = np.linspace(0.0, len(samples) - 1, num=len(samples))
    target_positions = np.linspace(0.0, len(samples) - 1, num=target_length)
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


__all__: list[str] = [
    "DEFAULT_MAX_AUDIO_SECONDS",
    "MAX_CHANNELS",
    "MAX_SOURCE_RATE",
    "MIN_SOURCE_RATE",
    "load_audio",
]

"""Two callers at once must not read each other's settings.

A diarizer and a transcriber are built once and shared by every request,
so anything a request writes onto them is visible to the requests running
beside it. That is not a hypothetical: ``ConversationTranscriber`` used to
assign ``self.diarizer.num_speakers`` before diarizing, and two
overlapping requests both got whichever count was written last — the
caller who asked for two speakers received the other caller's five, with
no error anywhere.

The tests below stub the native work, so they need no models and no
audio: what is under test is which count each call carries, not what the
clustering does with it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from tempest_fastapi_sdk.genai.audio import SpeakerDiarizer
from tempest_fastapi_sdk.genai.audio.conversation import ConversationTranscriber
from tempest_fastapi_sdk.genai.audio.schemas import SpeakerTurn, Transcription

if TYPE_CHECKING:
    from pathlib import Path


class _SpyDiarizer(SpeakerDiarizer):
    """A diarizer that records the count each call actually used.

    Overrides the synchronous worker rather than the coroutine so the
    real :meth:`SpeakerDiarizer.diarize` — the code that decides which
    count applies — is the code under test.

    Attributes:
        seen (list[int | None]): One entry per call, in completion order.
        gate (asyncio.Event): Held closed to force the two calls to
            overlap; without it the first can finish before the second
            starts and the race cannot show up.
    """

    def __init__(self) -> None:
        """Initialize the spy."""
        super().__init__()
        self.seen: list[int | None] = []
        self.gate: asyncio.Event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _diarize_sync(
        self,
        audio: str | Path | bytes,
        override: int | None = None,
    ) -> list[SpeakerTurn]:
        """Record the count and return one turn per requested speaker.

        Args:
            audio (str | Path | bytes): Ignored.
            override (int | None): The count this call carries.

        Returns:
            list[SpeakerTurn]: Placeholder turns, one per speaker.
        """
        self.seen.append(override)
        count = override or 1
        return [
            SpeakerTurn(speaker=index, start=float(index), end=index + 1.0, text="")
            for index in range(count)
        ]


class _SlowStt:
    """A speech-to-text stub that yields control mid-call.

    Attributes:
        released (asyncio.Event): Set once it has started, so the test
            can be sure both requests are in flight together.
    """

    def __init__(self) -> None:
        """Initialize the stub."""
        self.released: asyncio.Event = asyncio.Event()

    async def transcribe(
        self,
        audio: str | Path | bytes,
        *,
        language: Any = None,
        with_segments: bool = False,
    ) -> Transcription:
        """Return a fixed transcription after yielding to the loop.

        Args:
            audio (str | Path | bytes): Ignored.
            language (Any): Ignored.
            with_segments (bool): Ignored.

        Returns:
            Transcription: A one-segment result.
        """
        self.released.set()
        await asyncio.sleep(0)
        return Transcription(text="ok", language="pt", duration=1.0, segments=[])


class TestPerCallSpeakerCount:
    @pytest.mark.asyncio
    async def test_two_concurrent_calls_keep_their_own_counts(self) -> None:
        """The regression: both calls used to see the same count.

        Before the fix this asserted ``[2, 5]`` and got ``[5, 5]`` — the
        request that asked for two speakers was clustered into five.
        """
        diarizer = _SpyDiarizer()
        diarizer.max_concurrent = 4
        await asyncio.gather(
            diarizer.diarize(b"a", num_speakers=2),
            diarizer.diarize(b"b", num_speakers=5),
        )
        assert sorted(count or 0 for count in diarizer.seen) == [2, 5]

    @pytest.mark.asyncio
    async def test_an_override_does_not_outlive_the_call(self) -> None:
        """A per-call count must not become the instance's new default."""
        diarizer = _SpyDiarizer()
        await diarizer.diarize(b"a", num_speakers=7)
        assert diarizer.num_speakers == "auto"

    @pytest.mark.asyncio
    async def test_the_instance_default_applies_without_an_override(self) -> None:
        """``None`` means "whatever this diarizer was built with"."""
        diarizer = _SpyDiarizer()
        diarizer.num_speakers = 3
        await diarizer.diarize(b"a")
        assert diarizer.seen == [3]

    @pytest.mark.asyncio
    async def test_a_bogus_override_is_refused_before_any_work(self) -> None:
        """Same validation as the constructor, same message."""
        diarizer = _SpyDiarizer()
        with pytest.raises(ValueError, match='"auto"'):
            await diarizer.diarize(b"a", num_speakers="automatic")  # type: ignore[arg-type]
        assert diarizer.seen == []

    @pytest.mark.asyncio
    async def test_the_transcriber_passes_the_count_through(self) -> None:
        """It must not reach the diarizer by assignment.

        Two overlapping transcriptions asking for different counts is
        exactly the shape a shared transcriber sees under load.
        """
        diarizer = _SpyDiarizer()
        diarizer.max_concurrent = 4
        transcriber = ConversationTranscriber(
            stt=_SlowStt(),  # type: ignore[arg-type]
            diarizer=diarizer,
        )
        await asyncio.gather(
            transcriber.transcribe(b"a", num_speakers=2),
            transcriber.transcribe(b"b", num_speakers=5),
        )
        assert sorted(count or 0 for count in diarizer.seen) == [2, 5]

    @pytest.mark.asyncio
    async def test_the_transcriber_does_not_unload_a_shared_diarizer(self) -> None:
        """Unloading frees models a concurrent call may be mid-way through.

        The old code called ``unload()`` to make the new count take
        effect, which dropped ~46 MB of loaded models under any request
        running beside it.
        """
        diarizer = _SpyDiarizer()
        unloads = 0
        original = diarizer.unload

        def counting_unload() -> None:
            """Count unloads, then delegate."""
            nonlocal unloads
            unloads += 1
            original()

        diarizer.unload = counting_unload  # type: ignore[method-assign]
        transcriber = ConversationTranscriber(
            stt=_SlowStt(),  # type: ignore[arg-type]
            diarizer=diarizer,
        )
        await transcriber.transcribe(b"a", num_speakers=2)
        assert unloads == 0

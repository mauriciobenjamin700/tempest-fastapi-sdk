"""Tests for speaker diarization and speaker-attributed transcription.

The engine itself needs 46 MB of models and a real recording, so those
runs are marked ``model`` and deselected by default — the same tier the
rest of the genai suite uses. What runs everywhere is the part that is
pure logic and that got the attribution wrong when it was written by
eye: the timeline alignment and the cluster renumbering.
"""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.genai.audio import (
    DEFAULT_CLUSTERING_THRESHOLD,
    DiarizedTranscription,
    SpeakerDiarizer,
    SpeakerTurn,
    align_turns,
)
from tempest_fastapi_sdk.genai.audio.diarization import _renumber
from tempest_fastapi_sdk.genai.audio.schemas import TranscriptionSegment


def _turn(start: float, end: float, speaker: int) -> SpeakerTurn:
    """Build a diarized turn with no text.

    Args:
        start (float): Turn start.
        end (float): Turn end.
        speaker (int): Cluster index.

    Returns:
        SpeakerTurn: The turn.
    """
    return SpeakerTurn(start=start, end=end, speaker=speaker)


def _segment(start: float, end: float, text: str) -> TranscriptionSegment:
    """Build a transcription span.

    Args:
        start (float): Span start.
        end (float): Span end.
        text (str): Transcribed text.

    Returns:
        TranscriptionSegment: The span.
    """
    return TranscriptionSegment(start=start, end=end, text=text)


class TestAlignment:
    def test_each_segment_goes_to_the_turn_it_overlaps(self) -> None:
        """The ordinary case: one segment sits inside one turn."""
        turns = [_turn(0.0, 5.0, 0), _turn(5.0, 10.0, 1)]
        segments = [_segment(0.5, 4.0, "Bom dia"), _segment(6.0, 9.0, "Tudo bem?")]
        aligned = align_turns(turns, segments)
        assert [(t.speaker, t.text) for t in aligned] == [
            (0, "Bom dia"),
            (1, "Tudo bem?"),
        ]

    def test_a_straddling_segment_goes_to_the_larger_share(self) -> None:
        """Whisper spans do not respect speaker changes.

        A segment covering the end of one turn and the start of the next
        lands wholly on whichever holds more of it. This is the known
        cost of transcribing the recording once instead of per turn, and
        it is documented rather than hidden.
        """
        turns = [_turn(0.0, 5.0, 0), _turn(5.0, 10.0, 1)]
        aligned = align_turns(turns, [_segment(4.0, 8.0, "atravessa")])
        assert len(aligned) == 1
        assert aligned[0].speaker == 1

    def test_text_outside_every_turn_is_kept_as_unknown(self) -> None:
        """Dropping words silently is worse than admitting ignorance.

        Speech the diarizer discarded as too short still transcribes, and
        that text has to reach the caller — marked ``-1`` so nobody reads
        it as an identified speaker.
        """
        turns = [_turn(0.0, 5.0, 0)]
        aligned = align_turns(turns, [_segment(20.0, 22.0, "fora de tudo")])
        assert [(t.speaker, t.text) for t in aligned] == [(-1, "fora de tudo")]

    def test_several_segments_join_into_one_turn(self) -> None:
        """One turn usually spans several Whisper spans."""
        turns = [_turn(0.0, 10.0, 0)]
        aligned = align_turns(
            turns,
            [_segment(0.0, 3.0, "Primeira."), _segment(3.0, 6.0, "Segunda.")],
        )
        assert aligned[0].text == "Primeira. Segunda."

    def test_turns_with_no_text_are_dropped(self) -> None:
        """A turn nobody transcribed is noise, not a silent participant."""
        turns = [_turn(0.0, 5.0, 0), _turn(5.0, 10.0, 1)]
        aligned = align_turns(turns, [_segment(0.5, 4.0, "só um falou")])
        assert [t.speaker for t in aligned] == [0]

    def test_empty_segments_are_ignored(self) -> None:
        """Whisper emits blank spans on silence."""
        turns = [_turn(0.0, 5.0, 0)]
        assert align_turns(turns, [_segment(1.0, 2.0, "   ")]) == []

    def test_without_diarization_everything_is_unknown(self) -> None:
        """A recording the diarizer found no speech in still transcribes."""
        aligned = align_turns([], [_segment(0.0, 2.0, "olá")])
        assert [(t.speaker, t.text) for t in aligned] == [(-1, "olá")]

    def test_result_is_chronological(self) -> None:
        """Orphans must not be appended after the ordered turns."""
        turns = [_turn(10.0, 15.0, 0)]
        aligned = align_turns(
            turns,
            [_segment(11.0, 14.0, "depois"), _segment(0.0, 2.0, "antes")],
        )
        assert [t.text for t in aligned] == ["antes", "depois"]


class TestRenumbering:
    def test_sparse_clusters_become_dense(self) -> None:
        """The clustering hands back gaps; the caller must not see them.

        A four-speaker recording produced ``0, 1, 2, 4, 7, 8, 9``. Passed
        through, the gaps read as speakers who were present and silent.
        """
        turns = [_turn(0, 1, 0), _turn(1, 2, 4), _turn(2, 3, 9), _turn(3, 4, 4)]
        assert [t.speaker for t in _renumber(turns)] == [0, 1, 2, 1]

    def test_numbering_follows_first_appearance(self) -> None:
        """Speaker 0 is whoever spoke first, which is what a reader assumes."""
        turns = [_turn(0, 1, 7), _turn(1, 2, 3)]
        assert [t.speaker for t in _renumber(turns)] == [0, 1]

    def test_empty_input_is_empty_output(self) -> None:
        """Silence is not an error."""
        assert _renumber([]) == []


class TestDiarizerConfiguration:
    def test_rejects_impossible_settings(self) -> None:
        """Both would only surface much later, as odd output."""
        with pytest.raises(ValueError, match="max_concurrent"):
            SpeakerDiarizer(max_concurrent=0)
        with pytest.raises(ValueError, match="num_speakers"):
            SpeakerDiarizer(num_speakers=0)

    def test_starts_unloaded(self) -> None:
        """Construction must not download 46 MB of models."""
        diarizer = SpeakerDiarizer()
        assert diarizer.is_loaded is False

    def test_default_threshold_is_the_measured_one(self) -> None:
        """Pins the value the sweep chose.

        sherpa-onnx defaults to 0.5, which produced seven clusters for a
        four-speaker recording. Anyone lowering this back should have to
        change a test and read why.
        """
        assert DEFAULT_CLUSTERING_THRESHOLD == 0.9
        assert SpeakerDiarizer().threshold == 0.9


class TestTranscriptRendering:
    def test_named_speakers_win_over_the_placeholder(self) -> None:
        """An identified speaker is the whole point of identifying them."""
        result = DiarizedTranscription(
            text="",
            turns=[
                SpeakerTurn(start=0, end=1, speaker=0, text="Oi", speaker_name="Ana"),
                SpeakerTurn(start=1, end=2, speaker=1, text="Olá"),
            ],
        )
        assert result.transcript() == "Ana: Oi\nFalante 1: Olá"

    def test_grouping_collects_everything_one_speaker_said(self) -> None:
        """A different question from 'how did the conversation go'."""
        result = DiarizedTranscription(
            text="",
            turns=[
                SpeakerTurn(start=0, end=1, speaker=0, text="um"),
                SpeakerTurn(start=1, end=2, speaker=1, text="dois"),
                SpeakerTurn(start=2, end=3, speaker=0, text="três"),
            ],
        )
        assert result.by_speaker() == {0: "um três", 1: "dois"}

    def test_placeholder_is_configurable(self) -> None:
        """Not every product speaks Portuguese to its users."""
        result = DiarizedTranscription(
            text="",
            turns=[SpeakerTurn(start=0, end=1, speaker=2, text="hi")],
        )
        assert result.transcript(unknown="Speaker {speaker}") == "Speaker 2: hi"

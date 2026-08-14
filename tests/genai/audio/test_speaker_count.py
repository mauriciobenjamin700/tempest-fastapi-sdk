"""Tests for automatic speaker-count estimation.

The estimator is pure numerics over embeddings, so it is testable
without a model: synthetic vectors with a known cluster structure
exercise every branch that matters. The end-to-end accuracy claim —
10/10 on a benchmark whose speaker count is correct by construction —
belongs to the ``model`` tier and is recorded in the module docstring
and the recipe.
"""

from __future__ import annotations

import math

import pytest

from tempest_fastapi_sdk.genai.audio import SpeakerDiarizer
from tempest_fastapi_sdk.genai.audio.speaker_count import (
    DEFAULT_MAX_SPEAKERS,
    SOLO_COHESION_P10,
    affinity_report,
    estimate_speaker_count,
)


def _cluster(
    center: list[float], count: int, jitter: float = 0.02
) -> list[list[float]]:
    """Build ``count`` vectors near ``center``.

    Args:
        center (list[float]): The cluster centre.
        count (int): How many vectors to produce.
        jitter (float): Deterministic perturbation per vector, so the
            test does not depend on a random seed.

    Returns:
        list[list[float]]: The vectors.
    """
    vectors: list[list[float]] = []
    for index in range(count):
        noisy = list(center)
        # Perturb one dimension at a time so the cluster stays tight after
        # normalisation. Nudging every dimension by the same amount rotates
        # the vector far enough that the "cluster" stops being one.
        target = (index + 1) % len(noisy)
        noisy[target] += jitter * (1.0 + math.sin(index))
        vectors.append(noisy)
    return vectors


def _speakers(count: int, per_speaker: int = 4) -> list[list[float]]:
    """Build turn embeddings for ``count`` well-separated speakers.

    Args:
        count (int): How many distinct voices.
        per_speaker (int): Turns each of them takes.

    Returns:
        list[list[float]]: The embeddings, interleaved like a
        conversation rather than grouped, so ordering cannot be what
        the estimator keys on.
    """
    dimensions = max(count, 8)
    groups = []
    for speaker in range(count):
        center = [0.0] * dimensions
        center[speaker % dimensions] = 1.0
        groups.append(_cluster(center, per_speaker))
    interleaved: list[list[float]] = []
    for turn in range(per_speaker):
        for group in groups:
            interleaved.append(group[turn])
    return interleaved


class TestEstimate:
    @pytest.mark.parametrize("count", [2, 3, 4, 5, 6])
    def test_recovers_a_known_speaker_count(self, count: int) -> None:
        """The property the whole feature rests on."""
        assert estimate_speaker_count(_speakers(count)) == count

    def test_one_turn_is_one_speaker(self) -> None:
        """There is nothing to compare a single turn against."""
        assert estimate_speaker_count([[1.0, 0.0]]) == 1

    def test_no_turns_is_one_speaker(self) -> None:
        """An empty recording must not raise on the way to an answer."""
        assert estimate_speaker_count([]) == 1

    def test_one_voice_across_many_turns_stays_one(self) -> None:
        """Splitting a monologue into speakers is the loud failure.

        The gap search always finds a split, including where there is
        none: before the cohesion veto, a real six-turn dictation came
        back as two speakers. This is that regression.
        """
        assert estimate_speaker_count(_cluster([1.0, 0.0, 0.0], 8)) == 1

    def test_the_ceiling_is_respected(self) -> None:
        """Without a bound, a long tail of close eigenvalues wins on noise."""
        assert estimate_speaker_count(_speakers(6), max_speakers=3) <= 3

    def test_rejects_impossible_settings(self) -> None:
        """Both would silently produce a meaningless answer."""
        with pytest.raises(ValueError, match="max_speakers"):
            estimate_speaker_count(_speakers(2), max_speakers=0)
        for bad in (-0.1, 1.5):
            with pytest.raises(ValueError, match="percentile"):
                estimate_speaker_count(_speakers(2), percentile=bad)

    def test_default_ceiling_is_pinned(self) -> None:
        """Changing it changes what the estimator can ever answer."""
        assert DEFAULT_MAX_SPEAKERS == 10

    def test_the_solo_veto_sits_between_the_measured_ranges(self) -> None:
        """Measured: one voice 0.490-0.667, several -0.080-0.166.

        The value is a property of the bundled embedding model's
        similarity scale, so it is pinned here — swapping the model
        means re-measuring it, and a silent change would turn monologues
        back into conversations.
        """
        assert 0.166 < SOLO_COHESION_P10 < 0.490

    def test_identical_vectors_do_not_divide_by_zero(self) -> None:
        """Perfectly identical turns are a degenerate affinity matrix."""
        assert estimate_speaker_count([[1.0, 0.0]] * 5) == 1

    def test_zero_vectors_are_survivable(self) -> None:
        """Silence embeds to zero; the norm must not divide by it."""
        assert estimate_speaker_count([[0.0, 0.0], [0.0, 0.0]]) >= 1


class TestAffinityReport:
    def test_exposes_the_numbers_behind_the_estimate(self) -> None:
        """ "It said four" is not something a person can act on."""
        report = affinity_report(_speakers(3))
        assert report["estimated"] == 3
        assert len(report["eigenvalues"]) == 12
        assert len(report["gaps"]) == 11
        assert report["margin"] > 0.0

    def test_a_single_turn_reports_no_spectrum(self) -> None:
        """There is no matrix to decompose."""
        report = affinity_report([[1.0, 0.0]])
        assert report == {
            "estimated": 1,
            "eigenvalues": [],
            "gaps": [],
            "margin": 0.0,
        }

    def test_the_report_agrees_with_the_estimate(self) -> None:
        """The numbers shown must explain the answer given."""
        report = affinity_report(_speakers(4))
        assert report["estimated"] == estimate_speaker_count(_speakers(4))


class TestDiarizerWiring:
    def test_auto_is_the_default(self) -> None:
        """It beat every fixed threshold on the benchmark, so it leads."""
        assert SpeakerDiarizer().num_speakers == "auto"

    def test_an_explicit_count_is_kept(self) -> None:
        """Knowing the count is still the cheapest and most exact route."""
        assert SpeakerDiarizer(num_speakers=2).num_speakers == 2

    def test_none_still_means_threshold_only(self) -> None:
        """The previous behaviour stays reachable for callers who want it."""
        assert SpeakerDiarizer(num_speakers=None).num_speakers is None

    def test_a_bogus_mode_is_refused_at_construction(self) -> None:
        """A typo must not silently fall through to threshold clustering."""
        with pytest.raises(ValueError, match='"auto"'):
            SpeakerDiarizer(num_speakers="automatic")  # type: ignore[arg-type]

    def test_a_non_positive_count_is_refused(self) -> None:
        """Zero speakers is not a conversation."""
        with pytest.raises(ValueError, match="num_speakers"):
            SpeakerDiarizer(num_speakers=0)

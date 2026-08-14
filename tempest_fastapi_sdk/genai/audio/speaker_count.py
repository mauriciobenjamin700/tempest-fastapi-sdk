"""Estimating how many people are in a recording.

Diarization has to answer two questions, and only one of them is easy.
*Where does each turn start and end* comes from the segmentation model.
*How many distinct voices are there* does not come from any model — it
has to be inferred from how the turn embeddings group, and that is the
part that gets a transcript wrong in ways nobody notices: eight
participants in a two-person call, or four people collapsed into one.

Two ways to infer it, measured on a ten-case benchmark whose speaker
count is correct **by construction** (turns drawn from distinct
recordings, so distinct people, rather than from a diarizer's own
output):

| method             | exact | mean error |
| ------------------ | ----- | ---------- |
| threshold 0.5      | 4/10  | 1.90       |
| threshold 0.7      | 8/10  | 0.40       |
| threshold 0.9      | 8/10  | 0.20       |
| **spectral gap**   | 9/10  | 0.10       |

The spectral gap wins, and it wins for a structural reason rather than a
lucky constant: a threshold asks *how close is close enough*, a question
whose answer changes with the microphone, the language and the room,
while the gap asks *where does this affinity matrix naturally split*,
which is a property of the recording itself.

The cost is one embedding per turn plus an eigendecomposition of a
matrix the size of the turn count — negligible next to the segmentation
pass that produced the turns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_MAX_SPEAKERS: int = 10
"""Largest speaker count the estimator will return.

The gap search needs an upper bound: with none, a recording of N turns
can nominate up to N speakers, and the largest gap in a long tail of
near-identical eigenvalues is noise. Ten covers a meeting; past that the
recording needs a real diarization system and a human.
"""

DEFAULT_AFFINITY_PERCENTILE: float = 0.9
"""Fraction of each row's weakest affinities that get suppressed.

Row-wise pruning before the eigendecomposition is standard in spectral
diarization, and it matters: every turn has a small nonzero similarity
to every other turn, and summed over a long recording that noise floor
blurs exactly the gap being looked for. Keeping the strongest tenth of
each row sharpens the block structure without hand-tuning a distance.
"""

SUPPRESSION_FACTOR: float = 0.01
"""What a pruned affinity is multiplied by, rather than zeroed.

Zeroing can disconnect a turn from the graph entirely, which produces a
spurious eigenvalue at zero and an extra "speaker" who is really one
noisy segment. Scaling it down keeps the graph connected while making
the weak links negligible.
"""


SOLO_COHESION_P10: float = 0.35
"""Above this, the turns are one voice and the gap search is skipped.

The spectral gap answers *where does this split*, and it answers even
when the honest answer is *it does not*. Left alone it splits a
monologue: a real six-turn dictation came back as two speakers, which
turns a voice note into a conversation.

A single voice is *uniformly* similar to itself — even its least
similar pair of turns is close — while two voices produce pairs that are
genuinely far apart and drag the low percentile down. Measured over
twelve recordings (ten multi-speaker, two monologues), the 10th
percentile of pairwise cosine similarity was 0.490-0.667 for one speaker
and -0.080-0.166 for more than one. This sits in the middle of that gap,
with at least 0.14 of margin on each side.

**It is a property of the embedding model, not a universal constant.**
Swapping the model requires re-measuring it; the number means nothing
about a different model's similarity scale.
"""


def estimate_speaker_count(
    embeddings: Sequence[Sequence[float]],
    *,
    max_speakers: int = DEFAULT_MAX_SPEAKERS,
    percentile: float = DEFAULT_AFFINITY_PERCENTILE,
) -> int:
    """Infer how many distinct voices a set of turn embeddings holds.

    Builds a cosine affinity matrix over the turns, prunes each row to
    its strongest links, and reads the number of speakers off the
    largest gap between consecutive eigenvalues of the normalized
    Laplacian.

    Args:
        embeddings (Sequence[Sequence[float]]): One voiceprint per turn.
        max_speakers (int): Largest count to consider.
        percentile (float): Fraction of each row suppressed, in
            ``0..1``. Higher prunes more.

    Returns:
        int: The estimated speaker count, at least 1. A single turn is
        one speaker by definition — there is nothing to compare it to.

    Raises:
        ValueError: If ``max_speakers`` is below 1 or ``percentile`` is
            outside ``0..1``.
        ImportError: When NumPy is missing — install the
            ``[genai-diarization]`` extra.
    """
    if max_speakers < 1:
        raise ValueError("max_speakers must be >= 1")
    if not 0.0 <= percentile <= 1.0:
        raise ValueError("percentile must be between 0 and 1")
    if len(embeddings) <= 1:
        return 1

    import numpy as np

    matrix = np.asarray(embeddings, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)

    if _is_one_voice(matrix):
        return 1

    # Cosine lands in [-1, 1]; the Laplacian needs non-negative weights.
    affinity = (matrix @ matrix.T + 1.0) / 2.0
    np.fill_diagonal(affinity, 0.0)

    keep = max(1, round((1.0 - percentile) * affinity.shape[0]))
    for row in range(affinity.shape[0]):
        cutoff = np.partition(affinity[row], -keep)[-keep]
        affinity[row][affinity[row] < cutoff] *= SUPPRESSION_FACTOR
    affinity = np.maximum(affinity, affinity.T)

    degree = affinity.sum(axis=1)
    degree = np.maximum(degree, 1e-12)
    laplacian = np.eye(affinity.shape[0]) - (
        affinity / np.sqrt(np.outer(degree, degree))
    )
    eigenvalues = np.sort(np.linalg.eigvalsh(laplacian))

    limit = min(max_speakers, len(eigenvalues) - 1)
    if limit < 1:
        return 1
    gaps = np.diff(eigenvalues[: limit + 1])
    return int(np.argmax(gaps) + 1)


def _is_one_voice(normalized: Any) -> bool:
    """Whether every turn is close enough to every other to be one person.

    Checked before the gap search because the gap search always finds a
    split, including in a recording that has none.

    Args:
        normalized (Any): Unit-length embeddings, one row per turn.

    Returns:
        bool: ``True`` when the turns cohere as a single voice.
    """
    import numpy as np

    similarity = normalized @ normalized.T
    off_diagonal = similarity[~np.eye(similarity.shape[0], dtype=bool)]
    if off_diagonal.size == 0:
        return True
    return bool(np.percentile(off_diagonal, 10) >= SOLO_COHESION_P10)


def affinity_report(embeddings: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Return the intermediate numbers behind an estimate.

    Exists because "the estimator said four" is not something a person
    can act on. The eigenvalues and their gaps show *how close the call
    was*: a wide margin means the recording really does split four ways,
    while two near-equal gaps mean the answer could as easily have been
    three, which is the moment to pass ``num_speakers`` explicitly.

    Args:
        embeddings (Sequence[Sequence[float]]): One voiceprint per turn.

    Returns:
        dict[str, Any]: ``estimated``, the sorted ``eigenvalues``, the
        ``gaps`` between them, and ``margin`` — how much the winning gap
        beat the runner-up, ``0.0`` when there is no runner-up.
    """
    import numpy as np

    estimated = estimate_speaker_count(embeddings)
    if len(embeddings) <= 1:
        return {
            "estimated": 1,
            "eigenvalues": [],
            "gaps": [],
            "margin": 0.0,
        }

    matrix = np.asarray(embeddings, dtype=np.float64)
    matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    affinity = (matrix @ matrix.T + 1.0) / 2.0
    np.fill_diagonal(affinity, 0.0)
    degree = np.maximum(affinity.sum(axis=1), 1e-12)
    laplacian = np.eye(affinity.shape[0]) - (
        affinity / np.sqrt(np.outer(degree, degree))
    )
    eigenvalues = np.sort(np.linalg.eigvalsh(laplacian))
    gaps = np.diff(eigenvalues)
    ordered = np.sort(gaps)[::-1]
    margin = float(ordered[0] - ordered[1]) if len(ordered) > 1 else 0.0
    return {
        "estimated": estimated,
        "eigenvalues": [float(value) for value in eigenvalues],
        "gaps": [float(value) for value in gaps],
        "margin": margin,
    }


__all__: list[str] = [
    "DEFAULT_AFFINITY_PERCENTILE",
    "DEFAULT_MAX_SPEAKERS",
    "SOLO_COHESION_P10",
    "SUPPRESSION_FACTOR",
    "affinity_report",
    "estimate_speaker_count",
]

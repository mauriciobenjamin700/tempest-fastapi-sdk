"""Aligning a detected face into the crop the recognizer expects.

ArcFace-family recognizers are trained on faces warped so that the eyes,
nose and mouth corners sit at fixed pixel positions. Feeding them an
unaligned crop does not fail — it quietly costs accuracy, because the
network never saw a face at that pose during training. So alignment is
not a refinement here, it is what makes the embedding comparable at all.

The transform is closed-form: the least-squares similarity (rotation,
uniform scale, translation) that carries the five detected landmarks
onto the canonical template. No iteration, no optimiser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

FACE_SIZE: int = 112
"""Side of the aligned crop, in pixels.

Fixed by the recognition models, not chosen: they declare a
``[N, 3, 112, 112]`` input and a different size would need a different
template.
"""

ARCFACE_TEMPLATE: tuple[tuple[float, float], ...] = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)
"""Where the five landmarks must land in a 112x112 crop.

Order: left eye, right eye, nose tip, left mouth corner, right mouth
corner — the order the detector emits them in.

Ported from insightface's ``arcface_dst`` (``insightface/utils/face_align.py``).
These are not tunable: they are the positions the recognition weights
were trained against, so changing them degrades every embedding while
leaving the code working. Pinned by test for that reason.
"""


def similarity_transform(source: Any, target: Any) -> Any:
    """Fit the similarity transform carrying ``source`` onto ``target``.

    Umeyama's closed-form solution: centre both point sets, take the SVD
    of their cross-covariance for the rotation, and read the scale off
    the singular values. The determinant correction is what keeps the
    result a rotation rather than a reflection — without it a face can
    come out mirrored, which the recognizer will happily embed as
    somebody else.

    Args:
        source (Any): ``(N, 2)`` points to move.
        target (Any): ``(N, 2)`` points to move them onto.

    Returns:
        Any: A ``(2, 3)`` affine matrix.

    Raises:
        ValueError: When the point sets differ in shape, or hold fewer
            than two points — a similarity transform is underdetermined
            below that.
    """
    import numpy as np

    src = np.asarray(source, dtype=np.float64)
    dst = np.asarray(target, dtype=np.float64)
    if src.shape != dst.shape:
        raise ValueError(f"point sets differ in shape: {src.shape} vs {dst.shape}")
    if src.ndim != 2 or src.shape[0] < 2 or src.shape[1] != 2:
        raise ValueError("need at least two 2-D points in an (N, 2) array")

    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_centered = src - src_mean
    dst_centered = dst - dst_mean

    covariance = dst_centered.T @ src_centered / src.shape[0]
    unitary, singular, vt = np.linalg.svd(covariance)
    correction = np.diag([1.0, float(np.sign(np.linalg.det(unitary @ vt)))])
    rotation = unitary @ correction @ vt

    variance = src_centered.var(axis=0).sum()
    if variance == 0.0:
        raise ValueError("source points are coincident; no scale can be fitted")
    scale = float((singular * np.diag(correction)).sum() / variance)

    matrix = np.zeros((2, 3), dtype=np.float64)
    matrix[:, :2] = scale * rotation
    matrix[:, 2] = dst_mean - scale * (rotation @ src_mean)
    return matrix


def align_face(image: Any, landmarks: Sequence[Sequence[float]]) -> Any:
    """Warp a face into the recognizer's canonical 112x112 crop.

    Args:
        image (Any): The full ``PIL.Image`` the face was found in.
        landmarks (Sequence[Sequence[float]]): Five ``(x, y)`` points in
            image coordinates.

    Returns:
        Any: A ``PIL.Image`` of size ``112x112`` in RGB.

    Raises:
        ValueError: When ``landmarks`` does not hold exactly five points.
        ImportError: When Pillow or NumPy is missing — install the
            ``[faces]`` extra.
    """
    import numpy as np
    from PIL import Image

    points = np.asarray(landmarks, dtype=np.float64)
    if points.shape != (5, 2):
        raise ValueError(f"expected five (x, y) landmarks, got {points.shape}")

    matrix = similarity_transform(points, np.asarray(ARCFACE_TEMPLATE))
    # Pillow's AFFINE maps *output* coordinates back to input, so the
    # inverse is what it needs. Passing the forward matrix produces a
    # plausible-looking crop of the wrong region.
    homogeneous = np.vstack([matrix, [0.0, 0.0, 1.0]])
    inverse = np.linalg.inv(homogeneous)
    coefficients = (
        inverse[0, 0],
        inverse[0, 1],
        inverse[0, 2],
        inverse[1, 0],
        inverse[1, 1],
        inverse[1, 2],
    )
    return image.convert("RGB").transform(
        (FACE_SIZE, FACE_SIZE),
        Image.Transform.AFFINE,
        coefficients,
        Image.Resampling.BILINEAR,
    )


__all__: list[str] = [
    "ARCFACE_TEMPLATE",
    "FACE_SIZE",
    "align_face",
    "similarity_transform",
]

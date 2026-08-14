"""The ONNX models the face pipeline runs, and fetching them.

Two models do the work: a detector that finds faces and their five
landmarks, and a recognizer that turns an aligned crop into a comparable
vector. Neither is bundled — weights do not belong in a wheel most
services install for other reasons — and :func:`ensure_models` fetches
them once into a cache the deployment can bake into an image.

**Why not insightface, which packages this already.** Measured: it
installs 558 MB across 24 packages, and its ``opencv-python`` dependency
links against five GL libraries, so a slim container needs system
graphics libraries to recognise a face. Running the same ONNX models
directly needs ``onnxruntime``, NumPy and Pillow — which this SDK already
carries for other features — and no system libraries at all. The
detection decoding and the alignment transform are the code that buys
that, and they are closed-form geometry rather than a long tail.
"""

from __future__ import annotations

import logging
import os
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FaceModelPack:
    """A downloadable pair of detection and recognition models.

    Attributes:
        name (str): Pack name, used as the cache directory.
        url (str): Archive to fetch.
        detector (str): Detection model file inside the archive.
        recognizer (str): Recognition model file inside the archive.
        embedding_dimensions (int): Length of the vectors it produces.
        megabytes (int): Approximate size of the two models on disk,
            for the log line and the docs.
    """

    name: str
    url: str
    detector: str
    recognizer: str
    embedding_dimensions: int
    megabytes: int


_RELEASE: str = "https://github.com/deepinsight/insightface/releases/download/v0.7"

LIGHT_PACK: FaceModelPack = FaceModelPack(
    name="buffalo_s",
    url=f"{_RELEASE}/buffalo_s.zip",
    detector="det_500m.onnx",
    recognizer="w600k_mbf.onnx",
    embedding_dimensions=512,
    megabytes=16,
)
"""The default pack: SCRFD-500M detection plus MobileFaceNet recognition.

Measured against the large pack on a six-face group photo: same detection
count, 15 ms versus 54 ms, and a separation that is materially the same —
the same person across transformed crops scored 0.904-0.960 against
0.920-0.971, while different people topped out at 0.225 against 0.208.
Twelve times smaller for a 0.02 shift in either bound is not a trade
worth refusing.
"""

LARGE_PACK: FaceModelPack = FaceModelPack(
    name="buffalo_l",
    url=f"{_RELEASE}/buffalo_l.zip",
    detector="det_10g.onnx",
    recognizer="w600k_r50.onnx",
    embedding_dimensions=512,
    megabytes=191,
)
"""SCRFD-10G plus a ResNet50 recognizer.

Slightly tighter separation than :data:`LIGHT_PACK` at twelve times the
size and roughly three times the detection latency. Worth it when faces
are small, poorly lit or partially turned — the cases where the margin
matters — and not otherwise.
"""

PACKS: Mapping[str, FaceModelPack] = {
    LIGHT_PACK.name: LIGHT_PACK,
    LARGE_PACK.name: LARGE_PACK,
}
"""Pack name to definition, for the CLI and for ``pack=`` by string."""


def default_cache_dir() -> Path:
    """Return where model packs are cached.

    Honors ``TEMPEST_FACE_MODEL_DIR`` first so a deployment can point at
    a baked image layer or a mounted volume.

    Returns:
        Path: The cache root. Not created here.
    """
    override = os.environ.get("TEMPEST_FACE_MODEL_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "tempest" / "faces"


def resolve_pack(pack: FaceModelPack | str) -> FaceModelPack:
    """Accept a pack or its name.

    Args:
        pack (FaceModelPack | str): The pack, or a key of :data:`PACKS`.

    Returns:
        FaceModelPack: The resolved pack.

    Raises:
        ValueError: When the name is unknown. The message lists the ones
            that exist, so a typo is one read away from fixed.
    """
    if isinstance(pack, FaceModelPack):
        return pack
    try:
        return PACKS[pack]
    except KeyError as exc:
        available = ", ".join(sorted(PACKS))
        raise ValueError(f"unknown pack {pack!r}; available: {available}") from exc


def ensure_models(
    pack: FaceModelPack | str = LIGHT_PACK,
    cache_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    """Download a model pack if it is not cached, and return both paths.

    Call it at build or startup time. Leaving it to the first request
    means one caller pays the download inside their timeout.

    Args:
        pack (FaceModelPack | str): Which pack to ensure.
        cache_dir (str | Path | None): Where to keep it. ``None`` uses
            :func:`default_cache_dir`.

    Returns:
        tuple[Path, Path]: ``(detector, recognizer)`` paths on disk.

    Raises:
        OSError: When the download fails or the archive lacks a model.
    """
    resolved = resolve_pack(pack)
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    directory = root / resolved.name
    detector = directory / resolved.detector
    recognizer = directory / resolved.recognizer
    if not (detector.is_file() and recognizer.is_file()):
        directory.mkdir(parents=True, exist_ok=True)
        archive = root / f"{resolved.name}.zip"
        _download(resolved.url, archive)
        with zipfile.ZipFile(archive) as bundle:
            for member in (resolved.detector, resolved.recognizer):
                # The archives nest inconsistently across packs, so match
                # on the file name rather than trusting a fixed prefix.
                names = [n for n in bundle.namelist() if n.endswith(member)]
                if not names:
                    raise OSError(f"{resolved.name}: {member} missing from {archive}")
                with bundle.open(names[0]) as source:
                    (directory / member).write_bytes(source.read())
        archive.unlink(missing_ok=True)
    for path in (detector, recognizer):
        if not path.is_file():
            raise OSError(f"{resolved.name}: {path} missing after download")
    return detector, recognizer


def _download(url: str, destination: Path) -> None:
    """Fetch ``url`` into ``destination`` atomically.

    Downloads to a sibling temporary file and renames, so an interrupted
    fetch never leaves a half-written archive that unzips to a truncated
    model.

    Args:
        url (str): Source URL.
        destination (Path): Final path.

    Raises:
        OSError: When the download fails.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    _LOGGER.info("downloading face model pack from %s", url)
    with urllib.request.urlopen(url) as response, partial.open("wb") as handle:
        while chunk := response.read(1 << 20):
            handle.write(chunk)
    partial.replace(destination)


__all__: list[str] = [
    "LARGE_PACK",
    "LIGHT_PACK",
    "PACKS",
    "FaceModelPack",
    "default_cache_dir",
    "ensure_models",
    "resolve_pack",
]

"""Measure the face-recognition separation the docs quote, reproducibly.

Two places in this repository quoted a separation measured on "a
six-person group photo": ``docs/recipes/faces.md`` reported a same-person
minimum of 0.877 and a different-people maximum of 0.180, while
``tempest_fastapi_sdk/faces/recognizer.py`` reported 0.904-0.960 against
0.225. The numbers disagree, the lists of transformations disagree, and
the photo behind either one was never committed — so neither could be
checked, and picking one over the other would have been a coin toss
dressed as a correction.

This script replaces both with a measurement anyone can repeat. The faces
are generated, not photographed: six portraits from ``sdxl-turbo`` at
fixed seeds, which removes the privacy question of shipping real people's
biometrics and makes the input part of the repository rather than part of
someone's disk.

Read the ceiling honestly. Generated passport-style portraits are frontal,
evenly lit and unoccluded, so these margins are the **easy** end of the
range. Faces that are small, turned, or badly lit score lower, which is
exactly when the threshold stops being a free choice — that is what
``LARGE_PACK`` is for.

Run it:

.. code-block:: bash

    uv sync --extra faces --extra genai-image
    uv run python scripts/face_separation.py

First run downloads ``sdxl-turbo`` (~7 GB) and the ``buffalo_s`` pack
(16 MB); later runs reuse both from cache.
"""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path

from PIL import Image

from tempest_fastapi_sdk.faces import FaceRecognizer, compare_faces
from tempest_fastapi_sdk.genai import ImageGenerationConfig, ImageGenerator

CACHE: Path = Path(".cache/face-separation")
"""Where the generated portraits are kept between runs."""

PROMPTS: tuple[str, ...] = (
    "a passport photo of an elderly bearded man, plain grey background",
    "a passport photo of a young woman with curly hair, plain grey background",
    "a passport photo of a middle aged asian man, plain grey background",
    "a passport photo of a black woman with short hair, plain grey background",
    "a passport photo of a blonde teenage boy, plain grey background",
    "a passport photo of an indian woman with glasses, plain grey background",
)
"""One prompt per identity. Six, to match the group photo the docs used."""

FIRST_SEED: int = 1000
"""Seed of the first portrait; each next one takes the next integer."""


async def portraits() -> list[Path]:
    """Render one portrait per prompt, reusing any already on disk.

    Returns:
        list[Path]: The PNG paths, one per identity.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    paths = [CACHE / f"person_{index}.png" for index in range(len(PROMPTS))]
    missing = [path for path in paths if not path.exists()]
    if missing:
        generator = ImageGenerator("stabilityai/sdxl-turbo")
        for index, (prompt, path) in enumerate(zip(PROMPTS, paths, strict=True)):
            if path.exists():
                continue
            images = await generator.generate(
                prompt,
                config=ImageGenerationConfig(
                    seed=FIRST_SEED + index,
                    steps=4,
                    guidance_scale=0.0,
                ),
            )
            path.write_bytes(images[0].data)
    return paths


def transformed(path: Path) -> dict[str, bytes]:
    """Build the transformed versions of one portrait.

    Args:
        path (Path): The original portrait.

    Returns:
        dict[str, bytes]: Transformation label to encoded image bytes.
    """
    original = Image.open(path).convert("RGB")
    width, height = original.size
    out: dict[str, bytes] = {}

    def encode(image: Image.Image, fmt: str = "PNG", **kwargs: object) -> bytes:
        """Encode an image to bytes.

        Args:
            image (Image.Image): The image to encode.
            fmt (str): Target format.
            **kwargs (object): Extra arguments forwarded to ``Image.save``.

        Returns:
            bytes: The encoded image.
        """
        buffer = io.BytesIO()
        image.save(buffer, format=fmt, **kwargs)
        return buffer.getvalue()

    out["re-encode jpeg q40"] = encode(original, "JPEG", quality=40)
    out["rotated 8 degrees"] = encode(
        original.rotate(8, expand=True, fillcolor=(128, 128, 128))
    )
    inset_w, inset_h = width // 6, height // 6
    out["tight crop 112x112"] = encode(
        original.crop((inset_w, inset_h, width - inset_w, height - inset_h)).resize(
            (112, 112)
        )
    )
    out["mirrored"] = encode(original.transpose(Image.FLIP_LEFT_RIGHT))
    out["rescaled 50%"] = encode(original.resize((width // 2, height // 2)))
    return out


async def measure(pack: str, paths: list[Path]) -> None:
    """Measure one pack over the portraits and print the report.

    Args:
        pack (str): Model pack name, ``buffalo_s`` or ``buffalo_l``.
        paths (list[Path]): The portraits to compare.
    """
    print(f"\n=== pack {pack} ===")
    recognizer = FaceRecognizer(pack=pack)

    await recognizer.detect(paths[0])
    start = time.perf_counter()
    for path in paths:
        await recognizer.detect(path)
    detect_ms = (time.perf_counter() - start) / len(paths) * 1000
    print(f"detection: {detect_ms:.0f} ms per image (n={len(paths)}, warmed)")

    baseline: dict[int, list[float]] = {}
    for index, path in enumerate(paths):
        try:
            baseline[index] = await recognizer.embed_face(path)
        except Exception as exc:
            print(f"person_{index}: no face detected ({type(exc).__name__})")
    print(f"faces detected: {len(baseline)}/{len(paths)}")
    if len(baseline) < 2:
        print("not enough faces to measure")
        return

    print("\nsame person, transformed")
    per_label: dict[str, list[float]] = {}
    for index, path in enumerate(paths):
        if index not in baseline:
            continue
        for label, payload in transformed(path).items():
            try:
                vector = await recognizer.embed_face(payload)
            except Exception:
                continue
            per_label.setdefault(label, []).append(
                compare_faces(baseline[index], vector)
            )
    for label, scores in per_label.items():
        print(
            f"  {label:22s} n={len(scores)}  "
            f"min={min(scores):.3f}  max={max(scores):.3f}"
        )

    same = [score for scores in per_label.values() for score in scores]
    order = sorted(baseline)
    pairs = [
        compare_faces(baseline[left], baseline[right])
        for position, left in enumerate(order)
        for right in order[position + 1 :]
    ]

    print(f"\ndifferent people\n  n={len(pairs)} pairs  max={max(pairs):.3f}")
    print(
        f"\nsame person {min(same):.3f}-{max(same):.3f} (n={len(same)}), "
        f"different at most {max(pairs):.3f} (n={len(pairs)})"
    )
    print(f"gap between worst positive and best negative: {min(same) - max(pairs):.3f}")


async def main() -> None:
    """Render the portraits once and measure both packs over them."""
    paths = await portraits()
    for pack in ("buffalo_s", "buffalo_l"):
        await measure(pack, paths)


if __name__ == "__main__":
    asyncio.run(main())

# Face recognition

Finds faces in an image and turns each one into a comparable vector — enough
to count people in a photo, locate the face to crop, or tell whether two
photos show the same person.

!!! danger "This is biometric data. Read before storing it."
    A face vector identifies a person the way a fingerprint template does.
    Under Brazil's LGPD it is **sensitive personal data** (Art. 5, II), and
    processing it needs consent that is **specific and highlighted** for
    that purpose (Art. 11, I) — general terms of service do not cover it.

    This module **produces** the vectors. Storing them is a decision with
    obligations attached: recorded consent, deletion on request, and never
    keeping the original image longer than needed. The persistent enrolment
    layer ships separately, precisely so that part gets reviewed on its own.

!!! info "Required extra"
    ```bash
    uv add "tempest-fastapi-sdk[faces]"
    ```
    Brings `onnxruntime`, `pillow` and `numpy`. **No system libraries** —
    unlike `[pdf]`, a slim image is enough here.

## Your first recognition

```python
# scripts/faces.py

import asyncio

from tempest_fastapi_sdk.faces import FaceRecognizer, compare_faces


async def main() -> None:
    """Count faces in a photo and compare the first two."""
    recognizer = FaceRecognizer()
    faces = await recognizer.recognize("group.jpg")
    print(f"{len(faces)} faces")
    for face in faces:
        print(f"  {face.box.width:.0f}x{face.box.height:.0f}px  conf={face.confidence:.2f}")
    if len(faces) >= 2:
        print("same person?", compare_faces(faces[0].embedding, faces[1].embedding))


if __name__ == "__main__":
    asyncio.run(main())
```

Faces come back **largest first** — the subject of a photo is usually the
biggest face in it, so a caller taking `faces[0]` gets what they meant.

## Detecting without touching biometrics

```python
import asyncio

from tempest_fastapi_sdk.faces import FaceRecognizer


async def has_one_face(path: str) -> bool:
    """Validate that an upload contains exactly one face."""
    faces = await FaceRecognizer().detect(path)
    return len(faces) == 1
```

`detect()` returns the box, the score and the landmarks — and **no vector**.
For "is there a face here", "how many people" or "where do I crop the
thumbnail", it is cheaper and touches no biometric data.

## Comparing two photos

```python
import asyncio

from tempest_fastapi_sdk.faces import FaceRecognizer, compare_faces


async def same_person(first: str, second: str) -> bool:
    """Whether two photos show the same person."""
    recognizer = FaceRecognizer()
    left = await recognizer.embed_face(first)
    right = await recognizer.embed_face(second)
    return compare_faces(left, right) >= recognizer.threshold
```

`embed_face()` takes the largest face and **refuses** when there is none, or
when it is too small — because at enrolment time a bad vector is not a bad
answer, it is a permanently wrong profile.

### The measured margin

On a six-person group photo, with the default pack:

| comparison | similarity |
| --- | --- |
| same person (crop re-encoded at jpeg q40) | 0.962 |
| same person (rotated 8°) | 0.952 |
| same person (tight 112×112 crop) | 0.877 |
| **different people (15 pairs)** | **max 0.180** |

The default threshold is **0.45**, in the middle of a gap of nearly 0.7. It
is not a delicate choice — the opposite of the speaker-diarization case, and
worth knowing when carrying intuitions between the two.

!!! warning "Raise it for anything that grants access"
    The measurement is on cooperative, front-facing photos. Where
    recognition unlocks something, the expensive error stops being "did not
    recognise" and becomes "recognised the wrong person" — and there a
    stricter threshold trades that for asking somebody to try again.

## Choosing a model pack

| pack | size | detection | same person | diff max |
| --- | --- | --- | --- | --- |
| **`buffalo_s`** (default) | **16 MB** | **15 ms** | 0.904–0.960 | 0.225 |
| `buffalo_l` | 191 MB | 54 ms | 0.920–0.971 | 0.208 |

Twelve times smaller and 3.6× faster, for a 0.02 shift in either bound. The
large pack earns its size when faces are small, poorly lit or turned away —
the cases where the margin matters.

```python
from tempest_fastapi_sdk.faces import FaceRecognizer

recognizer = FaceRecognizer(pack="buffalo_l")
```

### The models are not in the wheel

```python
from tempest_fastapi_sdk.faces import ensure_models

ensure_models()  # honors TEMPEST_FACE_MODEL_DIR
```

Leaving it to the first request makes one user pay the download inside their
timeout.

## Why not `insightface`

It packages exactly this pipeline, and the cost was measured: **558 MB
across 24 packages**, and the `opencv-python` it requires links against
**five GL libraries** — so a slim image would need system graphics libraries
in order to recognise a face.

Running the same ONNX models directly costs `onnxruntime` + `numpy` +
`pillow`, which the SDK already carries for other features, and **zero**
system libraries. The price is the detection decoding and the alignment
transform — closed-form geometry rather than a long tail of corrections.

Rejected before that: `facenet-pytorch` (pins `torch<2.3.0`, which would cap
every consumer), `deepface` (brings TensorFlow) and `face-recognition`
(brings dlib, which has to compile).

## Details that bite

**A small face is detected but not embedded.** Below 40 px on a side the
aligned crop is mostly interpolation, and the vector would describe the
upscaling rather than the person. The face comes back with an empty
`embedding`, so a caller can tell "nobody recognisable" from "no face".

**A tight crop needs a margin.** A 112×112 photo whose face touches the
edges returned **zero** detections; with a 20% border, one. The detector
needs context around a face and an already-tight crop has none to give — so
the module adds the margin itself.

**Alignment is not a refinement.** The recognition models were trained with
eyes, nose and mouth corners at fixed positions. An unaligned crop does not
fail: it loses accuracy silently.

## Recap

- `FaceRecognizer.recognize()` detects and embeds; `detect()` only detects
  and touches no biometrics; `embed_face()` is the enrolment shape and
  refuses bad input.
- Measured margin: 0.877–0.962 same person against max 0.180 for different
  ones. Default threshold 0.45; raise it to grant access.
- The 16 MB default pack is a measurement, not an accident.
- No system libraries, no opencv, no torch.
- A face vector is **sensitive biometric data** — storing it has
  obligations.

Next: [PDF generation](pdf.md) if recognition feeds a document, or
[Self-hosted generative AI](genai.md) for speaker diarization, which follows
the same design.

# Computer vision (ONNX)

Classification, detection and segmentation APIs running on ONNX Runtime
via [`ort-vision-sdk`](https://pypi.org/project/ort-vision-sdk/). The
`[vision]` extra brings the inference engine; the
`tempest_fastapi_sdk.vision` module adds the layer an API needs: the
**Pydantic response schemas** and the **mappers** that turn a model
result into them.

```bash
uv add "tempest-fastapi-sdk[vision]"
```

!!! info "Submodule, not top-level"
    Like `cache`/`queue`/`tasks`, vision is heavy (ONNX Runtime) and
    lives in its submodule: `from tempest_fastapi_sdk.vision import Detector`.
    Accessing `Detector`/`Classifier`/`Segmenter` without the extra
    installed raises a clear `ImportError` pointing at `[vision]`. The
    schemas and mappers carry no such dependency — they always import.

## Detection

`Detector` loads an ONNX model (YOLO by default) and runs `async_predict`
(async via `asyncio.to_thread`). Each call returns a length-1 `list[...]`
— take `[0]` and map it to schemas:

```python
# src/api/routers/vision.py
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk.vision import DetectionSchema, Detector, to_detection_schemas

router = APIRouter(prefix="/api/vision", tags=["vision"])

# Load the model once (at startup / as a singleton), not per request.
detector = Detector("models/yolov8n.onnx", labels="coco")


@router.post("/detect")
async def detect(file: UploadFile) -> list[DetectionSchema]:
    """Detect objects in the uploaded image."""
    results = (await detector.async_predict(await file.read()))[0]
    return to_detection_schemas(results)
```

Each `DetectionSchema` carries `class_id`, `class_name`, `confidence` and
`box` (`x1/y1/x2/y2` in pixels). No detections → `[]`.

!!! tip "Load the model once"
    Instantiating `Detector` reads and optimizes the ONNX file — costly.
    Do it at startup (or a singleton on `app.state` / a dependency) and
    reuse it across requests. `async_predict` already offloads inference
    to a thread, so it won't block the event loop.

## Classification

`Classifier` returns the top-1 plus the ranked list.
`to_classification_schema` returns **one** `ClassificationSchema` (not a
list):

```python
from tempest_fastapi_sdk.vision import (
    ClassificationSchema,
    Classifier,
    to_classification_schema,
)

classifier = Classifier("models/resnet.onnx", labels="imagenet")


@router.post("/classify")
async def classify(file: UploadFile) -> ClassificationSchema:
    """Classify the image (top-1 + top-k probabilities)."""
    results = (await classifier.async_predict(await file.read(), top_k=5))[0]
    return to_classification_schema(results)
```

`ClassificationSchema`: `class_id`/`class_name`/`confidence` (top-1) +
`probabilities: list[ClassProbabilitySchema]` (ranked).

## Segmentation

`Segmenter` is like the detector but with masks.
`to_segmentation_schemas` returns box + label per instance — **the
per-pixel mask is omitted** from the JSON (rarely what an API wants; read
it from `SegmentationResult.mask` when you need the pixels):

```python
from tempest_fastapi_sdk.vision import (
    SegmentationSchema,
    Segmenter,
    to_segmentation_schemas,
)

segmenter = Segmenter("models/yolov8n-seg.onnx", labels="coco")


@router.post("/segment")
async def segment(file: UploadFile) -> list[SegmentationSchema]:
    results = (await segmenter.async_predict(await file.read()))[0]
    return to_segmentation_schemas(results)
```

## Accepted inputs + execution

`async_predict` accepts the same inputs as `ort-vision-sdk`: a path,
`bytes`, a `numpy.ndarray` or a PIL image — so `await file.read()` (bytes)
goes straight in.

| Method | When to use |
| --- | --- |
| `predict(img)` | Synchronous — scripts, offline jobs. |
| `async_predict(img)` | The FastAPI default — runs via `asyncio.to_thread`, never blocks the loop. |
| `ort_async_predict(img)` | ORT-native `run_async` — high concurrency. |

!!! note "Acceleration extras"
    GPU: `tempest-fastapi-sdk[vision]` + `pip install ort-vision-sdk[gpu]`
    (swaps in `onnxruntime-gpu`). OpenCV image backend:
    `ort-vision-sdk[opencv]`. Pick the providers in the constructor
    (`Detector(..., providers=[...])`).

## Ready endpoint: `make_vision_router`

Instead of wiring each route by hand, inject the objects you loaded and the
router mounts **only** the matching endpoints (mirrors `make_genai_router`):

```python
from fastapi import FastAPI
from tempest_fastapi_sdk.vision import Detector, make_vision_router

app = FastAPI()
app.include_router(make_vision_router(detector=Detector("yolov8n.onnx", labels="coco")))
# -> POST /api/vision/detect (UploadFile) -> list[DetectionSchema]
```

`classifier=` mounts `POST /classify`, `segmenter=` mounts `POST /segment`.
Only what you inject shows up; with no object it raises `ValueError`. Each
endpoint reads the `UploadFile`, calls `async_predict` and maps via
`to_*_schemas`.

## Recap

- `uv add "tempest-fastapi-sdk[vision]"`; import from `tempest_fastapi_sdk.vision` (submodule).
- `Detector` / `Classifier` / `Segmenter` (lazy; clear `ImportError` without the extra).
- Load the model **once**; use `async_predict` in FastAPI.
- Take `[0]` of the return and map it: `to_detection_schemas`, `to_classification_schema`, `to_segmentation_schemas`.
- The schemas (`DetectionSchema`/`ClassificationSchema`/`SegmentationSchema`) serialize straight into the response; the pixel mask stays out of JSON.

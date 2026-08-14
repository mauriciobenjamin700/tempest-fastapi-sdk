"""Face detection and recognition on ONNX Runtime.

    from tempest_fastapi_sdk.faces import FaceRecognizer, compare_faces

    recognizer = FaceRecognizer()
    faces = await recognizer.recognize("group.jpg")
    print(len(faces), "faces")
    print(compare_faces(faces[0].embedding, faces[1].embedding))

Needs the ``[faces]`` extra (``onnxruntime`` + ``pillow`` + ``numpy``) and
**no system libraries**. The models are fetched on first use — 16 MB for
the default pack — and ``ensure_models()`` moves that into a build step.

**A face embedding is biometric data.** Under Brazil's LGPD it is
sensitive personal data (Art. 5, II) whose processing needs specific,
highlighted consent (Art. 11, I). This module produces the vectors;
storing them is a decision with obligations attached, and the recipe
covers what they are.
"""

from tempest_fastapi_sdk.faces.detector import (
    DEFAULT_NMS_THRESHOLD as DEFAULT_NMS_THRESHOLD,
)
from tempest_fastapi_sdk.faces.detector import (
    DEFAULT_SCORE_THRESHOLD as DEFAULT_SCORE_THRESHOLD,
)
from tempest_fastapi_sdk.faces.detector import DETECT_SIZE as DETECT_SIZE
from tempest_fastapi_sdk.faces.detector import MIN_DETECT_PIXELS as MIN_DETECT_PIXELS
from tempest_fastapi_sdk.faces.detector import TIGHT_CROP_MARGIN as TIGHT_CROP_MARGIN
from tempest_fastapi_sdk.faces.detector import FaceDetector as FaceDetector
from tempest_fastapi_sdk.faces.geometry import ARCFACE_TEMPLATE as ARCFACE_TEMPLATE
from tempest_fastapi_sdk.faces.geometry import FACE_SIZE as FACE_SIZE
from tempest_fastapi_sdk.faces.geometry import align_face as align_face
from tempest_fastapi_sdk.faces.geometry import (
    similarity_transform as similarity_transform,
)
from tempest_fastapi_sdk.faces.models import LARGE_PACK as LARGE_PACK
from tempest_fastapi_sdk.faces.models import LIGHT_PACK as LIGHT_PACK
from tempest_fastapi_sdk.faces.models import PACKS as PACKS
from tempest_fastapi_sdk.faces.models import FaceModelPack as FaceModelPack
from tempest_fastapi_sdk.faces.models import default_cache_dir as default_cache_dir
from tempest_fastapi_sdk.faces.models import ensure_models as ensure_models
from tempest_fastapi_sdk.faces.models import resolve_pack as resolve_pack
from tempest_fastapi_sdk.faces.recognizer import (
    DEFAULT_MATCH_THRESHOLD as DEFAULT_MATCH_THRESHOLD,
)
from tempest_fastapi_sdk.faces.recognizer import MIN_FACE_PIXELS as MIN_FACE_PIXELS
from tempest_fastapi_sdk.faces.recognizer import FaceRecognizer as FaceRecognizer
from tempest_fastapi_sdk.faces.recognizer import compare_faces as compare_faces
from tempest_fastapi_sdk.faces.schemas import BoundingBox as BoundingBox
from tempest_fastapi_sdk.faces.schemas import DetectedFace as DetectedFace
from tempest_fastapi_sdk.faces.schemas import FaceMatch as FaceMatch

__all__: list[str] = [
    "ARCFACE_TEMPLATE",
    "DEFAULT_MATCH_THRESHOLD",
    "DEFAULT_NMS_THRESHOLD",
    "DEFAULT_SCORE_THRESHOLD",
    "DETECT_SIZE",
    "FACE_SIZE",
    "LARGE_PACK",
    "LIGHT_PACK",
    "MIN_DETECT_PIXELS",
    "MIN_FACE_PIXELS",
    "PACKS",
    "TIGHT_CROP_MARGIN",
    "BoundingBox",
    "DetectedFace",
    "FaceDetector",
    "FaceMatch",
    "FaceModelPack",
    "FaceRecognizer",
    "align_face",
    "compare_faces",
    "default_cache_dir",
    "ensure_models",
    "resolve_pack",
    "similarity_transform",
]

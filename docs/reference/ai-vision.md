# Referência: IA e visão computacional

Agentes, modelos generativos self-hosted, modelops (export, bench, quantização), visão computacional, reconhecimento facial e busca aproximada de texto.

[« Índice da referência](../reference.md)

---

## Agentes de IA

### `tempest_fastapi_sdk.agents`

::: tempest_fastapi_sdk.agents
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: alphabetical
      filters:
        - "!^_"

---

## Reconhecimento facial

### `tempest_fastapi_sdk.faces`

::: tempest_fastapi_sdk.faces.recognizer.FaceRecognizer
::: tempest_fastapi_sdk.faces.recognizer.compare_faces
::: tempest_fastapi_sdk.faces.recognizer.DEFAULT_MATCH_THRESHOLD
::: tempest_fastapi_sdk.faces.recognizer.MIN_FACE_PIXELS
::: tempest_fastapi_sdk.faces.detector.FaceDetector
::: tempest_fastapi_sdk.faces.detector.DETECT_SIZE
::: tempest_fastapi_sdk.faces.detector.DEFAULT_SCORE_THRESHOLD
::: tempest_fastapi_sdk.faces.detector.DEFAULT_NMS_THRESHOLD
::: tempest_fastapi_sdk.faces.detector.MIN_DETECT_PIXELS
::: tempest_fastapi_sdk.faces.detector.TIGHT_CROP_MARGIN
::: tempest_fastapi_sdk.faces.geometry.align_face
::: tempest_fastapi_sdk.faces.geometry.similarity_transform
::: tempest_fastapi_sdk.faces.geometry.ARCFACE_TEMPLATE
::: tempest_fastapi_sdk.faces.geometry.FACE_SIZE
::: tempest_fastapi_sdk.faces.models.FaceModelPack
::: tempest_fastapi_sdk.faces.models.LIGHT_PACK
::: tempest_fastapi_sdk.faces.models.LARGE_PACK
::: tempest_fastapi_sdk.faces.models.PACKS
::: tempest_fastapi_sdk.faces.models.ensure_models
::: tempest_fastapi_sdk.faces.models.resolve_pack
::: tempest_fastapi_sdk.faces.models.default_cache_dir
::: tempest_fastapi_sdk.faces.schemas.DetectedFace
::: tempest_fastapi_sdk.faces.schemas.BoundingBox
::: tempest_fastapi_sdk.faces.schemas.FaceMatch

---

## Computer vision

### `tempest_fastapi_sdk.vision`

::: tempest_fastapi_sdk.vision
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Modelops

### `tempest_fastapi_sdk.modelops`

::: tempest_fastapi_sdk.modelops
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Modelos self-hosted

A superfície de topo do `genai` — geração de texto, embeddings, VLM,
moderação, cache, métricas e router. Os submódulos que exigem outra
disciplina (`hub`, `image`, `inventory`, `rag`, `audio`) vêm depois.

### `tempest_fastapi_sdk.genai`

::: tempest_fastapi_sdk.genai
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: alphabetical
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.rag`

::: tempest_fastapi_sdk.genai.rag
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: alphabetical
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.audio`

::: tempest_fastapi_sdk.genai.audio
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: alphabetical
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.hub`

::: tempest_fastapi_sdk.genai.hub
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.inventory`

::: tempest_fastapi_sdk.genai.inventory
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.image`

::: tempest_fastapi_sdk.genai.image
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Texto (busca aproximada)

### `tempest_fastapi_sdk.text`

::: tempest_fastapi_sdk.text
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

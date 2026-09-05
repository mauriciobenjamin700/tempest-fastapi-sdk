# Visão computacional (ONNX)

APIs de classificação, detecção e segmentação rodando em ONNX Runtime,
via [`ort-vision-sdk`](https://pypi.org/project/ort-vision-sdk/). O extra
`[vision]` traz o motor de inferência; o módulo
`tempest_fastapi_sdk.vision` adiciona a camada que falta pra uma API: os
**schemas Pydantic** de resposta e os **mappers** que convertem o
resultado do modelo neles.

```bash
uv add "tempest-fastapi-sdk[vision]"
```

!!! info "Submódulo, não top-level"
    Como `cache`/`queue`/`tasks`, a visão é pesada (ONNX Runtime) e fica
    no submódulo: `from tempest_fastapi_sdk.vision import Detector`.
    Acessar `Detector`/`Classifier`/`Segmenter` sem o extra instalado
    levanta um `ImportError` claro apontando pra `[vision]`. Os schemas e
    mappers não dependem do extra — importam sempre.

## Detecção

`Detector` carrega um modelo ONNX (YOLO por padrão) e roda `async_predict`
(async via `asyncio.to_thread`). Cada chamada devolve `list[...]` de
tamanho 1 — pegue o `[0]` e mapeie pros schemas:

```python
# src/api/routers/vision.py
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk.vision import DetectionSchema, Detector, to_detection_schemas

router = APIRouter(prefix="/api/vision", tags=["vision"])

# Carregue o modelo uma vez (no startup / singleton), não por request.
detector = Detector("models/yolov8n.onnx", labels="coco")


@router.post("/detect")
async def detect(file: UploadFile) -> list[DetectionSchema]:
    """Detecta objetos na imagem enviada."""
    results = (await detector.async_predict(await file.read()))[0]
    return to_detection_schemas(results)
```

Cada `DetectionSchema` traz `class_id`, `class_name`, `confidence` e
`box` (`x1/y1/x2/y2` em pixels). Sem deteções → `[]`.

!!! tip "Carregue o modelo uma vez"
    Instanciar `Detector` lê e otimiza o arquivo ONNX — caro. Faça no
    startup (ou um singleton em `app.state` / dependency) e reuse entre
    requests. `async_predict` já joga a inferência numa thread, então não
    bloqueia o event loop.

## Classificação

`Classifier` devolve o top-1 + a lista ranqueada. `to_classification_schema`
retorna **um** `ClassificationSchema` (não uma lista):

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk.vision import (
    ClassificationSchema,
    Classifier,
    to_classification_schema,
)

router = APIRouter()


classifier = Classifier("models/resnet.onnx", labels="imagenet")


@router.post("/classify")
async def classify(file: UploadFile) -> ClassificationSchema:
    """Classifica a imagem (top-1 + probabilidades top-k)."""
    results = (await classifier.async_predict(await file.read(), top_k=5))[0]
    return to_classification_schema(results)
```

`ClassificationSchema`: `class_id`/`class_name`/`confidence` (top-1) +
`probabilities: list[ClassProbabilitySchema]` (ranqueado).

## Segmentação

`Segmenter` é como o detector mas com máscaras. `to_segmentation_schemas`
devolve box + label por instância — **a máscara em pixels é omitida** do
JSON (raramente é o que uma API quer; leia de `SegmentationResult.mask`
quando precisar dos pixels):

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk.vision import (
    SegmentationSchema,
    Segmenter,
    to_segmentation_schemas,
)

router = APIRouter()


segmenter = Segmenter("models/yolov8n-seg.onnx", labels="coco")


@router.post("/segment")
async def segment(file: UploadFile) -> list[SegmentationSchema]:
    results = (await segmenter.async_predict(await file.read()))[0]
    return to_segmentation_schemas(results)
```

## Detecção + classificação num grafo só

Às vezes o rótulo que interessa não é *o que* está na imagem, mas *qual é
o estado* de cada coisa encontrada: um detector acha cada ovelha, e um
classificador julga a mucosa de cada uma. Rodar os dois estágios como dois
modelos custa uma ida e volta por objeto detectado.

`DetectClassify` roda um `.onnx` que já tem os dois estágios fundidos —
uma inferência, quantos objetos houver:

```python
from fastapi import FastAPI, UploadFile

from tempest_fastapi_sdk.vision import (
    DetectClassify,
    DetectClassifySchema,
    to_detect_classify_schemas,
)

app: FastAPI = FastAPI()
pipeline: DetectClassify = DetectClassify("fused.onnx")


@app.post("/triage")
async def triage(file: UploadFile) -> list[DetectClassifySchema]:
    """Detect every animal and classify the crop of each one.

    Args:
        file (UploadFile): The uploaded image.

    Returns:
        list[DetectClassifySchema]: One entry per detected object, each
            carrying the second-stage verdict.
    """
    results = (await pipeline.async_predict(await file.read()))[0]
    return to_detect_classify_schemas(results)
```

A resposta aninha o segundo estágio dentro de cada detecção:

```json
[
  {
    "class_id": 18,
    "class_name": "sheep",
    "confidence": 0.94,
    "box": {"x1": 12.0, "y1": 40.0, "x2": 210.0, "y2": 300.0},
    "classification": {
      "class_id": 3,
      "class_name": "famacha_3",
      "confidence": 0.77,
      "probabilities": [
        {"class_id": 3, "class_name": "famacha_3", "probability": 0.77},
        {"class_id": 2, "class_name": "famacha_2", "probability": 0.21}
      ]
    }
  }
]
```

!!! warning "Os dois `class_id` não são comparáveis"
    Detector e classificador têm espaços de rótulo **independentes**: o
    `18` de `sheep` e o `3` de `famacha_3` não têm relação nenhuma. Por
    isso o veredito do classificador vem aninhado em `classification`, e
    não achatado ao lado — achatar faria os dois ids parecerem da mesma
    escala.

    `classification` é `null` quando o pipeline não produziu segundo
    estágio para aquele objeto.

!!! tip "Rodar é `[vision]`; fundir é `[modelops-compose]`"
    O `.onnx` fundido é um artefato de build. Servir ele **não** puxa
    `onnx` — só `[vision]`, como o resto desta página. Quem monta o
    arquivo é `fuse_detect_classify`, na receita
    [Modelops](modelops.md#fundir-detector-e-classificador).

## Inputs aceitos + execução

`async_predict` aceita os mesmos inputs do `ort-vision-sdk`: caminho,
`bytes`, `numpy.ndarray` ou imagem PIL — então `await file.read()` (bytes)
entra direto.

| Método | Quando usar |
| --- | --- |
| `predict(img)` | Síncrono — scripts, jobs offline. |
| `async_predict(img)` | Default em FastAPI — roda via `asyncio.to_thread`, não bloqueia o loop. |
| `ort_async_predict(img)` | `run_async` nativo do ORT — alta concorrência. |

!!! note "Extras de aceleração"
    GPU: `tempest-fastapi-sdk[vision]` + `pip install ort-vision-sdk[gpu]`
    (troca pra `onnxruntime-gpu`). Backend OpenCV de imagem:
    `ort-vision-sdk[opencv]`. Escolha os providers no construtor
    (`Detector(..., providers=[...])`).

## Endpoint pronto: `make_vision_router`

Em vez de fiar cada rota na mão, injete os objetos que você carregou e o
router monta **só** os endpoints correspondentes (espelha o
`make_genai_router`):

```python
from fastapi import FastAPI
from tempest_fastapi_sdk.vision import Detector, make_vision_router

app = FastAPI()
app.include_router(make_vision_router(detector=Detector("yolov8n.onnx", labels="coco")))
# -> POST /api/vision/detect (UploadFile) -> list[DetectionSchema]
```

`classifier=` monta `POST /classify`, `segmenter=` monta `POST /segment`.
Só o que for injetado aparece; sem nenhum objeto, levanta `ValueError`.
Cada endpoint lê o `UploadFile`, chama `async_predict` e mapeia via
`to_*_schemas`.

## Recap

- `uv add "tempest-fastapi-sdk[vision]"`; importe de `tempest_fastapi_sdk.vision` (submódulo).
- `Detector` / `Classifier` / `Segmenter` (lazy; `ImportError` claro sem o extra).
- Carregue o modelo **uma vez**; use `async_predict` no FastAPI.
- Pegue o `[0]` do retorno e mapeie: `to_detection_schemas`, `to_classification_schema`, `to_segmentation_schemas`.
- Os schemas (`DetectionSchema`/`ClassificationSchema`/`SegmentationSchema`) serializam direto na resposta; máscara de pixels fica de fora do JSON.

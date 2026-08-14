# Reconhecimento facial

Detecta rostos numa imagem e transforma cada um num vetor comparável — o
suficiente para contar pessoas numa foto, achar o rosto para recortar, ou
dizer se duas fotos são da mesma pessoa.

!!! danger "Isso é dado biométrico. Leia antes de guardar."
    Um vetor de rosto identifica uma pessoa como um template de digital.
    Pela LGPD é **dado pessoal sensível** (Art. 5º, II), e o tratamento
    exige consentimento **específico e destacado** para essa finalidade
    (Art. 11, I) — termo de uso genérico não cobre.

    Este módulo **produz** os vetores. Guardá-los é decisão com obrigações
    anexadas: consentimento registrado, exclusão a pedido, e nunca manter a
    imagem original sem necessidade. A camada de cadastro persistente vem
    numa entrega separada, justamente para essa parte ser revisada à parte.

!!! info "Extra necessário"
    ```bash
    uv add "tempest-fastapi-sdk[faces]"
    ```
    Traz `onnxruntime`, `pillow` e `numpy`. **Nenhuma biblioteca de
    sistema** — diferente do `[pdf]`, aqui a imagem slim basta.

## Seu primeiro reconhecimento

```python
# scripts/rostos.py

import asyncio

from tempest_fastapi_sdk.faces import FaceRecognizer, compare_faces


async def main() -> None:
    """Count faces in a photo and compare the first two."""
    recognizer = FaceRecognizer()
    faces = await recognizer.recognize("grupo.jpg")
    print(f"{len(faces)} rostos")
    for face in faces:
        print(f"  {face.box.width:.0f}x{face.box.height:.0f}px  conf={face.confidence:.2f}")
    if len(faces) >= 2:
        print("mesma pessoa?", compare_faces(faces[0].embedding, faces[1].embedding))


if __name__ == "__main__":
    asyncio.run(main())
```

Os rostos voltam **do maior para o menor** — o sujeito de uma foto costuma
ser o rosto maior nela, então quem pega `faces[0]` recebe o que quis dizer.

## Só detectar, sem biometria

```python
import asyncio

from tempest_fastapi_sdk.faces import FaceRecognizer


async def tem_rosto(caminho: str) -> bool:
    """Validate that an upload contains exactly one face."""
    faces = await FaceRecognizer().detect(caminho)
    return len(faces) == 1
```

`detect()` devolve caixa, confiança e pontos de referência — e **nenhum
vetor**. Para "tem rosto nessa foto?", "quantas pessoas?" ou "onde recortar
a miniatura?", é mais barato e não toca em dado biométrico.

## Comparar duas fotos

```python
import asyncio

from tempest_fastapi_sdk.faces import FaceRecognizer, compare_faces


async def mesma_pessoa(a: str, b: str) -> bool:
    """Whether two photos show the same person."""
    recognizer = FaceRecognizer()
    va = await recognizer.embed_face(a)
    vb = await recognizer.embed_face(b)
    return compare_faces(va, vb) >= recognizer.threshold
```

`embed_face()` pega o maior rosto e **recusa** quando não há rosto ou ele é
pequeno demais — porque na hora de cadastrar, um vetor ruim não é uma
resposta ruim, é um perfil permanentemente errado.

### A folga medida

Sobre uma foto de grupo com seis pessoas, no pack padrão:

| comparação | similaridade |
| --- | --- |
| mesma pessoa (recorte re-encodado em jpeg q40) | 0,962 |
| mesma pessoa (rotacionada 8°) | 0,952 |
| mesma pessoa (recorte apertado 112×112) | 0,877 |
| **pessoas diferentes (15 pares)** | **máx 0,180** |

O limiar padrão é **0,45**, no meio de uma folga de quase 0,7. Não é
escolha delicada — o oposto do caso da diarização de voz, e vale saber ao
transportar intuições entre os dois.

!!! warning "Suba o limiar para conceder acesso"
    A medição é em fotos cooperativas, de frente. Onde o reconhecimento
    libera algo, o erro caro deixa de ser "não reconheceu" e passa a ser
    "reconheceu errado" — e aí um limiar mais estrito troca isso por pedir
    à pessoa que tente de novo.

## Escolhendo o pack de modelos

| pack | tamanho | detecção | mesma pessoa | dif. máx |
| --- | --- | --- | --- | --- |
| **`buffalo_s`** (padrão) | **16 MB** | **15 ms** | 0,904–0,960 | 0,225 |
| `buffalo_l` | 191 MB | 54 ms | 0,920–0,971 | 0,208 |

Doze vezes menor e 3,6× mais rápido, por 0,02 de deslocamento em cada
limite. O pack grande vale quando os rostos são pequenos, mal iluminados ou
de perfil — os casos em que a margem importa.

```python
from tempest_fastapi_sdk.faces import FaceRecognizer

recognizer = FaceRecognizer(pack="buffalo_l")
```

### Os modelos não vêm no wheel

```python
from tempest_fastapi_sdk.faces import ensure_models

ensure_models()  # honra TEMPEST_FACE_MODEL_DIR
```

Deixar para a primeira requisição faz um usuário pagar o download dentro do
timeout dele.

## Por que não `insightface`

Ele empacota exatamente esse pipeline, e mediu-se o custo: **558 MB em 24
pacotes**, e o `opencv-python` que ele exige liga contra **cinco
bibliotecas GL** — ou seja, a imagem slim passa a precisar de bibliotecas
gráficas de sistema para reconhecer um rosto.

Rodar os mesmos modelos ONNX direto custa `onnxruntime` + `numpy` +
`pillow`, que o SDK já carrega para outras coisas, e **zero** biblioteca de
sistema. O preço é a decodificação da detecção e o alinhamento — geometria
de forma fechada, não uma cauda longa de correções.

Rejeitados antes disso: `facenet-pytorch` (trava `torch<2.3.0`, o que
limitaria todo consumidor), `deepface` (traz TensorFlow) e
`face-recognition` (traz dlib, que precisa compilar).

## Detalhes que mordem

**Rosto pequeno detecta mas não é embutido.** Abaixo de 40 px de lado, o
recorte alinhado é quase interpolação, e o vetor descreveria a ampliação em
vez da pessoa. O rosto volta com `embedding` vazio — assim quem chama
distingue "ninguém reconhecível" de "nenhum rosto".

**Recorte apertado precisa de moldura.** Uma foto 112×112 em que o rosto
toca as bordas devolvia **zero** detecções; com 20% de moldura, uma. O
detector precisa de contexto ao redor, e um recorte já apertado não tem
nenhum para dar — então o módulo adiciona a moldura sozinho.

**Alinhamento não é refinamento.** Os modelos de reconhecimento foram
treinados com olhos, nariz e cantos da boca em posições fixas. Recorte não
alinhado não falha: perde acurácia em silêncio.

## Recap

- `FaceRecognizer.recognize()` detecta e embute; `detect()` só detecta e não
  toca em biometria; `embed_face()` é a forma de cadastro e recusa entrada
  ruim.
- Folga medida: 0,877–0,962 mesma pessoa contra máx 0,180 entre diferentes.
  Limiar padrão 0,45; suba para conceder acesso.
- Pack padrão de 16 MB por medição, não por acaso.
- Sem biblioteca de sistema, sem opencv, sem torch.
- Vetor de rosto é **dado biométrico sensível** — guardar tem obrigações.

Próximo: [Geração de PDF](pdf.md) se o reconhecimento alimenta um documento,
ou [IA generativa self-hosted](genai.md) para a diarização de voz, que segue
o mesmo desenho.

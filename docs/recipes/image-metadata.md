# Metadados de imagem — limpar a foto do usuário

Uma foto tirada no celular carrega mais do que pixels. Dentro do arquivo vão
as **coordenadas de GPS** de onde ela foi tirada, a marca, o modelo e às vezes
o número de série do aparelho, a data e a hora, e de vez em quando uma
miniatura da foto original, sem o recorte que o usuário fez.

Ninguém vê isso na tela. Mas quando o seu serviço guarda a foto no storage, ou
a repassa a um terceiro (um provedor de IA, um parceiro, um bucket público), os
metadados vão junto. Uma foto de perfil vira o endereço de casa de quem a
enviou.

`strip_image_metadata` devolve a mesma imagem, no mesmo formato, sem nada
disso.

!!! info "Extra necessário"
    ```bash
    uv add "tempest-fastapi-sdk[image]"
    ```
    Traz só o `pillow`. O `[all]` já inclui.

## Sua primeira rota de upload limpa

Uma rota que recebe a foto, limpa e grava em disco:

```python
# main.py

import asyncio
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Form, UploadFile
from pydantic import BaseModel

from tempest_fastapi_sdk import (
    ImageMediaTypeEnum,
    read_upload_capped,
    register_exception_handlers,
    strip_image_metadata,
)

MAX_PHOTO_BYTES: int = 10 * 1024 * 1024
PHOTOS_DIR: Path = Path("photos")


class PhotoResponseSchema(BaseModel):
    """Onde a foto foi gravada, e com quantos bytes ficou."""

    key: str
    content_type: ImageMediaTypeEnum
    size: int


app = FastAPI()
register_exception_handlers(app)


@app.post("/photos")
async def upload_photo(
    content_type: Annotated[ImageMediaTypeEnum, Form()],
    file: UploadFile,
) -> PhotoResponseSchema:
    """Grava a foto só depois que os metadados saíram."""
    data: bytes = await read_upload_capped(file, max_bytes=MAX_PHOTO_BYTES)
    clean: bytes = await asyncio.to_thread(strip_image_metadata, data, content_type)
    key: str = f"{uuid4()}.{content_type.extension}"
    PHOTOS_DIR.mkdir(exist_ok=True)
    await asyncio.to_thread((PHOTOS_DIR / key).write_bytes, clean)
    return PhotoResponseSchema(key=key, content_type=content_type, size=len(clean))
```

### Experimente

Este script monta um JPEG como o celular grava (GPS, aparelho e orientação
"em pé"), manda para a rota e confere o arquivo gravado:

```python
# try_it.py

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from PIL.ExifTags import GPS, IFD, Base

from main import PHOTOS_DIR, app


def phone_photo() -> bytes:
    """Monta um JPEG como o celular grava: GPS, aparelho e orientação."""
    exif = Image.Exif()
    exif[Base.Make] = "AcmePhone"
    exif[Base.Orientation] = 6
    exif[IFD.GPSInfo] = {GPS.GPSLatitudeRef: "S", GPS.GPSLatitude: (23.0, 33.0, 1.0)}
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "teal").save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def main() -> None:
    """Envia a foto e inspeciona o que foi gravado."""
    client = TestClient(app)
    photo: bytes = phone_photo()
    response = client.post(
        "/photos",
        data={"content_type": "image/jpeg"},
        files={"file": ("photo.jpg", photo, "image/jpeg")},
    )
    print(response.status_code, response.json()["content_type"])
    stored: Path = PHOTOS_DIR / response.json()["key"]
    with Image.open(stored) as image:
        print("size:", image.size)
        print("exif tags:", len(image.getexif()))
    print("AcmePhone in the original:", b"AcmePhone" in photo)
    print("AcmePhone in the stored file:", b"AcmePhone" in stored.read_bytes())
    wrong = client.post(
        "/photos",
        data={"content_type": "image/png"},
        files={"file": ("photo.png", photo, "image/png")},
    )
    print(wrong.status_code, wrong.json()["code"])


if __name__ == "__main__":
    main()
```

Rode com `python try_it.py`:

```text
200 image/jpeg
size: (300, 400)
exif tags: 0
AcmePhone in the original: True
AcmePhone in the stored file: False
415 IMAGE_TYPE_MISMATCH
```

A foto enviada tinha 400 x 300 com a tag de orientação `6` ("gire 90° para
mostrar"). A gravada tem 300 x 400 e **nenhuma** tag: a rotação foi aplicada
aos pixels antes de o EXIF sair, então ela continua em pé em qualquer
visualizador. O mesmo JPEG declarado como PNG foi recusado.

## Pedaço por pedaço

### Ler com teto

`read_upload_capped` para de ler no teto de bytes, então um upload gigante não
chega inteiro na memória. É o limite do **arquivo**; o limite de **pixels**
vem a seguir.

### Limpar fora do event loop

`strip_image_metadata` decodifica e codifica de novo: trabalho de CPU, numa
função síncrona.
Chamada direto numa rota `async`, ela trava o event loop enquanto roda;
`asyncio.to_thread` leva para uma thread e o servidor segue atendendo.

O segundo argumento dela é o tipo **declarado** pelo cliente. A função confere os
bytes contra ele e recusa quando não batem: você nunca grava um PNG com
extensão `.jpg`.

### Gravar o resultado, nunca o original

Depois da limpeza, `data` não deve ir a lugar nenhum. Grave e repasse só
`clean`.

!!! tip "Erro nunca devolve os bytes originais"
    Quando a imagem não decodifica, não bate com o tipo ou passa do teto, a
    função **levanta**. Não existe caminho em que ela devolve `data` intacto
    "porque não deu para limpar", então um `try`/`except` que siga gravando
    o original seria escolha explícita de quem escreveu, não acidente.

## O que sai e o que fica

| Sai | Fica |
| --- | --- |
| EXIF (GPS, marca, modelo, data, miniatura embutida) | os pixels, já na orientação certa |
| XMP | o tamanho (com largura e altura trocadas quando a orientação gira 90°) |
| IPTC (segmento `Photoshop 3.0` do JPEG) | o modo (`RGB`, `RGBA`, `L`, `P`...) |
| perfil de cor ICC | a transparência |
| comentários (JPEG, GIF) e chunks de texto do PNG (`tEXt`, `iTXt`, `zTXt`) | o formato |

Formatos aceitos: JPEG, PNG, WEBP, GIF e BMP. O `image/heic` do
`ImageMediaTypeEnum` é recusado com `415`: o Pillow não traz codec HEIC.

!!! warning "Animação vira o primeiro quadro"
    GIF e WEBP animados voltam **só com o primeiro quadro**. Recodificar
    todos multiplicaria o custo por uma contagem de quadros que o cliente
    controla. Se o seu serviço precisa guardar animação, não passe esse
    upload por aqui.

!!! note "Sem o ICC, a cor é lida como sRGB"
    O perfil de cor também sai, e os pixels passam a ser lidos como sRGB.
    Numa foto que já era sRGB isso não muda nada; numa foto em gamut amplo
    (Display P3, comum em celular recente) a cor pode mudar levemente de
    saturação.

## Os erros

Todos estendem `AppException`, então o `register_exception_handlers` já os
transforma em resposta com `code`:

| Exceção | Status | `code` | Quando |
| --- | --- | --- | --- |
| `ImageDecodeException` | `422` | `IMAGE_DECODE_FAILED` | os bytes não são imagem de formato aceito, ou estão truncados |
| `ImageTypeMismatchException` | `415` | `IMAGE_TYPE_MISMATCH` | é imagem, mas de outro formato que o declarado |
| `ImageFormatUnsupportedException` | `415` | `IMAGE_FORMAT_UNSUPPORTED` | o tipo declarado não é um que a função recodifica (`image/heic`, `application/pdf`) |
| `ImageTooLargeException` | `413` | `IMAGE_TOO_LARGE` | a imagem tem mais pixels que `max_pixels` |

`ImageTypeMismatchException` e `ImageFormatUnsupportedException` estendem
`InvalidFileTypeException`, e `ImageTooLargeException` estende
`FileTooLargeException`: um `except` que já pegava esses dois continua
pegando.

## O teto de pixels

Um PNG de poucos KB pode declarar 20 000 x 20 000 pixels. Decodificado, vira
gigabytes de memória: é a *decompression bomb*. Por isso a função lê largura e
altura do cabeçalho e confere contra `max_pixels` **antes** de decodificar
qualquer pixel:

```python
from tempest_fastapi_sdk import DEFAULT_MAX_IMAGE_PIXELS, strip_image_metadata


def clean_avatar(data: bytes) -> bytes:
    """Limpa um avatar, aceitando no máximo 4 000 x 4 000 pixels."""
    return strip_image_metadata(data, "image/jpeg", max_pixels=16_000_000)


print(DEFAULT_MAX_IMAGE_PIXELS)
```

O padrão é `DEFAULT_MAX_IMAGE_PIXELS` (50 milhões), que cabe a foto cheia de um
sensor de 48 ou 50 MP. Memória cresce com os pixels: medido no Pillow 13.0.0
com um JPEG de 8000 x 6000, o pico da chamada subiu cerca de 186 MB com a foto
em pé e 368 MB com uma que a orientação gira 90°. Suba o teto sabendo disso.

??? info "Detalhes técnicos: o teto global do Pillow"
    O Pillow tem a própria proteção, `PIL.Image.MAX_IMAGE_PIXELS` (cerca de
    89,5 milhões por padrão), que vale para o processo inteiro. A função
    **não** mexe nela. Ela continua rodando quando o arquivo é aberto: acima
    dela o Pillow emite um `DecompressionBombWarning`, e acima do dobro recusa
    sozinho. Os dois casos chegam a você como `ImageTooLargeException` (o
    aviso, quando você o escala para erro; senão, o teto da função recusa em
    seguida).

## Qualidade da recodificação

| Formato | Como volta |
| --- | --- |
| JPEG | `jpeg_quality`, padrão `DEFAULT_JPEG_QUALITY` (90; o do Pillow é 75) |
| WEBP sem perda | continua sem perda |
| WEBP com perda | `webp_quality`, padrão `DEFAULT_WEBP_QUALITY` (90) |
| PNG, GIF, BMP | sem perda, como sempre |

JPEG é recodificado com perda, então os bytes mudam mesmo numa foto que não
tinha metadado nenhum. A função não redimensiona.

??? info "Detalhes técnicos: por que recodificar em vez de cortar segmentos"
    Dá para tirar o EXIF de um JPEG cortando o segmento `APP1`. Mas cada
    formato esconde metadado num lugar diferente: `APP1` (EXIF e XMP) e
    `APP13` (IPTC) no JPEG, `eXIf`, `iCCP`, `tEXt`, `iTXt` e `zTXt` no PNG,
    `EXIF`, `XMP ` e `ICCP` no WEBP, extensões de comentário no GIF. Um
    cortador à mão erra o caso que ele não conhece. A função abre a imagem
    pelo decodificador do Pillow, mantém só os pixels (e a transparência que
    eles precisam) e codifica de novo sem passar `exif=`, `icc_profile=` ou
    `pnginfo=`, então o que o arquivo de origem carregava não tem como voltar.

## Recap

- `strip_image_metadata(data, content_type)` devolve a imagem no mesmo
  formato, sem EXIF, XMP, IPTC, ICC, comentário e chunk de texto.
- A orientação do EXIF é aplicada aos pixels antes de sair: a foto continua
  em pé.
- Tipo declarado diferente do real, bytes que não decodificam e imagem acima
  de `max_pixels` levantam erro tipado; o original nunca volta.
- Numa rota `async`, chame por `asyncio.to_thread`.
- Animação volta como o primeiro quadro; HEIC é recusado.
- Extra `[image]` (só `pillow`).

Veja também: [Uploads](uploads.md), para receber e validar o arquivo antes de
limpar.

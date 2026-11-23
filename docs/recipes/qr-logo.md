# QR Code com logo (link de carona)

Um QR Code impresso no vidro do carro, num cartaz da rodoviária ou na tela
do motorista leva o passageiro direto à carona. Com o logo da marca no
centro, ele também diz de quem é o link antes de alguém apontar a câmera.

O logo cobre módulos do QR. Quem compensa isso é a **correção de erro**: o
nível `H` recupera até 30% do símbolo. Esta receita gera o QR no nível `H`,
cola o logo num quadro branco no centro, num tamanho que foi medido para
continuar lendo, e serve a imagem numa rota.

Ao final você terá:

- `qr_with_logo(url, logo)`: o PNG do QR com o logo no centro;
- `GET /caronas/{trip_id}/qr.png`: o QR de cada carona, apontando para a
  [página de compartilhamento](share-preview.md) dela;
- um teste que decodifica o QR gerado, para o logo nunca crescer além do que
  lê.

## Instalação

O QR é gerado pelo [`segno`](https://pypi.org/project/segno/), que é Python
puro e sem dependência. O logo é colado com o Pillow, do extra `[image]`:

```bash
uv add "tempest-fastapi-sdk[image]" segno
```

## Passo 1: o QR com o logo

```python
import io
from pathlib import Path

import segno
from PIL import Image

LOGO_FRACTION: float = 0.25
QR_SCALE: int = 10
QR_BORDER: int = 4
LOGO_PADDING: int = 10


def qr_with_logo(url: str, logo: Path, *, logo_fraction: float = LOGO_FRACTION) -> bytes:
    qr = segno.make(url, error="h", micro=False)
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=QR_SCALE, border=QR_BORDER)
    image = Image.open(buffer).convert("RGB")

    side = qr.symbol_size(scale=QR_SCALE, border=0)[0]
    logo_side = int(side * logo_fraction)
    mark = Image.open(logo).convert("RGBA")
    mark.thumbnail((logo_side, logo_side), Image.Resampling.LANCZOS)

    frame = Image.new(
        "RGB",
        (mark.width + 2 * LOGO_PADDING, mark.height + 2 * LOGO_PADDING),
        "white",
    )
    frame.paste(mark, (LOGO_PADDING, LOGO_PADDING), mark)
    image.paste(
        frame,
        ((image.width - frame.width) // 2, (image.height - frame.height) // 2),
    )

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
```

O resultado, com o link `https://app.buscar.com.br/s/42` e um logo de 400×400:

![QR Code preto e branco com um quadrado azul arredondado e a letra B amarela no centro](../assets/qr-logo.png)

Pedaço por pedaço:

- **`error="h"`** é o que permite o logo. Sem ele, o `segno` 1.6.6 escolheu
  `L` (7% de recuperação) para os links desta receita. No `H`, o QR fica mais
  denso (mais módulos para o mesmo link), e é essa redundância que o logo
  consome.
- **`micro=False`** evita o Micro QR, que o `segno` escolhe para textos bem
  curtos (para `"42"` ele gerou um `M1`) e que leitores de celular, em geral,
  não suportam.
- **`symbol_size(..., border=0)`** é o lado do QR **sem** a borda branca. O
  logo é dimensionado contra esse lado, não contra a imagem inteira.
- **`thumbnail`** reduz o logo mantendo a proporção. Um logo largo fica
  baixo, nunca passa do quadrado reservado.
- **O quadro branco** (`LOGO_PADDING`) separa o logo dos módulos em volta.
  Sem ele, um logo escuro encostado nos módulos pretos confunde o leitor
  sobre onde cada módulo termina.

## Quanto logo cabe

Medido com `segno` 1.6.6 e `zxing-cpp` 3.1.1. Configuração: QR nível `H`,
`scale=10`, logo quadrado e opaco com quadro branco de 10 px, e dois links, um
de 30 caracteres (versão 4 do QR) e um de 64 (versão 7). "Degradada" é a
imagem reduzida e borrada, em quatro combinações (redução de 1/2 a 1/4,
desfoque gaussiano de 0,6 a 1,0 px), para imitar uma foto de câmera:

| Lado do logo (fração do QR) | Área coberta | Link de 30 car.: limpa / degradada | Link de 64 car.: limpa / degradada |
| --- | --- | --- | --- |
| 20% | 4% | lê / 4 de 4 | lê / 4 de 4 |
| 25% | 6% | lê / 4 de 4 | lê / 4 de 4 |
| 30% | 9% | lê / 4 de 4 | lê / 4 de 4 |
| 35% | 12% | lê / 4 de 4 | lê / 4 de 4 |
| 40% | 16% | falha / 0 de 4 | lê / 2 de 4 |
| 45% | 20% | falha / 0 de 4 | falha / 0 de 4 |

O padrão `LOGO_FRACTION = 0.25` fica dois degraus abaixo do primeiro que
falhou (40%). A folga é para o que a medição não reproduz: reflexo no vidro,
impressão em baixa resolução, câmera de celular antigo.

!!! warning "A medição é de leitor, não de câmera"
    A tabela foi medida decodificando a imagem com `zxing-cpp`, não
    apontando um celular. Antes de imprimir em lote, teste o QR final com
    duas ou três câmeras diferentes, na distância e na luz em que ele vai
    ser lido.

Duas escolhas fora do logo também pesam:

- **Tamanho do link.** O link de 30 caracteres virou um QR versão 4 (33×33
  módulos), e o de 64 virou versão 7 (45×45). No mesmo tamanho impresso,
  mais módulos são módulos menores, e a câmera precisa chegar mais perto.
  Prefira `/s/42` a uma URL com UUID e parâmetros de campanha. Na tabela, os
  dois leram com 25%.
- **Cores.** A tabela foi medida com módulos pretos em fundo branco. Ponha a
  cor da marca no logo e deixe o QR em preto e branco. Outra combinação não
  foi medida.

## Passo 2: o QR de cada carona

O QR deve apontar para a **página de compartilhamento** da carona
(`/s/{id}`, da receita [Compartilhar com prévia](share-preview.md)), e não
para o app direto. Quem escaneia cai no navegador, e a página leva ao app. O
mesmo link, colado no WhatsApp, mostra a prévia com o card.

```python
import io
from pathlib import Path

import segno
from fastapi import FastAPI, HTTPException, Response
from PIL import Image

PUBLIC_URL: str = "https://app.buscar.com.br"
LOGO: Path = Path("static/logo.png")
TRIPS: set[str] = {"42"}
LOGO_FRACTION: float = 0.25
QR_SCALE: int = 10
QR_BORDER: int = 4
LOGO_PADDING: int = 10


def qr_with_logo(url: str, logo: Path, *, logo_fraction: float = LOGO_FRACTION) -> bytes:
    qr = segno.make(url, error="h", micro=False)
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=QR_SCALE, border=QR_BORDER)
    image = Image.open(buffer).convert("RGB")
    side = qr.symbol_size(scale=QR_SCALE, border=0)[0]
    logo_side = int(side * logo_fraction)
    mark = Image.open(logo).convert("RGBA")
    mark.thumbnail((logo_side, logo_side), Image.Resampling.LANCZOS)
    frame = Image.new(
        "RGB",
        (mark.width + 2 * LOGO_PADDING, mark.height + 2 * LOGO_PADDING),
        "white",
    )
    frame.paste(mark, (LOGO_PADDING, LOGO_PADDING), mark)
    image.paste(
        frame,
        ((image.width - frame.width) // 2, (image.height - frame.height) // 2),
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


app = FastAPI()


@app.get("/caronas/{trip_id}/qr.png", response_class=Response)
async def trip_qr(trip_id: str) -> Response:
    if trip_id not in TRIPS:
        raise HTTPException(status_code=404)
    png = qr_with_logo(f"{PUBLIC_URL}/s/{trip_id}", LOGO)
    return Response(
        png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
```

O QR de uma carona não muda: o link é o mesmo enquanto ela existir. Por isso
o `Cache-Control` é longo. Gerar um QR com logo levou cerca de 7 ms (média de
50, link de 64 caracteres, mesma máquina da tabela), então não vale guardar
em disco, mas também não há por que repetir a cada visita.

Para impressão grande, aumente `QR_SCALE` (pixels por módulo) em vez de
redimensionar o PNG depois: ampliar a imagem pronta borra as bordas dos
módulos.

## Passo 3: o teste que impede o logo de crescer

O tamanho do logo é o tipo de número que alguém aumenta "só um pouco" para a
marca aparecer mais. Um teste que **decodifica** o QR gerado transforma
esse limite em falha de CI:

```python
import io
from pathlib import Path

import zxingcpp
from PIL import Image

from app.qr import qr_with_logo


def test_qr_with_logo_still_decodes(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (300, 300), (11, 61, 145, 255)).save(logo)
    url = "https://app.buscar.com.br/s/42"

    png = qr_with_logo(url, logo)

    decoded = zxingcpp.read_barcodes(Image.open(io.BytesIO(png)))
    assert [result.text for result in decoded] == [url]
```

Instale o leitor só no grupo de desenvolvimento:
`uv add --dev zxing-cpp`.

## Recap

- **Nível `H`** (`segno.make(url, error="h", micro=False)`) é o que permite
  cobrir parte do QR com o logo.
- **Logo com 25% do lado do QR** (6% da área), num quadro branco. Na
  medição, até 35% leu em todos os casos, e com 40% o link curto já falhou.
- **O QR aponta para `/s/{id}`**, a página de compartilhamento: quem escaneia
  chega ao app, e o mesmo link tem prévia no WhatsApp.
- **Um teste decodifica o QR** com `zxing-cpp`, para o logo não crescer além
  do que lê.
- **Antes de imprimir**, teste com câmeras de verdade.

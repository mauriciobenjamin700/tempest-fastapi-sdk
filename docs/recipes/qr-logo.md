---
description: "Use para gerar QR Code com logo e moldura compartilhável; contém quanto logo cabe com correção `H`, a moldura com foto e trajeto e as rotas de um link de carona."
---

# QR Code com logo e moldura (link de carona)

Um QR Code impresso no vidro do carro, num cartaz da rodoviária ou numa
imagem postada no status do WhatsApp leva o passageiro direto à carona. Com o
logo da marca no centro, ele também diz de quem é o link antes de alguém
apontar a câmera. E, numa **moldura** com a foto e o nome do motorista, o
trajeto e o link escrito, a imagem se explica sozinha.

O logo cobre módulos do QR. Quem compensa isso é a **correção de erro**: o
nível `H` recupera até 30% do símbolo. O `tempest_fastapi_sdk.share` gera o QR
sempre no nível `H`, cola o logo num quadro branco num tamanho que foi medido
para continuar lendo, e recusa uma fração de logo acima do teto medido.

Ao final você terá:

- `render_qr(url, logo=...)`: o PNG do QR com o logo no centro;
- `ShareFrameRenderer`: a moldura em dois formatos, vertical para o status e
  quadrado para conversa e grupo;
- `GET /api/caronas/{trip_id}/qr.png` e `GET /api/caronas/{trip_id}/moldura.png`,
  apontando para a [página de compartilhamento](share-preview.md) da carona.

## Instalação

O QR é gerado pelo [`segno`](https://pypi.org/project/segno/) (Python puro,
sem dependência) e desenhado com o Pillow. Os dois vêm no extra `[image]`:

```bash
uv add "tempest-fastapi-sdk[image]>=0.371.0"
```

A moldura escreve texto, então precisa de uma fonte TrueType na máquina, como
o card da prévia. Veja
[Fonte no Docker](share-preview.md#fonte-no-docker).

## Passo 1: o QR com o logo

```python
from pathlib import Path

from tempest_fastapi_sdk.share import render_qr

png: bytes = render_qr(
    "https://app.buscar.com.br/s/42",
    logo=Path("static/logo.png"),
)
Path("qr.png").write_bytes(png)
```

O resultado, com um logo de 400×400:

![QR Code preto e branco com um quadrado azul arredondado e a letra B amarela no centro](../assets/qr-logo.png)

O que `render_qr` faz por você:

- **Nível `H`, sempre.** Sem pedir, o `segno` 1.6.6 escolheu `L` (7% de
  recuperação) para os links desta receita, e com `L` não sobra redundância
  para o logo.
- **Nunca Micro QR.** O `segno` escolhe Micro QR para texto bem curto (para
  `"42"` ele gerou um `M1`), e leitores de celular, em geral, não suportam
  esse formato.
- **Logo com 25% do lado do QR**, dimensionado contra o QR **sem** a borda,
  mantendo a proporção, num quadro branco de um módulo. O quadro separa o
  logo dos módulos em volta.
- **Teto de 35%.** `logo_fraction` acima de `MAX_QR_LOGO_FRACTION` dá
  `ValueError`, antes de desenhar.

Os outros parâmetros: `scale` é a quantidade de pixels por módulo. Para
impressão grande, aumente `scale` em vez de ampliar o PNG pronto, porque a
ampliação borra as bordas dos módulos. `border` é a margem branca em
módulos, e 4 é o que a norma pede.

## Quanto logo cabe

Medido com `segno` 1.6.6 e `zxing-cpp` 3.1.1. Configuração: QR nível `H`,
`scale=10`, logo quadrado e opaco com quadro branco, e dois links, um de 30
caracteres (versão 4 do QR, 33×33 módulos) e um de 64 (versão 7, 45×45).
"Degradada" é a imagem reduzida e borrada, em quatro combinações (redução de
1/2 a 1/4, desfoque gaussiano de 0,6 a 1,0 px), para imitar uma foto de
câmera:

| Lado do logo (fração do QR) | Área coberta | Link de 30 car.: limpa / degradada | Link de 64 car.: limpa / degradada |
| --- | --- | --- | --- |
| 20% | 4% | lê / 4 de 4 | lê / 4 de 4 |
| 25% | 6% | lê / 4 de 4 | lê / 4 de 4 |
| 30% | 9% | lê / 4 de 4 | lê / 4 de 4 |
| 35% | 12% | lê / 4 de 4 | lê / 4 de 4 |
| 40% | 16% | falha / 0 de 4 | lê / 2 de 4 |
| 45% | 20% | falha / 0 de 4 | falha / 0 de 4 |

Por isso o padrão é `DEFAULT_QR_LOGO_FRACTION = 0.25`, três degraus abaixo
da primeira falha (40%), e o teto é `MAX_QR_LOGO_FRACTION = 0.35`, o último valor
que leu em todos os casos. A folga do padrão é para o que a medição não
reproduz: reflexo no vidro, impressão em baixa resolução, câmera de celular
antigo. O teste `tests/share/test_qr.py` decodifica o QR no padrão e no teto,
nos dois links, e confirma que 40% deixa de ler o link curto.

!!! warning "A medição é de leitor, não de câmera"
    A tabela foi medida decodificando a imagem com `zxing-cpp`, não
    apontando um celular. Antes de imprimir em lote, teste o QR final com
    duas ou três câmeras diferentes, na distância e na luz em que ele vai
    ser lido.

Duas escolhas fora do logo também pesam:

- **Tamanho do link.** No mesmo tamanho impresso, mais módulos são módulos
  menores, e a câmera precisa chegar mais perto. Prefira `/s/42` a uma URL
  com UUID e parâmetros de campanha.
- **Cores.** A tabela foi medida com módulos pretos em fundo branco, que é o
  que `render_qr` desenha. Ponha a cor da marca no logo.

## Passo 2: a moldura para compartilhar

A moldura é a imagem que o motorista posta. Ela leva o próprio caminho de
volta: o QR, para quem vê a imagem noutra tela, e o link escrito, para quem
não consegue escanear.

```python
from pathlib import Path

from tempest_fastapi_sdk.share import (
    ShareFrameLayoutEnum,
    ShareFrameRenderer,
    ShareFrameSchema,
)

renderer = ShareFrameRenderer(
    background=("#0b3d91", "#061f4a"),
    accent_color="#ffd166",
    logo=Path("static/logo.png"),
)

frame = ShareFrameSchema(
    url="https://app.buscar.com.br/s/42",
    title="Ana Souza",
    subtitle="Teresina → Fortaleza · sexta, 18h",
    brand="BusCar",
    caption="Escaneie e reserve sua vaga",
)

story: bytes = renderer.render(
    frame,
    layout=ShareFrameLayoutEnum.STORY,
    photo=Path("ana.jpg").read_bytes(),
)
square: bytes = renderer.render(
    frame,
    layout=ShareFrameLayoutEnum.SQUARE,
    photo=Path("ana.jpg").read_bytes(),
)
```

| Vertical (`STORY`, 1080×1920), para o status | Quadrado (`SQUARE`, 1080×1080), para conversa e grupo |
| --- | --- |
| ![Moldura vertical: logo e BusCar no topo, foto redonda com anel amarelo, nome, trajeto, QR Code com logo num painel branco, link e chamada](../assets/share-frame-story.png) | ![Moldura quadrada: logo e BusCar no topo, foto redonda ao lado do nome e do trajeto, QR Code com logo num painel branco, link e chamada](../assets/share-frame-square.png) |

Pedaço por pedaço:

- **`ShareFrameRenderer`** recebe os mesmos argumentos do `ShareCardRenderer`
  da prévia: cores, `logo`, `font_path`. Use as mesmas cores nos dois, e o
  card e a moldura saem com a mesma cara. Cor inválida, logo quebrado e fonte
  ausente falham **na construção**.
- **O logo aparece duas vezes**: no topo, ao lado de `brand`, e no centro do
  QR, com a mesma regra do `render_qr`.
- **`photo`** é a foto da pessoa, em `bytes`. Ela é recortada no centro, em
  círculo, com um anel na cor de destaque, e a orientação do EXIF é aplicada.
  Sem foto, o círculo leva as iniciais de `title` (`"Ana Souza"` vira `AS`).
  Foto que não abre, ou com mais de `MAX_PHOTO_PIXELS`, dá `ValueError`.
- **`show_url=True`** escreve o link sem o `https://` embaixo do QR.
- **Texto longo** diminui e depois é cortado com `…`, e nunca sai da margem.
  Quebra de linha vira espaço.

O teste `tests/share/test_frame.py` decodifica o QR **dentro** da moldura nos
dois formatos: com foto e sem foto, com e sem logo, com texto longo e com um
link de UUID.

!!! tip "Por que a foto vem do seu serviço"
    `photo` é `bytes`, não URL. Buscar no servidor uma URL que veio do
    cliente abre caminho para SSRF (o servidor fazendo requisição para onde
    o atacante quiser). Leia a foto do seu storage, onde ela já foi validada
    no upload.

## Passo 3: as rotas da carona

O QR aponta para a **página de compartilhamento** da carona (`/s/{id}`, da
receita [Compartilhar com prévia](share-preview.md)), e não para o app
direto. Quem escaneia cai no navegador, e a página leva ao app. O mesmo link,
colado no WhatsApp, mostra a prévia com o card.

```python
import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response

from tempest_fastapi_sdk.share import (
    ShareFrameLayoutEnum,
    ShareFrameRenderer,
    ShareFrameSchema,
    render_qr,
)

PUBLIC_URL: str = "https://app.buscar.com.br"
LOGO: Path = Path("static/logo.png")
TRIPS: dict[str, dict[str, str]] = {
    "42": {
        "driver": "Ana Souza",
        "route": "Teresina → Fortaleza · sexta, 18h",
        "photo": "static/ana.jpg",
    },
}

renderer = ShareFrameRenderer(
    background=("#0b3d91", "#061f4a"),
    accent_color="#ffd166",
    logo=LOGO,
)
app = FastAPI()


def png_response(png: bytes) -> Response:
    return Response(
        png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/caronas/{trip_id}/qr.png", response_class=Response)
async def trip_qr(trip_id: str) -> Response:
    if trip_id not in TRIPS:
        raise HTTPException(status_code=404)
    png = await asyncio.to_thread(render_qr, f"{PUBLIC_URL}/s/{trip_id}", logo=LOGO)
    return png_response(png)


@app.get("/api/caronas/{trip_id}/moldura.png", response_class=Response)
async def trip_frame(
    trip_id: str,
    layout: ShareFrameLayoutEnum = ShareFrameLayoutEnum.STORY,
) -> Response:
    trip = TRIPS.get(trip_id)
    if trip is None:
        raise HTTPException(status_code=404)
    frame = ShareFrameSchema(
        url=f"{PUBLIC_URL}/s/{trip_id}",
        title=trip["driver"],
        subtitle=trip["route"],
        brand="BusCar",
        caption="Escaneie e reserve sua vaga",
    )
    photo = Path(trip["photo"]).read_bytes()
    png = await asyncio.to_thread(renderer.render, frame, layout=layout, photo=photo)
    return png_response(png)
```

As rotas ficam sob `/api/` de propósito. Atrás do nginx da
[página de compartilhamento](share-preview.md#passo-5-o-link-publico-no-ar),
só `/s/` e `/api/` vão para o backend, com `^~`. Fora deles, um caminho que
termina em `.png` cai no bloco de estáticos do SPA. Medido no `nginx:alpine`
1.31.9: `/caronas/42/qr.png` deu `404`, e `/api/caronas/42/qr.png` deu
`200 image/png`.

`GET /api/caronas/42/moldura.png` devolve o vertical, e
`GET /api/caronas/42/moldura.png?layout=square` devolve o quadrado. O FastAPI
valida `layout` contra o enum e responde `422` para outro valor.

**Fora do event loop.** Desenhar a moldura levou cerca de 105 ms (média de
10, formato vertical, com foto e logo, Pillow 13.0.0, na máquina de
desenvolvimento do SDK). O QR sozinho levou cerca de 13 ms (média de 50, link
de 64 caracteres, logo lido do disco a cada chamada). Os dois vão por
`asyncio.to_thread`, e o loop continua atendendo as outras requisições.

**Cache curto.** A moldura muda quando a carona muda: horário, foto,
motorista. Uma hora de `Cache-Control` limita o tempo em que uma imagem velha
circula. Se o seu modelo tem `updated_at`, ponha `?v=<updated_at>` no link da
imagem, como a [página de compartilhamento](share-preview.md#passo-4-cache-versao-e-o-whatsapp)
faz com o card.

## Recap

- **`render_qr(url, logo=...)`** gera o QR no nível `H`, sem Micro QR, com o
  logo em 25% do lado, num quadro branco. Acima de 35% ele recusa: 40% já
  falhou na medição.
- **`ShareFrameRenderer.render(frame, layout=..., photo=...)`** desenha a
  moldura: logo e marca, foto redonda (ou as iniciais), nome, trajeto, o QR
  num painel branco, o link escrito e a chamada. `STORY` (1080×1920) é para o
  status, `SQUARE` (1080×1080) é para conversa e grupo.
- **O QR aponta para `/s/{id}`**: quem escaneia chega ao app, e o mesmo link
  tem prévia no WhatsApp.
- **A foto vem do seu storage**, em `bytes`, nunca de uma URL buscada no
  servidor.
- **Antes de imprimir**, teste com câmeras de verdade.

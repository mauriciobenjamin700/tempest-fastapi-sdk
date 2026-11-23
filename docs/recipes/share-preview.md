# Compartilhar com prévia (Open Graph)

Quando alguém cola um link no WhatsApp, no Telegram, no Instagram ou no X, a
rede busca aquela URL e monta a prévia (imagem, título e descrição) a partir
das tags **Open Graph** do HTML que voltou. O robô dessas redes **não executa
JavaScript**: um app SPA devolve um `index.html` vazio, e a prévia sai sem
nada.

Por isso quem responde o link compartilhado é o **backend**. Ele devolve, por
item, uma página com as tags prontas e a imagem da prévia. O
`tempest_fastapi_sdk.share` monta isso a partir de uma função sua, que diz o
que mostrar de cada item.

Ao final desta receita você terá:

- `GET /s/{id}`: o HTML com `og:title`, `og:description`, `og:image`,
  `og:url`, `twitter:card` e `<link rel=canonical>`, e o humano levado para a
  tela do app;
- `GET /s/{id}/og.png`: um card 1200×630 desenhado com a sua marca, com
  `ETag` e `304`;
- uma prévia genérica para o item que não existe mais, sem `500` e sem vazar
  dado.

## Instalação

A página não precisa de nada além da instalação base. O card PNG usa o
Pillow, que vem no extra `[image]`:

```bash
uv add "tempest-fastapi-sdk[image]"
```

O card também precisa de uma fonte TrueType instalada na máquina. No
Debian/Ubuntu é o pacote `fonts-dejavu-core`. Veja
[Fonte no Docker](#fonte-no-docker).

## Passo 1: a página do item

Você escreve **uma** função: recebe o id que veio na URL e devolve um
`SharePreviewSchema`, ou `None` quando o item não deve aparecer.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.share import SharePreviewSchema, make_share_router

TRIPS: dict[str, dict[str, str]] = {
    "42": {"origin": "Teresina", "destination": "Fortaleza", "when": "Sexta, 18h"},
}


async def resolve_trip(item_id: str, /) -> SharePreviewSchema | None:
    trip = TRIPS.get(item_id)
    if trip is None:
        return None
    return SharePreviewSchema(
        title=f"Carona {trip['origin']} → {trip['destination']}",
        description=f"{trip['when']} · reserve sua vaga no app",
        redirect_url=f"https://app.example.com/caronas/{item_id}",
    )


app = FastAPI()
app.include_router(
    make_share_router(
        resolve_trip,
        public_url="https://api.example.com",
        fallback=SharePreviewSchema(
            title="BusCar",
            description="Caronas entre cidades",
            redirect_url="https://app.example.com",
        ),
        site_name="BusCar",
        locale="pt_BR",
        link_text="Abrir no app",
    )
)
```

Pedaço por pedaço:

- **`resolve_trip`** é o resolver. O `/` depois do `item_id` não é enfeite: o
  router chama o resolver pela posição, então o nome do parâmetro é seu.
- **`redirect_url`** é para onde vai a **pessoa** que clica no link. A página
  leva o navegador até lá com `<meta http-equiv="refresh">` e mostra também
  um link visível (`link_text`) para quando o refresh estiver bloqueado.
- **`public_url`** é a origem pública de onde o link é servido. `og:url` e a
  URL da imagem são montados em cima dela.
- **`fallback`** é a prévia mostrada quando o resolver devolve `None`.
- **`site_name`** e **`locale`** são iguais para todo item, por isso ficam no
  router e não no schema.

Com o app no ar, `GET /s/42` devolve:

```html
<title>Carona Teresina → Fortaleza</title>
<link rel="canonical" href="https://api.example.com/s/42">
<meta property="og:type" content="website">
<meta property="og:title" content="Carona Teresina → Fortaleza">
<meta property="og:description" content="Sexta, 18h · reserve sua vaga no app">
<meta property="og:url" content="https://api.example.com/s/42">
<meta property="og:site_name" content="BusCar">
<meta property="og:locale" content="pt_BR">
<meta name="twitter:card" content="summary">
```

As duas rotas respondem `GET` e `HEAD`, e ficam **fora do OpenAPI** por padrão. São páginas públicas, não API.
Passe `include_in_schema=True` se quiser vê-las no `/docs`.

!!! warning "Por que `public_url` é obrigatório"
    O router não monta URL a partir de `request.base_url`. Atrás do nginx,
    esse valor é o endereço interno (`http://backend:8000`), e o cabeçalho
    `Host` é o cliente quem escolhe. `og:image` precisa ser absoluta e
    pública. O WhatsApp não resolve caminho relativo e descarta a imagem sem
    avisar.

### O que é escapado, e o que é recusado

Todo valor que entra no HTML passa por `html.escape(..., quote=True)`: título,
descrição, texto alternativo, `site_name`, `locale`, `link_text` e as URLs. O
nome de um item com `"><script>` vira texto, não código.

Escapar não basta para URL: `javascript:alert(1)` continua executável dentro
de `href` mesmo escapada. Por isso `redirect_url`, `canonical_url` e
`image_url` só aceitam URL **absoluta** `http`/`https`. O schema recusa o
resto (`javascript:`, `data:`, `/caminho`, `//host`) com `ValidationError`,
antes de existir qualquer HTML.

## Passo 2: o item indisponível

Quando o resolver devolve `None`, a página **continua respondendo `200`**, com
o texto do `fallback`. `og:url` continua sendo o link compartilhado. O link que
alguém mandou antes de a carona ser cancelada abre a prévia genérica: nem
quebra, nem conta o que a carona era.

Devolva `None` para tudo que não deve aparecer: item apagado, privado,
esgotado ou de outro tenant. Exceção no resolver **não** vira fallback: ela
sobe e vira `500`. Banco fora do ar é falha, não "item indisponível", e
engolir isso esconde o problema.

## Passo 3: o card PNG

Passe um `ShareCardRenderer` e preencha `card` na prévia:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.share import (
    ShareCardLineSchema,
    ShareCardRenderer,
    ShareCardSchema,
    SharePreviewSchema,
    make_share_router,
)

TRIPS: dict[str, dict[str, str]] = {
    "42": {
        "origin": "Teresina",
        "destination": "Fortaleza",
        "when": "Sexta, 21/11 às 18h",
        "stops": "Piripiri · Sobral",
        "price": "R$ 80 por vaga",
        "seats": "3 vagas",
        "driver": "Ana Souza (4,9)",
        "updated_at": "2026-11-20T14:03:00+00:00",
    },
}


async def resolve_trip(item_id: str, /) -> SharePreviewSchema | None:
    trip = TRIPS.get(item_id)
    if trip is None:
        return None
    route = f"{trip['origin']} → {trip['destination']}"
    return SharePreviewSchema(
        title=f"Carona {route}",
        description=f"{trip['when']} · {trip['price']}",
        redirect_url=f"https://app.example.com/caronas/{item_id}",
        version=trip["updated_at"],
        card=ShareCardSchema(
            brand="BusCar",
            kicker="Carona disponível",
            title=route,
            lines=[
                ShareCardLineSchema(icon="→", text=f"Paradas: {trip['stops']}"),
                ShareCardLineSchema(icon="★", text=trip["when"]),
            ],
            chips=[trip["price"], trip["seats"]],
            footer=f"Motorista: {trip['driver']}",
        ),
    )


renderer = ShareCardRenderer(
    background=("#0b3d91", "#061f4a"),
    accent_color="#ffd166",
)

app = FastAPI()
app.include_router(
    make_share_router(
        resolve_trip,
        public_url="https://api.example.com",
        fallback=SharePreviewSchema(
            title="BusCar",
            description="Caronas entre cidades",
            redirect_url="https://app.example.com",
        ),
        renderer=renderer,
        site_name="BusCar",
        locale="pt_BR",
    )
)
```

Agora a página ganha `og:image` (com `og:image:width` 1200 e
`og:image:height` 630) e `twitter:card` vira `summary_large_image`.
`GET /s/42/og.png` devolve isto:

![Card de compartilhamento 1200x630 com marca, rota, paradas, horário, etiquetas de preço e vagas e o nome do motorista](../assets/share-card.png)

O layout, de cima para baixo:

| Campo | Onde | Quando não cabe |
| --- | --- | --- |
| `brand` | topo, cor de destaque | a fonte diminui, depois o texto é cortado com `…` |
| `kicker` | abaixo da marca, cor suave | a fonte diminui, depois o texto é cortado |
| `title` | negrito, de 72 px a 48 px | a fonte diminui, quebra em duas linhas, depois corta |
| `lines` | ícone na cor de destaque + texto | a linha que não cabe acima das etiquetas é descartada |
| `chips` | etiquetas arredondadas | a etiqueta que passaria da margem é descartada |
| `footer` | rodapé, cor suave | a fonte diminui, depois o texto é cortado |

Todo texto do card fica numa linha só: quebra de linha e espaço repetido viram
um espaço (`"Sai\nsexta"` vira `"Sai sexta"`), e etiqueta vazia é descartada.
Uma descrição digitada com Enter não derruba a imagem.

Nenhum texto sai da margem. O teste
`tests/share/test_card.py::TestRender::test_long_text_stays_inside_margins`
fixa isso com 80 palavras em todos os campos.

O renderer aceita qualquer cor que o Pillow entenda (`"#0b3d91"`,
`"rgb(11, 61, 145)"`, `"navy"`) e um `logo` (`bytes` ou `Path`, PNG com
transparência), desenhado no canto superior direito com 72 px de altura e no
máximo 352 px de largura (um logo mais largo é reduzido até caber). Cor
inválida, logo que não é imagem e fonte ausente dão erro **na construção**, no
boot do serviço, e não no primeiro compartilhamento.

!!! tip "O ícone é um caractere da fonte"
    `icon` é desenhado com a mesma fonte do card. `→`, `★`, `✓` e `·` existem
    no DejaVu. Emoji colorido não existe, e sai como quadradinho.

### Fora do event loop

Desenhar o card é trabalho de CPU: cerca de 49 ms por card, média de 20
renders seguidos de um card com todos os campos, no Pillow 13.0.0, na máquina
de desenvolvimento do SDK (WSL2). O router chama `renderer.render` via `asyncio.to_thread`, e o loop continua
atendendo as outras requisições enquanto isso. O teste
`tests/share/test_router.py::TestEventLoop` confirma que o render roda em
outra thread, e falha se o `to_thread` virar chamada direta.

## Passo 4: cache, versão e o WhatsApp

Duas camadas de cache estão em jogo, e cada uma pede uma coisa.

**O `ETag` evita redesenhar.** `og.png` responde com `ETag` fraco e
`Cache-Control: public, max-age=600` (mude com `cache_control=`). Quando o
cliente revalida com `If-None-Match` e a tag bate, a resposta é `304` **sem
desenhar nada**: custa uma chamada ao resolver e nenhuma renderização. A
comparação segue a RFC 9110 e aceita `W/"..."`, lista de tags e `*`.

A tag é derivada de:

- `version` da prévia (o seu `updated_at`), ou, sem `version`, do conteúdo do
  `card`;
- a impressão digital do renderer (cores, fontes e logo): trocar a cor da
  marca invalida todos os cards;
- o id do item.

A tag é **fraca** porque duas réplicas com FreeType diferente podem gerar
bytes diferentes para o mesmo card. Ela garante "mesmo conteúdo", não "mesmos
bytes".

**O `?v=` faz a rede buscar a imagem nova.** O robô guarda o que buscou, e a
chave desse cache é a URL. Por quanto tempo ele guarda, e se revalida, é
decisão de cada rede, fora do seu controle. Se a URL fosse sempre
`/s/42/og.png`, a carona editada poderia continuar mostrando a prévia velha,
com `304` correto ou não. Por isso a página publica a imagem como
`/s/42/og.png?v=<hash da versão>`. Mudou o `version`, mudou a URL, e para a
rede é outra imagem.

!!! warning "Preencha `version`"
    Sem `version`, o hash sai do conteúdo do card. Isso funciona, mas o
    resolver monta o card inteiro em toda revalidação. Com `updated_at` em
    `version`, o resolver pode responder rápido, e o hash muda exatamente
    quando o item muda.

## Passo 5: o link público no ar

O link que o motorista compartilha é `https://<domínio>/s/42`. Duas formas de
fazer esse caminho chegar ao backend:

**nginx na frente do app e da API.** Encaminhe o prefixo para o backend e
deixe o resto com o SPA:

```nginx
location /s/ {
    proxy_pass http://backend:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location / {
    root /usr/share/nginx/html;
    try_files $uri /index.html;
}
```

Nesse caso, `public_url` é o domínio do app (`https://app.example.com`),
porque é ele que aparece no link.

**O backend serve o SPA.** Com
[`make_spa_router`](react-spa.md), inclua o router de compartilhamento
**antes** do SPA. O FastAPI casa as rotas na ordem de registro, então
`/s/{id}` casa primeiro. Na ordem inversa, o fallback do SPA responde
`/s/42` com o `index.html`, e a prévia sai vazia.

Para conferir o que o robô vê, faça a mesma requisição que ele faz:

```bash
curl -s https://app.example.com/s/42 | grep 'property="og:'
curl -sI https://app.example.com/s/42/og.png
```

Para forçar uma rede a reler um link já compartilhado, use a ferramenta dela.
No Facebook e no Instagram é o
[Sharing Debugger](https://developers.facebook.com/tools/debug/), com
"Scrape Again".

## Fonte no Docker

Uma imagem `python:3.12-slim` não tem fonte TrueType nenhuma. Sem fonte, o
`ShareCardRenderer()` **não sobe**:

```text
FileNotFoundError: no TrueType font found for the share card. Install one
(`apt-get install fonts-dejavu-core` on Debian/Ubuntu) or pass font_path= to
the renderer.
```

O SDK não cai na fonte embutida do Pillow de propósito. Medido no Pillow
13.0.0, ela desenha `ã` e `→` com a mesma caixa do caractere inexistente
(`U+F0000`). A prévia em português sairia cheia de quadradinhos, sem erro
nenhum.

Duas saídas:

- instale a fonte na imagem. O `tempest generate --dockerfile` já gera esta
  linha quando o projeto fixa o extra `[image]`:

    ```dockerfile
    RUN apt-get update \
        && apt-get install -y --no-install-recommends fonts-dejavu-core \
        && rm -rf /var/lib/apt/lists/*
    ```

- ou passe o arquivo: `ShareCardRenderer(font_path=Path("fonts/Inter.ttf"),
  bold_font_path=Path("fonts/Inter-Bold.ttf"))`. Caminho explícito que não
  existe é erro. O renderer não procura outra fonte no lugar, porque um erro
  de digitação ali faria produção desenhar com uma fonte diferente da que foi
  revisada.

Sem `font_path`, o renderer procura nesta ordem: DejaVu do Debian/Ubuntu, do
Fedora e do Arch, depois Liberation. A lista está em
`DEFAULT_FONT_CANDIDATES`.

## Só a página, sem Pillow

Tem a imagem pronta num CDN? Não passe `renderer` e preencha `image_url`:

```python
from tempest_fastapi_sdk.share import SharePreviewSchema


async def resolve_product(item_id: str, /) -> SharePreviewSchema | None:
    return SharePreviewSchema(
        title="Tênis de corrida",
        description="R$ 299,90 · frete grátis",
        redirect_url=f"https://loja.example.com/p/{item_id}",
        image_url=f"https://cdn.example.com/produtos/{item_id}.jpg",
    )
```

Sem `renderer`, a rota `og.png` nem é registrada, e a instalação base basta.
`image_url` também vale com renderer: quando preenchida, ela vence o card
gerado.

## Recap

- **`make_share_router(resolver, public_url=..., fallback=...)`** serve
  `GET /s/{id}` com as tags Open Graph e Twitter, todas escapadas, e o humano
  redirecionado para `redirect_url`.
- **`SharePreviewSchema`** recusa URL que não seja absoluta `http`/`https`.
  Escapar não neutraliza `javascript:`, e recusar neutraliza.
- **O resolver devolve `None`** para item indisponível. A resposta é `200`
  com o `fallback`, sem vazar dado. Exceção sobe como `500`.
- **`ShareCardRenderer`** desenha o card 1200×630 com a sua marca. Texto
  longo diminui, quebra ou é cortado, e nunca sai da margem. Sem fonte, ele
  falha no boot em vez de desenhar quadradinhos.
- **`version`** alimenta o `ETag` (o `304` sem desenho) e o `?v=` da imagem
  (a URL nova que a rede busca de novo).
- **`public_url`** vem da configuração, nunca do `Host` da requisição.

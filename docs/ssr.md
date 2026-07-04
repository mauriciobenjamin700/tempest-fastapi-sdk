# SSR: páginas tipadas em Python renderizadas para HTML

Seu serviço FastAPI, full-stack, **tipado**, sem linguagem de template. 🚀

A camada de SSR (Server-Side Rendering) do SDK deixa você descrever
páginas como **componentes Python tipados** e devolvê-las de uma rota
já renderizadas em HTML. Nada de Jinja, nada de strings soltas: o mesmo
verificador de tipos que cobre seus schemas e services cobre também a
sua interface.

!!! info "O que você vai precisar"
    A camada de SSR mora no extra opcional `[ssr]`, que traz o
    renderizador [`tempestweb`](https://pypi.org/project/tempestweb/)
    (e, transitivamente, o `tempest-core` com os widgets tipados).

    ```bash
    pip install "tempest-fastapi-sdk[ssr]"
    ```

## Por que isso existe

Numa API tradicional você devolve JSON e um front-end separado desenha a
tela. Quando você só precisa de páginas server-driven (um painel interno,
um fluxo de onboarding, uma landing), montar um SPA inteiro é peso morto.

A alternativa clássica — um mecanismo de templates — te tira do mundo
tipado: o template é uma string, o editor não te ajuda, e um campo
renomeado no schema só quebra em produção.

A camada de SSR resolve isso mantendo **tudo em Python tipado**:

- Você declara a página como uma classe (`Page`) com campos tipados.
- Você monta o corpo com widgets (`Column`, `Row`, `Text`, `Button`, ...).
- Você devolve `html_response(...)` da rota — e recebe um `HTMLResponse`.

## Exemplo mínimo completo

Este é um programa completo e executável. Salve como `main.py`, instale o
extra e rode com `uvicorn main:app`.

```python
from tempest_core import Column, Text, Widget
from fastapi import FastAPI

from tempest_fastapi_sdk.ssr import Page, html_response

app: FastAPI = FastAPI()


class HomePage(Page):
    """A página inicial, com um único campo tipado."""

    user: str

    def body(self) -> Widget:
        """O conteúdo principal da página."""
        return Column(
            tag="main",
            children=[
                Text(content=f"Olá, {self.user}!", tag="h1"),
                Text(content="Bem-vindo ao seu app tipado.", tag="p"),
            ],
        )


@app.get("/")
def home() -> object:
    """Renderiza a HomePage como um documento HTML completo."""
    return html_response(HomePage(title="Início", user="Ana"), title="Início")
```

Acesse `http://127.0.0.1:8000/` e você recebe um documento HTML5 completo
com `<!doctype html>`, `<title>Início</title>` e o corpo `<main>` — tudo
gerado a partir das classes acima.

## Explicando peça por peça

### A classe `Page`

```python
class HomePage(Page):
    user: str

    def body(self) -> Widget:
        return Column(tag="main", children=[Text(content=f"Olá, {self.user}!")])
```

`Page` é um **componente `tempest_core`** (um modelo Pydantic). Isso
significa que os dados da página são **campos tipados** — aqui, `user: str`.
O campo herdado `title: str` alimenta o `<title>` do documento.

Você implementa **`body()`**, que devolve a árvore de widgets do conteúdo
principal. É o único método obrigatório.

!!! tip "Tags semânticas"
    Todo widget aceita `tag=` e `attrs=`. Use `tag="main"`, `tag="h1"`,
    `tag="nav"` para emitir HTML semântico em vez das tags neutras
    padrão (`<div>` / `<span>`).

### A função `html_response`

```python
return html_response(HomePage(title="Início", user="Ana"), title="Início")
```

`html_response` renderiza a árvore e devolve um `HTMLResponse` do FastAPI.
Sua assinatura:

```python
def html_response(
    widget: Widget,
    *,
    title: str | None = None,
    status_code: int = 200,
    htmx: bool = False,
    document: bool = True,
    lang: str = "pt-BR",
) -> HTMLResponse: ...
```

- **`document=True`** (padrão) → documento HTML5 completo. Requer `title`
  (levanta `ValueError` se `title is None`).
- **`document=False`** → fragmento HTML puro (sem `<!doctype>`), ideal
  para trocas parciais com HTMX.
- **`status_code`** → repassado ao `HTMLResponse`.
- **`htmx=True`** → injeta o `<script>` do HTMX **servido localmente**
  (nunca de uma CDN — veja abaixo).

!!! warning "`title` é obrigatório para documentos"
    Chamar `html_response(page)` com `document=True` (o padrão) e sem
    `title` levanta `ValueError`. Para fragmentos (`document=False`), o
    `title` é ignorado.

## Layout compartilhado com `shell()`

Toda página costuma dividir o mesmo cabeçalho, navegação e rodapé.
Em vez de repetir, sobrescreva **`shell()`** numa página base e herde por
herança normal de Python.

```python
from tempest_core import Column, Row, Text, Widget

from tempest_fastapi_sdk.ssr import Page, html_response


class BasePage(Page):
    """Layout compartilhado: barra de navegação + área principal."""

    def shell(self, body: Widget) -> Widget:
        return Column(
            tag="body",
            children=[
                Row(tag="nav", children=[Text(content="MeuApp")]),
                Column(tag="main", children=[body]),
            ],
        )


class DashboardPage(BasePage):
    """Herda a navegação, define só o próprio corpo."""

    def body(self) -> Widget:
        return Text(content="Painel", tag="h2")


class ReportsPage(BasePage):
    """Mesma navegação, corpo diferente."""

    def body(self) -> Widget:
        return Text(content="Relatórios", tag="h2")
```

`render()` (o gancho do componente) já compõe `shell(body())` para você —
**não sobrescreva `render()`**; sobrescreva `body()` e, se quiser,
`shell()`.

!!! note "Como a composição funciona"
    `Page.render()` devolve `self.shell(self.body())`. O renderizador
    expande componentes recursivamente, então uma página é só mais um
    widget na árvore.

## HTMX servido localmente (sem CDN)

Para interatividade server-driven sem escrever JavaScript, o SDK embute
o HTMX 2.x **dentro do pacote** e o serve a partir do seu próprio app —
CSP-friendly e offline. Nada de CDN.

Monte o router e ligue o `htmx=True`:

```python
from tempest_fastapi_sdk.ssr import make_htmx_router

app.include_router(make_htmx_router())  # serve GET /_ssr/htmx.js
```

Quando você chama `html_response(page, title=..., htmx=True)`, o
documento gerado aponta para `/_ssr/htmx.js` (o mesmo caminho servido
pelo router), nunca para `https://unpkg.com/...`.

### Receita: contador server-driven com HTMX

Um botão que incrementa um contador no servidor e troca só um fragmento —
sem escrever JavaScript. Programa completo:

```python
from tempest_core import Button, Column, Text, Widget
from fastapi import FastAPI

from tempest_fastapi_sdk.ssr import Page, html_response, make_htmx_router

app: FastAPI = FastAPI()
app.include_router(make_htmx_router())

_count: int = 0


class CounterFragment(Page):
    """O fragmento trocado a cada clique."""

    value: int

    def body(self) -> Widget:
        return Column(
            attrs={"id": "counter"},
            children=[
                Text(content=f"Total: {self.value}", tag="p"),
                Button(
                    label="Incrementar",
                    attrs={
                        "hx-post": "/increment",
                        "hx-target": "#counter",
                        "hx-swap": "outerHTML",
                    },
                ),
            ],
        )


class CounterPage(Page):
    """A página completa que carrega o HTMX e mostra o contador."""

    value: int

    def body(self) -> Widget:
        return CounterFragment(title="", value=self.value)


@app.get("/")
def index() -> object:
    """Documento completo, com o HTMX local carregado (htmx=True)."""
    return html_response(
        CounterPage(title="Contador", value=_count), title="Contador", htmx=True
    )


@app.post("/increment")
def increment() -> object:
    """Incrementa e devolve só o fragmento (document=False)."""
    global _count
    _count += 1
    return html_response(CounterFragment(title="", value=_count), document=False)
```

Como funciona:

1. `GET /` devolve o **documento completo** (`document=True`, `htmx=True`),
   então o HTMX local é carregado.
2. O botão tem `hx-post="/increment"` e `hx-swap="outerHTML"`.
3. `POST /increment` devolve só o **fragmento** (`document=False`), e o
   HTMX troca o `<div id="counter">` no lugar.

!!! check "Segurança por padrão"
    Todo texto é escapado na renderização. Um `Text(content="<script>")`
    vira `&lt;script&gt;` no HTML final — sem injeção acidental.

## Recap

- **`Page`** — componente tipado; declare campos, implemente `body()`,
  opcionalmente sobrescreva `shell()` para layout compartilhado. Não
  sobrescreva `render()`.
- **`html_response(widget, *, title, status_code, htmx, document, lang)`** —
  renderiza e devolve um `HTMLResponse`. `document=True` exige `title`;
  `document=False` devolve um fragmento para trocas HTMX.
- **`make_htmx_router(prefix="/_ssr")`** — serve o HTMX embutido
  localmente em `GET /_ssr/htmx.js`; combine com `htmx=True`.
- Tudo mora no extra `[ssr]` (`pip install "tempest-fastapi-sdk[ssr]"`),
  carregado sob demanda — `import tempest_fastapi_sdk` nunca exige o extra.

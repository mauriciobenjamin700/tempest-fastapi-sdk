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

## Catálogo de widgets

Os widgets vêm do `tempest_core`. Para SSR você usa um punhado deles como
blocos de montagem; **todos** aceitam `tag=`, `attrs=`, `style=` e `key=`.

| Widget | Renderiza | Uso |
|--------|-----------|-----|
| `Text(content=...)` | `<span>` (ou a `tag`) com o texto **escapado** | Qualquer texto: título, parágrafo, `<option>`, `<label>` |
| `Column(children=[...])` | `<div>` com `display:flex; flex-direction:column` | Empilhar verticalmente |
| `Row(children=[...])` | `<div>` com `display:flex` (linha) | Alinhar horizontalmente |
| `Container(child=...)` | `<div>` **neutro** (sem flex), um filho | Wrapper semântico (`tag="section"`, `tag="article"`) |
| `Button(label=...)` | `<button>` estilizado | Ações (via `attrs` HTMX — veja abaixo) |
| `Spacer()` | espaço flexível | Empurrar itens numa `Row`/`Column` |

```python
from tempest_core import Column, Container, Row, Spacer, Text

Container(
    tag="section",
    child=Row(
        children=[
            Text(content="Título", tag="h2"),
            Spacer(),
            Text(content="v1.0", tag="small"),
        ],
    ),
)
# <section><div style="display: flex"><h2>Título</h2>…<small>v1.0</small></div></section>
```

!!! warning "`Button.on_click` é ignorado no SSR"
    `on_click` é um handler de runtime (WASM/server), **não** roda em HTML
    estático. Para interatividade no SSR, use `attrs` com HTMX
    (`hx-post`, `hx-get`, …) — veja [HTMX](#htmx-servido-localmente-sem-cdn).

!!! tip "`tag` + `attrs` são o escape hatch universal"
    Não existe widget dedicado para cada tag HTML — e nem precisa. Qualquer
    elemento sai de um widget de container com `tag=` e `attrs=`:
    `Text(content="", tag="input", attrs={"name": "email", "type": "email"})`
    vira `<input name="email" type="email" />`.

## Estilização tipada com `Style`

Em vez de CSS solto, cada widget aceita um `Style` tipado que o
renderizador converte em CSS inline. Espaçamentos usam `Edge`.

```python
from tempest_core import Column, Style, Text
from tempest_core.style import Edge

Column(
    style=Style(gap=12.0, padding=Edge.all(16)),
    children=[Text(content="Card", tag="h3")],
)
# <div style="display: flex; flex-direction: column; gap: 12px; padding: 16px 16px 16px 16px">…
```

- `Edge.all(16)` / `Edge.symmetric(vertical=8, horizontal=16)` /
  `Edge.only(top=4)` — margens e paddings tipados.
- `gap`, `padding`, `margin`, cores e tipografia saem no `style=""` inline.
- A conversão `Style → CSS` é **byte-idêntica** entre o renderizador
  Python (SSR) e o cliente JS (WASM/server) — a mesma tela nos dois lados.

!!! info "Prefira classes/`attrs` para folhas de estilo externas"
    Para CSS de verdade (folhas externas, media queries), adicione
    `attrs={"class": "card"}` e sirva seu `.css` como estático. O `Style`
    inline é ótimo para layout local e componentes autocontidos.

## Componentes reutilizáveis

`Page` é um `Component`. Você pode extrair **qualquer** subárvore num
`Component` tipado e reusar — a página fica declarativa e testável em
pedaços.

```python
from tempest_core import Column, Text, Widget
from tempest_core.widgets import Component

from tempest_fastapi_sdk.ssr import Page, html_response


class Card(Component):
    """Um cartão reutilizável com título + corpo."""

    heading: str
    body_text: str

    def render(self) -> Widget:
        return Column(
            tag="section",
            attrs={"class": "card"},
            children=[
                Text(content=self.heading, tag="h3"),
                Text(content=self.body_text, tag="p"),
            ],
        )


class HomePage(Page):
    def body(self) -> Widget:
        return Column(
            tag="main",
            children=[
                Card(heading="Vendas", body_text="R$ 12.400 hoje"),
                Card(heading="Usuários", body_text="312 ativos"),
            ],
        )
```

Num `Component` você sobrescreve **`render()`** (não `body()`/`shell()` —
esses são só do `Page`). O renderizador expande cada `Component` pela sua
`render()`, recursivamente.

## Formulários e inputs

Não há widget de formulário dedicado — você compõe com `tag`/`attrs` e
recebe o POST com o `Form` do FastAPI, como em qualquer rota.

```python
from tempest_core import Button, Column, Text, Widget
from fastapi import FastAPI, Form

from tempest_fastapi_sdk.ssr import Page, html_response

app: FastAPI = FastAPI()


class SignupPage(Page):
    def body(self) -> Widget:
        return Column(
            tag="form",
            attrs={"method": "post", "action": "/signup"},
            children=[
                Text(content="", tag="input",
                     attrs={"name": "email", "type": "email", "required": "required"}),
                Text(content="", tag="input",
                     attrs={"name": "password", "type": "password", "required": "required"}),
                Button(label="Criar conta", attrs={"type": "submit"}),
            ],
        )


@app.get("/signup")
def signup_form() -> object:
    return html_response(SignupPage(title="Cadastro"), title="Cadastro")


@app.post("/signup")
def signup(email: str = Form(...), password: str = Form(...)) -> object:
    # ... crie o usuário via um Service/Repository do SDK ...
    return html_response(
        Text(content=f"Conta criada para {email}", tag="p"), document=False
    )
```

Um `<select>` sai da mesma forma: uma `Column(tag="select", ...)` com
`Text(tag="option", attrs={"value": ...})` como filhos.

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
    vira `&lt;script&gt;` no HTML final — sem injeção acidental. Isso vale
    para `content` e para os valores em `attrs` — nunca monte HTML por
    concatenação de string; deixe os widgets escaparem.

### Padrões de HTMX que você vai repetir

O HTMX lê atributos `hx-*` do HTML e faz o AJAX pra você. Os que mais
aparecem em páginas SSR:

| Atributo | O que faz |
|----------|-----------|
| `hx-get` / `hx-post` / `hx-put` / `hx-delete` | Dispara a request no método indicado |
| `hx-target` | Seletor CSS do elemento que recebe a resposta (`#id`, `closest li`) |
| `hx-swap` | Como aplicar: `outerHTML`, `innerHTML`, `beforeend` (append), `delete` |
| `hx-trigger` | O que dispara: `click` (padrão), `submit`, `keyup changed delay:300ms` |
| `hx-confirm` | Mostra um `confirm()` antes de enviar |
| `hx-indicator` | Seletor de um spinner mostrado durante a request |
| `hx-on::after-request` | JS inline num evento HTMX (ex.: `this.reset()` após enviar) |

A regra de ouro: a rota devolve **um fragmento** (`document=False`) e o
HTMX o encaixa via `hx-target` + `hx-swap`. Um fragmento vazio
(`Text(content="", tag="span")`) com `hx-swap="outerHTML"` **remove** o
elemento — é assim que "excluir" funciona.

!!! tip "Append numa lista"
    `hx-target="#lista"` + `hx-swap="beforeend"` num `<form>` faz cada
    submit **acrescentar** o novo `<li>` devolvido, sem recarregar o resto.

## Testando páginas SSR

Uma página SSR é só uma rota que devolve HTML — teste com o `TestClient`
e verifique os pedaços que importam. Rápido e sem browser:

```python
from fastapi.testclient import TestClient

from main import app


def test_home_renders() -> None:
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text.lower()
    assert "<title>Início</title>" in response.text
    assert "Olá, Ana!" in response.text


def test_increment_returns_fragment() -> None:
    with TestClient(app) as client:
        fragment = client.post("/increment")
    # Fragmento: sem <!doctype>, só o pedaço trocado.
    assert "<!doctype" not in fragment.text.lower()
    assert 'id="counter"' in fragment.text
```

!!! tip "Renderizar sem HTTP"
    Para um teste de unidade puro, chame o renderizador diretamente:
    `from tempestweb.html import render_to_html; html = render_to_html(MyPage(title="x").render())`.

## Qual abordagem usar

O SDK cobre o espectro inteiro de "HTML no servidor" até "SPA no browser".
Escolha pelo cenário:

| Você quer… | Use | Custo |
|------------|-----|-------|
| Página server-rendered, SEO, pouca interação | **SSR** (`Page` + `html_response`) | Nenhum build; HTML a cada request |
| Interação sem SPA nem JavaScript escrito | **SSR + HTMX** (`make_htmx_router`) | Nenhum build; trocas parciais |
| App rico que roda offline no browser | **SPA WASM** (`make_web_app_router`) | `tempestweb build --mode wasm` |
| UI reativa dirigida pelo servidor, boot instantâneo | **Server-mode** (`build_web_app`) | `tempestweb build --mode server` |

Os três últimos são projetos completos e rodáveis em
[Fullstack web](fullstack-web.md). Para o **frontend chamando o backend
do SDK** (HTTP tipado, idempotência, retry), veja a receita
[Frontend tempestweb + backend SDK](recipes/tempestweb-frontend.md).

## Servir um build compilado do tempestweb

As seções acima renderizam páginas **a cada request**. Se em vez disso
você compilou um frontend com o `tempestweb build`, o SDK hospeda o
artefato pronto — só serve o `dist/`, não builda (isso fica no CLI/CI do
tempestweb). São dois artefatos, cada um com a forma que combina:

| Artefato | O que é | Como servir |
|----------|---------|-------------|
| `dist/wasm` | SPA **estática** (Pyodide roda no browser: `index.html` + `bootstrap.js` + wasm + service worker) | `make_web_app_router` → `APIRouter` |
| `dist/server` | App **vivo** sobre WebSocket/SSE (engine server do tempestweb) | `build_web_app` → `FastAPI` (sub-app pra montar) |

`detect_build_mode(dir)` diz qual é (`"wasm"` ou `"server"`).

### SPA estática (`make_web_app_router`)

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.ssr import make_web_app_router

app = FastAPI()

# ... inclua PRIMEIRO os seus routers de API ...
# app.include_router(api_router)

# ... e o do frontend POR ÚLTIMO, pra as rotas específicas vencerem:
app.include_router(make_web_app_router("dist/wasm"))
```

O router serve cada arquivo do build e, pra qualquer caminho não
encontrado, cai no `index.html` (history fallback do SPA — refresh no
meio de uma rota client-side funciona).

!!! warning "Inclua por último e na raiz"
    A rota é um catch-all (`/{resource:path}`). O FastAPI casa na ordem de
    registro, então inclua o router do frontend **depois** dos seus
    routers de API — assim `/api/...` vence o fallback. O artefato wasm
    referencia `/sw.js` na raiz do site, então monte na raiz da app.

!!! tip "Transparente, sem mágica"
    - `index.html` e `sw.js` saem sempre com `Cache-Control: no-cache`
      (um redeploy é visto na hora); os demais assets usam
      `asset_cache_control` (padrão `public, max-age=3600`).
    - MIME correto pros arquivos que o `mimetypes` não conhece
      (`.wasm` → `application/wasm`, `.mjs`/`.js` → `text/javascript`,
      `.webmanifest`).
    - `sw.js` ganha `Service-Worker-Allowed: /` pra reivindicar o escopo
      da origem inteira.
    - **Nenhum CSP é imposto** — é código first-party e o Pyodide precisa
      de `wasm-unsafe-eval`; passe `security_headers=` pra adicionar o seu.
    - Traversal de caminho (`../`) é bloqueado.

### App server-mode (`build_web_app`)

O artefato server é um app **vivo** (rotas `/ws` + `/sse`), então é um
sub-app que você monta, não um router:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.ssr import build_web_app

app = FastAPI()
# ... seus routers de API ...

# monta o app do tempestweb (WebSocket/SSE + shell + /static) na raiz:
app.mount("/", build_web_app("dist/server"))
```

`build_web_app` carrega o `app.py` do artefato (contrato `make_state` +
`view`), monta o engine server do tempestweb via
`tempestweb.server.create_app`, serve o cliente em `/static` e o shell em
`/` — a mesma fiação que o `server.py` gerado faz, in-process. Dá pra
rodar direto com uvicorn também.

## Recap

- **`Page`** — componente tipado; declare campos, implemente `body()`,
  opcionalmente sobrescreva `shell()` para layout compartilhado. Não
  sobrescreva `render()`.
- **`html_response(widget, *, title, status_code, htmx, document, lang)`** —
  renderiza e devolve um `HTMLResponse`. `document=True` exige `title`;
  `document=False` devolve um fragmento para trocas HTMX.
- **`make_htmx_router(prefix="/_ssr")`** — serve o HTMX embutido
  localmente em `GET /_ssr/htmx.js`; combine com `htmx=True`.
- **`make_web_app_router(dir)`** — serve um build **wasm** (SPA estática)
  com history fallback; inclua por último. **`build_web_app(dir)`** —
  hospeda um build **server** (WebSocket/SSE) como sub-app pra montar.
  **`detect_build_mode(dir)`** distingue os dois.
- Tudo mora no extra `[ssr]` (`pip install "tempest-fastapi-sdk[ssr]"`),
  carregado sob demanda — `import tempest_fastapi_sdk` nunca exige o extra.

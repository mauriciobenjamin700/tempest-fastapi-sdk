# Ações SSR: confirmação, flash e redirect-back

Todo painel SSR repete o mesmo fluxo em cada ação:

1. a pessoa clica em **Remover** e o navegador pergunta "Remover o bucket
   fotos?";
2. o `POST` roda; deu certo → `303` para a tela com "Bucket removido.";
3. deu errado por regra de negócio → `303` **de volta para a tela de onde
   veio**, com "O bucket ainda tem objetos.";
4. a mesma exceção, chamada pela API, continua respondendo o JSON de
   sempre.

Cada passo tem uma armadilha de segurança: script inline para a pergunta
(e um nome com aspas vira código), texto do aviso na URL (e um link
forjado põe palavras na tela), `Referer` seguido às cegas (e o painel vira
open redirect). Esta receita mostra as peças do SDK que já resolvem as
três, para você não reescrever nenhuma.

!!! tip "Quando usar esta receita"
    - Seu serviço serve **HTML** com a [camada UI](ui.md) e tem
      formulários que mudam estado.
    - Você quer confirmar ação destrutiva sem escrever JavaScript.
    - Você quer "ação falhou → volta para a tela com aviso" sem montar
      cookie, `Referer` e exception handler na mão.

## O exemplo completo

Um painel de buckets: listar, e remover só o bucket vazio.

```python
from collections.abc import Sequence
from typing import ClassVar

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from tempest_core import Text, Widget

from tempest_fastapi_sdk import ConflictException, register_exception_handlers
from tempest_fastapi_sdk.ssr import (
    FlashMiddleware,
    flash,
    get_flashes,
    html_response,
    make_htmx_router,
    redirect_back,
    register_html_error_handlers,
)
from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.components import Card, FlashMessage, FlashMessages
from tempest_fastapi_sdk.ui.css import make_css_router
from tempest_fastapi_sdk.ui.forms import form_for
from tempest_fastapi_sdk.ui.layout import Shell
from tempest_fastapi_sdk.ui.pages import ErrorPage, Page

BUCKETS: dict[str, int] = {"fotos": 3, "rascunhos": 0}


class DeleteBucketSchema(BaseModel):
    """Remover não tem campo: a ação inteira está na URL."""


class BasePage(Page):
    """Chrome do painel: folha de estilo, título e avisos."""

    stylesheets: ClassVar[Sequence[str]] = ("/static/app.css",)
    title_suffix: ClassVar[str] = " · Painel"

    flashes: list[FlashMessage] = Field(default_factory=list)

    def shell(self, body: Widget) -> Widget:
        """Mostra os avisos pendentes acima do conteúdo."""
        return Shell(children=[FlashMessages(messages=self.flashes), body])


class AdminErrorPage(BasePage, ErrorPage):
    """A página de erro, com o chrome do painel."""


class BucketsPage(BasePage):
    """Lista de buckets, cada um com o seu botão de remover."""

    buckets: dict[str, int]

    def body(self) -> Widget:
        """Um card por bucket."""
        return Card(
            title="Buckets",
            children=[
                Card(
                    title=name,
                    children=[
                        Text(content=f"{objects} objetos", tag="p"),
                        form_for(
                            DeleteBucketSchema,
                            action=f"/admin/buckets/{name}/delete",
                            submit_label="Remover",
                            confirm=f"Remover o bucket {name}?",
                            id_prefix=f"delete-{name}",
                        ),
                    ],
                )
                for name, objects in self.buckets.items()
            ],
        )


app: FastAPI = FastAPI()
app.add_middleware(FlashMiddleware, secret="troque-por-um-segredo-das-settings")
app.include_router(make_htmx_router())
app.include_router(make_css_router(app_stylesheet()))
register_exception_handlers(app)
register_html_error_handlers(app, prefixes=["/admin"], error_page=AdminErrorPage)


@app.get("/admin/buckets")
async def list_buckets(request: Request) -> Response:
    """A tela: lê os avisos uma vez e os entrega à página."""
    return html_response(
        BucketsPage(title="Buckets", buckets=BUCKETS, flashes=get_flashes(request)),
    )


@app.post("/admin/buckets/{name}/delete")
async def delete_bucket(request: Request, name: str) -> RedirectResponse:
    """A ação: falha de regra vira exceção; sucesso vira aviso."""
    if BUCKETS.get(name):
        raise ConflictException(f"O bucket {name} ainda tem objetos.")
    BUCKETS.pop(name, None)
    flash(request, f"Bucket {name} removido.", "success")
    return redirect_back(request, fallback="/admin/buckets", allowed_prefix="/admin")
```

Rodando esse app com o `TestClient` (em `https://testserver`), o que sai:

```text
GET  /admin/buckets              200  <title>Buckets · Painel</title>
                                      <link rel="stylesheet" href="/static/app.css">
                                      <script src="/_ssr/confirm.js" defer></script>
                                      <form method="post" action="/admin/buckets/fotos/delete"
                                            class="tui-form" data-confirm="Remover o bucket fotos?">
POST /admin/buckets/fotos/delete 303  location: /admin/buckets   (set-cookie: tempest_flash=...)
GET  /admin/buckets              200  <div class="tui-alert tui-alert--error" role="alert">
                                      <p>O bucket fotos ainda tem objetos.</p>
                                      (set-cookie: tempest_flash=""; Max-Age=0 — lido, apagado)
POST /admin/buckets/rascunhos/delete 303  location: /admin/buckets
GET  /admin/buckets              200  <div class="tui-alert tui-alert--success" role="status">
                                      <p>Bucket rascunhos removido.</p>
GET  /admin/buckets/fotos/delete 405  <title>Erro 405 · Painel</title>
GET  /api/nada                   404  application/json {"detail":"Not Found"}
```

Nenhuma rota passou `title=`, `stylesheets=` nem escreveu uma linha de
JavaScript.

## Peça por peça

### Confirmar antes de remover

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.ui.forms import form_for


class DeleteBucketSchema(BaseModel):
    """Sem campos."""


form = form_for(
    DeleteBucketSchema,
    action="/admin/buckets/fotos/delete",
    submit_label="Remover",
    confirm="Remover o bucket fotos?",
)
```

`confirm=` põe `data-confirm="..."` no `<form>`. O `html_response` vê o
atributo no documento e inclui `/_ssr/confirm.js`, servido pelo mesmo
`make_htmx_router()` que já serve o HTMX. O script escuta `submit` no
documento inteiro, lê o texto com `getAttribute` e chama
`window.confirm`; na resposta "Cancelar", o submit não acontece.

- **O texto é dado, nunca código.** Ele é valor de atributo, escapado pelo
  renderer, e o script o lê de volta — nada é interpolado em JavaScript.
  `confirm=f"Remover {name}?"` com um nome cheio de aspas continua sendo
  só uma pergunta.
- **Sem JavaScript, o form funciona.** O atributo é inerte: o `POST`
  acontece, só sem a pergunta.
- **CSP amigável.** O script vem da própria aplicação; `script-src 'self'`
  basta, sem `'unsafe-inline'`.

Fora de um `form_for`, use `confirm()` — ele devolve o mesmo atributo
para espalhar em `attrs=` de um botão ou de um link:

```python
from tempest_core import Button, Text

from tempest_fastapi_sdk.ssr import confirm

button = Button(label="Remover tudo", attrs=confirm("Remover todos os buckets?"))
link = Text(
    content="Sair",
    tag="a",
    attrs={"href": "/logout", **confirm("Encerrar a sessão?")},
)
```

Num botão de submit, a pergunta do **botão** vale no lugar da do form —
dá para ter "Salvar" sem pergunta e "Remover" com pergunta no mesmo form.

!!! info "Quando o script entra na página"
    `html_response(confirm=None)` (o padrão) inclui o script quando o
    documento tem `data-confirm` **ou** quando `htmx=True`: uma troca do
    HTMX pode trazer um form com pergunta para uma página que não tinha
    nenhum, e o listener delegado no documento já cobre o fragmento que
    chegou. `confirm=True` força, `confirm=False` tira.

!!! tip "Elemento que dispara request HTMX"
    Para `hx-post`/`hx-delete`, prefira `htmx(confirm="...")`, que vira
    `hx-confirm` e é lido pelo próprio HTMX.

### Avisos de uma leitura só (flash)

```python
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from tempest_fastapi_sdk.ssr import FlashMiddleware, flash

app: FastAPI = FastAPI()
app.add_middleware(FlashMiddleware, secret="troque-por-um-segredo-das-settings")


@app.post("/admin/buckets")
async def create_bucket(request: Request) -> RedirectResponse:
    flash(request, "Bucket criado.", "success")
    return RedirectResponse("/admin/buckets", status_code=303)
```

- **`flash(request, texto, variante)`** enfileira o aviso. A variante é a
  mesma do `Alert`: `"info"`, `"success"`, `"warning"` ou `"error"`.
- **`FlashMiddleware`** grava a fila num cookie na resposta e, na próxima
  requisição, decodifica — só se a assinatura HMAC-SHA256 confere e o
  cookie tem menos de `max_age` segundos (padrão 300).
- **`get_flashes(request)`** devolve os avisos pendentes e os marca como
  lidos; o middleware apaga o cookie nessa resposta, então recarregar a
  página não repete o aviso. Uma tela que não chama `get_flashes` deixa o
  cookie como está, e o aviso espera a próxima que chamar.
- **`FlashMessages(messages=...)`** renderiza um `Alert` por aviso. Sem
  avisos, o wrapper sai vazio e a folha do SDK o esconde.

O texto **nunca** passa pela URL, então um link forjado não põe palavras
na tela; e um cookie forjado falha a assinatura e é apagado.

!!! warning "Assinado, não criptografado"
    Quem tem o cookie consegue ler o texto (é base64). Isso é uma escolha:
    o aviso é para a própria pessoa ler. Nunca ponha num flash o que ela
    não pode ver.

??? note "Detalhes técnicos do cookie"
    - `HttpOnly`, `SameSite=Lax` (sobrevive ao `303` de nível superior
      depois do form), `Secure` por padrão. Em `http://` de
      desenvolvimento passe `secure=False`: cookie `Secure` não volta por
      HTTP puro.
    - O segredo precisa de 16 caracteres ou mais; a assinatura usa um
      contexto fixo, então o mesmo segredo usado em outro lugar não produz
      um cookie de flash válido.
    - Cada aviso é cortado em `MAX_FLASH_MESSAGE_LENGTH` (500) caracteres,
      e a fila, em `MAX_FLASH_COOKIE_BYTES` (3800) bytes: o que não cabe
      perde os **mais antigos**, para o aviso mais recente sempre chegar.
    - `flash` e `get_flashes` sem o middleware levantam `RuntimeError`: um
      aviso enfileirado sem ele se perderia em silêncio.

### Voltar para a origem sem open redirect

```python
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from tempest_fastapi_sdk.ssr import redirect_back

app: FastAPI = FastAPI()


@app.post("/admin/buckets/{name}/delete")
async def delete_bucket(request: Request, name: str) -> RedirectResponse:
    return redirect_back(request, fallback="/admin/buckets", allowed_prefix="/admin")
```

`redirect_back` segue o `Referer` só quando ele aponta para **este host** e
para um path sob `allowed_prefix`; o `Location` enviado é sempre o path
**relativo**, nunca o header como chegou. O resto vai para `fallback`:

| `Referer` | Resultado |
| --- | --- |
| `https://testserver/admin/buckets?page=2` | `/admin/buckets?page=2` |
| ausente | `fallback` |
| `https://evil.example/admin` (outro host) | `fallback` |
| `https://testserver@evil.example/admin` (user info) | `fallback` |
| `/admin/buckets` (sem host) | `fallback` |
| `//evil.example/admin` (protocol-relative) | `fallback` |
| `javascript:...`, `data:...`, `ftp://...` | `fallback` |
| `https://testserver/public` (fora do prefixo) | `fallback` |
| `https://testserver/administrator` (prefixo sem fronteira) | `fallback` |
| `https://testserver//evil.example/x`, `.../%2Fevil.example` | `fallback` |
| `https://testserver/admin/../public`, `.../%2e%2e/...`, `\` | `fallback` |

Cada linha é um caso de `tests/ssr/test_redirects.py`. Precisa só do URL,
sem a resposta? `back_url(request, allowed_prefix=...)` devolve o path
validado ou `None`.

### A exceção vira página ou aviso

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.ssr import FlashMiddleware, register_html_error_handlers

app: FastAPI = FastAPI()
app.add_middleware(FlashMiddleware, secret="troque-por-um-segredo-das-settings")
register_exception_handlers(app)
register_html_error_handlers(app, prefixes=["/admin"])
```

`register_html_error_handlers` embrulha os handlers de `AppException` e de
`HTTPException` que já estão registrados — por isso vem **depois** do
`register_exception_handlers`. O handler JSON roda sempre (log, tradução
pelo catálogo, `on_server_error`), e só a forma da resposta muda:

| Requisição | Resposta |
| --- | --- |
| rota HTML, `GET`/`HEAD` | `error_page` com o mesmo status e a mensagem |
| rota HTML, outro método | flash `"error"` + `303` via `redirect_back` |
| qualquer outra | o JSON de sempre, intacto |

- **Rota HTML** é a que casa um dos `prefixes` (`"/admin"` casa `/admin`
  e `/admin/...`, não `/administrator`) ou tem uma das `tags=` do router.
  Path desconhecido não tem rota, então só o prefixo o reconhece.
- **`error_page=`** recebe a classe da página. Combine `ErrorPage` com a
  sua página-base — `class AdminErrorPage(BasePage, ErrorPage)` — e ela
  herda chrome, stylesheet e sufixo do título. O título vem do atributo
  de classe `title_template`, `"Erro {status_code}"` por padrão.
- **`fallback=`** é o destino quando o `Referer` é recusado; o padrão é o
  próprio prefixo casado.
- **Sem `FlashMiddleware`** o aviso se perderia, então o `POST` recebe a
  página de erro no lugar do redirect.

## Recap

- `form_for(..., confirm="...")` ou `confirm("...")` pergunta antes da
  ação; o script é local, o texto é dado, e sem JavaScript tudo funciona.
- `FlashMiddleware` + `flash` + `get_flashes` + `FlashMessages` levam um
  aviso através do redirect num cookie assinado, lido uma vez.
- `redirect_back` volta para a origem só quando ela é deste host e deste
  prefixo, e sempre com `Location` relativo.
- `register_html_error_handlers` faz a mesma exceção virar página de erro,
  aviso com redirect ou JSON, conforme quem pediu.
- `stylesheets`, `head` e `title_suffix` na página-base tiram esses
  argumentos de toda rota — veja a [camada UI](ui.md).

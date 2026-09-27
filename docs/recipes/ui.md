# Camada UI (páginas e componentes)

Uma camada de interface **no mesmo nível** de `controllers`, `services` e
`schemas` — não dentro deles. `src/ui/` responde a uma pergunta só: *como
isso aparece na tela*. Não abre sessão de banco, não chama API externa e
não decide regra de negócio.

!!! tip "Quando usar esta receita"
    - Seu serviço FastAPI precisa entregar **HTML**, não só JSON.
    - Você quer páginas em **Python tipado**, sem template engine e sem
      build de frontend.
    - Você quer que um agente de IA (ou outra pessoa) saiba **exatamente
      onde** colocar cada arquivo novo.

    Precisa de um SPA reativo ou de um build compilado? Veja
    [SSR (páginas tipadas)](../ssr.md) e
    [Fullstack web](../fullstack-web.md).

## A árvore, e o que vive em cada pasta

```text
src/
├── api/routers/       # HTTP: recebe request, delega, devolve resposta
├── controllers/       # orquestra services
├── services/          # regra de negócio
├── db/repositories/   # acesso a dados
├── schemas/           # DTOs Pydantic
└── ui/                # <- a camada de interface
    ├── pages/         # uma classe por tela
    ├── layout/        # o chrome que toda página herda
    ├── components/    # peças reutilizáveis
    └── styles.py      # a folha de estilo tipada do serviço
```

A regra de dependência é uma linha só, e vale para todo serviço:

| Camada | Pode importar | Nunca importa |
| --- | --- | --- |
| `api/routers` | `controllers`, `ui`, `schemas` | `db` |
| `ui` | `schemas`, outras partes de `ui` | `controllers`, `services`, `db` |
| `controllers` | `services`, `schemas` | `ui` |
| `services` | `db/repositories`, `schemas` | `ui` |

!!! warning "A página recebe dados prontos"
    Uma página **não** busca nada. O router carrega pelo controller e
    passa os dados já materializados para a página. Se você escreveu
    `await` dentro de `body()`, a responsabilidade escorregou de camada.

## Exemplo mínimo completo

Três arquivos: o chrome, a tela e a rota.

```python
# src/ui/layout/base.py
from collections.abc import Sequence
from typing import ClassVar

from tempest_core import Text, Widget

from tempest_fastapi_sdk.ui.components import NavBar, NavItem
from tempest_fastapi_sdk.ui.layout import Shell
from tempest_fastapi_sdk.ui.pages import Page

from src.ui.styles import CSS_URL

NAV_ITEMS: list[NavItem] = [
    NavItem(label="Início", href="/"),
    NavItem(label="Usuários", href="/users"),
]


class BasePage(Page):
    """Chrome compartilhado por todas as telas."""

    stylesheets: ClassVar[Sequence[str]] = (CSS_URL,)
    title_suffix: ClassVar[str] = " · Tempest"

    active_href: str = "/"

    def shell(self, body: Widget) -> Widget:
        """Envolve o corpo da página no layout comum."""
        return Shell(
            children=[body],
            header=NavBar(items=NAV_ITEMS, active_href=self.active_href),
            footer=Text(content="Tempest", tag="small"),
        )
```

```python
# src/ui/pages/users.py
from tempest_core import Widget

from tempest_fastapi_sdk.ui.components import Card, DataTable, EmptyState

from src.ui.layout.base import BasePage


class UsersPage(BasePage):
    """Lista de usuários."""

    users: list[dict[str, str]]

    def body(self) -> Widget:
        """Monta o conteúdo da tela."""
        if not self.users:
            return EmptyState(
                title="Nenhum usuário ainda",
                description="Eles aparecem aqui assim que o primeiro se cadastrar.",
            )
        return Card(title="Usuários", children=[DataTable(rows=self.users)])
```

```python
# src/api/routers/web.py
from fastapi import APIRouter
from fastapi.responses import Response

from tempest_fastapi_sdk.ssr import html_response

from src.ui.pages.users import UsersPage

router: APIRouter = APIRouter(tags=["web"], include_in_schema=False)


@router.get("/users")
async def users_page() -> Response:
    """Renderiza a lista de usuários."""
    users: list[dict[str, str]] = [{"nome": "Ana", "email": "ana@example.com"}]
    return html_response(
        UsersPage(title="Usuários", active_href="/users", users=users),
    )
```

Peça por peça:

- **`Page`** é um `Component` do `tempest_core`, ou seja, um modelo
  Pydantic: os dados da tela são **campos tipados** e um campo faltando
  falha na construção, não na renderização.
- **`body()`** devolve a árvore de widgets do conteúdo. É o único método
  que uma tela concreta precisa implementar.
- **`shell()`** envolve o corpo. Fica na página-base e é herdado por
  herança normal de Python — mudou o header, mudou em todas as telas.
- **`stylesheets`, `head` e `title_suffix`** são atributos de **classe**
  (`ClassVar`), não campos: declarados uma vez na página-base, valem para
  toda tela. O `html_response` lê de lá o `<link rel="stylesheet">`, o
  markup extra do `<head>` (favicon, meta) e o `<title>` — aqui,
  `"Usuários · Tempest"`, que é o `title` da página mais o sufixo.
- **`html_response`** renderiza para HTML e devolve a resposta do
  FastAPI. Os argumentos `title=`, `stylesheets=` e `head=` continuam
  valendo, e quando passados **substituem** o que a página declara — a
  rota nova que esquece de passá-los não sai mais sem estilo.

!!! tip "Precisa de mais que um sufixo no título?"
    Sobrescreva `document_title()` na página: ela recebe a instância, então
    pode montar `"(3) Fila · Tempest"` a partir de um campo.

## Componentes prontos

O SDK já traz as peças que todo painel repete. Todas produzem HTML
semântico com **classes**, não estilo inline — a aparência inteira vive
na folha de estilo (veja [CSS tipado](ui-css.md)).

```python
from tempest_core import Text

from tempest_fastapi_sdk.schemas import BasePaginationSchema
from tempest_fastapi_sdk.ui.components import (
    Alert,
    Card,
    DataTable,
    EmptyState,
    NavBar,
    NavItem,
    Pagination,
    pagination_for,
)

Alert(message="Conta criada.", variant="success")
Card(title="Resumo", children=[Text(content="12 pedidos")])
DataTable(rows=[{"nome": "Ana"}])
EmptyState(title="Nada por aqui")
NavBar(items=[NavItem(label="Início", href="/")], active_href="/")
Pagination(page=2, pages=5, url="/users")
```

| Componente | Para quê | Detalhe que economiza tempo |
| --- | --- | --- |
| `Card` | bloco titulado | escolha o nível do título com `heading_tag=` |
| `Alert` | mensagem por severidade | `warning`/`error` saem com `role="alert"` |
| `DataTable` | lista de schemas | deriva colunas e rótulos do próprio schema |
| `Pagination` | navegação de páginas | `pagination_for(envelope, url=...)` lê o `BasePaginationSchema` |
| `EmptyState` | coleção vazia | coleção vazia é `200 OK`, não 404 |
| `NavBar` | navegação principal | marca o item atual com `aria-current="page"` |
| `FlashMessages` | avisos de uma leitura só depois de um redirect | lê o que `get_flashes(request)` devolve; veja [Ações SSR](ssr-actions.md) |

`DataTable` é o que mais rende: passe as **response schemas** que o
serviço já devolve e o cabeçalho sai do `title` de cada campo.

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui.components import DataTable


class UserResponseSchema(BaseModel):
    name: str = Field(title="Nome")
    active: bool


table = DataTable(
    rows=[UserResponseSchema(name="Ana", active=True)],
    row_schema=UserResponseSchema,
)
```

Passar `row_schema=` faz o cabeçalho aparecer **mesmo com a lista
vazia** — e nesse caso a tabela mostra uma linha única com
`empty_text`.

### Link, botão e form dentro da célula

Tabela de painel quase nunca é só texto: o nome abre o detalhe, e cada
linha tem uma ação "remover". Para isso a coluna vira um `TableColumn`,
com um `render=` que recebe a linha e devolve widgets. Coluna de texto e
`TableColumn` se misturam na mesma lista:

```python hl_lines="15-26 33-35"
from pydantic import BaseModel
from tempest_core import Text, Widget
from tempest_core.widgets import Stack

from tempest_fastapi_sdk.ui.components import DataTable, TableColumn


class FileResponseSchema(BaseModel):
    id: int
    name: str
    size: int
    owner: str


def file_actions(row: FileResponseSchema) -> list[Widget]:
    return [
        Text(content=row.name, tag="a", attrs={"href": f"/files/{row.id}"}),
        Stack(
            tag="form",
            attrs={"method": "post", "action": f"/files/{row.id}/delete"},
            children=[
                Text(content="Remover", tag="button", attrs={"type": "submit"}),
            ],
        ),
    ]


table = DataTable(
    rows=[FileResponseSchema(id=7, name="a.txt", size=120, owner="ana")],
    row_schema=FileResponseSchema,
    columns=[
        TableColumn("name", header="Arquivo", render=file_actions),
        TableColumn("size", header="Bytes", align="right"),
        TableColumn("owner", class_name="col-optional"),
    ],
)
```

A linha sai assim (medido com o renderizador HTML do `tempestweb`):

```html
<tr><td><a href="/files/7">a.txt</a><form method="post" action="/files/7/delete"><button type="submit">Remover</button></form></td><td class="tui-table__cell--right">120</td><td class="col-optional">ana</td></tr>
```

Peça por peça:

- **`render=`** recebe a linha (o schema ou o dict de `rows`) e devolve um
  widget, uma lista de widgets ou uma `str`. Widget passa pelo
  renderizador normal — nada entra como HTML cru, e o texto de dentro
  continua escapado. `str` vira célula de texto escapada, igual ao
  default; `None` vira `none_text`.
- **`align=`** (`"left"`, `"center"`, `"right"`) aplica o modificador
  `tui-table__cell--<align>` no `<th>` e em cada `<td>` da coluna. O
  `"right"` também liga `font-variant-numeric: tabular-nums`, para os
  números alinharem pela unidade.
- **`class_name=`** vai no `<th>` e em cada `<td>`, para a sua folha
  mirar a coluna — por exemplo, escondê-la em tela estreita:

```python
from tempest_fastapi_sdk.ui.css import Media, Rule, StyleSheet

own: StyleSheet = StyleSheet(
    reset=False,
    rules=[Media.max_width(600, [Rule(".col-optional", declarations={"display": "none"})])],
)
```

- **`header=`** troca o rótulo só daquela coluna. Sem ele, vale a mesma
  cadeia de sempre: `headers=`, depois o `title` do campo, depois o nome
  humanizado.

E a paginação casa com o envelope do SDK:

```python
from tempest_fastapi_sdk.schemas import BasePaginationSchema
from tempest_fastapi_sdk.ui.components import pagination_for

envelope: BasePaginationSchema[str] = BasePaginationSchema[str](
    items=["a"], total=30, page=2, page_size=10, pages=3
)
control = pagination_for(envelope, url="/users", extra_query={"q": "ana"})
```

O `extra_query` preserva os filtros ativos em todos os links — o erro
clássico de paginação (trocar de página e perder a busca) não acontece.

## Layout

`Column`, `Row` e `Spacer` do `tempest_core` já cobrem flexbox, e o SDK
não os duplica. O que ele acrescenta é o que falta:

```python
from tempest_core import Text

from tempest_fastapi_sdk.ui.layout import Grid, Shell

Shell(children=[Text(content="conteúdo")], header=Text(content="topo"))
Grid(children=[Text(content="a"), Text(content="b")], columns=2)
```

- **`Shell`** monta os landmarks `<header>` / `<main>` / `<footer>` —
  estrutura que leitor de tela usa para navegar. Por default
  (`width="contained"`) o `<main>` fica numa coluna centralizada de
  `72rem`, com respiro dos lados — bom para página de leitura.
  `Shell(width="full")` acrescenta a classe `tui-shell__main--full`, que
  zera largura máxima, margem e padding: o layout clássico de painel, com
  sidebar colada na borda, ocupa a tela toda e cuida do próprio
  espaçamento, sem você sobrescrever CSS.
- **`Grid`** é CSS grid de verdade. Sem `columns=`, ele auto-ajusta
  (`minmax(16rem, 1fr)`), então vira uma coluna no celular sem media
  query nenhuma.

## Componentes próprios do serviço

Qualquer subárvore vira um `Component` tipado. É o mesmo mecanismo que
`Card` e `Alert` usam.

```python
from tempest_core import Text, Widget
from tempest_core.widgets import Component, Stack


class Stat(Component):
    """Um número grande com o rótulo embaixo."""

    label: str
    value: str

    def render(self) -> Widget:
        """Compõe a métrica."""
        return Stack(
            tag="div",
            attrs={"class": "stat"},
            children=[
                Text(content=self.value, tag="strong"),
                Text(content=self.label, tag="small"),
            ],
        )
```

!!! info "`Stack` para HTML semântico, `Column`/`Row` para flexbox"
    O renderizador injeta `display: flex` em `Column`/`Row` **pelo tipo
    do widget**, mesmo sem estilo. Um `<select>` ou `<table>` com
    `display: flex` quebra. `Stack` renderiza um elemento puro, sem
    estilo injetado — é o container certo para marcação semântica.
    Medido, e fixado em `tests/ui/test_core_contract.py`.

Num `Component` você sobrescreve `render()`. `body()` e `shell()`
existem só no `Page`.

## O scaffold escreve a camada inteira

```bash
tempest new meu-servico --extras "ssr"
```

Isso gera `src/ui/` completo — `styles.py`, `layout/base.py`,
`components/stat.py`, `pages/home.py` — mais `api/routers/web.py` já
ligando os três. Num projeto que já existe:

```bash
tempest generate --src
```

Ele lê os extras do seu `pyproject.toml` e escreve só as camadas que
faltam, sem tocar em arquivo existente (a menos que você passe
`--force`).

Falta apenas incluir os dois routers no `create_app`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.ui.css import make_css_router

from src.api.routers.web import router as web_router
from src.ui import CSS_PATH, STYLESHEET

app: FastAPI = FastAPI()
app.include_router(make_css_router(STYLESHEET, path=CSS_PATH))
app.include_router(web_router)
```

O router serve em `CSS_PATH`, e as páginas geradas linkam `CSS_URL`
(`STYLESHEET.url(CSS_PATH)`), a URL com a versão do conteúdo — o
[CSS tipado](ui-css.md#servindo-a-folha) explica o cache que isso compra.

## Recap

- `ui` é uma camada, no mesmo nível de `controllers` e `services`, e só
  responde "como isso aparece".
- `ui/pages/` tem uma classe por tela; `ui/layout/` tem o chrome que
  todas herdam; `ui/components/` tem as peças; `ui/styles.py` tem a
  folha.
- A página recebe dados prontos do router — nada de I/O dentro de
  `body()`.
- `Stack` para marcação semântica, `Column`/`Row` para flexbox.
- `DataTable` aceita `TableColumn(render=..., align=..., class_name=...)`
  para link, botão e form por linha, sem tabela escrita à mão.
- `Shell(width="full")` libera a largura toda para layout de painel.
- `tempest new --extras "ssr"` escreve tudo isso funcionando.

Próximos passos: [Formulários a partir de schemas Pydantic »](ui-forms.md)
e [CSS tipado »](ui-css.md).

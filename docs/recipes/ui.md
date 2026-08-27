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
from tempest_core import Text, Widget

from tempest_fastapi_sdk.ui.components import NavBar, NavItem
from tempest_fastapi_sdk.ui.layout import Shell
from tempest_fastapi_sdk.ui.pages import Page

NAV_ITEMS: list[NavItem] = [
    NavItem(label="Início", href="/"),
    NavItem(label="Usuários", href="/users"),
]


class BasePage(Page):
    """Chrome compartilhado por todas as telas."""

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
        title="Usuários",
        stylesheets=["/static/app.css"],
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
- **`html_response`** renderiza para HTML e devolve a resposta do
  FastAPI. O `stylesheets=` vira `<link rel="stylesheet">` no `<head>`.

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
  estrutura que leitor de tela usa para navegar.
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

## Recap

- `ui` é uma camada, no mesmo nível de `controllers` e `services`, e só
  responde "como isso aparece".
- `ui/pages/` tem uma classe por tela; `ui/layout/` tem o chrome que
  todas herdam; `ui/components/` tem as peças; `ui/styles.py` tem a
  folha.
- A página recebe dados prontos do router — nada de I/O dentro de
  `body()`.
- `Stack` para marcação semântica, `Column`/`Row` para flexbox.
- `tempest new --extras "ssr"` escreve tudo isso funcionando.

Próximos passos: [Formulários a partir de schemas Pydantic »](ui-forms.md)
e [CSS tipado »](ui-css.md).

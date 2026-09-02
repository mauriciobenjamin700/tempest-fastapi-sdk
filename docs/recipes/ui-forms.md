# Formulários a partir de schemas Pydantic

O schema que valida o request já descreve o formulário: nomes, tipos,
defaults, limites, títulos e descrições. Escrever o mesmo formulário duas
vezes — uma em HTML, outra em Pydantic — é o que esta receita apaga.

!!! tip "Quando usar"
    - Você tem um `*CreateSchema` / `*UpdateSchema` e precisa da tela que
      o preenche.
    - Você quer validação real (a do Pydantic) com mensagem por campo e
      o que a pessoa digitou preservado.
    - Você não quer manter `<input>` na mão sincronizado com o schema.

## O ciclo completo, em um arquivo

```python
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, EmailStr, Field

from tempest_fastapi_sdk.ssr import html_response
from tempest_fastapi_sdk.ui.forms import form_for, parse_form

app: FastAPI = FastAPI()


class SignupSchema(BaseModel):
    """Payload de cadastro — e a descrição do formulário."""

    email: EmailStr
    full_name: str = Field(min_length=3, max_length=50, description="Nome completo")
    password: str = Field(min_length=8)


@app.get("/signup")
async def signup_form() -> Response:
    """Mostra o formulário vazio."""
    return html_response(
        form_for(SignupSchema, action="/signup"),
        title="Cadastro",
        stylesheets=["/static/app.css"],
    )


@app.post("/signup")
async def signup(request: Request) -> Response:
    """Valida a submissão e recarrega a tela quando ela falha."""
    result = await parse_form(SignupSchema, request)
    if not result.ok:
        return html_response(
            form_for(
                SignupSchema,
                action="/signup",
                values=result.values,
                errors=result.errors,
                form_errors=result.form_errors,
            ),
            title="Cadastro",
            status_code=422,
            stylesheets=["/static/app.css"],
        )
    user = result.unwrap()
    return RedirectResponse(f"/welcome?email={user.email}", status_code=303)
```

São três chamadas:

1. **`form_for`** gera a árvore de widgets do `<form>` a partir do
   schema.
2. **`parse_form`** lê o corpo, ajusta o que HTML não expressa e valida.
3. Falhou? O mesmo `form_for` recebe `values=` e `errors=` do resultado
   e a tela volta com os erros no lugar certo e o texto preservado.

## O que sai de cada tipo

A tabela é a regra completa, na ordem em que é avaliada:

| Campo do schema | Controle |
| --- | --- |
| override `ui` em `json_schema_extra` | o que ele mandar |
| `Enum` / `Literal` | `<select>` |
| `bool` | `<input type="checkbox">` |
| `int` | `number` com `step="1"` |
| `float` / `Decimal` | `number` com `step="any"` |
| `EmailStr` | `email` |
| `HttpUrl` / `AnyUrl` | `url` |
| `SecretStr`, ou nome contendo `password`/`senha` | `password` |
| `date` / `datetime` / `time` | `date` / `datetime-local` / `time` |
| `UUID` | `text` |
| `str` com `max_length > 255` | `<textarea>` |
| `str` | `text` |
| `list[...]` de valores enumerados | `<select multiple>` |
| outras `list[...]` | `<textarea>`, um valor por linha |

Os limites do schema viram atributos nativos de validação, então o
navegador já barra o óbvio antes do round-trip:

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui.forms import fields_for


class ProductSchema(BaseModel):
    """Produto com limites declarados."""

    name: str = Field(min_length=3, max_length=50)
    quantity: int = Field(ge=1, le=99)


specs = fields_for(ProductSchema)
assert specs[0].constraints == {"minlength": "3", "maxlength": "50"}
assert specs[1].constraints == {"min": "1", "max": "99", "step": "1"}
```

!!! warning "`gt` e `lt` viram `min` e `max`"
    HTML só tem limites inclusivos. Um `Field(gt=0)` gera `min="0"`, que
    é uma dica um passo mais frouxa do que o schema. Quem rejeita o zero
    continua sendo o Pydantic, no submit — a validação de verdade nunca
    ficou no navegador.

## O HTML é acessível por padrão

Cada campo sai assim:

```html
<div class="tui-field tui-field--invalid">
  <label class="tui-field__label" for="f-email">
    <span>Email</span><span class="tui-field__required" aria-hidden="true">*</span>
  </label>
  <input name="email" id="f-email" class="tui-field__control" required="required"
         aria-invalid="true" aria-describedby="f-email-error"
         autocomplete="email" type="email" />
  <p class="tui-field__error" id="f-email-error">já cadastrado</p>
</div>
```

O que vem de graça: `<label for>` ligado ao controle, `aria-invalid` no
campo com erro, `aria-describedby` apontando para a dica e para a
mensagem, `autocomplete` quando o tipo permite deduzir, e o asterisco de
obrigatório marcado `aria-hidden` (a informação real está no `required`).

Duas telas com formulário na mesma página? Dê a cada uma seu
`id_prefix`, e os `id`/`for` deixam de colidir:

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.ui.forms import form_for


class SearchSchema(BaseModel):
    """Filtro de busca."""

    term: str


widget = form_for(SearchSchema, action="/search", method="get", id_prefix="search")
```

## Ajustando campo a campo

Para o que a introspecção não tem como adivinhar, declare no próprio
schema:

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui.forms import form_for


class ArticleSchema(BaseModel):
    """Artigo com dicas de apresentação no schema."""

    title: str
    body: str = Field(
        default="",
        json_schema_extra={
            "ui": {
                "control": "textarea",
                "rows": 12,
                "label": "Corpo",
                "placeholder": "Escreva em Markdown…",
                "help_text": "Aceita Markdown",
            },
        },
    )
    owner_id: str = Field(default="", json_schema_extra={"ui": {"hidden": True}})


widget = form_for(ArticleSchema, action="/articles", exclude=["owner_id"])
```

Chaves aceitas em `ui`: `control`, `input_type`, `label`, `placeholder`,
`help_text`, `autocomplete`, `rows`, `hidden`, `attrs`.

## Quando o schema não basta: edite a especificação

`form_for` é açúcar para dois passos. Separe-os quando quiser mexer no
formulário gerado antes de renderizar:

```python
from dataclasses import replace

from pydantic import BaseModel

from tempest_fastapi_sdk.ui.forms import form_spec_for, render_form


class ContactSchema(BaseModel):
    """Contato."""

    email: str
    message: str


spec = form_spec_for(ContactSchema, action="/contact", submit_label="Enviar mensagem")
spec = replace(
    spec,
    fields=[
        replace(field, placeholder="voce@exemplo.com") if field.name == "email" else field
        for field in spec.fields
    ],
)
widget = render_form(spec)
```

`FormSpec` e `FieldSpec` são dataclasses congeladas: `replace()` devolve
uma cópia alterada, e nada muda por baixo de você.

## Lendo a submissão

`parse_form` cuida das três coisas que HTML faz diferente do seu schema:

```python
from fastapi import Request
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui.forms import parse_form


class PreferencesSchema(BaseModel):
    """Preferências de uma conta."""

    newsletter: bool = True
    tags: list[str] = Field(default_factory=list)
    nickname: str | None = None


async def save(request: Request) -> str:
    """Lê o formulário e devolve um resumo."""
    result = await parse_form(PreferencesSchema, request)
    if not result.ok:
        return "inválido"
    return f"{result.unwrap().newsletter}"
```

- **Checkbox desmarcado não envia nada** — a chave ausente vira `False`,
  não "campo faltando".
- **Chave que o corpo não trouxe fica de fora do payload**, então o
  default do schema se aplica e um campo obrigatório reporta `Field
  required` contra si mesmo.
- **Texto vazio em campo opcional vira `None`**, e não `""`.
- **`<select multiple>`** manda a chave repetida; uma `textarea` de lista
  manda linhas. Os dois viram a mesma `list`.

Valores que o servidor é dono de decidir não devem sair do navegador:

```python
from fastapi import Request
from pydantic import BaseModel

from tempest_fastapi_sdk.ui.forms import parse_form


class OrderSchema(BaseModel):
    """Pedido."""

    product: str
    owner_id: str


async def create(request: Request, current_user_id: str) -> str:
    """Lê o pedido ignorando o dono que veio do formulário."""
    result = await parse_form(
        OrderSchema,
        request,
        exclude=["owner_id"],
        extra={"owner_id": current_user_id},
    )
    return "ok" if result.ok else "inválido"
```

`exclude=` proíbe a leitura daquele campo do corpo e `extra=` injeta o
valor do servidor. Chaves que não pertencem ao schema (token de CSRF,
bookkeeping do HTMX) são ignoradas de qualquer forma.

Para trocar o texto das mensagens do Pydantic, passe `error_message`:

```python
from collections.abc import Mapping
from typing import Any

MESSAGES: dict[str, str] = {
    "string_too_short": "Muito curto.",
    "value_error": "Valor inválido.",
    "missing": "Campo obrigatório.",
}


def translate(error: Mapping[str, Any]) -> str:
    """Traduz um erro do Pydantic para a mensagem mostrada na tela."""
    return MESSAGES.get(str(error["type"]), str(error["msg"]))
```

## O visual vem junto

As classes que o formulário emite (`tui-form`, `tui-field`, …) já têm
regras prontas, escritas com os design tokens:

```python
from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.css import make_css_router

router = make_css_router(app_stylesheet())
```

`app_stylesheet()` inclui as regras de formulário e de componentes. Se
você monta a folha peça a peça, use `form_stylesheet()`. Para plugar num
design system próprio, passe `classes=FormClasses(...)` tanto para
`form_for` quanto para `form_stylesheet` — os nomes acompanham.

## Limites, medidos

!!! danger "Dois campos param a geração de propósito"
    - **Modelo aninhado** levanta `UnsupportedFieldError`: ele precisa do
      próprio formulário, ou de um `exclude=` e o valor definido no
      servidor.
    - **Campo binário (`bytes`)** também levanta: upload é `UploadFile`
      na rota, não um valor coagido de string.

    Falhar alto é melhor do que renderizar um campo que nunca fecha o
    round-trip.

!!! info "Por que não usar `Input` / `Dropdown` do `tempest_core`"
    Medido contra o renderizador HTML, no `tempest-core` 0.18.0: `Form()`
    sai como `<div></div>` — sem `action`, sem `method`, não é um
    `<form>` — e **nenhum** dos controles renderiza `name`, então um
    formulário feito deles submete corpo vazio: falha sem mensagem de
    erro em lugar nenhum. Os widgets são do cliente reativo, não do SSR.

    A medição mudou de forma na 0.18.0, e vale registrar o que o upstream
    corrigiu: até a 0.14.0 o `Dropdown` e o `TextArea` saíam como `<div>`
    vazio, perdendo o tipo do elemento e a lista de opções. Hoje as tags
    estão certas — `<input>`, `<textarea>` e um `<select>` com os seus
    `<option>`. O que decide continua sendo a ausência do `name`.

    Por isso `ui.forms` emite os elementos direto pelo escape hatch
    `tag`/`attrs`. A medição está fixada em
    `tests/ui/test_core_contract.py`.

## Recap

- O schema descreve o formulário; `form_for` renderiza e `parse_form`
  lê de volta.
- Erro de validação vira mensagem por campo com o que a pessoa digitou
  preservado — sem estado extra no servidor.
- Limites do schema viram atributos nativos; a validação real continua no
  Pydantic.
- Ajuste fino por `json_schema_extra={"ui": {...}}` ou editando o
  `FormSpec` com `replace()`.
- `exclude=` + `extra=` mantêm valores do servidor fora do alcance do
  navegador.

Veja também: [Camada UI »](ui.md) e [CSS tipado »](ui-css.md).

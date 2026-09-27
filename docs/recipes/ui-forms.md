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
| `UploadFile` / `list[UploadFile]` | `<input type="file">` (`multiple` na lista) |
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
    owner_id: str = Field(default="", json_schema_extra={"ui": {"omit": True}})


widget = form_for(ArticleSchema, action="/articles")
```

Chaves aceitas em `ui`: `control`, `input_type`, `label`, `placeholder`,
`help_text`, `autocomplete`, `rows`, `accept`, `multiple`, `omit`,
`hidden`, `attrs`.

`{"omit": True}` tira o campo do formulário, como o `exclude=` faz na
chamada. `{"hidden": True}` é a grafia antiga do `omit` e continua
significando a mesma coisa: **não** gera campo oculto. Para isso, leia a
próxima seção.

### Campo oculto de verdade

Um formulário de ação por linha (remover o objeto `key` e voltar para a
pasta `prefix`) precisa devolver ao servidor valores que a pessoa não
edita. Marque o campo com `{"control": "hidden"}`:

```python
from pydantic import BaseModel, Field
from pydantic.json_schema import JsonDict
from tempestweb.html import render_to_html

from tempest_fastapi_sdk.ui.forms import form_for

HIDDEN: JsonDict = {"ui": {"control": "hidden"}}


class RemoveObjectSchema(BaseModel):
    """Remove um objeto; nenhum dos valores é digitado."""

    prefix: str = Field(max_length=64, json_schema_extra=HIDDEN)
    key: str = Field(max_length=1024, json_schema_extra=HIDDEN)
    position: int = Field(ge=0, json_schema_extra=HIDDEN)


html: str = render_to_html(
    form_for(
        RemoveObjectSchema,
        action="/objects/remove",
        values={"prefix": "docs/", "key": "docs/a.txt", "position": 3},
        submit_label="Remover",
    ),
)
print(html)
```

A saída tem só os três `<input type="hidden">` e o botão:

```html
<form method="post" action="/objects/remove" class="tui-form"><input type="hidden" name="prefix" value="docs/" /><input type="hidden" name="key" value="docs/a.txt" /><input type="hidden" name="position" value="3" /><div class="tui-form__actions"><button type="submit" class="tui-btn">Remover</button></div></form>
```

- **Sem label, ajuda nem wrapper**, qualquer que seja o tipo. Um `str`
  com `max_length > 255` continua um `<input type="hidden">`, e não vira
  `<textarea>`.
- **O valor atravessa o `parse_form`** e é validado pelo schema como
  qualquer campo: `position=-1` volta como erro de `position`.
- **O erro de um campo oculto não tem onde aparecer**, então ele sobe para
  a lista de erros do formulário, prefixado com o label:
  `Position: Input should be greater than or equal to 0`.

!!! warning "Oculto não é protegido"
    O navegador manda o que estiver no `value`, e qualquer pessoa edita o
    HTML antes de enviar. Campo oculto serve para contexto que a própria
    pessoa poderia escolher (a pasta em que ela estava). Valor que o
    servidor decide (dono, tenant, status) sai com `exclude=` e entra com
    `extra=`, como mostra [Lendo a submissão](#lendo-a-submissao).

### O texto de ajuda sai da `description`

Sem `help_text`, a dica embaixo do controle é a `description` do campo.
Só que a `description` também é a documentação do schema no OpenAPI, e
**o que você escreve ali aparece na tela** para quem preenche o
formulário. Um schema documentado em inglês para o time mostra essa frase
técnica para o usuário final.

Você controla isso de dois jeitos:

```python
from pydantic import BaseModel, Field
from tempestweb.html import render_to_html

from tempest_fastapi_sdk.ui.forms import form_for


class ObjectSchema(BaseModel):
    """Descrições escritas para desenvolvedor."""

    key: str = Field(description="Key of the object to remove.")
    note: str = Field(
        description="Free text stored with the object.",
        json_schema_extra={"ui": {"help_text": None}},
    )
    title: str = Field(
        description="Display title.",
        json_schema_extra={"ui": {"help_text": "Nome que aparece na lista"}},
    )


per_field: str = render_to_html(form_for(ObjectSchema, action="/objects"))
whole_form: str = render_to_html(
    form_for(ObjectSchema, action="/objects", describe=False),
)
assert "Key of the object to remove." in per_field
assert "Free text stored with the object." not in per_field
assert "Key of the object to remove." not in whole_form
assert "Nome que aparece na lista" in whole_form
```

- **Por campo:** a chave `help_text` presente sempre vence. `""`, `None`
  ou `False` suprimem o `<small>` (e o `aria-describedby` que aponta para
  ele) em vez de cair na `description`.
- **Por formulário:** `describe=False` desliga o fallback. Só o
  `help_text` explícito vira dica.

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

## Sem `Request` na rota: `form_dependency`

O `parse_form` recebe a `Request`, e é só por isso que ela aparece no
router. `form_dependency(Schema)` devolve uma dependency do FastAPI que
chama o `parse_form` por você, e a rota recebe o `FormResult` já tipado,
como qualquer outro `Depends`:

```python
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, EmailStr, Field

from tempest_fastapi_sdk.ssr import html_response
from tempest_fastapi_sdk.ui.forms import FormResult, form_dependency, form_for

app: FastAPI = FastAPI()


class SignupSchema(BaseModel):
    """Payload de cadastro."""

    email: EmailStr
    password: str = Field(min_length=8)


SignupForm = Annotated[FormResult[SignupSchema], Depends(form_dependency(SignupSchema))]


@app.post("/signup")
async def signup(result: SignupForm) -> Response:
    """Valida a submissão sem tocar na Request."""
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
        )
    user = result.unwrap()
    return RedirectResponse(f"/welcome?email={user.email}", status_code=303)
```

`form_dependency` aceita os mesmos `include=`, `exclude=`, `extra=` e
`error_message=` do `parse_form`. `result.unwrap()` sai tipado como
`SignupSchema` no mypy `--strict` (fixado em
`tests/ui/test_forms_dependency.py`), e a dependency não acrescenta nada
ao schema OpenAPI da rota.

!!! tip "Valor do servidor que depende do request"
    O `extra=` do `form_dependency` é fixo: ele é montado junto do módulo,
    antes de existir request. Para injetar o usuário corrente, deixe o
    campo em `exclude=` e defina o valor na rota, ou chame `parse_form`
    direto.

!!! note "Declare o alias no nível do módulo"
    Com `from __future__ import annotations`, o FastAPI resolve a anotação
    da rota contra as globais do módulo. Um `Depends(...)` guardado numa
    variável local da função não é encontrado, e o parâmetro vira query
    string obrigatória.

## Upload de arquivo

Anote o campo como `UploadFile` do FastAPI, e o `form_for` emite o
`<input type="file">` e troca o `enctype` do formulário para
`multipart/form-data` sozinho. `list[UploadFile]` vira um controle com
`multiple`. O `parse_form` entrega o próprio `UploadFile` no modelo:

```python
from typing import Annotated

from fastapi import Depends, FastAPI, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ssr import html_response
from tempest_fastapi_sdk.ui.forms import FormResult, form_dependency, form_for

app: FastAPI = FastAPI()


class AvatarSchema(BaseModel):
    """Título e imagem de perfil."""

    title: str = Field(min_length=3)
    avatar: UploadFile = Field(json_schema_extra={"ui": {"accept": "image/*"}})
    attachments: list[UploadFile] = Field(default_factory=list)


AvatarForm = Annotated[FormResult[AvatarSchema], Depends(form_dependency(AvatarSchema))]


@app.get("/avatar")
async def avatar_form() -> Response:
    """Mostra o formulário de upload."""
    return html_response(form_for(AvatarSchema, action="/avatar"), title="Avatar")


@app.post("/avatar")
async def upload_avatar(result: AvatarForm) -> Response:
    """Recebe o arquivo, ou recarrega a tela com o erro."""
    if not result.ok:
        return html_response(
            form_for(
                AvatarSchema,
                action="/avatar",
                values=result.values,
                errors=result.errors,
            ),
            title="Avatar",
            status_code=422,
        )
    form = result.unwrap()
    content: bytes = await form.avatar.read()
    return Response(f"{form.avatar.filename}: {len(content)} bytes")
```

- **O `enctype` é automático** quando o formulário tem campo de arquivo.
  Um `enctype` passado em `attrs=` vence.
- **`accept` e `multiple`** vêm do bloco `ui`. Com
  `{"ui": {"control": "file"}}` você marca o controle de arquivo pelo
  override em vez do tipo.
- **Controle de arquivo vazio conta como chave ausente.** O navegador
  manda uma parte sem nome de arquivo; o `parse_form` a descarta, então o
  campo obrigatório reporta `Field required` e o opcional fica no default.
- **No re-render com erro, o arquivo volta vazio**, com a mensagem, e os
  outros campos mantêm o valor. O navegador não deixa pré-preencher
  `<input type="file">`, então a pessoa escolhe o arquivo de novo; o
  arquivo nunca entra em `result.values`.

O caminho completo, com corpo `multipart/form-data` montado byte a byte
como o navegador manda, está em `tests/ui/test_forms_file.py`.

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
    - **Campo `bytes`** também levanta: arquivo se declara como
      `UploadFile`, que gera o controle de arquivo e chega ao modelo como
      o próprio upload ([Upload de arquivo](#upload-de-arquivo)).

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
- `{"control": "hidden"}` gera o `<input type="hidden">` puro;
  `{"omit": True}` tira o campo do formulário.
- A `description` vira texto de tela; `help_text=None` ou
  `describe=False` desligam.
- `form_dependency(Schema)` tira a `Request` da rota, e `UploadFile` no
  schema gera o upload com `multipart/form-data`.

Veja também: [Camada UI »](ui.md) e [CSS tipado »](ui-css.md).

# Cliente de integração a partir de um OpenAPI

Integrar com um sistema de terceiros é, hoje, transcrição manual: você abre a
documentação, lê campo por campo, escreve o schema Pydantic equivalente, decide
o nome pythônico de cada campo (`createdAt` → `created_at`), configura o `alias`
para o payload continuar batendo com o que vai na rede — e depois escreve mais
uma camada só para montar as requisições HTTP.

A especificação OpenAPI já descreve tudo isso formalmente. Então:

```bash
tempest openapi-client https://api.terceiro.com/openapi.json --name terceiro
```

```text
  + src/integrations/terceiro/__init__.py
  + src/integrations/terceiro/client.py
  + src/integrations/terceiro/schemas.py
4 schema(s), 12 operation(s).
```

Pronto. Schemas tipados com metadados preenchidos, e um cliente HTTP tipado em
cima deles. 🚀

## Por que isso importa

Três problemas na transcrição manual:

1. **Custo proporcional ao tamanho da API.** 40 endpoints e 60 modelos é uma
   tarde inteira de trabalho mecânico.
2. **Erra e apodrece.** Um campo opcional transcrito como obrigatório, um
   `alias` esquecido, um enum copiado incompleto — todos falham só em runtime,
   contra o serviço de terceiro, muitas vezes só em produção.
3. **A documentação se perde.** A spec descreve cada campo com descrição,
   formato e exemplo. Nada disso sobrevive à transcrição: o schema chega no
   repositório como uma lista de nomes e tipos.

O item 3 é o que o gerador ataca com mais força: **cada `Field` sai com
`title` / `description` / `examples` da especificação**, então o módulo gerado
_é_ a documentação da integração — e sobrevive ao terceiro mudar ou derrubar o
site de docs.

## O que sai

```text
src/integrations/terceiro/
├── __init__.py     re-exporta o cliente e a DEFAULT_BASE_URL
├── schemas.py      uma classe por componente, metadados preenchidos
└── client.py       um método async por operação
```

!!! info "Por que um pacote próprio, e não dentro de `src/schemas/`"
    Uma integração de terceiro é um **adaptador de saída**, não a camada de DTO
    do seu serviço. Jogar 60 schemas gerados em `src/schemas/` colidiria com os
    escritos à mão e poluiria o `__init__.py` daquele pacote. Um diretório
    próprio e inteiramente gerado é seguro de regenerar — nada ali é editado à
    mão.

    Precisa de outro lugar? `--out src/vendor/terceiro`.

### `schemas.py`

Para este trecho de especificação:

```json
{
  "Customer": {
    "type": "object",
    "description": "A billable customer account.",
    "required": ["id", "emailAddress"],
    "properties": {
      "id": {"type": "string", "format": "uuid", "description": "Server-assigned id."},
      "emailAddress": {"type": "string", "format": "email", "title": "Email",
                       "description": "Primary contact email.", "example": "ana@example.com"},
      "createdAt": {"type": "string", "format": "date-time"},
      "tags": {"type": "array", "items": {"type": "string"}},
      "class": {"type": "string", "description": "Reserved-word field name."}
    }
  }
}
```

sai isto:

```python
from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, EmailStr, Field

from tempest_fastapi_sdk import BaseSchema


class Customer(BaseSchema):
    """A billable customer account.

    Attributes:
        id (UUID): Server-assigned id.
        email_address (EmailStr): Primary contact email.
        created_at (datetime | None): Undocumented in the spec.
        tags (list[str]): Undocumented in the spec.
        class_ (str | None): Reserved-word field name.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(description="Server-assigned id.")
    email_address: EmailStr = Field(
        alias="emailAddress",
        title="Email",
        description="Primary contact email.",
        examples=["ana@example.com"],
    )
    created_at: datetime | None = Field(alias="createdAt", default=None)
    tags: list[str] = Field(default_factory=list)
    class_: str | None = Field(
        alias="class",
        description="Reserved-word field name.",
        default=None,
    )
```

Cinco coisas acontecendo aí:

- **Nomes pythônicos + `alias`.** `emailAddress` → `email_address`, com o nome
  de rede preservado. `populate_by_name=True` faz o schema aceitar **os dois**
  na entrada; `model_dump(by_alias=True)` devolve a forma da rede.
- **Palavra reservada resolvida.** `class` → `class_`, alias intacto.
- **`format` vira tipo rico.** `uuid` → `UUID`, `date-time` → `datetime`,
  `email` → `EmailStr`.
- **Coleção opcional é lista vazia**, nunca `list[X] | None` — a regra do
  projeto: "nenhum resultado" é lista vazia, não valor ausente.
- **Metadados preenchidos**, e **nada inventado**: campo sem `description` na
  origem sai sem ela (a docstring marca `Undocumented in the spec.`).

### `client.py`

```python
from tempest_fastapi_sdk import HTTPClient

from src.integrations.billing import Customer, CustomerStatus


class TerceiroClient:
    """Client for Billing API (version 2.1.0)."""

    def __init__(self, client: HTTPClient) -> None:
        """Initialize the client.

        Args:
            client (HTTPClient): The transport to issue requests through.
        """
        self._client: HTTPClient = client

    async def list_customers(
        self,
        *,
        page_size: int | None = None,
        status: CustomerStatus | None = None,
    ) -> list[Customer]:
        """List customers.

        Args:
            page_size (int | None): Rows per page. Omitted from the query when None.
            status (CustomerStatus | None): The status value. Omitted when None.

        Returns:
            list[Customer]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification
                documents 401.
        """
```

O cliente recebe um [`HTTPClient`](http-client.md) **por injeção** — não
constrói um. Então o retry, o backoff, o circuit breaker, o timeout e as
credenciais continuam sendo seus:

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import HTTPClient

from src.core.settings import settings
from src.integrations.terceiro import DEFAULT_BASE_URL, TerceiroClient

terceiro_http: HTTPClient = HTTPClient(
    base_url=DEFAULT_BASE_URL,
    default_headers={"Authorization": f"Bearer {settings.TERCEIRO_TOKEN}"},
    timeout=15.0,
)
terceiro: TerceiroClient = TerceiroClient(terceiro_http)
```

E usando:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient

from src.integrations.billing import CustomerStatus

terceiro = HTTPClient(base_url="https://api.parceiro.com")


async def main() -> None:
    """Run this example."""
    customers = await terceiro.list_customers(
        page_size=25,
        status=CustomerStatus.PAST_DUE,
    )
    for customer in customers:
        print(customer.email_address, customer.created_at)


asyncio.run(main())
```

!!! check "Testável sem rede"
    Como o transporte é injetado, um `httpx.MockTransport` cobre a integração
    inteira nos seus testes:

    ```python
    import httpx
    from tempest_fastapi_sdk import HTTPClient

    from src.integrations.terceiro import DEFAULT_BASE_URL, TerceiroClient


    def handler(request: httpx.Request) -> httpx.Response:
        """Responde a chamada sem sair da máquina."""
        return httpx.Response(200, json=[])


    async def test_list() -> None:
        """Exercita o cliente gerado contra um transporte falso."""
        http = HTTPClient(
            base_url=DEFAULT_BASE_URL,
            transport=httpx.MockTransport(handler),
        )
        async with http:
            assert await TerceiroClient(http).list_customers() == []
    ```

!!! warning "O cliente gerado exige o extra `[http]`"
    `HTTPClient` levanta `ImportError` sem ele. `uv add "tempest-fastapi-sdk[http]"`.

## Opções

| Opção | Efeito |
| --- | --- |
| `<spec>` (argumento) | URL (`http(s)://`) ou caminho da especificação |
| `--name` / `-n` | Nome da integração — vira o diretório e o prefixo da classe. Default: `info.title` slugificado |
| `--out` / `-o` | Destino. Default: `<src\|app>/integrations/<name>/` |
| `--header` / `-H` | Header para baixar a spec (`"Authorization: Bearer ..."`). Repetível |
| `--path` / `-p` | Raiz do projeto usada para resolver o destino default |
| `--schemas-only` | Não gerar `client.py` |
| `--force` / `-f` | Sobrescrever o que já existe |
| `--no-format` | Não rodar `ruff format` no resultado |

### Especificação atrás de autenticação

```bash
tempest openapi-client https://api.terceiro.com/openapi.json \
    --name terceiro \
    --header "Authorization: Bearer $TERCEIRO_TOKEN"
```

### Especificação em YAML

Funciona, mas precisa do extra `[openapi]` (PyYAML). JSON sai com a stdlib.

```bash
uv add "tempest-fastapi-sdk[openapi]"
tempest openapi-client ./vendor/terceiro.yaml --name terceiro
```

Sem o extra, a mensagem diz exatamente isso em vez de estourar um traceback.

### Atualizando quando o terceiro versiona a API

O diretório é **inteiramente gerado**, então regenerar é seguro:

```bash
tempest openapi-client https://api.terceiro.com/openapi.json --name terceiro --force
```

!!! tip "O diff é o changelog da integração"
    Rodar com uma spec inalterada produz um arquivo **byte a byte idêntico**
    (tem teste garantindo). Então qualquer linha que aparecer no `git diff`
    depois de um `--force` é uma mudança real do terceiro — campo novo, campo
    que virou obrigatório, enum que ganhou valor.

## Cobertura de OpenAPI

O que o gerador representa, declarado:

| Construção | Tratamento |
| --- | --- |
| `type: object` + `properties` | Classe herdando `BaseSchema` |
| `required` | Campo sem default; ausente → `X \| None = None` |
| `string`/`integer`/`number`/`boolean` | `str`/`int`/`float`/`bool` |
| `format: date-time`/`date`/`time` | `datetime`/`date`/`time` |
| `format: uuid`/`email`/`binary`/`decimal` | `UUID`/`EmailStr`/`bytes`/`Decimal` |
| `type: array` | `list[T]`; não obrigatório → `Field(default_factory=list)` |
| `enum` de strings / inteiros | Subclasse de `BaseStrEnum` / `BaseIntEnum` |
| `$ref` interno | Referência à classe gerada, ordenada por dependência |
| `allOf` | Achatado num único modelo |
| `oneOf` / `anyOf` | `A \| B`; com `discriminator`, `Annotated[..., Field(discriminator=...)]` |
| `nullable: true` (3.0) / `type: [x, "null"]` (3.1) | `X \| None` |
| `additionalProperties` | `dict[str, T]` |
| `minLength` / `maximum` / `pattern` / `minItems` / … | Constraints no `Field` |
| Recursivo / mutuamente recursivo | Anotações adiadas + `model_rebuild()` no fim do módulo |
| Parâmetros de `path` e `query` | Argumentos tipados do método |
| Corpo e resposta `application/json` | Schema gerado |
| Resposta 204 / sem corpo | Retorno `None` |

E o que **não** é representado — sempre com `Any` + nota no resumo do comando,
nunca em silêncio:

| Construção | Motivo |
| --- | --- |
| `not` | Sem equivalente em Python |
| `$ref` externo | Bundle a spec primeiro (`redocly bundle`) |
| Swagger 2.0 | Converta para OpenAPI 3 (`swagger2openapi`) |
| Parâmetros de `header` / `cookie` | Passe via `HTTPClient(default_headers=...)` |
| Corpo/resposta não-JSON (`multipart`, `octet-stream`) | Fora do escopo desta iteração |
| `type` com múltiplos valores concretos | Não modelado |

!!! danger "Nunca chuta"
    O contrato do parser: o que ele não consegue representar vira `Any`, ganha
    um comentário `# openapi: unsupported ...` e aparece no resumo do comando:

    ```text
    1 construct(s) could not be modelled (rendered as Any, marked in the output):
      - 'header' parameter 'X-Trace' skipped (pass it via HTTPClient default_headers)
    ```

    Um schema errado que **parece** certo é pior que uma lacuna documentada.

## O código gerado passa nos seus gates

O emissor produz código que passa `ruff check` e `ruff format --check` **antes**
de qualquer passada de formatação — anotações completas, aspas duplas,
docstrings Google em todo módulo/classe/método, imports na ordem do isort.

Isso é testado, não prometido: a suíte roda `ruff` contra a saída crua
(`--no-format`). Vale a pena saber por quê — foi essa asserção que pegou um
`UUID` não importado, um enum não importado, uma linha de docstring longa demais
e dois erros de ordenação de import que nenhuma asserção sobre o *shape* dos
schemas teria notado.

Então `--no-format` (ou uma máquina sem `ruff` instalado) ainda entrega um
pacote utilizável. A passada de `ruff` que o comando roda por padrão é polimento,
não correção.

## Recapitulando

1. **`tempest openapi-client <spec> --name X`** gera
   `src/integrations/x/` com `schemas.py` + `client.py`.
2. **Nomes pythônicos com `alias`** para o nome de rede, e `populate_by_name`
   para aceitar os dois na entrada.
3. **Metadados da spec preenchidos** em todo `Field` — o módulo gerado é a
   documentação da integração. Nada é inventado.
4. **O cliente recebe um `HTTPClient` injetado**, então retry / circuit breaker /
   credenciais continuam seus, e `httpx.MockTransport` testa tudo sem rede.
5. **`--force` regenera**, e como uma spec inalterada gera arquivo idêntico, o
   diff mostra exatamente o que o terceiro mudou.
6. **O que não é suportado vira `Any` + nota**, nunca silêncio.

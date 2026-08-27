# Cliente de integração a partir de um OpenAPI

Integrar com um sistema de terceiros é, hoje, transcrição manual: você abre a
documentação, lê campo por campo, escreve o schema Pydantic equivalente, decide
o nome pythônico de cada campo (`createdAt` → `created_at`), configura os aliases de rede
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
        validation_alias="emailAddress",
        serialization_alias="emailAddress",
        title="Email",
        description="Primary contact email.",
        examples=["ana@example.com"],
    )
    created_at: datetime | None = Field(
        validation_alias="createdAt",
        serialization_alias="createdAt",
        default=None,
    )
    tags: list[str] = Field(default_factory=list)
    class_: str | None = Field(
        validation_alias="class",
        serialization_alias="class",
        description="Reserved-word field name.",
        default=None,
    )
```

Cinco coisas acontecendo aí:

- **Nomes pythônicos + alias de rede.** `emailAddress` → `email_address`, com
  o nome de rede preservado em `validation_alias` **e** `serialization_alias`.
  `populate_by_name=True` faz o schema aceitar **os dois** na entrada;
  `model_dump(by_alias=True)` devolve a forma da rede.
- **Palavra reservada resolvida.** `class` → `class_`, aliases intactos.

!!! tip "Dois aliases, nunca `alias=`"
    O gerador escreve o nome do fio duas vezes — `validation_alias` para ler,
    `serialization_alias` para escrever — e nunca o `alias` único. A diferença
    não aparece em runtime, e aparece no editor de quem consome: com `alias`,
    o pyright renomeia o parâmetro do `__init__` e rejeita
    `UserSchema(email_address=...)` pedindo `emailAddress`.
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

from src.integrations.billing import CustomerStatus, TerceiroClient

terceiro = TerceiroClient(HTTPClient(base_url="https://api.parceiro.com"))


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

### Header declarado vira argumento da chamada

Header que a spec declara **na operação** é valor por requisição, então o
gerador o emite como argumento keyword-only — não como header default do
cliente:

```python
import uuid

from src.integrations.terceiro import TerceiroClient
from src.integrations.terceiro.schemas import PaymentRequest


async def cobrar(client: TerceiroClient) -> None:
    """Charge once, with a key that makes the retry safe.

    Args:
        client (TerceiroClient): The generated client.
    """
    await client.create_payment(
        body=PaymentRequest(transaction_amount=19.9),
        x_idempotency_key=uuid.uuid4(),
    )
```

!!! danger "Por que não `default_headers`"
    Antes o gerador descartava esses parâmetros com a nota "passe via
    `HTTPClient default_headers`". Para a maioria dos headers isso seria só
    inconveniente; para uma **chave de idempotência** é defeito: o
    `default_headers` manda o mesmo valor em toda requisição, então a segunda
    cobrança seria deduplicada em cima da primeira e o cliente veria um
    pagamento onde fez dois.

    Header que vale para a conexão inteira — `Authorization`, um
    `x-platform-id` fixo do seu app — continua cabendo em `default_headers`.
    A diferença é quem decide: agora você escolhe por chamada, em vez de o
    gerador escolher por você.

!!! tip "`None` não manda header vazio"
    Header opcional que você não passa simplesmente não vai no fio. Um
    `X-Idempotency-Key: ` vazio não é o mesmo que ausente — provedor que
    valida o header responderia 400 em toda chamada que não optou por ele.

    `cookie` continua sendo a única localização descartada, com nota: cookie
    é estado de conexão, não valor de chamada.

## Opções

| Opção | Efeito |
| --- | --- |
| `<spec>` (argumento) | URL (`http(s)://`) ou caminho da especificação |
| `--name` / `-n` | Nome da integração — vira o diretório e o prefixo da classe. Default: `info.title` slugificado |
| `--out` / `-o` | Destino. Default: ``<src|app>/integrations/<name>/`` |
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
| `required` | Campo sem default; ausente → ``X | None = None`` |
| `string`/`integer`/`number`/`boolean` | `str`/`int`/`float`/`bool` |
| `format: date-time`/`date`/`time` | `datetime`/`date`/`time` |
| `format: uuid`/`email`/`binary`/`decimal` | `UUID`/`EmailStr`/`bytes`/`Decimal` |
| `type: array` | `list[T]`; não obrigatório → `Field(default_factory=list)` |
| `enum` de strings / inteiros | Subclasse de `BaseStrEnum` / `BaseIntEnum` |
| `$ref` interno | Referência à classe gerada, ordenada por dependência |
| `allOf` | Achatado num único modelo |
| `oneOf` / `anyOf` | ``A | B``; com `discriminator`, `Annotated[..., Field(discriminator=...)]` |
| `nullable: true` (3.0) / `type: [x, "null"]` (3.1) | ``X | None`` |
| `additionalProperties` | `dict[str, T]` |
| `minLength` / `maximum` / `pattern` / `minItems` / … | Constraints no `Field` |
| Recursivo / mutuamente recursivo | Anotações adiadas + `model_rebuild()` no fim do módulo |
| Parâmetros de `path` e `query` | Argumentos tipados do método |
| Corpo e resposta `application/json` | Schema gerado |
| Resposta 204 / sem corpo | Retorno `None` |

E o que **não** é representado — sempre com uma linha no resumo do comando,
nunca em silêncio:

| Construção | Motivo |
| --- | --- |
| `not` | Sem equivalente em Python |
| `$ref` externo | Bundle a spec primeiro (`redocly bundle`) |
| Swagger 2.0 | Converta para OpenAPI 3 (`swagger2openapi`) |
| Parâmetros de `cookie` | Cookie é estado de conexão, não valor de chamada |
| Corpo/resposta não-JSON (`multipart`, `octet-stream`) | Fora do escopo desta iteração |
| `type` com múltiplos valores concretos | Não modelado |

!!! danger "Nunca chuta"
    O contrato do parser: o que ele não consegue representar como a spec
    escreveu vira uma linha no resumo, e **cada linha diz o que foi feito no
    lugar** — virou `Any`, foi descartado, foi sintetizado:

    ```text
    1 construct(s) could not be modelled as written — each line says what was
    generated instead, and the ones with something to mark carry an
    `# openapi: unsupported` comment in the output:
      - 'cookie' parameter 'sessionHint' skipped (pass it via HTTPClient default_headers)
    ```

    Um schema errado que **parece** certo é pior que uma lacuna documentada.

!!! check "A lacuna fica marcada no arquivo, não só no terminal"
    O resumo rola pra fora do terminal. Quem abrir `schemas.py` daqui a seis
    meses e encontrar um `Any` precisa do motivo **ao lado**, então o gerador
    escreve um comentário acima da linha afetada:

    ```python
    # openapi: unsupported — `not` in ThingWeird rendered as Any (no Python
    #   equivalent)
    weird: Any | None = None
    ```

    Vale para campo, para método (corpo `multipart`, resposta não modelada) e
    para parâmetro sintetizado. É greppável de propósito:
    `grep -rn "openapi: unsupported" src/integrations/` lista tudo que a
    integração perdeu. Uma lacuna sem nada no arquivo para marcar — um
    parâmetro de `cookie` descartado, por exemplo — continua só no resumo,
    porque não existe linha para comentar.

## O código gerado passa nos seus gates

O emissor produz código que passa `ruff check` e `ruff format --check` **antes**
de qualquer passada de formatação — anotações completas, docstrings Google em
todo módulo/classe/método, imports na ordem do isort, e aspas no estilo que o
`ruff format` normaliza (duplas, salvo o caso explicado
[logo abaixo](#o-texto-da-spec-nao-quebra-o-modulo)).

Isso é testado, não prometido: a suíte roda `ruff` contra a saída crua
(`--no-format`). Vale a pena saber por quê — foi essa asserção que pegou um
`UUID` não importado, um enum não importado, uma linha de docstring longa demais
e dois erros de ordenação de import que nenhuma asserção sobre o *shape* dos
schemas teria notado.

Então `--no-format` (ou uma máquina sem `ruff` instalado) ainda entrega um
pacote utilizável. A passada de `ruff` que o comando roda por padrão é polimento,
não correção.

### O texto da spec não quebra o módulo

A prosa da spec vai parar no código-fonte: em docstring, em `title`, em
`description`, em valor de enum. E o terceiro escreve o que quiser ali — aspas,
apóstrofo, barra invertida, quebra de linha vinda de um bloco YAML, uma frase
longa demais para caber na linha. Cada uma dessas já produziu um pacote que não
importava, não passava no lint, ou mudava em silêncio o que a spec dizia.

Veja num caso concreto. Esta propriedade tem quatro armadilhas ao mesmo tempo —
aspas no `title`, `\#` na descrição, texto longo demais para a linha, e um nome
de rede começando com dígito:

```json
{
  "Charge": {
    "type": "object",
    "required": ["reference"],
    "properties": {
      "reference": {
        "type": "string",
        "title": "O \"identificador\" do pagador",
        "description": "Codifique os caracteres (%, \\#, /) antes de enviar, porque o gateway rejeita a requisição e o erro devolvido não diz qual caractere causou."
      },
      "2fa": {"type": "boolean"}
    }
  }
}
```

E sai isto — código que passa no `ruff check` e no `ruff format --check` sem
nenhuma passada de formatação em cima:

```python
from pydantic import ConfigDict, Field

from tempest_fastapi_sdk import BaseSchema


class Charge(BaseSchema):
    r"""Schema generated for Charge.

    Attributes:
        reference (str): Codifique os caracteres (%, \#, /) antes de enviar, porque o
            gateway rejeita a requisição e o erro devolvido não diz qual caractere
            causou.
        field_2fa (bool | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    reference: str = Field(
        title='O "identificador" do pagador',
        description=(
            "Codifique os caracteres (%, \\#, /) antes de enviar, porque o gateway "
            "rejeita a requisição e o erro devolvido não diz qual caractere causou."
        ),
    )
    field_2fa: bool | None = Field(
        validation_alias="2fa",
        serialization_alias="2fa",
        default=None,
    )
```

Quatro decisões nessa saída, nenhuma óbvia:

1. **A docstring virou `r"""`.** `\#` não é escape de Python: sem o `r`, isso é
   `W605` no lint e `SyntaxWarning` a partir do 3.12.
2. **O `title` saiu com aspas simples**, apesar da regra de aspas duplas do
   projeto.
3. **A `description` foi partida em dois literais adjacentes**, e não deixada
   numa linha longa.
4. **`2fa` virou `field_2fa`**, com o nome do fio nos dois aliases — detalhado na
   [seção seguinte](#nomes-e-paths-que-a-spec-erra).

As duas do meio são a mesma causa, e vale entender:

!!! info "Por que aspas simples, se a regra do projeto é aspas duplas"
    Porque `ruff format` normaliza para o que **escapa menos**: texto com mais
    `"` que `'` sai com aspas simples. Emitir `title="O \"identificador\"..."`
    ali é código correto e legível — que falha o `ruff format --check` do
    consumidor na primeira rodada dele. O gerador não briga com o formatador do
    outro lado; ele reproduz a regra.

!!! warning "`ruff format` nunca quebra uma string"
    Uma `description` longa demais **sobrevive intacta** à passada de format e
    estoura o `E501` de quem consome. Então o emissor parte — e parte em **dois
    ou mais** pedaços, porque um literal solto entre parênteses o `ruff format`
    junta de volta na linha longa. Partir em um pedaço não é partir.

    O texto volta caractere a caractere: concatenar os literais emitidos devolve
    a descrição original, espaços incluídos. Nada é resumido nem truncado.

Resumindo o que o emissor garante para qualquer texto que a spec traga:

| Na spec | No código gerado |
| --- | --- |
| Barra invertida (`\#`, `\b`, `\x41`) | Escapada no literal, e a docstring vira `r"""` |
| Quebra de linha, tab, caractere de controle | `\n` / `\t` / `\xNN` no literal |
| `"aspas"` no texto | Literal com aspas **simples** |
| Descrição longa demais | Partida em dois ou mais literais adjacentes |
| Valor de enum longo demais | Partido igual, e o **nome** do membro é encurtado — o valor, nunca |

### Nomes longos e o orçamento de linha

O gerador **sintetiza** o nome de classe de um schema inline concatenando o
caminho inteiro — `PostApiV1DecodeEmvResponseEmvMerchantAccountInformationPix`.
Nada na spec limita esse tamanho, então a anotação sozinha pode estourar a
linha antes de qualquer argumento.

Isso não é cosmético. Quando a linha `nome: Anotação = Field(` estoura, o
`ruff format` embrulha a atribuição e **reindenta os argumentos um nível mais
fundo** — e cada string que o emissor tinha partido para caber em 88 sai com
92. Um defeito, dois sintomas.

O emissor escolhe a mesma forma que o `ruff format` escolheria, na mesma ordem
em que ele tenta:

| Situação | Forma emitida |
| --- | --- |
| Cabeçalho cabe | `x: T = Field(` com argumentos em 8 |
| Cabeçalho estoura, atribuição cabe | `x: T = (` / `Field(` com argumentos em 12 |
| Nenhum cabe | Anotação quebrada, argumentos de volta em 8 |
| Anotação é um subscript inteiro | Quebra **dentro** dos colchetes: `list[` / `Item` / `]` |

Nomes de classe sintetizados são limitados a 55 caracteres. O que aperta não é
a linha do `class`, e sim a entrada do `Attributes:` na docstring: ali a
anotação se compõe em volta do nome (`dict[str, Nome] | None` custa mais 18
colunas num indent de 12) e o `ruff format` não quebra nem uma coisa nem
outra. Medido na spec da OpenPix vendorizada: 8 de 373 nomes truncados, nenhuma colisão
nova.

!!! warning "Um caso não tem solução, e a doc não finge que tem"
    Nome de campo muito longo ao lado de uma anotação que é **um identificador
    único** — `x: NomeDeClasseLongo = Field(` — não tem formatação que caiba. O
    `ruff format` colapsa, porque um identificador solto não tem onde quebrar.
    Só encurtar o nome resolve. Toda anotação que contenha uma união ou um
    subscript **tem** forma estável, e é a que o emissor produz.

### Nomes e paths que a spec erra

Os mesmos testes cobrem o outro lado — quando a spec nomeia algo que Python não
aceita, ou descreve um path que não fecha com os parâmetros que declara:

| Na spec | No código gerado |
| --- | --- |
| Propriedade `2fa` | `field_2fa`, nome do fio nos dois aliases |
| `transaction` e `Transaction` juntos | `Transaction` e `Transaction2` |
| Parâmetro `path` que o template não interpola | Descartado, com nota |
| Placeholder que nenhum parâmetro declara | Sintetizado como `str` obrigatório, com nota |
| Parâmetros de path fora de ordem | Reordenados pela posição no template |

!!! tip "As escolhas menos óbvias"
    O prefixo é `field_`, e não `_`: sublinhado inicial faz o Pydantic tratar o
    atributo como **privado**, então o campo sumiria do modelo em vez de só
    mudar de nome. `Transaction_2` não é CapWords e falha o `N801` do
    consumidor. Um parâmetro que a requisição nunca carrega é pior que a
    ausência dele: quem chama passa um identificador e ele é jogado fora
    silenciosamente. E um placeholder sem declaração **não** pode ser
    ignorado — o path é uma f-string, então o módulo referenciaria um nome
    inexistente e nem importaria.

!!! danger "Toda correção dessas aparece no resumo"
    Descartar um parâmetro e sintetizar outro são decisões sobre a assinatura
    que **você** vai chamar, então elas saem no resumo do comando:

    ```text
    2 construct(s) could not be modelled (rendered as Any, marked in the output):
      - path parameter 'expand' of '/accounts/{accountId}' is declared but absent
        from the path template — skipped, since the value would never reach the request
      - path '/receipts/{receiptId}' interpolates 'receiptId', which no parameter
        declares — generated as a required str
    ```

    O parâmetro sintetizado também sai marcado no `client.py`, acima do método
    — quem descartamos não sai, porque não sobrou linha para comentar.

## Recapitulando

1. **`tempest openapi-client <spec> --name X`** gera
   `src/integrations/x/` com `schemas.py` + `client.py`.
2. **Nomes pythônicos com os dois aliases** para o nome de rede, e `populate_by_name`
   para aceitar os dois na entrada.
3. **Metadados da spec preenchidos** em todo `Field` — o módulo gerado é a
   documentação da integração. Nada é inventado.
4. **O cliente recebe um `HTTPClient` injetado**, então retry / circuit breaker /
   credenciais continuam seus, e `httpx.MockTransport` testa tudo sem rede.
5. **`--force` regenera**, e como uma spec inalterada gera arquivo idêntico, o
   diff mostra exatamente o que o terceiro mudou.
6. **A prosa da spec não quebra o módulo** — aspas, `\#`, quebra de linha e
   texto longo demais saem como literais válidos que passam no `ruff format
   --check` do seu lado, com o texto intacto.
7. **Nome ou path que a spec erra é corrigido, nunca chutado** — e a correção
   sai no resumo do comando.
8. **O que não é suportado vira uma linha no resumo** e um comentário
   `# openapi: unsupported` no arquivo, nunca silêncio.

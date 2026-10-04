# Agentes de IA (ferramentas)

Quase toda ferramenta que um serviço escreve cai numa de três famílias:

| A ferramenta… | Exemplo | Onde |
| --- | --- | --- |
| consulta o banco da aplicação | "quais serviços existem em Picos?" | [Agentes de IA (banco de dados)](agents-db.md) |
| chama uma API de fora | "de onde é o CEP 64600-000?" | [esta página](#ferramenta-que-chama-uma-api) |
| transforma texto livre num objeto | "tire o contato desta mensagem" | [esta página](#ferramenta-que-estrutura-texto) |

A primeira já tem página própria. Esta cobre as outras duas, e os três jeitos
de construir uma ferramenta que elas usam: `@tool`, `typed_tool` e `text_tool`.

!!! tip "Antes desta página"
    [Agentes de IA](agents.md) até *Escrever a sua própria ferramenta*. Todo
    exemplo daqui roda **offline**: o modelo é o `ScriptedBackend` de
    [Agentes de IA (testes)](agents-testing.md) e a API externa é um
    `httpx.MockTransport`. A saída mostrada é a que cada arquivo imprime.

## Ferramenta que chama uma API

Uma consulta de CEP: o modelo passa os oito dígitos, a ferramenta chama o
serviço de CEP e devolve cidade e estado.

```python title="cep_tool.py" hl_lines="61 63 64 92 99 101 107"
import asyncio

import httpx
from pydantic import Field

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBudget,
    AgentContext,
    AgentToolError,
    tool,
)
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
    tool_steps,
)
from tempest_fastapi_sdk.schemas import BaseSchema

CEP_TIMEOUT_SECONDS: float = 5.0
AGENT_BUDGET: AgentBudget = AgentBudget(max_steps=12, max_seconds=30.0)


def fake_cep_api(request: httpx.Request) -> httpx.Response:
    """Stand in for the real CEP service, so this file runs offline.

    Args:
        request (httpx.Request): The request the tool sent.

    Returns:
        httpx.Response: A canned answer chosen by the CEP in the path.

    Raises:
        httpx.ReadTimeout: For the CEP that simulates a slow upstream.
    """
    cep = request.url.path.rsplit("/", 1)[-1]
    if cep == "99999999":
        raise httpx.ReadTimeout("timed out", request=request)
    if cep == "50000000":
        return httpx.Response(503, text="upstream unavailable")
    if cep != "64600000":
        return httpx.Response(404, json={"erro": True})
    return httpx.Response(
        200,
        json={
            "cep": "64600-000",
            "logradouro": "",
            "bairro": "",
            "localidade": "Picos",
            "uf": "PI",
            "estado": "Piauí",
            "regiao": "Nordeste",
            "ibge": "2208007",
            "ddd": "89",
            "siafi": "1145",
        },
    )


http_client: httpx.AsyncClient = httpx.AsyncClient(
    base_url="https://cep.example.com",
    timeout=CEP_TIMEOUT_SECONDS,
    transport=httpx.MockTransport(fake_cep_api),
)


class CepArgs(BaseSchema):
    """Arguments for the postal-code lookup."""

    cep: str = Field(description="CEP com 8 dígitos, só números.", pattern=r"^\d{8}$")


@tool("lookup_cep", "Look up the city and state of a Brazilian postal code (CEP).")
async def lookup_cep(args: CepArgs, context: AgentContext) -> str:
    """Call the CEP service and keep only what the model needs.

    Args:
        args (CepArgs): The validated CEP.
        context (AgentContext): The run context (unused here).

    Returns:
        str: One line with the city and the state.

    Raises:
        AgentToolError: For every failure the model can act on — an unknown
            CEP, a slow upstream or an upstream error — with a message
            written for it to read.
    """
    try:
        response = await http_client.get(f"/cep/{args.cep}")
    except httpx.TimeoutException as exc:
        raise AgentToolError(
            f"o serviço de CEP não respondeu em {CEP_TIMEOUT_SECONDS:.0f} s; "
            "tente de novo mais tarde",
        ) from exc
    except httpx.TransportError as exc:
        raise AgentToolError("o serviço de CEP está fora do ar") from exc
    if response.status_code == 404:
        raise AgentToolError(f"o CEP {args.cep} não existe; confira os dígitos")
    if response.is_error:
        raise AgentToolError(
            f"o serviço de CEP falhou (HTTP {response.status_code}); "
            "tente de novo mais tarde",
        )
    data = response.json()
    return f"CEP {data['cep']}: {data['localidade']}/{data['uf']}"


async def main() -> None:
    """Script five calls — one success, four failures — and print the trace."""
    backend = ScriptedBackend(
        [
            replies_with_tool("lookup_cep", {"cep": "646"}),
            replies_with_tool("lookup_cep", {"cep": "00000000"}),
            replies_with_tool("lookup_cep", {"cep": "99999999"}),
            replies_with_tool("lookup_cep", {"cep": "50000000"}),
            replies_with_tool("lookup_cep", {"cep": "64600000"}),
            replies("O CEP 64600-000 é de Picos/PI."),
        ],
    )
    agent = Agent(backend, tools=[lookup_cep], budget=AGENT_BUDGET)
    run = await agent.run("De onde é o CEP 64600-000?")

    for step in tool_steps(run):
        print(step.arguments["cep"], "->", step.error or step.output)
    print(run.stop_reason, "|", run.output)
    await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python cep_tool.py
```

```text
646 -> AgentToolError: invalid arguments for lookup_cep: cep: String should match pattern '^\d{8}$'
00000000 -> AgentToolError: o CEP 00000000 não existe; confira os dígitos
99999999 -> AgentToolError: o serviço de CEP não respondeu em 5 s; tente de novo mais tarde
50000000 -> AgentToolError: o serviço de CEP falhou (HTTP 503); tente de novo mais tarde
64600000 -> CEP 64600-000: Picos/PI
completed | O CEP 64600-000 é de Picos/PI.
```

O modelo pediu cinco vezes; quatro deram errado, cada uma de um jeito, e a
quinta respondeu. Pedaço por pedaço:

**O cliente é do processo.** `http_client` é criado uma vez, no import, e toda
chamada da ferramenta reusa o mesmo pool de conexões. Num serviço ele mora ao
lado do gerador, em `runtime.py`, e é fechado no lifespan; aqui o
`await http_client.aclose()` no fim do `main` faz esse papel. Criar um
`AsyncClient` dentro da ferramenta abre conexão nova a cada chamada do modelo.

**`transport=` é a única linha offline.** `httpx.MockTransport(fake_cep_api)`
troca a rede por uma função. Em produção, tire o `transport=` e aponte
`base_url` para o serviço de verdade; o resto do arquivo não muda.

**O timeout do cliente fica abaixo do orçamento.** `CEP_TIMEOUT_SECONDS` (5 s)
é menor que o `max_seconds` do `AGENT_BUDGET` (30 s). A diferença decide quem
corta a chamada lenta — e [a seção seguinte](#o-timeout-do-cliente-fica-abaixo-do-orcamento)
mostra o que acontece quando é o orçamento.

**Toda falha vira `AgentToolError`, escrita para o modelo ler.** A mensagem
diz o que aconteceu **e** o que fazer: conferir os dígitos, tentar mais tarde.
É ela que o modelo lê como observação, e é ela que o traço grava. O CEP
`646` nem chegou a fazer requisição: o `pattern` do `CepArgs` reprovou o
argumento antes do handler rodar.

**Devolva pouco.** O serviço respondeu dez campos; a ferramenta devolve uma
linha com dois. Todo texto que a ferramenta devolve volta ao modelo em todas
as voltas seguintes da execução
([o contexto cresce a cada volta](agents-concepts.md#o-contexto-cresce-a-cada-volta-e-e-voce-que-paga)).

### O timeout do cliente fica abaixo do orçamento

Agora o contrário: um serviço que demora 5 s para responder, um cliente com
timeout de 10 s e uma execução com `max_seconds=1.0`.

```python title="cep_budget.py" hl_lines="31 62"
import asyncio

import httpx

from tempest_fastapi_sdk.agents import Agent, AgentBudget, AgentContext, tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
    tool_steps,
)

from cep_tool import CepArgs


async def slow_cep_api(request: httpx.Request) -> httpx.Response:
    """Answer only after five seconds, like an upstream having a bad day.

    Args:
        request (httpx.Request): The request the tool sent.

    Returns:
        httpx.Response: An empty JSON answer, too late to matter.
    """
    await asyncio.sleep(5)
    return httpx.Response(200, json={})


slow_client: httpx.AsyncClient = httpx.AsyncClient(
    base_url="https://cep.example.com",
    timeout=10.0,
    transport=httpx.MockTransport(slow_cep_api),
)


@tool("lookup_cep", "Look up the city and state of a Brazilian postal code (CEP).")
async def lookup_cep_slow(args: CepArgs, context: AgentContext) -> str:
    """Call the slow upstream with a timeout longer than the run's budget.

    Args:
        args (CepArgs): The validated CEP.
        context (AgentContext): The run context (unused here).

    Returns:
        str: The response body.
    """
    response = await slow_client.get(f"/cep/{args.cep}")
    return response.text


async def main() -> None:
    """Let the budget, not the client, end the call."""
    backend = ScriptedBackend(
        [
            replies_with_tool("lookup_cep", {"cep": "64600000"}),
            replies("Não consegui consultar o CEP agora."),
        ],
    )
    agent = Agent(
        backend,
        tools=[lookup_cep_slow],
        budget=AgentBudget(max_seconds=1.0),
    )
    run = await agent.run("De onde é o CEP 64600-000?")

    print(run.stop_reason, "|", tool_steps(run)[0].error)
    print("chamadas ao modelo:", backend.calls)
    await slow_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```text
timeout | tool 'lookup_cep' interrupted: the time budget ran out
chamadas ao modelo: 1
```

Quem cortou foi o orçamento: a execução terminou em `timeout` e o modelo foi
chamado **uma** vez só — ele nunca leu observação nenhuma, nunca teve chance
de responder "o serviço está lento". No `cep_tool.py`, com o timeout do
cliente abaixo do orçamento, o CEP `99999999` voltou como observação
(`não respondeu em 5 s`) e a execução seguiu até `completed`.

!!! info "O que o `MockTransport` simula ali"
    `fake_cep_api` levanta `httpx.ReadTimeout` para o CEP `99999999`. É a
    exceção que o transporte real levanta: medido com um servidor local que
    aceita a conexão e nunca responde, um `AsyncClient(timeout=0.2)` levantou
    `ReadTimeout`, subclasse de `httpx.TimeoutException` — a classe que a
    ferramenta captura.

!!! tip "Retry também gasta o relógio da execução"
    O [`HTTPClient`](http-client.md) do SDK faz retry com backoff e
    circuit-breaker, e aceita `transport=` como o `httpx.AsyncClient`. Dentro
    de uma ferramenta, toda tentativa corre sob o mesmo orçamento da execução:
    três tentativas de 5 s cada, mais o backoff, precisam caber no
    `max_seconds`.

### Por que traduzir a exceção

O atalho é chamar `raise_for_status()` e deixar a exceção do `httpx` subir:

```python title="cep_raw.py" hl_lines="34 35 49 50"
import asyncio

import httpx

from tempest_fastapi_sdk.agents import Agent, AgentContext, tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
    tool_steps,
)

from cep_tool import CepArgs

API_KEY: str = "s3cr3t"

raw_client: httpx.AsyncClient = httpx.AsyncClient(
    base_url="https://cep.example.com",
    transport=httpx.MockTransport(lambda request: httpx.Response(503)),
)


@tool("lookup_cep", "Look up the city and state of a Brazilian postal code (CEP).")
async def lookup_cep_raw(args: CepArgs, context: AgentContext) -> str:
    """Call the CEP service and let any failure escape untranslated.

    Args:
        args (CepArgs): The validated CEP.
        context (AgentContext): The run context (unused here).

    Returns:
        str: The raw response body.
    """
    response = await raw_client.get(f"/cep/{args.cep}", params={"apikey": API_KEY})
    response.raise_for_status()
    return response.text


async def main() -> None:
    """Show what the trace and the model get from an untranslated exception."""
    backend = ScriptedBackend(
        [
            replies_with_tool("lookup_cep", {"cep": "64600000"}),
            replies("Não consegui consultar o CEP agora."),
        ],
    )
    run = await Agent(backend, tools=[lookup_cep_raw]).run("De onde é o CEP 64600-000?")

    print(tool_steps(run)[0].error)
    print(backend.messages_seen[1][-1]["content"])
    await raw_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```text
HTTPStatusError: the tool failed (details withheld)
tool failed: HTTPStatusError
```

A primeira linha é o traço; a segunda, a observação que o modelo leu na volta
seguinte, tirada de
[`messages_seen`](agents-testing.md#testar-o-que-a-ferramenta-pos-na-frente-do-modelo).
Exceção que não é `AgentToolError` não leva o texto para nenhum dos dois: o
modelo lê só o tipo, o traço guarda só o tipo, e a exceção inteira vai para o
log (`tempest_fastapi_sdk.agents.agent`). O texto dela era este:

```text
HTTPStatusError: Server error '503 Service Unavailable' for url 'https://cep.example.com/cep/64600000?apikey=s3cr3t'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
```

A chave da API está na URL. Até a v0.303.2 esse texto inteiro chegava ao
modelo — o traço já o retinha, a conversa não — e o modelo pode repetir na
resposta o que leu. Agora o SDK retém nos dois lugares por default.

**Traduzir ainda vale a pena**, pelo outro motivo: `tool failed:
HTTPStatusError` não diz ao modelo o que mudar. Um 404 ("esse CEP não
existe; confira os dígitos") e um 503 ("o serviço caiu; tente mais tarde")
pedem respostas diferentes, e só a frase do `AgentToolError` do `cep_tool.py`
dá isso a ele — é ela que o modelo lê, e é ela que o traço grava. Traduza o
que o modelo pode resolver; o resto pode subir cru.

!!! warning "`expose_tool_errors=True` é para desenvolvimento"
    `Agent(..., expose_tool_errors=True)` entrega o texto da exceção ao modelo
    **e** ao traço. Antes, o SDK mascara as formas óbvias de credencial —
    parâmetro cujo nome contém `key`, `token`, `secret` ou `password`
    (`?apikey=***`), o valor de `Authorization` e a senha de uma URL
    (`postgresql://admin:***@db`). Medido com o `raw_client` acima respondendo
    `401`:

    ```text
    HTTPStatusError: Client error '401 Unauthorized' for url 'https://cep.example.com/cep/64600000?apikey=***'
    ```

    A máscara só conhece essas formas: segredo em qualquer outro formato passa
    intacto. É defesa adicional para quando você ligou o opt-in, não motivo
    para ligá-lo num endpoint aberto.

O mecanismo completo está em
[Falha de ferramenta não derruba a execução](agents.md#falha-de-ferramenta-nao-derruba-a-execucao).

### Cliente injetado: `typed_tool` sobre método ligado

`@tool` decora uma função de módulo, então a ferramenta acima lê o cliente de
uma variável global. Para testar com outro transporte sem mexer em global, o
cliente vira parâmetro de uma classe, e a ferramenta é o **método ligado** —
que o decorator não alcança, mas `typed_tool` sim, recebendo o modelo de
argumentos explicitamente:

```python title="cep_service.py"
import httpx

from tempest_fastapi_sdk.agents import AgentContext, AgentToolError

from cep_tool import CepArgs


class CepService:
    """The CEP lookup, around an HTTP client it receives instead of creating."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        """Store the client.

        Args:
            client (httpx.AsyncClient): The process-wide client in production,
                one over ``httpx.MockTransport`` in a test.
        """
        self.client: httpx.AsyncClient = client

    async def lookup(self, args: CepArgs, context: AgentContext) -> str:
        """Call the CEP service and keep only what the model needs.

        Args:
            args (CepArgs): The validated CEP.
            context (AgentContext): The run context (unused here).

        Returns:
            str: One line with the city and the state.

        Raises:
            AgentToolError: For an unknown CEP, a slow upstream or an
                upstream error, with a message written for the model.
        """
        try:
            response = await self.client.get(f"/cep/{args.cep}")
        except httpx.TimeoutException as exc:
            raise AgentToolError(
                "o serviço de CEP não respondeu; tente de novo mais tarde",
            ) from exc
        if response.status_code == 404:
            raise AgentToolError(f"o CEP {args.cep} não existe; confira os dígitos")
        if response.is_error:
            raise AgentToolError(
                f"o serviço de CEP falhou (HTTP {response.status_code})",
            )
        data = response.json()
        return f"CEP {data['cep']}: {data['localidade']}/{data['uf']}"
```

```python title="test_cep_service.py" hl_lines="20 26 30"
import httpx
import pytest

from tempest_fastapi_sdk.agents import Agent, typed_tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    assert_completed,
    failed_steps,
    replies,
    replies_with_tool,
)

from cep_service import CepService
from cep_tool import CepArgs


@pytest.mark.asyncio
async def test_an_unknown_cep_reaches_the_model_as_a_readable_error() -> None:
    """A 404 upstream must become an error the model can act on."""
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with httpx.AsyncClient(
        base_url="https://cep.example.com",
        transport=transport,
    ) as client:
        service = CepService(client)
        lookup = typed_tool(
            "lookup_cep",
            "Look up the city and state of a Brazilian postal code (CEP).",
            CepArgs,
            service.lookup,
        )
        backend = ScriptedBackend(
            [
                replies_with_tool("lookup_cep", {"cep": "00000000"}),
                replies("Esse CEP não existe."),
            ],
        )
        run = await Agent(backend, tools=[lookup]).run("De onde é o CEP 00000000?")

    assert_completed(run)
    assert failed_steps(run)[0].error == (
        "AgentToolError: o CEP 00000000 não existe; confira os dígitos"
    )
```

```bash
pytest test_cep_service.py -q
```

```text
.                                                                        [100%]
1 passed in 0.51s
```

`typed_tool("lookup_cep", ..., CepArgs, service.lookup)` gera o mesmo schema e
a mesma validação que o `@tool`: o handler recebe um `CepArgs` já validado. Em
produção, o `CepService` recebe o cliente do processo e a ferramenta é montada
onde o agente é composto (`src/ai/agents/`, na
[arquitetura](agents-architecture.md#agents-so-composicao)).

## Ferramenta que estrutura texto

O outro caso comum: uma mensagem em texto livre entra, um objeto sai. "Oi,
aqui é a Ana, de Picos, preciso de um eletricista" vira um lead com nome,
cidade e interesse. O truque é que **não existe parse de texto**: os
argumentos da ferramenta *são* a estrutura, e o modelo os preenche como
preenche os de qualquer ferramenta.

### O schema que o modelo vê: `schema_of`

```python title="lead_schema.py"
import json

from pydantic import Field

from tempest_fastapi_sdk.agents import schema_of
from tempest_fastapi_sdk.schemas import BaseSchema


class AddressSchema(BaseSchema):
    """Where the lead is."""

    city: str = Field(description="Cidade.")
    state: str = Field(description="UF com duas letras maiúsculas.", pattern=r"^[A-Z]{2}$")


class LeadSchema(BaseSchema):
    """A sales lead read out of a free-text message."""

    name: str = Field(description="Nome da pessoa.")
    email: str | None = Field(default=None, description="E-mail, só se aparecer no texto.")
    address: AddressSchema
    interests: list[str] = Field(
        default_factory=list,
        description="Serviços que a pessoa citou.",
    )


if __name__ == "__main__":
    print(sorted(LeadSchema.model_json_schema()))
    print(json.dumps(schema_of(LeadSchema), ensure_ascii=False, indent=2))
```

```text
['$defs', 'description', 'properties', 'required', 'title', 'type']
{
  "description": "A sales lead read out of a free-text message.",
  "properties": {
    "name": {
      "description": "Nome da pessoa.",
      "title": "Name",
      "type": "string"
    },
    "email": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "E-mail, só se aparecer no texto.",
      "title": "Email"
    },
    "address": {
      "description": "Where the lead is.",
      "properties": {
        "city": {
          "description": "Cidade.",
          "title": "City",
          "type": "string"
        },
        "state": {
          "description": "UF com duas letras maiúsculas.",
          "pattern": "^[A-Z]{2}$",
          "title": "State",
          "type": "string"
        }
      },
      "required": [
        "city",
        "state"
      ],
      "title": "AddressSchema",
      "type": "object"
    },
    "interests": {
      "description": "Serviços que a pessoa citou.",
      "items": {
        "type": "string"
      },
      "title": "Interests",
      "type": "array"
    }
  },
  "required": [
    "name",
    "address"
  ],
  "type": "object"
}
```

A primeira linha é o JSON-schema cru do Pydantic: o `AddressSchema` aninhado
fica em `$defs`, referenciado por `$ref`. `schema_of` — o que `@tool`,
`typed_tool` e `final_answer_tool` usam por baixo — **embute** a definição no
lugar da referência e tira o `title` do topo; o resultado é um objeto plano,
sem `$defs`. A docstring do `AddressSchema` virou a `description` do campo
`address`, e o `pattern` de `state` foi junto: é com isso que o modelo
preenche.

### Terminar a execução com o objeto: `final_answer_tool`

```python title="lead_extract.py" hl_lines="24 39 48"
import asyncio

from tempest_fastapi_sdk.agents import Agent, AgentContext, final_answer_tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies_with_tool,
    tool_steps,
)

from lead_schema import LeadSchema

MESSAGE: str = (
    "Oi, aqui é a Ana Souza, de Picos (PI). Preciso de um eletricista e de "
    "um encanador ainda este mês. Meu e-mail é ana@example.com."
)


async def main() -> None:
    """Extract a lead: one invalid answer, corrected on the next turn."""
    backend = ScriptedBackend(
        [
            replies_with_tool(
                "final_answer",
                {"name": "Ana Souza", "address": {"city": "Picos", "state": "Piauí"}},
            ),
            replies_with_tool(
                "final_answer",
                {
                    "name": "Ana Souza",
                    "email": "ana@example.com",
                    "address": {"city": "Picos", "state": "PI"},
                    "interests": ["eletricista", "encanador"],
                },
            ),
        ],
    )
    agent = Agent(
        backend,
        tools=[final_answer_tool(LeadSchema)],
        system_prompt="Extraia o contato da mensagem chamando final_answer.",
    )
    context = AgentContext()
    run = await agent.run(MESSAGE, context=context)

    for step in tool_steps(run):
        print(step.error or "ok")
    print(run.stop_reason, "| chamadas ao modelo:", backend.calls)
    print(repr(context.answer))


if __name__ == "__main__":
    asyncio.run(main())
```

```text
AgentToolError: invalid answer for final_answer: address.state: String should match pattern '^[A-Z]{2}$'
ok
completed | chamadas ao modelo: 2
LeadSchema(name='Ana Souza', email='ana@example.com', address=AddressSchema(city='Picos', state='PI'), interests=['eletricista', 'encanador'])
```

* **A primeira resposta não validou** (`state` com `Piauí`, contra o padrão de
  duas letras). Virou observação, o modelo corrigiu na volta seguinte.
* **A chamada válida encerrou a execução.** Duas chamadas ao modelo, não
  três: `final_answer_tool` devolve um `ToolResult` com `final=True`, e o
  agente para ali, em `completed`, sem pedir ao modelo uma resposta em prosa.
* **O objeto validado fica em `context.answer`**; `run.output` é o mesmo
  objeto em JSON.

!!! tip "`run_structured` faz isso por você"
    `agent.run_structured(goal, LeadSchema)` adiciona o `final_answer`, anexa
    ao prompt a instrução de terminar por ele e tenta recuperar a resposta
    quando o modelo responde em prosa
    ([Saída estruturada](agents-advanced.md#saida-estruturada-um-objeto-nao-um-paragrafo)).
    Use `final_answer_tool` direto quando quiser controlar o prompt inteiro,
    ou quando só um agente de um fluxo com vários entrega estrutura.

### Quando o objeto vai para algum lugar: `ToolResult(final=True)`

Se extrair é só o meio — o lead precisa ser **salvo** —, a própria ferramenta
de destino pode encerrar a execução:

```python title="lead_save.py" hl_lines="34 40"
import asyncio

from tempest_fastapi_sdk.agents import Agent, AgentContext, ToolResult, tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies_with_tools,
    tool_call,
)

from lead_schema import LeadSchema

SAVED: list[LeadSchema] = []

LEAD: dict[str, object] = {
    "name": "Ana Souza",
    "address": {"city": "Picos", "state": "PI"},
    "interests": ["eletricista"],
}


@tool("save_lead", "Save the contact found in the message. Call it once, at the end.")
async def save_lead(args: LeadSchema, context: AgentContext) -> ToolResult:
    """Store the validated lead and end the run with a receipt.

    Args:
        args (LeadSchema): The lead, already validated.
        context (AgentContext): The run context (unused here).

    Returns:
        ToolResult: The receipt, with ``final=True`` so no further model
        turn is spent.
    """
    SAVED.append(args)
    return ToolResult(text=f"lead {len(SAVED)} salvo: {args.name}", final=True)


async def main() -> None:
    """The model asks for two saves in one turn; only the first runs."""
    backend = ScriptedBackend(
        [replies_with_tools(tool_call("save_lead", LEAD), tool_call("save_lead", LEAD))],
    )
    run = await Agent(backend, tools=[save_lead]).run("Oi, aqui é a Ana, de Picos.")

    print(run.stop_reason, "|", run.output)
    print("salvos:", len(SAVED), "| chamadas ao modelo:", backend.calls)


if __name__ == "__main__":
    asyncio.run(main())
```

```text
completed | lead 1 salvo: Ana Souza
salvos: 1 | chamadas ao modelo: 1
```

O modelo pediu `save_lead` **duas vezes** na mesma volta. A primeira devolveu
`final=True`, a execução terminou ali com o texto dela como resposta, e a
segunda chamada não rodou: um lead salvo, uma chamada ao modelo.

## `text_tool`: um argumento de texto

Para a ferramenta que recebe só um texto, `text_tool` dispensa o modelo de
argumentos e escreve o schema por você:

```python title="word_count.py"
import json
from typing import Any

from tempest_fastapi_sdk.agents import AgentContext, AgentTool, ToolReturn, text_tool


async def count_words(arguments: dict[str, Any], context: AgentContext) -> ToolReturn:
    """Count the words of the text the model passed.

    Args:
        arguments (dict[str, Any]): The raw arguments, with ``text``.
        context (AgentContext): The run context (unused here).

    Returns:
        ToolReturn: The count, as one line.
    """
    return f"{len(str(arguments['text']).split())} palavras"


word_count: AgentTool = text_tool(
    "count_words",
    "Count the words in a piece of text.",
    count_words,
)

if __name__ == "__main__":
    print(json.dumps(word_count.to_spec()["function"]["parameters"], indent=2))
```

```text
{
  "type": "object",
  "properties": {
    "text": {
      "type": "string",
      "description": "The input text."
    }
  },
  "required": [
    "text"
  ]
}
```

O handler recebe o `dict` cru (`arguments["text"]`), sem validação: é o
formato de `AgentTool`. `ToolReturn` é o tipo do que um handler devolve —
`ToolResult | str`; uma `str` vira `ToolResult(text=...)` sozinha. Quando a
ferramenta passa a ter mais de um argumento, `parameters=` aceita um schema à
mão, mas a essa altura `@tool` com um modelo Pydantic é o caminho que não
deixa schema e handler divergirem.

| Construtor | Argumentos | O handler recebe | Use quando |
| --- | --- | --- | --- |
| `@tool(name, description)` | modelo Pydantic na anotação | instância validada | a função é de módulo |
| `typed_tool(name, description, Model, handler)` | modelo Pydantic explícito | instância validada | o handler é método ligado, lambda ou vem de fora |
| `text_tool(name, description, handler)` | só `text` | `dict` cru | um texto entra, um texto sai |
| `AgentTool(...)` | JSON-schema à mão | `dict` cru | o schema não cabe num modelo Pydantic |

## Recapitulando

- **Banco tem página própria**: [Agentes de IA (banco de dados)](agents-db.md).
- **Ferramenta que chama API** usa o `httpx.AsyncClient` do processo, com
  timeout **abaixo** do `max_seconds` do orçamento — senão quem corta é o
  orçamento, e o modelo nunca lê o erro.
- **4xx, 5xx e timeout viram `AgentToolError`** com uma frase que diz o que
  fazer. Exceção crua chega ao modelo só como `tool failed: <Tipo>` — sem a
  URL e o que estiver nela, e sem dizer o que mudar.
- **Devolva pouco**: dez campos da API viram uma linha.
- **`typed_tool` sobre método ligado** deixa o cliente injetável, e o teste
  usa `httpx.MockTransport`.
- **Estruturar texto não é parse**: os argumentos da ferramenta são o objeto.
  `schema_of` achata o schema aninhado; `final_answer_tool` encerra a
  execução com o objeto validado em `context.answer`.
- **`ToolResult(final=True)`** encerra a execução numa ferramenta sua, sem
  volta extra ao modelo.
- **`text_tool`** para um texto só; `@tool` assim que houver mais argumentos.

Veja também: [Agentes de IA (prompts)](agents-prompts.md) para o *quando* de
cada ferramenta, que mora no prompt; [Agentes de IA (testes)](agents-testing.md)
para afirmar sobre o que a ferramenta fez; [HTTP client (saída)](http-client.md)
para retry e circuit-breaker.

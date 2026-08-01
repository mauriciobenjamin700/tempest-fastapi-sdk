# Testar e validar um agente

O comportamento de um agente depende do que o modelo decide, o que dá a
impressão de que não dá para testar: você não consegue afirmar nada sobre o
humor de um modelo de 0.5B.

Mas quase todo bug que importa está **no seu código**, não no do modelo — uma
ferramenta que trata mal um argumento, um orçamento que nunca dispara, uma
skill cujas ferramentas não destravam, uma delegação que perde os artefatos.
Tudo isso é testável: você **escreve o que o modelo decidiria** e afirma sobre
o que o seu agente fez com aquilo.

São os mesmos helpers que a suíte do próprio SDK usa nos 200+ testes de
agentes.

```python
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies
```

!!! info "Sem modelo, sem rede, sem extra"
    `tempest_fastapi_sdk.agents.testing` importa sem nenhuma dependência
    opcional. Um teste que sobe um modelo de verdade é lento, instável e
    testa a coisa errada.

## O teste mínimo

```python
import pytest

from tempest_fastapi_sdk.agents import Agent, AgentContext, tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    assert_completed,
    assert_used_tools,
    replies,
    replies_with_tool,
)
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherArgs(BaseSchema):
    """Arguments for the weather tool."""

    city: str


@tool("get_weather", "Get the current weather for a city.")
async def get_weather(args: WeatherArgs, context: AgentContext) -> str:
    """Return a canned forecast."""
    return f"{args.city}: 22 graus"


@pytest.mark.asyncio
async def test_the_agent_uses_the_weather_tool() -> None:
    """The agent should call the tool and answer from its result."""
    backend = ScriptedBackend(
        [
            replies_with_tool("get_weather", {"city": "Recife"}),
            replies("Está 22 graus no Recife."),
        ],
    )

    run = await Agent(backend, tools=[get_weather]).run("Qual o tempo no Recife?")

    assert_completed(run)
    assert_used_tools(run, "get_weather")
    assert run.output == "Está 22 graus no Recife."
```

Você declarou o plano do modelo — "chame `get_weather` com Recife, depois
responda" — e afirmou sobre o que o agente fez. Nenhum modelo carregado, e o
teste roda em milissegundos.

## O que afirmar

| Helper | Responde |
| --- | --- |
| `assert_completed(run)` | O modelo terminou por conta própria (não foi cortado por orçamento). |
| `assert_used_tools(run, "a", "b")` | Exatamente essas ferramentas, nessa ordem. |
| `assert_artifact(run, "chart.png", media_type="image/png")` | O artefato existe e é do tipo certo. |
| `tool_steps(run)` | Só os passos de ferramenta, para inspecionar argumentos. |
| `failed_steps(run)` | Os passos que erraram — inclusive numa execução bem-sucedida. |

!!! danger "O erro que quase todo teste comete"
    Afirmar só sobre `run.output`. Uma execução cortada por orçamento **também
    traz texto** — é a última coisa que o modelo disse. Um teste que checa só o
    texto passa com trabalho pela metade.

    Por isso `assert_completed` existe, e por isso ele nomeia o `stop_reason`
    quando falha:

    ```text
    AssertionError: run did not complete: stop_reason=max_steps, output='working on it'
    ```

## Testar a recuperação de erro

Uma ferramenta que levanta **não** deve derrubar a execução — o erro vira
observação e o modelo tenta outro caminho. Teste isso:

```python
import pytest

from tempest_fastapi_sdk.agents import Agent, AgentContext, AgentToolError, text_tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    failed_steps,
    replies,
    replies_with_tool,
)


@pytest.mark.asyncio
async def test_the_agent_recovers_from_a_failing_tool() -> None:
    """A raising tool becomes an observation, not a crashed run."""

    async def save(arguments: dict[str, str], context: AgentContext) -> str:
        """Always fail, to exercise the recovery path."""
        raise AgentToolError("disco cheio")

    backend = ScriptedBackend(
        [
            replies_with_tool("save", {"text": "x"}),
            replies("Não consegui salvar, mas segue o conteúdo."),
        ],
    )

    run = await Agent(backend, tools=[text_tool("save", "Save it.", save)]).run("salve")

    assert run.succeeded is True
    assert "disco cheio" in failed_steps(run)[0].error
```

## Testar o orçamento

`repeat_last=True` faz o modelo scriptado nunca parar de pedir ferramenta —
que é exatamente o cenário para o qual o teto existe:

```python
import pytest

from tempest_fastapi_sdk.agents import Agent, AgentBudget, AgentContext, StopReason, text_tool
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies_with_tool


@pytest.mark.asyncio
async def test_the_step_budget_stops_a_runaway_agent() -> None:
    """A model that never stops asking is what the ceiling is for."""

    async def noop(arguments: dict[str, str], context: AgentContext) -> str:
        """Do nothing, successfully."""
        return "ok"

    backend = ScriptedBackend(
        [replies_with_tool("t", {"text": "x"})],
        repeat_last=True,
    )

    run = await Agent(
        backend,
        tools=[text_tool("t", "T.", noop)],
        budget=AgentBudget(max_steps=4, max_seconds=None),
    ).run("faça para sempre")

    assert run.stop_reason == StopReason.MAX_STEPS
    assert run.succeeded is False
```

## Testar que a skill escondeu suas ferramentas

`backend.specs_seen` guarda os nomes oferecidos em **cada** turno, que é como
se prova o carregamento sob demanda:

```python
@pytest.mark.asyncio
async def test_skill_tools_are_hidden_until_loaded() -> None:
    """The skill's tools must not exist before load_skill runs."""
    backend = ScriptedBackend(
        [
            replies_with_tool("load_skill", {"name": "invoicing"}),
            replies_with_tool("parse_nfe", {"text": "123"}),
            replies("Pronto."),
        ],
    )

    run = await Agent(backend, skills=[invoicing]).run("leia a nota")

    assert "parse_nfe" not in backend.specs_seen[0]
    assert "parse_nfe" in backend.specs_seen[1]
```

## Testar que a memória chegou ao modelo

`backend.system_prompts` guarda o prompt de sistema de cada turno:

```python
@pytest.mark.asyncio
async def test_facts_reach_the_model() -> None:
    """Stored facts must be injected, not merely available."""
    store = InMemoryFactStore()
    await store.put("timezone", "America/Recife", subject="u1")

    backend = ScriptedBackend([replies("ok")])
    agent = Agent(
        backend,
        system_prompt="Base." + await facts_prompt(store, subject="u1"),
    )
    await agent.run("que horas são?")

    assert "timezone: America/Recife" in backend.system_prompts[0]
```

## Testar a queda do backend

```python
from tempest_fastapi_sdk.agents.testing import FailingBackend


@pytest.mark.asyncio
async def test_a_backend_outage_does_not_escape() -> None:
    """A dead model must become an ERROR stop, not an exception."""
    run = await Agent(FailingBackend("ollama fora do ar")).run("oi")

    assert run.stop_reason == StopReason.ERROR
    assert "ollama fora do ar" in run.output
```

Isso importa porque um agente costuma estar atrás de um endpoint: uma exceção
escapando vira 500, enquanto um `StopReason.ERROR` vira uma resposta que você
controla.

## O script sobrou?

```python
assert backend.exhausted is True
```

Um teste que escreve cinco turnos e usa dois normalmente afirma menos do que o
autor pensa — o agente parou antes e o resto do script nunca rodou.

## E o modelo de verdade?

Scripting não cobre uma pergunta: **o modelo escolhe a ferramenta certa?**
Isso só um modelo responde. Mantenha esses testes separados e marcados, fora
da suíte rápida:

```python
import pytest

from tempest_fastapi_sdk.agents import Agent, AgentBudget
from tempest_fastapi_sdk.genai import TextGenerator


@pytest.mark.model
@pytest.mark.asyncio
async def test_a_real_model_picks_the_weather_tool() -> None:
    """The model must reach for the tool when the goal calls for it."""
    generator = TextGenerator(
        "Qwen/Qwen2.5-0.5B-Instruct",
        device="cpu",
        local_files_only=True,
    )
    agent = Agent(
        generator,
        tools=[get_weather],
        budget=AgentBudget(max_steps=4, max_seconds=300),
    )

    run = await agent.run("Qual o tempo no Recife? Use a ferramenta.")

    assert "get_weather" in run.tool_calls
```

Registre o marcador no `pyproject.toml` e exclua por padrão:

```toml
[tool.pytest.ini_options]
markers = ["model: needs a real local model (slow)"]
addopts = ["-m", "not model"]
```

!!! tip "Rode a camada do modelo antes de subir para produção"
    Não em cada commit, mas antes de cada release. Foi rodando contra o
    Qwen2.5-0.5B que descobrimos que modelos pequenos resolvem a tarefa e
    **respondem em prosa mesmo assim** — o que motivou a passada de extração
    do [`run_structured`](agents-advanced.md#saida-estruturada-um-objeto-nao-um-paragrafo).
    Nenhum teste com fake acharia isso: um `from_pretrained` falso aceita
    qualquer coisa.

## Recapitulando

- **Script as decisões do modelo** com `ScriptedBackend` — o resto do agente
  é código comum e testa-se como código comum.
- **`assert_completed` antes de `run.output`**: execução truncada também traz
  texto.
- **`specs_seen` / `system_prompts`** provam o que chegou ao modelo em cada
  turno — é assim que se testa skill e memória.
- **`FailingBackend`** garante que a queda do modelo vire resposta, não 500.
- **Uma camada `@model` separada** cobre a única coisa que o script não
  cobre: se o modelo escolhe certo.

Veja também: [Agentes de IA](agents.md) para a trilha básica e
[Agentes de IA (avançado)](agents-advanced.md) para memória, skills,
delegação e laços.

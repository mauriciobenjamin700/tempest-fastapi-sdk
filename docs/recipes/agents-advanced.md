# Agentes de IA (avançado)

Esta página continua de onde [Agentes de IA](agents.md) parou. Lá o agente
recebe um objetivo, chama ferramentas e devolve um traço. Aqui ele passa a
devolver **objetos tipados**, a **lembrar** entre execuções, a carregar
**capacidades sob demanda**, a **delegar** para especialistas e a **insistir**
até um critério passar.

Cada seção é independente — leia a que resolve o seu caso.

| Quero que o agente… | Vá para |
| --- | --- |
| devolva um objeto, não um parágrafo | [Saída estruturada](#saida-estruturada-um-objeto-nao-um-paragrafo) |
| lembre de algo | [Memória](#memoria-tres-camadas-e-qual-escolher) |
| tenha muitas capacidades sem inchar o prompt | [Skills](#skills-capacidades-carregadas-sob-demanda) |
| passe trabalho a um especialista | [Delegação](#delegar-para-outro-agente) |
| insista até acertar | [Laços](#loop-insistir-ate-passar-num-criterio) |
| guarde o histórico de execuções | [Guardar as execuções](#guardar-as-execucoes) |

## Saída estruturada: um objeto, não um parágrafo

Um agente que termina em prosa serve para chat e não serve para pipeline —
algo lá na frente tem que transformar "a nota totaliza R$ 1.240,50 e vence
dia 15" de volta em campos, e isso quebra no dia em que o modelo escrever
diferente.

```python
from pydantic import Field

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherReport(BaseSchema):
    """The structured answer."""

    city: str = Field(description="Cidade reportada.")
    celsius: int = Field(description="Temperatura em celsius.")
    sky: str = Field(description="Condição do céu, uma palavra.")


run = await agent.run_structured("Consulte o tempo no Recife e reporte.", WeatherReport)

if run.has_data:
    print(run.data.city, run.data.celsius)
else:
    print("sem dados:", run.parse_error)
```

```text
Recife 22
```

`run.data` é uma instância do **seu** modelo — `run.data.celsius` é `int`, e
o type-checker sabe disso.

### Por que não pedir JSON e dar parse

O agente ganha uma ferramenta temporária `final_answer` com o formato do seu
modelo, e **chamar essa ferramenta é como o modelo termina**. Os argumentos
*são* a resposta estruturada, já validados, carregados pela mesma máquina de
tool-calling que o resto do agente usa — sem um segundo formato para o modelo
errar.

!!! tip "Modelo pequeno responde em prosa mesmo assim"
    Modelos locais pequenos frequentemente resolvem a tarefa certo e depois
    respondem em texto, ignorando a instrução. Por isso existe uma **passada
    de extração**: quando a prosa não traz JSON, o SDK faz mais uma chamada
    cuja **única** ferramenta é a de resposta, pedindo para reescrever o que
    já foi dito naquele formato. Sem nada mais para chamar e nada mais para
    raciocinar, até um modelo de 0.5B preenche os campos.

    Custa uma chamada extra de modelo — troca certa quando a alternativa é
    perder a execução inteira. Desligue com `extraction_retry=False`.

!!! warning "Sempre cheque `has_data`"
    Uma execução pode ser `succeeded` e ainda assim vir com `data=None` — o
    orçamento acabou, ou nem a extração conseguiu. `run.parse_error` diz
    qual foi o caso. E modelos pequenos às vezes deixam um campo vazio em vez
    de omitir: valide os valores, não só a presença do objeto.

Para insistir até a forma chegar, componha com o laço:

```python
from tempest_fastapi_sdk.agents import run_until, structured_verdict
```

## Memória: três camadas, e qual escolher

"O agente precisa lembrar" esconde **três necessidades diferentes**, e
escolher a errada é a razão pela qual memória costuma decepcionar. As três
estão aqui, todas opt-in.

| Camada | Dura | Use quando |
| --- | --- | --- |
| **Scratchpad** | uma execução | Uma execução longa precisa guardar um achado para um passo posterior usar, sem re-derivar nem re-ler. |
| **Fatos** | para sempre, editável | Algo é **verdade** e deve continuar sendo: uma preferência, um id de conta, uma política. Você quer ler e corrigir isso fora do modelo. |
| **Recall** | para sempre, difuso | Conversas passadas *podem* ser relevantes e você não sabe de antemão quais. A busca semântica decide. |

!!! danger "A distinção que mais importa: fato vs recall"
    Um **fato** é afirmado e exato — você lista, edita, apaga, e mostra ao
    usuário o que o sistema acredita sobre ele. **Recall** é recuperado e
    aproximado — traz texto que *parece* relacionado, o que é poderoso e
    **não auditável**.

    Guardar "o plano do usuário é enterprise" no recall significa que
    ninguém consegue corrigir. Guardar uma conversa inteira como fato
    significa que nada de útil volta.

### Scratchpad — dentro da execução

```python
from tempest_fastapi_sdk.agents import Agent, AgentContext, scratchpad, scratchpad_tools

agent = Agent(generator, tools=scratchpad_tools())

context = AgentContext()
run = await agent.run("Some os itens da nota e depois aplique o desconto.", context=context)
print(scratchpad(context))
```

```text
{'subtotal': '1240.50'}
```

O modelo ganha `note_write` / `note_read` / `note_list`. As notas vivem no
`AgentContext.state` e **somem quando a execução termina** — isso é a
feature, não a limitação: uma nota de outra execução aparecendo no meio da
tarefa é pior que nota nenhuma.

Use quando a execução é longa e deriva algo vários passos antes de precisar.
Sem isso o modelo re-deriva (lento, e a segunda resposta pode divergir) ou
carrega tudo na conversa, disputando atenção com o resto.

### Fatos — duráveis e editáveis

```python
from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryFactStore,
    fact_tools,
    facts_prompt,
)

store = InMemoryFactStore()
await store.put("timezone", "America/Recife", subject="user-42")

agent = Agent(
    generator,
    tools=fact_tools(store, subject="user-42"),
    system_prompt=BASE_PROMPT + await facts_prompt(store, subject="user-42"),
)
```

O bloco injetado no prompt:

```text
What you already know:
- timezone: America/Recife
```

!!! tip "Injetar vence obrigar o modelo a consultar"
    Quando o modelo percebe que precisa saber o fuso, ele normalmente já
    respondeu no fuso errado. Fatos são curtos e poucos — o custo de prompt
    é pequeno e o modelo não consegue deixar de consultar.

`fact_tools` dá `fact_remember` / `fact_recall` / `fact_list` /
`fact_forget`. Passe `allow_forget=False` quando os fatos são curados em
outro lugar: **um modelo que pode apagar o que discorda, apaga**.

`subject=` isola por usuário ou tenant. Deixar `None` dá um namespace
compartilhado — certo para um agente de propósito único, errado para
qualquer coisa por usuário.

!!! warning "O `InMemoryFactStore` some no restart"
    Que é exatamente o que memória durável não deveria fazer. Use-o para
    testes e para começar, e troque por um dos dois abaixo antes que importe.

#### Fatos numa tabela

```python
from tempest_fastapi_sdk.agents import Agent, DbFactStore, fact_tools, make_fact_model

model = make_fact_model(tablename="agent_facts")
store = DbFactStore(db, model)

agent = Agent(generator, tools=fact_tools(store, subject=user_id))
```

Escolha esta quando os fatos fazem parte do seu domínio: você quer eles no
backup, no admin, cruzados com o usuário, e legíveis por algo que não é o
agente. Em produção, herde de `BaseFactModel` à mão para a migration
enxergar a tabela estaticamente.

!!! danger "Declare o índice único na migration"
    Um fato é identificado por `(subject, key)`, mas o SDK não pode declarar
    a constraint por você — não sabe se a sua tabela é particionada ou
    compartilhada. Adicione na migration:

    ```sql
    UNIQUE (subject, key)
    ```

    Sem ela, uma corrida entre duas escritas deixa duas linhas, e as leituras
    passam a devolver a que o banco preferir.

#### Fatos no Redis

```python
from tempest_fastapi_sdk.agents import RedisFactStore

store = RedisFactStore(redis, prefix="agent:facts")
```

Um hash por subject — listar os fatos de alguém é um `HGETALL`, e toda
operação é O(1). Escolha esta quando os fatos são preferências
compartilhadas entre réplicas e uma migration é mais cerimônia do que o dado
merece. Requer o extra `[cache]`.

Os três implementam o mesmo protocolo de quatro métodos, então trocar é
mudança de construtor.

### Recall — semântico, entre execuções

```python
from tempest_fastapi_sdk.agents import Agent, recall_prompt

goal = "Agende uma call com o cliente."
agent = Agent(
    generator,
    system_prompt=BASE_PROMPT + await recall_prompt(chat_memory, goal, user_id="u1"),
)
```

```text
Possibly relevant from earlier conversations:
- eles preferem reuniões de manhã
```

Reaproveita o `ChatMemory` (Chroma/pgvector) que o SDK já tem. Repare no
cabeçalho: **"possivelmente relevante"**. Recall traz o que *parece*
relacionado, e apresentar isso como fato é como um agente passa a afirmar
com convicção algo que ninguém escreveu.

!!! check "Falha de recall não derruba o agente"
    Se o vector store estiver fora, `recall_prompt` devolve string vazia e a
    execução segue. Recall é melhoria, não requisito.

### Combinando

Nada impede usar as três — elas não competem, respondem perguntas
diferentes:

```python
agent = Agent(
    generator,
    tools=[*scratchpad_tools(), *fact_tools(store, subject=user_id)],
    system_prompt=(
        BASE_PROMPT
        + await facts_prompt(store, subject=user_id)
        + await recall_prompt(chat_memory, goal, user_id=user_id)
    ),
)
```

## Skills: capacidades carregadas sob demanda

Toda ferramenta que o agente pode chamar ocupa espaço no prompt, e cada linha
ali custa contexto e dilui atenção. Dez capacidades bem documentadas — cada
uma com suas convenções, seus casos de borda, seu exemplo — é mais instrução
do que um modelo local pequeno consegue segurar, e a qualidade cai **nas
dez**.

Uma **skill** separa o que o modelo precisa para *escolher* do que ele precisa
para *fazer*:

```python
from tempest_fastapi_sdk.agents import Agent, Skill

invoicing = Skill(
    name="invoicing",
    description="Ler e validar notas fiscais brasileiras (NF-e).",
    instructions=INVOICE_GUIDE,          # tão longo quanto precisar
    tools=[parse_nfe, validate_cnpj],
)

agent = Agent(generator, skills=[invoicing])
```

No prompt fica só isto:

```text
- invoicing: Ler e validar notas fiscais brasileiras (NF-e).
```

Quando o modelo decide que a skill se aplica, ele chama `load_skill` e **aí**
recebe as instruções completas — e as ferramentas dela passam a existir.

```python
run = await agent.run("Valide a nota em anexo.")
print(run.tool_calls)
```

```text
['load_skill', 'parse_nfe', 'validate_cnpj']
```

!!! check "As ferramentas da skill ficam escondidas até o load"
    Antes do `load_skill`, `parse_nfe` não aparece na lista de ferramentas
    que o modelo vê — nome e schema não custam nada enquanto não são usados.
    Cem capacidades custam cem linhas curtas, e a que está em uso ganha a
    página inteira.

!!! tip "A descrição é o que decide"
    É o **único** texto que o modelo vê antes de carregar. Diga para que a
    skill serve, não como ela funciona: "ler e validar NF-e" faz o modelo
    escolher certo; "utilitários fiscais diversos" não.

### Skills em arquivo

Para adicionar capacidade sem mexer em código:

```python
from tempest_fastapi_sdk.agents import Agent, discover_skills

agent = Agent(generator, skills=discover_skills("skills/"))
```

Cada `skills/<nome>/SKILL.md`:

```markdown
---
name: invoicing
description: Ler e validar notas fiscais brasileiras (NF-e).
---

O guia completo vai aqui, do tamanho que precisar.
```

É o mesmo formato das skills do Claude Code, então o arquivo serve nos dois
lugares. Ferramentas não vêm de arquivo — são Python — então anexe depois:

```python
skill.tools.append(parse_nfe)
```

!!! note "Diretório ausente não é erro"
    `discover_skills` devolve `[]` quando o diretório não existe, para o
    serviço subir normalmente sem ele.

Para saber o que o agente carregou numa execução:

```python
from tempest_fastapi_sdk.agents import AgentContext, loaded_skills

context = AgentContext()
run = await agent.run("...", context=context)
print(loaded_skills(context))
```

## Delegar para outro agente

Não existe objeto "time" aqui, e isso é o design: um agente já sabe escolher
uma ferramenta pelo nome e ler o que ela devolve, então o jeito mais barato
de passar trabalho a um especialista é **transformar o especialista numa
ferramenta**.

```python
from tempest_fastapi_sdk.agents import Agent, agent_tool, web_search_tool

researcher = Agent(
    generator,
    tools=[web_search_tool(web_search)],
    name="researcher",
)
writer = Agent(
    generator,
    tools=[agent_tool(researcher, description="Pesquise um tema na web.")],
    name="writer",
)

run = await writer.run("Escreva um resumo sobre PIX.")
for step in run.steps:
    print(step.kind, step.name, len(step.children))
```

```text
model chat 0
agent ask_researcher 3
model chat 0
```

O passo da delegação é `agent`, não `tool` — e o traço do filho fica
**aninhado** nele, em `children`. Uma delegação é o único passo que pode
custar tanto quanto uma execução inteira; ler um traço onde o passo caro
parece uma chamada de função é como você interpreta errado para onde foi o
tempo. `step.total_steps` conta a subárvore.

### Três guardas que a delegação precisa

| Guarda | Por quê |
| --- | --- |
| **Relógio herdado** | O filho pode terminar antes do próprio orçamento, mas **nunca** depois do relógio do pai — é o pai que está segurando uma requisição aberta. Vale o menor dos dois. |
| **Profundidade limitada** | Nada impede o modelo de fazer A delegar para B que delega para A. `max_depth` (3 por padrão) transforma isso numa recusa que o modelo lê e contorna. |
| **Artefatos com prefixo** | O que o filho produz sobe para o pai como `researcher/report.md`. Dois especialistas escrevendo `report.md` não se sobrescrevem. |

```python
run = await writer.run("...")
print([a.name for a in run.artifacts])
```

```text
['illustrator/chart.png', 'researcher/notes.md']
```

!!! warning "O filho truncado é sinalizado, não escondido"
    Se o sub-agente parar por orçamento, o texto que volta ao pai começa com
    `[stopped: timeout]`. Um pai que recebesse só a resposta parcial a
    apresentaria como completa.

Vários especialistas de uma vez:

```python
from tempest_fastapi_sdk.agents import Agent, team_tools

coordinator = Agent(
    generator,
    tools=team_tools({
        researcher: "Pesquise fatos na web.",
        illustrator: "Desenhe imagens a partir de uma descrição.",
    }),
    name="coordinator",
)
```

!!! tip "A descrição é o que o coordenador usa para escolher"
    Passar a descrição junto de cada agente é a forma que mantém as
    descrições legíveis lado a lado — e é só nelas que o coordenador se
    baseia para decidir a quem entregar.

## Loop: insistir até passar num critério

Uma execução para quando o **modelo** diz que terminou. Isso costuma
significar "sem mais ideias", não "está bom".

```python
from tempest_fastapi_sdk.agents import AgentRun, run_until

def parses(run: AgentRun) -> bool:
    """Accept only output that is valid JSON."""
    import json
    try:
        json.loads(run.output)
    except ValueError:
        return False
    return run.succeeded

result = await run_until(
    agent,
    "Devolva os dados como JSON.",
    until=parses,
    max_rounds=4,
    max_seconds=120,
)
print(result.accepted, result.rounds, result.output)
```

O predicado é onde está o valor: um teste que **executa** a saída — faz o
parse, importa o módulo, chama o endpoint — é uma barreira muito mais dura
que perguntar ao modelo se ele está satisfeito. É por isso que esse laço
consegue melhorar sobre uma execução única.

Cada rodada seguinte vê a tentativa rejeitada:

```text
Devolva os dados como JSON.

Your previous attempt was rejected. It was:

Aqui estão os dados: nome=João, idade=30

Produce a different and better answer.
```

Um modelo que não enxerga a própria tentativa anterior tende a reproduzi-la.
Passe `feedback=` para escrever esse texto do seu jeito.

!!! danger "`accepted=False` significa que nada passou"
    Ficar sem rodadas não é aprovação. `result.output` é a melhor tentativa,
    não uma resposta validada — cheque `result.accepted`, não só o texto.

## Loop: gerar, criticar, revisar

Um segundo agente lendo a saída do primeiro pega o que o autor não pega —
pelo mesmo motivo que code review funciona com gente.

```python
from tempest_fastapi_sdk.agents import refine

result = await refine(writer, reviewer, "Escreva as notas de release.")

print(result.accepted, result.rounds)
for iteration in result.iterations:
    print(iteration.index, iteration.accepted, iteration.critique)
```

```text
True 2
0 False Vago demais sobre a mudança incompatível; cite a versão.
1 True None
```

O crítico aprova respondendo exatamente `APPROVED`. Um crítico solto hesita
— "está bom, embora você pudesse considerar..." é impossível de ramificar.
Uma palavra reservada torna a decisão legível por máquina e deixa a
**rejeição** livre, que é a metade que precisa ser expressiva.

!!! note "O crítico não reescreve"
    Obrigá-lo a descrever a correção mantém o trabalho (e a
    responsabilidade por ele) com o worker. Um crítico que reescreve acaba
    contrabandeando os próprios erros sem revisão.

!!! warning "Custo multiplica"
    `rounds` execuções de um agente com N passos são `rounds * N` chamadas
    de modelo. É exatamente o ponto desses laços — e por isso todos exigem
    um teto duro.

## Guardar as execuções

Por padrão nada é guardado: a execução volta para quem chamou e acabou.

```python
from tempest_fastapi_sdk.agents import InMemoryAgentRunSink

store = InMemoryAgentRunSink(max_runs=100)
agent = Agent(generator, tools=tools, run_sink=store)
```

O buffer é **limitado de propósito** — execuções carregam seus artefatos, e
uma lista infinita de execuções que geram imagem é um vazamento de memória
com pavio lento.

Para persistir de verdade:

```python
from tempest_fastapi_sdk.agents import DbAgentRunSink, make_agent_run_model

model = make_agent_run_model(tablename="agent_runs")
agent = Agent(generator, tools=tools, run_sink=DbAgentRunSink(db, model))
```

!!! note "A tabela guarda o traço, não os bytes"
    Artefatos são megabytes; uma tabela de execuções não é blob store. O que
    fica são os **nomes** e os tipos, então quem lê sabe o que foi produzido
    e vai buscar onde você guardou.

Qualquer callable `async` que aceite um `AgentRun` serve como sink — mandar
para um log, uma fila ou um bucket é uma linha. Falha do sink **nunca**
derruba a execução: o trabalho já foi feito e quem chamou já tem a resposta.

## Moderação

```python
agent = Agent(generator, tools=tools, moderator=moderator)
run = await agent.run("algo proibido")
print(run.stop_reason, run.output)
```

```text
blocked blocked by moderation (toxicity)
```

O objetivo é checado **antes** de o modelo ver qualquer coisa, e a resposta
antes de voltar. Recusa vira `StopReason.BLOCKED`, não exceção.

## Acompanhar em tempo real

```python
async for step in agent.stream("Desenhe um gato e descreva"):
    print(step.index, step.kind, step.name, step.error or step.output[:60])
```

A execução só é finalizada (e mandada ao sink) quando o iterador se esgota.
Abandonar no meio não deixa registro — que é o certo para uma requisição
cancelada.

## Recapitulando

- **`run_structured(goal, output=Model)`** devolve uma instância do seu
  modelo; cheque `has_data`, e lembre da passada de extração para modelos
  pequenos.
- **Memória** tem três camadas: scratchpad (uma execução), fatos (duráveis e
  editáveis) e recall (duráveis e difusos). Fato é auditável; recall não.
- **Skills** mantêm o prompt pequeno: só nome e descrição ficam nele.
- **`agent_tool`** faz de um especialista uma ferramenta; relógio herdado,
  profundidade limitada, traço aninhado.
- **`run_until` / `refine`** insistem até o seu predicado — ou um crítico —
  aceitar. `accepted=False` significa que nada passou.
- **Persistência é opt-in**: nada, buffer em memória, ou tabela.

Voltar para a [trilha básica](agents.md).

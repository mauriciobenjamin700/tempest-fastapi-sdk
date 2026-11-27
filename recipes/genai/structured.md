# Config tipada e saída estruturada

Use quando o modelo precisa devolver um objeto Pydantic preenchido, e para fixar os parâmetros de geração com `GenerationConfig`.

Parte da receita [IA generativa self-hosted](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai.md).

## `GenerationConfig` tipado

Em vez de espalhar `**kwargs` (`max_new_tokens=...`, `temperature=...`)
por cada chamada, monte um `GenerationConfig` validado e reutilizável e
passe via `config=`:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenerationConfig, TextGenerator

gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
config = GenerationConfig(max_new_tokens=512, temperature=0.2, top_p=0.9)


async def main() -> None:
    """Run this example."""
    await gen.generate("Explique PIX em uma frase.", config=config)
    await gen.chat([{"role": "user", "content": "Oi"}], config=config)


asyncio.run(main())
```

Só os campos definidos entram sobre os defaults; `**kwargs` explícitos
ainda vencem o config (`gen.generate(prompt, config=config,
temperature=0.9)` usa `0.9`).

!!! tip "Default no gerador: `config=`"
    `TextGenerator(..., config=GenerationConfig(...))` (e o mesmo no
    `OllamaGenerator`) vale para toda chamada. A ordem, da mais fraca para a
    mais forte: os defaults internos, o `config` do gerador, o `config` da
    chamada (campo a campo) e os `**kwargs` da chamada. É o caminho para
    quem não passa opção por chamada, como o laço do agente.

!!! tip "`seed` e `stop` valem no path local"
    `seed` e `stop` são honrados tanto no `OllamaGenerator` quanto no
    `TextGenerator` (transformers): mesma seed + `do_sample=True` reproduz a
    saída, e `stop` vira o argumento `stop_strings` de `model.generate`
    (requer transformers >= 4.44). Ambos podem vir do `GenerationConfig` ou
    por chamada — o override por chamada vence.

!!! info "A `seed` local é por chamada, e vale sob concorrência"
    O `model.generate` não aceita `torch.Generator` por chamada —
    `generate(..., generator=g)` levanta `ValueError` (`model_kwargs` não
    usado) no transformers 4.57.6 e no 5.17.0 — e amostra com o RNG global
    do processo. Por isso o `TextGenerator` não usa mais `transformers.set_seed`
    na amostragem simples (`do_sample=True`, `num_beams=1`, sem modelo
    assistente): um logits processor próprio sorteia o token com um
    `torch.Generator` só daquela chamada. Duas gerações concorrentes com a
    mesma seed dão o mesmo texto que uma rodando sozinha, e chamadas sem
    seed não viram determinísticas por tabela.

    Medido com `Qwen/Qwen2.5-0.5B-Instruct`, 5 pares de chamadas
    concorrentes (`asyncio.gather`) com a mesma seed: antes, os 5 pares
    divergiam da execução serial; agora, nenhum — na CPU (transformers
    4.57.6) e numa RTX 4070 Ti SUPER (4.57.6 e 5.17.0). Sem concorrência, a mesma seed
    dá o mesmo texto que antes da mudança, nos dois devices.

    O custo fica só nas chamadas com seed. O passo de escolha do token passou
    de 1,07 para 3,08 ms na GPU e de 7,3 para 13,3 ms na CPU (vocabulário de
    151 936 tokens, top-k 20 + top-p 0,9, melhor de 5 rodadas de 500 passos).
    De ponta a ponta, numa máquina carregada por outros processos, a geração
    com seed ficou de 4 a 6% mais lenta que o caminho antigo na GPU (3
    execuções de 10 x 64 tokens) e 12% na CPU (1 execução de 5 x 32 tokens).

    Beam sampling (`num_beams > 1`), decodificação assistida/prompt lookup
    e DoLa amostram num código que um logits processor não controla; nesses
    modos a seed continua indo por `transformers.set_seed`, global ao
    processo.

## Saída estruturada (JSON validado)

Force o modelo a devolver um schema Pydantic e receba a instância já
validada — em vez de torcer pra saída ser um JSON parseável:

```python
import asyncio

from pydantic import BaseModel
from tempest_fastapi_sdk.genai import OllamaGenerator


class Pessoa(BaseModel):
    nome: str
    idade: int


gen = OllamaGenerator("llama3.2")


async def main() -> None:
    """Run this example."""
    pessoa: Pessoa = await gen.generate_structured("Uma pessoa qualquer.", Pessoa)
    # -> Pessoa(nome="...", idade=...)


asyncio.run(main())
```

O `OllamaGenerator` manda o schema no campo `format` do daemon (o Ollama
garante JSON schema-válido nativamente) e faz o parse na saída — **é a
rota estruturada recomendada, sem biblioteca extra**.

!!! warning "Documento longo pede `num_ctx`, antes de qualquer outra coisa"
    A janela de contexto do Ollama tem default **4096 tokens**, e o que
    não cabe é truncado em silêncio — a extração devolve alguns itens, ou
    nenhum, sem erro nenhum. Medido contra `gpt-oss:20b` (Ollama 0.30.11)
    sobre um edital de 24.086 caracteres com 20 itens plantados, três
    execuções por configuração:

    | `num_ctx` | instrução em `system` | instrução concatenada |
    | --- | --- | --- |
    | 4096 (default) | 0, 6, 5 itens | erro, 2, 1 itens |
    | 16384 | **20, 20, 20** | **20, 20, 20** |

    Com a janela dimensionada para o documento, as duas formas acertam
    tudo. Com a janela default, as duas degradam — a instrução no turno
    `system` degrada menos, porque não disputa espaço com o documento que
    está sendo truncado, e é por isso que a separação ainda vale a pena:

    ```python
    await generator.generate_structured(
        documento, Schema, system="...", num_ctx=16384
    )
    ```

    Dimensione `num_ctx` pelo documento (uma regra grosseira: 1 token por
    ~3 caracteres, com folga para o schema e a resposta), e prefira a
    instrução em `system=`.

??? info "Por que a chamada vai em `/api/chat` e não em `/api/generate`"
    Num modelo de raciocínio (harmony, como o `gpt-oss`), o
    `/api/generate` **com** `format` responde `200 OK` com `eval_count`
    não-zero e `response` **vazio** — a resposta cai num canal de
    raciocínio que aquele endpoint não expõe. Sem `format` funciona; com
    `format`, não. O `/api/chat` devolve o JSON em `message.content`, e
    modelos sem raciocínio se comportam igual nos dois. Desde a v0.229.0
    a chamada usa `/api/chat`, e um conteúdo vazio levanta `ValueError`
    em vez de devolver nada.

!!! info "Todo campo vai em `required` no fio — inclusive os que têm default"
    O Pydantic deixa campo com default fora de `required` no JSON schema,
    e o decodificador constrangido do daemon deixa o modelo omiti-lo. O
    objeto volta válido (o default preenche o campo) e errado, sem sinal
    nenhum. Por isso `generate_structured` e
    `chat_structured` mandam no `format` uma cópia do schema em que **todo**
    objeto (raiz, aninhado, item de lista, `$defs`) lista todas as
    propriedades em `required`. Campo opcional (`X | None`) continua
    aceitando `null` — o modelo só passa a dizê-lo. A validação continua
    contra o seu model, sem mudança.

    Medido na pesquisa de LLMs locais de 2026-11-26/27 (Ollama 0.30.11,
    33 casos de extração em 3 schemas, temperatura 0), acurácia média por
    campo:

    | modelo | schema do Pydantic | todo campo em `required` |
    | --- | --- | --- |
    | `gpt-oss:20b` | 70,8 | **95,4** |
    | `qwen2.5:3b` | 69,2 | 87,3 |
    | `lfm2.5-thinking:1.2b` | 56,6 | 74,3 |
    | `qwen2.5-coder:14b` | 90,0 | 95,7 |
    | `ministral-3:14b` | 84,4 | 89,9 |

    No `gpt-oss:20b` o schema do Pydantic nunca fez sair um campo
    `Field(default_factory=list)`: os 11 pedidos voltaram com `itens=[]`.
    Você não precisa mais declarar o schema de extração sem default.

!!! tip "Instrução separada do documento — `chat_structured`"
    Extraindo campos de um documento longo, a instrução precisa ir num
    turno `system` e o conteúdo num `user`. Concatenar tudo num prompt só
    degrada a aderência ao schema de forma **medida**: o modelo passa a
    "responder" trechos do documento.

    ```python
    import asyncio

    from pydantic import BaseModel
    from tempest_fastapi_sdk.genai import OllamaGenerator


    class NotaFiscal(BaseModel):
        numero: str
        total_centavos: int


    gen = OllamaGenerator("gpt-oss:20b")


    async def main() -> None:
        """Run this example."""
        nota: NotaFiscal = await gen.chat_structured(
            [
                {"role": "system", "content": "Extraia os campos da nota."},
                {"role": "user", "content": "NF-1 — total R$ 49,90"},
            ],
            NotaFiscal,
        )


    asyncio.run(main())
    ```

    O `format` vai no **top-level** do corpo, que é onde o daemon lê.
    Passar o schema como keyword do `chat()` cai em `options`, e o Ollama
    **ignora em silêncio**: volta `200 OK` com texto livre e o erro só
    aparece no `ValidationError` — ou num parse que por acaso funciona.
    Por isso `chat_structured` recusa um `format=` explícito.

!!! info "No backend local (transformers)"
    `TextGenerator.generate_structured(prompt, schema, constrained=True)`
    restringe a decodificação com o `lm-format-enforcer`
    (extra `[genai-structured]`), então o modelo só emite tokens que
    mantêm o JSON válido — o adapter é construído a partir do core estável
    da lib, então funciona no transformers 4.x **e 5.x** (validado no
    Qwen2.5-3B). O `constrained=False` continua disponível pra best-effort
    sem o extra.

    Desde a v0.242.0 ele também expõe `chat_structured(messages, schema)`,
    a **mesma** chamada do daemon: o serviço que lê documentos tipa contra
    `StructuredTextBackend` e roda nos dois sem mudar uma linha no call
    site. A separação instrução/documento vale igual aqui — o template de
    chat do tokenizer é aplicado antes de gerar.

    ```python
    import asyncio
    import threading

    from pydantic import BaseModel
    from tempest_fastapi_sdk.genai import StructuredTextBackend, TextGenerator


    class NotaFiscal(BaseModel):
        numero: str
        total_centavos: int


    async def ler(backend: StructuredTextBackend) -> NotaFiscal:
        """Lê a nota com qualquer backend estruturado.

        Args:
            backend (StructuredTextBackend): Daemon Ollama ou modelo local.

        Returns:
            NotaFiscal: Os campos extraídos.
        """
        return await backend.chat_structured(
            [
                {"role": "system", "content": "Extraia os campos da nota."},
                {"role": "user", "content": "NF-1 — total R$ 49,90"},
            ],
            NotaFiscal,
        )


    async def main() -> None:
        """Run this example."""
        parar = threading.Event()
        local = TextGenerator("Qwen/Qwen2.5-3B-Instruct")
        nota: NotaFiscal = await local.chat_structured(
            [{"role": "user", "content": "NF-1 — total R$ 49,90"}],
            NotaFiscal,
            stop_event=parar,
        )
        del nota


    asyncio.run(main())
    ```

!!! info "Cancelar a chamada para o modelo local"
    A geração local roda numa thread, e thread não se cancela de fora. Por
    isso o `TextGenerator` (e o `VisionTextGenerator`) roda toda chamada
    sob um evento que os critérios de parada do transformers consultam a
    cada token, e o aciona quando a chamada é cancelada: um
    `asyncio.timeout`, o budget de um agente ou o
    [`run_cancellable`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/jobs.md#8-progresso-a-barra-que-nao-mente) param a
    decodificação em até um token. Medido com `Qwen/Qwen2.5-0.5B-Instruct`
    em CPU, 300 tokens forçados, cancelado em 1 s: a thread seguia
    decodificando 9,3 a 10,8 s depois do cancelamento (em medições
    diferentes); agora termina em 0,01 a 0,02 s.
    O `stop_event` continua servindo para parar **sem** cancelar (de outra
    thread, por exemplo), e também é acionado no cancelamento. No daemon
    nada disso é preciso: abortar a requisição HTTP já para a geração.

!!! tip "Só o parse"
    `parse_structured(texto, schema)` extrai o JSON de uma saída crua
    (tolera cercas markdown e texto ao redor) e valida contra o schema —
    útil pra reaproveitar em qualquer saída de modelo.

### Quando a resposta é uma **lista**

Prompt que pede "extraia as tarefas deste texto" volta com um array, não
com um objeto. `parse_structured` procura `{`…`}` e não serve; use o par
de lista:

```python
from pydantic import BaseModel
from tempest_fastapi_sdk.genai import extract_json_list, parse_structured_list


class Tarefa(BaseModel):
    titulo: str
    prazo: str | None = None


bruto = 'Claro! Aqui vão: [{"titulo": "revisar contrato"}]'

tarefas: list[Tarefa] = parse_structured_list(bruto, Tarefa)
# -> [Tarefa(titulo="revisar contrato", prazo=None)]

cru: list | None = extract_json_list(bruto)
# -> [{"titulo": "revisar contrato"}]
```

Cerca markdown ao redor do array (o ```` ```json ```` que o modelo
adiciona mesmo quando o prompt pede que não) é tolerada do mesmo jeito,
esteja ela envolvendo a resposta inteira ou enterrada entre duas frases.

- **`parse_structured_list(texto, schema)`** valida cada item e levanta
  como o `parse_structured`. `skip_invalid=True` descarta o item ruim e
  devolve os demais — o que se quer numa lista de sugestões, onde perder
  as nove boas por causa de uma malformada é o pior resultado.
- **`extract_json_list(texto)`** devolve a lista crua, ou `None` quando
  não há array decodificável. **`None` é o sinal de "tente gerar de
  novo"**, e é diferente de `[]`, que é "o modelo respondeu, e a resposta
  é nenhum item". Quem confunde os dois ou repete uma chamada que já deu
  certo, ou desiste de um erro de formatação recuperável.
- **`extract_json_object(texto)`** é o gêmeo para quando a resposta é
  **um** registro, não uma lista: classificador de intenção, decisão de
  roteamento, extração de campos fixos. Mesma tolerância a cerca e prosa
  do `parse_structured`, e o mesmo `None` de "tente de novo" no lugar da
  exceção — um laço de retry escrito em volta de um `except ValueError` é
  a forma que engole erro de verdade junto.

### Tentar de novo quando o modelo erra o formato

`generate_structured_list` junta as duas metades: gera, extrai, e se não
veio array, **gera de novo com temperatura maior**.

```python
import asyncio

from pydantic import BaseModel
from tempest_fastapi_sdk.genai import (
    OpenAICompatGenerator,
    StructuredFormatError,
    generate_structured_list,
)


class Tarefa(BaseModel):
    titulo: str


gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)


async def main() -> None:
    """Run this example."""
    try:
        tarefas: list[Tarefa] = await generate_structured_list(
            gen,
            "Extraia as tarefas. Responda só com um array JSON.",
            Tarefa,
            max_attempts=3,
            temperature_step=0.2,
        )
    except StructuredFormatError as exc:
        print(f"desisti apos {exc.attempts} tentativas: {exc.last_output}")
    else:
        print(len(tarefas))


asyncio.run(main())
```

!!! tip "Por que a temperatura sobe"
    Repetir a chamada na mesma temperatura quase não adianta: decodificação
    greedy é determinística, então a tentativa 2 reproduz a 1 token a
    token, e o retry gasta uma chamada para receber a mesma saída inútil. A
    1ª tentativa é greedy (é o tiro único mais confiável) e cada retry soma
    `temperature_step`, dando ao sampling chance real de sair do estado
    ruim.

!!! warning "Item ruim não gasta tentativa"
    Só falha **estrutural** (nenhum array na saída) consome uma tentativa.
    Um array que parseia com um item malformado é tratado por
    `skip_invalid=True`, que descarta o item e devolve o resto — perder
    nove sugestões boas por causa de uma errada seria o pior resultado.

    Pelo mesmo motivo, **`[]` é sucesso**: o modelo respondeu, e a resposta
    é nenhum item. Tentar de novo aí é regerar uma pergunta já respondida.

Aceita qualquer backend com `generate(prompt, config=...)` — o
`TextGenerator` local, o `OllamaGenerator` e o `OpenAICompatGenerator`.

!!! info "Colchete sobrando não quebra mais o parse"
    A extração conta profundidade em vez de cortar até o último `]`/`}`.
    Antes da v0.235.0, um `]` a mais no fim transformava uma resposta perfeita em
    `ValueError`, e as tentativas seguintes reproduziam o mesmo corte,
    porque o defeito
    estava no recorte e não na geração. Regex não-guloso não resolveria:
    ele para no primeiro `]` e trunca qualquer item com lista aninhada
    dentro.

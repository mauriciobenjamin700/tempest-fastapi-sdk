# Gerar texto (local e hospedado)

Use para gerar texto com um LLM local (`TextGenerator`) ou um backend compatível com OpenAI (DeepSeek, Groq, OpenRouter, vLLM), com custo por chamada e `extra_body`.

Parte da receita [IA generativa self-hosted](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai.md).

## Gerar texto com LLM local

`TextGenerator` carrega um LLM causal do HuggingFace **uma vez** e gera no
seu hardware. Resolve device e precisão sozinho, suporta quantização
int4/int8, carrega os pesos preguiçosamente (na 1ª chamada) e libera VRAM
quando ocioso. Requer `[genai]` (e `[genai-quant]` pra quantizar).

```python
import asyncio

from tempest_fastapi_sdk.genai import TextGenerator

gen = TextGenerator(
    "Qwen/Qwen2.5-7B-Instruct",
    quantization="int4",            # cabe em GPU modesta; None = precisão cheia
    idle_unload_seconds=300,        # libera VRAM após 5 min ocioso
)


async def main() -> None:
    """Run this example."""
    texto = await gen.generate("Explique PIX em uma frase.", max_new_tokens=128)

    # chat com template de papéis:
    resposta = await gen.chat([
        {"role": "system", "content": "Você responde em PT-BR."},
        {"role": "user", "content": "O que é PIX?"},
    ])

    # streaming token a token:
    async for pedaco in gen.stream("Escreva um haiku sobre chuva."):
        print(pedaco, end="", flush=True)

    gen.unload()                        # libera a memória na hora


asyncio.run(main())
```

!!! info "O peso baixa uma vez — depois é cache em disco"
    A primeira chamada escreve os GB em `$HF_HOME/hub` (ou
    `~/.cache/huggingface/hub`); as execuções seguintes leem de lá, sem rede.
    Num **container sem volume** isso se perde a cada restart. Como apontar o
    cache, fixar a revisão, pré-baixar no deploy e rodar offline está em
    **[Pesos de modelos »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/model-weights.md#onde-os-pesos-ficam-e-por-que-a-2a-execucao-e-instantanea)**.

A geração bloqueante roda em `asyncio.to_thread` — não trava o event loop.
Isso vale também pro `stream()`: o load da primeira chamada e a espera entre
tokens acontecem numa worker thread, e fechar o iterador antes do fim (um
`break`, um cliente que desconectou) para a geração no próximo token em vez
de deixar a GPU gerando até `max_new_tokens` pra ninguém. Medido com
`Qwen/Qwen2.5-0.5B-Instruct` numa RTX 4070 Ti SUPER: o primeiro stream
travava o loop por 4190 ms e agora trava de 164 a 193 ms (três execuções);
fechar depois de 5 de 200 tokens bloqueava 3532 ms e agora volta em ~0,01 ms,
com a worker thread encerrada 28 ms depois.

`device="auto"` escolhe CUDA → MPS → CPU; `dtype="auto"` usa bf16 em GPU e
fp32 em CPU.

!!! tip "Confira antes de carregar"
    Combine com o [capacity check](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/hardware.md#a-maquina-aguenta): rode `can_run` /
    `recommend` pra escolher `quantization`/`device` que **cabem** antes
    de instanciar o `TextGenerator`.

!!! tip "Libere VRAM entre picos"
    Com `idle_unload_seconds` setado, chame `gen.unload_if_idle()`
    periodicamente (ex.: num `@tq.interval(60)` do [TaskQueue](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/queue-tasks.md))
    — ele descarrega o modelo só quando passou do tempo ocioso, sem mágica
    de background thread. `unload()` libera na hora.

    Uma chamada em andamento **não** conta como ociosa: `seconds_idle` lê
    `0.0` enquanto ela roda e `unload_if_idle()` devolve `False`. Um
    `unload()` que chega no meio de uma chamada (inclusive o de uma evicção
    do `ModelRegistry`) espera: quem terminar por último solta os pesos. E
    várias primeiras chamadas simultâneas carregam o modelo **uma** vez só.
    Vale igual pra todo loader local (`Embedder`, `Reranker`,
    `ImageGenerator`, `SpeechToText`, `TextToSpeech`, `SpeakerDiarizer`,
    `VoiceEmbedder`, `OnnxEmbedder`...) e pro `FaceRecognizer` do
    [reconhecimento facial](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/faces.md).

### Vários pedidos ao mesmo tempo em CPU: `max_concurrent`

Cada chamada roda numa worker thread, e em CPU o `model.generate` de **uma**
thread já usa todo o pool intra-op do torch. Sem limite, quatro agentes
respondendo ao mesmo tempo viram quatro threads disputando os mesmos
núcleos: ninguém termina antes, e todo mundo termina tarde.
`max_concurrent` põe os pedidos excedentes numa fila:

```python
import asyncio

from tempest_fastapi_sdk.genai import TextGenerator

gen = TextGenerator(
    "Qwen/Qwen2.5-0.5B-Instruct",
    device="cpu",
    max_concurrent=1,               # um decode por vez; os outros esperam na fila
)


async def main() -> None:
    """Run this example."""
    respostas = await asyncio.gather(
        *(gen.generate(f"Dê uma dica de Python, número {n}.") for n in range(4))
    )
    for resposta in respostas:
        print(resposta)


asyncio.run(main())
```

Medido com `Qwen/Qwen2.5-0.5B-Instruct` em CPU (WSL2 num i9-13900F, 12 CPUs
lógicas visíveis = 6 núcleos × 2, torch 2.14 com 6 threads intra-op),
128 tokens forçados (`min_new_tokens` = `max_new_tokens`), `do_sample=False`,
cada configuração no seu próprio processo, mediana de 5 rodadas:

| Chamadas simultâneas | `max_concurrent` | Throughput | Latência mediana | Latência máxima |
| --- | --- | --- | --- | --- |
| 1 | `None` | 28,3 tokens/s | 4,53 s | 4,53 s |
| 2 | `None` | 24,6 tokens/s | 10,26 s | 10,42 s |
| 2 | `1` | 26,7 tokens/s | 7,27 s | 9,60 s |
| 2 | `2` | 27,7 tokens/s | 9,13 s | 9,24 s |
| 4 | `None` | 22,4 tokens/s | 22,73 s | 22,85 s |
| 4 | `1` | 26,9 tokens/s | 11,91 s | 19,03 s |
| 4 | `2` | 25,6 tokens/s | 14,80 s | 19,97 s |

Com quatro pedidos e `max_concurrent=1`, a vazão volta a 95% da de uma
chamada sozinha, a **latência mediana cai pela metade**,
e o último termina
antes do que terminaria sem limite. Uma chamada sozinha não muda: em duas
reexecuções de 9 rodadas, 4,56 s contra 4,57 s e 4,66 s contra 4,65 s.

!!! info "Por que uma fila de threads dedicadas, e não um semáforo"
    O runtime OpenMP do torch mantém **um time de workers por thread que
    chama** o modelo, e o executor padrão do asyncio entrega cada chamada à
    thread que estiver livre. Medido no mesmo hardware, decodificando
    estritamente um por vez: 4,5 s por chamada enquanto tudo rodava na mesma
    thread, 6,4 s por chamada depois de alternar entre quatro threads vivas
    — cada thread nova somou 6 threads ao processo, e a lentidão continuou
    mesmo voltando para a primeira. Um semáforo na frente do executor
    padrão serializava **e** deixava cada chamada mais lenta: quatro
    chamadas simultâneas fecharam em 18,5 tokens/s, abaixo dos 22,8 sem
    limite nenhum medidos no mesmo processo. Por isso `max_concurrent=N`
    cria um pool de **N threads
    próprias** do gerador, e a decodificação nunca sai delas.
    (`OMP_WAIT_POLICY=PASSIVE` não resolve: deixou a chamada sozinha em
    6,7 s.)

O default continua `None` (sem limite, cada chamada começa na hora), para
uma atualização do SDK não mudar a concorrência de ninguém — em GPU o
trade-off não foi medido. Em CPU, comece com `max_concurrent=1`. Um pedido
cancelado enquanto espera na fila sai dela sem rodar; um cancelado durante
a decodificação para no próximo token e só libera a vaga quando a thread
termina de fato. Vale para `generate`, `chat`, `chat_with_tools`,
`generate_structured`, `chat_structured` e `stream` (que segura a vaga até
o último token, ou até um token depois de você fechar o iterador). O
`VisionTextGenerator` aceita o mesmo `max_concurrent=`.

!!! note "E o número de threads?"
    Não há knob de threads no `TextGenerator` de propósito:
    `torch.set_num_threads` é **global ao processo**, não por gerador, e
    o default do torch aqui já era o número de núcleos físicos visíveis
    (6). Com `max_concurrent=1` a vazão sob carga ficou a 5% da de uma
    chamada sozinha, então sobrava pouco para ganhar. Se você precisar
    mudar, chame `torch.set_num_threads(n)` uma vez no startup, sabendo que
    vale para todo modelo do processo.

## Backend hospedado (DeepSeek, Groq, OpenRouter, vLLM...)

!!! tip "Anthropic, OpenAI e DeepSeek"

    Esta seção cobre o `OpenAICompatGenerator` em si. Para Claude (`AnthropicGenerator`), tool calling, saída estruturada, embeddings, settings (`HostedGenAISettings`) e erros tipados, veja [IA generativa hospedada](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai-hosted.md).

Nem todo serviço quer carregar peso: às vezes a conta que importa é a de
**custo por token**, não a de RAM. O formato `/chat/completions` da OpenAI
virou o denominador comum — DeepSeek, Groq, Together, OpenRouter, Mistral,
o servidor do vLLM, a rota OpenAI do TGI e a Azure falam todos ele — então
um cliente só alcança todos, trocando `base_url` e `model`.

```python
import asyncio

from tempest_fastapi_sdk.genai import OpenAICompatGenerator

gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",                        # nunca hardcoded: leia das settings
    base_url="https://api.deepseek.com",
)


async def main() -> None:
    """Run this example."""
    texto: str = await gen.generate("Explique PIX em uma frase.")
    print(texto)


asyncio.run(main())
```

Satisfaz o `TextBackend`, então entra no `make_genai_router` e no
`AIChatPipeline` no lugar do `TextGenerator` ou do `OllamaGenerator`, sem
mais nenhuma mudança. Chave vazia levanta `ValueError` **na construção** —
o erro é de configuração, e aparece onde a configuração é lida, não num
401 lá dentro do primeiro job em background.

### Quanto custou a chamada

`generate_with_usage` devolve o texto **e** o consumo que o próprio
provedor reportou. É esse número que é cobrado, então é ele que vale
guardar quando você precisa de contabilidade por usuário — não uma
recontagem local com outro tokenizer:

```python
import asyncio

from tempest_fastapi_sdk.genai import OpenAICompatGenerator, TokenUsage

gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)


async def main() -> None:
    """Run this example."""
    texto: str
    uso: TokenUsage | None = None
    texto, uso = await gen.generate_with_usage("Resuma isto.", system="Seja breve.")
    if uso is not None:
        print(uso.input_tokens, uso.output_tokens, uso.total_tokens)


asyncio.run(main())
```

`TokenUsage` soma com `+`, para um trabalho feito de várias chamadas (um
resumo por map-reduce é N chamadas de pedaço mais uma de redução, e o que
se quer registrar é o trabalho, não cada perna).

!!! warning "`None` não é zero"
    `uso is None` significa "o provedor não disse", e é diferente de um
    consumo zerado, que afirmaria que a chamada foi de graça. Quem persiste
    uso não deve gravar linha nesse caso.

!!! info "`total` vem do provedor, não da soma"
    Provedor nenhum é obrigado a cobrar `entrada + saída`: desconto de
    prefixo em cache aparece exatamente assim. O total reportado é a
    autoridade; a soma só entra quando o campo não veio.

#### O prefixo que veio do cache

Quando o começo do prompt bate com uma chamada recente, o provedor serve
essa parte do cache e cobra bem mais barato por ela — a tabela da DeepSeek
põe o token de acerto duas ordens de grandeza abaixo do de erro.
`cache_hit_tokens` é essa fatia:

```python
import asyncio

from tempest_fastapi_sdk.genai import OpenAICompatGenerator, TokenUsage

gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)


async def main() -> None:
    """Run this example."""
    uso: TokenUsage | None
    _texto, uso = await gen.generate_with_usage("Resuma isto.")
    if uso is not None:
        print(uso.input_tokens, "entrada, dos quais", uso.cache_hit_tokens, "em cache")


asyncio.run(main())
```

!!! danger "É uma fatia de `input_tokens`, não uma parcela a mais"
    `cache_hit_tokens` conta tokens que **já estão** em `input_tokens`.
    Somar os dois cobra o prefixo duas vezes. Quem precifica subtrai:
    `(entrada − cache) × preço_normal + cache × preço_de_cache`, que é
    exatamente o que `AIUsageStore.estimate_cost` faz.

!!! info "Duas grafias, as duas lidas"
    A mesma família OpenAI-compatível escreve isso de dois jeitos: a
    DeepSeek manda `prompt_cache_hit_tokens` na raiz do `usage`, a OpenAI
    manda `prompt_tokens_details.cached_tokens`. `from_payload` lê as duas
    — tratar só uma funciona no provedor em que você testou e cobra caro
    demais no outro, sem avisar.

    Provedor sem cache de prompt não manda campo nenhum, e aí a fatia é
    `0`. Aqui zero é honesto (nada veio do cache), diferente do `usage`
    inteiro ausente, que é `None`.

### Campo específico de um provedor: `extra_body`

O formato é comum, as extensões não. `extra_body` é mesclado no corpo de
toda requisição, **por baixo** dos campos calculados — então não dá para
redirecionar a chamada para outro modelo por acidente.

```python
from tempest_fastapi_sdk.genai import OpenAICompatGenerator

gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
    extra_body={"thinking": {"type": "disabled"}},
)
```

!!! danger "Modelo híbrido de raciocínio devolve conteúdo vazio"
    Esse `thinking` não é enfeite. Um modelo híbrido com raciocínio
    **ligado por padrão** gasta o `max_tokens` na cadeia oculta antes do
    conteúdo real — cobrada como saída normal —, então um orçamento
    dimensionado para a resposta estoura ali e o `content` volta vazio.
    Relatado por um serviço que bateu nisso contra a DeepSeek; este repo
    não reproduz contra provedor de verdade.
    Desligar é mais barato e sem risco, não perda de qualidade, quando
    você só quer o resultado.

!!! info "Campos só do HuggingFace não vão pro fio"
    `do_sample`, `top_k` e `repetition_penalty` existem no `GenerationConfig`
    mas não no formato OpenAI, e um provedor estrito recusa o corpo (a
    auditoria relatou `400` da OpenAI; os testes daqui usam transporte mock).
    Por isso eles são **descartados**; `do_sample=False` sem `temperature`
    explícita vira `temperature=0`. Um provedor que aceita esses nomes (o
    servidor do vLLM documenta `top_k` e `repetition_penalty`) é ligado com
    `forward_params=["top_k", "repetition_penalty"]`. E `model`, `messages` e
    `stream` não podem vir como keyword de geração — `generate("oi",
    model="outro")` levanta `TypeError` em vez de redirecionar a chamada.

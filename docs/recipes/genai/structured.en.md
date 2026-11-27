---
description: "Use when the model must return a filled-in Pydantic object, and to pin generation parameters with `GenerationConfig`."
---

# Typed config and structured output

Use when the model must return a filled-in Pydantic object, and to pin generation parameters with `GenerationConfig`.

Part of the [Self-hosted generative AI](../genai.md) recipe.

## Typed `GenerationConfig`

Instead of scattering `**kwargs` (`max_new_tokens=...`, `temperature=...`)
across every call, build a validated, reusable `GenerationConfig` and
pass it via `config=`:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenerationConfig, TextGenerator

gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
config = GenerationConfig(max_new_tokens=512, temperature=0.2, top_p=0.9)


async def main() -> None:
    """Run this example."""
    await gen.generate("Explain PIX in one sentence.", config=config)
    await gen.chat([{"role": "user", "content": "Hi"}], config=config)


asyncio.run(main())
```

Only the set fields layer over the defaults; explicit `**kwargs` still
win over the config (`gen.generate(prompt, config=config,
temperature=0.9)` uses `0.9`).

!!! tip "A default on the generator: `config=`"
    `TextGenerator(..., config=GenerationConfig(...))` (and the same on
    `OllamaGenerator`) applies to every call. The order, weakest to
    strongest: the built-in defaults, the generator's `config`, the call's
    `config` (field by field) and the call's `**kwargs`. It is the way in
    for a caller that passes no options per call, such as the agent loop.

!!! tip "`seed` and `stop` apply on the local path too"
    `seed` and `stop` are honored by both `OllamaGenerator` and
    `TextGenerator` (transformers): the same seed + `do_sample=True`
    reproduces the output, and `stop` becomes `model.generate`'s
    `stop_strings` argument (requires transformers >= 4.44). Either may come
    from the `GenerationConfig` or per call — the per-call override wins.

!!! info "The local `seed` is per call, and holds under concurrency"
    `model.generate` takes no per-call `torch.Generator` —
    `generate(..., generator=g)` raises `ValueError` (unused `model_kwargs`)
    on transformers 4.57.6 and on 5.17.0 — and samples with the process-wide
    RNG. So `TextGenerator` no longer uses `transformers.set_seed` for plain
    sampling (`do_sample=True`, `num_beams=1`, no assistant model): a logits
    processor of its own draws the token with a `torch.Generator` private
    to that call. Two concurrent generations with the same seed give the
    same text as one running alone, and unseeded calls do not become
    deterministic as a side effect.

    Measured with `Qwen/Qwen2.5-0.5B-Instruct`, 5 pairs of concurrent calls
    (`asyncio.gather`) with the same seed: before, all 5 pairs diverged from
    the serial run; now, none does — on CPU (transformers 4.57.6) and on an
    RTX 4070 Ti SUPER (4.57.6 and 5.17.0). Without concurrency, the same seed gives
    the same text as before the change, on both devices.

    The cost is paid by seeded calls only. The token-selection step went
    from 1.07 to 3.08 ms on the GPU and from 7.3 to 13.3 ms on the CPU
    (151 936-token vocabulary, top-k 20 + top-p 0.9, best of 5 rounds of 500
    steps). End to end, on a machine loaded by other processes, seeded
    generation came out 4 to 6% slower than the old path on the GPU (3 runs
    of 10 x 64 tokens) and 12% on the CPU (1 run of 5 x 32 tokens).

    Beam sampling (`num_beams > 1`), assisted/prompt-lookup decoding and
    DoLa sample in code a logits processor cannot steer; in those modes the
    seed still goes through `transformers.set_seed`, process-wide.

## Structured output (validated JSON)

Force the model to return a Pydantic schema and get the validated instance
back — instead of hoping the output happens to be parseable JSON:

```python
import asyncio

from pydantic import BaseModel
from tempest_fastapi_sdk.genai import OllamaGenerator


class Person(BaseModel):
    name: str
    age: int


gen = OllamaGenerator("llama3.2")


async def main() -> None:
    """Run this example."""
    person: Person = await gen.generate_structured("Any person.", Person)
    # -> Person(name="...", age=...)


asyncio.run(main())
```

`OllamaGenerator` sends the schema in the daemon's `format` field (Ollama
enforces JSON-schema-valid output natively) and parses the reply — **this is
the recommended structured route, no extra library**.

!!! warning "A long document needs `num_ctx` before anything else"
    Ollama's context window defaults to **4096 tokens**, and whatever does
    not fit is truncated silently — the extraction comes back with a few
    items, or none, and no error at all. Measured against `gpt-oss:20b`
    (Ollama 0.30.11) over a 24,086-character tender with 20 planted items,
    three runs per configuration:

    | `num_ctx` | instruction in `system` | instruction concatenated |
    | --- | --- | --- |
    | 4096 (default) | 0, 6, 5 items | error, 2, 1 items |
    | 16384 | **20, 20, 20** | **20, 20, 20** |

    With the window sized for the document, both shapes get everything.
    With the default window both degrade — the instruction in a `system`
    turn degrades less, because it is not competing for room with the
    document being truncated, and that is why the separation is still
    worth it:

    ```python
    await generator.generate_structured(
        document, Schema, system="...", num_ctx=16384
    )
    ```

    Size `num_ctx` by the document (a rough rule: one token per ~3
    characters, plus room for the schema and the answer), and keep the
    instruction in `system=`.

??? info "Why the call goes to `/api/chat` and not `/api/generate`"
    On a reasoning (harmony) model such as `gpt-oss`, `/api/generate`
    **with** `format` answers `200 OK` with a non-zero `eval_count` and an
    **empty** `response` — the reply lands in a reasoning channel that
    endpoint does not surface. Without `format` it works; with `format` it
    does not. `/api/chat` returns the JSON in `message.content`, and
    non-reasoning models behave identically on either. Since v0.229.0 the
    call uses `/api/chat`, and empty content raises `ValueError` instead of
    returning nothing.

!!! info "Every field goes into `required` on the wire — defaulted ones too"
    Pydantic leaves a defaulted field out of `required` in the JSON schema,
    and the daemon's constrained decoder lets the model omit it. The object
    comes back valid (the default fills the field) and wrong, with no signal
    at all. So `generate_structured` and `chat_structured`
    send as `format` a copy of the schema where **every** object (root,
    nested, list item, `$defs`) lists all of its properties in `required`.
    An optional field (`X | None`) still accepts `null` — the model just has
    to say it. Validation still runs against your model, unchanged.

    Measured in the 2026-11-26/27 local-LLM study (Ollama 0.30.11, 33
    extraction cases over 3 schemas, temperature 0), mean per-field
    accuracy:

    | model | Pydantic schema | every field `required` |
    | --- | --- | --- |
    | `gpt-oss:20b` | 70.8 | **95.4** |
    | `qwen2.5:3b` | 69.2 | 87.3 |
    | `lfm2.5-thinking:1.2b` | 56.6 | 74.3 |
    | `qwen2.5-coder:14b` | 90.0 | 95.7 |
    | `ministral-3:14b` | 84.4 | 89.9 |

    On `gpt-oss:20b` the Pydantic schema never produced a
    `Field(default_factory=list)` field: all 11 orders came back with
    `items=[]`. You no longer need to declare the extraction schema without
    defaults.

!!! tip "Instruction separated from the document — `chat_structured`"
    Extracting fields from a long document, the instruction belongs in a
    `system` turn and the content in a `user` one. Concatenating both
    into a single prompt degrades schema adherence **measurably**: the
    model starts answering *with* passages of the document.

    ```python
    import asyncio

    from pydantic import BaseModel
    from tempest_fastapi_sdk.genai import OllamaGenerator


    class Invoice(BaseModel):
        number: str
        total_cents: int


    gen = OllamaGenerator("gpt-oss:20b")


    async def main() -> None:
        """Run this example."""
        invoice: Invoice = await gen.chat_structured(
            [
                {"role": "system", "content": "Extract the invoice fields."},
                {"role": "user", "content": "NF-1 — total R$ 49.90"},
            ],
            Invoice,
        )


    asyncio.run(main())
    ```

    `format` goes at the **top level** of the body, which is where the
    daemon reads it. Passing the schema as a keyword to `chat()` lands it
    in `options`, and Ollama **ignores it silently**: `200 OK` with free
    text, and the failure only shows up at the `ValidationError` — or at
    a parse that happens to succeed. That is why `chat_structured`
    rejects an explicit `format=`.

!!! info "On the local backend (transformers)"
    `TextGenerator.generate_structured(prompt, schema, constrained=True)`
    constrains decoding with `lm-format-enforcer` (the `[genai-structured]`
    extra) so the model can only emit tokens that keep the JSON valid — the
    adapter is built from the library's stable core, so it works on
    transformers 4.x **and 5.x** (validated on Qwen2.5-3B). `constrained=False`
    stays available for best-effort parsing without the extra.

    Since v0.242.0 it also exposes `chat_structured(messages, schema)`, the
    **same** call the daemon answers: a service that reads documents types
    against `StructuredTextBackend` and runs on either without a line
    changing at the call site. The instruction/document split matters here
    too — the tokenizer's chat template is applied before generating.

    ```python
    import asyncio
    import threading

    from pydantic import BaseModel
    from tempest_fastapi_sdk.genai import StructuredTextBackend, TextGenerator


    class Invoice(BaseModel):
        number: str
        total_cents: int


    async def read(backend: StructuredTextBackend) -> Invoice:
        """Read the invoice with any structured backend.

        Args:
            backend (StructuredTextBackend): Ollama daemon or local model.

        Returns:
            Invoice: The extracted fields.
        """
        return await backend.chat_structured(
            [
                {"role": "system", "content": "Extract the invoice fields."},
                {"role": "user", "content": "INV-1 — total $49.90"},
            ],
            Invoice,
        )


    async def main() -> None:
        """Run this example."""
        stop = threading.Event()
        local = TextGenerator("Qwen/Qwen2.5-3B-Instruct")
        invoice: Invoice = await local.chat_structured(
            [{"role": "user", "content": "INV-1 — total $49.90"}],
            Invoice,
            stop_event=stop,
        )
        del invoice


    asyncio.run(main())
    ```

!!! info "Cancelling the call stops the local model"
    Local generation runs in a thread, and a thread cannot be cancelled
    from outside. So `TextGenerator` (and `VisionTextGenerator`) runs every
    call under an event that the transformers stopping criteria check after
    every token, and sets it when the call is cancelled: an
    `asyncio.timeout`, an agent budget or
    [`run_cancellable`](../jobs.en.md#8-progress-the-bar-that-does-not-lie)
    stop decoding within one token. Measured with
    `Qwen/Qwen2.5-0.5B-Instruct` on CPU, 300 forced tokens, cancelled after
    1 s: the thread used to keep decoding 9.3 to 10.8 s after the cancellation
    (across separate measurements); it
    now ends in 0.01 to 0.02 s. `stop_event` still serves to stop **without**
    cancelling (from another thread, say), and it is set on cancellation
    too. On the daemon none of this is needed: aborting the HTTP request
    stops the generation.

!!! tip "Just the parse"
    `parse_structured(text, schema)` pulls the JSON out of a raw completion
    (tolerating Markdown fences and surrounding prose) and validates it against
    the schema — reusable on any model output.

### When the answer is a **list**

A prompt asking "extract the tasks from this text" comes back as an array,
not an object. `parse_structured` looks for `{`…`}` and will not do; use
the list pair:

```python
from pydantic import BaseModel
from tempest_fastapi_sdk.genai import extract_json_list, parse_structured_list


class Task(BaseModel):
    title: str
    due: str | None = None


raw = 'Sure! Here they are: [{"title": "review the contract"}]'

tasks: list[Task] = parse_structured_list(raw, Task)
# -> [Task(title="review the contract", due=None)]

items: list | None = extract_json_list(raw)
# -> [{"title": "review the contract"}]
```

A Markdown fence around the array (the ```` ```json ```` a model adds even
when the prompt asks it not to) is tolerated just the same, whether it
wraps the whole completion or sits buried between two sentences.

- **`parse_structured_list(text, schema)`** validates every item and
  raises like `parse_structured` does. `skip_invalid=True` drops the bad
  item and returns the rest — what you want in a list of suggestions,
  where losing the nine good ones over one malformed item is the worst
  outcome.
- **`extract_json_list(text)`** returns the raw list, or `None` when there
  is no decodable array. **`None` is the "generate it again" signal**, and
  it differs from `[]`, which means "the model answered, and the answer is
  no items". Conflating the two either retries a call that already
  succeeded or gives up on a recoverable formatting slip.
- **`extract_json_object(text)`** is the twin for when the answer is
  **one** record rather than a list: an intent classifier, a routing
  decision, an extraction with fixed fields. Same tolerance for fences and
  prose as `parse_structured`, and the same `None`-means-retry in place of
  an exception — a retry loop written around `except ValueError` is the
  shape that swallows real errors along with format slips.

### Retrying when the model gets the format wrong

`generate_structured_list` joins the two halves: generate, extract, and if
no array came back, **generate again at a higher temperature**.

```python
import asyncio

from pydantic import BaseModel
from tempest_fastapi_sdk.genai import (
    OpenAICompatGenerator,
    StructuredFormatError,
    generate_structured_list,
)


class Task(BaseModel):
    title: str


gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)


async def main() -> None:
    """Run this example."""
    try:
        tasks: list[Task] = await generate_structured_list(
            gen,
            "Extract the tasks. Answer with a JSON array only.",
            Task,
            max_attempts=3,
            temperature_step=0.2,
        )
    except StructuredFormatError as exc:
        print(f"gave up after {exc.attempts} attempts: {exc.last_output}")
    else:
        print(len(tasks))


asyncio.run(main())
```

!!! tip "Why the temperature climbs"
    Repeating the call at the same temperature is close to pointless:
    greedy decoding is deterministic, so attempt two reproduces attempt one
    token for token, and the retry spends a call to get the same unusable
    output. The first attempt stays greedy (the most reliable single shot)
    and each retry adds `temperature_step`, giving sampling a real chance
    to leave the bad state.

!!! warning "A bad item does not cost an attempt"
    Only a **structural** failure — no array in the output at all —
    consumes an attempt. An array that parses but holds one malformed item
    is handled by `skip_invalid=True`, which drops the item and returns the
    rest; losing nine good suggestions over one bad one would be the worst
    outcome.

    For the same reason, **`[]` is a success**: the model answered, and the
    answer is no items. Retrying there re-asks a question already answered.

Takes any backend with `generate(prompt, config=...)` — the local
`TextGenerator`, `OllamaGenerator`, or `OpenAICompatGenerator`.

!!! info "A stray bracket no longer breaks the parse"
    Extraction counts depth instead of slicing to the last `]`/`}`. Before
    v0.235.0 one extra `]` at the end turned a perfect answer into a
    `ValueError`, and the retries
    reproduced the same cut, because the defect was in the slice and not
    in the generation. A non-greedy regex would not fix it: it stops at
    the first `]` and truncates any item with a nested list inside.

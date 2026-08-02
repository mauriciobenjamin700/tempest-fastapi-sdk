# Model weights (Hub lifecycle)

Loading a model by id is the easy half. The other half is everything the
first `from_pretrained` call hides: **which commit** you actually got, **how
many gigabytes** it wrote to **which directory**, whether the disk had room,
and how to make the next boot reproduce the same weights **without a
network**.

The `tempest_fastapi_sdk.genai.hub` module owns that half.

```bash
uv add "tempest-fastapi-sdk[genai-hub]"   # the weight lifecycle alone
uv add "tempest-fastapi-sdk[genai]"       # includes the above + torch/transformers
```

!!! info "The module imports with no extra at all"
    `huggingface_hub` is resolved inside the function that needs it. The
    schemas (`ModelRef`, `CachedModel`) stay usable and testable on a host
    that will never download anything; a missing dependency raises an
    `ImportError` naming the extra to install.

## Where the weights live (and why the second run is instant)

Before any configuration, the fact that answers the most common question:
**the download happens once**. The first call writes the weights to an
on-disk cache; every run after that reads them from there, with no network.

```text
$ python test.py          # first time
model.safetensors: 988MB [00:14, 66.7MB/s]
Loading weights: 100%|██████████| 290/290 [00:00<00:00, 1084 it/s]

$ python test.py          # every time after
Loading weights: 100%|██████████| 290/290 [00:00<00:00, 2335 it/s]
```

The default cache is `huggingface_hub`'s own:

| Where | When |
| --- | --- |
| `$HF_HOME/hub` | `HF_HOME` is set in the environment |
| `~/.cache/huggingface/hub` | the default, when it is not |
| `cache_dir="..."` | you passed the argument to the loader — wins over both |

!!! warning "Container restarts, cache gone"
    The cache lives on the process's filesystem. In a container with no
    volume, every restart downloads everything again — gigabytes of network
    and minutes of boot on each deploy. Mount a volume and point the cache
    at it:

    ```yaml
    # docker-compose.yaml
    services:
      api:
        environment:
          HF_HOME: /models
        volumes:
          - hf-cache:/models

    volumes:
      hf-cache:
    ```

    Or, if you prefer it in code, `TextGenerator(..., cache_dir="/models")`.

!!! tip "That rate-limit warning on stderr"
    ```text
    Warning: You are sending unauthenticated requests to the HF Hub.
    Please set a HF_TOKEN to enable higher rate limits and faster downloads.
    ```

    Anonymous downloads work, but they are throttled. Set `HF_TOKEN` in the
    environment (or pass `hf_token=` to the loader) and the warning goes away
    with the limit — and it is mandatory for a *gated* model such as Llama.

    **And it shows up even when the weights are already cached.** It is not
    the download: with `local_files_only=False` (the default) the load still
    reaches the Hub to resolve the revision, and that anonymous request is
    what prints the warning. With `local_files_only=True` there is no request
    at all — and no warning:

    ```text
    $ python app.py                          # default
    Warning: You are sending unauthenticated requests to the HF Hub...

    $ python app.py                          # local_files_only=True
    (nothing)
    ```

**Changing `model_id` or `revision` is a different cache entry.** That is not
a bug: they are different weights. If a second run downloaded again, one of
those two changed — or the process's `HF_HOME` did.

### Configure it once, in the environment

Passing `cache_dir=` and `local_files_only=` to every loader works, but it
scatters an infrastructure decision through domain code. All three values
have an environment variable — and **the argument always wins**, so the
service sets the default and a call that needs something else still can:

| Variable | Equivalent argument | Effect |
| --- | --- | --- |
| `GENAI_CACHE_DIR` | `cache_dir=` | Where weights are written and read |
| `GENAI_OFFLINE` | `local_files_only=` | Load from cache, never touch the network |
| `GENAI_HF_TOKEN` | `hf_token=` | Authenticate to the Hub (gated + no rate limit) |

```bash
# .env
GENAI_CACHE_DIR=/models
GENAI_OFFLINE=true
GENAI_HF_TOKEN=hf_xxx
```

```python title="settings.py"
from tempest_fastapi_sdk import BaseAppSettings, GenAISettings


class Settings(GenAISettings, BaseAppSettings):
    """The three variables, typed and visible to `tempest check-config`."""
```

!!! info "Declaring the class is optional"
    The loaders read the environment directly — a service that never
    declares `GenAISettings` behaves the same. The class is what makes the
    values typed, documented and visible in the config check.

`GENAI_OFFLINE` accepts `1`, `true`, `yes` and `on`, in any case; anything
else is false. And, repeating it because it is the point: passing the
argument beats the variable **in both directions** — with
`GENAI_OFFLINE=true` in the environment, an explicit `local_files_only=False`
goes back to using the network.

## The problem: `main` moves

This is what nearly every self-hosted service writes on day one:

```python
from tempest_fastapi_sdk.genai import TextGenerator

generator = TextGenerator("Qwen/Qwen2.5-0.5B-Instruct")
```

It works. And it has three holes:

| Hole | What actually happens |
| --- | --- |
| **Unpinned revision** | The author pushes to `main`. The pod restarts. You are serving different weights without having changed a line. |
| **Download inside the request** | The first `POST /generate` pays for a multi-gigabyte download while a client waits on the other end. |
| **No offline mode** | An air-gapped host has no way to *guarantee* the load will not reach the network — it only finds out when it fails. |

All three close with the same three keywords, and they are identical across
every loader in the SDK.

## Pin the revision

First, find the commit behind the branch:

```python
from tempest_fastapi_sdk.genai import resolve_revision

sha: str | None = resolve_revision("Qwen/Qwen2.5-0.5B-Instruct", revision="main")
print(sha)
```

```text
a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2
```

From the terminal, the same thing — alongside the download:

```bash
tempest model pull Qwen/Qwen2.5-0.5B-Instruct --pin
```

```text
Qwen/Qwen2.5-0.5B-Instruct
  revision   : default
  pin to     : a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2
  path       : /home/u/.cache/huggingface/hub/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/a8b602d
  size       : 999.59 MB
  files      : 9
```

Store the sha in the service configuration and pass it down:

```python
from tempest_fastapi_sdk.genai import TextGenerator

generator = TextGenerator(
    "Qwen/Qwen2.5-0.5B-Instruct",
    revision="a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2",
)
```

!!! tip "`resolve_revision` returns `None` when it cannot pin"
    Hub down, private repository without a token, revision that does not
    exist — the function returns `None` instead of raising. The caller
    decides whether to proceed unpinned or abort the deploy; that call is
    yours, not the library's.

## Download before serving

The place to pay for a download is the image build or the deploy step —
never the request path.

```python
from tempest_fastapi_sdk.genai import ModelSnapshot, download_model

snapshot: ModelSnapshot = download_model(
    "Qwen/Qwen2.5-0.5B-Instruct",
    revision="a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2",
    cache_dir="/var/lib/models",
    allow_patterns=["*.json", "*.safetensors"],
)
print(snapshot.size_bytes, snapshot.file_count, snapshot.path)
```

In the `Dockerfile`, one line:

```dockerfile
RUN tempest model pull Qwen/Qwen2.5-0.5B-Instruct \
    --revision a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2 \
    --cache-dir /var/lib/models \
    --allow "*.json" --allow "*.safetensors"
```

!!! check "`--allow` is not a detail"
    Many repositories publish the weights **twice** — `.bin` and
    `.safetensors`. Restricting to the formats you actually load usually cuts
    half the download and half the disk.

### It refuses to start what will not fit

`download_model` sizes the repository on the Hub before writing anything and
compares it against the free space:

```python
from tempest_fastapi_sdk.genai import download_model

try:
    download_model("meta-llama/Llama-3.1-70B", cache_dir="/var/lib/models")
except OSError as exc:
    print(exc)
```

```text
meta-llama/Llama-3.1-70B needs ~154.0 GB (estimate x1.1) but only 41.3 GB are free on /var/lib/models
```

Failing in two seconds with a number beats failing forty minutes later with
a half-written cache. To size it without downloading:

```python
from tempest_fastapi_sdk.genai import model_disk_bytes

needed: int | None = model_disk_bytes("Qwen/Qwen2.5-0.5B-Instruct")
print(needed)
```

!!! note "Disk and memory are different questions"
    `model_disk_bytes` answers "does it fit the volume?". What answers "does
    it fit RAM/VRAM?" is [`can_run`](genai.md) — the two checks are
    independent, and a healthy deploy runs both.

## Run offline

With the weights already cached, `local_files_only=True` turns any load into
a purely local operation:

```python
from tempest_fastapi_sdk.genai import Embedder, TextGenerator

generator = TextGenerator(
    "Qwen/Qwen2.5-0.5B-Instruct",
    revision="a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2",
    cache_dir="/var/lib/models",
    local_files_only=True,
)
embedder = Embedder(
    "sentence-transformers/all-MiniLM-L6-v2",
    cache_dir="/var/lib/models",
    local_files_only=True,
)
```

If the weights are not there the load fails **immediately**, instead of
quietly reaching the network from a host that was supposed to be isolated.

## The same three keywords everywhere

There is no per-class "way to pin":

| Class | Module |
| --- | --- |
| `TextGenerator` | `tempest_fastapi_sdk.genai` |
| `Embedder` | `tempest_fastapi_sdk.genai` |
| `VisionTextGenerator` | `tempest_fastapi_sdk.genai` |
| `ClassifierModerator` | `tempest_fastapi_sdk.genai` |
| `Reranker` | `tempest_fastapi_sdk.genai.rag` |

All of them take `revision=`, `local_files_only=` and `trust_remote_code=`,
on top of the `cache_dir=`/`hf_token=` that already existed. Two exceptions,
both forced by the library underneath:

- **`SpeechToText`** takes `revision=`, `local_files_only=` and `hf_token=`,
  but not `trust_remote_code` — CTranslate2 loads weights, never repository
  Python.
- **`OnnxEmbedder`** already has the graph on disk; only the tokenizer comes
  from the Hub, so the parameters are `tokenizer_revision=` and `hf_token=`.
  Point `tokenizer` at a local `tokenizer.json` when the host must not reach
  the network.

### `trust_remote_code` is opt-in on purpose

```python
from tempest_fastapi_sdk.genai import VisionTextGenerator

generator = VisionTextGenerator(
    "Qwen/Qwen2-VL-2B-Instruct",
    trust_remote_code=True,
)
```

!!! warning "This executes Python from the repository"
    Some architectures only load with `trust_remote_code=True`, and
    `transformers` says so in the error message. Flipping the switch runs code
    you did not review, from the same repository the weights came from — which
    is why it is per-model rather than an SDK default. If you do flip it, pin
    the revision alongside: the code you audited today and the code tomorrow
    are the same `main`.

## See and reclaim the cache

Weights are the biggest thing a self-hosted service writes to disk, and
nothing prunes them: every model ever loaded stays until someone removes it.

```bash
tempest model cache-list --revisions
```

```text
   4.43 GB  Qwen/Qwen2-VL-2B-Instruct
   4.43 GB    a1b2c3d4e5f6  [main]
 999.59 MB  Qwen/Qwen2.5-0.5B-Instruct
 999.59 MB    a8b602d5f1c9  [main]
 181.97 MB  sentence-transformers/all-MiniLM-L6-v2
 181.97 MB    1110a243fdf4  [main]
   5.61 GB  total
```

In Python, to expose it on an operations endpoint:

```python
from tempest_fastapi_sdk.genai import CachedModel, cache_size_bytes, list_cached_models

models: list[CachedModel] = list_cached_models()
for model in models:
    print(model.model_id, model.size_bytes, len(model.revisions))
print(cache_size_bytes())
```

To reclaim space:

```bash
tempest model cache-rm Qwen/Qwen2-VL-2B-Instruct --dry-run
```

```text
would free 4.43 GB by removing Qwen/Qwen2-VL-2B-Instruct
```

```python
from tempest_fastapi_sdk.genai import remove_cached_model

freed: int = remove_cached_model(
    "Qwen/Qwen2-VL-2B-Instruct",
    revision="a1b2c3d4e5f6",
)
print(freed)
```

!!! danger "Removing weights has no undo"
    The only way back is downloading them again. That is why the command asks
    for confirmation (skip it with `--yes`) and `--dry-run` reports the size
    without touching anything. A model that is not cached returns `0` — not an
    error, a successful no-op.

## The object underneath: `ModelRef`

Every loader builds a `ModelRef` and forwards it. You rarely need to
construct one by hand, but it is what explains the behaviour:

```python
from tempest_fastapi_sdk.genai import ModelRef

ref = ModelRef(
    model_id="Qwen/Qwen2.5-0.5B-Instruct",
    revision="a8b602d",
    local_files_only=True,
)
print(ref.loader_kwargs())
print(ref.download_kwargs())
```

```text
{'revision': 'a8b602d', 'local_files_only': True}
{'revision': 'a8b602d', 'local_files_only': True}
```

Only what **differs from the default** is emitted. That keeps the call
identical to what the SDK sent before when nothing is pinned, and keeps the
same dictionary usable with narrower loaders —
`tokenizers.Tokenizer.from_pretrained` accepts `revision` but not
`trust_remote_code`. `download_kwargs()` drops `trust_remote_code`, which is
a load-time decision and means nothing while fetching files.

## Recap

- **`resolve_revision`** turns `main` into the immutable sha. Pin it and the
  boot stops depending on the day.
- **`download_model`** / **`tempest model pull`** pays for the download in
  the build or the deploy, and refuses to start what the disk cannot hold.
- **`local_files_only=True`** makes the load purely local — the mode an
  air-gapped host wants.
- **`trust_remote_code=True`** is opt-in per model, because it executes
  repository code.
- **`list_cached_models`** / **`remove_cached_model`** (and `cache-list` /
  `cache-rm`) show and reclaim what the weights occupy.

Where to go next: [Self-hosted generative AI](genai.md) for what to do with
the weights once loaded, and [Modelops](modelops.md) to measure, quantize and
export them.

# Queue, email, storage, flags, cache, models and doctor

Use to operate the infrastructure from the command line: queue and tasks, email test, object store, feature flags, cache, models and diagnostics.

Part of the [CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli.md) recipe.

## Queue and tasks — `tempest queue` / `tempest tasks`

```bash
# Publish a message through the project's broker
tempest queue publish orders.paid '{"order_id": 7}' --json
tempest queue handlers                     # channels this service consumes

# Background tasks
tempest tasks list                         # registered names
tempest tasks run src.tasks:send_welcome --arg ana@example.com --kwarg retries=2
```

`publish` connects, publishes and **closes** — in that order, in one
process. Closing is the step a throwaway script forgets, and it is the one
that flushes the publish before the command returns.

!!! tip "A task's name is not its function's name"
    TaskIQ registers under `<module>:<function>` (`src.tasks:send_welcome`),
    so the name `run` takes is not the one in the source file. `list` is what
    tells you which is which — and it imports `<root>.tasks.jobs` first,
    because a task exists on the broker only after the module declaring it
    has been imported.

`tasks run` **enqueues**; it does not execute. The worker runs the job, so a
command that returns successfully means the message was accepted, not that
the job succeeded.

Without `--broker` / `--queue`, both probe `<root>.queue:broker` and
`<root>.tasks:tq` — the names `tempest generate --src` writes. When nothing
resolves, every attempt is listed with its cause before exit 2.

## Email — `tempest email test`

The pair that costs the most time in SMTP setup is STARTTLS (port 587,
`SMTP_USE_TLS`) against implicit TLS (port 465, `SMTP_USE_SSL`), and the only
honest way to learn which one a server wants is to connect and send.

```bash
tempest email test --to you@example.com
tempest email test --to you@example.com --subject "deploy ok" --body "..."
tempest email test --to you@example.com --html      # exercises the multipart path
```

It builds the client with `EmailUtils(**settings.email_kwargs())` — the same
construction the service performs — so a message that arrives here arrives
from the app. A refusal exits 1 carrying the server's own reply, plus the
line that recalls the TLS pair.

A project that composes no `EmailSettings` exits 2 saying so: composing only
the mixins you use is ordinary, not broken.

## Object store — `tempest storage`

```bash
tempest storage check                       # does the endpoint answer? bucket there?
tempest storage ls                          # keys in the default bucket
tempest storage ls reports/ --flat          # only the immediate level
tempest storage put ./note.pdf --key notes/2026-09.pdf
tempest storage get notes/2026-09.pdf --out ./downloaded.pdf
tempest storage presign notes/2026-09.pdf --expires 900
tempest storage rm notes/2026-09.pdf
```

Everything goes through `AsyncMinIOClient(**settings.storage_kwargs())`, so the
endpoint, credentials, region and — the one that matters most — the **public
endpoint** used to sign URLs are the service's own.

!!! warning "A URL signed with the internal endpoint is valid and useless"
    Signing against `minio:9000` produces a correct URL your user's browser
    cannot resolve. Set `STORAGE_PUBLIC_ENDPOINT`; `presign` prints on `stderr`
    which host it signed for, precisely so that mistake surfaces before the
    URL reaches a client.

An empty bucket lists nothing and exits 0 — "no object" is a result, not a
failure, the same convention the SDK's repositories follow.

Two commands act on the local backend (`STORAGE_BACKEND=local`), through
`LocalObjectStorageClient(**settings.local_storage_kwargs())`, and migrate the
volume to [encryption at rest](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/local-storage.md#encryption-at-rest):

```bash
tempest storage encrypt-local               # encrypt what is plaintext, every bucket
tempest storage reencrypt-local --bucket uploads   # move to the first key
```

Both are idempotent and exit with code 2 without
`STORAGE_LOCAL_ENCRYPTION_KEYS`.

## Feature flags — `tempest flags`

A flag exists to be moved without a deploy. With the Redis backend that move
lived in `redis-cli HSET feature_flags <name> 1` — the kind of command that
gets a digit wrong at the wrong hour.

```bash
tempest flags list                       # everything the backend holds
tempest flags enable new-checkout        # on for every process reading it
tempest flags disable new-checkout
tempest flags get new-checkout           # the stored value
tempest flags list --key ff:staging      # another hash, another environment
```

```text
new-checkout  on
legacy-upload off
```

All four speak through `RedisFeatureFlagBackend`, so the value the service
reads and the value the CLI writes go through the same encoding code.

!!! note "`off` and `unset` are different answers"
    `get` exits **1** when the backend holds no value (`unset`) and **0** when
    it holds `off`. The difference matters: an unset flag resolves to the
    *default* the caller passed in code, while `off` overrides that default.

The URL comes from `--redis-url` > `REDIS_URL` > the project's settings. A
Redis that is down exits 2 carrying the driver's own message — "connection
refused" names the fix, "an error occurred" does not.

## Cache — `tempest cache`

```bash
tempest cache ping                       # PONG, or exit 1
tempest cache stats                      # version, keys, memory, hit ratio
tempest cache flush --namespace orders   # invalidate what @cached registered
tempest cache flush --tag user:42
tempest cache flush --key "cache:orders:list:abc"
tempest cache flush --all --yes          # FLUSHDB
```

The targeted options go through `CacheInvalidator` — the same code the
service calls — so the keys deleted here are the keys `@cached` wrote there.
Pass the same `--key-prefix` those decorators use, or the registries will not
line up.

!!! danger "`--all` is `FLUSHDB`, not 'clear the cache'"
    It deletes **every** key in the selected database: sessions, rate-limit
    counters and feature flags included, when they share the Redis. That is
    why it demands `--yes`. When in doubt, invalidate by namespace.

!!! info "The hit ratio is the server's, not your application's"
    `keyspace_hits` / `keyspace_misses` are Redis counters since the last
    restart, summed across every client. Redis does not attribute a hit to a
    caller — the number answers "is this server earning its keep", not "is my
    endpoint X".

## Models — `tempest model`

Analyze, benchmark, convert and quantize ONNX models. Needs the
`[modelops-onnx]` extra, except `hardware`, which only reports what this
host can measure:

```bash
tempest model analyze models/classify.onnx
tempest model bench models/classify.onnx --dim height=224 --dim width=224
tempest model quantize models/classify.onnx models/classify.int8.onnx
tempest model export-ort models/classify.int8.onnx -o dist/mobile -t arm
tempest model hardware
```

| Command | What it does |
| --- | --- |
| `analyze` | Parameters, size, opset and shapes, without running the model. |
| `bench` | Latency (median/IQR/p95/p99), RAM, GPU and energy over N repetitions. |
| `optimize` | Persists ONNX Runtime's graph optimizations into a new `.onnx`. |
| `quantize` | Dynamic int8 quantization. |
| `export-ort` | Converts to `.ort` plus `.required_operators.config`. |
| `hardware` | What the host runs and which energy sampler is available. |

`analyze`, `bench` and `hardware` accept `--json`, which makes them usable
as a CI step. A missing extra exits 2 with the install line, never a
traceback. Details in [Modelops](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/modelops.md).

## Diagnostics — `tempest doctor`

`check-config` reads the settings and reasons about them; it never opens a
socket. That is the right trade for a fast gate and the wrong one for "why
does the service not come up", where the answer is almost always a dependency
that is down, unreachable from this host, or holding credentials nobody
updated.

```bash
tempest doctor
tempest doctor --json            # for scripts / deployment health checks
tempest doctor --timeout 2       # tighten the wait on each network probe
```

```text
OK    python         3.11.12 (/srv/app/.venv/bin/python)
OK    sdk            tempest-fastapi-sdk 0.293.0
OK    settings       Settings
OK    database       postgresql+asyncpg://app:***@db:5432/app
FAIL  redis          ConnectionError: Error 111 connecting to cache:6379
SKIP  rabbitmq       no RABBITMQ_URL
SKIP  smtp           SMTP_HOST is still the mixin default
SKIP  minio          the [minio] extra is not installed
OK    config checks  no error (2 warning(s))
```

Every line is a **real connection**, not a deduction. It exits 1 when any
check fails, so it works as a deployment smoke test.

!!! warning "`skip` is not `ok`, on purpose"
    A capability the project never configured reads `skip`. A green line for a
    Redis nobody set up is the kind of reassurance that costs an outage.

    The mixin default counts as unconfigured: `EmailSettings` ships
    `SMTP_HOST=localhost` and `StorageSettings` ships
    `STORAGE_ENDPOINT=localhost:9000`, so a service composing the mixin without
    using the capability would look configured. The comparison is against
    `model_fields[field].default` — the same ruler `check_secrets` uses.

The last line runs the static check registry (the one behind `check-config`),
so one command answers both halves of "is this deployment sane".

# System checks (`tempest check-config`)

Validate configuration **before** serving traffic — empty signing
secret, CORS `*` with credentials, SQLite in production. A Django-style
check framework: functions inspect your settings and emit messages; the
CLI (or a startup hook) runs them all and fails if any is serious.

## The problem

A deploy with an empty `JWT_SECRET` boots happily and only breaks (or
worse, accepts forged tokens) in production. Config errors don't show up
in tests — they depend on the environment. There was no place to declare
"this must be true to ship".

## Running the built-in checks

The SDK ships checks for the most common slips. Run them against your
settings:

```bash
tempest check-config
```

The CLI auto-detects the settings object at conventional locations
(`src.core.settings:settings`, `app.core.settings:settings`, …). Point it
explicitly when needed:

```bash
tempest check-config --settings src.core.settings:settings
```

Typical output:

```text
WARNING: (security.W004) JWT_SECRET still holds the default declared by its settings field — a deployment that never set it signs tokens with a value that lives in source control.
	HINT: Generate one with `tempest secrets rotate`.
INFO: (deployment.I001) SERVER_DEBUG is enabled.
	HINT: Ensure SERVER_DEBUG is off in production (it leaks internals).
2 message(s), 0 at/above ERROR.
```

It exits **non-zero** when any message reaches `--fail-level` (default
`error`) — so it doubles as a CI gate and a pre-deploy check. Raise the
bar to treat warnings as blocking:

```bash
tempest check-config --fail-level warning
```

Built-in checks (all best-effort — they skip silently when the attribute
is absent from your settings):

| id | Level | What |
|----|-------|------|
| `security.W001` / `W002` | WARNING | `JWT_SECRET` / `SECRET_KEY` / `TOKEN_SECRET` empty or < 32 chars |
| `security.W003` | WARNING | CORS `*` **with** credentials |
| `security.W004` | WARNING | secret still equal to the default declared on its own field |
| `database.W001` | WARNING | SQLite `DATABASE_URL` with debug off |
| `deployment.I001` | INFO | `SERVER_DEBUG` (or `DEBUG`) on |
| `deployment.I002` | INFO | bind on `0.0.0.0` |

!!! warning "The secret that passed was the only one guaranteed to be wrong"
    Up to v0.271.0, `security.W002` only rejected a secret shorter than
    32 characters — and the placeholder the SDK itself ships in
    `JWTSettings.JWT_SECRET` is **exactly** 32. A deployment that forgot
    to set `JWT_SECRET` signed tokens with a value published in the SDK
    source, and `tempest check-config` reported everything as fine.

    `security.W004` compares the value against
    `model_fields[name].default`, never against a copy of the string —
    so it keeps holding the day the placeholder changes.

!!! note "Which field the debug check reads"
    `deployment.I001` and `database.W001` resolve debug by name
    precedence: `SERVER_DEBUG` first (the name the `ServerSettings`
    mixin declares), then `DEBUG`, so a project that declared one by
    hand keeps working. Up to v0.271.0 both read only `DEBUG` — `I001`
    never fired, and `database.W001` warned backwards, complaining
    about SQLite precisely when debug was **on**.

## Writing your own check

A check is a function that receives the context (your settings) and
returns messages. Decorate it with `@check`:

```python
from tempest_fastapi_sdk.checks import check, error, CheckMessage


@check("security")
def stripe_key_present(settings: object) -> list[CheckMessage]:
    """Fail the deploy if the Stripe key is not configured."""
    if not getattr(settings, "STRIPE_API_KEY", ""):
        return [
            error(
                "STRIPE_API_KEY is not set.",
                hint="Export it before deploying the billing service.",
                id="billing.E001",
            )
        ]
    return []
```

The `debug` / `info` / `warning` / `error` / `critical` constructors
build a `CheckMessage` at the right level. The tag (`"security"`) lets
you run a subset:

```bash
tempest check-config --tag security
```

!!! note "Checks must be imported to register"
    `@check` registers on module import. The CLI imports your settings
    (and whatever they import), so checks defined next to the settings
    load themselves. For standalone modules, use `--import`:

    ```bash
    tempest check-config --import src.checks --import src.billing.checks
    ```

## Failing fast at startup

Run the checks in the lifespan so a misconfigured deploy does **not**
serve traffic:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from tempest_fastapi_sdk.checks import run_system_checks, SystemCheckError

from src.core.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    try:
        run_system_checks(settings)   # raises on ERROR+
    except SystemCheckError as exc:
        # log exc.messages and abort the boot
        raise
    yield
```

`run_system_checks` raises `SystemCheckError` when any message reaches
`fail_level` (default `ERROR`); `run_checks` does the same but only
returns the list, without raising.

## Production guard: `EnvironmentSettings`

The checks above **warn**. For what must never reach production — SQLite,
the placeholder `JWT_SECRET`, CORS `*` — a warning is not enough: the deploy
has to fail. That is what the `EnvironmentSettings` mixin does. It adds the
`ENV` field (`development`, `test` or `production`) and, with
`ENV=production`, refuses to build the settings while any composed mixin
still holds a development value:

```python
import os

from pydantic import ValidationError
from tempest_fastapi_sdk import (
    BaseAppSettings,
    DatabaseSettings,
    EnvironmentSettings,
    JWTSettings,
    ServerSettings,
)


class Settings(
    EnvironmentSettings,
    ServerSettings,
    DatabaseSettings,
    JWTSettings,
    BaseAppSettings,
):
    """Service settings, with the production guard."""


os.environ["ENV"] = "production"

try:
    Settings()
except ValidationError as exc:
    print(exc)
```

With neither `DATABASE_URL` nor `JWT_SECRET` in the environment, the output
lists **every** field at once — by name, never by value:

```text
1 validation error for Settings
  Value error, ENV=production refuses development values:
- DATABASE_URL: points at SQLite (the development default)
- JWT_SECRET: still the public placeholder declared by JWTSettings [type=value_error]
```

With `ENV=development` (the default) or `ENV=test`, nothing changes.

### What each mixin refuses

The guard only looks at the mixins you compose. Each one declares its own
rules in its `production_violations()` method:

| Mixin | Refused in production |
| --- | --- |
| `ServerSettings` | `SERVER_DEBUG=true`, `SERVER_RELOAD=true` |
| `DatabaseSettings` | a `DATABASE_URL` with a `sqlite` dialect |
| `JWTSettings` | `JWT_SECRET` equal to the default the mixin declares |
| `CORSSettings` | `"*"` in `CORS_ORIGINS` |
| `TokenSettings` | an empty `TOKEN_SECRET` (turns `X-Token` off) |
| `TaskIQSettings` | an empty `TASKIQ_BROKER_URL` (falls back to the in-memory broker) |
| `StorageSettings` | the default `minioadmin` keys, `STORAGE_SECURE=false`, an explicit `STORAGE_PUBLIC_SECURE=false` |

!!! info "The placeholder comes from the mixin, not from a literal"
    `JWT_SECRET` is compared with `JWTSettings.model_fields["JWT_SECRET"].default`.
    If the default changes, the check follows it.

### Adding (or waiving) a rule

`production_violations()` is cooperative: override it on your `Settings`,
call `super()` and work on the list. That is how you add a rule of the
service — or drop one you accepted on purpose, like SQLite on a single-node
service:

```python
import os

from pydantic import Field, ValidationError
from tempest_fastapi_sdk import BaseAppSettings, DatabaseSettings, EnvironmentSettings


class Settings(EnvironmentSettings, DatabaseSettings, BaseAppSettings):
    """Single-node service: SQLite in production is a decision already made."""

    PUBLIC_URL: str = Field(default="http://localhost:8000")

    def production_violations(self) -> list[str]:
        """Accept SQLite and require HTTPS on the public URL.

        Returns:
            list[str]: The mixins' violations, minus SQLite, plus the
            service's own rule.
        """
        violations: list[str] = [
            entry
            for entry in super().production_violations()
            if not entry.startswith("DATABASE_URL:")
        ]
        if not self.PUBLIC_URL.startswith("https://"):
            violations.append("PUBLIC_URL: not HTTPS")
        return violations


os.environ["ENV"] = "production"

try:
    Settings()
except ValidationError as exc:
    print(exc)

os.environ["PUBLIC_URL"] = "https://api.example.com"
print(Settings().ENV)
```

```text
1 validation error for Settings
  Value error, ENV=production refuses development values:
- PUBLIC_URL: not HTTPS [type=value_error]
production
```

!!! tip "A validation error does not print the environment"
    `BaseAppSettings` now sets `hide_input_in_errors=True`. On a
    `BaseSettings` the *input* of an error is the whole environment — a
    missing required field used to print `input_value={'JWT_SECRET': ...}`
    into the boot traceback. Now the message carries only the field and the
    reason. Set `hide_input_in_errors` in your own `model_config` to get the
    input back.

## Recap

- `tempest check-config` runs the checks against your settings; exits
  non-zero at `--fail-level` (default `error`).
- Built-ins cover secret, CORS, SQLite-in-prod, DEBUG, bind.
- `@check("tag")` registers your own; `debug`/`info`/`warning`/`error`/
  `critical` build the message.
- `run_system_checks(settings)` in the lifespan aborts a misconfigured boot.
- `EnvironmentSettings` with `ENV=production` **stops** the boot while a
  composed mixin still holds a development value, listing the fields
  without printing values; `production_violations()` + `super()` adds or
  waives a rule.

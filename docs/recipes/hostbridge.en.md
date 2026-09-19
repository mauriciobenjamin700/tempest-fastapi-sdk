# Host control (files, commands, power)

Your service runs inside WSL and needs to act on the **machine** hosting it:
read a file under `C:\Users\...`, run a PowerShell command, open the native
file picker, power the computer down at the end of the day.
`tempest_fastapi_sdk.hostbridge` is that layer — the one that used to live
as a separate service in every project that needed it.

Nothing here mounts itself: `create_app` does not know this module, and the
router exists because a service asked for it.

```bash
uv add "tempest-fastapi-sdk"
```

The system, file and command surface needs no extra at all — it is the
standard library and FastAPI. Reading PDFs needs `[pdf-read]`, and reading
them **with layout and tables** needs `[pdf-layout]`.

!!! danger "This is a shell on that machine"
    Whoever reaches these methods runs commands as the host user and powers
    the machine off. Two things follow, and neither is optional:
    `allowed_base_paths` starts **empty** — denying every path until someone
    configures it — and `make_hostbridge_router` **requires** auth
    dependencies, refusing an empty list. The write side (commands, writes,
    deletes, power) is behind `destructive=True` and off by default.

## The first read

`HostBridge` takes a `HostBridgeConfig` and nothing else. Everything is
`async`, and whatever touches the disk or a process runs off the event loop:

```python
import asyncio

from tempest_fastapi_sdk.hostbridge import HostBridge, HostBridgeConfig


async def main() -> None:
    bridge = HostBridge(
        HostBridgeConfig(allowed_base_paths=("/mnt/c/Users/me/Documents",)),
    )

    info = await bridge.host_info()
    print(info.computer_name, info.os_version)

    file = await bridge.read_text("C:\\Users\\me\\Documents\\notes.txt")
    print(file.size_bytes, file.content[:80])


asyncio.run(main())
```

Look at that path: it was written in **Windows** form and it worked. The
bridge takes both — `C:\Users\me` and `/mnt/c/Users/me` — and normalizes
before checking, through `wslpath` where it exists.

!!! warning "Order matters: resolve first, check second"
    The path is resolved (`..` collapsed, symlinks followed) **before** it is
    compared against `allowed_base_paths`. Checking before resolving is the
    classic bug: `/mnt/c/Users/me/../../../etc/passwd` starts inside an
    allowed base and ends outside it.

## Configuration from the environment

In a service, these options belong on `Settings`. Compose the mixin and
splat the mapper — the field → argument translation lives in one place:

```python
from tempest_fastapi_sdk.settings import HostBridgeSettings, ServerSettings
from tempest_fastapi_sdk.hostbridge import HostBridge, HostBridgeConfig


class Settings(HostBridgeSettings, ServerSettings):
    """Only what is specific to this service is declared here."""


settings = Settings()
bridge = HostBridge(HostBridgeConfig(**settings.hostbridge_kwargs()))
```

The environment variables are the field names themselves:
`HOST_ALLOWED_BASE_PATHS`, `HOST_POWERSHELL_BINARY`, `HOST_CMD_BINARY`,
`HOST_COMMAND_TIMEOUT` and `HOST_MAX_FILE_READ_BYTES`.

!!! info "`HOST_ALLOWED_BASE_PATHS` has no permissive default"
    Empty denies everything. That is the safe direction to fail: a bridge
    that can run PowerShell should not also be able to read the whole disk
    because nobody remembered to configure it.

## Exposing it over HTTP

`make_hostbridge_router` returns an `APIRouter` ready for `include_router`.
It **requires** the dependency list — the signature is what forces the auth
decision, instead of a warning in the docs:

```python
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import make_token_dependency, register_exception_handlers
from tempest_fastapi_sdk.hostbridge import (
    HostBridge,
    HostBridgeConfig,
    make_hostbridge_router,
)

app = FastAPI()
register_exception_handlers(app)

bridge = HostBridge(
    HostBridgeConfig(allowed_base_paths=("/mnt/c/Users/me/Documents",)),
)

app.include_router(
    make_hostbridge_router(
        bridge,
        dependencies=[Depends(make_token_dependency("a-long-secret"))],
    )
)
```

That mounts the side that **reads**:

| Method | Route | What it does |
| --- | --- | --- |
| `GET` | `/system/info` | Machine name, user, OS, uptime |
| `GET` | `/system/files` | Read a text file |
| `GET` | `/system/files/list` | List a directory |
| `GET` | `/system/files/pdf` | Extract a PDF, page by page |
| `POST` | `/system/files/pick` | Open the native file picker |

The side that **writes** — `POST /system/exec`, `POST`/`DELETE
/system/files`, `POST /system/{shutdown,restart,abort,lock,logoff}` — only
appears with `destructive=True`:

```python
from fastapi import Depends

from tempest_fastapi_sdk import make_token_dependency
from tempest_fastapi_sdk.hostbridge import (
    HostBridge,
    HostBridgeConfig,
    make_hostbridge_router,
)

bridge = HostBridge(
    HostBridgeConfig(allowed_base_paths=("/mnt/c/Users/me/Documents",)),
)

router = make_hostbridge_router(
    bridge,
    dependencies=[Depends(make_token_dependency("a-long-secret"))],
    destructive=True,
)
```

!!! tip "Turn the destructive side on only with someone there to confirm"
    An assistant that runs commands without human confirmation is one that
    deletes the wrong folder exactly once. The pattern that works is holding
    the call until the user approves it, showing the exact arguments.

## Errors, with a code

Every failure raises one of the SDK's envelope exceptions, so a service that
already calls `register_exception_handlers` answers `{detail, code, details}`
with no extra wiring — in PT-BR or en-US, per `Accept-Language`:

| Code | Status | When |
| --- | --- | --- |
| `HOST_INVALID_PATH` | 400 | Path outside the bases, or untranslatable |
| `HOST_FILE_NOT_FOUND` | 404 | No file there |
| `HOST_FILE_TOO_LARGE` | 413 | Over `max_file_read_bytes` |
| `HOST_FILE_DECODE_FAILED` | 400 | Bytes do not decode — a PDF or an image |
| `HOST_COMMAND_FAILED` | 500 | The command exited non-zero |
| `HOST_COMMAND_TIMEOUT` | 504 | The command ran past its timeout and was killed |
| `HOST_UNAVAILABLE` | 503 | Neither PowerShell nor `wslpath` exists here |

`HOST_UNAVAILABLE` is what tells "this machine has no Windows host" apart
from "the action failed": on a plain Linux container the caller degrades
instead of reporting an error.

## Reading a PDF from the host

```python
import asyncio

from tempest_fastapi_sdk.hostbridge import HostBridge, HostBridgeConfig
from tempest_fastapi_sdk.pdf import PdfExtractor


async def main() -> None:
    bridge = HostBridge(
        HostBridgeConfig(allowed_base_paths=("/mnt/c/Users/me/Documents",)),
    )
    document = await bridge.read_pdf(
        "C:\\Users\\me\\Documents\\contract.pdf",
        extractor=PdfExtractor.LAYOUT,
    )
    for page in document.pages:
        print(page.page_number, len(page.tables), page.text[:60])


asyncio.run(main())
```

`PdfExtractor.TEXT` is `pypdf` — fast, plain prose, `[pdf-read]`.
`PdfExtractor.LAYOUT` is `pdfplumber`, `[pdf-layout]`: slower, but it
recovers columns and fills `page.tables`.

!!! warning "There is no OCR here"
    A scanned page is an image with no text layer: it comes back with
    `text=""` rather than being dropped — entry `n` is always page `n`.
    Check for it and route those files elsewhere; handing a model an empty
    page is how a confident answer gets invented about something nobody read.

## Running commands

```python
import asyncio

from tempest_fastapi_sdk.hostbridge import (
    CommandSchema,
    HostBridge,
    HostBridgeConfig,
)


async def main() -> None:
    bridge = HostBridge(HostBridgeConfig())
    result = await bridge.run_command(
        CommandSchema(command="Get-Date -Format o", shell="powershell", timeout=10),
    )
    print(result.return_code, result.stdout.strip(), result.duration_seconds)


asyncio.run(main())
```

!!! note "Elevation (UAC) does not happen from here"
    A command that needs administrator rights fails. Wrap it through `gsudo`
    or a pre-created Task Scheduler entry on the Windows side.

## Recap

- `HostBridge` + `HostBridgeConfig` give you the whole surface;
  `HostBridgeSettings` is the same thing from the environment, via
  `hostbridge_kwargs()`.
- An empty `allowed_base_paths` denies everything, and a path is resolved
  before it is checked.
- `make_hostbridge_router` requires auth by signature, and only mounts the
  write side with `destructive=True`.
- Every failure carries a `code` — `HOST_UNAVAILABLE` says this machine has
  no host to control.
- PDFs: `[pdf-read]` for text, `[pdf-layout]` for columns and tables, no OCR
  in either.

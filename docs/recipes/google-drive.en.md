---
description: "Use to store uploads in a Google Drive folder or Shared Drive via a service account with `GoogleDriveUploadStorage`, from Cloud Console setup to errors."
---

# Google Drive (uploads)

Your service receives files — contracts, receipts, photos — and the company
wants to see them in a Google Drive folder, not in an S3 bucket. This recipe
wires `UploadUtils` to a company folder (or Shared Drive) through a
**service account**: no user login, no consent screen.

The backend is `GoogleDriveUploadStorage`. It implements the same
`UploadStorage` protocol as the local disk and MinIO, so your upload route
does not change at all: only the object passed as `backend=`.

```bash
uv add "tempest-fastapi-sdk[upload,google-drive]>=0.373.0"
```

The `[google-drive]` extra brings `pyjwt` and `cryptography` (`httpx` is
already in the base package). There is no `google-api-python-client` and no
`google-auth`: a service account's token exchange is one signed JWT and one
POST, and the SDK does it itself.

## Step 1 — Get the service account and its JSON key

You leave this step with a JSON file — the service account's credential. You
can do it in the console (clicking) or with `gcloud` (copying commands); pick
one.

!!! tip "Menu names change"
    The Google Cloud console renames and reorganizes menus from time to
    time. If a name below does not match the screen, follow the official
    guides: [Create service accounts](https://cloud.google.com/iam/docs/service-accounts-create)
    and [Create and delete service account keys](https://cloud.google.com/iam/docs/keys-create-delete).

### 1.1 — A Google Cloud project

The service account lives inside a Google Cloud project. Sign in to the
[console](https://console.cloud.google.com/) with the company's Google account
and, in the project picker at the top of the page, choose an existing project
or click **New project**. Write down the **project ID** (e.g.
`my-company-uploads`).

### 1.2 — Enable the Google Drive API

Without it, every call to the Drive API is refused. Under **APIs & Services →
Library**, search for **Google Drive API** and click **Enable**.

### 1.3 — Create the service account

Under **IAM & Admin → Service accounts**, click **Create service account**:

1. Give it a name (e.g. `uploader`). The console suggests the **ID** and
   shows the e-mail the account will have:
   `uploader@my-company-uploads.iam.gserviceaccount.com`.
2. At the roles step, **grant none** and finish. The account needs no IAM
   role on the project to use Drive: access to files comes from **sharing**
   the folder (Step 2), not from IAM.

### 1.4 — Generate the JSON key

Open the account, go to the **Keys → Add key → Create new key** tab, choose
**JSON** and confirm. The browser downloads a file like this (values
shortened):

```json
{
  "type": "service_account",
  "project_id": "my-company-uploads",
  "private_key_id": "3f2a…",
  "private_key": "-----BEGIN PRIVATE KEY-----\n…\n-----END PRIVATE KEY-----\n",
  "client_email": "uploader@my-company-uploads.iam.gserviceaccount.com",
  "token_uri": "https://oauth2.googleapis.com/token"
}
```

The two things the recipe uses are the `client_email` (you need it in Step 2)
and the whole file (it goes into the settings in Step 3).

!!! warning "Key creation blocked by the organization"
    If creating the key fails with an organization-policy message, your
    Google Cloud organization blocks service-account keys (the
    `iam.disableServiceAccountKeyCreation` constraint). Whoever administers
    the organization can grant an exception for this project; there is no
    way around it on the SDK side, which authenticates precisely with the
    key.

=== "With gcloud"

    The whole step, for those with the [gcloud CLI](https://cloud.google.com/sdk/docs/install)
    installed and signed in (`gcloud auth login`):

    ```bash
    gcloud projects create my-company-uploads
    gcloud config set project my-company-uploads
    gcloud services enable drive.googleapis.com
    gcloud iam service-accounts create uploader --display-name="Service uploads"
    gcloud iam service-accounts keys create drive-sa.json \
        --iam-account=uploader@my-company-uploads.iam.gserviceaccount.com
    ```

    The last command writes the key to `drive-sa.json` in the current
    directory.

!!! warning "That file is a password"
    Whoever holds the JSON acts as the service account. Keep it in your
    deployment's secret manager, never in the repository — if it sits in the
    project folder during development, add its name to `.gitignore`. If it
    leaks, delete the key in the account's **Keys** tab and generate a new
    one. The SDK never writes the key, the signed JWT or the access token to
    a log or an error message, and the credentials' `repr` hides the private
    key.

## Step 2 — Share the folder with the `client_email`

The service account is an account like any other in Drive: it only sees what
is shared with it. Take the `client_email` from the JSON and choose where the
files will live.

### Shared Drive (recommended)

Shared Drives exist on **Google Workspace** accounts; a personal Gmail account
has none. In [Google Drive](https://drive.google.com/):

1. Under **Shared drives**, create one (or open an existing one).
2. Click the drive name → **Manage members**, paste the `client_email` and
   give it the **Content manager** role (or higher — the backend's `delete`
   removes files, and roles below that cannot).
3. Optional: create a folder inside the drive for the uploads.

### Regular folder (My Drive)

Right-click the folder → **Share**, paste the `client_email` and give it the
**Editor** role. Read the "Why a Shared Drive" note below before choosing
this path.

### The IDs that go into the settings

Open the folder in the browser. The **folder id** is the last segment of the
URL: `https://drive.google.com/drive/folders/`**`1AbCdEfGhIjKlMnOp`** — that is
`GOOGLE_DRIVE_FOLDER_ID`.

If the folder is in a Shared Drive, open the drive's **root**: the last
segment of its URL is the drive id (`GOOGLE_DRIVE_DRIVE_ID`, optional — it
restricts searches to that drive). If you write straight into the Shared
Drive's root, the same value is also the folder id.

!!! info "Why a Shared Drive"
    A file the service account uploads into a person's **My Drive** folder
    is owned by the service account, and Google's documentation and forums
    describe service accounts as having no storage quota of their own — the
    upload is refused with `storageQuotaExceeded`. In a Shared Drive the
    file belongs to the company drive. This was **not measured** in this
    repository (there is no real credential here); read
    [Shared drives overview](https://developers.google.com/workspace/drive/api/guides/about-shareddrives)
    before deciding. Every SDK call to the Drive API sends
    `supportsAllDrives=true`, so both shapes work on the client side.

### Check before wiring the service

A one-minute script proves the key and the sharing are right: it writes a
test file into the folder and deletes it.

```python title="check_drive.py"
import asyncio
from collections.abc import AsyncIterator

from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveUploadStorage,
)
from tempest_fastapi_sdk.utils import UploadResult

PROBE_KEY: str = "tempest-credentials-check.txt"


async def probe_bytes() -> AsyncIterator[bytes]:
    """Yield the tiny payload of the check file."""
    yield b"tempest credentials check"


async def main() -> None:
    """Upload and delete a probe file to prove the key and the sharing."""
    storage: GoogleDriveUploadStorage = GoogleDriveUploadStorage.from_service_account(
        folder_id="1AbCdEfGhIjKlMnOp",
        credentials_path="drive-sa.json",
    )
    try:
        result: UploadResult = await storage.write_stream(
            PROBE_KEY, probe_bytes(), content_type="text/plain"
        )
        print("upload ok:", result.url)
        print("deleted:", await storage.delete(PROBE_KEY))
    finally:
        await storage.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

On success it prints the file link and `deleted: True`. On failure the
exception says what is missing:

| Exception | What it means | What to do |
| --- | --- | --- |
| `GoogleDriveException` with `reason` `invalid_private_key` | the JSON's `private_key` is not a valid PEM RSA key (file edited, truncated or pasted with the wrong line breaks) | download the key again (Step 1.4) |
| `GoogleDriveFolderNotFoundException` | Drive answered `404` for the folder: wrong id, or the folder is not shared with the `client_email` (the message names the e-mail) | check the id in the URL and the sharing (Step 2) |
| `GoogleDriveException` with another `reason` | Drive refused the call for another reason (API not enabled, role without permission, quota) — `details` carries the `status` and `reason` Google sent | read the `reason` and go back to the matching step |

The exceptions and the mapping are the SDK's and are covered by the test
suite; **which** status Google returns in each case (for instance `404` for
an unshared folder) is what the [error-handling guide](https://developers.google.com/workspace/drive/api/guides/handle-errors)
describes, and was not measured against Google in this repository.

## Step 3 — Configure the settings

Compose the `GoogleDriveSettings` mixin:

| Variable | What it is |
| --- | --- |
| `GOOGLE_DRIVE_CREDENTIALS_PATH` | path to the service-account JSON |
| `GOOGLE_DRIVE_CREDENTIALS_JSON` | the whole JSON, inline (wins over the path when both are set) |
| `GOOGLE_DRIVE_FOLDER_ID` | the folder files are written into |
| `GOOGLE_DRIVE_DRIVE_ID` | optional: the Shared Drive holding the folder — searches are restricted to it |

```bash
GOOGLE_DRIVE_CREDENTIALS_PATH=/run/secrets/drive-sa.json
GOOGLE_DRIVE_FOLDER_ID=1AbCdEfGhIjKlMnOp
```

`settings.google_drive_kwargs()` returns a `GoogleDriveStorageKwargs`
(`TypedDict`) ready to splat into
`GoogleDriveUploadStorage.from_service_account(...)` — the type-checker
checks the splat against the signature.

## Step 4 — Plug it into `UploadUtils`

A complete service:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile

from tempest_fastapi_sdk import UploadUtils
from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveUploadStorage,
)
from tempest_fastapi_sdk.settings import GoogleDriveSettings


class Settings(GoogleDriveSettings):
    """Service settings: only Google Drive, in this example."""


settings: Settings = Settings()
storage: GoogleDriveUploadStorage = GoogleDriveUploadStorage.from_service_account(
    **settings.google_drive_kwargs()
)
uploads: UploadUtils = UploadUtils(
    backend=storage,
    max_size_bytes=20 * 1024 * 1024,
    allowed_extensions={"pdf", "png", "jpg"},
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Close the HTTP client the backend created."""
    yield
    await storage.aclose()


app: FastAPI = FastAPI(lifespan=lifespan)


@app.post("/api/contracts")
async def upload_contract(file: UploadFile) -> dict[str, str]:
    """Validate the contract and send it to the Drive folder."""
    key = await uploads.save(file, subdir="contracts")
    return {"key": str(key)}


@app.delete("/api/contracts/{name}")
async def delete_contract(name: str) -> dict[str, bool]:
    """Delete the contract from Drive (it skips the trash)."""
    return {"deleted": await uploads.delete(f"contracts/{name}")}
```

Piece by piece:

- **`from_service_account(...)`** reads the JSON, creates its own
  `httpx.AsyncClient` and builds the `GoogleDriveClient` with the
  `ServiceAccountTokenProvider`. Since it created the client, the backend
  closes it in `aclose()` — hence the `lifespan`. Pass `http_client=` to
  reuse a client of yours (then you close it).
- **`UploadUtils(backend=storage, ...)`** still validates extension, MIME,
  size and magic bytes before any byte leaves the service.
- **`uploads.save(...)`** returns the *key* (`contracts/<uuid>.pdf`). That is
  what you store in the database, as with the local disk and MinIO.

## How a key becomes a file

Drive identifies files by **id**, not by path, and accepts two files with the
same name in one folder. So the backend:

- writes the key into `appProperties` (properties private to the app), under
  `tempest_key`;
- uses the key's last segment as `name`, only for people browsing the
  folder;
- finds the file with the search
  `'<folder>' in parents and appProperties has { key='tempest_key' and value='<key>' } and trashed = false`,
  with quotes and backslashes in the key **escaped** for Drive's search
  syntax.

`exists(key)` answers whether the search finds anything; `delete(key)`
permanently deletes (`files.delete`, no trash) every match and returns
`False` when nothing matched.

!!! note "Key length"
    The [properties documentation](https://developers.google.com/workspace/drive/api/guides/properties)
    caps each property at 124 bytes (name + value, UTF-8). With
    `tempest_key` that leaves 113 bytes for the key; more is refused with
    `ValidationException` (`422`, `code="VALIDATION_ERROR"`) **before** any
    request — the message says how many bytes the property takes and names
    the 124 limit, and `details` carries `property`, `bytes` and
    `limit_bytes`. The same applies to each `metadata=` pair. The key `UploadUtils`
    generates (uuid + extension) fits easily; mind
    `keep_original_name=True` with long names.

### Same key twice: the content is replaced

Writing a key that already exists **replaces the content of that same
file**: same id, same link, new media (resumable `files.update`). No new file
is created, and `exists` keeps finding exactly one — the same behaviour as
overwriting on the local disk or MinIO.

!!! warning "Two simultaneous writes of a new key"
    As far as the Drive documentation shows, there is no conditional create
    (not measured). If two requests write the **same new
    key** at the same time, both may create a file. Then `exists` answers
    `True`, the next write replaces the first one, and `delete` removes them
    all. The default `UploadUtils` key (a uuid) does not collide.

## `presigned_url` raises

Drive **has no presigned URLs**: it serves a file to whoever holds a Drive
permission, not through a temporary signed link. So
`storage.presigned_url(key)` raises `PresignedUrlUnsupportedException`
(`501`, `code="PRESIGNED_URL_UNSUPPORTED"`) instead of returning a permanent
link someone would treat as temporary.

To serve the file to your users, write an authenticated route of your own.
When the Drive link is what you want (in an internal panel, say), use
`await storage.web_view_link(key)`; every write's `UploadResult.url` carries
that `webViewLink` too. It is **permanent** and only opens for someone with
access to the file in Drive.

### Download in the admin panel

The admin panel (`make_admin_router`) with `upload_fields` uses the same
backend. The download route of an upload field asks for `presigned_url` and,
when the backend has no presigned URL — it returned `None` **or** raised
`PresignedUrlUnsupportedException` —, looks for a permanent link: if the
backend implements the `SupportsWebViewLink` protocol
(`async def web_view_link(self, key: str, /) -> str | None`), the route
answers `303` to that link. `GoogleDriveUploadStorage` implements it, so the
admin download **redirects to the Drive link**, which only opens for someone
with access to the file there. With no link (file deleted in Drive, or a
backend of yours without `web_view_link`) the route falls back to the usual
path: a local file, or `404` "file not available".

A backend of yours that wants the same only needs the method:

```python
from tempest_fastapi_sdk.utils import SupportsWebViewLink


class MyStorage:
    """Example: a backend with a permanent link per key."""

    async def web_view_link(self, key: str) -> str | None:
        """Return the file's permanent link, or None."""
        return f"https://files.example.com/{key}"


assert isinstance(MyStorage(), SupportsWebViewLink)
```

## Resumable upload, in pieces

The backend uses Drive v3's
[resumable upload](https://developers.google.com/workspace/drive/api/guides/manage-uploads#resumable):

- The stream goes up in pieces that are multiples of 256 KiB (`chunk_size=`,
  8 MiB by default) — memory holds a little over one piece (a piece plus the
  chunk the backend reads ahead), not the file.
- Every piece but the last carries `Content-Range: bytes a-b/*`; Drive
  answers `308` with a `Range` header saying how much it kept, and the next
  piece starts right after. Bytes Drive did not keep are re-sent; nothing
  else is.
- The backend reads **one piece ahead**: a piece only goes out with `/*` once
  more bytes are known to follow it. So the last `PUT` always carries bytes
  **and** the total (`bytes a-b/<total>`), including when the size is an
  exact multiple of the piece (256 KiB, 512 KiB, 8 MiB…). An empty
  `bytes */<total>` `PUT` is also how a session is **queried**; ending the
  upload with one left a fake HTTP server that treats it as a query stuck in
  `upload_stalled`. The exception is the **0-byte** file, which has no byte to
  carry: it closes with `bytes */0`.
- Retried with exponential backoff (0.5 s, 1 s, 2 s…, up to 16 s), up to
  `max_retries` times (5 by default): `429`, `5xx`, transport errors and
  **rate-limit `403`** — Drive reports an exhausted quota as `403` with
  `reason` `rateLimitExceeded` or `userRateLimitExceeded`
  ([handling errors](https://developers.google.com/workspace/drive/api/guides/handle-errors)).
  Any other `403` (`insufficientFilePermissions`, `storageQuotaExceeded`…)
  is raised at once. This holds for metadata calls and for pieces; on a
  piece, the backend asks the session how much it holds before retrying.
  The whole file is never re-sent.
- **`Retry-After`:** on a `429` or `503` carrying that header in seconds, the
  wait is what Drive asked for, capped at `backoff_max_seconds` (16 s by
  default); a missing header or the HTTP-date form falls back to the
  exponential backoff.
- A `308` with no progress (Drive kept none of the piece) waits the backoff
  before re-sending; after `max_retries` such answers in a row it becomes
  `GoogleDriveException` with `reason="upload_stalled"`. A `308` saying Drive
  holds **less** than it had already acknowledged — bytes the backend
  already dropped from memory — cannot be repaired and is raised at once
  with `reason="upload_range_regressed"`.
- A `404` on a piece or on the status query means the **resumable session**
  expired or was lost: it becomes `GoogleDriveException` with
  `reason="upload_session_not_found"` and a message saying to upload again —
  not `GoogleDriveFolderNotFoundException`, which is kept for the `404` of
  the search and of opening the session.
- The first read of the stream happens **before** the session is opened.
  So the `content_validator` of `UploadUtils.save(...)` (and the `validator`
  of a caller using `storage.write_stream(...)` directly), which runs on the
  first chunk, refuses without opening any session. `max_size_bytes` stops
  the stream midway, and the session is abandoned before its last piece.
  Per the
  [resumable upload guide](https://developers.google.com/workspace/drive/api/guides/manage-uploads#resumable),
  the file is created when the session completes, so an interrupted write
  **should** leave no file in the folder — this was not measured against the
  real Google.
- When the write fails, for any reason, the backend closes (`aclose`) the
  generator you passed, so its `finally` runs right away.

## Errors

| Situation | Exception | Status |
| --- | --- | --- |
| Folder missing or not shared (`404`) | `GoogleDriveFolderNotFoundException` — the message names the folder and the `client_email` to share it with | `502` |
| Resumable session expired or lost (`404` on a piece) | `GoogleDriveException`, `reason="upload_session_not_found"` | `502` |
| Retries exhausted (`429`, `5xx`, rate-limit `403`) | `GoogleDriveException` with the last answer's `status` and `reason` | `502` |
| Session stopped taking bytes / lost acknowledged bytes | `GoogleDriveException`, `reason="upload_stalled"` / `"upload_range_regressed"` | `502` |
| The JSON's `private_key` is not a PEM RSA key | `GoogleDriveException`, `reason="invalid_private_key"`, before any request; the message names the `client_email`, never the key | `502` |
| Any other refusal from Drive or the token endpoint | `GoogleDriveException`, with `details={"status": <http>, "reason": <error.errors[0].reason>}` (or the OAuth `error`, such as `invalid_grant`) | `502` |
| `presigned_url` | `PresignedUrlUnsupportedException` | `501` |
| Validator rejected | `InvalidFileTypeException` | `415` |
| Key (or a `metadata` property) over 124 bytes | `ValidationException` | `422` |
| Stream exceeded `max_size_bytes` | `FileTooLargeException` | `413` |

Per the [error-handling guide](https://developers.google.com/workspace/drive/api/guides/handle-errors), Drive should answer `404` both for a
folder that does not exist and for one the account cannot see (not measured
here), so the message talks about the common case: sharing.

## Swapping the service account for another token

The token exchange sits behind the `AccessTokenProvider` protocol, a single
method (`async def access_token(self) -> str`). A user OAuth token provider
replaces `ServiceAccountTokenProvider` without touching the client:

```python
import httpx

from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveClient,
    GoogleDriveUploadStorage,
)


class StaticTokenProvider:
    """Example: a token obtained outside the SDK."""

    def __init__(self, token: str) -> None:
        """Store the token."""
        self.token: str = token

    async def access_token(self) -> str:
        """Return the stored token."""
        return self.token


http: httpx.AsyncClient = httpx.AsyncClient()
client: GoogleDriveClient = GoogleDriveClient(
    token_provider=StaticTokenProvider("ya29.…"),
    http_client=http,
)
storage: GoogleDriveUploadStorage = GoogleDriveUploadStorage(
    client, folder_id="1AbCdEfGhIjKlMnOp"
)
```

## What was measured and what was not

The suite checks **the wire**, with `httpx.MockTransport`: the JWT (decoded
with the public half of an RSA pair generated in the test — `alg`, `kid`,
`iss`, `scope`, `aud`, `iat`, `exp`), the token POST body, the cache and the
single exchange under concurrency, the headers and `Content-Range` of each
piece, resuming after a short `308`, closing with bytes and the total on a
size that is an exact multiple of the piece (against a fake that treats an
empty `bytes */<total>` as a query), the backoff of a no-progress `308` and
the immediate error of a `308` that goes backwards, the `503`/`429` and
rate-limit `403` retry, `Retry-After` and its cap, the session `404`, the
escaped search, `supportsAllDrives` on every call, the error mapping and the
admin download redirect to `web_view_link`.

**Not measured against the real Google** (there is no credential in this
repository): service-account quota on My Drive, behaviour inside a Shared
Drive, the 124-byte property limit, the one-week lifetime of a resumable
session, the moment the file starts to exist, whether Drive closes a 0-byte
upload on the empty `bytes */0` `PUT`, the real shape of the rate-limit
`403` and of the expired-session `404`, and whether Drive sends
`Retry-After` — all of that comes from the official documentation linked
above. The test
`tests/integrations/storage/google_drive/test_live.py` does the full round
trip (upload, overwrite, check `exists`, delete) when
`TEST_GOOGLE_DRIVE_CREDENTIALS` and `TEST_GOOGLE_DRIVE_FOLDER_ID` are set:

```bash
TEST_GOOGLE_DRIVE_CREDENTIALS=/path/sa.json \
TEST_GOOGLE_DRIVE_FOLDER_ID=1AbCdEfGhIjKlMnOp \
uv run pytest -m network tests/integrations/storage/google_drive/test_live.py
```

## Recap

- `uv add "tempest-fastapi-sdk[upload,google-drive]>=0.373.0"`, a service account
  with a JSON key, the Drive API enabled, a folder (ideally in a Shared
  Drive) shared with the `client_email`.
- `GoogleDriveSettings` + `GoogleDriveUploadStorage.from_service_account(**settings.google_drive_kwargs())`.
- `UploadUtils(backend=storage)` — the upload route does not change.
- The key lives in `appProperties`; a repeated key replaces the content of
  the same file; `delete` skips the trash.
- `presigned_url` raises `PresignedUrlUnsupportedException`; the Drive link
  is in `web_view_link` and `UploadResult.url`, and the admin download
  redirects to it.
- The last piece always carries bytes and the total; `429`, `5xx` and
  rate-limit `403` are retried, honouring `Retry-After`; a `404` mid-upload
  is a lost session, not a missing folder.
- A Drive refusal becomes `GoogleDriveException` with status and `reason`; a
  folder not shared becomes `GoogleDriveFolderNotFoundException` naming who
  to share it with.

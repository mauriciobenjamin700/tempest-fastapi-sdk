# Google Drive (uploads)

Your service receives files — contracts, receipts, photos — and the company
wants to see them in a Google Drive folder, not in an S3 bucket. This recipe
wires `UploadUtils` to a company folder (or Shared Drive) through a
**service account**: no user login, no consent screen.

The backend is `GoogleDriveUploadStorage`. It implements the same
`UploadStorage` protocol as the local disk and MinIO, so your upload route
does not change at all: only the object passed as `backend=`.

```bash
uv add "tempest-fastapi-sdk[upload,google-drive]"
```

The `[google-drive]` extra brings `pyjwt` and `cryptography` (`httpx` is
already in the base package). There is no `google-api-python-client` and no
`google-auth`: a service account's token exchange is one signed JWT and one
POST, and the SDK does it itself.

## Step 1 — Create the service account and enable the Drive API

In the [Google Cloud console](https://console.cloud.google.com/):

1. Pick (or create) a project.
2. Under **APIs & Services → Library**, enable the **Google Drive API**.
3. Under **IAM & Admin → Service accounts**, create a service account.
4. On that account, **Keys → Add key → JSON**. The browser downloads a file
   like this (values shortened):

```json
{
  "type": "service_account",
  "project_id": "my-company",
  "private_key_id": "3f2a…",
  "private_key": "-----BEGIN PRIVATE KEY-----\n…\n-----END PRIVATE KEY-----\n",
  "client_email": "uploader@my-company.iam.gserviceaccount.com",
  "token_uri": "https://oauth2.googleapis.com/token"
}
```

!!! warning "That file is a password"
    Whoever holds the JSON acts as the service account. Keep it in your
    deployment's secret manager, never in the repository. The SDK never
    writes the key, the signed JWT or the access token to a log or an error
    message, and the credentials' `repr` hides the private key.

## Step 2 — Share the folder with the `client_email`

The service account only sees what is shared with it. Take the
`client_email` from the JSON and:

- **Shared Drive (recommended):** add the account as a member of the Shared
  Drive, with the **Content manager** role (or higher — `delete` removes
  files).
- **Regular folder:** share the folder with the account as **Editor**.

The **folder id** is the last segment of its URL:
`https://drive.google.com/drive/folders/`**`1AbCdEfGhIjKlMnOp`**.

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
    `InvalidFileTypeException` **before** any request. The key `UploadUtils`
    generates (uuid + extension) fits easily; mind
    `keep_original_name=True` with long names.

### Same key twice: the content is replaced

Writing a key that already exists **replaces the content of that same
file**: same id, same link, new media (resumable `files.update`). No new file
is created, and `exists` keeps finding exactly one — the same behaviour as
overwriting on the local disk or MinIO.

!!! warning "Two simultaneous writes of a new key"
    Drive has no conditional create. If two requests write the **same new
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

## Resumable upload, in pieces

The backend uses Drive v3's
[resumable upload](https://developers.google.com/workspace/drive/api/guides/manage-uploads#resumable):

- The stream goes up in pieces that are multiples of 256 KiB (`chunk_size=`,
  8 MiB by default) — memory holds one piece, not the file.
- Every piece but the last carries `Content-Range: bytes a-b/*`; Drive
  answers `308` with a `Range` header saying how much it kept, and the next
  piece starts right after. Bytes Drive did not keep are re-sent; nothing
  else is.
- `429` and `5xx` on a piece are retried with exponential backoff (0.5 s,
  1 s, 2 s…, up to 16 s) after asking the session how much it holds — up to
  `max_retries` times (5 by default). The whole file is never re-sent.
- `content_validator` runs on the first piece **before** the session is
  opened, and `max_size_bytes` stops the stream midway. Per Google's guide,
  the file only exists once the last piece lands, so an interrupted write
  leaves no file in the folder.

## Errors

| Situation | Exception | Status |
| --- | --- | --- |
| Folder missing or not shared (`404`) | `GoogleDriveFolderNotFoundException` — the message names the folder and the `client_email` to share it with | `502` |
| Any other refusal from Drive or the token endpoint | `GoogleDriveException`, with `details={"status": <http>, "reason": <error.errors[0].reason>}` (or the OAuth `error`, such as `invalid_grant`) | `502` |
| `presigned_url` | `PresignedUrlUnsupportedException` | `501` |
| Validator rejected / key too long | `InvalidFileTypeException` | `415` |
| Stream exceeded `max_size_bytes` | `FileTooLargeException` | `413` |

Drive answers `404` both for a folder that does not exist and for one the
account cannot see, so the message talks about the common case: sharing.

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
piece, resuming after a short `308`, the `503`/`429` retry, the escaped
search, `supportsAllDrives` on every call and the error mapping.

**Not measured against the real Google** (there is no credential in this
repository): service-account quota on My Drive, behaviour inside a Shared
Drive, the 124-byte property limit, the one-week lifetime of a resumable
session and the moment the file starts to exist — all of that comes from the
official documentation linked above. The test
`tests/integrations/storage/google_drive/test_live.py` does the full round
trip (upload, overwrite, check `exists`, delete) when
`TEST_GOOGLE_DRIVE_CREDENTIALS` and `TEST_GOOGLE_DRIVE_FOLDER_ID` are set:

```bash
TEST_GOOGLE_DRIVE_CREDENTIALS=/path/sa.json \
TEST_GOOGLE_DRIVE_FOLDER_ID=1AbCdEfGhIjKlMnOp \
uv run pytest -m network tests/integrations/storage/google_drive/test_live.py
```

## Recap

- `uv add "tempest-fastapi-sdk[upload,google-drive]"`, a service account
  with a JSON key, the Drive API enabled, a folder (ideally in a Shared
  Drive) shared with the `client_email`.
- `GoogleDriveSettings` + `GoogleDriveUploadStorage.from_service_account(**settings.google_drive_kwargs())`.
- `UploadUtils(backend=storage)` — the upload route does not change.
- The key lives in `appProperties`; a repeated key replaces the content of
  the same file; `delete` skips the trash.
- `presigned_url` raises `PresignedUrlUnsupportedException`; the Drive link
  is in `web_view_link` and `UploadResult.url`.
- A Drive refusal becomes `GoogleDriveException` with status and `reason`; a
  folder not shared becomes `GoogleDriveFolderNotFoundException` naming who
  to share it with.

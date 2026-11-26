"""An in-memory Google Drive behind ``httpx.MockTransport``.

:class:`FakeDrive` answers the five Drive v3 calls and the token endpoint
the backend uses, the way Google's reference documents them, and records
every request so a test can assert on the wire. It is a model of the
documentation, not of the live service: what it cannot prove is listed in
the recipe and exercised by ``test_live.py`` when credentials exist.

Where the documentation leaves room, the fake takes the **pessimistic**
reading: an empty ``PUT`` with ``Content-Range: bytes */<total>`` is a
status query and never completes the upload — it only answers ``308`` (or
the finished resource when the session already completed). The one
exception is ``bytes */0`` on a session that holds nothing, the only way
to close a zero-byte upload. A fake HTTP Google that treated the query
this way turned a payload of an exact multiple of the piece size into
``upload_stalled``, which is what these tests pin.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveClient,
    GoogleDriveUploadStorage,
    ServiceAccountCredentials,
    ServiceAccountTokenProvider,
)

CLIENT_EMAIL: str = "uploader@demo-project.iam.gserviceaccount.com"
FOLDER_ID: str = "folder-123"
UPLOAD_PREFIX: str = "https://www.googleapis.com/upload/drive/v3/files"
API_PREFIX: str = "https://www.googleapis.com/drive/v3/files"
TOKEN_URL: str = "https://oauth2.googleapis.com/token"

_QUERY = re.compile(
    r"^'((?:[^'\\]|\\.)*)' in parents and appProperties has "
    r"\{ key='tempest_key' and value='((?:[^'\\]|\\.)*)' \} and trashed = false$"
)


def unescape(value: str) -> str:
    """Undo the Drive query escaping (``\\'`` and ``\\\\``)."""
    return re.sub(r"\\(.)", r"\1", value)


@dataclass
class Session:
    """One resumable upload session held by the fake."""

    metadata: dict[str, Any]
    file_id: str | None
    received: bytearray = field(default_factory=bytearray)
    done: dict[str, Any] | None = None


@dataclass(frozen=True)
class Failure:
    """One scripted error answer: status, Drive reason, optional ``Retry-After``."""

    status: int
    reason: str = "backendError"
    retry_after: str | None = None


@dataclass
class FakeDrive:
    """A Drive that keeps files in memory and records every request."""

    folders: set[str] = field(default_factory=lambda: {FOLDER_ID})
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    contents: dict[str, bytes] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    token_calls: int = 0
    piece_failures: list[int | Failure] = field(default_factory=list)
    api_failures: list[int | Failure] = field(default_factory=list)
    keep_only: list[int] = field(default_factory=list)
    rewind: list[int | None] = field(default_factory=list)
    expire_sessions: bool = False
    expire_after_piece_failure: bool = False
    omit_link_on_upload: bool = False
    token_status: int = 200
    list_unknown_folder_as_empty: bool = False

    def _error(
        self,
        status: int,
        reason: str,
        message: str,
        *,
        retry_after: str | None = None,
    ) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"Retry-After": retry_after} if retry_after is not None else {},
            json={
                "error": {
                    "code": status,
                    "message": message,
                    "errors": [
                        {"domain": "global", "reason": reason, "message": message}
                    ],
                }
            },
        )

    def _scripted(self, failure: int | Failure) -> httpx.Response:
        scripted = failure if isinstance(failure, Failure) else Failure(failure)
        return self._error(
            scripted.status,
            scripted.reason,
            "Scripted failure",
            retry_after=scripted.retry_after,
        )

    def _resource(self, file_id: str, *, link: bool = True) -> dict[str, Any]:
        resource = dict(self.files[file_id])
        if not link:
            resource.pop("webViewLink", None)
        return resource

    def _finish(self, session: Session) -> dict[str, Any]:
        content = bytes(session.received)
        if session.file_id is None:
            file_id = f"file-{len(self.files) + 1}"
            self.files[file_id] = {
                "id": file_id,
                "parents": session.metadata.get("parents", []),
                "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
            }
        else:
            file_id = session.file_id
        record = self.files[file_id]
        record["name"] = session.metadata.get("name", record.get("name"))
        record["mimeType"] = session.metadata.get("mimeType")
        record["appProperties"] = session.metadata.get("appProperties", {})
        record["size"] = str(len(content))
        self.contents[file_id] = content
        session.done = self._resource(file_id, link=not self.omit_link_on_upload)
        return session.done

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Route one request, the way Google's reference documents it."""
        self.requests.append(request)
        url = str(request.url)
        params = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        if url == TOKEN_URL:
            self.token_calls += 1
            if self.token_status != 200:
                return httpx.Response(
                    self.token_status,
                    json={
                        "error": "invalid_grant",
                        "error_description": "Invalid JWT Signature.",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "access_token": f"ya29.token-{self.token_calls}",
                    "expires_in": 3599,
                    "token_type": "Bearer",
                },
            )
        path = request.url.path
        if request.method in {"POST", "PATCH"} and path.startswith(
            "/upload/drive/v3/files"
        ):
            metadata: dict[str, Any] = json.loads(request.content)
            file_id = path.rsplit("/", 1)[-1] if request.method == "PATCH" else None
            if file_id is None:
                parent = metadata["parents"][0]
                if parent not in self.folders:
                    return self._error(404, "notFound", f"File not found: {parent}.")
            elif file_id not in self.files:
                return self._error(404, "notFound", f"File not found: {file_id}.")
            upload_id = f"S{len(self.sessions) + 1}"
            self.sessions[upload_id] = Session(metadata=metadata, file_id=file_id)
            return httpx.Response(
                200,
                headers={
                    "Location": (
                        f"{UPLOAD_PREFIX}?uploadType=resumable&upload_id={upload_id}"
                    )
                },
            )
        if request.method == "PUT" and "upload_id" in params:
            session = self.sessions[params["upload_id"]]
            if self.expire_sessions:
                return self._error(404, "notFound", "Upload session not found.")
            content_range = request.headers["Content-Range"]
            match = re.fullmatch(r"bytes (\*|(\d+)-(\d+))/(\*|\d+)", content_range)
            assert match, content_range
            if match[1] == "*":
                assert request.content == b"", "a status query carries no body"
                if session.done is not None:
                    return httpx.Response(200, json=session.done)
                if match[4] == "0" and not session.received:
                    return httpx.Response(200, json=self._finish(session))
                headers = (
                    {"Range": f"bytes=0-{len(session.received) - 1}"}
                    if session.received
                    else {}
                )
                return httpx.Response(308, headers=headers)
            if self.piece_failures:
                self.expire_sessions = self.expire_after_piece_failure
                return self._scripted(self.piece_failures.pop(0))
            start, end = int(match[2]), int(match[3])
            assert start == len(session.received), (start, len(session.received))
            body = request.content
            assert len(body) == end - start + 1
            if self.keep_only:
                body = body[: self.keep_only.pop(0)]
            session.received.extend(body)
            if self.rewind:
                cut = self.rewind.pop(0)
                if cut is not None:
                    del session.received[cut:]
            total = match[4]
            if total != "*" and int(total) == len(session.received):
                return httpx.Response(200, json=self._finish(session))
            headers = (
                {"Range": f"bytes=0-{len(session.received) - 1}"}
                if session.received
                else {}
            )
            return httpx.Response(308, headers=headers)
        if request.method == "GET" and path == "/drive/v3/files":
            if self.api_failures:
                return self._scripted(self.api_failures.pop(0))
            found = _QUERY.match(params["q"])
            assert found, params["q"]
            folder, key = unescape(found[1]), unescape(found[2])
            if folder not in self.folders and not self.list_unknown_folder_as_empty:
                return self._error(404, "notFound", f"File not found: {folder}.")
            matches = [
                self._resource(file_id)
                for file_id, record in self.files.items()
                if folder in record.get("parents", [])
                and record.get("appProperties", {}).get("tempest_key") == key
            ]
            return httpx.Response(200, json={"files": matches})
        if path.startswith("/drive/v3/files/"):
            file_id = path.rsplit("/", 1)[-1]
            if file_id not in self.files:
                return self._error(404, "notFound", f"File not found: {file_id}.")
            if request.method == "DELETE":
                del self.files[file_id]
                self.contents.pop(file_id, None)
                return httpx.Response(204)
            if request.method == "GET":
                return httpx.Response(200, json=self._resource(file_id))
        raise AssertionError(f"unexpected request {request.method} {url}")

    def api_requests(self) -> list[httpx.Request]:
        """Requests to Drive itself (no token exchange, no session PUTs)."""
        return [
            r
            for r in self.requests
            if str(r.url) != TOKEN_URL and "upload_id" not in str(r.url)
        ]

    def piece_requests(self) -> list[httpx.Request]:
        """PUTs to a resumable session."""
        return [r for r in self.requests if "upload_id" in str(r.url)]


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    """One RSA key for the whole session (generation is the slow part)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def private_pem(rsa_key: rsa.RSAPrivateKey) -> str:
    """The key as the PKCS#8 PEM a service-account file carries."""
    return rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="session")
def public_pem(rsa_key: rsa.RSAPrivateKey) -> bytes:
    """The public half, to verify assertions taken off the wire."""
    return rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.fixture
def credentials_json(private_pem: str) -> str:
    """A service-account key file shaped like the one the console downloads."""
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "demo-project",
            "private_key_id": "kid-0001",
            "private_key": private_pem,
            "client_email": CLIENT_EMAIL,
            "client_id": "1234567890",
            "token_uri": TOKEN_URL,
        }
    )


@pytest.fixture
def fake() -> FakeDrive:
    """A fresh in-memory Drive."""
    return FakeDrive()


@pytest.fixture
async def http(fake: FakeDrive) -> AsyncIterator[httpx.AsyncClient]:
    """An ``httpx.AsyncClient`` wired to the fake."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as client:
        yield client


@dataclass
class Sleeps:
    """Records backoff delays instead of sleeping."""

    delays: list[float] = field(default_factory=list)

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


@pytest.fixture
def sleeps() -> Sleeps:
    """A no-op sleep that remembers what it was asked."""
    return Sleeps()


@pytest.fixture
def make_storage(
    http: httpx.AsyncClient, credentials_json: str, sleeps: Sleeps
) -> Iterator[Any]:
    """Build a backend over the fake with a small piece size."""

    def build(
        *, chunk_size: int = 256 * 1024, max_retries: int = 3
    ) -> GoogleDriveUploadStorage:
        credentials = ServiceAccountCredentials.from_json(credentials_json)
        client = GoogleDriveClient(
            token_provider=ServiceAccountTokenProvider(credentials, http_client=http),
            http_client=http,
            principal_email=credentials.client_email,
            chunk_size=chunk_size,
            max_retries=max_retries,
            sleep=sleeps,
        )
        return GoogleDriveUploadStorage(client, folder_id=FOLDER_ID)

    yield build


async def stream(*parts: bytes) -> AsyncIterator[bytes]:
    """Yield ``parts`` as an async byte stream."""
    for part in parts:
        yield part

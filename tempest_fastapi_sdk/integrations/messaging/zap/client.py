"""Typed HTTP client generated from the zap-api OpenAPI spec.

Do not edit by hand — rerun `tempest openapi-client` to refresh.

The client wraps an injected ``HTTPClient``, so the caller keeps
control of the base URL, timeout, retry policy, circuit breaker and
auth headers. Pass an ``httpx.MockTransport`` through the client in
tests to exercise these methods without network access.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from typing import Any, TypeVar
from urllib.parse import quote

from pydantic import BaseModel, TypeAdapter

from tempest_fastapi_sdk import HTTPClient

from .schemas import (
    AcceptedResponse,
    CheckNumberResponse,
    HistoryResponse,
    QrResponse,
    ReactionRequest,
    ReadRequest,
    SendAudioBase64Request,
    SendAudioRequest,
    SendDocumentBase64Request,
    SendDocumentRequest,
    SendImageBase64Request,
    SendImageRequest,
    SendTextRequest,
    SendVideoBase64Request,
    SendVideoRequest,
    SessionStartResponse,
    SessionStatusResponse,
    TypingRequest,
)

DEFAULT_BASE_URL: str = "/"
"""``servers[0].url`` from the specification."""


def _dump(payload: Any) -> Any:
    """Serialize a request body to JSON-ready data.

    ``exclude_unset`` rides along with ``exclude_none`` so a field
    the caller never touched stays off the wire. An optional array
    is generated with ``default_factory=list``, and to an API
    "informed as empty" is a different claim from "not informed":
    Woovi answers ``{"splits": []}`` with 400 *O array de split
    precisa ter ao menos um item*, and accepts the same body
    without the key.

    Args:
        payload (Any): A generated schema instance, or already-plain
            data when the specification typed the body loosely.

    Returns:
        Any: ``model_dump(by_alias=True, mode="json")`` for a Pydantic
        model — the wire spelling the third party expects — and the
        value untouched for anything else.
    """
    if isinstance(payload, BaseModel):
        return payload.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
            exclude_unset=True,
        )
    return payload


_T = TypeVar("_T")
"""The response type a call was declared to return."""


def _validate(annotation: type[_T], data: Any) -> _T:
    """Validate a response body against the generated annotation.

    Args:
        annotation (type[_T]): The response type — a generated model,
            a ``list[Model]``, or a primitive.
        data (Any): The decoded JSON body.

    Returns:
        _T: The validated value. ``TypeAdapter`` is used rather than
        ``Model.model_validate`` so container and union annotations
        work through the same call site.

    Generic rather than ``-> Any``: every method returns this call's
    result, so an ``Any`` here made each one a
    ``no-any-return`` under a strict type checker — 98 of them on a
    real specification, in the consumer's own gate.
    """
    return TypeAdapter(annotation).validate_python(data)


def _param(value: Any) -> Any:
    """Normalize a query-parameter value for the wire.

    Args:
        value (Any): The argument as the caller passed it.

    Returns:
        Any: ``Enum`` members become their ``value`` and dates their
        ISO-8601 form; lists and tuples are normalized element-wise.
        Without this, an ``Enum`` would reach the query string through
        ``str()`` — which for the SDK's ``BaseStrEnum`` renders
        ``"Class.MEMBER"``, not the value the third party expects.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_param(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _path_param(value: Any) -> str:
    """Escape a value into exactly one path segment.

    Args:
        value (Any): The argument as the caller passed it,
            normalized through ``_param`` first so an ``Enum``
            reaches the path as its value rather than as
            ``"Class.MEMBER"``.

    Returns:
        str: The value percent-encoded with an empty ``safe``
        set, so every reserved character is escaped — ``/``
        included, because an identifier is one segment and must
        not become two.

    Without this, a reserved character does not fail: it
    *retargets*. ``order#42`` interpolated raw yields
    ``/charge/order#42``, whose fragment the HTTP client never
    sends — so the request addresses ``/charge/order``, and on a
    ``DELETE`` route that is a destructive call against a
    different resource.
    """
    return quote(str(_param(value)), safe="")


class ZapClient:
    """Client for zap-api (version 1.0.0)."""

    def __init__(self, client: HTTPClient) -> None:
        """Initialize the client.

        Args:
            client (HTTPClient): The transport to issue requests
                through. Build it with
                ``HTTPClient(base_url=DEFAULT_BASE_URL)`` to target
                the server the specification declares, and attach
                credentials via its ``default_headers``.
        """
        self._client: HTTPClient = client

    async def health(self) -> None:
        """Liveness probe — always 200 while the process is up.

        Answers 200 even while WhatsApp is disconnected: the gateway still accepts sends
        into the outbox. Use /ready to gate traffic.

        Returns:
            None: Nothing — the operation answers 200 with no body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/health"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def check_number(
        self,
        number: str,
    ) -> CheckNumberResponse:
        """Check whether a number is registered on WhatsApp.

        Args:
            number (str): The number value.

        Returns:
            CheckNumberResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = f"/message/check-number/{_path_param(number)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(CheckNumberResponse, response.json())

    async def get_history(
        self,
        chat: str,
        *,
        limit: int | None = None,
    ) -> HistoryResponse:
        """Stored conversation with a number, newest first.

        Lets a bot rebuild context without keeping its own copy of the history. Accepts
        either the digits or the full JID the inbound webhook delivers. The raw Baileys
        envelope is never exposed.

        Args:
            chat (str): Phone digits, or the JID from the inbound webhook
            limit (int | None): The limit value. Omitted from the query when None.

        Returns:
            HistoryResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500, 503.
        """
        path = f"/message/history/{_path_param(chat)}"
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = _param(limit)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(HistoryResponse, response.json())

    async def react(
        self,
        *,
        body: ReactionRequest | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue an emoji reaction to a message.

        Goes through the outbox like any other send. An empty `emoji` removes a reaction
        previously sent.

        Args:
            body (ReactionRequest): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429, 500.
        """
        path = "/message/react"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def mark_read(
        self,
        *,
        body: ReadRequest | None = None,
    ) -> None:
        """Mark inbound messages as read (blue ticks).

        Visible to the other side, so it is an explicit call rather than something the
        gateway does on delivery. Bypasses the outbox for the same reason as presence.

        Args:
            body (ReadRequest): The request body. Optional.

        Returns:
            None: Nothing — the operation answers 204 with no body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429, 503.
        """
        path = "/message/read"
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def send_audio(
        self,
        *,
        body: SendAudioRequest | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue an audio message.

        Args:
            body (SendAudioRequest): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429, 500.
        """
        path = "/message/send-audio"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def send_audio_base64(
        self,
        *,
        body: SendAudioBase64Request | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue an audio file sent as base64.

        Args:
            body (SendAudioBase64Request): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 413, 429, 500.
        """
        path = "/message/send-audio-base64"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def send_document(
        self,
        *,
        body: SendDocumentRequest | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue a document message.

        Args:
            body (SendDocumentRequest): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429, 500.
        """
        path = "/message/send-document"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def send_document_base64(
        self,
        *,
        body: SendDocumentBase64Request | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue a document sent as base64.

        Args:
            body (SendDocumentBase64Request): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 413, 429, 500.
        """
        path = "/message/send-document-base64"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def send_image(
        self,
        *,
        body: SendImageRequest | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue an image message.

        Args:
            body (SendImageRequest): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429, 500.
        """
        path = "/message/send-image"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def send_image_base64(
        self,
        *,
        body: SendImageBase64Request | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue an image sent as base64.

        Args:
            body (SendImageBase64Request): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 413, 429, 500.
        """
        path = "/message/send-image-base64"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def send_text(
        self,
        *,
        body: SendTextRequest | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue a text message.

        Args:
            body (SendTextRequest): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429, 500.
        """
        path = "/message/send-text"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def send_video(
        self,
        *,
        body: SendVideoRequest | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue a video message.

        Args:
            body (SendVideoRequest): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429, 500.
        """
        path = "/message/send-video"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def send_video_base64(
        self,
        *,
        body: SendVideoBase64Request | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Enqueue a video sent as base64.

        Args:
            body (SendVideoBase64Request): The request body. Optional.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 413, 429, 500.
        """
        path = "/message/send-video-base64"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def set_typing(
        self,
        *,
        body: TypingRequest | None = None,
    ) -> None:
        """Publish a typing / recording indicator, or clear it.

        Sent straight to WhatsApp rather than through the outbox: presence is ephemeral,
        and an indicator replayed after a restart would misreport what the sender is
        doing. WhatsApp expires it on its own, so a caller that dies mid-thought stops
        showing as typing with no cleanup call.

        Args:
            body (TypingRequest): The request body. Optional.

        Returns:
            None: Nothing — the operation answers 204 with no body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429, 503.
        """
        path = "/message/typing"
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def upload_audio(
        self,
        *,
        file: bytes,
        to: str,
        reply_to: str | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Upload an audio file and enqueue it.

        The file travels in the request, so nothing has to be publicly reachable first —
        this is the route to use when the media exists only on the caller's disk.

        Capped at `MEDIA_MAX_BYTES` (16MB by default), enforced while the body streams:
        an oversized upload is cut off mid-flight rather than buffered in full and
        refused afterwards.

        Args:
            file (bytes): The file itself — the form's only file part
            to (str): Recipient phone number, digits only
            reply_to (str | None): Message id being replied to, quoted above this one.
                Omitted from the form body when None.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 413, 415, 429, 500.
        """
        path = "/message/upload-audio"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        data: dict[str, Any] = {}
        data["to"] = _param(to)
        if reply_to is not None:
            data["replyTo"] = _param(reply_to)
        files: dict[str, Any] = {}
        files["file"] = file
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            data=data,
            files=files,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def upload_document(
        self,
        *,
        file: bytes,
        to: str,
        file_name: str | None = None,
        reply_to: str | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Upload a document and enqueue it.

        The file travels in the request, so nothing has to be publicly reachable first —
        this is the route to use when the media exists only on the caller's disk.

        Capped at `MEDIA_MAX_BYTES` (16MB by default), enforced while the body streams:
        an oversized upload is cut off mid-flight rather than buffered in full and
        refused afterwards.

        Args:
            file (bytes): The file itself — the form's only file part
            to (str): Recipient phone number, digits only
            file_name (str | None): Name the recipient sees. Falls back to the uploaded
                part's own file name. Omitted from the form body when None.
            reply_to (str | None): Message id being replied to, quoted above this one.
                Omitted from the form body when None.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 413, 415, 429, 500.
        """
        path = "/message/upload-document"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        data: dict[str, Any] = {}
        data["to"] = _param(to)
        if file_name is not None:
            data["fileName"] = _param(file_name)
        if reply_to is not None:
            data["replyTo"] = _param(reply_to)
        files: dict[str, Any] = {}
        files["file"] = file
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            data=data,
            files=files,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def upload_image(
        self,
        *,
        file: bytes,
        to: str,
        caption: str | None = None,
        reply_to: str | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Upload an image and enqueue it.

        The file travels in the request, so nothing has to be publicly reachable first —
        this is the route to use when the media exists only on the caller's disk.

        Capped at `MEDIA_MAX_BYTES` (16MB by default), enforced while the body streams:
        an oversized upload is cut off mid-flight rather than buffered in full and
        refused afterwards.

        Args:
            file (bytes): The file itself — the form's only file part
            to (str): Recipient phone number, digits only
            caption (str | None): The caption value. Omitted from the form body when
                None.
            reply_to (str | None): Message id being replied to, quoted above this one.
                Omitted from the form body when None.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 413, 415, 429, 500.
        """
        path = "/message/upload-image"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        data: dict[str, Any] = {}
        data["to"] = _param(to)
        if caption is not None:
            data["caption"] = _param(caption)
        if reply_to is not None:
            data["replyTo"] = _param(reply_to)
        files: dict[str, Any] = {}
        files["file"] = file
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            data=data,
            files=files,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def upload_video(
        self,
        *,
        file: bytes,
        to: str,
        caption: str | None = None,
        reply_to: str | None = None,
        idempotency_key: str | None = None,
    ) -> AcceptedResponse:
        """Upload a video and enqueue it.

        The file travels in the request, so nothing has to be publicly reachable first —
        this is the route to use when the media exists only on the caller's disk.

        Capped at `MEDIA_MAX_BYTES` (16MB by default), enforced while the body streams:
        an oversized upload is cut off mid-flight rather than buffered in full and
        refused afterwards.

        Args:
            file (bytes): The file itself — the form's only file part
            to (str): Recipient phone number, digits only
            caption (str | None): The caption value. Omitted from the form body when
                None.
            reply_to (str | None): Message id being replied to, quoted above this one.
                Omitted from the form body when None.
            idempotency_key (str | None): Optional. Guards against a retry becoming a
                duplicate message.  Because the send is asynchronous, a lost `202`
                leaves you unable to tell whether the message was enqueued. Send a fresh
                key (a UUID) with each new message, and reuse that same key when
                retrying it: the second call returns the original row with `deduped:
                true` instead of enqueueing a second message. The key is scoped to your
                consumer, and stays claimed for as long as the row exists — so reusing
                an old key for a *new* message answers `deduped: true` and sends
                nothing.  This header does not authenticate. Auth is `x-api-key`
                (Authorize, top right). Omitted from the request headers when None.

        Returns:
            AcceptedResponse: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 413, 415, 429, 500.
        """
        path = "/message/upload-video"
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["idempotency-key"] = str(idempotency_key)
        data: dict[str, Any] = {}
        data["to"] = _param(to)
        if caption is not None:
            data["caption"] = _param(caption)
        if reply_to is not None:
            data["replyTo"] = _param(reply_to)
        files: dict[str, Any] = {}
        files["file"] = file
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            data=data,
            files=files,
        )
        response.raise_for_status()
        return _validate(AcceptedResponse, response.json())

    async def get_message_media(
        self,
        message_id: str,
    ) -> bytes:
        """Media bytes of a stored message.

        Addressed by the WhatsApp message id — the same id the inbound webhook delivers
        as `messageId`, and the one its `mediaUrl` points at.

        Media is fetched while the message is still in memory and never re-downloadable
        from WhatsApp afterwards, so a message whose download failed or exceeded
        `MEDIA_MAX_BYTES` is stored with a media type and no file, and answers `404`
        here.

        The response `Content-Type` is the media's own (`image/jpeg`, `video/mp4`, …),
        not `application/octet-stream`.

        Args:
            message_id (str): The WhatsApp message id (`messageId` in the webhook)

        Returns:
            bytes: The 200 response body, undecoded — the operation answers
                application/octet-stream, which is handed over as bytes rather than
                parsed.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 503.
        """
        path = f"/message/{_path_param(message_id)}/media"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return response.content

    async def metrics(self) -> bytes:
        """Prometheus metrics (text exposition format).

        Returns:
            bytes: The 200 response body, undecoded — the operation answers text/plain,
                which is handed over as bytes rather than parsed.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/metrics"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return response.content

    async def ready(self) -> None:
        """Readiness probe — 503 until the WhatsApp session is connected.

        Returns:
            None: Nothing — the operation answers 200 with no body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                503.
        """
        path = "/ready"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def disconnect_session(self) -> SessionStatusResponse:
        """Log out and invalidate the session.

        A deliberate logout: the reconnect loop does not undo it. Pairing again requires
        a new QR scan.

        Returns:
            SessionStatusResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401.
        """
        path = "/session/disconnect"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(SessionStatusResponse, response.json())

    async def get_session_qr(self) -> QrResponse:
        """Authenticated URL of the current pairing QR.

        Returns:
            QrResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 404.
        """
        path = "/session/qr"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(QrResponse, response.json())

    async def get_session_qr_image(self) -> bytes:
        """The pairing QR as a PNG.

        Served `image/png` with `Cache-Control: no-store`. Behind the same API key as
        everything else — the pairing code is never public.

        Returns:
            bytes: The 200 response body, undecoded — the operation answers image/png,
                which is handed over as bytes rather than parsed.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 404.
        """
        path = "/session/qr/image"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return response.content

    async def start_session(self) -> SessionStartResponse:
        """Start the WhatsApp connection.

        Idempotent: calling it while already connected returns the current status. `qr`
        is the authenticated URL of the pairing image, not the code itself.

        Returns:
            SessionStartResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401.
        """
        path = "/session/start"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(SessionStartResponse, response.json())

    async def get_session_status(self) -> SessionStatusResponse:
        """Current WhatsApp session status.

        Returns:
            SessionStatusResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401.
        """
        path = "/session/status"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(SessionStatusResponse, response.json())


__all__: list[str] = [
    "DEFAULT_BASE_URL",
    "ZapClient",
]

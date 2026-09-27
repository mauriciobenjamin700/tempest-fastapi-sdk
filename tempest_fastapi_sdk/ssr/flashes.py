"""Flash messages: one-shot notices carried across a redirect.

The pattern every SSR panel repeats: a ``POST`` succeeds (or fails a
business rule), answers ``303`` to a screen, and that screen shows
"Bucket criado." once. This module carries the notice in a **signed
cookie** that is read once:

* :func:`flash` queues a message on the current request;
* :class:`FlashMiddleware` writes the queue into the cookie on the way
  out, and on the next request decodes it — refusing a cookie whose
  HMAC-SHA256 signature does not verify or that is older than
  ``max_age``;
* :func:`get_flashes` returns the pending messages and marks them read,
  so the middleware clears the cookie on that response.

The text never travels in the URL, so a forged link cannot put words on
the screen, and a forged cookie fails the signature. The cookie is
signed, **not encrypted**: the person holding it can read it, so a flash
message never carries anything that person may not see.

The signature uses only the standard library (``hmac`` + ``hashlib``),
so this module needs no extra beyond the SDK's base dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast, get_args

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tempest_fastapi_sdk.ui.components.alert import AlertVariant
from tempest_fastapi_sdk.ui.components.flash import FlashMessage

FLASH_COOKIE_NAME: Final[str] = "tempest_flash"
"""Default name of the cookie that carries the pending messages."""

MAX_FLASH_MESSAGE_LENGTH: Final[int] = 500
"""Characters kept per message; longer text is cut and ends with ``…``."""

MAX_FLASH_COOKIE_BYTES: Final[int] = 3800
"""Upper bound of the encoded cookie value.

Browsers drop a cookie above roughly 4096 bytes (name, value and
attributes together) without telling the server. When the queue would
exceed this bound the **oldest** messages are dropped first, so the most
recent notice always arrives.
"""

_STATE_KEY: Final[str] = "tempest_flash"
_SIGNATURE_CONTEXT: Final[bytes] = b"tempest-fastapi-sdk.flash.v1|"
_VARIANTS: Final[frozenset[str]] = frozenset(get_args(AlertVariant))
_NOT_INSTALLED: Final[str] = (
    "Flash messages need FlashMiddleware: "
    "app.add_middleware(FlashMiddleware, secret=...)."
)


@dataclass(slots=True)
class _FlashState:
    """Per-request flash bookkeeping, stored in the ASGI scope state.

    Attributes:
        incoming (list[FlashMessage]): Messages decoded from the request
            cookie.
        outgoing (list[FlashMessage]): Messages queued during this
            request.
        consumed (bool): Whether :func:`get_flashes` read the queue.
        had_cookie (bool): Whether the request carried the cookie at all
            (valid or not), which decides if an empty queue must delete
            it.
    """

    incoming: list[FlashMessage] = field(default_factory=list)
    outgoing: list[FlashMessage] = field(default_factory=list)
    consumed: bool = False
    had_cookie: bool = False


def _b64encode(raw: bytes) -> str:
    """Encode bytes as unpadded URL-safe base64.

    Args:
        raw (bytes): The bytes to encode.

    Returns:
        str: The ASCII encoding, safe inside a cookie value.
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    """Decode unpadded URL-safe base64.

    Args:
        text (str): The encoded text.

    Returns:
        bytes: The decoded bytes.

    Raises:
        ValueError: When ``text`` is not valid base64.
    """
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _truncate(message: str) -> str:
    """Cut a message to :data:`MAX_FLASH_MESSAGE_LENGTH` characters.

    Args:
        message (str): The message text.

    Returns:
        str: The text unchanged when it fits, otherwise cut and ended
        with ``…``.
    """
    if len(message) <= MAX_FLASH_MESSAGE_LENGTH:
        return message
    return message[: MAX_FLASH_MESSAGE_LENGTH - 1] + "…"


class FlashMiddleware:
    """Pure ASGI middleware that carries flash messages in a signed cookie.

    Install it once; then :func:`flash` and :func:`get_flashes` work in
    every route and exception handler behind it.

    The cookie is ``HttpOnly`` (no script reads it), ``SameSite=Lax`` by
    default and scoped to ``path``. Its value is
    ``<payload>.<signature>``, where the signature is HMAC-SHA256 over
    the payload with ``secret`` and a fixed context string, so the same
    secret used elsewhere cannot produce a valid flash cookie. A cookie
    that fails the signature, fails to decode, or is older than
    ``max_age`` seconds is ignored and deleted.

    Example:
        ```python
        from fastapi import FastAPI

        from tempest_fastapi_sdk.ssr import FlashMiddleware

        app: FastAPI = FastAPI()
        app.add_middleware(FlashMiddleware, secret="a-long-random-secret-from-settings")
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        secret: str,
        cookie_name: str = FLASH_COOKIE_NAME,
        max_age: int = 300,
        path: str = "/",
        secure: bool = True,
        samesite: Literal["lax", "strict", "none"] = "lax",
    ) -> None:
        """Initialize the middleware.

        Args:
            app (ASGIApp): The wrapped application.
            secret (str): The signing key. Load it from settings; at least
                16 characters.
            cookie_name (str): Name of the flash cookie.
            max_age (int): Seconds a queued message stays valid. A
                message not read within it is dropped.
            path (str): Cookie path.
            secure (bool): Emit the ``Secure`` flag. Keep ``True`` in
                production; ``False`` only for a plain-HTTP dev server.
            samesite (Literal["lax", "strict", "none"]): ``SameSite``
                policy. ``"lax"`` keeps the cookie on the top-level
                ``303`` that follows a form post.

        Raises:
            ValueError: When ``secret`` is shorter than 16 characters or
                ``max_age`` is not positive.
        """
        if len(secret) < 16:
            raise ValueError("FlashMiddleware secret must be at least 16 characters.")
        if max_age <= 0:
            raise ValueError("FlashMiddleware max_age must be positive.")
        self.app: ASGIApp = app
        self.cookie_name: str = cookie_name
        self.max_age: int = max_age
        self.path: str = path
        self.secure: bool = secure
        self.samesite: Literal["lax", "strict", "none"] = samesite
        self._key: bytes = secret.encode("utf-8")

    def _sign(self, payload: str) -> str:
        """Compute the signature of an encoded payload.

        Args:
            payload (str): The base64 payload.

        Returns:
            str: The base64 HMAC-SHA256 digest.
        """
        digest = hmac.new(
            self._key,
            _SIGNATURE_CONTEXT + payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return _b64encode(digest)

    def encode(self, messages: list[FlashMessage]) -> str:
        """Encode and sign messages into a cookie value.

        Oldest messages are dropped until the value fits
        :data:`MAX_FLASH_COOKIE_BYTES`.

        Args:
            messages (list[FlashMessage]): The messages to carry.

        Returns:
            str: ``<payload>.<signature>``; empty when nothing fits.
        """
        pending = list(messages)
        while pending:
            body = json.dumps(
                {
                    "t": int(time.time()),
                    "m": [[item.message, item.variant] for item in pending],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            payload = _b64encode(body.encode("utf-8"))
            value = f"{payload}.{self._sign(payload)}"
            if len(value) <= MAX_FLASH_COOKIE_BYTES:
                return value
            pending.pop(0)
        return ""

    def decode(self, value: str) -> list[FlashMessage]:
        """Verify and decode a cookie value.

        Args:
            value (str): The raw cookie value.

        Returns:
            list[FlashMessage]: The carried messages; empty when the
            signature does not verify, the value is malformed or it is
            older than ``max_age``.
        """
        payload, _, signature = value.partition(".")
        if not payload or not signature:
            return []
        if not hmac.compare_digest(signature, self._sign(payload)):
            return []
        try:
            data: Any = json.loads(_b64decode(payload).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return []
        if not isinstance(data, dict):
            return []
        issued = data.get("t")
        if not isinstance(issued, int) or time.time() - issued > self.max_age:
            return []
        items = data.get("m")
        if not isinstance(items, list):
            return []
        messages: list[FlashMessage] = []
        for item in items:
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[0], str)
                and item[1] in _VARIANTS
            ):
                messages.append(
                    FlashMessage(message=item[0], variant=cast(AlertVariant, item[1])),
                )
        return messages

    def _set_cookie_header(self, value: str) -> str:
        """Build the ``Set-Cookie`` header for a value (empty deletes).

        Args:
            value (str): The cookie value; ``""`` expires the cookie.

        Returns:
            str: The header value, formatted by Starlette.
        """
        response = Response()
        if value:
            response.set_cookie(
                self.cookie_name,
                value,
                max_age=self.max_age,
                path=self.path,
                secure=self.secure,
                httponly=True,
                samesite=self.samesite,
            )
        else:
            response.delete_cookie(
                self.cookie_name,
                path=self.path,
                secure=self.secure,
                httponly=True,
                samesite=self.samesite,
            )
        return response.headers["set-cookie"]

    def _outgoing_header(self, state: _FlashState) -> str | None:
        """Decide the ``Set-Cookie`` header the response needs, if any.

        Args:
            state (_FlashState): The request's flash state.

        Returns:
            str | None: A header that writes the pending queue, one that
            deletes the cookie, or ``None`` when the cookie the browser
            holds is already right (nothing read, nothing queued).
        """
        if not state.consumed and not state.outgoing:
            if state.had_cookie and not state.incoming:
                return self._set_cookie_header("")
            return None
        pending = ([] if state.consumed else state.incoming) + state.outgoing
        if pending:
            return self._set_cookie_header(self.encode(pending))
        if state.had_cookie:
            return self._set_cookie_header("")
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Decode the flash cookie, run the app, and write the cookie back.

        Args:
            scope (Scope): The ASGI connection scope.
            receive (Receive): The ASGI receive callable.
            send (Send): The ASGI send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = Request(scope).cookies.get(self.cookie_name)
        state = _FlashState(
            incoming=self.decode(raw) if raw else [],
            had_cookie=raw is not None,
        )
        scope.setdefault("state", {})[_STATE_KEY] = state

        async def send_with_cookie(message: Message) -> None:
            """Append the flash ``Set-Cookie`` to the response start.

            Args:
                message (Message): The ASGI message being sent.
            """
            if message["type"] == "http.response.start":
                header = self._outgoing_header(state)
                if header is not None:
                    MutableHeaders(scope=message).append("set-cookie", header)
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def _state(request: Request) -> _FlashState | None:
    """Return the flash state the middleware stored for this request.

    Args:
        request (Request): The current request.

    Returns:
        _FlashState | None: The state, or ``None`` when
        :class:`FlashMiddleware` is not installed.
    """
    stored = request.scope.get("state", {}).get(_STATE_KEY)
    return stored if isinstance(stored, _FlashState) else None


def flash_enabled(request: Request) -> bool:
    """Tell whether :class:`FlashMiddleware` is handling this request.

    Args:
        request (Request): The current request.

    Returns:
        bool: ``True`` when :func:`flash` and :func:`get_flashes` can be
        called for ``request``.
    """
    return _state(request) is not None


def flash(request: Request, message: str, variant: AlertVariant = "info") -> None:
    """Queue a message for the next screen this user sees.

    Call it before returning the redirect; :class:`FlashMiddleware`
    writes the queue into the signed cookie on that response. Text
    longer than :data:`MAX_FLASH_MESSAGE_LENGTH` is cut.

    Args:
        request (Request): The current request.
        message (str): The notice text. Rendered escaped, but carried
            readable in the cookie — never put in it what the user may
            not see.
        variant (AlertVariant): The severity.

    Raises:
        RuntimeError: When :class:`FlashMiddleware` is not installed —
            a message queued without it would be lost silently.
    """
    state = _state(request)
    if state is None:
        raise RuntimeError(_NOT_INSTALLED)
    state.outgoing.append(FlashMessage(message=_truncate(message), variant=variant))


def get_flashes(request: Request) -> list[FlashMessage]:
    """Return every pending message and mark them read.

    Pending means the ones the request's cookie carried plus the ones
    queued by :func:`flash` during this request. After this call the
    middleware clears the cookie on the response, so a reload does not
    repeat them.

    Args:
        request (Request): The current request.

    Returns:
        list[FlashMessage]: The messages, oldest first; empty when there
        are none.

    Raises:
        RuntimeError: When :class:`FlashMiddleware` is not installed.
    """
    state = _state(request)
    if state is None:
        raise RuntimeError(_NOT_INSTALLED)
    messages = list(state.incoming) + list(state.outgoing)
    state.consumed = True
    state.outgoing.clear()
    return messages


__all__: list[str] = [
    "FLASH_COOKIE_NAME",
    "MAX_FLASH_COOKIE_BYTES",
    "MAX_FLASH_MESSAGE_LENGTH",
    "FlashMiddleware",
    "flash",
    "flash_enabled",
    "get_flashes",
]

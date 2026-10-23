"""Sign in to the admin panel from a test client.

``POST {prefix}/login`` requires the CSRF token the login form carries,
so a test cannot post the credentials on their own — it would get the
``403`` a cross-site submit gets. These helpers do what a browser does:
load the form, read the hidden ``csrf_token`` (its cookie lands in the
client's jar), and submit it with the credentials.

The client parameter is typed by what the helpers call, not by a class.
Starlette's ``TestClient`` subclasses ``httpx2.Client`` when ``httpx2`` is
installed and ``httpx.Client`` otherwise, so a parameter typed
``httpx.Client`` refused the ``TestClient`` under a strict type checker
even though the call works (#610). :class:`SyncFormClient` and
:class:`AsyncFormClient` accept any of them, and the helpers return the
response type the given client returns.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Mapping
from typing import Final, Protocol

CSRF_FIELD_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'name="csrf_token"\s+value="([^"]+)"',
)
"""Hidden ``csrf_token`` input as the admin templates render it."""


class FormResponse(Protocol):
    """The part of an HTTP response the login helpers read."""

    @property
    def text(self) -> str:
        """Return the decoded body.

        Returns:
            str: The response body as text.
        """
        ...


class SyncFormClient[ResponseT: FormResponse](Protocol):
    """A sync HTTP client that loads a page and posts a form.

    ``httpx.Client``, ``httpx2.Client`` and Starlette's ``TestClient``
    all satisfy it. The URL is positional-only, so an implementation is
    free to name it as it likes; the optional parameters are named, as
    they can only be passed by keyword.
    """

    def get(self, url: str, /) -> ResponseT:
        """Load ``url``.

        Args:
            url (str): The path or URL to load.

        Returns:
            ResponseT: The response.
        """
        ...

    def post(
        self,
        url: str,
        /,
        *,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool = False,
    ) -> ResponseT:
        """Post a form to ``url``.

        Args:
            url (str): The path or URL to post to.
            data (Mapping[str, str] | None): The form fields.
            headers (Mapping[str, str] | None): Extra request headers.
            follow_redirects (bool): Whether to follow a redirect.

        Returns:
            ResponseT: The response.
        """
        ...


class AsyncFormClient[ResponseT: FormResponse](Protocol):
    """The async counterpart of :class:`SyncFormClient`.

    ``httpx.AsyncClient`` and ``httpx2.AsyncClient`` satisfy it. The
    members return ``Awaitable`` rather than being ``async def``, which
    accepts both a coroutine function and a method returning any other
    awaitable.
    """

    def get(self, url: str, /) -> Awaitable[ResponseT]:
        """Load ``url``.

        Args:
            url (str): The path or URL to load.

        Returns:
            Awaitable[ResponseT]: The response, once awaited.
        """
        ...

    def post(
        self,
        url: str,
        /,
        *,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool = False,
    ) -> Awaitable[ResponseT]:
        """Post a form to ``url``.

        Args:
            url (str): The path or URL to post to.
            data (Mapping[str, str] | None): The form fields.
            headers (Mapping[str, str] | None): Extra request headers.
            follow_redirects (bool): Whether to follow a redirect.

        Returns:
            Awaitable[ResponseT]: The response, once awaited.
        """
        ...


def admin_csrf_token(html: str) -> str:
    """Extract the ``csrf_token`` hidden field from an admin page.

    Args:
        html (str): A rendered admin page carrying a form.

    Returns:
        str: The token value.

    Raises:
        ValueError: When the page has no ``csrf_token`` field — usually
            because it is not a form page, or the request was redirected.
    """
    match = CSRF_FIELD_PATTERN.search(html)
    if match is None:
        raise ValueError("no csrf_token field in the page")
    return match.group(1)


async def admin_login[ResponseT: FormResponse](
    client: AsyncFormClient[ResponseT],
    identifier: str,
    password: str,
    *,
    prefix: str = "/admin",
    headers: dict[str, str] | None = None,
) -> ResponseT:
    """Sign in through the admin login form with an async client.

    Args:
        client (AsyncFormClient[ResponseT]): A client bound to the app —
            ``httpx.AsyncClient`` or ``httpx2.AsyncClient``; it keeps the
            cookies between the two requests.
        identifier (str): The e-mail to submit.
        password (str): The password to submit.
        prefix (str): Where the admin router is mounted.
        headers (dict[str, str] | None): Extra headers for the submit,
            e.g. a trusted client-IP header.

    Returns:
        ResponseT: The ``POST {prefix}/login`` response, of the client's
        own response type, not followed — ``303`` on success, the
        re-rendered form otherwise.
    """
    form = await client.get(f"{prefix}/login")
    return await client.post(
        f"{prefix}/login",
        data={
            "identifier": identifier,
            "password": password,
            "csrf_token": admin_csrf_token(form.text),
        },
        headers=headers or {},
        follow_redirects=False,
    )


def admin_login_sync[ResponseT: FormResponse](
    client: SyncFormClient[ResponseT],
    identifier: str,
    password: str,
    *,
    prefix: str = "/admin",
    headers: dict[str, str] | None = None,
) -> ResponseT:
    """Sign in through the admin login form with a sync client.

    The ``fastapi.testclient.TestClient`` counterpart of
    :func:`admin_login`.

    Args:
        client (SyncFormClient[ResponseT]): A ``TestClient``,
            ``httpx.Client`` or ``httpx2.Client``; it keeps the cookies
            between the two requests.
        identifier (str): The e-mail to submit.
        password (str): The password to submit.
        prefix (str): Where the admin router is mounted.
        headers (dict[str, str] | None): Extra headers for the submit.

    Returns:
        ResponseT: The ``POST {prefix}/login`` response, of the client's
        own response type, not followed.
    """
    form = client.get(f"{prefix}/login")
    return client.post(
        f"{prefix}/login",
        data={
            "identifier": identifier,
            "password": password,
            "csrf_token": admin_csrf_token(form.text),
        },
        headers=headers or {},
        follow_redirects=False,
    )


__all__: list[str] = [
    "CSRF_FIELD_PATTERN",
    "AsyncFormClient",
    "FormResponse",
    "SyncFormClient",
    "admin_csrf_token",
    "admin_login",
    "admin_login_sync",
]

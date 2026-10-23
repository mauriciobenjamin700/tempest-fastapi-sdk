"""Sign in to the admin panel from a test client.

``POST {prefix}/login`` requires the CSRF token the login form carries,
so a test cannot post the credentials on their own — it would get the
``403`` a cross-site submit gets. These helpers do what a browser does:
load the form, read the hidden ``csrf_token`` (its cookie lands in the
client's jar), and submit it with the credentials.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import httpx

CSRF_FIELD_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'name="csrf_token"\s+value="([^"]+)"',
)
"""Hidden ``csrf_token`` input as the admin templates render it."""


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


async def admin_login(
    client: httpx.AsyncClient,
    identifier: str,
    password: str,
    *,
    prefix: str = "/admin",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Sign in through the admin login form with an async client.

    Args:
        client (httpx.AsyncClient): A client bound to the app; it keeps
            the cookies between the two requests.
        identifier (str): The e-mail to submit.
        password (str): The password to submit.
        prefix (str): Where the admin router is mounted.
        headers (dict[str, str] | None): Extra headers for the submit,
            e.g. a trusted client-IP header.

    Returns:
        httpx.Response: The ``POST {prefix}/login`` response, not
        followed — ``303`` on success, the re-rendered form otherwise.
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


def admin_login_sync(
    client: httpx.Client,
    identifier: str,
    password: str,
    *,
    prefix: str = "/admin",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Sign in through the admin login form with a sync client.

    The ``fastapi.testclient.TestClient`` counterpart of
    :func:`admin_login`.

    Args:
        client (httpx.Client): A ``TestClient`` or other sync client; it
            keeps the cookies between the two requests.
        identifier (str): The e-mail to submit.
        password (str): The password to submit.
        prefix (str): Where the admin router is mounted.
        headers (dict[str, str] | None): Extra headers for the submit.

    Returns:
        httpx.Response: The ``POST {prefix}/login`` response, not
        followed.
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
    "admin_csrf_token",
    "admin_login",
    "admin_login_sync",
]

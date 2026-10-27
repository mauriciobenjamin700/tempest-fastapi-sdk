"""Send the auth emails after the response, when the service opts in.

:class:`~tempest_fastapi_sdk.auth.UserAuthService` awaits the SMTP round
trip inside the request by default, so a slow server slows signup,
password reset and email change, and a failure answers ``503
EMAIL_UNAVAILABLE`` (#644). With ``AUTH_SEND_EMAILS_IN_BACKGROUND`` the
service hands the send to the request's :class:`fastapi.BackgroundTasks`
instead: the response leaves first, and the time it takes no longer
depends on whether an email was sent — the difference a password-reset
request for an unknown address used to reveal.

The router owns the request, the service owns the send, and neither
should know the other's signature, so :func:`bind_background_tasks` — a
dependency every route of :func:`~tempest_fastapi_sdk.auth.make_auth_router`
runs — puts the request's ``BackgroundTasks`` in a context variable the
service reads. Outside a request (a CLI command, a task, a test calling
the service) the variable is empty and the send is awaited as before.
Starlette runs a response's background tasks before the request ends, so
the server's graceful shutdown waits for them like any in-flight request.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import BackgroundTasks

LOGGER: logging.Logger = logging.getLogger(__name__)

_REQUEST_BACKGROUND: ContextVar[BackgroundTasks | None] = ContextVar(
    "tempest_auth_background_tasks",
    default=None,
)
"""The current request's ``BackgroundTasks``, or ``None`` outside one."""


async def bind_background_tasks(background: BackgroundTasks) -> None:
    """Expose the request's ``BackgroundTasks`` to the auth service.

    Async on purpose: a sync dependency runs in a worker thread, and a
    context variable set there never reaches the endpoint.

    Args:
        background (BackgroundTasks): Injected by FastAPI.
    """
    _REQUEST_BACKGROUND.set(background)


def current_background_tasks() -> BackgroundTasks | None:
    """Return the ``BackgroundTasks`` bound for this request.

    Returns:
        BackgroundTasks | None: The request's tasks, or ``None`` when the
        caller is not inside a request served by the auth router.
    """
    return _REQUEST_BACKGROUND.get()


def email_domain(address: str) -> str:
    """Keep only the domain of an address, for a log line.

    Args:
        address (str): The recipient.

    Returns:
        str: ``"@example.com"``, or ``"@?"`` without an ``@``.
    """
    _local, at, domain = address.rpartition("@")
    return f"@{domain}" if at else "@?"


async def send_and_log(
    send: Callable[[], Awaitable[object]],
    *,
    recipient: str,
    kind: str,
) -> None:
    """Run a deferred send, turning a failure into a log line.

    Nothing can answer the request any more — the response is gone — so
    the failure is logged at ``ERROR`` with the traceback, the kind of
    email and the recipient's domain only.

    Args:
        send (Callable[[], Awaitable[object]]): The send to run.
        recipient (str): Who it was for.
        kind (str): Which email (``"activation"``, ``"password_reset"``...).
    """
    try:
        await send()
    except Exception:
        LOGGER.exception(
            "Auth email %r to %s failed after the response was sent",
            kind,
            email_domain(recipient),
        )


__all__: list[str] = [
    "bind_background_tasks",
    "current_background_tasks",
    "email_domain",
    "send_and_log",
]

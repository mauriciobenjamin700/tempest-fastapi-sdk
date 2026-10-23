"""The throttled password login both auth routers run.

``make_auth_router`` (JSON) and ``make_auth_pages_router`` (HTML) answer
a spent budget differently — a ``429`` body versus a re-rendered form —
but the budget itself must be the same rule in both, or the HTML route
becomes the way around the JSON one's limits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tempest_fastapi_sdk.auth.exceptions import LoginThrottledException
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.utils.throttle import login_throttle_key

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.auth.service import UserAuthService
    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.utils.throttle import AttemptThrottle


async def throttled_login(
    service: UserAuthService,
    session: AsyncSession,
    *,
    email: str,
    password: str,
    client_ip: str,
    login_throttle: AttemptThrottle | None,
    login_ip_throttle: AttemptThrottle | None,
    throttled_message: str,
) -> BaseUserModel:
    """Run :meth:`UserAuthService.login` behind the login throttles.

    Order matters. The IP budget is read first, so a blocked source does
    not spend the e-mail budget of the address it is trying. Then the
    e-mail budget is **reserved** before the password is checked — the
    reservation, not a later count, is what keeps a burst of concurrent
    guesses from all passing a "not blocked yet" read together. Both are
    spent identically for an e-mail without an account, so neither
    ``429`` reveals which e-mails exist. A refused login counts against
    the IP; a successful one clears the e-mail's key.

    Args:
        service (UserAuthService): The auth service.
        session (AsyncSession): The request-scoped DB session.
        email (str): The submitted e-mail.
        password (str): The submitted password.
        client_ip (str): The address the IP budget is keyed on.
        login_throttle (AttemptThrottle | None): Per-e-mail budget.
        login_ip_throttle (AttemptThrottle | None): Per-IP budget.
        throttled_message (str): Message of the ``429``.

    Returns:
        BaseUserModel: The authenticated user.

    Raises:
        LoginThrottledException: When either budget is spent.
        UnauthorizedException: On wrong credentials (counted against the
            IP budget).
    """
    ip_key = f"login-ip:{client_ip}"
    if login_ip_throttle is not None:
        await login_ip_throttle.raise_if_blocked(
            ip_key,
            message=throttled_message,
            exception=LoginThrottledException,
        )
    email_key = login_throttle_key(email)
    if login_throttle is not None:
        attempt = await login_throttle.hit(email_key)
        if attempt.attempts > login_throttle.max_attempts:
            raise LoginThrottledException(
                message=throttled_message,
                retry_after_seconds=attempt.retry_after_seconds,
            )
    try:
        user = await service.login(session, email=email, password=password)
    except UnauthorizedException:
        if login_ip_throttle is not None:
            await login_ip_throttle.hit(ip_key)
        raise
    if login_throttle is not None:
        await login_throttle.reset(email_key)
    return user


__all__: list[str] = ["throttled_login"]

"""A credential change ends the cookie sessions of ``make_auth_pages_router``.

The pages router opens opaque :class:`SessionAuth` cookie sessions over the
same ``UserAuthService`` that serves the reset and change flows. Before the
router registered ``session_auth.revoke_all`` on the service,
``revoke_user_sessions`` flipped refresh-token rows only: a cookie opened
before a password reset kept answering ``200`` on a protected route while
the old password was already refused.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from tests.auth.test_auth_pages import (
    PASSWORD,
    SESSION_COOKIE,
    Harness,
    PagesUser,
    _account,
    _build,
    _client,
    _form,
    _session_cookie,
)
from tests.auth.test_auth_pages import (
    _dispose_open_engines as _dispose_open_engines,
)

NEW_PASSWORD: str = "brand-new-password-99"


async def _login_cookie(harness: Harness) -> str:
    """Log in through ``/login`` and return the session cookie.

    Args:
        harness (Harness): The app.

    Returns:
        str: The plaintext session id from the cookie.
    """
    async with _client(harness.app) as client:
        response = await _form(
            client, "/login", email="ana@example.com", password=PASSWORD
        )
        assert (await client.get("/me")).status_code == 200
    cookie = _session_cookie(response)
    assert cookie is not None
    return cookie


async def _replay(harness: Harness, cookie: str) -> int:
    """Present ``cookie`` to the protected route from a fresh browser.

    Args:
        harness (Harness): The app.
        cookie (str): The session cookie value.

    Returns:
        int: The status of ``GET /me``.
    """
    async with _client(harness.app) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        return (await client.get("/me")).status_code


class TestCookieSessionsEnd:
    async def test_password_reset_ends_the_cookie_session(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path)
        await _account(harness)
        cookie = await _login_cookie(harness)
        async with harness.factory() as session:
            token = await harness.service.request_password_reset(
                session, email="ana@example.com"
            )
            await session.commit()
        assert token is not None
        async with harness.factory() as session:
            await harness.service.confirm_password_reset(
                session, token=token.token, new_password=NEW_PASSWORD
            )
            await session.commit()
        assert await harness.session_auth.resolve(cookie) is None
        assert await _replay(harness, cookie) in {303, 307, 401}

    async def test_password_change_ends_the_cookie_session(
        self, tmp_path: Path
    ) -> None:
        harness = await _build(tmp_path)
        user = await _account(harness)
        cookie = await _login_cookie(harness)
        async with harness.factory() as session:
            loaded = await session.get(PagesUser, user.id)
            assert loaded is not None
            await harness.service.change_password(
                session,
                user=loaded,
                current_password=PASSWORD,
                new_password=NEW_PASSWORD,
            )
            await session.commit()
        assert await harness.session_auth.resolve(cookie) is None

    async def test_opt_out_keeps_the_cookie_session(self, tmp_path: Path) -> None:
        """``revoke_sessions=False`` stays the explicit way to keep them."""
        harness = await _build(tmp_path)
        user = await _account(harness)
        cookie = await _login_cookie(harness)
        async with harness.factory() as session:
            loaded = await session.get(PagesUser, user.id)
            assert loaded is not None
            await harness.service.change_password(
                session,
                user=loaded,
                current_password=PASSWORD,
                new_password=NEW_PASSWORD,
                revoke_sessions=False,
            )
            await session.commit()
        assert await _replay(harness, cookie) == 200

    async def test_another_users_cookie_survives(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path)
        ana = await _account(harness)
        other = await _account(harness, "bia@example.com")
        async with _client(harness.app) as client:
            response = await _form(
                client, "/login", email="bia@example.com", password=PASSWORD
            )
        bystander = _session_cookie(response)
        assert bystander is not None
        async with harness.factory() as session:
            revoked = await harness.service.revoke_user_sessions(
                session, user_id=ana.id
            )
        assert revoked == 0
        resolved = await harness.session_auth.resolve(bystander)
        assert resolved is not None
        assert UUID(str(resolved.user_id)) == other.id


class TestRegistration:
    async def test_the_router_registers_revoke_all_once(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path)
        assert harness.service.session_revokers == [harness.session_auth.revoke_all]
        harness.service.add_session_revoker(harness.session_auth.revoke_all)
        assert len(harness.service.session_revokers) == 1

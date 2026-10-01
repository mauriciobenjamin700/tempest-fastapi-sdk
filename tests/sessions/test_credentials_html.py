"""Fixed-credential ``SessionAuth`` and the middleware-free HTML dependency (#343)."""

from __future__ import annotations

import contextlib
import hmac
import subprocess
import sys
from collections.abc import AsyncIterator
from uuid import UUID, uuid4, uuid5

import pytest
from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from tempest_fastapi_sdk import (
    STATIC_CREDENTIAL_NAMESPACE,
    BaseUserModel,
    MemorySessionStore,
    PasswordUtils,
    Session,
    SessionAuth,
    SessionAuthenticator,
    SessionMiddleware,
    SessionSettings,
    StaticCredentialAuthenticator,
    make_session_dependency,
    make_session_router,
    redirect_to,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.sessions import authenticator as authenticator_module
from tempest_fastapi_sdk.utils import password as password_module


class _CredentialTestUser(BaseUserModel):
    __tablename__ = "session_credential_test_users"


def _settings() -> SessionSettings:
    return SessionSettings(SESSION_COOKIE_SECURE=False, SESSION_TTL_SECONDS=600)


def _auth(store: MemorySessionStore | None = None) -> SessionAuth:
    return SessionAuth.from_credentials(
        "root",
        "s3cret-root-pass",
        store=store or MemorySessionStore(),
        settings=_settings(),
    )


class TestStaticCredentialAuthenticator:
    async def test_accepts_the_pair_and_returns_derived_id(self) -> None:
        auth = StaticCredentialAuthenticator("root", "pw")
        assert await auth.authenticate("root", "pw") == uuid5(
            STATIC_CREDENTIAL_NAMESPACE, "root"
        )

    async def test_explicit_user_id_wins(self) -> None:
        uid = uuid4()
        auth = StaticCredentialAuthenticator("root", SecretStr("pw"), user_id=uid)
        assert await auth.authenticate("root", "pw") == uid

    @pytest.mark.parametrize(
        ("username", "password"),
        [
            ("root", "wrong"),
            ("admin", "pw"),
            ("admin", "wrong"),
            ("", ""),
            ("root", "pw "),
        ],
    )
    async def test_rejects_with_one_message(self, username: str, password: str) -> None:
        auth = StaticCredentialAuthenticator("root", "pw")
        with pytest.raises(UnauthorizedException) as info:
            await auth.authenticate(username, password)
        assert info.value.message == "invalid username or password"

    async def test_non_ascii_input_is_rejected_not_type_error(self) -> None:
        auth = StaticCredentialAuthenticator("root", "senha-ç")
        assert await auth.authenticate("root", "senha-ç") == auth.user_id
        with pytest.raises(UnauthorizedException):
            await auth.authenticate("rööt", "senha-ç")

    @pytest.mark.parametrize(
        ("username", "password"),
        [("root", "wrong"), ("admin", "pw"), ("admin", "wrong"), ("root", "pw")],
    )
    async def test_both_halves_always_compared(
        self,
        monkeypatch: pytest.MonkeyPatch,
        username: str,
        password: str,
    ) -> None:
        """No short-circuit: a wrong username still pays for the password check."""
        calls: list[tuple[int, int]] = []
        real = hmac.compare_digest

        def _counting(a: bytes, b: bytes) -> bool:
            calls.append((len(a), len(b)))
            return real(a, b)

        monkeypatch.setattr(authenticator_module.hmac, "compare_digest", _counting)
        auth = StaticCredentialAuthenticator("root", "pw")
        with contextlib.suppress(UnauthorizedException):
            await auth.authenticate(username, password)
        assert calls == [(32, 32), (32, 32)]

    @pytest.mark.parametrize(
        ("username", "password"), [("", "pw"), ("root", ""), ("root", SecretStr(""))]
    )
    def test_empty_credential_refused_at_construction(
        self,
        username: str,
        password: str | SecretStr,
    ) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            StaticCredentialAuthenticator(username, password)

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(
            StaticCredentialAuthenticator("root", "pw"), SessionAuthenticator
        )


class TestSessionAuthModes:
    def test_needs_exactly_one_of_user_model_and_authenticator(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            SessionAuth(store=MemorySessionStore(), settings=_settings())
        with pytest.raises(ValueError, match="exactly one"):
            SessionAuth(
                user_model=_CredentialTestUser,
                authenticator=StaticCredentialAuthenticator("root", "pw"),
                store=MemorySessionStore(),
                settings=_settings(),
            )

    async def test_user_model_mode_refuses_credential_login(self) -> None:
        auth = SessionAuth(
            user_model=_CredentialTestUser,
            store=MemorySessionStore(),
            settings=_settings(),
        )
        with pytest.raises(RuntimeError, match="user_model"):
            await auth.login_with_credentials("root", "pw")

    async def test_credential_mode_refuses_table_authenticate(self) -> None:
        with pytest.raises(RuntimeError, match="authenticator"):
            await _auth().authenticate(None, email="a@b.c", password="x")  # type: ignore[arg-type]

    def test_bundled_json_router_refuses_credential_mode(self) -> None:
        async def _factory() -> AsyncIterator[object]:
            yield object()

        with pytest.raises(ValueError, match="user_model"):
            make_session_router(_auth(), session_factory=_factory)  # type: ignore[arg-type]

    async def test_custom_authenticator_is_used(self) -> None:
        uid = uuid4()

        class _Upstream:
            async def authenticate(self, name: str, secret: str, /) -> UUID:
                if (name, secret) != ("ana", "ok"):
                    raise UnauthorizedException(message="nope")
                return uid

        store = MemorySessionStore()
        auth = SessionAuth(authenticator=_Upstream(), store=store, settings=_settings())
        session, plaintext = await auth.login_with_credentials("ana", "ok")
        assert session.user_id == uid
        assert (await auth.resolve(plaintext)) is not None

    async def test_failed_login_keeps_previous_session(self) -> None:
        auth = _auth()
        _first, plain = await auth.login_with_credentials("root", "s3cret-root-pass")
        with pytest.raises(UnauthorizedException):
            await auth.login_with_credentials(
                "root", "wrong", previous_session_id=plain
            )
        assert await auth.resolve(plain) is not None


def _html_app(auth: SessionAuth, *, with_handlers: bool) -> FastAPI:
    """Admin panel with a form login, one protected page and logout — no middleware."""
    app = FastAPI()
    if with_handlers:
        register_exception_handlers(app)
    settings = auth.settings
    require_admin = make_session_dependency(
        session_auth=auth, on_missing=redirect_to("/login")
    )

    @app.post("/login")
    async def login(
        request: Request,
        username: str = Form(),
        password: str = Form(),
    ) -> Response:
        try:
            _session, plaintext = await auth.login_with_credentials(
                username,
                password,
                previous_session_id=request.cookies.get(settings.SESSION_COOKIE_NAME),
            )
        except UnauthorizedException:
            return HTMLResponse("invalid", status_code=status.HTTP_401_UNAUTHORIZED)
        response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(value=plaintext, **settings.session_cookie_kwargs())
        return response

    @app.get("/admin")
    async def admin(session: Session = Depends(require_admin)) -> HTMLResponse:
        return HTMLResponse(f"hello {session.user_id}")

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
        if cookie:
            await auth.revoke(cookie)
        response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(**settings.session_cookie_delete_kwargs())
        return response

    return app


@pytest.mark.parametrize("with_handlers", [False, True])
class TestHtmlFlowWithoutMiddleware:
    async def test_redirect_to_login_without_cookie(self, with_handlers: bool) -> None:
        app = _html_app(_auth(), with_handlers=with_handlers)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/admin")
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    async def test_redirect_with_unknown_cookie(self, with_handlers: bool) -> None:
        app = _html_app(_auth(), with_handlers=with_handlers)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://t",
            cookies={"tempest_session": "forged"},
        ) as c:
            r = await c.get("/admin")
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    async def test_login_then_page_then_logout_revokes(
        self, with_handlers: bool
    ) -> None:
        store = MemorySessionStore()
        auth = _auth(store)
        app = _html_app(auth, with_handlers=with_handlers)
        owner = uuid5(STATIC_CREDENTIAL_NAMESPACE, "root")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            bad = await c.post("/login", data={"username": "root", "password": "nope"})
            assert bad.status_code == 401
            assert "tempest_session" not in c.cookies

            r = await c.post(
                "/login", data={"username": "root", "password": "s3cret-root-pass"}
            )
            assert r.status_code == 303
            assert r.headers["location"] == "/admin"
            plaintext = c.cookies["tempest_session"]

            page = await c.get("/admin")
            assert page.status_code == 200
            assert page.text == f"hello {owner}"
            assert len(await store.list_by_user(owner)) == 1

            out = await c.post("/logout")
            assert out.status_code == 303
            assert "max-age=0" in out.headers["set-cookie"].lower()
            assert await auth.resolve(plaintext) is None
            assert await store.list_by_user(owner) == []

            c.cookies.set("tempest_session", plaintext)
            again = await c.get("/admin")
        assert again.status_code == 303
        assert again.headers["location"] == "/login"


class TestSessionDependency:
    async def test_without_on_missing_answers_401(self) -> None:
        app = FastAPI()
        register_exception_handlers(app)
        dep = make_session_dependency(session_auth=_auth())

        @app.get("/me")
        async def me(session: Session = Depends(dep)) -> dict[str, str]:
            return {"user_id": str(session.user_id)}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/me")
        assert r.status_code == 401

    async def test_optional_returns_none(self) -> None:
        app = FastAPI()
        dep = make_session_dependency(required=False, session_auth=_auth())

        @app.get("/maybe")
        async def maybe(session: Session | None = Depends(dep)) -> dict[str, bool]:
            return {"authenticated": session is not None}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/maybe")
        assert r.json() == {"authenticated": False}

    async def test_reuses_the_middleware_session(self) -> None:
        """With the middleware mounted, the dependency does not resolve twice."""
        auth = _auth()
        _session, plaintext = await auth.login_with_credentials(
            "root", "s3cret-root-pass"
        )
        resolves: list[str] = []
        real_resolve = auth.resolve

        async def _counting(value: str) -> Session | None:
            resolves.append(value)
            return await real_resolve(value)

        auth.resolve = _counting  # type: ignore[method-assign]
        app = FastAPI()
        app.add_middleware(SessionMiddleware, session_auth=auth, settings=auth.settings)
        dep = make_session_dependency(session_auth=auth)

        @app.get("/me")
        async def me(session: Session = Depends(dep)) -> dict[str, str]:
            return {"user_id": str(session.user_id)}

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://t",
            cookies={"tempest_session": plaintext},
        ) as c:
            r = await c.get("/me")
        assert r.status_code == 200
        assert resolves == [plaintext]

    def test_on_missing_requires_required(self) -> None:
        with pytest.raises(ValueError, match="required=True"):
            make_session_dependency(required=False, on_missing=redirect_to("/login"))

    def test_redirect_to_refuses_non_redirect_status(self) -> None:
        with pytest.raises(ValueError, match="3xx"):
            redirect_to("/login", status_code=401)


_NO_AUTH_EXTRA_SCRIPT: str = """
import asyncio
import sys

sys.modules["bcrypt"] = None
sys.modules["jwt"] = None

from tempest_fastapi_sdk.sessions import MemorySessionStore, SessionAuth
from tempest_fastapi_sdk.settings import SessionSettings

auth = SessionAuth.from_credentials(
    "root", "s3cret", store=MemorySessionStore(), settings=SessionSettings()
)


async def main() -> None:
    session, plaintext = await auth.login_with_credentials("root", "s3cret")
    resolved = await auth.resolve(plaintext)
    assert resolved is not None
    assert resolved.user_id == session.user_id


asyncio.run(main())
for name in ("bcrypt", "jwt"):
    assert sys.modules[name] is None, name
print("ok")
"""


class TestCredentialModeWithoutAuthExtra:
    """The ``authenticator=`` mode never touches bcrypt, so it must not need it (#373).

    ``SessionAuth.__init__`` used to build a ``PasswordUtils()`` eagerly,
    which raises ``ImportError`` without the ``[auth]`` extra, so
    ``from_credentials`` failed in a service installed with ``[ssr]`` only.
    """

    def test_from_credentials_runs_with_bcrypt_and_jwt_unimportable(self) -> None:
        """A fresh interpreter where ``import bcrypt`` / ``import jwt`` raise.

        A subprocess, because ``utils.password`` binds bcrypt at import time
        and this interpreter already imported it; ``sys.modules[name] = None``
        makes the import statement raise ``ImportError``, the same thing a
        venv without the extra does.
        """
        result = subprocess.run(
            [sys.executable, "-c", _NO_AUTH_EXTRA_SCRIPT],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"

    def test_passwords_is_not_built_until_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(password_module, "_bcrypt", None)
        auth = _auth()
        with pytest.raises(ImportError, match=r"\[auth\] extra"):
            _ = auth.passwords

    def test_user_model_mode_still_fails_at_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(password_module, "_bcrypt", None)
        with pytest.raises(ImportError, match=r"\[auth\] extra"):
            SessionAuth(
                user_model=_CredentialTestUser,
                store=MemorySessionStore(),
                settings=_settings(),
            )

    def test_passwords_is_built_once_and_cached(self) -> None:
        auth = _auth()
        assert isinstance(auth.passwords, PasswordUtils)
        assert auth.passwords is auth.passwords

    def test_injected_passwords_is_kept_and_assignable(self) -> None:
        injected = PasswordUtils(rounds=4)
        auth = SessionAuth(
            user_model=_CredentialTestUser,
            store=MemorySessionStore(),
            settings=_settings(),
            passwords=injected,
        )
        assert auth.passwords is injected
        replacement = PasswordUtils(rounds=5)
        auth.passwords = replacement
        assert auth.passwords is replacement

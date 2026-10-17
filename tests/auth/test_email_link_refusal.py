"""Per-account refusal of the emailed-link flows (issue #566).

``UserAuthService(refuses_email_link=...)`` takes a predicate over the
account. When it matches, the *request* routes mint no token and send no
mail but answer exactly like an unknown address, so the refusal cannot be
enumerated; the *confirm* routes raise
:class:`~tempest_fastapi_sdk.EmailLinkForbiddenException` — ``403`` — after
spending the token and before touching the password, so a link that leaked
anyway authorizes nothing. ``None`` (the default) keeps the old behavior.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import tempest_fastapi_sdk
from tempest_fastapi_sdk import (
    ActivationToken,
    ActivationWithheld,
    AuthExceptions,
    BaseModel,
    BaseUserModel,
    EmailLinkForbiddenException,
    EmailUtils,
    UserAuthService,
    default_message_catalog,
    make_auth_router,
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"
REFUSED_EMAIL: str = "locked@a.com"
OPEN_EMAIL: str = "open@a.com"
GHOST_EMAIL: str = "ghost@a.com"


class _RefuseUser(BaseUserModel):
    __tablename__ = "email_refusal_test_users"


_RefuseUserToken = make_user_token_model(
    user_table="email_refusal_test_users",
    tablename="email_refusal_test_user_tokens",
    class_name="_RefuseUserToken",
)
_RefuseRefreshToken = make_user_refresh_token_model(
    user_table="email_refusal_test_users",
    tablename="email_refusal_test_refresh_tokens",
    class_name="_RefuseRefreshToken",
)


def _locked(user: BaseUserModel) -> bool:
    """Predicate refusing exactly one address.

    Args:
        user (BaseUserModel): The account being classified.

    Returns:
        bool: Whether the account must not use emailed links.
    """
    return user.email == REFUSED_EMAIL


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory database.

    Yields:
        AsyncSession: The session every request of a test shares.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(
    *,
    refuses: Callable[[BaseUserModel], bool] | None = None,
    auto_activate: bool = False,
    return_token: bool = True,
    email: EmailUtils | None = None,
    backend_links: bool = False,
    exceptions: AuthExceptions | None = None,
) -> UserAuthService:
    """Build an auth service wired for the refusal scenarios.

    Args:
        refuses (Callable[[BaseUserModel], bool] | None): The predicate
            under test. ``None`` keeps the pre-#566 behavior.
        auto_activate (bool): Whether a signup is born active.
        return_token (bool): Whether links are surfaced in the response.
        email (EmailUtils | None): Mailer, when the test asserts on mail.
        backend_links (bool): Whether to mount the HTML variants.
        exceptions (AuthExceptions | None): Override for the raised-class
            bundle; ``None`` keeps the SDK defaults.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_RefuseUser,
        token_model=_RefuseUserToken,
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=auto_activate,
            AUTH_RETURN_TOKEN_IN_RESPONSE=return_token,
            AUTH_BACKEND_LINKS=backend_links,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=email,
        refresh_token_model=_RefuseRefreshToken,
        exceptions=exceptions,
        refuses_email_link=refuses,
    )


def _client(
    service: UserAuthService,
    session: AsyncSession,
    *,
    catalog: bool = False,
) -> AsyncClient:
    """Mount the auth router over a shared session.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session every request shares.
        catalog (bool): Whether to translate error bodies with the SDK's
            built-in message catalog.

    Returns:
        AsyncClient: A client bound to the app.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    if catalog:
        register_exception_handlers(app, catalog=default_message_catalog())
    else:
        register_exception_handlers(app)
    app.include_router(make_auth_router(service, session_factory=_factory))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _token_from_url(url: str) -> str:
    """Pull the ``token`` query value out of a link the SDK built.

    Args:
        url (str): The activation or reset URL.

    Returns:
        str: The plaintext single-use token.
    """
    return parse_qs(urlparse(url).query)["token"][0]


async def _count_tokens(session: AsyncSession) -> int:
    """Count every token row, regardless of purpose.

    Args:
        session (AsyncSession): Active session.

    Returns:
        int: The number of persisted tokens.
    """
    result = await session.execute(select(func.count()).select_from(_RefuseUserToken))
    return int(result.scalar_one())


class TestTheExceptionIsPublic:
    def test_it_is_exported_with_its_code(self) -> None:
        """Both the class and its ``403`` code reach the consumer."""
        assert "EmailLinkForbiddenException" in tempest_fastapi_sdk.__all__
        assert EmailLinkForbiddenException.code == "EMAIL_LINK_FORBIDDEN"
        assert EmailLinkForbiddenException().status_code == 403

    async def test_a_swapped_class_is_the_one_raised(
        self, session: AsyncSession
    ) -> None:
        """Following the ``account_inactive`` seam, the route answers its code."""

        class _Custom(EmailLinkForbiddenException):
            code: str = "CUSTOM_EMAIL_LINK_FORBIDDEN"

        issuer = _service(auto_activate=False, return_token=True)
        guard = _service(
            refuses=_locked,
            auto_activate=False,
            exceptions=AuthExceptions(email_link_forbidden=_Custom),
        )
        _, activation = await issuer.signup(
            session, email=REFUSED_EMAIL, password=PASSWORD
        )
        assert isinstance(activation, ActivationToken)
        await session.commit()
        async with _client(guard, session) as client:
            refused = await client.post(f"/auth/activate/{activation.token}")
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "CUSTOM_EMAIL_LINK_FORBIDDEN"

    @pytest.mark.parametrize(
        ("language", "detail"),
        [
            ("pt-BR", "Esta conta não aceita links por e-mail"),
            ("en", "This account does not accept email links"),
        ],
    )
    async def test_the_catalog_translates_the_refusal(
        self, session: AsyncSession, language: str, detail: str
    ) -> None:
        """The ``EMAIL_LINK_FORBIDDEN`` key has a PT-BR and an EN text."""
        issuer = _service(auto_activate=False, return_token=True)
        guard = _service(refuses=_locked, auto_activate=False)
        _, activation = await issuer.signup(
            session, email=REFUSED_EMAIL, password=PASSWORD
        )
        assert isinstance(activation, ActivationToken)
        await session.commit()
        async with _client(guard, session, catalog=True) as client:
            refused = await client.post(
                f"/auth/activate/{activation.token}",
                headers={"Accept-Language": language},
            )
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "EMAIL_LINK_FORBIDDEN"
        assert refused.json()["detail"] == detail


class TestPredicateIsConsulted:
    async def test_none_means_the_old_behavior(self, session: AsyncSession) -> None:
        """Without a predicate every account keeps receiving links."""
        service = _service(auto_activate=False, return_token=True)
        await service.signup(session, email=REFUSED_EMAIL, password=PASSWORD)
        await session.commit()
        activation = await service.request_activation(session, email=REFUSED_EMAIL)
        assert isinstance(activation, ActivationToken)

    async def test_it_receives_the_service_user_model(
        self, session: AsyncSession
    ) -> None:
        """The predicate is called with the configured ``user_model``."""
        seen: list[BaseUserModel] = []

        def _record(user: BaseUserModel) -> bool:
            seen.append(user)
            return False

        service = _service(refuses=_record, auto_activate=False)
        await service.signup(session, email=OPEN_EMAIL, password=PASSWORD)
        await session.commit()
        await service.request_activation(session, email=OPEN_EMAIL)
        assert seen
        assert all(isinstance(user, _RefuseUser) for user in seen)


class TestRequestMintsNothing:
    async def test_activation_request_mints_nothing_for_a_refused_account(
        self, session: AsyncSession
    ) -> None:
        """The resend is short-circuited before the token insert.

        The account is created by a service without the predicate, so it
        holds the signup token a pre-#566 deployment would have issued.
        """
        issuer = _service(auto_activate=False, return_token=True)
        service = _service(refuses=_locked, auto_activate=False, return_token=True)
        await issuer.signup(session, email=REFUSED_EMAIL, password=PASSWORD)
        await session.commit()
        before = await _count_tokens(session)
        assert before == 1
        result = await service.request_activation(session, email=REFUSED_EMAIL)
        assert result is None
        assert await _count_tokens(session) == before

    async def test_activation_request_still_mints_for_a_normal_account(
        self, session: AsyncSession
    ) -> None:
        """The predicate does not over-trigger on other accounts."""
        service = _service(refuses=_locked, auto_activate=False, return_token=True)
        await service.signup(session, email=OPEN_EMAIL, password=PASSWORD)
        await session.commit()
        before = await _count_tokens(session)
        result = await service.request_activation(session, email=OPEN_EMAIL)
        assert result is not None
        assert await _count_tokens(session) == before + 1

    async def test_password_reset_request_mints_nothing_for_a_refused_account(
        self, session: AsyncSession
    ) -> None:
        """No reset token is persisted for the refused account."""
        service = _service(refuses=_locked, auto_activate=True, return_token=True)
        await service.signup(session, email=REFUSED_EMAIL, password=PASSWORD)
        await session.commit()
        result = await service.request_password_reset(session, email=REFUSED_EMAIL)
        assert result is None
        assert await _count_tokens(session) == 0

    async def test_password_reset_request_still_mints_for_a_normal_account(
        self, session: AsyncSession
    ) -> None:
        """The control: an ordinary account gets its reset token."""
        service = _service(refuses=_locked, auto_activate=True, return_token=True)
        await service.signup(session, email=OPEN_EMAIL, password=PASSWORD)
        await session.commit()
        result = await service.request_password_reset(session, email=OPEN_EMAIL)
        assert result is not None
        assert await _count_tokens(session) == 1


class TestRequestResponseIsIndistinguishable:
    async def test_activation_request_answers_alike(
        self, session: AsyncSession
    ) -> None:
        """Refused, normal and unknown all get byte-identical 202 bodies."""
        mail = FakeEmailUtils()
        service = _service(
            refuses=_locked, auto_activate=False, return_token=False, email=mail
        )
        async with _client(service, session) as client:
            for email in (REFUSED_EMAIL, OPEN_EMAIL):
                created = await client.post(
                    "/auth/signup", json={"email": email, "password": PASSWORD}
                )
                assert created.status_code == 201, created.text
            mail.outbox.clear()
            before = await _count_tokens(session)
            bodies = []
            for email in (REFUSED_EMAIL, OPEN_EMAIL, GHOST_EMAIL):
                response = await client.post(
                    "/auth/activation/request", json={"email": email}
                )
                assert response.status_code == 202, response.text
                bodies.append(response.content)
        assert bodies[0] == bodies[1] == bodies[2]
        assert [message.to[0] for message in mail.outbox] == [OPEN_EMAIL]
        assert await _count_tokens(session) == before + 1

    async def test_password_reset_request_answers_alike(
        self, session: AsyncSession
    ) -> None:
        """Same generic body whether or not the account refuses links."""
        mail = FakeEmailUtils()
        service = _service(
            refuses=_locked, auto_activate=True, return_token=False, email=mail
        )
        async with _client(service, session) as client:
            for email in (REFUSED_EMAIL, OPEN_EMAIL):
                created = await client.post(
                    "/auth/signup", json={"email": email, "password": PASSWORD}
                )
                assert created.status_code == 201, created.text
            mail.outbox.clear()
            before = await _count_tokens(session)
            bodies = []
            for email in (REFUSED_EMAIL, OPEN_EMAIL, GHOST_EMAIL):
                response = await client.post(
                    "/auth/password-reset/request", json={"email": email}
                )
                assert response.status_code == 202, response.text
                bodies.append(response.content)
        assert bodies[0] == bodies[1] == bodies[2]
        assert [message.to[0] for message in mail.outbox] == [OPEN_EMAIL]
        assert await _count_tokens(session) == before + 1


class TestConfirmReturns403Json:
    async def test_activate_refuses_and_spends_the_token(
        self, session: AsyncSession
    ) -> None:
        """403 on the JSON route; the token cannot be replayed."""
        issuer = _service(auto_activate=False, return_token=True)
        guard = _service(refuses=_locked, auto_activate=False)
        user, activation = await issuer.signup(
            session, email=REFUSED_EMAIL, password=PASSWORD
        )
        assert isinstance(activation, ActivationToken)
        await session.commit()
        async with _client(guard, session) as client:
            refused = await client.post(f"/auth/activate/{activation.token}")
            assert refused.status_code == 403, refused.text
            assert refused.json()["code"] == "EMAIL_LINK_FORBIDDEN"
            replayed = await client.post(f"/auth/activate/{activation.token}")
        assert replayed.status_code == 401, replayed.text
        assert replayed.json()["code"] == "INVALID_TOKEN"
        await session.refresh(user)
        assert user.is_active is False

    async def test_password_reset_confirm_refuses_keeps_password_spends_token(
        self, session: AsyncSession
    ) -> None:
        """403 on the JSON route; the old password still works."""
        issuer = _service(auto_activate=True, return_token=True)
        guard = _service(refuses=_locked, auto_activate=True)
        user, _ = await issuer.signup(session, email=REFUSED_EMAIL, password=PASSWORD)
        token = await issuer.request_password_reset(session, email=REFUSED_EMAIL)
        assert token is not None
        await session.commit()
        async with _client(guard, session) as client:
            refused = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token.token, "new_password": NEW_PASSWORD},
            )
            assert refused.status_code == 403, refused.text
            assert refused.json()["code"] == "EMAIL_LINK_FORBIDDEN"
            replayed = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token.token, "new_password": NEW_PASSWORD},
            )
        assert replayed.status_code == 401, replayed.text
        assert replayed.json()["code"] == "INVALID_TOKEN"
        await session.refresh(user)
        assert guard.passwords.verify(PASSWORD, user.hashed_password)
        assert not guard.passwords.verify(NEW_PASSWORD, user.hashed_password)


class TestConfirmReturns403Html:
    async def test_activate_html_renders_the_error_page_and_spends_the_token(
        self, session: AsyncSession
    ) -> None:
        """The landing page answers a 403 HTML page, not a JSON body."""
        issuer = _service(auto_activate=False, return_token=True)
        guard = _service(refuses=_locked, auto_activate=False, backend_links=True)
        user, activation = await issuer.signup(
            session, email=REFUSED_EMAIL, password=PASSWORD
        )
        assert isinstance(activation, ActivationToken)
        await session.commit()
        async with _client(guard, session) as client:
            refused = await client.get(f"/auth/activate/{activation.token}")
            replayed = await client.post(f"/auth/activate/{activation.token}")
        assert refused.status_code == 403, refused.text
        assert refused.headers["content-type"].startswith("text/html")
        assert "does not accept email links" in refused.text
        assert replayed.json()["code"] == "INVALID_TOKEN"
        await session.refresh(user)
        assert user.is_active is False

    async def test_password_reset_form_page_is_refused(
        self, session: AsyncSession
    ) -> None:
        """The GET form is not shown, and peeking leaves the token alone."""
        issuer = _service(auto_activate=True, return_token=True)
        guard = _service(refuses=_locked, auto_activate=True, backend_links=True)
        await issuer.signup(session, email=REFUSED_EMAIL, password=PASSWORD)
        token = await issuer.request_password_reset(session, email=REFUSED_EMAIL)
        assert token is not None
        await session.commit()
        async with _client(guard, session) as client:
            refused = await client.get(f"/auth/password-reset/{token.token}")
        assert refused.status_code == 403, refused.text
        assert refused.headers["content-type"].startswith("text/html")
        assert "does not accept email links" in refused.text
        assert 'name="new_password"' not in refused.text

    async def test_password_reset_form_submit_refuses_and_keeps_password(
        self, session: AsyncSession
    ) -> None:
        """The form-encoded submit renders a 403 page; the hash is intact."""
        issuer = _service(auto_activate=True, return_token=True)
        guard = _service(refuses=_locked, auto_activate=True, backend_links=True)
        user, _ = await issuer.signup(session, email=REFUSED_EMAIL, password=PASSWORD)
        token = await issuer.request_password_reset(session, email=REFUSED_EMAIL)
        assert token is not None
        await session.commit()
        async with _client(guard, session) as client:
            refused = await client.post(
                f"/auth/password-reset/{token.token}",
                data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            )
            replayed = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token.token, "new_password": NEW_PASSWORD},
            )
        assert refused.status_code == 403, refused.text
        assert refused.headers["content-type"].startswith("text/html")
        assert replayed.json()["code"] == "INVALID_TOKEN"
        await session.refresh(user)
        assert guard.passwords.verify(PASSWORD, user.hashed_password)

    async def test_password_reset_form_page_still_renders_for_a_normal_account(
        self, session: AsyncSession
    ) -> None:
        """The control: the predicate missing keeps the form."""
        issuer = _service(auto_activate=True, return_token=True)
        guard = _service(refuses=_locked, auto_activate=True, backend_links=True)
        await issuer.signup(session, email=OPEN_EMAIL, password=PASSWORD)
        token = await issuer.request_password_reset(session, email=OPEN_EMAIL)
        assert token is not None
        await session.commit()
        async with _client(guard, session) as client:
            page = await client.get(f"/auth/password-reset/{token.token}")
        assert page.status_code == 200, page.text
        assert 'name="new_password"' in page.text


class TestOldOverridesKeepWorking:
    async def test_a_subclass_with_the_old_init_still_mounts(
        self, session: AsyncSession
    ) -> None:
        """An ``__init__`` override that never heard of the kwarg is unaffected."""

        class _Legacy(UserAuthService):
            def __init__(self, **kwargs: Any) -> None:
                """Forward the pre-#566 keywords only.

                Args:
                    **kwargs (Any): Forwarded to ``UserAuthService``.
                """
                super().__init__(**kwargs)

        legacy = _Legacy(
            user_model=_RefuseUser,
            token_model=_RefuseUserToken,
            auth_settings=AuthSettings(
                _env_file=None,
                AUTH_AUTO_ACTIVATE=True,
                AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            ),
            jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
            refresh_token_model=_RefuseRefreshToken,
        )
        await legacy.signup(session, email=REFUSED_EMAIL, password=PASSWORD)
        await session.commit()
        async with _client(legacy, session) as client:
            requested = await client.post(
                "/auth/password-reset/request", json={"email": REFUSED_EMAIL}
            )
            confirmed = await client.post(
                "/auth/password-reset/confirm",
                json={
                    "token": _token_from_url(requested.json()["reset_url"]),
                    "new_password": NEW_PASSWORD,
                },
            )
        assert confirmed.status_code == 200, confirmed.text


class TestNormalAccountIsUntouched:
    async def test_activate_and_reset_still_work_when_predicate_misses(
        self, session: AsyncSession
    ) -> None:
        """An account the predicate does not match flows through normally."""
        service = _service(refuses=_locked, auto_activate=False, backend_links=True)
        async with _client(service, session) as client:
            created = await client.post(
                "/auth/signup", json={"email": OPEN_EMAIL, "password": PASSWORD}
            )
            assert created.status_code == 201, created.text
            activation_token = _token_from_url(created.json()["activation_url"])
            activated = await client.post(f"/auth/activate/{activation_token}")
            assert activated.status_code == 200, activated.text
            requested = await client.post(
                "/auth/password-reset/request", json={"email": OPEN_EMAIL}
            )
            reset_token = _token_from_url(requested.json()["reset_url"])
            confirmed = await client.post(
                "/auth/password-reset/confirm",
                json={"token": reset_token, "new_password": NEW_PASSWORD},
            )
        assert confirmed.status_code == 200, confirmed.text


class TestSignupWithholdsActivation:
    """Signup of an account the predicate refuses (issue #573).

    Before the fix, ``AUTH_AUTO_ACTIVATE=false`` minted and mailed the
    activation link at signup, and that link only failed on use (``403``).
    The signup now creates the account inactive, persists no token, sends
    no mail and answers the same ``201`` a normal pending signup gets.
    """

    async def test_service_returns_the_withheld_marker(
        self, session: AsyncSession
    ) -> None:
        """No token row, no mail, and a value that is not ``None``."""
        mail = FakeEmailUtils()
        service = _service(
            refuses=_locked, auto_activate=False, return_token=False, email=mail
        )
        user, activation = await service.signup(
            session, email=REFUSED_EMAIL, password=PASSWORD
        )
        assert isinstance(activation, ActivationWithheld)
        assert activation.user_id == user.id
        assert user.is_active is False
        assert await _count_tokens(session) == 0
        assert mail.outbox == []

    async def test_service_still_issues_for_a_normal_account(
        self, session: AsyncSession
    ) -> None:
        """The control: the predicate missing keeps the token and the mail."""
        mail = FakeEmailUtils()
        service = _service(
            refuses=_locked, auto_activate=False, return_token=False, email=mail
        )
        _, activation = await service.signup(
            session, email=OPEN_EMAIL, password=PASSWORD
        )
        assert isinstance(activation, ActivationToken)
        assert await _count_tokens(session) == 1
        assert [message.to[0] for message in mail.outbox] == [OPEN_EMAIL]

    async def test_http_response_is_indistinguishable(
        self, session: AsyncSession
    ) -> None:
        """Same status and body bytes, modulo the user id; no JWT pair."""
        mail = FakeEmailUtils()
        service = _service(
            refuses=_locked, auto_activate=False, return_token=False, email=mail
        )
        async with _client(service, session) as client:
            refused = await client.post(
                "/auth/signup", json={"email": REFUSED_EMAIL, "password": PASSWORD}
            )
            normal = await client.post(
                "/auth/signup", json={"email": OPEN_EMAIL, "password": PASSWORD}
            )
        assert refused.status_code == normal.status_code == 201, refused.text
        refused_id = refused.json()["user_id"].encode()
        normal_id = normal.json()["user_id"].encode()
        assert refused.content.replace(refused_id, b"") == normal.content.replace(
            normal_id, b""
        )
        assert refused.json()["access_token"] is None
        assert refused.json()["refresh_token"] is None
        assert [message.to[0] for message in mail.outbox] == [OPEN_EMAIL]
        assert await _count_tokens(session) == 1
        refused_user = (
            await session.execute(
                select(_RefuseUser).where(_RefuseUser.email == REFUSED_EMAIL)
            )
        ).scalar_one()
        assert refused_user.is_active is False

    async def test_surfacing_mode_returns_no_link(self, session: AsyncSession) -> None:
        """With links surfaced in the body, the refused account gets none."""
        service = _service(refuses=_locked, auto_activate=False, return_token=True)
        async with _client(service, session) as client:
            refused = await client.post(
                "/auth/signup", json={"email": REFUSED_EMAIL, "password": PASSWORD}
            )
        assert refused.status_code == 201, refused.text
        assert refused.json()["activation_required"] is True
        assert refused.json()["activation_url"] is None
        assert refused.json()["access_token"] is None
        assert await _count_tokens(session) == 0

    async def test_auto_activate_ignores_the_predicate(
        self, session: AsyncSession
    ) -> None:
        """An account born active depends on no link: signup is unchanged."""
        seen: list[BaseUserModel] = []

        def _record(user: BaseUserModel) -> bool:
            """Record the call and refuse every account.

            Args:
                user (BaseUserModel): The account being classified.

            Returns:
                bool: Always ``True``.
            """
            seen.append(user)
            return True

        service = _service(refuses=_record, auto_activate=True)
        async with _client(service, session) as client:
            created = await client.post(
                "/auth/signup", json={"email": REFUSED_EMAIL, "password": PASSWORD}
            )
        assert created.status_code == 201, created.text
        assert created.json()["activation_required"] is False
        assert created.json()["access_token"]
        assert seen == []

    async def test_an_old_signup_override_keeps_working(
        self, session: AsyncSession
    ) -> None:
        """An override without ``fields`` that forwards to ``super`` is fine."""

        class _Legacy(UserAuthService):
            async def signup(  # type: ignore[override]
                self,
                session: AsyncSession,
                *,
                email: str,
                password: str,
                name: str | None = None,
            ) -> tuple[BaseUserModel, Any]:
                """Forward the pre-0.305.0 keywords only.

                Args:
                    session (AsyncSession): Active session.
                    email (str): Account email.
                    password (str): Plaintext password.
                    name (str | None): Optional display name.

                Returns:
                    tuple[BaseUserModel, Any]: Whatever ``super`` returned.
                """
                return await super().signup(
                    session, email=email, password=password, name=name
                )

        mail = FakeEmailUtils()
        legacy = _Legacy(
            user_model=_RefuseUser,
            token_model=_RefuseUserToken,
            auth_settings=AuthSettings(
                _env_file=None,
                AUTH_AUTO_ACTIVATE=False,
                AUTH_RETURN_TOKEN_IN_RESPONSE=False,
            ),
            jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
            email=mail,
            refresh_token_model=_RefuseRefreshToken,
            refuses_email_link=_locked,
        )
        async with _client(legacy, session) as client:
            created = await client.post(
                "/auth/signup", json={"email": REFUSED_EMAIL, "password": PASSWORD}
            )
        assert created.status_code == 201, created.text
        assert created.json()["activation_required"] is True
        assert created.json()["access_token"] is None
        assert mail.outbox == []
        assert await _count_tokens(session) == 0

"""The SDK's own capability URLs never reach the access log in clear.

With ``AUTH_BACKEND_LINKS=True`` the reset e-mail points at
``GET /auth/password-reset/{token}``, which only peeks the token: it stays
redeemable after the visit. ``AccessLogMiddleware`` used to log the raw
path by default, and the default :class:`RedactionPolicy` matched none of
its shapes, so anyone who could read the logs could redeem the token. The
same held for the ``signature`` of a signed URL.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tempest_fastapi_sdk import (
    AccessLogMiddleware,
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.core.redaction import (
    REDACTED,
    RedactionFilter,
    RedactionPolicy,
    redact_capability_url,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.utils.signed_url import sign_path

LOGGER_NAME: str = "tempest.access.capability"


class _CapabilityUser(BaseUserModel):
    __tablename__ = "access_log_capability_users"


_CapabilityToken = make_user_token_model(
    user_table="access_log_capability_users",
    tablename="access_log_capability_tokens",
    class_name="_CapabilityToken",
)


async def _reset_app(**options: object) -> AsyncIterator[tuple[FastAPI, str]]:
    """Yield an auth app behind the access log plus a live reset token.

    Args:
        **options (object): Forwarded to :class:`AccessLogMiddleware`.

    Yields:
        tuple[FastAPI, str]: The app and the reset token it accepts.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = UserAuthService(
        user_model=_CapabilityUser,
        token_model=_CapabilityToken,
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=True,
            AUTH_BACKEND_LINKS=True,
            AUTH_DEFAULT_LOCALE="en-US",
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=None,
    )
    try:
        async with factory() as session:
            await service.signup(
                session, email="victim@example.com", password="victim-pass-1234"
            )
            await session.commit()
            issued = await service.request_password_reset(
                session, email="victim@example.com"
            )
            await session.commit()

            async def session_factory() -> AsyncIterator[AsyncSession]:
                yield session

            app = FastAPI()
            app.include_router(
                make_auth_router(service, session_factory=session_factory)
            )

            @app.get("/files/{key:path}")
            async def files(key: str) -> dict[str, str]:
                return {"key": key}

            app.add_middleware(AccessLogMiddleware, logger_name=LOGGER_NAME, **options)
            assert issued.token is not None
            yield app, issued.token
    finally:
        await engine.dispose()


@pytest.fixture
async def reset_app() -> AsyncIterator[tuple[FastAPI, str]]:
    """Yield the app with the default middleware options.

    Yields:
        tuple[FastAPI, str]: The app and the live reset token.
    """
    async for pair in _reset_app():
        yield pair


def _access_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return the access-log records captured so far.

    Args:
        caplog (pytest.LogCaptureFixture): The capture fixture.

    Returns:
        list[logging.LogRecord]: The middleware's records.
    """
    return [r for r in caplog.records if r.name == LOGGER_NAME]


class TestDefaultAccessLog:
    async def test_reset_token_path_is_redacted(
        self,
        reset_app: tuple[FastAPI, str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        app, token = reset_app
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as client:
                response = await client.get(f"/auth/password-reset/{token}")
        assert response.status_code == 200

        records = _access_records(caplog)
        assert len(records) == 1
        record = records[0]
        assert record.http_path == f"/auth/password-reset/{REDACTED}"
        assert token not in record.getMessage()
        assert token not in str(record.__dict__)

    async def test_signed_url_signature_is_redacted(
        self,
        reset_app: tuple[FastAPI, str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        app, _ = reset_app
        signed = sign_path(
            "/files/private/report.pdf",
            secret="s" * 32,
            purpose="files",
            expires_in=timedelta(minutes=10),
        )
        signature = re.search(r"signature=([^&]+)", signed)
        assert signature is not None
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as client:
                await client.get(signed)

        record = _access_records(caplog)[0]
        assert signature.group(1) not in record.http_query
        assert f"signature={REDACTED}" in record.http_query

    async def test_opt_out_logs_the_raw_path(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        async for app, token in _reset_app(redact_capability_urls=False):
            with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://t"
                ) as client:
                    await client.get(f"/auth/password-reset/{token}")
            assert _access_records(caplog)[0].http_path.endswith(token)

    async def test_custom_redactor_runs_after_the_default(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        async for app, token in _reset_app(redact=str.upper):
            with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://t"
                ) as client:
                    await client.get(f"/auth/password-reset/{token}")
            assert _access_records(caplog)[0].http_path == (
                f"/AUTH/PASSWORD-RESET/{REDACTED}"
            )


class TestRedactionPolicyDefaults:
    def test_capability_path_in_message_is_redacted(self) -> None:
        text = RedactionPolicy().redact_text("GET /auth/password-reset/abc-DEF_123 200")

        assert text == f"GET /auth/password-reset/{REDACTED} 200"

    def test_signature_extra_field_is_redacted(self) -> None:
        record = logging.LogRecord("x", logging.INFO, "", 0, "m", None, None)
        record.http_query = "expires=1&signature=deadbeef"
        record.signature = "deadbeef"

        RedactionFilter().filter(record)

        assert record.http_query == f"expires=1&signature={REDACTED}"
        assert record.signature == REDACTED


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/auth/activate/tok", f"/auth/activate/{REDACTED}"),
        ("/auth/email-change/tok", f"/auth/email-change/{REDACTED}"),
        ("/auth/email-verify/tok", f"/auth/email-verify/{REDACTED}"),
        ("/auth/password-reset/request", "/auth/password-reset/request"),
        ("/auth/email-change/confirm", "/auth/email-change/confirm"),
        ("/auth/activation/request", "/auth/activation/request"),
        ("token=abc&page=2", f"token={REDACTED}&page=2"),
        ("page=2&access_token=abc", f"page=2&access_token={REDACTED}"),
        ("page=2", "page=2"),
    ],
)
def test_redact_capability_url(value: str, expected: str) -> None:
    assert redact_capability_url(value) == expected

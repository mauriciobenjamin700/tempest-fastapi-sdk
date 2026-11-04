"""``POST /auth/mfa/enroll`` returns the provisioning URI as a QR code (#703).

The field is additive and on by default: with ``segno`` installed the
response carries an SVG ``data:`` URI that decodes to the exact
``provisioning_uri``; without it the field is ``None`` and the rest of the
response is unchanged. Nothing secret reaches the logs on the way.
"""

from __future__ import annotations

import base64
import io
import logging
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    MFAEnrollResponseSchema,
    MFAMixin,
    UserAuthService,
    make_auth_router,
    make_user_recovery_code_model,
    make_user_token_model,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.utils.totp import provisioning_qr_data_uri


class _QrUser(MFAMixin, BaseUserModel):
    __tablename__ = "mfa_qr_test_users"


_QrUserToken = make_user_token_model(
    user_table="mfa_qr_test_users",
    tablename="mfa_qr_test_user_tokens",
    class_name="_QrUserToken",
)

_QrRecoveryCode = make_user_recovery_code_model(
    user_table="mfa_qr_test_users",
    tablename="mfa_qr_test_recovery_codes",
    class_name="_QrRecoveryCode",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _enroll(session: AsyncSession, email: str) -> dict[str, Any]:
    """Sign a user up and call ``POST /auth/mfa/enroll`` as them.

    Args:
        session (AsyncSession): The test session.
        email (str): The account e-mail.

    Returns:
        dict[str, Any]: The JSON body of a ``200`` enroll response.
    """
    service = UserAuthService(
        user_model=_QrUser,
        token_model=_QrUserToken,
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=True,
            AUTH_MFA_ENABLED=True,
            AUTH_MFA_ISSUER="Tempest QR",
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=None,
    )
    user, _ = await service.signup(
        session, email=email, password="strong-pass-12-chars"
    )
    await session.commit()
    access, _ = service.issue_jwt_pair(user)

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    app.include_router(
        make_auth_router(
            service,
            session_factory=_factory,
            recovery_code_model=_QrRecoveryCode,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        response = await client.post(
            "/auth/mfa/enroll",
            headers={"Authorization": f"Bearer {access}"},
        )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestEnrollQr:
    async def test_enroll_returns_svg_qr_of_the_provisioning_uri(
        self, session: AsyncSession
    ) -> None:
        body = await _enroll(session, "qr@a.com")

        qr = body["provisioning_qr"]
        prefix = "data:image/svg+xml;base64,"
        assert qr.startswith(prefix)
        assert b"<svg" in base64.b64decode(qr.removeprefix(prefix))
        assert qr == provisioning_qr_data_uri(body["provisioning_uri"])

    async def test_enroll_qr_decodes_to_the_uri(self, session: AsyncSession) -> None:
        zxingcpp = pytest.importorskip("zxingcpp")
        image_module = pytest.importorskip("PIL.Image")
        body = await _enroll(session, "qr-decode@a.com")

        png = provisioning_qr_data_uri(body["provisioning_uri"], kind="png")
        assert body["provisioning_qr"].startswith("data:image/svg+xml;base64,")
        image = image_module.open(io.BytesIO(base64.b64decode(png.split(",", 1)[1])))
        assert [b.text for b in zxingcpp.read_barcodes(image)] == [
            body["provisioning_uri"]
        ]

    async def test_enroll_without_segno_returns_none(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(sys.modules, "segno", None)

        body = await _enroll(session, "qr-none@a.com")

        assert body["provisioning_qr"] is None
        assert body["provisioning_uri"].startswith("otpauth://totp/")
        assert body["secret"]

    async def test_enroll_logs_neither_the_qr_nor_the_uri(
        self,
        session: AsyncSession,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No log record at any level carries the QR code or the URI.

        The secret is checked only outside the ``aiosqlite`` logger: that
        driver logs every statement's bound parameters at ``DEBUG``,
        including the ``totp_secret`` column the enroll writes — a
        property of the driver's debug logging, not of this endpoint.
        """
        with caplog.at_level(logging.DEBUG):
            body = await _enroll(session, "qr-logs@a.com")

        def _text(records: list[logging.LogRecord]) -> str:
            return "\n".join(
                f"{record.getMessage()} {record.args!r}" for record in records
            )

        everything = _text(caplog.records)
        outside_driver = _text(
            [r for r in caplog.records if not r.name.startswith("aiosqlite")]
        )
        assert body["provisioning_qr"]
        assert body["provisioning_qr"] not in everything
        assert body["provisioning_qr"].split(",", 1)[1][:40] not in everything
        assert body["provisioning_uri"] not in everything
        assert body["secret"] not in outside_driver


class TestSchema:
    def test_provisioning_qr_is_optional(self) -> None:
        schema = MFAEnrollResponseSchema(
            secret="JBSWY3DPEHPK3PXP",
            provisioning_uri="otpauth://totp/x",
        )

        assert schema.provisioning_qr is None
        assert (
            "provisioning_qr"
            not in MFAEnrollResponseSchema.model_json_schema()["required"]
        )

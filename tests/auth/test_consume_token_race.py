"""Single-use tokens are spent by one conditional ``UPDATE``, not read-then-write.

Two redemptions of the same link used to both pass: ``_consume_token``
read the row, saw ``used_at IS NULL``, and wrote ``used_at`` afterwards,
so a second request reading in between saw the same empty column. The
redemption is now one ``UPDATE ... WHERE used_at IS NULL RETURNING``, and
the database picks the winner.

The engine is a SQLite **file** (two connections, two transactions) and,
under ``make test-docker``, a real PostgreSQL — the same split the wallet
guards use (``tests/wallet/support.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    UserAuthService,
    enable_sqlite_savepoints,
    make_user_token_model,
)
from tempest_fastapi_sdk.db.user_token_model import UserTokenPurpose
from tempest_fastapi_sdk.exceptions import InvalidTokenException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tests.wallet.support import postgres_url as postgres_url

PASSWORD: str = "strong-pass-12-chars"
REDEMPTIONS: int = 8


class _RaceUser(BaseUserModel):
    """User model for the redemption race."""

    __tablename__ = "race_auth_users"


_RaceUserToken = make_user_token_model(
    user_table="race_auth_users",
    tablename="race_auth_user_tokens",
    class_name="_RaceUserToken",
)

TABLES = [_RaceUser.__table__, _RaceUserToken.__table__]


@pytest_asyncio.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.docker)],
)
async def engine(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[AsyncEngine]:
    """Yield an engine with the race tables, on each dialect.

    Args:
        request (pytest.FixtureRequest): Carries the dialect parameter.
        tmp_path (Path): Directory for the SQLite file.

    Yields:
        AsyncEngine: The engine; tables are dropped afterwards.
    """
    if request.param == "sqlite":
        built = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
        enable_sqlite_savepoints(built)
    else:
        built = create_async_engine(request.getfixturevalue("postgres_url"))
    async with built.begin() as connection:
        await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
        await connection.run_sync(BaseModel.metadata.create_all, tables=TABLES)
    yield built
    async with built.begin() as connection:
        await connection.run_sync(BaseModel.metadata.drop_all, tables=TABLES)
    await built.dispose()


@pytest.fixture
def maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory on the parametrized engine.

    Args:
        engine (AsyncEngine): The engine.

    Returns:
        async_sessionmaker[AsyncSession]: The session factory.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


def _service() -> UserAuthService:
    """Build an auth service that hands the plaintext token back.

    Returns:
        UserAuthService: The service under test.
    """
    return UserAuthService(
        user_model=_RaceUser,
        token_model=_RaceUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=True,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
    )


async def _reset_token(
    service: UserAuthService,
    maker: async_sessionmaker[AsyncSession],
) -> str:
    """Sign a user up, request a password reset, and return the token.

    Args:
        service (UserAuthService): The service.
        maker (async_sessionmaker[AsyncSession]): Session factory.

    Returns:
        str: The plaintext reset token.
    """
    async with maker() as session:
        await service.signup(session, email="race@example.com", password=PASSWORD)
        issued = await service.request_password_reset(session, email="race@example.com")
        await session.commit()
    assert issued is not None
    return issued.token


class TestConcurrentRedemption:
    """Many sessions redeem one reset link at once."""

    async def test_exactly_one_redemption_wins(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """One ``confirm_password_reset`` succeeds; every other one is refused."""
        service = _service()
        token = await _reset_token(service, maker)

        async def redeem(index: int) -> str:
            async with maker() as session:
                await service.confirm_password_reset(
                    session,
                    token=token,
                    new_password=f"replacement-pass-{index:02d}",
                )
                await session.commit()
            return f"replacement-pass-{index:02d}"

        outcomes = await asyncio.gather(
            *(redeem(index) for index in range(REDEMPTIONS)),
            return_exceptions=True,
        )

        winners = [outcome for outcome in outcomes if isinstance(outcome, str)]
        losers = [outcome for outcome in outcomes if not isinstance(outcome, str)]
        assert len(winners) == 1
        assert all(isinstance(loser, InvalidTokenException) for loser in losers)
        async with maker() as session:
            await service.login(session, email="race@example.com", password=winners[0])

    async def test_session_that_peeked_first_is_refused(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A session that peeked before another redeemed is refused.

        Session B validates the link with ``peek_token`` (the ``GET``
        that renders the reset form), session A redeems and commits,
        then B submits. B commits after the peek so SQLite does not hold
        its shared lock across A's write.
        """
        service = _service()
        token = await _reset_token(service, maker)

        async with maker() as late:
            await service.peek_token(
                late, token=token, purpose=UserTokenPurpose.PASSWORD_RESET
            )
            await late.commit()
            async with maker() as early:
                await service.confirm_password_reset(
                    early, token=token, new_password="first-winner-pass"
                )
                await early.commit()
            with pytest.raises(InvalidTokenException, match="already used"):
                await service.confirm_password_reset(
                    late, token=token, new_password="second-attempt-pass"
                )


class TestRefusalReasons:
    """The failed claim still names why the token was refused."""

    async def test_unknown_token(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """A token that matches no row is ``not recognized``."""
        service = _service()
        await _reset_token(service, maker)
        async with maker() as session:
            with pytest.raises(InvalidTokenException, match="not recognized"):
                await service.confirm_password_reset(
                    session, token="no-such-token", new_password="whatever-pass-1"
                )

    async def test_expired_token_is_not_spent(
        self,
        maker: async_sessionmaker[AsyncSession],
    ) -> None:
        """An expired token is refused as ``expired`` and keeps ``used_at`` empty."""
        service = _service()
        token = await _reset_token(service, maker)
        async with maker() as session:
            record = (await session.execute(select(_RaceUserToken))).scalar_one()
            record.expires_at = record.created_at.replace(year=2000)
            await session.commit()
        async with maker() as session:
            with pytest.raises(InvalidTokenException, match="expired"):
                await service.confirm_password_reset(
                    session, token=token, new_password="whatever-pass-1"
                )
            await session.commit()
        async with maker() as session:
            record = (await session.execute(select(_RaceUserToken))).scalar_one()
            assert record.used_at is None

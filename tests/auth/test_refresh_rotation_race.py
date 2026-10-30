"""A DB-backed refresh token rotates once, even under concurrent redemption.

``refresh_tokens`` used to read the row, check ``used_at IS NULL`` in
Python and write ``used_at`` afterwards. Two redemptions that both read
before either wrote each minted a child in the family, and reuse
detection never ran: a stolen token replayed alongside the victim's own
refresh kept a live session. The rotation is now one conditional
``UPDATE ... RETURNING``; the loser is refused and the family revoked.

The engine is a SQLite **file** (two connections, two transactions). The
interleave is forced: each redemption waits, after it resolved its token,
until the other one got as far or a second passed — on the old shape both
arrive and both write; on the new one the second is held by the database
until the first commits.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
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
    make_user_refresh_token_model,
    make_user_token_model,
)
from tempest_fastapi_sdk.exceptions import InvalidTokenException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
ARRIVAL_TIMEOUT_SECONDS: float = 1.0
"""How long a redemption waits for the other one at the interleave point."""


class _RotUser(BaseUserModel):
    __tablename__ = "rotation_race_users"


_RotUserToken = make_user_token_model(
    user_table="rotation_race_users",
    tablename="rotation_race_user_tokens",
    class_name="_RotUserToken",
)
_RotRefreshToken = make_user_refresh_token_model(
    user_table="rotation_race_users",
    tablename="rotation_race_refresh_tokens",
    class_name="_RotRefreshToken",
)

TABLES = [
    _RotUser.__table__,
    _RotUserToken.__table__,
    _RotRefreshToken.__table__,
]


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Yield an engine over a SQLite file with the test tables.

    Args:
        tmp_path (Path): Directory for the SQLite file.

    Yields:
        AsyncEngine: The engine, disposed afterwards.
    """
    built = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rotation.db'}")
    async with built.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all, tables=TABLES)
    yield built
    await built.dispose()


class _InterleavedService(UserAuthService):
    """Hold each redemption at ``get_user`` until the other arrives.

    ``get_user`` runs in both shapes of ``refresh_tokens`` after the token
    was resolved and before the new pair is minted, so it is where the two
    redemptions are lined up.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Build the service and the arrival barrier.

        Args:
            **kwargs (Any): Forwarded to :class:`UserAuthService`.
        """
        super().__init__(**kwargs)
        self.arrived: int = 0
        self.gate: asyncio.Event = asyncio.Event()

    async def get_user(
        self, subject: str | UUID, session: AsyncSession
    ) -> BaseUserModel:
        """Wait for the other redemption, then resolve the user.

        Args:
            subject (str | UUID): The user id.
            session (AsyncSession): The redemption's session.

        Returns:
            BaseUserModel: The user.
        """
        self.arrived += 1
        if self.arrived >= 2:
            self.gate.set()
        try:
            await asyncio.wait_for(self.gate.wait(), ARRIVAL_TIMEOUT_SECONDS)
        except TimeoutError:
            self.gate.set()
        return await super().get_user(subject, session)


def _service(service_class: type[UserAuthService] = UserAuthService) -> Any:
    """Build a service with DB-backed refresh tokens.

    Args:
        service_class (type[UserAuthService]): The class to instantiate.

    Returns:
        Any: The configured service.
    """
    return service_class(
        user_model=_RotUser,
        token_model=_RotUserToken,
        auth_settings=AuthSettings(_env_file=None, AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        refresh_token_model=_RotRefreshToken,
    )


async def _issue(
    service: UserAuthService, maker: async_sessionmaker[AsyncSession]
) -> str:
    """Sign a user up and return a fresh refresh token.

    Args:
        service (UserAuthService): The service.
        maker (async_sessionmaker[AsyncSession]): Session factory.

    Returns:
        str: The plaintext refresh token.
    """
    async with maker() as session:
        user, _ = await service.signup(session, email="rot@acme.com", password=PASSWORD)
        _, refresh = await service.issue_token_pair(session, user)
        await session.commit()
    return refresh


async def _redeem(
    service: UserAuthService,
    maker: async_sessionmaker[AsyncSession],
    token: str,
) -> str:
    """Exchange ``token`` in a session of its own and commit.

    Args:
        service (UserAuthService): The service.
        maker (async_sessionmaker[AsyncSession]): Session factory.
        token (str): The refresh token.

    Returns:
        str: The new refresh token.
    """
    async with maker() as session:
        _, _, child = await service.refresh_tokens(session, refresh_token=token)
        await session.commit()
    return child


class TestConcurrentRotation:
    async def test_one_child_and_the_family_revoked(self, engine: AsyncEngine) -> None:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        service = _service(_InterleavedService)
        token = await _issue(service, maker)

        outcomes = await asyncio.gather(
            _redeem(service, maker, token),
            _redeem(service, maker, token),
            return_exceptions=True,
        )

        children = [outcome for outcome in outcomes if isinstance(outcome, str)]
        refused = [outcome for outcome in outcomes if not isinstance(outcome, str)]
        assert len(children) == 1
        assert len(refused) == 1
        assert isinstance(refused[0], InvalidTokenException)
        plain = _service()
        with pytest.raises(InvalidTokenException):
            await _redeem(plain, maker, children[0])

    async def test_sequential_replay_still_revokes_the_family(
        self, engine: AsyncEngine
    ) -> None:
        """Control: the detection that already held without concurrency."""
        maker = async_sessionmaker(engine, expire_on_commit=False)
        service = _service()
        token = await _issue(service, maker)
        child = await _redeem(service, maker, token)
        with pytest.raises(InvalidTokenException, match="reuse detected"):
            await _redeem(service, maker, token)
        with pytest.raises(InvalidTokenException, match="revoked"):
            await _redeem(service, maker, child)

    async def test_rotation_chain_still_works(self, engine: AsyncEngine) -> None:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        service = _service()
        token = await _issue(service, maker)
        for _ in range(3):
            token = await _redeem(service, maker, token)
        assert token

    async def test_unknown_token_is_not_recognized(self, engine: AsyncEngine) -> None:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        service = _service()
        await _issue(service, maker)
        with pytest.raises(InvalidTokenException, match="not recognized"):
            await _redeem(service, maker, "no-such-token")

"""Tests for tempest_fastapi_sdk.testing.database helpers."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk import testing as sdk_testing
from tempest_fastapi_sdk.testing import (
    create_test_engine,
    drop_test_metadata,
    init_test_metadata,
    make_test_database,
    make_test_session,
)


class Tinker(BaseModel):
    __tablename__ = "tinker_for_testing_helpers"
    label: Mapped[str] = mapped_column(String(32), nullable=False)


async def test_create_test_engine_yields_in_memory_sqlite() -> None:
    engine = create_test_engine()
    try:
        assert "sqlite" in str(engine.url)
    finally:
        await engine.dispose()


async def test_init_and_drop_metadata() -> None:
    engine = create_test_engine()
    try:
        await init_test_metadata(engine)
        await drop_test_metadata(engine)
    finally:
        await engine.dispose()


async def test_make_test_session_yields_working_session() -> None:
    async with make_test_session() as session:
        session.add(Tinker(label="hello"))
        await session.commit()
        loaded = (await session.execute(select(Tinker))).scalars().all()
        assert [t.label for t in loaded] == ["hello"]


async def test_make_test_database_yields_session_factory() -> None:
    async with make_test_database() as factory:
        assert isinstance(factory, async_sessionmaker)
        async with factory() as session:
            session.add(Tinker(label="one"))
            await session.commit()
        async with factory() as session:
            session.add(Tinker(label="two"))
            await session.commit()
            loaded = (await session.execute(select(Tinker))).scalars().all()
            assert {t.label for t in loaded} == {"one", "two"}


class TestDeprecatedAliases:
    """The old ``test_*`` names keep working, warn, and are not collected."""

    async def test_session_alias_warns_and_works(self) -> None:
        """The old name still yields a live session, with a DeprecationWarning."""
        with pytest.warns(DeprecationWarning, match="make_test_session"):
            manager = sdk_testing.test_session()
        async with manager as session:
            session.add(Tinker(label="legacy"))
            await session.commit()
            loaded = (await session.execute(select(Tinker))).scalars().all()
            assert [t.label for t in loaded] == ["legacy"]

    async def test_database_alias_warns_and_works(self) -> None:
        """The old name still yields a session factory, with a DeprecationWarning."""
        with pytest.warns(DeprecationWarning, match="make_test_database"):
            manager = sdk_testing.test_database()
        async with manager as factory:
            assert isinstance(factory, async_sessionmaker)

    def test_aliases_opt_out_of_collection(self) -> None:
        """Both aliases carry ``__test__ = False``, the attribute pytest honours."""
        assert getattr(sdk_testing.test_session, "__test__", True) is False
        assert getattr(sdk_testing.test_database, "__test__", True) is False


def test_consumer_suite_collects_no_phantom_test(tmp_path: Path) -> None:
    """A consumer test module importing every helper collects only its own test (#450).

    Runs a real pytest in a subprocess over a fresh project, with warnings
    as errors, so a phantom item or a ``PytestReturnNotNoneWarning`` fails.
    """
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_consumer.py").write_text(
        textwrap.dedent(
            """
            from tempest_fastapi_sdk.testing import (
                make_test_database,
                make_test_session,
                test_database,
                test_session,
            )


            def test_ok() -> None:
                assert make_test_database and make_test_session
                assert test_database and test_session
            """,
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-W",
            "error",
            "-p",
            "no:cacheprovider",
            "-v",
            str(tmp_path / "test_consumer.py"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "test_consumer.py::test_ok PASSED" in output
    assert "1 passed" in output
    assert "::test_session" not in output
    assert "::test_database" not in output
    assert "PytestReturnNotNoneWarning" not in output

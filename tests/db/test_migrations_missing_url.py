"""Tests for the error the AlembicHelper raises without a database URL (#832).

The ini the SDK generates carries ``sqlalchemy.url =`` empty on purpose, and
``Config.get_main_option`` returns ``""`` for it, not ``None``. The read path
checked ``is None``, so an unresolvable URL reached ``create_engine("")`` and
surfaced as SQLAlchemy's ``ArgumentError`` instead of the documented
``RuntimeError``. These tests pin that every entry point raises the same
actionable message the ``env.py`` of the write path raises.
"""

from pathlib import Path

import pytest

from tempest_fastapi_sdk.db import AlembicHelper
from tempest_fastapi_sdk.db.migrations import MISSING_DATABASE_URL_MESSAGE


@pytest.fixture
def helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AlembicHelper:
    """Scaffold an SDK Alembic project with no resolvable database URL.

    The ini comes from :meth:`AlembicHelper.init`, one empty revision exists
    (without it ``sync_schema`` returns ``no_migrations`` before reading the
    URL), ``DATABASE_URL`` is unset and the working directory holds no
    ``src/core/settings.py``.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    instance = AlembicHelper(config_path=str(tmp_path / "alembic.ini"))
    instance.init(directory=str(tmp_path / "alembic"))
    instance.revision("first", autogenerate=False)
    assert not (tmp_path / "src" / "core" / "settings.py").exists()
    assert instance.config.get_main_option("sqlalchemy.url") == ""
    return instance


class TestMissingDatabaseUrl:
    def test_current_raises_runtime_error(self, helper: AlembicHelper) -> None:
        """Cenário: ``current()`` sem URL resolvível.

        Esperado: ``RuntimeError`` com a mensagem acionável, não o
        ``ArgumentError`` do parser de URL do SQLAlchemy.
        """
        with pytest.raises(RuntimeError) as excinfo:
            helper.current()
        assert str(excinfo.value) == MISSING_DATABASE_URL_MESSAGE

    def test_sync_schema_raises_runtime_error(self, helper: AlembicHelper) -> None:
        """Cenário: ``sync_schema()`` sem URL resolvível.

        Esperado: ``RuntimeError`` com a mensagem acionável.
        """
        with pytest.raises(RuntimeError) as excinfo:
            helper.sync_schema()
        assert str(excinfo.value) == MISSING_DATABASE_URL_MESSAGE

    async def test_sync_schema_async_raises_runtime_error(
        self, helper: AlembicHelper
    ) -> None:
        """Cenário: ``await sync_schema_async()`` no lifespan sem URL resolvível.

        Esperado: ``RuntimeError`` com a mensagem acionável.
        """
        with pytest.raises(RuntimeError) as excinfo:
            await helper.sync_schema_async()
        assert str(excinfo.value) == MISSING_DATABASE_URL_MESSAGE

    def test_has_existing_schema_raises_runtime_error(
        self, helper: AlembicHelper
    ) -> None:
        """Cenário: ``has_existing_schema()`` sem URL resolvível.

        Esperado: ``RuntimeError`` com a mensagem acionável.
        """
        with pytest.raises(RuntimeError) as excinfo:
            helper.has_existing_schema()
        assert str(excinfo.value) == MISSING_DATABASE_URL_MESSAGE

    async def test_current_async_raises_runtime_error(
        self, helper: AlembicHelper
    ) -> None:
        """Cenário: ``await current_async()`` sem ``bind`` e sem URL resolvível.

        Esperado: ``RuntimeError`` com a mensagem acionável.
        """
        with pytest.raises(RuntimeError) as excinfo:
            await helper.current_async()
        assert str(excinfo.value) == MISSING_DATABASE_URL_MESSAGE

    def test_adopt_raises_runtime_error(self, helper: AlembicHelper) -> None:
        """Cenário: ``adopt()`` sem URL resolvível.

        Esperado: ``RuntimeError`` com a mensagem acionável.
        """
        with pytest.raises(RuntimeError) as excinfo:
            helper.adopt()
        assert str(excinfo.value) == MISSING_DATABASE_URL_MESSAGE

    def test_upgrade_raises_the_same_message_from_env_py(
        self, helper: AlembicHelper
    ) -> None:
        """Cenário: ``upgrade()`` sem URL resolvível, pelo ``env.py`` gerado.

        Esperado: ``RuntimeError`` que começa pela mesma mensagem dos
        métodos de leitura — o ``env.py`` roda dentro do Alembic e importa a
        constante do pacote.
        """
        with pytest.raises(RuntimeError) as excinfo:
            helper.upgrade()
        assert str(excinfo.value).startswith(MISSING_DATABASE_URL_MESSAGE)

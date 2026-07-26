"""Tests for ``BaseRepository``'s per-operation conflict exception classes.

A repository that raises the generic :class:`ConflictException` gives every
duplicate-key failure the same ``code = "CONFLICT"``, so a client cannot tell
"this coin pack name is taken" apart from any other 409 and
``error_responses()`` cannot document it. These assert that a domain subclass
can be plugged in per operation, and that omitting the kwargs keeps the old
behavior exactly.
"""

from __future__ import annotations

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository, ConflictException


class Widget(BaseModel):
    __tablename__ = "widget_for_conflict_exception_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class WidgetConflictError(ConflictException):
    """Domain conflict standing in for any project-owned subclass."""

    code: str = "WIDGET_CONFLICT"


class WidgetCreateConflictError(ConflictException):
    """Conflict specific to the create path."""

    code: str = "WIDGET_CREATE_CONFLICT"


class WidgetUpdateConflictError(ConflictException):
    """Conflict specific to the update path."""

    code: str = "WIDGET_UPDATE_CONFLICT"


class WidgetBulkCreateConflictError(ConflictException):
    """Conflict specific to the bulk-create path."""

    code: str = "WIDGET_BULK_CREATE_CONFLICT"


class WidgetBulkUpdateConflictError(ConflictException):
    """Conflict specific to the bulk-update path."""

    code: str = "WIDGET_BULK_UPDATE_CONFLICT"


class WidgetRepository(BaseRepository[Widget]):
    """Repository forwarding every conflict kwarg through to the base."""

    def __init__(
        self, session: AsyncSession, **kwargs: type[ConflictException]
    ) -> None:
        """Initialize the repository.

        Args:
            session (AsyncSession): The async database session.
            **kwargs (type[ConflictException]): Conflict exception classes
                forwarded verbatim, so each test declares only what it needs.
        """
        super().__init__(session, model=Widget, **kwargs)


async def _duplicate_add(repo: WidgetRepository) -> None:
    """Trigger the create conflict by inserting the same unique name twice.

    Args:
        repo (WidgetRepository): The repository under test.
    """
    await repo.add(Widget(name="taken"))
    await repo.add(Widget(name="taken"))


class TestDefaultIsUnchanged:
    """Omitting every new kwarg keeps the pre-existing behavior."""

    async def test_add_still_raises_the_generic_conflict(
        self, session: AsyncSession
    ) -> None:
        """The default class and its generic code are untouched."""
        repo = WidgetRepository(session)

        with pytest.raises(ConflictException) as excinfo:
            await _duplicate_add(repo)

        assert type(excinfo.value) is ConflictException
        assert excinfo.value.code == "CONFLICT"

    async def test_default_message_still_derives_from_the_model(
        self, session: AsyncSession
    ) -> None:
        """A custom class must not disturb the generated message."""
        repo = WidgetRepository(session)

        with pytest.raises(ConflictException) as excinfo:
            await _duplicate_add(repo)

        assert excinfo.value.detail == "Conflict creating Widget"


class TestBlanketOverride:
    """``conflict_exception`` covers every write at once."""

    async def test_create_uses_it(self, session: AsyncSession) -> None:
        """``add`` raises the blanket class."""
        repo = WidgetRepository(session, conflict_exception=WidgetConflictError)

        with pytest.raises(WidgetConflictError) as excinfo:
            await _duplicate_add(repo)

        assert excinfo.value.code == "WIDGET_CONFLICT"

    async def test_bulk_create_uses_it(self, session: AsyncSession) -> None:
        """``add_all`` raises the blanket class."""
        repo = WidgetRepository(session, conflict_exception=WidgetConflictError)
        await repo.add(Widget(name="taken"))

        with pytest.raises(WidgetConflictError):
            await repo.add_all([Widget(name="taken")])

    async def test_update_uses_it(self, session: AsyncSession) -> None:
        """``update`` raises the blanket class."""
        repo = WidgetRepository(session, conflict_exception=WidgetConflictError)
        await repo.add(Widget(name="first"))
        second = await repo.add(Widget(name="second"))
        second.name = "first"

        with pytest.raises(WidgetConflictError):
            await repo.update(second)

    async def test_bulk_update_uses_it(self, session: AsyncSession) -> None:
        """``update_many`` raises the blanket class."""
        repo = WidgetRepository(session, conflict_exception=WidgetConflictError)
        await repo.add(Widget(name="first"))
        second = await repo.add(Widget(name="second"))
        second.name = "first"

        with pytest.raises(WidgetConflictError):
            await repo.update_many([second])

    async def test_the_message_is_still_per_operation(
        self, session: AsyncSession
    ) -> None:
        """One class for every write does not collapse the messages."""
        repo = WidgetRepository(session, conflict_exception=WidgetConflictError)
        await repo.add(Widget(name="taken"))

        with pytest.raises(WidgetConflictError) as excinfo:
            await repo.add_all([Widget(name="taken")])

        assert excinfo.value.detail == "Conflict creating Widget batch"


class TestPerOperationOverride:
    """A per-operation kwarg wins over the blanket one."""

    async def test_create_override_beats_the_blanket(
        self, session: AsyncSession
    ) -> None:
        """``create_conflict_exception`` takes precedence on ``add``."""
        repo = WidgetRepository(
            session,
            conflict_exception=WidgetConflictError,
            create_conflict_exception=WidgetCreateConflictError,
        )

        with pytest.raises(WidgetCreateConflictError):
            await _duplicate_add(repo)

    async def test_the_blanket_still_covers_the_others(
        self, session: AsyncSession
    ) -> None:
        """Overriding create leaves update on the blanket class."""
        repo = WidgetRepository(
            session,
            conflict_exception=WidgetConflictError,
            create_conflict_exception=WidgetCreateConflictError,
        )
        await repo.add(Widget(name="first"))
        second = await repo.add(Widget(name="second"))
        second.name = "first"

        with pytest.raises(WidgetConflictError) as excinfo:
            await repo.update(second)

        assert type(excinfo.value) is WidgetConflictError

    async def test_update_override(self, session: AsyncSession) -> None:
        """``update_conflict_exception`` reaches ``update``."""
        repo = WidgetRepository(
            session, update_conflict_exception=WidgetUpdateConflictError
        )
        await repo.add(Widget(name="first"))
        second = await repo.add(Widget(name="second"))
        second.name = "first"

        with pytest.raises(WidgetUpdateConflictError):
            await repo.update(second)

    async def test_bulk_create_override(self, session: AsyncSession) -> None:
        """``bulk_create_conflict_exception`` reaches ``add_all``."""
        repo = WidgetRepository(
            session, bulk_create_conflict_exception=WidgetBulkCreateConflictError
        )
        await repo.add(Widget(name="taken"))

        with pytest.raises(WidgetBulkCreateConflictError):
            await repo.add_all([Widget(name="taken")])

    async def test_bulk_update_override(self, session: AsyncSession) -> None:
        """``bulk_update_conflict_exception`` reaches ``update_many``."""
        repo = WidgetRepository(
            session, bulk_update_conflict_exception=WidgetBulkUpdateConflictError
        )
        await repo.add(Widget(name="first"))
        second = await repo.add(Widget(name="second"))
        second.name = "first"

        with pytest.raises(WidgetBulkUpdateConflictError):
            await repo.update_many([second])

    async def test_an_override_does_not_leak_to_another_operation(
        self, session: AsyncSession
    ) -> None:
        """Setting only the update class leaves create on the default."""
        repo = WidgetRepository(
            session, update_conflict_exception=WidgetUpdateConflictError
        )

        with pytest.raises(ConflictException) as excinfo:
            await _duplicate_add(repo)

        assert type(excinfo.value) is ConflictException


class TestAttributesAreExposed:
    """The resolved classes are readable, like ``not_found_exception``."""

    def test_blanket_resolution(self, session: AsyncSession) -> None:
        """Every attribute falls back to the blanket class."""
        repo = WidgetRepository(session, conflict_exception=WidgetConflictError)

        assert repo.create_conflict_exception is WidgetConflictError
        assert repo.update_conflict_exception is WidgetConflictError
        assert repo.bulk_create_conflict_exception is WidgetConflictError
        assert repo.bulk_update_conflict_exception is WidgetConflictError

    def test_specific_resolution(self, session: AsyncSession) -> None:
        """A per-operation class replaces only its own attribute."""
        repo = WidgetRepository(
            session,
            conflict_exception=WidgetConflictError,
            bulk_update_conflict_exception=WidgetBulkUpdateConflictError,
        )

        assert repo.bulk_update_conflict_exception is WidgetBulkUpdateConflictError
        assert repo.create_conflict_exception is WidgetConflictError

    def test_default_resolution(self, session: AsyncSession) -> None:
        """With no kwargs at all, every attribute is the SDK's own class."""
        repo = WidgetRepository(session)

        assert repo.create_conflict_exception is ConflictException
        assert repo.update_conflict_exception is ConflictException
        assert repo.bulk_create_conflict_exception is ConflictException
        assert repo.bulk_update_conflict_exception is ConflictException

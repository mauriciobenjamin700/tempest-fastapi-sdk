"""Tests for the ``not_found_exception`` / ``conflict_exception`` factories.

The defect these exist to prevent is not verbosity. ``BaseRepository``
raises the class it was handed as ``exception_class(message=...)``, so a
hand-written 404 whose ``__init__`` takes only the record id — the shape
one writes first, because the id is what the caller holds — answers every
repository miss with a ``TypeError``, i.e. a 500 where the 404 belongs.
"""

from __future__ import annotations

import warnings
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    ConflictException,
    InheritedErrorCodeWarning,
    NotFoundException,
    conflict_exception,
    not_found_exception,
)

BudgetNotFoundException = not_found_exception(
    "BUDGET_NOT_FOUND",
    subject="Budget",
    field="budget_id",
)

EmailTakenException = conflict_exception(
    "EMAIL_TAKEN",
    subject="Email",
    field="email",
    template="{identifier} is already registered.",
)


class NaiveNotFound(NotFoundException):
    """The 404 everyone writes first — and the trap the factory removes."""

    code: str = "NAIVE_NOT_FOUND"

    def __init__(self, budget_id: str) -> None:
        """Initialize from the id alone.

        Args:
            budget_id (str): The id that matched no record.
        """
        super().__init__(message=f"Budget {budget_id} not found.")


class TestGeneratedClass:
    """Shape of the class the factory returns."""

    def test_it_is_a_real_subclass(self) -> None:
        assert issubclass(BudgetNotFoundException, NotFoundException)

    def test_status_code_is_404(self) -> None:
        assert BudgetNotFoundException().status_code == 404

    def test_code_is_declared_in_the_class_body(self) -> None:
        """Read without instantiating, which is what OpenAPI tooling does."""
        assert BudgetNotFoundException.code == "BUDGET_NOT_FOUND"
        assert "code" in vars(BudgetNotFoundException)

    def test_the_name_comes_from_the_code(self) -> None:
        """Tracebacks and reprs show something a reader recognizes."""
        assert BudgetNotFoundException.__name__ == "BudgetNotFoundException"

    def test_the_name_can_be_overridden(self) -> None:
        generated = not_found_exception("X_MISSING", name="MyOwnName")

        assert generated.__name__ == "MyOwnName"

    def test_it_declares_a_details_example(self) -> None:
        """The example is what a frontend developer reads in the schema."""
        assert BudgetNotFoundException.details_example == {"budget_id": "..."}

    def test_no_inherited_code_warning(self) -> None:
        """Declaring ``code`` is exactly what silences that warning."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", InheritedErrorCodeWarning)
            not_found_exception("SOMETHING_MISSING", subject="Something")


class TestCallShapes:
    """Both ways of raising it, which is the whole point."""

    def test_identifier_positionally(self) -> None:
        error = BudgetNotFoundException("abc")

        assert error.detail == "Budget abc not found."
        assert error.details == {"budget_id": "abc"}

    def test_message_keyword_only(self) -> None:
        """The shape ``BaseRepository`` uses."""
        error = BudgetNotFoundException(message="Nao encontrado.")

        assert error.detail == "Nao encontrado."
        assert error.details == {}

    def test_no_arguments_at_all(self) -> None:
        error = BudgetNotFoundException()

        assert error.detail == "Budget not found."

    def test_message_wins_over_the_template(self) -> None:
        error = BudgetNotFoundException("abc", message="Explicit.")

        assert error.detail == "Explicit."
        assert error.details == {"budget_id": "abc"}

    def test_extra_details_are_merged(self) -> None:
        error = BudgetNotFoundException("abc", details={"tenant": "t1"})

        assert error.details == {"budget_id": "abc", "tenant": "t1"}

    def test_a_uuid_identifier_is_stringified(self) -> None:
        """``details`` has to be JSON-serializable."""
        identifier = UUID("00000000-0000-0000-0000-0000000000ff")
        error = BudgetNotFoundException(identifier)

        assert error.details == {"budget_id": str(identifier)}

    def test_the_naive_class_is_the_one_that_breaks(self) -> None:
        """The guard fires: this is the failure the factory removes."""
        with pytest.raises(TypeError):
            NaiveNotFound(message="Not found.")


class TestTemplates:
    """Wording is the project's, in the project's language."""

    def test_templates_are_interpolated(self) -> None:
        generated = not_found_exception(
            "ORCAMENTO_NAO_ENCONTRADO",
            subject="Orcamento",
            field="orcamento_id",
            template="{subject} {identifier} nao encontrado.",
            template_anonymous="{subject} nao encontrado.",
        )

        assert generated("7").detail == "Orcamento 7 nao encontrado."
        assert generated().detail == "Orcamento nao encontrado."

    def test_the_class_level_message_matches_the_anonymous_template(self) -> None:
        """``cls.message`` is what the OpenAPI example shows."""
        assert BudgetNotFoundException.message == "Budget not found."


class TestConflictFactory:
    """The 409 twin."""

    def test_it_is_a_conflict(self) -> None:
        assert issubclass(EmailTakenException, ConflictException)
        assert EmailTakenException().status_code == 409

    def test_template_and_details(self) -> None:
        error = EmailTakenException("a@b.com")

        assert error.detail == "a@b.com is already registered."
        assert error.details == {"email": "a@b.com"}
        assert error.code == "EMAIL_TAKEN"

    def test_message_keyword_works_too(self) -> None:
        """``BaseRepository`` raises conflicts the same way it raises 404s."""
        assert EmailTakenException(message="Taken.").detail == "Taken."


class Budget(BaseModel):
    __tablename__ = "budget_for_exception_factory_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class BudgetRepository(BaseRepository[Budget]):
    """Repository wired with the generated exceptions."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository.

        Args:
            session (AsyncSession): The async database session.
        """
        super().__init__(
            session,
            model=Budget,
            not_found_exception=BudgetNotFoundException,
            conflict_exception=EmailTakenException,
        )


class TestAgainstTheRepository:
    """End to end: the miss that used to be a 500."""

    async def test_a_miss_raises_the_generated_404(self, session: AsyncSession) -> None:
        """``BaseRepository`` instantiates it as ``cls(message=...)``."""
        repository = BudgetRepository(session)

        with pytest.raises(BudgetNotFoundException) as excinfo:
            await repository.get_by_id(4242)

        assert excinfo.value.status_code == 404
        assert excinfo.value.code == "BUDGET_NOT_FOUND"

    async def test_the_naive_class_would_have_500ed(
        self, session: AsyncSession
    ) -> None:
        """The guard fires: same call, hand-written class, ``TypeError``."""

        class NaiveRepository(BaseRepository[Budget]):
            def __init__(self, db_session: AsyncSession) -> None:
                """Initialize the repository.

                Args:
                    db_session (AsyncSession): The async database session.
                """
                super().__init__(
                    db_session,
                    model=Budget,
                    not_found_exception=NaiveNotFound,
                )

        repository: Any = NaiveRepository(session)

        with pytest.raises(TypeError):
            await repository.get_by_id(4242)

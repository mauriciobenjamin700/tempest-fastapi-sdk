"""Tests for AppException.__init_subclass__ code-inheritance warnings."""

import warnings
from typing import Any, ClassVar

import pytest

from tempest_fastapi_sdk import (
    AppException,
    ConflictException,
    InheritedErrorCodeWarning,
    NotFoundException,
)


class TestInheritedErrorCodeWarning:
    """A subclass inheriting a generic SDK ``code`` is flagged."""

    def test_subclass_without_code_warns(self) -> None:
        """The defect case: the subclass answers ``"CONFLICT"`` silently."""
        with pytest.warns(InheritedErrorCodeWarning, match="declares no `code`"):

            class CategoryInUse(ConflictException):
                """Deleting a category services still reference."""

        assert CategoryInUse.code == "CONFLICT"

    def test_warning_names_the_class_and_the_inherited_value(self) -> None:
        """The message is actionable without opening the SDK source."""
        with pytest.warns(InheritedErrorCodeWarning) as record:

            class UserNotFound(NotFoundException):
                """User does not exist."""

        message = str(record[0].message)
        assert "UserNotFound" in message
        assert "NotFoundException.code = 'NOT_FOUND'" in message
        assert 'Declare `code = "..."`' in message

    def test_declaring_code_is_silent(self) -> None:
        """The documented class-body form produces no warning."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", InheritedErrorCodeWarning)

            class CategoryInUse(ConflictException):
                """Deleting a category services still reference."""

                code: str = "CATEGORY_IN_USE"

        assert CategoryInUse.code == "CATEGORY_IN_USE"

    def test_declaring_message_key_is_silent(self) -> None:
        """A subclass localizing under its own key is already specific."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", InheritedErrorCodeWarning)

            class CategoryInUse(ConflictException):
                """Deleting a category services still reference."""

                message_key: str = "CATEGORY_IN_USE"

    def test_inheriting_a_domain_code_is_silent(self) -> None:
        """Specializing a project exception is deliberate, not a defect."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", InheritedErrorCodeWarning)

            class DomainConflict(ConflictException):
                """Base for this project's conflicts."""

                code: str = "DOMAIN_CONFLICT"

            class Narrower(DomainConflict):
                """A narrowing subclass reusing the domain code."""

        assert Narrower.code == "DOMAIN_CONFLICT"

    def test_direct_app_exception_subclass_warns(self) -> None:
        """``INTERNAL_SERVER_ERROR`` is just as generic as the rest."""
        with pytest.warns(InheritedErrorCodeWarning):

            class Bare(AppException):
                """No code of its own."""

    def test_warning_is_a_user_warning(self) -> None:
        """Standard filters (``-W``, ``filterwarnings``) reach it."""
        assert issubclass(InheritedErrorCodeWarning, UserWarning)

    def test_can_be_silenced_by_category(self) -> None:
        """A project deliberately using the raise-site form can opt out."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warnings.filterwarnings("ignore", category=InheritedErrorCodeWarning)

            class Silenced(ConflictException):
                """Intentionally generic."""

        assert caught == []


class TestDetailsExample:
    """``details_example`` is documentation-only state."""

    def test_defaults_to_empty(self) -> None:
        """No example means an empty ``details`` object in the schema."""
        assert AppException.details_example == {}

    def test_is_not_read_at_runtime(self) -> None:
        """An instance's ``details`` never picks up the example."""

        class WithExample(ConflictException):
            """Carries an OpenAPI example."""

            code: str = "WITH_EXAMPLE"
            details_example: ClassVar[dict[str, Any]] = {"category_id": "abc"}

        assert WithExample("boom").details == {}

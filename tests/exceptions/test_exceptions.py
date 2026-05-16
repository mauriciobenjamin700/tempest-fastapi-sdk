"""Tests for the application exception hierarchy."""

import pytest

from tempest_fastapi_sdk import (
    AppException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)


class TestAppException:
    def test_defaults(self) -> None:
        with pytest.raises(AppException) as excinfo:
            raise AppException()
        assert excinfo.value.status_code == 500
        assert excinfo.value.code == "INTERNAL_SERVER_ERROR"
        assert excinfo.value.details == {}

    def test_message_and_details_override(self) -> None:
        exc = AppException(message="boom", details={"step": "x"})
        assert exc.detail == "boom"
        assert exc.details == {"step": "x"}


class TestSubclasses:
    @pytest.mark.parametrize(
        ("cls", "expected_status", "expected_code"),
        [
            (NotFoundException, 404, "NOT_FOUND"),
            (ConflictException, 409, "CONFLICT"),
            (ValidationException, 422, "VALIDATION_ERROR"),
            (UnauthorizedException, 401, "UNAUTHORIZED"),
            (ForbiddenException, 403, "FORBIDDEN"),
        ],
    )
    def test_status_and_code(
        self,
        cls: type[AppException],
        expected_status: int,
        expected_code: str,
    ) -> None:
        exc = cls()
        assert exc.status_code == expected_status
        assert exc.code == expected_code
        assert isinstance(exc, AppException)

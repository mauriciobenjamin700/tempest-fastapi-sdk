"""Tests for the application exception hierarchy."""

import pytest

from tempest_fastapi_sdk import (
    AppException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    TooManyRequestsException,
    UnauthorizedException,
    ValidationException,
)


class TestTooManyRequestsException:
    def test_defaults(self) -> None:
        exc = TooManyRequestsException()
        assert exc.status_code == 429
        assert exc.code == "TOO_MANY_REQUESTS"
        assert exc.details == {}

    def test_retry_after_populates_header_and_details(self) -> None:
        exc = TooManyRequestsException(retry_after_seconds=120)
        assert exc.headers is not None
        assert exc.headers["Retry-After"] == "120"
        assert exc.details["retry_after_seconds"] == 120

    def test_message_override(self) -> None:
        exc = TooManyRequestsException(message="slow down", retry_after_seconds=5)
        assert exc.detail == "slow down"


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

"""422 Unprocessable Entity exception."""

from tempest_fastapi_sdk.exceptions.base import AppException


class ValidationException(AppException):
    """Raised when input fails a business rule beyond Pydantic.

    Pydantic emits 422 automatically for schema validation; use this
    for downstream rules that only the service layer can enforce.
    """

    status_code: int = 422
    message: str = "Validation error"
    code: str = "VALIDATION_ERROR"


__all__: list[str] = [
    "ValidationException",
]

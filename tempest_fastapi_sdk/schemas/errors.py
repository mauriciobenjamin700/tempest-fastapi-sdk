"""The error envelope every ``AppException`` is serialized into."""

from typing import Any

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class ErrorResponseSchema(BaseSchema):
    """The JSON body the SDK's exception handlers emit on failure.

    Every handler registered by
    :func:`tempest_fastapi_sdk.register_exception_handlers` — the
    :class:`~tempest_fastapi_sdk.exceptions.AppException` handler, the
    raw ``HTTPException`` 5xx handler and the unhandled-error catch-all
    — writes this exact shape, so a client parses one envelope for
    every failure mode.

    Its reason to exist is the OpenAPI schema: without a declared model
    a route can only document *that* it returns a 409, never *what* the
    body looks like. Point ``responses=`` at this class (or let
    :func:`tempest_fastapi_sdk.error_responses` do it) and the generated
    client gets the envelope for free.

    Branch on ``code``, never on ``detail``. ``detail`` is
    human-readable prose that changes with the request's negotiated
    locale when a
    :class:`~tempest_fastapi_sdk.exceptions.MessageCatalog` is
    registered; ``code`` is the stable contract.

    Attributes:
        detail (str): Human-readable message. Localized when a catalog
            is registered, otherwise the exception's literal message.
        code (str): Stable, machine-readable identifier for the
            failure. The value a client should branch on.
        details (dict[str, Any]): Structured context about the failure
            (which id was missing, which field conflicted). Empty when
            the exception attached none.
    """

    detail: str = Field(
        title="Detail",
        description=(
            "Human-readable message. Localized to the request's "
            "negotiated locale when a MessageCatalog is registered. Show "
            "it to users, but never branch on it."
        ),
        examples=["Service not found", "Serviço não encontrado"],
    )
    code: str = Field(
        title="Code",
        description=(
            "Stable, machine-readable identifier for the failure. This "
            "is the value to branch on — it never changes with locale, "
            "and two failures sharing an HTTP status differ here."
        ),
        examples=["SERVICE_NOT_FOUND", "CANDIDATE_ALREADY_EXISTS"],
    )
    details: dict[str, Any] = Field(
        title="Details",
        description=(
            "Structured context for the failure. Empty when the "
            "exception attached none."
        ),
        examples=[{}, {"service_id": "123e4567-e89b-12d3-a456-426614174000"}],
        default_factory=dict,
    )


__all__: list[str] = [
    "ErrorResponseSchema",
]

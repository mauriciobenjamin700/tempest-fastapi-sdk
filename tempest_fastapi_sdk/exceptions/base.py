"""Base application exception integrated with FastAPI."""

import warnings
from typing import Any, ClassVar

from fastapi import HTTPException

_SDK_EXCEPTIONS_PACKAGE: str = "tempest_fastapi_sdk.exceptions"
"""Module prefix whose ``code`` attributes count as generic."""


class InheritedErrorCodeWarning(UserWarning):
    """Warns that a subclass silently inherited a generic ``code``.

    Emitted at class-creation time by
    :meth:`AppException.__init_subclass__` when a subclass declares
    neither ``code`` nor ``message_key`` and therefore answers with one
    of the SDK's generic identifiers (``"CONFLICT"``, ``"NOT_FOUND"``,
    …). Clients cannot tell such a failure apart from any other with the
    same status, and OpenAPI tooling cannot document it — see
    :func:`tempest_fastapi_sdk.error_responses`.

    Silence it per-project when the pattern is deliberate::

        import warnings

        from tempest_fastapi_sdk import InheritedErrorCodeWarning

        warnings.filterwarnings("ignore", category=InheritedErrorCodeWarning)
    """


class AppException(HTTPException):
    """Base exception for all application-level errors.

    Concrete projects raise a domain-specific subclass that declares its
    ``code`` (and ``status_code``, when the parent's is wrong) **in the
    class body**, and uses ``__init__`` only to build ``message`` /
    ``details``::

        class CategoryInUseException(ConflictException):
            \"\"\"Raised when deleting a category services still reference.\"\"\"

            code = "CATEGORY_IN_USE"

            def __init__(self, category_id: str) -> None:
                \"\"\"Initialize the exception.

                Args:
                    category_id (str): The category that cannot be deleted.
                \"\"\"
                super().__init__(
                    message="Cannot delete a category that still has services.",
                    details={"category_id": category_id},
                )

    The class-body form is the documented one because it is the only
    **introspectable** one. Passing ``code=`` at the raise site works
    identically at runtime, but hides the real value from every static
    consumer: ``CategoryInUseException.code`` would answer the inherited
    ``"CONFLICT"`` and only ``CategoryInUseException("x").code`` would
    answer ``"CATEGORY_IN_USE"``. Since reading it would then require
    instantiating — which means knowing each ``__init__`` signature —
    tooling that documents the errors a route can raise
    (:func:`tempest_fastapi_sdk.error_responses`) cannot work at all.
    A subclass that declares no ``code`` of its own warns with
    :class:`InheritedErrorCodeWarning`.

    The matching exception handler (see
    :mod:`tempest_fastapi_sdk.api.handlers`) emits the JSON shape
    described by
    :class:`tempest_fastapi_sdk.schemas.errors.ErrorResponseSchema`::

        {
            "detail": "<message>",
            "code": "<code>",
            "details": {"<any>": "<context>"}
        }

    Class attributes (defaults the constructor falls back to):
        status_code (int): HTTP status code.
        message (str): Default human-readable message.
        code (str): Stable, machine-readable identifier.
        details_example (dict[str, Any]): Representative ``details``
            payload, used **only** to build the OpenAPI example — never
            read at runtime. Declare it when the exception attaches
            context worth showing to a frontend developer.

    Instance attributes:
        status_code (int): The status code attached to this instance.
        code (str): The error code attached to this instance.
        message (str): The message attached to this instance — the one
            passed to the constructor, falling back to the class-level
            default. Kept in sync with ``detail`` so a caught exception
            reports what was raised, not the class default.
        details (dict[str, Any]): Free-form context attached to the
            response payload.
        message_key (str | None): Catalog key used to localize the
            ``detail`` when a ``MessageCatalog`` is registered. ``None``
            falls back to ``code`` at resolution time.
        message_params (dict[str, Any]): Values interpolated into the
            localized template via :meth:`str.format`.

    Localization:
        When :func:`register_exception_handlers` is given a ``catalog``,
        the handler resolves ``message_key`` (or ``code``) against the
        request's negotiated locale and replaces ``detail`` with the
        localized string. Without a catalog the behavior is unchanged —
        ``detail`` is the literal ``message``.
    """

    status_code: int = 500
    message: str = "Internal server error"
    code: str = "INTERNAL_SERVER_ERROR"
    message_key: str | None = None
    details_example: ClassVar[dict[str, Any]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Warn when a subclass inherits one of the SDK's generic codes.

        A subclass that declares no ``code`` answers with whichever
        ancestor last declared one. When that ancestor lives in
        ``tempest_fastapi_sdk.exceptions`` the value is a generic
        identifier (``"CONFLICT"``, ``"NOT_FOUND"``, …), which makes the
        failure indistinguishable from every other with the same status
        and invisible to OpenAPI tooling. That is a real, silent defect:
        a subclass shipped ``code: "CONFLICT"`` for months in a
        production service exactly this way.

        Inheriting a **domain** code (declared by a project-owned
        ancestor) is deliberate specialization, so it never warns.
        Declaring ``message_key`` also suppresses the warning, since the
        subclass then localizes under its own key.

        Args:
            cls (type[AppException]): The subclass being created.
            **kwargs (Any): Class-creation keyword arguments, forwarded
                to ``super()``.
        """
        super().__init_subclass__(**kwargs)
        if "code" in vars(cls) or "message_key" in vars(cls):
            return
        owner: type[AppException] | None = next(
            (
                base
                for base in cls.__mro__
                if "code" in vars(base) and issubclass(base, AppException)
            ),
            None,
        )
        if owner is None or not owner.__module__.startswith(_SDK_EXCEPTIONS_PACKAGE):
            return
        warnings.warn(
            f"{cls.__module__}.{cls.__qualname__} declares no `code`, so it "
            f"inherits the generic {owner.__name__}.code = {owner.code!r}. "
            f"Clients cannot tell it apart from any other "
            f"{owner.status_code} response and `error_responses()` cannot "
            f'document it. Declare `code = "..."` in the class body.',
            InheritedErrorCodeWarning,
            stacklevel=3,
        )

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        message_key: str | None = None,
        message_params: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message (str | None): Override the class-level message. Used
                verbatim as ``detail`` when no catalog localizes it.
            code (str | None): Override the class-level error code on
                this instance only — leaves other instances of the
                same class untouched.
            status_code (int | None): Override the class-level HTTP
                status code on this instance only.
            details (dict[str, Any] | None): Structured context to
                attach to the JSON response.
            headers (dict[str, str] | None): Optional HTTP headers
                to include in the response.
            message_key (str | None): Catalog key to localize against.
                Defaults to the class-level ``message_key`` (and, at
                resolution time, to ``code`` when both are ``None``).
            message_params (dict[str, Any] | None): Values interpolated
                into the localized message template.
        """
        cls = type(self)
        self.code: str = code if code is not None else cls.code
        self.message_key: str | None = (
            message_key if message_key is not None else cls.message_key
        )
        self.message_params: dict[str, Any] = message_params or {}
        effective_status: int = (
            status_code if status_code is not None else cls.status_code
        )
        self.details: dict[str, Any] = details or {}
        self.message: str = message or cls.message
        super().__init__(
            status_code=effective_status,
            detail=self.message,
            headers=headers,
        )


__all__: list[str] = [
    "AppException",
    "InheritedErrorCodeWarning",
]

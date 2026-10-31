"""Failures of the slide, answer-key and Markdown renderers.

Each one is an :class:`~tempest_fastapi_sdk.exceptions.AppException`, so a
route that lets it propagate answers with the SDK envelope and its own
``code``. None of them echoes the text it refused: the source is whatever
the user typed, and a size limit or an archive member name is reported
through ``message_params`` / ``details`` instead.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tempest_fastapi_sdk.exceptions import AppException, ValidationException


class SlideSourceEmptyError(ValidationException):
    """Raised when the text to parse or render is empty or whitespace."""

    code: str = "SLIDES_SOURCE_EMPTY"
    message: str = "The source text is empty"


class SlideSourceTooLargeError(AppException):
    """Raised when the text to parse or render exceeds the configured limit.

    The limit travels in ``message_params`` as ``limit``, so the catalog
    sentence names it in the caller's language.
    """

    status_code: int = 413
    code: str = "SLIDES_SOURCE_TOO_LARGE"
    message: str = "The source text exceeds the maximum of {limit} characters"
    details_example: ClassVar[dict[str, Any]] = {"limit": 200000}

    def __init__(self, limit: int) -> None:
        """Initialize the exception.

        Args:
            limit (int): The maximum length, in characters.
        """
        super().__init__(
            message=self.message.format(limit=limit),
            details={"limit": limit},
            message_params={"limit": limit},
        )


class MarkdownArchiveError(ValidationException):
    """Raised when a Markdown archive cannot be turned into a document.

    ``details["reason"]`` says which check failed — ``not_a_zip``,
    ``unsafe_member``, ``too_large``, ``too_many_members`` or
    ``no_markdown`` — so a client can branch without parsing prose. The
    member name of an unsafe entry is never included: it is attacker-chosen
    text.
    """

    code: str = "MARKDOWN_ARCHIVE_INVALID"
    message: str = "The archive is not a valid Markdown bundle"
    details_example: ClassVar[dict[str, Any]] = {"reason": "unsafe_member"}

    def __init__(self, reason: str) -> None:
        """Initialize the exception.

        Args:
            reason (str): Which check failed.
        """
        super().__init__(details={"reason": reason})

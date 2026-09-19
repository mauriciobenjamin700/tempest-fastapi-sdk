"""Every code this package raises has a sentence in every supported locale."""

import pytest

from tempest_fastapi_sdk.exceptions.i18n import default_message_catalog
from tempest_fastapi_sdk.hostbridge import (
    HostCommandError,
    HostCommandTimeoutError,
    HostFileDecodeError,
    HostFileNotFoundError,
    HostFileTooLargeError,
    HostUnavailableError,
    InvalidHostPathError,
)
from tempest_fastapi_sdk.pdf import PdfDecryptError, PdfExtractError

LOCALES: tuple[str, ...] = ("pt-BR", "en-US")

CODES: tuple[str, ...] = tuple(
    exception.code
    for exception in (
        InvalidHostPathError,
        HostFileNotFoundError,
        HostFileTooLargeError,
        HostFileDecodeError,
        HostCommandError,
        HostCommandTimeoutError,
        HostUnavailableError,
        PdfDecryptError,
        PdfExtractError,
    )
)

MESSAGE_KEYS: tuple[str, ...] = (
    "HOST_PATH_TRANSLATION_FAILED",
    "HOST_WSLPATH_MISSING",
    "PDF_PAGE_EXTRACT_FAILED",
)
"""Keys a raise site passes as ``message_key`` to say something specific.

They share a ``code`` with an exception above, so a client still branches on
one identifier while the reader gets the sentence that fits.
"""


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("key", CODES + MESSAGE_KEYS)
def test_every_code_has_a_sentence(key: str, locale: str) -> None:
    """A missing entry falls back to the raise site's untranslatable English."""
    assert default_message_catalog().resolve(key, locale)


@pytest.mark.parametrize(
    ("key", "placeholders"),
    [
        ("HOST_INVALID_PATH", ("{path}",)),
        ("HOST_FILE_NOT_FOUND", ("{path}",)),
        ("HOST_FILE_TOO_LARGE", ("{path}", "{size}", "{limit}")),
        ("HOST_FILE_DECODE_FAILED", ("{path}", "{encoding}")),
        ("HOST_COMMAND_TIMEOUT", ("{seconds}",)),
        ("PDF_PAGE_EXTRACT_FAILED", ("{page}",)),
    ],
)
@pytest.mark.parametrize("locale", LOCALES)
def test_dynamic_values_travel_as_placeholders(
    key: str, placeholders: tuple[str, ...], locale: str
) -> None:
    """A value interpolated at the raise site cannot be translated.

    Every locale needs the same placeholders, or one language shows the
    path and the other does not.
    """
    message = default_message_catalog().resolve(key, locale)
    for placeholder in placeholders:
        assert placeholder in message

"""The VAPID ``sub`` rule, ported from py_vapid and pinned to its source."""

from __future__ import annotations

import inspect
import re

import pytest

from tempest_fastapi_sdk.utils.vapid import (
    VAPID_SUBJECT_PATTERN,
    check_vapid_subject,
    is_valid_vapid_subject,
)

py_vapid = pytest.importorskip("py_vapid")

SUBJECTS: list[str] = [
    "mailto:admin@example.com",
    "MAILTO:Admin@Example.COM",
    "mailto:ops@localhost",
    "mailto:admin@example.com trailing",
    "https://example.com",
    "https://example.com/contact",
    "https://localhost",
    "admin@example.com",
    "",
    "   ",
    "mailto:",
    "mailto:admin",
    "http://example.com",
    "https://",
    "example.com",
]
"""Valid and invalid subjects, quirks included."""


def test_pattern_is_py_vapids_character_for_character() -> None:
    """An upstream change to ``_check_sub`` fails here instead of drifting."""
    source = inspect.getsource(py_vapid._check_sub)
    match = re.search(r'pattern = r"(.+)"', source)
    assert match is not None
    assert match.group(1) == VAPID_SUBJECT_PATTERN


@pytest.mark.parametrize("subject", SUBJECTS)
def test_decision_agrees_with_py_vapid(subject: str) -> None:
    assert is_valid_vapid_subject(subject) is py_vapid._check_sub(subject)


@pytest.mark.parametrize(
    ("subject", "detail"),
    [
        ("admin@example.com", "a bare e-mail; prefix it with 'mailto:'"),
        ("", "an empty value"),
        ("   ", "an empty value"),
        ("http://example.com", "an http:// URL; use https://"),
        ("mailto:admin", "'mailto:admin'"),
    ],
)
def test_message_names_what_is_wrong(subject: str, detail: str) -> None:
    with pytest.raises(ValueError) as caught:
        check_vapid_subject(subject)
    assert str(caught.value) == (
        f"VAPID_SUBJECT must be 'mailto:<email>' or 'https://<host>' (got {detail})"
    )


def test_name_is_used_in_the_message() -> None:
    with pytest.raises(ValueError, match=r"^vapid_subject must be"):
        check_vapid_subject("x", name="vapid_subject")


def test_valid_subject_passes() -> None:
    check_vapid_subject("mailto:admin@example.com")
    check_vapid_subject("https://example.com")

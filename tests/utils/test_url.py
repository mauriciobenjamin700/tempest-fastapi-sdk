"""Tests for :func:`tempest_fastapi_sdk.utils._url.mask_url_password` (#491).

The mask has to hold for the URL shapes a real service passes around:
a password that itself contains ``@``, a comma-separated host list, and a
credential in the query string. It also has to hold when the URL is only
part of a longer message — the failing probes quote it inside a sentence.
"""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.utils._url import mask_url_password

_SECRETS = (
    "db-secret",
    "redis-secret",
    "rabbit-secret",
    "pw2",
    "p@ss",
    "zzz",
)


class TestMaskUrlPassword:
    """Every credential shape comes back masked, and nothing else changes."""

    def test_ordinary_url(self) -> None:
        assert (
            mask_url_password("postgresql+asyncpg://u:db-secret@h:5432/app")
            == "postgresql+asyncpg://u:***@h:5432/app"
        )

    def test_password_containing_an_at_sign(self) -> None:
        """The password runs to the last ``@``, not the first (#491)."""
        masked = mask_url_password("amqp://u:p@ss@h/")
        assert "p@ss" not in masked
        assert masked == "amqp://u:***@h/"

    def test_password_only_userinfo(self) -> None:
        assert mask_url_password("redis://:redis-secret@h:6379/0") == (
            "redis://:***@h:6379/0"
        )

    def test_host_list_masks_every_segment(self) -> None:
        """A host list repeats ``user:pass@``; a second one must not survive."""
        masked = mask_url_password("amqp://u:pw@h1,u:pw2@h2/")
        assert masked == "amqp://u:***@h1,u:***@h2/"

    def test_query_string_password_is_masked(self) -> None:
        masked = mask_url_password("postgresql://u:pw@h/db?password=zzz")
        assert masked == "postgresql://u:***@h/db?password=***"

    def test_query_secret_names_after_ampersand(self) -> None:
        masked = mask_url_password("postgresql://u:pw@h/db?x=1&pwd=zzz&secret=yyy")
        assert "zzz" not in masked
        assert "yyy" not in masked
        assert "pwd=***" in masked
        assert "secret=***" in masked

    def test_url_inside_a_sentence_is_masked(self) -> None:
        """A failing probe embeds the URL in its message (#491)."""
        masked = mask_url_password("cannot reach redis://:redis-secret@h:6379/0")
        assert "redis-secret" not in masked
        assert masked == "cannot reach redis://:***@h:6379/0"

    def test_two_urls_in_one_message_are_both_masked(self) -> None:
        masked = mask_url_password(
            "cannot reach postgresql://u:db-secret@h/db nor amqp://g:rabbit-secret@h2/"
        )
        for secret in ("db-secret", "rabbit-secret"):
            assert secret not in masked

    def test_url_without_credentials_is_unchanged(self) -> None:
        for url in (
            "sqlite+aiosqlite:///:memory:",
            "postgresql://host/db",
            "sqlite+aiosqlite:///./file.db",
        ):
            assert mask_url_password(url) == url

    @pytest.mark.parametrize("secret", _SECRETS)
    def test_no_secret_leaks_from_the_shapes(self, secret: str) -> None:
        for url in (
            "amqp://u:p@ss@h/",
            "amqp://u:pw@h1,u:pw2@h2/",
            "amqp://u:pw@h1,u:pw2@h2/?password=zzz",
            "postgresql+asyncpg://u:db-secret@h/db",
            "redis://:redis-secret@h:6379/0",
            "amqp://g:rabbit-secret@h:5672/",
        ):
            assert secret not in mask_url_password(url)

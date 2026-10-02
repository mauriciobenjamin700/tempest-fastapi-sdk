"""Tests for ``sign_path`` / ``verify_path``."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from tempest_fastapi_sdk import (
    ExpiredSignedURLException,
    ForbiddenException,
    InvalidSignedURLException,
    sign_path,
    verify_path,
)
from tempest_fastapi_sdk.utils import signed_url as signed_url_module

SECRET: str = "app-secret"
PURPOSE: str = "files"
NOW: datetime = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def _split(url: str) -> tuple[str, int, str]:
    """Split a signed URL into decoded path, expiry and signature.

    Args:
        url (str): The URL returned by :func:`sign_path`.

    Returns:
        tuple[str, int, str]: ``(decoded path, expires, signature)``.
    """
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    return unquote(parts.path), int(query["expires"][0]), query["signature"][0]


def _sign(
    path: str = "/api/files/report.pdf",
    *,
    secret: str = SECRET,
    purpose: str = PURPOSE,
    expires_in: timedelta = timedelta(minutes=5),
    now: datetime = NOW,
) -> str:
    """Sign ``path`` with the module defaults.

    Args:
        path (str): The decoded path to sign.
        secret (str): The signing secret.
        purpose (str): The purpose label.
        expires_in (timedelta): Validity window.
        now (datetime): The signing instant.

    Returns:
        str: The signed URL.
    """
    return sign_path(
        path, secret=secret, purpose=purpose, expires_in=expires_in, now=now
    )


class TestSignPath:
    """The shape of the URL ``sign_path`` returns."""

    def test_appends_expires_and_signature(self) -> None:
        """The path is followed by ``expires`` and ``signature``."""
        url = _sign()
        path, expires, signature = _split(url)
        assert url.startswith("/api/files/report.pdf?expires=")
        assert path == "/api/files/report.pdf"
        assert expires == int((NOW + timedelta(minutes=5)).timestamp())
        assert len(signature) == 43
        assert "=" not in signature

    def test_known_answer_pins_the_construction(self) -> None:
        """The MAC is HMAC-SHA256(derived key, ``expires LF path``)."""
        _, expires, signature = _split(_sign())
        key = hmac.new(
            SECRET.encode(),
            b"tempest-fastapi-sdk.signed-url.v1\x00" + PURPOSE.encode(),
            hashlib.sha256,
        ).digest()
        digest = hmac.new(
            key, f"{expires}\n/api/files/report.pdf".encode(), hashlib.sha256
        ).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert signature == expected

    def test_percent_encodes_the_path(self) -> None:
        """Spaces, ``%``, ``?`` and non-ASCII travel encoded; ``/`` does not."""
        url = _sign("/files/a b/100%?/á.txt")
        assert url.split("?expires=")[0] == "/files/a%20b/100%25%3F/%C3%A1.txt"

    def test_naive_now_is_read_as_utc(self) -> None:
        """A naive ``now`` signs the same instant as its UTC equivalent."""
        assert _sign(now=NOW.replace(tzinfo=None)) == _sign()

    @pytest.mark.parametrize(
        ("path", "secret", "purpose", "expires_in"),
        [
            ("files/x", SECRET, PURPOSE, timedelta(minutes=1)),
            ("/files/x", SECRET, PURPOSE, timedelta(0)),
            ("/files/x", SECRET, PURPOSE, timedelta(seconds=-1)),
            ("/files/x", "", PURPOSE, timedelta(minutes=1)),
            ("/files/x", SECRET, "", timedelta(minutes=1)),
        ],
    )
    def test_rejects_bad_arguments(
        self, path: str, secret: str, purpose: str, expires_in: timedelta
    ) -> None:
        """Programming errors raise ``ValueError`` at signing time."""
        with pytest.raises(ValueError):
            _sign(path, secret=secret, purpose=purpose, expires_in=expires_in)


class TestVerifyPath:
    """Every rejection the issue lists, plus the success path."""

    def test_valid_signature_passes(self) -> None:
        """A freshly signed URL verifies."""
        path, expires, signature = _split(_sign())
        verify_path(
            path,
            expires=expires,
            signature=signature,
            secret=SECRET,
            purpose=PURPOSE,
            now=NOW,
        )

    def test_valid_until_the_last_second(self) -> None:
        """One second before ``expires`` still verifies."""
        path, expires, signature = _split(_sign())
        verify_path(
            path,
            expires=expires,
            signature=signature,
            secret=SECRET,
            purpose=PURPOSE,
            now=datetime.fromtimestamp(expires - 1, tz=UTC),
        )

    @pytest.mark.parametrize("delta", [0, 1, 3600])
    def test_expired_is_rejected(self, delta: int) -> None:
        """At and after ``expires`` an authentic URL is expired."""
        path, expires, signature = _split(_sign())
        with pytest.raises(ExpiredSignedURLException) as caught:
            verify_path(
                path,
                expires=expires,
                signature=signature,
                secret=SECRET,
                purpose=PURPOSE,
                now=datetime.fromtimestamp(expires + delta, tz=UTC),
            )
        assert caught.value.status_code == 403
        assert caught.value.code == "SIGNED_URL_EXPIRED"

    def test_altered_expires_is_rejected(self) -> None:
        """Extending ``expires`` breaks the MAC — reported as invalid."""
        path, expires, signature = _split(_sign())
        with pytest.raises(InvalidSignedURLException) as caught:
            verify_path(
                path,
                expires=expires + 3600,
                signature=signature,
                secret=SECRET,
                purpose=PURPOSE,
                now=NOW,
            )
        assert caught.value.status_code == 403
        assert caught.value.code == "SIGNED_URL_INVALID"

    def test_forged_expires_on_expired_url_is_invalid_not_expired(self) -> None:
        """Signature is checked before expiry."""
        path, expires, signature = _split(_sign())
        with pytest.raises(InvalidSignedURLException):
            verify_path(
                path,
                expires=expires - 1000,
                signature=signature,
                secret=SECRET,
                purpose=PURPOSE,
                now=NOW,
            )

    @pytest.mark.parametrize(
        "other_path",
        ["/api/files/other.pdf", "/api/files/report.pdf/", "/api/files/REPORT.pdf"],
    )
    def test_altered_path_is_rejected(self, other_path: str) -> None:
        """The signature is bound to the exact path."""
        _, expires, signature = _split(_sign())
        with pytest.raises(InvalidSignedURLException):
            verify_path(
                other_path,
                expires=expires,
                signature=signature,
                secret=SECRET,
                purpose=PURPOSE,
                now=NOW,
            )

    def test_other_purpose_is_rejected(self) -> None:
        """Domain separation: a ``files`` URL is not an ``email-link`` URL."""
        path, expires, signature = _split(_sign())
        with pytest.raises(InvalidSignedURLException):
            verify_path(
                path,
                expires=expires,
                signature=signature,
                secret=SECRET,
                purpose="email-link",
                now=NOW,
            )

    def test_other_secret_is_rejected(self) -> None:
        """A URL signed with another secret does not verify."""
        path, expires, signature = _split(_sign())
        with pytest.raises(InvalidSignedURLException):
            verify_path(
                path,
                expires=expires,
                signature=signature,
                secret="another-secret",
                purpose=PURPOSE,
                now=NOW,
            )

    @pytest.mark.parametrize("signature", ["", "x", "á" * 43, "A" * 43])
    def test_malformed_signature_is_rejected(self, signature: str) -> None:
        """Garbage, including non-ASCII, is invalid rather than a crash."""
        path, expires, _ = _split(_sign())
        with pytest.raises(InvalidSignedURLException):
            verify_path(
                path,
                expires=expires,
                signature=signature,
                secret=SECRET,
                purpose=PURPOSE,
                now=NOW,
            )

    def test_raw_secret_hmac_does_not_verify(self) -> None:
        """A MAC keyed with the raw secret is not accepted."""
        path, expires, _ = _split(_sign())
        digest = hmac.new(
            SECRET.encode(), f"{expires}\n{path}".encode(), hashlib.sha256
        ).digest()
        raw = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        with pytest.raises(InvalidSignedURLException):
            verify_path(
                path,
                expires=expires,
                signature=raw,
                secret=SECRET,
                purpose=PURPOSE,
                now=NOW,
            )

    def test_compares_with_compare_digest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The check goes through :func:`hmac.compare_digest`."""
        calls: list[tuple[bytes, bytes]] = []
        original = hmac.compare_digest

        def _spy(a: bytes, b: bytes) -> bool:
            """Record and delegate.

            Args:
                a (bytes): Expected digest.
                b (bytes): Presented digest.

            Returns:
                bool: The real comparison.
            """
            calls.append((a, b))
            return original(a, b)

        monkeypatch.setattr(signed_url_module.hmac, "compare_digest", _spy)
        path, expires, signature = _split(_sign())
        verify_path(
            path,
            expires=expires,
            signature=signature,
            secret=SECRET,
            purpose=PURPOSE,
            now=NOW,
        )
        assert calls == [(signature.encode(), signature.encode())]

    def test_exceptions_are_forbidden(self) -> None:
        """Both failures are ``ForbiddenException`` subclasses."""
        assert issubclass(InvalidSignedURLException, ForbiddenException)
        assert issubclass(ExpiredSignedURLException, ForbiddenException)

    def test_empty_material_raises_value_error(self) -> None:
        """An empty secret or purpose is a programming error."""
        with pytest.raises(ValueError):
            verify_path("/x", expires=1, signature="s", secret="", purpose="p")
        with pytest.raises(ValueError):
            verify_path("/x", expires=1, signature="s", secret="s", purpose="")

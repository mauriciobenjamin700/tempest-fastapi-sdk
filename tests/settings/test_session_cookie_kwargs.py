"""``SessionSettings`` cookie mappers and the ``SameSite`` literal (#344)."""

from __future__ import annotations

import itertools

import pytest
from pydantic import ValidationError
from starlette.responses import Response

from tempest_fastapi_sdk import SessionCookieSameSite, SessionSettings

SAMESITES: tuple[SessionCookieSameSite, ...] = ("lax", "strict", "none")


def _set_cookie_header(response: Response) -> str:
    """Return the single ``Set-Cookie`` header ``response`` carries.

    Args:
        response (Response): The response the mapper was applied to.

    Returns:
        str: The raw header value.
    """
    values = [v for k, v in response.raw_headers if k == b"set-cookie"]
    assert len(values) == 1
    return values[0].decode("latin-1")


class TestSameSiteLiteral:
    """The field is a ``Literal`` and a bad value fails at construction."""

    @pytest.mark.parametrize("value", SAMESITES)
    def test_accepts_each_policy(self, value: SessionCookieSameSite) -> None:
        assert (
            value
            == SessionSettings(SESSION_COOKIE_SAMESITE=value).SESSION_COOKIE_SAMESITE
        )

    @pytest.mark.parametrize("value", ["Lax", "LAX", "lax;", "strict,", "", "always"])
    def test_rejects_anything_else(self, value: str) -> None:
        with pytest.raises(ValidationError) as info:
            SessionSettings(SESSION_COOKIE_SAMESITE=value)
        assert info.value.errors()[0]["type"] == "literal_error"

    @pytest.mark.parametrize("value", ["lax ", " strict", "\tnone\n"])
    def test_surrounding_whitespace_is_trimmed(self, value: str) -> None:
        """The ``str`` field with ``pattern`` accepted these before the Literal."""
        assert (
            value.strip()
            == SessionSettings(SESSION_COOKIE_SAMESITE=value).SESSION_COOKIE_SAMESITE
        )

    def test_bad_value_from_environment_fails_at_boot(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "Strict")
        with pytest.raises(ValidationError):
            SessionSettings()


class TestSessionCookieKwargs:
    """``set_cookie(**kwargs)`` writes the attributes the settings name."""

    @pytest.mark.parametrize(
        ("secure", "samesite"),
        list(itertools.product((True, False), SAMESITES)),
    )
    def test_set_cookie_header_for_each_combination(
        self,
        secure: bool,
        samesite: SessionCookieSameSite,
    ) -> None:
        settings = SessionSettings(
            SESSION_COOKIE_NAME="app_sid",
            SESSION_TTL_SECONDS=3600,
            SESSION_COOKIE_SECURE=secure,
            SESSION_COOKIE_SAMESITE=samesite,
        )
        response = Response()
        response.set_cookie(value="tok", **settings.session_cookie_kwargs())
        header = _set_cookie_header(response)
        attrs = [part.strip() for part in header.split(";")]
        assert attrs[0] == "app_sid=tok"
        assert "Max-Age=3600" in attrs
        assert "Path=/" in attrs
        assert "HttpOnly" in attrs
        assert f"SameSite={samesite}" in attrs
        assert ("Secure" in attrs) is secure
        assert not any(a.startswith("Domain=") for a in attrs)

    def test_domain_path_and_httponly_follow_settings(self) -> None:
        settings = SessionSettings(
            SESSION_COOKIE_DOMAIN=".example.com",
            SESSION_COOKIE_PATH="/admin",
            SESSION_COOKIE_HTTPONLY=False,
        )
        response = Response()
        response.set_cookie(value="tok", **settings.session_cookie_kwargs())
        attrs = [p.strip() for p in _set_cookie_header(response).split(";")]
        assert "Domain=.example.com" in attrs
        assert "Path=/admin" in attrs
        assert "HttpOnly" not in attrs

    def test_kwargs_are_exactly_the_set_cookie_keywords(self) -> None:
        assert set(SessionSettings().session_cookie_kwargs()) == {
            "key",
            "max_age",
            "path",
            "domain",
            "secure",
            "httponly",
            "samesite",
        }


class TestSessionCookieDeleteKwargs:
    """``delete_cookie(**kwargs)`` matches what ``set_cookie`` wrote."""

    @pytest.mark.parametrize(
        ("secure", "samesite"),
        list(itertools.product((True, False), SAMESITES)),
    )
    def test_delete_matches_set_attributes(
        self,
        secure: bool,
        samesite: SessionCookieSameSite,
    ) -> None:
        settings = SessionSettings(
            SESSION_COOKIE_NAME="app_sid",
            SESSION_COOKIE_DOMAIN=".example.com",
            SESSION_COOKIE_PATH="/admin",
            SESSION_COOKIE_SECURE=secure,
            SESSION_COOKIE_SAMESITE=samesite,
        )
        setter = Response()
        setter.set_cookie(value="tok", **settings.session_cookie_kwargs())
        deleter = Response()
        deleter.delete_cookie(**settings.session_cookie_delete_kwargs())

        set_attrs = {p.strip() for p in _set_cookie_header(setter).split(";")}
        del_attrs = [p.strip() for p in _set_cookie_header(deleter).split(";")]
        assert del_attrs[0] == 'app_sid=""'
        assert "Max-Age=0" in del_attrs
        shared = {
            "Domain=.example.com",
            "Path=/admin",
            "HttpOnly",
            f"SameSite={samesite}",
        }
        if secure:
            shared.add("Secure")
        assert shared <= set_attrs
        assert shared <= set(del_attrs)
        assert ("Secure" in del_attrs) is secure

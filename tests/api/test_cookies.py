"""Tests for tempest_fastapi_sdk.api.cookies."""

from starlette.responses import Response

from tempest_fastapi_sdk.api import clear_cookie, set_cookie


def _header(response: Response) -> str:
    return response.headers["set-cookie"].lower()


class TestSetCookie:
    def test_secure_defaults(self) -> None:
        response = Response()
        set_cookie(response, "access", "tok", max_age=900)
        header = _header(response)
        assert "access=tok" in header
        assert "httponly" in header
        assert "secure" in header
        assert "samesite=lax" in header
        assert "max-age=900" in header

    def test_session_cookie_without_max_age(self) -> None:
        response = Response()
        set_cookie(response, "access", "tok")
        assert "max-age" not in _header(response)

    def test_scoped_path_and_samesite_none(self) -> None:
        response = Response()
        set_cookie(
            response,
            "refresh",
            "tok",
            max_age=1209600,
            path="/api/auth",
            samesite="none",
        )
        header = _header(response)
        assert "path=/api/auth" in header
        assert "samesite=none" in header

    def test_http_only_can_be_disabled(self) -> None:
        response = Response()
        set_cookie(response, "c", "v", http_only=False)
        assert "httponly" not in _header(response)


class TestClearCookie:
    def test_emits_expiring_cookie(self) -> None:
        response = Response()
        clear_cookie(response, "access")
        header = _header(response)
        assert "access=" in header
        # delete_cookie sets an expiry in the past / max-age=0
        assert "max-age=0" in header or "expires=" in header

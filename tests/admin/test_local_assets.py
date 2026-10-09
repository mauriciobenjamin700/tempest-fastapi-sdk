"""The admin renders without a third-party host, and the SQL console is styled.

``base.html`` loaded ``https://unpkg.com/htmx.org@2.0.3`` with no
``integrity`` attribute, so every admin page -- login, dashboard, the SQL
console -- ran a script from an origin the operator does not control, on the
same origin as ``POST /admin/sql`` (#433). The SDK already ships the file
(``ssr/_static/htmx.min.js``) and :func:`make_htmx_router` serves it; the admin
now mounts that router under its own prefix.

The SQL console template declared a whole ``tempest-admin-sql*`` family and
``admin.css`` had no rule for any of it (#438). The coverage check here reads
the stylesheet the package ships, so a class added to the template without a
rule fails instead of rendering as a raw form.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import AsyncIterator
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from tempest_fastapi_sdk import BaseUserModel
from tempest_fastapi_sdk.admin import AdminSite, SqlShellService, make_admin_router
from tempest_fastapi_sdk.admin.auth import UserModelAuthBackend
from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

_ADMIN_DIR = Path(__file__).resolve().parents[2] / "tempest_fastapi_sdk" / "admin"
"""The admin package directory, where the template and the stylesheet live."""

_URL_ATTRIBUTE = re.compile(r"""\b(?:src|href|action)\s*=\s*["']([^"']*)["']""")
"""Every attribute through which a page makes the browser fetch something."""

_APP_HOST = "testserver"
"""The host :class:`TestClient` serves on; ``url_for`` emits absolute URLs to it."""

_CSRF_INPUT = re.compile(r'name="csrf_token" value="([^"]+)"')
"""The hidden CSRF input the console form carries."""

_CLASS_ATTRIBUTE = re.compile(r'class="([^"]*)"')
"""A ``class`` attribute in a Jinja template."""

_JINJA_TAG = re.compile(r"\{[{%].*?[%}]\}")
"""A Jinja expression or statement embedded in an attribute value."""

_SHIPPED_CDN_LINE = '<script src="https://unpkg.com/htmx.org@2.0.3" defer></script>'
"""The exact line ``base.html`` shipped before #433."""


def _external_urls(html: str, *, app_host: str = _APP_HOST) -> list[str]:
    """Return every fetched URL in ``html`` that points off the application.

    ``url_for`` renders absolute URLs back to the application's own host, so
    a URL counts as external when it names any other host -- absolute or
    protocol-relative alike.

    Args:
        html (str): A rendered page.
        app_host (str): The host the application itself answers on.

    Returns:
        list[str]: The URLs that name another host, in page order.
    """
    return [
        url
        for url in _URL_ATTRIBUTE.findall(html)
        if urlsplit(url).netloc not in ("", app_host)
    ]


def _template_classes(template: str) -> set[str]:
    """Return the static class names a Jinja template uses.

    Jinja expressions inside the attribute are dropped, so a modifier built
    at render time does not count as a class of its own.

    Args:
        template (str): The template source.

    Returns:
        set[str]: Every literal class name.
    """
    names: set[str] = set()
    for value in _CLASS_ATTRIBUTE.findall(template):
        names.update(_JINJA_TAG.sub(" ", value).split())
    return names


def _stylesheet_classes(css: str) -> set[str]:
    """Return every class name a stylesheet has a selector for.

    Args:
        css (str): The stylesheet source.

    Returns:
        set[str]: The class names that appear in some selector.
    """
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return set(re.findall(r"\.([a-zA-Z_][\w-]*)", without_comments))


def _unstyled(template: str, css: str) -> list[str]:
    """Return the classes ``template`` uses that ``css`` never selects.

    Args:
        template (str): The template source.
        css (str): The stylesheet source.

    Returns:
        list[str]: The unstyled class names, sorted.
    """
    return sorted(_template_classes(template) - _stylesheet_classes(css))


class AssetUser(BaseUserModel):
    """Admin principal for the asset tests."""

    __tablename__ = "admin_local_asset_users"


async def _build_app(prefix: str) -> tuple[FastAPI, AsyncDatabaseManager]:
    """Build an app with the admin and its SQL console mounted at ``prefix``.

    Args:
        prefix (str): The admin URL prefix.

    Returns:
        tuple[FastAPI, AsyncDatabaseManager]: The app and its open database.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        await session.execute(text("CREATE TABLE orders (id INTEGER, total INTEGER)"))
        await session.execute(text("INSERT INTO orders VALUES (1, 100), (2, 200)"))
        user = AssetUser(email="ops@x.com", is_admin=True)
        user.set_password("pw")
        session.add(user)
    app = FastAPI()
    app.include_router(
        make_admin_router(
            AdminSite(title="Assets"),
            db=db,
            auth_backend=UserModelAuthBackend(AssetUser),
            secret_key="a" * 32,
            cookie_secure=False,
            prefix=prefix,
            sql_shell=SqlShellService(db, dialect="sqlite"),
        ),
    )
    return app, db


@pytest.fixture
async def admin_app() -> AsyncIterator[FastAPI]:
    """Yield an app with the admin mounted at the default prefix.

    Yields:
        FastAPI: The application.
    """
    app, db = await _build_app("/admin")
    yield app
    await db.disconnect()


@pytest.fixture
def signed_in(admin_app: FastAPI) -> TestClient:
    """Return a client that has logged in to the admin.

    Args:
        admin_app (FastAPI): The application.

    Returns:
        TestClient: The authenticated client.
    """
    client = TestClient(admin_app)
    client.post("/admin/login", data={"identifier": "ops@x.com", "password": "pw"})
    return client


def _run_sql(client: TestClient, sql: str) -> str:
    """Submit a statement through the console form and return the page.

    Args:
        client (TestClient): A signed-in client.
        sql (str): The statement.

    Returns:
        str: The rendered console with the outcome.

    Raises:
        AssertionError: When the console form carries no CSRF token.
    """
    match = _CSRF_INPUT.search(client.get("/admin/sql").text)
    assert match is not None, "the console form carries no csrf_token"
    response = client.post(
        "/admin/sql", data={"sql": sql, "csrf_token": match.group(1)}
    )
    assert response.status_code == 200
    return response.text


class TestHtmxIsServedLocally:
    """The admin serves the bundled HTMX itself, under its own prefix."""

    def test_the_script_needs_no_session(self, admin_app: FastAPI) -> None:
        """The login page loads it, so it is served before anyone signs in.

        Args:
            admin_app (FastAPI): The application.
        """
        response = TestClient(admin_app).get(
            "/admin/_ssr/htmx.js", follow_redirects=False
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/javascript")

    def test_the_bytes_are_the_bundled_file(self, admin_app: FastAPI) -> None:
        """What is served is the package's ``htmx.min.js``, byte for byte.

        Args:
            admin_app (FastAPI): The application.
        """
        bundled = (
            files("tempest_fastapi_sdk.ssr._static") / "htmx.min.js"
        ).read_bytes()
        served = TestClient(admin_app).get("/admin/_ssr/htmx.js").content
        assert served == bundled

    def test_the_login_page_points_at_the_local_path(self, admin_app: FastAPI) -> None:
        """The ``<script>`` resolves to the application, not unpkg.

        Args:
            admin_app (FastAPI): The application.
        """
        html = TestClient(admin_app).get("/admin/login").text
        assert '<script src="/admin/_ssr/htmx.js" defer></script>' in html
        assert "unpkg.com" not in html

    async def test_a_custom_prefix_moves_the_script_with_it(self) -> None:
        """Mounted elsewhere, the page and the route agree on the new path."""
        app, db = await _build_app("/backoffice")
        try:
            client = TestClient(app)
            html = client.get("/backoffice/login").text
            assert '<script src="/backoffice/_ssr/htmx.js" defer></script>' in html
            assert client.get("/backoffice/_ssr/htmx.js").status_code == 200
        finally:
            await db.disconnect()


class TestNoPageReachesAThirdPartyHost:
    """Guard: no admin page fetches anything from outside the application."""

    def test_the_login_page(self, admin_app: FastAPI) -> None:
        """The page served before authentication.

        Args:
            admin_app (FastAPI): The application.
        """
        assert _external_urls(TestClient(admin_app).get("/admin/login").text) == []

    def test_the_dashboard(self, signed_in: TestClient) -> None:
        """The first page after login, with the sidebar.

        Args:
            signed_in (TestClient): An authenticated client.
        """
        response = signed_in.get("/admin/")
        assert response.status_code == 200
        assert _external_urls(response.text) == []

    def test_the_sql_console_with_a_result(self, signed_in: TestClient) -> None:
        """The densest page, with the result grid rendered.

        Args:
            signed_in (TestClient): An authenticated client.
        """
        html = _run_sql(signed_in, "SELECT id, total FROM orders ORDER BY id")
        assert "tempest-admin-sql__grid" in html
        assert _external_urls(html) == []

    def test_the_guard_fires_on_the_line_that_shipped(self) -> None:
        """The pre-#433 ``<script>`` is reported, so the guard is not vacuous."""
        page = f"<head>{_SHIPPED_CDN_LINE}</head>"
        assert _external_urls(page) == ["https://unpkg.com/htmx.org@2.0.3"]

    def test_the_guard_fires_on_a_protocol_relative_url(self) -> None:
        """``//cdn.example`` leaves the application just the same."""
        page = '<link rel="stylesheet" href="//cdn.example/x.css">'
        assert _external_urls(page) == ["//cdn.example/x.css"]


class TestImportNeedsNoSsrExtra:
    """Mounting the HTMX router does not make ``[admin]`` require ``[ssr]``."""

    def test_the_admin_router_imports_without_tempestweb(self) -> None:
        """``tempestweb`` (what ``[ssr]`` installs) is blocked, and the import works.

        Measured in a fresh interpreter: the parent already imported the
        whole package, so an in-process check would prove nothing.
        """
        probe = (
            "import sys\n"
            "class _Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] == 'tempestweb':\n"
            "            raise ImportError('blocked ' + name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, _Block())\n"
            "from tempest_fastapi_sdk.admin import make_admin_router\n"
            "from tempest_fastapi_sdk.ssr.assets import make_htmx_router\n"
            "make_htmx_router()\n"
            "print(sorted(m for m in sys.modules if m.startswith('tempestweb')))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[]"


class TestSqlConsoleIsStyled:
    """Every class the SQL console uses has a rule in ``admin.css``."""

    def test_every_class_in_the_template_has_a_rule(self) -> None:
        """The template and the stylesheet that ships agree."""
        template = (_ADMIN_DIR / "templates" / "sql_shell.html").read_text()
        css = (_ADMIN_DIR / "static" / "admin.css").read_text()
        assert _unstyled(template, css) == []

    def test_the_check_fires_on_a_class_without_a_rule(self) -> None:
        """The pre-#438 shape: a family of classes and no selector for them."""
        template = '<section class="tempest-admin-sql {{ extra }}"></section>'
        css = "/* .tempest-admin-sql is only mentioned here */ .other {}"
        assert _unstyled(template, css) == ["tempest-admin-sql"]

    def test_the_result_grid_uses_the_shared_table_wrap(
        self, signed_in: TestClient
    ) -> None:
        """The grid sits in the same wrapper and table class as the list views.

        Args:
            signed_in (TestClient): An authenticated client.
        """
        html = _run_sql(signed_in, "SELECT id, total FROM orders ORDER BY id")
        assert 'class="tempest-admin-table-wrap tempest-admin-sql__grid-wrap"' in html
        assert 'class="tempest-admin-list__table tempest-admin-sql__grid"' in html

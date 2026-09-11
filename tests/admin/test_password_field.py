"""Creating a user through the panel, which was impossible.

``AdminModel(UserModel, can_create=True)`` rendered a form with no
password box — ``editable_field_names`` drops ``hashed_password``, and
rightly so, nobody types a bcrypt digest into a text input. The insert
then hit the column's ``NOT NULL`` and the operator read
``Conflict creating <Model>``, a message that reads like a duplicate
e-mail. Every consumer worked around it the same way, with
``can_create=False`` and a comment.

``password_fields`` closes the loop: the box takes plaintext and the
save hashes it, through the model's own ``set_password``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    PasswordPolicy,
    UserModelAuthBackend,
    make_admin_router,
)


class PanelUser(BaseUserModel):
    """The subclass a service registers — no column of its own."""

    __tablename__ = "admin_password_users"

    name: Mapped[str] = mapped_column(String(64), nullable=False, default="")


SECRET = "x" * 48
_SLUG = PanelUser.__tablename__


def _admin(**kwargs: object) -> AdminModel[PanelUser]:
    """Build the admin config under test.

    Args:
        **kwargs (object): Overrides forwarded to ``AdminModel``.

    Returns:
        AdminModel[PanelUser]: The configuration.
    """
    options: dict[str, object] = {
        "password_fields": [PanelUser.hashed_password],
        "list_display": [PanelUser.id, PanelUser.email],
    }
    options.update(kwargs)
    return AdminModel(model=PanelUser, **options)  # type: ignore[arg-type]


@pytest.fixture
async def app() -> AsyncIterator[tuple[FastAPI, AsyncDatabaseManager]]:
    """Yield an admin app with one registered user model.

    Yields:
        tuple[FastAPI, AsyncDatabaseManager]: The application and the
        database manager behind it, so a test can read a row back.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        root = PanelUser(email="root@example.com", hashed_password="", is_admin=True)
        root.set_password("hunter2hunter2")
        session.add(root)
        await session.commit()

    site = AdminSite(title="Test Admin")
    site.register(_admin(password_policy=PasswordPolicy(min_length=8)))
    application = FastAPI()
    application.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(PanelUser),
            secret_key=SECRET,
            cookie_secure=False,
        ),
    )
    yield application, db
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    """Return an ASGI client for the app.

    Args:
        app (FastAPI): The application.

    Returns:
        AsyncClient: The client.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login_csrf(client: AsyncClient, path: str = f"/admin/m/{_SLUG}/new") -> str:
    """Log in and return the CSRF token of the page at ``path``.

    Args:
        client (AsyncClient): The client to log in.
        path (str): The page whose token is wanted.

    Returns:
        str: The CSRF token.
    """
    await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2hunter2"},
    )
    page = await client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


async def _load(db: AsyncDatabaseManager, email: str) -> PanelUser:
    """Load a user by e-mail straight from the database.

    Args:
        db (AsyncDatabaseManager): The manager the app writes through.
        email (str): The address to look up.

    Returns:
        PanelUser: The stored row.
    """
    from sqlalchemy import select

    async with db.get_session_context() as session:
        result = await session.execute(
            select(PanelUser).where(PanelUser.email == email),
        )
        return result.scalar_one()


class TestConfiguration:
    """What the configuration object answers."""

    def test_password_column_becomes_editable(self) -> None:
        assert "hashed_password" in _admin().editable_field_names()

    def test_default_admin_still_hides_it(self) -> None:
        assert (
            "hashed_password"
            not in AdminModel(
                model=PanelUser,
            ).editable_field_names()
        )

    def test_it_never_reaches_list_display_or_export(self) -> None:
        admin = _admin(list_display=[PanelUser.email, PanelUser.hashed_password])

        assert admin.resolved_list_display() == ["email"]

    def test_unknown_column_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="does not have"):
            AdminModel(model=PanelUser, password_fields=["nope"])

    def test_csv_import_is_refused(self) -> None:
        """Hashing plaintext out of a file is a separate decision."""
        with pytest.raises(ValueError, match="can_import"):
            _admin(can_import=True)


class TestCreate:
    async def test_form_renders_a_password_box(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        application, _ = app
        async with _client(application) as client:
            await client.post(
                "/admin/login",
                data={
                    "identifier": "root@example.com",
                    "password": "hunter2hunter2",
                },
            )
            form = await client.get(f"/admin/m/{_SLUG}/new")

        assert 'type="password" name="hashed_password"' in form.text
        assert 'autocomplete="new-password"' in form.text

    async def test_created_user_can_authenticate(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        application, db = app
        async with _client(application) as client:
            token = await _login_csrf(client)
            response = await client.post(
                f"/admin/m/{_SLUG}/new",
                data={
                    "csrf_token": token,
                    "email": "ana@example.com",
                    "name": "Ana",
                    "hashed_password": "s3cret-pass",
                    "is_active": "on",
                },
                follow_redirects=False,
            )

        assert response.status_code == 303
        user = await _load(db, "ana@example.com")
        assert user.check_password("s3cret-pass")

    async def test_stored_value_is_not_the_plaintext(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        application, db = app
        async with _client(application) as client:
            token = await _login_csrf(client)
            await client.post(
                f"/admin/m/{_SLUG}/new",
                data={
                    "csrf_token": token,
                    "email": "bruno@example.com",
                    "name": "Bruno",
                    "hashed_password": "s3cret-pass",
                },
                follow_redirects=False,
            )

        user = await _load(db, "bruno@example.com")
        assert user.hashed_password != "s3cret-pass"
        assert user.hashed_password.startswith("$2b$")

    async def test_missing_password_is_a_field_error_not_a_conflict(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        """The old failure answered ``Conflict creating PanelUser``."""
        application, _ = app
        async with _client(application) as client:
            token = await _login_csrf(client)
            response = await client.post(
                f"/admin/m/{_SLUG}/new",
                data={
                    "csrf_token": token,
                    "email": "carla@example.com",
                    "name": "Carla",
                    "hashed_password": "",
                },
            )

        assert response.status_code == 400
        assert "This field is required." in response.text
        assert "Conflict creating" not in response.text

    async def test_policy_violation_is_reported_on_the_field(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        application, _ = app
        async with _client(application) as client:
            token = await _login_csrf(client)
            response = await client.post(
                f"/admin/m/{_SLUG}/new",
                data={
                    "csrf_token": token,
                    "email": "dora@example.com",
                    "name": "Dora",
                    "hashed_password": "short",
                },
            )

        assert response.status_code == 400
        assert "password must be at least 8 characters" in response.text


class TestEdit:
    async def test_blank_keeps_the_stored_hash_byte_for_byte(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        application, db = app
        async with _client(application) as client:
            token = await _login_csrf(client)
            await client.post(
                f"/admin/m/{_SLUG}/new",
                data={
                    "csrf_token": token,
                    "email": "edu@example.com",
                    "name": "Edu",
                    "hashed_password": "first-pass",
                },
                follow_redirects=False,
            )
            created = await _load(db, "edu@example.com")
            before = created.hashed_password
            edit_path = f"/admin/m/{_SLUG}/{created.id}/edit"
            edit_token = await _login_csrf(client, edit_path)
            response = await client.post(
                edit_path,
                data={
                    "csrf_token": edit_token,
                    "email": "edu2@example.com",
                    "name": "Edu",
                    "hashed_password": "",
                },
                follow_redirects=False,
            )

        assert response.status_code == 303
        after = await _load(db, "edu2@example.com")
        assert after.hashed_password == before

    async def test_a_value_rotates_the_hash(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        application, db = app
        async with _client(application) as client:
            token = await _login_csrf(client)
            await client.post(
                f"/admin/m/{_SLUG}/new",
                data={
                    "csrf_token": token,
                    "email": "fabi@example.com",
                    "name": "Fabi",
                    "hashed_password": "first-pass",
                },
                follow_redirects=False,
            )
            created = await _load(db, "fabi@example.com")
            before = created.hashed_password
            edit_path = f"/admin/m/{_SLUG}/{created.id}/edit"
            edit_token = await _login_csrf(client, edit_path)
            await client.post(
                edit_path,
                data={
                    "csrf_token": edit_token,
                    "email": "fabi@example.com",
                    "name": "Fabi",
                    "hashed_password": "second-pass",
                },
                follow_redirects=False,
            )

        after = await _load(db, "fabi@example.com")
        assert after.hashed_password != before
        assert after.check_password("second-pass")

    async def test_the_box_is_never_prefilled_with_the_digest(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        application, db = app
        async with _client(application) as client:
            token = await _login_csrf(client)
            await client.post(
                f"/admin/m/{_SLUG}/new",
                data={
                    "csrf_token": token,
                    "email": "gil@example.com",
                    "name": "Gil",
                    "hashed_password": "first-pass",
                },
                follow_redirects=False,
            )
            created = await _load(db, "gil@example.com")
            await _login_csrf(client)
            page = await client.get(f"/admin/m/{_SLUG}/{created.id}/edit")

        assert created.hashed_password not in page.text
        assert 'name="hashed_password" value=""' in page.text
        assert "Leave blank to keep the current password." in page.text


class TestDetailAndList:
    async def test_detail_hides_the_column(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        application, db = app
        async with _client(application) as client:
            token = await _login_csrf(client)
            await client.post(
                f"/admin/m/{_SLUG}/new",
                data={
                    "csrf_token": token,
                    "email": "helo@example.com",
                    "name": "Helo",
                    "hashed_password": "first-pass",
                },
                follow_redirects=False,
            )
            created = await _load(db, "helo@example.com")
            detail = await client.get(f"/admin/m/{_SLUG}/{created.id}")

        assert created.hashed_password not in detail.text

    async def test_export_omits_the_column(
        self,
        app: tuple[FastAPI, AsyncDatabaseManager],
    ) -> None:
        application, _ = app
        async with _client(application) as client:
            token = await _login_csrf(client)
            await client.post(
                f"/admin/m/{_SLUG}/new",
                data={
                    "csrf_token": token,
                    "email": "ivo@example.com",
                    "name": "Ivo",
                    "hashed_password": "first-pass",
                },
                follow_redirects=False,
            )
            export = await client.get(f"/admin/m/{_SLUG}/export.csv")

        assert "hashed_password" not in export.text

"""``GET {prefix}/search``: every searchable model, ``VIEW`` and ``row_scope``.

The route is not under ``/m/{slug}/``, so the route guard of
``test_row_scope.py`` does not enumerate it; its scope case lives here, run
against the scoped world and, as the proof that it bites, the unscoped one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, and_, event
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminPermissionEnum
from tempest_fastapi_sdk.testing import admin_login
from tests.admin.row_scope_world import (
    ScopeLeakError,
    ScopeWorld,
    hidden,
    new_database,
    open_world,
)

SECRET = "g" * 48


class SearchUser(BaseUserModel):
    """Operator of the search tests."""

    __tablename__ = "admin_global_search_users"


class SearchBook(BaseModel):
    """A searchable model the operator may view."""

    __tablename__ = "admin_global_search_books"
    title: Mapped[str] = mapped_column(String(64), nullable=False)


class SearchVault(BaseModel):
    """A searchable model the policy hides."""

    __tablename__ = "admin_global_search_vault"
    title: Mapped[str] = mapped_column(String(64), nullable=False)


class SearchPlain(BaseModel):
    """A model without ``search_fields``."""

    __tablename__ = "admin_global_search_plain"
    title: Mapped[str] = mapped_column(String(64), nullable=False)


def hide_vault(user: Any, admin: Any, action: AdminPermissionEnum) -> bool:
    """Deny every permission on :class:`SearchVault`.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.
        action (AdminPermissionEnum): The permission.

    Returns:
        bool: ``False`` for the vault.
    """
    return admin.model is not SearchVault


class HidingBookRepository(BaseRepository[SearchBook]):
    """Tenant-style repository whose ``paginate`` hides ``Dune part 0``."""

    def __init__(self, session: Any) -> None:
        """Bind the repository.

        Args:
            session (Any): The session.
        """
        super().__init__(session, model=SearchBook)

    async def paginate(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Paginate without ``Dune part 0``.

        Args:
            *args (Any): Forwarded.
            **kwargs (Any): Forwarded; ``where`` gains the filter.

        Returns:
            dict[str, Any]: The page.
        """
        hidden = SearchBook.title != "Dune part 0"
        where = kwargs.pop("where", None)
        kwargs["where"] = hidden if where is None else and_(where, hidden)
        return await super().paginate(*args, **kwargs)


async def _search_world(
    *, searchable: bool = True, book_repository: Any = None
) -> AsyncIterator[tuple[AsyncClient, list[str]]]:
    """Seed three matching books, one vault row, mount and sign in.

    Args:
        searchable (bool): Whether the book admin declares
            ``search_fields``.

    Yields:
        tuple[AsyncClient, list[str]]: The client and the SQL it caused.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = SearchUser(email="op@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add_all(
            [SearchBook(title=f"Dune part {n}") for n in range(3)]
            + [SearchVault(title="Dune vault"), SearchPlain(title="Dune plain")]
        )
        await session.commit()
    site = AdminSite(title="Search")
    site.register(
        AdminModel(
            SearchBook,
            search_fields=["title"] if searchable else [],
            repository_class=book_repository,
        )
    )
    site.register(AdminModel(SearchVault, search_fields=["title"]))
    site.register(AdminModel(SearchPlain))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(SearchUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=hide_vault,
            search_results_per_model=2,
            show_metrics=False,
        )
    )
    statements: list[str] = []

    def capture(
        conn: Any, cursor: Any, statement: str, *args: Any, **kwargs: Any
    ) -> None:
        """Record every statement sent to the database.

        Args:
            conn (Any): The connection.
            cursor (Any): The DBAPI cursor.
            statement (str): The SQL.
            *args (Any): Unused.
            **kwargs (Any): Unused.
        """
        statements.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", capture)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        yield client, statements
    event.remove(db.engine.sync_engine, "before_cursor_execute", capture)
    await db.drop_tables()
    await db.disconnect()


@pytest.mark.asyncio
async def test_results_respect_view_and_the_per_model_cap() -> None:
    async for client, _statements in _search_world():
        page = await client.get("/admin/search?q=dune")
        assert page.status_code == 200
        assert page.text.count("Dune part") == 2
        assert "See all" in page.text
        assert f"/admin/m/{SearchBook.__tablename__}/?q=dune" in page.text
        assert "Dune vault" not in page.text
        assert "Dune plain" not in page.text
        assert f"/admin/m/{SearchBook.__tablename__}/" in page.text


@pytest.mark.asyncio
async def test_blank_term_runs_no_query() -> None:
    async for client, statements in _search_world():
        statements.clear()
        page = await client.get("/admin/search?q=%20%20")
        assert page.status_code == 200
        assert "Type a term" in page.text
        touched = [
            sql
            for sql in statements
            if SearchBook.__tablename__ in sql or SearchVault.__tablename__ in sql
        ]
        assert touched == []


@pytest.mark.asyncio
async def test_header_box_shows_only_with_a_visible_searchable_model() -> None:
    async for client, _statements in _search_world():
        page = await client.get("/admin/")
        assert 'class="tempest-admin-header__search"' in page.text
        results = await client.get("/admin/search?q=dune")
        assert 'value="dune"' in results.text
    async for client, _statements in _search_world(searchable=False):
        page = await client.get("/admin/")
        assert 'class="tempest-admin-header__search"' not in page.text


@pytest.mark.asyncio
async def test_search_requires_a_session() -> None:
    async for client, _statements in _search_world():
        await client.post("/admin/logout", data={"csrf_token": "x"})
        client.cookies.clear()
        resp = await client.get("/admin/search?q=dune")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"


async def case_global_search(world: ScopeWorld) -> None:
    """The global search lists only rows inside each model's scope.

    Args:
        world (ScopeWorld): The signed-in world.
    """
    page = await world.client.get("/admin/search?q=ticket")
    assert page.status_code == 200
    assert "TicketInside" in page.text
    hidden("TicketOutside" not in page.text, "foreign ticket found by search")
    notes = await world.client.get("/admin/search?q=note")
    assert "NoteInside" in notes.text
    hidden("NoteOutside" not in notes.text, "foreign note found by search")


@pytest.fixture
async def database() -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a fresh in-memory database with the tables created.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    db = await new_database()
    yield db
    await db.drop_tables()
    await db.disconnect()


@pytest.mark.asyncio
async def test_global_search_honours_row_scope(
    database: AsyncDatabaseManager, tmp_path: Path
) -> None:
    async for world in open_world(database, tmp_path, scoped=True):
        await case_global_search(world)


@pytest.mark.asyncio
async def test_global_search_case_fails_without_the_scope(
    database: AsyncDatabaseManager, tmp_path: Path
) -> None:
    async for world in open_world(database, tmp_path, scoped=False):
        with pytest.raises(ScopeLeakError):
            await case_global_search(world)


def test_cap_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="search_results_per_model"):
        make_admin_router(
            AdminSite(title="x"),
            db=AsyncDatabaseManager("sqlite+aiosqlite:///:memory:"),
            auth_backend=UserModelAuthBackend(SearchUser),
            secret_key=SECRET,
            search_results_per_model=0,
        )


@pytest.mark.asyncio
async def test_custom_repository_is_not_bypassed() -> None:
    async for client, _statements in _search_world(
        book_repository=HidingBookRepository
    ):
        page = await client.get("/admin/search?q=dune")
        assert "Dune part 0" not in page.text
        assert page.text.count("Dune part") == 2
        assert "See all" not in page.text


@pytest.mark.asyncio
async def test_oversized_term_runs_no_query() -> None:
    from tempest_fastapi_sdk.admin import (
        ADMIN_SEARCH_MAX_QUERY_LENGTH,
        ADMIN_SEARCH_MAX_TERMS,
    )

    async for client, statements in _search_world():
        for term in (
            "d" * (ADMIN_SEARCH_MAX_QUERY_LENGTH + 1),
            " ".join(["dune"] * (ADMIN_SEARCH_MAX_TERMS + 1)),
        ):
            statements.clear()
            page = await client.get("/admin/search", params={"q": term})
            assert page.status_code == 400
            assert "too long" in page.text
            touched = [sql for sql in statements if SearchBook.__tablename__ in sql]
            assert touched == []
        at_limit = " ".join(["dune"] * ADMIN_SEARCH_MAX_TERMS)
        ok = await client.get("/admin/search", params={"q": at_limit})
        assert ok.status_code == 200
        assert "Dune part" in ok.text

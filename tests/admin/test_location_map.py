"""BRLocationMap: the admin page of where a model's rows are in Brazil."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Boolean
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminModel, AdminPermissionEnum, BRLocationMap
from tempest_fastapi_sdk.geo import BRLocationMixin
from tempest_fastapi_sdk.testing import admin_login

SECRET: str = "m" * 48
"""Admin session secret."""


class _MapUser(BRLocationMixin, BaseUserModel):
    """The panel's users, with a location."""

    __tablename__ = "location_map_users"

    paid: Mapped[bool] = mapped_column(Boolean, default=False)


class _Plain(BaseModel):
    """A model without the mixin."""

    __tablename__ = "location_map_plain"


@dataclass
class _Panel:
    """The panel and its database."""

    client: AsyncClient
    db: AsyncDatabaseManager


@asynccontextmanager
async def _open(
    tmp_path: Path,
    maps: Sequence[BRLocationMap],
    *,
    access_policy: Callable[[Any, Any, AdminPermissionEnum], bool] | None = None,
    register: bool = True,
) -> AsyncIterator[_Panel]:
    """Open a panel with the given maps, signed in, with users in three cities.

    Args:
        tmp_path (Path): Where the SQLite file lives.
        maps (Sequence[BRLocationMap]): The map pages.
        access_policy (Callable | None): The per-model policy.
        register (bool): Register ``_MapUser`` as a model admin.

    Yields:
        _Panel: The panel.
    """
    db = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'map.db'}")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        admin = _MapUser(email="ops@example.com", hashed_password="", is_admin=True)
        admin.set_password("hunter2")
        session.add(admin)
        for index, (city, state) in enumerate(
            [("São Paulo", "SP"), ("São Paulo", "SP"), ("Recife", "PE")]
        ):
            user = _MapUser(
                email=f"u{index}@example.com", hashed_password="", paid=index != 1
            )
            user.set_br_location(city, state)
            session.add(user)
        await session.commit()
    site = AdminSite(title="Admin", location_maps=list(maps))
    if register:
        site.register(AdminModel(_MapUser))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(_MapUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=access_policy,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "ops@example.com", "hunter2")
        yield _Panel(client=client, db=db)
    await db.drop_tables()
    await db.disconnect()


USERS_MAP: BRLocationMap = BRLocationMap(
    "Usuários por região", _MapUser, value_label="usuários"
)
"""The map every test opens."""


class TestPage:
    async def test_renders_map_counts_and_tables(self, tmp_path: Path) -> None:
        async with _open(tmp_path, [USERS_MAP]) as p:
            page = await p.client.get("/admin/maps/usuarios-por-regiao")
        assert page.status_code == 200
        text = page.text
        assert '<svg class="tempest-br-map"' in text
        assert 'data-uf="SP" d=' in text
        assert 'tempest-br-map__state tempest-br-map__state--l5" data-uf="SP"' in text
        assert "<title>São Paulo (SP): 2 usuários</title>" in text
        assert (
            '<td>Recife</td><td>PE</td><td class="tempest-admin-geo__num">1</td>'
            in text
        )
        assert '<span class="tempest-admin-stat__value">4</span>' in text
        assert '<span class="tempest-admin-stat__value">1</span>' in text

    async def test_sidebar_lists_the_map(self, tmp_path: Path) -> None:
        async with _open(tmp_path, [USERS_MAP]) as p:
            page = await p.client.get("/admin/")
        assert 'href="/admin/maps/usuarios-por-regiao"' in page.text
        assert ">Maps</span>" in page.text

    async def test_where_filters_the_counts(self, tmp_path: Path) -> None:
        paid_map = BRLocationMap(
            "Pagantes", _MapUser, where=lambda: _MapUser.paid.is_(True)
        )
        async with _open(tmp_path, [paid_map]) as p:
            page = await p.client.get("/admin/maps/pagantes")
        assert "<title>São Paulo (SP): 1 users</title>" in page.text

    async def test_unknown_slug_is_404(self, tmp_path: Path) -> None:
        async with _open(tmp_path, [USERS_MAP]) as p:
            page = await p.client.get("/admin/maps/nope")
        assert page.status_code == 404

    async def test_unregistered_model_still_renders(self, tmp_path: Path) -> None:
        async with _open(tmp_path, [USERS_MAP], register=False) as p:
            page = await p.client.get("/admin/maps/usuarios-por-regiao")
        assert page.status_code == 200

    async def test_count_failure_renders_the_error(self, tmp_path: Path) -> None:
        def broken() -> Any:
            raise RuntimeError("boom")

        failing = BRLocationMap("Quebrado", _MapUser, where=broken)
        async with _open(tmp_path, [failing]) as p:
            page = await p.client.get("/admin/maps/quebrado")
        assert page.status_code == 200
        assert "This map could not be counted" in page.text


class TestPermission:
    async def test_view_denied_is_403_and_hidden(self, tmp_path: Path) -> None:
        def no_users(principal: Any, admin: Any, action: AdminPermissionEnum) -> bool:
            return admin.model is not _MapUser

        async with _open(tmp_path, [USERS_MAP], access_policy=no_users) as p:
            page = await p.client.get("/admin/maps/usuarios-por-regiao")
            index = await p.client.get("/admin/")
        assert page.status_code == 403
        assert "/admin/maps/" not in index.text


class TestConfig:
    def test_slug_drops_accents(self) -> None:
        assert USERS_MAP.slug == "usuarios-por-regiao"

    def test_model_without_the_mixin_is_refused(self) -> None:
        with pytest.raises(TypeError, match="mix BRLocationMixin in"):
            BRLocationMap("Plain", _Plain)  # type: ignore[arg-type]

    def test_options_are_keyword_only(self) -> None:
        with pytest.raises(TypeError, match="positional"):
            BRLocationMap("Users", _MapUser, None, 5)  # type: ignore[misc]

    def test_city_limit_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="city_limit"):
            BRLocationMap("Users", _MapUser, city_limit=0)

    def test_repeated_slug_is_refused(self) -> None:
        with pytest.raises(ValueError, match="share the URL slug"):
            AdminSite(
                location_maps=[
                    USERS_MAP,
                    BRLocationMap("Usuarios por regiao", _MapUser),
                ]
            )

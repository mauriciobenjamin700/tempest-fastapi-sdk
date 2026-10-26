"""The list search box: words matched by substring across the search fields.

#635: the term used to become ``filters[field] = q`` for every search
field — exact equality, ANDed across fields — so ``cliente1`` found nothing
among ``cliente1@mail.com`` and a second search field made every search
miss. Each case below fails on that code.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.testing import admin_login

SECRET: str = "x" * 48
SLUG: str = "admin_search_customer"


class SearchUser(BaseUserModel):
    """Signs in to the panel."""

    __tablename__ = "admin_search_users"


class SearchCustomer(BaseModel):
    """A row with two searchable text columns."""

    __tablename__ = SLUG
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(64), nullable=False)


CUSTOMERS: list[tuple[str, str]] = [
    ("Ana Souza", "ana@mail.com"),
    ("Bruno Lima", "cliente1@mail.com"),
    ("Carla Dias", "cliente10@mail.com"),
    ("Davi 100% Real", "davi@mail.com"),
    ("Eva_Rocha", "eva@mail.com"),
]


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = SearchUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add_all(
            [SearchCustomer(name=name, email=email) for name, email in CUSTOMERS]
        )
        await session.commit()

    site = AdminSite(title="Search")
    site.register(
        AdminModel(
            model=SearchCustomer,
            list_display=[SearchCustomer.name, SearchCustomer.email],
            search_fields=[SearchCustomer.name, SearchCustomer.email],
        )
    )
    application = FastAPI()
    application.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(SearchUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield application
    await db.drop_tables()
    await db.disconnect()


@asynccontextmanager
async def signed_in(application: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        await admin_login(client, "root@example.com", "hunter2")
        yield client


async def exported_names(application: FastAPI, q: str) -> list[str]:
    """Export the list as CSV under a search and read the names back.

    Args:
        application (FastAPI): The app.
        q (str): The search term.

    Returns:
        list[str]: The ``name`` column, sorted.
    """
    async with signed_in(application) as client:
        response = await client.get(f"/admin/m/{SLUG}/export.csv", params={"q": q})
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    return sorted(row["name"] for row in rows)


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("cliente1", ["Bruno Lima", "Carla Dias"]),
        ("CLIENTE10", ["Carla Dias"]),
        ("souza", ["Ana Souza"]),
        ("bruno mail", ["Bruno Lima"]),
        ("bruno davi", []),
        ("100%", ["Davi 100% Real"]),
        ("a_s", []),
        ("_", ["Eva_Rocha"]),
        ("   ", sorted(name for name, _ in CUSTOMERS)),
    ],
)
async def test_search(app: FastAPI, q: str, expected: list[str]) -> None:
    """Substring, case-insensitive, any field, every word, literal wildcards."""
    assert await exported_names(app, q) == expected


async def test_the_list_counts_what_the_search_finds(app: FastAPI) -> None:
    async with signed_in(app) as client:
        page = await client.get(f"/admin/m/{SLUG}/", params={"q": "cliente1"})

    assert page.status_code == 200
    assert "2 records" in page.text
    assert "Bruno Lima" in page.text
    assert "Ana Souza" not in page.text

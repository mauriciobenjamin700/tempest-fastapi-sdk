"""``AdminSite(locale=...)``: the panel's own text in English or Portuguese."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

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
from tempest_fastapi_sdk.admin import admin_plural, admin_text
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login

SECRET = "l" * 48


class LocaleUser(BaseUserModel):
    """Operator of the locale tests."""

    __tablename__ = "admin_locale_users"


class LocaleItem(BaseModel):
    """A row listed and edited in both languages."""

    __tablename__ = "admin_locale_items"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


ITEMS = f"/admin/m/{LocaleItem.__tablename__}"


async def _client(locale: str | None) -> AsyncIterator[AsyncClient]:
    """Mount a site in ``locale`` over one row and sign in.

    Args:
        locale (str | None): The site locale, or ``None`` for the default.

    Yields:
        AsyncClient: The signed-in client.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = LocaleUser(email="op@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add_all([user, LocaleItem(name="Widget", created_at=datetime.now(UTC))])
        await session.commit()
    site = (
        AdminSite(title="Painel")
        if locale is None
        else AdminSite(title="Painel", locale=locale)  # type: ignore[arg-type]
    )
    site.register(AdminModel(LocaleItem, list_filter=["created_at", "is_active"]))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(LocaleUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    await db.drop_tables()
    await db.disconnect()


@pytest.fixture
async def portuguese() -> AsyncIterator[AsyncClient]:
    """Client of a ``pt-BR`` site.

    Yields:
        AsyncClient: The client, not signed in.
    """
    async for client in _client("pt-BR"):
        yield client


@pytest.fixture
async def default() -> AsyncIterator[AsyncClient]:
    """Client of a site built without ``locale=``.

    Yields:
        AsyncClient: The client, not signed in.
    """
    async for client in _client(None):
        yield client


@pytest.mark.asyncio
async def test_portuguese_login_and_list(portuguese: AsyncClient) -> None:
    login = await portuguese.get("/admin/login")
    assert '<html lang="pt-BR">' in login.text
    assert "Entrar" in login.text
    assert "Senha" in login.text
    assert "Administração do site" in login.text
    await admin_login(portuguese, "op@example.com", "hunter2")
    page = await portuguese.get(f"{ITEMS}/")
    assert page.status_code == 200
    for text in (
        "1 registro",
        "Exportar CSV",
        "Aplicar",
        "— qualquer —",
        "Hoje",
        "7 dias",
        "Mês passado",
        "Sim",
        "Não",
        "Painel",
        "Sair",
        "Ver</a>",
        "Excluir",
    ):
        assert text in page.text, text
    assert "Export CSV" not in page.text
    assert "Logout" not in page.text


@pytest.mark.asyncio
async def test_portuguese_form_errors(portuguese: AsyncClient) -> None:
    await admin_login(portuguese, "op@example.com", "hunter2")
    form = await portuguese.get(f"{ITEMS}/new")
    assert "Novo: Locale Item" in form.text
    assert "Cancelar" in form.text
    refused = await portuguese.post(
        f"{ITEMS}/new", data={"csrf_token": admin_csrf_token(form.text), "name": ""}
    )
    assert refused.status_code == 400
    assert "Este campo é obrigatório." in refused.text


@pytest.mark.asyncio
async def test_default_locale_keeps_the_english_panel(default: AsyncClient) -> None:
    login = await default.get("/admin/login")
    assert '<html lang="en">' in login.text
    assert "Sign in" in login.text
    assert "Site administration" in login.text
    await admin_login(default, "op@example.com", "hunter2")
    page = await default.get(f"{ITEMS}/")
    for text in ("1 record", "Export CSV", "Apply", "— any —", "Today", "Logout"):
        assert text in page.text, text


def test_unknown_locale_is_refused() -> None:
    with pytest.raises(ValueError, match="locale 'fr' is not one of"):
        AdminSite(locale="fr")  # type: ignore[arg-type]


def test_explicit_subtitle_wins_over_the_catalog() -> None:
    assert AdminSite(locale="pt-BR").index_subtitle == "Administração do site"
    assert AdminSite(locale="pt-BR", index_subtitle="Backoffice").index_subtitle == (
        "Backoffice"
    )


def test_helpers_fall_back_and_pluralize() -> None:
    assert admin_text("pt-BR", "list.apply") == "Aplicar"
    assert admin_text("en", "list.new", model="Order") == "New Order"
    assert admin_text("pt-BR", "list.new", model="Pedido") == "Novo: Pedido"
    assert admin_text("pt-BR", "no.such.key") == "no.such.key"
    assert admin_plural("pt-BR", "list.records", 1) == "1 registro"
    assert admin_plural("pt-BR", "list.records", 3) == "3 registros"


@pytest.mark.asyncio
async def test_backend_messages_keep_their_own_language(
    portuguese: AsyncClient,
) -> None:
    refused = await admin_login(portuguese, "op@example.com", "wrong")
    assert "Invalid credentials" in refused.text
    assert "Entrar" in refused.text

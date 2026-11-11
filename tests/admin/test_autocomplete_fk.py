"""End-to-end tests for admin autocomplete FK fields."""

from __future__ import annotations

from collections.abc import AsyncIterator
from html.parser import HTMLParser
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import ForeignKey, String, Uuid
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
from tempest_fastapi_sdk import admin as admin_package
from tempest_fastapi_sdk.testing import admin_login


class AcUser(BaseUserModel):
    __tablename__ = "admin_ac_users"


class Company(BaseModel):
    __tablename__ = "admin_ac_company"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Employee(BaseModel):
    __tablename__ = "admin_ac_employee"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    company_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("admin_ac_company.id"), nullable=False
    )


SECRET = "x" * 48


@pytest.fixture
async def app_ac() -> AsyncIterator[tuple[FastAPI, str, str]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    async with db.get_session_context() as session:
        user = AcUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        acme = Company(name="Acme Corp")
        globex = Company(name="Globex")
        session.add_all([acme, globex])
        await session.commit()
        await session.refresh(acme)
        await session.refresh(globex)
        acme_id = str(acme.id)
        emp = Employee(name="Ada", company_id=acme.id)
        session.add(emp)
        await session.commit()
        await session.refresh(emp)
        emp_id = str(emp.id)

    site = AdminSite(title="AC Admin")
    site.register(AdminModel(model=Company, search_fields=[Company.name]))
    site.register(AdminModel(model=Employee, autocomplete_fields=[Employee.company_id]))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(AcUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, acme_id, emp_id
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient) -> None:
    await admin_login(client, "root@example.com", "hunter2")


@pytest.mark.asyncio
async def test_new_form_renders_autocomplete_widget(
    app_ac: tuple[FastAPI, str, str],
) -> None:
    app, _acme_id, _emp_id = app_ac
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{Employee.__tablename__}/new")

    assert response.status_code == 200
    body = response.text
    # Autocomplete widget, not a full <select> of companies.
    assert "tempest-admin-ac" in body
    assert "/autocomplete/company_id" in body
    # The company names are NOT pre-loaded into the form.
    assert "Acme Corp" not in body


@pytest.mark.asyncio
async def test_autocomplete_endpoint_filters(
    app_ac: tuple[FastAPI, str, str],
) -> None:
    app, acme_id, _emp_id = app_ac
    async with _client(app) as client:
        await _login(client)
        response = await client.get(
            f"/admin/m/{Employee.__tablename__}/autocomplete/company_id?q=acme"
        )

    assert response.status_code == 200
    body = response.text
    assert "Acme Corp" in body
    assert acme_id in body
    # Globex does not match "acme".
    assert "Globex" not in body


@pytest.mark.asyncio
async def test_autocomplete_unknown_field_404(
    app_ac: tuple[FastAPI, str, str],
) -> None:
    app, _acme_id, _emp_id = app_ac
    async with _client(app) as client:
        await _login(client)
        response = await client.get(
            f"/admin/m/{Employee.__tablename__}/autocomplete/name?q=x"
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_edit_form_prefills_display_label(
    app_ac: tuple[FastAPI, str, str],
) -> None:
    app, _acme_id, emp_id = app_ac
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{Employee.__tablename__}/{emp_id}/edit")
    assert response.status_code == 200
    # The current company's label is shown in the search box.
    assert 'value="Acme Corp"' in response.text


class _OnclickParser(HTMLParser):
    """Collect the ``onclick`` attribute of each autocomplete option."""

    def __init__(self) -> None:
        """Start with no attributes collected."""
        super().__init__()
        self.onclicks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Keep the ``onclick`` of every ``<button>``.

        Args:
            tag (str): The tag name.
            attrs (list[tuple[str, str | None]]): Its attributes.
        """
        if tag == "button":
            self.onclicks.extend(
                value or "" for name, value in attrs if name == "onclick"
            )


def _onclicks(html: str) -> list[str]:
    """Parse ``html`` as a browser would and return each option's handler.

    Args:
        html (str): The autocomplete fragment.

    Returns:
        list[str]: The ``onclick`` values, as the browser sees them.
    """
    parser = _OnclickParser()
    parser.feed(html)
    return parser.onclicks


@pytest.mark.asyncio
async def test_choosing_an_option_sets_the_hidden_value(
    app_ac: tuple[FastAPI, str, str],
) -> None:
    """Cenário: o fragmento que o endpoint devolve, lido como HTML.

    Esperado: o ``onclick`` inteiro chega ao browser, com o id e o rótulo.
    Antes, o ``tojson`` (aspas duplas) dentro de ``onclick="..."`` fechava o
    atributo no primeiro valor: o handler virava
    ``...__value').value=`` e a seleção nunca gravava o id.
    """
    app, acme_id, _emp_id = app_ac
    async with _client(app) as client:
        await _login(client)
        response = await client.get(
            f"/admin/m/{Employee.__tablename__}/autocomplete/company_id?q=acme"
        )
    (handler,) = _onclicks(response.text)
    assert f'.value="{acme_id}"' in handler
    assert '.value="Acme Corp"' in handler
    assert handler.endswith('innerHTML="";')


def test_a_label_with_quotes_stays_inside_the_handler() -> None:
    """Cenário: rótulo com apóstrofo, aspas e ``&``.

    Esperado: o ``onclick`` continua inteiro, com o apóstrofo escapado como
    ``\\u0027`` pelo ``tojson``.
    """
    environment = Environment(
        loader=FileSystemLoader(str(Path(admin_package.__file__).parent / "templates")),
        autoescape=True,
    )
    html = environment.get_template("autocomplete.html").render(
        results=[("1", 'O\'Hara "Q" & Co')]
    )
    (handler,) = _onclicks(html)
    assert handler.endswith('innerHTML="";')
    assert "\\u0027" in handler

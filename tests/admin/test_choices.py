"""Tests for ``AdminModel(choices=...)``: a String column shown as a select."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseStrEnum,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin.forms import build_form_fields
from tempest_fastapi_sdk.testing import admin_login
from tempest_fastapi_sdk.wallet.models import make_wallet_entry_model
from tempest_fastapi_sdk.wallet.schemas import WalletEntryKindEnum


class ChoiceUser(BaseUserModel):
    __tablename__ = "admin_choice_users"


class ShipmentStatusEnum(BaseStrEnum):
    """Statuses kept as text in the column."""

    PENDING = "PENDING"
    SHIPPED = "SHIPPED"


class ShipmentModel(BaseModel):
    __tablename__ = "admin_choice_shipments"
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    carrier: Mapped[str | None] = mapped_column(String(32), nullable=True)


SECRET = "x" * 48
_SLUG = ShipmentModel.__tablename__


def _admin() -> AdminModel[ShipmentModel]:
    """Return the shipment admin with both kinds of choices.

    Returns:
        AdminModel[ShipmentModel]: The admin.
    """
    return AdminModel(
        ShipmentModel,
        choices={
            ShipmentModel.status: ShipmentStatusEnum,
            "carrier": [("correios", "Correios"), "jadlog"],
        },
    )


@pytest.fixture
async def app() -> AsyncIterator[tuple[FastAPI, AsyncDatabaseManager]]:
    """An admin with the shipment model.

    Yields:
        tuple[FastAPI, AsyncDatabaseManager]: The app and its database.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = ChoiceUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
    site = AdminSite(title="Choices")
    site.register(_admin())
    application = FastAPI()
    application.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ChoiceUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
        )
    )
    yield application, db
    await db.drop_tables()
    await db.disconnect()


def _client(application: FastAPI) -> AsyncClient:
    """Return a client for ``application``.

    Args:
        application (FastAPI): The app.

    Returns:
        AsyncClient: The client.
    """
    return AsyncClient(transport=ASGITransport(app=application), base_url="http://test")


class TestDeclaration:
    def test_enum_list_and_pairs_become_options(self) -> None:
        """Cenário: ``Enum``, string solta e par ``(valor, rótulo)``.

        Esperado: opções na ordem declarada, rótulo do enum humanizado.
        """
        admin = _admin()
        assert admin.choices == {
            "status": [("PENDING", "Pending"), ("SHIPPED", "Shipped")],
            "carrier": [("correios", "Correios"), ("jadlog", "jadlog")],
        }

    def test_unknown_column_is_refused(self) -> None:
        """Cenário: ``choices`` com nome que não é coluna.

        Esperado: ``ValueError`` nomeando o campo.
        """
        with pytest.raises(ValueError, match="stauts"):
            AdminModel(ShipmentModel, choices={"stauts": ShipmentStatusEnum})

    def test_empty_entry_is_refused(self) -> None:
        """Cenário: lista vazia.

        Esperado: ``ValueError``.
        """
        with pytest.raises(ValueError, match="at least one"):
            AdminModel(ShipmentModel, choices={"carrier": []})

    def test_the_form_field_is_a_select(self) -> None:
        """Cenário: os controles do form.

        Esperado: as duas colunas String viram ``select`` com as opções.
        """
        fields = {field.name: field for field in build_form_fields(_admin())}
        assert fields["status"].widget == "select"
        assert fields["status"].options == [
            ("PENDING", "Pending"),
            ("SHIPPED", "Shipped"),
        ]
        assert fields["carrier"].widget == "select"

    def test_the_wallet_kind_can_be_restricted(self) -> None:
        """Cenário: o ``kind`` da carteira (``String(64)``) com o enum do SDK.

        Esperado: select com os valores do ``WalletEntryKindEnum``.
        """
        wallet_entry = make_wallet_entry_model(
            user_table=ChoiceUser.__tablename__,
            tablename="admin_choice_wallet_entries",
            class_name="ChoiceWalletEntryModel",
        )
        admin = AdminModel(wallet_entry, choices={"kind": WalletEntryKindEnum})
        kind = next(field for field in build_form_fields(admin) if field.name == "kind")
        assert kind.widget == "select"
        assert [value for value, _ in kind.options] == [
            member.value for member in WalletEntryKindEnum
        ]


@pytest.mark.asyncio
class TestSubmission:
    async def test_the_form_renders_a_select(
        self, app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: o form de criação no browser.

        Esperado: ``<select name="status">`` com as opções, sem input de texto.
        """
        application, _ = app
        async with _client(application) as client:
            await admin_login(client, "root@example.com", "hunter2")
            page = await client.get(f"/admin/m/{_SLUG}/new")
        assert '<select name="status"' in page.text
        assert '<option value="SHIPPED"' in page.text
        assert 'type="text" name="status"' not in page.text

    async def test_a_value_outside_the_list_is_refused(
        self, app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: POST com valor forjado fora da lista.

        Esperado: ``400`` com o erro do campo e nada gravado.
        """
        application, db = app
        async with _client(application) as client:
            await admin_login(client, "root@example.com", "hunter2")
            page = await client.get(f"/admin/m/{_SLUG}/new")
            token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
            assert token is not None
            response = await client.post(
                f"/admin/m/{_SLUG}/new",
                data={"csrf_token": token.group(1), "status": "LOST"},
                follow_redirects=False,
            )
        assert response.status_code == 400
        assert "Choose one of the listed values for Status." in response.text
        async with db.get_session_context() as session:
            assert (await session.execute(select(ShipmentModel))).scalars().all() == []

    async def test_a_listed_value_is_saved(
        self, app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: POST com valor da lista e o opcional vazio.

        Esperado: gravado como texto; o opcional vazio vira ``None``.
        """
        application, db = app
        async with _client(application) as client:
            await admin_login(client, "root@example.com", "hunter2")
            page = await client.get(f"/admin/m/{_SLUG}/new")
            token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
            assert token is not None
            response = await client.post(
                f"/admin/m/{_SLUG}/new",
                data={"csrf_token": token.group(1), "status": "SHIPPED", "carrier": ""},
                follow_redirects=False,
            )
        assert response.status_code == 303
        async with db.get_session_context() as session:
            (row,) = (await session.execute(select(ShipmentModel))).scalars().all()
            assert (row.status, row.carrier) == ("SHIPPED", None)

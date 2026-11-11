"""Tests for ``AdminModel(create_handler=...)`` and single-use submissions (#739)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import BaseModel as PydanticModel
from pydantic import Field
from sqlalchemy import ForeignKey, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminCreateHandler,
    AdminModel,
    AdminSecret,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseStrEnum,
    BaseUserModel,
    ConflictException,
    UserModelAuthBackend,
    admin_action,
    make_admin_router,
)
from tempest_fastapi_sdk.testing import admin_login


class KeyUser(BaseUserModel):
    __tablename__ = "admin_key_users"


class ApiKeyModel(BaseModel):
    __tablename__ = "admin_api_keys"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_key_users.id"), nullable=True
    )


class KeyScopeEnum(BaseStrEnum):
    """What a key may do."""

    READ = "read"
    WRITE = "write"


class ApiKeyCreateSchema(PydanticModel):
    """What the operator chooses when issuing a key."""

    name: str = Field(title="Key name", min_length=3)
    scope: KeyScopeEnum = Field(default=KeyScopeEnum.READ, title="Scope")
    expires_in_days: int | None = Field(default=None, title="Expires in (days)")
    user_id: UUID | None = None


SECRET = "x" * 48
KEY_VALUE = "tsk_live_plaintext_shown_once"
_SLUG = ApiKeyModel.__tablename__
CALLS: list[str] = []


async def issue_api_key(
    data: ApiKeyCreateSchema, ctx: AdminActionContext
) -> AdminActionResult:
    """Store the hash and return the plaintext once.

    Args:
        data (ApiKeyCreateSchema): The validated form.
        ctx (AdminActionContext): The admin context.

    Returns:
        AdminActionResult: The secret to show.

    Raises:
        ConflictException: For the name ``taken``.
    """
    CALLS.append(data.name)
    if data.name == "taken":
        raise ConflictException("A key with this name already exists.")
    ctx.db_session.add(
        ApiKeyModel(
            name=data.name,
            key_hash=hashlib.sha256(KEY_VALUE.encode()).hexdigest(),
            user_id=data.user_id,
        )
    )
    return AdminActionResult("Key issued", secrets=[AdminSecret(data.name, KEY_VALUE)])


async def note_only(
    data: ApiKeyCreateSchema, ctx: AdminActionContext
) -> AdminActionResult:
    """Return a message without secrets.

    Args:
        data (ApiKeyCreateSchema): The validated form.
        ctx (AdminActionContext): The admin context.

    Returns:
        AdminActionResult: A flash message.
    """
    CALLS.append(data.name)
    return AdminActionResult(f"Noted {data.name}")


@admin_action(label="Rotate")
async def rotate(ctx: AdminActionContext) -> AdminActionResult:
    """Return a secret from a bulk action.

    Args:
        ctx (AdminActionContext): The admin context.

    Returns:
        AdminActionResult: The secret to show.
    """
    CALLS.append("rotate")
    return AdminActionResult("Rotated", secrets=[AdminSecret("new", KEY_VALUE)])


async def _app(handler: AdminCreateHandler) -> tuple[FastAPI, AsyncDatabaseManager]:
    """Build an admin with the API key model behind ``handler``.

    Args:
        handler (AdminCreateHandler): The create flow.

    Returns:
        tuple[FastAPI, AsyncDatabaseManager]: The app and its database.
    """
    CALLS.clear()
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = KeyUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add(ApiKeyModel(name="seed", key_hash="0" * 64))
        await session.commit()
    site = AdminSite(title="Keys")
    site.register(AdminModel(KeyUser))
    site.register(
        AdminModel(
            ApiKeyModel,
            create_handler=handler,
            autocomplete_fields=["user_id"],
            actions=[rotate],
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(KeyUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
        )
    )
    return app, db


@pytest.fixture
async def keys_app() -> AsyncIterator[tuple[FastAPI, AsyncDatabaseManager]]:
    """The admin with the secret-returning handler.

    Yields:
        tuple[FastAPI, AsyncDatabaseManager]: The app and its database.
    """
    app, db = await _app(
        AdminCreateHandler(
            schema=ApiKeyCreateSchema, handler=issue_api_key, label="New API key"
        )
    )
    yield app, db
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    """Return a client for ``app``.

    Args:
        app (FastAPI): The app.

    Returns:
        AsyncClient: The client.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _hidden(html: str, name: str) -> str:
    """Read a hidden input's value from a page.

    Args:
        html (str): The page.
        name (str): The input name.

    Returns:
        str: The value.
    """
    match = re.search(rf'name="{name}" value="([^"]+)"', html)
    assert match is not None, name
    return match.group(1)


async def _submit(
    client: AsyncClient, form: dict[str, str], page: str | None = None
) -> tuple[Response, dict[str, str]]:
    """Open the create form and post ``form`` with its tokens.

    Args:
        client (AsyncClient): A signed-in client.
        form (dict[str, str]): The fields to post.
        page (str | None): A form page already rendered, to reuse its tokens.

    Returns:
        tuple[Response, dict[str, str]]: The response and the exact data
        posted, so a test can resubmit it.
    """
    html = (
        page if page is not None else (await client.get(f"/admin/m/{_SLUG}/new")).text
    )
    data = {
        "csrf_token": _hidden(html, "csrf_token"),
        "submission_nonce": _hidden(html, "submission_nonce"),
        **form,
    }
    response = await client.post(
        f"/admin/m/{_SLUG}/new", data=data, follow_redirects=False
    )
    return response, data


async def _count(db: AsyncDatabaseManager) -> int:
    """Count the stored API keys.

    Args:
        db (AsyncDatabaseManager): The database.

    Returns:
        int: The row count.
    """
    async with db.get_session_context() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(ApiKeyModel))
            ).scalar_one()
        )


class TestDeclaration:
    def test_handler_and_column_form_are_exclusive(self) -> None:
        """Cenário: ``create_handler`` com ``can_create=True``.

        Esperado: ``ValueError`` na construção.
        """
        with pytest.raises(ValueError, match="create_handler"):
            AdminModel(
                ApiKeyModel,
                can_create=True,
                create_handler=AdminCreateHandler(
                    schema=ApiKeyCreateSchema, handler=issue_api_key
                ),
            )

    def test_the_handler_turns_the_column_form_off(self) -> None:
        """Cenário: ``create_handler`` sem ``can_create``.

        Esperado: ``can_create`` é ``False`` e ``creates`` é ``True``.
        """
        admin = AdminModel(
            ApiKeyModel,
            create_handler=AdminCreateHandler(
                schema=ApiKeyCreateSchema, handler=issue_api_key
            ),
        )
        assert (admin.can_create, admin.creates) == (False, True)


@pytest.mark.asyncio
class TestCreateForm:
    async def test_the_form_comes_from_the_schema_not_the_columns(
        self, keys_app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: a lista e o form de criação.

        Esperado: o botão leva o rótulo do handler; o form tem os campos do
        schema (com o enum como select e o FK como autocomplete) e nenhum
        campo de coluna como ``key_hash``.
        """
        app, _ = keys_app
        async with _client(app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            listing = await client.get(f"/admin/m/{_SLUG}/")
            page = await client.get(f"/admin/m/{_SLUG}/new")
        assert "+ New API key" in listing.text
        assert page.status_code == 200
        assert "<h1>New API key</h1>" in page.text
        for name in ("name", "scope", "expires_in_days"):
            assert f'name="{name}"' in page.text
        assert 'name="key_hash"' not in page.text
        assert '<option value="write"' in page.text
        assert f"/admin/m/{_SLUG}/autocomplete/user_id" in page.text
        assert 'name="submission_nonce"' in page.text

    async def test_the_secret_is_shown_once_on_the_post(
        self, keys_app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: submissão válida.

        Esperado: ``200`` no próprio ``POST`` com o segredo e ``no-store``;
        a linha gravada só tem o hash.
        """
        app, db = keys_app
        async with _client(app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            response, _ = await _submit(client, {"name": "ci-deploy", "scope": "write"})
        assert response.status_code == 200
        assert KEY_VALUE in response.text
        assert "Reloading will not show it again." in response.text
        assert response.headers["cache-control"] == "no-store"
        assert await _count(db) == 2
        assert CALLS == ["ci-deploy"]

    async def test_a_reload_does_not_call_the_handler_again(
        self, keys_app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: o mesmo ``POST`` reenviado, como o reload faz.

        Esperado: ``303`` para a lista com aviso, sem segredo, sem segunda
        linha e sem segunda chamada.
        """
        app, db = keys_app
        async with _client(app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            _, posted = await _submit(client, {"name": "ci-deploy"})
            again = await client.post(
                f"/admin/m/{_SLUG}/new", data=posted, follow_redirects=False
            )
        assert again.status_code == 303
        assert "already+submitted" in again.headers["location"]
        assert KEY_VALUE not in again.text
        assert CALLS == ["ci-deploy"]
        assert await _count(db) == 2

    async def test_a_validation_error_keeps_what_was_typed(
        self, keys_app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: nome curto demais e dias que não são número.

        Esperado: ``400`` com o erro de cada campo e o que foi digitado; o
        handler não roda e o nonce não é gasto — a submissão corrigida com
        o mesmo form passa.
        """
        app, _ = keys_app
        async with _client(app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            page = (await client.get(f"/admin/m/{_SLUG}/new")).text
            bad, _ = await _submit(
                client, {"name": "ab", "expires_in_days": "soon"}, page=page
            )
            fixed, _ = await _submit(client, {"name": "abc"}, page=page)
        assert bad.status_code == 400
        assert 'value="ab"' in bad.text
        assert 'value="soon"' in bad.text
        assert "at least 3 characters" in bad.text
        assert "valid integer" in bad.text
        assert fixed.status_code == 200
        assert CALLS == ["abc"]

    async def test_an_app_exception_re_renders_with_its_message(
        self, keys_app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: o handler levanta ``ConflictException``.

        Esperado: ``400`` com a mensagem, o valor digitado e nada gravado.
        """
        app, db = keys_app
        async with _client(app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            response, _ = await _submit(client, {"name": "taken"})
        assert response.status_code == 400
        assert "A key with this name already exists." in response.text
        assert 'value="taken"' in response.text
        assert "root@example.com" in response.text
        assert await _count(db) == 1

    async def test_a_result_without_secrets_redirects_with_the_message(
        self,
    ) -> None:
        """Cenário: handler que devolve só mensagem.

        Esperado: ``303`` para a lista com o flash.
        """
        app, db = await _app(
            AdminCreateHandler(schema=ApiKeyCreateSchema, handler=note_only)
        )
        try:
            async with _client(app) as client:
                await admin_login(client, "root@example.com", "hunter2")
                response, _ = await _submit(client, {"name": "memo"})
        finally:
            await db.drop_tables()
            await db.disconnect()
        assert response.status_code == 303
        assert "Noted+memo" in response.headers["location"]

    async def test_a_post_without_nonce_is_refused(
        self, keys_app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: ``POST`` válido sem ``submission_nonce``.

        Esperado: ``400`` e o handler não roda.
        """
        app, _ = keys_app
        async with _client(app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            page = (await client.get(f"/admin/m/{_SLUG}/new")).text
            response = await client.post(
                f"/admin/m/{_SLUG}/new",
                data={"csrf_token": _hidden(page, "csrf_token"), "name": "ci"},
                follow_redirects=False,
            )
        assert response.status_code == 400
        assert CALLS == []


@pytest.mark.asyncio
class TestBulkActionReload:
    async def test_a_reloaded_bulk_action_with_secrets_runs_once(
        self, keys_app: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        """Cenário: ação em lote que devolve segredo, reenviada.

        Esperado: a primeira mostra o segredo; a segunda vira ``303`` com
        aviso e a ação roda uma vez só.
        """
        app, _ = keys_app
        async with _client(app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            page = (await client.get(f"/admin/m/{_SLUG}/")).text
            data = {
                "csrf_token": _hidden(page, "csrf_token"),
                "submission_nonce": _hidden(page, "submission_nonce"),
                "action": "custom:rotate",
                "ids": re.findall(r'name="ids" value="([^"]+)"', page)[:1],
            }
            first = await client.post(
                f"/admin/m/{_SLUG}/bulk", data=data, follow_redirects=False
            )
            second = await client.post(
                f"/admin/m/{_SLUG}/bulk", data=data, follow_redirects=False
            )
        assert first.status_code == 200
        assert KEY_VALUE in first.text
        assert second.status_code == 303
        assert CALLS == ["rotate"]

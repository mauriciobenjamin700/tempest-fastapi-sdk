"""Custom admin actions: commit, per-action permission and input form.

#784: an action that only changes the session must be committed by the
panel. #783: an ``action_policy`` grants one action without ``EDIT`` on the
model. #782: an action with a ``schema`` asks the operator for input first.
Every test goes through the real panel over a SQLite file, so the panel's
session and the check that reads the row back are two connections.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel as PydanticModel
from pydantic import Field
from sqlalchemy import Boolean, String, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    AdminAction,
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    AdminPermissionEnum,
    admin_action,
)
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.testing import admin_login

SECRET: str = "z" * 48
"""Admin session secret."""

SLUG: str = "custom_flow_items"
"""The model's slug in the panel."""


class _FlowUser(BaseUserModel):
    __tablename__ = "custom_flow_users"


class _FlowItem(BaseModel):
    """A row the actions flip."""

    __tablename__ = "custom_flow_items"

    name: Mapped[str] = mapped_column(String(32))
    flag: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


@admin_action(label="Marcar")
async def mark(ctx: AdminActionContext) -> AdminActionResult:
    """Flip ``flag`` through the session only, with no commit of its own."""
    for row_id in ctx.ids:
        row = await ctx.db_session.get(_FlowItem, row_id)
        assert row is not None
        row.flag = True
    return AdminActionResult(f"{len(ctx.ids)} marcado(s)")


@admin_action(label="Marcar e falhar")
async def mark_then_fail(ctx: AdminActionContext) -> AdminActionResult:
    """Change the row, then raise: nothing may be written."""
    row = await ctx.db_session.get(_FlowItem, ctx.ids[0])
    assert row is not None
    row.flag = True
    raise RuntimeError("action failed")


@admin_action(label="Desmarcar")
async def unmark(ctx: AdminActionContext) -> AdminActionResult:
    """A second action, for per-action permission."""
    for row_id in ctx.ids:
        row = await ctx.db_session.get(_FlowItem, row_id)
        assert row is not None
        row.flag = False
    return AdminActionResult("desmarcado(s)")


class _ReasonSchema(PydanticModel):
    """The input the "with reason" action asks for."""

    reason: str = Field(min_length=10, max_length=500)


CALLS: list[tuple[str, list[str]]] = []
"""``(reason, ids)`` the "with reason" action ran with, in order."""


@admin_action(label="Anotar com motivo", schema=_ReasonSchema)
async def annotate(data: _ReasonSchema, ctx: AdminActionContext) -> AdminActionResult:
    """Write the reason into each row; ``"recusar..."`` raises an app error."""
    if data.reason.startswith("recusar"):
        raise ValidationException("motivo recusado pela regra do serviço")
    CALLS.append((data.reason, [str(row_id) for row_id in ctx.ids]))
    for row_id in ctx.ids:
        row = await ctx.db_session.get(_FlowItem, row_id)
        assert row is not None
        row.note = data.reason
    return AdminActionResult(f"anotado(s): {len(ctx.ids)}")


@dataclass
class _Panel:
    """The panel and its database."""

    client: AsyncClient
    db: AsyncDatabaseManager


@asynccontextmanager
async def _open(
    tmp_path: Path,
    actions: Sequence[Callable[..., Any]],
    *,
    can_edit: bool = True,
    access_policy: Callable[[Any, Any, AdminPermissionEnum], bool] | None = None,
    action_policy: Callable[[Any, Any, AdminAction], bool] | None = None,
) -> AsyncIterator[_Panel]:
    """Open a panel with the given actions and policies, signed in.

    Args:
        tmp_path (Path): Where the SQLite file lives.
        actions (Sequence[Callable[..., Any]]): The custom actions.
        can_edit (bool): ``AdminModel(can_edit=...)``.
        access_policy (Callable | None): The per-model policy.
        action_policy (Callable | None): The per-action policy.

    Yields:
        _Panel: The panel.
    """
    CALLS.clear()
    db = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'panel.db'}")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = _FlowUser(email="ops@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
    site = AdminSite(title="Admin")
    site.register(AdminModel(_FlowItem, actions=list(actions), can_edit=can_edit))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(_FlowUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=access_policy,
            action_policy=action_policy,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "ops@example.com", "hunter2")
        yield _Panel(client=client, db=db)
    await db.drop_tables()
    await db.disconnect()


async def _row(p: _Panel, name: str = "item") -> str:
    """Insert one row.

    Returns:
        str: Its id.
    """
    async with p.db.get_session_context() as session:
        row = _FlowItem(name=name)
        session.add(row)
        await session.commit()
        return str(row.id)


async def _stored(p: _Panel, row_id: str) -> _FlowItem:
    """Read one row back on a fresh session."""
    async with p.db.get_session_context() as session:
        result = await session.execute(
            select(_FlowItem).where(_FlowItem.id == _identity(row_id))
        )
        return result.scalar_one()


def _identity(row_id: str) -> Any:
    """Turn the form's id back into the column's type."""
    from uuid import UUID

    return UUID(row_id)


async def _token(p: _Panel) -> str:
    """Read the CSRF token from the list page."""
    page = await p.client.get(f"/admin/m/{SLUG}/")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


async def _bulk(p: _Panel, action: str, ids: list[str], **extra: str) -> Any:
    """Post the bulk form, without following the redirect.

    Returns:
        Any: The response.
    """
    return await p.client.post(
        f"/admin/m/{SLUG}/bulk",
        data={
            "csrf_token": await _token(p),
            "action": f"custom:{action}",
            "ids": ids,
            **extra,
        },
        follow_redirects=False,
    )


class TestCommit:
    """#784: the panel commits what an action leaves in the session."""

    async def test_change_left_in_the_session_is_committed(
        self, tmp_path: Path
    ) -> None:
        async with _open(tmp_path, [mark]) as p:
            row_id = await _row(p)
            response = await _bulk(p, "mark", [row_id])
            assert response.status_code == 303
            assert (await _stored(p, row_id)).flag is True

    async def test_action_that_raises_writes_nothing(self, tmp_path: Path) -> None:
        async with _open(tmp_path, [mark_then_fail]) as p:
            row_id = await _row(p)
            with pytest.raises(RuntimeError, match="action failed"):
                await _bulk(p, "mark_then_fail", [row_id])
            assert (await _stored(p, row_id)).flag is False


def _view_only(principal: Any, admin: Any, action: AdminPermissionEnum) -> bool:
    """The read-only panel of the issue: only ``VIEW`` on every model."""
    return action is AdminPermissionEnum.VIEW


def _only_mark(principal: Any, admin: Any, action: AdminAction) -> bool:
    """Grant the "Marcar" action and nothing else."""
    return action.name == "mark"


class TestActionPolicy:
    """#783: one action granted without ``EDIT`` on the model."""

    async def test_granted_action_runs_and_the_other_is_403(
        self, tmp_path: Path
    ) -> None:
        async with _open(
            tmp_path,
            [mark, unmark],
            access_policy=_view_only,
            action_policy=_only_mark,
        ) as p:
            row_id = await _row(p)
            page = await p.client.get(f"/admin/m/{SLUG}/")
            assert 'value="custom:mark"' in page.text
            assert 'value="custom:unmark"' not in page.text
            assert 'value="activate"' not in page.text
            granted = await _bulk(p, "mark", [row_id])
            denied = await _bulk(p, "unmark", [row_id])
            assert granted.status_code == 303
            assert denied.status_code == 403
            assert (await _stored(p, row_id)).flag is True

    async def test_edit_form_stays_forbidden(self, tmp_path: Path) -> None:
        async with _open(
            tmp_path, [mark], access_policy=_view_only, action_policy=_only_mark
        ) as p:
            row_id = await _row(p)
            edit = await p.client.get(f"/admin/m/{SLUG}/{row_id}/edit")
            assert edit.status_code == 403
            assert (await _bulk(p, "mark", [row_id])).status_code == 303

    async def test_no_permitted_action_hides_the_bulk_form(
        self, tmp_path: Path
    ) -> None:
        async with _open(
            tmp_path,
            [unmark],
            access_policy=_view_only,
            action_policy=_only_mark,
        ) as p:
            await _row(p)
            page = await p.client.get(f"/admin/m/{SLUG}/")
            assert "tempest-admin-bulk" not in page.text

    async def test_builtin_actions_follow_edit_and_delete(self, tmp_path: Path) -> None:
        def no_delete(principal: Any, admin: Any, action: AdminPermissionEnum) -> bool:
            return action is not AdminPermissionEnum.DELETE

        async with _open(tmp_path, [mark], access_policy=no_delete) as p:
            await _row(p)
            page = await p.client.get(f"/admin/m/{SLUG}/")
            assert 'value="activate"' in page.text
            assert 'value="deactivate"' in page.text
            assert 'value="delete"' not in page.text
            assert 'value="custom:mark"' in page.text

    async def test_without_action_policy_edit_still_decides(
        self, tmp_path: Path
    ) -> None:
        async with _open(tmp_path, [mark], access_policy=_view_only) as p:
            row_id = await _row(p)
            page = await p.client.get(f"/admin/m/{SLUG}/")
            assert 'value="custom:mark"' not in page.text
            assert (await _bulk(p, "mark", [row_id])).status_code == 403


class TestInputForm:
    """#782: an action with ``schema`` asks for input before it runs."""

    async def test_first_post_answers_the_form_and_runs_nothing(
        self, tmp_path: Path
    ) -> None:
        async with _open(tmp_path, [annotate]) as p:
            first = await _row(p, "a")
            second = await _row(p, "b")
            response = await _bulk(p, "annotate", [first, second])
            assert response.status_code == 200
            assert 'name="reason"' in response.text
            assert 'name="action_input" value="1"' in response.text
            assert f'name="ids" value="{first}"' in response.text
            assert f'name="ids" value="{second}"' in response.text
            assert "2 row(s) selected." in response.text
            assert CALLS == []

    async def _form(self, p: _Panel, row_ids: list[str]) -> dict[str, Any]:
        """Open the input form and read the fields it posts back.

        Returns:
            dict[str, Any]: The hidden fields, ready to submit.
        """
        page = (await _bulk(p, "annotate", row_ids)).text
        nonce = re.search(r'name="submission_nonce" value="([^"]+)"', page)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page)
        assert nonce is not None and token is not None
        return {
            "csrf_token": token.group(1),
            "submission_nonce": nonce.group(1),
            "action": "custom:annotate",
            "action_input": "1",
            "ids": row_ids,
        }

    async def test_valid_submission_runs_once_with_data_and_ids(
        self, tmp_path: Path
    ) -> None:
        async with _open(tmp_path, [annotate]) as p:
            row_id = await _row(p)
            fields = {**await self._form(p, [row_id]), "reason": "pedido do suporte"}
            done = await p.client.post(
                f"/admin/m/{SLUG}/bulk", data=fields, follow_redirects=False
            )
            again = await p.client.post(
                f"/admin/m/{SLUG}/bulk", data=fields, follow_redirects=False
            )
            assert done.status_code == 303
            assert "anotado%28s%29%3A+1" in done.headers["location"]
            assert [("pedido do suporte", [row_id])] == CALLS
            assert (await _stored(p, row_id)).note == "pedido do suporte"
            assert again.status_code == 303
            assert "already+submitted" in again.headers["location"]
            assert len(CALLS) == 1

    async def test_invalid_submission_is_400_and_runs_nothing(
        self, tmp_path: Path
    ) -> None:
        async with _open(tmp_path, [annotate]) as p:
            row_id = await _row(p)
            fields = {**await self._form(p, [row_id]), "reason": "curto"}
            response = await p.client.post(
                f"/admin/m/{SLUG}/bulk", data=fields, follow_redirects=False
            )
            assert response.status_code == 400
            assert "String should have at least 10 characters" in response.text
            assert 'name="reason" value="curto"' in response.text
            assert CALLS == []
            assert (await _stored(p, row_id)).note is None

    async def test_invalid_submission_keeps_the_nonce(self, tmp_path: Path) -> None:
        async with _open(tmp_path, [annotate]) as p:
            row_id = await _row(p)
            fields = await self._form(p, [row_id])
            refused = await p.client.post(
                f"/admin/m/{SLUG}/bulk",
                data={**fields, "reason": "curto"},
                follow_redirects=False,
            )
            accepted = await p.client.post(
                f"/admin/m/{SLUG}/bulk",
                data={**fields, "reason": "pedido do suporte"},
                follow_redirects=False,
            )
            assert refused.status_code == 400
            assert accepted.status_code == 303
            assert [("pedido do suporte", [row_id])] == CALLS

    async def test_app_exception_rerenders_with_the_message(
        self, tmp_path: Path
    ) -> None:
        async with _open(tmp_path, [annotate]) as p:
            row_id = await _row(p)
            fields = {
                **await self._form(p, [row_id]),
                "reason": "recusar este pedido",
            }
            response = await p.client.post(
                f"/admin/m/{SLUG}/bulk", data=fields, follow_redirects=False
            )
            assert response.status_code == 400
            assert "motivo recusado pela regra do serviço" in html.unescape(
                response.text
            )
            assert 'name="reason" value="recusar este pedido"' in response.text
            assert (await _stored(p, row_id)).note is None

    async def test_form_without_a_nonce_is_400(self, tmp_path: Path) -> None:
        async with _open(tmp_path, [annotate]) as p:
            row_id = await _row(p)
            fields = {**await self._form(p, [row_id]), "reason": "pedido do suporte"}
            del fields["submission_nonce"]
            response = await p.client.post(
                f"/admin/m/{SLUG}/bulk", data=fields, follow_redirects=False
            )
            assert response.status_code == 400
            assert CALLS == []

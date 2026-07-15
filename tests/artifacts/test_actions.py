"""Tests for make_activate_artifact_action (admin action factory)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AdminActionContext,
    make_activate_artifact_action,
)
from tests.artifacts.support import add_version, make_repository


def _ctx(session: AsyncSession, ids: list[UUID]) -> AdminActionContext:
    """Build an AdminActionContext with the selected ids for the test model."""
    return AdminActionContext(
        ids=list(ids),
        repository=make_repository(session),
        db_session=session,
        request=None,
        session=None,
        principal=None,
    )


class TestActivateAction:
    async def test_activates_selected_and_clears_siblings(
        self,
        session: AsyncSession,
    ) -> None:
        v1 = await add_version(
            session, name="detect", version="1", file_key="k1", is_current=True
        )
        v2 = await add_version(session, name="detect", version="2", file_key="k2")
        other = await add_version(
            session, name="classify", version="1", file_key="k3", is_current=True
        )

        action = make_activate_artifact_action()
        result = await action.handler(_ctx(session, [v2.id]))

        assert result is not None
        assert result.category == "success"
        await session.refresh(v1)
        await session.refresh(v2)
        await session.refresh(other)
        assert v2.is_current is True
        assert v1.is_current is False
        assert other.is_current is True

    async def test_rejects_multiple_selection(self, session: AsyncSession) -> None:
        v1 = await add_version(session, name="detect", version="1", file_key="k1")
        v2 = await add_version(session, name="detect", version="2", file_key="k2")

        action = make_activate_artifact_action()
        result = await action.handler(_ctx(session, [v1.id, v2.id]))

        assert result is not None
        assert result.category == "warning"
        await session.refresh(v1)
        await session.refresh(v2)
        assert v1.is_current is False
        assert v2.is_current is False

    async def test_label_and_name_are_customizable(self) -> None:
        action = make_activate_artifact_action(label="Ativar versão", name="act")
        assert action.label == "Ativar versão"
        assert action.name == "act"
        assert hasattr(action.handler, "__admin_action__")

    async def test_registerable_handler_carries_marker(self) -> None:
        action = make_activate_artifact_action()
        marker: Any = action.handler.__admin_action__
        assert marker is action

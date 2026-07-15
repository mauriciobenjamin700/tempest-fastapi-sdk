"""Tests for ArtifactRegistry + build_manifest_entries."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import NotFoundException, build_manifest_entries
from tests.artifacts.support import ArtifactVersionModel, add_version, make_registry


class TestActivate:
    async def test_sets_current_and_clears_same_name_siblings(
        self,
        session: AsyncSession,
    ) -> None:
        registry = make_registry(session)
        v1 = await add_version(
            session, name="detect", version="1", file_key="k1", is_current=True
        )
        v2 = await add_version(session, name="detect", version="2", file_key="k2")
        other = await add_version(
            session, name="classify", version="1", file_key="k3", is_current=True
        )

        activated = await registry.activate(v2.id)

        assert activated.id == v2.id
        assert activated.is_current is True
        await session.refresh(v1)
        await session.refresh(other)
        assert v1.is_current is False
        assert other.is_current is True

    async def test_activate_missing_raises(self, session: AsyncSession) -> None:
        registry = make_registry(session)
        with pytest.raises(NotFoundException):
            await registry.activate(uuid4())


class TestCurrent:
    async def test_returns_active_row(self, session: AsyncSession) -> None:
        registry = make_registry(session)
        await add_version(session, name="detect", version="1", file_key="k1")
        v2 = await add_version(
            session, name="detect", version="2", file_key="k2", is_current=True
        )
        current = await registry.current("detect")
        assert current is not None
        assert current.id == v2.id

    async def test_none_when_no_current(self, session: AsyncSession) -> None:
        registry = make_registry(session)
        await add_version(session, name="detect", version="1", file_key="k1")
        assert await registry.current("detect") is None

    async def test_unknown_name_returns_none(self, session: AsyncSession) -> None:
        registry = make_registry(session)
        assert await registry.current("nope") is None


class TestListCurrent:
    async def test_one_current_per_name(self, session: AsyncSession) -> None:
        registry = make_registry(session)
        await add_version(session, name="detect", version="1", file_key="k1")
        await add_version(
            session, name="detect", version="2", file_key="k2", is_current=True
        )
        await add_version(
            session, name="classify", version="1", file_key="k3", is_current=True
        )
        rows = await registry.list_current()
        assert {r.name for r in rows} == {"detect", "classify"}
        assert {r.file_key for r in rows} == {"k2", "k3"}

    async def test_empty_when_none_active(self, session: AsyncSession) -> None:
        registry = make_registry(session)
        await add_version(session, name="detect", version="1", file_key="k1")
        assert await registry.list_current() == []


class TestBuildManifestEntries:
    async def test_reflects_current_rows(self, session: AsyncSession) -> None:
        registry = make_registry(session)
        await add_version(session, name="detect", version="1", file_key="old")
        await add_version(
            session, name="detect", version="2", file_key="k2", is_current=True
        )
        await add_version(
            session, name="classify", version="5", file_key="k5", is_current=True
        )

        async def digest_source(row: ArtifactVersionModel) -> tuple[str, int]:
            return (f"sha-{row.file_key}", len(row.file_key))

        entries = await build_manifest_entries(registry, digest_source=digest_source)

        by_name = {e.name: e for e in entries}
        assert set(by_name) == {"detect", "classify"}
        assert by_name["detect"].version == "2"
        assert by_name["detect"].file_key == "k2"
        assert by_name["detect"].sha256 == "sha-k2"
        assert by_name["detect"].size == 2

    async def test_empty_when_nothing_active(self, session: AsyncSession) -> None:
        registry = make_registry(session)
        await add_version(session, name="detect", version="1", file_key="k1")

        async def digest_source(row: ArtifactVersionModel) -> tuple[str, int]:
            return ("x", 0)

        assert await build_manifest_entries(registry, digest_source=digest_source) == []

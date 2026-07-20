"""Tests for tempest_fastapi_sdk.db.mixins."""

from uuid import uuid4

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AuditMixin,
    BaseModel,
    Locale,
    LocaleColumnMixin,
    SoftDeleteMixin,
)


class Article(SoftDeleteMixin, AuditMixin, BaseModel):
    __tablename__ = "article_for_mixins_test"

    title: Mapped[str] = mapped_column(String(64), nullable=False)


class Profile(LocaleColumnMixin, BaseModel):
    __tablename__ = "profile_for_locale_mixin_test"

    handle: Mapped[str] = mapped_column(String(64), nullable=False)


class TestSoftDeleteMixin:
    async def test_default_is_alive(self, session: AsyncSession) -> None:
        article = Article(title="alive")
        session.add(article)
        await session.commit()
        await session.refresh(article)
        assert article.deleted_at is None
        assert article.is_deleted is False

    async def test_mark_deleted_sets_timestamp(self, session: AsyncSession) -> None:
        article = Article(title="dead")
        article.mark_deleted()
        assert article.deleted_at is not None
        assert article.is_deleted is True

    async def test_mark_restored_clears_timestamp(self) -> None:
        article = Article(title="zombie")
        article.mark_deleted()
        article.mark_restored()
        assert article.deleted_at is None
        assert article.is_deleted is False


class TestAuditMixin:
    async def test_defaults_to_null(self, session: AsyncSession) -> None:
        article = Article(title="bare")
        session.add(article)
        await session.commit()
        await session.refresh(article)
        assert article.created_by is None
        assert article.updated_by is None

    async def test_stamp_created_by_sets_both(self) -> None:
        user_id = uuid4()
        article = Article(title="stamped")
        article.stamp_created_by(user_id)
        assert article.created_by == user_id
        assert article.updated_by == user_id

    async def test_stamp_updated_by_only_updates_updated(self) -> None:
        creator = uuid4()
        editor = uuid4()
        article = Article(title="changed")
        article.stamp_created_by(creator)
        article.stamp_updated_by(editor)
        assert article.created_by == creator
        assert article.updated_by == editor


class TestLocaleColumnMixin:
    async def test_defaults_to_null(self, session: AsyncSession) -> None:
        profile = Profile(handle="no-pref")
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        assert profile.locale is None

    async def test_persists_locale_enum_value(self, session: AsyncSession) -> None:
        profile = Profile(handle="brazilian", locale=Locale.PT_BR)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        assert profile.locale == "pt-BR"

    async def test_persists_raw_tag(self, session: AsyncSession) -> None:
        profile = Profile(handle="raw", locale="en-US")
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        assert profile.locale == "en-US"

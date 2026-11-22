"""Models, seed and client of the many-to-many, related-count, duplicate,
bulk-edit and recent-activity tests of the admin."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, ColumnElement, ForeignKey, String, Table, select
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    RelatedCount,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminAccessPolicy
from tempest_fastapi_sdk.db.audit import BaseAuditLogModel
from tempest_fastapi_sdk.db.mixins import AuditMixin
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login

SECRET = "w" * 48
OWN = "acme"
OTHER = "globex"


class W2User(BaseUserModel):
    """Principal carrying the company its scope is keyed on."""

    __tablename__ = "admin_w2_users"
    company: Mapped[str] = mapped_column(String(32), nullable=False, default=OWN)


article_tags = Table(
    "admin_w2_article_tags",
    BaseModel.metadata,
    Column("article_id", ForeignKey("admin_w2_articles.id"), primary_key=True),
    Column("tag_id", ForeignKey("admin_w2_tags.id"), primary_key=True),
)


class W2Tag(BaseModel):
    """Many-to-many target of :class:`W2Article`."""

    __tablename__ = "admin_w2_tags"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    company: Mapped[str] = mapped_column(String(32), nullable=False)


class W2Article(AuditMixin, BaseModel):
    """The scoped parent: tags, comments, audit columns, a unique slug."""

    __tablename__ = "admin_w2_articles"
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    company: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    tags: Mapped[list[W2Tag]] = relationship(secondary=article_tags)


class W2Comment(BaseModel):
    """Child of :class:`W2Article`, counted on the article list."""

    __tablename__ = "admin_w2_comments"
    body: Mapped[str] = mapped_column(String(64), nullable=False)
    company: Mapped[str] = mapped_column(String(32), nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_w2_articles.id"), nullable=False
    )


class W2Audit(BaseAuditLogModel):
    """Audit table of the articles and comments."""

    __tablename__ = "admin_w2_audit"


def company_scope(column: Any) -> Callable[[Any, Any], ColumnElement[bool]]:
    """Build a scope keeping the principal's own company.

    Args:
        column (Any): The model's ``company`` column.

    Returns:
        Callable[[Any, Any], ColumnElement[bool]]: The ``row_scope``.
    """

    def scope(user: Any, admin: Any) -> ColumnElement[bool]:
        """Return ``company == user.company``.

        Args:
            user (Any): The principal.
            admin (Any): The admin asked about.

        Returns:
            ColumnElement[bool]: The clause.
        """
        return column == user.company

    return scope


@dataclass
class Wave2World:
    """A signed-in client, its database and the seeded ids."""

    client: AsyncClient
    db: AsyncDatabaseManager
    csrf: str
    ids: dict[str, uuid.UUID] = field(default_factory=dict)

    async def article(self, key: str) -> W2Article | None:
        """Reload a seeded article with its tags.

        Args:
            key (str): The id key (``"article_in"``, ``"article_out"``...).

        Returns:
            W2Article | None: The row.
        """
        async with self.db.get_session_context() as session:
            result = await session.execute(
                select(W2Article)
                .options(selectinload(W2Article.tags))
                .where(W2Article.id == self.ids[key])
            )
            return result.scalar_one_or_none()

    async def audit_entries(self) -> list[W2Audit]:
        """Return every audit entry.

        Returns:
            list[W2Audit]: The entries.
        """
        async with self.db.get_session_context() as session:
            return list((await session.execute(select(W2Audit))).scalars())


def build_site(
    *,
    scoped: bool = True,
    recent_activity: int | None = None,
    article_options: dict[str, Any] | None = None,
    comment_options: dict[str, Any] | None = None,
) -> AdminSite:
    """Register the four admins.

    Args:
        scoped (bool): Whether the models declare their ``row_scope``.
        recent_activity (int | None): The site's feed size.
        article_options (dict[str, Any] | None): Overrides of the article
            admin's options.
        comment_options (dict[str, Any] | None): Overrides of the comment
            admin's options.

    Returns:
        AdminSite: The site.
    """
    site = AdminSite(title="Wave2", recent_activity=recent_activity)
    site.register(
        AdminModel(
            W2User,
            search_fields=["email"],
            password_fields=["hashed_password"],
            can_duplicate=True,
            row_scope=company_scope(W2User.company) if scoped else None,
        )
    )
    site.register(
        AdminModel(
            W2Tag,
            search_fields=["name"],
            row_scope=company_scope(W2Tag.company) if scoped else None,
        )
    )
    options: dict[str, Any] = {
        "list_display": ["title", "company", "status"],
        "search_fields": ["title"],
        "m2m_fields": [W2Article.tags],
        "related_counts": [RelatedCount(W2Comment.article_id, label="Comments")],
        "can_duplicate": True,
        "bulk_edit_fields": [W2Article.status, W2Article.company],
        "choices": {W2Article.status: ["draft", "published", "archived"]},
        "audit_model": W2Audit,
        "row_scope": company_scope(W2Article.company) if scoped else None,
    }
    options.update(article_options or {})
    site.register(AdminModel(W2Article, **options))
    comment: dict[str, Any] = {
        "search_fields": ["body"],
        "audit_model": W2Audit,
        "row_scope": company_scope(W2Comment.company) if scoped else None,
    }
    comment.update(comment_options or {})
    site.register(AdminModel(W2Comment, **comment))
    return site


async def seed(db: AsyncDatabaseManager) -> dict[str, uuid.UUID]:
    """Insert one row per company of each model, and two operators.

    The article inside the scope is tagged with both tags — the foreign one
    included, which the operator must not see nor remove — and has two
    comments of its own company and one of the other.

    Args:
        db (AsyncDatabaseManager): The connected database.

    Returns:
        dict[str, uuid.UUID]: The seeded ids.
    """
    async with db.get_session_context() as session:
        operator = W2User(
            email="op@example.com", hashed_password="", is_admin=True, company=OWN
        )
        operator.set_password("hunter2")
        stranger = W2User(
            email="other@example.com",
            hashed_password="",
            is_admin=True,
            company=OTHER,
        )
        stranger.set_password("hunter2")
        tag_in = W2Tag(name="TagInside", company=OWN)
        tag_out = W2Tag(name="TagOutside", company=OTHER)
        session.add_all([operator, stranger, tag_in, tag_out])
        await session.flush()
        article_in = W2Article(
            title="ArticleInside",
            slug="inside",
            company=OWN,
            status="draft",
            tags=[tag_in, tag_out],
            created_by=operator.id,
        )
        article_out = W2Article(
            title="ArticleOutside", slug="outside", company=OTHER, status="draft"
        )
        session.add_all([article_in, article_out])
        await session.flush()
        session.add_all(
            [
                W2Comment(body="CommentInA", company=OWN, article_id=article_in.id),
                W2Comment(body="CommentInB", company=OWN, article_id=article_in.id),
                W2Comment(body="CommentOutA", company=OTHER, article_id=article_in.id),
                W2Comment(body="CommentOutB", company=OTHER, article_id=article_out.id),
            ]
        )
        await session.commit()
        return {
            "operator": operator.id,
            "stranger": stranger.id,
            "tag_in": tag_in.id,
            "tag_out": tag_out.id,
            "article_in": article_in.id,
            "article_out": article_out.id,
        }


async def new_database() -> AsyncDatabaseManager:
    """Open a fresh in-memory database with the tables created.

    Returns:
        AsyncDatabaseManager: The connected manager.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    return db


async def open_world(
    db: AsyncDatabaseManager,
    *,
    scoped: bool = True,
    recent_activity: int | None = None,
    access_policy: AdminAccessPolicy | None = None,
    article_options: dict[str, Any] | None = None,
    comment_options: dict[str, Any] | None = None,
    export_max_rows: int = 5000,
    drop: tuple[type[BaseModel], ...] = (),
) -> AsyncIterator[Wave2World]:
    """Seed, mount and sign in as the operator of ``OWN``.

    Args:
        db (AsyncDatabaseManager): The database.
        scoped (bool): Whether the scope is declared.
        recent_activity (int | None): The site's feed size.
        access_policy (AdminAccessPolicy | None): The router's policy.
        article_options (dict[str, Any] | None): Overrides of the article
            admin's options.
        comment_options (dict[str, Any] | None): Overrides of the comment
            admin's options.
        export_max_rows (int): The router's export cap.
        drop (tuple[type[BaseModel], ...]): Models to leave unregistered.

    Yields:
        Wave2World: The signed-in world.
    """
    ids = await seed(db)
    site = build_site(
        scoped=scoped,
        recent_activity=recent_activity,
        article_options=article_options,
        comment_options=comment_options,
    )
    for model in drop:
        site.unregister(model)
    router = make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(W2User),
        secret_key=SECRET,
        cookie_secure=False,
        access_policy=access_policy,
        export_max_rows=export_max_rows,
    )
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        page = await client.get(f"{ARTICLES}/")
        yield Wave2World(
            client=client, db=db, csrf=admin_csrf_token(page.text), ids=ids
        )


ARTICLES = f"/admin/m/{W2Article.__tablename__}"
COMMENTS = f"/admin/m/{W2Comment.__tablename__}"
USERS = f"/admin/m/{W2User.__tablename__}"

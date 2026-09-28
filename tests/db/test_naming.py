"""Tests for the composite-aware naming convention and its migration helper.

The convention used to name a unique constraint, an index and a foreign
key after their first column only, so ``UniqueConstraint("title")`` and
``UniqueConstraint("title", "release_year")`` both became
``uq_books_title``. These tests pin the new names, pin that every
single-column name is unchanged, and cover
:func:`~tempest_fastapi_sdk.legacy_constraint_renames`. The PostgreSQL
side — the refused DDL, the rename applied to a real server — lives in
``test_naming_live.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from tempest_fastapi_sdk import (
    LEGACY_NAMING_CONVENTION,
    NAMING_CONVENTION,
    BaseModel,
    ConstraintKind,
    ConstraintRename,
    legacy_constraint_renames,
)
from tempest_fastapi_sdk.db import BaseUserModel, make_user_oauth_account_model


def _books(convention: dict[str, str], *, schema: str | None = None) -> Table:
    """Build the ``books`` table from the issue under ``convention``.

    Args:
        convention (dict[str, str]): The naming convention to apply.
        schema (str | None): Optional schema for the table.

    Returns:
        Table: ``books`` with one single-column and two composite uniques,
        a single and a composite index, and a single and a composite
        foreign key.
    """
    metadata = MetaData(naming_convention=convention)
    Table(
        "authors",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", Integer),
        schema=schema,
    )
    target = f"{schema}.authors" if schema else "authors"
    books = Table(
        "books",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("title", String),
        Column("release_year", Integer),
        Column("author", String),
        Column("tenant_id", Integer),
        Column("author_id", Integer),
        UniqueConstraint("title"),
        UniqueConstraint("title", "release_year"),
        UniqueConstraint("title", "author"),
        ForeignKeyConstraint(["author_id"], [f"{target}.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "author_id"],
            [f"{target}.tenant_id", f"{target}.id"],
        ),
        CheckConstraint("release_year > 0", name="positive_year"),
        schema=schema,
    )
    Index(None, books.c.title)
    Index(None, books.c.title, books.c.author)
    return books


def _names(table: Table) -> dict[tuple[str, tuple[str, ...]], str]:
    """Map every named item of ``table`` to its name, keyed by kind and columns.

    Args:
        table (Table): The table to read.

    Returns:
        dict[tuple[str, tuple[str, ...]], str]: ``(kind, columns) -> name``.
    """
    names: dict[tuple[str, tuple[str, ...]], str] = {}
    for item in [*table.constraints, *table.indexes]:
        if isinstance(item, ForeignKeyConstraint):
            columns = tuple(element.parent.name for element in item.elements)
        else:
            columns = tuple(column.name for column in item.columns)
        names[(type(item).__name__, columns)] = str(item.name)
    return names


class TestCompositeNames:
    """Composite constraints are named after every column."""

    def test_two_uniques_sharing_a_first_column_get_distinct_names(self) -> None:
        names = _names(_books(NAMING_CONVENTION))

        assert names[("UniqueConstraint", ("title",))] == "uq_books_title"
        assert (
            names[("UniqueConstraint", ("title", "release_year"))]
            == "uq_books_title_release_year"
        )
        assert (
            names[("UniqueConstraint", ("title", "author"))] == "uq_books_title_author"
        )

    def test_legacy_convention_collides_on_the_same_shape(self) -> None:
        """The defect this convention fixes, pinned against the old templates."""
        names = _names(_books(LEGACY_NAMING_CONVENTION))

        assert names[("UniqueConstraint", ("title",))] == "uq_books_title"
        assert names[("UniqueConstraint", ("title", "release_year"))] == (
            "uq_books_title"
        )

    def test_composite_index_and_foreign_key(self) -> None:
        names = _names(_books(NAMING_CONVENTION))

        assert names[("Index", ("title", "author"))] == "ix_books_title_books_author"
        assert (
            names[("ForeignKeyConstraint", ("tenant_id", "author_id"))]
            == "fk_books_tenant_id_author_id_authors"
        )

    def test_postgres_ddl_carries_each_name_once(self) -> None:
        ddl = str(
            CreateTable(_books(NAMING_CONVENTION)).compile(
                dialect=postgresql.dialect(),
            )
        )

        assert ddl.count("CONSTRAINT uq_books_title UNIQUE") == 1
        assert ddl.count("CONSTRAINT uq_books_title_release_year UNIQUE") == 1
        assert ddl.count("CONSTRAINT uq_books_title_author UNIQUE") == 1


class TestSingleColumnNamesUnchanged:
    """Every single-column name is byte-for-byte what the legacy one was."""

    @pytest.mark.parametrize("schema", [None, "library"])
    def test_single_column_names_match_the_legacy_convention(
        self,
        schema: str | None,
    ) -> None:
        new = _names(_books(NAMING_CONVENTION, schema=schema))
        old = _names(_books(LEGACY_NAMING_CONVENTION, schema=schema))

        single = {key for key in new if len(key[1]) == 1}
        assert single
        assert {key: new[key] for key in single} == {key: old[key] for key in single}

    def test_known_single_column_names(self) -> None:
        names = _names(_books(NAMING_CONVENTION))

        assert names[("UniqueConstraint", ("title",))] == "uq_books_title"
        assert names[("Index", ("title",))] == "ix_books_title"
        assert (
            names[("ForeignKeyConstraint", ("author_id",))]
            == "fk_books_author_id_authors"
        )
        assert names[("CheckConstraint", ())] == "ck_books_positive_year"
        assert names[("PrimaryKeyConstraint", ("id",))] == "pk_books"

    def test_base_model_metadata_carries_the_new_convention(self) -> None:
        assert BaseModel.metadata.naming_convention == NAMING_CONVENTION
        assert NAMING_CONVENTION["uq"] == "uq_%(table_name)s_%(column_0_N_name)s"


class TestLongNames:
    """PostgreSQL's 63-character limit is met by SQLAlchemy's own truncation."""

    @staticmethod
    def _long_table() -> Table:
        """Build a table whose composite unique name exceeds 63 characters.

        Returns:
            Table: ``subscription_billing_events`` with a 90-character
            convention name.
        """
        metadata = MetaData(naming_convention=NAMING_CONVENTION)
        return Table(
            "subscription_billing_events",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("customer_identifier", String),
            Column("billing_period_start", Integer),
            Column("billing_period_end", Integer),
            UniqueConstraint(
                "customer_identifier",
                "billing_period_start",
                "billing_period_end",
            ),
        )

    def test_postgres_name_is_truncated_with_an_md5_suffix(self) -> None:
        full = (
            "uq_subscription_billing_events_customer_identifier_"
            "billing_period_start_billing_period_end"
        )
        expected = f"{full[:55]}_{hashlib.md5(full.encode()).hexdigest()[-4:]}"

        ddl = str(
            CreateTable(self._long_table()).compile(
                dialect=postgresql.dialect(),
            )
        )

        assert len(full) == 90
        assert len(expected) == 60
        assert f"CONSTRAINT {expected} UNIQUE" in ddl

    def test_truncation_is_the_same_on_every_build(self) -> None:
        first = str(
            CreateTable(self._long_table()).compile(dialect=postgresql.dialect())
        )
        second = str(
            CreateTable(self._long_table()).compile(dialect=postgresql.dialect())
        )

        assert first == second

    def test_sqlite_keeps_the_full_name(self) -> None:
        ddl = str(CreateTable(self._long_table()).compile(dialect=sqlite.dialect()))

        assert (
            "uq_subscription_billing_events_customer_identifier_"
            "billing_period_start_billing_period_end"
        ) in ddl


class TestLegacyConstraintRenames:
    """What drifted between the two conventions, and the SQL that fixes it."""

    def test_lists_every_composite_kind_and_nothing_else(self) -> None:
        renames = legacy_constraint_renames(_books(NAMING_CONVENTION).metadata)

        assert [(r.kind, r.old_name, r.new_name) for r in renames] == [
            (
                ConstraintKind.FOREIGN_KEY,
                "fk_books_tenant_id_authors",
                "fk_books_tenant_id_author_id_authors",
            ),
            (
                ConstraintKind.INDEX,
                "ix_books_title",
                "ix_books_title_books_author",
            ),
            (ConstraintKind.UNIQUE, "uq_books_title", "uq_books_title_author"),
            (
                ConstraintKind.UNIQUE,
                "uq_books_title",
                "uq_books_title_release_year",
            ),
        ]
        assert renames[0].columns == ("tenant_id", "author_id")

    def test_explicit_names_are_left_alone(self) -> None:
        metadata = MetaData(naming_convention=NAMING_CONVENTION)
        Table(
            "books",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("title", String),
            Column("release_year", Integer),
            UniqueConstraint("title", "release_year", name="uq_books_title"),
        )

        assert legacy_constraint_renames(metadata) == []

    def test_metadata_without_composites_is_empty(self) -> None:
        metadata = MetaData(naming_convention=NAMING_CONVENTION)
        Table(
            "books",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("title", String, unique=True, index=True),
        )

        assert legacy_constraint_renames(metadata) == []

    def test_expression_index_is_named_after_its_columns(self) -> None:
        metadata = MetaData(naming_convention=NAMING_CONVENTION)
        books = Table(
            "books",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("title", String),
            Column("tenant_id", Integer),
        )
        Index(None, func.lower(books.c.title), books.c.tenant_id)

        [rename] = legacy_constraint_renames(metadata)

        assert rename.kind is ConstraintKind.INDEX
        assert rename.old_name == "ix_books_title"
        assert rename.new_name == "ix_books_title_books_tenant_id"

    def test_schema_is_carried_and_quoted(self) -> None:
        renames = legacy_constraint_renames(
            _books(NAMING_CONVENTION, schema="Library").metadata,
        )
        index = next(r for r in renames if r.kind is ConstraintKind.INDEX)
        unique = next(r for r in renames if r.kind is ConstraintKind.UNIQUE)

        assert unique.schema == "Library"
        assert unique.statement(postgresql.dialect()).startswith(
            'ALTER TABLE "Library".books RENAME CONSTRAINT',
        )
        assert index.statement(postgresql.dialect()).startswith(
            'ALTER INDEX "Library".',
        )

    def test_postgres_statements(self) -> None:
        renames = legacy_constraint_renames(_books(NAMING_CONVENTION).metadata)
        dialect = postgresql.dialect()

        assert [r.statement(dialect) for r in renames] == [
            "ALTER TABLE books RENAME CONSTRAINT fk_books_tenant_id_authors "
            "TO fk_books_tenant_id_author_id_authors",
            "ALTER INDEX ix_books_title RENAME TO ix_books_title_books_author",
            "ALTER TABLE books RENAME CONSTRAINT uq_books_title "
            "TO uq_books_title_author",
            "ALTER TABLE books RENAME CONSTRAINT uq_books_title "
            "TO uq_books_title_release_year",
        ]

    def test_statement_truncates_like_the_ddl(self) -> None:
        table = TestLongNames._long_table()
        [rename] = legacy_constraint_renames(table.metadata)
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))

        new = rename.statement(postgresql.dialect()).rsplit(" TO ", 1)[1]

        assert len(new) == 60
        assert f"CONSTRAINT {new} UNIQUE" in ddl

    def test_inverse_swaps_the_names(self) -> None:
        rename = ConstraintRename(
            kind=ConstraintKind.UNIQUE,
            table="books",
            schema=None,
            columns=("title", "release_year"),
            old_name="uq_books_title",
            new_name="uq_books_title_release_year",
        )

        assert rename.inverse().statement(postgresql.dialect()) == (
            "ALTER TABLE books RENAME CONSTRAINT uq_books_title_release_year "
            "TO uq_books_title"
        )
        assert rename.inverse().inverse() == rename

    def test_sqlite_is_refused_with_the_way_out(self) -> None:
        [*_, rename] = legacy_constraint_renames(_books(NAMING_CONVENTION).metadata)

        with pytest.raises(ValueError, match="batch migration"):
            rename.statement(sqlite.dialect())


class TestShippedModels:
    """The SDK's own composite constraints are part of the drift."""

    @pytest.fixture
    def oauth_metadata(self) -> Iterator[MetaData]:
        """Map a user table and the OAuth account table onto a fresh base.

        Yields:
            MetaData: ``BaseModel.metadata`` with both tables, removed
            afterwards so other tests see the metadata unchanged.
        """

        class NamingUserModel(BaseUserModel):
            __tablename__ = "naming_users"

        oauth = make_user_oauth_account_model(
            user_table="naming_users",
            tablename="naming_oauth_accounts",
            class_name="NamingOAuthAccountModel",
        )
        try:
            yield BaseModel.metadata
        finally:
            BaseModel.metadata.remove(oauth.__table__)
            BaseModel.metadata.remove(NamingUserModel.__table__)

    def test_oauth_account_uniques_are_renamed(self, oauth_metadata: MetaData) -> None:
        renames = [
            (r.old_name, r.new_name)
            for r in legacy_constraint_renames(oauth_metadata)
            if r.table == "naming_oauth_accounts"
        ]

        assert renames == [
            (
                "uq_naming_oauth_accounts_provider",
                "uq_naming_oauth_accounts_provider_subject",
            ),
            (
                "uq_naming_oauth_accounts_user_id",
                "uq_naming_oauth_accounts_user_id_provider",
            ),
        ]

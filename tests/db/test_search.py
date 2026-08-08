"""Tests for tempest_fastapi_sdk.db.search and the repository search methods.

Two kinds of assertion, because the two supported backends are exercised
differently. Behavior runs for real against the SQLite test database.
The PostgreSQL-only full-text path has no server here, so it is asserted
on the **compiled SQL** — which is what actually pins ``to_tsvector`` /
``websearch_to_tsquery`` / ``setweight`` being emitted with the right
configuration, and would catch a regression that silently dropped the
whole full-text branch into the fallback.
"""

import pytest
from sqlalchemy import String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository, ValidationException
from tempest_fastapi_sdk.db.search import (
    POSTGRESQL_DIALECT,
    TextSearchLanguage,
    TextSearchWeight,
    TokenMatch,
    full_text_condition,
    full_text_rank,
    like_search_condition,
    resolve_search_column,
    supports_full_text,
    tokenize,
)


class Article(BaseModel):
    __tablename__ = "article_for_search_test"

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.fixture
def articles(session: AsyncSession) -> BaseRepository[Article]:
    return BaseRepository(session, model=Article)


@pytest.fixture
async def seeded(articles: BaseRepository[Article]) -> BaseRepository[Article]:
    await articles.add_all(
        [
            Article(title="Nota fiscal emitida", body="pedido 10", author="joao"),
            Article(title="Nota cancelada", body="pedido 11", author="maria"),
            Article(title="Relatorio mensal", body="nota de rodape", author="joao"),
            Article(title="100% aprovado", body="taxa de 50_00", author="ana"),
        ],
    )
    return articles


def _pg_sql(clause: object) -> str:
    """Compile a clause with the PostgreSQL dialect and return the SQL.

    Args:
        clause (object): The SQLAlchemy clause to compile.

    Returns:
        str: The rendered SQL, with literals inlined so the text-search
        configuration name is visible in the assertion.
    """
    compiled = clause.compile(  # type: ignore[attr-defined]
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    return str(compiled)


class TestTokenize:
    def test_splits_on_whitespace(self) -> None:
        assert tokenize("  nota   fiscal ") == ["nota", "fiscal"]

    def test_blank_term_has_no_tokens(self) -> None:
        assert tokenize("   ") == []


class TestResolveSearchColumn:
    def test_accepts_a_name(self) -> None:
        assert resolve_search_column(Article, "title") is Article.title

    def test_accepts_the_mapped_attribute(self) -> None:
        assert resolve_search_column(Article, Article.title) is Article.title

    def test_rejects_an_unknown_column(self) -> None:
        with pytest.raises(ValidationException) as exc:
            resolve_search_column(Article, "nope")
        assert exc.value.details["field"] == "nope"

    def test_rejects_a_non_column_attribute(self) -> None:
        """``metadata`` exists on the class but is not a mapped column."""
        with pytest.raises(ValidationException):
            resolve_search_column(Article, "metadata")


class TestLikeSearchCondition:
    def test_blank_term_yields_no_condition(self) -> None:
        assert like_search_condition(Article, "  ", ["title"]) is None

    def test_empty_field_list_is_refused(self) -> None:
        """Searching zero columns would match every row."""
        with pytest.raises(ValidationException, match="at least one column"):
            like_search_condition(Article, "nota", [])

    async def test_tokens_must_all_match_by_default(
        self, seeded: BaseRepository[Article]
    ) -> None:
        rows = await seeded.search("nota fiscal", fields=["title", "body"])
        assert [row.title for row in rows] == ["Nota fiscal emitida"]

    async def test_tokens_may_match_across_different_fields(
        self, seeded: BaseRepository[Article]
    ) -> None:
        """``nota`` is in the title, ``rodape`` in the body — one row."""
        rows = await seeded.search("nota rodape", fields=["title", "body"])
        assert [row.title for row in rows] == ["Relatorio mensal"]

    async def test_token_match_any_widens(
        self, seeded: BaseRepository[Article]
    ) -> None:
        rows = await seeded.search(
            "fiscal cancelada",
            fields=["title"],
            token_match=TokenMatch.ANY,
        )
        assert sorted(row.title for row in rows) == [
            "Nota cancelada",
            "Nota fiscal emitida",
        ]

    async def test_search_is_case_insensitive(
        self, seeded: BaseRepository[Article]
    ) -> None:
        rows = await seeded.search("NOTA FISCAL", fields=["title"])
        assert len(rows) == 1

    async def test_wildcards_in_the_term_are_literal(
        self, seeded: BaseRepository[Article]
    ) -> None:
        """A bare ``%`` would otherwise match every row."""
        rows = await seeded.search("100%", fields=["title"])
        assert [row.title for row in rows] == ["100% aprovado"]

    async def test_underscore_in_the_term_is_literal(
        self, seeded: BaseRepository[Article]
    ) -> None:
        """``_`` is LIKE's single-character wildcard until escaped."""
        rows = await seeded.search("50_00", fields=["body"])
        assert [row.author for row in rows] == ["ana"]

    async def test_blank_term_lists_instead_of_hiding(
        self, seeded: BaseRepository[Article]
    ) -> None:
        rows = await seeded.search("", fields=["title"])
        assert len(rows) == 4

    async def test_mapped_attribute_fields_work(
        self, seeded: BaseRepository[Article]
    ) -> None:
        rows = await seeded.search("joao", fields=[Article.author])
        assert len(rows) == 2

    async def test_filters_narrow_the_search(
        self, seeded: BaseRepository[Article]
    ) -> None:
        rows = await seeded.search(
            "nota", fields=["title", "body"], filters={"author": "joao"}
        )
        assert sorted(row.title for row in rows) == [
            "Nota fiscal emitida",
            "Relatorio mensal",
        ]

    async def test_limit_caps_the_result(self, seeded: BaseRepository[Article]) -> None:
        rows = await seeded.search("nota", fields=["title", "body"], limit=1)
        assert len(rows) == 1


class TestComposesWithPagination:
    async def test_search_condition_feeds_where(
        self, seeded: BaseRepository[Article]
    ) -> None:
        """The reason the condition is returned instead of executed."""
        page = await seeded.paginate(
            where=seeded.search_condition("nota", fields=["title", "body"]),
            page=1,
            page_size=2,
        )
        assert page["total"] == 3
        assert len(page["items"]) == 2

    async def test_blank_condition_leaves_the_page_unfiltered(
        self, seeded: BaseRepository[Article]
    ) -> None:
        page = await seeded.paginate(
            where=seeded.search_condition("", fields=["title"]),
        )
        assert page["total"] == 4

    async def test_count_honours_a_raw_clause(
        self, seeded: BaseRepository[Article]
    ) -> None:
        total = await seeded.count(
            where=seeded.search_condition("joao", fields=["author"]),
        )
        assert total == 2


class TestSqliteFallback:
    def test_sqlite_has_no_full_text_engine(self) -> None:
        assert supports_full_text("sqlite") is False
        assert supports_full_text(POSTGRESQL_DIALECT) is True

    async def test_repository_reports_the_backend(
        self, articles: BaseRepository[Article]
    ) -> None:
        assert articles.dialect == "sqlite"
        assert articles.supports_full_text is False

    async def test_full_text_search_still_returns_the_rows(
        self, seeded: BaseRepository[Article]
    ) -> None:
        rows = await seeded.full_text_search("nota fiscal", fields=["title", "body"])
        assert [row.title for row in rows] == ["Nota fiscal emitida"]

    def test_fallback_condition_is_the_like_layer(self) -> None:
        clause = full_text_condition(Article, "nota", ["title"], dialect="sqlite")
        sql = _pg_sql(clause)
        assert "to_tsvector" not in sql
        assert "ILIKE" in sql
        assert "ESCAPE" in sql

    def test_rank_is_absent_without_a_ranking_backend(self) -> None:
        """``None`` tells the caller to keep its own ordering."""
        assert full_text_rank(Article, "nota", ["title"], dialect="sqlite") is None


class TestPostgresFullTextSql:
    def test_uses_websearch_to_tsquery(self) -> None:
        sql = _pg_sql(full_text_condition(Article, "nota fiscal", ["title", "body"]))
        assert "websearch_to_tsquery" in sql
        assert "to_tsvector" in sql
        assert "@@" in sql

    def test_language_reaches_the_query(self) -> None:
        sql = _pg_sql(
            full_text_condition(
                Article,
                "nota",
                ["title"],
                language=TextSearchLanguage.ENGLISH,
            ),
        )
        assert "english" in sql
        assert "portuguese" not in sql

    def test_portuguese_is_the_default(self) -> None:
        sql = _pg_sql(full_text_condition(Article, "nota", ["title"]))
        assert "portuguese" in sql

    def test_null_columns_are_coalesced(self) -> None:
        """One NULL operand would otherwise blank the whole document."""
        sql = _pg_sql(full_text_condition(Article, "nota", ["title", "body"]))
        assert "coalesce" in sql.lower()

    def test_weights_emit_setweight_per_column(self) -> None:
        sql = _pg_sql(
            full_text_condition(
                Article,
                "nota",
                ["title", "body"],
                weights={"title": TextSearchWeight.A},
            ),
        )
        assert sql.count("setweight") == 2
        assert "'A'" in sql
        assert "'D'" in sql

    def test_rank_expression_is_ts_rank(self) -> None:
        clause = full_text_rank(Article, "nota", ["title"])
        assert clause is not None
        assert "ts_rank" in _pg_sql(clause)

    def test_blank_term_yields_no_condition(self) -> None:
        assert full_text_condition(Article, "  ", ["title"]) is None
        assert full_text_rank(Article, "  ", ["title"]) is None

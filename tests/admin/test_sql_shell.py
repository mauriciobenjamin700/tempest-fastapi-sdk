"""Tests for the admin SQL console: analyser, policy and execution.

The bypass-attempt cases are the point of this file. They document what
the parser *does* catch, and the module docstring documents that a parser
is not the boundary — the database grants are.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from tempest_fastapi_sdk.admin.sql_shell import (
    SqlAudit,
    SqlCapability,
    SqlShellDenied,
    SqlShellError,
    SqlShellPolicy,
    SqlShellService,
    analyze_sql,
    check_policy,
)
from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


@pytest.fixture
async def db() -> AsyncIterator[AsyncDatabaseManager]:
    """Return a manager over an in-memory SQLite with one seeded table."""
    from sqlalchemy import text

    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    async with manager.get_session_context() as session:
        await session.execute(text("CREATE TABLE orders (id INTEGER, total INTEGER)"))
        await session.execute(text("INSERT INTO orders VALUES (1, 100), (2, 200)"))
    yield manager
    await manager.disconnect()


class TestAnalyzer:
    def test_classifies_a_select_as_read(self) -> None:
        statement = analyze_sql("SELECT * FROM orders")[0]
        assert statement.capability == SqlCapability.READ
        assert statement.tables == ["orders"]
        assert statement.returns_rows is True

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("INSERT INTO orders VALUES (1)", SqlCapability.INSERT),
            ("UPDATE orders SET total = 1 WHERE id = 1", SqlCapability.UPDATE),
            ("DELETE FROM orders WHERE id = 1", SqlCapability.DELETE),
            ("CREATE TABLE t (id INT)", SqlCapability.DDL),
            ("ALTER TABLE orders ADD COLUMN x INT", SqlCapability.DDL),
            ("DROP TABLE orders", SqlCapability.DROP),
            ("TRUNCATE TABLE orders", SqlCapability.DROP),
            ("GRANT SELECT ON orders TO bob", SqlCapability.ADMIN),
        ],
    )
    def test_classifies_each_family(self, sql: str, expected: SqlCapability) -> None:
        assert analyze_sql(sql)[0].capability == expected

    def test_finds_tables_inside_a_subquery(self) -> None:
        statement = analyze_sql(
            "SELECT * FROM orders WHERE id IN (SELECT id FROM secrets)",
        )[0]
        assert statement.tables == ["orders", "secrets"]

    def test_finds_tables_inside_a_cte(self) -> None:
        statement = analyze_sql(
            "WITH t AS (SELECT * FROM secrets) SELECT * FROM t",
        )[0]
        assert statement.tables == ["secrets"]

    def test_cte_aliases_are_not_reported_as_tables(self) -> None:
        statement = analyze_sql(
            "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
        )[0]
        assert statement.tables == ["orders"]

    def test_finds_both_sides_of_a_join(self) -> None:
        statement = analyze_sql(
            "SELECT * FROM orders o JOIN customers c ON o.id = c.id",
        )[0]
        assert statement.tables == ["customers", "orders"]

    def test_a_where_clause_is_detected(self) -> None:
        assert analyze_sql("DELETE FROM orders WHERE id = 1")[0].has_where is True
        assert analyze_sql("DELETE FROM orders")[0].has_where is False

    def test_multiple_statements_are_all_returned(self) -> None:
        statements = analyze_sql("SELECT 1; DROP TABLE orders")
        assert len(statements) == 2
        assert statements[1].capability == SqlCapability.DROP

    def test_unparseable_sql_is_refused(self) -> None:
        with pytest.raises(SqlShellError, match="parse"):
            analyze_sql("SELECT FROM WHERE ((((")

    def test_empty_input_is_refused(self) -> None:
        with pytest.raises(SqlShellError, match="no statement"):
            analyze_sql("   ")

    def test_an_unknown_construct_needs_the_admin_capability(self) -> None:
        statement = analyze_sql("VACUUM")[0]
        assert statement.capability == SqlCapability.ADMIN


class TestPolicy:
    def test_read_only_by_default(self) -> None:
        policy = SqlShellPolicy()
        assert policy.capabilities == {SqlCapability.READ}
        assert policy.read_only is True

    def test_a_mutating_capability_makes_it_not_read_only(self) -> None:
        policy = SqlShellPolicy(
            capabilities={SqlCapability.READ, SqlCapability.UPDATE},
        )
        assert policy.read_only is False

    def test_a_disallowed_capability_is_refused(self) -> None:
        refusal = check_policy(analyze_sql("DROP TABLE orders"), SqlShellPolicy())
        assert refusal is not None
        assert "drop is not permitted" in refusal

    def test_the_refusal_lists_what_is_permitted(self) -> None:
        refusal = check_policy(analyze_sql("DROP TABLE orders"), SqlShellPolicy())
        assert "read" in refusal

    def test_denied_tables_are_refused(self) -> None:
        policy = SqlShellPolicy(denied_tables={"users"})
        refusal = check_policy(analyze_sql("SELECT * FROM users"), policy)
        assert refusal is not None
        assert "users" in refusal

    def test_deny_beats_allow(self) -> None:
        policy = SqlShellPolicy(
            allowed_tables={"users", "orders"},
            denied_tables={"users"},
        )
        assert policy.table_allowed("users") is False
        assert policy.table_allowed("orders") is True

    def test_an_empty_allowlist_permits_every_table(self) -> None:
        assert SqlShellPolicy().table_allowed("anything") is True

    def test_an_allowlist_refuses_everything_else(self) -> None:
        policy = SqlShellPolicy(allowed_tables={"orders"})
        assert check_policy(analyze_sql("SELECT * FROM orders"), policy) is None
        assert check_policy(analyze_sql("SELECT * FROM secrets"), policy) is not None

    def test_table_matching_ignores_case(self) -> None:
        policy = SqlShellPolicy(denied_tables={"Users"})
        assert policy.table_allowed("USERS") is False

    def test_a_denied_table_hidden_in_a_subquery_is_still_caught(self) -> None:
        policy = SqlShellPolicy(denied_tables={"secrets"})
        refusal = check_policy(
            analyze_sql("SELECT * FROM orders WHERE id IN (SELECT id FROM secrets)"),
            policy,
        )
        assert refusal is not None
        assert "secrets" in refusal

    def test_a_denied_table_hidden_in_a_cte_is_still_caught(self) -> None:
        policy = SqlShellPolicy(denied_tables={"secrets"})
        refusal = check_policy(
            analyze_sql("WITH t AS (SELECT * FROM secrets) SELECT * FROM t"),
            policy,
        )
        assert refusal is not None

    def test_a_second_statement_cannot_ride_along(self) -> None:
        policy = SqlShellPolicy()
        refusal = check_policy(analyze_sql("SELECT 1; DROP TABLE orders"), policy)
        assert refusal is not None
        assert "2 statements" in refusal

    def test_multi_statement_can_be_opened_up_deliberately(self) -> None:
        policy = SqlShellPolicy(max_statements=5)
        assert check_policy(analyze_sql("SELECT 1; SELECT 2"), policy) is None

    def test_a_mutation_without_where_is_refused(self) -> None:
        policy = SqlShellPolicy(capabilities={SqlCapability.UPDATE})
        refusal = check_policy(analyze_sql("UPDATE orders SET total = 0"), policy)
        assert refusal is not None
        assert "WHERE" in refusal

    def test_a_mutation_with_where_passes(self) -> None:
        policy = SqlShellPolicy(capabilities={SqlCapability.UPDATE})
        allowed = analyze_sql("UPDATE orders SET total = 0 WHERE id = 1")
        assert check_policy(allowed, policy) is None

    def test_the_where_rule_can_be_switched_off(self) -> None:
        policy = SqlShellPolicy(
            capabilities={SqlCapability.DELETE},
            require_where=False,
        )
        assert check_policy(analyze_sql("DELETE FROM orders"), policy) is None


class TestExecution:
    @pytest.mark.asyncio
    async def test_a_select_returns_columns_and_rows(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        service = SqlShellService(db, dialect="sqlite")
        result = await service.execute("SELECT id, total FROM orders ORDER BY id")
        assert result.columns == ["id", "total"]
        assert result.rows == [[1, 100], [2, 200]]
        assert result.row_count == 2
        assert result.truncated is False
        assert result.seconds >= 0.0

    @pytest.mark.asyncio
    async def test_rows_are_capped_and_flagged(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        service = SqlShellService(
            db,
            policy=SqlShellPolicy(max_rows=1),
            dialect="sqlite",
        )
        result = await service.execute("SELECT id FROM orders")
        assert len(result.rows) == 1
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_a_refused_statement_raises_denied(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        service = SqlShellService(db, dialect="sqlite")
        with pytest.raises(SqlShellDenied, match="drop is not permitted"):
            await service.execute("DROP TABLE orders")

    @pytest.mark.asyncio
    async def test_a_refused_statement_never_reaches_the_database(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        service = SqlShellService(db, dialect="sqlite")
        with pytest.raises(SqlShellDenied):
            await service.execute("DROP TABLE orders")
        survivor = await service.execute("SELECT count(*) FROM orders")
        assert survivor.rows[0][0] == 2

    @pytest.mark.asyncio
    async def test_a_read_only_policy_rolls_back(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        service = SqlShellService(db, dialect="sqlite")
        await service.execute("SELECT count(*) FROM orders")
        check = await service.execute("SELECT count(*) FROM orders")
        assert check.rows[0][0] == 2

    @pytest.mark.asyncio
    async def test_a_permitted_mutation_persists(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        service = SqlShellService(
            db,
            policy=SqlShellPolicy(
                capabilities={SqlCapability.READ, SqlCapability.UPDATE},
            ),
            dialect="sqlite",
        )
        result = await service.execute("UPDATE orders SET total = 999 WHERE id = 1")
        assert result.row_count == 1
        check = await service.execute("SELECT total FROM orders WHERE id = 1")
        assert check.rows[0][0] == 999

    @pytest.mark.asyncio
    async def test_a_database_error_becomes_a_shell_error(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        service = SqlShellService(db, dialect="sqlite")
        with pytest.raises(SqlShellError):
            await service.execute("SELECT * FROM does_not_exist")


class TestAudit:
    @pytest.mark.asyncio
    async def test_a_successful_run_is_audited(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        seen: list[SqlAudit] = []

        async def auditor(entry: SqlAudit) -> None:
            seen.append(entry)

        service = SqlShellService(db, dialect="sqlite", auditor=auditor)
        await service.execute("SELECT id FROM orders", principal="ops@x.com")
        assert seen[0].principal == "ops@x.com"
        assert seen[0].allowed is True
        assert seen[0].capability == SqlCapability.READ
        assert seen[0].tables == ["orders"]
        assert seen[0].row_count == 2

    @pytest.mark.asyncio
    async def test_a_refusal_is_audited_too(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        seen: list[SqlAudit] = []

        async def auditor(entry: SqlAudit) -> None:
            seen.append(entry)

        service = SqlShellService(db, dialect="sqlite", auditor=auditor)
        with pytest.raises(SqlShellDenied):
            await service.execute("DROP TABLE orders", principal="ops@x.com")
        assert seen[0].allowed is False
        assert "drop is not permitted" in seen[0].reason
        assert seen[0].sql == "DROP TABLE orders"

    @pytest.mark.asyncio
    async def test_unparseable_input_is_audited(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        seen: list[SqlAudit] = []

        async def auditor(entry: SqlAudit) -> None:
            seen.append(entry)

        service = SqlShellService(db, dialect="sqlite", auditor=auditor)
        with pytest.raises(SqlShellError):
            await service.execute("SELECT FROM ((((", principal="ops@x.com")
        assert seen[0].allowed is False
        assert "parse" in seen[0].reason

    @pytest.mark.asyncio
    async def test_a_sync_auditor_works(self, db: AsyncDatabaseManager) -> None:
        seen: list[SqlAudit] = []
        service = SqlShellService(db, dialect="sqlite", auditor=seen.append)
        await service.execute("SELECT 1")
        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_a_broken_auditor_does_not_break_the_console(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        def broken(_entry: SqlAudit) -> None:
            raise RuntimeError("sink down")

        service = SqlShellService(db, dialect="sqlite", auditor=broken)
        result = await service.execute("SELECT id FROM orders")
        assert result.row_count == 2


class TestMissingExtra:
    def test_the_import_error_names_the_extra(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import builtins

        from tempest_fastapi_sdk.admin import sql_shell

        real_import = builtins.__import__

        def fail(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "sqlglot":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail)
        with pytest.raises(ImportError, match=r"\[admin-sql\]"):
            sql_shell._require_sqlglot()

"""A SQL console for the admin, with a policy in front of it.

Every serious admin panel grows one of these — phpMyAdmin, Adminer,
Metabase's native query, Django SQL Explorer — because eventually someone
needs an answer the list view cannot give. This is that console, plus the
guard rails to make it survivable.

## Read this before you enable it

**A SQL filter in the application is defence in depth, not a security
boundary.** The analyser here parses statements properly (via `sqlglot`)
rather than matching strings, which stops the ordinary mistakes: a
`DROP` typed by someone who only meant to `SELECT`, an `UPDATE` with no
`WHERE`, a query against a table that holds card data. It will not stop a
determined operator with time — SQL has CTEs, subqueries, functions,
dialect extensions and comment tricks, and any parser-based allowlist is
a game of coverage.

The boundary that actually holds is the **database user**. A role granted
only `SELECT` on three tables cannot `DROP` anything no matter what
reaches it:

```sql
CREATE ROLE admin_console LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE app TO admin_console;
GRANT SELECT ON orders, customers, invoices TO admin_console;
```

Point the console at *that* connection, then use the policy below to
narrow further and to produce a readable refusal instead of a database
error. Used that way the two layers complement each other. Used alone,
the policy is a speed bump.

The console is **off by default** and every attempt — allowed or refused
— goes to the audit hook.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from pydantic import Field

from tempest_fastapi_sdk.core import BaseStrEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

_EXTRA_HINT: str = (
    "The admin SQL console requires sqlglot. Install with: "
    "pip install tempest-fastapi-sdk[admin-sql]"
)


def _require_sqlglot() -> Any:
    """Import ``sqlglot`` or raise a helpful error.

    Returns:
        Any: The ``sqlglot`` module.

    Raises:
        ImportError: When the ``[admin-sql]`` extra is not installed.
    """
    try:
        import sqlglot
    except ImportError as exc:
        raise ImportError(_EXTRA_HINT) from exc
    return sqlglot


class SqlCapability(BaseStrEnum):
    """What a console may do, one statement family per member.

    Split the way an operator thinks about risk rather than the way SQL
    groups keywords: ``DELETE`` is separate from ``UPDATE`` because losing
    rows and corrupting them are different incidents, and ``DROP`` is
    separate from the rest of DDL because it is the one nobody undoes.

    * ``READ`` — ``SELECT``, ``WITH`` … ``SELECT``, ``EXPLAIN``, ``SHOW``.
    * ``INSERT`` — adds rows.
    * ``UPDATE`` — changes rows.
    * ``DELETE`` — removes rows.
    * ``DDL`` — ``CREATE`` / ``ALTER`` / ``COMMENT``.
    * ``DROP`` — ``DROP`` and ``TRUNCATE``: irreversible structure loss.
    * ``ADMIN`` — ``GRANT`` / ``REVOKE`` / ``SET`` and anything the
      analyser cannot classify. Unknown statements land here on purpose,
      so a construct nobody anticipated needs the most privileged
      capability rather than slipping through as harmless.
    """

    READ = "read"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    DDL = "ddl"
    DROP = "drop"
    ADMIN = "admin"


#: Statement families that change data or structure.
MUTATING: frozenset[SqlCapability] = frozenset(
    {
        SqlCapability.INSERT,
        SqlCapability.UPDATE,
        SqlCapability.DELETE,
        SqlCapability.DDL,
        SqlCapability.DROP,
        SqlCapability.ADMIN,
    },
)

_EXPRESSION_CAPABILITY: dict[str, SqlCapability] = {
    "Select": SqlCapability.READ,
    "Union": SqlCapability.READ,
    "Intersect": SqlCapability.READ,
    "Except": SqlCapability.READ,
    "Subquery": SqlCapability.READ,
    "With": SqlCapability.READ,
    "Describe": SqlCapability.READ,
    "Show": SqlCapability.READ,
    "Pragma": SqlCapability.READ,
    "Insert": SqlCapability.INSERT,
    "Update": SqlCapability.UPDATE,
    "Delete": SqlCapability.DELETE,
    "Merge": SqlCapability.UPDATE,
    "Create": SqlCapability.DDL,
    "Alter": SqlCapability.DDL,
    "AlterTable": SqlCapability.DDL,
    "Comment": SqlCapability.DDL,
    "Drop": SqlCapability.DROP,
    "TruncateTable": SqlCapability.DROP,
    "Grant": SqlCapability.ADMIN,
    "Revoke": SqlCapability.ADMIN,
    "Set": SqlCapability.ADMIN,
    "Transaction": SqlCapability.ADMIN,
    "Commit": SqlCapability.ADMIN,
    "Rollback": SqlCapability.ADMIN,
}
"""sqlglot expression class name to capability.

Anything absent maps to :attr:`SqlCapability.ADMIN` — see that member's
docstring for why the unknown case is the privileged one.
"""


class SqlShellError(Exception):
    """The console refused or could not run a statement."""


class SqlShellDenied(SqlShellError):  # noqa: N818
    """The policy refused the statement.

    Distinct from a database error so a caller can tell "you are not
    allowed" from "your SQL is wrong" — the two need different messages
    and different log severities.
    """


class SqlStatement(BaseSchema):
    """One analysed statement.

    Attributes:
        sql (str): The statement as it will be executed.
        capability (SqlCapability): What running it would do.
        tables (list[str]): Tables it touches, lowercased and without a
            schema qualifier.
        has_where (bool): Whether a mutating statement carries a ``WHERE``.
        returns_rows (bool): Whether to expect a result set.
    """

    sql: str = Field(
        title="SQL",
        description="The statement as it will be executed.",
        examples=["SELECT * FROM orders LIMIT 10"],
    )
    capability: SqlCapability = Field(
        title="Capability",
        description="What running this statement would do.",
        examples=["read"],
    )
    tables: list[str] = Field(
        default_factory=list,
        title="Tables",
        description="Tables the statement touches.",
        examples=[["orders"]],
    )
    has_where: bool = Field(
        default=False,
        title="Has WHERE",
        description="Whether a mutating statement carries a WHERE clause.",
        examples=[True],
    )
    returns_rows: bool = Field(
        default=False,
        title="Returns rows",
        description="Whether to expect a result set.",
        examples=[True],
    )


class SqlShellPolicy(BaseSchema):
    """What the console is allowed to do.

    The default is the safest useful thing: read-only, every table, a
    thousand rows. Widening it is a deliberate act.

    Attributes:
        capabilities (set[SqlCapability]): Statement families allowed.
            Defaults to read-only.
        allowed_tables (set[str]): When non-empty, **only** these tables
            may be touched. Compared lowercased and without schema.
        denied_tables (set[str]): Never touchable, even when listed in
            ``allowed_tables`` — deny wins, so adding a table here cannot
            be undone by a broad allow rule elsewhere.
        max_rows (int): Rows fetched from a result set before truncating.
            Protects the browser and the server, not the database.
        max_statements (int): How many statements one submission may
            carry. Defaults to ``1``: multi-statement input is how an
            allowed `SELECT` smuggles a `DROP` past a reader skimming the
            box.
        require_where (bool): Refuse ``UPDATE`` / ``DELETE`` without a
            ``WHERE``. The single most common console accident.
        statement_timeout_ms (int | None): Server-side statement timeout
            where the dialect supports it. ``None`` leaves the database
            default.
    """

    capabilities: set[SqlCapability] = Field(
        default_factory=lambda: {SqlCapability.READ},
        title="Capabilities",
        description="Statement families the console may run.",
    )
    allowed_tables: set[str] = Field(
        default_factory=set,
        title="Allowed tables",
        description="When non-empty, the only touchable tables.",
        examples=[{"orders", "customers"}],
    )
    denied_tables: set[str] = Field(
        default_factory=set,
        title="Denied tables",
        description="Never touchable; deny wins over allow.",
        examples=[{"users", "user_tokens"}],
    )
    max_rows: int = Field(
        default=1000,
        gt=0,
        title="Max rows",
        description="Rows fetched before truncating the result.",
        examples=[1000],
    )
    max_statements: int = Field(
        default=1,
        gt=0,
        title="Max statements",
        description="Statements allowed per submission.",
        examples=[1],
    )
    require_where: bool = Field(
        default=True,
        title="Require WHERE",
        description="Refuse UPDATE/DELETE without a WHERE clause.",
        examples=[True],
    )
    statement_timeout_ms: int | None = Field(
        default=10_000,
        gt=0,
        title="Statement timeout (ms)",
        description="Server-side timeout where the dialect supports it.",
        examples=[10_000],
    )

    @property
    def read_only(self) -> bool:
        """Return whether the policy permits nothing that mutates.

        Returns:
            bool: ``True`` when every allowed capability is read-only,
            which is what lets the service open a read-only transaction.
        """
        return not (self.capabilities & MUTATING)

    def table_allowed(self, table: str) -> bool:
        """Return whether ``table`` may be touched.

        Args:
            table (str): The table name, any case.

        Returns:
            bool: Whether the policy permits it. Deny is checked first, so
            a table in both lists is denied.
        """
        name = table.lower()
        if name in {item.lower() for item in self.denied_tables}:
            return False
        if not self.allowed_tables:
            return True
        return name in {item.lower() for item in self.allowed_tables}


class SqlResult(BaseSchema):
    """What running a statement produced.

    Attributes:
        columns (list[str]): Column names, empty for a statement that
            returns no rows.
        rows (list[list[Any]]): The rows, already capped at
            ``policy.max_rows``.
        row_count (int): Rows returned, or rows affected for a mutation.
        truncated (bool): Whether more rows existed than were fetched.
        seconds (float): Wall-clock duration.
        capability (SqlCapability): What the statement did.
        statement (str): The statement that ran.
    """

    columns: list[str] = Field(
        default_factory=list,
        title="Columns",
        description="Column names of the result set.",
    )
    rows: list[list[Any]] = Field(
        default_factory=list,
        title="Rows",
        description="The returned rows, capped by the policy.",
    )
    row_count: int = Field(
        default=0,
        title="Row count",
        description="Rows returned, or affected for a mutation.",
        examples=[42],
    )
    truncated: bool = Field(
        default=False,
        title="Truncated",
        description="Whether more rows existed than were fetched.",
        examples=[False],
    )
    seconds: float = Field(
        default=0.0,
        title="Seconds",
        description="Wall-clock duration.",
        examples=[0.031],
    )
    capability: SqlCapability = Field(
        default=SqlCapability.READ,
        title="Capability",
        description="What the statement did.",
        examples=["read"],
    )
    statement: str = Field(
        default="",
        title="Statement",
        description="The statement that ran.",
    )


class SqlAudit(BaseSchema):
    """One console attempt, allowed or refused.

    Every attempt is audited, including the refused ones — a record of
    what someone *tried* to run is usually more interesting than the list
    of what succeeded.

    Attributes:
        principal (str): Who ran it.
        sql (str): What they submitted, verbatim.
        allowed (bool): Whether the policy let it through.
        capability (SqlCapability | None): What it would do, when the
            analyser could tell.
        tables (list[str]): Tables involved.
        reason (str | None): Why it was refused.
        seconds (float): Duration, ``0`` for a refusal.
        row_count (int): Rows returned or affected.
    """

    principal: str = Field(
        title="Principal",
        description="Who ran the statement.",
        examples=["admin@example.com"],
    )
    sql: str = Field(
        title="SQL",
        description="What was submitted, verbatim.",
    )
    allowed: bool = Field(
        title="Allowed",
        description="Whether the policy let it through.",
        examples=[True],
    )
    capability: SqlCapability | None = Field(
        default=None,
        title="Capability",
        description="What the statement would do.",
    )
    tables: list[str] = Field(
        default_factory=list,
        title="Tables",
        description="Tables involved.",
    )
    reason: str | None = Field(
        default=None,
        title="Reason",
        description="Why the statement was refused.",
    )
    seconds: float = Field(
        default=0.0,
        title="Seconds",
        description="Duration; zero for a refusal.",
    )
    row_count: int = Field(
        default=0,
        title="Row count",
        description="Rows returned or affected.",
    )


#: Called once per console attempt, allowed or refused.
SqlAuditor = Callable[[SqlAudit], Awaitable[None] | None]


def analyze_sql(sql: str, *, dialect: str | None = None) -> list[SqlStatement]:
    """Parse ``sql`` and describe each statement it contains.

    Example:

        >>> analyze_sql("SELECT id FROM orders WHERE id = 1")[0].capability
        <SqlCapability.READ: 'read'>

    Args:
        sql (str): One or more statements.
        dialect (str | None): sqlglot dialect (``"postgres"``,
            ``"sqlite"``). Parsing with the right dialect matters: a
            statement valid in one and not another would otherwise be
            misread.

    Returns:
        list[SqlStatement]: One entry per statement, in order.

    Raises:
        ImportError: When the ``[admin-sql]`` extra is missing.
        SqlShellError: When the SQL does not parse. Refusing unparseable
            input is deliberate — a statement the analyser cannot read is
            a statement the policy cannot judge.
    """
    sqlglot = _require_sqlglot()
    text = sql.strip()
    if not text:
        raise SqlShellError("no statement given")
    try:
        parsed = sqlglot.parse(text, read=dialect)
    except Exception as exc:
        raise SqlShellError(f"could not parse SQL: {exc}") from exc

    statements: list[SqlStatement] = []
    for expression in parsed:
        if expression is None:
            continue
        kind = type(expression).__name__
        capability = _EXPRESSION_CAPABILITY.get(kind, SqlCapability.ADMIN)
        tables = _tables_of(expression, sqlglot)
        statements.append(
            SqlStatement(
                sql=expression.sql(dialect=dialect),
                capability=capability,
                tables=tables,
                has_where=expression.args.get("where") is not None,
                returns_rows=capability == SqlCapability.READ,
            ),
        )
    if not statements:
        raise SqlShellError("no statement given")
    return statements


def _tables_of(expression: Any, sqlglot: Any) -> list[str]:
    """Return every real table an expression references, deduplicated.

    Walks the whole tree, so tables inside CTEs, subqueries and joins are
    found — a policy that only looked at the top-level ``FROM`` would miss
    exactly the places someone hides a table they should not read.

    CTE **names** are subtracted, because they are aliases for a query
    rather than tables: leaving them in makes
    ``WITH recent AS (SELECT … FROM orders) SELECT * FROM recent`` look
    like it touches a table called ``recent``, and an ``allowed_tables``
    policy would refuse a query that only ever reads ``orders``.

    Args:
        expression (Any): A parsed sqlglot expression.
        sqlglot (Any): The sqlglot module.

    Returns:
        list[str]: Lowercased table names without schema, sorted.
    """
    aliases: set[str] = set()
    for cte in expression.find_all(sqlglot.exp.CTE):
        alias = cte.alias_or_name
        if alias:
            aliases.add(str(alias).lower())

    names: set[str] = set()
    for table in expression.find_all(sqlglot.exp.Table):
        name = table.name
        if name and str(name).lower() not in aliases:
            names.add(str(name).lower())
    return sorted(names)


def check_policy(
    statements: list[SqlStatement],
    policy: SqlShellPolicy,
) -> str | None:
    """Return why ``statements`` are refused, or ``None`` when allowed.

    Checks in order of bluntness — count, then capability, then tables,
    then the WHERE rule — so the message names the first and most
    fundamental problem rather than a detail behind it.

    Args:
        statements (list[SqlStatement]): The analysed statements.
        policy (SqlShellPolicy): The policy to apply.

    Returns:
        str | None: A human-readable refusal, or ``None`` to allow.
    """
    if len(statements) > policy.max_statements:
        return (
            f"{len(statements)} statements submitted; the policy allows "
            f"{policy.max_statements}"
        )
    for statement in statements:
        if statement.capability not in policy.capabilities:
            allowed = ", ".join(sorted(str(item) for item in policy.capabilities))
            return (
                f"{statement.capability} is not permitted; this console may: "
                f"{allowed or 'nothing'}"
            )
        for table in statement.tables:
            if not policy.table_allowed(table):
                return f"table {table!r} is not accessible from this console"
        if (
            policy.require_where
            and statement.capability in {SqlCapability.UPDATE, SqlCapability.DELETE}
            and not statement.has_where
        ):
            return (
                f"{statement.capability} without a WHERE clause is refused; "
                "add one, or disable require_where in the policy"
            )
    return None


class SqlShellService:
    """Runs console statements under a policy, and audits every attempt.

    Example:

        >>> service = SqlShellService(
        ...     db,
        ...     policy=SqlShellPolicy(
        ...         capabilities={SqlCapability.READ},
        ...         denied_tables={"users"},
        ...     ),
        ...     dialect="postgres",
        ... )
        >>> result = await service.execute("SELECT 1", principal="ops@x.com")

    Attributes:
        policy (SqlShellPolicy): What the console may do.
        dialect (str | None): sqlglot dialect used to parse.
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        *,
        policy: SqlShellPolicy | None = None,
        dialect: str | None = None,
        auditor: SqlAuditor | None = None,
    ) -> None:
        """Configure the service.

        Args:
            db (AsyncDatabaseManager): The database to run against. Point
                this at a **restricted role** — see the module docstring;
                the policy narrows and explains, the grants enforce.
            policy (SqlShellPolicy | None): What is permitted; a
                read-only default when ``None``.
            dialect (str | None): sqlglot dialect for parsing.
            auditor (SqlAuditor | None): Called once per attempt, allowed
                or refused. Its failures are swallowed so a broken audit
                sink cannot take the console down — but a console you
                cannot audit is one you should turn off, so log there.
        """
        self._db = db
        self.policy = policy or SqlShellPolicy()
        self.dialect = dialect
        self._auditor = auditor

    async def _audit(self, entry: SqlAudit) -> None:
        """Send one entry to the auditor, swallowing its failures."""
        if self._auditor is None:
            return
        try:
            outcome = self._auditor(entry)
            if outcome is not None:
                await outcome
        except Exception:
            pass

    async def execute(self, sql: str, *, principal: str = "unknown") -> SqlResult:
        """Analyse, authorise and run ``sql``.

        Args:
            sql (str): The statement to run.
            principal (str): Who is running it, for the audit trail.

        Returns:
            SqlResult: Columns, rows (capped), row count and timing.

        Raises:
            SqlShellDenied: When the policy refuses it.
            SqlShellError: When it does not parse, or the database
                rejects it.
        """
        try:
            statements = analyze_sql(sql, dialect=self.dialect)
        except SqlShellError as exc:
            await self._audit(
                SqlAudit(
                    principal=principal,
                    sql=sql,
                    allowed=False,
                    reason=str(exc),
                ),
            )
            raise

        refusal = check_policy(statements, self.policy)
        statement = statements[0]
        if refusal is not None:
            await self._audit(
                SqlAudit(
                    principal=principal,
                    sql=sql,
                    allowed=False,
                    capability=statement.capability,
                    tables=statement.tables,
                    reason=refusal,
                ),
            )
            raise SqlShellDenied(refusal)

        started = time.monotonic()
        try:
            result = await self._run(statement)
        except Exception as exc:
            await self._audit(
                SqlAudit(
                    principal=principal,
                    sql=sql,
                    allowed=True,
                    capability=statement.capability,
                    tables=statement.tables,
                    reason=f"database error: {exc}",
                    seconds=time.monotonic() - started,
                ),
            )
            raise SqlShellError(str(exc)) from exc

        result.seconds = time.monotonic() - started
        await self._audit(
            SqlAudit(
                principal=principal,
                sql=sql,
                allowed=True,
                capability=statement.capability,
                tables=statement.tables,
                seconds=result.seconds,
                row_count=result.row_count,
            ),
        )
        return result

    async def _run(self, statement: SqlStatement) -> SqlResult:
        """Execute one approved statement.

        A read under a read-only policy runs inside a transaction that is
        rolled back, so a `SELECT` that turns out to mutate — a function
        with a side effect, a dialect quirk — leaves nothing behind.

        Args:
            statement (SqlStatement): The approved statement.

        Returns:
            SqlResult: The outcome.
        """
        from sqlalchemy import text as sql_text

        async with self._db.get_session_context() as session:
            await self._apply_timeout(session)
            cursor: Any = await session.execute(sql_text(statement.sql))
            result = SqlResult(
                capability=statement.capability,
                statement=statement.sql,
            )
            if cursor.returns_rows:
                columns = list(cursor.keys())
                fetched = cursor.fetchmany(self.policy.max_rows + 1)
                truncated = len(fetched) > self.policy.max_rows
                rows = fetched[: self.policy.max_rows]
                result.columns = columns
                result.rows = [list(row) for row in rows]
                result.row_count = len(result.rows)
                result.truncated = truncated
            else:
                result.row_count = int(cursor.rowcount or 0)
            if self.policy.read_only:
                await session.rollback()
            return result

    async def _apply_timeout(self, session: Any) -> None:
        """Apply the statement timeout where the dialect supports one.

        PostgreSQL has ``SET LOCAL statement_timeout``; SQLite has no
        equivalent, so the setting is silently skipped rather than
        pretending to protect something it does not.

        Args:
            session (Any): The open session.
        """
        if self.policy.statement_timeout_ms is None:
            return
        from sqlalchemy import text as sql_text

        dialect = session.bind.dialect.name if session.bind is not None else ""
        if dialect == "postgresql":
            timeout = int(self.policy.statement_timeout_ms)
            await session.execute(
                sql_text(f"SET LOCAL statement_timeout = {timeout}"),
            )


__all__: list[str] = [
    "MUTATING",
    "SqlAudit",
    "SqlAuditor",
    "SqlCapability",
    "SqlResult",
    "SqlShellDenied",
    "SqlShellError",
    "SqlShellPolicy",
    "SqlShellService",
    "SqlStatement",
    "analyze_sql",
    "check_policy",
]

"""Guard: every engine the SDK builds goes through the SQLite configurator.

Before #395 the SDK had two engine paths that had already drifted:
``AsyncDatabaseManager.connect`` applied the savepoint fix and
``create_test_engine`` applied nothing, and neither enforced foreign
keys. Both now call ``_configure_sqlite_engine``. A third
``create_async_engine`` added somewhere else would silently start a new
divergence, so every engine construction in the package must either sit
in a function that calls the configurator or be listed below with the
reason it must not.
"""

import ast
from pathlib import Path

PACKAGE: Path = Path(__file__).resolve().parent.parent / "tempest_fastapi_sdk"
"""Root of the shipped package."""

ENGINE_FACTORIES: frozenset[str] = frozenset(
    {
        "create_async_engine",
        "create_engine",
        "async_engine_from_config",
        "engine_from_config",
    }
)
"""SQLAlchemy calls that build an engine."""

CONFIGURATOR: str = "_configure_sqlite_engine"
"""The one function that decides what an SDK SQLite engine looks like."""

ALLOWLIST: dict[tuple[str, str], str] = {
    ("db/migrations.py", "AlembicHelper._read"): (
        "migration read engine: foreign keys must stay off on every engine "
        "that runs Alembic, because batch mode rebuilding a parent cascades "
        "to its children (pinned in tests/db/test_migrations_foreign_keys.py)"
    ),
    ("db/migrations.py", "AlembicHelper._read_via_async._run"): (
        "async fallback of the migration read engine; same reason as _read"
    ),
    ("db/_alembic_templates/env.py.template", "run_async_migrations"): (
        "the generated env.py migration engine; foreign keys must stay off "
        "for batch mode, and a handed-over connection is checked by "
        "require_sqlite_foreign_keys_off instead"
    ),
}
"""``(path relative to the package, function qualname)`` -> reason."""


def _called_name(node: ast.Call) -> str | None:
    """Return the bare name a call targets, for ``f()`` and ``mod.f()``."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def engine_constructions(source: str) -> list[tuple[str, bool]]:
    """List every engine construction in ``source``.

    Args:
        source (str): Python source to scan.

    Returns:
        list[tuple[str, bool]]: One ``(qualname, configured)`` pair per
        engine-building call, where ``qualname`` is the enclosing function
        (``"<module>"`` at top level) and ``configured`` says whether that
        same function also calls the configurator.
    """
    found: list[tuple[str, bool]] = []

    def visit(node: ast.AST, scope: list[str], body: ast.AST | None) -> None:
        """Walk ``node`` tracking the enclosing qualname and function body."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, [*scope, child.name], child)
            elif isinstance(child, ast.ClassDef):
                visit(child, [*scope, child.name], body)
            else:
                if (
                    isinstance(child, ast.Call)
                    and _called_name(child) in ENGINE_FACTORIES
                ):
                    configured = body is not None and any(
                        isinstance(inner, ast.Call)
                        and _called_name(inner) == CONFIGURATOR
                        for inner in ast.walk(body)
                    )
                    found.append((".".join(scope) or "<module>", configured))
                visit(child, scope, body)

    visit(ast.parse(source), [], None)
    return found


def _package_sources() -> list[Path]:
    """Every Python and Alembic template file shipped in the package."""
    return sorted([*PACKAGE.rglob("*.py"), *PACKAGE.rglob("env.py.template")])


def test_every_engine_construction_is_configured_or_allowlisted() -> None:
    offenders: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in _package_sources():
        relative = path.relative_to(PACKAGE).as_posix()
        for qualname, configured in engine_constructions(path.read_text("utf-8")):
            key = (relative, qualname)
            seen.add(key)
            if not configured and key not in ALLOWLIST:
                offenders.append(f"{relative}:{qualname}")
    assert offenders == [], (
        "engine built without _configure_sqlite_engine; call it (gated on "
        "SQLite) or add the function to ALLOWLIST with the reason: "
        f"{offenders}"
    )
    assert set(ALLOWLIST) <= seen, f"stale allowlist entries: {set(ALLOWLIST) - seen}"


def test_both_sdk_engine_paths_are_detected_as_configured() -> None:
    """The guard sees the two paths that motivated it, and sees them fixed."""
    manager = engine_constructions(
        (PACKAGE / "db" / "connection.py").read_text("utf-8")
    )
    testing = engine_constructions(
        (PACKAGE / "testing" / "database.py").read_text("utf-8")
    )
    assert manager == [("AsyncDatabaseManager.connect", True)]
    assert testing == [("create_test_engine", True)]


def test_guard_fires_on_the_pre_395_test_engine() -> None:
    """``create_test_engine`` as it shipped through 0.303.2 is flagged."""
    shipped = """
def create_test_engine(database_url: str = "sqlite+aiosqlite:///:memory:"):
    kwargs = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(database_url, **kwargs)
"""
    assert engine_constructions(shipped) == [("create_test_engine", False)]

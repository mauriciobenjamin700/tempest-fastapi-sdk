"""Guard: ``render_enum_types`` is assignable to Alembic's ``render_item``.

Through 0.335.0 it was annotated ``-> str | bool | None``, while Alembic
types ``render_item`` as ``RenderItemFn`` — ``str | Literal[False]`` — in
both 1.19.1 (our floor) and 1.20.0. ``render_item=render_enum_types`` in a
consumer's ``env.py``, the wiring the docstring prescribes, was an
``arg-type`` error under mypy. Measured in the punk-store service, which
carried a ``type: ignore[arg-type]`` for it.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa

from tempest_fastapi_sdk.db.enum_migrations import render_enum_types

PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent.parent
"""The repository root."""

CONSUMER_SNIPPET: str = """from alembic.runtime.environment import RenderItemFn

from tempest_fastapi_sdk.db.enum_migrations import render_enum_types

render_item: RenderItemFn = render_enum_types
"""
"""What an ``env.py`` hands to ``context.configure(render_item=...)``."""


def test_render_enum_types_is_a_render_item_fn(tmp_path: Path) -> None:
    """mypy --strict accepts the function where Alembic expects ``RenderItemFn``.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    mypy_api = pytest.importorskip("mypy.api", reason="mypy is a dev-group dependency")
    module: Path = tmp_path / "consumer_env.py"
    module.write_text(CONSUMER_SNIPPET, encoding="utf-8")
    digest: str = hashlib.sha256(str(PACKAGE_ROOT).encode("utf-8")).hexdigest()[:12]
    cache: Path = Path(tempfile.gettempdir()) / f"tempest-mypy-{digest}-render-item"
    stdout, _stderr, _status = mypy_api.run(
        [
            str(module),
            "--strict",
            "--no-error-summary",
            "--hide-error-context",
            "--no-color-output",
            "--cache-dir",
            str(cache),
            "--python-executable",
            sys.executable,
        ]
    )
    errors: list[str] = [line for line in stdout.splitlines() if ": error:" in line]
    assert not errors, "\n".join(errors)


def test_type_bound_check_is_still_omitted() -> None:
    """The omit answer is still ``None`` at runtime, which Alembic drops."""
    table = sa.Table(
        "t",
        sa.MetaData(),
        sa.Column("state", sa.Enum("a", "b", name="state", create_constraint=True)),
    )
    bound: list[sa.CheckConstraint] = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
        and getattr(constraint, "_type_bound", False)
    ]
    assert bound, "SQLAlchemy no longer marks the enum CHECK as type bound"
    assert render_enum_types("check", bound[0], None) is None  # type: ignore[arg-type]
    assert render_enum_types("check", sa.CheckConstraint("1 = 1"), None) is False  # type: ignore[arg-type]

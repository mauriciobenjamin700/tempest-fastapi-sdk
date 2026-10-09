"""Static-typing regression tests for ``BaseController``'s generic bounds.

The bug these guard against is invisible at runtime: ``BaseController`` and
``BaseService`` both declare their ``UpdateT`` parameter with a PEP 696 default
of ``BaseSchema``. A bound written as ``BaseService[Any, Any]`` is therefore not
a partial application — the checker fills ``UpdateT`` with ``BaseSchema``, and
since that parameter is invariant the bound admits only services whose update
schema is exactly ``BaseSchema``. Every service declared the documented way,
``BaseService[Repo, Resp, MyUpdateSchema]``, was rejected with
``Type parameter "UpdateT@BaseService" is invariant``.

Nothing fails at import time, and ``mypy`` only runs over the package (not over
``tests/``), so the regular suite could not see it. These tests close both gaps:
one inspects the bound directly, the other runs ``mypy`` over a snippet shaped
like real downstream code.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path
from typing import Any, get_args

import pytest

from tempest_fastapi_sdk.controllers.base import ServiceT

DOWNSTREAM_SNIPPET = '''\
"""Downstream service/controller pair with a concrete update schema."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseController,
    BaseModel,
    BaseRepository,
    BaseResponseSchema,
    BaseSchema,
    BaseService,
)


class Widget(BaseModel):
    __tablename__ = "widget_typing_snippet"

    name: Mapped[str] = mapped_column(String(64), nullable=False)


class WidgetResponseSchema(BaseResponseSchema):
    name: str


class WidgetUpdateSchema(BaseSchema):
    name: str | None = None


class WidgetRepository(BaseRepository[Widget]):
    pass


class WidgetService(
    BaseService[WidgetRepository, WidgetResponseSchema, WidgetUpdateSchema]
):
    pass


class WidgetController(
    BaseController[WidgetService, WidgetResponseSchema, WidgetUpdateSchema]
):
    pass
'''


class TestServiceTBound:
    def test_bound_spells_every_service_parameter(self) -> None:
        """``ServiceT``'s bound must parameterize all three of ``BaseService``.

        Written as ``BaseService[Any, Any]``, the omitted ``UpdateT`` resolves to
        its ``BaseSchema`` default and pins the bound to that exact schema. The
        third explicit ``Any`` is what keeps the invariant parameter open.
        """
        bound = ServiceT.__bound__
        assert bound is not None
        assert get_args(bound) == (Any, Any, Any)


def _shared_mypy_cache(tag: str) -> Path:
    """Return a mypy cache directory that survives between runs.

    A cache under ``tmp_path`` started cold on every run and re-checked the
    whole SDK: 38 s for this test alone in the 2026-10-09 ``--durations``
    report. mypy's incremental mode is sound -- a changed module is
    re-analysed -- so a persistent cache only skips work whose inputs did not
    move. The directory is keyed on the repository path (a worktree gets its
    own) and on ``tag`` (no two tests write the same cache concurrently),
    the same scheme ``tests/test_docs_type_guard.py`` uses.

    Args:
        tag (str): Name of the test owning the cache.

    Returns:
        Path: The cache directory.
    """
    root: str = str(Path(__file__).resolve().parents[2])
    digest: str = hashlib.sha256(root.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"tempest-mypy-{digest}-{tag}"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestDownstreamTypeChecks:
    def test_controller_accepts_service_with_concrete_update_schema(
        self, tmp_path: Path
    ) -> None:
        """``mypy`` must accept a controller typed over a concrete update schema.

        Runs the checker over a snippet written the way the docs tell services to
        be written, then asserts no ``type-var`` diagnostic surfaced. Only that
        error code is asserted on, so unrelated diagnostics from the snippet's
        environment cannot make this test flap.
        """
        mypy_api = pytest.importorskip(
            "mypy.api", reason="mypy is a dev-group dependency"
        )
        module = tmp_path / "downstream_widget.py"
        module.write_text(DOWNSTREAM_SNIPPET, encoding="utf-8")

        stdout, stderr, _ = mypy_api.run(
            [
                str(module),
                "--strict",
                "--no-error-summary",
                "--hide-error-context",
                "--cache-dir",
                str(_shared_mypy_cache("generic-bounds")),
                "--python-executable",
                sys.executable,
            ]
        )

        type_var_errors = [line for line in stdout.splitlines() if "[type-var]" in line]
        assert not type_var_errors, f"{type_var_errors}\n{stderr}"

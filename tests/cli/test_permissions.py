"""Tests for tempest_fastapi_sdk.cli.permissions."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.cli.openapi_errors import analyze_paths
from tempest_fastapi_sdk.cli.permissions import analyze_permissions

EXCEPTIONS_MODULE: str = '''"""Domain exceptions."""

from tempest_fastapi_sdk import ForbiddenException


class NotOrderOwnerException(ForbiddenException):
    """The user does not own the order."""

    code = "NOT_ORDER_OWNER"
'''

MODELS_MODULE: str = '''"""Database models."""

from tempest_fastapi_sdk import BaseUserModel


class UserModel(BaseUserModel):
    """Application user."""

    __tablename__ = "user"
'''

GOOD_GUARDS: str = '''"""Authorization guards."""

from src.core.exceptions import NotOrderOwnerException
from src.db.models import UserModel


def order_owner(user: UserModel) -> UserModel:
    """Assert the user owns the order.

    Args:
        user (UserModel): The authenticated user.

    Returns:
        UserModel: The same user.

    Raises:
        NotOrderOwnerException: When the user is not the owner.
    """
    if not user.is_admin:
        raise NotOrderOwnerException()
    return user
'''

GOOD_ROUTER: str = '''"""Order routes."""

from fastapi import Depends
from tempest_fastapi_sdk import requires
from tempest_fastapi_sdk.auth import require_active

from src.api.guards import order_owner
from src.db.models import UserModel


@router.delete("/orders/{order_id}")
@requires(require_active, order_owner)
async def delete_order(
    order_id: str,
    user: UserModel = Depends(get_current_user),
) -> None:
    """Delete an order.

    Args:
        order_id (str): The order to delete.
        user (UserModel): The authenticated user.
    """
    return None
'''


def write_project(root: Path, *, guards: str, router: str) -> Path:
    """Materialize a minimal ``src`` layout to analyze.

    Args:
        root (Path): The temporary project root.
        guards (str): Source of ``src/api/guards.py``.
        router (str): Source of ``src/api/routers/orders.py``.

    Returns:
        Path: The ``src`` directory to scan.
    """
    src = root / "src"
    (src / "core").mkdir(parents=True)
    (src / "db").mkdir(parents=True)
    (src / "api" / "routers").mkdir(parents=True)
    (src / "core" / "exceptions.py").write_text(EXCEPTIONS_MODULE, encoding="utf-8")
    (src / "db" / "models.py").write_text(MODELS_MODULE, encoding="utf-8")
    (src / "api" / "guards.py").write_text(guards, encoding="utf-8")
    (src / "api" / "routers" / "orders.py").write_text(router, encoding="utf-8")
    return src


def codes(
    root: Path,
    *,
    guards: str = GOOD_GUARDS,
    router: str = GOOD_ROUTER,
) -> set[str]:
    """Analyze a generated project and return the finding codes.

    Args:
        root (Path): The temporary project root.
        guards (str): Source of the guards module.
        router (str): Source of the router module.

    Returns:
        set[str]: The codes reported.
    """
    src = write_project(root, guards=guards, router=router)
    return {finding.code for finding in analyze_permissions([src])}


class TestCleanProject:
    def test_no_findings(self, tmp_path: Path) -> None:
        assert codes(tmp_path) == set()

    def test_sdk_guards_are_not_reported(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            "@requires(require_active, order_owner)",
            "@requires(require_active)",
        )
        assert codes(tmp_path, router=router) == set()


class TestGuardDefinitionFindings:
    def test_foreign_exception(self, tmp_path: Path) -> None:
        guards = GOOD_GUARDS.replace(
            "        NotOrderOwnerException: When the user is not the owner.",
            "        ValueError: When the user is not the owner.",
        ).replace("raise NotOrderOwnerException()", 'raise ValueError("nope")')
        assert "guard-foreign-exception" in codes(tmp_path, guards=guards)

    def test_returns_bool(self, tmp_path: Path) -> None:
        guards = GOOD_GUARDS.replace(
            "def order_owner(user: UserModel) -> UserModel:",
            "def order_owner(user: UserModel) -> bool:",
        ).replace("    return user", "    return True")
        assert "guard-returns-bool" in codes(tmp_path, guards=guards)

    def test_arity(self, tmp_path: Path) -> None:
        guards = GOOD_GUARDS.replace(
            "def order_owner(user: UserModel) -> UserModel:",
            "def order_owner(user: UserModel, order: object) -> UserModel:",
        )
        assert "guard-arity" in codes(tmp_path, guards=guards)

    def test_required_keyword_only(self, tmp_path: Path) -> None:
        guards = GOOD_GUARDS.replace(
            "def order_owner(user: UserModel) -> UserModel:",
            "def order_owner(user: UserModel, *, strict: bool) -> UserModel:",
        )
        assert "guard-arity" in codes(tmp_path, guards=guards)

    def test_async_guard_in_sync_function(self, tmp_path: Path) -> None:
        guards = GOOD_GUARDS.replace("def order_owner(", "async def order_owner(")
        router = GOOD_ROUTER.replace("async def delete_order(", "def delete_order(")
        assert "guard-async-in-sync" in codes(tmp_path, guards=guards, router=router)

    def test_async_guard_in_async_function_is_fine(self, tmp_path: Path) -> None:
        guards = GOOD_GUARDS.replace("def order_owner(", "async def order_owner(")
        assert codes(tmp_path, guards=guards) == set()

    def test_never_denies(self, tmp_path: Path) -> None:
        guards = '''"""Authorization guards."""

from src.db.models import UserModel


def order_owner(user: UserModel) -> UserModel:
    """Return the user untouched.

    Args:
        user (UserModel): The authenticated user.

    Returns:
        UserModel: The same user.
    """
    return user
'''
        assert "guard-never-denies" in codes(tmp_path, guards=guards)

    def test_missing_annotations(self, tmp_path: Path) -> None:
        guards = GOOD_GUARDS.replace(
            "def order_owner(user: UserModel) -> UserModel:",
            "def order_owner(user):",
        )
        assert "guard-missing-annotation" in codes(tmp_path, guards=guards)

    def test_unexpected_return_type(self, tmp_path: Path) -> None:
        guards = GOOD_GUARDS.replace(
            "def order_owner(user: UserModel) -> UserModel:",
            "def order_owner(user: UserModel) -> str:",
        ).replace("    return user", '    return "ok"')
        assert "guard-return-type" in codes(tmp_path, guards=guards)

    def test_delegated_raise_counts_as_denial(self, tmp_path: Path) -> None:
        guards = '''"""Authorization guards."""

from src.core.exceptions import NotOrderOwnerException
from src.db.models import UserModel


def _deny() -> None:
    """Deny access.

    Raises:
        NotOrderOwnerException: Always.
    """
    raise NotOrderOwnerException()


def order_owner(user: UserModel) -> UserModel:
    """Assert the user owns the order.

    Args:
        user (UserModel): The authenticated user.

    Returns:
        UserModel: The same user.
    """
    if not user.is_admin:
        _deny()
    return user
'''
        assert codes(tmp_path, guards=guards) == set()


class TestUsageFindings:
    def test_no_guards(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            "@requires(require_active, order_owner)", "@requires()"
        )
        assert "no-guards" in codes(tmp_path, router=router)

    def test_user_param_missing(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            "    user: UserModel = Depends(get_current_user),\n", ""
        )
        assert "user-param-missing" in codes(tmp_path, router=router)

    def test_user_param_ambiguous(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            "    user: UserModel = Depends(get_current_user),",
            "    user: UserModel = Depends(get_current_user),\n"
            "    target: UserModel = Depends(get_target_user),",
        )
        assert "user-param-ambiguous" in codes(tmp_path, router=router)

    def test_explicit_user_param_resolves_ambiguity(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            "    user: UserModel = Depends(get_current_user),",
            "    user: UserModel = Depends(get_current_user),\n"
            "    target: UserModel = Depends(get_target_user),",
        ).replace(
            "@requires(require_active, order_owner)",
            '@requires(require_active, order_owner, user_param="target")',
        )
        assert codes(tmp_path, router=router) == set()

    def test_unknown_explicit_user_param(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            "@requires(require_active, order_owner)",
            '@requires(order_owner, user_param="nope")',
        )
        assert "user-param-missing" in codes(tmp_path, router=router)

    def test_lambda_guard_is_unresolved(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            "@requires(require_active, order_owner)",
            "@requires(lambda user: user)",
        )
        assert "guard-unresolved" in codes(tmp_path, router=router)

    def test_guard_outside_the_scanned_paths(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            "@requires(require_active, order_owner)",
            "@requires(imported_from_elsewhere)",
        )
        assert "guard-unresolved" in codes(tmp_path, router=router)

    def test_decorator_on_a_service_method(self, tmp_path: Path) -> None:
        router = '''"""Order service."""

from tempest_fastapi_sdk import requires

from src.api.guards import order_owner
from src.db.models import UserModel


class OrderService:
    """Business logic for orders."""

    @requires(order_owner)
    async def delete(self, order_id: str, user: UserModel) -> None:
        """Delete an order.

        Args:
            order_id (str): The order to delete.
            user (UserModel): The authenticated user.
        """
        return None
'''
        assert codes(tmp_path, router=router) == set()


class TestErrorDocsIntegration:
    def test_guard_exception_is_reachable_from_the_route(self, tmp_path: Path) -> None:
        src = write_project(tmp_path, guards=GOOD_GUARDS, router=GOOD_ROUTER)
        findings = analyze_paths([src])
        assert [finding.undocumented for finding in findings] == [
            ["NotOrderOwnerException"]
        ]

    def test_declaring_it_clears_the_drift(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            '@router.delete("/orders/{order_id}")',
            "@router.delete(\n"
            '    "/orders/{order_id}",\n'
            "    responses=error_responses(NotOrderOwnerException),\n"
            ")",
        )
        src = write_project(tmp_path, guards=GOOD_GUARDS, router=router)
        assert analyze_paths([src]) == []


class TestCommand:
    def test_clean_project_exits_zero(self, tmp_path: Path) -> None:
        write_project(tmp_path, guards=GOOD_GUARDS, router=GOOD_ROUTER)
        result = CliRunner().invoke(
            app, ["permissions", "--check", "--path", str(tmp_path / "src")]
        )
        assert result.exit_code == 0
        assert "honors its contract" in result.output

    def test_error_fails_the_check(self, tmp_path: Path) -> None:
        guards = GOOD_GUARDS.replace(
            "        NotOrderOwnerException: When the user is not the owner.",
            "        ValueError: When the user is not the owner.",
        ).replace("raise NotOrderOwnerException()", 'raise ValueError("nope")')
        write_project(tmp_path, guards=guards, router=GOOD_ROUTER)
        result = CliRunner().invoke(
            app, ["permissions", "--check", "--path", str(tmp_path / "src")]
        )
        assert result.exit_code == 1
        assert "guard-foreign-exception" in result.output

    def test_warning_only_fails_under_strict(self, tmp_path: Path) -> None:
        router = GOOD_ROUTER.replace(
            "@requires(require_active, order_owner)",
            "@requires(lambda user: user)",
        )
        write_project(tmp_path, guards=GOOD_GUARDS, router=router)
        target = ["--path", str(tmp_path / "src")]

        lenient = CliRunner().invoke(app, ["permissions", "--check", *target])
        assert lenient.exit_code == 0

        strict = CliRunner().invoke(
            app, ["permissions", "--check", "--strict", *target]
        )
        assert strict.exit_code == 1

    def test_missing_source_directory(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            app, ["permissions", "--path", str(tmp_path / "absent")]
        )
        assert result.exit_code == 2

    def test_no_default_source_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app, ["permissions"])
        assert result.exit_code == 2
        assert "No source directory found" in result.output

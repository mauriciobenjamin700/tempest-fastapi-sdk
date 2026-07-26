"""Tests for the ``@requires`` permission decorator."""

from __future__ import annotations

import inspect
import warnings
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    ForbiddenException,
    UnauthorizedException,
    declared_guards,
    guard_metadata,
    guarded_user_param,
    register_exception_handlers,
    requires,
)
from tempest_fastapi_sdk.auth import require_active, require_admin
from tempest_fastapi_sdk.authz import GuardContractWarning, TempestPermissionError
from tempest_fastapi_sdk.db import BaseUserModel


class UserModel(BaseUserModel):
    """Concrete user model for these tests."""

    __tablename__ = "requires_test_user"


def make_user(*, is_admin: bool = False, is_active: bool = True) -> UserModel:
    """Build an unflushed user instance.

    Args:
        is_admin (bool): Whether the user is an administrator.
        is_active (bool): Whether the account is active.

    Returns:
        UserModel: The user.
    """
    return UserModel(
        email="user@example.com",
        hashed_password="x",
        is_admin=is_admin,
        is_active=is_active,
    )


def owner_only(user: UserModel) -> UserModel:
    """Allow only the administrator, as a stand-in for ownership.

    Args:
        user (UserModel): The current user.

    Returns:
        UserModel: The same user.

    Raises:
        ForbiddenException: When the user is not the owner.
    """
    if not user.is_admin:
        raise ForbiddenException(message="Not the owner")
    return user


class TestGuardExecution:
    def test_sync_guard_allows_and_narrows(self) -> None:
        seen: list[UserModel | None] = []

        @requires(require_active)
        def handler(user: UserModel | None) -> str:
            """Record the user the body received."""
            seen.append(user)
            return "ok"

        user = make_user()
        assert handler(user) == "ok"
        assert seen == [user]

    def test_denial_raises_the_guard_exception(self) -> None:
        @requires(owner_only)
        def handler(user: UserModel) -> str:
            """Never reached on denial."""
            return "ok"

        with pytest.raises(ForbiddenException):
            handler(make_user())

    def test_guards_run_left_to_right(self) -> None:
        order: list[str] = []

        def first(user: UserModel) -> UserModel:
            """Record that the first guard ran."""
            order.append("first")
            return user

        def second(user: UserModel) -> UserModel:
            """Record that the second guard ran."""
            order.append("second")
            return user

        @requires(first, second)
        def handler(user: UserModel) -> None:
            """Record that the body ran."""
            order.append("body")

        handler(make_user())
        assert order == ["first", "second", "body"]

    def test_inactive_user_denied_before_the_body(self) -> None:
        @requires(require_active)
        def handler(user: UserModel | None) -> str:
            """Never reached for an inactive user."""
            return "ok"

        with pytest.raises(ForbiddenException):
            handler(make_user(is_active=False))

    def test_missing_user_denied_by_the_sdk_guard(self) -> None:
        @requires(require_active)
        def handler(user: UserModel | None = None) -> str:
            """Never reached without a user."""
            return "ok"

        with pytest.raises(UnauthorizedException):
            handler()

    async def test_async_guard_is_awaited(self) -> None:
        async def async_owner(user: UserModel) -> UserModel:
            """Deny a non-admin asynchronously.

            Args:
                user (UserModel): The current user.

            Returns:
                UserModel: The same user.

            Raises:
                ForbiddenException: When the user is not an admin.
            """
            if not user.is_admin:
                raise ForbiddenException(message="Not the owner")
            return user

        @requires(async_owner)
        async def handler(user: UserModel) -> str:
            """Run only for an admin."""
            return "ok"

        assert await handler(make_user(is_admin=True)) == "ok"
        with pytest.raises(ForbiddenException):
            await handler(make_user())

    async def test_sync_guard_on_async_function(self) -> None:
        @requires(require_admin)
        async def handler(user: UserModel | None) -> str:
            """Run only for an admin."""
            return "ok"

        assert await handler(make_user(is_admin=True)) == "ok"

    def test_guard_return_replaces_the_user(self) -> None:
        replacement = make_user(is_admin=True)

        def swap(user: UserModel) -> UserModel:
            """Return a different user than the one received.

            Args:
                user (UserModel): The current user.

            Returns:
                UserModel: The replacement user.
            """
            return replacement

        @requires(swap)
        def handler(user: UserModel) -> UserModel:
            """Return whichever user the guards produced."""
            return user

        assert handler(make_user()) is replacement

    def test_user_passed_positionally(self) -> None:
        @requires(owner_only)
        def handler(order_id: str, user: UserModel) -> str:
            """Accept the user as the second positional argument."""
            return order_id

        assert handler("order-1", make_user(is_admin=True)) == "order-1"


class TestUserParamResolution:
    def test_explicit_user_param(self) -> None:
        @requires(owner_only, user_param="actor")
        def handler(actor: Any) -> str:
            """Read the user from an untyped parameter."""
            return "ok"

        assert handler(make_user(is_admin=True)) == "ok"
        assert guarded_user_param(handler) == "actor"

    def test_explicit_user_param_disambiguates(self) -> None:
        @requires(owner_only, user_param="target")
        def handler(actor: UserModel, target: UserModel) -> str:
            """Guard the target rather than the actor."""
            return "ok"

        with pytest.raises(ForbiddenException):
            handler(make_user(is_admin=True), make_user())

    def test_unannotated_name_fallback(self) -> None:
        @requires(owner_only)
        def handler(current_user) -> str:  # noqa: ANN001
            """Resolve the user by parameter name."""
            return "ok"

        assert guarded_user_param(handler) == "current_user"

    def test_ambiguous_user_params_rejected(self) -> None:
        with pytest.raises(TempestPermissionError, match="several parameters"):

            @requires(owner_only)
            def handler(actor: UserModel, target: UserModel) -> str:
                """Never decorated successfully."""
                return "ok"

    def test_no_user_param_rejected(self) -> None:
        with pytest.raises(TempestPermissionError, match="no parameter annotated"):

            @requires(owner_only)
            def handler(order_id: str) -> str:
                """Never decorated successfully."""
                return order_id

    def test_unknown_explicit_user_param_rejected(self) -> None:
        with pytest.raises(TempestPermissionError, match="no such parameter"):

            @requires(owner_only, user_param="nope")
            def handler(user: UserModel) -> str:
                """Never decorated successfully."""
                return "ok"


class TestDecorationTimeValidation:
    def test_no_guards_rejected(self) -> None:
        with pytest.raises(TempestPermissionError, match="at least one guard"):
            requires()

    def test_non_callable_guard_rejected(self) -> None:
        with pytest.raises(TempestPermissionError, match="is not callable"):

            @requires("owner")  # type: ignore[arg-type]
            def handler(user: UserModel) -> str:
                """Never decorated successfully."""
                return "ok"

    def test_wrong_arity_rejected(self) -> None:
        def three_params(user: UserModel, meta: dict[str, Any], obj: Any) -> UserModel:
            """Take one parameter too many.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.
                obj (Any): The unfillable extra.

            Returns:
                UserModel: The same user.
            """
            return user

        with pytest.raises(TempestPermissionError, match=r"expected 1 \(user\) or 2"):

            @requires(three_params)
            def handler(user: UserModel) -> str:
                """Never decorated successfully."""
                return "ok"

    def test_required_keyword_only_rejected(self) -> None:
        def keyword_guard(user: UserModel, *, strict: bool) -> UserModel:
            """Require a keyword the decorator cannot supply.

            Args:
                user (UserModel): The current user.
                strict (bool): Unfillable flag.

            Returns:
                UserModel: The same user.
            """
            return user

        with pytest.raises(TempestPermissionError, match="keyword-only"):

            @requires(keyword_guard)
            def handler(user: UserModel) -> str:
                """Never decorated successfully."""
                return "ok"

    def test_async_guard_on_sync_function_rejected(self) -> None:
        async def async_guard(user: UserModel) -> UserModel:
            """Return the user asynchronously.

            Args:
                user (UserModel): The current user.

            Returns:
                UserModel: The same user.
            """
            return user

        with pytest.raises(TempestPermissionError, match="is async but"):

            @requires(async_guard)
            def handler(user: UserModel) -> str:
                """Never decorated successfully."""
                return "ok"

    def test_guard_with_defaults_accepted(self) -> None:
        def tolerant(user: UserModel, obj: Any = None) -> UserModel:
            """Accept an optional second parameter.

            Args:
                user (UserModel): The current user.
                obj (Any): Ignored.

            Returns:
                UserModel: The same user.
            """
            return user

        @requires(tolerant)
        def handler(user: UserModel) -> str:
            """Run with a defaulted guard."""
            return "ok"

        assert handler(make_user()) == "ok"


class TestRuntimeContractWarnings:
    def test_foreign_exception_warns_and_propagates(self) -> None:
        def broken(user: UserModel) -> UserModel:
            """Deny with the wrong exception type.

            Args:
                user (UserModel): The current user.

            Returns:
                UserModel: Never returns.

            Raises:
                ValueError: Always.
            """
            raise ValueError("nope")

        @requires(broken)
        def handler(user: UserModel) -> str:
            """Never reached."""
            return "ok"

        with (
            pytest.warns(GuardContractWarning, match="not an AppException"),
            pytest.raises(ValueError, match="nope"),
        ):
            handler(make_user())

    def test_app_exception_does_not_warn(self) -> None:
        @requires(owner_only)
        def handler(user: UserModel) -> str:
            """Never reached on denial."""
            return "ok"

        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            with pytest.raises(ForbiddenException):
                handler(make_user())
        assert not [w for w in record if w.category is GuardContractWarning]

    def test_bool_return_warns_and_is_ignored(self) -> None:
        def predicate(user: UserModel) -> Any:
            """Return a boolean instead of the user.

            Args:
                user (UserModel): The current user.

            Returns:
                Any: ``False``, which the decorator cannot honor.
            """
            return False

        @requires(predicate)
        def handler(user: UserModel) -> UserModel:
            """Run despite the falsy guard return."""
            return user

        user = make_user()
        with pytest.warns(GuardContractWarning, match="returned bool"):
            assert handler(user) is user

    def test_awaitable_from_a_sync_call_site_raises(self) -> None:
        class LazyUser:
            """Awaitable that is not a coroutine, so decoration allows it."""

            def __init__(self, user: UserModel) -> None:
                self.user: UserModel = user

            def __await__(self) -> Any:
                """Yield the wrapped user.

                Returns:
                    Any: The iterator protocol expected by ``await``.
                """
                return iter([self.user])

        class AwaitableGuard:
            """Callable object returning an awaitable rather than the user."""

            def __call__(self, user: UserModel) -> Any:
                """Return an awaitable the sync wrapper cannot resolve.

                Args:
                    user (UserModel): The current user.

                Returns:
                    Any: The awaitable.
                """
                return LazyUser(user)

        @requires(AwaitableGuard())
        def handler(user: UserModel) -> str:
            """Never reached."""
            return "ok"

        with pytest.raises(TempestPermissionError, match="returned an awaitable"):
            handler(make_user())

    def test_unresolved_dependency_default_raises(self) -> None:
        def get_current_user() -> UserModel:
            """Return a user, never called here.

            Returns:
                UserModel: The current user.
            """
            return make_user()

        @requires(owner_only)
        def handler(user: UserModel = Depends(get_current_user)) -> str:
            """Never reached when called outside FastAPI."""
            return "ok"

        with pytest.raises(TempestPermissionError, match="was called without"):
            handler()


class TestIntrospection:
    def test_declared_guards_lists_the_guards(self) -> None:
        @requires(require_active, owner_only)
        def handler(user: UserModel | None) -> str:
            """Expose its guards for auditing."""
            return "ok"

        assert declared_guards(handler) == (require_active, owner_only)

    def test_undecorated_function_has_no_guards(self) -> None:
        def handler(user: UserModel) -> str:
            """Carry no guards."""
            return "ok"

        assert declared_guards(handler) == ()
        assert guarded_user_param(handler) is None

    def test_signature_is_preserved(self) -> None:
        @requires(owner_only)
        def handler(order_id: str, user: UserModel) -> str:
            """Keep its own signature after decoration."""
            return order_id

        parameters = inspect.signature(handler).parameters
        assert list(parameters) == ["order_id", "user"]
        assert parameters["user"].annotation is UserModel
        assert handler.__name__ == "handler"


class TestFastAPIIntegration:
    def test_route_denies_and_allows(self) -> None:
        app = FastAPI()
        register_exception_handlers(app)
        state: dict[str, UserModel] = {"user": make_user()}

        def get_current_user() -> UserModel:
            """Return the user the test installed.

            Returns:
                UserModel: The current user.
            """
            return state["user"]

        @app.delete("/orders/{order_id}")
        @requires(require_active, owner_only)
        async def delete_order(
            order_id: str,
            user: UserModel = Depends(get_current_user),
        ) -> dict[str, str]:
            """Delete an order the caller owns.

            Args:
                order_id (str): The order to delete.
                user (UserModel): The authenticated user.

            Returns:
                dict[str, str]: The deleted order id.
            """
            return {"order_id": order_id, "email": user.email}

        client = TestClient(app)

        denied = client.delete("/orders/order-1")
        assert denied.status_code == 403
        assert denied.json()["code"] == "FORBIDDEN"

        state["user"] = make_user(is_admin=True)
        allowed = client.delete("/orders/order-1")
        assert allowed.status_code == 200
        assert allowed.json()["order_id"] == "order-1"

    def test_openapi_still_documents_the_path_parameter(self) -> None:
        app = FastAPI()

        def get_current_user() -> UserModel:
            """Return a fixed admin user.

            Returns:
                UserModel: The current user.
            """
            return make_user(is_admin=True)

        @app.get("/orders/{order_id}")
        @requires(owner_only)
        async def get_order(
            order_id: str,
            user: UserModel = Depends(get_current_user),
        ) -> dict[str, str]:
            """Return one order.

            Args:
                order_id (str): The order to read.
                user (UserModel): The authenticated user.

            Returns:
                dict[str, str]: The order id.
            """
            return {"order_id": order_id}

        schema = app.openapi()
        parameters = schema["paths"]["/orders/{order_id}"]["get"]["parameters"]
        assert [param["name"] for param in parameters] == ["order_id"]


class TestMetadata:
    def test_static_meta_reaches_the_guard(self) -> None:
        seen: list[dict[str, Any]] = []

        def has_role(user: UserModel, meta: dict[str, Any]) -> UserModel:
            """Record the metadata and deny when the role is missing.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.

            Returns:
                UserModel: The same user.

            Raises:
                ForbiddenException: When the declared role is missing.
            """
            seen.append(meta)
            if meta["role"] != "manager":
                raise ForbiddenException(message="Role required")
            return user

        @requires(has_role, meta={"role": "manager"})
        def handler(user: UserModel) -> str:
            """Run for a manager."""
            return "ok"

        assert handler(make_user()) == "ok"
        assert seen == [{"role": "manager"}]

    def test_static_meta_denies(self) -> None:
        def has_role(user: UserModel, meta: dict[str, Any]) -> UserModel:
            """Deny unless the declared role matches.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.

            Returns:
                UserModel: The same user.

            Raises:
                ForbiddenException: When the declared role is missing.
            """
            if meta["role"] != "manager":
                raise ForbiddenException(message="Role required")
            return user

        @requires(has_role, meta={"role": "auditor"})
        def handler(user: UserModel) -> str:
            """Never reached."""
            return "ok"

        with pytest.raises(ForbiddenException):
            handler(make_user())

    def test_include_args_merges_the_call_arguments(self) -> None:
        seen: list[dict[str, Any]] = []

        def owns(user: UserModel, meta: dict[str, Any]) -> UserModel:
            """Record the metadata built for this call.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.

            Returns:
                UserModel: The same user.
            """
            seen.append(dict(meta))
            return user

        @requires(owns, include_args=True)
        def handler(order_id: str, user: UserModel, tenant: str = "acme") -> str:
            """Run with the arguments visible to the guard."""
            return order_id

        assert handler("order-1", make_user()) == "order-1"
        assert seen == [{"order_id": "order-1", "tenant": "acme"}]

    def test_include_args_excludes_the_user(self) -> None:
        seen: list[dict[str, Any]] = []

        def probe(user: UserModel, meta: dict[str, Any]) -> None:
            """Record the metadata keys.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.
            """
            seen.append(dict(meta))

        @requires(probe, include_args=True)
        def handler(user: UserModel) -> str:
            """Run with no argument other than the user."""
            return "ok"

        handler(make_user())
        assert seen == [{}]

    def test_static_meta_wins_over_an_argument(self) -> None:
        seen: list[dict[str, Any]] = []

        def probe(user: UserModel, meta: dict[str, Any]) -> None:
            """Record the metadata.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.
            """
            seen.append(dict(meta))

        @requires(probe, meta={"scope": "declared"}, include_args=True)
        def handler(scope: str, user: UserModel) -> str:
            """Run with a colliding argument name."""
            return scope

        handler("from-call", make_user())
        assert seen == [{"scope": "declared"}]

    def test_one_param_guards_are_untouched(self) -> None:
        calls: list[str] = []

        def wants_meta(user: UserModel, meta: dict[str, Any]) -> None:
            """Record that the two-parameter guard got the metadata.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.
            """
            calls.append(f"meta:{meta['role']}")

        def wants_user(user: UserModel) -> None:
            """Record that the one-parameter guard still gets only the user.

            Args:
                user (UserModel): The current user.
            """
            calls.append("user-only")

        @requires(wants_meta, wants_user, meta={"role": "manager"})
        def handler(user: UserModel) -> str:
            """Mix guards that want metadata with guards that do not."""
            return "ok"

        assert handler(make_user()) == "ok"
        assert calls == ["meta:manager", "user-only"]

    def test_guards_share_the_mapping_within_a_call(self) -> None:
        def stamp(user: UserModel, meta: dict[str, Any]) -> None:
            """Add a key for the next guard.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.
            """
            meta["stamped"] = True

        seen: list[dict[str, Any]] = []

        def read(user: UserModel, meta: dict[str, Any]) -> None:
            """Read what the previous guard added.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.
            """
            seen.append(dict(meta))

        @requires(stamp, read, meta={"role": "manager"})
        def handler(user: UserModel) -> str:
            """Run both metadata guards."""
            return "ok"

        handler(make_user())
        handler(make_user())
        assert seen == [
            {"role": "manager", "stamped": True},
            {"role": "manager", "stamped": True},
        ]

    def test_declared_meta_is_copied_at_decoration(self) -> None:
        declared: dict[str, Any] = {"role": "manager"}

        def probe(user: UserModel, meta: dict[str, Any]) -> None:
            """Assert the guard sees the value declared at decoration.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.
            """
            assert meta["role"] == "manager"

        @requires(probe, meta=declared)
        def handler(user: UserModel) -> str:
            """Run with the copied metadata."""
            return "ok"

        declared["role"] = "intern"
        assert handler(make_user()) == "ok"
        assert guard_metadata(handler) == {"role": "manager"}

    async def test_async_guard_receives_metadata(self) -> None:
        seen: list[dict[str, Any]] = []

        async def probe(user: UserModel, meta: dict[str, Any]) -> UserModel:
            """Record the metadata asynchronously.

            Args:
                user (UserModel): The current user.
                meta (dict[str, Any]): Injected metadata.

            Returns:
                UserModel: The same user.
            """
            seen.append(dict(meta))
            return user

        @requires(probe, meta={"role": "manager"}, include_args=True)
        async def handler(order_id: str, user: UserModel) -> str:
            """Run with metadata on the async path."""
            return order_id

        assert await handler("order-1", make_user()) == "order-1"
        assert seen == [{"order_id": "order-1", "role": "manager"}]

    def test_meta_without_a_consumer_rejected(self) -> None:
        with pytest.raises(TempestPermissionError, match="no guard declares"):

            @requires(owner_only, meta={"role": "manager"})
            def handler(user: UserModel) -> str:
                """Never decorated successfully."""
                return "ok"

    def test_include_args_without_a_consumer_rejected(self) -> None:
        with pytest.raises(TempestPermissionError, match="no guard declares"):

            @requires(owner_only, include_args=True)
            def handler(user: UserModel) -> str:
                """Never decorated successfully."""
                return "ok"

    def test_non_mapping_meta_rejected(self) -> None:
        with pytest.raises(TempestPermissionError, match="must be a mapping"):
            requires(owner_only, meta=["role"])  # type: ignore[arg-type]

    def test_metadata_is_empty_without_configuration(self) -> None:
        @requires(owner_only)
        def handler(user: UserModel) -> str:
            """Carry no declared metadata."""
            return "ok"

        assert guard_metadata(handler) == {}
        assert guard_metadata(owner_only) == {}

"""Tests for the object-level permission framework."""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest

from tempest_fastapi_sdk import ForbiddenException
from tempest_fastapi_sdk.authz import (
    PermissionMixin,
    PermissionRegistry,
    check_permission,
    default_registry,
    has_perm,
    permission,
)


class User:
    """Minimal user stand-in with the attributes the resolvers read."""

    def __init__(
        self,
        *,
        is_admin: bool = False,
        permissions: tuple[str, ...] = (),
    ) -> None:
        self.id: UUID = uuid4()
        self.is_admin: bool = is_admin
        self.permissions: set[str] = set(permissions)


class Order:
    """Minimal owned object."""

    def __init__(self, owner_id: UUID) -> None:
        self.owner_id: UUID = owner_id


@pytest.fixture
def registry() -> PermissionRegistry:
    return PermissionRegistry()


class TestHasPerm:
    async def test_none_user_denied(self, registry: PermissionRegistry) -> None:
        assert await registry.has_perm(None, "order.delete") is False

    async def test_superuser_bypasses(self, registry: PermissionRegistry) -> None:
        admin = User(is_admin=True)
        # No rule, no static permission — still allowed via bypass.
        assert await registry.has_perm(admin, "order.delete") is True

    async def test_object_rule_grants_owner_denies_other(
        self, registry: PermissionRegistry
    ) -> None:
        owner = User()
        other = User()
        order = Order(owner_id=owner.id)

        @registry.rule("order.delete")
        def only_owner(user: User, obj: Order) -> bool:
            return obj is not None and obj.owner_id == user.id

        assert await registry.has_perm(owner, "order.delete", order) is True
        assert await registry.has_perm(other, "order.delete", order) is False

    async def test_static_fallback_when_no_rule(
        self, registry: PermissionRegistry
    ) -> None:
        holder = User(permissions=("order.read",))
        nobody = User()
        assert await registry.has_perm(holder, "order.read") is True
        assert await registry.has_perm(nobody, "order.read") is False

    async def test_blanket_static_applies_to_object_without_rule(
        self, registry: PermissionRegistry
    ) -> None:
        holder = User(permissions=("order.read",))
        order = Order(owner_id=uuid4())
        # No object rule for order.read → blanket capability applies.
        assert await registry.has_perm(holder, "order.read", order) is True

    async def test_wildcard_prefix_rule(self, registry: PermissionRegistry) -> None:
        user = User()

        @registry.rule("order.*")
        def allow_all_order(_user: User, _obj: object) -> bool:
            return True

        assert await registry.has_perm(user, "order.delete") is True
        assert await registry.has_perm(user, "order.update") is True
        assert await registry.has_perm(user, "invoice.delete") is False

    async def test_global_wildcard_rule(self, registry: PermissionRegistry) -> None:
        user = User()
        registry.register("*", lambda _u, _o: True)
        assert await registry.has_perm(user, "anything.at.all") is True

    async def test_async_rule_is_awaited(self, registry: PermissionRegistry) -> None:
        user = User()

        @registry.rule("order.delete")
        async def allow(_user: User, _obj: object) -> bool:
            return True

        assert await registry.has_perm(user, "order.delete") is True

    async def test_object_none_with_rules_uses_static_too(
        self, registry: PermissionRegistry
    ) -> None:
        holder = User(permissions=("order.delete",))

        @registry.rule("order.delete")
        def deny(_user: User, _obj: object) -> bool:
            return False

        # Rule denies for obj=None, but the static set still grants.
        assert await registry.has_perm(holder, "order.delete") is True

    async def test_async_resolver(self) -> None:
        async def resolver(user: User) -> set[str]:
            return {"order.read"}

        registry = PermissionRegistry(permission_resolver=resolver)
        assert await registry.has_perm(User(), "order.read") is True

    async def test_custom_superuser_predicate(self) -> None:
        registry = PermissionRegistry(
            is_superuser=lambda u: getattr(u, "is_root", False)
        )
        user = User()
        user.is_root = True  # type: ignore[attr-defined]
        assert await registry.has_perm(user, "order.delete") is True


class TestCheckPermission:
    async def test_allows_silently(self, registry: PermissionRegistry) -> None:
        registry.register("order.read", lambda _u, _o: True)
        await registry.check_permission(User(), "order.read")

    async def test_raises_forbidden_on_deny(self, registry: PermissionRegistry) -> None:
        with pytest.raises(ForbiddenException, match=r"order\.delete"):
            await registry.check_permission(User(), "order.delete")


class TestModuleLevelHelpers:
    @pytest.fixture(autouse=True)
    def _clear_default(self) -> Iterator[None]:
        default_registry.clear()
        yield
        default_registry.clear()

    async def test_permission_decorator_and_has_perm(self) -> None:
        owner = User()
        order = Order(owner_id=owner.id)

        @permission("order.delete")
        def only_owner(user: User, obj: Order) -> bool:
            return obj.owner_id == user.id

        assert await has_perm(owner, "order.delete", order) is True
        assert await has_perm(User(), "order.delete", order) is False

    async def test_module_check_permission_raises(self) -> None:
        with pytest.raises(ForbiddenException):
            await check_permission(User(), "order.delete", Order(uuid4()))

    async def test_registry_override(self) -> None:
        reg = PermissionRegistry()
        reg.register("x.do", lambda _u, _o: True)
        # Default registry has no such rule; the override registry does.
        assert await has_perm(User(), "x.do", registry=reg) is True
        assert await has_perm(User(), "x.do") is False


class TestPermissionMixin:
    @pytest.fixture(autouse=True)
    def _clear_default(self) -> Iterator[None]:
        default_registry.clear()
        yield
        default_registry.clear()

    async def test_user_has_perm_method(self) -> None:
        class MixedUser(PermissionMixin):
            def __init__(self, owner: bool) -> None:
                self.id: UUID = uuid4()
                self.is_admin: bool = False
                self.permissions: set[str] = set()
                self._owner = owner

        @permission("order.delete")
        def only_owner(user: MixedUser, obj: object) -> bool:
            return user._owner

        assert await MixedUser(owner=True).has_perm("order.delete", obj=object())
        assert not await MixedUser(owner=False).has_perm("order.delete", obj=object())

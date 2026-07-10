"""Object-level permission framework.

Static role/permission checks (``make_permission_dependency`` in
``tempest_fastapi_sdk.api``) answer "does this token carry the
``orders:write`` capability?". They cannot answer "may **this** user
edit **this** order?" — the answer depends on the row (ownership,
tenant, workflow state), not just the token.

This module adds that object level. You register a predicate per
permission string and ask the registry:

```python
from tempest_fastapi_sdk.authz import permission, has_perm


@permission("order.delete")
def only_owner_deletes(user: UserModel, order: OrderModel) -> bool:
    return order.owner_id == user.id


allowed: bool = await has_perm(current_user, "order.delete", obj=order)
```

Resolution order for ``has_perm(user, perm, obj)``:

1. ``user`` is ``None`` → ``False``.
2. Superuser bypass — ``is_superuser(user)`` (default: the user's
   ``is_admin`` attribute) → ``True``.
3. If object-level rules are registered for ``perm``:
   * ``obj`` given → granted when **any** rule returns truthy
     (rules are authoritative for the object level).
   * ``obj`` is ``None`` → granted when the static permission set holds
     ``perm`` **or** any rule (called with ``obj=None``) returns truthy.
4. No rule registered → fall back to the static permission set
   (``permission_resolver(user)``); a blanket capability applies to
   every object.

Rules match by exact string or a trailing-``*`` / global ``*`` pattern
(``"order.*"`` matches ``"order.delete"``; ``"*"`` matches everything),
so you can register a broad rule and a specific one together.
"""

from __future__ import annotations

import inspect as _inspect
from collections.abc import Awaitable, Callable
from typing import Any

from tempest_fastapi_sdk.exceptions.forbidden import ForbiddenException

#: A predicate deciding one permission for a ``(user, obj)`` pair.
#: ``obj`` is ``None`` for model-level checks. Sync or ``async``.
PermissionCheck = Callable[[Any, Any], bool | Awaitable[bool]]

#: Resolves the static (object-independent) permission strings a user
#: holds. Sync or ``async``.
PermissionResolver = Callable[[Any], set[str] | Awaitable[set[str]]]

#: Decides whether a user bypasses every check. Always synchronous.
SuperuserPredicate = Callable[[Any], bool]


def _default_superuser(user: Any) -> bool:
    """Treat a user with a truthy ``is_admin`` attribute as superuser.

    Args:
        user (Any): The user under evaluation.

    Returns:
        bool: ``True`` when ``user.is_admin`` is truthy.
    """
    return bool(getattr(user, "is_admin", False))


def _default_resolver(user: Any) -> set[str]:
    """Read static permissions from a ``permissions`` attribute.

    Accepts any iterable of strings; missing attribute → empty set.

    Args:
        user (Any): The user whose static permissions to read.

    Returns:
        set[str]: The permission strings the user holds.
    """
    raw = getattr(user, "permissions", None)
    if raw is None:
        return set()
    return {str(p) for p in raw}


def _matches(pattern: str, permission: str) -> bool:
    """Return whether ``pattern`` covers ``permission``.

    ``"*"`` matches everything; a trailing ``".*"`` matches any
    permission under that dotted prefix; otherwise an exact match.

    Args:
        pattern (str): The registered rule key.
        permission (str): The permission being checked.

    Returns:
        bool: ``True`` when the pattern applies.
    """
    if pattern == "*" or pattern == permission:
        return True
    if pattern.endswith(".*"):
        return permission.startswith(pattern[:-1])
    return False


class PermissionRegistry:
    """A registry of object-level permission rules.

    Instantiate your own for isolated rule sets (tests, plugins), or use
    the process-wide :data:`default_registry` via the module-level
    :func:`permission` / :func:`has_perm` / :func:`check_permission`
    helpers.

    Attributes:
        is_superuser (SuperuserPredicate): Bypass predicate.
        permission_resolver (PermissionResolver): Static-permission
            resolver used as the fallback.
    """

    def __init__(
        self,
        *,
        is_superuser: SuperuserPredicate = _default_superuser,
        permission_resolver: PermissionResolver = _default_resolver,
    ) -> None:
        """Initialize the registry.

        Args:
            is_superuser (SuperuserPredicate): Predicate granting a
                blanket bypass. Defaults to reading ``user.is_admin``.
            permission_resolver (PermissionResolver): Resolver for the
                static permission set. Defaults to reading
                ``user.permissions``.
        """
        self.is_superuser: SuperuserPredicate = is_superuser
        self.permission_resolver: PermissionResolver = permission_resolver
        self._rules: dict[str, list[PermissionCheck]] = {}

    def register(self, permission: str, check: PermissionCheck) -> None:
        """Register ``check`` as a rule for ``permission``.

        Args:
            permission (str): The permission string (or ``*`` pattern).
            check (PermissionCheck): The predicate ``(user, obj) ->
                bool`` (sync or async).
        """
        self._rules.setdefault(permission, []).append(check)

    def clear(self) -> None:
        """Drop every registered rule.

        Intended for test isolation — call it in a fixture teardown so
        rules registered against :data:`default_registry` by one test
        never leak into the next.
        """
        self._rules.clear()

    def rule(self, permission: str) -> Callable[[PermissionCheck], PermissionCheck]:
        """Decorator form of :meth:`register`.

        Args:
            permission (str): The permission string (or ``*`` pattern).

        Returns:
            Callable[[PermissionCheck], PermissionCheck]: A decorator
            registering the wrapped predicate and returning it
            unchanged.
        """

        def decorator(check: PermissionCheck) -> PermissionCheck:
            self.register(permission, check)
            return check

        return decorator

    def _rules_for(self, permission: str) -> list[PermissionCheck]:
        """Collect every rule whose pattern covers ``permission``.

        Args:
            permission (str): The permission being checked.

        Returns:
            list[PermissionCheck]: The matching predicates.
        """
        matched: list[PermissionCheck] = []
        for pattern, checks in self._rules.items():
            if _matches(pattern, permission):
                matched.extend(checks)
        return matched

    async def _static_permissions(self, user: Any) -> set[str]:
        """Resolve the user's static permission set, awaiting if needed.

        Args:
            user (Any): The user under evaluation.

        Returns:
            set[str]: The static permission strings.
        """
        result = self.permission_resolver(user)
        if _inspect.isawaitable(result):
            return await result
        return result

    async def has_perm(
        self,
        user: Any,
        permission: str,
        obj: Any = None,
    ) -> bool:
        """Return whether ``user`` holds ``permission`` (on ``obj``).

        See the module docstring for the full resolution order.

        Args:
            user (Any): The user under evaluation (``None`` → denied).
            permission (str): The permission string, e.g.
                ``"order.delete"``.
            obj (Any): The target object for an object-level check, or
                ``None`` for a model-level check.

        Returns:
            bool: ``True`` when the permission is granted.
        """
        if user is None:
            return False
        if self.is_superuser(user):
            return True

        rules = self._rules_for(permission)
        if rules:
            for check in rules:
                result = check(user, obj)
                if _inspect.isawaitable(result):
                    result = await result
                if result:
                    return True
            if obj is not None:
                return False
            # obj is None: rules did not grant → try the static set too.

        return permission in await self._static_permissions(user)

    async def check_permission(
        self,
        user: Any,
        permission: str,
        obj: Any = None,
        *,
        message: str | None = None,
    ) -> None:
        """Raise :class:`ForbiddenException` unless the permission holds.

        Args:
            user (Any): The user under evaluation.
            permission (str): The permission string.
            obj (Any): The target object, or ``None``.
            message (str | None): Override for the forbidden message.

        Raises:
            ForbiddenException: When the permission is denied.
        """
        if not await self.has_perm(user, permission, obj):
            raise ForbiddenException(
                message=message or f"Permission denied: {permission}",
            )


#: The process-wide registry backing the module-level helpers.
default_registry = PermissionRegistry()


def permission(
    perm: str,
    *,
    registry: PermissionRegistry | None = None,
) -> Callable[[PermissionCheck], PermissionCheck]:
    """Decorator registering a rule on a registry (default: global).

    Args:
        perm (str): The permission string (or ``*`` pattern).
        registry (PermissionRegistry | None): Target registry;
            ``None`` uses :data:`default_registry`.

    Returns:
        Callable[[PermissionCheck], PermissionCheck]: The decorator.
    """
    return (registry or default_registry).rule(perm)


async def has_perm(
    user: Any,
    permission: str,
    obj: Any = None,
    *,
    registry: PermissionRegistry | None = None,
) -> bool:
    """Module-level :meth:`PermissionRegistry.has_perm`.

    Args:
        user (Any): The user under evaluation.
        permission (str): The permission string.
        obj (Any): The target object, or ``None``.
        registry (PermissionRegistry | None): Target registry; ``None``
            uses :data:`default_registry`.

    Returns:
        bool: ``True`` when the permission is granted.
    """
    return await (registry or default_registry).has_perm(user, permission, obj)


async def check_permission(
    user: Any,
    permission: str,
    obj: Any = None,
    *,
    registry: PermissionRegistry | None = None,
    message: str | None = None,
) -> None:
    """Module-level :meth:`PermissionRegistry.check_permission`.

    Args:
        user (Any): The user under evaluation.
        permission (str): The permission string.
        obj (Any): The target object, or ``None``.
        registry (PermissionRegistry | None): Target registry; ``None``
            uses :data:`default_registry`.
        message (str | None): Override for the forbidden message.

    Raises:
        ForbiddenException: When the permission is denied.
    """
    await (registry or default_registry).check_permission(
        user, permission, obj, message=message
    )


class PermissionMixin:
    """Mixin adding ``await user.has_perm(perm, obj=...)`` to a model.

    Delegates to :data:`default_registry`. Mix into a user model
    alongside :class:`BaseUserModel` when you want the ergonomic
    ``user.has_perm(...)`` call site from the roadmap.
    """

    async def has_perm(self, permission: str, obj: Any = None) -> bool:
        """Return whether this user holds ``permission`` (on ``obj``).

        Args:
            permission (str): The permission string.
            obj (Any): The target object, or ``None``.

        Returns:
            bool: ``True`` when the permission is granted.
        """
        return await default_registry.has_perm(self, permission, obj)


__all__: list[str] = [
    "PermissionCheck",
    "PermissionMixin",
    "PermissionRegistry",
    "PermissionResolver",
    "SuperuserPredicate",
    "check_permission",
    "default_registry",
    "has_perm",
    "permission",
]

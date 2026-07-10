"""Object-level authorization primitives.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit
re-export form together with ``__all__`` so every type-checker accepts
``from tempest_fastapi_sdk.authz import has_perm`` without a diagnostic.
"""

from tempest_fastapi_sdk.authz.dependencies import (
    make_permission_checker as make_permission_checker,
)
from tempest_fastapi_sdk.authz.permissions import PermissionCheck as PermissionCheck
from tempest_fastapi_sdk.authz.permissions import PermissionMixin as PermissionMixin
from tempest_fastapi_sdk.authz.permissions import (
    PermissionRegistry as PermissionRegistry,
)
from tempest_fastapi_sdk.authz.permissions import (
    PermissionResolver as PermissionResolver,
)
from tempest_fastapi_sdk.authz.permissions import (
    SuperuserPredicate as SuperuserPredicate,
)
from tempest_fastapi_sdk.authz.permissions import check_permission as check_permission
from tempest_fastapi_sdk.authz.permissions import default_registry as default_registry
from tempest_fastapi_sdk.authz.permissions import has_perm as has_perm
from tempest_fastapi_sdk.authz.permissions import permission as permission

__all__: list[str] = [
    "PermissionCheck",
    "PermissionMixin",
    "PermissionRegistry",
    "PermissionResolver",
    "SuperuserPredicate",
    "check_permission",
    "default_registry",
    "has_perm",
    "make_permission_checker",
    "permission",
]

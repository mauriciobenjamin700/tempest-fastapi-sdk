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
from tempest_fastapi_sdk.authz.requires import USER_PARAM_NAMES as USER_PARAM_NAMES
from tempest_fastapi_sdk.authz.requires import Guard as Guard
from tempest_fastapi_sdk.authz.requires import (
    GuardContractWarning as GuardContractWarning,
)
from tempest_fastapi_sdk.authz.requires import (
    TempestPermissionError as TempestPermissionError,
)
from tempest_fastapi_sdk.authz.requires import declared_guards as declared_guards
from tempest_fastapi_sdk.authz.requires import guard_metadata as guard_metadata
from tempest_fastapi_sdk.authz.requires import guarded_user_param as guarded_user_param
from tempest_fastapi_sdk.authz.requires import requires as requires

__all__: list[str] = [
    "USER_PARAM_NAMES",
    "Guard",
    "GuardContractWarning",
    "PermissionCheck",
    "PermissionMixin",
    "PermissionRegistry",
    "PermissionResolver",
    "SuperuserPredicate",
    "TempestPermissionError",
    "check_permission",
    "declared_guards",
    "default_registry",
    "guard_metadata",
    "guarded_user_param",
    "has_perm",
    "make_permission_checker",
    "permission",
    "requires",
]

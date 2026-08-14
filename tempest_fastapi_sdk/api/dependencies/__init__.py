"""FastAPI dependency providers used across SDK consumers."""

from tempest_fastapi_sdk.api.dependencies.auth import (
    make_bearer_token_dependency as make_bearer_token_dependency,
)
from tempest_fastapi_sdk.api.dependencies.auth import (
    make_jwt_user_dependency as make_jwt_user_dependency,
)
from tempest_fastapi_sdk.api.dependencies.auth import (
    make_permission_dependency as make_permission_dependency,
)
from tempest_fastapi_sdk.api.dependencies.auth import (
    make_role_dependency as make_role_dependency,
)
from tempest_fastapi_sdk.api.dependencies.auth import (
    make_token_dependency as make_token_dependency,
)
from tempest_fastapi_sdk.api.dependencies.auth import (
    require_x_token as require_x_token,
)

__all__: list[str] = [
    "make_bearer_token_dependency",
    "make_jwt_user_dependency",
    "make_permission_dependency",
    "make_role_dependency",
    "make_token_dependency",
    "require_x_token",
]

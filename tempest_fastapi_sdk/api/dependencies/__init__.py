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
from tempest_fastapi_sdk.api.dependencies.rate_limit import (
    key_by_body_field as key_by_body_field,
)
from tempest_fastapi_sdk.api.dependencies.rate_limit import (
    make_rate_limit_dependency as make_rate_limit_dependency,
)
from tempest_fastapi_sdk.api.dependencies.signed_url import (
    make_signed_path_dependency as make_signed_path_dependency,
)

__all__: list[str] = [
    "key_by_body_field",
    "make_bearer_token_dependency",
    "make_jwt_user_dependency",
    "make_permission_dependency",
    "make_rate_limit_dependency",
    "make_role_dependency",
    "make_signed_path_dependency",
    "make_token_dependency",
    "require_x_token",
]

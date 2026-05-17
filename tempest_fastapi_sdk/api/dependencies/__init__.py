"""FastAPI dependency providers used across SDK consumers."""

from tempest_fastapi_sdk.api.dependencies.auth import (
    make_bearer_token_dependency,
    make_jwt_user_dependency,
    make_permission_dependency,
    make_role_dependency,
    make_token_dependency,
    require_x_token,
)

__all__: list[str] = [
    "make_bearer_token_dependency",
    "make_jwt_user_dependency",
    "make_permission_dependency",
    "make_role_dependency",
    "make_token_dependency",
    "require_x_token",
]

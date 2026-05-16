"""FastAPI dependency providers used across SDK consumers."""

from tempest_fastapi_sdk.api.dependencies.auth import (
    make_token_dependency,
    require_x_token,
)

__all__: list[str] = [
    "make_token_dependency",
    "require_x_token",
]

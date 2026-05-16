"""FastAPI integration primitives exposed at module level."""

from tempest_fastapi_sdk.api.handlers import (
    app_exception_handler,
    register_exception_handlers,
)

__all__: list[str] = [
    "app_exception_handler",
    "register_exception_handlers",
]

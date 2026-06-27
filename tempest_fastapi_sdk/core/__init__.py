"""Core cross-cutting primitives: logging, context, configuration."""

from tempest_fastapi_sdk.core.context import (
    clear_request_id,
    get_request_id,
    request_id_ctx,
    set_request_id,
)
from tempest_fastapi_sdk.core.enums import (
    BaseIntEnum,
    BaseStrEnum,
)
from tempest_fastapi_sdk.core.logging import (
    JSONFormatter,
    configure_logging,
)
from tempest_fastapi_sdk.core.typed import (
    require_annotations,
    strict_types,
    typed,
)

__all__: list[str] = [
    "BaseIntEnum",
    "BaseStrEnum",
    "JSONFormatter",
    "clear_request_id",
    "configure_logging",
    "get_request_id",
    "request_id_ctx",
    "require_annotations",
    "set_request_id",
    "strict_types",
    "typed",
]

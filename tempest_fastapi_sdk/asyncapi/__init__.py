"""AsyncAPI 3.0: generating a typed WebSocket client from a document.

The sibling of :mod:`tempest_fastapi_sdk.openapi`, for the surface OpenAPI
cannot describe. Payload schemas are shared machinery — both specifications
keep JSON Schema under ``components.schemas`` — so what lives here is the
connection, the frames, and the direction each one travels.
"""

from tempest_fastapi_sdk.asyncapi.ir import AsyncApiIR as AsyncApiIR
from tempest_fastapi_sdk.asyncapi.ir import ChannelIR as ChannelIR
from tempest_fastapi_sdk.asyncapi.ir import MessageIR as MessageIR
from tempest_fastapi_sdk.asyncapi.ir import OperationIR as OperationIR
from tempest_fastapi_sdk.asyncapi.ir import StreamIR as StreamIR
from tempest_fastapi_sdk.asyncapi.loader import (
    PERSPECTIVE_EXTENSION as PERSPECTIVE_EXTENSION,
)
from tempest_fastapi_sdk.asyncapi.loader import (
    SERVER_PERSPECTIVE as SERVER_PERSPECTIVE,
)
from tempest_fastapi_sdk.asyncapi.loader import check_perspective as check_perspective
from tempest_fastapi_sdk.asyncapi.loader import check_version as check_version
from tempest_fastapi_sdk.asyncapi.loader import (
    load_asyncapi_spec as load_asyncapi_spec,
)
from tempest_fastapi_sdk.asyncapi.parse import parse_asyncapi as parse_asyncapi

__all__: list[str] = [
    "PERSPECTIVE_EXTENSION",
    "SERVER_PERSPECTIVE",
    "AsyncApiIR",
    "ChannelIR",
    "MessageIR",
    "OperationIR",
    "StreamIR",
    "check_perspective",
    "check_version",
    "load_asyncapi_spec",
    "parse_asyncapi",
]

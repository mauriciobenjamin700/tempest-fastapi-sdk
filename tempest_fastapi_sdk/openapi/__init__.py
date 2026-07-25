"""Generate Pydantic schemas + a typed HTTP client from an OpenAPI spec.

Integrating with a third party means transcribing their documentation by
hand: reading every field, writing the equivalent Pydantic model, choosing
the Python name (``createdAt`` -> ``created_at``), wiring the ``alias`` so
the payload still matches the wire, and then writing another layer just to
assemble the HTTP calls. The specification already describes all of it
formally, so this package does the transcription instead.

Point it at a specification and it writes a self-contained package:

.. code-block:: bash

    tempest openapi-client https://api.example.com/openapi.json --name example

.. code-block:: text

    src/integrations/example/
    |-- __init__.py     re-exports the client
    |-- schemas.py      one class per component, metadata filled in
    `-- client.py       one async method per operation

Every generated ``Field`` carries the ``title`` / ``description`` /
``examples`` the specification provided, so the module doubles as the
integration's documentation and survives the third party changing or
retiring their docs site.

Re-exports follow the PEP 484 ``from x import Y as Y`` explicit re-export
form **in addition to** ``__all__``, so every type-checker accepts
``from tempest_fastapi_sdk.openapi import load_spec`` without a
"private import usage" diagnostic.
"""

from tempest_fastapi_sdk.openapi.emit_client import emit_client as emit_client
from tempest_fastapi_sdk.openapi.emit_schemas import emit_schemas as emit_schemas
from tempest_fastapi_sdk.openapi.generate import GenerationResult as GenerationResult
from tempest_fastapi_sdk.openapi.generate import (
    default_output_dir as default_output_dir,
)
from tempest_fastapi_sdk.openapi.generate import (
    generate_integration as generate_integration,
)
from tempest_fastapi_sdk.openapi.generate import (
    suggest_client_class as suggest_client_class,
)
from tempest_fastapi_sdk.openapi.ir import ClientIR as ClientIR
from tempest_fastapi_sdk.openapi.ir import FieldIR as FieldIR
from tempest_fastapi_sdk.openapi.ir import OperationIR as OperationIR
from tempest_fastapi_sdk.openapi.ir import ParameterIR as ParameterIR
from tempest_fastapi_sdk.openapi.ir import SchemaIR as SchemaIR
from tempest_fastapi_sdk.openapi.ir import SpecIR as SpecIR
from tempest_fastapi_sdk.openapi.loader import SpecError as SpecError
from tempest_fastapi_sdk.openapi.loader import load_spec as load_spec
from tempest_fastapi_sdk.openapi.loader import (
    parse_header_options as parse_header_options,
)
from tempest_fastapi_sdk.openapi.parse import parse_spec as parse_spec

__all__: list[str] = [
    "ClientIR",
    "FieldIR",
    "GenerationResult",
    "OperationIR",
    "ParameterIR",
    "SchemaIR",
    "SpecError",
    "SpecIR",
    "default_output_dir",
    "emit_client",
    "emit_schemas",
    "generate_integration",
    "load_spec",
    "parse_header_options",
    "parse_spec",
    "suggest_client_class",
]

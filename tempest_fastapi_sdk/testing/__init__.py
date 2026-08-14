"""Test helpers shared across SDK consumers.

The utilities here are intentionally framework-agnostic — they don't
require ``pytest`` to be importable so the SDK can be used without
pulling test dependencies into production runtimes. Wrap them in
``@pytest.fixture`` (or any other harness) inside the consuming
project's ``conftest.py``.
"""

from tempest_fastapi_sdk.testing.database import (
    create_test_engine as create_test_engine,
)
from tempest_fastapi_sdk.testing.database import (
    create_test_session_factory as create_test_session_factory,
)
from tempest_fastapi_sdk.testing.database import (
    drop_test_metadata as drop_test_metadata,
)
from tempest_fastapi_sdk.testing.database import (
    init_test_metadata as init_test_metadata,
)
from tempest_fastapi_sdk.testing.database import (
    test_database as test_database,
)
from tempest_fastapi_sdk.testing.database import (
    test_session as test_session,
)
from tempest_fastapi_sdk.testing.factories import (
    ModelFactory as ModelFactory,
)
from tempest_fastapi_sdk.testing.factories import (
    seq as seq,
)

__all__: list[str] = [
    "ModelFactory",
    "create_test_engine",
    "create_test_session_factory",
    "drop_test_metadata",
    "init_test_metadata",
    "seq",
    "test_database",
    "test_session",
]

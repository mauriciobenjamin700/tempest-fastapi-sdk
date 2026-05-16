"""Test helpers shared across SDK consumers.

The utilities here are intentionally framework-agnostic — they don't
require ``pytest`` to be importable so the SDK can be used without
pulling test dependencies into production runtimes. Wrap them in
``@pytest.fixture`` (or any other harness) inside the consuming
project's ``conftest.py``.
"""

from tempest_fastapi_sdk.testing.database import (
    create_test_engine,
    create_test_session_factory,
    drop_test_metadata,
    init_test_metadata,
    test_database,
    test_session,
)

__all__: list[str] = [
    "create_test_engine",
    "create_test_session_factory",
    "drop_test_metadata",
    "init_test_metadata",
    "test_database",
    "test_session",
]

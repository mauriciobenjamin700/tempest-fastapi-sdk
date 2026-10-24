"""Test helpers shared across SDK consumers.

The utilities here are intentionally framework-agnostic — they don't
require ``pytest`` to be importable so the SDK can be used without
pulling test dependencies into production runtimes. Wrap them in
``@pytest.fixture`` (or any other harness) inside the consuming
project's ``conftest.py``.
"""

from tempest_fastapi_sdk.testing.admin import (
    AsyncFormClient as AsyncFormClient,
)
from tempest_fastapi_sdk.testing.admin import FormResponse as FormResponse
from tempest_fastapi_sdk.testing.admin import SyncFormClient as SyncFormClient
from tempest_fastapi_sdk.testing.admin import (
    admin_csrf_token as admin_csrf_token,
)
from tempest_fastapi_sdk.testing.admin import admin_login as admin_login
from tempest_fastapi_sdk.testing.admin import (
    admin_login_sync as admin_login_sync,
)
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
    make_test_database as make_test_database,
)
from tempest_fastapi_sdk.testing.database import (
    make_test_session as make_test_session,
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
from tempest_fastapi_sdk.testing.privacy import (
    assert_subject_graph_valid as assert_subject_graph_valid,
)
from tempest_fastapi_sdk.testing.schemas import (
    SchemaMetadataFinding as SchemaMetadataFinding,
)
from tempest_fastapi_sdk.testing.schemas import (
    assert_schema_metadata as assert_schema_metadata,
)
from tempest_fastapi_sdk.testing.schemas import (
    schema_metadata_findings as schema_metadata_findings,
)

__all__: list[str] = [
    "AsyncFormClient",
    "FormResponse",
    "ModelFactory",
    "SchemaMetadataFinding",
    "SyncFormClient",
    "admin_csrf_token",
    "admin_login",
    "admin_login_sync",
    "assert_schema_metadata",
    "assert_subject_graph_valid",
    "create_test_engine",
    "create_test_session_factory",
    "drop_test_metadata",
    "init_test_metadata",
    "make_test_database",
    "make_test_session",
    "schema_metadata_findings",
    "seq",
    "test_database",
    "test_session",
]

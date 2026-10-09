"""Data-subject export and erasure (LGPD art. 18, GDPR art. 15/17).

Two halves, one per place personal data lives:

- :class:`SubjectGraph` derives, from SQLAlchemy ``MetaData``, the tables a
  delete of the subject's root row cascades to, exports one subject's rows
  without secret columns, and lists the foreign keys that would break
  erasure (:func:`~tempest_fastapi_sdk.testing.assert_subject_graph_valid`
  turns that into a CI guard);
- :class:`SubjectObjectStorage` keeps every object of a subject under one
  prefix of an :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient` and
  erases the prefix in S3 batch deletes.

``SubjectGraph`` needs only SQLAlchemy (base install).
``SubjectObjectStorage`` needs a client, which needs the ``[minio]`` extra;
importing this package does not.
"""

from tempest_fastapi_sdk.privacy.graph import (
    DEFAULT_SECRET_MARKERS as DEFAULT_SECRET_MARKERS,
)
from tempest_fastapi_sdk.privacy.graph import (
    SECRET_INFO_KEY as SECRET_INFO_KEY,
)
from tempest_fastapi_sdk.privacy.graph import (
    SubjectGraph as SubjectGraph,
)
from tempest_fastapi_sdk.privacy.storage import (
    SubjectErasureError as SubjectErasureError,
)
from tempest_fastapi_sdk.privacy.storage import (
    SubjectObjectStorage as SubjectObjectStorage,
)

__all__: list[str] = [
    "DEFAULT_SECRET_MARKERS",
    "SECRET_INFO_KEY",
    "SubjectErasureError",
    "SubjectGraph",
    "SubjectObjectStorage",
]

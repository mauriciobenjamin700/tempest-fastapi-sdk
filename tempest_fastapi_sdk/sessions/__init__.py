"""Server-side session module — store + service + middleware + router.

Alternative to the JWT-based auth flow shipped by
:class:`tempest_fastapi_sdk.UserAuthService`. The cookie carries
only an opaque session id; the rest of the state (user id, TTL,
client metadata, app-level bag) lives in a pluggable
:class:`SessionStore` (``Memory`` for dev / tests, ``Redis`` for
production). Revocation is instant — deleting the row in the
store logs the user out everywhere, with no JWT-style "wait until
the access token expires" lag.

The PEP 484 ``from x import Y as Y`` re-export form is used
alongside ``__all__`` so every type-checker accepts
``from tempest_fastapi_sdk.sessions import SessionAuth`` without
a "private import usage" diagnostic.
"""

from tempest_fastapi_sdk.sessions.dependencies import (
    make_session_dependency as make_session_dependency,
)
from tempest_fastapi_sdk.sessions.middleware import (
    SessionMiddleware as SessionMiddleware,
)
from tempest_fastapi_sdk.sessions.router import (
    make_session_router as make_session_router,
)
from tempest_fastapi_sdk.sessions.schemas import Session as Session
from tempest_fastapi_sdk.sessions.schemas import (
    SessionLoginSchema as SessionLoginSchema,
)
from tempest_fastapi_sdk.sessions.schemas import (
    SessionResponseSchema as SessionResponseSchema,
)
from tempest_fastapi_sdk.sessions.schemas import (
    SessionSummarySchema as SessionSummarySchema,
)
from tempest_fastapi_sdk.sessions.service import SessionAuth as SessionAuth
from tempest_fastapi_sdk.sessions.store import (
    MemorySessionStore as MemorySessionStore,
)
from tempest_fastapi_sdk.sessions.store import (
    RedisSessionStore as RedisSessionStore,
)
from tempest_fastapi_sdk.sessions.store import SessionStore as SessionStore

__all__: list[str] = [
    "MemorySessionStore",
    "RedisSessionStore",
    "Session",
    "SessionAuth",
    "SessionLoginSchema",
    "SessionMiddleware",
    "SessionResponseSchema",
    "SessionStore",
    "SessionSummarySchema",
    "make_session_dependency",
    "make_session_router",
]

"""Django-admin-style management UI for FastAPI services.

Mount via :func:`make_admin_router`; register one :class:`AdminModel`
per SQLAlchemy model on the :class:`AdminSite` instance. The site
auto-derives list/detail views from the existing
:class:`tempest_fastapi_sdk.BaseRepository` so no extra controllers
are needed for read-only management.

Requires the ``[admin]`` optional extra (``jinja2`` +
``itsdangerous``). Authentication relies on
:class:`tempest_fastapi_sdk.BaseUserModel`: any user with
``is_admin=True`` may sign in.
"""

from tempest_fastapi_sdk.admin.auth import (
    AdminAuthBackend,
    AdminAuthError,
    UserModelAuthBackend,
)
from tempest_fastapi_sdk.admin.config import AdminModel, FieldRef, OrderRef
from tempest_fastapi_sdk.admin.router import make_admin_router
from tempest_fastapi_sdk.admin.session import (
    AdminSession,
    SessionStore,
    SignedCookieSessionStore,
)
from tempest_fastapi_sdk.admin.site import AdminSite

__all__: list[str] = [
    "AdminAuthBackend",
    "AdminAuthError",
    "AdminModel",
    "AdminSession",
    "AdminSite",
    "FieldRef",
    "OrderRef",
    "SessionStore",
    "SignedCookieSessionStore",
    "UserModelAuthBackend",
    "make_admin_router",
]

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

from tempest_fastapi_sdk.admin.actions import AdminAction as AdminAction
from tempest_fastapi_sdk.admin.actions import AdminActionContext as AdminActionContext
from tempest_fastapi_sdk.admin.actions import AdminActionResult as AdminActionResult
from tempest_fastapi_sdk.admin.actions import admin_action as admin_action
from tempest_fastapi_sdk.admin.auth import AdminAuthBackend as AdminAuthBackend
from tempest_fastapi_sdk.admin.auth import AdminAuthError as AdminAuthError
from tempest_fastapi_sdk.admin.auth import UserModelAuthBackend as UserModelAuthBackend
from tempest_fastapi_sdk.admin.config import AdminModel as AdminModel
from tempest_fastapi_sdk.admin.config import FieldRef as FieldRef
from tempest_fastapi_sdk.admin.config import Inline as Inline
from tempest_fastapi_sdk.admin.config import OrderRef as OrderRef
from tempest_fastapi_sdk.admin.discovery import discover_models as discover_models
from tempest_fastapi_sdk.admin.router import make_admin_router as make_admin_router
from tempest_fastapi_sdk.admin.session import AdminSession as AdminSession
from tempest_fastapi_sdk.admin.session import SessionStore as SessionStore
from tempest_fastapi_sdk.admin.session import (
    SignedCookieSessionStore as SignedCookieSessionStore,
)
from tempest_fastapi_sdk.admin.site import AdminSite as AdminSite
from tempest_fastapi_sdk.admin.theme import AdminTheme as AdminTheme

__all__: list[str] = [
    "AdminAction",
    "AdminActionContext",
    "AdminActionResult",
    "AdminAuthBackend",
    "AdminAuthError",
    "AdminModel",
    "AdminSession",
    "AdminSite",
    "AdminTheme",
    "FieldRef",
    "Inline",
    "OrderRef",
    "SessionStore",
    "SignedCookieSessionStore",
    "UserModelAuthBackend",
    "admin_action",
    "discover_models",
    "make_admin_router",
]

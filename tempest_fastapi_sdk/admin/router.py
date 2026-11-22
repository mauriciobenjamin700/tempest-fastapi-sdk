"""HTML router wiring the admin site to FastAPI."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import secrets
from collections.abc import Awaitable, Callable, Collection
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import Enum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, cast
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import JSON, and_, event, or_, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import update as sa_update
from sqlalchemy.exc import NoInspectionAvailable
from starlette.concurrency import run_in_threadpool

from tempest_fastapi_sdk.admin.actions import (
    ActionHandler,
    AdminAction,
    AdminActionContext,
    AdminActionResult,
    SchemaActionHandler,
)
from tempest_fastapi_sdk.admin.auth import AdminAuthBackend, AdminAuthError
from tempest_fastapi_sdk.admin.config import AdminModel, Inline
from tempest_fastapi_sdk.admin.csv_import import (
    ADMIN_IMPORT_MAX_BYTES,
    ImportOutcome,
    ImportPayloadError,
    read_import_payload,
    run_import,
    sign_import_payload,
)
from tempest_fastapi_sdk.admin.dashboard import card_entry
from tempest_fastapi_sdk.admin.display import (
    cell_view,
    format_number,
    humanize_field,
    short_identity,
)
from tempest_fastapi_sdk.admin.forms import (
    FormField,
    apply_password_fields,
    build_form_fields,
    fk_fields,
    fk_label,
    inline_editable_names,
    parse_password_submission,
    parse_schema_submission,
    parse_submission,
    schema_form_fields,
    to_display_timezone,
)
from tempest_fastapi_sdk.admin.i18n import admin_plural, admin_text
from tempest_fastapi_sdk.admin.location_map import BRLocationMap
from tempest_fastapi_sdk.admin.permissions import (
    AdminAccessPolicy,
    AdminActionPolicy,
    AdminPermissionEnum,
    AdminSurfaceEnum,
    AdminSurfacePolicy,
)
from tempest_fastapi_sdk.admin.session import (
    ADMIN_SESSION_MAX_AGE_SECONDS,
    AdminLoginCSRF,
    AdminSession,
    AdminSessionRevocations,
    AdminSubmissionNonces,
    SessionStore,
    SignedCookieSessionStore,
    admin_credential_digest,
)
from tempest_fastapi_sdk.admin.site import AdminSite
from tempest_fastapi_sdk.admin.sql_shell import (
    SqlShellDenied,
    SqlShellError,
    SqlShellService,
)
from tempest_fastapi_sdk.admin.storage import (
    StorageBrowserError,
    StorageBrowserService,
    StorageObjectMissingError,
    check_storage_key,
)
from tempest_fastapi_sdk.admin.tasks import TaskPanelService
from tempest_fastapi_sdk.api.routers.logs import (
    LogSource,
    is_log_dir_unset,
    read_log_entries,
    render_entries_json,
    render_entries_markdown,
    resolve_log_files,
    rotated_log_files,
)
from tempest_fastapi_sdk.db.expressions import escape_like
from tempest_fastapi_sdk.db.search import like_search_condition
from tempest_fastapi_sdk.db.signals import RepositorySignalEnum, emit, has_handlers
from tempest_fastapi_sdk.db.transaction import transaction
from tempest_fastapi_sdk.exceptions import AppException
from tempest_fastapi_sdk.ssr.assets import make_htmx_router
from tempest_fastapi_sdk.storage import object_download_response
from tempest_fastapi_sdk.tasks.jobs import (
    CANCELLABLE_JOB_STATUSES,
    JobStatusEnum,
)
from tempest_fastapi_sdk.utils.client_ip import get_client_ip
from tempest_fastapi_sdk.utils.storage_backends import LocalUploadStorage
from tempest_fastapi_sdk.utils.throttle import (
    AttemptThrottle,
    InMemoryThrottleBackend,
    login_throttle_key,
)
from tempest_fastapi_sdk.utils.upload import SNIFFABLE_MIMETYPES, sniff_mime

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
    from tempest_fastapi_sdk.db.model import BaseModel


logger = logging.getLogger(__name__)

LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
"""Hosts a browser treats as a secure context even over ``http://``.

The login page skips the plain-scheme warning on them: a ``Secure``
cookie works on ``http://localhost``, so local development is not the
proxy misconfiguration the warning names.
"""

ADMIN_MFA_THROTTLE_MAX_ATTEMPTS: int = 5
"""Wrong codes one admin principal may submit to ``/mfa`` per window.

The same budget ``make_auth_router`` applies to ``/auth/mfa/verify``.
"""

ADMIN_MFA_THROTTLE_WINDOW_SECONDS: int = 900
"""Length, in seconds, of the default admin ``/mfa`` failure window."""

ADMIN_LOGIN_THROTTLE_MAX_ATTEMPTS: int = 5
"""Password attempts one identifier may spend on ``/login`` per window.

The same budget ``make_auth_router`` applies to ``/auth/login``.
"""

ADMIN_LOGIN_IP_THROTTLE_MAX_ATTEMPTS: int = 20
"""Failed ``/login`` attempts one client IP may make per window.

Four identifiers' worth of the per-identifier budget: enough for a team
behind one office NAT to mistype, low enough that spraying one password
across a list of addresses stops after twenty.
"""

ADMIN_LOGIN_THROTTLE_WINDOW_SECONDS: int = 900
"""Length, in seconds, of the default admin ``/login`` failure windows."""

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
STYLESHEET_VERSION_LENGTH: int = 12
"""Hex characters of the stylesheet digest appended as ``?v=``."""


def _stylesheet_version() -> str:
    """Fingerprint the bundled ``admin.css`` for its URL.

    The stylesheet is served at a fixed path, so a browser that cached it
    kept the old rules after an SDK upgrade and drew the new markup with
    them. ``base.html`` appends this digest as ``?v=``; it changes exactly
    when the file does.

    Returns:
        str: The first :data:`STYLESHEET_VERSION_LENGTH` hex characters of
        the file's SHA-256.
    """
    digest = hashlib.sha256((_STATIC_DIR / "admin.css").read_bytes()).hexdigest()
    return digest[:STYLESHEET_VERSION_LENGTH]


_SCRIPTS_SUBPATH = "/_ssr"
"""Sub-path, under the admin prefix, where the bundled HTMX is served.

The admin mounts :func:`tempest_fastapi_sdk.ssr.assets.make_htmx_router`
here, so ``base.html`` loads ``{prefix}/_ssr/htmx.js`` from the
application itself instead of a CDN.
"""

# Max related rows loaded into a foreign-key <select>. Beyond this the
# dropdown would be unusable (Django switches to raw-id widgets); we cap
# and rely on the plain UUID input being acceptable for huge tables.
FK_OPTION_CAP = 1000


def _pluralize(value: int) -> str:
    """Return ``""`` when ``value`` is 1 and ``"s"`` otherwise.

    Args:
        value (int): The count.

    Returns:
        str: Plural suffix.
    """
    return "" if value == 1 else "s"


class _Pagination:
    """Tiny pagination payload exposed to templates."""

    def __init__(
        self,
        *,
        page: int,
        size: int,
        total: int,
        query_params: dict[str, str],
    ) -> None:
        """Initialize the pagination payload.

        Args:
            page (int): Current 1-indexed page.
            size (int): Page size.
            total (int): Total records.
            query_params (dict[str, str]): Other query parameters to
                preserve when rendering page links.
        """
        self.page: int = page
        self.size: int = size
        self.total: int = total
        self.pages: int = max(1, (total + size - 1) // size) if total else 1
        self._query_params: dict[str, str] = query_params

    def query_for(self, page: int) -> str:
        """Encode the query string for ``page``.

        Args:
            page (int): The target page.

        Returns:
            str: An ``application/x-www-form-urlencoded`` query string.
        """
        params = {**self._query_params, "page": str(page)}
        return urlencode(params)


def make_admin_router(
    site: AdminSite,
    *,
    db: AsyncDatabaseManager,
    auth_backend: AdminAuthBackend[Any],
    secret_key: str,
    prefix: str = "/admin",
    session_store: SessionStore | None = None,
    cookie_secure: bool = True,
    export_max_rows: int = 5000,
    show_metrics: bool = True,
    show_logs: bool = False,
    log_dir: str | Path = "logs",
    access_policy: AdminAccessPolicy | None = None,
    action_policy: AdminActionPolicy | None = None,
    surface_policy: AdminSurfacePolicy | None = None,
    sql_shell: SqlShellService | None = None,
    tasks: TaskPanelService[Any] | None = None,
    storage: StorageBrowserService | None = None,
    mfa_throttle: AttemptThrottle | None = None,
    login_throttle: AttemptThrottle | Literal[False] | None = None,
    login_ip_throttle: AttemptThrottle | Literal[False] | None = None,
    trusted_ip_header: str | None = None,
    session_revocations: AdminSessionRevocations | None = None,
) -> APIRouter:
    """Build the FastAPI router that mounts the admin site.

    Routes attached:

    * ``GET  {prefix}/login`` — login form.
    * ``POST {prefix}/login`` — login submit.
    * ``POST {prefix}/logout`` — clear session + redirect.
    * ``GET/POST {prefix}/mfa`` — TOTP challenge for MFA-enabled
      principals (between the password step and full access).
    * ``GET  {prefix}/`` — dashboard listing registered admins.
    * ``GET  {prefix}/logs`` — structured application logs (when
      ``show_logs=True``).
    * ``GET  {prefix}/m/{slug}/`` — list view (paginated, sortable,
      filterable).
    * ``GET  {prefix}/m/{slug}/export.{fmt}`` — CSV/JSON export of the
      current (filtered + sorted) result set.
    * ``POST {prefix}/m/{slug}/bulk`` — bulk delete / activate /
      deactivate, or a custom action, on the selected rows; a custom action
      declared with ``schema=`` answers its input form first.
    * ``GET/POST {prefix}/m/{slug}/new`` — create form + submit
      (when ``can_create``).
    * ``GET  {prefix}/m/{slug}/{identity}`` — detail view.
    * ``GET/POST {prefix}/m/{slug}/{identity}/edit`` — edit form +
      submit (when ``can_edit``).
    * ``POST {prefix}/m/{slug}/{identity}/delete`` — delete row
      (when ``can_delete``).
    * Static files under ``{prefix}/static`` named ``admin_static``.
    * ``GET  {prefix}/_ssr/htmx.js`` — the HTMX bundled in the SDK
      (``ssr/_static/htmx.min.js``), served without a session because the
      login page loads it too. No admin page fetches a script from a
      third-party host.

    Args:
        site (AdminSite): The configured registry.
        db (AsyncDatabaseManager): Active DB manager (used for both
            sessions and the readiness check).
        auth_backend (AdminAuthBackend[Any]): Backend resolving login
            credentials to a principal.
        secret_key (str): Secret used to sign the session cookie.
            32 bytes minimum.
        prefix (str): URL prefix; defaults to ``"/admin"``.
        session_store (SessionStore | None): Override the default
            :class:`SignedCookieSessionStore`.
        cookie_secure (bool): Set the ``Secure`` flag on cookies.
            Default ``True``; disable only for local HTTP dev.
        export_max_rows (int): Hard cap on rows returned by the
            CSV/JSON export endpoint. Exports beyond this are
            truncated (a header/log notes it) to bound memory.
        show_metrics (bool): When ``True`` (default), the dashboard
            renders a CPU/RAM/disk panel via ``MetricsUtils`` — silently
            omitted when the ``[metrics]`` extra is not installed. Set
            ``False`` to skip the per-request sample entirely.
        show_logs (bool): When ``True``, a ``GET {prefix}/logs`` page is
            mounted and a "Logs" entry appears in the sidebar. It reads
            the structured JSON files written by ``configure_logging``
            (see ``log_dir``) and renders them filtered + paginated.
            Defaults to ``False`` (opt-in — log payloads expose
            tracebacks and request metadata).
        log_dir (str | Path): Directory holding the structured log
            files, matching the ``log_dir`` passed to
            ``configure_logging``. Only consulted when ``show_logs`` is
            ``True``. Defaults to ``"logs"``. When the directory has no
            log files yet the page renders an empty state.
        access_policy (AdminAccessPolicy | None): Optional granular RBAC
            hook asked ``(principal, admin, AdminPermissionEnum)`` for every
            model action. ``None`` (default) allows everything (subject
            to the ``AdminModel.can_*`` flags). A policy that denies an
            action yields ``403`` and hides the model from the dashboard
            + nav for ``VIEW``. Composes with the ``can_*`` flags — both
            must allow. It also governs what a page renders of another
            model: inline tables need ``VIEW`` on the child (an editable
            inline's add row ``CREATE``, its delete box ``DELETE``), and
            foreign-key selects, list filters and autocomplete need
            ``VIEW`` on the referenced admin.
        action_policy (AdminActionPolicy | None): Hook asked
            ``(principal, admin, AdminAction)`` before a custom action runs
            and when the list builds its action menu. When set, a custom
            action needs ``VIEW`` on the model (``access_policy``) and this
            policy's approval, and no longer ``EDIT``: one action can be
            granted without the edit form, and one role can run an action
            another cannot. A denied action is left out of the menu and its
            ``POST`` answers ``403``. ``None`` (default) keeps asking
            ``EDIT`` for every custom action.
        surface_policy (AdminSurfacePolicy | None): Hook asked
            ``(principal, AdminSurfaceEnum)`` before the SQL console, the task
            panel (cancel included) or the logs page (export included) is
            served; a denial yields ``403`` and drops the sidebar entry.
            ``None`` (default) allows every surface when ``access_policy``
            is also ``None``, and **denies** every surface once an
            ``access_policy`` is configured: a principal restricted to
            some models would otherwise read any table through the
            console. Pass one to grant the surfaces to the roles that
            should have them.
        sql_shell (SqlShellService | None): When given, mounts the SQL
            console at ``GET/POST {prefix}/sql`` and adds its nav entry.
        tasks (TaskPanelService[Any] | None): When given, mounts the task
            panel at ``GET {prefix}/tasks`` (declared schedule + persisted
            runs), the run detail at ``GET {prefix}/tasks/{job_id}`` and
            the cancel action at ``POST {prefix}/tasks/{job_id}/cancel``,
            and adds its nav entry. Either half of the service may be
            absent; a section with no source is not rendered.
        storage (StorageBrowserService | None): When given, mounts the
            storage browser at ``GET {prefix}/storage`` (folder listing),
            ``GET {prefix}/storage/object`` (one object: metadata, preview,
            text editor), ``GET {prefix}/storage/raw`` (the bytes, ``inline``
            only for inline-safe types) and the ``POST`` writes under
            ``{prefix}/storage/`` (upload, replace, edit, rename, delete),
            and adds its nav entry. A ``read_only`` service answers ``403``
            to every write.
        mfa_throttle (AttemptThrottle | None): Failure budget for the
            ``POST {prefix}/mfa`` challenge, keyed on the principal, so a
            fresh password login does not reset it. Once spent, the
            challenge answers ``429`` with ``Retry-After`` before the code
            is checked; a correct code resets the key. ``None`` (default)
            allows ``ADMIN_MFA_THROTTLE_MAX_ATTEMPTS`` (5) wrong codes per
            ``ADMIN_MFA_THROTTLE_WINDOW_SECONDS`` (900) on an
            :class:`~tempest_fastapi_sdk.InMemoryThrottleBackend`, which
            counts per process: behind more than one worker, pass an
            :class:`~tempest_fastapi_sdk.AttemptThrottle` on Redis.
        login_throttle (AttemptThrottle | Literal[False] | None): Failure
            budget for ``POST {prefix}/login`` per identifier, keyed
            ``"login:<sha256 of the normalized identifier>"``
            (:func:`~tempest_fastapi_sdk.login_throttle_key`, the key
            ``make_auth_router`` uses). Each attempt reserves one unit
            **before** the password is checked and a successful login
            clears the key, so only failures accumulate; the attempt past
            ``max_attempts`` re-renders the login page with ``429`` and
            ``Retry-After`` whatever the password, so the response is no
            oracle. Spent the same way for an identifier with no account.
            ``None`` (default) allows ``ADMIN_LOGIN_THROTTLE_MAX_ATTEMPTS``
            (5) per ``ADMIN_LOGIN_THROTTLE_WINDOW_SECONDS`` (900) on an
            :class:`~tempest_fastapi_sdk.InMemoryThrottleBackend` — per
            process, so pass one on Redis behind more than one worker.
            ``False`` turns it off.
        login_ip_throttle (AttemptThrottle | Literal[False] | None):
            Failure budget for ``POST {prefix}/login`` per client IP
            (``"admin-login-ip:<addr>"``), for one source trying many
            identifiers. Read before the identifier budget is spent and
            counted only on a refused login; a success does not clear it,
            since one valid account would otherwise reset the counter
            between guesses. ``None`` (default) turns it on with
            ``ADMIN_LOGIN_IP_THROTTLE_MAX_ATTEMPTS`` (20) per 900 s in
            memory **only when** ``trusted_ip_header`` is set, and leaves
            it off otherwise: behind a reverse proxy without that header
            every operator shares the proxy's address, and twenty bad
            logins from anyone would lock all of them out. A throttle
            passed explicitly always applies. ``False`` turns it off.
        trusted_ip_header (str | None): Single edge-set header holding the
            real client IP (e.g. ``"x-real-ip"``) for ``login_ip_throttle``.
            ``None`` uses the transport peer. See
            :func:`~tempest_fastapi_sdk.get_client_ip`.
        session_revocations (AdminSessionRevocations | None): Where
            ``POST {prefix}/logout`` marks the session it ends; every
            request refuses a marked session, so a copy of the cookie
            stops working at logout instead of at ``max_age``. ``None``
            (default) keeps the marks on an
            :class:`~tempest_fastapi_sdk.InMemoryThrottleBackend` for the
            store's ``max_age_seconds`` — per process, so behind more than
            one worker pass ``AdminSessionRevocations(redis_client)``.
            Independently of this, the session is bound to
            :meth:`AdminAuthBackend.credential_fingerprint`, so a password
            change ends every session issued before it, on every replica.

    Returns:
        APIRouter: A router ready to attach via ``app.include_router``.

    Raises:
        ImportError: When the ``[admin]`` extra is not installed, so
            Jinja2 is unavailable to render the templates.
    """
    try:
        from fastapi.templating import Jinja2Templates
    except ImportError as exc:
        raise ImportError(
            "Admin requires the [admin] extra. "
            "Install with `pip install tempest-fastapi-sdk[admin]`."
        ) from exc

    _mounted_surfaces: tuple[AdminSurfaceEnum, ...] = tuple(
        surface
        for surface, mounted in (
            (AdminSurfaceEnum.LOGS, show_logs),
            (AdminSurfaceEnum.SQL, sql_shell is not None),
            (AdminSurfaceEnum.TASKS, tasks is not None),
            (AdminSurfaceEnum.STORAGE, storage is not None),
            (AdminSurfaceEnum.METRICS, bool(site.metrics_areas)),
        )
        if mounted
    )
    store: SessionStore = session_store or SignedCookieSessionStore(
        secret_key,
        secure=cookie_secure,
        path=prefix,
    )
    login_csrf = AdminLoginCSRF(secret_key, secure=cookie_secure, path=prefix)
    revocations: AdminSessionRevocations = session_revocations or (
        AdminSessionRevocations(
            InMemoryThrottleBackend(),
            ttl_seconds=int(
                getattr(store, "max_age_seconds", ADMIN_SESSION_MAX_AGE_SECONDS),
            ),
        )
    )
    nonces = AdminSubmissionNonces(
        revocations.backend, ttl_seconds=revocations.ttl_seconds
    )
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    stylesheet_version = _stylesheet_version()
    templates.env.filters["pluralize"] = _pluralize
    templates.env.filters["cell"] = partial(cell_view, locale=site.locale)
    templates.env.filters["field_label"] = humanize_field
    templates.env.filters["number"] = format_number
    templates.env.filters["short_identity"] = short_identity
    templates.env.globals["t"] = partial(admin_text, site.locale)
    templates.env.globals["tn"] = partial(admin_plural, site.locale)
    templates.env.globals["locale"] = site.locale
    resolved_mfa_throttle: AttemptThrottle = mfa_throttle or AttemptThrottle(
        InMemoryThrottleBackend(),
        max_attempts=ADMIN_MFA_THROTTLE_MAX_ATTEMPTS,
        window_seconds=ADMIN_MFA_THROTTLE_WINDOW_SECONDS,
    )
    resolved_login_throttle: AttemptThrottle | None = (
        None
        if login_throttle is False
        else login_throttle
        or AttemptThrottle(
            InMemoryThrottleBackend(),
            max_attempts=ADMIN_LOGIN_THROTTLE_MAX_ATTEMPTS,
            window_seconds=ADMIN_LOGIN_THROTTLE_WINDOW_SECONDS,
        )
    )
    resolved_login_ip_throttle: AttemptThrottle | None
    if login_ip_throttle is False:
        resolved_login_ip_throttle = None
    elif login_ip_throttle is not None:
        resolved_login_ip_throttle = login_ip_throttle
    elif trusted_ip_header:
        resolved_login_ip_throttle = AttemptThrottle(
            InMemoryThrottleBackend(),
            max_attempts=ADMIN_LOGIN_IP_THROTTLE_MAX_ATTEMPTS,
            window_seconds=ADMIN_LOGIN_THROTTLE_WINDOW_SECONDS,
        )
    else:
        resolved_login_ip_throttle = None

    def _t(key: str, /, **params: Any) -> str:
        """Return the catalog text for ``key`` in the site's locale.

        Args:
            key (str): The :mod:`~tempest_fastapi_sdk.admin.i18n` key.
            **params (Any): Values for the text's placeholders.

        Returns:
            str: The localized text.
        """
        return admin_text(site.locale, key, **params)

    def _tn(key: str, count: int, /, **params: Any) -> str:
        """Return the singular or plural catalog text for ``count``.

        Args:
            key (str): The catalog key without ``.one`` / ``.other``.
            count (int): The number the text describes.
            **params (Any): Further placeholder values.

        Returns:
            str: The localized text.
        """
        return admin_plural(site.locale, key, count, **params)

    router = APIRouter(prefix=prefix, include_in_schema=False)
    router.include_router(make_htmx_router(prefix=_SCRIPTS_SUBPATH))

    @router.get("/static/{path:path}", name="admin_static")
    async def static_files(path: str) -> FileResponse:
        """Serve a file from the admin static directory.

        Args:
            path (str): Relative path under the static directory.

        Returns:
            FileResponse: The requested file.

        Raises:
            HTTPException: ``404`` when the file does not exist or
                escapes the static root.
        """
        target = (_STATIC_DIR / path).resolve()
        if _STATIC_DIR not in target.parents and target != _STATIC_DIR:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        if not target.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        return FileResponse(target)

    _db_session = db.session_dependency

    async def _load_session(request: Request) -> AdminSession | None:
        """Load the request's session, treating a logged-out one as absent.

        Args:
            request (Request): The inbound request.

        Returns:
            AdminSession | None: The session, or ``None`` when the cookie
            is absent, invalid, or was ended by ``POST /logout``.
        """
        session = store.load(request)
        if session is None or await revocations.is_revoked(session):
            return None
        return session

    def _credential_digest(principal: Any) -> str:
        """Return the digest of ``principal``'s credential to bind a session to.

        Args:
            principal (Any): The authenticated principal.

        Returns:
            str: :func:`admin_credential_digest` of the backend's
            fingerprint, ``""`` when the backend binds nothing.
        """
        return admin_credential_digest(
            secret_key, auth_backend.credential_fingerprint(principal)
        )

    def _credential_matches(admin_session: AdminSession, principal: Any) -> bool:
        """Whether the session was issued for the principal's current credential.

        Args:
            admin_session (AdminSession): The session from the cookie.
            principal (Any): The reloaded principal.

        Returns:
            bool: ``False`` once the credential changed after sign-in (or
            for a cookie issued before sessions carried the digest, when
            the backend binds one).
        """
        return hmac.compare_digest(
            admin_session.credential_digest, _credential_digest(principal)
        )

    async def _require_session(request: Request) -> AdminSession:
        """Return the active session or redirect to login.

        Args:
            request (Request): The inbound request.

        Returns:
            AdminSession: The validated session payload.

        Raises:
            HTTPException: ``303`` redirect to ``/login`` when absent or
                logged out.
        """
        session = await _load_session(request)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                detail="login required",
                headers={"location": f"{prefix}/login"},
            )
        if session.mfa_pending:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                detail="mfa required",
                headers={"location": f"{prefix}/mfa"},
            )
        return session

    async def _allows_surface(principal: Any, surface: AdminSurfaceEnum) -> bool:
        """Return whether ``principal`` may open ``surface``.

        Args:
            principal (Any): The current admin principal.
            surface (AdminSurfaceEnum): The panel being opened.

        Returns:
            bool: ``True`` when permitted — always with neither policy
            configured, never with only ``access_policy`` configured, and
            whatever ``surface_policy`` answers when it is given.
        """
        if surface_policy is None:
            return access_policy is None
        result = surface_policy(principal, surface)
        if isinstance(result, bool):
            return result
        return bool(await result)

    async def _resolve_principal(
        request: Request,
        db_session: AsyncSession,
        admin_session: AdminSession,
        *,
        surface: AdminSurfaceEnum | None = None,
    ) -> Any:
        """Reload the admin principal or redirect to login.

        Also records on ``request.state`` which of the mounted surfaces
        the principal may open, so :func:`_render` can drop the sidebar
        entries it would refuse.

        Args:
            request (Request): The inbound request.
            db_session (AsyncSession): The DB session.
            admin_session (AdminSession): The validated cookie.
            surface (AdminSurfaceEnum | None): The non-model panel this
                request serves, if any; the principal must be allowed it.

        Returns:
            Any: The reloaded principal.

        Raises:
            HTTPException: ``303`` redirect when the principal is gone or
                its credential changed after the session was issued;
                ``403`` when ``surface`` is given and denied.
        """
        principal = await auth_backend.load_principal(
            db_session, admin_session.principal_id
        )
        if principal is None or not _credential_matches(admin_session, principal):
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                detail="login required",
                headers={"location": f"{prefix}/login"},
            )
        allowed: set[AdminSurfaceEnum] = set()
        for mounted in _mounted_surfaces:
            if await _allows_surface(principal, mounted):
                allowed.add(mounted)
        request.state.tempest_admin_surfaces = frozenset(allowed)
        request.state.tempest_admin_nav = await _visible_nav(principal)
        request.state.tempest_admin_maps = await _visible_maps(principal)
        if surface is not None and surface not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return principal

    async def _reload_expired(
        db_session: AsyncSession,
        *instances: Any,
    ) -> None:
        """Reload rows a failed write expired, before rendering reads them.

        A rollback expires every object in the identity map, not only the
        row that failed: SQLAlchemy's
        ``SessionTransaction._restore_snapshot`` expires all states
        unconditionally, so ``expire_on_commit=False`` does not cover it.
        The error pages still render the signed-in principal (and, for the
        inline formset, the parent row), and reading an expired column
        from async code emits sync IO, which surfaces as
        ``MissingGreenlet``. Awaiting the reload here keeps that IO inside
        the greenlet SQLAlchemy needs.

        Args:
            db_session (AsyncSession): The live DB session.
            *instances (Any): Rows the caller is about to render. Values
                that are not persistent ORM instances are skipped.
        """
        for instance in instances:
            if instance is None:
                continue
            try:
                state = sa_inspect(instance)
            except NoInspectionAvailable:
                continue
            if state.persistent and state.expired:
                await db_session.refresh(instance)

    def _render(
        request: Request,
        template: str,
        context: dict[str, Any],
        *,
        status_code: int = 200,
    ) -> HTMLResponse:
        """Render ``template`` with the SDK's shared context.

        Args:
            request (Request): The inbound request (required by Jinja).
            template (str): The template filename.
            context (dict[str, Any]): Extra template variables.
            status_code (int): HTTP status code.

        Returns:
            HTMLResponse: The rendered response.

        Notes:
            The sidebar navigation is injected here rather than per view
            because every authenticated page shares it. The entries are
            the models the principal may VIEW, computed once by
            ``_resolve_principal`` and read from ``request.state``, so no
            page can fall back to listing the whole registry; a page
            rendered without a resolved principal (login) gets no
            entries.
        """
        context.setdefault("site", site)
        context.setdefault("messages", [])
        context.setdefault("static_url", f"{prefix}/static")
        context.setdefault("stylesheet_version", stylesheet_version)
        context.setdefault("htmx_url", f"{prefix}{_SCRIPTS_SUBPATH}/htmx.js")
        context.setdefault(
            "nav_models",
            getattr(request.state, "tempest_admin_nav", []),
        )
        context.setdefault(
            "nav_location_maps",
            getattr(request.state, "tempest_admin_maps", []),
        )
        context.setdefault("nav_index_url", f"{prefix}/")
        allowed: frozenset[AdminSurfaceEnum] = getattr(
            request.state, "tempest_admin_surfaces", frozenset()
        )
        context.setdefault(
            "nav_logs_url",
            f"{prefix}/logs" if AdminSurfaceEnum.LOGS in allowed else None,
        )
        context.setdefault(
            "nav_sql_url",
            f"{prefix}/sql" if AdminSurfaceEnum.SQL in allowed else None,
        )
        context.setdefault(
            "nav_tasks_url",
            f"{prefix}/tasks" if AdminSurfaceEnum.TASKS in allowed else None,
        )
        context.setdefault(
            "nav_metrics_areas",
            [
                {"title": area.title, "url": f"{prefix}/metrics/{area.slug}"}
                for area in site.metrics_areas
            ]
            if AdminSurfaceEnum.METRICS in allowed
            else [],
        )
        context.setdefault(
            "nav_storage_url",
            f"{prefix}/storage" if AdminSurfaceEnum.STORAGE in allowed else None,
        )
        return templates.TemplateResponse(
            request,
            template,
            context,
            status_code=status_code,
        )

    def _secrets_response(
        request: Request,
        admin: AdminModel[Any],
        session: AdminSession,
        principal: Any,
        result: AdminActionResult,
        *,
        reload_safe: bool = False,
    ) -> HTMLResponse:
        """Render a custom action's secrets on the action's own response.

        The values never travel in a redirect: a flash rides the query
        string, which lands in the browser history, the access log and
        the next ``Referer``. The page is marked ``no-store`` so neither
        the browser nor a proxy keeps a copy, and ``no-referrer`` so the
        link back to the list does not leak the action URL either.

        Args:
            request (Request): The inbound request.
            admin (AdminModel[Any]): The model the action ran on.
            session (AdminSession): The validated session.
            principal (Any): The admin who ran the action; renders the
                panel chrome (header and sidebar) like every other page.
            result (AdminActionResult): The action's result.
            reload_safe (bool): Whether the ``POST`` carried a single-use
                nonce, so a reload cannot run it again; the page's warning
                says which.

        Returns:
            HTMLResponse: The page, status ``200``.
        """
        response = _render(
            request,
            "action_secrets.html",
            {
                "admin": admin,
                "session": session,
                "user": principal,
                "user_display": auth_backend.display_name(principal),
                "result": result,
                "reload_safe": reload_safe,
                "back_url": f"{prefix}/m/{admin.get_slug()}/",
            },
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def _already_processed(slug: str) -> RedirectResponse:
        """Answer a resubmitted single-use form without running it again.

        A ``303`` to the list turns the browser's reload of the ``POST``
        into a ``GET``, with a warning flash saying why nothing happened.

        Args:
            slug (str): The admin slug.

        Returns:
            RedirectResponse: Redirect to the list with the flash.
        """
        query = urlencode(
            {
                "flash": _t("flash.already_submitted"),
                "flash_cat": "warning",
            }
        )
        return RedirectResponse(
            url=f"{prefix}/m/{slug}/?{query}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    async def _handler_form(
        request: Request,
        admin: AdminModel[Any],
        session: AdminSession,
        principal: Any,
        db_session: AsyncSession,
        *,
        submitted: Any | None = None,
        errors: dict[str, str] | None = None,
        form_error: str | None = None,
        status_code: int = status.HTTP_200_OK,
    ) -> HTMLResponse:
        """Render the form of an admin's ``create_handler``.

        Every render carries a fresh single-use nonce, so a form re-rendered
        after an error can still be submitted once.

        Args:
            request (Request): The inbound request.
            admin (AdminModel[Any]): The admin, with ``create_handler`` set.
            session (AdminSession): The validated session.
            principal (Any): The signed-in admin.
            db_session (AsyncSession): The DB session, for autocomplete labels.
            submitted (Any | None): The submitted form on a re-render.
            errors (dict[str, str] | None): Field errors on a re-render.
            form_error (str | None): A message for the whole form.
            status_code (int): The response status.

        Returns:
            HTMLResponse: ``form.html`` with the schema's controls.
        """
        handler = admin.create_handler
        assert handler is not None
        fields = schema_form_fields(
            handler.schema,
            autocomplete=set(admin.autocomplete_fields),
            submitted=submitted,
            errors=errors,
        )
        if admin.autocomplete_fields:
            await _decorate_autocomplete_fields(admin, fields, db_session, principal)
        slug = admin.get_slug()
        return _render(
            request,
            "form.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "mode": "create",
                "form_title": handler.label,
                "form_fields": fields,
                "action_url": f"{prefix}/m/{slug}/new",
                "back_url": f"{prefix}/m/{slug}/",
                "form_error": form_error or (errors or {}).get("__all__"),
                "submission_nonce": nonces.issue(),
            },
            status_code=status_code,
        )

    async def _run_create_handler(
        request: Request,
        admin: AdminModel[Any],
        session: AdminSession,
        principal: Any,
        db_session: AsyncSession,
        form: Any,
    ) -> Response:
        """Validate a ``create_handler`` submission and run the handler.

        Order matters: the schema is validated first, and an invalid
        submission re-renders without spending the nonce. Only a valid one
        consumes it, right before the handler runs, so a reload of the
        answer is refused while a corrected resubmission is not. An
        ``AppException`` from the handler rolls the session back, reloads
        the principal (the rollback expires it) and re-renders with the
        message and what was typed.

        Args:
            request (Request): The inbound request.
            admin (AdminModel[Any]): The admin, with ``create_handler`` set.
            session (AdminSession): The validated session.
            principal (Any): The signed-in admin.
            db_session (AsyncSession): The DB session.
            form (Any): The submitted form.

        Returns:
            Response: The secrets page (``200``), a redirect to the list
            (``303``, with the flash when the handler returned a message),
            or the re-rendered form (``400``).

        Raises:
            HTTPException: ``400`` when the form carries no nonce.
        """
        handler = admin.create_handler
        assert handler is not None
        data, errors = parse_schema_submission(handler.schema, form)
        if data is None:
            return await _handler_form(
                request,
                admin,
                session,
                principal,
                db_session,
                submitted=form,
                errors=errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        nonce = form.get("submission_nonce")
        if not isinstance(nonce, str) or not nonce:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing submission nonce")
        slug = admin.get_slug()
        if not await nonces.consume(session, nonce):
            return _already_processed(slug)
        context = AdminActionContext(
            ids=[],
            repository=admin.build_repository(db_session),
            db_session=db_session,
            request=request,
            session=session,
            principal=principal,
        )
        try:
            result = await _inserts_in_scope(
                admin,
                db_session,
                principal,
                partial(handler.handler, data, context),
            )
        except (AppException, _RowOutOfScopeError) as exc:
            await db_session.rollback()
            await _reload_expired(db_session, principal)
            return await _handler_form(
                request,
                admin,
                session,
                principal,
                db_session,
                submitted=form,
                form_error=exc.message
                if isinstance(exc, AppException)
                else _t("error.out_of_scope"),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if result is not None and result.secrets:
            await db_session.commit()
            return _secrets_response(
                request, admin, session, principal, result, reload_safe=True
            )
        await db_session.commit()
        url = f"{prefix}/m/{slug}/"
        if result is not None:
            url += "?" + urlencode(
                {"flash": result.message, "flash_cat": result.category}
            )
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    async def _custom_action_form(
        request: Request,
        admin: AdminModel[Any],
        action: AdminAction,
        session: AdminSession,
        principal: Any,
        db_session: AsyncSession,
        ids: list[str],
        *,
        submitted: Any | None = None,
        errors: dict[str, str] | None = None,
        form_error: str | None = None,
        status_code: int = status.HTTP_200_OK,
    ) -> HTMLResponse:
        """Render the input form of a custom action declared with ``schema``.

        The form posts back to the bulk route with the action, the selected
        ids and an ``action_input`` marker in hidden fields, plus a fresh
        single-use nonce.

        Args:
            request (Request): The inbound request.
            admin (AdminModel[Any]): The model the action runs on.
            action (AdminAction): The action, with ``schema`` set.
            session (AdminSession): The validated session.
            principal (Any): The signed-in admin.
            db_session (AsyncSession): The DB session, for autocomplete labels.
            ids (list[str]): The selected ids, as the list submitted them.
            submitted (Any | None): The submitted form on a re-render.
            errors (dict[str, str] | None): Field errors on a re-render.
            form_error (str | None): A message for the whole form.
            status_code (int): The response status.

        Returns:
            HTMLResponse: ``form.html`` with the schema's controls.
        """
        assert action.schema is not None
        fields = schema_form_fields(
            action.schema,
            autocomplete=set(admin.autocomplete_fields),
            submitted=submitted,
            errors=errors,
        )
        if admin.autocomplete_fields:
            await _decorate_autocomplete_fields(admin, fields, db_session, principal)
        slug = admin.get_slug()
        hidden: list[tuple[str, str]] = [
            ("action", f"custom:{action.name}"),
            (_ACTION_INPUT_FIELD, "1"),
            *(("ids", row_id) for row_id in ids),
        ]
        return _render(
            request,
            "form.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "mode": "action",
                "form_title": action.label,
                "form_intro": _tn("action.rows_selected", len(ids)),
                "form_fields": fields,
                "hidden_inputs": hidden,
                "submit_label": action.label,
                "action_url": f"{prefix}/m/{slug}/bulk",
                "back_url": f"{prefix}/m/{slug}/",
                "form_error": form_error or (errors or {}).get("__all__"),
                "submission_nonce": nonces.issue(),
            },
            status_code=status_code,
        )

    async def _call_custom_action(
        action: AdminAction,
        data: Any,
        context: AdminActionContext,
        db_session: AsyncSession,
    ) -> AdminActionResult | None:
        """Run a custom action and commit what it left in the session.

        The session is committed when the handler returns, with or without
        ``secrets`` in the result, and rolled back when it raises, so an
        action that only changes rows through ``ctx.db_session`` or
        ``ctx.repository`` is persisted like a ``create_handler``.

        Args:
            action (AdminAction): The action.
            data (Any): The validated input, for an action with ``schema``;
                ignored otherwise.
            context (AdminActionContext): The selection and the session.
            db_session (AsyncSession): The request session.

        Returns:
            AdminActionResult | None: What the handler returned.

        Raises:
            BaseException: Whatever the handler raised, after the rollback.
        """
        try:
            if action.schema is None:
                plain = cast("ActionHandler", action.handler)
                result = await plain(context)
            else:
                with_input = cast("SchemaActionHandler", action.handler)
                result = await with_input(data, context)
        except BaseException:
            await db_session.rollback()
            raise
        await db_session.commit()
        return result

    def _require_admin(
        slug: str,
        allowed: Callable[[AdminModel[Any]], bool],
    ) -> AdminModel[Any]:
        """Return the admin for ``slug`` or raise ``404``.

        Args:
            slug (str): The admin slug.
            allowed (Callable[[AdminModel[Any]], bool]): Predicate the
                admin must satisfy (e.g. ``lambda a: a.can_create``);
                a failing predicate is treated as "not found" so
                disabled views don't leak their existence.

        Returns:
            AdminModel[Any]: The matched admin.

        Raises:
            HTTPException: ``404`` when missing or not permitted.
        """
        admin = site.get(slug)
        if admin is None or not allowed(admin):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        return admin

    async def _allows(
        principal: Any,
        admin: AdminModel[Any],
        action: AdminPermissionEnum,
    ) -> bool:
        """Return whether ``principal`` may perform ``action`` on ``admin``.

        With no ``access_policy`` configured, everything is allowed (the
        historical behavior). Otherwise the policy decides.

        Args:
            principal (Any): The current admin principal.
            admin (AdminModel[Any]): The target admin.
            action (AdminPermissionEnum): The action being attempted.

        Returns:
            bool: ``True`` when permitted.
        """
        if access_policy is None:
            return True
        result = access_policy(principal, admin, action)
        if isinstance(result, bool):
            return result
        return bool(await result)

    async def _require_access(
        principal: Any,
        admin: AdminModel[Any],
        action: AdminPermissionEnum,
    ) -> None:
        """Raise ``403`` unless ``principal`` may perform ``action``.

        Args:
            principal (Any): The current admin principal.
            admin (AdminModel[Any]): The target admin.
            action (AdminPermissionEnum): The action being attempted.

        Raises:
            HTTPException: ``403`` when the policy denies the action.
        """
        if not await _allows(principal, admin, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")

    async def _allows_custom_action(
        principal: Any,
        admin: AdminModel[Any],
        action: AdminAction,
    ) -> bool:
        """Return whether ``principal`` may see and run a custom action.

        Without ``action_policy`` a custom action needs ``EDIT`` on the
        model, as it always did. With one, it needs ``VIEW`` and the
        policy's approval of that action.

        Args:
            principal (Any): The current admin principal.
            admin (AdminModel[Any]): The model the action belongs to.
            action (AdminAction): The custom action.

        Returns:
            bool: ``True`` when permitted.
        """
        if action_policy is None:
            return await _allows(principal, admin, AdminPermissionEnum.EDIT)
        if not await _allows(principal, admin, AdminPermissionEnum.VIEW):
            return False
        result = action_policy(principal, admin, action)
        if isinstance(result, bool):
            return result
        return bool(await result)

    async def _permitted_bulk_actions(
        principal: Any, admin: AdminModel[Any]
    ) -> list[tuple[str, str, str]]:
        """Return the bulk actions the principal may run on ``admin``.

        Activate / deactivate need ``EDIT``, delete needs ``DELETE`` and a
        custom action what :func:`_allows_custom_action` says, so the menu
        never offers an action whose ``POST`` would answer ``403``. An empty
        list hides the bulk form.

        Args:
            principal (Any): The current admin principal.
            admin (AdminModel[Any]): The admin whose list is rendered.

        Returns:
            list[tuple[str, str, str]]: ``(value, label, confirm)`` per
            permitted action.
        """
        permitted: list[tuple[str, str, str]] = []
        for entry in _bulk_actions(admin, site.locale):
            value = entry[0]
            if value.startswith("custom:"):
                custom = admin.get_action(value.removeprefix("custom:"))
                allowed = custom is not None and await _allows_custom_action(
                    principal, admin, custom
                )
            elif value == "delete":
                allowed = await _allows(principal, admin, AdminPermissionEnum.DELETE)
            else:
                allowed = await _allows(principal, admin, AdminPermissionEnum.EDIT)
            if allowed:
                permitted.append(entry)
        return permitted

    async def _visible_nav(principal: Any) -> list[dict[str, str]]:
        """Return the sidebar nav entries the principal may VIEW.

        Args:
            principal (Any): The current admin principal.

        Returns:
            list[dict[str, str]]: ``{label, url}`` for each allowed model.
        """
        entries: list[dict[str, str]] = []
        for admin in site.iter_models():
            if await _allows(principal, admin, AdminPermissionEnum.VIEW):
                entries.append(
                    {
                        "label": admin.get_verbose_name_plural(),
                        "url": f"{prefix}/m/{admin.get_slug()}/",
                    }
                )
        return entries

    def _map_admin(entry: BRLocationMap) -> AdminModel[Any]:
        """Return the admin a map page asks ``VIEW`` of.

        Args:
            entry (BRLocationMap): The map.

        Returns:
            AdminModel[Any]: The model's registered admin, or a throwaway
            ``AdminModel(model)`` when it is not registered, so a policy
            that decides by ``admin.model`` answers the same.
        """
        registered = site.get(cast("type[BaseModel]", entry.model))
        if registered is not None:
            return registered
        return AdminModel(cast("type[BaseModel]", entry.model))

    async def _visible_maps(principal: Any) -> list[dict[str, str]]:
        """Return the sidebar entries of the map pages the principal may see.

        Args:
            principal (Any): The current admin principal.

        Returns:
            list[dict[str, str]]: ``{title, url}`` for each map whose model
            the principal may ``VIEW``.
        """
        entries: list[dict[str, str]] = []
        for entry in site.location_maps:
            if await _allows(principal, _map_admin(entry), AdminPermissionEnum.VIEW):
                entries.append(
                    {"title": entry.title, "url": f"{prefix}/maps/{entry.slug}"}
                )
        return entries

    def _check_csrf(session: AdminSession, token: str) -> None:
        """Reject the request when the submitted CSRF token mismatches.

        Args:
            session (AdminSession): The validated session.
            token (str): The token submitted with the form.

        Raises:
            HTTPException: ``403`` on mismatch.
        """
        if not secrets.compare_digest(session.csrf_token, token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf token mismatch")

    async def _get_scoped(
        admin: AdminModel[Any],
        db_session: AsyncSession,
        principal: Any,
        identity: str,
    ) -> Any | None:
        """Load one row by its URL identity, inside the principal's scope.

        Args:
            admin (AdminModel[Any]): The admin the row belongs to.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin principal.
            identity (str): The identity segment from the URL.

        Returns:
            Any | None: The row, or ``None`` when it does not exist **or**
            falls outside :meth:`AdminModel.scope_for`; callers answer
            ``404`` for both, so the response never tells them apart.
        """
        return await admin.build_repository(db_session).get_or_none(
            {admin.identity_field: _identity_value(identity)},
            where=await admin.scope_for(principal),
        )

    async def _write_in_scope(
        admin: AdminModel[Any],
        db_session: AsyncSession,
        principal: Any,
        write: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run a row write and refuse it when the row leaves the scope.

        With no scope the write runs as before, committing on its own.
        With one, it runs inside :func:`~tempest_fastapi_sdk.db.transaction`
        so the repository only flushes; the written row is then counted
        against the clause in the same transaction, and a row that no
        longer matches raises, which rolls the write back before anything
        is committed. That is what keeps an operator from creating a row
        for another tenant, or editing one of theirs into it.

        Args:
            admin (AdminModel[Any]): The admin the row belongs to.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin principal.
            write (Callable[[], Awaitable[Any]]): Performs the write and
                returns the written row.

        Returns:
            Any: What ``write`` returned.

        Raises:
            _RowOutOfScopeError: When the written row falls outside the
                scope; nothing was committed.
        """
        scope = await admin.scope_for(principal)
        if scope is None:
            return await write()
        async with transaction(db_session):
            saved = await write()
            matches = await admin.build_repository(db_session).count(
                {"id": saved.id}, where=scope
            )
            if not matches:
                raise _RowOutOfScopeError
        return saved

    async def _delete_scoped(
        admin: AdminModel[Any],
        db_session: AsyncSession,
        principal: Any,
        instance: Any,
    ) -> bool:
        """Delete one row with the scope inside the ``DELETE`` statement.

        Without a scope this is the repository's ``delete``. With one, the
        statement is ``DELETE ... WHERE id = :id AND <scope>``, so a row
        that left the scope between the lookup and the delete is not
        removed; ``PRE_DELETE`` / ``POST_DELETE`` handlers still run around
        it, as :meth:`BaseRepository.delete` runs them.

        Args:
            admin (AdminModel[Any]): The row's admin.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin principal.
            instance (Any): The row, already loaded inside the scope.

        Returns:
            bool: Whether a row was deleted.
        """
        repository = admin.build_repository(db_session)
        scope = await admin.scope_for(principal)
        if scope is None:
            await repository.delete(instance.id)
            return True
        signals = has_handlers(
            admin.model, RepositorySignalEnum.PRE_DELETE
        ) or has_handlers(admin.model, RepositorySignalEnum.POST_DELETE)
        async with transaction(db_session):
            if signals:
                await emit(type(instance), RepositorySignalEnum.PRE_DELETE, instance)
            deleted = await repository.delete_many({"id": instance.id}, where=scope)
        if deleted and signals:
            await emit(type(instance), RepositorySignalEnum.POST_DELETE, instance)
        return bool(deleted)

    async def _toggle_in_scope(
        admin: AdminModel[Any],
        db_session: AsyncSession,
        ids: list[Any],
        scope: ColumnElement[bool],
        *,
        active: bool,
    ) -> bool:
        """Activate or deactivate rows with the scope in the ``UPDATE``.

        Follows the edit form's rule: when the new ``is_active`` takes any
        of the rows out of the scope (a scope that filters on it), the
        whole statement is rolled back.

        Args:
            admin (AdminModel[Any]): The rows' admin.
            db_session (AsyncSession): The DB session.
            ids (list[Any]): The selected, reachable ids.
            scope (ColumnElement[bool]): The principal's scope.
            active (bool): The new ``is_active``.

        Returns:
            bool: ``True`` when committed, ``False`` when refused.
        """
        try:
            async with transaction(db_session):
                result = await db_session.execute(
                    sa_update(admin.model)
                    .where(admin.model.id.in_(ids), scope)
                    .values(is_active=active)
                    .execution_options(synchronize_session=False)
                )
                touched = cast("CursorResult[Any]", result).rowcount or 0
                still = await admin.build_repository(db_session).count(
                    {"id": ids}, where=scope
                )
                if still < touched:
                    raise _RowOutOfScopeError
        except _RowOutOfScopeError:
            return False
        return True

    async def _inserts_in_scope(
        admin: AdminModel[Any],
        db_session: AsyncSession,
        principal: Any,
        run: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run code that inserts rows of ``admin.model`` and check each one.

        For a ``create_handler``, which writes through a service and returns
        no row. With a scope, ``run`` executes inside
        :func:`~tempest_fastapi_sdk.db.transaction`, every instance of
        :attr:`AdminModel.model` that becomes persistent in the session is
        collected (SQLAlchemy's ``pending_to_persistent`` event), and they
        are counted against the scope in the same transaction before it
        commits; one outside it raises and rolls everything back. A row
        inserted through Core (``insert()``, ``bulk_create_values``) or a
        handler that commits the session itself escapes the check.

        Args:
            admin (AdminModel[Any]): The admin the handler creates rows of.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin principal.
            run (Callable[[], Awaitable[Any]]): The write.

        Returns:
            Any: What ``run`` returned.

        Raises:
            _RowOutOfScopeError: When a created row falls outside the scope.
        """
        scope = await admin.scope_for(principal)
        if scope is None:
            return await run()
        created: list[Any] = []

        def _collect(_session: Any, instance: Any) -> None:
            """Remember a row of the admin's model that was just inserted.

            Args:
                _session (Any): The sync session (unused).
                instance (Any): The instance that became persistent.
            """
            if isinstance(instance, admin.model):
                created.append(instance)

        sync_session = db_session.sync_session
        event.listen(sync_session, "pending_to_persistent", _collect)
        try:
            async with transaction(db_session):
                result = await run()
                await db_session.flush()
                ids = {row.id for row in created}
                if ids:
                    reachable = await admin.build_repository(db_session).count(
                        {"id": list(ids)}, where=scope
                    )
                    if reachable != len(ids):
                        raise _RowOutOfScopeError
        finally:
            event.remove(sync_session, "pending_to_persistent", _collect)
        return result

    async def _fk_scope_errors(
        admin: AdminModel[Any],
        db_session: AsyncSession,
        principal: Any,
        data: dict[str, Any],
        *,
        current: Any | None = None,
    ) -> dict[str, str]:
        """Refuse foreign-key values that point outside the target's scope.

        Only a foreign key whose target table has a registered admin with a
        ``row_scope`` is checked: its value must name a row the principal
        reaches there. A row outside the scope gets the same field error as
        one that does not exist, so a forged ``POST`` learns nothing about
        other tenants' ids. A value equal to what ``current`` already holds
        is not checked, so an edit keeps a reference it cannot see instead
        of failing on it.

        Args:
            admin (AdminModel[Any]): The admin being written.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin principal.
            data (dict[str, Any]): The parsed values about to be written.
            current (Any | None): The stored row, on an edit.

        Returns:
            dict[str, str]: Field → error for each refused value.
        """
        errors: dict[str, str] = {}
        columns = sa_inspect(admin.model).columns
        for field_name, table in fk_fields(admin).items():
            value = data.get(field_name)
            if value is None or field_name not in data:
                continue
            if current is not None and getattr(current, field_name, None) == value:
                continue
            referenced = site.get(table)
            if referenced is None or referenced.row_scope is None:
                continue
            target = next(iter(columns[field_name].foreign_keys)).column.name
            found = await referenced.build_repository(db_session).count(
                {target: value}, where=await referenced.scope_for(principal)
            )
            if not found:
                errors[field_name] = _t("form.error.fk_missing")
        return errors

    async def _resolve_fk_options(
        admin: AdminModel[Any],
        db_session: AsyncSession,
        principal: Any,
    ) -> dict[str, list[tuple[str, str]]]:
        """Build select options for FK fields whose target is registered.

        A foreign key whose referenced table has its own
        :class:`AdminModel` becomes a dropdown of related rows
        (Django's FK select). FKs to unmanaged tables stay plain
        UUID text inputs, and so do FKs to an admin the principal may
        not ``VIEW``: listing its rows' labels in a select would read
        that model through another one's form.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin principal.

        Returns:
            dict[str, list[tuple[str, str]]]: Field → ``(value, label)``
            options, capped at ``FK_OPTION_CAP`` rows.

        Notes:
            Fields listed in ``autocomplete_fields`` are skipped: those are
            searched on demand over HTMX and must never be pre-loaded, which
            is the whole point of marking them autocomplete.
        """
        options: dict[str, list[tuple[str, str]]] = {}
        for field_name, table in fk_fields(admin).items():
            if field_name in admin.autocomplete_fields:
                continue
            referenced = site.get(table)
            if referenced is None or not await _allows(
                principal, referenced, AdminPermissionEnum.VIEW
            ):
                continue
            rows = await referenced.build_repository(db_session).list(
                where=await referenced.scope_for(principal)
            )
            options[field_name] = [
                (str(row.id), fk_label(referenced, row)) for row in rows[:FK_OPTION_CAP]
            ]
        return options

    async def _fk_filter_options(
        admin: AdminModel[Any],
        field: str,
        db_session: AsyncSession,
        principal: Any,
    ) -> list[tuple[str, str]]:
        """Return ``(value, label)`` options for a FK list-filter field.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            field (str): The foreign-key column key.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin principal.

        Returns:
            list[tuple[str, str]]: Related-row options (``[]`` when the
            target table has no registered admin, or one the principal
            may not ``VIEW``), capped at ``FK_OPTION_CAP``.
        """
        column = sa_inspect(admin.model).columns.get(field)
        if column is None or not column.foreign_keys:
            return []
        table = next(iter(column.foreign_keys)).column.table.name
        referenced = site.get(table)
        if referenced is None or not await _allows(
            principal, referenced, AdminPermissionEnum.VIEW
        ):
            return []
        rows = await referenced.build_repository(db_session).list(
            where=await referenced.scope_for(principal)
        )
        return [
            (str(row.id), fk_label(referenced, row)) for row in rows[:FK_OPTION_CAP]
        ]

    async def _filter_specs(
        admin: AdminModel[Any],
        request: Request,
        db_session: AsyncSession,
        principal: Any,
    ) -> list[dict[str, Any]]:
        """Build the list-view filter widgets for each ``list_filter`` field.

        Each spec is a ``select`` (bool / enum / FK, or a column listed in
        ``choices``, which offers exactly those values), a ``daterange`` (two
        date inputs) or a ``text`` input, with the active value(s)
        pre-filled.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            request (Request): The inbound request (for active values).
            db_session (AsyncSession): The DB session (for FK options).
            principal (Any): The signed-in admin principal (FK options
                need ``VIEW`` on the referenced admin).

        Returns:
            list[dict[str, Any]]: Widget descriptors for the template.
        """
        specs: list[dict[str, Any]] = []
        for field in admin.list_filter:
            kind = _filter_kind(admin, field)
            label = field.replace("_", " ").strip().title()
            if kind == "date":
                value_from = request.query_params.get(f"filter_{field}_from", "")
                value_to = request.query_params.get(f"filter_{field}_to", "")
                specs.append(
                    {
                        "field": field,
                        "label": label,
                        "type": "daterange",
                        "value_from": value_from,
                        "value_to": value_to,
                        "shortcuts": [
                            {
                                "label": shortcut_label,
                                "url": _date_shortcut_url(request, field, start, end),
                                "active": value_from == start.isoformat()
                                and value_to == end.isoformat(),
                            }
                            for shortcut_label, (start, end) in zip(
                                (
                                    _t("filter.shortcut.today"),
                                    _t("filter.shortcut.last_7_days"),
                                    _t("filter.shortcut.last_30_days"),
                                    _t("filter.shortcut.this_month"),
                                    _t("filter.shortcut.last_month"),
                                ),
                                _date_shortcut_ranges(
                                    datetime.now(admin.display_tzinfo or UTC).date()
                                ),
                                strict=True,
                            )
                        ],
                    }
                )
                continue
            if field in admin.choices:
                active = request.query_params.get(f"filter_{field}", "")
                specs.append(
                    {
                        "field": field,
                        "label": label,
                        "type": "select",
                        "options": [
                            {"value": v, "label": lbl, "selected": active == v}
                            for v, lbl in admin.choices[field]
                        ],
                    }
                )
                continue
            if kind in ("bool", "enum", "fk"):
                active = request.query_params.get(f"filter_{field}", "")
                if kind == "bool":
                    pairs = [("true", _t("common.yes")), ("false", _t("common.no"))]
                elif kind == "enum":
                    enum_cls = _filter_python_type(admin, field)
                    pairs = _enum_filter_options(enum_cls) if enum_cls else []
                else:
                    pairs = await _fk_filter_options(
                        admin, field, db_session, principal
                    )
                specs.append(
                    {
                        "field": field,
                        "label": label,
                        "type": "select",
                        "options": [
                            {"value": v, "label": lbl, "selected": active == v}
                            for v, lbl in pairs
                        ],
                    }
                )
                continue
            specs.append(
                {
                    "field": field,
                    "label": label,
                    "type": "text",
                    "value": request.query_params.get(f"filter_{field}", ""),
                }
            )
        return specs

    async def _form_fields(
        admin: AdminModel[Any],
        db_session: AsyncSession,
        principal: Any,
        *,
        instance: Any | None = None,
        submitted: Any | None = None,
        errors: Any | None = None,
        creating: bool = False,
        only: Collection[str] | None = None,
    ) -> Any:
        """Resolve FK options then build the form-field descriptors.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin principal (FK options
                and autocomplete labels need ``VIEW`` on the referenced
                admin).
            instance (Any | None): Row being edited (pre-fill).
            submitted (Any | None): Rejected submission to re-render.
            errors (Any | None): Per-field error messages.
            creating (bool): Whether this is the create form.
            only (Collection[str] | None): Restrict to these fields — the
                edit form passes the row's
                :meth:`AdminModel.editable_field_names`, which honours
                ``readonly_fields_for``.

        Returns:
            Any: The list of form-field descriptors.

        Notes:
            A foreign-key ``<select>`` whose current value is not among its
            options — a row outside the target's ``row_scope``, or past
            ``FK_OPTION_CAP`` — gets one extra option carrying that value,
            labelled with the id alone. Without it the browser would post
            the blank or the first option and the save would rewrite the
            reference silently; the id is already in the form, so the
            label tells nothing new.
        """
        fk_options = await _resolve_fk_options(admin, db_session, principal)
        fields = build_form_fields(
            admin,
            instance=instance,
            submitted=submitted,
            errors=errors,
            fk_options=fk_options,
            creating=creating,
            only=only,
        )
        for form_field in fields:
            options = fk_options.get(form_field.name)
            value = "" if form_field.value is None else str(form_field.value)
            if options is not None and value and value not in {v for v, _ in options}:
                form_field.options = [*form_field.options, (value, value)]
        if admin.autocomplete_fields:
            await _decorate_autocomplete_fields(admin, fields, db_session, principal)
        return fields

    async def _decorate_autocomplete_fields(
        admin: AdminModel[Any],
        fields: list[Any],
        db_session: AsyncSession,
        principal: Any,
    ) -> None:
        """Fill in the search URL + current label for autocomplete fields.

        The current row's label is left out when the principal may not
        ``VIEW`` the referenced admin, the same rule the search endpoint
        applies.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            fields (list[Any]): The form fields to mutate in place.
            db_session (AsyncSession): The DB session, used to resolve the
                label of the currently-selected row (one lookup per field).
            principal (Any): The signed-in admin principal.
        """
        tables = fk_fields(admin)
        slug = admin.get_slug()
        for form_field in fields:
            if form_field.widget != "autocomplete":
                continue
            form_field.autocomplete_url = (
                f"{prefix}/m/{slug}/autocomplete/{form_field.name}"
            )
            table = tables.get(form_field.name)
            referenced = site.get(table) if table else None
            if referenced is None or not form_field.value:
                continue
            if not await _allows(principal, referenced, AdminPermissionEnum.VIEW):
                continue
            current = await _get_scoped(
                referenced, db_session, principal, str(form_field.value)
            )
            if current is not None:
                form_field.display_label = fk_label(referenced, current)

    @router.get("/login", name="admin_login_form")
    async def login_form(
        request: Request,
        next: str | None = None,
    ) -> HTMLResponse:
        """Render the login form (or redirect when already signed in).

        Args:
            request (Request): The inbound request.
            next (str | None): Optional post-login destination.

        Returns:
            HTMLResponse: The rendered login template.

        With ``Secure`` cookies, a request that arrives as ``http`` on a
        public host is a TLS-terminating proxy the app does not know
        about: the form posts to ``http://``, the browser flags the
        password field as not secure, and the ``Secure`` cookies never
        come back. The page still renders; a warning names the setting
        that fixes it, since nothing else in the response says why.
        """
        if (
            cookie_secure
            and request.url.scheme == "http"
            and request.url.hostname not in LOOPBACK_HOSTNAMES
        ):
            logger.warning(
                "Admin login page served over http on %s with Secure cookies:"
                " the form posts to http:// and the browser flags it as not"
                " secure. Behind a TLS-terminating proxy, set"
                " SERVER_PUBLIC_SCHEME=https and add PublicSchemeMiddleware.",
                request.url.hostname,
            )
        if await _load_session(request) is not None:
            return _render_login(request, {"user": None})
        return _render_login(
            request,
            {"user": None, "error": None, "next": next or ""},
        )

    def _render_login(
        request: Request,
        context: dict[str, Any],
        *,
        status_code: int = 200,
    ) -> HTMLResponse:
        """Render ``login.html`` with a CSRF token in the form and the cookie.

        Every render of the login page goes through here — the plain form,
        a wrong password, a spent budget, an expired form — so whichever
        page the operator submits next carries a token the submit accepts.

        Args:
            request (Request): The inbound request.
            context (dict[str, Any]): Template variables.
            status_code (int): HTTP status code.

        Returns:
            HTMLResponse: The login page, with the token cookie set.
        """
        token = login_csrf.issue(request)
        response = _render(
            request,
            "login.html",
            {**context, "csrf_token": token},
            status_code=status_code,
        )
        login_csrf.attach(response, token)
        return response

    def _login_throttled(request: Request, retry_after_seconds: int) -> Response:
        """Re-render the login form as ``429`` with ``Retry-After``.

        Args:
            request (Request): The inbound request.
            retry_after_seconds (int): Seconds until the budget resets.

        Returns:
            Response: The login page carrying the throttle message.
        """
        blocked = _render_login(
            request,
            {"user": None, "error": _t("login.error.throttled")},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        blocked.headers["Retry-After"] = str(max(retry_after_seconds, 1))
        return blocked

    @router.post("/login", name="admin_login")
    async def login_submit(
        request: Request,
        identifier: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(""),
        db_session: AsyncSession = Depends(_db_session),
    ) -> Response:
        """Verify credentials and issue the session cookie.

        Args:
            request (Request): The inbound request.
            identifier (str): The email submitted in the form.
            password (str): The submitted password.
            csrf_token (str): The pre-login token from the form's hidden
                field; empty when the field is missing, which is refused
                like a wrong one rather than as a ``422``.
            db_session (AsyncSession): The DB session.

        The CSRF token is checked first, before either budget and before
        the password: a cross-site submit must not spend the operator's
        attempts, and must not learn whether the credentials work. It is
        refused with ``403`` and the form re-rendered with a fresh token.

        The two login budgets run in the order ``make_auth_router`` uses
        for ``/auth/login``. The IP budget is **reserved** first, so a
        blocked source does not spend the budget of the identifier it is
        trying. The identifier budget is then reserved too, both before
        the password is checked — a burst of concurrent guesses cannot
        all pass a "not blocked yet" read together. A refused login keeps
        its IP slot spent; any other outcome gives it back. A successful
        login clears the identifier's key.

        Returns:
            Response: Redirect to the dashboard on success; rendered
            login form with an error message on failure, ``429`` with
            ``Retry-After`` when a budget is spent, ``403`` when the CSRF
            token is missing, expired or does not match its cookie.
        """
        if not login_csrf.verify(request, csrf_token):
            return _render_login(
                request,
                {"user": None, "error": _t("login.error.csrf")},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        ip_key = (
            f"admin-login-ip:{get_client_ip(request, trusted_header=trusted_ip_header)}"
        )
        if resolved_login_ip_throttle is not None:
            ip_status = await resolved_login_ip_throttle.hit(ip_key)
            if ip_status.attempts > resolved_login_ip_throttle.max_attempts:
                return _login_throttled(request, ip_status.retry_after_seconds)
        failed = False
        try:
            identifier_key = login_throttle_key(identifier)
            if resolved_login_throttle is not None:
                attempt = await resolved_login_throttle.hit(identifier_key)
                if attempt.attempts > resolved_login_throttle.max_attempts:
                    return _login_throttled(request, attempt.retry_after_seconds)
            try:
                principal = await auth_backend.authenticate(
                    db_session,
                    identifier=identifier,
                    password=password,
                )
            except AdminAuthError as exc:
                failed = True
                return _render_login(
                    request,
                    {"user": None, "error": exc.message},
                    status_code=exc.status_code,
                )
        finally:
            if resolved_login_ip_throttle is not None and not failed:
                await resolved_login_ip_throttle.release(ip_key)
        if resolved_login_throttle is not None:
            await resolved_login_throttle.reset(identifier_key)
        pending = auth_backend.mfa_enabled(principal)
        session = AdminSession(
            principal_id=auth_backend.principal_id(principal),
            issued_at=datetime.now(tz=UTC).timestamp(),
            csrf_token=secrets.token_urlsafe(32),
            mfa_pending=pending,
            credential_digest=_credential_digest(principal),
        )
        response = RedirectResponse(
            url=f"{prefix}/mfa" if pending else f"{prefix}/",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        store.save(response, session)
        login_csrf.clear(response)
        return response

    @router.post("/logout", name="admin_logout")
    async def logout(
        request: Request,
        csrf_token: str = Form(...),
    ) -> Response:
        """Clear the active session.

        Args:
            request (Request): The inbound request.
            csrf_token (str): CSRF token submitted with the form.

        Returns:
            Response: Redirect to the login page.

        Raises:
            HTTPException: ``403`` when the CSRF token mismatches.
        """
        session = store.load(request)
        if session is not None and not secrets.compare_digest(
            session.csrf_token, csrf_token
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf token mismatch")
        if session is not None:
            await revocations.revoke(session)
        response = RedirectResponse(
            url=f"{prefix}/login",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        store.clear(response)
        return response

    @router.get("/mfa", name="admin_mfa")
    async def mfa_challenge(request: Request) -> Response:
        """Render the TOTP challenge after a password step that needs MFA.

        Args:
            request (Request): The inbound request.

        Returns:
            Response: The challenge page, or a redirect to login (no
            session) / dashboard (already fully authenticated).
        """
        session = await _load_session(request)
        if session is None:
            return RedirectResponse(
                f"{prefix}/login", status_code=status.HTTP_303_SEE_OTHER
            )
        if not session.mfa_pending:
            return RedirectResponse(f"{prefix}/", status_code=status.HTTP_303_SEE_OTHER)
        return _render(
            request,
            "mfa.html",
            {"user": None, "session": session, "error": None},
        )

    @router.post("/mfa", name="admin_mfa_submit")
    async def mfa_submit(
        request: Request,
        code: str = Form(...),
        csrf_token: str = Form(...),
        db_session: AsyncSession = Depends(_db_session),
    ) -> Response:
        """Verify the TOTP code and upgrade the pending session.

        Args:
            request (Request): The inbound request.
            code (str): The submitted authenticator code.
            csrf_token (str): CSRF token from the challenge form.
            db_session (AsyncSession): The DB session.

        Returns:
            Response: Redirect to the dashboard on success; the
            re-rendered challenge (``401``) on a bad code.

        Raises:
            HTTPException: ``403`` on CSRF mismatch.
        """
        session = await _load_session(request)
        if session is None or not session.mfa_pending:
            return RedirectResponse(
                f"{prefix}/login", status_code=status.HTTP_303_SEE_OTHER
            )
        if not secrets.compare_digest(session.csrf_token, csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf token mismatch")
        principal = await auth_backend.load_principal(db_session, session.principal_id)
        if principal is None or not _credential_matches(session, principal):
            return RedirectResponse(
                f"{prefix}/login", status_code=status.HTTP_303_SEE_OTHER
            )
        throttle_key = f"admin-mfa:{session.principal_id}"
        attempt = await resolved_mfa_throttle.hit(throttle_key)
        if attempt.attempts > resolved_mfa_throttle.max_attempts:
            blocked = _render(
                request,
                "mfa.html",
                {
                    "user": None,
                    "session": session,
                    "error": _t("mfa.error.throttled"),
                },
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            blocked.headers["Retry-After"] = str(attempt.retry_after_seconds)
            return blocked
        if not await auth_backend.claim_mfa_step(db_session, principal, code):
            return _render(
                request,
                "mfa.html",
                {"user": None, "session": session, "error": _t("mfa.error.invalid")},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        await db_session.commit()
        await resolved_mfa_throttle.reset(throttle_key)
        upgraded = AdminSession(
            principal_id=session.principal_id,
            issued_at=datetime.now(tz=UTC).timestamp(),
            csrf_token=secrets.token_urlsafe(32),
            mfa_pending=False,
            credential_digest=_credential_digest(principal),
        )
        response = RedirectResponse(
            url=f"{prefix}/", status_code=status.HTTP_303_SEE_OTHER
        )
        store.save(response, upgraded)
        return response

    @router.get("/", name="admin_index")
    async def dashboard(
        request: Request,
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Render the dashboard listing registered admins.

        Args:
            request (Request): The inbound request.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The dashboard template.
        """
        principal = await _resolve_principal(request, db_session, session)
        models_view: list[dict[str, Any]] = []
        for admin in site.iter_models():
            if not await _allows(principal, admin, AdminPermissionEnum.VIEW):
                continue
            try:
                count = await admin.build_repository(db_session).count(
                    where=await admin.scope_for(principal)
                )
            except Exception:
                logger.exception(
                    "Admin dashboard could not count %s; showing no count",
                    admin.get_slug(),
                )
                count = None
            models_view.append(
                {
                    "admin": admin,
                    "count": count,
                    "url": f"{prefix}/m/{admin.get_slug()}/",
                    "new_url": f"{prefix}/m/{admin.get_slug()}/new"
                    if admin.creates
                    and await _allows(principal, admin, AdminPermissionEnum.CREATE)
                    else None,
                }
            )
        cards = await _build_dashboard_cards(db_session)
        return _render(
            request,
            "dashboard.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "models_view": models_view,
                "cards": cards,
                "metrics": await _system_metrics() if show_metrics else None,
            },
        )

    async def _build_dashboard_cards(
        db_session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Compute the site's business-metric cards for the dashboard.

        A card whose ``compute`` raises is skipped, so one broken metric
        never blanks the whole dashboard. The failure is logged with its
        traceback through the module logger (``logger.exception``), naming
        the card label, so a broken card is visible in the logs instead of
        silently disappearing from the page.

        Args:
            db_session (AsyncSession): The DB session passed to each card.

        Returns:
            list[dict[str, Any]]: Template-ready card descriptors.
        """
        cards: list[dict[str, Any]] = []
        for card in site.dashboard_cards:
            try:
                data = await card.compute(db_session)
            except Exception:
                logger.exception(
                    "Admin dashboard card %r failed to compute; skipping it",
                    card.label,
                )
                continue
            cards.append(card_entry(card.label, card.help_text, data))
        return cards

    @router.get("/metrics/{slug}", name="admin_metrics_area")
    async def metrics_area(
        request: Request,
        slug: str,
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Render one indicator area: one card per field of its schema.

        The page is the ``METRICS`` surface, so a ``surface_policy`` decides
        who opens it. When the provider or the method raises, or the method
        returns the wrong type, the page still renders, with an error in
        place of the cards and the traceback in the module logger — one
        broken service never turns the panel into a 500.

        Args:
            request (Request): The inbound request.
            slug (str): The area's URL segment.
            db_session (AsyncSession): The DB session handed to the provider.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The ``metrics_area.html`` page.

        Raises:
            HTTPException: ``404`` for an unknown slug; ``403`` when the
                surface policy denies ``METRICS``.
        """
        area = next((item for item in site.metrics_areas if item.slug == slug), None)
        if area is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "metrics area not found")
        principal = await _resolve_principal(
            request, db_session, session, surface=AdminSurfaceEnum.METRICS
        )
        cards: list[dict[str, Any]] = []
        failed = False
        try:
            result = await area.collect(db_session)
        except Exception:
            logger.exception(
                "Admin metrics area %r failed to collect; rendering the error",
                area.title,
            )
            failed = True
        else:
            for label, help_text, data, level in area.cards(result):
                entry = card_entry(label, help_text, data)
                entry["level"] = None if level is None else level.value
                cards.append(entry)
        return _render(
            request,
            "metrics_area.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "area": area,
                "cards": cards,
                "failed": failed,
            },
        )

    @router.get("/maps/{slug}", name="admin_location_map")
    async def location_map(
        request: Request,
        slug: str,
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Render one location map: Brazil shaded by row count per state.

        When the count raises, the page still renders, with an error in
        place of the map and the traceback in the module logger, like an
        indicator area.

        Args:
            request (Request): The inbound request.
            slug (str): The map's URL segment.
            db_session (AsyncSession): The DB session the counts run on.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The ``location_map.html`` page.

        Raises:
            HTTPException: ``404`` for an unknown slug; ``403`` when the
                ``access_policy`` denies ``VIEW`` on the map's model.
        """
        entry = next((item for item in site.location_maps if item.slug == slug), None)
        if entry is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "location map not found")
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, _map_admin(entry), AdminPermissionEnum.VIEW)
        page: dict[str, Any] = {}
        failed = False
        try:
            distribution = await entry.collect(db_session)
        except Exception:
            logger.exception(
                "Admin location map %r failed to count; rendering the error",
                entry.title,
            )
            failed = True
        else:
            page = entry.page_context(distribution)
        return _render(
            request,
            "location_map.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "location_map": entry,
                "failed": failed,
                **page,
            },
        )

    if show_logs:
        _log_base = Path(log_dir)
        _logs_off = is_log_dir_unset(log_dir)
        _logs_page_size = 50
        _logs_export_max = 500

        async def _collect_log_entries(
            source: LogSource, q: str | None
        ) -> list[dict[str, Any]]:
            """Read, filter and sort the log records for a source, newest first.

            Shared by the logs page and the export so the exported document is
            exactly the selection on screen. Duplicating the filter would let the
            two drift, and an export that quietly disagrees with the page it was
            taken from is worse than no export.

            Args:
                source (LogSource): Which log file(s) to read.
                q (str | None): Case-insensitive message substring filter.

            Returns:
                list[dict[str, Any]]: The matching records, newest first.
            """
            if _logs_off:
                return []
            result = await run_in_threadpool(
                partial(read_log_entries, _log_base, source, q=q)
            )
            if result.truncated:
                logger.warning(
                    "Admin log source %r exceeds the per-file read cap; older "
                    "records were not read for this request.",
                    source,
                )
            return result.entries

        @router.get("/logs/export", name="admin_logs_export")
        async def logs_export(
            request: Request,
            source: LogSource = Query(default="all"),
            q: str | None = Query(default=None),
            format: Literal["md", "json"] = Query(default="md"),
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Download the current log selection as markdown or JSON.

            Answers the "a 500 happened, paste me the trace" loop. The records
            already carry the formatted traceback under ``exception`` — the SDK's
            exception handlers log with ``exc_info=True`` — so the export is a
            rendering concern, not a capture one. Markdown puts each traceback in
            a fenced block so it survives a paste into an issue; JSON ships the
            records verbatim for tooling.

            Honors the same ``source`` / ``q`` filters as the page, so the file
            matches what the operator was looking at. At most
            ``_logs_export_max`` of the newest matching records are written, and
            the markdown header states the cap when it bites rather than letting
            a partial export read as a complete one.

            Inherits the admin session guard: tracebacks and request metadata are
            exactly the payload that must not be world-readable. The principal
            is reloaded before anything is read, so an account deleted,
            deactivated or demoted after the login is redirected to the login
            page on this request instead of exporting until the cookie expires.
            Nothing here renders the principal, so the reloaded value is not
            kept.

            Args:
                request (Request): The inbound request.
                source (LogSource): Which log file(s) to read.
                q (str | None): Case-insensitive message substring filter.
                format (Literal["md", "json"]): Output format.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated admin session.

            Returns:
                Response: The rendered document as a file attachment.

            Raises:
                HTTPException: ``303`` redirect to the login page when the
                    principal no longer qualifies.
            """
            await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.LOGS
            )
            entries = await _collect_log_entries(source, q)
            total = len(entries)
            window = entries[:_logs_export_max]
            truncated_from = total if total > len(window) else None

            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            if format == "json":
                body = render_entries_json(window)
                media_type = "application/json"
                suffix = "json"
            else:
                body = render_entries_markdown(
                    window,
                    source=source,
                    query=q,
                    truncated_from=truncated_from,
                )
                media_type = "text/markdown; charset=utf-8"
                suffix = "md"

            filename = f"logs-{source}-{stamp}.{suffix}"
            return Response(
                content=body,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )

        @router.get("/logs", name="admin_logs")
        async def logs_view(
            request: Request,
            source: LogSource = Query(default="all"),
            q: str | None = Query(default=None),
            page: int = Query(default=1, ge=1),
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> HTMLResponse:
            """Render a filtered, paginated page of structured logs.

            Args:
                request (Request): The inbound request.
                source (LogSource): Which log file(s) to read.
                q (str | None): Case-insensitive message substring filter.
                page (int): The 1-indexed page number.
                db_session (AsyncSession): The DB session.
                session (AdminSession): The validated session.

            The page carries a refresh link (``refresh_url``): the same
            ``source`` and ``q`` without ``page``, so it reloads the newest
            records under the current filter. Records are read from disk on
            every request, so reloading is all a refresh takes; the admin
            ships no JavaScript, and a link keeps it that way. ``read_at``
            shows when this read happened, so a reload that brings nothing
            new still visibly ran.

            Returns:
                HTMLResponse: The logs template (empty state when no log
                files exist on disk).
            """
            principal = await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.LOGS
            )

            entries = await _collect_log_entries(source, q)

            total = len(entries)
            offset = (page - 1) * _logs_page_size
            window = entries[offset : offset + _logs_page_size]

            available = (
                not _logs_off
                and _log_base.exists()
                and any(
                    candidate.exists() or bool(rotated_log_files(candidate))
                    for candidate in resolve_log_files(_log_base, "all")
                )
            )

            kept_params = {"source": source, "q": q or ""}
            pagination = _Pagination(
                page=page,
                size=_logs_page_size,
                total=total,
                query_params={k: v for k, v in kept_params.items() if v},
            )
            export_query = urlencode(
                {k: v for k, v in kept_params.items() if v},
            )
            export_base = f"{prefix}/logs/export"
            separator = "&" if export_query else ""
            return _render(
                request,
                "logs.html",
                {
                    "user": principal,
                    "session": session,
                    "user_display": auth_backend.display_name(principal),
                    "entries": window,
                    "log_sources": [
                        "all",
                        "debug",
                        "info",
                        "warning",
                        "error",
                        "critical",
                        "500",
                    ],
                    "current_source": source,
                    "query": q or "",
                    "available": available,
                    "pagination": pagination,
                    "export_md_url": (
                        f"{export_base}?{export_query}{separator}format=md"
                    ),
                    "export_json_url": (
                        f"{export_base}?{export_query}{separator}format=json"
                    ),
                    "export_max": _logs_export_max,
                    "refresh_url": (
                        f"{prefix}/logs?{export_query}"
                        if export_query
                        else f"{prefix}/logs"
                    ),
                    "read_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "context_keys": ("path", "method", "status_code", "request_id"),
                },
            )

    @router.get("/m/{slug}/", name="admin_list")
    async def list_view(
        request: Request,
        slug: str,
        page: int = 1,
        q: str = "",
        sort: str = "",
        dir: str = "asc",
        lens: str = "",
        flash: str = "",
        flash_cat: str = "success",
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Render the list view for a registered admin.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug from the URL.
            page (int): 1-indexed page number.
            q (str): Free-text search term.
            sort (str): Column to sort by (validated against the
                sortable columns; the admin default applies otherwise).
            dir (str): Sort direction, ``"asc"`` or ``"desc"``.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The list template.

        Notes:
            A lens is a saved preset. Its filters are ANDed underneath the
            user's — user keys win on a conflict — and its ordering applies
            only while the user has not clicked a column sort.

            Two query-parameter sets are carried onto the rendered links so
            navigating never silently drops the user's context: pagination
            links keep search, filters and sort (``active_filters`` already
            holds the full ``filter_*`` keys), and sort links keep the same
            minus the paging and sort keys they are about to replace.
        """
        admin = site.get(slug)
        if admin is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown admin")
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.VIEW)

        repository = admin.build_repository(db_session)

        (
            filters,
            active_filters,
            order_key,
            ascending,
            active_sort,
            active_ascending,
        ) = _resolve_filters_and_order(admin, request, q, sort, dir)

        active_lens = admin.get_lens(lens) if lens else None
        if active_lens is not None:
            filters = {**active_lens.filters, **filters}
            if active_lens.order_by and not sort:
                if active_lens.order_by.startswith("-"):
                    order_key, ascending = active_lens.order_by[1:], False
                else:
                    order_key, ascending = active_lens.order_by, True

        page = max(1, page)
        size = max(1, admin.page_size)
        result = await repository.paginate(
            filters=filters,
            page=page,
            page_size=size,
            order_by=order_key,
            ascending=ascending,
            where=_all_of(_search_where(admin, q), await admin.scope_for(principal)),
        )

        columns = admin.resolved_list_display()
        rows = [
            _RowView(instance, columns, admin.identity_field, admin.display_tzinfo)
            for instance in result["items"]
        ]

        query_params: dict[str, str] = {}
        if q:
            query_params["q"] = q
        if active_lens is not None:
            query_params["lens"] = lens
        query_params.update(active_filters)
        sort_base = dict(query_params)
        if active_sort:
            query_params["sort"] = active_sort
            query_params["dir"] = "asc" if active_ascending else "desc"

        pagination = _Pagination(
            page=result["page"],
            size=result["page_size"],
            total=result["total"],
            query_params=query_params,
        )

        export_query = urlencode(query_params)

        lens_tabs: list[dict[str, Any]] = []
        if admin.lenses:
            lens_tabs.append(
                {
                    "label": _t("list.lens_all"),
                    "url": f"{prefix}/m/{slug}/",
                    "active": active_lens is None,
                }
            )
            for defined in admin.lenses:
                lens_tabs.append(
                    {
                        "label": defined.get_label(),
                        "url": f"{prefix}/m/{slug}/?lens={defined.slug()}",
                        "active": active_lens is not None
                        and active_lens.slug() == defined.slug(),
                    }
                )

        return _render(
            request,
            "list.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "flash": flash,
                "flash_cat": flash_cat,
                "columns": columns,
                "rows": rows,
                "lens_tabs": lens_tabs,
                "pagination": pagination,
                "query": {"q": q},
                "sort_state": _sort_state(
                    admin, active_sort, active_ascending, sort_base
                ),
                "export_query": export_query,
                "can_create": admin.creates
                and await _allows(principal, admin, AdminPermissionEnum.CREATE),
                "new_label": admin.create_handler.label
                if admin.create_handler is not None
                and admin.create_handler.label is not None
                else None,
                "new_url": f"{prefix}/m/{slug}/new",
                "bulk_actions": await _permitted_bulk_actions(principal, admin),
                "submission_nonce": nonces.issue(),
                "bulk_url": f"{prefix}/m/{slug}/bulk",
                "filters": await _filter_specs(admin, request, db_session, principal),
            },
        )

    @router.get("/m/{slug}/export.{fmt}", name="admin_export")
    async def export_view(
        request: Request,
        slug: str,
        fmt: str,
        q: str = "",
        sort: str = "",
        dir: str = "asc",
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> Response:
        """Export the current (filtered + sorted) result set as CSV/JSON.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            fmt (str): ``"csv"`` or ``"json"``.
            q (str): Free-text search term (same semantics as the list).
            sort (str): Sort column.
            dir (str): Sort direction.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            Response: An attachment with the serialized rows.

        Raises:
            HTTPException: ``404`` for an unknown admin or unsupported
                format.
        """
        admin = site.get(slug)
        if admin is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown admin")
        if fmt not in {"csv", "json"}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unsupported format")
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.VIEW)

        repository = admin.build_repository(db_session)
        filters, _active, order_key, ascending, _sort, _asc = (
            _resolve_filters_and_order(admin, request, q, sort, dir)
        )
        result = await repository.paginate(
            filters=filters,
            page=1,
            page_size=export_max_rows,
            order_by=order_key,
            ascending=ascending,
            where=_all_of(_search_where(admin, q), await admin.scope_for(principal)),
        )
        columns = admin.resolved_list_display()
        items = result["items"]
        filename = f"{admin.get_slug()}.{fmt}"

        if fmt == "csv":
            payload = _to_csv(columns, items)
            media = "text/csv; charset=utf-8"
        else:
            payload = _to_json(columns, items)
            media = "application/json; charset=utf-8"
        return Response(
            content=payload,
            media_type=media,
            headers={"content-disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/m/{slug}/new", name="admin_create")
    async def create_form(
        request: Request,
        slug: str,
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Render the create form for a registered admin.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The form template.

        Raises:
            HTTPException: ``404`` for unknown admin or when creation is
                disabled.

        Notes:
            Editable fields are pre-filled from the query string. That is
            what an inline "Add" link relies on to seed the parent foreign
            key, so only names in ``editable_field_names()`` are accepted —
            an arbitrary query parameter can never reach the form.
        """
        admin = _require_admin(slug, lambda a: a.creates)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.CREATE)
        if admin.create_handler is not None:
            return await _handler_form(request, admin, session, principal, db_session)
        editable = set(admin.editable_field_names())
        prefill = {
            key: value for key, value in request.query_params.items() if key in editable
        }
        return _render(
            request,
            "form.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "mode": "create",
                "form_fields": await _form_fields(
                    admin,
                    db_session,
                    principal,
                    submitted=prefill or None,
                    creating=True,
                ),
                "action_url": f"{prefix}/m/{slug}/new",
                "back_url": f"{prefix}/m/{slug}/",
                "form_error": None,
            },
        )

    def _import_page(
        request: Request,
        admin: AdminModel[Any],
        session: AdminSession,
        principal: Any,
        *,
        outcome: ImportOutcome | None = None,
        payload: str | None = None,
        form_error: str | None = None,
        status_code: int = status.HTTP_200_OK,
    ) -> HTMLResponse:
        """Render the import page: the upload form, and a preview when given.

        Args:
            request (Request): The inbound request.
            admin (AdminModel[Any]): The admin being imported into.
            session (AdminSession): The validated session.
            principal (Any): The signed-in admin.
            outcome (ImportOutcome | None): The rehearsal to preview.
            payload (str | None): The signed CSV the confirm form posts;
                ``None`` leaves the confirm button disabled.
            form_error (str | None): A message for the whole page.
            status_code (int): The response status.

        Returns:
            HTMLResponse: ``import.html``.
        """
        slug = admin.get_slug()
        return _render(
            request,
            "import.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "columns": admin.editable_field_names(creating=True),
                "action_url": f"{prefix}/m/{slug}/import",
                "confirm_url": f"{prefix}/m/{slug}/import/confirm",
                "back_url": f"{prefix}/m/{slug}/",
                "preview": outcome,
                "payload": payload,
                "submission_nonce": nonces.issue() if payload else None,
                "max_bytes": ADMIN_IMPORT_MAX_BYTES,
                "form_error": form_error,
            },
            status_code=status_code,
        )

    async def _import_pass(
        admin: AdminModel[Any],
        db_session: AsyncSession,
        principal: Any,
        text: str,
        *,
        commit: bool,
    ) -> ImportOutcome:
        """Run :func:`run_import` with the principal's scope and audit stamp.

        Args:
            admin (AdminModel[Any]): The admin being imported into.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin.
            text (str): The decoded CSV.
            commit (bool): Whether to keep the rows when every one went in.

        Returns:
            ImportOutcome: What the pass found. The principal is reloaded
            afterwards, since the rollback that ends a rehearsal expires it.
        """
        actor_id = auth_backend.principal_id(principal)
        outcome = await run_import(
            admin,
            db_session,
            text,
            scope=await admin.scope_for(principal),
            prepare=partial(_stamp_audit, actor_id=actor_id, creating=True),
            commit=commit,
            locale=site.locale,
            check=partial(_fk_scope_errors, admin, db_session, principal),
        )
        await _reload_expired(db_session, principal)
        return outcome

    @router.get("/m/{slug}/import", name="admin_import")
    async def import_form(
        request: Request,
        slug: str,
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Render the CSV import page for an admin.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The import template.

        Raises:
            HTTPException: ``404`` when the admin is unknown or import is
                disabled.
        """
        admin = _require_admin(slug, lambda a: a.can_import and a.can_create)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.CREATE)
        return _import_page(request, admin, session, principal)

    @router.post("/m/{slug}/import", name="admin_import_submit")
    async def import_submit(
        request: Request,
        slug: str,
        csrf_token: str = Form(...),
        file: UploadFile = File(...),
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Preview an uploaded CSV without writing anything.

        Every row is validated with the create form's rules and rehearsed
        inside a transaction that is rolled back (see :func:`run_import`),
        so the preview also shows what only the database or the
        ``row_scope`` would refuse. The page lists how many rows enter, a
        sample of them, and each refused row's number and field errors.
        The confirm button posts the CSV back signed; it is disabled while
        any row has an error or no row would enter.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            csrf_token (str): CSRF token from the form.
            file (UploadFile): The uploaded CSV file.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The import page with the preview (``200``), or
            with an error (``400``) when the file is larger than
            :data:`ADMIN_IMPORT_MAX_BYTES` or is not UTF-8 CSV.

        Raises:
            HTTPException: ``404`` when import is disabled; ``403`` on
                CSRF mismatch.

        Notes:
            Row numbering in the error report starts at 2 because row 1 is the
            CSV header, so the numbers line up with what the user sees in a
            spreadsheet.
        """
        admin = _require_admin(slug, lambda a: a.can_import and a.can_create)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.CREATE)
        _check_csrf(session, csrf_token)
        raw = await file.read(ADMIN_IMPORT_MAX_BYTES + 1)
        if len(raw) > ADMIN_IMPORT_MAX_BYTES:
            return _import_page(
                request,
                admin,
                session,
                principal,
                form_error=_t(
                    "import.error.too_large", max_kib=ADMIN_IMPORT_MAX_BYTES // 1024
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        try:
            text = raw.decode("utf-8-sig")
            outcome = await _import_pass(
                admin, db_session, principal, text, commit=False
            )
        except (UnicodeDecodeError, csv.Error):
            await _reload_expired(db_session, principal)
            return _import_page(
                request,
                admin,
                session,
                principal,
                form_error=_t("import.error.unreadable"),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        payload = (
            sign_import_payload(
                secret_key,
                slug=slug,
                principal_id=auth_backend.principal_id(principal),
                text=text,
            )
            if outcome.accepted and not outcome.errors
            else None
        )
        return _import_page(
            request, admin, session, principal, outcome=outcome, payload=payload
        )

    @router.post("/m/{slug}/import/confirm", name="admin_import_confirm")
    async def import_confirm(
        request: Request,
        slug: str,
        csrf_token: str = Form(...),
        payload: str = Form(...),
        submission_nonce: str = Form(...),
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> Response:
        """Commit a previewed CSV, all rows or none.

        The CSV comes from the signed ``payload`` the preview rendered, so
        it is the file that was previewed; a payload signed for another
        model or operator, altered, or older than
        :data:`ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS` is refused. The rows
        are then inserted again, for real, in one transaction: if any row
        is refused now (the table changed since the preview), nothing is
        committed and the preview comes back with the errors. The
        single-use nonce makes a reload of the answer a no-op.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            csrf_token (str): CSRF token from the form.
            payload (str): The signed CSV.
            submission_nonce (str): The preview's single-use nonce.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            Response: A redirect to the list with the created count
            (``303``); the import page with the error (``400``) when the
            payload is refused or a row fails.

        Raises:
            HTTPException: ``404`` when import is disabled; ``403`` on
                CSRF mismatch.
        """
        admin = _require_admin(slug, lambda a: a.can_import and a.can_create)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.CREATE)
        _check_csrf(session, csrf_token)
        try:
            text = read_import_payload(
                secret_key,
                payload,
                slug=slug,
                principal_id=auth_backend.principal_id(principal),
            )
        except ImportPayloadError:
            return _import_page(
                request,
                admin,
                session,
                principal,
                form_error=_t("import.error.payload"),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if not await nonces.consume(session, submission_nonce):
            return _already_processed(slug)
        outcome = await _import_pass(admin, db_session, principal, text, commit=True)
        if not outcome.committed:
            return _import_page(
                request,
                admin,
                session,
                principal,
                outcome=outcome,
                form_error=_t("import.error.not_committed"),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        query = urlencode(
            {
                "flash": _tn("import.created", outcome.accepted),
                "flash_cat": "success",
            }
        )
        return RedirectResponse(
            url=f"{prefix}/m/{slug}/?{query}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @router.post("/m/{slug}/new", name="admin_create_submit")
    async def create_submit(
        request: Request,
        slug: str,
        csrf_token: str = Form(...),
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> Response:
        """Validate + persist a new row from the create form.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            csrf_token (str): CSRF token from the form.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            Response: Redirect to the new row's detail on success; the
            re-rendered form (``400``) on validation/integrity errors.
        """
        admin = _require_admin(slug, lambda a: a.creates)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.CREATE)
        _check_csrf(session, csrf_token)
        form = await request.form()
        if admin.create_handler is not None:
            return await _run_create_handler(
                request, admin, session, principal, db_session, form
            )
        data, errors = parse_submission(admin, form, creating=True, locale=site.locale)
        errors.update(await _fk_scope_errors(admin, db_session, principal, data))
        errors.update(
            await _save_uploads(admin, form, data, creating=True, locale=site.locale)
        )
        uploaded = _uploaded_keys(admin, data)
        passwords, password_errors = parse_password_submission(
            admin,
            form,
            creating=True,
            locale=site.locale,
        )
        errors.update(password_errors)
        form_error: str | None = None
        if not errors:
            repository = admin.build_repository(db_session)
            instance = admin.model(**data)
            apply_password_fields(admin, instance, passwords)
            _stamp_audit(instance, auth_backend.principal_id(principal), creating=True)
            try:
                saved = await _write_in_scope(
                    admin,
                    db_session,
                    principal,
                    partial(repository.add, instance),
                )
            except AppException as exc:
                form_error = exc.message
                await _reload_expired(db_session, principal)
            except _RowOutOfScopeError:
                form_error = _t("error.out_of_scope")
                await _reload_expired(db_session, principal)
            else:
                identity = getattr(saved, admin.identity_field)
                return RedirectResponse(
                    url=f"{prefix}/m/{slug}/{identity}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
        await _discard_uploads(admin, uploaded)
        return _render(
            request,
            "form.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "mode": "create",
                "form_fields": await _form_fields(
                    admin,
                    db_session,
                    principal,
                    submitted=form,
                    errors=errors,
                    creating=True,
                ),
                "action_url": f"{prefix}/m/{slug}/new",
                "back_url": f"{prefix}/m/{slug}/",
                "form_error": form_error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    @router.post("/m/{slug}/bulk", name="admin_bulk")
    async def bulk_action(
        request: Request,
        slug: str,
        csrf_token: str = Form(...),
        action: str = Form(...),
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> Response:
        """Apply a bulk action (delete / activate / deactivate) to rows.

        Args:
            request (Request): The inbound request (carries the
                repeated ``ids`` form field).
            slug (str): The admin slug.
            csrf_token (str): CSRF token from the form.
            action (str): One of ``delete`` / ``activate`` /
                ``deactivate``.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            Response: Redirect back to the list view, a custom action's
            secrets page, or its input form (``200``, or ``400`` on an
            invalid submission or an ``AppException`` from the handler).

        Raises:
            HTTPException: ``404`` for unknown admin, ``403`` on CSRF
                mismatch or a denied action, ``400`` for an unknown /
                unpermitted action or an input form without a nonce.

        Notes:
            The permission needed depends on the action: ``delete`` removes
            rows and so requires ``DELETE``, activate / deactivate require
            ``EDIT``, and a custom action requires ``EDIT`` or, with an
            ``action_policy``, ``VIEW`` plus that policy's approval. A
            custom action is committed when its handler returns and rolled
            back when it raises. One declared with ``schema`` answers its
            input form on the first ``POST`` (no ``action_input`` marker)
            and runs only on a valid submission carrying a fresh nonce.
        """
        admin = site.get(slug)
        if admin is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown admin")
        principal = await _resolve_principal(request, db_session, session)
        custom: AdminAction | None = None
        if action.startswith("custom:"):
            custom = admin.get_action(action.removeprefix("custom:"))
            if custom is None:
                await _require_access(principal, admin, AdminPermissionEnum.EDIT)
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown action")
            if not await _allows_custom_action(principal, admin, custom):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        else:
            needed = (
                AdminPermissionEnum.DELETE
                if action == "delete"
                else AdminPermissionEnum.EDIT
            )
            await _require_access(principal, admin, needed)
        _check_csrf(session, csrf_token)
        form = await request.form()
        raw_ids = [str(raw) for raw in form.getlist("ids")]
        ids = [_identity_value(raw) for raw in raw_ids]
        scope = await admin.scope_for(principal)
        if scope is not None and ids:
            reachable = {
                str(row.id)
                for row in await admin.build_repository(db_session).list(
                    {"id": ids}, where=scope
                )
            }
            raw_ids = [
                raw
                for raw, value in zip(raw_ids, ids, strict=True)
                if str(value) in reachable
            ]
            ids = [_identity_value(raw) for raw in raw_ids]
        redirect_url = f"{prefix}/m/{slug}/"
        if ids and custom is not None:
            admin_action = custom
            data: Any = None
            if admin_action.schema is not None:
                if form.get(_ACTION_INPUT_FIELD) != "1":
                    return await _custom_action_form(
                        request,
                        admin,
                        admin_action,
                        session,
                        principal,
                        db_session,
                        raw_ids,
                    )
                data, errors = parse_schema_submission(admin_action.schema, form)
                if data is None:
                    return await _custom_action_form(
                        request,
                        admin,
                        admin_action,
                        session,
                        principal,
                        db_session,
                        raw_ids,
                        submitted=form,
                        errors=errors,
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
            nonce = form.get("submission_nonce")
            if admin_action.schema is not None and (
                not isinstance(nonce, str) or not nonce
            ):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "missing submission nonce"
                )
            if isinstance(nonce, str) and not await nonces.consume(session, nonce):
                return _already_processed(slug)
            context = AdminActionContext(
                ids=ids,
                repository=admin.build_repository(db_session),
                db_session=db_session,
                request=request,
                session=session,
                principal=principal,
            )
            try:
                result = await _call_custom_action(
                    admin_action, data, context, db_session
                )
            except AppException as exc:
                if admin_action.schema is None:
                    raise
                await _reload_expired(db_session, principal)
                return await _custom_action_form(
                    request,
                    admin,
                    admin_action,
                    session,
                    principal,
                    db_session,
                    raw_ids,
                    submitted=form,
                    form_error=exc.message,
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            if result is not None and result.secrets:
                return _secrets_response(
                    request,
                    admin,
                    session,
                    principal,
                    result,
                    reload_safe=isinstance(nonce, str),
                )
            if result is not None:
                redirect_url += "?" + urlencode(
                    {"flash": result.message, "flash_cat": result.category}
                )
        elif ids:
            repository = admin.build_repository(db_session)
            if action == "delete":
                if not admin.can_delete:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "delete disabled")
                if scope is None:
                    await repository.delete_batch(ids)
                else:
                    await repository.delete_many({"id": ids}, where=scope)
            elif action in ("activate", "deactivate"):
                if not admin.can_edit or not admin.can_toggle_active:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "activate/deactivate disabled"
                    )
                if scope is None:
                    await repository.bulk_update(
                        {"id": ids}, {"is_active": action == "activate"}
                    )
                elif not await _toggle_in_scope(
                    admin, db_session, ids, scope, active=action == "activate"
                ):
                    redirect_url += "?" + urlencode(
                        {"flash": _t("error.out_of_scope"), "flash_cat": "error"}
                    )
            else:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown action")
        return RedirectResponse(
            url=redirect_url,
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @router.get("/m/{slug}/{identity}/upload/{field}", name="admin_upload_download")
    async def upload_download(
        request: Request,
        slug: str,
        identity: str,
        field: str,
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> Response:
        """Download the file an upload field of one row points at.

        A backend that mints a temporary URL (MinIO) answers a redirect to
        it; :class:`~tempest_fastapi_sdk.utils.LocalUploadStorage` streams
        the file from disk as an attachment, with ``nosniff``.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            identity (str): The primary-key value from the URL.
            field (str): An ``upload_fields`` column.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            Response: The redirect or the file.

        Raises:
            HTTPException: ``404`` for an unknown admin, row or field, a row
                with no file, or a backend that can serve neither.
        """
        admin = site.get(slug)
        if admin is None or field not in admin.upload_fields:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown upload field")
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.VIEW)
        instance = await _get_scoped(admin, db_session, principal, identity)
        key = None if instance is None else getattr(instance, field, None)
        if not key:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no file")
        storage = admin.upload_storage
        assert storage is not None, "upload_fields require upload_storage"
        url = await storage.presigned_url(key)
        if url:
            return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
        if isinstance(storage, LocalUploadStorage):
            path = storage.local_path(key)
            if path.is_file():
                return FileResponse(
                    path,
                    filename=path.name,
                    headers={"X-Content-Type-Options": "nosniff"},
                )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not available")

    @router.get("/m/{slug}/{identity}", name="admin_detail")
    async def detail_view(
        request: Request,
        slug: str,
        identity: str,
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Render the detail (read-only) view for one row.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            identity (str): The primary-key value from the URL.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The detail template.
        """
        admin = site.get(slug)
        if admin is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown admin")
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.VIEW)
        instance = await _get_scoped(admin, db_session, principal, identity)
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")

        return await _detail_response(
            request, admin, slug, identity, instance, principal, session, db_session
        )

    async def _detail_response(
        request: Request,
        admin: AdminModel[Any],
        slug: str,
        identity: str,
        instance: Any,
        principal: Any,
        session: AdminSession,
        db_session: AsyncSession,
        *,
        inline_overrides: dict[str, list[tuple[str, dict[str, str], dict[str, str]]]]
        | None = None,
        inline_error: str | None = None,
        status_code: int = status.HTTP_200_OK,
    ) -> HTMLResponse:
        """Render the detail view for ``instance``.

        Shared by the read-only GET and the inline-formset POST error
        path (which passes ``inline_overrides`` so the formset re-renders
        the submitted values + per-field errors).

        Args:
            request (Request): The inbound request.
            admin (AdminModel[Any]): The parent admin.
            slug (str): The parent admin slug.
            identity (str): The parent identity value.
            instance (Any): The parent row.
            principal (Any): The signed-in principal.
            session (AdminSession): The validated session.
            db_session (AsyncSession): The DB session.
            inline_overrides (dict | None): Submitted inline rows to
                re-render on error, keyed by child slug.
            inline_error (str | None): A banner message for the inline.
            status_code (int): HTTP status (``400`` on inline errors).

        Returns:
            HTMLResponse: The rendered detail template.

        Notes:
            Two column groups are held back from the field list:
            :meth:`AdminModel.hidden_field_names` (``hashed_password``,
            ``password_fields``, ``exclude_fields``) is never shown — not
            in the fields and not in the audit timeline's before/after
            rows — and the audit/timestamp
            columns move to the dedicated audit panel further down the page.
            ``JSON`` columns are pretty-printed, mirroring what the edit
            form's JSON widget does, so the two views read the same.
        """
        columns = admin.column_names()
        readonly = admin.readonly_field_names_for(instance)
        model_columns = sa_inspect(admin.model).columns
        json_columns: set[str] = set()
        fields: list[tuple[str, Any]] = []
        hidden_columns = admin.hidden_field_names()
        for column in columns:
            if column in hidden_columns or column in _AUDIT_FIELDS:
                continue
            raw_value = _localized(
                getattr(instance, column, None),
                admin.display_tzinfo,
            )
            col = model_columns.get(column)
            if col is not None and isinstance(col.type, JSON) and raw_value is not None:
                raw_value = json.dumps(
                    raw_value, indent=2, ensure_ascii=False, sort_keys=True
                )
                json_columns.add(column)
            fields.append((column, raw_value))

        async def _actor(uid: Any) -> str | None:
            """Name the author of a change, within the user admin's scope.

            When the principal's model is registered with a ``row_scope``,
            the name is shown only for a user the signed-in principal may
            ``VIEW`` and reaches through that scope; any other author is
            shown by id, so the audit panel does not read another
            tenant's users.

            Args:
                uid (Any): The stored actor id.

            Returns:
                str | None: The display name, the id, or ``None``.
            """
            if uid is None:
                return None
            actor = await auth_backend.load_principal(db_session, str(uid))
            if actor is None:
                return str(uid)
            actor_admin = site.get(cast("type[BaseModel]", type(actor)))
            if actor_admin is not None and actor_admin.row_scope is not None:
                if not await _allows(principal, actor_admin, AdminPermissionEnum.VIEW):
                    return str(uid)
                visible = await actor_admin.build_repository(db_session).count(
                    {"id": getattr(actor, "id", None)},
                    where=await actor_admin.scope_for(principal),
                )
                if not visible:
                    return str(uid)
            return auth_backend.display_name(actor)

        audit = {
            "created_at": _localized(
                getattr(instance, "created_at", None),
                admin.display_tzinfo,
            ),
            "updated_at": _localized(
                getattr(instance, "updated_at", None),
                admin.display_tzinfo,
            ),
            "created_by": await _actor(getattr(instance, "created_by", None)),
            "updated_by": await _actor(getattr(instance, "updated_by", None)),
            "has_actors": "created_by" in columns or "updated_by" in columns,
        }

        history: list[dict[str, Any]] = []
        if admin.audit_model is not None:
            audit_model = admin.audit_model
            entity_id = str(getattr(instance, "id", identity))
            result = await db_session.execute(
                select(audit_model)
                .where(audit_model.entity == admin.model.__name__)
                .where(audit_model.entity_id == entity_id)
                .order_by(audit_model.created_at.desc())
                .limit(_AUDIT_HISTORY_LIMIT)
            )
            for entry in result.scalars().all():
                history.append(
                    {
                        "action": entry.action,
                        "at": entry.created_at,
                        "actor": await _actor(entry.actor) or entry.actor,
                        "changes": [
                            row
                            for row in _format_audit_changes(
                                entry.action, entry.changes
                            )
                            if row["field"] not in hidden_columns
                        ],
                        "context": entry.context,
                    }
                )

        inlines = await _build_inlines(
            admin,
            instance,
            db_session,
            principal,
            slug,
            identity,
            overrides=inline_overrides,
        )

        return _render(
            request,
            "detail.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "identity": identity,
                "fields": fields,
                "json_columns": json_columns,
                "upload_urls": {
                    name: f"{prefix}/m/{slug}/{identity}/upload/{name}"
                    for name in admin.upload_fields
                    if getattr(instance, name, None)
                },
                "readonly": readonly,
                "audit": audit,
                "history": history,
                "inlines": inlines,
                "inline_error": inline_error,
                "can_edit": admin.can_edit
                and await _allows(principal, admin, AdminPermissionEnum.EDIT),
                "can_delete": admin.can_delete
                and await _allows(principal, admin, AdminPermissionEnum.DELETE),
                "edit_url": f"{prefix}/m/{slug}/{identity}/edit",
                "delete_url": f"{prefix}/m/{slug}/{identity}/delete",
            },
            status_code=status_code,
        )

    async def _build_inlines(
        admin: AdminModel[Any],
        instance: Any,
        db_session: AsyncSession,
        principal: Any,
        parent_slug: str,
        parent_identity: str,
        *,
        overrides: dict[str, list[tuple[str, dict[str, str], dict[str, str]]]]
        | None = None,
    ) -> list[dict[str, Any]]:
        """Build the related-child blocks for a parent's detail view.

        For each configured :class:`Inline`, queries the child rows that
        reference ``instance`` via the inline's ``fk_field``. A read-only
        inline packages the rows as a table with links to the child admin
        (when registered); an ``editable`` inline whose child admin allows
        editing packages them as an in-place formset (one input row per
        child, plus a blank add row when the principal may ``CREATE`` and
        a delete box when it may ``DELETE``).

        An inline is left out entirely when the principal may not ``VIEW``
        the child admin; the rows are that model's data, whichever page
        renders them. A child with no registered admin is judged through a
        throwaway ``AdminModel(child)``: the access policy is asked about
        it like any other admin, and its :meth:`AdminModel.hidden_field_names`
        filters the columns, so ``hashed_password`` and the model's
        ``__secret_columns__`` never reach the table.

        Args:
            admin (AdminModel[Any]): The parent admin.
            instance (Any): The parent row.
            db_session (AsyncSession): The DB session.
            principal (Any): The signed-in admin principal (for the
                per-child VIEW, EDIT, CREATE and DELETE checks).
            parent_slug (str): The parent's admin slug (for the formset
                POST action URL).
            parent_identity (str): The parent's identity value.
            overrides (dict[str, list[...]] | None): For an error
                re-render, maps a child slug to the submitted rows
                ``(rowkey, values, errors)`` so the formset shows what the
                user typed plus per-field errors instead of DB values.

        Returns:
            list[dict[str, Any]]: One entry per inline, ready for the
            template.
        """
        parent_id = getattr(instance, "id", None)
        overrides = overrides or {}
        blocks: list[dict[str, Any]] = []
        for inline in admin.inlines:
            child_slug = inline.get_slug()
            child_admin = site.get(child_slug)
            view_admin = (
                child_admin
                if child_admin is not None
                else AdminModel(inline.model, can_create=False)
            )
            if not await _allows(principal, view_admin, AdminPermissionEnum.VIEW):
                continue
            repository = view_admin.build_repository(db_session)
            children = await repository.list(
                {inline.fk_field: parent_id},
                where=await view_admin.scope_for(principal),
            )
            if inline.list_display is not None:
                display = inline.list_display
            else:
                display = view_admin.resolved_list_display()
            child_hidden = view_admin.hidden_field_names()
            display = [name for name in display if name not in child_hidden]

            editable = (
                inline.editable
                and child_admin is not None
                and child_admin.can_edit
                and await _allows(principal, child_admin, AdminPermissionEnum.EDIT)
            )
            if editable and child_admin is not None:
                blocks.append(
                    _build_editable_inline(
                        inline,
                        child_admin,
                        children,
                        f"{prefix}/m/{parent_slug}/{parent_identity}"
                        f"/inlines/{child_slug}",
                        overrides.get(child_slug),
                        can_create=child_admin.can_create
                        and await _allows(
                            principal, child_admin, AdminPermissionEnum.CREATE
                        ),
                        can_delete=inline.can_delete
                        and child_admin.can_delete
                        and await _allows(
                            principal, child_admin, AdminPermissionEnum.DELETE
                        ),
                    )
                )
                continue

            rows = [
                {
                    "id": str(getattr(child, "id", "")),
                    "cells": [(col, getattr(child, col, None)) for col in display],
                    "url": (
                        f"{prefix}/m/{child_slug}/{getattr(child, 'id', '')}"
                        if child_admin is not None
                        else None
                    ),
                }
                for child in children[:_INLINE_ROW_LIMIT]
            ]
            can_add = (
                child_admin is not None
                and child_admin.can_create
                and await _allows(principal, child_admin, AdminPermissionEnum.CREATE)
            )
            add_url = (
                f"{prefix}/m/{child_slug}/new?{inline.fk_field}={parent_id}"
                if can_add
                else None
            )
            blocks.append(
                {
                    "label": inline.get_label(),
                    "editable": False,
                    "columns": display,
                    "rows": rows,
                    "add_url": add_url,
                    "total": len(children),
                    "truncated": len(children) > _INLINE_ROW_LIMIT,
                }
            )
        return blocks

    @router.get("/m/{slug}/autocomplete/{field}", name="admin_autocomplete")
    async def autocomplete(
        request: Request,
        slug: str,
        field: str,
        q: str = Query(default=""),
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Return matching related rows for an autocomplete FK field.

        Backs the HTMX search box: given a query ``q``, searches the
        referenced admin's ``search_fields`` (ILIKE, ORed) and returns an
        ``<li>`` fragment of up to ``_AUTOCOMPLETE_LIMIT`` ``(id, label)``
        options.

        Answers ``403`` unless the principal may ``VIEW`` both the admin
        being edited and the referenced one: the options are rows of the
        referenced model, so a search here is a read of it.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug being edited.
            field (str): The autocomplete FK column key.
            q (str): The search term.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The options fragment (``autocomplete.html``).
        """
        admin = site.get(slug)
        if admin is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown admin")
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.VIEW)
        if field not in admin.autocomplete_fields:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "field is not an autocomplete field"
            )
        table = fk_fields(admin).get(field)
        referenced = site.get(table) if table else None
        if referenced is not None:
            await _require_access(principal, referenced, AdminPermissionEnum.VIEW)
        results: list[tuple[str, str]] = []
        if referenced is not None:
            ref_model = referenced.model
            stmt = select(ref_model)
            term = q.strip()
            if term:
                conditions = []
                for search_field in referenced.search_fields:
                    column = getattr(ref_model, search_field, None)
                    if column is not None:
                        conditions.append(
                            column.ilike(f"%{escape_like(term)}%", escape="\\")
                        )
                if conditions:
                    stmt = stmt.where(or_(*conditions))
            scope = await referenced.scope_for(principal)
            if scope is not None:
                stmt = stmt.where(scope)
            stmt = stmt.limit(_AUTOCOMPLETE_LIMIT)
            rows = (await db_session.execute(stmt)).scalars().all()
            results = [(str(row.id), fk_label(referenced, row)) for row in rows]
        return templates.TemplateResponse(
            request,
            "autocomplete.html",
            {"results": results},
        )

    @router.get("/m/{slug}/{identity}/edit", name="admin_edit")
    async def edit_form(
        request: Request,
        slug: str,
        identity: str,
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Render the edit form for one row.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            identity (str): The primary-key value from the URL.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The form template.

        Raises:
            HTTPException: ``404`` for unknown admin, disabled editing,
                or a missing row.
        """
        admin = _require_admin(slug, lambda a: a.can_edit)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.EDIT)
        instance = await _get_scoped(admin, db_session, principal, identity)
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
        return _render(
            request,
            "form.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "mode": "edit",
                "identity": identity,
                "form_fields": await _form_fields(
                    admin,
                    db_session,
                    principal,
                    instance=instance,
                    only=admin.editable_field_names(instance=instance),
                ),
                "action_url": f"{prefix}/m/{slug}/{identity}/edit",
                "back_url": f"{prefix}/m/{slug}/{identity}",
                "form_error": None,
                "version_field": _VERSION_FIELD,
                "version_token": _version_token(admin, instance),
            },
        )

    @router.post("/m/{slug}/{identity}/edit", name="admin_edit_submit")
    async def edit_submit(
        request: Request,
        slug: str,
        identity: str,
        csrf_token: str = Form(...),
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> Response:
        """Validate + persist edits to one row.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            identity (str): The primary-key value from the URL.
            csrf_token (str): CSRF token from the form.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            Response: Redirect to the row's detail on success; the
            re-rendered form (``400``) on validation/integrity errors, and
            ``409`` when the row changed after the form was opened.

        Notes:
            Optimistic lock. The edit form carries the row's ``updated_at``
            as read when it was rendered (:data:`_VERSION_FIELD`). Before
            any column is assigned, the save issues
            ``UPDATE ... SET updated_at = updated_at WHERE id = :id AND
            updated_at = :read`` in the transaction the write then
            commits; zero rows means another save landed in between, so
            nothing is written and the form comes back with the
            operator's values, a warning and the **current** version, so
            saving again overwrites knowingly. A model without ``updated_at``,
            or a post without the field, saves as before. The inline
            formset and the bulk/custom actions take no such lock.
        """
        admin = _require_admin(slug, lambda a: a.can_edit)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.EDIT)
        _check_csrf(session, csrf_token)
        repository = admin.build_repository(db_session)
        instance = await _get_scoped(admin, db_session, principal, identity)
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
        form = await request.form()
        editable = admin.editable_field_names(instance=instance)
        data, errors = parse_submission(admin, form, only=editable, locale=site.locale)
        if admin.validate_edit is not None:
            errors.update(admin.validate_edit(instance, form))
        errors.update(
            await _fk_scope_errors(admin, db_session, principal, data, current=instance)
        )
        if not errors:
            errors.update(
                await _save_uploads(
                    admin, form, data, creating=False, locale=site.locale
                )
            )
        uploaded = _uploaded_keys(admin, data)
        passwords, password_errors = parse_password_submission(
            admin,
            form,
            creating=False,
            locale=site.locale,
        )
        errors.update(password_errors)
        form_error: str | None = None
        submitted_version = form.get(_VERSION_FIELD)
        version_token = (
            submitted_version
            if isinstance(submitted_version, str) and submitted_version
            else None
        )
        expected_version = _parse_version(admin, version_token)
        status_code = status.HTTP_400_BAD_REQUEST

        async def _save() -> Any:
            """Claim the read version, then assign and persist the edit.

            Returns:
                Any: The updated row.

            Raises:
                _EditConflictError: When the row's ``updated_at`` no longer
                    matches the version the form was opened with.
            """
            if expected_version is not None:
                claimed = await db_session.execute(
                    sa_update(admin.model)
                    .where(
                        admin.model.id == instance.id,
                        admin.model.updated_at == expected_version,
                    )
                    .values(updated_at=admin.model.updated_at)
                    .execution_options(synchronize_session=False)
                )
                if not cast("CursorResult[Any]", claimed).rowcount:
                    raise _EditConflictError
            for key, value in data.items():
                setattr(instance, key, value)
            apply_password_fields(admin, instance, passwords)
            _stamp_audit(instance, auth_backend.principal_id(principal), creating=False)
            return await repository.update(instance)

        if not errors:
            try:
                await _write_in_scope(admin, db_session, principal, _save)
            except _EditConflictError:
                await db_session.rollback()
                await _reload_expired(db_session, principal, instance)
                form_error = _t("error.edit_conflict")
                version_token = _version_token(admin, instance)
                status_code = status.HTTP_409_CONFLICT
            except AppException as exc:
                form_error = exc.message
                await _reload_expired(db_session, principal)
            except _RowOutOfScopeError:
                form_error = _t("error.out_of_scope")
                await _reload_expired(db_session, principal)
            else:
                return RedirectResponse(
                    url=f"{prefix}/m/{slug}/{identity}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
        await _discard_uploads(admin, uploaded)
        return _render(
            request,
            "form.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "mode": "edit",
                "identity": identity,
                "form_fields": await _form_fields(
                    admin,
                    db_session,
                    principal,
                    submitted=form,
                    errors=errors,
                    only=editable,
                ),
                "action_url": f"{prefix}/m/{slug}/{identity}/edit",
                "back_url": f"{prefix}/m/{slug}/{identity}",
                "form_error": form_error,
                "version_field": _VERSION_FIELD,
                "version_token": version_token,
            },
            status_code=status_code,
        )

    @router.post(
        "/m/{slug}/{identity}/inlines/{child_slug}", name="admin_inline_submit"
    )
    async def inline_submit(
        request: Request,
        slug: str,
        identity: str,
        child_slug: str,
        csrf_token: str = Form(...),
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> Response:
        """Persist an editable inline formset in one transaction.

        Parses the ``row.<key>.<field>`` inputs posted from the parent's
        detail view: existing rows (key = child id) are updated, a blank
        row with any value becomes a new child (foreign key forced to the
        parent), and a checked ``__delete`` box removes an existing row
        (when the inline and child admin allow it). All child rows are
        scoped to this parent — a row whose foreign key doesn't match is
        ignored, never cross-edited.

        Args:
            request (Request): The inbound request.
            slug (str): The parent admin slug.
            identity (str): The parent identity value.
            child_slug (str): The child admin slug (the inline target).
            csrf_token (str): CSRF token from the form.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            Response: Redirect to the parent detail on success; the
            re-rendered detail (``400``) with per-field errors otherwise.

        Notes:
            The parent foreign key is excluded from the parsed names and
            forced from the URL instead, so a crafted form can never
            re-point a child row at a different parent.

            The formset is held to the same permissions as the child's own
            routes: editing needs ``EDIT``, a filled-in new row needs the
            child admin's ``can_create`` and ``CREATE``, and a checked
            delete box (when the inline and child admin allow deletion)
            needs ``DELETE``. A submission asking for an action the
            principal lacks is refused with ``403`` before anything is
            written, rather than applied in part.

            Posted inputs are named ``row.<key>.<field>`` and grouped by
            row key before parsing. A new row whose every value is blank is
            treated as an add row the user left untouched and skipped, so
            submitting the formset without filling the extra row does not
            create an empty child.
        """
        parent_admin = _require_admin(slug, lambda a: a.can_edit)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, parent_admin, AdminPermissionEnum.VIEW)
        _check_csrf(session, csrf_token)

        inline = next(
            (
                i
                for i in parent_admin.inlines
                if i.get_slug() == child_slug and i.editable
            ),
            None,
        )
        child_admin = site.get(child_slug)
        if inline is None or child_admin is None or not child_admin.can_edit:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "editable inline not found")
        await _require_access(principal, child_admin, AdminPermissionEnum.EDIT)

        parent = await _get_scoped(parent_admin, db_session, principal, identity)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
        parent_id = getattr(parent, "id", None)

        form = await request.form()
        names = set(inline_editable_names(child_admin)) - {inline.fk_field}
        child_repo = child_admin.build_repository(db_session)
        actor_id = auth_backend.principal_id(principal)

        grouped: dict[str, dict[str, str]] = {}
        to_delete: set[str] = set()
        for full_key in form:
            if not full_key.startswith("row."):
                continue
            parts = full_key.split(".", 2)
            if len(parts) != 3:
                continue
            _, key, field_name = parts
            if field_name == "__delete":
                raw = str(form.get(full_key) or "").lower()
                if raw not in ("", "false", "off", "0", "no"):
                    to_delete.add(key)
                continue
            grouped.setdefault(key, {})[field_name] = str(form.get(full_key) or "")

        adds = any(
            key.startswith("new") and any(v.strip() for v in values.values())
            for key, values in grouped.items()
        )
        if adds and not (
            child_admin.can_create
            and await _allows(principal, child_admin, AdminPermissionEnum.CREATE)
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        can_delete = inline.can_delete and child_admin.can_delete
        if to_delete and can_delete:
            await _require_access(principal, child_admin, AdminPermissionEnum.DELETE)
        error_rows: list[tuple[str, dict[str, str], dict[str, str]]] = []
        form_error: str | None = None
        child_scope = await child_admin.scope_for(principal)

        async def _owned(key: str) -> Any:
            """Return the child row for ``key`` if it belongs to the parent."""
            existing = await child_repo.get_or_none(
                {"id": _identity_value(key)}, where=child_scope
            )
            if existing is None:
                return None
            if getattr(existing, inline.fk_field, None) != parent_id:
                return None
            return existing

        for key, values in grouped.items():
            is_new = key.startswith("new")
            if not is_new and can_delete and key in to_delete:
                existing = await _owned(key)
                if existing is not None:
                    await _delete_scoped(child_admin, db_session, principal, existing)
                continue
            if is_new and not any(v.strip() for v in values.values()):
                continue
            data, errs = parse_submission(
                child_admin, values, only=names, locale=site.locale
            )
            current_row = None if is_new else await _owned(key)
            if not is_new and current_row is None:
                continue
            errs.update(
                await _fk_scope_errors(
                    child_admin, db_session, principal, data, current=current_row
                )
            )
            if errs:
                error_rows.append((key, values, errs))
                continue
            data[inline.fk_field] = parent_id
            try:
                if is_new:
                    child = child_admin.model(**data)
                    _stamp_audit(child, actor_id, creating=True)
                    await _write_in_scope(
                        child_admin,
                        db_session,
                        principal,
                        partial(child_repo.add, child),
                    )
                else:
                    existing = current_row
                    for field_name, value in data.items():
                        setattr(existing, field_name, value)
                    _stamp_audit(existing, actor_id, creating=False)
                    await _write_in_scope(
                        child_admin,
                        db_session,
                        principal,
                        partial(child_repo.update, existing),
                    )
            except AppException as exc:
                form_error = exc.message
                error_rows.append((key, values, {}))
            except _RowOutOfScopeError:
                form_error = _t("error.out_of_scope")
                error_rows.append((key, values, {}))

        if error_rows or form_error:
            await _reload_expired(db_session, principal, parent)
            return await _detail_response(
                request,
                parent_admin,
                slug,
                identity,
                parent,
                principal,
                session,
                db_session,
                inline_overrides={child_slug: error_rows},
                inline_error=form_error or _t("detail.inline_error"),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return RedirectResponse(
            url=f"{prefix}/m/{slug}/{identity}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @router.post("/m/{slug}/{identity}/delete", name="admin_delete")
    async def delete_submit(
        request: Request,
        slug: str,
        identity: str,
        csrf_token: str = Form(...),
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> Response:
        """Delete one row after CSRF validation.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            identity (str): The primary-key value from the URL.
            csrf_token (str): CSRF token from the form.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            Response: Redirect to the list view.

        Raises:
            HTTPException: ``404`` for unknown admin, disabled deletion,
                or a missing row.
        """
        admin = _require_admin(slug, lambda a: a.can_delete)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermissionEnum.DELETE)
        _check_csrf(session, csrf_token)
        instance = await _get_scoped(admin, db_session, principal, identity)
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
        if not await _delete_scoped(admin, db_session, principal, instance):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
        return RedirectResponse(
            url=f"{prefix}/m/{slug}/",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if sql_shell is not None:
        _shell = sql_shell

        async def _sql_context(
            session: AdminSession,
            principal: Any,
        ) -> dict[str, Any]:
            """Build the template context describing the active policy.

            The policy is shown on the page so the operator knows the
            limits before typing, rather than discovering them through
            refusals. The chrome (header, logout form, sidebar) is built
            from the reloaded principal, the same way the task panel does,
            so the sidebar lists only the models that principal may view.

            Args:
                session (AdminSession): The validated admin session, whose
                    CSRF token the console form submits.
                principal (Any): The principal reloaded for this request.

            Returns:
                dict[str, Any]: The base context.
            """
            policy = _shell.policy
            return {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "capabilities": sorted(str(item) for item in policy.capabilities),
                "allowed_tables": sorted(policy.allowed_tables),
                "denied_tables": sorted(policy.denied_tables),
                "max_rows": policy.max_rows,
                "read_only": policy.read_only,
                "principal_label": session.principal_id,
            }

        @router.get("/sql", response_class=HTMLResponse, name="admin_sql")
        async def sql_console(
            request: Request,
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Render the empty SQL console.

            Args:
                request (Request): The incoming request.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated session.

            Returns:
                Response: The console page.

            Raises:
                HTTPException: ``303`` redirect to the login page when the
                    principal no longer qualifies.
            """
            principal = await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.SQL
            )
            return _render(
                request,
                "sql_shell.html",
                await _sql_context(session, principal),
            )

        @router.post("/sql", response_class=HTMLResponse, name="admin_sql_run")
        async def sql_console_run(
            request: Request,
            sql: str = Form(default=""),
            csrf_token: str = Form(...),
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Run a statement and render the result or the refusal.

            A refusal and a database error are rendered differently on
            purpose: "you may not do that" and "your SQL is wrong" send
            the operator to different fixes, and collapsing them into one
            red box wastes the distinction the service went to the trouble
            of making.

            Args:
                request (Request): The incoming request.
                sql (str): The submitted statement.
                csrf_token (str): CSRF token from the form.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated session.

            Returns:
                Response: The console page with the outcome.

            Raises:
                HTTPException: ``303`` redirect to the login page when the
                    principal no longer qualifies, before the statement
                    reaches the shell; ``403`` on CSRF mismatch.
            """
            principal = await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.SQL
            )
            _check_csrf(session, csrf_token)
            context = await _sql_context(session, principal)
            context["submitted"] = sql
            try:
                context["result"] = await _shell.execute(
                    sql,
                    principal=str(session.principal_id),
                )
            except SqlShellDenied as exc:
                context["error_kind"] = _t("sql.error.denied")
                context["error"] = str(exc)
            except SqlShellError as exc:
                context["error_kind"] = _t("sql.error.failed")
                context["error"] = str(exc)
            return _render(request, "sql_shell.html", context)

    if tasks is not None:
        _panel = tasks

        @router.get("/tasks", response_class=HTMLResponse, name="admin_tasks")
        async def task_panel(
            request: Request,
            status: str = "",
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Render the declared schedule and the persisted runs.

            Neither section is invented: the schedule is the broker's own
            registry and the runs are rows a worker wrote. Live queue
            depth is not shown because TaskIQ exposes none.

            Args:
                request (Request): The incoming request.
                status (str): Optional job status to filter the runs by.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal the chrome renders.
                session (AdminSession): The validated session.

            Returns:
                Response: The task panel page.
            """
            principal = await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.TASKS
            )
            runs = await _panel.recent_runs(status=status or None)
            return _render(
                request,
                "tasks.html",
                {
                    "user": principal,
                    "session": session,
                    "user_display": auth_backend.display_name(principal),
                    "shows_runs": _panel.shows_runs,
                    "shows_schedule": _panel.shows_schedule,
                    "runs": runs,
                    "schedule": _panel.schedule(),
                    "status_filter": status,
                    "statuses": sorted(JobStatusEnum),
                    "tasks_url": f"{prefix}/tasks",
                },
            )

        @router.get(
            "/tasks/{job_id}",
            response_class=HTMLResponse,
            name="admin_task_detail",
        )
        async def task_detail(
            request: Request,
            job_id: UUID,
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Render one run: progress, stage, attempts and error.

            Args:
                request (Request): The incoming request.
                job_id (UUID): The run to show.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal the chrome renders.
                session (AdminSession): The validated session.

            Returns:
                Response: The detail page, or a 404 page when the row is
                gone — a job an operator can reach by URL after a
                retention sweep is exactly the case that would otherwise
                raise.
            """
            principal = await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.TASKS
            )
            chrome: dict[str, Any] = {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "tasks_url": f"{prefix}/tasks",
            }
            job = await _panel.run(job_id)
            if job is None:
                return _render(
                    request,
                    "task_detail.html",
                    {**chrome, "job": None},
                    status_code=404,
                )
            return _render(
                request,
                "task_detail.html",
                {
                    **chrome,
                    "job": job,
                    "cancellable": job.status in CANCELLABLE_JOB_STATUSES,
                    "cancel_url": f"{prefix}/tasks/{job_id}/cancel",
                },
            )

        @router.post("/tasks/{job_id}/cancel", name="admin_task_cancel")
        async def task_cancel(
            request: Request,
            job_id: UUID,
            csrf_token: str = Form(...),
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Ask a queued or running job to stop, then show it again.

            Cancelling is cooperative: the row flips and the worker
            notices at its next progress tick, so the page the operator
            lands on may still say ``running`` for a moment. Saying that
            on the page beats implying the work stopped.

            Args:
                request (Request): The incoming request.
                job_id (UUID): The run to cancel.
                csrf_token (str): CSRF token from the form.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated session.

            Returns:
                Response: A redirect back to the run's detail page.

            Raises:
                HTTPException: ``303`` redirect to the login page when the
                    principal no longer qualifies, before the row is
                    touched; ``403`` on CSRF mismatch.
            """
            await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.TASKS
            )
            _check_csrf(session, csrf_token)
            await _panel.cancel(job_id)
            return RedirectResponse(
                f"{prefix}/tasks/{job_id}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

    if storage is not None:
        _browser = storage
        storage_url = f"{prefix}/storage"

        def _storage_redirect(path: str, params: dict[str, str]) -> RedirectResponse:
            """Redirect back to a storage page carrying a flash message.

            Args:
                path (str): Path under ``{prefix}/storage``; ``""`` for the
                    listing, ``"/object"`` for an object page.
                params (dict[str, str]): Query parameters, ``flash`` and
                    ``flash_cat`` included.

            Returns:
                RedirectResponse: ``303`` to the page.
            """
            return RedirectResponse(
                f"{storage_url}{path}?{urlencode(params)}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        def _folder_of(key: str) -> str:
            """Return the folder holding ``key``.

            Args:
                key (str): Object key.

            Returns:
                str: ``""`` for the root, otherwise the folder with a
                trailing ``/``.
            """
            folder, _, _ = key.rpartition("/")
            return f"{folder}/" if folder else ""

        def _refuse_read_only() -> None:
            """Refuse a write on a read-only browser, after auth and CSRF.

            Every write route resolves the principal and compares the CSRF
            token first, so a revoked operator learns nothing from the
            response, then calls this before the store is touched.

            Raises:
                HTTPException: ``403`` when the browser is read-only.
            """
            if _browser.read_only:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "storage browser is read-only"
                )

        @router.get("/storage", response_class=HTMLResponse, name="admin_storage")
        async def storage_list(
            request: Request,
            prefix: str = "",
            page: int = 1,
            flash: str = "",
            flash_cat: str = "success",
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Render one folder of the bucket, with the upload form.

            Args:
                request (Request): The incoming request.
                prefix (str): Folder to list; ``""`` for the root.
                page (int): 1-based page of the folder.
                flash (str): Message from the write that redirected here.
                flash_cat (str): ``success`` or ``error``.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal the chrome renders.
                session (AdminSession): The validated session.

            Returns:
                Response: The listing page; ``400`` with the reason when
                ``prefix`` is not a valid folder.
            """
            principal = await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.STORAGE
            )
            chrome: dict[str, Any] = {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "storage_url": storage_url,
                "browser": _browser,
                "flash": flash,
                "flash_cat": "error" if flash_cat == "error" else "success",
            }
            try:
                listing = await _browser.browse(prefix, page=page)
            except StorageBrowserError as exc:
                return _render(
                    request,
                    "storage.html",
                    {**chrome, "listing": None, "error": str(exc)},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            return _render(request, "storage.html", {**chrome, "listing": listing})

        @router.get(
            "/storage/object",
            response_class=HTMLResponse,
            name="admin_storage_object",
        )
        async def storage_object(
            request: Request,
            key: str = "",
            flash: str = "",
            flash_cat: str = "success",
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Render one object: metadata, preview and the edit forms.

            Args:
                request (Request): The incoming request.
                key (str): Object key.
                flash (str): Message from the write that redirected here.
                flash_cat (str): ``success`` or ``error``.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal the chrome renders.
                session (AdminSession): The validated session.

            Returns:
                Response: The object page; ``404`` when it is not stored,
                ``400`` when the key is invalid.
            """
            principal = await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.STORAGE
            )
            chrome: dict[str, Any] = {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "storage_url": storage_url,
                "browser": _browser,
                "flash": flash,
                "flash_cat": "error" if flash_cat == "error" else "success",
                "key": key,
            }
            try:
                view = await _browser.describe(key)
            except StorageObjectMissingError as exc:
                return _render(
                    request,
                    "storage_object.html",
                    {**chrome, "item": None, "error": str(exc)},
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            except StorageBrowserError as exc:
                return _render(
                    request,
                    "storage_object.html",
                    {**chrome, "item": None, "error": str(exc)},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            return _render(request, "storage_object.html", {**chrome, "item": view})

        @router.get("/storage/raw", name="admin_storage_raw")
        async def storage_raw(
            request: Request,
            key: str = "",
            download: bool = False,
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Stream an object's bytes, for the preview or a download.

            ``download=false`` asks for ``inline``, which
            :func:`~tempest_fastapi_sdk.storage.object_download_response`
            grants only to inline-safe types — HTML and SVG always arrive as
            attachments — and every response carries ``nosniff`` and a
            sandboxing CSP. ``Range`` is honoured, so audio and video seek.

            Args:
                request (Request): The incoming request.
                key (str): Object key.
                download (bool): Force ``attachment``.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated session.

            Returns:
                Response: The bytes, ``private, no-store``.

            Raises:
                HTTPException: ``400`` for an invalid key, ``404`` when the
                    object is not stored.
            """
            await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.STORAGE
            )
            try:
                check_storage_key(key)
                if not await _browser.exists(key):
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "object not found")
            except StorageBrowserError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
            return await object_download_response(
                _browser.client,
                key,
                request=request,
                bucket=_browser.bucket,
                filename=key.rpartition("/")[2],
                as_attachment=download,
                cache_control="private, no-store",
            )

        @router.post("/storage/upload", name="admin_storage_upload")
        async def storage_upload(
            request: Request,
            file: UploadFile = File(...),
            prefix: str = Form(default=""),
            name: str = Form(default=""),
            overwrite: bool = Form(default=False),
            csrf_token: str = Form(...),
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Store an uploaded file in the current folder.

            Args:
                request (Request): The incoming request.
                file (UploadFile): The file.
                prefix (str): Folder to store it in.
                name (str): Name to store it under, which may hold ``/`` to
                    create sub-folders; empty keeps the file's own name.
                overwrite (bool): Replace an object already under that key.
                csrf_token (str): CSRF token from the form.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated session.

            Returns:
                Response: A redirect to the new object's page, or back to
                the folder with the reason it was refused.
            """
            await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.STORAGE
            )
            _check_csrf(session, csrf_token)
            _refuse_read_only()
            try:
                key = await _browser.upload(
                    prefix,
                    name.strip() or (file.filename or ""),
                    file.read,
                    content_type=file.content_type,
                    overwrite=overwrite,
                )
            except StorageBrowserError as exc:
                return _storage_redirect(
                    "",
                    {"prefix": prefix, "flash": str(exc), "flash_cat": "error"},
                )
            finally:
                await file.close()
            return _storage_redirect(
                "/object", {"key": key, "flash": _t("storage.flash.uploaded", key=key)}
            )

        @router.post("/storage/object/replace", name="admin_storage_replace")
        async def storage_replace(
            request: Request,
            file: UploadFile = File(...),
            key: str = Form(...),
            csrf_token: str = Form(...),
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Overwrite an object's content with an uploaded file.

            Args:
                request (Request): The incoming request.
                file (UploadFile): The new content.
                key (str): Object key.
                csrf_token (str): CSRF token from the form.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated session.

            Returns:
                Response: A redirect back to the object page with the
                outcome.
            """
            await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.STORAGE
            )
            _check_csrf(session, csrf_token)
            _refuse_read_only()
            try:
                await _browser.replace(
                    key,
                    file.read,
                    content_type=file.content_type,
                    filename=file.filename,
                )
            except StorageBrowserError as exc:
                return _storage_redirect(
                    "/object", {"key": key, "flash": str(exc), "flash_cat": "error"}
                )
            finally:
                await file.close()
            return _storage_redirect(
                "/object", {"key": key, "flash": _t("storage.flash.replaced", key=key)}
            )

        @router.post("/storage/object/edit", name="admin_storage_edit")
        async def storage_edit(
            request: Request,
            key: str = Form(...),
            text: str = Form(default=""),
            etag: str = Form(default=""),
            csrf_token: str = Form(...),
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Save the text editor back to the object.

            Args:
                request (Request): The incoming request.
                key (str): Object key.
                text (str): The edited content.
                etag (str): ETag the editor was opened at; a mismatch means
                    someone wrote in between and the save is refused.
                csrf_token (str): CSRF token from the form.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated session.

            Returns:
                Response: A redirect back to the object page with the
                outcome.
            """
            await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.STORAGE
            )
            _check_csrf(session, csrf_token)
            _refuse_read_only()
            try:
                await _browser.save_text(key, text, expected_etag=etag or None)
            except StorageBrowserError as exc:
                return _storage_redirect(
                    "/object", {"key": key, "flash": str(exc), "flash_cat": "error"}
                )
            return _storage_redirect(
                "/object", {"key": key, "flash": _t("storage.flash.saved", key=key)}
            )

        @router.post("/storage/object/rename", name="admin_storage_rename")
        async def storage_rename(
            request: Request,
            key: str = Form(...),
            new_key: str = Form(...),
            csrf_token: str = Form(...),
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Move an object to another key.

            Args:
                request (Request): The incoming request.
                key (str): Current key.
                new_key (str): Target key, folders included.
                csrf_token (str): CSRF token from the form.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated session.

            Returns:
                Response: A redirect to the moved object's page, or back to
                the current one with the reason it was refused.
            """
            await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.STORAGE
            )
            _check_csrf(session, csrf_token)
            _refuse_read_only()
            try:
                target = await _browser.rename(key, new_key)
            except StorageBrowserError as exc:
                return _storage_redirect(
                    "/object", {"key": key, "flash": str(exc), "flash_cat": "error"}
                )
            return _storage_redirect(
                "/object",
                {
                    "key": target,
                    "flash": _t("storage.flash.moved", key=key, target=target),
                },
            )

        @router.post("/storage/object/delete", name="admin_storage_delete")
        async def storage_delete(
            request: Request,
            key: str = Form(...),
            csrf_token: str = Form(...),
            db_session: AsyncSession = Depends(_db_session),
            session: AdminSession = Depends(_require_session),
        ) -> Response:
            """Delete an object and return to its folder.

            Args:
                request (Request): The incoming request.
                key (str): Object key.
                csrf_token (str): CSRF token from the form.
                db_session (AsyncSession): The DB session, used to resolve
                    the principal.
                session (AdminSession): The validated session.

            Returns:
                Response: A redirect to the holding folder with the outcome.
            """
            await _resolve_principal(
                request, db_session, session, surface=AdminSurfaceEnum.STORAGE
            )
            _check_csrf(session, csrf_token)
            _refuse_read_only()
            try:
                await _browser.delete(key)
            except StorageBrowserError as exc:
                return _storage_redirect(
                    "",
                    {
                        "prefix": _folder_of(key),
                        "flash": str(exc),
                        "flash_cat": "error",
                    },
                )
            return _storage_redirect(
                "",
                {
                    "prefix": _folder_of(key),
                    "flash": _t("storage.flash.deleted", key=key),
                },
            )

    return router


class _RowView:
    """Bound row used by the list template.

    Exposes ``identity`` (the value used in the detail URL) and a
    ``values`` mapping keyed by column name.
    """

    def __init__(
        self,
        instance: Any,
        columns: list[str],
        identity_field: str,
        timezone: tzinfo | None = None,
    ) -> None:
        """Initialize the view.

        Args:
            instance (Any): The ORM row.
            columns (list[str]): Columns to expose.
            identity_field (str): Column to read for the row identity.
            timezone (tzinfo | None): When the admin declares a
                ``display_timezone``, every datetime cell is converted
                to it. The list would otherwise print the stored UTC
                hour beside a form that reads and writes local time,
                which is the same row described two ways.
        """
        self.instance: Any = instance
        self.identity: Any = getattr(instance, identity_field, None)
        self.pk: Any = getattr(instance, "id", None)
        self.values: dict[str, Any] = {
            col: _localized(getattr(instance, col, None), timezone) for col in columns
        }


def _localized(value: Any, timezone: tzinfo | None) -> Any:
    """Convert a datetime to ``timezone``, leaving anything else alone.

    Args:
        value (Any): The cell value.
        timezone (tzinfo | None): Target zone, or ``None`` to keep UTC.

    Returns:
        Any: The converted datetime, or ``value`` untouched.
    """
    if timezone is None or not isinstance(value, datetime):
        return value
    return to_display_timezone(value, timezone)


class _RowOutOfScopeError(Exception):
    """A write left its row outside the principal's ``row_scope``.

    Raised inside the write's transaction so the rollback discards it;
    the route turns it into the ``error.out_of_scope`` text on the form.
    """


class _EditConflictError(Exception):
    """The edited row changed after its form was opened."""


_VERSION_FIELD: Final[str] = "_row_version"
"""Hidden edit-form field carrying the ``updated_at`` the form was read at."""


def _version_token(admin: Any, instance: Any) -> str | None:
    """Return the row version an edit form carries.

    Args:
        admin (Any): The admin configuration.
        instance (Any): The row being edited.

    Returns:
        str | None: ``updated_at`` in ISO 8601, or ``None`` when the model
        has no ``updated_at`` column (or the row holds no value), which
        turns the lock off for that form.
    """
    if "updated_at" not in admin.column_names():
        return None
    value = getattr(instance, "updated_at", None)
    return value.isoformat() if isinstance(value, datetime) else None


def _parse_version(admin: Any, token: str | None) -> datetime | None:
    """Parse the version posted back by an edit form.

    Args:
        admin (Any): The admin configuration.
        token (str | None): The posted :data:`_VERSION_FIELD`.

    Returns:
        datetime | None: The version to claim, or ``None`` — no lock — when
        the model has no ``updated_at`` or the token is absent or not a
        datetime.
    """
    if token is None or "updated_at" not in admin.column_names():
        return None
    try:
        return datetime.fromisoformat(token)
    except ValueError:
        return None


def _all_of(*clauses: ColumnElement[bool] | None) -> ColumnElement[bool] | None:
    """AND the given clauses, skipping ``None``.

    Args:
        *clauses (ColumnElement[bool] | None): Conditions to combine.

    Returns:
        ColumnElement[bool] | None: ``None`` when every clause is ``None``,
        the single clause when only one is set, their ``AND`` otherwise.
    """
    present = [clause for clause in clauses if clause is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return and_(*present)


def _search_where(admin: Any, q: str) -> ColumnElement[bool] | None:
    """Build the list search: every word, in any of the ``search_fields``.

    The term used to become ``filters[field] = q`` for each search field —
    exact equality, and ANDed across fields — so a partial email found
    nothing and two search fields had to all equal the whole term (#635).
    It is now
    :func:`~tempest_fastapi_sdk.db.search.like_search_condition`: each
    word is an escaped ``ILIKE %word%`` ORed across the fields, and every
    word must match somewhere. ``%`` and ``_`` in the term are literal.
    The autocomplete endpoint already searched this way.

    Args:
        admin (Any): The admin configuration.
        q (str): The search box.

    Returns:
        ColumnElement[bool] | None: The condition, or ``None`` when the
        term is blank or the admin has no ``search_fields``.
    """
    if not admin.search_fields:
        return None
    return like_search_condition(admin.model, q, admin.search_fields)


def _resolve_filters_and_order(
    admin: Any,
    request: Request,
    q: str,
    sort: str,
    direction: str,
) -> tuple[dict[str, Any], dict[str, str], str | None, bool, str, bool]:
    """Build the repository filters + ordering from the request.

    Shared by the list view and the export endpoint so both honor the
    same filter / sort semantics. The free-text search is not a filter:
    :func:`_search_where` builds it as a ``WHERE`` of its own.

    Args:
        admin (Any): The admin configuration.
        request (Request): The inbound request (for filter query params).
        q (str): Free-text search term; unused here, kept so both call
            sites read the same request in one call.
        sort (str): Requested sort column (validated against the
            sortable columns; ignored otherwise).
        direction (str): ``"asc"`` or ``"desc"``.

    Returns:
        tuple: ``(filters, active_filters, order_key, ascending,
        active_sort, active_ascending)`` — ``active_sort`` is ``""``
        when the request did not select a valid sortable column (the
        admin default ordering applies instead).
    """
    filters: dict[str, Any] = {}
    active_filters: dict[str, str] = {}
    for field in admin.list_filter:
        kind = _filter_kind(admin, field)
        if kind == "date":
            py = _filter_python_type(admin, field)
            raw_from = request.query_params.get(f"filter_{field}_from")
            raw_to = request.query_params.get(f"filter_{field}_to")
            zone = admin.display_tzinfo
            bound = _coerce_date_bound(raw_from, py, end=False, zone=zone)
            if bound is not None:
                filters[f"{field}__gte"] = bound
                active_filters[f"filter_{field}_from"] = raw_from or ""
            bound = _coerce_date_bound(raw_to, py, end=True, zone=zone)
            if bound is not None:
                lookup = "lt" if _is_datetime_type(py) else "lte"
                filters[f"{field}__{lookup}"] = bound
                active_filters[f"filter_{field}_to"] = raw_to or ""
            continue
        value = request.query_params.get(f"filter_{field}")
        if value:
            active_filters[f"filter_{field}"] = value
            filters[field] = (
                _identity_value(value) if kind == "fk" else _coerce_filter_value(value)
            )
    sortable = _sortable_columns(admin)
    if sort and sort in sortable:
        ascending = direction != "desc"
        return filters, active_filters, sort, ascending, sort, ascending
    return filters, active_filters, admin.order_key, admin.order_ascending, "", True


def _sortable_columns(admin: Any) -> list[str]:
    """Return the displayed columns that map to a real DB column.

    Only real mapped columns can be ordered by, so this filters the
    ``list_display`` down to what is safe to pass to ``ORDER BY``.

    Args:
        admin (Any): The admin configuration.

    Returns:
        list[str]: Sortable column keys.
    """
    real = set(admin.column_names())
    return [col for col in admin.resolved_list_display() if col in real]


def _sort_state(
    admin: Any,
    active_sort: str,
    active_ascending: bool,
    base_params: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Build the per-column sort link + state for the list header.

    Args:
        admin (Any): The admin configuration.
        active_sort (str): The currently active sort column (``""`` for
            none).
        active_ascending (bool): Direction of the active sort.
        base_params (dict[str, str]): Query params to preserve in each
            sort link (search + filters); paging is intentionally reset.

    Returns:
        dict[str, dict[str, Any]]: Keyed by column → ``{url, active,
        ascending}``. Columns absent from the mapping are not sortable.
    """
    state: dict[str, dict[str, Any]] = {}
    for col in _sortable_columns(admin):
        is_active = col == active_sort
        next_dir = "desc" if (is_active and active_ascending) else "asc"
        params = {**base_params, "sort": col, "dir": next_dir}
        state[col] = {
            "url": "?" + urlencode(params),
            "active": is_active,
            "ascending": active_ascending if is_active else None,
        }
    return state


async def _system_metrics() -> dict[str, Any] | None:
    """Return a compact CPU/RAM/disk snapshot for the dashboard.

    Degrades to ``None`` when the ``[metrics]`` extra is missing or the
    sample fails, so the dashboard never errors on metrics.

    Returns:
        dict[str, Any] | None: ``cpu_percent`` / ``mem_percent`` /
        ``mem_used_gb`` / ``mem_total_gb`` / ``disk_percent``, or
        ``None`` when unavailable.
    """
    try:
        from tempest_fastapi_sdk.utils.metrics import MetricsUtils

        snap = await MetricsUtils.snapshot_async()
    except Exception:
        return None
    disk = snap.disks[0] if snap.disks else None
    return {
        "cpu_percent": round(snap.cpu.percent, 1),
        "mem_percent": round(snap.memory.percent, 1),
        "mem_used_gb": round(snap.memory.used_bytes / 1e9, 2),
        "mem_total_gb": round(snap.memory.total_bytes / 1e9, 2),
        "disk_percent": round(disk.percent, 1) if disk else None,
    }


_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _uploaded_keys(admin: Any, data: dict[str, Any]) -> list[str]:
    """Return the storage keys :func:`_save_uploads` just wrote into ``data``.

    Args:
        admin (Any): The admin configuration.
        data (dict[str, Any]): The parsed submission, after the uploads.

    Returns:
        list[str]: One key per upload field that received a file.
    """
    return [str(data[name]) for name in admin.upload_fields if data.get(name)]


async def _discard_uploads(admin: Any, keys: list[str]) -> None:
    """Delete files stored by a submission that was then refused.

    Best effort: the row was never written, so nothing points at the file;
    a failed delete is logged and the response goes on.

    Args:
        admin (Any): The admin configuration (owner of ``upload_storage``).
        keys (list[str]): The keys to remove.
    """
    for key in keys:
        try:
            await admin.upload_storage.delete(key)
        except Exception:
            logger.exception("Admin could not delete orphan upload %r", key)


async def _save_uploads(
    admin: Any,
    form: Any,
    data: dict[str, Any],
    *,
    creating: bool,
    locale: str,
) -> dict[str, str]:
    """Persist uploaded files and inject their storage keys into ``data``.

    For each configured upload field, a posted file is streamed to
    ``admin.upload_storage`` and the returned key is written to
    ``data[field]``. When no file is posted, the field is left untouched
    (so an edit keeps its current value); on create, a missing file for a
    non-nullable column is reported as a required-field error.

    A field listed in ``admin.upload_accept`` is checked before anything is
    written: the declared content type must be listed and, when a listed
    type is one :func:`~tempest_fastapi_sdk.sniff_mime` recognises, the
    first bytes must sniff as a listed type. A refused file is a field
    error and is never stored.

    Args:
        admin (Any): The admin configuration.
        form (Any): The parsed multipart form (Starlette ``FormData``).
        data (dict[str, Any]): The model kwargs being built (mutated).
        creating (bool): Whether this is a create (vs edit) submission.
        locale (str): Language of the field errors.

    Returns:
        dict[str, str]: Field → error message for missing or refused files.
    """
    errors: dict[str, str] = {}
    if not admin.upload_fields:
        return errors
    columns = sa_inspect(admin.model).columns
    for name in admin.upload_fields:
        upload = form.get(name)
        filename = getattr(upload, "filename", None)
        if upload is not None and filename:
            content_type = (
                getattr(upload, "content_type", None) or "application/octet-stream"
            )
            first: bytes = await upload.read(_UPLOAD_CHUNK_BYTES)
            refusal = _upload_refusal(
                admin.upload_accept.get(name), content_type, first, locale=locale
            )
            if refusal is not None:
                errors[name] = refusal
                continue

            async def _chunks(source: Any = upload, head: bytes = first) -> Any:
                if head:
                    yield head
                while True:
                    chunk = await source.read(_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    yield chunk

            key = f"{admin.get_slug()}/{name}/{uuid4().hex}{Path(filename).suffix}"
            result = await admin.upload_storage.write_stream(
                key, _chunks(), content_type=content_type
            )
            data[name] = result.key
        elif creating:
            column = columns.get(name)
            if column is not None and not column.nullable:
                errors[name] = admin_text(locale, "form.error.required")
    return errors


def _upload_refusal(
    accept: list[str] | None, content_type: str, head: bytes, *, locale: str
) -> str | None:
    """Tell why an upload is refused by its field's accepted types.

    Args:
        accept (list[str] | None): The field's ``upload_accept`` entry;
            ``None`` accepts anything.
        content_type (str): The type the browser declared.
        head (bytes): The file's first bytes.
        locale (str): Language of the field error.

    Returns:
        str | None: The field error, or ``None`` when the file is accepted.
    """
    if accept is None:
        return None
    message = admin_text(locale, "form.error.upload_type", types=", ".join(accept))
    if content_type.split(";")[0].strip().lower() not in accept:
        return message
    if set(accept) & SNIFFABLE_MIMETYPES and sniff_mime(head) not in accept:
        return message
    return None


_ACTION_INPUT_FIELD: Final[str] = "action_input"
"""Hidden marker of a submitted custom-action input form.

The list's bulk ``POST`` lacks it, so an action declared with ``schema``
answers its form; the form's own ``POST`` carries it and runs the action.
"""


def _bulk_confirm_text(label: str, *, dangerous: bool, locale: str) -> str:
    """Write the confirm prompt the list shows before running a bulk action.

    Args:
        label (str): The action's label.
        dangerous (bool): Whether the action is destructive
            (:attr:`~tempest_fastapi_sdk.admin.actions.AdminAction.dangerous`,
            and the built-in delete).
        locale (str): Language of the prompt.

    Returns:
        str: The generic prompt for a plain action; for a dangerous one, a
        prompt that names the action and says it cannot be undone.
    """
    if not dangerous:
        return admin_text(locale, "list.bulk_confirm")
    return admin_text(locale, "list.bulk_confirm_dangerous", label=label)


def _bulk_actions(admin: Any, locale: str) -> list[tuple[str, str, str]]:
    """Return the bulk actions available for ``admin``.

    Activation toggles need ``can_edit`` and ``can_toggle_active`` (every
    model carries the ``is_active`` flag from ``BaseModel``); delete needs
    ``can_delete``. The permission check on top of these flags is
    ``_permitted_bulk_actions``, inside :func:`make_admin_router`.

    Args:
        admin (Any): The admin configuration.
        locale (str): Language of the built-in labels and prompts; a custom
            action keeps the label it was declared with.

    Returns:
        list[tuple[str, str, str]]: ``(value, label, confirm)`` per action
        (empty when no mutation is permitted); ``confirm`` is the prompt
        the list shows before submitting it (:func:`_bulk_confirm_text`).

    Notes:
        Custom actions are namespaced as ``custom:<name>`` so their values
        can never collide with the built-in ones.
    """
    actions: list[tuple[str, str, str]] = []
    plain = _bulk_confirm_text("", dangerous=False, locale=locale)
    if admin.can_edit and admin.can_toggle_active:
        actions.append(("activate", admin_text(locale, "list.activate"), plain))
        actions.append(("deactivate", admin_text(locale, "list.deactivate"), plain))
    if admin.can_delete:
        delete_label = admin_text(locale, "common.delete")
        actions.append(
            (
                "delete",
                delete_label,
                _bulk_confirm_text(delete_label, dangerous=True, locale=locale),
            )
        )
    for action in admin.custom_actions():
        actions.append(
            (
                f"custom:{action.name}",
                action.label,
                _bulk_confirm_text(
                    action.label, dangerous=action.dangerous, locale=locale
                ),
            )
        )
    return actions


_AUDIT_FIELDS = ("created_at", "updated_at", "created_by", "updated_by")

#: Cap on audit-history rows rendered in the detail view.
_AUDIT_HISTORY_LIMIT = 50

#: Cap on options returned by an autocomplete FK search.
_AUTOCOMPLETE_LIMIT = 20

#: Cap on child rows rendered per inline on the detail view.
_INLINE_ROW_LIMIT = 50


def _prefix_row_fields(key: str, fields: list[FormField]) -> list[FormField]:
    """Namespace each form field's name for one formset row.

    Args:
        key (str): The row key (child id, or ``newN`` for a blank row).
        fields (list[FormField]): The child's form fields.

    Returns:
        list[FormField]: The same fields with ``name`` rewritten to
        ``row.<key>.<field>`` so rows don't collide in the POST body.
    """
    for form_field in fields:
        form_field.name = f"row.{key}.{form_field.name}"
    return fields


def _build_editable_inline(
    inline: Inline,
    child_admin: AdminModel[Any],
    children: list[Any],
    form_action: str,
    submitted_rows: list[tuple[str, dict[str, str], dict[str, str]]] | None,
    *,
    can_create: bool,
    can_delete: bool,
) -> dict[str, Any]:
    """Package one editable inline as an in-place formset block.

    Args:
        inline (Inline): The inline config.
        child_admin (AdminModel[Any]): The child's registered admin.
        children (list[Any]): The child rows referencing the parent.
        form_action (str): The POST URL the formset submits to.
        submitted_rows (list[...] | None): On an error re-render, the
            submitted ``(rowkey, values, errors)`` per row, so inputs show
            what the user typed with per-field errors.
        can_create (bool): Whether the blank add row is rendered — the
            child admin's ``can_create`` and the principal's ``CREATE``.
        can_delete (bool): Whether the per-row delete box is rendered —
            the inline's and the child admin's ``can_delete`` and the
            principal's ``DELETE``.

    Returns:
        dict[str, Any]: The template-ready editable inline block.

    Notes:
        The parent foreign key is dropped from the field list: it is implied
        by the row being edited and must never become a formset input.
    """
    names = [n for n in inline_editable_names(child_admin) if n != inline.fk_field]
    headers = [
        form_field.label for form_field in build_form_fields(child_admin, only=names)
    ]
    submitted_map = {key: (values, errs) for key, values, errs in submitted_rows or []}
    rows: list[dict[str, Any]] = []
    for child in children[:_INLINE_ROW_LIMIT]:
        key = str(getattr(child, "id", ""))
        if key in submitted_map:
            values, errs = submitted_map[key]
            fields = build_form_fields(
                child_admin, submitted=values, errors=errs, only=names
            )
        else:
            fields = build_form_fields(child_admin, instance=child, only=names)
        rows.append(
            {"key": key, "fields": _prefix_row_fields(key, fields), "is_new": False}
        )
    new_count = 0
    for key, (values, errs) in submitted_map.items():
        if key.startswith("new") and can_create:
            fields = build_form_fields(
                child_admin, submitted=values, errors=errs, only=names
            )
            rows.append(
                {"key": key, "fields": _prefix_row_fields(key, fields), "is_new": True}
            )
            new_count += 1
    new_row: dict[str, Any] | None = None
    if can_create:
        blank_key = f"new{new_count}"
        blank_fields = build_form_fields(child_admin, only=names)
        new_row = {
            "key": blank_key,
            "fields": _prefix_row_fields(blank_key, blank_fields),
        }
    return {
        "label": inline.get_label(),
        "editable": True,
        "columns": headers,
        "rows": rows,
        "new_row": new_row,
        "can_delete": can_delete,
        "form_action": form_action,
        "total": len(children),
        "truncated": len(children) > _INLINE_ROW_LIMIT,
    }


def _format_audit_changes(action: str, changes: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a stored audit ``changes`` blob into uniform rows.

    ``BaseAuditLogModel`` stores ``{"after": {...}}`` for create,
    ``{"before": {...}}`` for delete, and ``{field: {"before", "after"}}``
    for update. This flattens all three into
    ``[{"field", "before", "after"}, ...]`` so the template renders one
    table regardless of action.

    Args:
        action (str): The audit action value (``create`` / ``update`` /
            ``delete``).
        changes (dict[str, Any]): The stored diff blob.

    Returns:
        list[dict[str, Any]]: One row per changed field, sorted by name.
    """
    rows: list[dict[str, Any]] = []
    if action == "create":
        after = changes.get("after", {})
        rows = [{"field": k, "before": None, "after": v} for k, v in after.items()]
    elif action == "delete":
        before = changes.get("before", {})
        rows = [{"field": k, "before": v, "after": None} for k, v in before.items()]
    else:
        for field, delta in changes.items():
            if isinstance(delta, dict):
                rows.append(
                    {
                        "field": field,
                        "before": delta.get("before"),
                        "after": delta.get("after"),
                    }
                )
    return sorted(rows, key=lambda row: str(row["field"]))


def _stamp_audit(instance: Any, actor_id: str, *, creating: bool) -> None:
    """Stamp ``created_by`` / ``updated_by`` with the acting admin.

    No-op for models without the audit columns (``AuditMixin``) or when
    ``actor_id`` is not a UUID.

    Args:
        instance (Any): The row being created or edited.
        actor_id (str): The acting principal's id.
        creating (bool): ``True`` on create (stamps both columns),
            ``False`` on edit (stamps ``updated_by`` only).
    """
    from uuid import UUID

    try:
        actor = UUID(actor_id)
    except (ValueError, TypeError):
        return
    if creating and hasattr(instance, "created_by"):
        instance.created_by = actor
    if hasattr(instance, "updated_by"):
        instance.updated_by = actor


def _identity_value(identity: str) -> Any:
    """Coerce a URL identity segment to a UUID when possible.

    Args:
        identity (str): The raw path segment.

    Returns:
        Any: A :class:`uuid.UUID` when ``identity`` parses as one, else
        the original string (so non-UUID primary keys still work).
    """
    from uuid import UUID

    try:
        return UUID(identity)
    except (ValueError, TypeError):
        return identity


def _export_value(value: Any) -> Any:
    """Normalize a column value for export serialization.

    Args:
        value (Any): The raw ORM attribute value.

    Returns:
        Any: ``None`` untouched (so CSV emits blank / JSON emits null);
        ``datetime`` as ISO 8601; everything else stringified for CSV
        safety but JSON-native scalars (bool/int/float) preserved by the
        caller.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


_CSV_FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
"""Leading characters a spreadsheet reads as the start of a formula.

The set OWASP lists for CSV injection. A cell starting with one of them
is written with a leading ``'`` so Excel, LibreOffice and Google Sheets
show it as text instead of evaluating it.
"""


def _csv_cell(value: Any) -> Any:
    """Neutralize a CSV cell a spreadsheet would evaluate as a formula.

    Only text is touched: a number keeps its sign (``-5`` is an ``int``,
    not a formula), while a stored string such as ``=HYPERLINK(...)`` or
    ``-5`` gains a leading ``'``. The JSON export is left alone, since no
    spreadsheet evaluates it.

    Args:
        value (Any): A value already passed through :func:`_export_value`.

    Returns:
        Any: ``value`` unchanged, or the string prefixed with ``'``.
    """
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_TRIGGERS):
        return f"'{value}"
    return value


def _to_csv(columns: list[str], items: list[Any]) -> str:
    """Serialize ``items`` to a CSV document with a header row.

    Every cell goes through :func:`_csv_cell`, so a value that would open
    as a live formula in a spreadsheet is written as text.

    Args:
        columns (list[str]): Column keys (header + value order).
        items (list[Any]): ORM rows.

    Returns:
        str: The CSV text.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for instance in items:
        writer.writerow(
            [_csv_cell(_export_value(getattr(instance, col, None))) for col in columns]
        )
    return buffer.getvalue()


def _to_json(columns: list[str], items: list[Any]) -> str:
    """Serialize ``items`` to a JSON array of column→value objects.

    Args:
        columns (list[str]): Column keys.
        items (list[Any]): ORM rows.

    Returns:
        str: The JSON text.
    """
    rows = [
        {col: _export_value(getattr(instance, col, None)) for col in columns}
        for instance in items
    ]
    return json.dumps(rows, ensure_ascii=False, default=str)


def _coerce_filter_value(raw: str) -> Any:
    """Best-effort coercion of a query-string value to a Python value.

    Recognizes booleans and integers; everything else is returned as
    the original string so the repository's filter pipeline applies
    its usual conventions (``ILIKE`` for strings, etc.).

    Args:
        raw (str): The raw query parameter value.

    Returns:
        Any: The coerced value.
    """
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


def _filter_python_type(admin: Any, field: str) -> type | None:
    """Return the Python type of a mapped column, or ``None``.

    Args:
        admin (Any): The admin configuration.
        field (str): The column key.

    Returns:
        type | None: The column's Python type, or ``None`` when the
        column is unknown or has no resolvable type.
    """
    column = sa_inspect(admin.model).columns.get(field)
    if column is None:
        return None
    try:
        py: type = column.type.python_type
    except (NotImplementedError, AttributeError):
        return None
    return py


def _filter_kind(admin: Any, field: str) -> str:
    """Classify a list-filter field to pick its widget + query semantics.

    Args:
        admin (Any): The admin configuration.
        field (str): The list-filter field key.

    Returns:
        str: One of ``"fk"`` / ``"bool"`` / ``"enum"`` / ``"date"`` /
        ``"other"``.
    """
    column = sa_inspect(admin.model).columns.get(field)
    if column is None:
        return "other"
    if column.foreign_keys:
        return "fk"
    py = _filter_python_type(admin, field)
    if py is bool:
        return "bool"
    if isinstance(py, type) and issubclass(py, Enum):
        return "enum"
    if isinstance(py, type) and issubclass(py, (datetime, date)):
        return "date"
    return "other"


def _is_datetime_type(py: type | None) -> bool:
    """Tell whether a column's Python type is ``datetime`` (not ``date``).

    Args:
        py (type | None): The column's Python type.

    Returns:
        bool: ``True`` for ``datetime`` and its subclasses.
    """
    return isinstance(py, type) and issubclass(py, datetime)


def _coerce_date_bound(
    raw: str | None,
    py: type | None,
    *,
    end: bool,
    zone: tzinfo | None = None,
) -> Any:
    """Parse a ``YYYY-MM-DD`` filter bound into a date/datetime.

    For a ``datetime`` column the day is the operator's day: it starts at
    midnight in ``zone`` (the admin's ``display_timezone``, UTC when none
    is set) and the bound is returned in UTC. The upper bound is the
    **next** midnight, meant for an exclusive ``<``: a ``23:59:59`` cut
    left out a row stamped ``23:59:59.5``. A ``date`` column compares
    dates and is returned as is.

    Args:
        raw (str | None): The submitted ``date`` input value.
        py (type | None): The column's Python type (``date`` vs
            ``datetime``).
        end (bool): Whether this is the upper bound of the range.
        zone (tzinfo | None): Zone the day is read in; ``None`` is UTC.

    Returns:
        Any: A ``date`` / ``datetime`` bound, or ``None`` when ``raw`` is
        empty or unparseable (the bound is then skipped).
    """
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    if _is_datetime_type(py):
        day = parsed + timedelta(days=1) if end else parsed
        return datetime.combine(day, time(0, 0, 0), tzinfo=zone or UTC).astimezone(UTC)
    return parsed


_NON_FILTER_PARAMS: Final[frozenset[str]] = frozenset({"page", "flash", "flash_cat"})
"""Query parameters a filter link drops: paging restarts, a flash is spent."""


def _date_shortcut_ranges(today: date) -> list[tuple[date, date]]:
    """Return the inclusive day ranges behind the date-filter shortcuts.

    In order: today; the last 7 days and the last 30 days, both ending
    today; this calendar month; the previous calendar month. The bounds
    are dates the filter then reads in the model's ``display_timezone``
    (:func:`_coerce_date_bound`), which is also where ``today`` comes from,
    so "today" is the operator's day, not UTC's.

    Args:
        today (date): The current day in the model's display zone.

    Returns:
        list[tuple[date, date]]: ``(from, to)`` per shortcut, both inclusive.
    """
    first_of_month = today.replace(day=1)
    next_month = (first_of_month + timedelta(days=32)).replace(day=1)
    last_of_previous = first_of_month - timedelta(days=1)
    return [
        (today, today),
        (today - timedelta(days=6), today),
        (today - timedelta(days=29), today),
        (first_of_month, next_month - timedelta(days=1)),
        (last_of_previous.replace(day=1), last_of_previous),
    ]


def _date_shortcut_url(request: Request, field: str, start: date, end: date) -> str:
    """Build the list link that applies one date-range shortcut.

    Every other query parameter — search, the other filters, the sort, the
    lens — is kept as the operator set it; only this field's two bounds
    are replaced, and paging and a pending flash are dropped.

    Args:
        request (Request): The list request whose query is kept.
        field (str): The date column the shortcut filters.
        start (date): The first day, inclusive.
        end (date): The last day, inclusive.

    Returns:
        str: A relative ``?query`` link.
    """
    replaced = {f"filter_{field}_from", f"filter_{field}_to"}
    kept = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key not in replaced and key not in _NON_FILTER_PARAMS
    ]
    kept.append((f"filter_{field}_from", start.isoformat()))
    kept.append((f"filter_{field}_to", end.isoformat()))
    return "?" + urlencode(kept)


def _enum_filter_options(enum_cls: Any) -> list[tuple[str, str]]:
    """Return ``(value, label)`` options for an enum column.

    Args:
        enum_cls (Any): The enum class (an ``Enum`` subclass).

    Returns:
        list[tuple[str, str]]: One pair per member (value, member name).
    """
    return [(str(member.value), member.name) for member in enum_cls]


__all__: list[str] = [
    "make_admin_router",
]

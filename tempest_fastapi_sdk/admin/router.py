"""HTML router wiring the admin site to FastAPI."""

from __future__ import annotations

import csv
import io
import json
import secrets
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from uuid import uuid4

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
from sqlalchemy import JSON, or_, select
from sqlalchemy import inspect as sa_inspect
from starlette.concurrency import run_in_threadpool

from tempest_fastapi_sdk.admin.actions import AdminActionContext
from tempest_fastapi_sdk.admin.auth import AdminAuthBackend, AdminAuthError
from tempest_fastapi_sdk.admin.config import AdminModel
from tempest_fastapi_sdk.admin.dashboard import (
    MetricPartition,
    MetricTrend,
)
from tempest_fastapi_sdk.admin.forms import (
    build_form_fields,
    fk_fields,
    fk_label,
    parse_submission,
)
from tempest_fastapi_sdk.admin.permissions import AdminAccessPolicy, AdminPermission
from tempest_fastapi_sdk.admin.session import (
    AdminSession,
    SessionStore,
    SignedCookieSessionStore,
)
from tempest_fastapi_sdk.admin.site import AdminSite
from tempest_fastapi_sdk.api.routers.logs import (
    LogSource,
    _read_entries,
    _resolve_files,
)
from tempest_fastapi_sdk.db.expressions import escape_like
from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.exceptions import AppException

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

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
    auth_backend: AdminAuthBackend,
    secret_key: str,
    prefix: str = "/admin",
    session_store: SessionStore | None = None,
    cookie_secure: bool = True,
    export_max_rows: int = 5000,
    show_metrics: bool = True,
    show_logs: bool = False,
    log_dir: str | Path = "logs",
    access_policy: AdminAccessPolicy | None = None,
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
      deactivate on the selected rows.
    * ``GET/POST {prefix}/m/{slug}/new`` — create form + submit
      (when ``can_create``).
    * ``GET  {prefix}/m/{slug}/{identity}`` — detail view.
    * ``GET/POST {prefix}/m/{slug}/{identity}/edit`` — edit form +
      submit (when ``can_edit``).
    * ``POST {prefix}/m/{slug}/{identity}/delete`` — delete row
      (when ``can_delete``).
    * Static files under ``{prefix}/static`` named ``admin_static``.

    Args:
        site (AdminSite): The configured registry.
        db (AsyncDatabaseManager): Active DB manager (used for both
            sessions and the readiness check).
        auth_backend (AdminAuthBackend): Backend resolving login
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
            hook asked ``(principal, admin, AdminPermission)`` for every
            model action. ``None`` (default) allows everything (subject
            to the ``AdminModel.can_*`` flags). A policy that denies an
            action yields ``403`` and hides the model from the dashboard
            + nav for ``VIEW``. Composes with the ``can_*`` flags — both
            must allow.

    Returns:
        APIRouter: A router ready to attach via ``app.include_router``.
    """
    try:
        from fastapi.templating import Jinja2Templates
    except ImportError as exc:
        raise ImportError(
            "Admin requires the [admin] extra. "
            "Install with `pip install tempest-fastapi-sdk[admin]`."
        ) from exc

    store: SessionStore = session_store or SignedCookieSessionStore(
        secret_key,
        secure=cookie_secure,
        path=prefix,
    )
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["pluralize"] = _pluralize

    router = APIRouter(prefix=prefix, include_in_schema=False)

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

    async def _require_session(request: Request) -> AdminSession:
        """Return the active session or redirect to login.

        Args:
            request (Request): The inbound request.

        Returns:
            AdminSession: The validated session payload.

        Raises:
            HTTPException: ``303`` redirect to ``/login`` when absent.
        """
        session = store.load(request)
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

    async def _resolve_principal(
        request: Request,
        db_session: AsyncSession,
        admin_session: AdminSession,
    ) -> Any:
        """Reload the admin principal or redirect to login.

        Args:
            request (Request): The inbound request.
            db_session (AsyncSession): The DB session.
            admin_session (AdminSession): The validated cookie.

        Returns:
            Any: The reloaded principal.

        Raises:
            HTTPException: ``303`` redirect when the principal is gone.
        """
        principal = await auth_backend.load_principal(
            db_session, admin_session.principal_id
        )
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                detail="login required",
                headers={"location": f"{prefix}/login"},
            )
        return principal

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
        """
        context.setdefault("site", site)
        context.setdefault("messages", [])
        context.setdefault("static_url", f"{prefix}/static")
        # Sidebar navigation, shared by every authenticated page. Built
        # from the registry (no DB hit) so a single source drives both
        # the dashboard cards and the persistent sidebar.
        context.setdefault(
            "nav_models",
            [
                {
                    "label": admin.get_verbose_name_plural(),
                    "url": f"{prefix}/m/{admin.get_slug()}/",
                }
                for admin in site.iter_models()
            ],
        )
        context.setdefault("nav_index_url", f"{prefix}/")
        context.setdefault("nav_logs_url", f"{prefix}/logs" if show_logs else None)
        return templates.TemplateResponse(
            request,
            template,
            context,
            status_code=status_code,
        )

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
        action: AdminPermission,
    ) -> bool:
        """Return whether ``principal`` may perform ``action`` on ``admin``.

        With no ``access_policy`` configured, everything is allowed (the
        historical behavior). Otherwise the policy decides.

        Args:
            principal (Any): The current admin principal.
            admin (AdminModel[Any]): The target admin.
            action (AdminPermission): The action being attempted.

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
        action: AdminPermission,
    ) -> None:
        """Raise ``403`` unless ``principal`` may perform ``action``.

        Args:
            principal (Any): The current admin principal.
            admin (AdminModel[Any]): The target admin.
            action (AdminPermission): The action being attempted.

        Raises:
            HTTPException: ``403`` when the policy denies the action.
        """
        if not await _allows(principal, admin, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")

    async def _visible_nav(principal: Any) -> list[dict[str, str]]:
        """Return the sidebar nav entries the principal may VIEW.

        Args:
            principal (Any): The current admin principal.

        Returns:
            list[dict[str, str]]: ``{label, url}`` for each allowed model.
        """
        entries: list[dict[str, str]] = []
        for admin in site.iter_models():
            if await _allows(principal, admin, AdminPermission.VIEW):
                entries.append(
                    {
                        "label": admin.get_verbose_name_plural(),
                        "url": f"{prefix}/m/{admin.get_slug()}/",
                    }
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

    async def _resolve_fk_options(
        admin: AdminModel[Any],
        db_session: AsyncSession,
    ) -> dict[str, list[tuple[str, str]]]:
        """Build select options for FK fields whose target is registered.

        A foreign key whose referenced table has its own
        :class:`AdminModel` becomes a dropdown of related rows
        (Django's FK select). FKs to unmanaged tables stay plain
        UUID text inputs.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            db_session (AsyncSession): The DB session.

        Returns:
            dict[str, list[tuple[str, str]]]: Field → ``(value, label)``
            options, capped at ``FK_OPTION_CAP`` rows.
        """
        options: dict[str, list[tuple[str, str]]] = {}
        for field_name, table in fk_fields(admin).items():
            # Autocomplete FKs are searched on demand — never pre-loaded.
            if field_name in admin.autocomplete_fields:
                continue
            referenced = site.get(table)
            if referenced is None:
                continue
            rows = await referenced.build_repository(db_session).list()
            options[field_name] = [
                (str(row.id), fk_label(referenced, row)) for row in rows[:FK_OPTION_CAP]
            ]
        return options

    async def _fk_filter_options(
        admin: AdminModel[Any],
        field: str,
        db_session: AsyncSession,
    ) -> list[tuple[str, str]]:
        """Return ``(value, label)`` options for a FK list-filter field.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            field (str): The foreign-key column key.
            db_session (AsyncSession): The DB session.

        Returns:
            list[tuple[str, str]]: Related-row options (``[]`` when the
            target table has no registered admin), capped at
            ``FK_OPTION_CAP``.
        """
        column = sa_inspect(admin.model).columns.get(field)
        if column is None or not column.foreign_keys:
            return []
        table = next(iter(column.foreign_keys)).column.table.name
        referenced = site.get(table)
        if referenced is None:
            return []
        rows = await referenced.build_repository(db_session).list()
        return [
            (str(row.id), fk_label(referenced, row)) for row in rows[:FK_OPTION_CAP]
        ]

    async def _filter_specs(
        admin: AdminModel[Any],
        request: Request,
        db_session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Build the list-view filter widgets for each ``list_filter`` field.

        Each spec is a ``select`` (bool / enum / FK), a ``daterange`` (two
        date inputs) or a ``text`` input, with the active value(s)
        pre-filled.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            request (Request): The inbound request (for active values).
            db_session (AsyncSession): The DB session (for FK options).

        Returns:
            list[dict[str, Any]]: Widget descriptors for the template.
        """
        specs: list[dict[str, Any]] = []
        for field in admin.list_filter:
            kind = _filter_kind(admin, field)
            label = field.replace("_", " ").strip().title()
            if kind == "date":
                specs.append(
                    {
                        "field": field,
                        "label": label,
                        "type": "daterange",
                        "value_from": request.query_params.get(
                            f"filter_{field}_from", ""
                        ),
                        "value_to": request.query_params.get(f"filter_{field}_to", ""),
                    }
                )
                continue
            if kind in ("bool", "enum", "fk"):
                active = request.query_params.get(f"filter_{field}", "")
                if kind == "bool":
                    pairs = [("true", "Yes"), ("false", "No")]
                elif kind == "enum":
                    enum_cls = _filter_python_type(admin, field)
                    pairs = _enum_filter_options(enum_cls) if enum_cls else []
                else:
                    pairs = await _fk_filter_options(admin, field, db_session)
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
        *,
        instance: Any | None = None,
        submitted: Any | None = None,
        errors: Any | None = None,
    ) -> Any:
        """Resolve FK options then build the form-field descriptors.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            db_session (AsyncSession): The DB session.
            instance (Any | None): Row being edited (pre-fill).
            submitted (Any | None): Rejected submission to re-render.
            errors (Any | None): Per-field error messages.

        Returns:
            Any: The list of form-field descriptors.
        """
        fk_options = await _resolve_fk_options(admin, db_session)
        fields = build_form_fields(
            admin,
            instance=instance,
            submitted=submitted,
            errors=errors,
            fk_options=fk_options,
        )
        if admin.autocomplete_fields:
            await _decorate_autocomplete_fields(admin, fields, db_session)
        return fields

    async def _decorate_autocomplete_fields(
        admin: AdminModel[Any],
        fields: list[Any],
        db_session: AsyncSession,
    ) -> None:
        """Fill in the search URL + current label for autocomplete fields.

        Args:
            admin (AdminModel[Any]): The admin being rendered.
            fields (list[Any]): The form fields to mutate in place.
            db_session (AsyncSession): The DB session, used to resolve the
                label of the currently-selected row (one lookup per field).
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
            current = await referenced.build_repository(db_session).get_or_none(
                {referenced.identity_field: _identity_value(str(form_field.value))}
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
        """
        if store.load(request) is not None:
            return _render(request, "login.html", {"user": None})
        return _render(
            request,
            "login.html",
            {"user": None, "error": None, "next": next or ""},
        )

    @router.post("/login", name="admin_login")
    async def login_submit(
        request: Request,
        identifier: str = Form(...),
        password: str = Form(...),
        db_session: AsyncSession = Depends(_db_session),
    ) -> Response:
        """Verify credentials and issue the session cookie.

        Args:
            request (Request): The inbound request.
            identifier (str): The email submitted in the form.
            password (str): The submitted password.
            db_session (AsyncSession): The DB session.

        Returns:
            Response: Redirect to the dashboard on success; rendered
            login form with an error message on failure.
        """
        try:
            principal = await auth_backend.authenticate(
                db_session,
                identifier=identifier,
                password=password,
            )
        except AdminAuthError as exc:
            return _render(
                request,
                "login.html",
                {"user": None, "error": exc.message},
                status_code=exc.status_code,
            )
        pending = auth_backend.mfa_enabled(principal)
        session = AdminSession(
            principal_id=auth_backend.principal_id(principal),
            issued_at=datetime.now(tz=UTC).timestamp(),
            csrf_token=secrets.token_urlsafe(32),
            mfa_pending=pending,
        )
        response = RedirectResponse(
            url=f"{prefix}/mfa" if pending else f"{prefix}/",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        store.save(response, session)
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
        session = store.load(request)
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
        session = store.load(request)
        if session is None or not session.mfa_pending:
            return RedirectResponse(
                f"{prefix}/login", status_code=status.HTTP_303_SEE_OTHER
            )
        if not secrets.compare_digest(session.csrf_token, csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf token mismatch")
        principal = await auth_backend.load_principal(db_session, session.principal_id)
        if principal is None:
            return RedirectResponse(
                f"{prefix}/login", status_code=status.HTTP_303_SEE_OTHER
            )
        if not auth_backend.verify_mfa(principal, code):
            return _render(
                request,
                "mfa.html",
                {"user": None, "session": session, "error": "Invalid code"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        upgraded = AdminSession(
            principal_id=session.principal_id,
            issued_at=datetime.now(tz=UTC).timestamp(),
            csrf_token=secrets.token_urlsafe(32),
            mfa_pending=False,
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
            if not await _allows(principal, admin, AdminPermission.VIEW):
                continue
            try:
                count = await admin.build_repository(db_session).count()
            except Exception:
                count = None
            models_view.append(
                {
                    "admin": admin,
                    "count": count,
                    "url": f"{prefix}/m/{admin.get_slug()}/",
                    "new_url": f"{prefix}/m/{admin.get_slug()}/new"
                    if admin.can_create
                    and await _allows(principal, admin, AdminPermission.CREATE)
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
                "nav_models": await _visible_nav(principal),
                "cards": cards,
                "metrics": await _system_metrics() if show_metrics else None,
            },
        )

    async def _build_dashboard_cards(
        db_session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Compute the site's business-metric cards for the dashboard.

        A card whose ``compute`` raises is skipped, so one broken metric
        never blanks the whole dashboard.

        Args:
            db_session (AsyncSession): The DB session passed to each card.

        Returns:
            list[dict[str, Any]]: Template-ready card descriptors.
        """
        cards: list[dict[str, Any]] = []
        for card in site.dashboard_cards:
            try:
                data = await card.compute(db_session)
            except Exception:  # a broken metric must not blank the page
                continue
            entry: dict[str, Any] = {"label": card.label, "help": card.help_text}
            if isinstance(data, MetricTrend):
                entry.update(
                    kind="trend",
                    value=data.value,
                    previous=data.previous,
                    unit=data.unit,
                    delta=data.delta,
                    pct=data.pct,
                    direction=data.direction,
                )
            elif isinstance(data, MetricPartition):
                total = data.total
                entry.update(
                    kind="partition",
                    total=total,
                    segments=[
                        {
                            "label": label,
                            "value": value,
                            "pct": (value / total * 100.0) if total else 0.0,
                        }
                        for label, value in data.segments
                    ],
                )
            else:
                entry.update(kind="value", value=data.value, unit=data.unit)
            cards.append(entry)
        return cards

    if show_logs:
        _log_base = Path(log_dir)
        _logs_page_size = 50

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

            Returns:
                HTMLResponse: The logs template (empty state when no log
                files exist on disk).
            """
            principal = await _resolve_principal(request, db_session, session)

            files = _resolve_files(_log_base, source)
            entries = await run_in_threadpool(_read_entries, files)

            needle = q.lower() if q else None
            if needle is not None:
                entries = [
                    entry
                    for entry in entries
                    if needle in str(entry.get("message", "")).lower()
                ]
            entries.sort(
                key=lambda item: str(item.get("timestamp", "")),
                reverse=True,
            )

            total = len(entries)
            offset = (page - 1) * _logs_page_size
            window = entries[offset : offset + _logs_page_size]

            available = _log_base.exists() and any(
                candidate.exists() for candidate in _resolve_files(_log_base, "all")
            )

            kept_params = {"source": source, "q": q or ""}
            pagination = _Pagination(
                page=page,
                size=_logs_page_size,
                total=total,
                query_params={k: v for k, v in kept_params.items() if v},
            )
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
        """
        admin = site.get(slug)
        if admin is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown admin")
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermission.VIEW)

        repository = admin.build_repository(db_session)

        (
            filters,
            active_filters,
            order_key,
            ascending,
            active_sort,
            active_ascending,
        ) = _resolve_filters_and_order(admin, request, q, sort, dir)

        # A lens is a saved preset: its filters are ANDed under the
        # user's (user keys win on conflict) and its ordering applies
        # unless the user clicked a column sort.
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
        )

        columns = admin.resolved_list_display()
        rows = [
            _RowView(instance, columns, admin.identity_field)
            for instance in result["items"]
        ]

        # Params preserved on pagination links (search + filters + sort).
        # active_filters already holds the full ``filter_*`` query keys.
        query_params: dict[str, str] = {}
        if q:
            query_params["q"] = q
        if active_lens is not None:
            query_params["lens"] = lens
        query_params.update(active_filters)
        # Params preserved on sort links — same minus paging/sort.
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
                    "label": "All",
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
                "can_create": admin.can_create
                and await _allows(principal, admin, AdminPermission.CREATE),
                "new_url": f"{prefix}/m/{slug}/new",
                "bulk_actions": _bulk_actions(admin),
                "bulk_url": f"{prefix}/m/{slug}/bulk",
                "filters": await _filter_specs(admin, request, db_session),
                "nav_models": await _visible_nav(principal),
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
        await _require_access(principal, admin, AdminPermission.VIEW)

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
        """
        admin = _require_admin(slug, lambda a: a.can_create)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermission.CREATE)
        # Pre-fill editable fields from query params — this is what an
        # inline "Add" link uses to seed the parent foreign key.
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
                    admin, db_session, submitted=prefill or None
                ),
                "action_url": f"{prefix}/m/{slug}/new",
                "back_url": f"{prefix}/m/{slug}/",
                "form_error": None,
            },
        )

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
        await _require_access(principal, admin, AdminPermission.CREATE)
        return _render(
            request,
            "import.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "columns": admin.editable_field_names(),
                "action_url": f"{prefix}/m/{slug}/import",
                "back_url": f"{prefix}/m/{slug}/",
                "result": None,
                "form_error": None,
            },
        )

    @router.post("/m/{slug}/import", name="admin_import_submit")
    async def import_submit(
        request: Request,
        slug: str,
        csrf_token: str = Form(...),
        file: UploadFile = File(...),
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Bulk-create rows from an uploaded CSV.

        Each CSV row is validated + coerced with the same rules as the
        create form; valid rows are inserted (best-effort, one row's
        failure never aborts the others) and a per-row error report is
        rendered alongside the created count.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug.
            csrf_token (str): CSRF token from the form.
            file (UploadFile): The uploaded CSV file.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The import template with the result report.

        Raises:
            HTTPException: ``404`` when import is disabled; ``403`` on
                CSRF mismatch.
        """
        admin = _require_admin(slug, lambda a: a.can_import and a.can_create)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermission.CREATE)
        _check_csrf(session, csrf_token)

        form_error: str | None = None
        result: dict[str, Any] | None = None
        try:
            raw = await file.read()
            text = raw.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
        except (UnicodeDecodeError, csv.Error):
            form_error = "Could not read the file as UTF-8 CSV."
        else:
            repository = admin.build_repository(db_session)
            actor_id = auth_backend.principal_id(principal)
            created = 0
            row_errors: list[dict[str, Any]] = []
            for line_no, row in enumerate(reader, start=2):  # row 1 is the header
                data, errors = parse_submission(admin, row)
                if errors:
                    row_errors.append({"row": line_no, "errors": errors})
                    continue
                instance = admin.model(**data)
                _stamp_audit(instance, actor_id, creating=True)
                try:
                    await repository.add(instance)
                except AppException as exc:
                    row_errors.append({"row": line_no, "errors": {"": exc.message}})
                    continue
                created += 1
            result = {"created": created, "errors": row_errors}

        status_code = status.HTTP_400_BAD_REQUEST if form_error else status.HTTP_200_OK
        return _render(
            request,
            "import.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "columns": admin.editable_field_names(),
                "action_url": f"{prefix}/m/{slug}/import",
                "back_url": f"{prefix}/m/{slug}/",
                "result": result,
                "form_error": form_error,
            },
            status_code=status_code,
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
        admin = _require_admin(slug, lambda a: a.can_create)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermission.CREATE)
        _check_csrf(session, csrf_token)
        form = await request.form()
        data, errors = parse_submission(admin, form)
        errors.update(await _save_uploads(admin, form, data, creating=True))
        form_error: str | None = None
        if not errors:
            repository = admin.build_repository(db_session)
            instance = admin.model(**data)
            _stamp_audit(instance, auth_backend.principal_id(principal), creating=True)
            try:
                saved = await repository.add(instance)
            except AppException as exc:
                form_error = exc.message
            else:
                identity = getattr(saved, admin.identity_field)
                return RedirectResponse(
                    url=f"{prefix}/m/{slug}/{identity}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
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
                    admin, db_session, submitted=form, errors=errors
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
            Response: Redirect back to the list view.

        Raises:
            HTTPException: ``404`` for unknown admin, ``403`` on CSRF
                mismatch, ``400`` for an unknown / unpermitted action.
        """
        admin = site.get(slug)
        if admin is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown admin")
        principal = await _resolve_principal(request, db_session, session)
        # "delete" removes rows → DELETE; activate/deactivate/custom mutate
        # rows → EDIT.
        needed = AdminPermission.DELETE if action == "delete" else AdminPermission.EDIT
        await _require_access(principal, admin, needed)
        _check_csrf(session, csrf_token)
        form = await request.form()
        ids = [_identity_value(str(raw)) for raw in form.getlist("ids")]
        redirect_url = f"{prefix}/m/{slug}/"
        if ids and action.startswith("custom:"):
            admin_action = admin.get_action(action.removeprefix("custom:"))
            if admin_action is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown action")
            result = await admin_action.handler(
                AdminActionContext(
                    ids=ids,
                    repository=admin.build_repository(db_session),
                    db_session=db_session,
                    request=request,
                    session=session,
                    principal=principal,
                )
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
                await repository.delete_batch(ids)
            elif action in ("activate", "deactivate"):
                if not admin.can_edit:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "edit disabled")
                await repository.bulk_update(
                    {"id": ids}, {"is_active": action == "activate"}
                )
            else:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown action")
        return RedirectResponse(
            url=redirect_url,
            status_code=status.HTTP_303_SEE_OTHER,
        )

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
        await _require_access(principal, admin, AdminPermission.VIEW)
        repository = admin.build_repository(db_session)

        instance = await repository.get_or_none(
            {admin.identity_field: _identity_value(identity)}
        )
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")

        columns = admin.column_names()
        readonly = set(admin.readonly_fields)
        model_columns = sa_inspect(admin.model).columns
        json_columns: set[str] = set()
        fields: list[tuple[str, Any]] = []
        for column in columns:
            # The hashed password is never shown; audit/timestamp columns
            # move to the dedicated audit panel below.
            if column == "hashed_password" or column in _AUDIT_FIELDS:
                continue
            raw_value = getattr(instance, column, None)
            col = model_columns.get(column)
            if col is not None and isinstance(col.type, JSON) and raw_value is not None:
                # Pretty-print JSON, mirroring the edit-form JSON widget.
                raw_value = json.dumps(
                    raw_value, indent=2, ensure_ascii=False, sort_keys=True
                )
                json_columns.add(column)
            fields.append((column, raw_value))

        async def _actor(uid: Any) -> str | None:
            if uid is None:
                return None
            actor = await auth_backend.load_principal(db_session, str(uid))
            return auth_backend.display_name(actor) if actor is not None else str(uid)

        audit = {
            "created_at": getattr(instance, "created_at", None),
            "updated_at": getattr(instance, "updated_at", None),
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
                        "changes": _format_audit_changes(entry.action, entry.changes),
                        "context": entry.context,
                    }
                )

        inlines = await _build_inlines(admin, instance, db_session)

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
                "readonly": readonly,
                "audit": audit,
                "history": history,
                "inlines": inlines,
                "nav_models": await _visible_nav(principal),
                "can_edit": admin.can_edit
                and await _allows(principal, admin, AdminPermission.EDIT),
                "can_delete": admin.can_delete
                and await _allows(principal, admin, AdminPermission.DELETE),
                "edit_url": f"{prefix}/m/{slug}/{identity}/edit",
                "delete_url": f"{prefix}/m/{slug}/{identity}/delete",
            },
        )

    async def _build_inlines(
        admin: AdminModel[Any],
        instance: Any,
        db_session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Build the related-child tables for a parent's detail view.

        For each configured :class:`Inline`, queries the child rows that
        reference ``instance`` via the inline's ``fk_field`` and packages
        them with the columns to show plus links to the child admin (when
        the child model is registered).

        Args:
            admin (AdminModel[Any]): The parent admin.
            instance (Any): The parent row.
            db_session (AsyncSession): The DB session.

        Returns:
            list[dict[str, Any]]: One entry per inline, ready for the
            template.
        """
        parent_id = getattr(instance, "id", None)
        blocks: list[dict[str, Any]] = []
        for inline in admin.inlines:
            child_admin = site.get(inline.get_slug())
            repository = (
                child_admin.build_repository(db_session)
                if child_admin is not None
                else BaseRepository(db_session, model=inline.model)
            )
            children = await repository.list({inline.fk_field: parent_id})
            if inline.list_display is not None:
                display = inline.list_display
            elif child_admin is not None:
                display = child_admin.resolved_list_display()
            else:
                display = list(sa_inspect(inline.model).columns.keys())
            child_slug = inline.get_slug()
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
            can_add = child_admin is not None and child_admin.can_create
            add_url = (
                f"{prefix}/m/{child_slug}/new?{inline.fk_field}={parent_id}"
                if can_add
                else None
            )
            blocks.append(
                {
                    "label": inline.get_label(),
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
        await _require_access(principal, admin, AdminPermission.VIEW)
        if field not in admin.autocomplete_fields:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "field is not an autocomplete field"
            )
        table = fk_fields(admin).get(field)
        referenced = site.get(table) if table else None
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
        await _require_access(principal, admin, AdminPermission.EDIT)
        repository = admin.build_repository(db_session)
        instance = await repository.get_or_none(
            {admin.identity_field: _identity_value(identity)}
        )
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
                "form_fields": await _form_fields(admin, db_session, instance=instance),
                "action_url": f"{prefix}/m/{slug}/{identity}/edit",
                "back_url": f"{prefix}/m/{slug}/{identity}",
                "form_error": None,
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
            re-rendered form (``400``) on validation/integrity errors.
        """
        admin = _require_admin(slug, lambda a: a.can_edit)
        principal = await _resolve_principal(request, db_session, session)
        await _require_access(principal, admin, AdminPermission.EDIT)
        _check_csrf(session, csrf_token)
        repository = admin.build_repository(db_session)
        instance = await repository.get_or_none(
            {admin.identity_field: _identity_value(identity)}
        )
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
        form = await request.form()
        data, errors = parse_submission(admin, form)
        errors.update(await _save_uploads(admin, form, data, creating=False))
        form_error: str | None = None
        if not errors:
            for key, value in data.items():
                setattr(instance, key, value)
            _stamp_audit(instance, auth_backend.principal_id(principal), creating=False)
            try:
                await repository.update(instance)
            except AppException as exc:
                form_error = exc.message
            else:
                return RedirectResponse(
                    url=f"{prefix}/m/{slug}/{identity}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
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
                    admin, db_session, submitted=form, errors=errors
                ),
                "action_url": f"{prefix}/m/{slug}/{identity}/edit",
                "back_url": f"{prefix}/m/{slug}/{identity}",
                "form_error": form_error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
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
        await _require_access(principal, admin, AdminPermission.DELETE)
        _check_csrf(session, csrf_token)
        repository = admin.build_repository(db_session)
        instance = await repository.get_or_none(
            {admin.identity_field: _identity_value(identity)}
        )
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
        await repository.delete(instance.id)
        return RedirectResponse(
            url=f"{prefix}/m/{slug}/",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return router


class _RowView:
    """Bound row used by the list template.

    Exposes ``identity`` (the value used in the detail URL) and a
    ``values`` mapping keyed by column name.
    """

    def __init__(self, instance: Any, columns: list[str], identity_field: str) -> None:
        """Initialize the view.

        Args:
            instance (Any): The ORM row.
            columns (list[str]): Columns to expose.
            identity_field (str): Column to read for the row identity.
        """
        self.instance: Any = instance
        self.identity: Any = getattr(instance, identity_field, None)
        self.pk: Any = getattr(instance, "id", None)
        self.values: dict[str, Any] = {
            col: getattr(instance, col, None) for col in columns
        }


def _resolve_filters_and_order(
    admin: Any,
    request: Request,
    q: str,
    sort: str,
    direction: str,
) -> tuple[dict[str, Any], dict[str, str], str | None, bool, str, bool]:
    """Build the repository filters + ordering from the request.

    Shared by the list view and the export endpoint so both honor the
    same search / filter / sort semantics.

    Args:
        admin (Any): The admin configuration.
        request (Request): The inbound request (for filter query params).
        q (str): Free-text search term.
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
            bound = _coerce_date_bound(raw_from, py, end=False)
            if bound is not None:
                filters[f"{field}__gte"] = bound
                active_filters[f"filter_{field}_from"] = raw_from or ""
            bound = _coerce_date_bound(raw_to, py, end=True)
            if bound is not None:
                filters[f"{field}__lte"] = bound
                active_filters[f"filter_{field}_to"] = raw_to or ""
            continue
        value = request.query_params.get(f"filter_{field}")
        if value:
            active_filters[f"filter_{field}"] = value
            filters[field] = (
                _identity_value(value) if kind == "fk" else _coerce_filter_value(value)
            )
    if q:
        for field in admin.search_fields:
            filters[field] = q

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


async def _save_uploads(
    admin: Any,
    form: Any,
    data: dict[str, Any],
    *,
    creating: bool,
) -> dict[str, str]:
    """Persist uploaded files and inject their storage keys into ``data``.

    For each configured upload field, a posted file is streamed to
    ``admin.upload_storage`` and the returned key is written to
    ``data[field]``. When no file is posted, the field is left untouched
    (so an edit keeps its current value); on create, a missing file for a
    non-nullable column is reported as a required-field error.

    Args:
        admin (Any): The admin configuration.
        form (Any): The parsed multipart form (Starlette ``FormData``).
        data (dict[str, Any]): The model kwargs being built (mutated).
        creating (bool): Whether this is a create (vs edit) submission.

    Returns:
        dict[str, str]: Field → error message for missing required files.
    """
    errors: dict[str, str] = {}
    if not admin.upload_fields:
        return errors
    columns = sa_inspect(admin.model).columns
    for name in admin.upload_fields:
        upload = form.get(name)
        filename = getattr(upload, "filename", None)
        if upload is not None and filename:

            async def _chunks(source: Any = upload) -> Any:
                while True:
                    chunk = await source.read(_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    yield chunk

            key = f"{admin.get_slug()}/{name}/{uuid4().hex}{Path(filename).suffix}"
            content_type = (
                getattr(upload, "content_type", None) or "application/octet-stream"
            )
            result = await admin.upload_storage.write_stream(
                key, _chunks(), content_type=content_type
            )
            data[name] = result.key
        elif creating:
            column = columns.get(name)
            if column is not None and not column.nullable:
                errors[name] = "This field is required."
    return errors


def _bulk_actions(admin: Any) -> list[tuple[str, str]]:
    """Return the bulk actions available for ``admin`` as ``(value, label)``.

    Activation toggles need ``can_edit`` (every model carries the
    ``is_active`` flag from ``BaseModel``); delete needs ``can_delete``.

    Args:
        admin (Any): The admin configuration.

    Returns:
        list[tuple[str, str]]: Action option pairs (empty when no
        mutation is permitted).
    """
    actions: list[tuple[str, str]] = []
    if admin.can_edit:
        actions.append(("activate", "Activate"))
        actions.append(("deactivate", "Deactivate"))
    if admin.can_delete:
        actions.append(("delete", "Delete"))
    # Custom actions are namespaced (``custom:<name>``) so they can never
    # collide with the built-in values above.
    for action in admin.custom_actions():
        actions.append((f"custom:{action.name}", action.label))
    return actions


_AUDIT_FIELDS = ("created_at", "updated_at", "created_by", "updated_by")

#: Cap on audit-history rows rendered in the detail view.
_AUDIT_HISTORY_LIMIT = 50

#: Cap on options returned by an autocomplete FK search.
_AUTOCOMPLETE_LIMIT = 20

#: Cap on child rows rendered per inline on the detail view.
_INLINE_ROW_LIMIT = 50


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


def _to_csv(columns: list[str], items: list[Any]) -> str:
    """Serialize ``items`` to a CSV document with a header row.

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
            [_export_value(getattr(instance, col, None)) for col in columns]
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


def _coerce_date_bound(raw: str | None, py: type | None, *, end: bool) -> Any:
    """Parse a ``YYYY-MM-DD`` filter bound into a date/datetime.

    Args:
        raw (str | None): The submitted ``date`` input value.
        py (type | None): The column's Python type (``date`` vs
            ``datetime``).
        end (bool): When the column is a ``datetime`` and this is the
            upper bound, snap to end-of-day so the whole day is included.

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
    if isinstance(py, type) and issubclass(py, datetime):
        clock = time(23, 59, 59) if end else time(0, 0, 0)
        return datetime.combine(parsed, clock, tzinfo=UTC)
    return parsed


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

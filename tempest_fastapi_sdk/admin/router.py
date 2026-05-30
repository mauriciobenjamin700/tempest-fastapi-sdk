"""HTML router wiring the admin site to FastAPI."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from tempest_fastapi_sdk.admin.auth import AdminAuthBackend, AdminAuthError
from tempest_fastapi_sdk.admin.session import (
    AdminSession,
    SessionStore,
    SignedCookieSessionStore,
)
from tempest_fastapi_sdk.admin.site import AdminSite

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


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
) -> APIRouter:
    """Build the FastAPI router that mounts the admin site.

    Routes attached:

    * ``GET  {prefix}/login`` — login form.
    * ``POST {prefix}/login`` — login submit.
    * ``POST {prefix}/logout`` — clear session + redirect.
    * ``GET  {prefix}/`` — dashboard listing registered admins.
    * ``GET  {prefix}/m/{slug}/`` — list view (paginated).
    * ``GET  {prefix}/m/{slug}/{identity}`` — detail view.
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

    Returns:
        APIRouter: A router ready to attach via ``app.include_router``.
    """
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
        return templates.TemplateResponse(
            request,
            template,
            context,
            status_code=status_code,
        )

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
        session = AdminSession(
            principal_id=auth_backend.principal_id(principal),
            issued_at=datetime.now(tz=UTC).timestamp(),
            csrf_token=secrets.token_urlsafe(32),
        )
        response = RedirectResponse(
            url=f"{prefix}/",
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
        return _render(
            request,
            "dashboard.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admins": site.iter_models(),
            },
        )

    @router.get("/m/{slug}/", name="admin_list")
    async def list_view(
        request: Request,
        slug: str,
        page: int = 1,
        q: str = "",
        db_session: AsyncSession = Depends(_db_session),
        session: AdminSession = Depends(_require_session),
    ) -> HTMLResponse:
        """Render the list view for a registered admin.

        Args:
            request (Request): The inbound request.
            slug (str): The admin slug from the URL.
            page (int): 1-indexed page number.
            q (str): Free-text search term.
            db_session (AsyncSession): The DB session.
            session (AdminSession): The validated session.

        Returns:
            HTMLResponse: The list template.
        """
        admin = site.get(slug)
        if admin is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown admin")
        principal = await _resolve_principal(request, db_session, session)

        repository = admin.build_repository(db_session)

        filters: dict[str, Any] = {}
        active_filters: dict[str, str] = {}
        for field in admin.list_filter:
            value = request.query_params.get(f"filter_{field}")
            if value:
                active_filters[field] = value
                filters[field] = _coerce_filter_value(value)
        if q:
            for field in admin.search_fields:
                filters[field] = q

        page = max(1, page)
        size = max(1, admin.page_size)
        result = await repository.paginate(
            filters=filters,
            page=page,
            page_size=size,
            order_by=admin.order_key,
            ascending=admin.order_ascending,
        )

        columns = admin.resolved_list_display()
        rows = [
            _RowView(instance, columns, admin.identity_field)
            for instance in result["items"]
        ]

        query_params: dict[str, str] = {}
        if q:
            query_params["q"] = q
        for field, value in active_filters.items():
            query_params[f"filter_{field}"] = value

        pagination = _Pagination(
            page=result["page"],
            size=result["size"],
            total=result["total"],
            query_params=query_params,
        )

        return _render(
            request,
            "list.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "columns": columns,
                "rows": rows,
                "pagination": pagination,
                "query": {"q": q},
                "filter_options": {
                    field: _filter_options(admin, field, active_filters.get(field))
                    for field in admin.list_filter
                },
            },
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
        repository = admin.build_repository(db_session)
        try:
            from uuid import UUID

            value: Any = UUID(identity)
        except (ValueError, TypeError):
            value = identity

        instance = await repository.get_or_none({admin.identity_field: value})
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")

        columns = admin.column_names()
        readonly = set(admin.readonly_fields)
        fields: list[tuple[str, Any]] = []
        for column in columns:
            if column == "hashed_password":
                continue
            raw_value = getattr(instance, column, None)
            fields.append((column, raw_value))
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
                "readonly": readonly,
            },
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
        self.values: dict[str, Any] = {
            col: getattr(instance, col, None) for col in columns
        }


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


def _filter_options(
    admin: Any,
    field: str,
    active: str | None,
) -> list[dict[str, Any]]:
    """Return the static filter-options for ``field``.

    The Phase 1 list view does not introspect distinct DB values; it
    only renders True/False options for boolean columns. Future
    phases can broaden this to enums and FK lookups.

    Args:
        admin (Any): The admin configuration.
        field (str): The filter field name.
        active (str | None): The currently selected value.

    Returns:
        list[dict[str, Any]]: Option dicts ready for the template.
    """
    column = getattr(admin.model, field, None)
    if column is None:
        return []
    python_type = getattr(getattr(column, "type", None), "python_type", None)
    if python_type is bool:
        return [
            {
                "value": "true",
                "label": "Yes",
                "selected": active == "true",
            },
            {
                "value": "false",
                "label": "No",
                "selected": active == "false",
            },
        ]
    return []


__all__: list[str] = [
    "make_admin_router",
]

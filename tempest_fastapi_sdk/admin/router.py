"""HTML router wiring the admin site to FastAPI."""

from __future__ import annotations

import csv
import io
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from tempest_fastapi_sdk.admin.auth import AdminAuthBackend, AdminAuthError
from tempest_fastapi_sdk.admin.config import AdminModel
from tempest_fastapi_sdk.admin.forms import (
    build_form_fields,
    fk_fields,
    fk_label,
    parse_submission,
)
from tempest_fastapi_sdk.admin.session import (
    AdminSession,
    SessionStore,
    SignedCookieSessionStore,
)
from tempest_fastapi_sdk.admin.site import AdminSite
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
) -> APIRouter:
    """Build the FastAPI router that mounts the admin site.

    Routes attached:

    * ``GET  {prefix}/login`` — login form.
    * ``POST {prefix}/login`` — login submit.
    * ``POST {prefix}/logout`` — clear session + redirect.
    * ``GET/POST {prefix}/mfa`` — TOTP challenge for MFA-enabled
      principals (between the password step and full access).
    * ``GET  {prefix}/`` — dashboard listing registered admins.
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
            referenced = site.get(table)
            if referenced is None:
                continue
            rows = await referenced.build_repository(db_session).list()
            options[field_name] = [
                (str(row.id), fk_label(referenced, row)) for row in rows[:FK_OPTION_CAP]
            ]
        return options

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
        return build_form_fields(
            admin,
            instance=instance,
            submitted=submitted,
            errors=errors,
            fk_options=fk_options,
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
                    else None,
                }
            )
        return _render(
            request,
            "dashboard.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admins": site.iter_models(),
                "models_view": models_view,
                "metrics": await _system_metrics() if show_metrics else None,
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

        repository = admin.build_repository(db_session)

        (
            filters,
            active_filters,
            order_key,
            ascending,
            active_sort,
            active_ascending,
        ) = _resolve_filters_and_order(admin, request, q, sort, dir)

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
        query_params: dict[str, str] = {}
        if q:
            query_params["q"] = q
        for field, value in active_filters.items():
            query_params[f"filter_{field}"] = value
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
                "sort_state": _sort_state(
                    admin, active_sort, active_ascending, sort_base
                ),
                "export_query": export_query,
                "can_create": admin.can_create,
                "new_url": f"{prefix}/m/{slug}/new",
                "bulk_actions": _bulk_actions(admin),
                "bulk_url": f"{prefix}/m/{slug}/bulk",
                "filter_options": {
                    field: _filter_options(admin, field, active_filters.get(field))
                    for field in admin.list_filter
                },
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
        await _resolve_principal(request, db_session, session)

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
        return _render(
            request,
            "form.html",
            {
                "user": principal,
                "session": session,
                "user_display": auth_backend.display_name(principal),
                "admin": admin,
                "mode": "create",
                "form_fields": await _form_fields(admin, db_session),
                "action_url": f"{prefix}/m/{slug}/new",
                "back_url": f"{prefix}/m/{slug}/",
                "form_error": None,
            },
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
        _check_csrf(session, csrf_token)
        form = await request.form()
        data, errors = parse_submission(admin, form)
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
        await _resolve_principal(request, db_session, session)
        _check_csrf(session, csrf_token)
        form = await request.form()
        ids = [_identity_value(str(raw)) for raw in form.getlist("ids")]
        if ids:
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
            url=f"{prefix}/m/{slug}/",
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
        repository = admin.build_repository(db_session)

        instance = await repository.get_or_none(
            {admin.identity_field: _identity_value(identity)}
        )
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")

        columns = admin.column_names()
        readonly = set(admin.readonly_fields)
        fields: list[tuple[str, Any]] = []
        for column in columns:
            # The hashed password is never shown; audit/timestamp columns
            # move to the dedicated audit panel below.
            if column == "hashed_password" or column in _AUDIT_FIELDS:
                continue
            raw_value = getattr(instance, column, None)
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
                "audit": audit,
                "can_edit": admin.can_edit,
                "can_delete": admin.can_delete,
                "edit_url": f"{prefix}/m/{slug}/{identity}/edit",
                "delete_url": f"{prefix}/m/{slug}/{identity}/delete",
            },
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
        _check_csrf(session, csrf_token)
        repository = admin.build_repository(db_session)
        instance = await repository.get_or_none(
            {admin.identity_field: _identity_value(identity)}
        )
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
        form = await request.form()
        data, errors = parse_submission(admin, form)
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
        await _resolve_principal(request, db_session, session)
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
        value = request.query_params.get(f"filter_{field}")
        if value:
            active_filters[field] = value
            filters[field] = _coerce_filter_value(value)
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
    return actions


_AUDIT_FIELDS = ("created_at", "updated_at", "created_by", "updated_by")


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

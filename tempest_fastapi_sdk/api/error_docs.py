"""Document the ``AppException`` set a route can raise in its OpenAPI schema.

The SDK serializes every failure into one envelope
(:class:`~tempest_fastapi_sdk.schemas.errors.ErrorResponseSchema`), but
that contract lives only in the backend: FastAPI documents the success
response and the ``422`` it generates itself, never the ``404`` / ``409``
/ ``403`` a handler raises. A frontend developer therefore discovers the
error codes at runtime, or by reading the service's Python.

This module closes that gap with two layers over the same data:

* :func:`error_responses` — pass the exception classes, get the dict
  FastAPI's ``responses=`` parameter expects.
* :func:`raises` + :class:`TempestAPIRouter` — declare the same list as a
  decorator next to the handler and let the router inject ``responses=``.

Both are explicit on purpose. The list of exceptions a route raises is
versioned in the diff, renaming a class is caught by mypy and the IDE,
and nothing depends on call-graph heuristics at import time. Use
``tempest openapi-errors --check`` (see
:mod:`tempest_fastapi_sdk.cli.openapi_errors`) to catch a declaration
that drifted from the code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import APIRouter

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.i18n import DEFAULT_LOCALE, MessageCatalog
from tempest_fastapi_sdk.schemas.errors import ErrorResponseSchema

RAISES_ATTRIBUTE: str = "__tempest_raises__"
"""Attribute :func:`raises` sets on the endpoint it decorates."""

EndpointT = TypeVar("EndpointT", bound=Callable[..., Any])


@dataclass(frozen=True, slots=True)
class RaisesSpec:
    """The error contract :func:`raises` attaches to an endpoint.

    Attributes:
        exceptions (tuple[type[AppException], ...]): The exception
            classes the handler's flow can raise, in declaration order.
        catalog (MessageCatalog | None): Catalog used to fill each
            example's ``detail``. ``None`` uses the class-level
            ``message``.
        locale (str): Locale resolved against ``catalog``.
    """

    exceptions: tuple[type[AppException], ...]
    catalog: MessageCatalog | None
    locale: str


def _summary_of(exception: type[AppException]) -> str:
    """Return a one-line summary for an exception class.

    Args:
        exception (type[AppException]): The class to summarize.

    Returns:
        str: The first line of the class docstring, or the class name
        when it has none. The docstring is the natural place for this
        text — the project convention already requires one.
    """
    doc = (exception.__doc__ or "").strip()
    if not doc:
        return exception.__name__
    return doc.splitlines()[0].strip()


def _detail_of(
    exception: type[AppException],
    catalog: MessageCatalog | None,
    locale: str,
) -> str:
    """Return the ``detail`` string to show in the OpenAPI example.

    Args:
        exception (type[AppException]): The class to resolve a message
            for.
        catalog (MessageCatalog | None): Catalog consulted first, under
            the class's ``message_key`` (falling back to its ``code``).
            ``None`` skips localization entirely.
        locale (str): Locale to resolve in.

    Returns:
        str: The localized message when the catalog knows the key,
        otherwise the class-level ``message``. A partial catalog
        therefore degrades to the literal message instead of blanking
        the example — the same fallback the runtime handler uses.
    """
    if catalog is not None:
        localized = catalog.resolve(exception.message_key or exception.code, locale)
        if localized is not None:
            return localized
    return exception.message


def error_responses(
    *exceptions: type[AppException],
    catalog: MessageCatalog | None = None,
    locale: str = DEFAULT_LOCALE,
    descriptions: Mapping[int, str] | None = None,
) -> dict[int | str, dict[str, Any]]:
    """Build the FastAPI ``responses=`` dict for a set of exceptions.

    Reads ``status_code`` / ``code`` / ``message`` / ``details_example``
    off each **class** — no instantiation, so no knowledge of each
    ``__init__`` signature is needed. This is why the SDK documents
    declaring ``code`` in the class body: an exception that only receives
    its ``code`` at the raise site reports the inherited generic value
    here, and :class:`~tempest_fastapi_sdk.InheritedErrorCodeWarning`
    flags that at class creation.

    OpenAPI allows exactly **one** response object per status code, so
    exceptions are **grouped by status** and their codes distinguished
    through the ``examples`` map — the shape Swagger UI and ReDoc render
    as a selector. Two ``404``s with different codes are therefore both
    visible, which a plain ``{404: {...}}`` entry could never express::

        {
            404: {
                "model": ErrorResponseSchema,
                "description": "SERVICE_NOT_FOUND | CATEGORY_NOT_FOUND",
                "content": {"application/json": {"examples": {
                    "SERVICE_NOT_FOUND": {"summary": ..., "value": {...}},
                    "CATEGORY_NOT_FOUND": {"summary": ..., "value": {...}},
                }}},
            },
        }

    Usage::

        from tempest_fastapi_sdk import error_responses

        @router.post(
            "/{service_id}/candidates",
            status_code=201,
            responses=error_responses(
                ServiceNotFoundException,
                CategoryNotFoundException,
                ServiceFullException,
            ),
        )
        async def apply_to_service(service_id: UUID) -> CandidateResponseSchema:
            ...

    Args:
        *exceptions (type[AppException]): The exception classes the route
            can raise. Order decides the order of the ``examples`` map;
            duplicates are collapsed.
        catalog (MessageCatalog | None): Catalog used to fill each
            example's ``detail``, keeping the text out of the route
            declaration. ``None`` (the default) uses the class-level
            ``message`` verbatim, so the generated spec never picks a
            language implicitly — pass
            :func:`tempest_fastapi_sdk.default_message_catalog` (merged
            with the project's codes) to localize.
        locale (str): Locale resolved against ``catalog``. Ignored when
            ``catalog`` is ``None``.
        descriptions (Mapping[int, str] | None): Per-status description
            overrides. Unlisted statuses keep the generated
            ``"CODE_A | CODE_B"`` summary.

    Returns:
        dict[int | str, dict[str, Any]]: A mapping ready to hand to
        ``responses=`` on any FastAPI route decorator. Empty when no
        exceptions are given, so an unconditional
        ``responses=error_responses(*maybe_empty)`` stays valid.

    Raises:
        TypeError: If an argument is not an :class:`AppException`
            subclass. Passing an *instance* is the likely mistake, and
            silently producing an empty schema would be worse than
            failing at import.
    """
    for exception in exceptions:
        if not (isinstance(exception, type) and issubclass(exception, AppException)):
            raise TypeError(
                f"error_responses() takes AppException subclasses, got "
                f"{exception!r}. Pass the class itself, not an instance."
            )

    by_status: dict[int, list[type[AppException]]] = {}
    for exception in exceptions:
        group = by_status.setdefault(exception.status_code, [])
        if exception not in group:
            group.append(exception)

    responses: dict[int | str, dict[str, Any]] = {}
    for status_code, group in by_status.items():
        examples: dict[str, dict[str, Any]] = {}
        codes: list[str] = []
        for exception in group:
            code = exception.code
            if code not in codes:
                codes.append(code)
            # Two classes may legitimately share a code (a base and a
            # narrowing subclass). Keep both examples by qualifying the
            # duplicate key, since an examples map cannot hold two
            # entries under one name.
            key = code if code not in examples else f"{code} ({exception.__name__})"
            examples[key] = {
                "summary": _summary_of(exception),
                "value": {
                    "detail": _detail_of(exception, catalog, locale),
                    "code": code,
                    "details": dict(exception.details_example),
                },
            }
        responses[status_code] = {
            "model": ErrorResponseSchema,
            "description": (descriptions or {}).get(status_code) or " | ".join(codes),
            "content": {"application/json": {"examples": examples}},
        }
    return responses


def raises(
    *exceptions: type[AppException],
    catalog: MessageCatalog | None = None,
    locale: str = DEFAULT_LOCALE,
) -> Callable[[EndpointT], EndpointT]:
    """Declare the exceptions a route handler's flow can raise.

    Same information as :func:`error_responses`, expressed next to the
    handler instead of inside the route decorator's argument list::

        @router.post("/{service_id}/candidates", status_code=201)
        @raises(
            ServiceNotFoundException,
            ServiceFullException,
            CandidateAlreadyExistsException,
        )
        async def apply_to_service(service_id: UUID) -> CandidateResponseSchema:
            ...

    The decorator only tags the function — it returns the **same**
    object, never a wrapper, so FastAPI still sees the original
    signature, annotations and dependencies. Turning the tag into
    ``responses=`` is :class:`TempestAPIRouter`'s job; on a plain
    ``APIRouter`` the tag is inert, and
    ``responses=error_responses(...)`` is the way to go.

    Note the decorator order: ``@raises`` must sit **below**
    ``@router.post`` so it runs first and the route decorator receives an
    already-tagged function.

    Args:
        *exceptions (type[AppException]): The exception classes the
            handler's flow can raise.
        catalog (MessageCatalog | None): Forwarded to
            :func:`error_responses`.
        locale (str): Forwarded to :func:`error_responses`.

    Returns:
        Callable[[EndpointT], EndpointT]: A decorator that attaches a
        :class:`RaisesSpec` to the endpoint and returns it unchanged.
    """

    def decorator(endpoint: EndpointT) -> EndpointT:
        """Attach the spec to ``endpoint`` and return it untouched.

        Args:
            endpoint (EndpointT): The route handler being decorated.

        Returns:
            EndpointT: The very same callable, now carrying the spec.
        """
        setattr(
            endpoint,
            RAISES_ATTRIBUTE,
            RaisesSpec(exceptions=exceptions, catalog=catalog, locale=locale),
        )
        return endpoint

    return decorator


def declared_raises(endpoint: Callable[..., Any]) -> RaisesSpec | None:
    """Return the :class:`RaisesSpec` attached to an endpoint, if any.

    Args:
        endpoint (Callable[..., Any]): A route handler, decorated with
            :func:`raises` or not.

    Returns:
        RaisesSpec | None: The attached spec, or ``None`` when the
        handler carries none.
    """
    spec = getattr(endpoint, RAISES_ATTRIBUTE, None)
    return spec if isinstance(spec, RaisesSpec) else None


class TempestAPIRouter(APIRouter):
    """``APIRouter`` that turns :func:`raises` tags into ``responses=``.

    A drop-in replacement for ``fastapi.APIRouter``: every argument,
    method and behavior is inherited, and the only addition is that
    :meth:`add_api_route` expands a handler's :func:`raises` tag into the
    ``responses`` mapping :func:`error_responses` would have produced.

    ::

        from tempest_fastapi_sdk import TempestAPIRouter, raises

        router: TempestAPIRouter = TempestAPIRouter(prefix="/api/jobs")

        @router.post("/{service_id}/candidates", status_code=201)
        @raises(ServiceNotFoundException, ServiceFullException)
        async def apply_to_service(service_id: UUID) -> CandidateResponseSchema:
            ...

    Injection happens **before** ``APIRoute`` is constructed, so the
    declared model reaches ``components.schemas`` as a proper ``$ref``
    (mutating ``route.responses`` afterwards would only carry the
    description — FastAPI builds the response fields in the route's
    constructor). ``app.include_router(router)`` preserves the result:
    FastAPI copies each route's ``responses`` onto the parent.

    An explicit ``responses=`` on the route decorator wins per status
    code, so a hand-written entry can always override the generated one.
    """

    def add_api_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        **kwargs: Any,
    ) -> None:
        """Register a route, expanding the endpoint's :func:`raises` tag.

        Args:
            path (str): The route path, relative to the router prefix.
            endpoint (Callable[..., Any]): The handler.
            **kwargs (Any): Every other ``APIRouter.add_api_route``
                argument, forwarded untouched. ``responses`` is merged
                on top of the generated mapping so an explicit entry
                takes precedence for its status code.
        """
        spec = declared_raises(endpoint)
        if spec is not None and spec.exceptions:
            generated = error_responses(
                *spec.exceptions,
                catalog=spec.catalog,
                locale=spec.locale,
            )
            generated.update(kwargs.get("responses") or {})
            kwargs["responses"] = generated
        super().add_api_route(path, endpoint, **kwargs)


__all__: list[str] = [
    "RAISES_ATTRIBUTE",
    "RaisesSpec",
    "TempestAPIRouter",
    "declared_raises",
    "error_responses",
    "raises",
]

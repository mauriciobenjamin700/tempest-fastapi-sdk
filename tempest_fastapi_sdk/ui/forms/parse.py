"""Parse a submitted form back into the Pydantic schema that generated it.

The POST half of the round trip. :func:`parse_form` reads the request
body, coerces the shapes HTML cannot express (an unchecked checkbox sends
nothing at all; an empty text control sends ``""`` where the schema wants
``None``; a multiple select sends repeated keys), validates with the
schema, and returns a :class:`FormResult` that is either a valid model or
the messages needed to re-render the form with the reader's input intact.

```python
@app.post("/signup")
async def signup(request: Request) -> Response:
    result = await parse_form(SignupSchema, request)
    if not result.ok:
        return html_response(
            form_for(
                SignupSchema,
                action="/signup",
                values=result.values,
                errors=result.errors,
            ),
            title="Cadastro",
            status_code=422,
        )
    ...
```

A route that would rather not see the ``Request`` takes the result as a
dependency, built by :func:`form_dependency`:

```python
@app.post("/signup")
async def signup(
    result: Annotated[FormResult[SignupSchema], Depends(form_dependency(SignupSchema))],
) -> Response:
    ...
```
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import UnionType
from typing import Any, Generic, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile
from starlette.requests import Request

from tempest_fastapi_sdk.ui.forms.introspect import _ui_overrides, _upload_annotation

T = TypeVar("T", bound=BaseModel)

_TRUTHY = frozenset({"true", "on", "yes", "1"})


@dataclass(frozen=True, slots=True)
class FormResult(Generic[T]):
    """The outcome of parsing a submitted form.

    Attributes:
        value (T | None): The validated model, or ``None`` when
            validation failed.
        errors (dict[str, list[str]]): Validation messages per field
            name, ready to hand back to
            :func:`~tempest_fastapi_sdk.ui.forms.form_for`.
        form_errors (list[str]): Messages that belong to no single field
            (model-level validators).
        values (dict[str, Any]): The raw submitted values, so a
            re-rendered form keeps what the reader typed.
    """

    value: T | None = None
    errors: dict[str, list[str]] = field(default_factory=dict)
    form_errors: list[str] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether the submission validated.

        Returns:
            bool: ``True`` when a model was produced and no message was
            collected.
        """
        return self.value is not None and not self.errors and not self.form_errors

    def unwrap(self) -> T:
        """Return the validated model, or fail loudly.

        Returns:
            T: The validated model.

        Raises:
            ValueError: When the submission did not validate. Check
                :attr:`ok` first and re-render the form instead of
                calling this on an invalid result.
        """
        if self.value is None:
            raise ValueError(
                "FormResult holds no value; the submission failed validation "
                f"({sum(len(items) for items in self.errors.values())} field "
                f"error(s), {len(self.form_errors)} form error(s)).",
            )
        return self.value


def _is_sequence_field(annotation: Any) -> bool:
    """Whether a field annotation holds many values.

    Args:
        annotation (Any): The declared field annotation.

    Returns:
        bool: ``True`` for ``list`` / ``set`` / ``tuple`` fields,
        including their optional forms.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        return any(_is_sequence_field(arg) for arg in get_args(annotation))
    return origin in {list, set, tuple, frozenset}


def _is_bool_field(annotation: Any) -> bool:
    """Whether a field annotation is a boolean.

    Args:
        annotation (Any): The declared field annotation.

    Returns:
        bool: ``True`` for ``bool`` and ``bool | None``.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        return any(_is_bool_field(arg) for arg in get_args(annotation))
    return annotation is bool


def _accepts_none(annotation: Any) -> bool:
    """Whether a field annotation accepts ``None``.

    Args:
        annotation (Any): The declared field annotation.

    Returns:
        bool: ``True`` when ``None`` is a member of the annotation.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        return any(arg is type(None) for arg in get_args(annotation))
    return annotation is type(None)


def _is_file_field(field_info: Any) -> bool:
    """Whether a schema field receives uploaded files.

    Args:
        field_info (Any): The Pydantic ``FieldInfo``.

    Returns:
        bool: ``True`` when the annotation is an ``UploadFile`` (bare,
        optional or in a list) or the ``ui`` block forces
        ``{"control": "file"}`` — the same rule
        :func:`~tempest_fastapi_sdk.ui.forms.fields_for` renders by.
    """
    if _upload_annotation(field_info.annotation)[0]:
        return True
    return _ui_overrides(field_info).get("control") == "file"


def _uploads(body: Any, name: str) -> list[UploadFile]:
    """Collect the files a body carries under one name.

    An ``<input type="file">`` left empty still submits a part, with an
    empty filename and no content; that part is dropped, so an empty
    control reads as an absent key.

    Args:
        body (Any): The parsed form body.
        name (str): The field name.

    Returns:
        list[UploadFile]: The uploads that carry a filename, in order.
    """
    items = body.getlist(name) if hasattr(body, "getlist") else [body.get(name)]
    return [item for item in items if isinstance(item, UploadFile) and item.filename]


def _lines(raw: Any) -> list[str]:
    """Split a textarea value into one item per non-blank line.

    Args:
        raw (Any): The submitted text.

    Returns:
        list[str]: The trimmed lines that carry content.
    """
    return [line.strip() for line in str(raw).splitlines() if line.strip()]


def _coerce(
    raw: Any,
    *,
    annotation: Any,
    submitted: bool,
) -> Any:
    """Turn one submitted value into what the schema expects.

    A collection field reads every repeated key (what a multiple select
    submits). When exactly one value came back and it spans lines, it is
    split per line instead — the shape a ``textarea`` produces for a list
    the introspection could not offer as a select.

    Args:
        raw (Any): The submitted value (string, list of strings, or
            ``None`` when the key was absent).
        annotation (Any): The field's declared annotation.
        submitted (bool): Whether the key was present in the body at
            all — the only way to tell an unchecked checkbox from a
            missing field.

    Returns:
        Any: The coerced value, or ``None`` for an empty optional.
    """
    if _is_bool_field(annotation):
        if not submitted:
            return False
        text = raw[0] if isinstance(raw, list) else raw
        return str(text).strip().lower() in _TRUTHY
    if _is_sequence_field(annotation):
        if isinstance(raw, list):
            items = [item for item in raw if item != ""]
            if len(items) == 1 and "\n" in str(items[0]):
                return _lines(items[0])
            return items
        if raw in (None, ""):
            return []
        return _lines(raw)
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    if raw == "" and _accepts_none(annotation):
        return None
    return raw


async def parse_form(
    schema: type[T],
    request: Any,
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    extra: Mapping[str, Any] | None = None,
    error_message: Callable[[Mapping[str, Any]], str] | None = None,
) -> FormResult[T]:
    """Read, coerce and validate a submitted form against a schema.

    Only the schema's own fields are read from the body; anything else
    the browser sent (CSRF tokens, HTMX bookkeeping) is ignored, so a
    stray input cannot smuggle a value into the model.

    A key the body does not carry at all is left out of the payload
    rather than sent as ``None``, so the schema's own default applies
    and a required field reports ``Field required`` against itself.
    Booleans are the exception: an unchecked checkbox submits nothing,
    so an absent boolean key means ``False``.

    A file field (``UploadFile``, ``list[UploadFile]``, or a field whose
    ``ui`` block forces ``{"control": "file"}``) receives the uploaded
    ``UploadFile`` objects themselves. An empty file control counts as an
    absent key, and a file never goes back into
    :attr:`FormResult.values` — a browser cannot pre-fill a file input,
    so the re-rendered control comes back empty.

    Args:
        schema (type[T]): The schema to validate against — the same one
            the form was generated from.
        request (Any): The incoming ``fastapi.Request`` (or any object
            exposing an awaitable ``form()``).
        include (Sequence[str]): When non-empty, read only these fields
            from the body.
        exclude (Sequence[str]): Field names never read from the body.
            Use it for values the server owns (an owner id, a status)
            and pass them through ``extra``.
        extra (Mapping[str, Any] | None): Server-side values merged in
            after the body, overriding anything submitted under the same
            name.
        error_message (Callable[[Mapping[str, Any]], str] | None): Maps
            a raw Pydantic error dict to the message shown to the
            reader. Defaults to Pydantic's own ``msg`` — pass a callable
            to translate or reword.

    Returns:
        FormResult[T]: The validated model, or the messages and raw
        values needed to re-render the form.

    Example:
        ```python
        from fastapi import Request
        from pydantic import BaseModel

        from tempest_fastapi_sdk.ui.forms import parse_form


        class SignupSchema(BaseModel):
            email: str


        async def handle(request: Request) -> str:
            result = await parse_form(SignupSchema, request)
            return result.value.email if result.ok else "invalid"
        ```
    """
    body = await request.form()
    model_fields: dict[str, Any] = dict(schema.model_fields)
    names = [
        name
        for name in (include or model_fields)
        if name in model_fields and name not in set(exclude)
    ]

    raw_values: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    for name in names:
        annotation = model_fields[name].annotation
        if _is_file_field(model_fields[name]):
            raw_values[name] = ""
            files = _uploads(body, name)
            if files:
                payload[name] = files if _is_sequence_field(annotation) else files[0]
            continue
        submitted = name in body
        if _is_sequence_field(annotation) and hasattr(body, "getlist"):
            raw: Any = body.getlist(name)
        else:
            raw = body.get(name)
        if isinstance(raw, UploadFile):
            continue
        raw_values[name] = raw if raw is not None else ""
        if not submitted and not _is_bool_field(annotation):
            continue
        payload[name] = _coerce(raw, annotation=annotation, submitted=submitted)

    payload.update(dict(extra or {}))

    try:
        value = schema.model_validate(payload)
    except ValidationError as exc:
        field_errors: dict[str, list[str]] = {}
        form_errors: list[str] = []
        for error in exc.errors():
            message = error_message(error) if error_message else str(error["msg"])
            location = error.get("loc") or ()
            key = str(location[0]) if location else ""
            if key and key in model_fields:
                field_errors.setdefault(key, []).append(message)
            else:
                form_errors.append(message)
        return FormResult(
            value=None,
            errors=field_errors,
            form_errors=form_errors,
            values=raw_values,
        )

    return FormResult(value=value, values=raw_values)


def form_dependency(
    schema: type[T],
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    extra: Mapping[str, Any] | None = None,
    error_message: Callable[[Mapping[str, Any]], str] | None = None,
) -> Callable[[Request], Awaitable[FormResult[T]]]:
    """Build a FastAPI dependency that parses a form into a schema.

    The dependency is :func:`parse_form` bound to ``schema`` and the
    given options, taking the ``Request`` itself so the route does not
    have to. It is a framework hook, not a wrapper: FastAPI injects the
    ``Request`` into the returned callable and hands its result to the
    route.

    Args:
        schema (type[T]): The schema to validate against.
        include (Sequence[str]): Read only these fields from the body.
        exclude (Sequence[str]): Field names never read from the body.
        extra (Mapping[str, Any] | None): Fixed server-side values merged
            in after the body. A value that depends on the request (the
            current user) is not known when the dependency is built:
            exclude the field, parse, and set it in the route — or call
            :func:`parse_form` directly.
        error_message (Callable[[Mapping[str, Any]], str] | None): Maps
            a raw Pydantic error dict to the message shown to the reader.

    Returns:
        Callable[[Request], Awaitable[FormResult[T]]]: The dependency,
        ready for ``Depends(...)``.

    Example:
        ```python
        from typing import Annotated

        from fastapi import Depends, FastAPI
        from pydantic import BaseModel

        from tempest_fastapi_sdk.ui.forms import FormResult, form_dependency

        app: FastAPI = FastAPI()


        class SignupSchema(BaseModel):
            email: str


        SignupForm = Annotated[
            FormResult[SignupSchema],
            Depends(form_dependency(SignupSchema)),
        ]


        @app.post("/signup")
        async def signup(result: SignupForm) -> dict[str, bool]:
            return {"ok": result.ok}
        ```
    """
    fixed_extra: dict[str, Any] = dict(extra or {})

    async def dependency(request: Request) -> FormResult[T]:
        """Parse the request body into the bound schema.

        Args:
            request (Request): The incoming request, injected by FastAPI.

        Returns:
            FormResult[T]: The outcome of :func:`parse_form`.
        """
        return await parse_form(
            schema,
            request,
            include=include,
            exclude=exclude,
            extra=fixed_extra,
            error_message=error_message,
        )

    return dependency


__all__: list[str] = ["FormResult", "form_dependency", "parse_form"]

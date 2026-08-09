"""Render :class:`~tempest_fastapi_sdk.openapi.ir.ClientIR` into ``client.py``.

The generated class is the second half of the issue's ask — "um serviço
montando com requisições HTTP e Schemas elaborados". It takes an
:class:`~tempest_fastapi_sdk.utils.http_client.HTTPClient` by injection
rather than constructing one, so the caller owns the timeout, retry policy,
circuit breaker and auth headers, and tests can pass an
``httpx.MockTransport`` without touching the network.
"""

from __future__ import annotations

import re

from tempest_fastapi_sdk.openapi.ir import ClientIR, OperationIR, ParameterIR
from tempest_fastapi_sdk.openapi.source import (
    MAX_LINE as _MAX_LINE,
)
from tempest_fastapi_sdk.openapi.source import (
    string_literal as _string_literal,
)
from tempest_fastapi_sdk.openapi.source import (
    unsupported_comment as _unsupported_comment,
)
from tempest_fastapi_sdk.openapi.source import (
    wrap as _wrap,
)


def _signature_lines(operation: OperationIR) -> list[str]:
    """Render an operation's ``async def`` signature.

    Args:
        operation (OperationIR): The operation to render.

    Returns:
        list[str]: Source lines. Every argument is keyword-or-positional
        for path parameters and keyword-only past them, so adding a query
        parameter to the specification later cannot silently reorder an
        existing caller's positional arguments.
    """
    arguments: list[str] = ["self"]
    for parameter in operation.path_parameters:
        arguments.append(f"{parameter.name}: {parameter.annotation}")

    keyword_only: list[str] = []
    if operation.body_annotation is not None:
        default = "" if operation.body_required else " = None"
        annotation = operation.body_annotation
        if not operation.body_required and not annotation.endswith("| None"):
            annotation = f"{annotation} | None"
        keyword_only.append(f"body: {annotation}{default}")
    for parameter in operation.query_parameters:
        default = "" if parameter.required else " = None"
        keyword_only.append(f"{parameter.name}: {parameter.annotation}{default}")

    if keyword_only:
        arguments.append("*")
        arguments.extend(keyword_only)

    return_type = operation.response_annotation or "None"
    single = f"    async def {operation.name}({', '.join(arguments)}) -> {return_type}:"
    if arguments == ["self"] and len(single) <= _MAX_LINE:
        return [single]

    lines = [f"    async def {operation.name}("]
    for argument in arguments:
        lines.append(f"        {argument}," if argument != "*" else "        *,")
    lines.append(f"    ) -> {return_type}:")
    return lines


def _docstring_lines(operation: OperationIR) -> list[str]:
    r"""Render an operation's Google-style docstring.

    Args:
        operation (OperationIR): The operation to render.

    Returns:
        list[str]: Source lines for the docstring, always multi-line so
        the ``Args:`` / ``Returns:`` / ``Raises:`` sections have a home.
        Prefixed ``r\"\"\"`` when any of the specification's prose carries a
        backslash — ``\#`` is not a Python escape, so the plain form raised
        ``W605`` and, from 3.12, a ``SyntaxWarning``. It survived review
        because the generator's own ``ruff --fix`` pass adds the prefix
        afterwards, which hides the defect from everyone except a caller
        passing ``--no-format``.

    The prose is rendered twice rather than patched after the fact: the
    ``r`` costs a column on the line already closest to the budget, so a
    summary that wrapped to exactly 88 characters would overrun.
    """
    lines = _render_docstring(operation, '"""')
    if any("\\" in line for line in lines):
        return _render_docstring(operation, 'r"""')
    return lines


def _render_docstring(operation: OperationIR, opening: str) -> list[str]:
    """Render the docstring with a given opening delimiter.

    Args:
        operation (OperationIR): The operation to render.
        opening (str): ``'\"\"\"'`` or ``'r\"\"\"'``.

    Returns:
        list[str]: Source lines, wrapped against the budget the delimiter
        leaves.
    """
    lines = _wrap(operation.summary, "        ", opening, hanging=False)
    if operation.description:
        lines.append("")
        for paragraph in operation.description.split("\n\n"):
            collapsed = " ".join(paragraph.split())
            if collapsed:
                lines.extend(_wrap(collapsed, "        ", hanging=False))
                lines.append("")
        while lines and not lines[-1]:
            lines.pop()

    has_arguments = bool(operation.parameters) or operation.body_annotation is not None
    if has_arguments:
        lines.extend(["", "        Args:"])
        for parameter in operation.path_parameters:
            lines.extend(_parameter_doc(parameter))
        if operation.body_annotation is not None:
            requirement = "" if operation.body_required else " Optional."
            lines.extend(
                _wrap(
                    f"The request body.{requirement}",
                    "            ",
                    f"body ({operation.body_annotation}): ",
                )
            )
        for parameter in operation.query_parameters:
            lines.extend(_parameter_doc(parameter))

    lines.extend(["", "        Returns:"])
    if operation.response_annotation is None:
        lines.extend(
            _wrap(
                f"Nothing — the operation answers {operation.success_status} "
                f"with no JSON body.",
                "            ",
                "None: ",
            )
        )
    else:
        lines.extend(
            _wrap(
                f"The {operation.success_status} response body, validated.",
                "            ",
                f"{operation.response_annotation}: ",
            )
        )

    lines.extend(["", "        Raises:"])
    lines.extend(
        _wrap(
            "For any non-2xx response. The specification documents "
            + (
                ", ".join(status for status, _ in operation.error_statuses)
                if operation.error_statuses
                else "no error status"
            )
            + ".",
            "            ",
            "httpx.HTTPStatusError: ",
        )
    )
    lines.append('        """')
    return lines


def _operation_markers(operation: OperationIR) -> list[str]:
    """Render the ``# openapi: unsupported`` comments above one method.

    Args:
        operation (OperationIR): The operation about to be emitted.

    Returns:
        list[str]: Comment lines, empty when nothing was lost. The
        operation's own gaps come first (a non-JSON body, an unmodellable
        response), then each parameter's, so a reader sees every reason the
        signature differs from the specification before reading it.

    The parameter comments sit here rather than inside the signature: a
    comment between two arguments of a call is legal Python but
    ``ruff format`` moves it, so the generated file would stop being
    format-stable.
    """
    notes: list[str] = list(operation.unsupported)
    for parameter in operation.parameters:
        notes.extend(note for note in parameter.unsupported if note not in notes)
    return _unsupported_comment(tuple(notes), "    ")


def _parameter_doc(parameter: ParameterIR) -> list[str]:
    """Render one ``Args:`` entry.

    Args:
        parameter (ParameterIR): The parameter to document.

    Returns:
        list[str]: Wrapped source lines.
    """
    description = parameter.description or f"The {parameter.wire_name} value."
    if not parameter.required:
        description = f"{description} Omitted from the query when None."
    return _wrap(
        description,
        "            ",
        f"{parameter.name} ({parameter.annotation}): ",
    )


def _body_lines(operation: OperationIR) -> list[str]:
    """Render the statements that build the request keyword arguments.

    Args:
        operation (OperationIR): The operation to render.

    Returns:
        list[str]: Source lines for the method body, up to and including
        the ``request`` call and the return.

    The path and every wire name go through :func:`string_literal` rather
    than being interpolated between bare quotes: both come from the
    specification, and one carrying a quote or a backslash would emit a
    module that does not parse. The ``f`` prefix is added only when the
    template still holds a placeholder — after the parser's repair pass,
    every remaining brace pair is one.
    """
    lines: list[str] = []

    rendered_path = _as_fstring(operation)
    if "{" in rendered_path:
        lines.append(f"        path = f{_string_literal(rendered_path)}")
    else:
        lines.append(f"        path = {_string_literal(operation.path)}")

    if operation.query_parameters:
        lines.append("        params: dict[str, Any] = {}")
        for parameter in operation.query_parameters:
            key = _string_literal(parameter.wire_name)
            if parameter.required:
                lines.append(
                    f"        params[{key}] = _param({parameter.name})",
                )
            else:
                lines.extend(
                    [
                        f"        if {parameter.name} is not None:",
                        f"            params[{key}] = _param({parameter.name})",
                    ]
                )

    call_arguments = ['"' + operation.http_method.upper() + '"', "path"]
    if operation.query_parameters:
        call_arguments.append("params=params")
    if operation.body_annotation is not None:
        if operation.body_required:
            lines.append("        payload = _dump(body)")
        else:
            lines.append("        payload = None if body is None else _dump(body)")
        call_arguments.append("json=payload")

    lines.append("        response = await self._client.request(")
    for argument in call_arguments:
        lines.append(f"            {argument},")
    lines.append("        )")
    lines.append("        response.raise_for_status()")

    if operation.response_annotation is None:
        lines.append("        return None")
    else:
        lines.append(
            f"        return _validate("
            f"{operation.response_annotation}, response.json()"
            f")"
        )
    return lines


def _as_fstring(operation: OperationIR) -> str:
    """Rewrite a path template so Python interpolates the argument names.

    OpenAPI writes ``/users/{userId}``; the generated argument is
    ``user_id``. The braces are rewritten to the Python name so the
    f-string resolves.

    Args:
        operation (OperationIR): The operation whose path is rewritten.

    Returns:
        str: The path with each ``{wireName}`` replaced by ``{python_name}``.
    """
    path = operation.path
    for parameter in operation.path_parameters:
        path = path.replace(f"{{{parameter.wire_name}}}", f"{{{parameter.name}}}")
    return path


def _annotation_text(client: ClientIR) -> str:
    """Join every annotation the module renders, for import detection.

    Args:
        client (ClientIR): The parsed client.

    Returns:
        str: Newline-joined annotations from bodies, responses and
        parameters.
    """
    parts: list[str] = []
    for operation in client.operations:
        parts.extend(
            annotation
            for annotation in (
                operation.body_annotation,
                operation.response_annotation,
                *(parameter.annotation for parameter in operation.parameters),
            )
            if annotation
        )
    return "\n".join(parts)


def _import_block(client: ClientIR) -> list[str]:
    """Render the generated module's isort-clean import block.

    Two things make this more than a fixed header. A path parameter typed
    ``format: uuid`` puts ``UUID`` in a method signature, so the client
    needs the same type detection the schemas module does — without it the
    generated file fails ``ruff check`` with an undefined name. And the
    groups have to come out in isort's order with modules sorted, so the
    file is lint-clean even under ``--no-format`` or with ruff absent.

    ``date``, ``time``, ``datetime`` and ``Enum`` are imported
    unconditionally because the emitted ``_param`` helper references all
    four in its body, not only when an annotation mentions them.

    Args:
        client (ClientIR): The parsed client.

    Returns:
        list[str]: Source lines, ending with a blank line.
    """
    rendered = _annotation_text(client)
    stdlib: dict[str, list[str]] = {
        "datetime": ["date", "datetime", "time"],
        "enum": ["Enum"],
        "typing": ["Any"],
    }
    if _uses(rendered, "Decimal"):
        stdlib["decimal"] = ["Decimal"]
    if _uses(rendered, "UUID"):
        stdlib["uuid"] = ["UUID"]

    pydantic_names = ["BaseModel", "TypeAdapter"]
    if _uses(rendered, "EmailStr"):
        pydantic_names.append("EmailStr")
    third_party: dict[str, list[str]] = {
        "pydantic": pydantic_names,
        "tempest_fastapi_sdk": ["HTTPClient"],
    }

    lines: list[str] = ["from __future__ import annotations", ""]
    for group in (stdlib, third_party):
        for module in sorted(group):
            lines.append(f"from {module} import {', '.join(sorted(group[module]))}")
        lines.append("")
    return lines


def _uses(rendered: str, name: str) -> bool:
    """Report whether a type name appears in the rendered annotations.

    Args:
        rendered (str): Every annotation, newline-joined.
        name (str): The type name to look for.

    Returns:
        bool: ``True`` when the name appears as a whole word.
    """
    return re.search(rf"\b{re.escape(name)}\b", rendered) is not None


def rerun_hint(source: str, name: str, *, schemas_only: bool = False) -> list[str]:
    """Render the "rerun to refresh" command as short lines.

    A specification path or URL is often long enough that inlining the
    whole command would push the generated docstring past the line budget
    and make the generated file fail ``ruff check``. Three forms, in order
    of preference:

    1. The whole command on one line, when it fits.
    2. Shell backslash continuations, when the source alone fits on an
       indented line — still copy-pasteable.
    3. The command with a ``<spec>`` placeholder, when the source is
       longer than a line can hold. Dropping it beats emitting a file that
       fails the project's own lint gate, and the source is the caller's
       own input — it is in their shell history or CI config.

    Args:
        source (str): The specification URL or path.
        name (str): The integration name.
        schemas_only (bool): Whether ``--schemas-only`` was used.

    Returns:
        list[str]: Indented command lines, none exceeding the line budget.
    """
    tail = f"--name {name}" + (" --schemas-only" if schemas_only else "")
    single = f"    tempest openapi-client {source} {tail}"
    if len(single) <= _MAX_LINE:
        return [single]
    continued = f"        {source} \\"
    if len(continued) <= _MAX_LINE:
        return [
            "    tempest openapi-client \\",
            continued,
            f"        {tail}",
        ]
    return [
        f"    tempest openapi-client <spec> {tail}",
        "",
        "where <spec> is the specification this package was generated from.",
    ]


def emit_client(client: ClientIR, *, schemas_module: str = "schemas") -> str:
    """Render the generated ``client.py`` module.

    Args:
        client (ClientIR): The parsed client.
        schemas_module (str): Module name to import the schemas from,
            relative to the generated package.

    Returns:
        str: The complete module source, ending in a newline.

    Notes:
        One blank line separates the import block from
        ``DEFAULT_BASE_URL``, not two: isort wants a single separator
        before a plain assignment, and reserves two for a ``def`` or
        ``class``.
    """
    schema_names = sorted(_referenced_schema_names(client))
    lines: list[str] = [
        f'"""Typed HTTP client generated from the {client.title} OpenAPI spec.',
        "",
        "Do not edit by hand — rerun `tempest openapi-client` to refresh.",
        "",
        "The client wraps an injected ``HTTPClient``, so the caller keeps",
        "control of the base URL, timeout, retry policy, circuit breaker and",
        "auth headers. Pass an ``httpx.MockTransport`` through the client in",
        "tests to exercise these methods without network access.",
        '"""',
        "",
        *_import_block(client),
    ]
    if schema_names:
        imported = ", ".join(schema_names)
        import_line = f"from .{schemas_module} import {imported}"
        if len(import_line) <= _MAX_LINE:
            lines.append(import_line)
        else:
            lines.append(f"from .{schemas_module} import (")
            lines.extend(f"    {name}," for name in schema_names)
            lines.append(")")
        lines.append("")

    lines.extend(
        [
            f'DEFAULT_BASE_URL: str = "{client.base_url}"',
            '"""``servers[0].url`` from the specification."""',
            "",
            "",
            "def _dump(payload: Any) -> Any:",
            '    """Serialize a request body to JSON-ready data.',
            "",
            "    Args:",
            "        payload (Any): A generated schema instance, or already-plain",
            "            data when the specification typed the body loosely.",
            "",
            "    Returns:",
            '        Any: ``model_dump(by_alias=True, mode="json")`` for a Pydantic',
            "        model — the wire spelling the third party expects — and the",
            "        value untouched for anything else.",
            '    """',
            "    if isinstance(payload, BaseModel):",
            '        return payload.model_dump(by_alias=True, mode="json", '
            "exclude_none=True)",
            "    return payload",
            "",
            "",
            "def _validate(annotation: Any, data: Any) -> Any:",
            '    """Validate a response body against the generated annotation.',
            "",
            "    Args:",
            "        annotation (Any): The response type — a generated model, a",
            "            ``list[Model]``, or a primitive.",
            "        data (Any): The decoded JSON body.",
            "",
            "    Returns:",
            "        Any: The validated value. ``TypeAdapter`` is used rather than",
            "        ``Model.model_validate`` so container and union annotations",
            "        work through the same call site.",
            '    """',
            "    return TypeAdapter(annotation).validate_python(data)",
            "",
            "",
            "def _param(value: Any) -> Any:",
            '    """Normalize a query-parameter value for the wire.',
            "",
            "    Args:",
            "        value (Any): The argument as the caller passed it.",
            "",
            "    Returns:",
            "        Any: ``Enum`` members become their ``value`` and dates their",
            "        ISO-8601 form; lists and tuples are normalized element-wise.",
            "        Without this, an ``Enum`` would reach the query string through",
            "        ``str()`` — which for the SDK's ``BaseStrEnum`` renders",
            '        ``"Class.MEMBER"``, not the value the third party expects.',
            '    """',
            "    if isinstance(value, Enum):",
            "        return value.value",
            "    if isinstance(value, (list, tuple)):",
            "        return [_param(item) for item in value]",
            "    if isinstance(value, (datetime, date, time)):",
            "        return value.isoformat()",
            "    return value",
            "",
            "",
            f"class {client.class_name}:",
            f'    """Client for {client.title}'
            + (f" (version {client.version})." if client.version else ".")
            + '"""',
            "",
            "    def __init__(self, client: HTTPClient) -> None:",
            '        """Initialize the client.',
            "",
            "        Args:",
            "            client (HTTPClient): The transport to issue requests",
            "                through. Build it with",
            "                ``HTTPClient(base_url=DEFAULT_BASE_URL)`` to target",
            "                the server the specification declares, and attach",
            "                credentials via its ``default_headers``.",
            '        """',
            "        self._client: HTTPClient = client",
            "",
        ]
    )

    if not client.operations:
        lines.append("    # The specification declares no operations.")
    for operation in client.operations:
        lines.extend(_operation_markers(operation))
        lines.extend(_signature_lines(operation))
        lines.extend(_docstring_lines(operation))
        lines.extend(_body_lines(operation))
        lines.append("")

    lines.extend(
        [
            "",
            "__all__: list[str] = [",
            '    "DEFAULT_BASE_URL",',
            f'    "{client.class_name}",',
            "]",
        ]
    )

    return _normalize(lines)


def _referenced_schema_names(client: ClientIR) -> set[str]:
    """Collect the generated class names the client module must import.

    Args:
        client (ClientIR): The parsed client.

    Returns:
        set[str]: Class names appearing in any annotation the module
        renders — request body, response, **and** parameters. Missing the
        parameter annotations would emit a module that references an
        un-imported enum. Primitive and container syntax is filtered out
        by requiring the identifier to start with an upper-case letter.
    """
    names: set[str] = set()
    for operation in client.operations:
        annotations = [
            operation.body_annotation,
            operation.response_annotation,
            *(parameter.annotation for parameter in operation.parameters),
        ]
        for annotation in annotations:
            if not annotation:
                continue
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", annotation):
                if token[:1].isupper() and token not in {
                    "Any",
                    "Annotated",
                    "Decimal",
                    "EmailStr",
                    "Field",
                    "None",
                    "UUID",
                }:
                    names.add(token)
    return names


def _normalize(lines: list[str]) -> str:
    """Collapse runs of blank lines and terminate the file with a newline.

    Args:
        lines (list[str]): The emitted source lines.

    Returns:
        str: The module source, with at most two consecutive blank lines.
    """
    output: list[str] = []
    blanks = 0
    for line in lines:
        if line.strip():
            blanks = 0
            output.append(line.rstrip())
            continue
        blanks += 1
        if blanks <= 2:
            output.append("")
    while output and not output[-1]:
        output.pop()
    return "\n".join(output) + "\n"


__all__: list[str] = [
    "emit_client",
    "rerun_hint",
]

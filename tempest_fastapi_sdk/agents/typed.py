"""Tools whose arguments are a Pydantic model, not a hand-written schema.

Writing JSON-schema by hand next to a Python handler means two
descriptions of the same thing, drifting apart from the first edit — the
schema says ``city`` is required, the handler reads ``arguments["town"]``,
and nothing catches it until a model calls the tool. The `@tool` decorator
removes the duplicate: declare the arguments once as a Pydantic model, and
the schema the model sees is generated from it.

    >>> class WeatherArgs(BaseSchema):
    ...     city: str = Field(description="City to look up.")
    ...
    >>> @tool("get_weather", "Get the current weather for a city.")
    ... async def get_weather(args: WeatherArgs, ctx: AgentContext) -> str:
    ...     return f"{args.city}: 22 degrees"

The handler receives a **validated instance**, so `args.city` is typed and
`mypy` checks it. And because validation now happens before the handler
runs, a model that invents an argument gets Pydantic's own error message
back as an observation — precise enough to correct from — instead of the
handler failing on a `KeyError` halfway through its work.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar, get_type_hints

from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.agents.schemas import ToolResult
from tempest_fastapi_sdk.agents.tools import (
    AgentContext,
    AgentTool,
    AgentToolError,
    ToolReturn,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

ArgsT = TypeVar("ArgsT", bound=BaseModel)

#: A handler taking a validated args model plus the run context.
TypedHandler = Callable[[Any, AgentContext], Awaitable[ToolReturn]]


def schema_of(model: type[BaseModel]) -> dict[str, Any]:
    """Return the JSON-schema a tool-calling model should see for ``model``.

    Pydantic emits ``$defs`` + ``$ref`` for nested models. Most local
    tool-calling models handle a flat object far more reliably, so nested
    definitions are inlined and the bookkeeping keys dropped. What is left
    is a plain ``{"type": "object", "properties": …}`` — the shape every
    backend documents.

    Args:
        model (type[BaseModel]): The arguments model.

    Returns:
        dict[str, Any]: The inlined schema.
    """
    raw = model.model_json_schema()
    defs: dict[str, Any] = raw.pop("$defs", {})

    def inline(node: Any) -> Any:
        """Replace every ``$ref`` with the definition it points at."""
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs.get(ref.split("/")[-1], {})
                merged = {**inline(target)}
                for key, value in node.items():
                    if key != "$ref":
                        merged[key] = inline(value)
                return merged
            return {key: inline(value) for key, value in node.items()}
        if isinstance(node, list):
            return [inline(item) for item in node]
        return node

    schema: dict[str, Any] = inline(raw)
    schema.pop("title", None)
    schema.setdefault("type", "object")
    return schema


def _args_model(handler: Callable[..., Any]) -> type[BaseModel]:
    """Return the Pydantic model annotated on the handler's first parameter.

    Args:
        handler (Callable[..., Any]): The decorated function.

    Returns:
        type[BaseModel]: The arguments model.

    Raises:
        TypeError: When the first parameter is missing or is not annotated
            with a Pydantic model. Raised at **decoration** time, so a
            mistyped tool fails at import rather than the first time a
            model happens to call it.
    """
    signature = inspect.signature(handler)
    parameters = list(signature.parameters.values())
    if len(parameters) < 2:
        raise TypeError(
            f"{handler.__name__} must take (args, context); "
            f"got {len(parameters)} parameter(s)",
        )
    try:
        hints = get_type_hints(handler)
    except Exception as exc:  # pragma: no cover - unresolvable annotations
        raise TypeError(
            f"cannot resolve type hints on {handler.__name__}: {exc}",
        ) from exc
    annotation = hints.get(parameters[0].name)
    if annotation is None or not (
        isinstance(annotation, type) and issubclass(annotation, BaseModel)
    ):
        raise TypeError(
            f"{handler.__name__}'s first parameter must be annotated with a "
            "Pydantic model describing the tool arguments",
        )
    return annotation


def tool(
    name: str,
    description: str,
) -> Callable[[TypedHandler], AgentTool]:
    """Build an :class:`~tempest_fastapi_sdk.agents.AgentTool` from a handler.

    The arguments model is read from the handler's first parameter
    annotation, and its JSON-schema is what the language model sees. The
    handler is called with a **validated instance** of that model.

    Example:

        >>> class SearchArgs(BaseSchema):
        ...     query: str = Field(description="What to search for.")
        ...     limit: int = Field(default=5, ge=1, le=50)
        ...
        >>> @tool("search", "Search the knowledge base.")
        ... async def search(args: SearchArgs, ctx: AgentContext) -> str:
        ...     return await backend.find(args.query, limit=args.limit)

    Constraints declared on the model (``ge``, ``le``, ``max_length``,
    enums) are enforced too, so a model asking for 500 results is corrected
    before your code sees it.

    Args:
        name (str): The function name the language model calls.
        description (str): What the tool does, written for the model. This
            is the only text steering tool choice, so it earns more care
            than the implementation.

    Returns:
        Callable[[TypedHandler], AgentTool]: The decorator.

    Raises:
        TypeError: At decoration time, when the handler does not take
            ``(args, context)`` with a Pydantic-annotated first parameter.
    """

    def decorate(handler: TypedHandler) -> AgentTool:
        """Wrap ``handler`` into a tool that validates before calling it."""
        model = _args_model(handler)

        async def invoke(
            arguments: dict[str, Any],
            context: AgentContext,
        ) -> ToolReturn:
            """Validate the raw arguments, then run the real handler."""
            try:
                parsed = model.model_validate(arguments)
            except ValidationError as exc:
                raise AgentToolError(
                    f"invalid arguments for {name}: {_explain(exc)}",
                ) from exc
            return await handler(parsed, context)

        return AgentTool(
            name=name,
            description=description,
            parameters=schema_of(model),
            handler=invoke,
        )

    return decorate


def _explain(error: ValidationError) -> str:
    """Render a validation error as one line a language model can act on.

    Pydantic's default rendering is multi-line and carries a URL, which
    wastes context and reads as noise to a model. This keeps the part that
    identifies the fix: which field, and what was wrong with it.

    Args:
        error (ValidationError): The failure.

    Returns:
        str: ``"field: message"`` entries joined by ``"; "``.
    """
    parts: list[str] = []
    for item in error.errors():
        location = ".".join(str(piece) for piece in item.get("loc", ())) or "(root)"
        parts.append(f"{location}: {item.get('msg', 'invalid')}")
    return "; ".join(parts)


def typed_tool(
    name: str,
    description: str,
    model: type[ArgsT],
    handler: Callable[[ArgsT, AgentContext], Coroutine[Any, Any, ToolReturn]],
) -> AgentTool:
    """Build a typed tool without the decorator.

    Same behaviour as :func:`tool`, for the cases where the handler is a
    lambda, a bound method, or comes from somewhere the decorator cannot
    reach — the args model is passed explicitly instead of being read from
    an annotation.

    Args:
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.
        model (type[ArgsT]): The arguments model.
        handler (Callable[[ArgsT, AgentContext], Coroutine[Any, Any, ToolReturn]]):
            Async implementation taking the validated model.

    Returns:
        AgentTool: The tool.
    """

    async def invoke(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> ToolReturn:
        """Validate the raw arguments, then run the real handler."""
        try:
            parsed = model.model_validate(arguments)
        except ValidationError as exc:
            raise AgentToolError(
                f"invalid arguments for {name}: {_explain(exc)}",
            ) from exc
        return await handler(parsed, context)

    return AgentTool(
        name=name,
        description=description,
        parameters=schema_of(model),
        handler=invoke,
    )


__all__: list[str] = [
    "ToolResult",
    "TypedHandler",
    "schema_of",
    "tool",
    "typed_tool",
]

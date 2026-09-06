"""Turn an AsyncAPI document into a generated package on disk.

Writes the three files the OpenAPI generator writes, for the same reason:
payloads in one module, the client in another, and a barrel that re-exports
both so a consumer imports from the package rather than reaching inside it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from tempest_fastapi_sdk.asyncapi.emit_stream import emit_stream
from tempest_fastapi_sdk.asyncapi.loader import load_asyncapi_spec
from tempest_fastapi_sdk.asyncapi.parse import as_spec_ir, parse_asyncapi
from tempest_fastapi_sdk.openapi.emit_schemas import emit_schemas


@dataclass(frozen=True, slots=True)
class StreamGenerationResult:
    """What one generation run produced.

    Attributes:
        written (tuple[Path, ...]): Files written, in write order.
        message_count (int): Frames the document declared.
        schema_count (int): Payload classes emitted.
        unsupported (tuple[str, ...]): Notes collected while parsing.
    """

    written: tuple[Path, ...]
    message_count: int
    schema_count: int
    unsupported: tuple[str, ...] = field(default=())


def _format(paths: list[Path]) -> bool:
    """Run ruff over the generated files, when it is available.

    Args:
        paths (list[Path]): Files to format.

    Returns:
        bool: Whether ruff ran.

    The generator writes source that already respects the line budget, so
    this normalizes quoting and import order rather than rescuing anything
    — and the drift test compares checked-in bytes against a run with
    formatting on, so skipping it silently would make the two disagree.
    """
    try:
        for arguments in (
            ["ruff", "check", "--fix-only", "--quiet", *map(str, paths)],
            ["ruff", "format", "--quiet", *map(str, paths)],
        ):
            subprocess.run(arguments, check=False, capture_output=True)
    except FileNotFoundError:
        return False
    return True


def generate_stream(
    source: str,
    *,
    out: Path,
    name: str,
    headers: Mapping[str, str] | None = None,
    run_format: bool = True,
) -> StreamGenerationResult:
    """Generate a WebSocket client package from an AsyncAPI document.

    Args:
        source (str): ``http(s)://`` URL, or a filesystem path.
        out (Path): Directory the package is written to. Created when
            missing.
        name (str): Base name for the generated client class.
        headers (Mapping[str, str] | None): Extra request headers, for a
            document behind authentication.
        run_format (bool): Whether to run ruff over the output.

    Returns:
        StreamGenerationResult: Files written and what they carry.

    Raises:
        SpecError: When the document cannot be loaded, is not AsyncAPI 3.x,
            does not declare whose point of view its actions record, or
            declares no frames.
    """
    document = load_asyncapi_spec(source, headers=headers)
    parsed = parse_asyncapi(document, client_name=name)

    out.mkdir(parents=True, exist_ok=True)
    schemas_path = out / "schemas.py"
    stream_path = out / "stream.py"
    init_path = out / "__init__.py"

    schemas_path.write_text(
        emit_schemas(as_spec_ir(parsed), title=parsed.stream.title),
        encoding="utf-8",
    )
    stream_path.write_text(
        emit_stream(parsed.stream, schemas_module="schemas"),
        encoding="utf-8",
    )
    init_path.write_text(_emit_init(parsed), encoding="utf-8")

    written = [schemas_path, stream_path, init_path]
    if run_format:
        _format(written)

    return StreamGenerationResult(
        written=tuple(written),
        message_count=len(parsed.stream.messages),
        schema_count=len(parsed.schemas),
        unsupported=parsed.unsupported,
    )


def _emit_init(parsed: object) -> str:
    """Render the package barrel.

    Args:
        parsed (object): The parsed document, as an ``AsyncApiIR``.

    Returns:
        str: The module source, ending in a newline.

    Every symbol is re-exported in both forms — ``from x import Y as Y``
    and ``__all__`` — because consumers run type-checkers at varying
    strictness and basedpyright flags a plain import here as private usage.
    """
    from tempest_fastapi_sdk.asyncapi.ir import AsyncApiIR

    assert isinstance(parsed, AsyncApiIR)
    stream = parsed.stream
    exported = sorted(
        {
            stream.class_name,
            f"{stream.class_name}FrameError",
            f"{stream.class_name}ClientFrame",
            f"{stream.class_name}ServerFrame",
            *(["DEFAULT_URL"] if stream.default_url else []),
        }
    )
    payloads = sorted({schema.name for schema in parsed.schemas})

    lines = [
        f'"""Generated client for {stream.title}.',
        "",
        "Do not edit by hand — rerun the generator to refresh.",
        '"""',
        "",
    ]
    for name in exported:
        lines.append(f"from .stream import {name} as {name}")
    for name in payloads:
        lines.append(f"from .schemas import {name} as {name}")
    lines.extend(["", "__all__: list[str] = ["])
    for name in sorted({*exported, *payloads}):
        lines.append(f'    "{name}",')
    lines.append("]")
    return "\n".join(lines) + "\n"


__all__: list[str] = [
    "StreamGenerationResult",
    "generate_stream",
]

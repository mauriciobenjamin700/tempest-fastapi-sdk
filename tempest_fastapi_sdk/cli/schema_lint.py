"""``tempest lint-schemas`` — report schema fields without documentation.

The layer rules read source with :mod:`ast`; this one has to import the
project, because a field's ``title``, ``description`` and ``examples``
only exist once Pydantic built the model. It imports the schemas package
(``src.schemas`` or ``app.schemas`` by default), runs
:func:`tempest_fastapi_sdk.testing.schema_metadata_findings` over it and
prints one line per field. The same check runs inside the suite as
:func:`tempest_fastapi_sdk.testing.assert_schema_metadata`, which is how
the project ``tempest new`` writes enforces it on every ``tempest check``.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path
from typing import TextIO

SCHEMAS_MODULES: tuple[str, ...] = ("src.schemas", "app.schemas")
"""Modules tried, in order, when no module is named."""


def default_schemas_module(root: Path) -> str:
    """Pick the schemas package of the project at ``root``.

    Args:
        root (Path): The project root.

    Returns:
        str: ``app.schemas`` when the project has ``app/`` and no
        ``src/``, ``src.schemas`` otherwise.
    """
    if (root / "app").is_dir() and not (root / "src").is_dir():
        return SCHEMAS_MODULES[1]
    return SCHEMAS_MODULES[0]


def run_lint_schemas(
    module: str | None = None,
    *,
    root: str = ".",
    stream: TextIO | None = None,
) -> int:
    """Import the schemas package and print its undocumented fields.

    Args:
        module (str | None): Dotted name of the package to check.
            ``None`` picks :func:`default_schemas_module`.
        root (str): The project root, put first on ``sys.path`` so the
            import finds the project.
        stream (TextIO | None): Where the report goes. ``None`` means
            standard output.

    Returns:
        int: ``0`` when every field is documented, ``1`` when one is not,
        ``2`` when the package does not import.
    """
    from tempest_fastapi_sdk.testing.schemas import schema_metadata_findings

    output = stream or sys.stdout
    project = Path(root).resolve()
    name = module or default_schemas_module(project)
    sys.path.insert(0, str(project))
    try:
        target = importlib.import_module(name)
    except Exception:
        output.write(f"cannot import {name}:\n{traceback.format_exc()}")
        return 2
    finally:
        sys.path.remove(str(project))
    findings = schema_metadata_findings(target)
    for finding in findings:
        output.write(finding.render() + "\n")
    if findings:
        output.write(f"{len(findings)} schema field(s) without documentation.\n")
        return 1
    output.write(f"Schema metadata: {name} is fully documented.\n")
    return 0


__all__: list[str] = ["SCHEMAS_MODULES", "default_schemas_module", "run_lint_schemas"]

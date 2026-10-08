"""Command-line interface for the SDK (optional ``[cli]`` extra).

Exposes :data:`tempest_fastapi_sdk.cli.main.app` as the entry point
behind the ``tempest`` console script. Sub-commands cover project
scaffolding (``tempest new``) and the quality gates the SDK expects
(``tempest lint`` / ``format`` / ``fmt-check`` / ``type`` / ``test``
/ ``check``).

Every name below is re-exported **lazily**: the CLI needs the ``[cli]``
extra (``tempest-cli``, ``typer``, ``click``), while helpers in this
package such as :mod:`tempest_fastapi_sdk.cli.src_layers` are imported by
the base package. Importing this package is therefore always safe;
reading one of the names without the extra raises an ``ImportError``
carrying the install line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tempest_fastapi_sdk.cli.config import (
        DEFAULT_TYPING_STRICTNESS as DEFAULT_TYPING_STRICTNESS,
    )
    from tempest_fastapi_sdk.cli.config import (
        TempestConfig as TempestConfig,
    )
    from tempest_fastapi_sdk.cli.config import (
        TypingStrictness as TypingStrictness,
    )
    from tempest_fastapi_sdk.cli.config import (
        load_tempest_config as load_tempest_config,
    )
    from tempest_fastapi_sdk.cli.main import app as app

_LAZY_EXPORTS: dict[str, str] = {
    "DEFAULT_TYPING_STRICTNESS": "tempest_fastapi_sdk.cli.config",
    "TempestConfig": "tempest_fastapi_sdk.cli.config",
    "TypingStrictness": "tempest_fastapi_sdk.cli.config",
    "app": "tempest_fastapi_sdk.cli.main",
    "load_tempest_config": "tempest_fastapi_sdk.cli.config",
}
"""Public name → module that defines it, resolved on first access."""


def __getattr__(name: str) -> Any:
    """Lazily resolve the CLI's public names.

    Args:
        name (str): The attribute requested.

    Returns:
        Any: The symbol from the module :data:`_LAZY_EXPORTS` maps it to.

    Raises:
        ImportError: When the ``[cli]`` extra is not installed.
        AttributeError: For any other attribute name.
    """
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    from tempest_fastapi_sdk.cli.entrypoint import raise_for_missing_cli_extra

    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise_for_missing_cli_extra(exc)
        raise
    return getattr(module, name)


__all__: list[str] = [
    "DEFAULT_TYPING_STRICTNESS",
    "TempestConfig",
    "TypingStrictness",
    "app",
    "load_tempest_config",
]

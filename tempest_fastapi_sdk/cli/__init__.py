"""Command-line interface for the SDK.

Exposes :data:`tempest_fastapi_sdk.cli.main.app` as the entry point
behind the ``tempest`` console script. Sub-commands cover project
scaffolding (``tempest new``) and the quality gates the SDK expects
(``tempest lint`` / ``format`` / ``fmt-check`` / ``type`` / ``test``
/ ``check``).
"""

from tempest_fastapi_sdk.cli.config import (
    DEFAULT_TYPING_STRICTNESS,
    TempestConfig,
    TypingStrictness,
    load_tempest_config,
)
from tempest_fastapi_sdk.cli.main import app

__all__: list[str] = [
    "DEFAULT_TYPING_STRICTNESS",
    "TempestConfig",
    "TypingStrictness",
    "app",
    "load_tempest_config",
]

"""System-check framework — Django-style startup / config validation.

Importing this package registers the built-in settings checks on
:data:`default_registry`, so ``run_system_checks(settings)`` and the
``tempest check-config`` CLI command work out of the box. Add your own
with the :func:`check` decorator.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit form plus
``__all__`` so every type-checker accepts
``from tempest_fastapi_sdk.checks import run_system_checks``.
"""

# Importing builtins registers the built-in checks as a side effect.
from tempest_fastapi_sdk.checks import builtins as builtins
from tempest_fastapi_sdk.checks.messages import CheckLevel as CheckLevel
from tempest_fastapi_sdk.checks.messages import CheckMessage as CheckMessage
from tempest_fastapi_sdk.checks.messages import critical as critical
from tempest_fastapi_sdk.checks.messages import debug as debug
from tempest_fastapi_sdk.checks.messages import error as error
from tempest_fastapi_sdk.checks.messages import info as info
from tempest_fastapi_sdk.checks.messages import warning as warning
from tempest_fastapi_sdk.checks.registry import CheckFn as CheckFn
from tempest_fastapi_sdk.checks.registry import CheckRegistry as CheckRegistry
from tempest_fastapi_sdk.checks.registry import SystemCheckError as SystemCheckError
from tempest_fastapi_sdk.checks.registry import check as check
from tempest_fastapi_sdk.checks.registry import default_registry as default_registry
from tempest_fastapi_sdk.checks.registry import register_check as register_check
from tempest_fastapi_sdk.checks.registry import run_checks as run_checks
from tempest_fastapi_sdk.checks.registry import run_system_checks as run_system_checks

__all__: list[str] = [
    "CheckFn",
    "CheckLevel",
    "CheckMessage",
    "CheckRegistry",
    "SystemCheckError",
    "builtins",
    "check",
    "critical",
    "debug",
    "default_registry",
    "error",
    "info",
    "register_check",
    "run_checks",
    "run_system_checks",
    "warning",
]

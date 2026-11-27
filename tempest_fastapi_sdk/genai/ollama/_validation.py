"""Validation of the ``think`` and ``truncate`` request fields."""

from __future__ import annotations

from tempest_fastapi_sdk.genai.ollama._constants import _THINK_LEVELS, ThinkLevel


def _check_think(think: ThinkLevel | None) -> None:
    """Reject a ``think`` value the daemon does not define.

    Args:
        think (ThinkLevel | None): The value to check.

    Raises:
        ValueError: When ``think`` is neither ``None``, a bool nor one of
            ``"low"``, ``"medium"``, ``"high"``.
    """
    if think is None or isinstance(think, bool) or think in _THINK_LEVELS:
        return
    raise ValueError(
        f"think must be None, a bool or one of {sorted(_THINK_LEVELS)}, got {think!r}",
    )


def _check_truncate(truncate: bool | None) -> None:
    """Reject a ``truncate`` value that is not a bool.

    Args:
        truncate (bool | None): The value to check.

    Raises:
        ValueError: When ``truncate`` is neither ``None`` nor a bool.
    """
    if truncate is None or isinstance(truncate, bool):
        return
    raise ValueError(f"truncate must be None or a bool, got {truncate!r}")

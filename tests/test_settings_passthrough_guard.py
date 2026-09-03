"""Guard: every transport knob is reachable through ``from_settings``.

``TaskQueue.from_settings`` derives some of the arguments it hands the
transport factory. A derived value that is baked into the call while
``**options`` is splatted alongside it makes the knob **unreachable**:
the caller who passes it gets

    TypeError: TaskQueue.redis() got multiple values for keyword
    argument 'results'

which is how ``results=False`` — the one thing a cron-only service
wants, since no caller waits on a return value — could not be expressed
through the factory at all.

The knobs are read off the signatures, so a knob added to a transport
factory and forgotten in ``from_settings`` fails here instead of in a
consumer's tracebook.

Blind spot: this sees keyword collisions, not a keyword the callee
absorbs into a ``**kwargs`` of its own. That is how
``result_ex_time=3600`` passed construction and only raised inside
``connect()`` — ``RedisStreamBroker`` swallows unknown keys as
connection kwargs. ``tests/tasks/test_queue_from_settings.py`` covers
that the two result knobs reach the backend.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, Final

import pytest

from tempest_fastapi_sdk.tasks import TaskQueue

REDIS_URL = "redis://localhost:6379/0"

SAMPLES: Final[Mapping[str, Any]] = {
    "results": False,
    "result_ttl_seconds": 60,
    "result_prefix": "guard",
    "resources": (),
}
"""A value to try for each knob.

Deliberately a required mapping rather than a value derived from the
annotation: a knob added without a sample fails
:meth:`TestEveryKnobIsCovered.test_every_knob_has_a_sample` instead of
being silently skipped.
"""


class Settings:
    """The settings shape ``from_settings`` reads.

    Attributes:
        TASKIQ_BROKER_URL (str): Broker URL.
        TASKIQ_RESULT_BACKEND_URL (str | None): Result backend URL.
        TASKIQ_STORE_RESULTS (bool): Whether results are stored.
        TASKIQ_RESULT_TTL_SECONDS (int): Seconds a result survives.
    """

    TASKIQ_BROKER_URL: str = REDIS_URL
    TASKIQ_RESULT_BACKEND_URL: str | None = None
    TASKIQ_STORE_RESULTS: bool = True
    TASKIQ_RESULT_TTL_SECONDS: int = 86_400


def keyword_knobs(method: Any) -> list[str]:
    """Return the keyword-only parameter names of ``method``.

    Args:
        method (Any): The callable to inspect.

    Returns:
        list[str]: The keyword-only names, in declaration order.
    """
    return [
        name
        for name, parameter in inspect.signature(method).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    ]


def collides(factory: Any, name: str, value: Any) -> bool:
    """Whether passing ``name`` to ``factory`` hits a keyword collision.

    Args:
        factory (Any): The callable to probe, taking a settings object.
        name (str): The keyword to pass.
        value (Any): The value to pass.

    Returns:
        bool: ``True`` when the call raises the "multiple values for
        keyword argument" ``TypeError``. Any other failure is not this
        defect and reports ``False``.
    """
    try:
        factory(Settings(), **{name: value})
    except TypeError as exc:
        return "multiple values for keyword argument" in str(exc)
    except Exception:
        return False
    return False


class TestEveryKnobIsCovered:
    def test_every_knob_has_a_sample(self) -> None:
        knobs = set(keyword_knobs(TaskQueue.redis)) | set(
            keyword_knobs(TaskQueue.rabbitmq),
        )
        assert knobs <= set(SAMPLES), knobs - set(SAMPLES)

    @pytest.mark.parametrize("name", keyword_knobs(TaskQueue.redis))
    def test_no_redis_knob_collides(self, name: str) -> None:
        assert collides(TaskQueue.from_settings, name, SAMPLES[name]) is False

    @pytest.mark.parametrize("name", keyword_knobs(TaskQueue.rabbitmq))
    def test_no_rabbitmq_knob_collides(self, name: str) -> None:
        assert collides(TaskQueue.from_settings, name, SAMPLES[name]) is False


class TestTheGuardFires:
    """The detector has to catch the shape that shipped in 0.283.1."""

    def test_a_baked_keyword_beside_a_splat_is_reported(self) -> None:
        def transport(url: str, *, results: bool | str = True, **options: Any) -> str:
            """Stand in for ``TaskQueue.redis``.

            Args:
                url (str): Broker URL.
                results (bool | str): Where results go.
                **options (Any): Forwarded to the broker.

            Returns:
                str: A marker, unused.
            """
            return f"{url}:{results}:{options}"

        def shipped(settings: Settings, **options: Any) -> str:
            """The 0.283.1 shape: the keyword baked in beside the splat.

            Args:
                settings (Settings): The settings to read.
                **options (Any): Forwarded to the transport.

            Returns:
                str: Whatever the transport returned.
            """
            return transport(
                settings.TASKIQ_BROKER_URL,
                results=settings.TASKIQ_RESULT_BACKEND_URL or True,
                **options,
            )

        assert collides(shipped, "results", False) is True

    def test_the_fixed_shape_is_not_reported(self) -> None:
        def transport(url: str, *, results: bool | str = True, **options: Any) -> str:
            """Stand in for ``TaskQueue.redis``.

            Args:
                url (str): Broker URL.
                results (bool | str): Where results go.
                **options (Any): Forwarded to the broker.

            Returns:
                str: A marker, unused.
            """
            return f"{url}:{results}:{options}"

        def fixed(
            settings: Settings,
            *,
            results: bool | str | None = None,
            **options: Any,
        ) -> str:
            """The shape that lets an explicit value win.

            Args:
                settings (Settings): The settings to read.
                results (bool | str | None): Override, or ``None`` to
                    derive.
                **options (Any): Forwarded to the transport.

            Returns:
                str: Whatever the transport returned.
            """
            derived: bool | str = settings.TASKIQ_RESULT_BACKEND_URL or True
            return transport(
                settings.TASKIQ_BROKER_URL,
                results=derived if results is None else results,
                **options,
            )

        assert collides(fixed, "results", False) is False

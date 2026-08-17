"""Retrying a bad generation only helps if something changes.

Greedy decoding is deterministic, so a second attempt at temperature 0
reproduces the first one token for token — the retry loop spends a call and
gets the same unusable output back. Raising the temperature per attempt is
what gives sampling a chance to leave the bad state; the first attempt stays
greedy because it is the most reliable single shot.

The backend here is a scripted stub, so the tests assert on which
temperature each attempt carried, not on what a model happened to do.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import GenerationConfig, StructuredFormatError
from tempest_fastapi_sdk.genai.structured import generate_structured_list


class _Item(BaseModel):
    """One extracted item.

    Attributes:
        title (str): The item's title.
    """

    title: str


class _ScriptedBackend:
    """Returns a canned completion per call, recording the config it got.

    Attributes:
        temperatures (list[float | None]): The temperature of each attempt,
            in order.
        calls (int): How many generations were requested.
    """

    def __init__(self, outputs: list[str]) -> None:
        """Build the stub.

        Args:
            outputs (list[str]): One completion per attempt. The last one
                repeats if the loop asks for more than were scripted.
        """
        self._outputs = outputs
        self.temperatures: list[float | None] = []
        self.calls: int = 0

    async def generate(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Return the next scripted completion.

        Args:
            prompt (str): Ignored.
            config (GenerationConfig | None): Recorded, for the assertions.
            **kwargs (Any): Ignored.

        Returns:
            str: The scripted output for this attempt.
        """
        self.temperatures.append(config.temperature if config else None)
        index = min(self.calls, len(self._outputs) - 1)
        self.calls += 1
        return self._outputs[index]


async def test_first_attempt_is_greedy() -> None:
    """Attempt one runs at temperature 0 — the most reliable single shot."""
    backend = _ScriptedBackend(['[{"title": "a"}]'])

    items = await generate_structured_list(backend, "prompt", _Item)

    assert [item.title for item in items] == ["a"]
    assert backend.temperatures == [0.0]
    assert backend.calls == 1


async def test_temperature_climbs_on_each_retry() -> None:
    """Each retry adds the step, so a retry is not a rerun of the same call.

    This is the whole reason the loop exists: without the climb, attempts
    two and three are the first one again.
    """
    backend = _ScriptedBackend(["no array here", "still nothing", '[{"title": "c"}]'])

    items = await generate_structured_list(
        backend,
        "prompt",
        _Item,
        max_attempts=3,
        temperature_step=0.2,
    )

    assert [item.title for item in items] == ["c"]
    assert backend.temperatures == [0.0, 0.2, 0.4]


async def test_stops_as_soon_as_it_parses() -> None:
    """A usable answer ends the loop; the budget is a ceiling, not a quota."""
    backend = _ScriptedBackend(["garbage", '[{"title": "b"}]'])

    await generate_structured_list(backend, "prompt", _Item, max_attempts=5)

    assert backend.calls == 2


async def test_exhausted_budget_raises_with_the_last_output() -> None:
    """Giving up says what the model actually wrote, not just that it failed."""
    backend = _ScriptedBackend(["I cannot do that"])

    with pytest.raises(StructuredFormatError) as excinfo:
        await generate_structured_list(backend, "prompt", _Item, max_attempts=2)

    assert excinfo.value.attempts == 2
    assert "I cannot do that" in excinfo.value.last_output
    assert backend.calls == 2


async def test_error_is_a_value_error() -> None:
    """Existing ``except ValueError`` callers keep working."""
    backend = _ScriptedBackend(["nope"])

    with pytest.raises(ValueError):
        await generate_structured_list(backend, "prompt", _Item, max_attempts=1)


async def test_empty_list_is_a_success_not_a_retry() -> None:
    """``[]`` means the model answered "no items" — retrying would be wrong.

    Spending the whole budget re-asking a question that was already answered
    is the bug this pins down.
    """
    backend = _ScriptedBackend(["[]"])

    items = await generate_structured_list(backend, "prompt", _Item, max_attempts=3)

    assert items == []
    assert backend.calls == 1


async def test_one_bad_item_does_not_cost_an_attempt() -> None:
    """A malformed item is not a formatting failure of the whole answer.

    Nine good suggestions should not be lost, nor a generation spent, over
    one item the model got wrong.
    """
    backend = _ScriptedBackend(['[{"title": "a"}, {"wrong": 1}, {"title": "b"}]'])

    items = await generate_structured_list(backend, "prompt", _Item, max_attempts=3)

    assert [item.title for item in items] == ["a", "b"]
    assert backend.calls == 1


async def test_skip_invalid_off_raises_on_a_bad_item() -> None:
    """All-or-nothing callers can still have it."""
    backend = _ScriptedBackend(['[{"title": "a"}, {"wrong": 1}]'])

    with pytest.raises(ValueError):
        await generate_structured_list(
            backend,
            "prompt",
            _Item,
            skip_invalid=False,
        )


async def test_base_config_is_not_mutated() -> None:
    """The caller's config is shared; raising its temperature would leak.

    A config is commonly built once and reused across calls, so mutating it
    in place would push this retry's temperature into every later call.
    """
    config = GenerationConfig(max_new_tokens=500, temperature=0.1)
    backend = _ScriptedBackend(["nope", '[{"title": "a"}]'])

    await generate_structured_list(backend, "prompt", _Item, config=config)

    assert config.temperature == 0.1


async def test_other_config_fields_survive_the_retry() -> None:
    """Only the temperature is replaced per attempt."""
    captured: list[GenerationConfig | None] = []

    class _Capturing(_ScriptedBackend):
        async def generate(
            self,
            prompt: str,
            *,
            config: GenerationConfig | None = None,
            **kwargs: Any,
        ) -> str:
            """Capture the whole config, then defer to the script.

            Args:
                prompt (str): Ignored.
                config (GenerationConfig | None): Captured.
                **kwargs (Any): Ignored.

            Returns:
                str: The scripted output.
            """
            captured.append(config)
            return await super().generate(prompt, config=config, **kwargs)

    backend = _Capturing(["nope", '[{"title": "a"}]'])
    await generate_structured_list(
        backend,
        "prompt",
        _Item,
        config=GenerationConfig(max_new_tokens=500, temperature=0.1),
    )

    assert [c.max_new_tokens for c in captured if c] == [500, 500]
    assert [c.temperature for c in captured if c] == [0.0, 0.2]


async def test_zero_attempts_is_refused() -> None:
    """A budget below one would return without ever calling the model."""
    backend = _ScriptedBackend(["[]"])

    with pytest.raises(ValueError, match="max_attempts"):
        await generate_structured_list(backend, "prompt", _Item, max_attempts=0)

    assert backend.calls == 0

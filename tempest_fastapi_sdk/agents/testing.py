"""Testing an agent without a model.

An agent's behaviour is a function of what the model decides, which makes
it feel untestable: you cannot assert on a 0.5B model's mood. But almost
every bug worth catching is in *your* code, not the model's — a tool that
mishandles an argument, a budget that never fires, a skill whose tools
never unlock, a delegation that loses its artifacts.

All of that is testable by **scripting the model's decisions**. You state
what the model would reply, and assert on what your agent did with it:

    >>> backend = ScriptedBackend([
    ...     replies_with_tool("get_weather", city="Recife"),
    ...     replies("It is 22 degrees."),
    ... ])
    >>> run = await Agent(backend, tools=[get_weather]).run("weather?")
    >>> assert_used_tools(run, "get_weather")
    >>> assert run.output == "It is 22 degrees."

These are the same helpers the SDK's own agent suite uses. They are
exported because your agent needs the identical treatment, and because a
test that boots a real model is slow, flaky and tests the wrong thing.

Import needs no extra and no model. For the cases scripting cannot reach —
does the model actually pick the right tool? — run it against a small
local model in a separate, marked test, and keep it out of the fast suite.
"""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.agents.schemas import AgentRun, AgentStep, StepKind


def replies(content: str) -> dict[str, Any]:
    """Script one plain answer from the model.

    Args:
        content (str): What the model says.

    Returns:
        dict[str, Any]: A backend reply with no tool calls, which is how a
        model signals it is finished.
    """
    return {"content": content, "tool_calls": []}


def tool_call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one tool call in the shape a backend reports it.

    Arguments are a dict rather than keywords so a tool argument literally
    named ``name`` cannot collide with the tool's own name.

    Args:
        name (str): The tool the model wants to call.
        arguments (dict[str, Any] | None): What it passes.

    Returns:
        dict[str, Any]: The call entry.
    """
    return {"function": {"name": name, "arguments": arguments or {}}}


def replies_with_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    content: str = "",
) -> dict[str, Any]:
    """Script the model asking for one tool.

    Args:
        name (str): The tool to call.
        arguments (dict[str, Any] | None): What to pass it.
        content (str): Any text alongside the call.

    Returns:
        dict[str, Any]: The backend reply.
    """
    return {"content": content, "tool_calls": [tool_call(name, arguments)]}


def replies_with_tools(*calls: dict[str, Any], content: str = "") -> dict[str, Any]:
    """Script the model asking for several tools in one turn.

    Args:
        *calls (dict[str, Any]): Entries from :func:`tool_call`.
        content (str): Any text alongside the calls.

    Returns:
        dict[str, Any]: The backend reply.
    """
    return {"content": content, "tool_calls": list(calls)}


class ScriptedBackend:
    """A text backend that replays decisions you wrote.

    Example:

        >>> backend = ScriptedBackend([
        ...     replies_with_tool("search", {"query": "pix"}),
        ...     replies("PIX is instant payments."),
        ... ])

    Attributes:
        prompts (list[str]): The goal seen on each call, in order.
        system_prompts (list[str]): The system prompt seen on each call —
            useful for asserting that memory or skills reached the model.
        specs_seen (list[list[str]]): The tool names offered on each call,
            which is how you assert a skill's tools stayed hidden until it
            was loaded.
        calls (int): How many times the backend was asked.
    """

    def __init__(
        self,
        script: list[dict[str, Any]],
        *,
        repeat_last: bool = False,
    ) -> None:
        """Configure the backend.

        Args:
            script (list[dict[str, Any]]): The replies, in order. Build
                them with :func:`replies` / :func:`replies_with_tool`.
            repeat_last (bool): Keep returning the final reply instead of
                falling through to a generic answer. Use it to test a
                budget: a model that never stops asking for tools is
                exactly what a step or time ceiling exists for.
        """
        self._script = list(script)
        self._repeat_last = repeat_last
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []
        self.specs_seen: list[list[str]] = []
        self.calls = 0

    def _record(self, messages: list[dict[str, Any]]) -> None:
        """Capture what the agent sent this turn."""
        self.calls += 1
        if messages:
            self.system_prompts.append(str(messages[0].get("content", "")))
        if len(messages) > 1:
            self.prompts.append(str(messages[1].get("content", "")))

    def _next(self) -> dict[str, Any]:
        """Return the next scripted reply."""
        if not self._script:
            return replies("done")
        if self._repeat_last and len(self._script) == 1:
            return self._script[0]
        return self._script.pop(0)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return the next scripted decision.

        Args:
            messages (list[dict[str, Any]]): The conversation so far.
            specs (list[dict[str, Any]]): The tools on offer this turn.

        Returns:
            dict[str, Any]: The scripted reply.
        """
        self._record(messages)
        self.specs_seen.append([spec["function"]["name"] for spec in specs])
        return self._next()

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        """Return the next scripted answer as plain text.

        Args:
            messages (list[dict[str, Any]]): The conversation so far.

        Returns:
            str: The reply's content.
        """
        self._record(messages)
        self.specs_seen.append([])
        return str(self._next().get("content", ""))

    @property
    def exhausted(self) -> bool:
        """Return whether every scripted reply was used.

        A test that scripts five turns and uses two is usually asserting
        less than its author thinks — the agent stopped early and the
        remaining script never ran.

        Returns:
            bool: ``True`` when the script is spent.
        """
        return not self._script


class FailingBackend:
    """A backend that always raises, for testing the error path.

    An agent must turn a backend outage into
    :attr:`~tempest_fastapi_sdk.agents.StopReason.ERROR` with the message
    preserved, not into an exception escaping into a request handler.
    """

    def __init__(self, message: str = "backend is down") -> None:
        """Configure the failure.

        Args:
            message (str): The error text to raise.
        """
        self.message = message

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Raise the configured failure.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError(self.message)

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        """Raise the configured failure.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError(self.message)


def tool_steps(run: AgentRun) -> list[AgentStep]:
    """Return only the tool steps of a run, including failed ones.

    Args:
        run (AgentRun): The finished run.

    Returns:
        list[AgentStep]: The tool steps, in order.
    """
    return [step for step in run.steps if step.kind == StepKind.TOOL]


def failed_steps(run: AgentRun) -> list[AgentStep]:
    """Return the steps that carried an error.

    A run can be successful and still contain failed steps — that is the
    design, since a tool error is an observation the model recovers from.
    Asserting on this is how you check the recovery actually happened.

    Args:
        run (AgentRun): The finished run.

    Returns:
        list[AgentStep]: The failed steps, in order.
    """
    return [step for step in run.steps if step.error]


def assert_used_tools(run: AgentRun, *names: str) -> None:
    """Assert the run called exactly these tools, in this order.

    Args:
        run (AgentRun): The finished run.
        *names (str): Expected tool names, in order.

    Raises:
        AssertionError: When the calls differ, with both sequences in the
            message so the diff is readable.
    """
    actual = run.tool_calls
    if actual != list(names):
        raise AssertionError(
            f"expected tools {list(names)}, got {actual}",
        )


def assert_completed(run: AgentRun) -> None:
    """Assert the model finished on its own terms.

    The check people forget: a run stopped by a budget still carries text,
    so asserting only on ``output`` passes for truncated work.

    Args:
        run (AgentRun): The finished run.

    Raises:
        AssertionError: When the run did not complete, naming the reason.
    """
    if not run.succeeded:
        raise AssertionError(
            f"run did not complete: stop_reason={run.stop_reason}, "
            f"output={run.output[:120]!r}",
        )


def assert_artifact(run: AgentRun, name: str, *, media_type: str | None = None) -> None:
    """Assert the run produced an artifact under ``name``.

    Args:
        run (AgentRun): The finished run.
        name (str): The expected artifact name.
        media_type (str | None): Also assert the media type.

    Raises:
        AssertionError: When the artifact is missing or the type differs.
    """
    found = run.artifact(name)
    if found is None:
        available = [item.name for item in run.artifacts] or ["none"]
        raise AssertionError(
            f"no artifact named {name!r}; produced: {available}",
        )
    if media_type is not None and found.media_type != media_type:
        raise AssertionError(
            f"artifact {name!r} is {found.media_type}, expected {media_type}",
        )


__all__: list[str] = [
    "FailingBackend",
    "ScriptedBackend",
    "assert_artifact",
    "assert_completed",
    "assert_used_tools",
    "failed_steps",
    "replies",
    "replies_with_tool",
    "replies_with_tools",
    "tool_call",
    "tool_steps",
]

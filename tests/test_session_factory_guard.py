"""Every router factory registers ``session_factory`` through the helper (#524).

#493 fixed ``make_auth_router`` alone, and five other routers kept the
pre-#493 shape — ``async for session in session_factory(): yield session``
inside a local wrapper — which FastAPI cannot see into: a ``get_session``
with sub-dependencies dies with ``TypeError`` and
``app.dependency_overrides[get_session]`` never reaches the routes. The rule
now lives in :func:`tempest_fastapi_sdk.api.dependencies._session.session_provider_for`,
and this guard reads every ``make_*`` function in the package that takes a
``session_factory`` parameter and fails when its body:

* never calls ``session_provider_for(session_factory)``; or
* calls ``session_factory()`` by hand, or registers ``Depends(session_factory)``
  directly — the two shapes that bypass the helper.

Blind spot: a factory whose session parameter has another name, and a call
made through an alias (``f = session_factory; f()``).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

PACKAGE: Path = Path(__file__).resolve().parent.parent / "tempest_fastapi_sdk"

PARAM: str = "session_factory"
HELPER: str = "session_provider_for"

EXPECTED_FACTORIES: frozenset[str] = frozenset(
    {
        "app_errors/router.py:make_app_error_router",
        "auth/router.py:make_auth_router",
        "chat/router.py:make_chat_router",
        "genai/audio/router.py:make_voice_router",
        "push/router.py:make_push_router",
        "reviews/router.py:make_reviews_router",
        "sessions/router.py:make_session_router",
        "wallet/router.py:make_wallet_router",
        "webpush/router.py:make_web_push_router",
    }
)
"""The factories that took a ``session_factory`` when the guard was written.

Pinned so the scan cannot pass by finding nothing; a new factory joins the
set and is checked like the others.
"""

PRE_524_PUSH_ROUTER: str = """
def make_push_router(*, service_factory, session_factory, current_user_id):
    router = APIRouter(prefix="/api/push")

    async def _session() -> AsyncIterator[AsyncSession]:
        async for session in session_factory():
            yield session

    def _service(session: AsyncSession = Depends(_session)):
        return service_factory(session)

    return router
"""
"""The shape ``make_push_router`` shipped up to #524."""

PRE_524_APP_ERRORS_ROUTER: str = """
def make_app_error_router(*, service_factory, session_factory):
    router = APIRouter(prefix="/api/app-errors")

    @router.post("")
    async def report(session=Depends(session_factory)):
        return service_factory(session)

    return router
"""
"""The shape ``make_app_error_router`` shipped up to #524: ``Depends``
directly, so a zero-argument non-generator factory was never iterated."""


def _is_name_call(node: ast.AST, name: str) -> TypeGuard[ast.Call]:
    """Tell whether ``node`` is a call to the bare name ``name``.

    Args:
        node (ast.AST): Any node.
        name (str): The callee's name.

    Returns:
        TypeGuard[ast.Call]: ``True`` for ``name(...)``.
    """
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    )


def _passes_param(call: ast.Call) -> bool:
    """Tell whether ``session_factory`` is the call's first argument.

    Args:
        call (ast.Call): The call.

    Returns:
        bool: ``True`` for ``f(session_factory, ...)``.
    """
    return bool(call.args) and (
        isinstance(call.args[0], ast.Name) and call.args[0].id == PARAM
    )


def violations(source: str) -> dict[str, list[str]]:
    """Find factories that register ``session_factory`` outside the helper.

    Args:
        source (str): A module's source.

    Returns:
        dict[str, list[str]]: Each factory taking ``session_factory``,
        mapped to what is wrong with it (empty when it is fine).
    """
    found: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("make_"):
            continue
        params = [arg.arg for arg in (*node.args.args, *node.args.kwonlyargs)]
        if PARAM not in params:
            continue
        problems: list[str] = []
        uses_helper = False
        for inner in ast.walk(node):
            if _is_name_call(inner, HELPER) and _passes_param(inner):
                uses_helper = True
            if _is_name_call(inner, PARAM):
                problems.append(f"line {inner.lineno}: calls {PARAM}() by hand")
            if _is_name_call(inner, "Depends") and _passes_param(inner):
                problems.append(
                    f"line {inner.lineno}: Depends({PARAM}) bypasses {HELPER}"
                )
        if not uses_helper:
            problems.append(f"never calls {HELPER}({PARAM})")
        found[node.name] = problems
    return found


def _scan() -> dict[str, list[str]]:
    """Run :func:`violations` over every module of the package.

    Returns:
        dict[str, list[str]]: Keyed ``"<relative path>:<factory>"``.
    """
    result: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        for name, problems in violations(path.read_text(encoding="utf-8")).items():
            result[f"{relative}:{name}"] = problems
    return result


class TestSessionFactoryGuard:
    def test_every_factory_registers_the_session_through_the_helper(self) -> None:
        """No router iterates or registers ``session_factory`` on its own."""
        broken = {key: problems for key, problems in _scan().items() if problems}

        assert broken == {}

    def test_the_scan_finds_the_known_factories(self) -> None:
        """Guard against passing because the scan matched nothing."""
        assert set(_scan()) >= EXPECTED_FACTORIES

    def test_fires_on_the_pre_524_wrapper(self) -> None:
        """The exact shape ``make_push_router`` shipped is caught."""
        problems = violations(PRE_524_PUSH_ROUTER)["make_push_router"]

        assert any("by hand" in problem for problem in problems)
        assert any(HELPER in problem for problem in problems)

    def test_fires_on_a_direct_depends(self) -> None:
        """The shape ``make_app_error_router`` shipped is caught too."""
        problems = violations(PRE_524_APP_ERRORS_ROUTER)["make_app_error_router"]

        assert any("bypasses" in problem for problem in problems)

"""Guard: every token-issuing path of ``make_auth_router`` is classified (#543).

``POST /auth/login`` has always deferred the JWT pair of an MFA-enrolled
account to ``/auth/mfa/verify``. ``POST /auth/activate/{token}`` and
``POST /auth/password-reset/confirm`` minted the pair straight from an
emailed single-use token, so whoever held the mailbox walked past the
second factor; #534 (0.313.0) gave both the same branch as ``/login``.
Nothing stopped the next route from repeating the mistake, so this guard
reads ``auth/router.py`` and lists every function that calls a pair
issuer. Each one must be in :data:`ALLOWLIST` under a category:

* ``gated`` — the function returns from an ``if`` on
  ``service.is_mfa_enrolled(...)`` before it issues, exactly like ``/login``.
* ``completes-mfa`` — the route *is* the second factor (``/mfa/verify``,
  ``/mfa/confirm``, WebAuthn), and issues through ``_issue_pair``, which
  stamps the ``amr`` the factor proved.
* ``new-account`` — the account was created in the same request, so no
  factor can be enrolled yet.
* ``rotation`` — ``refresh_tokens`` rotates an existing session and repeats
  its stored ``amr``; it opens nothing new.
* ``helper`` — ``_issue_pair`` itself, whose callers are classified above.

The scan is by AST, not over a mounted router, because most routes are
mounted only under a setting (cookie delivery, backend links, MFA,
WebAuthn, OAuth) and a router built with one combination would skip the
others.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

ROUTER: Path = (
    Path(__file__).resolve().parent.parent
    / "tempest_fastapi_sdk"
    / "auth"
    / "router.py"
)
"""The module that defines ``make_auth_router``."""

FACTORY: str = "make_auth_router"
"""The factory whose nested route functions are scanned."""

ISSUERS: frozenset[str] = frozenset(
    {
        "issue_token_pair",
        "issue_jwt_pair",
        "_issue_pair",
        "_issue_pair_with_amr",
        "_encode_access",
        "login_with_oauth",
        "refresh_tokens",
    }
)
"""Calls that hand back an access token (directly or inside a tuple)."""

MFA_DECISION: str = "is_mfa_enrolled"
"""The call a ``gated`` path branches on before it issues."""

AMR_STAMPING_ISSUER: str = "_issue_pair"
"""The issuer a ``completes-mfa`` path must use, so the factor reaches ``amr``."""

ALLOWLIST: dict[str, tuple[str, str]] = {
    "_issue_pair": (
        "helper",
        "forwards amr to service.issue_token_pair; every caller is listed here",
    ),
    "signup": (
        "new-account",
        "issues only when AUTH_AUTO_ACTIVATE made the account active in this "
        "same request, so no TOTP or WebAuthn factor can exist yet",
    ),
    "activate": (
        "gated",
        "an activation token reaches a deactivated MFA account through "
        "/activation/request (#534)",
    ),
    "login": ("gated", "the reference two-step login"),
    "login_cookie": ("gated", "same decision as /login, pair set as cookies"),
    "password_reset_confirm": (
        "gated",
        "a reset token proves the mailbox, not the second factor (#534)",
    ),
    "refresh": (
        "rotation",
        "rotates an existing session and repeats the amr stored on it",
    ),
    "refresh_cookie": ("rotation", "same as /refresh, from the refresh cookie"),
    "password_change": (
        "gated",
        "the current password proves the first factor only; an MFA account "
        "without a code in the body gets the /login challenge, and with one "
        "the code is spent by reauthenticate before the pair (#662)",
    ),
    "mfa_confirm": (
        "completes-mfa",
        "the request proves a fresh TOTP code; issues with MFA_AMR",
    ),
    "mfa_verify": (
        "completes-mfa",
        "step two of login: TOTP or recovery code; issues with MFA_AMR",
    ),
    "webauthn_authenticate_complete": (
        "completes-mfa",
        "a WebAuthn assertion; issues with amr=['hwk']",
    ),
    "oauth_callback": (
        "gated",
        "the provider proves the identity, not the second factor; an account "
        "with TOTP enrolled gets the mfa_token like /login",
    ),
    "oauth_token": ("gated", "same as oauth_callback, token-in-hand"),
}
"""Function name inside ``make_auth_router`` -> ``(category, reason)``."""


@dataclass(frozen=True)
class IssuingPath:
    """One function of the factory that calls a pair issuer.

    Attributes:
        name (str): The function name (route handler or helper).
        issuers (frozenset[str]): The issuer names it calls.
        gated (bool): Whether it returns from an ``if`` on
            ``is_mfa_enrolled`` that sits before its first issuer call.
    """

    name: str
    issuers: frozenset[str]
    gated: bool


def _called_name(node: ast.Call) -> str | None:
    """Return the bare name a call targets, for ``f()`` and ``obj.f()``.

    Args:
        node (ast.Call): The call.

    Returns:
        str | None: The target name, ``None`` for anything else.
    """
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _own_nodes(function: ast.AST) -> list[ast.AST]:
    """Walk ``function`` without descending into nested functions.

    Args:
        function (ast.AST): The function definition.

    Returns:
        list[ast.AST]: Every node that belongs to this function's own body.
    """
    found: list[ast.AST] = []
    stack: list[ast.AST] = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        found.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return found


def _returns_on_mfa_decision(node: ast.If) -> bool:
    """Tell whether ``node`` is ``if service.is_mfa_enrolled(...): ... return``.

    Args:
        node (ast.If): The candidate branch.

    Returns:
        bool: ``True`` when the test calls the MFA decision and the branch
        body ends in a ``return``.
    """
    decides = any(
        isinstance(inner, ast.Call) and _called_name(inner) == MFA_DECISION
        for inner in ast.walk(node.test)
    )
    return decides and bool(node.body) and isinstance(node.body[-1], ast.Return)


def issuing_paths(source: str) -> list[IssuingPath]:
    """List every function nested in the factory that calls a pair issuer.

    Args:
        source (str): Python source holding a ``make_auth_router`` function.

    Returns:
        list[IssuingPath]: One entry per issuing function, in source order.

    Raises:
        AssertionError: When ``source`` defines no ``make_auth_router``.
    """
    tree = ast.parse(source)
    factory = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == FACTORY
        ),
        None,
    )
    assert factory is not None, f"{FACTORY} not found in the scanned source"
    paths: list[IssuingPath] = []
    for node in ast.walk(factory):
        if node is factory or not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        own = _own_nodes(node)
        calls = [
            call
            for call in own
            if isinstance(call, ast.Call) and _called_name(call) in ISSUERS
        ]
        if not calls:
            continue
        first_issue = min(call.lineno for call in calls)
        gated = any(
            isinstance(branch, ast.If)
            and branch.lineno < first_issue
            and _returns_on_mfa_decision(branch)
            for branch in own
        )
        paths.append(
            IssuingPath(
                name=node.name,
                issuers=frozenset(
                    name for call in calls if (name := _called_name(call)) is not None
                ),
                gated=gated,
            )
        )
    return sorted(paths, key=lambda path: path.name)


def violations(paths: list[IssuingPath]) -> list[str]:
    """Check each issuing path against :data:`ALLOWLIST`.

    Args:
        paths (list[IssuingPath]): The scan result.

    Returns:
        list[str]: One message per path that is unlisted or breaks the
        contract of its category; empty when every path is accounted for.
    """
    found: list[str] = []
    for path in paths:
        entry = ALLOWLIST.get(path.name)
        if entry is None:
            found.append(
                f"{path.name}: issues a token pair ({sorted(path.issuers)}) and "
                "is not in ALLOWLIST; branch on service.is_mfa_enrolled(user) "
                "like /login, or list it with the reason no factor can apply"
            )
            continue
        category = entry[0]
        if category == "gated" and not path.gated:
            found.append(
                f"{path.name}: listed as gated but issues without first "
                "returning from an `if service.is_mfa_enrolled(...)` branch"
            )
        if category == "completes-mfa" and path.issuers != {AMR_STAMPING_ISSUER}:
            found.append(
                f"{path.name}: completes MFA but issues through "
                f"{sorted(path.issuers)}, not {AMR_STAMPING_ISSUER}, so the "
                "factor never reaches the amr claim"
            )
    return found


def test_every_issuing_path_is_classified() -> None:
    """Every issuing function is listed, keeps its contract, and no entry is stale."""
    paths = issuing_paths(ROUTER.read_text("utf-8"))
    assert violations(paths) == []
    stale = set(ALLOWLIST) - {path.name for path in paths}
    assert stale == set(), f"stale ALLOWLIST entries: {sorted(stale)}"


def test_the_scan_sees_every_path_the_issue_names() -> None:
    """The routes of #543 are found, and found gated."""
    by_name = {path.name: path for path in issuing_paths(ROUTER.read_text("utf-8"))}
    for name in (
        "login",
        "login_cookie",
        "activate",
        "password_reset_confirm",
        "oauth_callback",
        "oauth_token",
    ):
        assert by_name[name].gated, name


def test_the_html_backend_link_routes_issue_nothing() -> None:
    """``activate_html`` and the reset form render a page, never a pair."""
    names = {path.name for path in issuing_paths(ROUTER.read_text("utf-8"))}
    assert "activate_html" not in names
    assert "password_reset_form_submit" not in names


SHIPPED_PRE_534: str = """
def make_auth_router(service, session_dep):
    async def activate(token, session=session_dep):
        user = await service.activate(session, token=token)
        access, refresh = await service.issue_token_pair(session, user)
        await session.commit()
        return ActivationResponseSchema(
            user_id=user.id, access_token=access, refresh_token=refresh
        )

    async def password_reset_confirm(payload, session=session_dep):
        user = await service.confirm_password_reset(
            session, token=payload.token, new_password=payload.new_password
        )
        access, refresh = await service.issue_token_pair(session, user)
        await session.commit()
        return LoginResponseSchema(
            user_id=user.id, access_token=access, refresh_token=refresh
        )
"""
"""The two routes as they shipped through 0.312.0, before #534."""


def test_guard_fires_on_the_routes_as_they_shipped_before_534() -> None:
    """The exact shape #534 fixed is flagged on both routes."""
    found = violations(issuing_paths(SHIPPED_PRE_534))
    assert len(found) == 2
    assert found[0].startswith("activate: listed as gated")
    assert found[1].startswith("password_reset_confirm: listed as gated")


def test_guard_fires_on_a_new_route_outside_the_allowlist() -> None:
    """A magic-link login added without the MFA branch is flagged."""
    source = """
def make_auth_router(service, session_dep):
    async def magic_link(token, session=session_dep):
        user = await service.consume_magic_link(session, token=token)
        access, refresh = await service.issue_token_pair(session, user)
        return access, refresh
"""
    found = violations(issuing_paths(source))
    assert len(found) == 1
    assert found[0].startswith("magic_link: issues a token pair")


def test_a_check_after_the_issue_does_not_count_as_gated() -> None:
    """The MFA branch must come before the pair, not after it."""
    source = """
def make_auth_router(service, session_dep):
    async def login(payload, session=session_dep):
        user = await service.authenticate(session, payload)
        access, refresh = await service.issue_token_pair(session, user)
        if service.is_mfa_enrolled(user):
            return None
        return access, refresh
"""
    found = violations(issuing_paths(source))
    assert found == [
        "login: listed as gated but issues without first returning from an "
        "`if service.is_mfa_enrolled(...)` branch"
    ]


def test_guard_fires_on_a_factor_route_that_drops_the_amr() -> None:
    """``/mfa/verify`` issuing through the bare service call loses MFA_AMR."""
    source = """
def make_auth_router(service, session_dep):
    async def mfa_verify(payload, session=session_dep):
        user = await service.verify_mfa(session, payload)
        return await service.issue_token_pair(session, user)
"""
    found = violations(issuing_paths(source))
    assert len(found) == 1
    assert found[0].startswith("mfa_verify: completes MFA but issues through")


UNGATED_PASSWORD_CHANGE: str = """
def make_auth_router(service, session_dep):
    async def password_change(payload, session=session_dep, user=None, claims=None):
        user = await service.change_password(
            session,
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
        access, refresh = await _issue_pair(session, user, token_amr(claims))
        await session.commit()
        return LoginResponseSchema(
            user_id=user.id, access_token=access, refresh_token=refresh
        )
"""
"""``/password-change`` reissuing the pair with the caller's ``amr`` (#662 draft).

The current password alone would mint a pair for an MFA account, and the
copied ``amr`` would claim a factor this request never proved.
"""


def test_guard_fires_on_a_password_change_that_skips_the_factor() -> None:
    """The draft shape of #662 is flagged: it issues without the MFA branch."""
    found = violations(issuing_paths(UNGATED_PASSWORD_CHANGE))
    assert found == [
        "password_change: listed as gated but issues without first "
        "returning from an `if service.is_mfa_enrolled(...)` branch"
    ]

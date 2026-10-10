"""What the Mercado Pago sandbox showed on 2026-10-09, pinned offline.

Issue #226 had two halves: type the operations whose shape nobody had
observed, and find out whether the 47 non-`GET` operations only our
document carries are routed at all. Both were answered against the sandbox
with a `TEST-` token, and the evidence is in `vendor/mercadopago-evidence.md`
section 8.

The offline tests replay the redacted payload in `fixtures/` and pin the
classification, so `make check` covers it without network. The tests marked
`network` re-measure against the real API; they skip without
`MERCADO_PAGO_TEST_ACCESS_TOKEN` and refuse any token that is not `TEST-`.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    AuthenticatedUser,
    ChargebackSearchResponse,
    MercadoPagoClient,
)

REPO_ROOT: Path = Path(__file__).resolve().parents[4]
SCRIPTS: str = str(REPO_ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from mercadopago_overlay import (  # noqa: E402
    OBSERVED_SCHEMAS,
    OFFICIAL_SDK_CALLS,
    PROBED_OPERATIONS,
    SANDBOX_ROUTED_OPERATIONS,
    SELLER_ROUTED_GETS,
    UNROUTED_OPERATIONS,
    UNVERIFIED_NOTE,
    apply,
    normalise,
)

FIXTURES: Path = Path(__file__).parent / "fixtures"
TOKEN_ENV: str = "MERCADO_PAGO_TEST_ACCESS_TOKEN"


def _users_me() -> dict[str, Any]:
    """Load the redacted `/users/me` response the sandbox returned.

    Returns:
        dict[str, Any]: The payload, with every personal value replaced by
        a stable fake and every key and JSON type kept.
    """
    payload: dict[str, Any] = json.loads(
        (FIXTURES / "users_me.json").read_text(encoding="utf-8")
    )
    return payload


def _mock_client(body: dict[str, Any], seen: list[httpx.Request]) -> HTTPClient:
    """Build an ``HTTPClient`` that answers every request with ``body``.

    Args:
        body (dict[str, Any]): The JSON the fake server returns.
        seen (list[httpx.Request]): Collects every request sent.

    Returns:
        HTTPClient: A client wired to an ``httpx.MockTransport``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the request and answer with the canned body.

        Args:
            request (httpx.Request): The request the client built.

        Returns:
            httpx.Response: ``200`` with ``body`` as JSON.
        """
        seen.append(request)
        return httpx.Response(200, json=body)

    return HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": "Bearer a-test-token"},
        transport=httpx.MockTransport(handler),
    )


def _operations() -> dict[tuple[str, str], dict[str, Any]]:
    """Read every operation of the corrected document, keyed normalised.

    Returns:
        dict[tuple[str, str], dict[str, Any]]: Operation objects by
        ``(METHOD, path)`` with placeholders collapsed to ``{}``.
    """
    document, _ = apply(
        yaml.safe_load(
            (REPO_ROOT / "vendor" / "mercadopago-openapi.yaml").read_text(
                encoding="utf-8"
            )
        )
    )
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if isinstance(operation, dict) and method in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
            }:
                found[normalise(method, path)] = operation
    return found


class TestTheAuthenticatedUserIsTyped:
    """`GET /users/me` answers a model built from the observed response."""

    def test_the_observed_payload_validates(self) -> None:
        """The model accepts the exact shape the sandbox returned."""
        user = AuthenticatedUser.model_validate(_users_me())

        assert user.id == 123456789
        assert user.site_id == "MLB"
        assert user.identification is not None
        assert user.identification.type == "CPF"
        assert user.tags == ["normal", "messages_as_seller", "user_product_seller"]

    def test_every_declared_field_was_observed_non_null(self) -> None:
        """A field is declared only if the response carried a value for it.

        That is the rule that keeps this from becoming the OpenPix v0.259.0
        defect: nothing here is read from documentation.
        """
        payload = _users_me()
        for name, schema in OBSERVED_SCHEMAS.items():
            if not name.startswith("AuthenticatedUser"):
                continue
            node: Any = payload
            suffix = name.removeprefix("AuthenticatedUser")
            if suffix:
                node = payload[suffix.lower()]
            for field in schema["properties"]:
                assert field in node, f"{name}.{field}"
                assert node[field] is not None, f"{name}.{field}"

    def test_undeclared_fields_are_kept_not_dropped(self) -> None:
        """The reputation and status blocks ride along as extra fields."""
        user = AuthenticatedUser.model_validate(_users_me())

        assert user.model_extra is not None
        assert user.model_extra["status"]["site_status"] == "active"
        assert "seller_reputation" in user.model_extra

    async def test_the_client_answers_the_model(self) -> None:
        """The generated method validates into the model, on the SDK's path."""
        seen: list[httpx.Request] = []
        async with _mock_client(_users_me(), seen) as http:
            user = await MercadoPagoClient(http).get_authenticated_user()

        assert isinstance(user, AuthenticatedUser)
        assert user.nickname == "TESTUSER0000001"
        assert seen[0].method == "GET"
        assert seen[0].url.path == "/users/me"


class TestTheChargebackSearchIsTyped:
    """`GET /v1/chargebacks/search` needs `payment_id` and answers an envelope.

    Observed on 2026-10-10 with a test seller's token (evidence 9.7): without
    `payment_id` the call is `400`, which the method generated before this
    — `limit` and `offset` only — could never avoid.
    """

    async def test_the_client_sends_payment_id_and_answers_the_model(self) -> None:
        """The observed envelope validates; `payment_id` is in the query."""
        body: dict[str, Any] = json.loads(
            (FIXTURES / "chargebacks_search.json").read_text(encoding="utf-8")
        )
        seen: list[httpx.Request] = []
        async with _mock_client(body, seen) as http:
            found = await MercadoPagoClient(http).search_chargebacks(
                payment_id="123456789"
            )

        assert isinstance(found, ChargebackSearchResponse)
        assert found.paging is not None
        assert (found.paging.offset, found.paging.limit, found.paging.total) == (
            0,
            25,
            0,
        )
        assert found.results == []
        assert seen[0].url.path == "/v1/chargebacks/search"
        assert dict(seen[0].url.params) == {"payment_id": "123456789"}

    def test_payment_id_is_required_and_the_ignored_paging_is_gone(self) -> None:
        """Keyword-only, no default; `limit` / `offset` not offered."""
        parameters = inspect.signature(MercadoPagoClient.search_chargebacks).parameters

        assert list(parameters) == ["self", "payment_id"]
        assert parameters["payment_id"].default is inspect.Parameter.empty
        assert parameters["payment_id"].kind is inspect.Parameter.KEYWORD_ONLY


class TestTheProbeOnlyGetsWereReEvaluated:
    """Issue #488: the 34 `GET` only the unauthenticated probe vouched for.

    Re-measured on 2026-10-10 with a test seller's token, each against an
    invented sibling (evidence 9.8): 20 told apart, 2 unrouted, 12 that the
    probe cannot speak for and now say so.
    """

    def test_the_34_split_20_2_12(self) -> None:
        """Every probe-only entry landed in exactly one bucket."""
        operations = _operations()
        unverified_gets = {
            key
            for key, operation in operations.items()
            if key[0] == "GET"
            and UNVERIFIED_NOTE.strip() in str(operation.get("description") or "")
        }
        unrouted_gets = {
            normalise(e.method, e.path)
            for e in UNROUTED_OPERATIONS
            if e.method == "get"
        }

        assert len(SELLER_ROUTED_GETS) == 20
        assert unrouted_gets == {
            ("GET", "/v1/account/release_report"),
            ("GET", "/v1/account/settlement_report"),
        }
        assert len(unverified_gets) == 12
        assert not (set(SELLER_ROUTED_GETS) & unverified_gets)
        assert not (set(SELLER_ROUTED_GETS) & set(PROBED_OPERATIONS))

    def test_what_is_left_in_the_old_probe_the_sdk_also_calls(self) -> None:
        """The unauthenticated status no longer stands alone for any entry."""
        operations = _operations()
        lonely = [
            key
            for key in PROBED_OPERATIONS
            if key in operations and key not in OFFICIAL_SDK_CALLS
        ]

        assert lonely == []

    def test_every_entry_names_what_it_was_compared_to(self) -> None:
        """A status alone is not evidence: each says what the sibling did."""
        for key, evidence in SELLER_ROUTED_GETS.items():
            assert "sibling" in evidence or "non-numeric" in evidence, key


class TestTheSandboxClassifiedTheUnverified47:
    """32 routed, 4 unrouted, 11 still unknown: 47 in all."""

    def test_the_counts_add_up_to_47(self) -> None:
        """Every one of the 47 landed in exactly one bucket."""
        operations = _operations()
        unverified = {
            key
            for key, operation in operations.items()
            if key[0] != "GET"
            and UNVERIFIED_NOTE.strip() in str(operation.get("description") or "")
        }
        unrouted = {
            normalise(e.method, e.path)
            for e in UNROUTED_OPERATIONS
            if e.method != "get"
        }

        assert len(SANDBOX_ROUTED_OPERATIONS) == 32
        assert len(unrouted) == 4
        assert len(unverified) == 11
        assert not (set(SANDBOX_ROUTED_OPERATIONS) & unrouted)
        assert not (set(SANDBOX_ROUTED_OPERATIONS) & unverified)
        assert not (unrouted & unverified)

    def test_only_non_get_operations_were_probed_this_way(self) -> None:
        """The sandbox probe answered the question the GET probe could not."""
        assert {method for method, _ in SANDBOX_ROUTED_OPERATIONS} <= {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }
        assert {e.method for e in UNROUTED_OPERATIONS} <= {"get", "put", "patch"}

    def test_every_routed_entry_names_its_discriminator(self) -> None:
        """A status alone is not evidence: each says what it was compared to."""
        for key, evidence in SANDBOX_ROUTED_OPERATIONS.items():
            assert "sibling" in evidence, key

    def test_a_routed_operation_lost_the_unverified_marker(self) -> None:
        """`PUT /pos/{id}` was unverified until the sandbox told it apart."""
        operation = _operations()[("PUT", "/pos/{}")]

        assert UNVERIFIED_NOTE.strip() not in str(operation.get("description") or "")

    def test_an_unrouted_operation_is_kept_and_says_so(self) -> None:
        """Removing a public method is a separate decision; the docstring warns."""
        doc = MercadoPagoClient.update_chargeback.__doc__ or ""

        assert "**Not routed.**" in doc
        assert "**Unverified.**" not in doc


def _sandbox_token() -> str:
    """Read the sandbox token from the environment, or skip.

    Returns:
        str: The `TEST-` access token.
    """
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        pytest.skip(f"{TOKEN_ENV} is not set")
    if not token.startswith("TEST-"):
        pytest.fail(f"{TOKEN_ENV} must be a sandbox (TEST-) token")
    return token


@pytest.mark.network
async def test_live_authenticated_user_validates() -> None:
    """The real `/users/me` still validates into the observed model."""
    token = _sandbox_token()
    http = HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    )
    async with http:
        user = await MercadoPagoClient(http).get_authenticated_user()

    assert isinstance(user, AuthenticatedUser)
    assert user.id is not None
    assert user.site_id is not None


@pytest.mark.network
async def test_live_unrouted_and_routed_answer_differently() -> None:
    """One unrouted and one routed operation, re-measured without effect.

    Both bodies are malformed JSON and the id does not exist, so neither
    request can succeed.
    """
    _sandbox_token()
    async with httpx.AsyncClient(base_url=DEFAULT_BASE_URL, timeout=30) as http:
        unrouted = await http.put(
            "/v1/payments/999999999999/cancellations",
            content=b"{",
            headers={"Content-Type": "application/json"},
        )
        routed = await http.put(
            "/v1/payments/999999999999",
            content=b"{",
            headers={"Content-Type": "application/json"},
        )

    assert unrouted.status_code == 404
    assert unrouted.json()["error"] == "resource not found"
    assert routed.status_code == 400

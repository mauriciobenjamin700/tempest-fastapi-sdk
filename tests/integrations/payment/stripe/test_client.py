"""The client: form-encoded writes, idempotent retries, typed errors.

Every test drives a real :class:`HTTPClient` over an
``httpx.MockTransport``, so the request that would reach Stripe is
inspected byte for byte — the encoding is the feature, and a mocked client
object would assert nothing about it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from tempest_fastapi_sdk.integrations.payment.stripe import (
    STRIPE_API_VERSION,
    STRIPE_IDEMPOTENCY_HEADER,
    StripeClient,
    StripeCustomer,
    StripeError,
    stripe_http_client,
)
from tempest_fastapi_sdk.utils.http_client import HTTPClient

CUSTOMER: dict[str, Any] = {
    "id": "cus_123",
    "object": "customer",
    "email": "ana@example.com",
    "metadata": {"order": "1042"},
}


class _Recorder:
    """Mock transport recording requests and replaying canned responses.

    Attributes:
        requests (list[httpx.Request]): Every request that was made.
    """

    def __init__(self, *responses: tuple[int, dict[str, Any]]) -> None:
        """Initialize the recorder.

        Args:
            *responses (tuple[int, dict[str, Any]]): ``(status, body)``
                pairs, replayed in order. The last one repeats.
        """
        self.requests: list[httpx.Request] = []
        self._responses = list(responses) or [(200, CUSTOMER)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer the next canned response.

        Args:
            request (httpx.Request): The outgoing request.

        Returns:
            httpx.Response: The canned response.
        """
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        status, body = self._responses[index]
        return httpx.Response(status, json=body)

    @property
    def last_form(self) -> dict[str, list[str]]:
        """Return the last request's body parsed as a form.

        Returns:
            dict[str, list[str]]: Field name to values.
        """
        return parse_qs(self.requests[-1].content.decode())


def _client(recorder: _Recorder) -> StripeClient:
    """Build a Stripe client wired to a recorder.

    Args:
        recorder (_Recorder): The transport double.

    Returns:
        StripeClient: The client under test.
    """
    http = HTTPClient(
        base_url="https://api.stripe.com",
        default_headers={"Authorization": "Bearer sk_test_x"},
        transport=httpx.MockTransport(recorder.handler),
    )
    return StripeClient(http)


class TestRequestShape:
    async def test_write_is_form_encoded_with_brackets(self) -> None:
        """Stripe takes no JSON on writes, and nests through brackets."""
        recorder = _Recorder()
        client = _client(recorder)

        await client.customers.create(
            {"email": "ana@example.com", "metadata": {"order": "1042"}}
        )

        assert recorder.last_form == {
            "email": ["ana@example.com"],
            "metadata[order]": ["1042"],
        }

    async def test_content_type_is_form(self) -> None:
        """httpx sets it from ``data=``; this pins that we pass ``data``."""
        recorder = _Recorder()
        client = _client(recorder)

        await client.customers.create({"email": "ana@example.com"})

        content_type = recorder.requests[-1].headers["content-type"]
        assert content_type.startswith("application/x-www-form-urlencoded")

    async def test_list_of_objects_reaches_the_wire_indexed(self) -> None:
        """A Checkout session's line items keep their order."""
        recorder = _Recorder(
            (200, {"id": "cs_1", "object": "checkout.session", "status": "open"})
        )
        client = _client(recorder)

        await client.checkout_sessions.create(
            {
                "mode": "payment",
                "line_items": [{"price": "price_1", "quantity": 2}],
            }
        )

        assert recorder.last_form["line_items[0][price]"] == ["price_1"]
        assert recorder.last_form["line_items[0][quantity]"] == ["2"]

    async def test_query_parameters_are_bracket_encoded_too(self) -> None:
        """``expand`` is a list, and Stripe reads it indexed."""
        recorder = _Recorder()
        client = _client(recorder)

        await client.customers.retrieve("cus_123", params={"expand": ["subscriptions"]})

        assert recorder.requests[-1].url.params["expand[0]"] == "subscriptions"

    async def test_nested_path_resource_keeps_its_segments(self) -> None:
        """``checkout/sessions`` is one resource with a slash in its path."""
        recorder = _Recorder(
            (200, {"id": "cs_1", "object": "checkout.session", "status": "open"})
        )
        client = _client(recorder)

        await client.checkout_sessions.retrieve("cs_1")

        assert recorder.requests[-1].url.path == "/v1/checkout/sessions/cs_1"


class TestIdempotency:
    async def test_writes_carry_a_key_by_default(self) -> None:
        """Retrying a charge without one is how a customer is billed twice."""
        recorder = _Recorder()
        client = _client(recorder)

        await client.customers.create({"email": "ana@example.com"})

        assert recorder.requests[-1].headers[STRIPE_IDEMPOTENCY_HEADER]

    async def test_caller_key_is_used_verbatim(self) -> None:
        """A caller keying on their own order id must see that id sent."""
        recorder = _Recorder()
        client = _client(recorder)

        await client.customers.create(
            {"email": "ana@example.com"}, idempotency_key="order-1042"
        )

        assert recorder.requests[-1].headers[STRIPE_IDEMPOTENCY_HEADER] == "order-1042"

    async def test_generated_keys_differ_between_calls(self) -> None:
        """Two deliberate creates are two customers, not one replayed."""
        recorder = _Recorder()
        client = _client(recorder)

        await client.customers.create({"email": "a@example.com"})
        await client.customers.create({"email": "b@example.com"})

        first = recorder.requests[0].headers[STRIPE_IDEMPOTENCY_HEADER]
        second = recorder.requests[1].headers[STRIPE_IDEMPOTENCY_HEADER]
        assert first != second

    async def test_reads_carry_no_key(self) -> None:
        """A GET has nothing to deduplicate."""
        recorder = _Recorder()
        client = _client(recorder)

        await client.customers.retrieve("cus_123")

        assert STRIPE_IDEMPOTENCY_HEADER not in recorder.requests[-1].headers


class TestResponses:
    async def test_response_validates_into_the_model(self) -> None:
        """Named fields are typed."""
        recorder = _Recorder()
        client = _client(recorder)

        customer = await client.customers.create({"email": "ana@example.com"})

        assert isinstance(customer, StripeCustomer)
        assert customer.id == "cus_123"
        assert customer.metadata == {"order": "1042"}

    async def test_unknown_fields_are_kept_not_dropped(self) -> None:
        """The models are thin on purpose; nothing may be lost."""
        recorder = _Recorder((200, {**CUSTOMER, "tax_exempt": "none"}))
        client = _client(recorder)

        customer = await client.customers.retrieve("cus_123")

        assert customer.model_extra is not None
        assert customer.model_extra["tax_exempt"] == "none"

    async def test_delete_returns_the_tombstone(self) -> None:
        """A delete answers the id and ``deleted: true``."""
        recorder = _Recorder(
            (200, {"id": "cus_123", "object": "customer", "deleted": True})
        )
        client = _client(recorder)

        deleted = await client.customers.delete("cus_123")

        assert deleted.deleted is True


class TestErrors:
    async def test_card_error_becomes_a_typed_exception(self) -> None:
        """A decline is the failure a payments integration handles most."""
        recorder = _Recorder(
            (
                402,
                {
                    "error": {
                        "type": "card_error",
                        "code": "card_declined",
                        "decline_code": "insufficient_funds",
                        "message": "Your card has insufficient funds.",
                        "param": "payment_method",
                    }
                },
            )
        )
        client = _client(recorder)

        with pytest.raises(StripeError) as error:
            await client.payment_intents.create({"amount": 1000, "currency": "brl"})

        assert error.value.status_code == 402
        assert error.value.error_type == "card_error"
        assert error.value.code == "card_declined"
        assert error.value.decline_code == "insufficient_funds"
        assert error.value.param == "payment_method"

    async def test_non_stripe_error_body_still_raises_with_the_status(self) -> None:
        """A proxy or a load balancer can answer something else entirely."""
        recorder = _Recorder((503, {"nope": True}))
        client = _client(recorder)

        with pytest.raises(StripeError) as error:
            await client.customers.retrieve("cus_123")

        assert error.value.status_code == 503


class TestPagination:
    async def test_list_returns_a_page(self) -> None:
        """One request, one page, typed items."""
        recorder = _Recorder(
            (200, {"object": "list", "data": [CUSTOMER], "has_more": False})
        )
        client = _client(recorder)

        page = await client.customers.list({"limit": 1})

        assert page.has_more is False
        assert page.data[0].id == "cus_123"

    async def test_auto_paginate_follows_the_cursor(self) -> None:
        """The cursor is the last item's id, not an offset."""
        first = {
            "object": "list",
            "data": [{**CUSTOMER, "id": "cus_1"}],
            "has_more": True,
        }
        second = {
            "object": "list",
            "data": [{**CUSTOMER, "id": "cus_2"}],
            "has_more": False,
        }
        recorder = _Recorder((200, first), (200, second))
        client = _client(recorder)

        seen = [customer.id async for customer in client.customers.auto_paginate()]

        assert seen == ["cus_1", "cus_2"]
        assert recorder.requests[1].url.params["starting_after"] == "cus_1"

    async def test_auto_paginate_stops_on_an_empty_page(self) -> None:
        """``has_more`` lying would otherwise loop forever."""
        recorder = _Recorder((200, {"object": "list", "data": [], "has_more": True}))
        client = _client(recorder)

        seen = [customer.id async for customer in client.customers.auto_paginate()]

        assert seen == []
        assert len(recorder.requests) == 1


class TestFactory:
    """The factory is checked through the wire, not through attributes.

    ``HTTPClient`` hands its default headers to the underlying transport
    rather than keeping them, so the only honest assertion is on the
    request that comes out.
    """

    async def _request_headers(self, **kwargs: Any) -> httpx.Headers:
        """Make one call through the factory and return its headers.

        Args:
            **kwargs (Any): Forwarded to :func:`stripe_http_client`.

        Returns:
            httpx.Headers: The headers of the request that was made.
        """
        recorder = _Recorder()
        http = stripe_http_client(
            "sk_test_x", transport=httpx.MockTransport(recorder.handler), **kwargs
        )
        await StripeClient(http).customers.retrieve("cus_123")
        return recorder.requests[-1].headers

    async def test_pins_the_api_version(self) -> None:
        """An account upgraded in the dashboard must not change our shapes."""
        headers = await self._request_headers()

        assert headers["Stripe-Version"] == STRIPE_API_VERSION

    async def test_sends_the_key_as_a_bearer(self) -> None:
        """One of the two schemes the specification declares."""
        headers = await self._request_headers()

        assert headers["Authorization"] == "Bearer sk_test_x"

    async def test_extra_headers_are_merged(self) -> None:
        """Connect platforms act on behalf of a connected account."""
        headers = await self._request_headers(
            extra_headers={"Stripe-Account": "acct_1"}
        )

        assert headers["Stripe-Account"] == "acct_1"


async def test_error_body_that_is_not_json_does_not_mask_the_status() -> None:
    """An HTML error page from an edge proxy still raises, with its status."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer with HTML instead of Stripe's envelope.

        Args:
            request (httpx.Request): The outgoing request.

        Returns:
            httpx.Response: A 502 carrying HTML.
        """
        return httpx.Response(502, text="<html>bad gateway</html>")

    http = HTTPClient(
        base_url="https://api.stripe.com",
        retry_policy=None,
        transport=httpx.MockTransport(handler),
    )
    client = StripeClient(http)

    with pytest.raises(StripeError) as error:
        await client.customers.retrieve("cus_123")

    assert error.value.status_code == 502

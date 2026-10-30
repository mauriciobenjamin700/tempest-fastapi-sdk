"""A typed async client for the Stripe resources a payments flow uses.

Hand-written, and that is a measured decision rather than a shortcut. The
SDK generates integrations from OpenAPI — that is how the OpenPix client
exists — but Stripe's specification does not survive the trip: generating
it yields 3.3 MB of schemas whose import costs 5.8 s and 492 MB of RSS,
and no resource subset is smaller, because ``/v1/prices`` alone reaches
864 of the 1440 component schemas. The numbers and the method are in
``scripts/regen_stripe.py``.

What is left is small and honest:

* one generic :class:`StripeResource` with the five verbs Stripe gives
  every resource, so adding one is a line rather than a file;
* request bodies as plain mappings, form-encoded by
  :func:`~tempest_fastapi_sdk.form_encode` — Stripe takes no JSON on
  writes, and its parameter surface is far too wide to retype;
* responses validated into the thin models in
  :mod:`~tempest_fastapi_sdk.integrations.payment.stripe.schemas`, which
  keep unknown fields rather than dropping them;
* an ``Idempotency-Key`` on every write, because retrying a charge
  without one is how a customer gets billed twice.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any, Generic
from urllib.parse import quote

from tempest_fastapi_sdk.integrations.payment.stripe.environment import (
    STRIPE_IDEMPOTENCY_HEADER,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    ResourceT,
    StripeCheckoutSession,
    StripeCustomer,
    StripeDeleted,
    StripeEventObject,
    StripeInvoice,
    StripeList,
    StripePaymentIntent,
    StripePrice,
    StripeProduct,
    StripeRefund,
    StripeSubscription,
)
from tempest_fastapi_sdk.utils.forms import form_encode
from tempest_fastapi_sdk.utils.http_client import HTTPClient


def _path_segment(resource_id: str) -> str:
    """Escape a Stripe object id into exactly one path segment.

    Args:
        resource_id (str): The object id as the caller passed it.

    Returns:
        str: The id percent-encoded with an empty ``safe`` set, so ``/``,
        ``?`` and ``#`` are escaped instead of adding a segment, a query or
        a fragment to the request.

    Raises:
        ValueError: When the id is empty, ``.`` or ``..``. Percent-encoding
            leaves the dot alone and the HTTP client resolves dot segments
            before sending, so ``/v1/customers/..`` would go out as a
            request to ``/v1`` with the same method and the secret key.

    Interpolating the id raw let it address a different object family or a
    sub-action under the same credentials — ``../subscriptions/sub_1``
    passed to ``customers.delete`` went out as ``DELETE
    /v1/subscriptions/sub_1``. The generated clients enforce the same rule
    through their ``_path_param``.
    """
    value = str(resource_id)
    if value in {"", ".", ".."}:
        raise ValueError(f"invalid Stripe object id: {value!r}")
    return quote(value, safe="")


class StripeError(RuntimeError):
    """Raised when Stripe answers with an error payload.

    Stripe's error body is a small, stable envelope — ``error.type``,
    ``error.code``, ``error.message``, and often ``error.param`` naming the
    field at fault. Surfacing those beats an ``HTTPStatusError`` whose text
    the caller has to re-parse.

    Attributes:
        status_code (int): The HTTP status.
        error_type (str): Stripe's ``error.type`` — ``card_error``,
            ``invalid_request_error``, ``api_error``, ``idempotency_error``,
            ``rate_limit_error``.
        code (str): Stripe's ``error.code``, e.g. ``card_declined``.
        param (str): The parameter Stripe blames, when it names one.
        decline_code (str): For a declined card, the issuer's reason.
        request_id (str): Stripe's request id, the one their support asks
            for.
        payload (dict[str, Any]): The whole decoded error body.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_type: str = "",
        code: str = "",
        param: str = "",
        decline_code: str = "",
        request_id: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the error.

        Args:
            message (str): Stripe's human-readable message.
            status_code (int): The HTTP status.
            error_type (str): Stripe's ``error.type``.
            code (str): Stripe's ``error.code``.
            param (str): The offending parameter, when named.
            decline_code (str): The issuer's decline reason.
            request_id (str): Stripe's request id.
            payload (Mapping[str, Any] | None): The decoded body.
        """
        super().__init__(message)
        self.status_code: int = status_code
        self.error_type: str = error_type
        self.code: str = code
        self.param: str = param
        self.decline_code: str = decline_code
        self.request_id: str = request_id
        self.payload: dict[str, Any] = dict(payload or {})


class StripeObjectMismatchError(StripeError):
    """Raised when a ``2xx`` body is not the kind of object the call asked for.

    Every Stripe object carries its type in ``object`` (``"customer"``,
    ``"payment_intent"``, ``"checkout.session"``), and a deleted one keeps
    the same value next to ``deleted: true``. A body whose ``object`` is
    missing or names another type did not come from the endpoint the
    resource addresses, so validating it into the resource's model would
    hand the caller another object's data under the wrong type.

    Subclasses :class:`StripeError`, so ``except StripeError`` keeps
    catching it. ``status_code`` is the (successful) status the body came
    with; ``error_type`` and ``code`` stay empty, since Stripe raised
    nothing.

    Attributes:
        expected_object (str): The ``object`` value the resource expects.
        received_object (str): The ``object`` value in the body; empty
            when the key is missing or not a string.
    """

    def __init__(
        self,
        *,
        expected_object: str,
        received_object: str,
        status_code: int,
        request_id: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the error.

        Args:
            expected_object (str): The ``object`` value the resource expects.
            received_object (str): The ``object`` value the body carried.
            status_code (int): The HTTP status of the response.
            request_id (str): Stripe's request id.
            payload (Mapping[str, Any] | None): The decoded body.
        """
        super().__init__(
            f"Stripe returned object {received_object!r} where "
            f"{expected_object!r} was expected",
            status_code=status_code,
            request_id=request_id,
            payload=payload,
        )
        self.expected_object: str = expected_object
        self.received_object: str = received_object


class StripeResource(Generic[ResourceT]):
    """The five calls Stripe gives every resource, typed to one model.

    Generic parameters:
        ResourceT: The model this resource returns.

    Attributes:
        path (str): Path segment under ``/v1`` — ``"customers"``,
            ``"checkout/sessions"``.
        model (type[ResourceT]): The model responses validate into.
        object_type (str | None): The ``object`` value every single-object
            response must carry (``"customer"``). :meth:`create`,
            :meth:`retrieve`, :meth:`update` and :meth:`delete` raise
            :class:`StripeObjectMismatchError` on any other value. ``None``
            skips the check.

    The parametrized page model (``StripeList[model]``) is built once in
    the constructor: subscripting a generic Pydantic model creates a new
    class each time, so doing it per call would rebuild a model on every
    listing.
    """

    def __init__(
        self,
        client: StripeClient,
        path: str,
        model: type[ResourceT],
        *,
        object_type: str | None = None,
    ) -> None:
        """Initialize the resource.

        Args:
            client (StripeClient): The owning client.
            path (str): Path segment under ``/v1``.
            model (type[ResourceT]): Model for responses.
            object_type (str | None): The ``object`` value single-object
                responses must carry. ``None`` skips the check.
        """
        self._client: StripeClient = client
        self.path: str = path
        self.model: type[ResourceT] = model
        self.object_type: str | None = object_type
        self._page_model: type[StripeList[ResourceT]] = StripeList[model]  # type: ignore[valid-type]

    async def create(
        self,
        params: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> ResourceT:
        """Create the resource.

        Args:
            params (Mapping[str, Any]): The request body, nested freely —
                ``{"metadata": {"order": "1042"}}`` becomes
                ``metadata[order]=1042``.
            idempotency_key (str | None): Key Stripe uses to collapse a
                retry onto the first attempt. ``None`` generates a UUID4,
                so a retried create never charges twice by accident.

        Returns:
            ResourceT: The created object.

        Raises:
            StripeObjectMismatchError: When the body's ``object`` is not
                :attr:`object_type`.
            StripeError: When Stripe rejects the request.
        """
        payload = await self._client.request(
            "POST",
            f"/v1/{self.path}",
            data=params,
            idempotency_key=idempotency_key,
            expected_object=self.object_type,
        )
        return self.model.model_validate(payload)

    async def retrieve(
        self,
        resource_id: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> ResourceT:
        """Read one object by id.

        Args:
            resource_id (str): The object id.
            params (Mapping[str, Any] | None): Query parameters —
                ``{"expand": ["customer"]}`` to inline an expandable field.

        Returns:
            ResourceT: The object.

        Raises:
            ValueError: When ``resource_id`` is empty, ``.`` or ``..``.
            StripeObjectMismatchError: When the body's ``object`` is not
                :attr:`object_type`.
            StripeError: When Stripe rejects the request, including the
                ``404`` for an unknown id.
        """
        payload = await self._client.request(
            "GET",
            f"/v1/{self.path}/{_path_segment(resource_id)}",
            params=params,
            expected_object=self.object_type,
        )
        return self.model.model_validate(payload)

    async def update(
        self,
        resource_id: str,
        params: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> ResourceT:
        """Update one object.

        Args:
            resource_id (str): The object id.
            params (Mapping[str, Any]): Fields to change.
            idempotency_key (str | None): See :meth:`create`.

        Returns:
            ResourceT: The updated object.

        Raises:
            ValueError: When ``resource_id`` is empty, ``.`` or ``..``.
            StripeObjectMismatchError: When the body's ``object`` is not
                :attr:`object_type`.
            StripeError: When Stripe rejects the request.
        """
        payload = await self._client.request(
            "POST",
            f"/v1/{self.path}/{_path_segment(resource_id)}",
            data=params,
            idempotency_key=idempotency_key,
            expected_object=self.object_type,
        )
        return self.model.model_validate(payload)

    async def delete(self, resource_id: str) -> StripeDeleted:
        """Delete one object.

        Args:
            resource_id (str): The object id.

        Returns:
            StripeDeleted: The id and the tombstone flag.

        Raises:
            ValueError: When ``resource_id`` is empty, ``.`` or ``..``.
            StripeObjectMismatchError: When the body's ``object`` is not
                :attr:`object_type` — Stripe's tombstone keeps the deleted
                object's type.
            StripeError: When Stripe rejects the request.
        """
        payload = await self._client.request(
            "DELETE",
            f"/v1/{self.path}/{_path_segment(resource_id)}",
            expected_object=self.object_type,
        )
        return StripeDeleted.model_validate(payload)

    async def list(
        self, params: Mapping[str, Any] | None = None
    ) -> StripeList[ResourceT]:
        """Read one page of objects.

        Args:
            params (Mapping[str, Any] | None): Query parameters —
                ``limit`` (1-100), ``starting_after``, and the resource's
                own filters.

        Returns:
            StripeList[ResourceT]: The page, with ``has_more`` telling you
            whether to ask again.

        Raises:
            StripeError: When Stripe rejects the request.
        """
        payload = await self._client.request("GET", f"/v1/{self.path}", params=params)
        return self._page_model.model_validate(payload)

    async def auto_paginate(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        page_size: int = 100,
    ) -> AsyncIterator[ResourceT]:
        """Walk every page, yielding objects one at a time.

        Args:
            params (Mapping[str, Any] | None): Filters applied to every
                page.
            page_size (int): Objects per request. Stripe caps this at 100.

        Yields:
            ResourceT: Each object, newest first, across every page.

        Raises:
            StripeError: When Stripe rejects any of the page requests.

        The cursor is the **last item's id**, not an offset, so pagination
        stays correct while objects are being created underneath it.
        """
        query: dict[str, Any] = {**(params or {}), "limit": page_size}
        while True:
            page = await self.list(query)
            for item in page.data:
                yield item
            if not page.has_more or not page.data:
                return
            query = {**query, "starting_after": page.data[-1].id}


class StripeClient:
    """Typed entry point for the Stripe resources a payments flow needs.

    Build it with an :class:`HTTPClient` from
    :func:`~tempest_fastapi_sdk.integrations.payment.stripe.stripe_http_client`,
    so retries, the circuit breaker and the pinned ``Stripe-Version`` come
    from one place and tests can inject an ``httpx.MockTransport``.

    Attributes:
        customers (StripeResource[StripeCustomer]): ``/v1/customers``.
        payment_intents (StripeResource[StripePaymentIntent]):
            ``/v1/payment_intents``.
        refunds (StripeResource[StripeRefund]): ``/v1/refunds``.
        products (StripeResource[StripeProduct]): ``/v1/products``.
        prices (StripeResource[StripePrice]): ``/v1/prices``.
        subscriptions (StripeResource[StripeSubscription]):
            ``/v1/subscriptions``.
        invoices (StripeResource[StripeInvoice]): ``/v1/invoices``.
        checkout_sessions (StripeResource[StripeCheckoutSession]):
            ``/v1/checkout/sessions``.
        events (StripeResource[StripeEventObject]): ``/v1/events``.
    """

    def __init__(self, client: HTTPClient) -> None:
        """Initialize the client.

        Args:
            client (HTTPClient): Configured transport. The caller owns its
                lifecycle, timeout, retry policy and auth headers.
        """
        self._client: HTTPClient = client
        self.customers: StripeResource[StripeCustomer] = StripeResource(
            self, "customers", StripeCustomer, object_type="customer"
        )
        self.payment_intents: StripeResource[StripePaymentIntent] = StripeResource(
            self, "payment_intents", StripePaymentIntent, object_type="payment_intent"
        )
        self.refunds: StripeResource[StripeRefund] = StripeResource(
            self, "refunds", StripeRefund, object_type="refund"
        )
        self.products: StripeResource[StripeProduct] = StripeResource(
            self, "products", StripeProduct, object_type="product"
        )
        self.prices: StripeResource[StripePrice] = StripeResource(
            self, "prices", StripePrice, object_type="price"
        )
        self.subscriptions: StripeResource[StripeSubscription] = StripeResource(
            self, "subscriptions", StripeSubscription, object_type="subscription"
        )
        self.invoices: StripeResource[StripeInvoice] = StripeResource(
            self, "invoices", StripeInvoice, object_type="invoice"
        )
        self.checkout_sessions: StripeResource[StripeCheckoutSession] = StripeResource(
            self,
            "checkout/sessions",
            StripeCheckoutSession,
            object_type="checkout.session",
        )
        self.events: StripeResource[StripeEventObject] = StripeResource(
            self, "events", StripeEventObject, object_type="event"
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        expected_object: str | None = None,
    ) -> dict[str, Any]:
        """Perform one Stripe call and return the decoded body.

        Args:
            method (str): HTTP method.
            path (str): Path starting with ``/v1``.
            params (Mapping[str, Any] | None): Query parameters. Nested
                values are bracket-flattened the same way bodies are, so
                ``{"expand": ["customer"]}`` becomes ``expand[0]=customer``.
            data (Mapping[str, Any] | None): Request body, form-encoded.
            idempotency_key (str | None): Value for the
                ``Idempotency-Key`` header on writes. ``None`` generates a
                UUID4 for every non-``GET``.
            expected_object (str | None): The ``object`` value a successful
                body must carry. ``None`` skips the check.

        Returns:
            dict[str, Any]: The decoded JSON body.

        Raises:
            StripeError: On any non-2xx response.
            StripeObjectMismatchError: When ``expected_object`` is given
                and the successful body's ``object`` differs or is missing.

        Writes carry an idempotency key **by default**. Stripe replays the
        original response for a repeated key within 24 hours, which is what
        makes the SDK's own retry policy safe here: without it, a timeout
        on ``POST /v1/payment_intents`` followed by a retry can create two
        payments.
        """
        headers: dict[str, str] = {}
        if method.upper() != "GET":
            headers[STRIPE_IDEMPOTENCY_HEADER] = idempotency_key or str(uuid.uuid4())

        response = await self._client.request(
            method,
            path,
            params=form_encode(params) or None,
            data=form_encode(data) if data is not None else None,
            headers=headers or None,
        )
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            raise _error_from(response.status_code, payload, response.headers)
        body: dict[str, Any] = (
            payload if isinstance(payload, dict) else {"data": payload}
        )
        if expected_object is not None:
            received = body.get("object")
            if received != expected_object:
                raise StripeObjectMismatchError(
                    expected_object=expected_object,
                    received_object=received if isinstance(received, str) else "",
                    status_code=response.status_code,
                    request_id=str(response.headers.get("Request-Id", "")),
                    payload=body,
                )
        return body


def _error_from(
    status_code: int, payload: Any, headers: Mapping[str, str]
) -> StripeError:
    """Build a :class:`StripeError` from an error response.

    Args:
        status_code (int): The HTTP status.
        payload (Any): The decoded body, which may not be a mapping when
            something upstream of Stripe answered.
        headers (Mapping[str, str]): Response headers, read for
            ``Request-Id``.

    Returns:
        StripeError: The typed error, falling back to the status alone
        when the body is not Stripe's envelope.
    """
    error: dict[str, Any] = {}
    if isinstance(payload, Mapping) and isinstance(payload.get("error"), Mapping):
        error = dict(payload["error"])
    message = str(error.get("message") or f"Stripe request failed ({status_code})")
    return StripeError(
        message,
        status_code=status_code,
        error_type=str(error.get("type", "")),
        code=str(error.get("code", "")),
        param=str(error.get("param", "")),
        decline_code=str(error.get("decline_code", "")),
        request_id=str(headers.get("Request-Id", "")),
        payload=payload if isinstance(payload, Mapping) else {},
    )


__all__: list[str] = [
    "StripeClient",
    "StripeError",
    "StripeObjectMismatchError",
    "StripeResource",
]

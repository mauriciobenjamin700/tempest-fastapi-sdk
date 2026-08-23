"""Typed HTTP client generated from the MercadoPago API OpenAPI spec.

Do not edit by hand — rerun `tempest openapi-client` to refresh.

The client wraps an injected ``HTTPClient``, so the caller keeps
control of the base URL, timeout, retry policy, circuit breaker and
auth headers. Pass an ``httpx.MockTransport`` through the client in
tests to exercise these methods without network access.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, EmailStr, TypeAdapter

from tempest_fastapi_sdk import HTTPClient

from .schemas import (
    AddOrderTransactionBody,
    AddOrderTransactionResponse,
    Address,
    AttachClaimFileResponse,
    AuthorizedPayment,
    AuthorizedPaymentSearchResult,
    AuthorizedPaymentStatus,
    CancelPaymentBody,
    CaptureOrderResponse,
    Card,
    CardToken,
    CardTokenRequest,
    Claim,
    ClaimEvidence,
    ClaimHistoryEntry,
    ClaimMessage,
    ClaimReason,
    ClaimSearchResult,
    ClaimStage,
    ClaimStatus,
    ClaimType,
    ConfirmCashoutQrBody,
    CreateAdvancedPaymentBody,
    CreateAdvancedPaymentResponse,
    CreateMerchantOrderBody,
    CreateMerchantOrderBodySiteId,
    CreateMerchantOrderResponse,
    CreateOAuthTokenBody,
    CreateOAuthTokenResponse,
    CreatePayoutBody,
    CreatePointPaymentIntentBody,
    CreatePointPaymentIntentResponse,
    CreatePointRefundIntentBody,
    CreatePointRefundIntentResponse,
    CreateQrIntegratorConfigBody,
    CreateRefundResponse,
    CreateStoreBody,
    CreateTerminalActionBody,
    CreateTerminalActionResponse,
    CreateWalletAgreementBody,
    CreateWalletAgreementResponse,
    CreateWalletDiscountBody,
    CreateWalletDiscountResponse,
    CreateWalletPayerTokenBody,
    CreateWalletPayerTokenResponse,
    Customer,
    CustomerRequest,
    CustomerSearchResult,
    ExportSubscriptionsSort,
    GetAdvancedPaymentResponse,
    GetChargebackResponse,
    GetClaimFileResponse,
    GetInstallmentsResponseItem,
    GetMerchantOrderResponse,
    GetPointRefundIntentResponse,
    GetQrIntegratorConfigResponse,
    GetRefundResponse,
    GetTerminalActionResponse,
    GetWalletAgreementResponse,
    ListIdentificationTypesResponseItem,
    ListPaymentMethodsResponseItem,
    ListPointDevicesResponse,
    ListRefundsResponse,
    ListTerminalsResponse,
    MediationResolution,
    MerchantOrderStatus,
    Order,
    OrderRefundRequest,
    OrderRequest,
    OrderRequestType,
    OrderSearchResult,
    OrderStatus,
    OrderTransactionPayment,
    Payment,
    PaymentRequest,
    PaymentSearchResult,
    PaymentUpdateRequest,
    Pos,
    PosRequest,
    Preference,
    PreferenceRequest,
    ProcessTransactionIntentBody,
    RefundOrderResponse,
    RefundRequest,
    ReportConfig,
    ReportListResult,
    ReportRequest,
    ReportTask,
    SaveCardRequest,
    SearchMerchantOrdersResponse,
    SearchPaymentsRange,
    SearchPosResponse,
    SearchPreferencesResponse,
    SearchStoresResponse,
    SearchSubscriptionPlansCriteria,
    SendMessageRequest,
    Store,
    StoreRequest,
    Subscription,
    SubscriptionPlan,
    SubscriptionPlanRequest,
    SubscriptionPlanStatus,
    SubscriptionRequest,
    SubscriptionRequestStatus,
    SubscriptionSearchResult,
    SubscriptionUpdateRequest,
    UpdateAdvancedPaymentBody,
    UpdateAdvancedPaymentResponse,
    UpdateCardRequest,
    UpdateChargebackBody,
    UpdateMerchantOrderBody,
    UpdateOrderTransactionBody,
    UpdateTerminalOperationModeBody,
    UploadShippingEvidenceBody,
    ValidateWalletCouponBody,
    ValidateWalletCouponResponse,
)

DEFAULT_BASE_URL: str = "https://api.mercadopago.com"
"""``servers[0].url`` from the specification."""


def _dump(payload: Any) -> Any:
    """Serialize a request body to JSON-ready data.

    Args:
        payload (Any): A generated schema instance, or already-plain
            data when the specification typed the body loosely.

    Returns:
        Any: ``model_dump(by_alias=True, mode="json")`` for a Pydantic
        model — the wire spelling the third party expects — and the
        value untouched for anything else.
    """
    if isinstance(payload, BaseModel):
        return payload.model_dump(by_alias=True, mode="json", exclude_none=True)
    return payload


_T = TypeVar("_T")
"""The response type a call was declared to return."""


def _validate(annotation: type[_T], data: Any) -> _T:
    """Validate a response body against the generated annotation.

    Args:
        annotation (type[_T]): The response type — a generated model,
            a ``list[Model]``, or a primitive.
        data (Any): The decoded JSON body.

    Returns:
        _T: The validated value. ``TypeAdapter`` is used rather than
        ``Model.model_validate`` so container and union annotations
        work through the same call site.

    Generic rather than ``-> Any``: every method returns this call's
    result, so an ``Any`` here made each one a
    ``no-any-return`` under a strict type checker — 98 of them on a
    real specification, in the consumer's own gate.
    """
    return TypeAdapter(annotation).validate_python(data)


def _param(value: Any) -> Any:
    """Normalize a query-parameter value for the wire.

    Args:
        value (Any): The argument as the caller passed it.

    Returns:
        Any: ``Enum`` members become their ``value`` and dates their
        ISO-8601 form; lists and tuples are normalized element-wise.
        Without this, an ``Enum`` would reach the query string through
        ``str()`` — which for the SDK's ``BaseStrEnum`` renders
        ``"Class.MEMBER"``, not the value the third party expects.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_param(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


class MercadoPagoClient:
    """Client for MercadoPago API (version 1.0.0)."""

    def __init__(self, client: HTTPClient) -> None:
        """Initialize the client.

        Args:
            client (HTTPClient): The transport to issue requests
                through. Build it with
                ``HTTPClient(base_url=DEFAULT_BASE_URL)`` to target
                the server the specification declares, and attach
                credentials via its ``default_headers``.
        """
        self._client: HTTPClient = client

    async def search_authorized_payments(
        self,
        *,
        preapproval_id: str | None = None,
        payment_id: int | None = None,
        payer_id: int | None = None,
        status: AuthorizedPaymentStatus | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> AuthorizedPaymentSearchResult:
        """Search subscription invoices.

        Search billing invoices generated by subscriptions.

        Args:
            preapproval_id (str | None): The preapproval_id value. Omitted from the
                query when None.
            payment_id (int | None): The payment_id value. Omitted from the query when
                None.
            payer_id (int | None): The payer_id value. Omitted from the query when None.
            status (AuthorizedPaymentStatus | None): The status value. Omitted from the
                query when None.
            limit (int | None): The limit value. Omitted from the query when None.
            offset (int | None): The offset value. Omitted from the query when None.

        Returns:
            AuthorizedPaymentSearchResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = "/authorized_payments"
        params: dict[str, Any] = {}
        if preapproval_id is not None:
            params["preapproval_id"] = _param(preapproval_id)
        if payment_id is not None:
            params["payment_id"] = _param(payment_id)
        if payer_id is not None:
            params["payer_id"] = _param(payer_id)
        if status is not None:
            params["status"] = _param(status)
        if limit is not None:
            params["limit"] = _param(limit)
        if offset is not None:
            params["offset"] = _param(offset)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(AuthorizedPaymentSearchResult, response.json())

    async def get_authorized_payment(
        self,
        id: int,
    ) -> AuthorizedPayment:
        """Get subscription invoice.

        Args:
            id (int): The id value.

        Returns:
            AuthorizedPayment: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = f"/authorized_payments/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(AuthorizedPayment, response.json())

    async def create_preference(
        self,
        *,
        body: PreferenceRequest,
    ) -> Preference:
        """Create a preference.

        Creates a Checkout Pro preference. The response contains `init_point`
        (production) and **Webhook events triggered:** payment, merchant_order

        Args:
            body (PreferenceRequest): The request body.

        Returns:
            Preference: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/checkout/preferences"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Preference, response.json())

    async def search_preferences(
        self,
        *,
        external_reference: str | None = None,
        marketplace: str | None = None,
        site_id: str | None = None,
        sponsor_id: int | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> SearchPreferencesResponse:
        """Search preferences.

        Args:
            external_reference (str | None): The external_reference value. Omitted from
                the query when None.
            marketplace (str | None): The marketplace value. Omitted from the query when
                None.
            site_id (str | None): The site_id value. Omitted from the query when None.
            sponsor_id (int | None): The sponsor_id value. Omitted from the query when
                None.
            limit (int | None): The limit value. Omitted from the query when None.
            offset (int | None): The offset value. Omitted from the query when None.

        Returns:
            SearchPreferencesResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/checkout/preferences/search"
        params: dict[str, Any] = {}
        if external_reference is not None:
            params["external_reference"] = _param(external_reference)
        if marketplace is not None:
            params["marketplace"] = _param(marketplace)
        if site_id is not None:
            params["site_id"] = _param(site_id)
        if sponsor_id is not None:
            params["sponsor_id"] = _param(sponsor_id)
        if limit is not None:
            params["limit"] = _param(limit)
        if offset is not None:
            params["offset"] = _param(offset)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(SearchPreferencesResponse, response.json())

    async def get_preference(
        self,
        id: str,
    ) -> Preference:
        """Get preference by ID.

        Args:
            id (str): The id value.

        Returns:
            Preference: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404.
        """
        path = f"/checkout/preferences/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Preference, response.json())

    async def update_preference(
        self,
        id: str,
        *,
        body: PreferenceRequest,
    ) -> Preference:
        """Update a preference.

        Args:
            id (str): The id value.
            body (PreferenceRequest): The request body.

        Returns:
            Preference: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/checkout/preferences/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Preference, response.json())

    async def put_checkout_preferences_by_id_expire(
        self,
        id: int,
    ) -> Preference:
        """Expires a Preference.

        Expires a payment preference. Enter the preference ID and it will be expired.

        Args:
            id (int): Preference ID

        Returns:
            Preference: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404, 500.
        """
        path = f"/checkout/preferences/{id}/expire"
        response = await self._client.request(
            "PUT",
            path,
        )
        response.raise_for_status()
        return _validate(Preference, response.json())

    async def get_qr_integrator_config(self) -> GetQrIntegratorConfigResponse:
        """Get QR integrator configuration.

        Returns:
            GetQrIntegratorConfigResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401.
        """
        path = "/instore/integrator"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetQrIntegratorConfigResponse, response.json())

    async def create_qr_integrator_config(
        self,
        *,
        body: CreateQrIntegratorConfigBody,
    ) -> None:
        """Create or update QR integrator configuration.

        Configures the integrator settings for QR in-store payments.

        Args:
            body (CreateQrIntegratorConfigBody): The request body.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/instore/integrator"
        payload = _dump(body)
        response = await self._client.request(
            "PATCH",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def create_qr_tramma_dynamic(
        self,
        user_id: int,
        external_pos_id: str,
        *,
        body: dict[str, Any],
    ) -> None:
        """Create a QR trama (deprecated Dynamic QR).

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM) **Migration guide:**
        https://www.mercadopago.com/developers/en/docs/qr-code/orders/create-order

        Args:
            user_id (int): The user_id value.
            external_pos_id (str): The external_pos_id value.
            body (dict[str, Any]): The request body.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = (
            f"//instore/orders/qr/seller/collectors/{user_id}/pos/{external_pos_id}/qrs"
        )
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def create_dynamic_qr_order(
        self,
        user_id: int,
        external_pos_id: str,
        *,
        body: dict[str, Any],
    ) -> None:
        """Create dynamic QR order (deprecated).

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM) **Migration guide:**
        https://www.mercadopago.com/developers/en/docs/qr-code/orders/create-order

        Args:
            user_id (int): The user_id value.
            external_pos_id (str): The external_pos_id value.
            body (dict[str, Any]): The request body.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = (
            f"//instore/orders/qr/seller/collectors/{user_id}/pos/{external_pos_id}/qrs"
        )
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def confirm_cashout_qr(
        self,
        merchant_order_id: str,
        *,
        body: ConfirmCashoutQrBody,
    ) -> None:
        """Confirm QR cashout status.

        Confirms the cashout status for a QR-based cash withdrawal order. **Available
        in:** Argentina, Brazil (MLA, MLB)

        Args:
            merchant_order_id (str): The merchant_order_id value.
            body (ConfirmCashoutQrBody): The request body.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = f"/instore/orders/{merchant_order_id}/confirmation"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def get_instore_order_v2(
        self,
        user_id: int,
        external_pos_id: str,
    ) -> None:
        """Get in-store order (deprecated V2).

        **Migration guide:**
        https://www.mercadopago.com/developers/en/docs/qr-code/orders/create-order

        Args:
            user_id (int): The user_id value.
            external_pos_id (str): The external_pos_id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/instore/qr/seller/collectors/{user_id}/pos/{external_pos_id}/orders"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def delete_instore_order_v2(
        self,
        user_id: int,
        external_pos_id: str,
    ) -> None:
        """Delete in-store order (deprecated V2).

        **Migration guide:**
        https://www.mercadopago.com/developers/en/docs/qr-code/orders/create-order

        Args:
            user_id (int): The user_id value.
            external_pos_id (str): The external_pos_id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/instore/qr/seller/collectors/{user_id}/pos/{external_pos_id}/orders"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def create_instore_order_v2(
        self,
        user_id: int,
        external_store_id: str,
        external_pos_id: str,
        *,
        body: dict[str, Any],
    ) -> None:
        """Create in-store order (deprecated V2).

        **Migration guide:**
        https://www.mercadopago.com/developers/en/docs/qr-code/orders/create-order

        Args:
            user_id (int): The user_id value.
            external_store_id (str): The external_store_id value.
            external_pos_id (str): The external_pos_id value.
            body (dict[str, Any]): The request body.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = (
            f"//instore/qr/seller/collectors/{user_id}/stores/{external_store_id}/pos"
            f"/{external_pos_id}/orders"
        )
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def create_merchant_order(
        self,
        *,
        body: CreateMerchantOrderBody,
    ) -> CreateMerchantOrderResponse:
        """Create a merchant order.

        **Webhook events triggered:** merchant_order

        Args:
            body (CreateMerchantOrderBody): The request body.

        Returns:
            CreateMerchantOrderResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/merchant_orders"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreateMerchantOrderResponse, response.json())

    async def search_merchant_orders(
        self,
        *,
        status: MerchantOrderStatus | None = None,
        preference_id: str | None = None,
        application_id: str | None = None,
        payer_id: int | None = None,
        sponsor_id: int | None = None,
        external_reference: str | None = None,
        site_id: CreateMerchantOrderBodySiteId | None = None,
        marketplace: str | None = None,
        date_created_from: datetime | None = None,
        date_created_to: datetime | None = None,
        last_updated_from: datetime | None = None,
        last_updated_to: datetime | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> SearchMerchantOrdersResponse:
        """Search merchant orders.

        Args:
            status (MerchantOrderStatus | None): The status value. Omitted from the
                query when None.
            preference_id (str | None): The preference_id value. Omitted from the query
                when None.
            application_id (str | None): The application_id value. Omitted from the
                query when None.
            payer_id (int | None): The payer_id value. Omitted from the query when None.
            sponsor_id (int | None): The sponsor_id value. Omitted from the query when
                None.
            external_reference (str | None): The external_reference value. Omitted from
                the query when None.
            site_id (CreateMerchantOrderBodySiteId | None): The site_id value. Omitted
                from the query when None.
            marketplace (str | None): The marketplace value. Omitted from the query when
                None.
            date_created_from (datetime | None): The date_created_from value. Omitted
                from the query when None.
            date_created_to (datetime | None): The date_created_to value. Omitted from
                the query when None.
            last_updated_from (datetime | None): The last_updated_from value. Omitted
                from the query when None.
            last_updated_to (datetime | None): The last_updated_to value. Omitted from
                the query when None.
            limit (int | None): The limit value. Omitted from the query when None.
            offset (int | None): The offset value. Omitted from the query when None.

        Returns:
            SearchMerchantOrdersResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/merchant_orders/search"
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = _param(status)
        if preference_id is not None:
            params["preference_id"] = _param(preference_id)
        if application_id is not None:
            params["application_id"] = _param(application_id)
        if payer_id is not None:
            params["payer_id"] = _param(payer_id)
        if sponsor_id is not None:
            params["sponsor_id"] = _param(sponsor_id)
        if external_reference is not None:
            params["external_reference"] = _param(external_reference)
        if site_id is not None:
            params["site_id"] = _param(site_id)
        if marketplace is not None:
            params["marketplace"] = _param(marketplace)
        if date_created_from is not None:
            params["date_created_from"] = _param(date_created_from)
        if date_created_to is not None:
            params["date_created_to"] = _param(date_created_to)
        if last_updated_from is not None:
            params["last_updated_from"] = _param(last_updated_from)
        if last_updated_to is not None:
            params["last_updated_to"] = _param(last_updated_to)
        if limit is not None:
            params["limit"] = _param(limit)
        if offset is not None:
            params["offset"] = _param(offset)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(SearchMerchantOrdersResponse, response.json())

    async def get_merchant_order(
        self,
        id: int,
    ) -> GetMerchantOrderResponse:
        """Get merchant order.

        Args:
            id (int): The id value.

        Returns:
            GetMerchantOrderResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/merchant_orders/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetMerchantOrderResponse, response.json())

    async def update_merchant_order(
        self,
        id: int,
        *,
        body: UpdateMerchantOrderBody,
    ) -> None:
        """Update merchant order.

        Args:
            id (int): The id value.
            body (UpdateMerchantOrderBody): The request body.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = f"/merchant_orders/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def create_instore_order_v1(
        self,
        user_id: int,
        external_id: str,
        *,
        body: dict[str, Any],
    ) -> None:
        """Create in-store order (deprecated V1).

        **Migration guide:**
        https://www.mercadopago.com/developers/en/docs/qr-code/orders/create-order

        Args:
            user_id (int): The user_id value.
            external_id (str): The external_id value.
            body (dict[str, Any]): The request body.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/mpmobile/instore/qr/{user_id}/{external_id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def delete_instore_order_v1(
        self,
        user_id: int,
        external_id: str,
    ) -> None:
        """Delete in-store order (deprecated V1).

        **Migration guide:**
        https://www.mercadopago.com/developers/en/docs/qr-code/orders/create-order

        Args:
            user_id (int): The user_id value.
            external_id (str): The external_id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/mpmobile/instore/qr/{user_id}/{external_id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def create_o_auth_token(
        self,
        *,
        body: CreateOAuthTokenBody,
    ) -> CreateOAuthTokenResponse:
        """Create OAuth token.

        Exchanges authorization codes for access tokens, refreshes expired tokens, or
        requests client credentials tokens for machine-to-machine flows. **Security
        notes:** - The `state` parameter is mandatory for authorization_code flows to
        prevent CSRF. - Store refresh tokens in server-side encrypted storage — never in
        localStorage. - Rotate `client_secret` regularly and store in a secrets manager.

        Args:
            body (CreateOAuthTokenBody): The request body.

        Returns:
            CreateOAuthTokenResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429.
        """
        path = "/oauth/token"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreateOAuthTokenResponse, response.json())

    async def list_point_devices(self) -> ListPointDevicesResponse:
        """List Point devices.

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Returns:
            ListPointDevicesResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/point/integration-api/devices"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ListPointDevicesResponse, response.json())

    async def create_point_payment_intent(
        self,
        deviceid: str,
        *,
        body: CreatePointPaymentIntentBody,
    ) -> CreatePointPaymentIntentResponse:
        """Create a payment intent on a Point device.

        Creates a payment intent that is sent to a Point POS device for the customer to
        tap/insert their card. The device handles card capture. **Available in:**
        Argentina, Brazil, Mexico (MLA, MLB, MLM) **Idempotent:** Supports
        `X-Idempotency-Key` header to safely retry without duplicate charges. **Webhook
        events triggered:** point_integration_wh

        Args:
            deviceid (str): The deviceid value.
            body (CreatePointPaymentIntentBody): The request body.

        Returns:
            CreatePointPaymentIntentResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/point/integration-api/devices/{deviceid}/payment-intents"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreatePointPaymentIntentResponse, response.json())

    async def cancel_point_payment_intent(
        self,
        deviceid: str,
        paymentintentid: str,
    ) -> None:
        """Cancel a payment intent.

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            deviceid (str): The deviceid value.
            paymentintentid (str): The paymentintentid value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = (
            f"//point/integration-api/devices/{deviceid}/payment-intents"
            f"/{paymentintentid}"
        )
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def create_point_refund_intent(
        self,
        deviceid: str,
        *,
        body: CreatePointRefundIntentBody,
    ) -> CreatePointRefundIntentResponse:
        """Create a refund intent on a terminal.

        Initiates a refund intent on a Point terminal device.

        Args:
            deviceid (str): Terminal device identifier
            body (CreatePointRefundIntentBody): The request body.

        Returns:
            CreatePointRefundIntentResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/point/integration-api/devices/{deviceid}/refund"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreatePointRefundIntentResponse, response.json())

    async def cancel_point_refund_intent(
        self,
        deviceid: str,
        refundintentid: str,
    ) -> None:
        """Cancel a refund intent on a terminal.

        Args:
            deviceid (str): The deviceid value.
            refundintentid (str): The refundintentid value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/point/integration-api/devices/{deviceid}/refund/{refundintentid}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def get_point_payment_intent(
        self,
        paymentintentid: str,
    ) -> None:
        """Get payment intent details.

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            paymentintentid (str): The paymentintentid value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/point/integration-api/payment-intents/{paymentintentid}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def get_point_refund_intent(
        self,
        refundintentid: str,
    ) -> GetPointRefundIntentResponse:
        """Get a refund intent status.

        Args:
            refundintentid (str): Refund intent identifier

        Returns:
            GetPointRefundIntentResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/point/integration-api/refund/{refundintentid}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetPointRefundIntentResponse, response.json())

    async def search_pos(
        self,
        *,
        external_id: str | None = None,
        external_store_id: str | None = None,
        store_id: str | None = None,
        category: int | None = None,
    ) -> SearchPosResponse:
        """Search points of sale.

        Args:
            external_id (str | None): The external_id value. Omitted from the query when
                None.
            external_store_id (str | None): The external_store_id value. Omitted from
                the query when None.
            store_id (str | None): The store_id value. Omitted from the query when None.
            category (int | None): The category value. Omitted from the query when None.

        Returns:
            SearchPosResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/pos"
        params: dict[str, Any] = {}
        if external_id is not None:
            params["external_id"] = _param(external_id)
        if external_store_id is not None:
            params["external_store_id"] = _param(external_store_id)
        if store_id is not None:
            params["store_id"] = _param(store_id)
        if category is not None:
            params["category"] = _param(category)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(SearchPosResponse, response.json())

    async def create_pos(
        self,
        *,
        body: PosRequest,
    ) -> Pos:
        """Create a point of sale.

        Creates a point of sale in a store to receive payments for products or services.
        Each POS will have a unique QR code linked to it.

        Args:
            body (PosRequest): The request body.

        Returns:
            Pos: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/pos"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Pos, response.json())

    async def get_pos(
        self,
        id: str,
    ) -> Pos:
        """Get a point of sale.

        Args:
            id (str): POS identifier

        Returns:
            Pos: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/pos/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Pos, response.json())

    async def update_pos(
        self,
        id: str,
        *,
        body: PosRequest,
    ) -> Pos:
        """Update a point of sale.

        Args:
            id (str): The id value.
            body (PosRequest): The request body.

        Returns:
            Pos: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/pos/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Pos, response.json())

    async def delete_pos(
        self,
        id: str,
    ) -> None:
        """Delete a point of sale.

        Args:
            id (str): POS identifier to delete

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/pos/{id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def get_claim_reasons(
        self,
        reason_id: str,
    ) -> ClaimReason:
        """Get claim reason.

        Returns the description and metadata for a specific claim reason code.

        Args:
            reason_id (str): The reason_id value.

        Returns:
            ClaimReason: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/post-purchase/v1/claims/reasons/{reason_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ClaimReason, response.json())

    async def search_claims(
        self,
        *,
        id: int | None = None,
        type: ClaimType | None = None,
        stage: ClaimStage | None = None,
        status: ClaimStatus | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> ClaimSearchResult:
        """Search claims.

        Args:
            id (int | None): The id value. Omitted from the query when None.
            type (ClaimType | None): The type value. Omitted from the query when None.
            stage (ClaimStage | None): The stage value. Omitted from the query when
                None.
            status (ClaimStatus | None): The status value. Omitted from the query when
                None.
            limit (int | None): The limit value. Omitted from the query when None.
            offset (int | None): The offset value. Omitted from the query when None.

        Returns:
            ClaimSearchResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401.
        """
        path = "/post-purchase/v1/claims/search"
        params: dict[str, Any] = {}
        if id is not None:
            params["id"] = _param(id)
        if type is not None:
            params["type"] = _param(type)
        if stage is not None:
            params["stage"] = _param(stage)
        if status is not None:
            params["status"] = _param(status)
        if limit is not None:
            params["limit"] = _param(limit)
        if offset is not None:
            params["offset"] = _param(offset)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(ClaimSearchResult, response.json())

    async def get_claim(
        self,
        claim_id: int,
    ) -> Claim:
        """Get claim details.

        Returns full details of a post-sale claim including status, stage, and players.

        Args:
            claim_id (int): The claim_id value.

        Returns:
            Claim: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 404.
        """
        path = f"/post-purchase/v1/claims/{claim_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Claim, response.json())

    async def upload_shipping_evidence(
        self,
        claim_id: int,
        *,
        body: UploadShippingEvidenceBody,
    ) -> None:
        """Upload shipping evidence.

        Uploads shipping evidence (tracking code, proof of delivery) to support the
        seller's case in a claim. Accepted as part of the claims resolution process.

        Args:
            claim_id (int): The claim_id value.
            body (UploadShippingEvidenceBody): The request body.

        Returns:
            None: Nothing — the operation answers 201 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/actions/evidences"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def request_claim_mediation(
        self,
        claim_id: int,
    ) -> None:
        """Request claim mediation.

        Escalates a claim to mediation, requesting MP to intervene in the dispute.

        Args:
            claim_id (int): The claim_id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/actions/open-dispute"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return None

    async def send_claim_message(
        self,
        claim_id: int,
        *,
        body: SendMessageRequest,
        application_id: str | None = None,
    ) -> None:
        """Send a message in a claim.

        Sends a text message (with optional attachments) in the claim thread.

        Args:
            claim_id (int): The claim_id value.
            body (SendMessageRequest): The request body.
            application_id (str | None): The application_id value. Omitted from the
                query when None.

        Returns:
            None: Nothing — the operation answers 201 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/actions/send-message"
        params: dict[str, Any] = {}
        if application_id is not None:
            params["application_id"] = _param(application_id)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            params=params,
            json=payload,
        )
        response.raise_for_status()
        return None

    # openapi: unsupported — request body of AttachClaimFile uses multipart/form-data —
    #   only application/json and application/x-www-form-urlencoded are modelled
    async def attach_claim_file(
        self,
        claim_id: int,
    ) -> AttachClaimFileResponse:
        """Attach a file to a claim message.

        Uploads and attaches a file (image, PDF) to a claim. Supported formats: JPEG,
        PNG, PDF. Max size: 10 MB.

        Args:
            claim_id (int): The claim_id value.

        Returns:
            AttachClaimFileResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/attachments"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(AttachClaimFileResponse, response.json())

    async def get_claim_file(
        self,
        claim_id: int,
        file_name: str,
    ) -> GetClaimFileResponse:
        """Get attached file metadata.

        Args:
            claim_id (int): The claim_id value.
            file_name (str): The fileName value.

        Returns:
            GetClaimFileResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/attachments/{file_name}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetClaimFileResponse, response.json())

    # openapi: unsupported — response of DownloadClaimFile uses application/octet-stream
    #   — only application/json is modelled
    async def download_claim_file(
        self,
        claim_id: int,
        file_name: str,
    ) -> None:
        """Download an attached file.

        Args:
            claim_id (int): The claim_id value.
            file_name (str): The fileName value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/attachments/{file_name}/download"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def get_claim_evidence(
        self,
        claim_id: int,
    ) -> list[ClaimEvidence]:
        """Get claim evidence.

        Args:
            claim_id (int): The claim_id value.

        Returns:
            list[ClaimEvidence]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/evidences"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(list[ClaimEvidence], response.json())

    async def get_claim_mediation_resolutions(
        self,
        claim_id: int,
    ) -> list[MediationResolution]:
        """Get expected mediation resolutions.

        Returns the possible resolution options for a claim at the mediation stage.

        Args:
            claim_id (int): The claim_id value.

        Returns:
            list[MediationResolution]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/expected-resolutions"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(list[MediationResolution], response.json())

    async def get_claim_messages(
        self,
        claim_id: int,
    ) -> list[ClaimMessage]:
        """Get claim messages.

        Args:
            claim_id (int): The claim_id value.

        Returns:
            list[ClaimMessage]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/messages"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(list[ClaimMessage], response.json())

    async def get_claim_history(
        self,
        claim_id: int,
    ) -> list[ClaimHistoryEntry]:
        """Get claim status history.

        Args:
            claim_id (int): The claim_id value.

        Returns:
            list[ClaimHistoryEntry]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/post-purchase/v1/claims/{claim_id}/status_history"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(list[ClaimHistoryEntry], response.json())

    async def create_subscription(
        self,
        *,
        body: SubscriptionRequest,
    ) -> Subscription:
        """Create a subscription.

        Args:
            body (SubscriptionRequest): The request body.

        Returns:
            Subscription: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 500.
        """
        path = "/preapproval"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Subscription, response.json())

    # openapi: unsupported — response of ExportSubscriptions uses text/csv — only
    #   application/json is modelled
    async def export_subscriptions(
        self,
        *,
        collector_id: int,
        preapproval_plan_id: str | None = None,
        status: SubscriptionRequestStatus | None = None,
        sort: ExportSubscriptionsSort | None = None,
    ) -> None:
        """Export subscriptions.

        Exports a list of subscriptions for a collector as a downloadable file. Filter
        by plan ID, status, and sort order.

        Args:
            collector_id (int): Collector (seller) user ID
            preapproval_plan_id (str | None): Filter by plan ID Omitted from the query
                when None.
            status (SubscriptionRequestStatus | None): The status value. Omitted from
                the query when None.
            sort (ExportSubscriptionsSort | None): The sort value. Omitted from the
                query when None.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/preapproval/export"
        params: dict[str, Any] = {}
        params["collector_id"] = _param(collector_id)
        if preapproval_plan_id is not None:
            params["preapproval_plan_id"] = _param(preapproval_plan_id)
        if status is not None:
            params["status"] = _param(status)
        if sort is not None:
            params["sort"] = _param(sort)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return None

    async def search_subscriptions(
        self,
        *,
        q: str | None = None,
        payer_id: int | None = None,
        payer_email: EmailStr | None = None,
        preapproval_plan_id: str | None = None,
        transaction_amount: float | None = None,
        semaphore: str | None = None,
        status: SubscriptionRequestStatus | None = None,
        sort: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> SubscriptionSearchResult:
        """Search subscriptions.

        Args:
            q (str | None): The q value. Omitted from the query when None.
            payer_id (int | None): The payer_id value. Omitted from the query when None.
            payer_email (EmailStr | None): The payer_email value. Omitted from the query
                when None.
            preapproval_plan_id (str | None): The preapproval_plan_id value. Omitted
                from the query when None.
            transaction_amount (float | None): The transaction_amount value. Omitted
                from the query when None.
            semaphore (str | None): The semaphore value. Omitted from the query when
                None.
            status (SubscriptionRequestStatus | None): The status value. Omitted from
                the query when None.
            sort (str | None): The sort value. Omitted from the query when None.
            limit (int | None): The limit value. Omitted from the query when None.
            offset (int | None): The offset value. Omitted from the query when None.

        Returns:
            SubscriptionSearchResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = "/preapproval/search"
        params: dict[str, Any] = {}
        if q is not None:
            params["q"] = _param(q)
        if payer_id is not None:
            params["payer_id"] = _param(payer_id)
        if payer_email is not None:
            params["payer_email"] = _param(payer_email)
        if preapproval_plan_id is not None:
            params["preapproval_plan_id"] = _param(preapproval_plan_id)
        if transaction_amount is not None:
            params["transaction_amount"] = _param(transaction_amount)
        if semaphore is not None:
            params["semaphore"] = _param(semaphore)
        if status is not None:
            params["status"] = _param(status)
        if sort is not None:
            params["sort"] = _param(sort)
        if limit is not None:
            params["limit"] = _param(limit)
        if offset is not None:
            params["offset"] = _param(offset)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(SubscriptionSearchResult, response.json())

    async def get_subscription(
        self,
        id: str,
    ) -> Subscription:
        """Get subscription.

        Args:
            id (str): The id value.

        Returns:
            Subscription: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403, 404, 500.
        """
        path = f"/preapproval/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Subscription, response.json())

    async def update_subscription(
        self,
        id: str,
        *,
        body: SubscriptionUpdateRequest,
    ) -> Subscription:
        """Update subscription.

        Update subscription status or billing details. Common use: `{"status":
        "paused"}` to pause, `{"status": "authorized"}` to resume, `{"status":
        "cancelled"}` to cancel.

        Args:
            id (str): The id value.
            body (SubscriptionUpdateRequest): The request body.

        Returns:
            Subscription: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = f"/preapproval/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Subscription, response.json())

    async def create_subscription_plan(
        self,
        *,
        body: SubscriptionPlanRequest,
    ) -> SubscriptionPlan:
        """Create a subscription plan.

        Creates a recurring billing plan. Individual subscriptions reference this plan.
        The response includes `init_point` to send subscribers to authorize billing.

        Args:
            body (SubscriptionPlanRequest): The request body.

        Returns:
            SubscriptionPlan: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 500.
        """
        path = "/preapproval_plan"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(SubscriptionPlan, response.json())

    async def search_subscription_plans(
        self,
        *,
        status: SubscriptionPlanStatus | None = None,
        q: str | None = None,
        sort: str | None = None,
        criteria: SearchSubscriptionPlansCriteria | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> SubscriptionSearchResult:
        """Search subscription plans.

        Args:
            status (SubscriptionPlanStatus | None): The status value. Omitted from the
                query when None.
            q (str | None): The q value. Omitted from the query when None.
            sort (str | None): The sort value. Omitted from the query when None.
            criteria (SearchSubscriptionPlansCriteria | None): The criteria value.
                Omitted from the query when None.
            limit (int | None): The limit value. Omitted from the query when None.
            offset (int | None): The offset value. Omitted from the query when None.

        Returns:
            SubscriptionSearchResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = "/preapproval_plan/search"
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = _param(status)
        if q is not None:
            params["q"] = _param(q)
        if sort is not None:
            params["sort"] = _param(sort)
        if criteria is not None:
            params["criteria"] = _param(criteria)
        if limit is not None:
            params["limit"] = _param(limit)
        if offset is not None:
            params["offset"] = _param(offset)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(SubscriptionSearchResult, response.json())

    async def get_subscription_plan(
        self,
        id: str,
    ) -> SubscriptionPlan:
        """Get subscription plan.

        Args:
            id (str): The id value.

        Returns:
            SubscriptionPlan: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 404, 500.
        """
        path = f"/preapproval_plan/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(SubscriptionPlan, response.json())

    async def update_subscription_plan(
        self,
        id: str,
        *,
        body: SubscriptionPlanRequest,
    ) -> SubscriptionPlan:
        """Update a subscription plan.

        Args:
            id (str): The id value.
            body (SubscriptionPlanRequest): The request body.

        Returns:
            SubscriptionPlan: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 500.
        """
        path = f"/preapproval_plan/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(SubscriptionPlan, response.json())

    async def get_store(
        self,
        id: str,
    ) -> Store:
        """Get store by ID.

        Args:
            id (str): Store ID

        Returns:
            Store: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/stores/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Store, response.json())

    async def create_terminal_action(
        self,
        *,
        body: CreateTerminalActionBody,
    ) -> CreateTerminalActionResponse:
        """Create a terminal print action.

        Sends a print action to a Point terminal — either a receipt image or a DTE
        (electronic tax document, available in Chile/MLC only). Set `type` to
        `PRINT_INFO` for image printing or `PRINT_DTE` for DTE. **Available in:**
        Argentina, Brazil, Chile, Mexico (MLA, MLB, MLC, MLM)

        Args:
            body (CreateTerminalActionBody): The request body.

        Returns:
            CreateTerminalActionResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429.
        """
        path = "/terminals/v1/actions"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreateTerminalActionResponse, response.json())

    async def get_terminal_action(
        self,
        action_id: str,
    ) -> GetTerminalActionResponse:
        """Get terminal action status.

        **Available in:** Argentina, Brazil, Chile, Mexico (MLA, MLB, MLC, MLM)

        Args:
            action_id (str): The action_id value.

        Returns:
            GetTerminalActionResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/terminals/v1/actions/{action_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetTerminalActionResponse, response.json())

    async def cancel_terminal_action(
        self,
        action_id: str,
    ) -> None:
        """Cancel a terminal action.

        **Available in:** Argentina, Brazil, Chile, Mexico (MLA, MLB, MLC, MLM)

        Args:
            action_id (str): The action_id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/terminals/v1/actions/{action_id}/cancel"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return None

    async def list_terminals(self) -> ListTerminalsResponse:
        """Get list of terminals.

        Returns all Point hardware terminals registered to the account. **Available
        in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Returns:
            ListTerminalsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401.
        """
        path = "/terminals/v1/list"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ListTerminalsResponse, response.json())

    async def update_terminal_operation_mode(
        self,
        *,
        body: UpdateTerminalOperationModeBody,
    ) -> None:
        """Update terminal operation mode.

        Changes the operating mode of one or more terminals. PDV — integrated POS mode
        (connected to your system); STANDALONE — independent mode (no system
        integration). **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            body (UpdateTerminalOperationModeBody): The request body.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429.
        """
        path = "/terminals/v1/setup"
        payload = _dump(body)
        response = await self._client.request(
            "PATCH",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def list_pos(
        self,
        user_id: int,
    ) -> None:
        """List POS devices for a user.

        Args:
            user_id (int): The user_id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/users/{user_id}/pos"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def create_store(
        self,
        user_id: int,
        *,
        body: CreateStoreBody,
    ) -> None:
        """Create a store.

        Args:
            user_id (int): The user_id value.
            body (CreateStoreBody): The request body.

        Returns:
            None: Nothing — the operation answers 201 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/users/{user_id}/stores"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def search_stores(
        self,
        user_id: int,
        *,
        external_id: str | None = None,
    ) -> SearchStoresResponse:
        """Search stores.

        Args:
            user_id (int): The user_id value.
            external_id (str | None): Filter by your external store ID Omitted from the
                query when None.

        Returns:
            SearchStoresResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/users/{user_id}/stores/search"
        params: dict[str, Any] = {}
        if external_id is not None:
            params["external_id"] = _param(external_id)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(SearchStoresResponse, response.json())

    async def update_store(
        self,
        user_id: int,
        id: str,
        *,
        body: StoreRequest,
    ) -> Store:
        """Update a store.

        Args:
            user_id (int): The user_id value.
            id (str): The id value.
            body (StoreRequest): The request body.

        Returns:
            Store: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/users/{user_id}/stores/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Store, response.json())

    async def delete_store(
        self,
        user_id: int,
        id: str,
    ) -> None:
        """Delete a store.

        Args:
            user_id (int): The user_id value.
            id (str): The id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/users/{user_id}/stores/{id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def get_release_report(self) -> ReportListResult:
        """Get releases report list.

        Returns:
            ReportListResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/release_report"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ReportListResult, response.json())

    async def create_release_report(
        self,
        *,
        body: ReportRequest,
    ) -> ReportTask:
        """Create a releases report.

        Generates a one-time releases report for the specified date range. Returns a
        task ID to poll for completion via the task endpoint.

        Args:
            body (ReportRequest): The request body.

        Returns:
            ReportTask: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429.
        """
        path = "/v1/account/release_report"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(ReportTask, response.json())

    async def get_release_report_config(self) -> ReportConfig:
        """Get releases report configuration.

        Returns:
            ReportConfig: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/release_report/config"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ReportConfig, response.json())

    async def create_release_report_config(
        self,
        *,
        body: ReportConfig,
    ) -> ReportConfig:
        """Create releases report configuration.

        Creates the configuration for automatic releases report generation. Defines
        columns, schedule frequency, file format, and optional SFTP delivery.

        Args:
            body (ReportConfig): The request body.

        Returns:
            ReportConfig: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429.
        """
        path = "/v1/account/release_report/config"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(ReportConfig, response.json())

    async def update_release_report_config(
        self,
        *,
        body: ReportConfig,
    ) -> ReportConfig:
        """Update releases report configuration.

        Args:
            body (ReportConfig): The request body.

        Returns:
            ReportConfig: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/release_report/config"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(ReportConfig, response.json())

    async def list_scheduled_release_reports(self) -> ReportListResult:
        """List scheduled releases reports.

        Returns:
            ReportListResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/release_report/list"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ReportListResult, response.json())

    async def enable_release_report_schedule(self) -> None:
        """Enable automatic releases report generation.

        Enables scheduled report generation based on the configured frequency.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401.
        """
        path = "/v1/account/release_report/schedule"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return None

    async def disable_release_report_schedule(self) -> None:
        """Disable automatic releases report generation.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/release_report/schedule"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def search_release_reports(
        self,
        *,
        begin_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> ReportListResult:
        """Search releases reports.

        Args:
            begin_date (datetime | None): The begin_date value. Omitted from the query
                when None.
            end_date (datetime | None): The end_date value. Omitted from the query when
                None.

        Returns:
            ReportListResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/release_report/search"
        params: dict[str, Any] = {}
        if begin_date is not None:
            params["begin_date"] = _param(begin_date)
        if end_date is not None:
            params["end_date"] = _param(end_date)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(ReportListResult, response.json())

    async def get_release_report_task(
        self,
        task_id: str,
    ) -> ReportTask:
        """Get releases report task status.

        Polls the status of a report generation task. Check until `status=done`.

        Args:
            task_id (str): The task_id value.

        Returns:
            ReportTask: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/v1/account/release_report/task/{task_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ReportTask, response.json())

    # openapi: unsupported — response of DownloadReleaseReport uses text/csv — only
    #   application/json is modelled
    async def download_release_report(
        self,
        file_name: str,
    ) -> None:
        """Download a releases report file.

        Downloads the generated report CSV file by filename.

        Args:
            file_name (str): The file_name value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/v1/account/release_report/{file_name}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def get_settlement_report(self) -> ReportListResult:
        """Get settlements report list.

        Returns:
            ReportListResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/settlement_report"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ReportListResult, response.json())

    async def create_settlement_report(
        self,
        *,
        body: ReportRequest,
    ) -> ReportTask:
        """Create a settlements report.

        Generates a one-time all-transactions report for the specified date range.
        Returns a task ID to poll for completion.

        Args:
            body (ReportRequest): The request body.

        Returns:
            ReportTask: The 202 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429.
        """
        path = "/v1/account/settlement_report"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(ReportTask, response.json())

    async def get_settlement_report_config(self) -> ReportConfig:
        """Get settlements report configuration.

        Returns:
            ReportConfig: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/settlement_report/config"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ReportConfig, response.json())

    async def create_settlement_report_config(
        self,
        *,
        body: ReportConfig,
    ) -> ReportConfig:
        """Create settlements report configuration.

        Creates the configuration for automatic all-transactions (settlements) report
        generation. Defines columns, schedule frequency, file format, and optional SFTP
        delivery.

        Args:
            body (ReportConfig): The request body.

        Returns:
            ReportConfig: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429.
        """
        path = "/v1/account/settlement_report/config"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(ReportConfig, response.json())

    async def update_settlement_report_config(
        self,
        *,
        body: ReportConfig,
    ) -> ReportConfig:
        """Update settlements report configuration.

        Args:
            body (ReportConfig): The request body.

        Returns:
            ReportConfig: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/settlement_report/config"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(ReportConfig, response.json())

    async def list_scheduled_settlement_reports(self) -> ReportListResult:
        """List scheduled settlements reports.

        Returns:
            ReportListResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/settlement_report/list"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ReportListResult, response.json())

    async def enable_settlement_report_schedule(self) -> None:
        """Enable automatic settlements report generation.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/settlement_report/schedule"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return None

    async def disable_settlement_report_schedule(self) -> None:
        """Disable automatic settlements report generation.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/settlement_report/schedule"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def search_settlement_reports(
        self,
        *,
        begin_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> ReportListResult:
        """Search settlements reports.

        Args:
            begin_date (datetime | None): The begin_date value. Omitted from the query
                when None.
            end_date (datetime | None): The end_date value. Omitted from the query when
                None.

        Returns:
            ReportListResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/account/settlement_report/search"
        params: dict[str, Any] = {}
        if begin_date is not None:
            params["begin_date"] = _param(begin_date)
        if end_date is not None:
            params["end_date"] = _param(end_date)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(ReportListResult, response.json())

    async def get_settlement_report_task(
        self,
        task_id: str,
    ) -> ReportTask:
        """Get settlements report task status.

        Args:
            task_id (str): The task_id value.

        Returns:
            ReportTask: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/v1/account/settlement_report/task/{task_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ReportTask, response.json())

    # openapi: unsupported — response of DownloadSettlementReport uses text/csv — only
    #   application/json is modelled
    async def download_settlement_report(
        self,
        file_name: str,
    ) -> None:
        """Download a settlements report file.

        Args:
            file_name (str): The file_name value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/v1/account/settlement_report/{file_name}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def create_advanced_payment(
        self,
        *,
        body: CreateAdvancedPaymentBody,
        x_idempotency_key: UUID,
        x_meli_session_id: str | None = None,
    ) -> CreateAdvancedPaymentResponse:
        """Create an advanced payment.

        Creates a Wallet Connect advanced payment (marketplace split payment).

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            body (CreateAdvancedPaymentBody): The request body.
            x_idempotency_key (UUID): The X-Idempotency-Key value.
            x_meli_session_id (str | None): The X-Meli-Session-Id value. Omitted from
                the request headers when None.

        Returns:
            CreateAdvancedPaymentResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = "/v1/advanced_payments"
        headers: dict[str, str] = {}
        headers["X-Idempotency-Key"] = str(x_idempotency_key)
        if x_meli_session_id is not None:
            headers["X-Meli-Session-Id"] = str(x_meli_session_id)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreateAdvancedPaymentResponse, response.json())

    async def get_advanced_payment(
        self,
        advanced_payment_id: int,
    ) -> GetAdvancedPaymentResponse:
        """Get an advanced payment.

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            advanced_payment_id (int): The advanced_payment_id value.

        Returns:
            GetAdvancedPaymentResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 500.
        """
        path = f"/v1/advanced_payments/{advanced_payment_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetAdvancedPaymentResponse, response.json())

    async def update_advanced_payment(
        self,
        advanced_payment_id: int,
        *,
        body: UpdateAdvancedPaymentBody,
    ) -> UpdateAdvancedPaymentResponse:
        """Capture or cancel an advanced payment.

        Capture (capture=true) or cancel (status=cancelled).

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            advanced_payment_id (int): The advanced_payment_id value.
            body (UpdateAdvancedPaymentBody): The request body.

        Returns:
            UpdateAdvancedPaymentResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 500.
        """
        path = f"/v1/advanced_payments/{advanced_payment_id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(UpdateAdvancedPaymentResponse, response.json())

    async def create_card_token(
        self,
        *,
        body: CardTokenRequest,
    ) -> CardToken:
        """Create a card token (client-side only).

        **CLIENT-SIDE ONLY.** Creates a single-use card token from raw card data. Call
        this from your frontend using MercadoPago.js with the PUBLIC_KEY. Never call
        this endpoint server-side — doing so puts raw PAN and CVV on your server and
        brings your integration into full PCI DSS scope. **PCI scope:** Handling this
        endpoint brings your integration into PCI DSS scope. **Auth note:** Use
        PUBLIC_KEY for this endpoint (not ACCESS_TOKEN). Public key is safe in frontend
        code. Using ACCESS_TOKEN here from the browser exposes your server credentials.

        Args:
            body (CardTokenRequest): The request body.

        Returns:
            CardToken: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/v1/card_tokens"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CardToken, response.json())

    async def get_card_token(
        self,
        id: str,
    ) -> CardToken:
        """Get a card token.

        **PCI scope:** Handling this endpoint brings your integration into PCI DSS
        scope.

        Args:
            id (str): The id value.

        Returns:
            CardToken: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/v1/card_tokens/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(CardToken, response.json())

    async def get_chargeback(
        self,
        id: str,
    ) -> GetChargebackResponse:
        """Get chargeback by ID.

        Args:
            id (str): The id value.

        Returns:
            GetChargebackResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/v1/chargebacks/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetChargebackResponse, response.json())

    async def update_chargeback(
        self,
        id: str,
        *,
        body: UpdateChargebackBody,
    ) -> None:
        """Upload chargeback documentation.

        Args:
            id (str): The id value.
            body (UpdateChargebackBody): The request body.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/v1/chargebacks/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def create_customer(
        self,
        *,
        body: CustomerRequest,
    ) -> Customer:
        """Create a customer.

        Creates a customer profile for storing payment methods. The customer email is
        unique per account.

        Args:
            body (CustomerRequest): The request body.

        Returns:
            Customer: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/v1/customers"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Customer, response.json())

    async def search_customers(
        self,
        *,
        email: EmailStr | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> CustomerSearchResult:
        """Search customers.

        Args:
            email (EmailStr | None): The email value. Omitted from the query when None.
            limit (int | None): The limit value. Omitted from the query when None.
            offset (int | None): The offset value. Omitted from the query when None.

        Returns:
            CustomerSearchResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/v1/customers/search"
        params: dict[str, Any] = {}
        if email is not None:
            params["email"] = _param(email)
        if limit is not None:
            params["limit"] = _param(limit)
        if offset is not None:
            params["offset"] = _param(offset)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(CustomerSearchResult, response.json())

    async def list_customer_addresses(
        self,
        customer_id: str,
    ) -> list[Address]:
        """List customer addresses.

        Args:
            customer_id (str): The customer_id value.

        Returns:
            list[Address]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401.
        """
        path = f"/v1/customers/{customer_id}/addresses"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(list[Address], response.json())

    async def create_customer_address(
        self,
        customer_id: str,
        *,
        body: Address,
    ) -> Address:
        """Create a customer address.

        Adds a shipping or billing address to a customer profile.

        Args:
            customer_id (str): The customer_id value.
            body (Address): The request body.

        Returns:
            Address: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 429.
        """
        path = f"/v1/customers/{customer_id}/addresses"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Address, response.json())

    async def get_customer_address(
        self,
        customer_id: str,
        address_id: str,
    ) -> Address:
        """Get a customer address.

        Args:
            customer_id (str): The customer_id value.
            address_id (str): The address_id value.

        Returns:
            Address: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/v1/customers/{customer_id}/addresses/{address_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Address, response.json())

    async def update_customer_address(
        self,
        customer_id: str,
        address_id: str,
        *,
        body: Address,
    ) -> Address:
        """Update a customer address.

        Args:
            customer_id (str): The customer_id value.
            address_id (str): The address_id value.
            body (Address): The request body.

        Returns:
            Address: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/v1/customers/{customer_id}/addresses/{address_id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Address, response.json())

    async def delete_customer_address(
        self,
        customer_id: str,
        address_id: str,
    ) -> None:
        """Delete a customer address.

        Args:
            customer_id (str): The customer_id value.
            address_id (str): The address_id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/v1/customers/{customer_id}/addresses/{address_id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def list_cards(
        self,
        customer_id: str,
    ) -> list[Card]:
        """List customer cards.

        Args:
            customer_id (str): The customer_id value.

        Returns:
            list[Card]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = f"/v1/customers/{customer_id}/cards"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(list[Card], response.json())

    async def save_card(
        self,
        customer_id: str,
        *,
        body: SaveCardRequest,
    ) -> Card:
        """Save a card to a customer.

        Saves a tokenized card to a customer profile for future payments. The token must
        be created client-side via MercadoPago.js. Raw card data must never pass through
        your server. **PCI scope:** Handling this endpoint brings your integration into
        PCI DSS scope.

        Args:
            customer_id (str): The customer_id value.
            body (SaveCardRequest): The request body.

        Returns:
            Card: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = f"/v1/customers/{customer_id}/cards"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Card, response.json())

    async def get_card(
        self,
        customer_id: str,
        id: str,
    ) -> Card:
        """Get a saved card.

        Args:
            customer_id (str): The customer_id value.
            id (str): The id value.

        Returns:
            Card: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/customers/{customer_id}/cards/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Card, response.json())

    async def update_card(
        self,
        customer_id: str,
        id: str,
        *,
        body: UpdateCardRequest,
    ) -> Card:
        """Update a saved card.

        Args:
            customer_id (str): The customer_id value.
            id (str): The id value.
            body (UpdateCardRequest): The request body.

        Returns:
            Card: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/customers/{customer_id}/cards/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Card, response.json())

    async def delete_card(
        self,
        customer_id: str,
        id: str,
    ) -> Card:
        """Delete a saved card.

        Args:
            customer_id (str): The customer_id value.
            id (str): The id value.

        Returns:
            Card: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/customers/{customer_id}/cards/{id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(Card, response.json())

    async def get_customer(
        self,
        id: str,
    ) -> Customer:
        """Get customer by ID.

        Args:
            id (str): The id value.

        Returns:
            Customer: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/customers/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Customer, response.json())

    async def update_customer(
        self,
        id: str,
        *,
        body: CustomerRequest,
    ) -> Customer:
        """Update a customer.

        Args:
            id (str): The id value.
            body (CustomerRequest): The request body.

        Returns:
            Customer: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/customers/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Customer, response.json())

    async def delete_customer(
        self,
        id: str,
    ) -> Customer:
        """Delete a customer.

        Permanently deletes a customer profile. This action cannot be undone. Saved
        cards associated with the customer will also be removed.

        Args:
            id (str): Customer ID to delete

        Returns:
            Customer: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 404.
        """
        path = f"/v1/customers/{id}/delete"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(Customer, response.json())

    async def list_identification_types(
        self,
    ) -> list[ListIdentificationTypesResponseItem]:
        """List identification types.

        Returns valid ID document types for the credential's site_id. Examples: CPF/CNPJ
        (Brazil), DNI/CUIL/CUIT (Argentina), RFC/CURP (Mexico). Use this to populate
        identification type selectors and validate inputs.

        Returns:
            list[ListIdentificationTypesResponseItem]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = "/v1/identification_types"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(list[ListIdentificationTypesResponseItem], response.json())

    async def search_orders(
        self,
        *,
        begin_date: datetime,
        end_date: datetime,
        external_reference: str | None = None,
        type: OrderRequestType | None = None,
        status: OrderStatus | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> OrderSearchResult:
        """Search orders.

        Search for orders using date range and optional filters. begin_date and end_date
        are required.

        Args:
            begin_date (datetime): Start of date range (ISO 8601)
            end_date (datetime): End of date range (ISO 8601)
            external_reference (str | None): The external_reference value. Omitted from
                the query when None.
            type (OrderRequestType | None): The type value. Omitted from the query when
                None.
            status (OrderStatus | None): The status value. Omitted from the query when
                None.
            limit (int | None): The limit value. Omitted from the query when None.
            offset (int | None): The offset value. Omitted from the query when None.

        Returns:
            OrderSearchResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/v1/orders"
        params: dict[str, Any] = {}
        params["begin_date"] = _param(begin_date)
        params["end_date"] = _param(end_date)
        if external_reference is not None:
            params["external_reference"] = _param(external_reference)
        if type is not None:
            params["type"] = _param(type)
        if status is not None:
            params["status"] = _param(status)
        if limit is not None:
            params["limit"] = _param(limit)
        if offset is not None:
            params["offset"] = _param(offset)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(OrderSearchResult, response.json())

    async def create_order(
        self,
        *,
        body: OrderRequest,
        x_idempotency_key: UUID,
    ) -> Order:
        """Create an order.

        Creates an Order for processing payment transactions. Supports automatic
        (single-stage, set processing_mode=automatic) and manual (multi-stage, set
        processing_mode=manual) modes.

        In automatic mode, include the transactions.payments array with the payment
        method. In manual mode, omit transactions and add them later via POST
        /v1/orders/{id}/transactions, then trigger processing with POST
        /v1/orders/{id}/process.

        Available for: credit card, debit card, Pix (MLB), Boleto (MLB), OXXO (MLM),
        SPEI (MLM), PSE (MCO), Rapipago (MLA), Pago Fácil (MLA).

        Args:
            body (OrderRequest): The request body.
            x_idempotency_key (UUID): Unique key per order creation attempt. Prevents
                duplicate orders on retry.

        Returns:
            Order: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 422, 423, 500.
        """
        path = "/v1/orders"
        headers: dict[str, str] = {}
        headers["X-Idempotency-Key"] = str(x_idempotency_key)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Order, response.json())

    async def get_order(
        self,
        id: str,
    ) -> Order:
        """Get order by ID.

        Returns all order information for the given order ID.

        Args:
            id (str): Order ID

        Returns:
            Order: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/orders/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Order, response.json())

    async def cancel_order(
        self,
        order_id: str,
        *,
        x_idempotency_key: UUID,
    ) -> Order:
        """Cancel an order.

        Cancels an order and all its transactions. Only orders with
        status=action_required or status=created can be canceled.

        Args:
            order_id (str): The order_id value.
            x_idempotency_key (UUID): The X-Idempotency-Key value.

        Returns:
            Order: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 409.
        """
        path = f"/v1/orders/{order_id}/cancel"
        headers: dict[str, str] = {}
        headers["X-Idempotency-Key"] = str(x_idempotency_key)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
        )
        response.raise_for_status()
        return _validate(Order, response.json())

    async def capture_order(
        self,
        order_id: str,
        *,
        x_idempotency_key: UUID,
    ) -> CaptureOrderResponse:
        """Capture an authorized order.

        Fully captures a previously authorized order (capture_mode=manual). All
        associated authorized transactions are captured in full.

        Args:
            order_id (str): The order_id value.
            x_idempotency_key (UUID): The X-Idempotency-Key value.

        Returns:
            CaptureOrderResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 409.
        """
        path = f"/v1/orders/{order_id}/capture"
        headers: dict[str, str] = {}
        headers["X-Idempotency-Key"] = str(x_idempotency_key)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
        )
        response.raise_for_status()
        return _validate(CaptureOrderResponse, response.json())

    async def process_order(
        self,
        order_id: str,
        *,
        x_idempotency_key: UUID,
    ) -> Order:
        """Process an order.

        Triggers processing of an order and all its transactions. Only available for
        manual-mode orders (processing_mode=manual). After calling this endpoint the
        order transitions to processed or action_required.

        Args:
            order_id (str): The order_id value.
            x_idempotency_key (UUID): The X-Idempotency-Key value.

        Returns:
            Order: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 409.
        """
        path = f"/v1/orders/{order_id}/process"
        headers: dict[str, str] = {}
        headers["X-Idempotency-Key"] = str(x_idempotency_key)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
        )
        response.raise_for_status()
        return _validate(Order, response.json())

    async def refund_order(
        self,
        order_id: str,
        *,
        body: OrderRefundRequest | None = None,
        x_idempotency_key: UUID,
    ) -> RefundOrderResponse:
        """Refund an order.

        Performs a full or partial refund of transactions associated with an order. For
        a full refund, send an empty request body (omit transactions). For a partial
        refund, include the transactions array with the specific transaction IDs and
        amounts to refund.

        Args:
            order_id (str): The order_id value.
            body (OrderRefundRequest): The request body. Optional.
            x_idempotency_key (UUID): The X-Idempotency-Key value.

        Returns:
            RefundOrderResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 409.
        """
        path = f"/v1/orders/{order_id}/refund"
        headers: dict[str, str] = {}
        headers["X-Idempotency-Key"] = str(x_idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(RefundOrderResponse, response.json())

    async def add_order_transaction(
        self,
        order_id: str,
        *,
        body: AddOrderTransactionBody,
        x_idempotency_key: UUID,
    ) -> AddOrderTransactionResponse:
        """Add a transaction to an order.

        Adds a payment transaction to an order. Only available when
        processing_mode=manual. The order must be in status=created.

        Args:
            order_id (str): The order_id value.
            body (AddOrderTransactionBody): The request body.
            x_idempotency_key (UUID): The X-Idempotency-Key value.

        Returns:
            AddOrderTransactionResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 422.
        """
        path = f"/v1/orders/{order_id}/transactions"
        headers: dict[str, str] = {}
        headers["X-Idempotency-Key"] = str(x_idempotency_key)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AddOrderTransactionResponse, response.json())

    async def update_order_transaction(
        self,
        order_id: str,
        transaction_id: str,
        *,
        body: UpdateOrderTransactionBody,
        x_idempotency_key: UUID,
    ) -> OrderTransactionPayment:
        """Update a transaction on an order.

        Updates the payment method of a pending transaction on a manual-mode order.

        Args:
            order_id (str): The order_id value.
            transaction_id (str): The transaction_id value.
            body (UpdateOrderTransactionBody): The request body.
            x_idempotency_key (UUID): The X-Idempotency-Key value.

        Returns:
            OrderTransactionPayment: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/orders/{order_id}/transactions/{transaction_id}"
        headers: dict[str, str] = {}
        headers["X-Idempotency-Key"] = str(x_idempotency_key)
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(OrderTransactionPayment, response.json())

    async def delete_order_transaction(
        self,
        order_id: str,
        transaction_id: str,
    ) -> None:
        """Delete a transaction from an order.

        Removes a transaction from a manual-mode order. Only available before
        processing.

        Args:
            order_id (str): The order_id value.
            transaction_id (str): The transaction_id value.

        Returns:
            None: Nothing — the operation answers 204 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/orders/{order_id}/transactions/{transaction_id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def list_payment_methods(self) -> list[ListPaymentMethodsResponseItem]:
        """List available payment methods.

        Returns all payment methods available for the credential's site_id. Use this to
        build payment method selectors and validate method availability before
        attempting a payment.

        Returns:
            list[ListPaymentMethodsResponseItem]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = "/v1/payment_methods"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(list[ListPaymentMethodsResponseItem], response.json())

    async def get_installments(
        self,
        *,
        payment_method_id: str,
        amount: float,
        issuer_id: str | None = None,
        bin: str | None = None,
    ) -> list[GetInstallmentsResponseItem]:
        """Get installment options.

        Returns available installment plans for a given card BIN, amount, and site. Use
        this to populate installment selectors in your checkout.

        Args:
            payment_method_id (str): The payment_method_id value.
            amount (float): The amount value.
            issuer_id (str | None): The issuer_id value. Omitted from the query when
                None.
            bin (str | None): First 6 digits of the card (BIN) for more accurate
                installment pricing Omitted from the query when None.

        Returns:
            list[GetInstallmentsResponseItem]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/payment_methods/installments"
        params: dict[str, Any] = {}
        params["payment_method_id"] = _param(payment_method_id)
        params["amount"] = _param(amount)
        if issuer_id is not None:
            params["issuer_id"] = _param(issuer_id)
        if bin is not None:
            params["bin"] = _param(bin)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(list[GetInstallmentsResponseItem], response.json())

    async def create_payment(
        self,
        *,
        body: PaymentRequest,
        x_idempotency_key: UUID | None = None,
    ) -> Payment:
        """Create a payment.

        Creates a payment. For card payments, generate a card token client-side via
        MercadoPago.js before calling this endpoint. For cash/offline methods (Boleto,
        OXXO, Pix), the response includes a payment URL in
        `transaction_details.external_resource_url`. **Idempotency**: Include
        `X-Idempotency-Key` to safely retry on network errors without risk of double
        charges. **Recommendation**: For new integrations, prefer the Orders API (`POST
        /v1/orders`). **Idempotent:** Supports `X-Idempotency-Key` header to safely
        retry without duplicate charges. **Webhook events triggered:** payment,
        merchant_order

        Args:
            body (PaymentRequest): The request body.
            x_idempotency_key (UUID | None): Unique key per payment attempt. If you
                retry with the same key and the original payment was processed, MP
                returns the original result without creating a duplicate. Omitted from
                the request headers when None.

        Returns:
            Payment: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 422.
        """
        path = "/v1/payments"
        headers: dict[str, str] = {}
        if x_idempotency_key is not None:
            headers["X-Idempotency-Key"] = str(x_idempotency_key)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Payment, response.json())

    async def search_payments(
        self,
        *,
        sort: str,
        criteria: SearchSubscriptionPlansCriteria,
        external_reference: str | None = None,
        range: SearchPaymentsRange | None = None,
        begin_date: datetime | None = None,
        end_date: datetime | None = None,
        status: str | None = None,
        store_id: str | None = None,
        pos_id: str | None = None,
        collector_id: str | None = None,
        payer_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> PaymentSearchResult:
        """Search payments.

        Args:
            sort (str): The sort value.
            criteria (SearchSubscriptionPlansCriteria): The criteria value.
            external_reference (str | None): The external_reference value. Omitted from
                the query when None.
            range (SearchPaymentsRange | None): The range value. Omitted from the query
                when None.
            begin_date (datetime | None): The begin_date value. Omitted from the query
                when None.
            end_date (datetime | None): The end_date value. Omitted from the query when
                None.
            status (str | None): The status value. Omitted from the query when None.
            store_id (str | None): The store_id value. Omitted from the query when None.
            pos_id (str | None): The pos_id value. Omitted from the query when None.
            collector_id (str | None): The collector.id value. Omitted from the query
                when None.
            payer_id (str | None): The payer.id value. Omitted from the query when None.
            limit (int | None): The limit value. Omitted from the query when None.
            offset (int | None): The offset value. Omitted from the query when None.

        Returns:
            PaymentSearchResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403.
        """
        path = "/v1/payments/search"
        params: dict[str, Any] = {}
        params["sort"] = _param(sort)
        params["criteria"] = _param(criteria)
        if external_reference is not None:
            params["external_reference"] = _param(external_reference)
        if range is not None:
            params["range"] = _param(range)
        if begin_date is not None:
            params["begin_date"] = _param(begin_date)
        if end_date is not None:
            params["end_date"] = _param(end_date)
        if status is not None:
            params["status"] = _param(status)
        if store_id is not None:
            params["store_id"] = _param(store_id)
        if pos_id is not None:
            params["pos_id"] = _param(pos_id)
        if collector_id is not None:
            params["collector.id"] = _param(collector_id)
        if payer_id is not None:
            params["payer.id"] = _param(payer_id)
        if limit is not None:
            params["limit"] = _param(limit)
        if offset is not None:
            params["offset"] = _param(offset)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(PaymentSearchResult, response.json())

    async def get_payment(
        self,
        id: int,
    ) -> Payment:
        """Get payment by ID.

        Args:
            id (int): Payment ID

        Returns:
            Payment: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/v1/payments/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Payment, response.json())

    async def update_payment(
        self,
        id: int,
        *,
        body: PaymentUpdateRequest,
    ) -> Payment:
        """Update or capture a payment.

        Update payment fields or capture an authorized payment. To capture an authorized
        two-step payment: send `{"capture": true}`. To cancel an authorized payment:
        send `{"status": "cancelled"}`.

        Args:
            id (int): The id value.
            body (PaymentUpdateRequest): The request body.

        Returns:
            Payment: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/v1/payments/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Payment, response.json())

    async def cancel_payment(
        self,
        id: int,
        *,
        body: CancelPaymentBody,
    ) -> Payment:
        """Cancel a payment.

        Cancels a payment that is in `pending` or `authorized` status. Only payments
        that have not yet been captured or processed can be cancelled. For approved
        payments use the refunds endpoint instead.

        Args:
            id (int): Payment ID to cancel
            body (CancelPaymentBody): The request body.

        Returns:
            Payment: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 422, 429.
        """
        path = f"/v1/payments/{id}/cancellations"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(Payment, response.json())

    async def list_refunds(
        self,
        id: int,
    ) -> ListRefundsResponse:
        """List refunds for a payment.

        Args:
            id (int): The id value.

        Returns:
            ListRefundsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/payments/{id}/refunds"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ListRefundsResponse, response.json())

    async def create_refund(
        self,
        id: int,
        *,
        body: RefundRequest | None = None,
        x_idempotency_key: UUID | None = None,
    ) -> CreateRefundResponse:
        """Create a refund.

        Creates a full or partial refund for an approved payment. Omit the `amount`
        field for a full refund. Partial refunds are supported; multiple partials are
        allowed up to the original transaction amount. **Idempotent:** Supports
        `X-Idempotency-Key` header to safely retry without duplicate charges.

        Args:
            id (int): Payment ID to refund
            body (RefundRequest): The request body. Optional.
            x_idempotency_key (UUID | None): The X-Idempotency-Key value. Omitted from
                the request headers when None.

        Returns:
            CreateRefundResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/v1/payments/{id}/refunds"
        headers: dict[str, str] = {}
        if x_idempotency_key is not None:
            headers["X-Idempotency-Key"] = str(x_idempotency_key)
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreateRefundResponse, response.json())

    async def get_refund(
        self,
        id: int,
        refund_id: int,
    ) -> GetRefundResponse:
        """Get a specific refund.

        Args:
            id (int): The id value.
            refund_id (int): The refund_id value.

        Returns:
            GetRefundResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404.
        """
        path = f"/v1/payments/{id}/refunds/{refund_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetRefundResponse, response.json())

    async def create_payout(
        self,
        *,
        body: CreatePayoutBody,
    ) -> None:
        """Create a batch of payout transactions.

        **Available in:** Argentina, Mexico (MLA, MLM)

        Args:
            body (CreatePayoutBody): The request body.

        Returns:
            None: Nothing — the operation answers 201 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/payouts"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def list_payout_transactions(
        self,
        payout_id: str,
    ) -> None:
        """List payout transactions.

        **Available in:** Argentina, Mexico (MLA, MLM)

        Args:
            payout_id (str): The payout_id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/v1/payouts/{payout_id}/transactions"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def cancel_payout_transaction(
        self,
        payout_id: str,
        transaction_id: str,
    ) -> None:
        """Cancel a payout transaction.

        **Available in:** Argentina, Mexico (MLA, MLM)

        Args:
            payout_id (str): The payout_id value.
            transaction_id (str): The transaction_id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/v1/payouts/{payout_id}/transactions/{transaction_id}/cancel"
        response = await self._client.request(
            "PUT",
            path,
        )
        response.raise_for_status()
        return None

    async def process_transaction_intent(
        self,
        *,
        body: ProcessTransactionIntentBody,
    ) -> None:
        """Create a disbursement (Pix or bank transfer).

        Creates a Pix or bank transfer disbursement for Brazil. The `payment_method_id`
        determines the method: - `pix` — instant Pix transfer (available 24/7) -
        `bank_transfer` — TED/DOC bank transfer **Available in:** Brazil (MLB)

        Args:
            body (ProcessTransactionIntentBody): The request body.

        Returns:
            None: Nothing — the operation answers 201 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/v1/transaction-intents/process"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return None

    async def get_transaction_intent(
        self,
        id: str,
    ) -> None:
        """Get disbursement status.

        **Available in:** Brazil (MLB)

        Args:
            id (str): The id value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/v1/transaction-intents/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def create_wallet_agreement(
        self,
        *,
        body: CreateWalletAgreementBody,
        client_id: str | None = None,
        x_platform_id: str | None = None,
    ) -> CreateWalletAgreementResponse:
        """Create a Wallet Connect agreement.

        Creates an authorization agreement for Wallet Connect. Returns an agreement
        token to redirect the payer to MP for wallet authorization. **Available in:**
        Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            body (CreateWalletAgreementBody): The request body.
            client_id (str | None): The client.id value. Omitted from the query when
                None.
            x_platform_id (str | None): The x-platform-id value. Omitted from the
                request headers when None.

        Returns:
            CreateWalletAgreementResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/v2/wallet_connect/agreements"
        params: dict[str, Any] = {}
        if client_id is not None:
            params["client.id"] = _param(client_id)
        headers: dict[str, str] = {}
        if x_platform_id is not None:
            headers["x-platform-id"] = str(x_platform_id)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            params=params,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreateWalletAgreementResponse, response.json())

    async def get_wallet_agreement(
        self,
        agreement_id: str,
        *,
        client_id: str | None = None,
        x_platform_id: str | None = None,
    ) -> GetWalletAgreementResponse:
        """Get a Wallet Connect agreement.

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            agreement_id (str): The agreement_id value.
            client_id (str | None): The client.id value. Omitted from the query when
                None.
            x_platform_id (str | None): The x-platform-id value. Omitted from the
                request headers when None.

        Returns:
            GetWalletAgreementResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404, 500.
        """
        path = f"/v2/wallet_connect/agreements/{agreement_id}"
        params: dict[str, Any] = {}
        if client_id is not None:
            params["client.id"] = _param(client_id)
        headers: dict[str, str] = {}
        if x_platform_id is not None:
            headers["x-platform-id"] = str(x_platform_id)
        response = await self._client.request(
            "GET",
            path,
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        return _validate(GetWalletAgreementResponse, response.json())

    async def delete_wallet_agreement(
        self,
        agreement_id: str,
        *,
        client_id: str | None = None,
        x_platform_id: str | None = None,
    ) -> None:
        """Revoke a Wallet Connect agreement.

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            agreement_id (str): The agreement_id value.
            client_id (str | None): The client.id value. Omitted from the query when
                None.
            x_platform_id (str | None): The x-platform-id value. Omitted from the
                request headers when None.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404, 500.
        """
        path = f"/v2/wallet_connect/agreements/{agreement_id}"
        params: dict[str, Any] = {}
        if client_id is not None:
            params["client.id"] = _param(client_id)
        headers: dict[str, str] = {}
        if x_platform_id is not None:
            headers["x-platform-id"] = str(x_platform_id)
        response = await self._client.request(
            "DELETE",
            path,
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        return None

    async def create_wallet_payer_token(
        self,
        agreement_id: str,
        *,
        body: CreateWalletPayerTokenBody,
        x_platform_id: str | None = None,
    ) -> CreateWalletPayerTokenResponse:
        """Create a payer token from agreement.

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            agreement_id (str): The agreement_id value.
            body (CreateWalletPayerTokenBody): The request body.
            x_platform_id (str | None): The x-platform-id value. Omitted from the
                request headers when None.

        Returns:
            CreateWalletPayerTokenResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404, 500.
        """
        path = f"/v2/wallet_connect/agreements/{agreement_id}/payer_token"
        headers: dict[str, str] = {}
        if x_platform_id is not None:
            headers["x-platform-id"] = str(x_platform_id)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreateWalletPayerTokenResponse, response.json())

    async def validate_wallet_coupon(
        self,
        *,
        body: ValidateWalletCouponBody,
        x_payer_token: str,
    ) -> ValidateWalletCouponResponse:
        """Validate a coupon.

        Checks coupon status and returns description and legal terms.

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            body (ValidateWalletCouponBody): The request body.
            x_payer_token (str): The x-payer-token value.

        Returns:
            ValidateWalletCouponResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = "/v2/wallet_connect/coupons"
        headers: dict[str, str] = {}
        headers["x-payer-token"] = str(x_payer_token)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(ValidateWalletCouponResponse, response.json())

    async def create_wallet_discount(
        self,
        *,
        body: CreateWalletDiscountBody,
        x_payer_token: str,
    ) -> CreateWalletDiscountResponse:
        """Create a discount promise.

        Validates a coupon and returns discount amount and legal terms.

        **Available in:** Argentina, Brazil, Mexico (MLA, MLB, MLM)

        Args:
            body (CreateWalletDiscountBody): The request body.
            x_payer_token (str): Payer wallet token from agreement flow.

        Returns:
            CreateWalletDiscountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = "/v2/wallet_connect/discounts"
        headers: dict[str, str] = {}
        headers["x-payer-token"] = str(x_payer_token)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreateWalletDiscountResponse, response.json())


__all__: list[str] = [
    "DEFAULT_BASE_URL",
    "MercadoPagoClient",
]

"""Typed HTTP client generated from the OpenPix OpenAPI spec.

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

from pydantic import BaseModel, TypeAdapter

from tempest_fastapi_sdk import HTTPClient

from .schemas import (
    ApplicationPayload,
    BoletoValidateRequest,
    BoletoValidateResponse,
    ChargePatchPayload,
    ChargePayload,
    ChargeRefundPayload,
    ChargeStatus,
    CustomerPatchPayload,
    CustomerPayload,
    DeleteApiV1AccountByAccountIdResponse,
    DeleteApiV1AccountRegisterByIdResponse,
    DeleteApiV1ApplicationResponse,
    DeleteApiV1ChargeByIdResponse,
    DeleteApiV1QrcodeStaticByIdResponse,
    DeleteApiV1SubaccountByIdResponse,
    DeleteApiV1WebhookByIdResponse,
    FundsRecovery,
    FundsRecoveryPayload,
    GetApiImageQrcodeBase64ByIdResponse,
    GetApiV1AccountByAccountIdResponse,
    GetApiV1AccountRegisterResponse,
    GetApiV1AccountResponse,
    GetApiV1CashbackFidelityBalanceByTaxIdResponse,
    GetApiV1ChargeByIdRefundResponse,
    GetApiV1ChargeByIdResponse,
    GetApiV1ChargeResponse,
    GetApiV1CompanyResponse,
    GetApiV1CustomerByIdResponse,
    GetApiV1CustomerResponse,
    GetApiV1DisputeByIdResponse,
    GetApiV1DisputeResponse,
    GetApiV1InstallmentsByIdResponse,
    GetApiV1LimitsByAccountIdResponse,
    GetApiV1PartnerAffiliateResponse,
    GetApiV1PartnerCompanyByTaxIdResponse,
    GetApiV1PartnerCompanyResponse,
    GetApiV1PaymentByIdResponse,
    GetApiV1PaymentResponse,
    GetApiV1PixKeysResponse,
    GetApiV1PixKeysTokensLogsResponse,
    GetApiV1PspResponse,
    GetApiV1QrcodeStaticByIdResponse,
    GetApiV1QrcodeStaticResponse,
    GetApiV1ReceiptByReceiptTypeByEndToEndIdReceiptType,
    GetApiV1RefundByIdResponse,
    GetApiV1RefundResponse,
    GetApiV1StablecoinQuoteResponse,
    GetApiV1StatementResponseItem,
    GetApiV1SubaccountByIdResponse,
    GetApiV1SubaccountByIdStatementResponseItem,
    GetApiV1SubaccountResponse,
    GetApiV1SubscriptionsByIdInstallmentsResponse,
    GetApiV1SubscriptionsByIdResponse,
    GetApiV1SubscriptionsResponse,
    GetApiV1TransactionByIdResponse,
    GetApiV1TransactionResponse,
    GetApiV1TransactionType,
    GetApiV1WebhookEventsResponse,
    GetApiV1WebhookIpsResponse,
    GetApiV1WebhookResponse,
    KycOnboardingRequest,
    PatchApiV1ChargeByIdResponse,
    PatchApiV1CustomerByCorrelationIdResponse,
    PatchApiV1InvoiceIntegrationBody,
    PatchApiV1InvoiceIntegrationResponse,
    PaymentApprovePayload,
    PixKey,
    PixKeyCheck,
    PixKeyCreate,
    PixKeyTokens,
    PixQrCodePayload,
    PostApiV1AccountByAccountIdWithdrawBody,
    PostApiV1AccountByAccountIdWithdrawResponse,
    PostApiV1AccountResponse,
    PostApiV1ApplicationResponse,
    PostApiV1CashbackFidelityBody,
    PostApiV1CashbackFidelityResponse,
    PostApiV1ChargeByIdRefundResponse,
    PostApiV1ChargeResponse,
    PostApiV1CustomerResponse,
    PostApiV1DecodeEmvBody,
    PostApiV1DecodeEmvResponse,
    PostApiV1DisputeIdEvidenceBody,
    PostApiV1DisputeIdEvidenceResponse,
    PostApiV1InstallmentsByIdCobrBody,
    PostApiV1InstallmentsByIdCobrRetryBody,
    PostApiV1InvoiceByCorrelationIdCancelResponse,
    PostApiV1InvoiceIntegrationBody,
    PostApiV1InvoiceIntegrationCertificateBody,
    PostApiV1InvoiceIntegrationCertificateResponse,
    PostApiV1InvoiceIntegrationResponse,
    PostApiV1InvoiceIntegrationTestResponse,
    PostApiV1InvoiceResponse,
    PostApiV1KycOnboardingResponse,
    PostApiV1PartnerApplicationBody,
    PostApiV1PartnerApplicationResponse,
    PostApiV1PaymentApproveResponse,
    PostApiV1PaymentBody,
    PostApiV1PaymentResponse,
    PostApiV1PixKeysCheckBody,
    PostApiV1QrcodeStaticResponse,
    PostApiV1RefundResponse,
    PostApiV1StablecoinDepositApproveBody,
    PostApiV1StablecoinDepositApproveResponse,
    PostApiV1SubaccountByIdCreditBody,
    PostApiV1SubaccountByIdCreditResponse,
    PostApiV1SubaccountByIdDebitBody,
    PostApiV1SubaccountByIdDebitResponse,
    PostApiV1SubaccountByIdWithdrawResponse,
    PostApiV1SubaccountResponse,
    PostApiV1SubscriptionsResponse,
    PostApiV1TransferResponse,
    PostApiV1WebhookBody,
    PostApiV1WebhookResponse,
    PreRegistrationPayloadObject,
    PutApiV1InvoiceIntegrationBody,
    PutApiV1InvoiceIntegrationResponse,
    RefundPayload,
    StablecoinDepositRequest,
    StablecoinDepositRequestCurrency,
    StablecoinDepositResponse,
    StablecoinSubAccountCreateRequest,
    StablecoinSubAccountCreateResponse,
    StablecoinSubAccountGetResponse,
    StablecoinSubAccountListResponse,
    SubAccountPayload,
    SubAccountTransferPayload,
    SubAccountTransferResponsePayload,
    SubAccountWithdrawPayload,
    SubscriptionPayload,
    TransferCreatePayload,
)

DEFAULT_BASE_URL: str = "https://api.openpix.com.br"
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


class OpenPixClient:
    """Client for OpenPix (version 1.0.0)."""

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

    async def get_api_image_qrcode_base64_by_id(
        self,
        id: str,
        *,
        size: str | None = None,
    ) -> GetApiImageQrcodeBase64ByIdResponse:
        """Get a base64 encoded QR Code image from a Charge.

        Args:
            id (str): charge ID, payment link ID, or QR code ID
            size (str | None): Size for the image. This size should be between 600 and
                4096. If the size parameter is not passed, the default value will be
                1024. Omitted from the query when None.

        Returns:
            GetApiImageQrcodeBase64ByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 500.
        """
        path = f"/api/image/qrcode/base64/{id}"
        params: dict[str, Any] = {}
        if size is not None:
            params["size"] = _param(size)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiImageQrcodeBase64ByIdResponse, response.json())

    async def post_api_v1_account(self) -> PostApiV1AccountResponse:
        """Duplicates the Account.

        Duplicates the account associated with the authorization appId. Requires the
        bank account feature to be enabled.

        Returns:
            PostApiV1AccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = "/api/v1/account"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(PostApiV1AccountResponse, response.json())

    async def get_api_v1_account_register(self) -> GetApiV1AccountRegisterResponse:
        """Get account register by CorrelationID.

        Retrieves an existing account registration by CorrelationID

        Returns:
            GetApiV1AccountRegisterResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404, 500.
        """
        path = "/api/v1/account-register"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1AccountRegisterResponse, response.json())

    # openapi: unsupported — path '/api/v1/account-register/{id}' interpolates 'id',
    #   which no parameter declares — generated as a required str
    async def delete_api_v1_account_register_by_id(
        self,
        id: str,
    ) -> DeleteApiV1AccountRegisterByIdResponse:
        """Delete an account registration.

        Deletes an account registration that is in PENDING status

        Args:
            id (str): The id value.

        Returns:
            DeleteApiV1AccountRegisterByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404, 500.
        """
        path = f"/api/v1/account-register/{id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteApiV1AccountRegisterByIdResponse, response.json())

    async def get_api_v1_account(
        self,
        *,
        email: str | None = None,
        skip: float | None = None,
        limit: float | None = None,
    ) -> GetApiV1AccountResponse:
        """Get a list of Accounts.

        Args:
            email (str | None): You can use the email to filter accounts Omitted from
                the query when None.
            skip (float | None): Number of items to skip for pagination Omitted from the
                query when None.
            limit (float | None): Maximum number of items to return Omitted from the
                query when None.

        Returns:
            GetApiV1AccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/account/"
        params: dict[str, Any] = {}
        if email is not None:
            params["email"] = _param(email)
        if skip is not None:
            params["skip"] = _param(skip)
        if limit is not None:
            params["limit"] = _param(limit)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiV1AccountResponse, response.json())

    async def get_api_v1_account_by_account_id(
        self,
        account_id: str,
    ) -> GetApiV1AccountByAccountIdResponse:
        """Get an Account.

        Args:
            account_id (str): ID of the Account

        Returns:
            GetApiV1AccountByAccountIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/account/{account_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1AccountByAccountIdResponse, response.json())

    async def delete_api_v1_account_by_account_id(
        self,
        account_id: str,
    ) -> DeleteApiV1AccountByAccountIdResponse:
        """Close an Account.

        Closes an Account.

        Notes: - Accounts with balance cannot be closed.

        Args:
            account_id (str): ID of the Account

        Returns:
            DeleteApiV1AccountByAccountIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403, 404, 500.
        """
        path = f"/api/v1/account/{account_id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteApiV1AccountByAccountIdResponse, response.json())

    async def post_api_v1_account_by_account_id_withdraw(
        self,
        account_id: str,
        *,
        body: PostApiV1AccountByAccountIdWithdrawBody,
    ) -> PostApiV1AccountByAccountIdWithdrawResponse:
        """Withdraw from an Account.

        An additional fee may be charged depending on the minimum free withdrawal
        amount. See more about at
        https://developers.openpix.com.br/docs/FAQ/faq-virtual-account/#onde-posso-consultar-as-taxas-da-minha-conta-virtual

        Args:
            account_id (str): ID of the Account
            body (PostApiV1AccountByAccountIdWithdrawBody): The request body.

        Returns:
            PostApiV1AccountByAccountIdWithdrawResponse: The 200 response body,
                validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/account/{account_id}/withdraw"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1AccountByAccountIdWithdrawResponse, response.json())

    async def post_api_v1_application(
        self,
        *,
        body: ApplicationPayload,
    ) -> PostApiV1ApplicationResponse:
        """Create a new application.

        Creates a new application for a company. If the company has the
        APPLICATION_SCOPES_REQUIRED feature enabled, the scopes field is required.

        Args:
            body (ApplicationPayload): The request body.

        Returns:
            PostApiV1ApplicationResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403, 500.
        """
        path = "/api/v1/application"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1ApplicationResponse, response.json())

    async def delete_api_v1_application(self) -> DeleteApiV1ApplicationResponse:
        """Delete an application.

        Deactivates an application by setting isActive to false and adding a removedAt
        timestamp

        Returns:
            DeleteApiV1ApplicationResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404, 500.
        """
        path = "/api/v1/application"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteApiV1ApplicationResponse, response.json())

    async def post_api_v1_boleto_validate(
        self,
        *,
        body: BoletoValidateRequest,
    ) -> BoletoValidateResponse:
        """Validate a boleto by barcode.

        Validates a boleto by its barcode before paying it. This is step 1 of the Boleto
        OUT flow: it confirms the amount, due date and beneficiary so you can review the
        boleto before creating the payment.

        The barcode must have 44, 47 or 48 digits. All monetary values are returned in
        cents.

        Requires the `BOLETO_VALIDATE_POST` scope on the application.

        After validating, create the payment with `POST /api/v1/payment` using `type:
        "BOLETO"` and the `boletoBarcode`.

        Args:
            body (BoletoValidateRequest): The request body.

        Returns:
            BoletoValidateResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403.
        """
        path = "/api/v1/boleto/validate"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(BoletoValidateResponse, response.json())

    async def post_api_v1_cashback_fidelity(
        self,
        *,
        body: PostApiV1CashbackFidelityBody,
    ) -> PostApiV1CashbackFidelityResponse:
        """Get or create cashback for a customer.

        Create a new cashback exclusive for the customer with a given taxID. If the
        customer already has a pending excluisve cashback, this endpoint will return it
        instead.

        Args:
            body (PostApiV1CashbackFidelityBody): The request body.

        Returns:
            PostApiV1CashbackFidelityResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/cashback-fidelity"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1CashbackFidelityResponse, response.json())

    async def get_api_v1_cashback_fidelity_balance_by_tax_id(
        self,
        tax_id: str,
    ) -> GetApiV1CashbackFidelityBalanceByTaxIdResponse:
        """Get the exclusive cashback amount an user still has to receive by taxID.

        Args:
            tax_id (str): The raw tax ID from the customer you want to get the balance.

        Returns:
            GetApiV1CashbackFidelityBalanceByTaxIdResponse: The 200 response body,
                validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/cashback-fidelity/balance/{tax_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(
            GetApiV1CashbackFidelityBalanceByTaxIdResponse, response.json()
        )

    async def get_api_v1_charge(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        status: ChargeStatus | None = None,
        customer: str | None = None,
        subscription: str | None = None,
    ) -> GetApiV1ChargeResponse:
        """Get a list of charges.

        Args:
            start (datetime | None): The start value. Omitted from the query when None.
            end (datetime | None): The end value. Omitted from the query when None.
            status (ChargeStatus | None): The status value. Omitted from the query when
                None.
            customer (str | None): Customer Correlation ID Omitted from the query when
                None.
            subscription (str | None): Subscription Correlation ID Omitted from the
                query when None.

        Returns:
            GetApiV1ChargeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/charge"
        params: dict[str, Any] = {}
        if start is not None:
            params["start"] = _param(start)
        if end is not None:
            params["end"] = _param(end)
        if status is not None:
            params["status"] = _param(status)
        if customer is not None:
            params["customer"] = _param(customer)
        if subscription is not None:
            params["subscription"] = _param(subscription)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiV1ChargeResponse, response.json())

    async def post_api_v1_charge(
        self,
        *,
        body: ChargePayload,
        return_existing: bool | None = None,
    ) -> PostApiV1ChargeResponse:
        """Create a new Charge.

        Endpoint to create a new Charge for a customer

        Args:
            body (ChargePayload): The request body.
            return_existing (bool | None): Make the endpoint idempotent, will return an
                existent charge if already has a one with the correlationID Omitted from
                the query when None.

        Returns:
            PostApiV1ChargeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/charge"
        params: dict[str, Any] = {}
        if return_existing is not None:
            params["return_existing"] = _param(return_existing)
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            params=params,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1ChargeResponse, response.json())

    async def get_api_v1_charge_by_id(
        self,
        id: str,
    ) -> GetApiV1ChargeByIdResponse:
        r"""Get one charge.

        Args:
            id (str): charge ID or correlation ID. You will need URI encoding if your
                correlation ID has characters outside the ASCII set or reserved
                characters (%, \#, /).

        Returns:
            GetApiV1ChargeByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1ChargeByIdResponse, response.json())

    async def patch_api_v1_charge_by_id(
        self,
        id: str,
        *,
        body: ChargePatchPayload,
    ) -> PatchApiV1ChargeByIdResponse:
        r"""Edit expiration date of a charge.

        Args:
            id (str): correlation ID. You will need URI encoding if your correlation ID
                has characters outside the ASCII set or reserved characters (%, \#, /).
            body (ChargePatchPayload): The request body.

        Returns:
            PatchApiV1ChargeByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{id}"
        payload = _dump(body)
        response = await self._client.request(
            "PATCH",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PatchApiV1ChargeByIdResponse, response.json())

    async def delete_api_v1_charge_by_id(
        self,
        id: str,
    ) -> DeleteApiV1ChargeByIdResponse:
        r"""Delete a charge.

        Args:
            id (str): charge ID or correlation ID. You will need URI encoding if your
                correlation ID has characters outside the ASCII set or reserved
                characters (%, \#, /).

        Returns:
            DeleteApiV1ChargeByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteApiV1ChargeByIdResponse, response.json())

    async def get_api_v1_charge_by_id_refund(
        self,
        id: str,
    ) -> GetApiV1ChargeByIdRefundResponse:
        r"""Get all refunds of a charge.

        Endpoint to get all refunds of a charge

        Args:
            id (str): The correlation ID of the charge. You will need URI encoding if
                your correlation ID has characters outside the ASCII set or reserved
                characters (%, \#, /).

        Returns:
            GetApiV1ChargeByIdRefundResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{id}/refund"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1ChargeByIdRefundResponse, response.json())

    async def post_api_v1_charge_by_id_refund(
        self,
        id: str,
        *,
        body: ChargeRefundPayload,
    ) -> PostApiV1ChargeByIdRefundResponse:
        r"""Create a new refund for a charge.

        Endpoint to create a new refund for a charge

        Args:
            id (str): The correlation ID of the charge. You will need URI encoding if
                your correlation ID has characters outside the ASCII set or reserved
                characters (%, \#, /).
            body (ChargeRefundPayload): The request body.

        Returns:
            PostApiV1ChargeByIdRefundResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{id}/refund"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1ChargeByIdRefundResponse, response.json())

    async def get_api_v1_company(self) -> GetApiV1CompanyResponse:
        """Get a Company.

        Returns:
            GetApiV1CompanyResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/company"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1CompanyResponse, response.json())

    async def get_api_v1_customer(self) -> GetApiV1CustomerResponse:
        """Get a list of customers.

        Returns:
            GetApiV1CustomerResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/customer"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1CustomerResponse, response.json())

    async def post_api_v1_customer(
        self,
        *,
        body: CustomerPayload,
    ) -> PostApiV1CustomerResponse:
        """Create a new Customer.

        Endpoint to create a new Customer

        Args:
            body (CustomerPayload): The request body.

        Returns:
            PostApiV1CustomerResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/customer"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1CustomerResponse, response.json())

    async def patch_api_v1_customer_by_correlation_id(
        self,
        correlation_id: str,
        *,
        body: CustomerPatchPayload,
    ) -> PatchApiV1CustomerByCorrelationIdResponse:
        """Update a Customer.

        Endpoint to update a Customer

        Args:
            correlation_id (str): correlation ID
            body (CustomerPatchPayload): The request body.

        Returns:
            PatchApiV1CustomerByCorrelationIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/customer/{correlation_id}"
        payload = _dump(body)
        response = await self._client.request(
            "PATCH",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PatchApiV1CustomerByCorrelationIdResponse, response.json())

    async def get_api_v1_customer_by_id(
        self,
        id: str,
    ) -> GetApiV1CustomerByIdResponse:
        """Get one customer.

        Args:
            id (str): Correlation ID or Tax ID

        Returns:
            GetApiV1CustomerByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/customer/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1CustomerByIdResponse, response.json())

    async def post_api_v1_decode_emv(
        self,
        *,
        body: PostApiV1DecodeEmvBody,
    ) -> PostApiV1DecodeEmvResponse:
        """Parse EMV (PIX) QR code and optionally resolve COB/REC locations.

        Args:
            body (PostApiV1DecodeEmvBody): The request body.

        Returns:
            PostApiV1DecodeEmvResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 500.
        """
        path = "/api/v1/decode/emv"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1DecodeEmvResponse, response.json())

    async def get_api_v1_dispute(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> GetApiV1DisputeResponse:
        """Get a list of disputes.

        Args:
            start (datetime | None): The start value. Omitted from the query when None.
            end (datetime | None): The end value. Omitted from the query when None.

        Returns:
            GetApiV1DisputeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/dispute"
        params: dict[str, Any] = {}
        if start is not None:
            params["start"] = _param(start)
        if end is not None:
            params["end"] = _param(end)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiV1DisputeResponse, response.json())

    async def post_api_v1_dispute_id_evidence(
        self,
        *,
        body: PostApiV1DisputeIdEvidenceBody,
    ) -> PostApiV1DisputeIdEvidenceResponse:
        r"""Upload new evidence.

        Upload evidence files for dispute/med. \nOBS para obter esse o id da disputa,
        veja esse artigo
        https://developers.woovi.com/docs/disputa/how-add-new-evidence-in-dispute#1-obter-o-id-da-disputa

        Args:
            body (PostApiV1DisputeIdEvidenceBody): The request body.

        Returns:
            PostApiV1DisputeIdEvidenceResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/dispute/:id/evidence"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1DisputeIdEvidenceResponse, response.json())

    async def get_api_v1_dispute_by_id(
        self,
        id: str,
    ) -> GetApiV1DisputeByIdResponse:
        """Get one dispute.

        Args:
            id (str): The id must be the endToEndId of the transaction that originated
                the Dispute

        Returns:
            GetApiV1DisputeByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 500.
        """
        path = f"/api/v1/dispute/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1DisputeByIdResponse, response.json())

    async def post_api_v1_funds_recovery(
        self,
        *,
        body: FundsRecoveryPayload,
    ) -> FundsRecovery:
        """Open a funds recovery (MED).

        Endpoint to open a funds recovery (MED) for a Pix transaction sent from your
        account.

        Only one funds recovery can be opened per transaction. The transaction must have
        been sent from your account and cannot have been rejected.

        Args:
            body (FundsRecoveryPayload): The request body.

        Returns:
            FundsRecovery: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404, 422.
        """
        path = "/api/v1/funds-recovery"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(FundsRecovery, response.json())

    async def get_api_v1_funds_recovery_by_id(
        self,
        id: UUID,
    ) -> FundsRecovery:
        """Get one funds recovery (MED).

        Endpoint to get a funds recovery (MED). Use it to follow the progress of the
        funds recovery through the `status` and `events` fields.

        Args:
            id (UUID): The `dictId` returned when the funds recovery was created

        Returns:
            FundsRecovery: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/api/v1/funds-recovery/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(FundsRecovery, response.json())

    async def post_api_v1_funds_recovery_by_id_cancel(
        self,
        id: UUID,
    ) -> FundsRecovery:
        """Cancel a funds recovery (MED).

        Endpoint to cancel a funds recovery (MED). The request does not need a body.

        Only funds recoveries opened by your account that have not reached a terminal
        status (`COMPLETED` or `CANCELLED`) can be cancelled.

        Args:
            id (UUID): The `dictId` returned when the funds recovery was created

        Returns:
            FundsRecovery: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404, 422.
        """
        path = f"/api/v1/funds-recovery/{id}/cancel"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(FundsRecovery, response.json())

    async def get_api_v1_installments_by_id(
        self,
        id: str,
    ) -> GetApiV1InstallmentsByIdResponse:
        """Get one installment.

        Args:
            id (str): The globalID of the installment or the endToEndId from
                transaction.

        Returns:
            GetApiV1InstallmentsByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/installments/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1InstallmentsByIdResponse, response.json())

    async def post_api_v1_installments_by_id_cobr(
        self,
        id: str,
        *,
        body: PostApiV1InstallmentsByIdCobrBody | None = None,
    ) -> dict[str, Any]:
        """Create a new Cobr Manually.

        Create a new Cobr Manually.

        Args:
            id (str): The globalID of the installment.
            body (PostApiV1InstallmentsByIdCobrBody): The request body. Optional.

        Returns:
            dict[str, Any]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/installments/{id}/cobr"
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(dict[str, Any], response.json())

    async def post_api_v1_installments_by_id_cobr_retry(
        self,
        id: str,
        *,
        body: PostApiV1InstallmentsByIdCobrRetryBody | None = None,
    ) -> dict[str, Any]:
        """Create a new Retry Manually.

        Create a new Retry Manually.

        Args:
            id (str): The globalID of the installment.
            body (PostApiV1InstallmentsByIdCobrRetryBody): The request body. Optional.

        Returns:
            dict[str, Any]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/installments/{id}/cobr/retry"
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(dict[str, Any], response.json())

    async def get_api_v1_invoice(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        skip: float | None = None,
        limit: float | None = None,
    ) -> Any:
        """Get invoices.

        Args:
            start (str | None): The start value. Omitted from the query when None.
            end (str | None): The end value. Omitted from the query when None.
            skip (float | None): The skip value. Omitted from the query when None.
            limit (float | None): The limit value. Omitted from the query when None.

        Returns:
            Any: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403.
        """
        path = "/api/v1/invoice"
        params: dict[str, Any] = {}
        if start is not None:
            params["start"] = _param(start)
        if end is not None:
            params["end"] = _param(end)
        if skip is not None:
            params["skip"] = _param(skip)
        if limit is not None:
            params["limit"] = _param(limit)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(Any, response.json())

    async def post_api_v1_invoice(
        self,
        *,
        body: Any,
    ) -> PostApiV1InvoiceResponse:
        """Create a new invoice.

        Args:
            body (Any): The request body.

        Returns:
            PostApiV1InvoiceResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403.
        """
        path = "/api/v1/invoice"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1InvoiceResponse, response.json())

    async def get_api_v1_invoice_integration(self) -> Any:
        """Get the NFe.io integration status and config for the authenticated company.

        Returns:
            Any: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 403, 404.
        """
        path = "/api/v1/invoice/integration"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(Any, response.json())

    async def post_api_v1_invoice_integration(
        self,
        *,
        body: PostApiV1InvoiceIntegrationBody | None = None,
    ) -> PostApiV1InvoiceIntegrationResponse:
        """Create or upsert the NFe.io integration for the authenticated company.

        Upserts the NFe.io integration for the authenticated company and sets its tax
        fields. Optionally activates it (only allowed once configured).

        Args:
            body (PostApiV1InvoiceIntegrationBody): The request body. Optional.

        Returns:
            PostApiV1InvoiceIntegrationResponse: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404, 409.
        """
        path = "/api/v1/invoice/integration"
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1InvoiceIntegrationResponse, response.json())

    async def put_api_v1_invoice_integration(
        self,
        *,
        body: PutApiV1InvoiceIntegrationBody,
    ) -> PutApiV1InvoiceIntegrationResponse:
        """Update the tax fields of the invoice integration.

        Updates the tax configuration of the authenticated company's existing NFEIO
        integration (city service code, municipal subscription, rps number, special tax,
        tax regime, legal nature and tax determination fields). The integration must
        already exist; otherwise a 404 is returned. The response never echoes
        credentials.

        Args:
            body (PutApiV1InvoiceIntegrationBody): The request body.

        Returns:
            PutApiV1InvoiceIntegrationResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404.
        """
        path = "/api/v1/invoice/integration"
        payload = _dump(body)
        response = await self._client.request(
            "PUT",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PutApiV1InvoiceIntegrationResponse, response.json())

    async def patch_api_v1_invoice_integration(
        self,
        *,
        body: PatchApiV1InvoiceIntegrationBody,
    ) -> PatchApiV1InvoiceIntegrationResponse:
        """Activate or deactivate the NFe.io integration for the authenticated company.

        Args:
            body (PatchApiV1InvoiceIntegrationBody): The request body.

        Returns:
            PatchApiV1InvoiceIntegrationResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = "/api/v1/invoice/integration"
        payload = _dump(body)
        response = await self._client.request(
            "PATCH",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PatchApiV1InvoiceIntegrationResponse, response.json())

    async def post_api_v1_invoice_integration_certificate(
        self,
        *,
        body: PostApiV1InvoiceIntegrationCertificateBody,
    ) -> PostApiV1InvoiceIntegrationCertificateResponse:
        """Upload the NFe.io A1 certificate for the invoice integration.

        Uploads the company's NFe.io A1 certificate (base64-encoded pkcs12) to the
        configured NFEIO integration. The response returns only the resulting
        integration status and never echoes the certificate, passphrase or credentials.

        Args:
            body (PostApiV1InvoiceIntegrationCertificateBody): The request body.

        Returns:
            PostApiV1InvoiceIntegrationCertificateResponse: The 200 response body,
                validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = "/api/v1/invoice/integration/certificate"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(
            PostApiV1InvoiceIntegrationCertificateResponse, response.json()
        )

    async def post_api_v1_invoice_integration_test(
        self,
    ) -> PostApiV1InvoiceIntegrationTestResponse:
        """Issue a NFe.io test invoice for the invoice integration.

        Issues a test NFe.io invoice for the authenticated company's NFEIO integration.
        This is the bootstrap step that moves the integration to VALIDATING; once NFe.io
        confirms the test note via webhook the integration becomes CONFIGURED and
        active, which unblocks real invoice issuance. A configured integration can no
        longer issue test invoices.

        Returns:
            PostApiV1InvoiceIntegrationTestResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404.
        """
        path = "/api/v1/invoice/integration/test"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(PostApiV1InvoiceIntegrationTestResponse, response.json())

    async def post_api_v1_invoice_by_correlation_id_cancel(
        self,
        correlation_id: str,
    ) -> PostApiV1InvoiceByCorrelationIdCancelResponse:
        """Cancel an invoice.

        Args:
            correlation_id (str): The correlationID value.

        Returns:
            PostApiV1InvoiceByCorrelationIdCancelResponse: The 200 response body,
                validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/api/v1/invoice/{correlation_id}/cancel"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(PostApiV1InvoiceByCorrelationIdCancelResponse, response.json())

    # openapi: unsupported — response of GetApiV1InvoiceByCorrelationIdPdf uses
    #   application/pdf — only application/json is modelled
    async def get_api_v1_invoice_by_correlation_id_pdf(
        self,
        correlation_id: str,
    ) -> None:
        """Get invoice PDF document.

        Args:
            correlation_id (str): The correlationID value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 403, 404, 500.
        """
        path = f"/api/v1/invoice/{correlation_id}/pdf"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    # openapi: unsupported — response of GetApiV1InvoiceByCorrelationIdXml uses
    #   application/xml — only application/json is modelled
    async def get_api_v1_invoice_by_correlation_id_xml(
        self,
        correlation_id: str,
    ) -> None:
        """Get invoice XML document.

        Args:
            correlation_id (str): The correlationID value.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 403, 404, 500.
        """
        path = f"/api/v1/invoice/{correlation_id}/xml"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def post_api_v1_kyc_onboarding(
        self,
        *,
        body: KycOnboardingRequest,
    ) -> PostApiV1KycOnboardingResponse:
        """Create a KYC onboarding.

        Creates a new KYC onboarding for a merchant. Returns a link that should be sent
        to the merchant so they can fill in their registration data.

        The API is idempotent by `correlationID`. If the same `correlationID` is sent
        again for the same company, the API returns the existing onboarding link (200
        OK) instead of creating a new one.

        The fields `officialName`, `tradeName` and `representatives[].name` are
        automatically populated via data enrichment when available. You do not need to
        send them in the request.

        If `redirectUrl` is provided, the merchant is automatically redirected to that
        URL 5 seconds after completing the onboarding flow (terminal states: submitted,
        approved, or rejected). The `redirectUrl` is bound to the onboarding link at
        creation time and cannot be changed later — subsequent idempotent calls will
        return the original value.

        Args:
            body (KycOnboardingRequest): The request body.

        Returns:
            PostApiV1KycOnboardingResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 500.
        """
        path = "/api/v1/kyc/onboarding"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1KycOnboardingResponse, response.json())

    async def get_api_v1_limits_by_account_id(
        self,
        account_id: str,
    ) -> GetApiV1LimitsByAccountIdResponse:
        """Get account limits.

        Retrieves the most recent account limits configured for a given bank account.
        Only the public-safe fields are returned; internal-only fields are stripped from
        the response.

        Args:
            account_id (str): Bank account identifier (ObjectId) for which limits should
                be returned

        Returns:
            GetApiV1LimitsByAccountIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/api/v1/limits/{account_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1LimitsByAccountIdResponse, response.json())

    async def get_api_v1_partner_affiliate(self) -> GetApiV1PartnerAffiliateResponse:
        """Get every affiliate company that is managed by you.

        Returns:
            GetApiV1PartnerAffiliateResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/partner/affiliate"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1PartnerAffiliateResponse, response.json())

    async def post_api_v1_partner_application(
        self,
        *,
        body: PostApiV1PartnerApplicationBody,
    ) -> PostApiV1PartnerApplicationResponse:
        """Create a new application to some of your preregistration's company.

        As a partner company, you can create a new application to some of your
        companies. The application should give access to our API to this companies, so
        they can use it too.

        Args:
            body (PostApiV1PartnerApplicationBody): The request body.

        Returns:
            PostApiV1PartnerApplicationResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = "/api/v1/partner/application"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1PartnerApplicationResponse, response.json())

    async def get_api_v1_partner_company(self) -> GetApiV1PartnerCompanyResponse:
        """Get every preregistration that is managed by you.

        Returns:
            GetApiV1PartnerCompanyResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/partner/company"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1PartnerCompanyResponse, response.json())

    async def post_api_v1_partner_company(
        self,
        *,
        body: PreRegistrationPayloadObject,
    ) -> PreRegistrationPayloadObject:
        """Create a pre registration with a partner reference (your company).

        As a partner company, you can create a new pre registration referencing your
        company as a partner.

        Args:
            body (PreRegistrationPayloadObject): The request body.

        Returns:
            PreRegistrationPayloadObject: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = "/api/v1/partner/company"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PreRegistrationPayloadObject, response.json())

    async def get_api_v1_partner_company_by_tax_id(
        self,
        tax_id: str,
    ) -> GetApiV1PartnerCompanyByTaxIdResponse:
        """Get an specific preregistration via taxID param.

        Args:
            tax_id (str): The raw tax ID from the preregistration that you want to get.

        Returns:
            GetApiV1PartnerCompanyByTaxIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/partner/company/{tax_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1PartnerCompanyByTaxIdResponse, response.json())

    async def get_api_v1_payment(self) -> GetApiV1PaymentResponse:
        """Get a list of payments.

        Returns:
            GetApiV1PaymentResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/payment"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1PaymentResponse, response.json())

    async def post_api_v1_payment(
        self,
        *,
        body: PostApiV1PaymentBody,
    ) -> PostApiV1PaymentResponse:
        """Create a Payment Request.

        Endpoint to request a payment. Supports four payment types: Pix Key (`PIX_KEY`),
        QR Code (`QR_CODE`), Manual (`MANUAL`), and Boleto (`BOLETO`).

        For QR Code payments, the system decodes the BR Code string and extracts the
        destination and value automatically.

        For Boleto payments, send `type: "BOLETO"` and the `boletoBarcode`. The amount,
        due date and beneficiary are resolved from the boleto, so `value` and
        destination are not sent in the body. Validate the barcode first with `POST
        /api/v1/boleto/validate`.

        Set `autoApprove: true` to create and immediately approve the payment in a
        single call, returning the enriched response with transaction and destination
        data. Without this flag, the payment is created in `CREATED` status and can be
        approved later via `POST /api/v1/payment/approve`.

        Args:
            body (PostApiV1PaymentBody): The request body.

        Returns:
            PostApiV1PaymentResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/payment"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1PaymentResponse, response.json())

    async def post_api_v1_payment_approve(
        self,
        *,
        body: PaymentApprovePayload,
    ) -> PostApiV1PaymentApproveResponse:
        """Approve a Payment Request.

        Endpoint to approve a payment

        Args:
            body (PaymentApprovePayload): The request body.

        Returns:
            PostApiV1PaymentApproveResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/payment/approve"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1PaymentApproveResponse, response.json())

    async def get_api_v1_payment_by_id(
        self,
        id: str,
    ) -> GetApiV1PaymentByIdResponse:
        """Get one Payment.

        Args:
            id (str): payment ID or correlation ID

        Returns:
            GetApiV1PaymentByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/payment/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1PaymentByIdResponse, response.json())

    async def get_api_v1_pix_keys(
        self,
        *,
        skip: float | None = None,
        limit: float | None = None,
    ) -> GetApiV1PixKeysResponse:
        """Get all Pix keys.

        Retrieves a list of all Pix keys

        Args:
            skip (float | None): The skip value. Omitted from the query when None.
            limit (float | None): The limit value. Omitted from the query when None.

        Returns:
            GetApiV1PixKeysResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403.
        """
        path = "/api/v1/pix-keys"
        params: dict[str, Any] = {}
        if skip is not None:
            params["skip"] = _param(skip)
        if limit is not None:
            params["limit"] = _param(limit)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiV1PixKeysResponse, response.json())

    async def post_api_v1_pix_keys(
        self,
        *,
        body: PixKeyCreate,
    ) -> PixKey:
        """Create a new Pix key.

        Creates a new Pix key

        Args:
            body (PixKeyCreate): The request body.

        Returns:
            PixKey: The 201 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403.
        """
        path = "/api/v1/pix-keys"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PixKey, response.json())

    async def post_api_v1_pix_keys_check(
        self,
        *,
        body: PostApiV1PixKeysCheckBody,
    ) -> PixKeyCheck:
        """Check data from a Pix key.

        Get data from a Pix key if it exists

        Args:
            body (PostApiV1PixKeysCheckBody): The request body.

        Returns:
            PixKeyCheck: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404, 429.
        """
        path = "/api/v1/pix-keys/check"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PixKeyCheck, response.json())

    async def get_api_v1_pix_keys_tokens(self) -> PixKeyTokens:
        """Get tokens data.

        Returns:
            PixKeyTokens: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/api/v1/pix-keys/tokens"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(PixKeyTokens, response.json())

    async def get_api_v1_pix_keys_tokens_logs(
        self,
        *,
        skip: float | None = None,
        limit: float | None = None,
        company_bank_account: str | None = None,
    ) -> GetApiV1PixKeysTokensLogsResponse:
        """Get token bucket logs.

        Get a list of token bucket operation logs

        Args:
            skip (float | None): The skip value. Omitted from the query when None.
            limit (float | None): The limit value. Omitted from the query when None.
            company_bank_account (str | None): Filter logs by company bank account ID
                Omitted from the query when None.

        Returns:
            GetApiV1PixKeysTokensLogsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403.
        """
        path = "/api/v1/pix-keys/tokens/logs"
        params: dict[str, Any] = {}
        if skip is not None:
            params["skip"] = _param(skip)
        if limit is not None:
            params["limit"] = _param(limit)
        if company_bank_account is not None:
            params["companyBankAccount"] = _param(company_bank_account)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiV1PixKeysTokensLogsResponse, response.json())

    async def delete_api_v1_pix_keys_by_pix_key(
        self,
        pix_key: str,
    ) -> None:
        """Delete a Pix key.

        Deletes a specific Pix key, you cannot delete the default pix key

        Args:
            pix_key (str): The Pix key to delete

        Returns:
            None: Nothing — the operation answers 204 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/api/v1/pix-keys/{pix_key}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def get_api_v1_pix_keys_by_pix_key_check(
        self,
        pix_key: str,
    ) -> PixKeyCheck:
        """Check data from a Pix key.

        Get data from a Pix key if it exists

        Args:
            pix_key (str): The Pix key to check

        Returns:
            PixKeyCheck: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404, 429.
        """
        path = f"/api/v1/pix-keys/{pix_key}/check"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(PixKeyCheck, response.json())

    async def put_api_v1_pix_keys_by_pix_key_default(
        self,
        pix_key: str,
    ) -> PixKey:
        """Set a pix key as default.

        Args:
            pix_key (str): The pix key to set as default

        Returns:
            PixKey: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = f"/api/v1/pix-keys/{pix_key}/default"
        response = await self._client.request(
            "PUT",
            path,
        )
        response.raise_for_status()
        return _validate(PixKey, response.json())

    async def get_api_v1_psp(
        self,
        *,
        ispb: str | None = None,
        name: str | None = None,
        compe: str | None = None,
    ) -> GetApiV1PspResponse:
        """Get a list of PSPs (Payment Service Providers).

        Args:
            ispb (str | None): Filter PSPs by ISPB code Omitted from the query when
                None.
            name (str | None): Filter PSPs by name Omitted from the query when None.
            compe (str | None): Filter PSPs by COMPE code Omitted from the query when
                None.

        Returns:
            GetApiV1PspResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 500.
        """
        path = "/api/v1/psp"
        params: dict[str, Any] = {}
        if ispb is not None:
            params["ispb"] = _param(ispb)
        if name is not None:
            params["name"] = _param(name)
        if compe is not None:
            params["compe"] = _param(compe)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiV1PspResponse, response.json())

    async def get_api_v1_qrcode_static(self) -> GetApiV1QrcodeStaticResponse:
        """Get a list of Pix QrCodes.

        Returns:
            GetApiV1QrcodeStaticResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/qrcode-static"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1QrcodeStaticResponse, response.json())

    async def post_api_v1_qrcode_static(
        self,
        *,
        body: PixQrCodePayload,
    ) -> PostApiV1QrcodeStaticResponse:
        """Create a new Pix QrCode Static.

        Endpoint to create a new Pix QrCode Static

        Args:
            body (PixQrCodePayload): The request body.

        Returns:
            PostApiV1QrcodeStaticResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/qrcode-static"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1QrcodeStaticResponse, response.json())

    async def get_api_v1_qrcode_static_by_id(
        self,
        id: str,
    ) -> GetApiV1QrcodeStaticByIdResponse:
        """Get one Pix QrCode.

        Args:
            id (str): pixQrCode ID, correlation ID or emv identifier

        Returns:
            GetApiV1QrcodeStaticByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/qrcode-static/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1QrcodeStaticByIdResponse, response.json())

    async def delete_api_v1_qrcode_static_by_id(
        self,
        id: str,
    ) -> DeleteApiV1QrcodeStaticByIdResponse:
        """Delete a Pix QrCode Static.

        Endpoint to delete a Pix QrCode Static

        Args:
            id (str): QrCode ID, correlationID or identifier

        Returns:
            DeleteApiV1QrcodeStaticByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/qrcode-static/{id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteApiV1QrcodeStaticByIdResponse, response.json())

    # openapi: unsupported — response of GetApiV1ReceiptByReceiptTypeByEndToEndId uses
    #   application/pdf — only application/json is modelled
    async def get_api_v1_receipt_by_receipt_type_by_end_to_end_id(
        self,
        receipt_type: GetApiV1ReceiptByReceiptTypeByEndToEndIdReceiptType,
        end_to_end_id: str,
    ) -> None:
        """Get a PDF document related to a payment transaction formatted as a receipt by
        type (pix-in, pix-out or pix-refund).

        Args:
            receipt_type (GetApiV1ReceiptByReceiptTypeByEndToEndIdReceiptType): The
                ReceiptType from the payment transaction to export.
            end_to_end_id (str): The EndToEndId from the payment transaction to export.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = f"/api/v1/receipt/{receipt_type}/{end_to_end_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def get_api_v1_refund(self) -> GetApiV1RefundResponse:
        """Get a list of refunds.

        Returns:
            GetApiV1RefundResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/refund"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1RefundResponse, response.json())

    async def post_api_v1_refund(
        self,
        *,
        body: RefundPayload,
    ) -> PostApiV1RefundResponse:
        """Create a new refund.

        Endpoint to create a new refund for a customer

        Args:
            body (RefundPayload): The request body.

        Returns:
            PostApiV1RefundResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/refund"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1RefundResponse, response.json())

    async def get_api_v1_refund_by_id(
        self,
        id: str,
    ) -> GetApiV1RefundByIdResponse:
        """Get one refund.

        Args:
            id (str): refund ID or correlation ID

        Returns:
            GetApiV1RefundByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/refund/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1RefundByIdResponse, response.json())

    async def post_api_v1_stablecoin_deposit(
        self,
        *,
        body: StablecoinDepositRequest,
    ) -> StablecoinDepositResponse:
        """Create a stablecoin deposit.

        Creates a stablecoin deposit (PIX-in to stable-out) for a company. The deposit
        converts a BRL amount (in cents) into the requested stablecoin on the chosen
        network and returns a quote with the applied fees.

        The company must have a stable subaccount in `CONFIRMED` status (a completed
        KYB). Otherwise the request is rejected with a `400`.

        Not every asset is available on every network. The supported matrix is: - USDT:
        POLYGON, ETHEREUM, CELO, TRON - USDC: POLYGON, ETHEREUM, BASE, CELO - BRLA:
        POLYGON, ETHEREUM, BASE, CELO

        If `network` is omitted it defaults to `POLYGON`. Sending an asset/network
        combination outside the matrix above returns a `400`.

        Idempotency is supported via `correlationId`.

        Args:
            body (StablecoinDepositRequest): The request body.

        Returns:
            StablecoinDepositResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/api/v1/stablecoin/deposit"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(StablecoinDepositResponse, response.json())

    async def post_api_v1_stablecoin_deposit_approve(
        self,
        *,
        body: PostApiV1StablecoinDepositApproveBody,
    ) -> PostApiV1StablecoinDepositApproveResponse:
        """Approve (settle) a stablecoin deposit.

        Approves a previously created stablecoin deposit identified by its
        `correlationId`, triggering the on-chain settlement (pay the stable qrcode) for
        the company's deposit.

        The deposit moves to `PROCESSING` while settlement is in flight. The call is
        rejected with `400` when the deposit cannot be approved, e.g. it was already
        `COMPLETED`, it is already `PROCESSING`, there is no source account to pay it,
        or the provider quote/payment fails.

        Requires the `STABLECOIN_DEPOSIT_CREATE` scope.

        Args:
            body (PostApiV1StablecoinDepositApproveBody): The request body.

        Returns:
            PostApiV1StablecoinDepositApproveResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/api/v1/stablecoin/deposit/approve"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1StablecoinDepositApproveResponse, response.json())

    async def get_api_v1_stablecoin_quote(
        self,
        *,
        value: float,
        currency: StablecoinDepositRequestCurrency | None = None,
    ) -> GetApiV1StablecoinQuoteResponse:
        """Get a stablecoin quote without creating a deposit.

        Returns a PIX (BRL) -> stablecoin quote for the given `value` and `currency`
        without creating a deposit. Use it to display the exact amount of stablecoin the
        customer would receive before confirming.

        The quote is fetched from the provider and cached for 60 seconds.

        Requires the `STABLECOIN_DEPOSIT_CREATE` scope.

        Args:
            value (float): Amount to quote, in cents (BRL). Must be positive.
            currency (StablecoinDepositRequestCurrency | None): Stablecoin to receive.
                Defaults to USDT. Omitted from the query when None.

        Returns:
            GetApiV1StablecoinQuoteResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 502.
        """
        path = "/api/v1/stablecoin/quote"
        params: dict[str, Any] = {}
        params["value"] = _param(value)
        if currency is not None:
            params["currency"] = _param(currency)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiV1StablecoinQuoteResponse, response.json())

    async def get_api_v1_stablecoin_subaccount(
        self,
    ) -> StablecoinSubAccountListResponse:
        """List a company's stablecoin subaccounts.

        Lists the authenticated company's stablecoin subaccounts, most recent first.

        A subaccount is created when the company completes a KYB with the stablecoin
        provider. Use this endpoint to discover the `subAccountId` values available to
        the company.

        Requires the `STABLECOIN_SUBACCOUNT_LIST` scope.

        Returns:
            StablecoinSubAccountListResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401.
        """
        path = "/api/v1/stablecoin/subaccount"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(StablecoinSubAccountListResponse, response.json())

    async def post_api_v1_stablecoin_subaccount(
        self,
        *,
        body: StablecoinSubAccountCreateRequest,
    ) -> StablecoinSubAccountCreateResponse:
        """Request a new stablecoin subaccount (KYB).

        Requests the creation of a stablecoin subaccount for the authenticated company,
        reusing the KYC data already on the referenced account register.

        Pass the company's `accountRegisterId`; the provider subaccount is created
        immediately and a `StableSubAccount` is persisted with status `IN_REVIEW` while
        the KYB is processed. When the KYB resolves, the merchant receives a
        `STABLECOIN_SUBACCOUNT_CONFIRMED` or `STABLECOIN_SUBACCOUNT_REJECTED` webhook.

        The request is idempotent on `accountRegisterId`: a repeat call returns the
        existing subaccount (HTTP `200`) instead of creating a duplicate. The first,
        creating call returns HTTP `201`.

        Requires the `STABLECOIN_SUBACCOUNT_CREATE` scope and the company `STABLECOIN`
        feature.

        Args:
            body (StablecoinSubAccountCreateRequest): The request body.

        Returns:
            StablecoinSubAccountCreateResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 502.
        """
        path = "/api/v1/stablecoin/subaccount"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(StablecoinSubAccountCreateResponse, response.json())

    async def get_api_v1_stablecoin_subaccount_by_sub_account_id(
        self,
        sub_account_id: str,
    ) -> StablecoinSubAccountGetResponse:
        """Get a stablecoin subaccount by id.

        Fetches a single stablecoin subaccount for the authenticated company by its
        provider `subAccountId`.

        Returns `404` when no subaccount with that `subAccountId` exists for the
        company.

        Requires the `STABLECOIN_SUBACCOUNT_LIST` scope.

        Args:
            sub_account_id (str): The provider subaccount id.

        Returns:
            StablecoinSubAccountGetResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 404.
        """
        path = f"/api/v1/stablecoin/subaccount/{sub_account_id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(StablecoinSubAccountGetResponse, response.json())

    async def get_api_v1_statement(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        skip: float | None = None,
        limit: float | None = None,
    ) -> list[GetApiV1StatementResponseItem]:
        """Get statement by company.

        Retrieves the statement/ledger entries for a company's bank account

        Args:
            start (datetime | None): The start value. Omitted from the query when None.
            end (datetime | None): The end value. Omitted from the query when None.
            skip (float | None): The skip value. Omitted from the query when None.
            limit (float | None): The limit value. Omitted from the query when None.

        Returns:
            list[GetApiV1StatementResponseItem]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 500.
        """
        path = "/api/v1/statement"
        params: dict[str, Any] = {}
        if start is not None:
            params["start"] = _param(start)
        if end is not None:
            params["end"] = _param(end)
        if skip is not None:
            params["skip"] = _param(skip)
        if limit is not None:
            params["limit"] = _param(limit)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(list[GetApiV1StatementResponseItem], response.json())

    async def get_api_v1_subaccount(self) -> GetApiV1SubaccountResponse:
        """Get a list of subaccounts.

        Returns:
            GetApiV1SubaccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/subaccount"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1SubaccountResponse, response.json())

    async def post_api_v1_subaccount(
        self,
        *,
        body: SubAccountPayload,
    ) -> PostApiV1SubaccountResponse:
        """Create a subaccount.

        Args:
            body (SubAccountPayload): The request body.

        Returns:
            PostApiV1SubaccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/subaccount"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1SubaccountResponse, response.json())

    async def post_api_v1_subaccount_transfer(
        self,
        *,
        body: SubAccountTransferPayload,
    ) -> SubAccountTransferResponsePayload:
        """Transfer between subaccounts.

        Args:
            body (SubAccountTransferPayload): The request body.

        Returns:
            SubAccountTransferResponsePayload: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/subaccount/transfer"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(SubAccountTransferResponsePayload, response.json())

    async def get_api_v1_subaccount_by_id(
        self,
        id: str,
    ) -> GetApiV1SubaccountByIdResponse:
        """Get subaccount details.

        Args:
            id (str): pix key registered to the subaccount

        Returns:
            GetApiV1SubaccountByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subaccount/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1SubaccountByIdResponse, response.json())

    async def delete_api_v1_subaccount_by_id(
        self,
        id: str,
    ) -> DeleteApiV1SubaccountByIdResponse:
        """Delete a Sub Account.

        Deletes a Sub Account if it has no remaining balance

        Args:
            id (str): Pix key registered to the subaccount

        Returns:
            DeleteApiV1SubaccountByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/subaccount/{id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteApiV1SubaccountByIdResponse, response.json())

    async def post_api_v1_subaccount_by_id_credit(
        self,
        id: str,
        *,
        body: PostApiV1SubaccountByIdCreditBody,
    ) -> PostApiV1SubaccountByIdCreditResponse:
        """Credit subaccount.

        Transfers the amount from the main account to the subaccount.

        Args:
            id (str): Pix key registered to the subaccount
            body (PostApiV1SubaccountByIdCreditBody): The request body.

        Returns:
            PostApiV1SubaccountByIdCreditResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/subaccount/{id}/credit"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1SubaccountByIdCreditResponse, response.json())

    async def post_api_v1_subaccount_by_id_debit(
        self,
        id: str,
        *,
        body: PostApiV1SubaccountByIdDebitBody,
    ) -> PostApiV1SubaccountByIdDebitResponse:
        """Debit subaccount.

        Transfers the amount from the subaccount to the main account.

        Args:
            id (str): Pix key registered to the subaccount
            body (PostApiV1SubaccountByIdDebitBody): The request body.

        Returns:
            PostApiV1SubaccountByIdDebitResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/subaccount/{id}/debit"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1SubaccountByIdDebitResponse, response.json())

    async def get_api_v1_subaccount_by_id_statement(
        self,
        id: str,
        *,
        skip: int | None = None,
        limit: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[GetApiV1SubaccountByIdStatementResponseItem]:
        """Get Sub Account statement.

        Returns the ledger entries (statement) for a specific subaccount.

        Args:
            id (str): Pix key registered to the subaccount
            skip (int | None): Number of entries to skip for pagination Omitted from the
                query when None.
            limit (int | None): Maximum number of entries to return Omitted from the
                query when None.
            start (datetime | None): Start date for filtering entries (ISO 8601 format)
                Omitted from the query when None.
            end (datetime | None): End date for filtering entries (ISO 8601 format)
                Omitted from the query when None.

        Returns:
            list[GetApiV1SubaccountByIdStatementResponseItem]: The 200 response body,
                validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/subaccount/{id}/statement"
        params: dict[str, Any] = {}
        if skip is not None:
            params["skip"] = _param(skip)
        if limit is not None:
            params["limit"] = _param(limit)
        if start is not None:
            params["start"] = _param(start)
        if end is not None:
            params["end"] = _param(end)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(
            list[GetApiV1SubaccountByIdStatementResponseItem], response.json()
        )

    async def post_api_v1_subaccount_by_id_withdraw(
        self,
        id: str,
        *,
        body: SubAccountWithdrawPayload,
    ) -> PostApiV1SubaccountByIdWithdrawResponse:
        """Withdraw from a Sub Account.

        Withdraw from a Sub Account and return the withdrawal transaction information

        Args:
            id (str): pix key registered to the subaccount
            body (SubAccountWithdrawPayload): The request body.

        Returns:
            PostApiV1SubaccountByIdWithdrawResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subaccount/{id}/withdraw"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1SubaccountByIdWithdrawResponse, response.json())

    async def get_api_v1_subscriptions(self) -> GetApiV1SubscriptionsResponse:
        """Get a list of subscriptions.

        Returns:
            GetApiV1SubscriptionsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/subscriptions"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1SubscriptionsResponse, response.json())

    async def post_api_v1_subscriptions(
        self,
        *,
        body: SubscriptionPayload,
    ) -> PostApiV1SubscriptionsResponse:
        """Create a new Subscription.

        Endpoint to create a new Subcription

        Args:
            body (SubscriptionPayload): The request body.

        Returns:
            PostApiV1SubscriptionsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/subscriptions"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1SubscriptionsResponse, response.json())

    async def get_api_v1_subscriptions_by_id(
        self,
        id: str,
    ) -> GetApiV1SubscriptionsByIdResponse:
        """Get one subscription.

        Args:
            id (str): The globalID or correlationID of the subscription.

        Returns:
            GetApiV1SubscriptionsByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subscriptions/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1SubscriptionsByIdResponse, response.json())

    async def put_api_v1_subscriptions_by_id_cancel(
        self,
        id: str,
    ) -> dict[str, Any]:
        """Cancel an Subscription.

        Args:
            id (str): The globalID or correlationID of the subscription.

        Returns:
            dict[str, Any]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subscriptions/{id}/cancel"
        response = await self._client.request(
            "PUT",
            path,
        )
        response.raise_for_status()
        return _validate(dict[str, Any], response.json())

    async def get_api_v1_subscriptions_by_id_installments(
        self,
        id: str,
    ) -> GetApiV1SubscriptionsByIdInstallmentsResponse:
        """Get a list of installments by subscription.

        Args:
            id (str): The globalID of the subscription.

        Returns:
            GetApiV1SubscriptionsByIdInstallmentsResponse: The 200 response body,
                validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subscriptions/{id}/installments"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1SubscriptionsByIdInstallmentsResponse, response.json())

    async def put_api_v1_subscriptions_by_id_value(
        self,
        id: str,
    ) -> dict[str, Any]:
        """Update the value of the next installments of the subscription. It is only
        possible if pix automatic accepts dynamic value.

        Args:
            id (str): The globalID or correlationID of the subscription.

        Returns:
            dict[str, Any]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subscriptions/{id}/value"
        response = await self._client.request(
            "PUT",
            path,
        )
        response.raise_for_status()
        return _validate(dict[str, Any], response.json())

    async def get_api_v1_transaction(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        charge: str | None = None,
        pix_qr_code: str | None = None,
        withdrawal: str | None = None,
        has_webhook: bool | None = None,
        type: GetApiV1TransactionType | None = None,
    ) -> GetApiV1TransactionResponse:
        """Get a list of transactions.

        Args:
            start (datetime | None): The start value. Omitted from the query when None.
            end (datetime | None): The end value. Omitted from the query when None.
            charge (str | None): You can use the charge ID or correlation ID or
                transaction ID of charge to get a list of transactions related of this
                transaction Omitted from the query when None.
            pix_qr_code (str | None): You can use the QrCode static ID or correlation ID
                or identifier field of QrCode static to get a list of QrCode related of
                this transaction Omitted from the query when None.
            withdrawal (str | None): You can use the ID or EndToEndId of a withdrawal
                transaction to get all transactions related to the withdrawal Omitted
                from the query when None.
            has_webhook (bool | None): Filter transactions by webhook delivery status.
                Use true to get only transactions that had a successful webhook delivery
                (HTTP 200), or false to get transactions without successful webhook
                delivery. Omitted from the query when None.
            type (GetApiV1TransactionType | None): Filter transactions by type Omitted
                from the query when None.

        Returns:
            GetApiV1TransactionResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/transaction"
        params: dict[str, Any] = {}
        if start is not None:
            params["start"] = _param(start)
        if end is not None:
            params["end"] = _param(end)
        if charge is not None:
            params["charge"] = _param(charge)
        if pix_qr_code is not None:
            params["pixQrCode"] = _param(pix_qr_code)
        if withdrawal is not None:
            params["withdrawal"] = _param(withdrawal)
        if has_webhook is not None:
            params["hasWebhook"] = _param(has_webhook)
        if type is not None:
            params["type"] = _param(type)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiV1TransactionResponse, response.json())

    async def get_api_v1_transaction_by_id(
        self,
        id: str,
    ) -> GetApiV1TransactionByIdResponse:
        """Get a Transaction.

        Args:
            id (str): you can use the transaction id from openpix or the endToEndId of
                transaction from bank

        Returns:
            GetApiV1TransactionByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/transaction/{id}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1TransactionByIdResponse, response.json())

    async def post_api_v1_transfer(
        self,
        *,
        body: TransferCreatePayload,
    ) -> PostApiV1TransferResponse:
        """Create a Transfer.

        Endpoint to to transfer values between accounts

        Args:
            body (TransferCreatePayload): The request body.

        Returns:
            PostApiV1TransferResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/transfer"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1TransferResponse, response.json())

    async def get_api_v1_webhook(
        self,
        *,
        url: str | None = None,
    ) -> GetApiV1WebhookResponse:
        """Get a list of webhooks.

        Args:
            url (str | None): You can use the url to filter all webhooks Omitted from
                the query when None.

        Returns:
            GetApiV1WebhookResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/webhook"
        params: dict[str, Any] = {}
        if url is not None:
            params["url"] = _param(url)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetApiV1WebhookResponse, response.json())

    async def post_api_v1_webhook(
        self,
        *,
        body: PostApiV1WebhookBody,
    ) -> PostApiV1WebhookResponse:
        """Create a new Webhook.

        Endpoint to create a new Webhook

        Args:
            body (PostApiV1WebhookBody): The request body.

        Returns:
            PostApiV1WebhookResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/webhook"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(PostApiV1WebhookResponse, response.json())

    async def get_api_v1_webhook_events(self) -> GetApiV1WebhookEventsResponse:
        """Get a list of webhook events.

        Returns:
            GetApiV1WebhookEventsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/webhook/events"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1WebhookEventsResponse, response.json())

    async def get_api_v1_webhook_ips(self) -> GetApiV1WebhookIpsResponse:
        """Get a list of webhook IPs.

        Returns:
            GetApiV1WebhookIpsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/api/v1/webhook/ips"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetApiV1WebhookIpsResponse, response.json())

    async def delete_api_v1_webhook_by_id(
        self,
        id: str,
    ) -> DeleteApiV1WebhookByIdResponse:
        """Delete a Webhook.

        Endpoint to delete a Webhook

        Args:
            id (str): webhook ID

        Returns:
            DeleteApiV1WebhookByIdResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/webhook/{id}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteApiV1WebhookByIdResponse, response.json())

    async def get_openpix_charge_brcode_image_id_png(
        self,
        id: str,
        *,
        size: str | None = None,
    ) -> None:
        """Get an image of Qr Code from a Charge.

        Args:
            id (str): charge link payment ID
            size (str | None): Size for the image. This size should be between 600 and
                4096. if the size parameter was not passed, the default value will be
                1024. Omitted from the query when None.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/openpix/charge/brcode/image/{id}.png"
        params: dict[str, Any] = {}
        if size is not None:
            params["size"] = _param(size)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return None


__all__: list[str] = [
    "DEFAULT_BASE_URL",
    "OpenPixClient",
]

"""Typed HTTP client generated from the Woovi OpenAPI spec.

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
from urllib.parse import quote
from uuid import UUID

from pydantic import BaseModel, TypeAdapter

from tempest_fastapi_sdk import HTTPClient

from .schemas import (
    ActivateAnticipationBeneficiaryResponse,
    AnticipationBalanceBatchPayload,
    AnticipationBalanceBatchResult,
    AnticipationBeneficiaryCreatePayload,
    AnticipationRequestStatus,
    ApplicationPayload,
    ApproveAnticipationResponse,
    ApprovePaymentResponse,
    ApproveStablecoinDepositBody,
    ApproveStablecoinDepositResponse,
    BoletoTransactionListResponse,
    BoletoTransactionStatus,
    BoletoTransactionType,
    BoletoValidateRequest,
    BoletoValidateResponse,
    CancelInvoiceResponse,
    ChargePatchPayload,
    ChargePayload,
    ChargeRefundPayload,
    ChargeStatus,
    CheckPixKeyBody,
    CloseAccountResponse,
    CreateAnticipationBeneficiaryResponse,
    CreateApplicationResponse,
    CreateCashbackFidelityBody,
    CreateCashbackFidelityResponse,
    CreateChargeResponse,
    CreateCustomerResponse,
    CreateInstallmentCobrBody,
    CreateInvoiceResponse,
    CreateKycOnboardingResponse,
    CreatePartnerApplicationBody,
    CreatePartnerApplicationResponse,
    CreatePaymentBody,
    CreatePaymentResponse,
    CreateRefundResponse,
    CreateStablecoinPayoutBody,
    CreateStablecoinPayoutResponse,
    CreateStaticQrCodeResponse,
    CreateSubaccountResponse,
    CreateSubscriptionResponse,
    CreateTransferResponse,
    CreateWebhookBody,
    CreateWebhookResponse,
    CreditSubaccountBody,
    CreditSubaccountResponse,
    CustomerPatchPayload,
    CustomerPayload,
    DeactivateAnticipationBeneficiaryResponse,
    DebitSubaccountBody,
    DebitSubaccountResponse,
    DecodeEmvBody,
    DecodeEmvResponse,
    DeleteAccountRegisterResponse,
    DeleteApplicationResponse,
    DeleteChargeResponse,
    DeleteStaticQrCodeResponse,
    DeleteSubaccountResponse,
    DeleteWebhookResponse,
    DuplicateAccountResponse,
    FilePayload,
    FundsRecovery,
    FundsRecoveryPayload,
    GetAccountLimitsResponse,
    GetAccountRegisterResponse,
    GetAccountResponse,
    GetBoletoTransactionResponse,
    GetCashbackFidelityBalanceResponse,
    GetChargeQrCodeBase64Response,
    GetChargeResponse,
    GetCompanyResponse,
    GetCustomerResponse,
    GetDisputeResponse,
    GetInstallmentResponse,
    GetPartnerCompanyResponse,
    GetPaymentResponse,
    GetReceiptReceiptType,
    GetRefundResponse,
    GetStablecoinQuoteResponse,
    GetStablecoinSubaccountBalancesResponse,
    GetStatementResponseItem,
    GetStaticQrCodeResponse,
    GetSubaccountResponse,
    GetSubaccountStatementResponseItem,
    GetSubscriptionResponse,
    GetTransactionResponse,
    KycOnboardingRequest,
    KycValidation,
    KycValidationRequest,
    ListAccountsResponse,
    ListAnticipationRequestsResponse,
    ListChargeRefundsResponse,
    ListChargesResponse,
    ListCustomersResponse,
    ListDisputesResponse,
    ListPartnerAffiliatesResponse,
    ListPartnerCompaniesResponse,
    ListPaymentsResponse,
    ListPixKeysResponse,
    ListPixKeyTokenLogsResponse,
    ListPspsResponse,
    ListRefundsResponse,
    ListStablecoinSubaccountWalletsResponse,
    ListStablecoinWalletsResponse,
    ListStaticQrCodesResponse,
    ListSubaccountsResponse,
    ListSubscriptionInstallmentsResponse,
    ListSubscriptionsResponse,
    ListTransactionsResponse,
    ListTransactionsType,
    ListWebhookEventsResponse,
    ListWebhookIpsResponse,
    ListWebhookPublicKeysResponse,
    ListWebhooksResponse,
    PaymentApprovePayload,
    PixKey,
    PixKeyCheck,
    PixKeyCreate,
    PixKeyTokens,
    PixQrCodePayload,
    PreRegistrationPayloadObject,
    QuoteStablecoinPayoutResponse,
    RefundChargeResponse,
    RefundPayload,
    RejectAnticipationBody,
    RejectAnticipationResponse,
    RetryInstallmentCobrBody,
    SetInvoiceIntegrationStatusBody,
    SetInvoiceIntegrationStatusResponse,
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
    TestInvoiceIntegrationResponse,
    TransferCreatePayload,
    UpdateChargeResponse,
    UpdateCustomerResponse,
    UpdateInvoiceIntegrationTaxFieldsBody,
    UpdateInvoiceIntegrationTaxFieldsResponse,
    UploadDisputeEvidenceBody,
    UploadDisputeEvidenceResponse,
    UploadInvoiceIntegrationCertificateBody,
    UploadInvoiceIntegrationCertificateResponse,
    UpsertInvoiceIntegrationBody,
    UpsertInvoiceIntegrationResponse,
    WithdrawFromAccountBody,
    WithdrawFromAccountResponse,
    WithdrawFromSubaccountResponse,
)

DEFAULT_BASE_URL: str = "https://api.woovi.com"
"""``servers[0].url`` from the specification."""


def _dump(payload: Any) -> Any:
    """Serialize a request body to JSON-ready data.

    ``exclude_unset`` rides along with ``exclude_none`` so a field
    the caller never touched stays off the wire. An optional array
    is generated with ``default_factory=list``, and to an API
    "informed as empty" is a different claim from "not informed":
    Woovi answers ``{"splits": []}`` with 400 *O array de split
    precisa ter ao menos um item*, and accepts the same body
    without the key.

    Args:
        payload (Any): A generated schema instance, or already-plain
            data when the specification typed the body loosely.

    Returns:
        Any: ``model_dump(by_alias=True, mode="json")`` for a Pydantic
        model — the wire spelling the third party expects — and the
        value untouched for anything else.
    """
    if isinstance(payload, BaseModel):
        return payload.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
            exclude_unset=True,
        )
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


def _path_param(value: Any) -> str:
    """Escape a value into exactly one path segment.

    Args:
        value (Any): The argument as the caller passed it,
            normalized through ``_param`` first so an ``Enum``
            reaches the path as its value rather than as
            ``"Class.MEMBER"``.

    Returns:
        str: The value percent-encoded with an empty ``safe``
        set, so every reserved character is escaped — ``/``
        included, because an identifier is one segment and must
        not become two.

    Without this, a reserved character does not fail: it
    *retargets*. ``order#42`` interpolated raw yields
    ``/charge/order#42``, whose fragment the HTTP client never
    sends — so the request addresses ``/charge/order``, and on a
    ``DELETE`` route that is a destructive call against a
    different resource.
    """
    return quote(str(_param(value)), safe="")


class OpenPixClient:
    """Client for Woovi (version 1.0.0)."""

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

    async def get_charge_qr_code_base64(
        self,
        id: str,
        *,
        size: str | None = None,
    ) -> GetChargeQrCodeBase64Response:
        """Get a base64 encoded QR Code image from a Charge.

        Args:
            id (str): charge ID, payment link ID, or QR code ID
            size (str | None): Size for the image. This size should be between 600 and
                4096. If the size parameter is not passed, the default value will be
                1024. Omitted from the query when None.

        Returns:
            GetChargeQrCodeBase64Response: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 500.
        """
        path = f"/api/image/qrcode/base64/{_path_param(id)}"
        params: dict[str, Any] = {}
        if size is not None:
            params["size"] = _param(size)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetChargeQrCodeBase64Response, response.json())

    async def list_accounts(
        self,
        *,
        email: str | None = None,
        skip: int | None = None,
        limit: int | None = None,
    ) -> ListAccountsResponse:
        """Get a list of Accounts.

        Args:
            email (str | None): You can use the email to filter accounts Omitted from
                the query when None.
            skip (int | None): Number of items to skip for pagination Omitted from the
                query when None.
            limit (int | None): Maximum number of items to return Omitted from the query
                when None.

        Returns:
            ListAccountsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = "/api/v1/account"
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
        return _validate(ListAccountsResponse, response.json())

    async def duplicate_account(self) -> DuplicateAccountResponse:
        """Duplicates the Account.

        Duplicates the account associated with the authorization appId. Requires the
        bank account feature to be enabled.

        Returns:
            DuplicateAccountResponse: The 200 response body, validated.

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
        return _validate(DuplicateAccountResponse, response.json())

    async def get_account_register(
        self,
        id: str,
    ) -> GetAccountRegisterResponse:
        """Get account register by CorrelationID.

        Retrieves an existing account registration by CorrelationID

        Args:
            id (str): CorrelationID of the account register

        Returns:
            GetAccountRegisterResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404, 500.
        """
        path = f"/api/v1/account-register/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetAccountRegisterResponse, response.json())

    async def delete_account_register(
        self,
        id: str,
    ) -> DeleteAccountRegisterResponse:
        """Delete an account registration.

        Deletes an account registration that is in PENDING status

        Args:
            id (str): CorrelationID of the account register to delete

        Returns:
            DeleteAccountRegisterResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 404, 500.
        """
        path = f"/api/v1/account-register/{_path_param(id)}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteAccountRegisterResponse, response.json())

    async def get_account(
        self,
        account_id: str,
    ) -> GetAccountResponse:
        """Get an Account.

        Args:
            account_id (str): ID of the Account

        Returns:
            GetAccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/account/{_path_param(account_id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetAccountResponse, response.json())

    async def close_account(
        self,
        account_id: str,
    ) -> CloseAccountResponse:
        """Close an Account.

        Closes an Account.

        Notes: - Accounts with balance cannot be closed.

        Args:
            account_id (str): ID of the Account

        Returns:
            CloseAccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403, 404, 500.
        """
        path = f"/api/v1/account/{_path_param(account_id)}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(CloseAccountResponse, response.json())

    async def withdraw_from_account(
        self,
        account_id: str,
        *,
        body: WithdrawFromAccountBody,
    ) -> WithdrawFromAccountResponse:
        """Withdraw from an Account.

        An additional fee may be charged depending on the minimum free withdrawal
        amount. See more about at
        https://developers.openpix.com.br/docs/FAQ/faq-virtual-account/#onde-posso-consultar-as-taxas-da-minha-conta-virtual

        Args:
            account_id (str): ID of the Account
            body (WithdrawFromAccountBody): The request body.

        Returns:
            WithdrawFromAccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/account/{_path_param(account_id)}/withdraw"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(WithdrawFromAccountResponse, response.json())

    async def list_anticipation_requests(
        self,
        *,
        status: AnticipationRequestStatus | None = None,
        limit: int | None = None,
    ) -> ListAnticipationRequestsResponse:
        """List anticipation requests.

        Lists the company's anticipation requests. Poll `?status=PENDING` to discover
        requests awaiting your approval before settlement. Requires the
        `anticipation.request.read` scope.

        Args:
            status (AnticipationRequestStatus | None): Filter by status. Omitted from
                the query when None.
            limit (int | None): Max items to return (default 100, capped at 1000).
                Omitted from the query when None.

        Returns:
            ListAnticipationRequestsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/api/v1/anticipation"
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = _param(status)
        if limit is not None:
            params["limit"] = _param(limit)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(ListAnticipationRequestsResponse, response.json())

    async def sync_anticipation_balances(
        self,
        *,
        body: AnticipationBalanceBatchPayload,
    ) -> AnticipationBalanceBatchResult:
        """Bulk-sync beneficiary balances.

        Absolute set of availableAmount/maxAdvanceableAmount for up to 1000
        beneficiaries in one call (nightly payroll sync). Returns a per-item report so
        partial failures can be reconciled.

        Args:
            body (AnticipationBalanceBatchPayload): The request body.

        Returns:
            AnticipationBalanceBatchResult: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403.
        """
        path = "/api/v1/anticipation/balance/batch"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(AnticipationBalanceBatchResult, response.json())

    async def create_anticipation_beneficiary(
        self,
        *,
        body: AnticipationBeneficiaryCreatePayload,
        return_existing: bool | None = None,
    ) -> CreateAnticipationBeneficiaryResponse:
        """Register a beneficiary.

        Registers a beneficiary bound to the company resolved from the app_id.
        Idempotent on the payout key: send `?return_existing=true` to get the existing
        beneficiary (200) instead of a 409.

        Args:
            body (AnticipationBeneficiaryCreatePayload): The request body.
            return_existing (bool | None): When `true`, an already-existing beneficiary
                is returned with 200 instead of a 409. Omitted from the query when None.

        Returns:
            CreateAnticipationBeneficiaryResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 409.
        """
        path = "/api/v1/anticipation/beneficiary"
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
        return _validate(CreateAnticipationBeneficiaryResponse, response.json())

    async def activate_anticipation_beneficiary(
        self,
        tax_id: str,
    ) -> ActivateAnticipationBeneficiaryResponse:
        """Activate a beneficiary.

        Reactivates a beneficiary previously deactivated.

        Args:
            tax_id (str): Payout key (CPF or CNPJ), with or without mask.

        Returns:
            ActivateAnticipationBeneficiaryResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/api/v1/anticipation/beneficiary/{_path_param(tax_id)}/activate"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(ActivateAnticipationBeneficiaryResponse, response.json())

    async def deactivate_anticipation_beneficiary(
        self,
        tax_id: str,
    ) -> DeactivateAnticipationBeneficiaryResponse:
        """Deactivate a beneficiary.

        Deactivates a beneficiary; blocks new anticipations and app login.

        Args:
            tax_id (str): Payout key (CPF or CNPJ), with or without mask.

        Returns:
            DeactivateAnticipationBeneficiaryResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/api/v1/anticipation/beneficiary/{_path_param(tax_id)}/deactivate"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(DeactivateAnticipationBeneficiaryResponse, response.json())

    async def approve_anticipation(
        self,
        id: str,
    ) -> ApproveAnticipationResponse:
        """Approve an anticipation request.

        Approves a PENDING request and triggers the Pix Out to the beneficiary.
        Idempotent: re-approving an already-approved request returns 200 with the
        current state; a rejected/terminal one returns 409. Requires the
        `anticipation.request.approve` scope.

        Args:
            id (str): Anticipation request id.

        Returns:
            ApproveAnticipationResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 409.
        """
        path = f"/api/v1/anticipation/{_path_param(id)}/approve"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(ApproveAnticipationResponse, response.json())

    async def reject_anticipation(
        self,
        id: str,
        *,
        body: RejectAnticipationBody | None = None,
    ) -> RejectAnticipationResponse:
        """Reject an anticipation request.

        Rejects a PENDING request (releases the reserved cycle limit). Idempotent:
        re-rejecting a canceled request returns 200; an approved/terminal one returns
        409. Requires the `anticipation.request.approve` scope.

        Args:
            id (str): Anticipation request id.
            body (RejectAnticipationBody): The request body. Optional.

        Returns:
            RejectAnticipationResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 409.
        """
        path = f"/api/v1/anticipation/{_path_param(id)}/reject"
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(RejectAnticipationResponse, response.json())

    async def create_application(
        self,
        *,
        body: ApplicationPayload,
    ) -> CreateApplicationResponse:
        """Create a new application.

        Creates a new application for a company. If the company has the
        APPLICATION_SCOPES_REQUIRED feature enabled, the scopes field is required.

        Args:
            body (ApplicationPayload): The request body.

        Returns:
            CreateApplicationResponse: The 201 response body, validated.

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
        return _validate(CreateApplicationResponse, response.json())

    async def delete_application(self) -> DeleteApplicationResponse:
        """Delete an application.

        Deactivates an application by setting isActive to false and adding a removedAt
        timestamp

        Returns:
            DeleteApplicationResponse: The 200 response body, validated.

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
        return _validate(DeleteApplicationResponse, response.json())

    async def list_boleto_transactions(
        self,
        *,
        type: BoletoTransactionType | None = None,
        status: BoletoTransactionStatus | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        settled_start: datetime | None = None,
        settled_end: datetime | None = None,
        skip: int | None = None,
        limit: int | None = None,
    ) -> BoletoTransactionListResponse:
        """List boleto transactions.

        Lists the boleto transactions of your company, most recent first, so you can
        reconcile boleto receivables and payments against your own records.

        A transaction is either a `BOLETO_IN` — a boleto your payer paid — or a
        `BOLETO_OUT` — a boleto your company paid. All monetary values are in cents.

        `value` is the amount that actually moved, which for a boleto paid after the due
        date is above the emitted amount in `charge.value` because of interest and fine.
        `finesValue` and `interestsValue` split that difference, as charged by the bank,
        so you can reconcile an overdue boleto without recomputing it from the charge
        settings. Each is absent when there was none, so a boleto paid on time carries
        neither.

        Two independent date ranges are offered because they answer different questions:
        `start`/`end` filter by when the transaction was created, and
        `settledStart`/`settledEnd` by when Woovi credited the amount to your account.
        Both dates come back on every item.

        Requires the `BOLETO_TRANSACTION_GET_LIST` scope on the application.

        Args:
            type (BoletoTransactionType | None): Only transactions of this type. Omitted
                from the query when None.
            status (BoletoTransactionStatus | None): Only transactions in this status.
                Omitted from the query when None.
            start (datetime | None): Only transactions created from this date on.
                Omitted from the query when None.
            end (datetime | None): Only transactions created up to this date. Omitted
                from the query when None.
            settled_start (datetime | None): Only transactions settled from this date
                on. Omitted from the query when None.
            settled_end (datetime | None): Only transactions settled up to this date.
                Omitted from the query when None.
            skip (int | None): Rows to skip. Capped at 10000 — past that, narrow the
                window with the date filters instead of paginating deeper. Omitted from
                the query when None.
            limit (int | None): Rows to return. Omitted from the query when None.

        Returns:
            BoletoTransactionListResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 422.
        """
        path = "/api/v1/boleto-transaction"
        params: dict[str, Any] = {}
        if type is not None:
            params["type"] = _param(type)
        if status is not None:
            params["status"] = _param(status)
        if start is not None:
            params["start"] = _param(start)
        if end is not None:
            params["end"] = _param(end)
        if settled_start is not None:
            params["settledStart"] = _param(settled_start)
        if settled_end is not None:
            params["settledEnd"] = _param(settled_end)
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
        return _validate(BoletoTransactionListResponse, response.json())

    async def get_boleto_transaction(
        self,
        boleto_transaction_id: str,
    ) -> GetBoletoTransactionResponse:
        """Get a boleto transaction.

        Returns one boleto transaction of your company.

        The id is the `boletoTransactionID` delivered in the `BOLETO_SETTLED` webhook,
        so you can confirm a settlement you were notified about.

        Requires the `BOLETO_TRANSACTION_GET` scope on the application.

        Args:
            boleto_transaction_id (str): The boleto transaction id, as delivered in the
                webhook.

        Returns:
            GetBoletoTransactionResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                404.
        """
        path = f"/api/v1/boleto-transaction/{_path_param(boleto_transaction_id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetBoletoTransactionResponse, response.json())

    async def validate_boleto(
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

    async def create_cashback_fidelity(
        self,
        *,
        body: CreateCashbackFidelityBody,
    ) -> CreateCashbackFidelityResponse:
        """Get or create cashback for a customer.

        Create a new cashback exclusive for the customer with a given taxID. If the
        customer already has a pending excluisve cashback, this endpoint will return it
        instead.

        Args:
            body (CreateCashbackFidelityBody): The request body.

        Returns:
            CreateCashbackFidelityResponse: The 200 response body, validated.

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
        return _validate(CreateCashbackFidelityResponse, response.json())

    async def get_cashback_fidelity_balance(
        self,
        tax_id: str,
    ) -> GetCashbackFidelityBalanceResponse:
        """Get the exclusive cashback amount an user still has to receive by taxID.

        Args:
            tax_id (str): The raw tax ID from the customer you want to get the balance.

        Returns:
            GetCashbackFidelityBalanceResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/cashback-fidelity/balance/{_path_param(tax_id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetCashbackFidelityBalanceResponse, response.json())

    async def list_charges(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        status: ChargeStatus | None = None,
        customer: str | None = None,
        subscription: str | None = None,
    ) -> ListChargesResponse:
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
            ListChargesResponse: The 200 response body, validated.

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
        return _validate(ListChargesResponse, response.json())

    async def create_charge(
        self,
        *,
        body: ChargePayload,
        return_existing: bool | None = None,
    ) -> CreateChargeResponse:
        """Create a new Charge.

        Endpoint to create a new Charge for a customer.

        ## Split

        You can split the value of a charge across other accounts by sending the
        `splits` array in the request body. Each item accepts:

        - `value`: amount in cents that will be split to the destination. - `pixKey`:
        Pix key of the account that will receive this split. - `splitType`: how the
        split is processed — one of `SPLIT_INTERNAL_TRANSFER`, `SPLIT_SUB_ACCOUNT` or
        `SPLIT_PARTNER`. [See how each split type is
        processed](https://developers.openpix.com.br/docs/splits/split-introduction).

        See the *Charge with Split Internal Transfer* and *Charge with Split
        Subaccounts* request body examples below for the full payload shape.

        Args:
            body (ChargePayload): The request body.
            return_existing (bool | None): Make the endpoint idempotent, will return an
                existent charge if already has a one with the correlationID Omitted from
                the query when None.

        Returns:
            CreateChargeResponse: The 200 response body, validated.

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
        return _validate(CreateChargeResponse, response.json())

    async def get_charge(
        self,
        id: str,
    ) -> GetChargeResponse:
        r"""Get one charge.

        Args:
            id (str): charge ID or correlation ID. You will need URI encoding if your
                correlation ID has characters outside the ASCII set or reserved
                characters (%, \#, /).

        Returns:
            GetChargeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetChargeResponse, response.json())

    async def update_charge(
        self,
        id: str,
        *,
        body: ChargePatchPayload,
    ) -> UpdateChargeResponse:
        r"""Edit expiration date of a charge.

        Args:
            id (str): correlation ID. You will need URI encoding if your correlation ID
                has characters outside the ASCII set or reserved characters (%, \#, /).
            body (ChargePatchPayload): The request body.

        Returns:
            UpdateChargeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{_path_param(id)}"
        payload = _dump(body)
        response = await self._client.request(
            "PATCH",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(UpdateChargeResponse, response.json())

    async def delete_charge(
        self,
        id: str,
    ) -> DeleteChargeResponse:
        r"""Delete a charge.

        Args:
            id (str): charge ID or correlation ID. You will need URI encoding if your
                correlation ID has characters outside the ASCII set or reserved
                characters (%, \#, /).

        Returns:
            DeleteChargeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{_path_param(id)}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteChargeResponse, response.json())

    async def list_charge_refunds(
        self,
        id: str,
    ) -> ListChargeRefundsResponse:
        r"""Get all refunds of a charge.

        Endpoint to get all refunds of a charge

        Args:
            id (str): The correlation ID of the charge. You will need URI encoding if
                your correlation ID has characters outside the ASCII set or reserved
                characters (%, \#, /).

        Returns:
            ListChargeRefundsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{_path_param(id)}/refund"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ListChargeRefundsResponse, response.json())

    async def refund_charge(
        self,
        id: str,
        *,
        body: ChargeRefundPayload,
    ) -> RefundChargeResponse:
        r"""Create a new refund for a charge.

        Endpoint to create a new refund for a charge

        Args:
            id (str): The correlation ID of the charge. You will need URI encoding if
                your correlation ID has characters outside the ASCII set or reserved
                characters (%, \#, /).
            body (ChargeRefundPayload): The request body.

        Returns:
            RefundChargeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/charge/{_path_param(id)}/refund"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(RefundChargeResponse, response.json())

    async def get_company(self) -> GetCompanyResponse:
        """Get a Company.

        Returns:
            GetCompanyResponse: The 200 response body, validated.

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
        return _validate(GetCompanyResponse, response.json())

    async def list_customers(self) -> ListCustomersResponse:
        """Get a list of customers.

        Returns:
            ListCustomersResponse: The 200 response body, validated.

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
        return _validate(ListCustomersResponse, response.json())

    async def create_customer(
        self,
        *,
        body: CustomerPayload,
    ) -> CreateCustomerResponse:
        """Create a new Customer.

        Endpoint to create a new Customer

        Args:
            body (CustomerPayload): The request body.

        Returns:
            CreateCustomerResponse: The 200 response body, validated.

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
        return _validate(CreateCustomerResponse, response.json())

    async def get_customer(
        self,
        id: str,
    ) -> GetCustomerResponse:
        """Get one customer.

        Args:
            id (str): Correlation ID or Tax ID

        Returns:
            GetCustomerResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/customer/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetCustomerResponse, response.json())

    async def update_customer(
        self,
        id: str,
        *,
        body: CustomerPatchPayload,
    ) -> UpdateCustomerResponse:
        """Update a Customer.

        Endpoint to update a Customer

        Args:
            id (str): correlation ID
            body (CustomerPatchPayload): The request body.

        Returns:
            UpdateCustomerResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/customer/{_path_param(id)}"
        payload = _dump(body)
        response = await self._client.request(
            "PATCH",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(UpdateCustomerResponse, response.json())

    async def decode_emv(
        self,
        *,
        body: DecodeEmvBody,
    ) -> DecodeEmvResponse:
        """Parse EMV (PIX) QR code and optionally resolve COB/REC locations.

        Args:
            body (DecodeEmvBody): The request body.

        Returns:
            DecodeEmvResponse: The 200 response body, validated.

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
        return _validate(DecodeEmvResponse, response.json())

    async def list_disputes(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> ListDisputesResponse:
        """Get a list of disputes.

        Args:
            start (datetime | None): The start value. Omitted from the query when None.
            end (datetime | None): The end value. Omitted from the query when None.

        Returns:
            ListDisputesResponse: The 200 response body, validated.

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
        return _validate(ListDisputesResponse, response.json())

    async def get_dispute(
        self,
        id: str,
    ) -> GetDisputeResponse:
        """Get one dispute.

        Args:
            id (str): The id must be the endToEndId of the transaction that originated
                the Dispute

        Returns:
            GetDisputeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 500.
        """
        path = f"/api/v1/dispute/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetDisputeResponse, response.json())

    async def upload_dispute_evidence(
        self,
        id: str,
        *,
        body: UploadDisputeEvidenceBody,
    ) -> UploadDisputeEvidenceResponse:
        """Upload new evidence.

        Upload evidence files for a dispute (MED).

        [How to get the dispute
        id](https://developers.woovi.com/docs/disputa/how-add-new-evidence-in-dispute#1-obter-o-id-da-disputa)

        Args:
            id (str): id of the dispute the evidence belongs to
            body (UploadDisputeEvidenceBody): The request body.

        Returns:
            UploadDisputeEvidenceResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/dispute/{_path_param(id)}/evidence"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(UploadDisputeEvidenceResponse, response.json())

    # openapi: unsupported — request body of UploadFile uses multipart/form-data — only
    #   application/json and application/x-www-form-urlencoded are modelled
    async def upload_file(self) -> FilePayload:
        """Upload a file.

        Uploads a file and returns its metadata with a pre-signed download URL. The file
        is sent as `multipart/form-data` on the `file` field, together with the
        `purpose` that describes what the file is for.

        Send exactly one file per request, of one of the supported content types, up to
        10 MiB (`10485760` bytes).

        When you send a `correlationID`, repeating the request with the same
        `correlationID` and `purpose` returns the file already stored (`200`) instead of
        uploading a second copy, so the request is safe to retry. Without a
        `correlationID` one is generated for you and every call stores a new file.

        The `url` in the response is temporary and expires at `urlExpiresAt`; ask for
        the file again to get a fresh one.

        Returns:
            FilePayload: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 413, 415, 502.
        """
        path = "/api/v1/files"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(FilePayload, response.json())

    async def create_funds_recovery(
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

    async def get_funds_recovery(
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
        path = f"/api/v1/funds-recovery/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(FundsRecovery, response.json())

    async def cancel_funds_recovery(
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
        path = f"/api/v1/funds-recovery/{_path_param(id)}/cancel"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(FundsRecovery, response.json())

    async def get_installment(
        self,
        id: str,
    ) -> GetInstallmentResponse:
        """Get one installment.

        Args:
            id (str): The globalID of the installment or the endToEndId from
                transaction.

        Returns:
            GetInstallmentResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/installments/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetInstallmentResponse, response.json())

    async def create_installment_cobr(
        self,
        id: str,
        *,
        body: CreateInstallmentCobrBody | None = None,
    ) -> dict[str, Any]:
        """Create a new Cobr Manually.

        Create a new Cobr Manually.

        Args:
            id (str): The globalID of the installment.
            body (CreateInstallmentCobrBody): The request body. Optional.

        Returns:
            dict[str, Any]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/installments/{_path_param(id)}/cobr"
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(dict[str, Any], response.json())

    async def retry_installment_cobr(
        self,
        id: str,
        *,
        body: RetryInstallmentCobrBody | None = None,
    ) -> dict[str, Any]:
        """Create a new Retry Manually.

        Create a new Retry Manually.

        Args:
            id (str): The globalID of the installment.
            body (RetryInstallmentCobrBody): The request body. Optional.

        Returns:
            dict[str, Any]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/installments/{_path_param(id)}/cobr/retry"
        payload = None if body is None else _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(dict[str, Any], response.json())

    async def list_invoices(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        skip: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """Get invoices.

        Args:
            start (str | None): The start value. Omitted from the query when None.
            end (str | None): The end value. Omitted from the query when None.
            skip (int | None): The skip value. Omitted from the query when None.
            limit (int | None): The limit value. Omitted from the query when None.

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

    async def create_invoice(
        self,
        *,
        body: Any,
    ) -> CreateInvoiceResponse:
        """Create a new invoice.

        Args:
            body (Any): The request body.

        Returns:
            CreateInvoiceResponse: The 201 response body, validated.

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
        return _validate(CreateInvoiceResponse, response.json())

    async def get_invoice_integration(self) -> Any:
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

    async def upsert_invoice_integration(
        self,
        *,
        body: UpsertInvoiceIntegrationBody | None = None,
    ) -> UpsertInvoiceIntegrationResponse:
        """Create or upsert the NFe.io integration for the authenticated company.

        Upserts the NFe.io integration for the authenticated company and sets its tax
        fields. Optionally activates it (only allowed once configured).

        Args:
            body (UpsertInvoiceIntegrationBody): The request body. Optional.

        Returns:
            UpsertInvoiceIntegrationResponse: The 201 response body, validated.

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
        return _validate(UpsertInvoiceIntegrationResponse, response.json())

    async def update_invoice_integration_tax_fields(
        self,
        *,
        body: UpdateInvoiceIntegrationTaxFieldsBody,
    ) -> UpdateInvoiceIntegrationTaxFieldsResponse:
        """Update the tax fields of the invoice integration.

        Updates the tax configuration of the authenticated company's existing NFEIO
        integration (city service code, municipal subscription, rps number, special tax,
        tax regime, legal nature and tax determination fields). The integration must
        already exist; otherwise a 404 is returned. The response never echoes
        credentials.

        Args:
            body (UpdateInvoiceIntegrationTaxFieldsBody): The request body.

        Returns:
            UpdateInvoiceIntegrationTaxFieldsResponse: The 200 response body, validated.

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
        return _validate(UpdateInvoiceIntegrationTaxFieldsResponse, response.json())

    async def set_invoice_integration_status(
        self,
        *,
        body: SetInvoiceIntegrationStatusBody,
    ) -> SetInvoiceIntegrationStatusResponse:
        """Activate or deactivate the NFe.io integration for the authenticated company.

        Args:
            body (SetInvoiceIntegrationStatusBody): The request body.

        Returns:
            SetInvoiceIntegrationStatusResponse: The 200 response body, validated.

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
        return _validate(SetInvoiceIntegrationStatusResponse, response.json())

    async def upload_invoice_integration_certificate(
        self,
        *,
        body: UploadInvoiceIntegrationCertificateBody,
    ) -> UploadInvoiceIntegrationCertificateResponse:
        """Upload the NFe.io A1 certificate for the invoice integration.

        Uploads the company's NFe.io A1 certificate (base64-encoded pkcs12) to the
        configured NFEIO integration. The response returns only the resulting
        integration status and never echoes the certificate, passphrase or credentials.

        Args:
            body (UploadInvoiceIntegrationCertificateBody): The request body.

        Returns:
            UploadInvoiceIntegrationCertificateResponse: The 200 response body,
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
        return _validate(UploadInvoiceIntegrationCertificateResponse, response.json())

    async def test_invoice_integration(self) -> TestInvoiceIntegrationResponse:
        """Issue a NFe.io test invoice for the invoice integration.

        Issues a test NFe.io invoice for the authenticated company's NFEIO integration.
        This is the bootstrap step that moves the integration to VALIDATING; once NFe.io
        confirms the test note via webhook the integration becomes CONFIGURED and
        active, which unblocks real invoice issuance. A configured integration can no
        longer issue test invoices.

        Returns:
            TestInvoiceIntegrationResponse: The 200 response body, validated.

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
        return _validate(TestInvoiceIntegrationResponse, response.json())

    async def cancel_invoice(
        self,
        correlation_id: str,
    ) -> CancelInvoiceResponse:
        """Cancel an invoice.

        Args:
            correlation_id (str): The correlationID value.

        Returns:
            CancelInvoiceResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/api/v1/invoice/{_path_param(correlation_id)}/cancel"
        response = await self._client.request(
            "POST",
            path,
        )
        response.raise_for_status()
        return _validate(CancelInvoiceResponse, response.json())

    # openapi: unsupported — response of GetInvoicePdf uses application/pdf — only
    #   application/json is modelled
    async def get_invoice_pdf(
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
        path = f"/api/v1/invoice/{_path_param(correlation_id)}/pdf"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    # openapi: unsupported — response of GetInvoiceXml uses application/xml — only
    #   application/json is modelled
    async def get_invoice_xml(
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
        path = f"/api/v1/invoice/{_path_param(correlation_id)}/xml"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def create_kyc_validation(
        self,
        *,
        body: KycValidationRequest,
    ) -> KycValidation:
        """Create a KYC validation for a Tax ID.

        Screens a CPF or CNPJ against fraud, dispute, sanctions, PEP and lawsuit signals
        and returns a verdict.

        Requires the `KYC_VALIDATION` feature on the company and the
        `KYC_VALIDATION_POST` scope on the application.

        Args:
            body (KycValidationRequest): The request body.

        Returns:
            KycValidation: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 502.
        """
        path = "/api/v1/kyc-validation/taxid"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(KycValidation, response.json())

    async def get_kyc_validation(
        self,
        correlation_id: str,
    ) -> KycValidation:
        """Get a KYC validation by correlationID.

        Reads back a validation created with `POST /api/v1/kyc-validation/taxid`, scoped
        to your own company. Free — reading a validation is never billed.

        Poll this until `status` leaves `PROCESSING`, or subscribe to the
        `KYC_VALIDATION_COMPLETED` / `KYC_VALIDATION_FAILED` webhook events and skip the
        polling entirely.

        Requires the `KYC_VALIDATION` feature on the company and the
        `KYC_VALIDATION_GET` scope on the application.

        Args:
            correlation_id (str): The `correlationID` you sent when creating the
                validation.

        Returns:
            KycValidation: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 403, 404.
        """
        path = f"/api/v1/kyc-validation/{_path_param(correlation_id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(KycValidation, response.json())

    async def create_kyc_onboarding(
        self,
        *,
        body: KycOnboardingRequest,
    ) -> CreateKycOnboardingResponse:
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
            CreateKycOnboardingResponse: The 200 response body, validated.

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
        return _validate(CreateKycOnboardingResponse, response.json())

    async def get_account_limits(
        self,
        account_id: str,
    ) -> GetAccountLimitsResponse:
        """Get account limits.

        Retrieves the most recent account limits configured for a given bank account.
        Only the public-safe fields are returned; internal-only fields are stripped from
        the response.

        Args:
            account_id (str): Bank account identifier (ObjectId) for which limits should
                be returned

        Returns:
            GetAccountLimitsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404.
        """
        path = f"/api/v1/limits/{_path_param(account_id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetAccountLimitsResponse, response.json())

    async def list_partner_affiliates(self) -> ListPartnerAffiliatesResponse:
        """Get every affiliate company that is managed by you.

        Returns:
            ListPartnerAffiliatesResponse: The 200 response body, validated.

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
        return _validate(ListPartnerAffiliatesResponse, response.json())

    async def create_partner_application(
        self,
        *,
        body: CreatePartnerApplicationBody,
    ) -> CreatePartnerApplicationResponse:
        """Create a new application to some of your preregistration's company.

        As a partner company, you can create a new application to some of your
        companies. The application should give access to our API to this companies, so
        they can use it too.

        Args:
            body (CreatePartnerApplicationBody): The request body.

        Returns:
            CreatePartnerApplicationResponse: The 200 response body, validated.

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
        return _validate(CreatePartnerApplicationResponse, response.json())

    async def list_partner_companies(self) -> ListPartnerCompaniesResponse:
        """Get every preregistration that is managed by you.

        Returns:
            ListPartnerCompaniesResponse: The 200 response body, validated.

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
        return _validate(ListPartnerCompaniesResponse, response.json())

    async def create_partner_company(
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

    async def get_partner_company(
        self,
        tax_id: str,
    ) -> GetPartnerCompanyResponse:
        """Get an specific preregistration via taxID param.

        Args:
            tax_id (str): The raw tax ID from the preregistration that you want to get.

        Returns:
            GetPartnerCompanyResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/partner/company/{_path_param(tax_id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetPartnerCompanyResponse, response.json())

    async def list_payments(self) -> ListPaymentsResponse:
        """Get a list of payments.

        Returns:
            ListPaymentsResponse: The 200 response body, validated.

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
        return _validate(ListPaymentsResponse, response.json())

    async def create_payment(
        self,
        *,
        body: CreatePaymentBody,
    ) -> CreatePaymentResponse:
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
            body (CreatePaymentBody): The request body.

        Returns:
            CreatePaymentResponse: The 200 response body, validated.

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
        return _validate(CreatePaymentResponse, response.json())

    async def approve_payment(
        self,
        *,
        body: PaymentApprovePayload,
    ) -> ApprovePaymentResponse:
        """Approve a Payment Request.

        Endpoint to approve a payment

        Args:
            body (PaymentApprovePayload): The request body.

        Returns:
            ApprovePaymentResponse: The 200 response body, validated.

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
        return _validate(ApprovePaymentResponse, response.json())

    async def get_payment(
        self,
        id: str,
    ) -> GetPaymentResponse:
        """Get one Payment.

        Args:
            id (str): payment ID or correlation ID

        Returns:
            GetPaymentResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/payment/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetPaymentResponse, response.json())

    async def list_pix_keys(
        self,
        *,
        skip: int | None = None,
        limit: int | None = None,
    ) -> ListPixKeysResponse:
        """Get all Pix keys.

        Retrieves a list of all Pix keys

        Args:
            skip (int | None): The skip value. Omitted from the query when None.
            limit (int | None): The limit value. Omitted from the query when None.

        Returns:
            ListPixKeysResponse: The 200 response body, validated.

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
        return _validate(ListPixKeysResponse, response.json())

    async def create_pix_key(
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

    async def check_pix_key(
        self,
        *,
        body: CheckPixKeyBody,
    ) -> PixKeyCheck:
        """Check data from a Pix key.

        Get data from a Pix key if it exists.

        **This endpoint is not enabled by default.** It queries the DICT for the holder
        of a Pix key that is not yours, so it has to be requested from support and goes
        through an internal review before being turned on. Calls are billed per query.

        If what you need is to confirm that an account belongs to who you expect, use
        bank data validation instead — it is available to every account with no
        approval:

        - [Validating bank data with a Pix
        key](https://developers.woovi.com/docs/flows/validate-bank-data) - [Validating
        bank data with branch and account
        number](https://developers.woovi.com/docs/flows/validate-bank-data-manual)

        Args:
            body (CheckPixKeyBody): The request body.

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

    async def list_pix_key_tokens(self) -> PixKeyTokens:
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

    async def list_pix_key_token_logs(
        self,
        *,
        skip: int | None = None,
        limit: int | None = None,
        company_bank_account: str | None = None,
    ) -> ListPixKeyTokenLogsResponse:
        """Get token bucket logs.

        Get a list of token bucket operation logs

        Args:
            skip (int | None): The skip value. Omitted from the query when None.
            limit (int | None): The limit value. Omitted from the query when None.
            company_bank_account (str | None): Filter logs by company bank account ID
                Omitted from the query when None.

        Returns:
            ListPixKeyTokenLogsResponse: The 200 response body, validated.

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
        return _validate(ListPixKeyTokenLogsResponse, response.json())

    async def delete_pix_key(
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
        path = f"/api/v1/pix-keys/{_path_param(pix_key)}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return None

    async def check_pix_key_by_key(
        self,
        pix_key: str,
    ) -> PixKeyCheck:
        """Check data from a Pix key.

        Get data from a Pix key if it exists.

        **This endpoint is not enabled by default.** It queries the DICT for the holder
        of a Pix key that is not yours, so it has to be requested from support and goes
        through an internal review before being turned on. Calls are billed per query.

        If what you need is to confirm that an account belongs to who you expect, use
        bank data validation instead — it is available to every account with no
        approval:

        - [Validating bank data with a Pix
        key](https://developers.woovi.com/docs/flows/validate-bank-data) - [Validating
        bank data with branch and account
        number](https://developers.woovi.com/docs/flows/validate-bank-data-manual)

        Args:
            pix_key (str): The Pix key to check

        Returns:
            PixKeyCheck: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 404, 429.
        """
        path = f"/api/v1/pix-keys/{_path_param(pix_key)}/check"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(PixKeyCheck, response.json())

    async def set_default_pix_key(
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
        path = f"/api/v1/pix-keys/{_path_param(pix_key)}/default"
        response = await self._client.request(
            "PUT",
            path,
        )
        response.raise_for_status()
        return _validate(PixKey, response.json())

    async def list_psps(
        self,
        *,
        ispb: str | None = None,
        name: str | None = None,
        compe: str | None = None,
    ) -> ListPspsResponse:
        """Get a list of PSPs (Payment Service Providers).

        Args:
            ispb (str | None): Filter PSPs by ISPB code Omitted from the query when
                None.
            name (str | None): Filter PSPs by name Omitted from the query when None.
            compe (str | None): Filter PSPs by COMPE code Omitted from the query when
                None.

        Returns:
            ListPspsResponse: The 200 response body, validated.

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
        return _validate(ListPspsResponse, response.json())

    async def list_static_qr_codes(self) -> ListStaticQrCodesResponse:
        """Get a list of Pix QrCodes.

        Returns:
            ListStaticQrCodesResponse: The 200 response body, validated.

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
        return _validate(ListStaticQrCodesResponse, response.json())

    async def create_static_qr_code(
        self,
        *,
        body: PixQrCodePayload,
    ) -> CreateStaticQrCodeResponse:
        """Create a new Pix QrCode Static.

        Endpoint to create a new Pix QrCode Static

        Args:
            body (PixQrCodePayload): The request body.

        Returns:
            CreateStaticQrCodeResponse: The 200 response body, validated.

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
        return _validate(CreateStaticQrCodeResponse, response.json())

    async def get_static_qr_code(
        self,
        id: str,
    ) -> GetStaticQrCodeResponse:
        """Get one Pix QrCode.

        Args:
            id (str): pixQrCode ID, correlation ID or emv identifier

        Returns:
            GetStaticQrCodeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/qrcode-static/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetStaticQrCodeResponse, response.json())

    async def delete_static_qr_code(
        self,
        id: str,
    ) -> DeleteStaticQrCodeResponse:
        """Delete a Pix QrCode Static.

        Endpoint to delete a Pix QrCode Static

        Args:
            id (str): QrCode ID, correlationID or identifier

        Returns:
            DeleteStaticQrCodeResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/qrcode-static/{_path_param(id)}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteStaticQrCodeResponse, response.json())

    # openapi: unsupported — response of GetReceipt uses application/pdf — only
    #   application/json is modelled
    async def get_receipt(
        self,
        receipt_type: GetReceiptReceiptType,
        end_to_end_id: str,
    ) -> None:
        """Get a PDF document related to a payment transaction formatted as a receipt by
        type (pix-in, pix-out or pix-refund).

        Args:
            receipt_type (GetReceiptReceiptType): The ReceiptType from the payment
                transaction to export.
            end_to_end_id (str): The EndToEndId from the payment transaction to export.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = (
            f"//api/v1/receipt/{_path_param(receipt_type)}/{_path_param(end_to_end_id)}"
        )
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def list_refunds(self) -> ListRefundsResponse:
        """Get a list of refunds.

        Returns:
            ListRefundsResponse: The 200 response body, validated.

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
        return _validate(ListRefundsResponse, response.json())

    async def create_refund(
        self,
        *,
        body: RefundPayload,
    ) -> CreateRefundResponse:
        """Create a new refund.

        Endpoint to create a new refund for a customer

        Args:
            body (RefundPayload): The request body.

        Returns:
            CreateRefundResponse: The 200 response body, validated.

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
        return _validate(CreateRefundResponse, response.json())

    async def get_refund(
        self,
        id: str,
    ) -> GetRefundResponse:
        """Get one refund.

        Args:
            id (str): refund ID or correlation ID

        Returns:
            GetRefundResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/refund/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetRefundResponse, response.json())

    async def create_stablecoin_deposit(
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
        POLYGON, ETHEREUM, CELO, TRON, BNB - USDC: POLYGON, ETHEREUM, BASE, CELO, BNB -
        BRLA: POLYGON, ETHEREUM, BASE, CELO

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

    async def approve_stablecoin_deposit(
        self,
        *,
        body: ApproveStablecoinDepositBody,
    ) -> ApproveStablecoinDepositResponse:
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
            body (ApproveStablecoinDepositBody): The request body.

        Returns:
            ApproveStablecoinDepositResponse: The 200 response body, validated.

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
        return _validate(ApproveStablecoinDepositResponse, response.json())

    async def get_stablecoin_payout_by_correlation_id(
        self,
        *,
        correlation_id: str,
    ) -> None:
        """Get the current status of a payout by correlationId.

        Same as `GET /api/v1/stablecoin/payout/{payoutId}`, looked up by the
        `correlationId` sent on create.

        Requires the `STABLECOIN_PAYOUT_CREATE` scope.

        Args:
            correlation_id (str): The idempotency key sent on create.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404.
        """
        path = "/api/v1/stablecoin/payout"
        params: dict[str, Any] = {}
        params["correlationId"] = _param(correlation_id)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return None

    async def create_stablecoin_payout(
        self,
        *,
        body: CreateStablecoinPayoutBody,
    ) -> CreateStablecoinPayoutResponse:
        """Pay out INTERNAL balance to BRL via Pix.

        Converts a stablecoin balance held on the company's INTERNAL float into BRL and
        sends it to the given Pix key. Supported input assets: `USDT`, `USDC`, `BRLA`.

        `value` is the amount to spend from the INTERNAL balance, in cents of the input
        asset. The subaccount is resolved from the Application's `companyBankAccount`,
        same as the wallets endpoint.

        Flow: balance check → consume Woovi **OUT** limit → resolve Pix beneficiary →
        provider quote/ticket. There is no deposit or approval step.

        Fund the INTERNAL float first via `GET /api/v1/stablecoin/wallets` (send
        USDT/USDC/BRLA on-chain to a returned address), then call this endpoint.

        Idempotency is supported via `correlationId`: reusing one returns the payout
        already created for it.

        Requires the `STABLECOIN_PAYOUT_CREATE` scope.

        Args:
            body (CreateStablecoinPayoutBody): The request body.

        Returns:
            CreateStablecoinPayoutResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401.
        """
        path = "/api/v1/stablecoin/payout"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreateStablecoinPayoutResponse, response.json())

    async def quote_stablecoin_payout(
        self,
        *,
        value: int,
        currency: StablecoinDepositRequestCurrency,
    ) -> QuoteStablecoinPayoutResponse:
        """Quote an INTERNAL balance off-ramp to BRL via Pix.

        Returns a quote for converting a stablecoin balance held on the company's
        INTERNAL float into BRL delivered via Pix. Supported input assets: `USDT`,
        `USDC`, `BRLA`.

        `value` is the amount to spend from the INTERNAL balance, in cents of the input
        asset.

        Requires the `STABLECOIN_PAYOUT_CREATE` scope.

        Args:
            value (int): Amount to quote, in cents of the input asset.
            currency (StablecoinDepositRequestCurrency): Stablecoin asset to spend from
                the INTERNAL balance.

        Returns:
            QuoteStablecoinPayoutResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 502.
        """
        path = "/api/v1/stablecoin/payout/quote"
        params: dict[str, Any] = {}
        params["value"] = _param(value)
        params["currency"] = _param(currency)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(QuoteStablecoinPayoutResponse, response.json())

    async def get_stablecoin_payout(
        self,
        payout_id: str,
    ) -> None:
        """Get the current status of a payout.

        Returns the current state of a payout created through `POST
        /api/v1/stablecoin/payout`.

        While the payout is still in flight the provider ticket is re-read and the
        payout is updated before responding. Terminal payouts are served from storage.

        Requires the `STABLECOIN_PAYOUT_CREATE` scope.

        Args:
            payout_id (str): The `payoutId` returned by the create call.

        Returns:
            None: Nothing — the operation answers 200 with no JSON body.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 404.
        """
        path = f"/api/v1/stablecoin/payout/{_path_param(payout_id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return None

    async def get_stablecoin_quote(
        self,
        *,
        value: int,
        currency: StablecoinDepositRequestCurrency | None = None,
    ) -> GetStablecoinQuoteResponse:
        """Get a stablecoin quote without creating a deposit.

        Returns a PIX (BRL) -> stablecoin quote for the given `value` and `currency`
        without creating a deposit. Use it to display the exact amount of stablecoin the
        customer would receive before confirming.

        The quote is fetched from the provider and cached for 60 seconds.

        Requires the `STABLECOIN_DEPOSIT_CREATE` scope.

        Args:
            value (int): Amount to quote, in cents (BRL). Must be positive.
            currency (StablecoinDepositRequestCurrency | None): Stablecoin to receive.
                Defaults to USDT. Omitted from the query when None.

        Returns:
            GetStablecoinQuoteResponse: The 200 response body, validated.

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
        return _validate(GetStablecoinQuoteResponse, response.json())

    async def list_stablecoin_subaccounts(self) -> StablecoinSubAccountListResponse:
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

    async def create_stablecoin_subaccount(
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

    async def get_stablecoin_subaccount(
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
        path = f"/api/v1/stablecoin/subaccount/{_path_param(sub_account_id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(StablecoinSubAccountGetResponse, response.json())

    async def get_stablecoin_subaccount_balances(
        self,
        sub_account_id: str,
    ) -> GetStablecoinSubaccountBalancesResponse:
        """Read the INTERNAL balances of a subaccount's float.

        Returns the provider's INTERNAL balance per asset for this subaccount — exactly
        the balance `POST /api/v1/stablecoin/payout` debits.

        Poll this after sending funds to one of the addresses from `GET
        /api/v1/stablecoin/wallets` (or the subaccount wallets route) and only create
        the payout once the credit has landed.

        Only subaccounts belonging to the authenticated company resolve; any other id
        returns `404`.

        Requires the `STABLECOIN_SUBACCOUNT_LIST` scope.

        Args:
            sub_account_id (str): Provider subaccount id (`subAccountId`, not the Woovi
                `id`).

        Returns:
            GetStablecoinSubaccountBalancesResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 404, 502.
        """
        path = f"/api/v1/stablecoin/subaccount/{_path_param(sub_account_id)}/balances"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetStablecoinSubaccountBalancesResponse, response.json())

    async def list_stablecoin_subaccount_wallets(
        self,
        sub_account_id: str,
    ) -> ListStablecoinSubaccountWalletsResponse:
        """List the deposit addresses of a subaccount's float.

        Returns the wallets the provider holds for this subaccount. Sending an asset
        on-chain to one of these addresses credits the subaccount's INTERNAL balance —
        the same balance `POST /api/v1/stablecoin/swap` spends and `POST
        /api/v1/stablecoin/withdraw` pays out from.

        Use this to resolve where to prefund instead of hardcoding an address: funding a
        different account's wallet leaves the swap float empty and the swap fails for
        lack of balance.

        Only subaccounts belonging to the authenticated company resolve; any other id
        returns `404`.

        Requires the `STABLECOIN_SUBACCOUNT_LIST` scope.

        Args:
            sub_account_id (str): Provider subaccount id (`subAccountId`, not the Woovi
                `id`).

        Returns:
            ListStablecoinSubaccountWalletsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                401, 404, 502.
        """
        path = f"/api/v1/stablecoin/subaccount/{_path_param(sub_account_id)}/wallets"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ListStablecoinSubaccountWalletsResponse, response.json())

    async def list_stablecoin_wallets(self) -> ListStablecoinWalletsResponse:
        """List custodian deposit wallets for the AppID bank account.

        Returns the deposit addresses for the stable sub-account linked to the
        authenticated Application's `companyBankAccount` (live from the provider — not
        stored in Mongo).

        A company can have more than one bank account / KYB sub-account. The sub-account
        is resolved from `Application.companyBankAccount` (the account bound to the
        AppID), not from the company alone.

        Sending an asset on-chain to one of these addresses credits the INTERNAL float
        used by `POST /api/v1/stablecoin/payout` (USDT/USDC/BRLA → Pix).

        For an explicit provider id use `GET
        /api/v1/stablecoin/subaccount/{subAccountId}/wallets`. To read the float balance
        after funding use `GET /api/v1/stablecoin/subaccount/{subAccountId}/balances`.

        Requires the `STABLECOIN_SUBACCOUNT_LIST` scope.

        Returns:
            ListStablecoinWalletsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 404, 502.
        """
        path = "/api/v1/stablecoin/wallets"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ListStablecoinWalletsResponse, response.json())

    async def get_statement(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        skip: int | None = None,
        limit: int | None = None,
        company_bank_account: str | None = None,
    ) -> list[GetStatementResponseItem]:
        """Get statement by company.

        Retrieves the statement/ledger entries for a company's bank account

        Args:
            start (datetime | None): The start value. Omitted from the query when None.
            end (datetime | None): The end value. Omitted from the query when None.
            skip (int | None): The skip value. Omitted from the query when None.
            limit (int | None): The limit value. Omitted from the query when None.
            company_bank_account (str | None): Read the statement of another bank
                account of your company instead of the one linked to the appID. Only a
                MASTER application of a company with the MASTER_APP_READ_ANY_ACCOUNT
                feature can use it. Use the accountId returned by GET /api/v1/account.
                Omitted from the query when None.

        Returns:
            list[GetStatementResponseItem]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 401, 403, 500.
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
        if company_bank_account is not None:
            params["companyBankAccount"] = _param(company_bank_account)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(list[GetStatementResponseItem], response.json())

    async def list_subaccounts(self) -> ListSubaccountsResponse:
        """Get a list of subaccounts.

        Returns:
            ListSubaccountsResponse: The 200 response body, validated.

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
        return _validate(ListSubaccountsResponse, response.json())

    async def create_subaccount(
        self,
        *,
        body: SubAccountPayload,
    ) -> CreateSubaccountResponse:
        """Create a subaccount.

        Args:
            body (SubAccountPayload): The request body.

        Returns:
            CreateSubaccountResponse: The 200 response body, validated.

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
        return _validate(CreateSubaccountResponse, response.json())

    async def transfer_between_subaccounts(
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

    async def get_subaccount(
        self,
        id: str,
    ) -> GetSubaccountResponse:
        """Get subaccount details.

        Args:
            id (str): pix key registered to the subaccount

        Returns:
            GetSubaccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subaccount/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetSubaccountResponse, response.json())

    async def delete_subaccount(
        self,
        id: str,
    ) -> DeleteSubaccountResponse:
        """Delete a Sub Account.

        Deletes a Sub Account if it has no remaining balance

        Args:
            id (str): Pix key registered to the subaccount

        Returns:
            DeleteSubaccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/subaccount/{_path_param(id)}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteSubaccountResponse, response.json())

    async def credit_subaccount(
        self,
        id: str,
        *,
        body: CreditSubaccountBody,
    ) -> CreditSubaccountResponse:
        """Credit subaccount.

        Transfers the amount from the main account to the subaccount.

        Args:
            id (str): Pix key registered to the subaccount
            body (CreditSubaccountBody): The request body.

        Returns:
            CreditSubaccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/subaccount/{_path_param(id)}/credit"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(CreditSubaccountResponse, response.json())

    async def debit_subaccount(
        self,
        id: str,
        *,
        body: DebitSubaccountBody,
    ) -> DebitSubaccountResponse:
        """Debit subaccount.

        Transfers the amount from the subaccount to the main account.

        Args:
            id (str): Pix key registered to the subaccount
            body (DebitSubaccountBody): The request body.

        Returns:
            DebitSubaccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/subaccount/{_path_param(id)}/debit"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(DebitSubaccountResponse, response.json())

    async def get_subaccount_statement(
        self,
        id: str,
        *,
        skip: int | None = None,
        limit: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[GetSubaccountStatementResponseItem]:
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
            list[GetSubaccountStatementResponseItem]: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/subaccount/{_path_param(id)}/statement"
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
        return _validate(list[GetSubaccountStatementResponseItem], response.json())

    async def withdraw_from_subaccount(
        self,
        id: str,
        *,
        body: SubAccountWithdrawPayload,
    ) -> WithdrawFromSubaccountResponse:
        """Withdraw from a Sub Account.

        Withdraw from a Sub Account and return the withdrawal transaction information

        Args:
            id (str): pix key registered to the subaccount
            body (SubAccountWithdrawPayload): The request body.

        Returns:
            WithdrawFromSubaccountResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subaccount/{_path_param(id)}/withdraw"
        payload = _dump(body)
        response = await self._client.request(
            "POST",
            path,
            json=payload,
        )
        response.raise_for_status()
        return _validate(WithdrawFromSubaccountResponse, response.json())

    async def list_subscriptions(self) -> ListSubscriptionsResponse:
        """Get a list of subscriptions.

        Returns:
            ListSubscriptionsResponse: The 200 response body, validated.

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
        return _validate(ListSubscriptionsResponse, response.json())

    async def create_subscription(
        self,
        *,
        body: SubscriptionPayload,
    ) -> CreateSubscriptionResponse:
        """Create a new Subscription.

        Endpoint to create a new Subcription

        Args:
            body (SubscriptionPayload): The request body.

        Returns:
            CreateSubscriptionResponse: The 200 response body, validated.

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
        return _validate(CreateSubscriptionResponse, response.json())

    async def get_subscription(
        self,
        id: str,
    ) -> GetSubscriptionResponse:
        """Get one subscription.

        Args:
            id (str): The globalID or correlationID of the subscription.

        Returns:
            GetSubscriptionResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subscriptions/{_path_param(id)}"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(GetSubscriptionResponse, response.json())

    async def cancel_subscription(
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
        path = f"/api/v1/subscriptions/{_path_param(id)}/cancel"
        response = await self._client.request(
            "PUT",
            path,
        )
        response.raise_for_status()
        return _validate(dict[str, Any], response.json())

    async def list_subscription_installments(
        self,
        id: str,
    ) -> ListSubscriptionInstallmentsResponse:
        """Get a list of installments by subscription.

        Args:
            id (str): The globalID of the subscription.

        Returns:
            ListSubscriptionInstallmentsResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/subscriptions/{_path_param(id)}/installments"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ListSubscriptionInstallmentsResponse, response.json())

    async def update_subscription_value(
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
        path = f"/api/v1/subscriptions/{_path_param(id)}/value"
        response = await self._client.request(
            "PUT",
            path,
        )
        response.raise_for_status()
        return _validate(dict[str, Any], response.json())

    async def list_transactions(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        charge: str | None = None,
        pix_qr_code: str | None = None,
        withdrawal: str | None = None,
        has_webhook: bool | None = None,
        type: ListTransactionsType | None = None,
    ) -> ListTransactionsResponse:
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
            type (ListTransactionsType | None): Filter transactions by type Omitted from
                the query when None.

        Returns:
            ListTransactionsResponse: The 200 response body, validated.

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
        return _validate(ListTransactionsResponse, response.json())

    async def get_transaction(
        self,
        id: str,
        *,
        company_bank_account: str | None = None,
    ) -> GetTransactionResponse:
        """Get a Transaction.

        Args:
            id (str): you can use the transaction id from openpix or the endToEndId of
                transaction from bank
            company_bank_account (str | None): Restrict the lookup to another bank
                account of your company instead of the one linked to the appID. Only a
                MASTER application of a company with the MASTER_APP_READ_ANY_ACCOUNT
                feature can use it. Use the accountId returned by GET /api/v1/account.
                Omitted from the query when None.

        Returns:
            GetTransactionResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400, 403.
        """
        path = f"/api/v1/transaction/{_path_param(id)}"
        params: dict[str, Any] = {}
        if company_bank_account is not None:
            params["companyBankAccount"] = _param(company_bank_account)
        response = await self._client.request(
            "GET",
            path,
            params=params,
        )
        response.raise_for_status()
        return _validate(GetTransactionResponse, response.json())

    async def create_transfer(
        self,
        *,
        body: TransferCreatePayload,
    ) -> CreateTransferResponse:
        """Create a Transfer.

        Endpoint to to transfer values between accounts

        Args:
            body (TransferCreatePayload): The request body.

        Returns:
            CreateTransferResponse: The 200 response body, validated.

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
        return _validate(CreateTransferResponse, response.json())

    async def list_webhooks(
        self,
        *,
        url: str | None = None,
    ) -> ListWebhooksResponse:
        """Get a list of webhooks.

        Args:
            url (str | None): You can use the url to filter all webhooks Omitted from
                the query when None.

        Returns:
            ListWebhooksResponse: The 200 response body, validated.

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
        return _validate(ListWebhooksResponse, response.json())

    async def create_webhook(
        self,
        *,
        body: CreateWebhookBody,
    ) -> CreateWebhookResponse:
        """Create a new Webhook.

        Endpoint to create a new Webhook

        Args:
            body (CreateWebhookBody): The request body.

        Returns:
            CreateWebhookResponse: The 200 response body, validated.

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
        return _validate(CreateWebhookResponse, response.json())

    async def list_webhook_events(self) -> ListWebhookEventsResponse:
        """Get a list of webhook events.

        Returns:
            ListWebhookEventsResponse: The 200 response body, validated.

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
        return _validate(ListWebhookEventsResponse, response.json())

    async def list_webhook_ips(self) -> ListWebhookIpsResponse:
        """Get a list of webhook IPs.

        Returns:
            ListWebhookIpsResponse: The 200 response body, validated.

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
        return _validate(ListWebhookIpsResponse, response.json())

    async def list_webhook_public_keys(self) -> ListWebhookPublicKeysResponse:
        """Get the public keys that verify the webhook signature.

        Retorna a chave pública usada para verificar o header `x-webhook-signature`,
        presente em todo webhook enviado pela Woovi.

        A assinatura é `base64(RSA-SHA256)` sobre o corpo **bruto** da request —
        verifique antes de fazer parse do JSON, porque reserializar muda os bytes e
        invalida a assinatura.

        Este endpoint **não exige autenticação**: a chave é pública por definição, e
        quem recebe webhook normalmente valida a assinatura em um contexto que não tem o
        AppID em mãos.

        A resposta é uma lista, e não uma chave só, para permitir rotação: durante uma
        troca de chave publicamos a antiga e a nova ao mesmo tempo, com `is_current`
        indicando qual está assinando agora. Aceite qualquer chave da lista ao verificar
        e você não quebra quando a rotação acontecer.

        Returns:
            ListWebhookPublicKeysResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                no error status.
        """
        path = "/api/v1/webhook/public-keys"
        response = await self._client.request(
            "GET",
            path,
        )
        response.raise_for_status()
        return _validate(ListWebhookPublicKeysResponse, response.json())

    async def delete_webhook(
        self,
        id: str,
    ) -> DeleteWebhookResponse:
        """Delete a Webhook.

        Endpoint to delete a Webhook

        Args:
            id (str): webhook ID

        Returns:
            DeleteWebhookResponse: The 200 response body, validated.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response. The specification documents
                400.
        """
        path = f"/api/v1/webhook/{_path_param(id)}"
        response = await self._client.request(
            "DELETE",
            path,
        )
        response.raise_for_status()
        return _validate(DeleteWebhookResponse, response.json())

    async def get_charge_qr_code_image(
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
        path = f"/openpix/charge/brcode/image/{_path_param(id)}.png"
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

# Referência: Integrações e pagamentos

O contrato canônico de pagamentos, os provedores (Mercado Pago, OpenPix, RevenueCat), a carteira com saque Pix, o gateway de WhatsApp e o relatório de erros de app.

[« Índice da referência](../reference.md)

---

## App errors

### `tempest_fastapi_sdk.app_errors`

::: tempest_fastapi_sdk.app_errors.models.BaseAppErrorModel
::: tempest_fastapi_sdk.app_errors.models.make_app_error_model
::: tempest_fastapi_sdk.app_errors.service.AppErrorService
::: tempest_fastapi_sdk.app_errors.router.make_app_error_router
::: tempest_fastapi_sdk.app_errors.schemas.AppPlatformEnum
::: tempest_fastapi_sdk.app_errors.schemas.AppErrorReportSchema
::: tempest_fastapi_sdk.app_errors.schemas.AppErrorCreateSchema
::: tempest_fastapi_sdk.app_errors.schemas.AppErrorResponseSchema
::: tempest_fastapi_sdk.app_errors.schemas.AppErrorDeviceSchema
::: tempest_fastapi_sdk.app_errors.schemas.AppErrorFilterSchema

---

## Payments (canonical contract)

### `tempest_fastapi_sdk.integrations.payment.base`

::: tempest_fastapi_sdk.integrations.payment.base.PixProvider
::: tempest_fastapi_sdk.integrations.payment.base.PixChargeRequest
::: tempest_fastapi_sdk.integrations.payment.base.PixCharge
::: tempest_fastapi_sdk.integrations.payment.base.PixPayer
::: tempest_fastapi_sdk.integrations.payment.base.PixPaymentEvent
::: tempest_fastapi_sdk.integrations.payment.base.PaymentStatusEnum
::: tempest_fastapi_sdk.integrations.payment.base.PixEventTypeEnum
::: tempest_fastapi_sdk.integrations.payment.base.PixRefundProvider
::: tempest_fastapi_sdk.integrations.payment.base.PixRefund
::: tempest_fastapi_sdk.integrations.payment.base.PixRefundStatusEnum
::: tempest_fastapi_sdk.integrations.payment.base.CardProvider
::: tempest_fastapi_sdk.integrations.payment.base.CardChargeRequest
::: tempest_fastapi_sdk.integrations.payment.base.CardCharge
::: tempest_fastapi_sdk.integrations.payment.base.PayoutProvider
::: tempest_fastapi_sdk.integrations.payment.base.PayoutRequest
::: tempest_fastapi_sdk.integrations.payment.base.PayoutResult
::: tempest_fastapi_sdk.integrations.payment.base.PayoutStatusEnum

### `tempest_fastapi_sdk.integrations.payment.settlement`

::: tempest_fastapi_sdk.integrations.payment.settlement.confirm_pix_payment
::: tempest_fastapi_sdk.integrations.payment.settlement.PixPaymentConfirmation
::: tempest_fastapi_sdk.integrations.payment.settlement.PixConfirmationOutcomeEnum

### `tempest_fastapi_sdk.integrations.payment.routing`

::: tempest_fastapi_sdk.integrations.payment.routing.PixProviderRouter

### `tempest_fastapi_sdk.integrations.payment.adapters`

::: tempest_fastapi_sdk.integrations.payment.adapters.openpix.OpenPixPixProvider
::: tempest_fastapi_sdk.integrations.payment.adapters.openpix.OpenPixPayoutProvider
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago.MercadoPagoPixProvider
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago.MercadoPagoCardProvider
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago.MercadoPagoOrderDelivery
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago.make_mercado_pago_webhook_delivery_dependency
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router.make_mercado_pago_router
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router.MercadoPagoWithdrawConfig
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router.MercadoPagoWebhookAckSchema
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router.MercadoPagoDeliveryHandler
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router.DEFAULT_WEBHOOK_PATH
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_router.DEFAULT_WITHDRAW_PATH
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.mercado_pago_provider_for
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.simulation_api_for
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.MercadoPagoSimulationResultSchema
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.MercadoPagoSimulatedDeliverySchema
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.SIMULATION_TAG
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.RemoteMercadoPagoSimulation
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.MercadoPagoSimulationBackend
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.DEFAULT_SIMULATION_PREFIX
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.MercadoPagoTransition
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation.ensure_simulation_allowed
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulator.MercadoPagoSimulator
::: tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulator.make_mercado_pago_simulation_router

---

## WhatsApp (zap-api)

### `tempest_fastapi_sdk.integrations.messaging.zap`

O cliente e os schemas são gerados de `vendor/zap-openapi.yaml` por
`scripts/regen_zap.py`. O receiver de webhook (`webhooks.py`) é escrito à
mão, portado do código do gateway. As duas metades resolvem de forma lazy,
então importar o namespace não constrói nenhum modelo.

::: tempest_fastapi_sdk.integrations.messaging.zap.client.ZapClient
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.AcceptedResponse
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.AcceptedResponseStatus
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.SendTextRequest
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.SendImageRequest
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.SendVideoRequest
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.SendAudioRequest
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.SendDocumentRequest
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.ReactionRequest
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.ReadRequest
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.TypingRequest
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.TypingRequestState
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.CheckNumberResponse
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.HistoryResponse
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.HistoryMessage
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.HistoryMessageDirection
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.QrResponse
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.SessionStartResponse
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.SessionStatusResponse
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.SessionStatusResponseStatus
::: tempest_fastapi_sdk.integrations.messaging.zap.schemas.ErrorResponse
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZAP_INBOUND_EVENT
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZAP_WEBHOOK_SIGNATURE_HEADER
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZAP_WEBHOOK_SIGNATURE_PREFIX
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZapWebhookEventEnum
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZapInboundMediaTypeEnum
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZapJidServerEnum
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZapOutboundKindEnum
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZapInboundMessage
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZapStatusCallback
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.ZapWebhookDelivery
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.webhook_verifier
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.make_zap_webhook_dependency
::: tempest_fastapi_sdk.integrations.messaging.zap.webhooks.is_forward_transition

### `tempest_fastapi_sdk.integrations.messaging.zap.router`

::: tempest_fastapi_sdk.integrations.messaging.zap.router.ZapInboundHandler
::: tempest_fastapi_sdk.integrations.messaging.zap.router.ZapStatusHandler
::: tempest_fastapi_sdk.integrations.messaging.zap.router.ZapWebhookAckSchema
::: tempest_fastapi_sdk.integrations.messaging.zap.router.make_zap_webhook_router

### `tempest_fastapi_sdk.integrations.messaging.zap.inbox`

::: tempest_fastapi_sdk.integrations.messaging.zap.inbox.ZapInboundMessageMixin
::: tempest_fastapi_sdk.integrations.messaging.zap.inbox.ZapDeliveryMixin
::: tempest_fastapi_sdk.integrations.messaging.zap.inbox.ZapInboundOutcomeEnum
::: tempest_fastapi_sdk.integrations.messaging.zap.inbox.ZapStatusOutcomeEnum
::: tempest_fastapi_sdk.integrations.messaging.zap.inbox.ZapInbox
::: tempest_fastapi_sdk.integrations.messaging.zap.inbox.DEFAULT_ACCEPTED_SERVERS
::: tempest_fastapi_sdk.integrations.messaging.zap.inbox.InboundT
::: tempest_fastapi_sdk.integrations.messaging.zap.inbox.DeliveryT

---

## Mercado Pago

### `tempest_fastapi_sdk.integrations.payment.mercado_pago`

::: tempest_fastapi_sdk.integrations.payment.mercado_pago.client.MercadoPagoClient
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.environment.DEFAULT_BASE_URL
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.events.MercadoPagoEventEnum
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.money.to_cents
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.money.from_cents
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.money.format_amount
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.MercadoPagoWebhookEvent
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.SignatureHeader
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.DEFAULT_SIGNATURE_VERSIONS
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.DEFAULT_TOLERANCE_SECONDS
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.MERCADO_PAGO_DATA_ID_QUERY
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.make_mercado_pago_webhook_dependency
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.verify_signature
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.sign_manifest
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.build_manifest
::: tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.parse_signature_header

---

## OpenPix

### `tempest_fastapi_sdk.integrations.payment.openpix`

::: tempest_fastapi_sdk.integrations.payment.openpix.client.OpenPixClient
::: tempest_fastapi_sdk.integrations.payment.openpix.environment.OpenPixEnvironmentEnum
::: tempest_fastapi_sdk.integrations.payment.openpix.events.OpenPixEventEnum
::: tempest_fastapi_sdk.integrations.payment.openpix.money.to_cents
::: tempest_fastapi_sdk.integrations.payment.openpix.money.reais_to_cents
::: tempest_fastapi_sdk.integrations.payment.openpix.money.cents_to_reais
::: tempest_fastapi_sdk.integrations.payment.openpix.webhooks.OpenPixWebhookEvent
::: tempest_fastapi_sdk.integrations.payment.openpix.webhooks.make_openpix_webhook_dependency
::: tempest_fastapi_sdk.integrations.payment.openpix.webhooks.webhook_verifier
::: tempest_fastapi_sdk.integrations.payment.openpix.webhooks.decode_public_key

---

## RevenueCat

### `tempest_fastapi_sdk.integrations.subscriptions.revenuecat`

::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.client.RevenueCatClient
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.client.revenuecat_http_client
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.client.REVENUECAT_API_BASE_URL
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.make_revenuecat_webhook_router
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.make_revenuecat_webhook_dependency
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.RevenueCatWebhookDelivery
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.RevenueCatWebhookAck
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.SecretSource
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.verify_authorization
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.verify_signature
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.sign_payload
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.parse_signature_header
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.parse_webhook
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.REVENUECAT_PROVIDER
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.REVENUECAT_AUTHORIZATION_HEADER
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.REVENUECAT_SIGNATURE_HEADER
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks.DEFAULT_SIGNATURE_TOLERANCE_SECONDS
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.snapshot.normalize
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.snapshot.SubscriptionSnapshot
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.snapshot.SubscriptionStatusEnum
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas.RevenueCatWebhook
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas.RevenueCatEvent
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas.RevenueCatExperiment
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas.RevenueCatCustomerInfo
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas.RevenueCatSubscriber
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas.RevenueCatSubscription
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas.RevenueCatEntitlement
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events.RevenueCatEventTypeEnum
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events.RevenueCatPeriodTypeEnum
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events.RevenueCatEnvironmentEnum
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events.RevenueCatStoreEnum
::: tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events.RevenueCatReasonEnum

## Google Drive (uploads)

### `tempest_fastapi_sdk.integrations.storage.google_drive`

::: tempest_fastapi_sdk.integrations.storage.google_drive.storage
::: tempest_fastapi_sdk.integrations.storage.google_drive.client
::: tempest_fastapi_sdk.integrations.storage.google_drive.auth

---

## Carteira e saque Pix

### `tempest_fastapi_sdk.wallet`

::: tempest_fastapi_sdk.wallet
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

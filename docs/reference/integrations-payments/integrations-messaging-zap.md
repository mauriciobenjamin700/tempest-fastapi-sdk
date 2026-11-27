# `tempest_fastapi_sdk.integrations.messaging.zap`

zap-api, o gateway de WhatsApp da casa: cliente, schemas e receiver de webhook.

[« Integrações e pagamentos](../integrations-payments.md)

---

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

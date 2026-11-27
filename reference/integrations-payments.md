# Referência: Integrações e pagamentos

O contrato canônico de pagamentos, os provedores (Mercado Pago, OpenPix, Stripe, RevenueCat), o backend de upload do Google Drive, a carteira com saque Pix, o gateway de WhatsApp e o relatório de erros de app.

[« Índice da referência](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference.md)

---

## Módulos

Uma página por módulo; cada uma renderiza a API pública dele a partir das docstrings.

| Módulo | O que cobre |
| --- | --- |
| [`app_errors`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/app-errors.md) | Erros que o app cliente reporta de volta ao serviço: schema, model e router. |
| [`integrations.messaging.zap`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-messaging-zap.md) | zap-api, o gateway de WhatsApp da casa: cliente, schemas e receiver de webhook. |
| [`integrations.messaging.zap.inbox`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-messaging-zap-inbox.md) | A camada de serviço que todo consumidor do zap-api escreve: reivindicar, acompanhar e enviar. |
| [`integrations.messaging.zap.router`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-messaging-zap-router.md) | O router opcional do FastAPI que recebe o webhook do zap-api. |
| [`integrations.payment.adapters`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-payment-adapters.md) | Os provedores traduzidos para o contrato canônico de pagamento. |
| [`integrations.payment.base`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-payment-base.md) | O contrato canônico de pagamento em que todo provedor embutido é mapeado. |
| [`integrations.payment.mercado_pago`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-payment-mercado-pago.md) | A superfície do Mercado Pago: cliente, schemas e webhook. |
| [`integrations.payment.openpix`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-payment-openpix.md) | A superfície do OpenPix: cliente, schemas e webhook. |
| [`integrations.payment.routing`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-payment-routing.md) | Rodar dois provedores de Pix lado a lado durante a migração de um para o outro. |
| [`integrations.payment.settlement`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-payment-settlement.md) | Confirmar um Pix no provedor antes de liberar qualquer coisa. |
| [`integrations.payment.stripe`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-payment-stripe.md) | Stripe, da chave de API ao webhook verificado. |
| [`integrations.storage.google_drive`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-storage-google-drive.md) | Google Drive como backend de `UploadUtils`: upload para uma pasta compartilhada. |
| [`integrations.subscriptions.revenuecat`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/integrations-subscriptions-revenuecat.md) | RevenueCat: assinaturas in-app da App Store e do Google Play. |
| [`wallet`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments/wallet.md) | Carteira: saldo, extrato, retenções e saque Pix em centavos inteiros. |

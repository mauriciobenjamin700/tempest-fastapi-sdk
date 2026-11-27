---
description: "Índice dos módulos de integração: contrato de pagamentos, Mercado Pago, OpenPix, Stripe, RevenueCat, upload no Google Drive, carteira Pix, gateway de WhatsApp e erros de app."
---

# Referência: Integrações e pagamentos

O contrato canônico de pagamentos, os provedores (Mercado Pago, OpenPix, Stripe, RevenueCat), o backend de upload do Google Drive, a carteira com saque Pix, o gateway de WhatsApp e o relatório de erros de app.

[« Índice da referência](../reference.md)

---

## Módulos

Uma página por módulo; cada uma renderiza a API pública dele a partir das docstrings.

| Módulo | O que cobre |
| --- | --- |
| [`app_errors`](integrations-payments/app-errors.md) | Erros que o app cliente reporta de volta ao serviço: schema, model e router. |
| [`integrations.messaging.zap`](integrations-payments/integrations-messaging-zap.md) | zap-api, o gateway de WhatsApp da casa: cliente, schemas e receiver de webhook. |
| [`integrations.messaging.zap.inbox`](integrations-payments/integrations-messaging-zap-inbox.md) | A camada de serviço que todo consumidor do zap-api escreve: reivindicar, acompanhar e enviar. |
| [`integrations.messaging.zap.router`](integrations-payments/integrations-messaging-zap-router.md) | O router opcional do FastAPI que recebe o webhook do zap-api. |
| [`integrations.payment.adapters`](integrations-payments/integrations-payment-adapters.md) | Os provedores traduzidos para o contrato canônico de pagamento. |
| [`integrations.payment.base`](integrations-payments/integrations-payment-base.md) | O contrato canônico de pagamento em que todo provedor embutido é mapeado. |
| [`integrations.payment.mercado_pago`](integrations-payments/integrations-payment-mercado-pago.md) | A superfície do Mercado Pago: cliente, schemas e webhook. |
| [`integrations.payment.openpix`](integrations-payments/integrations-payment-openpix.md) | A superfície do OpenPix: cliente, schemas e webhook. |
| [`integrations.payment.routing`](integrations-payments/integrations-payment-routing.md) | Rodar dois provedores de Pix lado a lado durante a migração de um para o outro. |
| [`integrations.payment.settlement`](integrations-payments/integrations-payment-settlement.md) | Confirmar um Pix no provedor antes de liberar qualquer coisa. |
| [`integrations.payment.stripe`](integrations-payments/integrations-payment-stripe.md) | Stripe, da chave de API ao webhook verificado. |
| [`integrations.storage.google_drive`](integrations-payments/integrations-storage-google-drive.md) | Google Drive como backend de `UploadUtils`: upload para uma pasta compartilhada. |
| [`integrations.subscriptions.revenuecat`](integrations-payments/integrations-subscriptions-revenuecat.md) | RevenueCat: assinaturas in-app da App Store e do Google Play. |
| [`wallet`](integrations-payments/wallet.md) | Carteira: saldo, extrato, retenções e saque Pix em centavos inteiros. |

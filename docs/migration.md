# Guia de migração

Passo a passo das mudanças que quebram compatibilidade, agrupadas por release minor. Siga a versão que casa com aquela **de onde** você está atualizando. As seções estão listadas da mais nova para a mais antiga, então num salto de várias versões leia e aplique-as de baixo para cima.

## 0.260.0 — a spec da OpenPix foi refrescada, e os métodos mudaram de nome

Quebra **todo** chamador do `OpenPixClient`: os 125 métodos mudaram de nome.
Quebra também quem importa classe de schema derivada de operação, quem lê
`OpenPixEnvironment.PRODUCTION`, e quem dependia de `extra="allow"` num model
que também é payload.

### Por que

O documento vendorizado estava duas versões atrás do publicado — `3.0.3`
intitulado "OpenPix" contra o `3.1.0` "Woovi" —, e nada no repositório media
isso. O documento novo traz `operationId` em **125 de 125** operações, onde o
antigo tinha zero: o gerador derivava nome do path, e agora usa o nome que o
provedor deu.

Isso é upside disfarçado de quebra. `post_api_v1_charge` era o nome que sobrava
de não haver nome; `create_charge` é o nome da operação.

### O que fazer

**1. Renomeie as chamadas.** Cada linha é uma substituição direta. Das 103, só
três mudaram de assinatura:

| Método | Mudança |
| --- | --- |
| `update_customer` | o path param passou de `correlation_id` para `id` — quem chamava por posição não sente, quem chamava por keyword sim |
| `get_statement` | ganhou `company_bank_account`, opcional |
| `get_transaction` | ganhou `company_bank_account`, opcional |

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargePayload,
    OpenPixClient,
)


async def cobrar(client: OpenPixClient, payload: ChargePayload) -> None:
    """Create one charge with the renamed method."""
    # até a v0.259.0: await client.post_api_v1_charge(body=payload)
    await client.create_charge(body=payload)
```

| Antes (v0.259.0) | Agora (v0.260.0) |
| --- | --- |
| `get_api_image_qrcode_base64_by_id` | `get_charge_qr_code_base64` |
| `post_api_v1_account` | `duplicate_account` |
| `delete_api_v1_account_register_by_id` | `delete_account_register` |
| `get_api_v1_account` | `list_accounts` |
| `delete_api_v1_account_by_account_id` | `close_account` |
| `get_api_v1_account_by_account_id` | `get_account` |
| `post_api_v1_account_by_account_id_withdraw` | `withdraw_from_account` |
| `delete_api_v1_application` | `delete_application` |
| `post_api_v1_application` | `create_application` |
| `post_api_v1_boleto_validate` | `validate_boleto` |
| `post_api_v1_cashback_fidelity` | `create_cashback_fidelity` |
| `get_api_v1_cashback_fidelity_balance_by_tax_id` | `get_cashback_fidelity_balance` |
| `get_api_v1_charge` | `list_charges` |
| `post_api_v1_charge` | `create_charge` |
| `delete_api_v1_charge_by_id` | `delete_charge` |
| `get_api_v1_charge_by_id` | `get_charge` |
| `patch_api_v1_charge_by_id` | `update_charge` |
| `get_api_v1_charge_by_id_refund` | `list_charge_refunds` |
| `post_api_v1_charge_by_id_refund` | `refund_charge` |
| `get_api_v1_company` | `get_company` |
| `get_api_v1_customer` | `list_customers` |
| `post_api_v1_customer` | `create_customer` |
| `patch_api_v1_customer_by_correlation_id` | `update_customer` |
| `get_api_v1_customer_by_id` | `get_customer` |
| `post_api_v1_decode_emv` | `decode_emv` |
| `get_api_v1_dispute` | `list_disputes` |
| `get_api_v1_dispute_by_id` | `get_dispute` |
| `post_api_v1_funds_recovery` | `create_funds_recovery` |
| `get_api_v1_funds_recovery_by_id` | `get_funds_recovery` |
| `post_api_v1_funds_recovery_by_id_cancel` | `cancel_funds_recovery` |
| `get_api_v1_installments_by_id` | `get_installment` |
| `post_api_v1_installments_by_id_cobr` | `create_installment_cobr` |
| `post_api_v1_installments_by_id_cobr_retry` | `retry_installment_cobr` |
| `get_api_v1_invoice` | `list_invoices` |
| `post_api_v1_invoice` | `create_invoice` |
| `get_api_v1_invoice_integration` | `get_invoice_integration` |
| `patch_api_v1_invoice_integration` | `set_invoice_integration_status` |
| `post_api_v1_invoice_integration` | `upsert_invoice_integration` |
| `put_api_v1_invoice_integration` | `update_invoice_integration_tax_fields` |
| `post_api_v1_invoice_integration_certificate` | `upload_invoice_integration_certificate` |
| `post_api_v1_invoice_integration_test` | `test_invoice_integration` |
| `post_api_v1_invoice_by_correlation_id_cancel` | `cancel_invoice` |
| `get_api_v1_invoice_by_correlation_id_pdf` | `get_invoice_pdf` |
| `get_api_v1_invoice_by_correlation_id_xml` | `get_invoice_xml` |
| `post_api_v1_kyc_onboarding` | `create_kyc_onboarding` |
| `get_api_v1_limits_by_account_id` | `get_account_limits` |
| `get_api_v1_partner_affiliate` | `list_partner_affiliates` |
| `post_api_v1_partner_application` | `create_partner_application` |
| `get_api_v1_partner_company` | `list_partner_companies` |
| `post_api_v1_partner_company` | `create_partner_company` |
| `get_api_v1_partner_company_by_tax_id` | `get_partner_company` |
| `get_api_v1_payment` | `list_payments` |
| `post_api_v1_payment` | `create_payment` |
| `post_api_v1_payment_approve` | `approve_payment` |
| `get_api_v1_payment_by_id` | `get_payment` |
| `get_api_v1_pix_keys` | `list_pix_keys` |
| `post_api_v1_pix_keys` | `create_pix_key` |
| `post_api_v1_pix_keys_check` | `check_pix_key` |
| `get_api_v1_pix_keys_tokens` | `list_pix_key_tokens` |
| `get_api_v1_pix_keys_tokens_logs` | `list_pix_key_token_logs` |
| `delete_api_v1_pix_keys_by_pix_key` | `delete_pix_key` |
| `get_api_v1_pix_keys_by_pix_key_check` | `check_pix_key_by_key` |
| `put_api_v1_pix_keys_by_pix_key_default` | `set_default_pix_key` |
| `get_api_v1_psp` | `list_psps` |
| `get_api_v1_qrcode_static` | `list_static_qr_codes` |
| `post_api_v1_qrcode_static` | `create_static_qr_code` |
| `delete_api_v1_qrcode_static_by_id` | `delete_static_qr_code` |
| `get_api_v1_qrcode_static_by_id` | `get_static_qr_code` |
| `get_api_v1_receipt_by_receipt_type_by_end_to_end_id` | `get_receipt` |
| `get_api_v1_refund` | `list_refunds` |
| `post_api_v1_refund` | `create_refund` |
| `get_api_v1_refund_by_id` | `get_refund` |
| `post_api_v1_stablecoin_deposit` | `create_stablecoin_deposit` |
| `post_api_v1_stablecoin_deposit_approve` | `approve_stablecoin_deposit` |
| `get_api_v1_stablecoin_quote` | `get_stablecoin_quote` |
| `get_api_v1_stablecoin_subaccount` | `list_stablecoin_subaccounts` |
| `post_api_v1_stablecoin_subaccount` | `create_stablecoin_subaccount` |
| `get_api_v1_stablecoin_subaccount_by_sub_account_id` | `get_stablecoin_subaccount` |
| `get_api_v1_statement` | `get_statement` |
| `get_api_v1_subaccount` | `list_subaccounts` |
| `post_api_v1_subaccount` | `create_subaccount` |
| `post_api_v1_subaccount_transfer` | `transfer_between_subaccounts` |
| `delete_api_v1_subaccount_by_id` | `delete_subaccount` |
| `get_api_v1_subaccount_by_id` | `get_subaccount` |
| `post_api_v1_subaccount_by_id_credit` | `credit_subaccount` |
| `post_api_v1_subaccount_by_id_debit` | `debit_subaccount` |
| `get_api_v1_subaccount_by_id_statement` | `get_subaccount_statement` |
| `post_api_v1_subaccount_by_id_withdraw` | `withdraw_from_subaccount` |
| `get_api_v1_subscriptions` | `list_subscriptions` |
| `post_api_v1_subscriptions` | `create_subscription` |
| `get_api_v1_subscriptions_by_id` | `get_subscription` |
| `put_api_v1_subscriptions_by_id_cancel` | `cancel_subscription` |
| `get_api_v1_subscriptions_by_id_installments` | `list_subscription_installments` |
| `put_api_v1_subscriptions_by_id_value` | `update_subscription_value` |
| `get_api_v1_transaction` | `list_transactions` |
| `get_api_v1_transaction_by_id` | `get_transaction` |
| `post_api_v1_transfer` | `create_transfer` |
| `get_api_v1_webhook` | `list_webhooks` |
| `post_api_v1_webhook` | `create_webhook` |
| `get_api_v1_webhook_events` | `list_webhook_events` |
| `get_api_v1_webhook_ips` | `list_webhook_ips` |
| `delete_api_v1_webhook_by_id` | `delete_webhook` |
| `get_openpix_charge_brcode_image_id_png` | `get_charge_qr_code_image` |

**2. Três métodos não têm substituição direta.**

| Antes | Agora | O que estava errado |
| --- | --- | --- |
| `post_api_v1_dispute_id_evidence(body=...)` | `upload_dispute_evidence(id, *, body=...)` | O path era `/api/v1/dispute/:id/evidence`, com dois-pontos literal na URL, e não havia argumento para nomear a disputa |
| `get_api_v1_account_register()` | `get_account_register(id)` | Docstring dizia "by CorrelationID" e o método não recebia nada |
| `delete_api_v1_payment_by_id(id)` | *removido* | O endpoint não existe: o documento publicado tem só `get` nesse path, e o DELETE que a Woovi documenta é em `/api/v1/charge/{id}` |

**3. Classe de schema derivada de operação mudou de nome.** As de
`components` **não** mudaram — `Charge`, `ChargePayload`, `ChargeStatus`,
`Transaction`, `Customer` seguem iguais. O que mudou é o inline:

O nome que existia até a v0.259.0 era `GetApiV1ChargeResponsePageInfo`. A
partir da v0.260.0:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ListChargesResponsePageInfo,
)
```

O padrão é o mesmo do método: `GetApiV1ChargeResponse…` vira
`ListChargesResponse…`, `PostApiV1PaymentBody…` vira `CreatePaymentBody…`.

**4. `OpenPixEnvironment.PRODUCTION` agora é `https://api.woovi.com`.** É o
`servers[0]` do documento refrescado. O host antigo continua no ar — medido em
2026-08-28, `GET /api/v1/charge` devolve `401` em `api.openpix.com.br`,
`api.woovi.com` e `api.woovi-sandbox.com` igualmente —, então serviço com a URL
fixada não quebra. Se você assere o valor em teste, atualize a expectativa.

**5. Se você lia `model_extra` de um model que também é payload.** Uma classe
alcançável pela resposta **e** pelo request body volta a `extra="ignore"`. São
**4 classes na OpenPix** — `PreRegistrationPayloadObject`, que é o body e o
`200` da mesma operação, mais as três que ela alcança — e **25 na Mercado
Pago**. O motivo é o que a v0.259.0 já dizia e não conseguia cumprir: chave
inesperada num payload é typo de quem chamou, e levá-la ao provedor é pior que
descartar.

### O que ganha sem fazer nada

- 24 operações novas: `anticipation` (7), `stablecoin` payout e wallets (7),
  `boleto-transaction` (2), `kyc-validation` (2), `files`, `webhook/public-keys`.
- `Transaction.webhook_sent[].status` passa a ser `int` — é um código HTTP.
- `to_cents` e `reais_to_cents` recusam **sempre** com `ValueError`. Antes,
  `"abc"` levantava `decimal.InvalidOperation`, `None` levantava `TypeError` e
  `float("inf")` levantava `OverflowError`.

## 0.259.0 — dinheiro da OpenPix passa a ser `int`

Quebra quem anotou variável com o tipo do model gerado, ou quem compara o dump
com um JSON esperado.

### O que muda

154 campos do pacote `integrations/payment/openpix` deixam de ser `float`:

- **valor monetário** — `Charge.value`, `ChargePayload.value`,
  `ChargeRefundPayload.value`, `Transaction.value`, `SubAccount.balance`, todos
  os `pix*Limit`, e os demais dos 50 `value` do documento;
- **contagem e offset de dia** — `skip` e `limit`, `installmentsCount`,
  `dayDue`, `daysForDueDate`, `expiresIn`. O par de paginação aparecia 27
  vezes como **campo de resposta** (`pageInfo`, `Pagination`) e em 6
  operações como query param.

A Woovi liquida em centavo inteiro — a própria spec diz isso na descrição do
campo, em 58 schemas numéricos — e tipava tudo como `number`. O efeito
visível é no fio:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import ChargePayload

ChargePayload(correlation_id="abc-1", value=1000).model_dump(
    by_alias=True, mode="json", exclude_none=True
)
# até a v0.258.0: {"correlationID": "abc-1", "value": 1000.0, ...}
# a partir da v0.259.0: {"correlationID": "abc-1", "value": 1000, ...}
```

**19 campos continuam `float`**: `basePrice` (taxa de câmbio),
`inputAmount`/`outputAmount` da cotação de stablecoin (o primeiro é
documentado como *"currency unit, not cents"*), `rate`, o balde de tokens de
rate limit, `annualRevenue` e `Installment.expiration` — os dois últimos sem
unidade declarada no documento, e por isso deixados como estavam.

!!! warning "Corrigido na v0.260.0"
    Esta seção dizia originalmente "18 campos", e que eles eram "os únicos onde
    a fração é real". Eram 19, e um deles era
    `Transaction.webhookSent[].status`, descrito no próprio documento como
    *"HTTP response status code of the webhook delivery attempt"* — código HTTP
    nunca é fracionário. Ele virou `int` na v0.260.0.

### O que fazer

Na maioria dos casos, nada — `int` satisfaz onde `float` era esperado em tempo
de execução. Confira três pontos:

- **Anotação sua.** `valor: float = charge.value` passa a ser erro de tipo.
  Troque para `int`.
- **Comparação de dump em teste.** `assert dumped == {"value": 1000.0}` falha:
  agora é `1000`. Em Python `1000 == 1000.0` é `True`, mas
  `{"value": 1000} == {"value": 1000.0}` também é — o que quebra é comparação
  de **string** JSON, e snapshot de `model_dump_json()`.
- **`to_cents` sobre model gerado.** Continua funcionando e continua validando
  (recusa negativo e fração), só não estreita mais nada. Para payload cru — o
  dicionário que saiu do JSON, um webhook — ele continua sendo a forma certa.

### O outro lado: seus campos param de sumir

Model de resposta gerado agora usa `extra="allow"`. Campo que o provedor
responde e a spec não declara fica em `model_extra` em vez de ser descartado
na validação:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import Charge

charge = Charge.model_validate({"value": 1000, "wooviAdicionouIsso": "depois"})
(charge.model_extra or {})["wooviAdicionouIsso"]   # "depois"
```

Model de **payload** não mudou: continua `extra="ignore"`, porque ali chave
inesperada é erro de digitação de quem chamou.

Três campos que estavam nessa situação passaram a ser declarados e tipados:
`Charge.fee`, `Charge.discount` e `Charge.value_with_discount`. Se você lia
algum deles pelo `HTTPClient` cru por causa disso, agora dá para usar o método
gerado.

## 0.258.0 — os arquivos de log passam a rotacionar

Não quebra assinatura nenhuma. Muda o que existe **no disco**: quem lia
`logs/info.log` de fora do SDK passa a ver uma janela, não o histórico
inteiro.

### O que muda

Os handlers por nível eram `logging.FileHandler` — crescimento sem teto. Agora
são `RotatingFileHandler` com `max_bytes=10_000_000` e `backup_count=5`: cada
nível para em ~60 MB (cinco rotacionados mais o que está sendo escrito), e
`info.log.1`, `info.log.2`, … aparecem ao lado do arquivo corrente.

O motivo é o mesmo que já tinha fechado o lado leitor deste par: o router de
`/logs` limita a leitura a `DEFAULT_MAX_RECORDS_PER_FILE = 20_000` registros
por arquivo, adicionado depois que um serviço com diretório de log em
gigabytes respondeu com worker morto. Num serviço que loga uma linha por
request, rodando em host de longa duração, é o `info.log` que enche o disco —
e disco cheio derruba o serviço e o que mais dividir a partição.

### O que fazer

Nada, na maioria dos casos. Confira dois pontos:

- **Coletor que segue o arquivo pelo nome.** Filebeat, Promtail, Fluent Bit e
  companhia lidam com rotação, mas um `tail -F` caseiro ou um script que abre
  o arquivo uma vez no boot perde a virada. Aponte o padrão para
  `info.log*` se você precisa dos rotacionados.
- **`GET /logs` mostra a janela corrente.** O endpoint lê os nomes exatos
  (`info.log`, `error.log`, …), então rotacionado não entra na resposta.
  Retenção mais longa é trabalho de coletor, não do endpoint.

### Se você quer o comportamento antigo

`max_bytes=0` volta ao `FileHandler` puro — para host onde `logrotate` ou um
sidecar já é o dono da retenção:

```python
from tempest_fastapi_sdk import configure_logging

configure_logging(level="INFO", max_bytes=0)
```

Os dois knobs são keyword-only e independentes: `backup_count` só é lido quando a
rotação está ligada.

## 0.257.0 — os fakes de agente passam a chamar o parâmetro de `tools`

Quebra quem chamava `chat_with_tools(..., specs=[...])` por keyword.

### O que muda

`ScriptedBackend` e `FailingBackend` existem para fingir os protocolos
`ChatBackend` / `ToolCallingBackend` — e não satisfaziam nenhum dos dois sob
mypy. O protocolo chama o parâmetro de `tools` e aceita `**kwargs`; os fakes
chamavam de `specs` e não aceitavam nada além. Um membro de protocolo só é
implementado por assinatura com o **mesmo nome de parâmetro**, então a linha de
abertura da receita de teste era erro de tipo:

```python
from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

agent = Agent(ScriptedBackend([replies("ok")]))
# até a v0.256.0:
# Argument 1 to "Agent" has incompatible type "ScriptedBackend";
# expected "ChatBackend | ToolCallingBackend"  [arg-type]
```

### O que fazer

Nada, se você chama posicionalmente — que é o que o `Agent` faz por dentro, e o
que a receita mostra. Se o seu teste chama o fake direto, por keyword:

```diff
-decision = await backend.chat_with_tools(messages, specs=specs)
+decision = await backend.chat_with_tools(messages, tools=specs)
```

O atributo `specs_seen` — onde o fake grava os nomes de tool oferecidos a cada
turno — **não** mudou de nome.

### O que passou a compilar

Três anotações que recusavam o argumento que a própria doc mandava passar:

- `EventStream.response(on_disconnect=task.cancel)`, porque `Task.cancel`
  devolve `bool` e a anotação pedia `None`;
- `RedisIdempotencyStore(Redis.from_url(...))`, `RedisResponseCacheStore(...)` e
  `RedisWebAuthnChallengeStore(...)`, porque os protocolos exigiam o nome de
  parâmetro `key`/`name` e retorno `Coroutine`, e o redis-py devolve
  `Awaitable`;
- `require_authenticated(identity)` com um `FirebaseIdentity`, porque o
  `TypeVar` estava preso a `BaseUserModel`.

Nenhuma delas muda runtime — só param de exigir contorno (`# type: ignore`,
`cast`) de quem roda type-checker.

## 0.256.0 — o 429 do `RateLimitMiddleware` passa a ser JSON

Quebra quem lê o corpo do 429 como texto.

### O que muda

Até a v0.255.0 o middleware respondia `text/plain` com o `error_message` cru:

```text
HTTP/1.1 429 Too Many Requests
content-type: text/plain; charset=utf-8

Too many requests
```

Agora responde o mesmo envelope que `register_exception_handlers` escreve em todo handler:

```text
HTTP/1.1 429 Too Many Requests
content-type: application/json
retry-after: 60

{"detail": "Too many requests",
 "code": "TOO_MANY_REQUESTS",
 "details": {"retry_after_seconds": 60, "limit": 15}}
```

O motivo é uma contradição do próprio SDK: `error_responses()` sempre apontou o 429 para o `ErrorResponseSchema`, então cliente gerado a partir do OpenAPI quebrava ao desserializar o texto — e quem adotava `register_exception_handlers` junto com o middleware ficava com duas formas de erro na mesma API.

### O que fazer

- **Cliente que ramifica por `status === 429`:** nada. O status e o `Retry-After` não mudaram.
- **Cliente que lê o corpo como texto:** passe a ler JSON e a usar `detail` para exibir, `code` para ramificar.

```typescript
// antes
const message = await response.text();

// depois
const { detail, code } = await response.json();
```

- **Serviço que reescrevia a resposta** (subclasse do middleware convertendo o texto em envelope) pode apagar o contorno: `error_message` e o novo `error_code` cobrem o caso.

### Se você precisa do texto de volta

Não há flag para isso. O corpo antigo era incompatível com o schema que a própria rota documenta; manter as duas formas manteria o defeito atrás de uma opção. Um serviço que realmente precise de outro formato pode subclassar o middleware e sobrescrever a resposta, como antes.

## 0.252.0 — `:memory:` do SQLite ganha conexão por sessão

Não quebra API. Muda a topologia de conexão de um banco `:memory:`, e por isso vale ler antes de atualizar se você depende do comportamento antigo.

### O que muda

`AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")` passa a construir o engine sobre um banco in-memory de **cache compartilhado** (`file:<nome-único>?mode=memory&cache=shared&uri=true`) com pool normal, em vez de deixar o SQLAlchemy escolher `StaticPool` com uma única conexão. O manager mantém uma conexão viva enquanto existir, porque banco de cache compartilhado morre com a última conexão.

Isso conserta o erro que apareceu na v0.200.0, quando o `BEGIN` explícito passou a ser emitido para todo engine SQLite:

```text
sqlite3.OperationalError: cannot start a transaction within a transaction
[SQL: BEGIN]
```

Duas sessões sobrepostas voltam a funcionar em `:memory:`, e o `RELEASE SAVEPOINT` continua não sendo um commit disfarçado.

### Se você depende de uma conexão só

Passe o pool explicitamente — pool informado pelo caller nunca é sobrescrito:

```python
from sqlalchemy.pool import StaticPool
from tempest_fastapi_sdk import AsyncDatabaseManager

db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
```

Isso restaura a topologia anterior, incluindo a falha em sessões sobrepostas.

### Se você trocou `:memory:` por arquivo temporário como contorno

Pode voltar para `:memory:`. O contorno continua funcionando, então não é urgente.

## 0.251.0 — a assinatura de webhook do Mercado Pago passa a seguir o algoritmo do provedor

Quebra quem passava `manifest_template=` ou desempacotava o retorno de
`parse_signature_header`. Se você só chama `verify_signature`, **nada muda no
seu código** — só passam a verificar entregas que antes eram rejeitadas.

### O manifesto omite par ausente, e por isso não é mais template

A implementação anterior renderizava
`"id:{data_id};request-id:{request_id};ts:{ts};"`. O validador oficial do
Mercado Pago (`mercadopago/sdk-nodejs`, `src/utils/webhook/index.ts`, commit
`99857f33`) **omite** o par cujo valor é ausente. Uma entrega sem `data.id`
assina `request-id:...;ts:...;`, e o template fixo assinava
`id:;request-id:...;ts:...;` — hash diferente, verificação falhando sempre.

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import build_manifest

# antes: DEFAULT_MANIFEST_TEMPLATE + str.format
# agora: a regra, exportada
build_manifest(data_id="", request_id="req-1", timestamp="1771891200")
# "request-id:req-1;ts:1771891200;"
```

`DEFAULT_MANIFEST_TEMPLATE` e o parâmetro `manifest_template=` foram
**removidos**: existiam porque o algoritmo era desconhecido, e um template não
consegue expressar a regra de omissão. Se você havia medido um manifesto
diferente e passava o seu, compare com `build_manifest` e abra uma issue se
ainda divergir.

### `parse_signature_header` devolve um objeto, não uma tupla

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import parse_signature_header

# antes
# timestamp, digest = parse_signature_header(header)

# agora
parsed = parse_signature_header("ts=1771891200,v1=abc123")
parsed.timestamp        # "1771891200"
parsed.digest()         # "abc123" — a primeira versão suportada
parsed.hashes           # {"v1": "abc123"} — o header pode carregar v1 e v2
```

### O que passou a existir

- `versions=` em `verify_signature`, default `("v1",)`. Uma migração do
  provedor para `v2` vira `versions=("v2", "v1")`, sem esperar release.
- `tolerance_seconds=` (e `now=`, para teste), que é o que faz o `ts` do
  manifesto trabalhar contra replay. Continua opt-in, como no upstream.
- Chave de header case-insensitive, valor só-espaço tratado como ausente, e
  `ts` não-numérico rejeitado como header malformado — três regras do
  upstream que faltavam.

## 0.234.0 — modelo gerado se constrói pelo nome Python, e o type-checker aceita

Nada muda em runtime. Muda o que o pyright aceita.

### Construa pelo nome do campo, não pelo alias

Os campos com nome de fio passaram de `Field(alias=...)` para
`Field(validation_alias=..., serialization_alias=...)`. O runtime é o mesmo — os
modelos já tinham `populate_by_name=True`, então as duas grafias sempre
validaram. O que muda é o parâmetro que um type-checker enxerga:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import ChargePayload

# antes: rodava, mas o pyright acusava "No parameter named correlation_id"
# agora: aceito pelos dois
payload = ChargePayload(correlation_id="pedido-1", value=1990)
```

Se o seu código escrevia pelo alias só para calar o checker
(`ChargePayload(correlationID="pedido-1")`), ele **continua rodando** — a
validação aceita o alias — mas agora é o checker que reclama. Troque pelo nome
Python.

Leitura e escrita não mudaram: `model_validate({"correlationID": ...})` continua
aceitando a grafia do fornecedor, e `model_dump(by_alias=True)` continua
emitindo ela.

## 0.233.0 — dois enums gerados da OpenPix mudaram de nome

Uma mudança, e ela só quebra quem importava os dois enums de pagamento pelo nome.

### `PaymentType` e `PaymentDestinationAliasType` foram renomeados

O gerador passou a emitir as variantes de `PaymentCreatePayload` (`oneOf` com quatro formas: Pix key, QR Code, Manual, Boleto), e são elas que agora registram esses enums primeiro. O nome passou a vir da variante:

| Antes | Agora |
| --- | --- |
| `PaymentType` | `PaymentCreatePayloadPixKeyType` |
| `PaymentDestinationAliasType` | `PaymentCreatePayloadPixKeyDestinationAliasType` |

Mesmos membros, mesmos valores — só o nome da classe mudou:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    PaymentCreatePayloadPixKeyType,
)

assert PaymentCreatePayloadPixKeyType.PIX_KEY.value == "PIX_KEY"
```

Se você comparava o valor em vez de importar a classe (`payment.type == "PIX_KEY"`), nada a fazer.

### O que **não** quebrou

`PaymentCreatePayload` e `PostApiV1PaymentBody` continuam importáveis: viraram alias de união sobre as variantes, então uma anotação `body: PostApiV1PaymentBody` segue válida. O que mudou é que agora elas carregam os campos do pagamento — antes eram modelos **sem propriedade nenhuma**, e `extra="ignore"` descartava em silêncio tudo que você passasse.

## 0.229.0 — saída estruturada do Ollama sai de `/api/generate` para `/api/chat`

Uma mudança, e ela só quebra **teste**, não runtime.

### `generate_structured` agora fala com `/api/chat`

`OllamaGenerator.generate_structured` postava em `/api/generate` com o schema no campo `format`. Isso está quebrado em modelo de raciocínio: contra o `gpt-oss:20b`, o daemon responde `200 OK` com `eval_count` não-zero e `response` **vazio**, porque a resposta cai num canal que aquele endpoint não expõe. Em `/api/chat` o JSON vem em `message.content`, e modelo sem raciocínio se comporta igual nos dois.

Em runtime não há o que ajustar — a chamada que devolvia lixo (ou nada) passa a devolver a instância. O que quebra é **teste com mock preso ao endpoint antigo**:

```python
import httpx
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import OllamaGenerator
from tempest_fastapi_sdk.utils import HTTPClient


class Pessoa(BaseModel):
    nome: str


async def antes() -> Pessoa:
    """Mock que casava com /api/generate — para de casar."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": '{"nome": "Ana"}', "done": True})

    client = HTTPClient(transport=httpx.MockTransport(handler))
    gen = OllamaGenerator("llama3.2", http_client=client)
    return await gen.generate_structured("Uma pessoa.", Pessoa)


async def depois() -> Pessoa:
    """A resposta agora vem em message.content."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": '{"nome": "Ana"}'}, "done": True},
        )

    client = HTTPClient(transport=httpx.MockTransport(handler))
    gen = OllamaGenerator("llama3.2", http_client=client)
    return await gen.generate_structured("Uma pessoa.", Pessoa)
```

Duas mudanças de comportamento acompanham:

- **Conteúdo vazio levanta `ValueError`** em vez de devolver nada. Se você tinha `try/except` tratando resultado vazio como "modelo não respondeu", troque por `except ValueError`.
- **`system=` é um parâmetro novo**, opcional. Use-o para a instrução quando o `prompt` for um documento longo: instrução colada acima do documento é ignorada — medido, 0 itens extraídos contra 20 com a instrução no turno `system`.

## 0.174.0 — erros que eram 500 viram 422, e o `order_by` é validado

Correções de robustez. Todas trocam um crash por uma resposta correta; nenhuma exige mudança de código, mas quatro mudam o status ou a exceção que o seu serviço vê.

### Senha longa agora é 422

Existe um teto: `AUTH_PASSWORD_MAX_BYTES`, default `72` — o limite duro do bcrypt, contado em **bytes** UTF-8. Antes, senha acima disso levantava `ValueError` do `hashpw` e subia como **500** no signup / reset / troca. Agora é `ValidationException` (**422**).

Se o seu frontend não valida comprimento, ele passa a receber 422 onde recebia 500. Se você trocou o hasher por um sem esse limite, suba o valor.

### `order_by` inválido agora é `ValidationException`

`BaseRepository.paginate` e `cursor_paginate` resolvem `order_by` pelo mapper do model. Nome que não é coluna mapeada levanta `ValidationException` (**422**) em vez de `AttributeError` (**500**).

Mudança de contrato em `cursor_paginate`: ele levantava `ValueError` nesse caso. Quem tinha `except ValueError` em volta precisa ajustar:

```python
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.exceptions import ValidationException

from src.db.models import UserModel

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

repo = BaseRepository(session, model=UserModel)


async def main() -> None:
    """Run this example."""
    try:
        page = await repo.cursor_paginate(order_by="nao_e_coluna")
    except ValidationException:
        ...


asyncio.run(main())
```

`ValueError` continua sendo o erro de cursor malformado.

### `BodySizeLimitMiddleware`: body grande em streaming responde 413

O 413 passou a ser emitido no instante em que a contagem estoura, e o que o app enviar depois é descartado. Antes ele saía num `finally`, depois de o app já ter respondido — e o FastAPI responde, convertendo o `ClientDisconnect` do guard em **400**. O segundo `http.response.start` fazia o uvicorn levantar `RuntimeError: Response already started`.

Efeito prático: um upload em streaming acima do limite responde **413** onde recentemente respondia **400** (com um `RuntimeError` no log). Um handler que não lê o body continua respondendo o que ele mesmo respondia — não há como retirar uma resposta já enviada.

### `make_csrf_token_dependency` grava o cookie

Antes ela só devolvia o token, então o cookie ficava ausente e o `POST` seguinte caía com 403. Agora ela grava (`Secure` + `SameSite=Lax`, não `HttpOnly` — o cliente precisa ler pra ecoar no header).

Se você já gravava o cookie à mão no handler, o valor é o mesmo (`request.state.csrf_token`) e nada muda: a dependency não sobrescreve cookie existente. Em dev sobre HTTP puro, passe `secure=False`, senão o browser não devolve o cookie.

### `OAuthUser.email_verified`

Campo novo (default `None`), nada quebra. Mas **leia a nota**: se você liga login social a conta existente pelo e-mail, exija `profile.email_verified is True`. No GitHub o valor é sempre `None` — o `GET /user` não traz campo de verificação, e o e-mail que ele devolve é o do perfil público, que o GitHub não exige verificar.

### `GET /logs` lê no máximo 20 000 registros por arquivo

Ajuste com `make_logs_router(max_records_per_file=...)`. São os mais recentes; o endpoint ordena do mais novo e pagina, então o que ficou fora não era alcançável. Um `WARNING` é logado quando o corte acontece.

## 0.173.0 — token só vale onde foi emitido pra valer, e cache não é mais compartilhado

Três correções de segurança mudam comportamento de default. Nenhuma exige mexer em código, mas vale conferir se você dependia do comportamento antigo.

### Refresh e MFA-pendente deixam de autorizar rota

`make_bearer_token_dependency`, `make_jwt_user_dependency`, `make_role_dependency`, `make_permission_dependency` e `UserAuthService.current_user_dependency()` aceitam agora **só** token de tipo `access`.

Antes, os três JWTs que o `UserAuthService` emite com o mesmo segredo verificavam identicamente, então o refresh token e o `mfa_token` do passo 1 do login funcionavam como bearer em qualquer rota autenticada — o segundo fator era contornável com só a senha.

Você é afetado se **de propósito** mandava um refresh token para uma rota comum:

```python
from tempest_fastapi_sdk import (
    JWTUtils,
    REFRESH_TOKEN_TYPE,
    make_bearer_token_dependency,
)

from src.core.settings import settings

tokens = JWTUtils(settings)


# Volta a aceitar aquele tipo naquela rota específica:
require_refresh = make_bearer_token_dependency(tokens, accepted_typ=(REFRESH_TOKEN_TYPE,))
```

Token assinado à mão com `JWTUtils.encode()` e **sem** `typ` continua aceito — a atualização não derruba sessão ativa. Só os marcadores que o próprio SDK estampava (`refresh: True`, `purpose: "mfa_pending"`) passam a ser rejeitados como access.

### `ResponseCacheMiddleware`: `private` por padrão, credencial não usa o store

Dois defaults mudaram:

- O `Cache-Control` emitido passou de `public, max-age=N` para `private, max-age=N`. Se você servia conteúdo genuinamente compartilhado e contava com cache de CDN, declare de novo: `cache_control="public, max-age=N"`.
- Requisição com `Authorization` ou `Cookie` não lê nem escreve no store compartilhado (`ETag`/`304` continuam). Para recuperar cache em rota autenticada, passe `cache_credentialed=True` — a credencial entra na chave, então cada chamador tem a sua entrada.

O header `X-Cache` também só aparece quando existe `store=`; antes vinha `MISS` mesmo no modo só-ETag.

### `IdempotencyMiddleware`: chave escopada por chamador

A chave passou de `(method, path, key)` para `(chamador, method, path, key)`, com o chamador vindo de um digest de `Authorization`/`Cookie`. Reuse da chave de outra pessoa não devolve mais a resposta dela.

Se o seu cliente troca de credencial entre o pedido original e o retry (rotação de token no meio do backoff), o retry deixa de bater na entrada anterior. Nesse caso aponte a identidade para algo estável:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import IdempotencyMiddleware, MemoryIdempotencyStore

store = MemoryIdempotencyStore()

app = FastAPI()


app.add_middleware(
    IdempotencyMiddleware,
    store=store,
    principal_resolver=lambda request: request.headers.get("x-api-key-id", ""),
)
```

Também mudou: `5xx` não é mais cacheado (`cache_server_errors=True` restaura), `Set-Cookie` fica fora da cópia guardada, e requisições concorrentes com a mesma chave no mesmo processo são serializadas.


## 0.138.1 — `BaseAppSettings` tem que ser a **última** base

A 0.138.1 passou a fazer **todo mixin de settings herdar `BaseAppSettings`** (antes eles estendiam `pydantic_settings.BaseSettings` cru). Isso conserta o `.env` deixando de ser carregado quando um mixin aparecia antes da base — o `model_config` canônico agora é materializado em cada mixin, independente da ordem.

Em troca, a ordem das bases deixou de ser estilo e virou **regra dura**: como os mixins são subclasses de `BaseAppSettings`, a linearização C3 do Python proíbe a base preceder a própria subclasse.

```python
# docs-guard: skip — os dois primeiros exemplos são o erro que a seção descreve
# ❌ quebra em tempo de import

from tempest_fastapi_sdk import BaseAppSettings, DatabaseSettings, RedisSettings


class Settings(DatabaseSettings, BaseAppSettings, RedisSettings): ...

# ❌ também quebra
class Settings(BaseAppSettings, DatabaseSettings): ...

# ✅ BaseAppSettings por último
class Settings(DatabaseSettings, RedisSettings, BaseAppSettings): ...
```

Antes da 0.159.1 o sintoma era o `TypeError` cru do pydantic, que não indica a correção:

```text
TypeError: Cannot create a consistent method resolution order (MRO) for bases BaseAppSettings, RedisSettings
```

e o `mypy` (com o plugin do pydantic) acusava duas vezes na mesma linha, sendo a segunda enganosa — sugere conflito de metaclasse quando a causa é só a posição de uma base:

```text
settings.py:4: error: Cannot determine consistent method resolution order (MRO) for "Settings"  [misc]
settings.py:4: error: Metaclass conflict: the metaclass of a derived class must be a (non-strict) subclass of the metaclasses of all its bases  [metaclass]
```

A partir da 0.159.1, `BaseAppSettings` usa a metaclasse [`AppSettingsMeta`](reference.md), que pré-checa a posição das bases e troca a mensagem por uma instrução:

```text
TypeError: Settings: BaseAppSettings must be the LAST base — RedisSettings already subclasses it, so listing BaseAppSettings before it is an invalid method resolution order (MRO). Move BaseAppSettings to the end of the base list: class Settings(RedisSettings, BaseAppSettings).
```

### Verifique

```bash
# procure Settings com BaseAppSettings fora do fim da lista de bases
grep -rn "class Settings(" -A 12 src/core/settings.py
```

- Mova `BaseAppSettings` para o **último** item da lista de bases.
- A ordem **entre os mixins** continua livre — só a posição da base importa.
- Nenhuma mudança de env var, de campo ou de valor: é exclusivamente ordem de herança.

## 0.92.0 — coluna `payload` no token de usuário

A 0.92.0 adiciona o fluxo de **troca / re-verificação / recuperação de e-mail**. Para carregar o e-mail pendente até a confirmação, `BaseUserTokenModel` ganhou uma coluna nova:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column


payload: Mapped[str | None] = mapped_column(String(320), nullable=True, default=None)
```

Como sua tabela `user_tokens` herda de `BaseUserTokenModel`, a coluna aparece automaticamente no modelo — mas o banco precisa de uma **migration**. É aditiva e segura (coluna anulável, sem default obrigatório):

```bash
# gere e aplique
tempest db revision -m "add payload to user_tokens"
tempest db upgrade
```

Ou, na mão:

```sql
ALTER TABLE user_tokens ADD COLUMN payload VARCHAR(320) NULL;
```

!!! info "Só isso"
    Nenhuma renomeação, nenhum default backfill. Fluxos existentes (ativação, reset de senha) continuam gravando `payload = NULL`. O novo fluxo de e-mail é totalmente opt-in — a recuperação (`POST /auth/email-recovery/request`) só é montada com `AUTH_EMAIL_RECOVERY_ENABLED=True`.

### Verifique

- Rode a migration antes de subir a 0.92.0 (a coluna precisa existir).
- Se você escreve `src/db/models/user_token.py` à mão em vez de usar `make_user_token_model`, a coluna vem da base abstrata — não precisa redeclarar, só migrar.

## 0.63.0 — usuário autenticado carregado na sessão de request

Antes da 0.63.0, `UserAuthService.current_user_dependency()` carregava o usuário autenticado chamando `load_user`, que abria a **própria** sessão (via `db.get_session_context()`) e a fechava ao terminar. O `UserModel` entregue à rota ficava **detached**: mutá-lo e dar `commit`/`refresh` na sessão de request (a dos seus repositories) levantava
`InvalidRequestError: Instance is not persistent within this Session`.

A partir da 0.63.0 a dependência carrega o usuário na **sessão de request** (`db.session_dependency` por padrão), via `get_user(subject, session)`. O usuário fica anexado à mesma sessão que os repositories usam, então leituras de relacionamentos lazy e escritas funcionam sem reanexar nada.

!!! warning "Compatibilidade"
    A dependência de auth e seus repositories precisam compartilhar o **mesmo callable** de sessão para o cache de sub-dependências do FastAPI casar. Quem segue o padrão recomendado já está coberto:

    ```python
    # resources.py
    get_session = db.session_dependency          # um único objeto, reutilizado
    ```

    Se você embrulha a sessão num provider próprio (`async def get_session(): ...`), passe-o explicitamente para a dependência, senão ela abre uma segunda sessão e o usuário volta a ficar detached:

    ```python
    get_current_user = auth.current_user_dependency(session_dependency=get_session)
    ```

!!! info "Defesa adicional"
    `BaseRepository.resolve()` agora reanexa instâncias detached via `session.merge()`. Mesmo que algum fluxo ainda receba um usuário detached, o `resolve` o traz de volta à sessão ativa em vez de quebrar — então serviços que faziam workarounds manuais (re-fetch por id antes de mutar) podem removê-los.

### Verifique

- Remova qualquer workaround do tipo "re-fetch por id antes de mutar o usuário autenticado" — não é mais necessário.
- Se você passava um `user_loader` de um argumento para `make_jwt_user_dependency`, ele continua funcionando. Para compartilhar a sessão de request, passe `session_dependency=` e use um loader de dois argumentos `(subject, session)`.

## 0.8.0 — renomeação de `ServerSettings`

A 0.8.0 renomeia todos os campos de `ServerSettings`, extrai os campos de log para um novo mixin `LogSettings` e adiciona onze outros primitivos. As renomeações são as únicas mudanças **que quebram** — todo primitivo novo é opt-in.

#### 1. Renomeie as variáveis de ambiente

| Antiga | Nova | Mixin |
| --- | --- | --- |
| `HOST` | `SERVER_HOST` | `ServerSettings` |
| `PORT` | `SERVER_PORT` | `ServerSettings` |
| `DEBUG` | `SERVER_DEBUG` | `ServerSettings` |
| *(nova)* | `SERVER_RELOAD` | `ServerSettings` |
| `LOG_LEVEL` | `LOG_LEVEL` | **movida para** `LogSettings` |
| `LOG_JSON` | `LOG_JSON` | **movida para** `LogSettings` |

`sed` mecânico em todo `.env` / `docker-compose.yml` / manifesto de deploy:

```bash
sed -i \
  -e 's/^HOST=/SERVER_HOST=/' \
  -e 's/^PORT=/SERVER_PORT=/' \
  -e 's/^DEBUG=/SERVER_DEBUG=/' \
  .env .env.example .env.test
```

`LOG_LEVEL` e `LOG_JSON` mantêm os nomes — só o mixin muda.

#### 2. Renomeie as referências no código

```bash
# `settings.HOST` → `settings.SERVER_HOST`, idem para PORT/DEBUG
grep -rn "settings\.\(HOST\|PORT\|DEBUG\)\b" src/ tests/
```

Substitua cada ocorrência pela forma `SERVER_*`. Se um serviço usava a
flag antiga `settings.DEBUG` para comportamento de debug a nível de
aplicação, troque para `settings.SERVER_DEBUG`; se ela era lida apenas
para o auto-reload do uvicorn, troque para `settings.SERVER_RELOAD`.

#### 3. Misture `LogSettings` no `Settings` do projeto

```diff
 from tempest_fastapi_sdk import (
     BaseAppSettings,
     CORSSettings,
     DatabaseSettings,
     JWTSettings,
+    LogSettings,
     RabbitMQSettings,
     RedisSettings,
     ServerSettings,
 )


 class Settings(
     ServerSettings,
+    LogSettings,
     DatabaseSettings,
     RedisSettings,
     RabbitMQSettings,
     JWTSettings,
     CORSSettings,
     BaseAppSettings,
 ):
     ...
```

Pule este passo se o serviço nunca leu `settings.LOG_LEVEL` /
`settings.LOG_JSON` — `configure_logging` aceita os valores diretamente
como argumentos nomeados.

#### 4. (Opcional) Adote os novos primitivos

Escolha o que se encaixa. Nenhum deles é obrigatório.

- Substitua o `uvicorn.run(...)` escrito à mão no `src/server.py` por
  [`run_server(...)`](recipes/http.md#ponto-de-entrada-programatico-do-servidor).
- Substitua o `get_current_user` escrito à mão por
  [`make_jwt_user_dependency(tokens, load_user)`](recipes/http.md#dependencias-jwt-bearer-usuario-atual-role).
- Mova os campos `SMTP_*` / `UPLOAD_*` / `TOKEN_SECRET` / `VAPID_*` /
  `TASKIQ_*` do `Settings` do projeto para o mixin correspondente do
  SDK ([Composição de mixins de settings](recipes/http.md#composicao-de-mixins-de-settings)).
- Adote o
  [`Outbox`](recipes/outbox.md) se
  você já escreve efeitos colaterais a partir da mesma transação que
  grava as linhas de domínio.

#### 5. Verifique

```bash
uv sync                      # pega as novas deps do pyproject
uv run pytest -q             # suite completa
uv run ruff check src tests  # confirma que nenhuma referência a `HOST`/`PORT`/`DEBUG` escapou
```

Se o `pytest` falhar com um `ValidationError` do Pydantic referenciando
`HOST` / `PORT` / `DEBUG`, alguma variável de ambiente não foi renomeada
(olhe o ambiente do processo ou o `.env`).

---

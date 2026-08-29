# openpix-evidence.md — o que foi medido no documento da OpenPix

A evidência por trás de `scripts/openpix_overlay.py` e do refresh da
v0.260.0. Toda linha aqui saiu de um comando que rodou; nenhuma saiu de
leitura de código.

Medições de **2026-08-28**, contra `vendor/openpix-openapi.json`
(`openapi: 3.1.0`, título `Woovi`, 125 operações, 153 schemas). Procedência
em [`PROVENANCE.md`](PROVENANCE.md).

## Como reproduzir

```bash
make openpix-diff          # relatório contra o documento publicado agora
make openpix-fetch         # rebaixa, regrava o digest, regenera
uv run pytest tests/integrations/payment/openpix
```

## 1. O documento vendorizado estava duas versões atrás

O que a v0.259.0 gerava, e o que o provedor publicava no mesmo dia:

```
VENDORIZADO  openapi 3.0.3 | "OpenPix" | 105 operações |  96 schemas
PUBLICADO    openapi 3.1.0 | "Woovi"   | 125 operações | 153 schemas
comum: 101 operações
```

Os 96 schemas do vendorizado existiam no publicado com **conjunto de
propriedades idêntico** — drift zero em `Charge` (22 props), `Customer`
(6), `Transaction` (15), `Refund` (6), `Subscription` (14). O arquivo era
um subconjunto fiel de uma publicação anterior, não uma edição à mão. O que
faltava era tempo, não integridade — e nada no repositório media isso.

### Dois métodos que não podiam funcionar

| Método (v0.259.0) | Defeito | Sucessor |
| --- | --- | --- |
| `post_api_v1_dispute_id_evidence` | montava `path = "/api/v1/dispute/:id/evidence"` — `:id` literal na URL, e a assinatura só recebia `body` | `upload_dispute_evidence(id, *, body)` |
| `get_api_v1_account_register` | docstring "Get account register by CorrelationID", **zero argumentos**: o parâmetro era declarado `in: path` num template sem placeholder | `get_account_register(id)` |

Os dois vinham de defeitos que a Woovi publicou e depois corrigiu. O
gerador tinha nota para o segundo caso, mas ela morria no sumário —
`_build_parameters` rodava fora do `capture()` da operação, então nunca
virava comentário no arquivo gerado. Corrigido na v0.260.0, com
`tests/openapi/test_parse.py::TestPathTemplateGaps` fixando as duas formas.

### O que o refresh trouxe

24 operações: `anticipation` (7), `stablecoin` payout e wallets (7),
`boleto-transaction` (2), `kyc-validation` (2), `files`, `webhook/public-keys`,
mais as quatro que substituem caminhos corrigidos.

E `operationId` em **125 de 125** operações, onde o documento antigo tinha
zero — é por isso que os métodos passaram a se chamar `create_charge` em
vez de `post_api_v1_charge`. Mapa completo em `docs/migration.md`.

## 2. Unidade inteira: 157 campos `number` viram `integer`

| Medida | Valor |
| --- | --- |
| Campos retipados | 157 |
| Campos `number` que sobram | 19 |
| Schemas numéricos com "cents"/"centavos" na própria descrição | 58 (ver método abaixo) |
| Campos `value` que a Woovi **já** tipa `integer` | 45 (contra 52 que ainda precisam da correção) |
| Operações que recebem `skip`/`limit` como query param | 7 |

As duas regras de descrição são **load-bearing, e por exatamente um campo
cada** — medido no documento anterior desligando uma de cada vez:

| Regra desligada | Efeito |
| --- | --- |
| `CENTS_PATTERN` | perde `StablecoinDepositListItem.inputAmount` |
| `NOT_CENTS_PATTERN` | retipa indevidamente o `inputAmount` da cotação |

É o que justifica ler a descrição **antes** do nome: o mesmo `inputAmount` é
centavo na lista de depósito e é `"currency unit, not cents"` na cotação.
`inputAmount` não está em `INTEGER_PROPERTY_NAMES` — se estivesse, a regra
de nome comeria a distinção.

**O custo, que a migração da v0.259.0 não citava.** Retipar endurece a
validação de entrada: `Charge.model_validate({"value": 1990.5})` levanta
`ValidationError` e derruba a resposta inteira, onde antes devolvia
`1990.5`. A maioria dos retipos sai do allowlist de nome, sem evidência na
descrição do próprio campo, então o modo de falha de um palpite errado não
é "tipo levemente errado" — é "chamada de dinheiro quebrada". Está dito em
`docs/migration.md` desde a v0.260.0.

## 3. Os 19 campos que continuam `float`

`tests/integrations/payment/openpix/test_overlay.py::TestWhatTheOverlayLeavesAlone`
fixa este conjunto. Um `number` novo num refresh falha ali, de propósito: a
unidade de um campo é julgamento de pessoa, não default silencioso.

| Campo | Por quê |
| --- | --- |
| `basePrice`, `inputAmount` ×2, `outputAmount` ×3, `rate` ×2 | cotação de stablecoin — fração real, e `inputAmount` é documentado como *"currency unit, not cents"* |
| `AnticipationRequest.monthlyFeePercentage` | percentual |
| `PixKeyTokens.{tokens,maxTokens,tokensAfterRefresh,refreshRate}`, `TokenBucketLog.{tokens,tokensBefore,tokensAfter}` | balde de tokens de rate limit; fração plausível, não verificada |
| `AccountRegister.annualRevenue`, `AccountRegisterPayload.annualRevenue` | dinheiro, mas o documento **não declara a unidade** |
| `Installment.expiration` | sem descrição; unidade não declarada |
| um schema alcançado sem nome (`balances.additionalProperties`) | mapa de saldo de stablecoin |

Silêncio não é evidência: campo sem descrição e fora do allowlist fica como
está. A v0.259.0 publicou prosa afirmando que os restantes eram "os únicos
onde a fração é real" — falso então, e a razão de este pin existir agora.

**Corrigido na v0.260.0:** `Transaction.webhookSent[].status`, descrito no
documento como *"HTTP response status code of the webhook delivery
attempt"*, era `float`. Passou a `integer` por `STATUS_CODE_PATTERN`, uma
regra de **descrição** e não mais um nome na lista: `status` é um dos nomes
mais reusados de qualquer API, e afirmar que todo `status` é inteiro seria
o tipo de palpite que este módulo existe para evitar.

## 4. Campos que a resposta traz e o documento não declara

Ainda justificado: o documento publicado em 2026-08-28 também não declara
nenhum dos quatro.

| Schema | Campo | No documento |
| --- | --- | --- |
| `Charge` | `fee`, `discount`, `valueWithDiscount` | ausentes |
| `ChargeRefund` | `refundId` | ausente (`endToEndId` existe, como `string`) |

`discount` e `valueWithDiscount` entraram em `INTEGER_PROPERTY_NAMES` na
v0.260.0. Sem isso a decisão "isto é centavo" morava em dois lugares que
discordavam: `_declare` pula propriedade que o provedor passe a declarar
("o overlay se aposenta sozinho"), e só a família 1 recuperaria o tipo —
que cobria `fee` e não os outros dois. Simulado antes da correção, o dia em
que a Woovi declarasse os três como `number` reverteria dois deles a
`float`, em silêncio.

## 5. A operação inventada foi removida

A v0.259.0 adicionou `DELETE /api/v1/payment/{id}` e publicou
`OpenPixClient.delete_api_v1_payment_by_id`, com docstring afirmando
*"Cancels a payment that was requested and not yet approved"*.

Medido contra o documento publicado:

```
métodos em /api/v1/payment/{id}: ['get']
paths de payment com delete:     []
```

Nenhum DELETE em nenhum caminho de payment; o DELETE que a Woovi documenta
é em `/api/v1/charge/{id}`. Corrigir um documento é para o que ele erra —
um endpoint que ninguém observou é palpite, e palpite não entra em caminho
de dinheiro. Removido na v0.260.0, com `test_no_operation_is_invented`
fixando a política.

## 6. Números que a prosa da v0.259.0 publicou errado

Corrigidos na v0.260.0. Ficam registrados porque nenhum guard lê prosa, e
porque a forma do erro se repete.

| Escrito | Medido |
| --- | --- |
| "18 campos continuam `float`" | 19 (no documento daquela release) |
| "são os únicos onde a fração é real" | Falso: `status` é código HTTP, `expiration` e `annualRevenue` não têm unidade declarada |
| "`skip` e `limit` ... em 27 operações cada" | 27 é a contagem como **campo de resposta** (`pageInfo`/`Pagination`); só **6** operações os recebiam como query param |
| "os demais 51 `value` do documento" | 50 retipados; 49 propriedades `value` typed `number` |
| "a OpenPix inteira: 373 schemas e 105 operações" | O client tinha **106** métodos — a doc publicava o número de antes da própria release |

Sobrevive: "35 vezes" reproduzia como `grep -ci "in cents"` no YAML de então
— número real, escopo não declarado.

**O método por trás do 58**, porque contagem sem método é o mesmo defeito:
percorre o documento inteiro, conta todo nó que tem `description` casando
`\b(cents?|centavos?)\b` **e** `type` em `number`/`integer`, pulando
`example`, `examples`, `enum` e `default`. Dá 73 nós no total, 58 numéricos.
Contagens restritas a `components.schemas`, ou que expandem `$ref`, dão outros
valores — foi o que uma auditoria desta release mediu ao tentar reproduzir o
número sem o método, chegando a onze resultados diferentes. Por isso ele está
escrito aqui.

```python
import json, re

document = json.load(open("vendor/openpix-openapi.json"))
pattern = re.compile(r"\b(cents?|centavos?)\b", re.IGNORECASE)
total = numeric = 0


def walk(node: object) -> None:
    """Count nodes whose own description names the unit."""
    global total, numeric
    if isinstance(node, list):
        for entry in node:
            walk(entry)
        return
    if not isinstance(node, dict):
        return
    if pattern.search(str(node.get("description") or "")):
        total += 1
        if node.get("type") in ("number", "integer"):
            numeric += 1
    for key, value in node.items():
        if key not in {"example", "examples", "enum", "default"}:
            walk(value)


walk(document)
print(total, numeric)   # 73 58
```

## 7. Um campo declarado com o tipo errado: `Charge.expiresIn`

Medição de **2026-08-29**, contra `api.woovi-sandbox.com` com AppID válido,
reportada na [issue #238](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/238).
Com o `splits` da v0.265.0 corrigido, o `POST /api/v1/charge` passa e a
**resposta** é que falha:

```text
pydantic_core._pydantic_core.ValidationError: 1 validation error for CreateChargeResponse
charge.expiresIn
  Input should be a valid string [type=string_type, input_value=3600, input_type=int]
```

Corpo cru da mesma chamada, HTTP 200:

```json
{"charge": {"value": 1190, "identifier": "5400e12f…", "status": "ACTIVE",
            "expiresIn": 3600, "fee": 50,
            "expiresDate": "2026-08-29T15:17:16.060Z"}}
```

O documento se contradiz sobre esse campo em **três** lugares — e é isso que
torna a correção uma leitura, não um palpite:

```bash
uv run python -c "
import json
schemas = json.load(open('vendor/openpix-openapi.json'))['components']['schemas']
print({n: s['properties']['expiresIn'].get('type')
       for n, s in schemas.items()
       if isinstance(s, dict) and isinstance(s.get('properties'), dict)
       and 'expiresIn' in s['properties']})
"
```

```text
{'Charge': 'string', 'ChargePayload': 'number', 'WebhookCharge': 'integer'}
```

`WebhookCharge` é o **mesmo objeto de cobrança** entregue por webhook, e ali
o documento já diz `integer`. Nenhuma API devolve como texto no corpo o
inteiro que anuncia no webhook.

A correção vive em `MISTYPED_PROPERTIES`, no `scripts/openpix_overlay.py`, e
**se aposenta sozinha**: `_retype` pula a propriedade que já estiver
declarada com o tipo certo, então no dia em que a Woovi corrigir o documento
a linha `overlay: ! Charge.expiresIn (type corrected)` some do log de
regeração.

### O `expiresIn` não estava sozinho

Varrendo o documento inteiro atrás da mesma forma — um nome de propriedade
declarado `string` num schema e numérico noutro —, aparecem mais dois, os
dois **dinheiro**, os dois em resposta que o cliente valida:

```text
PixQrCode.value            "string"  vs  PixQrCodePayload.value        "number"
WithdrawTransaction.value  "string"  vs  PixWithdrawTransaction.value  "number"
```

`PixQrCode` contra `PixQrCodePayload` é o mesmo objeto indo e voltando.
Sete dos 106 métodos do cliente não conseguiam ler uma resposta real por
causa disso: `get_static_qr_code`, `list_static_qr_codes`,
`create_static_qr_code`, `withdraw_from_account`, `get_dispute`,
`get_transaction` e `list_transactions` — as duas últimas porque
`Transaction.pixQrCode` é um `PixQrCode`.

Os dois entram no `MISTYPED_PROPERTIES` sem mudança de mecanismo. **Dois
casos reais ficaram de fora** e foram registrados no guard, não esquecidos: o
`dispute.value` inline de `GET /api/v1/dispute/{id}` e o `pix.value` dos três
callbacks `receivedPix*`. `_retype` endereça
`components.schemas.<Nome>.properties.<prop>`; esses precisavam de override
por JSON pointer, que é a seção 8.

O que impede o próximo é
`tests/integrations/payment/openpix/test_spec_type_conflicts.py`: ele varre o
documento corrigido e falha quando um nome novo passa a se contradizer.
Conflito que **não** é defeito precisa de uma entrada escrita dizendo por quê
— e uma entrada que deixou de conflitar também falha, para a tabela não virar
silenciador.

Rejeitado: `int | str` em união. Empurraria a ambiguidade para todo
consumidor, que passaria a precisar de `int(charge.expires_in)` defensivo
sem nunca saber se algum dia recebe texto.

## 8. Os dois que só um JSON pointer alcança

Medições de **2026-08-29**, contra o mesmo `vendor/openpix-openapi.json`
(digest inalterado). Fecham a classe que a v0.269.0 deixou aberta —
[issue #244](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/244).

### `dispute.value` quebrava a leitura hoje

`get_dispute` é um dos sete métodos que não liam resposta real, e continuava
quebrado depois da v0.269.0: a correção por nome de componente não alcança
schema inline. Reprodução, **antes** da correção:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import GetDisputeResponse

body = {
    "dispute": {
        "status": "CREATED",
        "name": "Fulano de Tal",
        "email": "fulano@example.com",
        "phoneNumber": "+5511999999999",
        "value": 15000,
        "disputeReason": "Produto nao entregue",
        "endToEndId": "E18236120202508291500s0123456789",
        "type": "MED",
    }
}
print(GetDisputeResponse.model_validate(body))
```

```text
pydantic_core._pydantic_core.ValidationError: 1 validation error for GetDisputeResponse
dispute.value
  Input should be a valid string [type=string_type, input_value=15000, input_type=int]
```

**Depois**, o mesmo corpo:

```text
ok: 15000 int
```

O que torna a correção uma leitura e não um palpite: o mesmo objeto de
disputa é declarado com **três** tipos, e o `string` é o único que aparece
uma vez só.

```bash
uv run python -c "
import json

document = json.load(open('vendor/openpix-openapi.json'))


def walk(node: object, trail: str) -> None:
    \"\"\"Print every schema shaped like the dispute object.\"\"\"
    if isinstance(node, list):
        for index, entry in enumerate(node):
            walk(entry, f'{trail}[{index}]')
        return
    if not isinstance(node, dict):
        return
    properties = node.get('properties')
    if isinstance(properties, dict) and {'value', 'disputeReason'} <= set(properties):
        print(f\"{properties['value'].get('type'):8} {trail}\")
    for key, value in node.items():
        if key not in {'example', 'examples', 'enum', 'default'}:
            walk(value, f'{trail}.{key}' if trail else key)


walk(document, '')
"
```

```text
string   paths./api/v1/dispute/{id}.get.responses.200.content.application/json.schema.properties.dispute
number   components.schemas.Dispute
number   components.schemas.DisputePayload
integer  components.schemas.WebhookOpenpixDisputeCreatedPayload.properties.dispute
integer  components.schemas.WebhookOpenpixDisputeAcceptedPayload.properties.dispute
integer  components.schemas.WebhookOpenpixDisputeRejectedPayload.properties.dispute
integer  components.schemas.WebhookOpenpixDisputeCanceledPayload.properties.dispute
```

`GET /api/v1/dispute` — a **lista** — não inlina nada: ela `$ref`a
`components.schemas.Dispute`, que o overlay já retipa para `integer` pela
regra de nome. Era a mesma API devolvendo o mesmo objeto com dois tipos,
dependendo de qual das duas rotas você chamasse.

### Os três callbacks `receivedPix*` não quebram nada hoje

```text
string   callbacks.receivedPix.{$request.body#/webhook.url}
string   callbacks.receivedPixDetached.{$request.body#/webhook.url}
string   callbacks.receivedPixQrCode.{$request.body#/webhook.url}
integer  components.schemas.WebhookOpenpixChargeCompletedPayload.pix
integer  components.schemas.WebhookOpenpixChargeCompletedNotSameCustomerPayerPayload.pix
integer  components.schemas.WebhookOpenpixTransactionReceivedPayload.pix
integer  components.schemas.WebhookOpenpixTransactionRefundReceivedPayload.pix
```

Mesmo objeto `pix`, `string` nos três callbacks e `integer` nos quatro
payloads de componente. Não quebra leitura porque o gerador não emite modelo
para `callbacks`:

```bash
grep -c "Callback" tempest_fastapi_sdk/integrations/payment/openpix/schemas.py
```

```text
0
```

Corrigido assim mesmo. Tipo errado no documento é defeito quer o gerador de
hoje leia aquela parte, quer não — e o dia em que alguém gerar os modelos de
callback não é o dia de descobrir isto.

### O mecanismo, e as duas disciplinas que ele herda

`MISTYPED_POINTERS`, em `scripts/openpix_overlay.py`, endereça o schema por
JSON pointer (RFC 6901) em vez de por nome de componente — é por isso que a
chave do callback, `{$request.body#/webhook.url}`, aparece escrita
`{$request.body#~1webhook.url}`. `_retype_pointer` **só aplica quando o tipo
declarado difere**, então o override se aposenta sozinho, e **reporta**:

```text
$ make openpix-regen
  + tempest_fastapi_sdk/integrations/payment/openpix/schemas.py
  + tempest_fastapi_sdk/integrations/payment/openpix/client.py
  ~ tempest_fastapi_sdk/integrations/payment/openpix/__init__.py (__all__)
  overlay: 157 numeric fields retyped as integer
  overlay: + Charge.fee
  overlay: + Charge.discount
  overlay: + Charge.valueWithDiscount
  overlay: + ChargeRefund.refundId
  overlay: ! Charge.expiresIn (type corrected)
  overlay: ! PixQrCode.value (type corrected)
  overlay: ! WithdrawTransaction.value (type corrected)
  overlay: ! /paths/~1api~1v1~1dispute~1{id}/get/responses/200/content/application~1json/schema/properties/dispute/properties/value (type corrected, by pointer)
  overlay: ! /paths/~1api~1v1~1webhook/post/callbacks/receivedPix/{$request.body#~1webhook.url}/post/requestBody/content/application~1json/schema/properties/pix/properties/value (type corrected, by pointer)
  overlay: ! /paths/~1api~1v1~1webhook/post/callbacks/receivedPixDetached/{$request.body#~1webhook.url}/post/requestBody/content/application~1json/schema/properties/pix/properties/value (type corrected, by pointer)
  overlay: ! /paths/~1api~1v1~1webhook/post/callbacks/receivedPixQrCode/{$request.body#~1webhook.url}/post/requestBody/content/application~1json/schema/properties/pix/properties/value (type corrected, by pointer)
  overlay: ~ Charge.status (enum lifted to its own component)
```

O diff gerado é de um campo só — `GetDisputeResponseDispute.value`, de
`str | None` para `int | None` —, que é a medida de C acima virando texto:
os callbacks corrigidos não têm modelo para mudar.

### O que sobrou no `KNOWN_CONFLICTS["value"]`

Só o `additionalInfo`. Depois da correção, os sete trails que ainda declaram
`value` como `string` são todos par chave/valor de `additionalInfo`, onde
texto é o tipo certo — fixados em `MONEY_VALUE_STRING_TRAILS`. Os quatro que
saíram de lá entraram no `MONEY_VALUE_POINTER_TRAILS`, que exige o contrário:
numéricos depois do overlay. A entrada de `value` continua no
`KNOWN_CONFLICTS` porque `value` **continua** se contradizendo (texto no
`additionalInfo`, inteiro no dinheiro), que é o que
`test_an_explanation_without_a_conflict_is_stale` cobra.

Os cinco guards foram vistos falhar com a tabela `MISTYPED_POINTERS`
esvaziada, e passar com ela:

```text
FAILED test_overlay.py::TestPointerOverrides::test_every_pointer_fires_on_the_document_we_vendor
FAILED test_overlay.py::TestPointerOverrides::test_the_dispute_value_stops_being_text
FAILED test_overlay.py::TestPointerOverrides::test_an_override_upstream_fixed_retires
FAILED test_spec_type_conflicts.py::...::test_the_money_field_is_text_only_where_it_should_be
FAILED test_spec_type_conflicts.py::...::test_the_pointer_corrections_are_numeric
5 failed, 36 passed in 0.90s
```

## 9. O enum fechado numa resposta é uma leitura recusada

Medições de **2026-08-29**, mesmo `vendor/openpix-openapi.json` —
[issue #241](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/241).

### O documento se contradiz sobre quanto o estado é fechado

```text
Charge.status        -> {"type": "string", "enum": ["ACTIVE", "COMPLETED", "EXPIRED"]}
WebhookCharge.status -> {"type": "string"}
```

`WebhookCharge` é o mesmo objeto de cobrança entregue por webhook. A Woovi
não se comprometeu com a lista no objeto que entrega, então um quarto estado
é coisa que a fonte que vendoramos permite.

### O que um quarto estado fazia

```python
from tempest_fastapi_sdk.integrations.payment.openpix import GetChargeResponse

GetChargeResponse.model_validate({"charge": {"value": 1190, "status": "CANCELLED"}})
```

```text
pydantic_core._pydantic_core.ValidationError: 1 validation error for GetChargeResponse
charge.status
  Input should be 'ACTIVE', 'COMPLETED' or 'EXPIRED' [input_value='CANCELLED']
```

A cobrança nunca chegava a existir, então o serviço respondia **500** sem o
valor real em lugar nenhum da resposta.

### Por que é um lift, e não uma deleção

Tirar o `enum` da propriedade resolve a leitura e **apaga a classe**
`ChargeStatus`, que existe só porque `Charge.status` declara aqueles valores
inline. Medido, comparando os dois patches contra o mesmo documento:

```bash
uv run python -c "
import copy
import json
import sys

sys.path.insert(0, 'scripts')
import openpix_overlay
from tempest_fastapi_sdk.openapi import parse_spec

document = json.load(open('vendor/openpix-openapi.json'))

naive = copy.deepcopy(document)
del naive['components']['schemas']['Charge']['properties']['status']['enum']
names = {schema.name for schema in parse_spec(naive, client_name='C').schemas}
print('delecao simples ->', 'ChargeStatus' in names)

patched, _report = openpix_overlay.apply(document)
names = {schema.name for schema in parse_spec(patched, client_name='C').schemas}
print('lift            ->', 'ChargeStatus' in names)
"
```

```text
delecao simples -> False
lift            -> True
```

`ChargeStatus` é superfície pública: está no `__all__`, chaveia o
`STATUS_MAP` do adapter, é o que a receita ensina a comparar
(`charge.status == ChargeStatus.COMPLETED`) e o guia de migração da v0.232.0
o cita como classe que **não** mudou de nome.

Por isso `LIFTED_ENUMS` move os valores para um componente próprio e troca a
propriedade por um `anyOf` dele com o tipo cru. O componente precisa ser
referenciado — componente que ninguém referencia o gerador poda —, e o campo
sai assim:

```text
Charge.status -> ChargeStatus | str | None
```

Valor conhecido continua chegando como membro do enum; valor novo chega como
a string que o provedor mandou.

### Os guards do lift foram vistos falhar

Com `LIFTED_ENUMS` esvaziada:

```text
FAILED test_overlay.py::TestLiftedEnums::test_the_property_stops_being_restricted
FAILED test_overlay.py::TestLiftedEnums::test_the_values_survive_as_a_component
FAILED test_overlay.py::TestLiftedEnums::test_the_property_still_references_the_component
FAILED test_overlay.py::TestLiftedEnums::test_a_lift_upstream_already_did_retires
FAILED test_overlay.py::TestLiftedEnums::test_the_table_names_the_component_consumers_import
5 failed, 31 passed in 0.72s
```

Os cinco, e não quatro: `test_a_lift_upstream_already_did_retires` passava
com a tabela vazia — "não reportou nada" é verdade tanto para a correção
aposentada quanto para a correção ausente. Ele ganhou uma primeira asserção
de que a tabela **dispara** no documento intacto, que é o que dá sentido à
segunda.


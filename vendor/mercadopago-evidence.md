# mercadopago-evidence.md — o que foi medido na integração do Mercado Pago

Medições de **2026-08-28**, com a seção 1 corrigida em **2026-08-30**.
Procedência em [`PROVENANCE.md`](PROVENANCE.md).

## 1. O documento upstream existe — a seção anterior estava errada

**Corrigido em 2026-08-30.** Esta seção afirmava que não existe documento
upstream. As sondas de 2026-08-28 continuam valendo; a conclusão tirada delas
não.

| Tentativa | 2026-08-28 | 2026-08-30 |
| --- | --- | --- |
| `https://api.mercadopago.com/openapi.json` | `404` | `404` |
| `https://api.mercadopago.com/openapi` | `404` | `404` |
| `https://raw.githubusercontent.com/mercadopago/openapi/main/openapi.yaml` | `404` | `404` |
| `https://raw.githubusercontent.com/mercadopago/openapi/main/spec3.yaml` | não sondado | **`200`** |

A terceira linha adivinhou o nome do arquivo. O repositório
`github.com/mercadopago/openapi` existe — público, Apache-2.0, criado em
2026-05-20, descrição *"MercadoPago's OpenAPI Specification"* — e o arquivo se
chama `spec3.yaml`. O `404` era do nome, não do repositório, e "a org não tem
repositório de especificação" nunca foi medido: foi inferido de um `404` sobre
outra coisa.

E o `scripts/regen_mercado_pago.py` **já nomeava a origem corretamente**, no
docstring do módulo, desde que o arquivo foi vendorizado:

> The specification comes from Mercado Pago's own repository,
> `github.com/mercadopago/openapi` (Apache-2.0), pinned at commit `73bc0e49`
> of 2026-08-04.

Trinta linhas abaixo, no mesmo arquivo, `SPEC_PATH` dizia *"no upstream to diff
against"*. O repositório se contradizia, e nenhum guard lê prosa.

`vendor/mercadopago-openapi.yaml` é byte a byte aquele `spec3.yaml`:

```text
vendorizado        260935 bytes  sha256 893ec14bfd912dd3…
commit 73bc0e49    260935 bytes  sha256 893ec14bfd912dd3…
main (2026-08-30)  260935 bytes  sha256 893ec14bfd912dd3…
```

`make mercadopago-fetch` rebaixa, desde a v0.276.0.

### O que isso não conserta

O upstream é do provedor, mas **não é completo**: ele omite as sete operações
que o SDK oficial da própria empresa chama (seção 5), e três operações que ele
carrega responderam `404` quando sondadas. Rebaixar responde *"o documento
mudou?"*, não *"a operação existe?"*. Por isso o overlay continua, e a
autoridade em conflito continua sendo o SDK oficial.

## 2. A autoridade é o SDK oficial

`mercadopago` no PyPI — **3.5.0**, `github.com/mercadopago/sdk-python` — é
escrito pelo provedor e soletra a URL de toda operação que chama.

**A regra é autoridade em conflito, não teto de superfície.** Onde o nosso
documento e o SDK discordam, o SDK vence. Onde o SDK é silencioso, o nosso
documento fica: ele é wrapper fino sobre os recursos mais usados, e as 82
operações que só nós carregamos — relatórios de liberação e settlement,
`post-purchase`, `instore` QR, terminais, wallet connect, lojas e POS —
respondem `401`/`403`, não `404`. Silêncio do SDK não é negação.

```bash
make mercadopago-diff
```

`scripts/mercadopago_diff.py` baixa o sdist, extrai as URLs e relata as duas
direções. A direção que importa — o que o SDK chama e não modelamos — está
em **zero** desde a v0.260.0, e um teste offline fixa isso contra
`OFFICIAL_SDK_CALLS`.

**Sobre o parser.** É `ast`, não regex, e resolve variável local. Duas
armadilhas, ambas medidas:

1. Regex que lê até o primeiro `)` atribui o verbo de uma chamada
   multi-linha à URL da seguinte — produziu **3 operações fantasma**
   (`POST /v1/customers/{}`, `POST /v1/payments/{}`,
   `POST /v1/advanced_payments/{}`), todas falsas.
2. Ler só argumento literal perde a URL montada em variável.
   `disbursement_refund.py` monta três assim, e **escondia 2 operações
   reais** — o inventário passou de 63 para 65 chamadas ao corrigir. O que o
   leitor ainda não resolver é **relatado**, nunca descartado calado.

## 3. Como uma rota inexistente se distingue — e o limite disso

Requisição **sem credencial** contra `api.mercadopago.com`:

| Resposta | Significado |
| --- | --- |
| `401` / `403` / `400` | a rota existe; o gate de auth ou de parâmetro respondeu antes |
| `404` | esta **combinação método+path** não é roteada |

**A sonda vale só para o verbo que ela usa.** Controle medido:

```
GET  /v1/customers        -> 404      mas POST /v1/customers é onde o SDK cria cliente
GET  /oauth/token         -> 405
GET  /v1/card_tokens      -> 401
```

Um `404` em `GET` não diz nada sobre um `DELETE` no mesmo path. Por isso
**toda remoção abaixo é de `GET`**, e a correção do customer se apoia só no
SDK.

> Uma versão anterior deste arquivo afirmava que
> `GET /v1/customers/123/delete → 404` provava que a rota `DELETE` não
> existia. Não prova. A correção continua certa — o SDK do provedor chama
> `DELETE /v1/customers/<id>` — mas por uma fonte, não por duas.

## 4. Duas rotas corrigidas

| Nossa spec | Correta | Evidência |
| --- | --- | --- |
| `DELETE /v1/customers/{id}/delete` | `DELETE /v1/customers/{id}` | `resources/customer.py:delete` chama a segunda. Só o SDK: a sonda `GET` não fala por um `DELETE` |
| `GET /authorized_payments` | `GET /authorized_payments/search` | `resources/authorized_payment.py:search`; e aqui a sonda vale, `GET` contra `GET` — a nossa `404`, a do SDK `401` |

A do customer **mescla o verbo**: `/v1/customers/{id}` já declara `get` e
`put`, e mover o path inteiro derrubaria os dois que estavam certos.

## 5. Sete operações adicionadas

O SDK chama, o documento omitia. Confirmadas duas vezes: o SDK as chama, e a
sonda responde `401`/`400`.

```
GET   /users/me
GET   /v1/advanced_payments/search
GET   /v1/advanced_payments/{advanced_payment_id}/refunds
POST  /v1/advanced_payments/{advanced_payment_id}/refunds
POST  /v1/advanced_payments/{advanced_payment_id}/disbursements/{disbursement_id}/refunds
POST  /v1/advanced_payments/{advanced_payment_id}/disburses
GET   /v1/chargebacks/search
```

**Corpo e resposta são `dict[str, Any]`, exceto `/users/me`.** Path e verbo
são medidos; a forma, só onde o sandbox a mostrou (seção 8). Declarar shape que
ninguém mediu é o defeito que a v0.259.0 shippou na OpenPix — com a diferença
de que lá nem o endpoint tinha fonte.

`limit` e `offset` são declarados nas duas buscas porque **todo** `/search`
deste documento os declara. Isso é convenção do próprio documento, uma
inferência declarada — não uma medição.

## 6. Três operações removidas

`GET` que responde `404` onde a vizinhança responde `401`/`403`, e sem
contraparte no SDK para corrigir na direção certa.

| Operação | Resposta | Vizinhança |
| --- | --- | --- |
| `GET /instore/integrator` | `404` | os demais `/instore` dão `401`/`403` |
| `GET /stores/{id}` | `404` | `GET /users/123/stores/search` dá `403` |
| `GET /post-purchase/v1/claims/reasons/{reason_id}` | `404` | o resto de `/post-purchase` dá `403` |

`PATCH /instore/integrator` **fica**. O `404` é por método, e nenhuma sonda
falou pelo `PATCH`.

## 7. O que continua fora, e como está marcado

As 82 operações que só nós carregamos ficam, pela regra da seção 2. Mas "só
nós carregamos" não é uma situação só, e desde a v0.262.0 o
`make mercadopago-diff` as separa por **o que responde por elas**:

| Balde | Qtd | O que sustenta |
| --- | --- | --- |
| Sondada viva | 35 | Requisição sem credencial respondeu `401`/`403`/`400`/`200` em 2026-08-28 |
| Nada responde por ela | 47 | Só o documento vendorizado, cuja origem não está registrada |

As 47 são **todas não-`GET`**, e isso não é coincidência: a sonda é por
método **e** path, então fala só pelo verbo que usa (seção 3). Mandar `POST`,
`PUT` ou `DELETE` para uma API de pagamento em produção para descobrir se
rotea não é forma aceitável de responder a pergunta.

Cada uma dessas 47 carrega `UNVERIFIED_NOTE` na própria docstring gerada:

```
**Unverified.** Neither the provider's SDK nor an unauthenticated probe
covers this operation, so nothing here confirms the API routes it.
See issue #227.
```

O inventário da sondagem vive em `PROBED_OPERATIONS`, com o código que cada
rota respondeu e a data. Contando o total: **147 operações = 65 cobertas pelo
SDK + 35 sondadas vivas + 47 sem evidência**.

**Atualizado em 2026-10-09:** a seção 8 sondou as 47 no sandbox. 32 viraram
"roteada", 4 foram marcadas "não roteada" e 11 continuam sem evidência.

Isso não torna as 47 erradas — torna visível que elas são de outra classe. A
diferença entre operação que o provedor chama e operação que só um documento
de origem desconhecida declara não devia ser invisível para quem lê o cliente
gerado.

## 8. Sandbox, 2026-10-09 (issue #226)

Credencial: access token de teste da aplicação (`TEST-…`, redigido), lido de
`~/.config/tempest-fastapi-sdk/mercadopago-sandbox.env`, fora do repositório.
Credencial `TEST-` pertence à conta real do integrador, mas opera no sandbox.
As requisições foram **escolhidas para não poderem dar certo** (corpo
malformado, id inexistente); o que está medido são as respostas, não a
inocuidade. Toda chamada usou `curl` contra
`https://api.mercadopago.com`.

### 8.1 Método: requisição que não pode dar certo, comparada a um irmão inventado

Para cada operação, duas requisições:

- **A**: sem credencial;
- **B**: com o token de teste.

Em ambas o corpo é JSON malformado (`{`) e todo id de path é `999999999999`.
`DELETE` vai sem corpo. Os dois `DELETE .../schedule` não têm id no path, e
com token desligariam um agendamento real, então foram só na forma A.

**Status sozinho não prova rota.** Os controles, paths inventados sob o mesmo
prefixo, mostraram três respostas que acontecem **antes** do roteamento:

| Controle (path que não existe) | A | B |
| --- | --- | --- |
| `POST /v1/tempest-probe-unrouted` | `404` `resource not found` (borda) | igual |
| `PATCH /v1/customers` | `404` `Resource /customers not found.` (serviço) | `403` PolicyAgent |
| `POST /terminals/v1/tempest-unrouted` | **`401`** `authorization value not present` | `403` PolicyAgent |
| `POST /post-purchase/v1/claims/<id>/actions/tempest-unrouted` | **`403`** PolicyAgent | `403` PolicyAgent |
| `POST /v1/account/release_report/tempest-unrouted` | **`403`** PolicyAgent | `404` `Resource … not found.` |
| `POST /users/<id>/tempest-unrouted` | **`403`** HTML do proxy | igual |
| `PUT /mpmobile/instore/qr/<id>/<id>/tempest-unrouted` | `405` HTML do proxy | igual |

E com token o PolicyAgent responde `403` para a API de customers inteira,
inclusive o `PATCH /v1/customers` que não existe. Por isso a operação só conta
como roteada quando a resposta **difere** da do irmão inventado; cada entrada de
`SANDBOX_ROUTED_OPERATIONS` em `scripts/mercadopago_overlay.py` diz contra o
que foi comparada.

Controles positivos (o SDK oficial chama): `POST /v1/customers` e
`DELETE /v1/customers/<id>` → A `401`; `PUT /v1/payments/<id>` → A e B `400`
`Bad JSON format`.

### 8.2 As 47: 32 roteadas, 4 não roteadas, 11 inconclusivas

**Roteadas (32)**, com o discriminador:

| Operação | Evidência (vs. irmão inventado) |
| --- | --- |
| `PUT /checkout/preferences/{id}/expire` | B `404` *"The preference with identifier … was not found"*; irmão B `404` genérico |
| `POST /v2/wallet_connect/agreements`, `DELETE …/agreements/{id}`, `POST …/agreements/{id}/payer_token`, `POST /v2/wallet_connect/discounts`, `POST /v2/wallet_connect/coupons` | A `403`; irmão A `404` |
| `POST /v1/payouts`, `PUT /v1/payouts/{id}/transactions/{id}/cancel` | A `400` `Invalid site`; irmão A `404` |
| `POST /v1/transaction-intents/process` | A `400` `Invalid site`; irmão A `405` |
| `DELETE /instore/qr/seller/collectors/{id}/pos/{id}/orders` | B `400` `pos_obtainment_by_external_id_error`; irmão `404` |
| `PUT /instore/qr/seller/collectors/{id}/stores/{id}/pos/{id}/orders` | B `400` *"Collector ID and Caller ID must be the same"*; irmão `404` |
| `POST /instore/orders/{id}/confirmation`, `POST`/`PUT /instore/orders/qr/seller/collectors/{id}/pos/{id}/qrs` | A `403`; irmão A `404` |
| `DELETE /mpmobile/instore/qr/{id}/{id}` | A `403`, B `403` `Forbidden` do serviço; irmão `404` `Route not found` |
| `POST /users/{id}/stores` | A `400` `Malformed Json`; irmão `403` do proxy |
| `PUT`/`DELETE /users/{id}/stores/{id}` | A `404` `store_not_found`; irmão `403` do proxy |
| `POST /pos`, `PUT /pos/{id}`, `DELETE /pos/{id}` | A `403`; irmão A `404` |
| `POST /v1/customers/{id}/addresses`, `PUT`/`DELETE …/addresses/{id}` | A `401`; irmão A `404` do serviço de customers |
| `POST`/`PUT /v1/account/release_report/config`, `POST /v1/account/release_report`, `POST …/release_report/schedule` | B `400`; irmão B `404` `Resource … not found.` |
| `POST`/`PUT /v1/account/settlement_report/config`, `POST /v1/account/settlement_report` | B `400`; irmão B `404` |
| `POST /v1/account/settlement_report/schedule` | B `404` *"Configuration not found. Please create a configuration first."*; irmão B `404` `Resource … not found.` |

Saem do `UNVERIFIED_NOTE`.

**Não roteadas (4)**: ficam no cliente, marcadas com `**Not routed.**` na
docstring (`UNROUTED_OPERATIONS`). Remover método público é decisão à parte;
a medição vai para quem lê o cliente.

| Operação | A | B |
| --- | --- | --- |
| `PUT /v1/chargebacks/{id}` | `404` *"Request method 'PUT' is not supported"* | igual |
| `PUT /v1/payments/{id}/cancellations` | `404` `resource not found` (borda) | igual; `PUT /v1/payments/{id}` chega ao serviço |
| `PATCH /instore/integrator` | `404` `resource not found` (borda) | igual; `POST` e `PUT` também |
| `PUT /mpmobile/instore/qr/{id}/{id}` | `405` do proxy | igual; `POST` no mesmo path chega ao serviço (B `400` `invalid_caller_id`) |

**Inconclusivas (11)**: resposta igual à do irmão inventado. Continuam com
`UNVERIFIED_NOTE`.

```
POST   /point/integration-api/devices/{id}/refund          A 401, B 401 = irmão
DELETE /point/integration-api/devices/{id}/refund/{id}     A 403, B 401 = irmão
PATCH  /terminals/v1/setup                                  A 401, B 403 = irmão
POST   /terminals/v1/actions                                A 401, B 403 = irmão
POST   /terminals/v1/actions/{id}/cancel                    A 401, B 403 = irmão
POST   /post-purchase/v1/claims/{id}/actions/send-message   A 403, B 403 = irmão
POST   /post-purchase/v1/claims/{id}/attachments            A 403, B 403 = irmão
POST   /post-purchase/v1/claims/{id}/actions/open-dispute   A 403, B 403 = irmão
POST   /post-purchase/v1/claims/{id}/actions/evidences      A 403, B 403 = irmão
DELETE /v1/account/release_report/schedule                  só A (403 = irmão)
DELETE /v1/account/settlement_report/schedule               só A (403 = irmão)
```

### 8.3 As sete do SDK: o que deu para tipar

| Operação | B (token de teste) | Resultado |
| --- | --- | --- |
| `GET /users/me` | `200` | **tipada**: `AuthenticatedUser` + 4 objetos aninhados |
| `GET /v1/advanced_payments/search` | `403` PolicyAgent | `dict[str, Any]` |
| `GET /v1/advanced_payments/{id}/refunds` | `403` PolicyAgent | `dict[str, Any]` |
| `POST /v1/advanced_payments/{id}/refunds` | `403` PolicyAgent | `dict[str, Any]` |
| `POST …/disbursements/{id}/refunds` | `403` PolicyAgent | `dict[str, Any]` |
| `POST /v1/advanced_payments/{id}/disburses` | `403` PolicyAgent | `dict[str, Any]` |
| `GET /v1/chargebacks/search` | `403` PolicyAgent | `dict[str, Any]` |

O PolicyAgent barra o token `TEST-` de aplicação nessas APIs. Com o mesmo
token, `GET /v1/payments/search` e `GET /v1/payment_methods` respondem `200`.
Advanced payments exige aplicação marketplace com vendedor vinculado por OAuth,
que esta conta não tem. Chargeback não se gera no sandbox, então mesmo com
acesso a busca viria vazia. Fica para uma credencial de usuário de teste
vendedor (`APP_USR-…` de conta com tag `test_user`).

O `AuthenticatedUser` declara só campo que veio **não nulo** na resposta, com o
tipo JSON observado, e nenhum como obrigatório, porque uma observação não diz o
que o provedor sempre manda. Os blocos de reputação, `status`, `credit`,
`context` e os campos que vieram `null` ficam como campo extra
(`extra="allow"`), sem perda. A resposta redigida está em
`tests/integrations/payment/mercado_pago/fixtures/users_me.json`: toda chave e
todo tipo JSON mantidos, valores pessoais trocados por fictícios estáveis.
`test_sandbox_observations.py` valida a fixture contra o modelo gerado e fixa
que todo campo declarado aparece não nulo nela.

### 8.4 As 35 "sondadas vivas" da seção 7, reavaliadas

Os controles da 8.1 mostram que `401`/`403` sem credencial não prova rota em
todo prefixo, e a seção 7 tirou as 35 justamente disso. Reavaliadas com `GET`
(só leitura) com e sem token, contra irmãos inventados:

- **Continuam sustentadas**: `/pos`, `/pos/{id}`, `/preapproval/export`,
  `GET /instore/qr/seller/collectors/{id}/pos/{id}/orders`,
  `/v2/wallet_connect/agreements/{id}`, `/v1/payouts/{id}/transactions`,
  `/v1/transaction-intents/{id}`, `/users/{id}/stores/search`,
  `/post-purchase/v1/claims/search`, `/post-purchase/v1/claims/{id}` (A `403`;
  irmão `/post-purchase/v1/claims/tempest-unrouted` dá `404`), os
  `release_report`/`settlement_report` de `config`, `list`, `search` e
  `task/{id}` (B `200`, ou erro do próprio serviço), e
  `/v1/account/settlement_report/{id}` (B `403` `forbidden` do serviço).
- **Evidência anterior não sustenta** (resposta igual à do irmão inventado):
  `GET /terminals/v1/actions/{id}` e `GET /terminals/v1/list` (A `401` =
  irmão), `GET /point/integration-api/refund/{id}` (`401` = irmão),
  `GET /users/{id}/pos` (`403` do proxy = irmão), os seis
  `GET /post-purchase/v1/claims/{id}/…` (`403` = irmão), e
  `GET /v1/account/release_report/{id}` (B `404` igual ao do irmão).
- **Respondem como não roteadas**: `GET /v1/account/release_report` → B `405`
  *"Method 'GET' is not supported"*; `GET /v1/account/settlement_report` → B
  `404` `Resource /account/settlement_report not found.`, igual ao irmão.

Nada disso mudou o `PROBED_OPERATIONS` nesta rodada. A issue #226 pedia as 47
não-`GET`, e rebaixar ou remover operações já sustentadas é decisão de
superfície. Registrado na issue #488.

### 8.5 Criar Pix na API de Payments — histórico (2026-10-09)

**Histórico.** Esta seção mediu a API de Payments, que o SDK deixou de modelar
logo depois (seção 9). Fica como registro do que aquela API exigia; para a API
de Orders, veja 9.5.

`POST /v1/payments` com `payment_method_id: pix`, `transaction_amount: 19.9`
e `X-Idempotency-Key` nova por requisição, com o token `TEST-` da aplicação
(`curl` e `MercadoPagoPixProvider.create_pix_charge`):

| `payer` enviado | Resposta |
| --- | --- |
| ausente | `500` `fill and validate error list: payer_cannot_be_nil` |
| `{"first_name": "Test"}` | `500` `payer_cannot_be_nil` |
| nickname de usuário de teste no lugar do e-mail | `400` `payer.email must be a valid email` |
| `{"email": "buyer@example.com"}` | `500` `fill and validate error list: not_found` |
| e-mail em formato Gmail | `500` `not_found` |
| `test_user_0000001@testuser.com` (inventado) | `500` `not_found` |

Na mesma API, com cartão de teste e o token `TEST-`: sem e-mail de pagador,
`400 Params Error` (código 1); `buyer@example.com`, `400 excludes_by_rule`
(10113); e-mail inventado no formato `@testuser.com`, `403 Payer email
forbidden` (4390). O `not_found` do Pix com e-mail válido não foi isolado.
O ciclo completo rodou depois, na API de Orders, com uma vendedora de teste
(seção 9).

## 9. API de Orders, com vendedora de teste (2026-10-09)

### 9.1 Qual credencial cobra

| Credencial | Pagador | Pix | Cartão |
| --- | --- | --- | --- |
| `TEST-` da aplicação da conta real | e-mail de comprador de teste | `500 not_found` | `403 Payer email forbidden` (4390) |
| `TEST-` da aplicação da conta real | `buyer@example.com` | `500 not_found` | `400 excludes_by_rule` (10113) |
| `APP_USR-` da vendedora de teste, aplicação Checkout Pro | qualquer | `401 Unauthorized use of live credentials` (7) | igual |
| `APP_USR-` da vendedora de teste, aplicação Checkout Transparente / API de Orders | e-mail da compradora de teste | `201` | `201` |

A conta vendedora foi conferida em `GET /users/me` antes de cada rodada:
`tags` com `test_user`, `site_id: MLB`. Com o token Checkout Pro,
`POST /checkout/preferences` respondeu `201`: o token vale, só não cobra
direto. No painel da conta de teste, "Credenciais de teste" responde *"Não é
possível utilizar credenciais de teste em um ambiente de teste"*, e não há
opção de ativar credenciais de produção (relatado ao criar a aplicação). Ao
escolher o tipo de API, o painel mostra para a API de Payments o aviso
*"Esta API será descontinuada em breve"*.

### 9.2 Pix por Orders

`POST /v1/orders` com `type: online`, `processing_mode: automatic`,
`total_amount: "19.90"` (string), `payer.email` e um pagamento
`{amount: "19.90", expiration_time: "PT30M", payment_method: {id: pix, type: bank_transfer}}`,
com `X-Idempotency-Key`:

| Etapa | Resposta |
| --- | --- |
| criar | `201`, order `action_required` / `waiting_transfer`; o pagamento traz `qr_code`, `qr_code_base64`, `ticket_url` em `payment_method` e `date_of_expiration` |
| `GET /v1/orders/{id}` | `200`, igual |
| `POST /v1/orders/{id}/cancel` | `200`, `canceled` / `canceled`; pagamento `canceled_transaction` |

O id da order é texto (`ORDTST01…`), e o do pagamento dentro dela também
(`PAY01…`).

### 9.3 Cartão por Orders

Cartão tokenizado em `POST /v1/card_tokens` com o mesmo access token (o
token sai `live_mode: true`), Visa `4235 6477 2802 5682`, `11/2030`, CVV
`123`:

| Etapa | Resposta |
| --- | --- |
| cobrar, titular `APRO` | `201`, `processed` / `accredited`, `capture_mode: automatic_async` |
| cobrar, titular `OTHE` | **`402`**, `errors[0].details = ["PAY…: rejected_by_issuer"]`, a order inteira em `data` com `status: failed` |
| `capture_mode: manual` | `201`, `action_required` / `waiting_capture` |
| `POST /v1/orders/{id}/capture` | `200`, `processed` / `accredited` |
| `POST /v1/orders/{id}/cancel` sobre autorização | `200`, `canceled` |
| `POST /v1/orders/{id}/refund` com `{"transactions":[{"id":"PAY…","amount":"30.00"}]}` | `201`, `processed` / `partially_refunded` |
| `POST /v1/orders/{id}/refund` sem corpo | `201`, `refunded` |
| `GET` depois | `refunded`; pagamento com `refunded_amount: "100.00"` e duas entradas em `transactions.refunds` |

Mastercard `5031 4332 1540 6351` com o mesmo token e corpo: `422
unprocessable_content`, sem detalhe. Causa não isolada.

### 9.4 Ação logo depois de criar: "ainda não"

Medido em 2026-10-09, 10 tentativas de cada:

| Ação imediata | Primeira resposta | Depois |
| --- | --- | --- |
| cancelar order `capture_mode: manual` | `409 processor_communication_error` (*"Try again shortly."*) em 3/10 | as 3 deram `200 canceled` 2 s depois |
| reembolsar order aprovada (`automatic_async`) | `422 unprocessable_entity` em 7/10, `409 post_processing_operation_pending` em 1/10 | todas reembolsaram em até ~5 s, repetindo com a mesma `X-Idempotency-Key` |

A order aprovada já aparece `processed` enquanto a captura assíncrona termina.
O adapter repete essas respostas, e só essas (`RETRYABLE_ACTION_ERRORS`,
`REFUND_RETRYABLE_ERRORS`), com a mesma chave, após 1, 2 e 4 s. Repetir com a
mesma chave não "envenenou" a chave: as repetições acima reembolsaram.
O mesmo levantamento achou o `GET /v1/orders/<id inexistente>` respondendo
`404`, que é o que a simulação de notificação do painel produz na releitura.

### 9.5 Pagador e credencial na API de Orders (2026-10-10)

Com o token de **produção da vendedora de teste** (aplicação Checkout
Transparente / API de Orders), Pix por `POST /v1/orders`:

| `payer` enviado | Resposta |
| --- | --- |
| ausente | `400 required_properties` — `'$.payer' - minimum 1 properties allowed, but found 0 properties` |
| `{}` | `400 minimum_properties`, mesmo detalhe |
| `{"first_name": "Test"}` | `400 required_properties` — `'$.payer.email' or '$.payer.customer_id' or '$.payer.id'` |
| `{"email": "TESTUSER0000000001"}` | `400 property_value` — `'$.payer.email' - does not match pattern` |
| `{"email": "buyer@example.com"}` | `201`, `action_required` |
| `{"email": "test_user_0000001@testuser.com"}` (inventado) | `201`, `action_required` |

Cartão (Visa de teste, `APRO`) com `buyer@example.com` e com o
`test_user` inventado: `201`, `processed` / `accredited` nos dois. **Na API
de Orders, com a credencial certa, o pagador não precisa ser um comprador de
teste.**

Com o token `TEST-` da aplicação da conta real, a mesma order — sem pagador,
com `buyer@example.com` ou com o e-mail da compradora de teste, Pix e
cartão — responde `403 At least one policy returned UNAUTHORIZED.` nas seis
combinações.

### 9.6 Pix pago, Pix vencido, estorno de Pix, idempotência, parcelas e Mastercard (2026-10-10)

Mesma credencial da 9.5. Script em `~/.cache/tempest-fastapi-sdk/mp-sandbox/tools/followups_probe*.py`
(fora do repositório); respostas redigidas em
`tests/integrations/payment/adapters/fixtures/mercado_pago_orders/pix_*.json`.

**Pix pago (#500).** Pagador `{"email": "buyer@example.com", "first_name": "APRO"}`:
a order nasce `action_required` / `waiting_transfer` e, relida a cada 2 s,
aparece `processed` / `accredited` em 2 s numa rodada e em 4 s noutra (com
`last_name` `APRO` também), com `e2e_id` `PIXPRUEBA…`. Com `first_name`
`OTHE`, ficou `action_required` / `waiting_transfer` nas 8 leituras (16 s).

**Pix vencido (#500).** `expiration_time` aceito como `PT60S`, `PT5M`,
`PT10M` e `PT30M` — `date_of_expiration` = criação + o prazo, nos quatro. A
order `PT60S`, relida depois do prazo: `canceled` / `expired`; o pagamento,
`expired` / `expired`. O adapter passou a ler esse par como
`PaymentStatus.EXPIRED` (antes: `CANCELLED`).

**Estorno de Pix (#505).** Num Pix pago: `POST /v1/orders/{id}/refund` com
`{"transactions": [{"id": "<PAY…>", "amount": "5.00"}]}` → `201`, order
`processed` / `partially_refunded`; sem corpo, o resto → `201`, `refunded` /
`refunded`. A resposta traz só id, estado e `transactions.refunds`, como no
cartão. Num Pix **não** pago: `409 cannot_refund_order` — *"The order must be
in 'processed' status to be refunded."*

**Idempotência (#502).** Mesma `X-Idempotency-Key`, mesmo corpo, duas vezes:
`201` e `201`, **o mesmo id de order**. Mesma chave com outro corpo (outro
valor e outra referência): `409 idempotency_key_already_used`.

**Parcelas (#502).** `GET /v1/payment_methods/installments?bin=423564&amount=1000&payment_method_id=visa`
ofereceu só `[1]`. Visa `APRO` com `installments` 3 e 6 em R$ 1.000,00:
`400 invalid_transaction_amount` nos dois. Parcelamento não é mensurável com
esta conta de teste.

**Mastercard (#501).** `GET /v1/payment_methods/search?bins=503143&site_id=MLB`
devolve `results: []` — o BIN do `5031 4332 1540 6351` não é reconhecido no
MLB —, e a order com `master` responde `422 unprocessable_content`, com e
sem `first_name` `APRO`. O `5474 9254 3267 0366` (BIN `547492`) volta
`[("master", "credit_card")]` na mesma busca, e a order com ele, titular
`APRO`, responde `201`, `processed` / `accredited`. `debmaster` é recusado no
schema (`payment_method.id` aceita `amex`, `elo`, `diners`, `hipercard`,
`master`, `visa`).

### 9.7 As seis operações ainda `dict`, com a vendedora de teste (2026-10-10)

Mesma credencial da 9.5. Script em `~/.cache/tempest-fastapi-sdk/mp-sandbox/tools/dict_ops_probe.py`
e `chargebacks_probe.py` (fora do repositório).

| Requisição | Resposta |
| --- | --- |
| `GET /v1/advanced_payments/search` | `403 PA_UNAUTHORIZED_RESULT_FROM_POLICIES` |
| `GET /v1/advanced_payments/123456789` e `…/refunds` | `403`, idem |
| `GET /v1/advanced_payments/123456789/zz-not-a-route` (inventado) | `403`, idem |
| `POST /v1/advanced_payments` com `{}` | `403`, idem |
| `GET /v1/chargebacks/search` | `400 Wrong parameters in Search Cases` |
| `GET /v1/chargebacks/search?limit=10&offset=0` | `400`, idem |
| `GET /v1/chargebacks/search?date_created_from=2026-10-01T00:00:00.000-03:00` | `400`, idem |
| `GET /v1/chargebacks/search?payment_id=123456789` | `200 {"paging": {"offset": 0, "limit": 25, "total": 0}, "results": []}` |
| `GET /v1/chargebacks/search?payment_id=123456789&limit=10&offset=0` | `200`, mesmo corpo — `paging.limit` continua 25 |
| `GET /v1/chargebacks/zz-not-a-route` (inventado) | `404 Case for zz-not-a-route doesn't exist` |

Advanced payments: o path inventado responde igual aos reais, então a
política barra antes do roteamento — nada a observar com esta credencial
(provavelmente exige aplicação marketplace; não medido). As cinco seguem
`dict[str, Any]`.

`search_chargebacks`: a rota existe (o inventado dá `404` de outro formato),
exige `payment_id`, e o envelope de `200` foi observado. O overlay passou a
declarar `payment_id` obrigatório, tirou `limit` e `offset` (o `limit` foi
ignorado) e tipa a resposta como `ChargebackSearchResponse` /
`ChargebackSearchPaging`; os itens de `results` não foram vistos.

**`transaction-intents` (#499).** `POST /v1/transaction-intents/process` com
`{}` e com `{"receiver": {}}`, `GET /v1/transaction-intents/123456789` e um
path inventado sob o prefixo: `403` do PolicyAgent nos quatro. O shape do
`receiver` não pôde ser descoberto com esta credencial.

### 9.8 As 34 `GET` que só a sondagem sem credencial sustentava (2026-10-10, #488)

Mesma credencial da 9.5, só `GET`, id `999999999999` (sob `/users/{id}`, o
id da própria conta, aqui `<uid>`). Cada path comparado com um irmão
inventado: o último segmento trocado por `tempest-unrouted`. Scripts em
`~/.cache/tempest-fastapi-sdk/mp-sandbox/tools/probe488*.py` (fora do
repositório). Onde o último segmento é parâmetro, o irmão cai no mesmo
template; ali só conta uma resposta que nomeia o domínio ou o parâmetro.

**Distinguidas (20) → `SELLER_ROUTED_GETS`:**

| `GET` | Real | Irmão inventado |
| --- | --- | --- |
| `/terminals/v1/actions/{id}` | `404 order_not_found` | `404 not_found` genérico |
| `/terminals/v1/list` | `200 {"data": {"terminals": []}, "paging": …}` | `404` |
| `/point/integration-api/refund/{id}` | `400 Intent ID is not valid` | `404 Action not supported` |
| `/pos` | `200` paging | `404` |
| `/pos/{id}` | `404 pos_does_not_exist` | `404` genérico da borda |
| `/post-purchase/v1/claims/search` | `403` PolicyAgent | `404` |
| `/post-purchase/v1/claims/{id}` | `403` PolicyAgent | `404` |
| `/preapproval/export` | `200`, corpo ZIP | `404` |
| `/users/<uid>/stores/search` | `200` paging | `403` HTML do proxy |
| `/v1/account/release_report/config` | `404 config_not_found_for_user` | `404 Resource … not found.` |
| `/v1/account/release_report/list` | `200 []` | `404` |
| `/v1/account/release_report/search` | `200` paging | `404` |
| `/v1/account/release_report/task/{id}` | `403 internal_error` | `400 Failed to convert 'taskId'` (mesmo template, nomeia o parâmetro) |
| `/v1/account/settlement_report/config` | `400`, corpo `config_not_found_for_user` | `404` vazio |
| `/v1/account/settlement_report/list` | `200 []` | `404` vazio |
| `/v1/account/settlement_report/search` | `200` paging | `404` vazio |
| `/v1/account/settlement_report/task/{id}` | `400 internal_error` | `400` com campos vazios (mesmo template) |
| `/v1/customers/{id}/addresses` | `401 access denied` | `404 Resource … not found.` |
| `/v1/payment_methods/installments` | `400 the payment_method_id or bin are required` | `404` |
| `/v2/wallet_connect/agreements/{id}` | `403` PolicyAgent | `404` |

**Não roteadas (2) → `UNROUTED_OPERATIONS`, marcadas e mantidas:**
`GET /v1/account/release_report` → `405 Method 'GET' is not supported`;
`GET /v1/account/settlement_report` → `404 Resource /account/settlement_report not found.`
— enquanto `…/list` e `…/search` de cada uma respondem `200`.

**Indistinguíveis (12) → agora `**Unverified.**`:** `/users/{id}/pos`
(HTML `403` do proxy nos dois, também com `<uid>`); os seis
`/post-purchase/v1/claims/{id}/…` (PolicyAgent nos dois);
`/v1/account/release_report/{id}` e `/v1/account/settlement_report/{id}`
("não encontrado" genérico do serviço, que o irmão também recebe);
`/v1/customers/{id}/addresses/{id}` (`401` nos dois);
`/v1/payouts/{id}/transactions` e `/v1/transaction-intents/{id}` (PolicyAgent
nos dois).

O `PROBED_OPERATIONS` ficou só com entradas que o SDK oficial também chama.

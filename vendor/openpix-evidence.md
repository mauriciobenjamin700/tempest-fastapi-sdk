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

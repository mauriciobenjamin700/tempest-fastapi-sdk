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

**Corpo e resposta são `dict[str, Any]`.** Path e verbo são medidos; a forma
não, e este repositório não tem credencial do Mercado Pago para observá-la.
Declarar shape que ninguém mediu é o defeito que a v0.259.0 shippou na
OpenPix — com a diferença de que lá nem o endpoint tinha fonte.

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

Isso não torna as 47 erradas — torna visível que elas são de outra classe. A
diferença entre operação que o provedor chama e operação que só um documento
de origem desconhecida declara não devia ser invisível para quem lê o cliente
gerado.

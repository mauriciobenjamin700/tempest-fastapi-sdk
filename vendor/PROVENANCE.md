# PROVENANCE.md — de onde vem cada documento vendorizado

Este diretório guarda documento de **terceiro**, entrada de build que nunca
entra no wheel. Cada arquivo aqui é a fonte a partir da qual código
publicado é gerado, então "de onde ele veio" é parte do contrato, não
curiosidade.

**Regra:** todo refresh de um arquivo de `vendor/` atualiza a linha
correspondente aqui **no mesmo commit** — URL, data, sha256, contagens.
O hash é o que o guard compara; sem ele, "byte a byte o que o provedor
publica" é uma afirmação que ninguém pode verificar.

## Por que o hash existe

O overlay da OpenPix (`scripts/openpix_overlay.py`) foi desenhado sobre uma
promessa: o documento vendorizado continua byte a byte o que a Woovi
publica, e tudo que a gente sabe que ele erra vive fora dele. Isso torna o
refresh um diff só do que **eles** mudaram.

Até 2026-08-28 nada no repositório registrava nem verificava essa
propriedade. `test_specification_is_vendored` checava existência e
`st_size > 100_000`. Editar o YAML vendorizado à mão e regenerar passava
verde, e ninguém conseguia dizer de qual publicação o arquivo tinha vindo.

Medido em 2026-08-28: o documento vendorizado da OpenPix estava atrás do
publicado por uma minor inteira de OpenAPI (3.0.3 contra 3.1.0), 24
operações e 45 campos que a Woovi já tinha retipado. Nenhum teste podia
detectar isso. A v0.260.0 refrescou o documento e fechou o buraco:
`test_specification_is_the_bytes_the_provider_served` compara o sha256 com
`SPEC_SHA256`, então editar o vendorizado à mão passou a falhar. Evidência
completa em [`openpix-evidence.md`](openpix-evidence.md).

## Inventário

| Arquivo | Origem | Obtido em | sha256 (12) | Bytes |
| --- | --- | --- | --- | --- |
| `openpix-openapi.json` | `https://api.woovi.com/api/openapi.json` | 2026-08-28 | `9b14fb336276` | 1.318.389 |
| `mercadopago-openapi.yaml` | `https://raw.githubusercontent.com/mercadopago/openapi/main/spec3.yaml` | 2026-08-30 | `893ec14bfd91` | 260.935 |
| `stripe-api-facts.yaml` | derivado de `https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.yaml` | ver `api_version` no próprio arquivo | `171c35ef4e09` | 7.965 |

## `openpix-openapi.json`

| Campo | Valor |
| --- | --- |
| `openapi` | `3.1.0` |
| `info.title` | `Woovi` |
| `info.version` | `1.0.0` (a Woovi nunca bumpa este campo — não serve para datar) |
| Operações | 125 |
| Schemas em `components` | 153 |
| sha256 | `9b14fb33627f68424fd220298019703b897233c5b301c44b81cd4f0a3f83eb5e` |
| Obtido em | 2026-08-28 |
| Gera | `tempest_fastapi_sdk/integrations/payment/openpix/{schemas,client}.py` |
| Refresh | `make openpix-fetch` (rede, regrava e regenera) · `make openpix-regen` (offline) · `make openpix-diff` (relatório contra o publicado) |

Guardado **como o provedor serve**: JSON, sem reformatação. Reserializar
para YAML faria o digest descrever o nosso formatador em vez dos bytes
deles, e a afirmação "byte a byte" voltaria a ser inverificável. O arquivo
se chamava `openpix-openapi.yaml` até a v0.260.0.

**Origem.** A URL acima foi localizada em 2026-08-28 no botão "Download
OpenAPI specification" da página Redoc que a Woovi publica em
<https://developers.woovi.com/en/api-redoc>. As duas páginas de
documentação que o usuário enxerga —
<https://developers.woovi.com/api> e o API Explorer em
<https://developers.woovi.com/en/docs/apis/api-explorer> — renderizam esse
mesmo documento. Não são fonte comparável por scraping: são SPA, e o texto
que sai delas por leitura de HTML diverge dos caminhos reais (a leitura
automática devolveu `/api/v1/pixKey`, `/api/v1/subscription` e
`/api/v1/pixQrCode`, que não existem no documento; os reais são
`/api/v1/pix-keys`, `/api/v1/subscriptions` e `/api/v1/qrcode-static`).
**Compare sempre contra o `openapi.json`, nunca contra a página.**

A resposta não traz `Last-Modified` nem `ETag`, então a data de obtenção e
o digest são toda a procedência que existe — por isso os dois ficam
registrados aqui e em `SPEC_SHA256`.

A origem recusa o `User-Agent` default do urllib: medido 2026-08-28,
`Python-urllib/3.11` recebe `HTTP 403` e a mesma URL sob curl recebe `200`.
`fetch_spec` manda um `User-Agent` próprio por causa disso.

O arquivo anterior (`openpix-openapi.yaml`, sha256 `84472c196eaf…`,
846.745 bytes, `openapi: 3.0.3`, `info.title: OpenPix`, 105 operações, 96
schemas) nunca teve procedência registrada. `mtime` no checkout era
`2026-08-09`, pista e não procedência.

## `mercadopago-openapi.yaml`

| Campo | Valor |
| --- | --- |
| `openapi` | `3.1.0` |
| `info.title` | `MercadoPago API` |
| `info.version` | `1.0.0` |
| `servers` | `https://api.mercadopago.com` (Production) |
| Paths | 109 |
| Operações (path × verbo) | 143 |
| Schemas em `components` | 99 |
| sha256 | `893ec14bfd912dd377626fa0b4a4e9896afc2fbfb8f67fd6293502d39d0f6d46` |
| Guard do digest | `tests/integrations/payment/mercado_pago/test_generated_drift.py::TestVendoredSpec::test_the_vendored_document_is_the_one_that_was_justified` |
| Gera | `tempest_fastapi_sdk/integrations/payment/mercado_pago/{schemas,client}.py` |
| Refresh | `make mercadopago-fetch` (rede, rebaixa do provedor) · `make mercadopago-regen` (offline) · `make mercadopago-diff` (rede, valida contra o SDK oficial) |
| Overlay | `scripts/mercadopago_overlay.py` — 2 correções, 7 adições, 3 remoções |
| Autoridade | `mercadopago` 3.5.0 (PyPI), fixado em `OFFICIAL_SDK_CALLS` |

**O upstream existe, e este arquivo é ele.** Medido em 2026-08-30:

```text
vendorizado        260935 bytes  sha256 893ec14bfd912dd3…
commit 73bc0e49    260935 bytes  sha256 893ec14bfd912dd3…
main (2026-08-30)  260935 bytes  sha256 893ec14bfd912dd3…
```

`github.com/mercadopago/openapi` (Apache-2.0, público, criado 2026-05-20) é o
repositório de especificação do próprio provedor. O arquivo vendorizado aqui é
o `spec3.yaml` da raiz, byte a byte, no commit `73bc0e49` — que continua sendo
o `main`. `make mercadopago-fetch` rebaixa.

**Esta seção afirmou o contrário até a v0.276.0.** Ela dizia *"não existe
documento upstream"*, e o `scripts/regen_mercado_pago.py` dizia *"no upstream
to diff against"* — trinta linhas abaixo do docstring do módulo, que **nomeava
o repositório corretamente**. A afirmação errada saiu de uma sonda que
adivinhou o nome do arquivo:

```text
404  raw.githubusercontent.com/mercadopago/openapi/main/openapi.yaml
200  raw.githubusercontent.com/mercadopago/openapi/main/spec3.yaml
```

Os `404` de `api.mercadopago.com/openapi{,.json}` são reais e continuam reais —
o provedor não serve a spec pela API. A conclusão tirada deles é que estava
errada. Mesma forma de erro que a issue #230 já tinha corrigido em si mesma:
evidência sobre uma coisa, afirmação sobre outra.

O repositório publica outras variantes. Medido no mesmo commit:

| Arquivo | Bytes | Paths | Operações |
| --- | --- | --- | --- |
| `spec3.yaml` (vendorizado) | 260.935 | 109 | 143 |
| `spec3.reference.yaml` | 423.740 | 108 | 142 |
| `spec3.sdk.yaml` | 243.874 | 108 | 142 |

A raiz é a vendorizada por ser o superconjunto — a operação a mais é
`PUT /checkout/preferences/{id}/expire` — e por ser a que o README deles
descreve como *"fully self-contained"*. O `spec3.sdk.yaml` anota
`x-mp-sdk-coverage` por operação, dizendo quais SDKs oficiais cobrem cada uma;
cruzar isso com o nosso `OFFICIAL_SDK_CALLS` é trabalho separado.

Rebaixar responde "ainda está certo?", mas **não** responde "toda operação
existe?": o documento do provedor omite sete operações que o SDK oficial dele
chama. Por isso a segunda opinião continua sendo o **SDK oficial** —
`mercadopago` no PyPI, que soletra a URL de toda operação que chama.

```bash
make mercadopago-diff
```

`scripts/mercadopago_diff.py` baixa o sdist, extrai as URLs com `ast` e relata
a diferença nas duas direções. Achou duas rotas que a nossa spec soletrava
errado e que a API não roteia — corrigidas em
`scripts/mercadopago_overlay.py`, no mesmo padrão da OpenPix. Evidência,
sondas e o que ficou **sem** correção: [`mercadopago-evidence.md`](mercadopago-evidence.md).

O digest acima é o dos bytes que o provedor serve, então mudança nele é
mudança **deles** — e um `make mercadopago-fetch` mostra qual. Mudança sem
refetch é edição nossa, e precisa de justificativa no overlay ou no evidence.

**Desde a v0.271.0 isso é forçado por teste.** Até então a frase acima era só
prosa: editar o YAML à mão e regenerar passava verde, e o diff parecia um
refresh de codegen de rotina. Medido — acrescentar uma linha de comentário ao
documento deixava o teste de drift dos arquivos gerados **passar**, porque a
regeneração usa o arquivo editado e produz saída consistente com ele:

```text
# com um comentário acrescentado ao vendorizado, antes do guard
14 passed

# com o guard
FAILED ...::test_the_vendored_document_is_the_one_that_was_justified
1 failed, 13 passed
```

O que o guard **não** faz, e é a diferença que importa: ele não diz de onde o
arquivo veio. Para a OpenPix o digest é de bytes que o provedor serviu, então
sustenta uma afirmação sobre **eles**. Aqui sustenta uma afirmação sobre
**nós** — que o documento não mudou desde que alguém justificou o conteúdo.
As 82 operações que o SDK oficial não toca continuam sem segunda fonte, e três
delas responderam `404` quando sondadas. Achar a origem continua aberto na
[issue #228](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/228);
um digest não substitui isso.

## `stripe-api-facts.yaml`

Não é uma especificação: é o **extrato** que `scripts/regen_stripe.py`
produz da spec da Stripe — versão da API fixada, base URL e a lista de
tipos de evento. Guardar o `spec3.yaml` inteiro custaria dezenas de MB para
gerar um enum.

| Campo | Valor |
| --- | --- |
| Origem | `https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.yaml` |
| `api_version` no arquivo | `2026-07-29.dahlia` |
| sha256 | `171c35ef4e09e7ad88694aeb1bc298db02f3dc4678c2391124a1f53e1a856200` |
| Gera | enum de eventos da Stripe |
| Refresh | `make stripe-fetch` (rede) e depois `make stripe-regen` |

É o único dos três que registra a origem em código (`SPEC_URL`,
`scripts/regen_stripe.py:49`) e o único com alvo de refresh que busca de
fato. É o padrão que os outros dois devem seguir.

## Pendências

- [x] `SPEC_URL` + alvo `make openpix-fetch` em `scripts/regen_openpix.py`,
      espelhando o que a Stripe já faz. (v0.260.0)
- [x] Guard que compara o sha256 do arquivo com o registrado, para edição à
      mão do vendorizado falhar em vez de passar calada. (v0.260.0)
- [x] Refresh do documento da OpenPix. (v0.260.0)
- [x] Investigar a origem do `mercadopago-openapi.yaml`. **Não existe**: o
      provedor não publica OpenAPI. A validação passou a ser contra o SDK
      oficial, por `make mercadopago-diff`. (v0.260.0)
- [x] Sete operações que o SDK oficial chama e não modelávamos — adicionadas
      com `dict[str, Any]` nos dois lados, porque path e verbo são medidos e a
      forma não. (v0.260.0)
- [x] Três rotas `GET` que respondem `404` — removidas. (v0.260.0)
- [ ] Credencial de sandbox do Mercado Pago, para tipar corpo e resposta das
      sete operações que hoje são `dict[str, Any]`, e para verificar as 47
      operações não-`GET` que só o nosso documento carrega.

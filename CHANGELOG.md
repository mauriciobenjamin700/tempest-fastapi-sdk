# Changelog

All notable changes to **tempest-fastapi-sdk** are listed below.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.262.0] — 2026-08-28

### Added

- **Operação que nenhuma fonte confirma passa a dizer isso na própria
  docstring.**
  ([#227](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/227))
  O Mercado Pago não publica OpenAPI, então o documento vendorizado não tem
  upstream e nem origem registrada. A v0.261.0 fez o SDK oficial do provedor
  virar a autoridade em conflito — mas ele cobre 65 das 147 operações, e as
  outras 82 ficavam indistinguíveis dele no cliente gerado.

  Agora são três baldes, e cada um tem lastro diferente:

  | Balde | Qtd | O que responde por ela |
  | --- | --- | --- |
  | O SDK oficial chama | 65 | O provedor |
  | Sondada viva | 35 | Resposta `401`/`403`/`400` sem credencial, em 2026-08-28 |
  | Nada responde | 47 | Só o documento vendorizado |

  As 47 carregam `**Unverified.**` na docstring gerada. Não quer dizer que
  estão erradas — quer dizer que ninguém verificou, e a diferença entre uma
  operação que o provedor chama e uma que só um documento de origem
  desconhecida declara não devia ser invisível para quem lê o cliente.

  São **todas** `POST`/`PUT`/`PATCH`/`DELETE`, e isso não é coincidência: a
  sonda que separa rota viva de rota morta é por método **e** path. Medido,
  `GET /v1/customers` responde `404` enquanto `POST /v1/customers` é onde o
  SDK oficial cria cliente — então um `404` em `GET` não fala pelo `DELETE` no
  mesmo path. Mandar `POST`/`PUT`/`DELETE` para uma API de pagamento em
  produção só para descobrir se rotea não é forma aceitável de responder a
  pergunta.

- **`PROBED_OPERATIONS` e `PROBE_DATE` em `scripts/mercadopago_overlay.py`.**
  O inventário de sondagem versionado: o que foi sondado, quando, e com que
  código cada rota respondeu. 66 rotas `GET`, zero `404` — as três que
  respondiam foram removidas na v0.261.0.

### Changed

- **`make mercadopago-diff` relata os três baldes** em vez de uma lista única
  de "só nós carregamos", que fazia 82 operações de lastro muito diferente
  parecerem uniformes.

## [0.261.0] — 2026-08-28

### Added

- **`heartbeat` — liveness para qualquer endpoint WebSocket.**
  ([#225](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/225))
  A mecânica existia desde a v0.197.0, mas só dentro de
  `make_websocket_router`, que impõe bearer no handshake **e** registro num
  `WebSocketHub` indexado por `user_id: UUID`. Um endpoint fora desse molde —
  signaling de WebRTC com salas anônimas, endereçado por `peer_id`, onde o
  código da sala é o único segredo — reimplementava o heartbeat inteiro.

  Agora é um context manager async que não pede nada além de um socket já
  aceito:

  ```python
  from fastapi import WebSocket

  from tempest_fastapi_sdk import WebSocketSettings
  from tempest_fastapi_sdk.websockets import heartbeat


  async def relay(ws: WebSocket, settings: WebSocketSettings) -> None:
      """Sala anônima, sem auth e sem hub."""
      await ws.accept()
      async with heartbeat(ws, settings=settings) as live:
          await ws.send_json(
              {"type": "hello", "heartbeat_seconds": live.interval_seconds}
          )
  ```

  O problema que resolve é medido: sem heartbeat, um socket cujo peer sumiu sem
  close frame — celular que perdeu sinal ou foi encerrado à força — fica aberto
  até o timeout de TCP do kernel, que é de minutos, e nada levanta, porque
  mandar para socket half-open funciona.

  `live.touch()` marca vida por evidência que o socket não enxerga. O cap de
  tamanho é opcional e separado (`max_message_bytes=`), porque é outra política.
  Na saída do bloco o `ws.receive` é restaurado, então um bloco aninhado não
  deixa guard para trás.

- **`CompactPaginationSchema` e `CompactPaginationFilterSchema`.**
  ([#209](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/209))
  Publicam o tamanho de página como `size` no fio, mantendo `page_size` como
  nome Python. Para um serviço que já publica
  `{"total", "items", "page", "size", "pages"}`, adotar o envelope do SDK
  deixa de ser um break em todo endpoint paginado de uma vez — e um app já em
  loja não atualiza em lockstep com o backend.

  ```python
  from tempest_fastapi_sdk import CompactPaginationSchema

  page = CompactPaginationSchema[int](
      items=[1], total=1, page=1, page_size=20, pages=1
  )
  page.model_dump(by_alias=True)["size"]   # 20
  page.page_size                           # 20 — o nome Python não muda
  ```

  `BaseRepository.paginate` continua devolvendo `page_size` seja qual for o
  envelope: a renomeação vive no schema e em nenhum outro lugar.

### Changed

- **BREAKING — o primeiro frame do `make_websocket_router` passa a ser um
  `hello`.** `{"type": "hello", "data": {"heartbeat_seconds": N}, "request_id": null}`,
  antes de qualquer coisa que o handler mande.

  O motivo é do lado do cliente: o socket do browser só reporta conexão que
  fecha **limpo**, e um link que morre em voo deixa o `readyState` em `OPEN`
  com nada chegando nunca mais. Silêncio é o único sintoma, então o cliente
  precisa do próprio watchdog — e se ele hard-codar o intervalo, retunar
  `WS_HEARTBEAT_SECONDS` no servidor faz todo cliente confundir intervalo
  normal com queda.

  Cliente que despacha por `type` e ignora o que não conhece não sente. Cliente
  que lê o **primeiro** frame por posição, sim: cinco testes desta suíte
  quebraram exatamente assim. Passo a passo em `docs/migration.md`.

- **Qualquer frame de entrada conta como prova de vida, não só `pong`.** Um
  peer no meio de uma negociação está demonstravelmente presente, e exigir a
  resposta específica desconectava cliente ocupado que só não tinha chegado
  nela ainda. O `pong` continua sendo consumido antes do handler.

- **`BasePaginationSchema` e `BasePaginationFilterSchema` ligam
  `populate_by_name`.** Sem isso, sobrescrever um campo para renomeá-lo no fio
  impedia construir o model pelo nome Python. O nome do fio se escreve duas
  vezes — `validation_alias` para ler, `serialization_alias` para escrever;
  `Field(alias=...)` sozinho passa no mypy e quebra em pyright.

## [0.260.0] — 2026-08-28

### Added

- **Procedência do documento vendorizado — `vendor/PROVENANCE.md` +
  `SPEC_SHA256`.** O overlay da OpenPix foi desenhado sobre uma promessa: o
  documento vendorizado continua byte a byte o que a Woovi publica, e tudo
  que a gente sabe que ele erra vive fora dele. Nada registrava nem
  verificava isso — `test_specification_is_vendored` checava existência e
  `st_size > 100_000`, então editar o YAML à mão e regenerar passava verde,
  e ninguém conseguia dizer de qual publicação o arquivo tinha vindo.

  Agora o digest está em `scripts/regen_openpix.py` e um teste o compara.
  `make openpix-fetch` rebaixa o documento e imprime o hash novo;
  `make openpix-diff` relata a distância entre o vendorizado e o publicado,
  sem gatear nada — a resposta a uma divergência é julgamento, não build
  vermelho.

- **`tests/openapi/test_parse.py::TestPathTemplateGaps`.** Dois guards no
  gerador, escritos sobre as duas formas que de fato shipparam. Path com
  sintaxe Express (`/dispute/:id/evidence`) passa a ser anotado — não é
  templating de OpenAPI, o dois-pontos ia literal na URL. E parâmetro
  declarado `in: path` cujo placeholder não existe no template passa a
  chegar ao arquivo gerado: a nota existia, mas `_build_parameters` rodava
  fora do `capture()` da operação, então morria no sumário.

- **`TestWhatTheOverlayLeavesAlone`.** O overlay tinha teste para tudo que
  corrige e nenhum para o que deixa passar — que é onde ele estava errado.
  O conjunto de campos que continuam `float` virou pin: `number` novo num
  refresh falha, de propósito.

### Changed

- **BREAKING — a spec da OpenPix foi refrescada, e os 125 métodos mudaram
  de nome.** O documento vendorizado estava duas versões atrás: `3.0.3`
  intitulado "OpenPix" contra o `3.1.0` "Woovi" que o provedor publica,
  105 operações contra 125.

  O documento novo traz `operationId` em **125 de 125** operações; o antigo
  tinha zero, e o gerador derivava nome do path. Então
  `post_api_v1_charge` virou `create_charge`, `get_api_v1_charge_by_id`
  virou `get_charge`, `get_api_v1_charge` virou `list_charges` — 103
  renomeações, com tabela completa em `docs/migration.md`.

  ```python
  from tempest_fastapi_sdk.integrations.payment.openpix import (
      ChargePayload,
      OpenPixClient,
  )

  # até a v0.259.0: await client.post_api_v1_charge(body=payload)
  # a partir da v0.260.0:
  async def cobrar(client: OpenPixClient, payload: ChargePayload) -> None:
      """Create one charge with the renamed method."""
      await client.create_charge(body=payload)
  ```

  Schemas de `components` mantiveram o nome (`Charge`, `ChargePayload`,
  `ChargeStatus`); o que mudou foi o nome das classes inline derivadas de
  operação — `GetApiV1ChargeResponsePageInfo` virou
  `ListChargesResponsePageInfo`.

  A superfície foi de 373 para **686 schemas** e de 106 para **125
  operações**: entram `anticipation` (7), `stablecoin` payout e wallets
  (7), `boleto-transaction` (2), `kyc-validation` (2), `files` e
  `webhook/public-keys`.

- **BREAKING — dois métodos que não podiam funcionar foram substituídos.**
  Não é regressão do refresh: eram defeitos que a Woovi publicou, corrigiu
  no documento, e que a gente carregava congelados.

  - `post_api_v1_dispute_id_evidence` montava
    `path = "/api/v1/dispute/:id/evidence"` — dois-pontos literal na URL — e
    a assinatura só recebia `body`, sem nenhuma forma de nomear a disputa.
    Agora é `upload_dispute_evidence(id, *, body)`.
  - `get_api_v1_account_register` tinha docstring "Get account register by
    CorrelationID" e **zero argumentos**. Agora é `get_account_register(id)`.

- **BREAKING — `OpenPixEnvironment.PRODUCTION` passa a ser
  `https://api.woovi.com`.** É o `servers[0]` do documento refrescado, e
  alinha o enum com o `DEFAULT_BASE_URL` gerado, que já vinha de lá. O host
  antigo continua no ar: medido 2026-08-28, `GET /api/v1/charge` devolve
  `401` em `api.openpix.com.br`, `api.woovi.com` e `api.woovi-sandbox.com`
  igualmente, então serviço fixado no nome antigo segue funcionando.

- **BREAKING — model de payload volta a recusar chave inesperada quando
  também é resposta.** A v0.259.0 marcava `extra="allow"` por
  alcançabilidade a partir da resposta, e classe que é **as duas coisas**
  caía na regra errada. Na OpenPix a âncora é `PreRegistrationPayloadObject`,
  body e `200` da mesma operação, onde `model_validate({"usre": {...}})` levava
  o typo do caller para o provedor no dump. Agora o fecho do body é subtraído
  do fecho da resposta: **4 classes voltam para `extra="ignore"` na OpenPix**
  (a âncora e as três que ela alcança) e **25 na Mercado Pago**.

- **`Transaction.webhookSent[].status` passa a ser `int`.** O documento o
  tipa `number` e o descreve como *"HTTP response status code of the
  webhook delivery attempt"*. Vira inteiro por `STATUS_CODE_PATTERN`, regra
  de descrição — `status` não entrou no allowlist de nome, porque afirmar
  que todo `status` de qualquer API é inteiro seria o palpite que o overlay
  existe para evitar.

- **BREAKING — o SDK oficial do Mercado Pago passa a ser a autoridade sobre a
  API.** O provedor **não publica OpenAPI** — medido em 2026-08-28,
  `api.mercadopago.com/openapi{,.json}` respondem `404` e a org no GitHub não
  tem repositório de spec —, então `vendor/mercadopago-openapi.yaml` não tem
  upstream e ninguém sabe como foi montado.

  A autoridade passou a ser `mercadopago` no PyPI (3.5.0), escrito pelo
  provedor, que soletra a URL de toda operação que chama. **A regra é
  autoridade em conflito, não teto de superfície:** onde os dois discordam o
  SDK vence; onde o SDK é silencioso o nosso documento fica, porque ele é
  wrapper fino e as 82 operações que só nós carregamos respondem `401`/`403`,
  não `404`.

  `make mercadopago-diff` baixa o sdist e relata as duas direções. A que
  importa — o que o SDK chama e não modelamos — está em **zero**, e
  `TestTheSdkIsTheAuthority` fixa isso offline contra `OFFICIAL_SDK_CALLS`.

  **Duas rotas corrigidas:**

  | Antes | Agora |
  | --- | --- |
  | `DELETE /v1/customers/{id}/delete` | `DELETE /v1/customers/{id}` |
  | `GET /authorized_payments` | `GET /authorized_payments/search` |

  `MercadoPagoClient.delete_customer` montava a primeira: um método que nunca
  poderia ter funcionado, a mesma forma do `:id` da OpenPix.

  **Sete operações adicionadas** — `get_authenticated_user`,
  `search_advanced_payments`, `search_chargebacks`,
  `list_disbursement_refunds`, `create_disbursement_refunds`,
  `create_disbursement_refund`, `update_advanced_payment_release_date`. Corpo
  e resposta são `dict[str, Any]`: path e verbo são medidos, a forma não, e
  este repositório não tem credencial para observá-la.

  **Três operações removidas** — `GET /instore/integrator`, `GET /stores/{id}`
  e `GET /post-purchase/v1/claims/reasons/{id}`, que respondem `404` onde a
  vizinhança responde `401`/`403` e não têm contraparte no SDK. O
  `PATCH /instore/integrator` fica: `404` é por método, e nenhuma sonda falou
  pelo `PATCH`.

  A superfície vai de 324/143 para **323 schemas / 147 operações**.

### Fixed

- **`to_cents` e `reais_to_cents` recusam com `ValueError`, sempre.** A
  docstring prometia só `ValueError`, e quatro entradas escapavam: `"abc"` e
  `""` levantavam `decimal.InvalidOperation`, `None` levantava `TypeError`,
  `float("inf")` levantava `OverflowError`. É exatamente o input para o qual
  a migração recomenda a função — payload cru, corpo de webhook — e quem
  escrevia `except ValueError` em volta de conversão de dinheiro não pegava
  nenhuma das quatro.

- **`DELETE /api/v1/payment/{id}` foi removido.** A v0.259.0 o adicionou ao
  overlay e publicou `delete_api_v1_payment_by_id`, com docstring afirmando
  que cancela um pagamento pendente. O documento publicado tem só `get`
  nesse path, e nenhum caminho de payment tem `delete` — o DELETE que a
  Woovi documenta é em `/api/v1/charge/{id}`. Corrigir um documento é para o
  que ele erra; endpoint que ninguém observou é palpite.

- **`__all__` gerado volta a passar no `RUF022`.** A ordenação do gerador
  era de string, e a do ruff é natural: com o contador de desambiguação
  chegando a dois dígitos no documento novo, ele queria `...Orig9` antes de
  `...Orig10`. A chave virou `scripts/export_order.py`, compartilhada pelos
  dois geradores em vez de duplicada.

- **Números da v0.259.0 corrigidos na doc.** "18 campos continuam `float`"
  eram 19; "`skip` e `limit` em 27 operações cada" contava campo de
  **resposta**, e só 6 operações os recebiam como query param; "os demais 51
  `value`" eram 50; e "105 operações" era o número de antes da própria
  release, que shippou 106. Detalhe em `vendor/openpix-evidence.md`.

## [0.259.0] — 2026-08-27

### Added

- **Overlay versionado da spec da OpenPix — `scripts/openpix_overlay.py`.**
  `vendor/openpix-openapi.yaml` continua byte a byte o documento que a Woovi
  publica, então refresh upstream continua sendo um diff só do que **eles**
  mudaram. Tudo que a gente sabe que o documento erra passou a viver no
  overlay, uma correção nomeada por vez, cada uma com a evidência ao lado, e
  aplicada antes de gerar. Três famílias, e o `make openpix-regen` imprime o
  que cada uma fez.

- **`Charge.fee`, `Charge.discount`, `Charge.value_with_discount` e
  `ChargeRefund.refund_id`.** Campos que a API responde e o documento não
  declara — o `Charge` da spec (linha 21876) não tem nenhum dos três, e
  `refundId` está declarado só no `Refund`, de estorno de transação Pix.
  Sem declaração, `extra="ignore"` os descartava: quem gravava
  `charge.fee` numa coluna de ledger escreveria **zero em toda linha**, e o
  erro só apareceria na conciliação.

  ```python
  from tempest_fastapi_sdk.integrations.payment.openpix import Charge

  charge = Charge.model_validate(
      {"value": 199000, "fee": 2500, "discount": 0, "valueWithDiscount": 199000}
  )
  charge.fee, charge.value_with_discount   # (2500, 199000)
  ```

  Todos opcionais — resposta sem eles continua validando. `refund_id` é campo
  próprio, e não um segundo nome para `end_to_end_id`: no `Refund` os dois são
  identificadores diferentes, e colapsá-los faria o model afirmar o que o
  documento contradiz.

  Fecha [#223](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/223).

- **`OpenPixClient.delete_api_v1_payment_by_id(id)`.** O documento não tem
  `DELETE` de payment, então o fluxo de transferência em dois passos ficava
  **sem caminho de recuperação**: com o `POST /payment` criado e o
  `POST /payment/approve` falhando, a transferência fica pendente no gateway e
  pode ser liberada depois — e cancelar exigia descer para o `HTTPClient` cru
  justamente no passo mais delicado do fluxo de dinheiro.

  O retorno é `dict[str, Any]` de propósito: este repositório não tem
  credencial da Woovi para observar o corpo da resposta, então ele não é
  modelado. Modelar um shape que ninguém mediu seria pior que não modelar, e
  `dict[str, Any]` não descarta nada.

  Fecha [#222](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/222).

### Changed

- **BREAKING — dinheiro e contagem na OpenPix passam de `float` para `int`
  (154 campos).** A Woovi liquida em centavo inteiro e a spec diz isso na
  própria descrição, 35 vezes ("Value in cents of this charge", "Number in
  cents that represent the balance") — e tipa tudo como `number`. O cliente
  gerado então mandava `{"value": 1990.0}` onde a API documenta `1990`, e
  qualquer aritmética que o consumidor fizesse sobre o valor começava de uma
  aproximação binária.

  ```python
  ChargePayload(correlation_id="abc-1", value=1000).model_dump(
      by_alias=True, mode="json", exclude_none=True
  )
  # antes: {"correlationID": "abc-1", "value": 1000.0, ...}
  # agora: {"correlationID": "abc-1", "value": 1000, ...}
  ```

  Não é só dinheiro: `skip` e `limit` — o par de paginação, repetido em 27
  operações cada — também eram `float`, e um cliente mandando `skip=0.0` numa
  query string está pedindo tolerância ao provedor.

  **Os 18 campos que continuam `float`** são os únicos onde a fração é real: a
  cotação de stablecoin (`basePrice` é taxa de câmbio, `inputAmount` é
  documentado como *"currency unit, not cents"*, `outputAmount` é quantidade de
  stablecoin), o balde de tokens de rate limit, e `annualRevenue`, cuja unidade
  o documento não diz. A regra lê a descrição **antes** do nome exatamente por
  isso: `inputAmount` é centavo na lista de depósito e não é na cotação.

  **Migração:** passo a passo em `docs/migration.md`, seção 0.259.0.

- **Model de resposta gerado passa a usar `extra="allow"`.** Vale para toda
  integração gerada (OpenPix e Mercado Pago), e é transitivo: o objeto aninhado
  é justamente onde o campo descartado se esconde. Model de **payload**
  continua com `extra="ignore"` — ali chave inesperada é erro de digitação de
  quem chamou, e levá-la ao provedor é pior que descartar.

  Isso fecha na raiz um defeito que este repo já conhecia e contornava: o
  `point_of_interaction` do Mercado Pago — o QR do Pix — era **descartado** na
  validação, sem exceção e sem aviso, deixando um
  `transaction_details.external_resource_url` com cara de resposta enquanto a
  string de copiar-e-colar já tinha sumido. Ele agora sobrevive em
  `model_extra`; `parse_pix_payment` continua sendo a forma tipada de lê-lo.

### Fixed

- **O adapter de OpenPix trunca `expires_in` para segundo inteiro.**
  `timedelta.total_seconds()` devolve `float` e o campo agora é `int`.

## [0.258.0] — 2026-08-27

### Added

- **`EmailUtils.send_many()`, `BulkEmailReport` e `FailedRecipient`.** Avisar a
  base inteira com `send()` num laço custa uma conexão SMTP por mensagem, e o
  primeiro endereço ruim aborta o resto. `send_many` abre **uma** conexão por
  lote e reporta o que não entregou em vez de levantar:

  ```python
  from tempest_fastapi_sdk import BulkEmailReport, EmailUtils

  mailer = EmailUtils(**settings.email_kwargs())

  report: BulkEmailReport = await mailer.send_many(
      destinatarios,
      subject="Manutenção programada",
      body="Vamos ficar fora das 02h às 03h.",
  )

  report.delivered          # aceitos pelo servidor
  report.permanent          # 5xx — a caixa não existe; pode podar da base
  report.transient          # 4xx — cheia ou greylisted; reenfileire
  ```

  Quatro coisas que o laço não faz, e por que a resposta não é `gather` sobre
  `send()`:

  - **Uma conexão por lote, não por mensagem.** `send()` conecta, autentica e
    dá `QUIT` toda vez; o número de conexões cai de `len(recipients)` para
    `len(recipients) / batch_size` (default 500).
  - **Fan-out com teto.** `gather` sobre a lista inteira abre uma conexão por
    destinatário, e todo provedor de SMTP hospedado limita quantas um
    remetente segura ao mesmo tempo — passando do teto, as excedentes são
    estranguladas ou derrubadas, e o teto é do provedor, não nosso.
    `max_concurrency` (default 32) limita as conexões abertas
    — os dois defaults são os mesmos do broadcast de Web Push
    (`_DEFAULT_BROADCAST_PAGE_SIZE`, `_DEFAULT_BROADCAST_CONCURRENCY`), pelo
    mesmo motivo.
  - **Falha parcial vira relatório.** Recusa de destinatário nunca levanta.
    Só falha da operação levanta — host que não resolve, conexão recusada,
    autenticação negada.
  - **`5xx` e `4xx` chegam separados.** Colapsar os dois numa lista "falhou"
    obriga o chamador a reparsear o código SMTP que ele já tinha. Recusa sem
    código entra como transitória: o erro barato é tentar de novo, não apagar
    endereço bom.

  Cada destinatário recebe a própria mensagem — ninguém vê o endereço de
  ninguém. Concorrência é por conexão, não por mensagem: SMTP é serial num
  socket só, então o lote é enviado sequencialmente pela conexão dele e o
  paralelismo vem de rodar vários lotes ao mesmo tempo.

- **`DatabaseBackup(docker_container=...)` roda o tooling do Postgres dentro do
  container do banco.** Sem isso, a imagem da aplicação precisa carregar
  `postgresql-client` numa versão compatível com o servidor só para o job
  noturno. A imagem do banco já tem o `pg_dump` exato da versão dela:

  ```python
  backup = DatabaseBackup(settings.DATABASE_URL, docker_container="app-db")

  written = backup.backup(Path("backups/app.dump"))
  backup.restore(written)
  ```

  O dump é produzido dentro do container e atravessa pelo stdout para o arquivo
  local; o restore faz o inverso, com o arquivo entrando pelo stdin do
  `pg_restore`/`psql` — nada é copiado para dentro, então não sobra arquivo
  temporário nem janela em que ele está pela metade. Três detalhes que o teste
  fixa:

  - **`-h`/`-p` são descartados.** Host e porta da URL descrevem como a
    *aplicação* alcança o banco de fora; dentro do container esse caminho não
    existe. Usuário e database continuam vindo da URL.
  - **A senha atravessa por nome.** O comando carrega `-e PGPASSWORD` sem
    valor, e o Docker copia do ambiente do processo chamador — escrever
    `-e PGPASSWORD=…` colocaria a senha na linha de comando do container, que
    qualquer `ps` no host lê.
  - **Dump que falhou não fica no disco.** O arquivo de destino é removido
    quando o processo sai não-zero, para um dump truncado não passar por um
    backup bom.

  `BackupToolMissingError` passa a checar o `docker` no lugar do `pg_dump`
  neste modo. SQLite ignora o parâmetro — é cópia de arquivo dos dois jeitos.
  A propriedade que importa (o dump gerado lá dentro volta e restaura lá
  dentro) é testada atravessando de verdade: `tests/db/test_backup.py` sobe um
  `postgres:16-alpine` sob a marca `docker`, opt-in por `make test-docker`.

- **Os métodos de `LogUtils` aceitam posicionais `%`-style e `stacklevel`.**
  Adotar o SDK num serviço que já loga em `%`-style não exige reescrever call
  site, e a interpolação continua lazy — o template fica a mesma string entre
  chamadas, que é no que o agregador de log agrupa:

  ```python
  log.info("Email enviado com sucesso para %s", "ana@example.com")
  log.error("Falha ao enviar para %s: %s", email, motivo, op="send")
  ```

  Os dois estilos convivem: os posicionais montam a mensagem, `**fields`
  continua virando chave de topo no JSON. `stacklevel` tem default `2`, então
  `funcName`/`lineno` do record apontam para o **seu** call site em vez de para
  dentro da fachada; quem embrulha o `LogUtils` numa camada própria passa
  `stacklevel=3`.

- **`MetricsUtils.disk_async()`, e `strict=` em `disks`/`disks_async`.**
  `disks_async([path])` era a única forma async de ler um caminho só, e ela
  **loga e pula** o erro — porque a lista é plural e um caminho ruim não deve
  derrubar os outros quatro. Para um caminho só isso vira ausência silenciosa:
  o endpoint responde `200` com o bloco de disco simplesmente ausente, e o
  dashboard não distingue mount que sumiu de disco que ninguém pediu.
  `disk_async` propaga, como o `disk` síncrono. `strict=True` leva o mesmo
  comportamento para a variante plural.

- **`token_type_allowed(strict=..., legacy_claims=...)`.** O default — token
  sem `typ` é aceito — é certo para token que o SDK mintou e furado para token
  que o consumidor mintou. Um serviço que já separava access de refresh com um
  claim próprio (`type`, `token_type`) não tem `typ` em nenhum token legado nem
  os marcadores do SDK, então **todos** caem no "aceita" e um refresh token
  autoriza chamada de API pela vida inteira do refresh:

  ```python
  legado = {"sub": "u1", "type": "refresh"}

  token_type_allowed(legado, [ACCESS_TOKEN_TYPE])
  # True  — o SDK não conhece o claim `type`

  token_type_allowed(
      legado,
      [ACCESS_TOKEN_TYPE],
      strict=True,
      legacy_claims=("type",),
  )
  # False
  ```

  `legacy_claims` é lido em ordem e só quando `typ` está ausente; `strict=True`
  recusa o que continuar sem classificação e **não** desliga os marcadores
  antigos do SDK — `refresh: True` continua sendo refresh.

### Changed

- **Os arquivos de log passam a rotacionar por padrão.** `configure_logging`
  ganha `max_bytes` (default `10_000_000`) e `backup_count` (default `5`), e os
  handlers por nível viram `RotatingFileHandler`. `FileHandler` puro cresce sem
  teto: num serviço com uma linha por request, rodando em host de longa
  duração, `info.log` é o que enche o disco — e disco cheio derruba o serviço e
  o que mais dividir a partição. O lado leitor deste par já tinha o teto
  (`DEFAULT_MAX_RECORDS_PER_FILE = 20_000` no router de `/logs`, adicionado
  depois que um serviço com diretório de log em gigabytes respondeu com worker
  morto); este é o lado de quem escreve.

  `max_bytes=0` volta ao `FileHandler` puro, para host onde `logrotate` ou um
  sidecar é o dono da retenção. Detalhe em `docs/migration.md`, seção 0.258.0.

### Fixed

- **Bound numérico em schema `type: string` vira bound de tamanho no codegen —
  e os dois campos `comment` da OpenPix voltam a se construir.** Nada legítimo
  produz `maximum` num `type: string` (string não tem magnitude), e spec no
  mundo real escreve assim mesmo: `ChargeRefundPayload.comment` e
  `RefundPayload.comment` carregam `maximum: 140` sob uma descrição que diz
  "Maximum length of 140 characters". Passado ao pé da letra, o gerador emitia
  `Field(le=140)` num `str` — e pydantic não rejeita o valor, ele levanta na
  construção:

  ```python
  ChargeRefundPayload(comment="obrigado")
  # TypeError: Unable to apply constraint 'le' to supplied value obrigado
  ```

  Ou seja: todo refund com comentário falhava antes de sair do processo.
  `maximum` / `minimum` num schema de string agora são relidos como
  `max_length` / `min_length`; bound exclusivo não tem equivalente de tamanho
  que valha adivinhar e é descartado. Schema numérico não muda.

## [0.257.0] — 2026-08-27

### Changed

- **BREAKING (pequeno) — `ScriptedBackend` e `FailingBackend` renomeiam o
  parâmetro `specs` para `tools`, e as quatro assinaturas ganham `**kwargs`.**
  Os dois fakes existem para fingir `ChatBackend` / `ToolCallingBackend` — e
  não satisfaziam nenhum dos dois sob mypy: o protocolo chama o parâmetro
  `tools` e aceita `**kwargs`, e um membro de protocolo só é implementado por
  assinatura com o mesmo nome de parâmetro.

  ```python
  from tempest_fastapi_sdk.agents import Agent
  from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

  agent = Agent(ScriptedBackend([replies("ok")]))
  # antes: Argument 1 to "Agent" has incompatible type "ScriptedBackend";
  #        expected "ChatBackend | ToolCallingBackend"  [arg-type]
  ```

  Era a **primeira linha** de sete blocos da receita
  `recipes/agents-testing.md`: quem seguia a receita num serviço com mypy
  ligado colhia o erro no próprio teste.

  **Migração:** a chamada posicional — a que o `Agent` faz — não muda. Quem
  chamava `backend.chat_with_tools(messages, specs=[...])` por keyword troca
  para `tools=`. O atributo `specs_seen` continua com o nome de sempre. Passo a
  passo no guia de migração: `docs/migration.md`, seção 0.257.0.

### Fixed

- **`on_disconnect` aceita callback que devolve alguma coisa.** A anotação era
  `Callable[[], Awaitable[None] | None] | None`, mas o corpo faz
  `result = on_disconnect()` e só aguarda quando o retorno é awaitable —
  qualquer outro valor é descartado. Então o exemplo canônico da receita de
  SSE não passava no type-checker do leitor:

  ```python
  task = asyncio.create_task(producer())
  return stream.response(on_disconnect=task.cancel)  # Task.cancel devolve bool
  ```

  Passa a ser `Callable[[], object] | None` em `EventStream.response`,
  `sse_response` e no `_guard_stream` que os dois usam. Nada muda em runtime —
  medido com um callback síncrono que devolve `bool` contra um endpoint de
  verdade: `200`, dois frames entregues, callback executado, retorno
  descartado. A suíte não fixa essa forma (o teste existente usa
  `async def cleanup() -> None`); quem a exercita hoje é a receita de SSE,
  pelo guard de tipo.

- **Os stores de Redis aceitam o `redis.asyncio.Redis` que a receita manda
  passar.** `_RedisLike` (idempotência e cache de resposta) e `RedisLike`
  (WebAuthn) declaravam os membros como `async def get(self, key: str) -> ...`.
  Duas consequências: um membro `async def` exige retorno `Coroutine`, e um
  parâmetro nomeado exige o **mesmo nome** na implementação. O redis-py chama
  o parâmetro de `name` e devolve `Awaitable`, então a linha da receita era
  erro de tipo:

  ```python
  store = RedisIdempotencyStore(Redis.from_url(settings.REDIS_URL))
  # antes: Argument 1 to "RedisIdempotencyStore" has incompatible type
  #        "Redis"; expected "_RedisLike"  [arg-type]
  ```

  Os três protocolos passam a declarar parâmetro **posicional** e retorno
  `Awaitable[str | bytes | None]` — a forma que o `RedisLike` do rate limiter
  já usava, e que é por isso que aquele nunca teve o problema. Medido nos seis
  stores de Redis exportados: três recusavam o cliente real (idempotência,
  cache de resposta, WebAuthn) e três aceitavam (rate limit, quota, sessão);
  agora nenhum recusa.

  `Awaitable[Any]` também faria o cliente passar, e foi por onde esta correção
  passou antes de ser medida — mas apaga o tipo do valor lido: com ele,
  `raw = await client.get(key)` vira `Any` dentro do próprio SDK, e o
  `json.loads(raw)` logo abaixo deixa de ser checado. Aceitar o cliente e
  manter o tipo é a mesma linha; só a forma estreita faz as duas coisas.

- **`require_authenticated` aceita qualquer sujeito, não só `BaseUserModel`.**
  A função não lê atributo nenhum — só rejeita `None` — mas o `TypeVar` estava
  presa ao modelo de usuário, e a receita do Firebase guarda um
  `FirebaseIdentity` com ela. mypy resolvia o `TypeVar` para `None` e recusava
  o argumento. Agora usa um `SubjectT` sem bound; `require_active` e
  `require_admin` mantêm o bound, porque leem `is_active` / `is_admin`.

### Note

- **Guard novo: `tests/test_docs_type_guard.py`.** Os quatro defeitos acima
  têm a mesma origem — nenhum guard rodava um type-checker sobre os exemplos
  da doc, e os dois guards de exemplo diziam isso no próprio docstring
  ("o que ele não pega, de propósito: tipo de argumento"). O guard novo
  escreve cada bloco parseável do site como um módulo e roda mypy com a config
  deste repo, lendo quatro códigos de erro: `arg-type`, `call-arg`,
  `name-defined` e `used-before-def`.

  Ao rodar pela primeira vez — 1999 blocos, 226 arquivos, as duas línguas
  mais o `README.md`: **162 achados**. Além dos
  quatro defeitos de tipagem do SDK, 89 eram `NameError` na colagem — uma
  passada anterior de "deixar todo exemplo completo" tinha acrescentado o
  placeholder **depois** da linha que o usa (`repository = BaseRepository(session, ...)`
  com `session = None` três linhas abaixo). O resto era exemplo que o leitor
  não conseguia rodar: `PutObjectItem(data="thumbs/a.png")` subindo o nome do
  arquivo como conteúdo, `body={...}` num cliente que pede o schema gerado,
  `emb._embed_many` (privado, e com outra assinatura) passado ao
  `BatchScheduler`.

  A varredura também limpou o que o guard não lê — `email = "ana@example.com"`
  sendo usado como `await email.send(...)` em duas receitas, `order.id` sobre
  um `dict`, `provider.advance(...)` chamado sobre a anotação do protocolo em
  vez do fake. Sobraram 25 `attr-defined`, todos das duas formas legítimas:
  excerto de classe sem `__init__`, e o `op.replace_enum` que o Alembic
  registra em runtime.

  O achado mais caro não quebrava nada: a paginação por cursor do `README.md`
  comparava `(created_at, id) > (valor, id)` como **tupla do Python**, que
  compara só o primeiro elemento e devolve a expressão dele — SQL válido,
  desempate silenciosamente perdido, linhas repetidas ou puladas entre páginas.
  Agora usa `tuple_()`, e a página descendente usa `<` em vez de `~(... > ...)`,
  que incluía a própria linha do cursor.

  Custo: ~40s com cache de mypy frio, menos de 1s quente.

## [0.256.0] — 2026-08-27

### Added

- **Celular BR: `is_valid_mobile_phone_br`, `normalize_mobile_phone_br`,
  `MobilePhoneBRField` e `parse_phone_br`.** `is_valid_phone_br` responde a
  uma pergunta de formato — *isto tem cara de telefone brasileiro?* — e aceita
  fixo. Quando o número é o **canal de entrega** (WhatsApp, SMS, um código de
  verificação), aceitar fixo não é validação frouxa: é falha silenciosa. O
  número entra no cadastro, passa por toda a validação, e só falha lá na
  frente, quando a notificação não é entregue — sem erro para o usuário e sem
  log óbvio para quem opera.

  ```python
  from tempest_fastapi_sdk.utils import is_valid_mobile_phone_br, is_valid_phone_br

  is_valid_phone_br("(11) 3333-4444")          # True  — é um telefone
  is_valid_mobile_phone_br("(11) 3333-4444")   # False — mas não é um celular
  ```

  Em schema é a troca de um tipo de campo por outro:

  ```python
  from tempest_fastapi_sdk import BaseSchema
  from tempest_fastapi_sdk.utils import MobilePhoneBRField

  class NotificationTargetSchema(BaseSchema):
      phone: MobilePhoneBRField   # fixo devolve 422
  ```

  `parse_phone_br` entrega o número já quebrado, para quem precisa formatar ou
  gravar E.164:

  ```python
  from tempest_fastapi_sdk.utils import parse_phone_br

  parsed = parse_phone_br("+55 (11) 98888-7777")
  parsed.area_code, parsed.number, parsed.is_mobile, parsed.e164
  # ("11", "988887777", True, "+5511988887777")
  ```

  Três detalhes que o teste fixa, porque nenhum deles é óbvio:

  - **As duas normalizações divergem de propósito.** `normalize_phone_br`
    preserva o que foi digitado, então a mesma linha vira `"5511988887777"` ou
    `"11988887777"` conforme a grafia, e a coluna guarda duas strings para um
    número só. `normalize_mobile_phone_br` sempre devolve os **11 dígitos da
    forma nacional**. O `+55` continua disponível em `PhoneNumberBR.e164`.
  - **`parse_phone_br` é mais estrito que `is_valid_phone_br`.** Aplica os
    prefixos da ANATEL — assinante de 8 dígitos começa em `2`-`5` — então
    `"8912345678"` passa pelo validador antigo e volta `None` aqui.
  - **DDD 55 não é o código do país.** Santa Maria (RS) colide com o `+55`;
    o prefixo só é descartado quando o total de dígitos (12 ou 13) prova que
    ele está lá.

  O nome `PhoneBR` já era um alias deprecado de `PhoneBRField` desde a v0.76,
  então o resultado do parse chama-se `PhoneNumberBR`.

  Fecha [#208](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/208).

- **`AsyncRedisManager.client_proxy`: o handle que pode ser construído antes do
  `connect()`.** Os dois ciclos de vida não se encaixam. O FastAPI exige
  middleware registrado no import do módulo — `add_middleware` depois do
  startup levanta — mas o `connect()` só roda no lifespan. Resultado:
  `RedisRateLimitStore(redis_manager.client)` levantava `RuntimeError` no
  único momento em que o store precisa existir, e não havia ordem em que os
  dois coubessem.

  ```python
  app.add_middleware(
      RateLimitMiddleware,
      store=RedisRateLimitStore(cache.client_proxy),   # válido desde já
  )
  ```

  O guard não estava protegendo nada: `connect()` não faz I/O — `from_url()`
  só monta o `ConnectionPool`, e o `redis-py` conecta preguiçosamente no
  primeiro comando. Medido contra um host não-roteável, `from_url()` retorna em
  0,0001 s; quem falha é o primeiro comando — 1,00 s com
  `socket_connect_timeout=1`, isto é, o timeout configurado, não uma
  propriedade do `from_url()`.

  Adiantar a construção também não bastava: `disconnect()` descarta o client e
  o `connect()` seguinte cria um **objeto novo**, então um store que guardou a
  referência antiga fica com client morto. Por isso o proxy resolve o client a
  cada acesso de atributo, em vez de capturá-lo uma vez —
  `test_survives_a_reconnect` fixa isso.

  Vale para os sete stores Redis do SDK, que recebem client pronto e não
  factory. `client` continua como está, para quem lê dentro de um request; os
  docstrings de `RedisSessionStore` e `RedisFeatureFlagBackend`, que sugeriam
  `AsyncRedisManager.client` como valor — justamente a chamada que levanta —
  agora apontam o `client_proxy` para construção antecipada.

  O tipo declarado é `Redis` para o proxy encaixar em todo parâmetro de store
  sem cast do lado do consumidor. Ele é um handle que encaminha, não um
  `Redis`: `isinstance` é `False` e protocolo dunder resolvido no tipo não é
  encaminhado.

  Fecha [#210](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/210).

### Changed

- **BREAKING — o 429 do `RateLimitMiddleware` passa a ser JSON, no envelope de
  erro do SDK.** O SDK define um envelope canônico e o aplica em todo handler
  registrado por `register_exception_handlers` — menos no próprio rate limiter,
  que respondia `text/plain` com o `error_message` cru. Quem adotava os dois
  ficava com duas formas de erro na mesma API, e o cliente precisava de um caso
  especial checando `status === 429` para ler `text()` em vez de `json()`.

  ```json
  {"detail": "Too many requests",
   "code": "TOO_MANY_REQUESTS",
   "details": {"retry_after_seconds": 60, "limit": 15}}
  ```

  Não era só inconsistência de estilo: `error_responses()` sempre apontou o 429
  para o `ErrorResponseSchema`, então o corpo real contradizia o schema que a
  rota publica e cliente gerado quebrava ao desserializar.

  Novo parâmetro `error_code`, default `TooManyRequestsException.code` — lido da
  própria exceção, para os dois não divergirem. `details` carrega o que antes só
  existia em header: `retry_after_seconds` e o `limit` da regra que barrou.

  **Migração:** cliente que ramifica por `status === 429` não muda; cliente que
  lê o corpo como texto passa a ler JSON e usar `detail`. Serviço que
  reescrevia a resposta com uma subclasse pode apagar o contorno. Passo a passo
  no guia de migração: `docs/migration.md`, seção 0.256.0.

  O middleware monta a resposta em vez de levantar a exceção porque
  `BaseHTTPMiddleware` adicionado por `add_middleware` fica fora do
  `ExceptionMiddleware` do Starlette: exceção levantada no `dispatch` não
  encontra handler e vira 500.

  Conferido nos vizinhos que a issue mandou olhar: `BodySizeLimitMiddleware` já
  emitia o envelope, e `IdempotencyMiddleware` / `ResponseCacheMiddleware` nunca
  emitem corpo de erro próprio — só replayam resposta cacheada. O
  `rate_limit.py` era o único site fora do envelope.

  Fecha [#211](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/211).

## [0.255.0] — 2026-08-25

### Added

- **`WebPushSubscriptionService.notify_all()`: o aviso global, com a mesma poda
  do envio por usuário.** O segundo caso de uso mais comum do Web Push —
  manutenção, release, campanha — era o único que obrigava o consumidor a
  descer para o dispatcher: listar as linhas por fora, montar os schemas na
  mão, chamar `send_many` e chamar `prune` com o retorno, reimplementando o
  `notify_user` inteiro só para trocar a fonte da lista.

  ```python
  entregues = await service.notify_all(
      WebPushPayloadSchema(title="Manutenção programada", body="02h às 03h."),
  )
  ```

  Duas diferenças em relação ao `notify_user`, as duas sobre tamanho.
  **Anda em lotes** (`page_size=500`), em vez de carregar a tabela inteira e
  abrir N mil corrotinas de uma vez. E **limita o paralelismo**
  (`max_concurrency=32`), porque cada dispatch é uma requisição TLS a um push
  service e milhares ao mesmo tempo rendem rate limit, não envio mais rápido.

  O passeio é por cursor, não por offset, porque o método **apaga enquanto
  anda**. Medido numa tabela de 8 inscrições, apagando 4 no caminho, em páginas
  de 2: o passeio por offset visitou 6 linhas e nunca chegou em duas — uma
  delas viva. O cursor compara `(created_at, id)` em vez de contar posições.
  Linha criada durante o broadcast não é visitada, e é isso que faz o passeio
  terminar.

- **`WebPushSubscriptionService.list_all()`.** Todas as inscrições, de todos os
  usuários, para quem precisa das linhas em si — export, contagem por host,
  migração. Para entregar, prefira `notify_all`, que não segura a tabela em
  memória.

- **`WebPushDispatcher.send_many(max_concurrency=...)`.** Semáforo opcional
  sobre o fan-out. `None` (default) mantém o comportamento de sempre — todo
  dispatch começa de imediato, que é o certo para os poucos aparelhos de uma
  pessoa.

- **`SSEBroker.broadcast()`: um evento para todos os canais.** `publish`
  resolve um canal para N conexões; faltava o eixo ortogonal — um evento para
  N canais, que é o "aviso global", "manutenção em 5 minutos", "saiu versão
  nova". Sem ele, o serviço mantinha por fora um registro paralelo dos canais
  abertos em cada worker, só para conseguir iterar e chamar `publish` em cada
  um: metade do broker reimplementada no consumidor, e ainda assim só do
  worker local.

  ```python
  await broker.broadcast({"type": "MAINTENANCE"}, event="notice")
  ```

  Em processo único, varre os canais locais. Com Redis, publica no canal
  reservado `__broadcast__` — que o `PSUBSCRIBE {prefixo}:*` de todo worker já
  alcança, sem subscrição nova — e o `run()` de cada worker refana para todos
  os streams locais dele. Um stream inscrito em mais de um canal recebe uma
  vez só: o fan-out é sobre o conjunto de streams.

  `BROADCAST_CHANNEL` é reservado de verdade: `register` e `publish` recusam
  esse nome com `ValueError`, porque é ele que o lado receptor lê para decidir
  entre entregar a um canal e entregar a todos.

- **`SSEBroker.local_channels()`.** Os canais com pelo menos um stream aberto
  neste worker — a contraparte do `local_subscribers(canal)`, que só responde
  por um. Resolve "quantos conectados agora" sem o consumidor ler `_channels`,
  que é privado e mudaria em silêncio.

- **`EventStream(heartbeat_event=...)`: o batimento como evento visível.** O
  heartbeat era `ServerSentEvent(comment="keepalive")` hardcoded no corpo do
  `stream()`. Comentário SSE **não dispara `onmessage`**: mantém o TCP vivo,
  que é o propósito declarado, mas é invisível para o JavaScript. Um cliente
  que usa o batimento como prova de conexão viva — para reconectar, acender
  indicador, armar watchdog — não tinha o que escutar.

  ```python
  stream = EventStream(
      heartbeat_seconds=15.0,
      heartbeat_event=ServerSentEvent(data={"type": "PING"}, event="ping"),
  )
  ```

  `None` mantém o comentário de sempre; um `ServerSentEvent` troca o frame; um
  callable é resolvido **por batimento**, para quem carimba timestamp no
  payload. As duas escolhas eram uma só antes: `heartbeat_seconds=None`
  desligava o batimento **e** a detecção de ociosidade, e o valor numérico
  ligava o batimento só na forma de comentário.

  `SSEBroker(heartbeat_event=...)` repassa para todo stream que abrir. É o
  único caminho de composição possível: `register` constrói o `EventStream`
  por dentro, então nem subclasse do consumidor entra no fluxo.

### Fixed

- **Inscrição morta do Edge agora é podada: o WNS diz `400`, não `404`.** O
  dispatcher tratava `404` e `410` como assinatura morta — os dois status que o
  padrão reserva para isso, e que FCM e Apple respondem. O WNS, push service da
  Microsoft usado pelo Edge, responde **`400 Bad Request` com corpo vazio**
  quando o navegador foi desinstalado, o usuário saiu da conta Microsoft, ou a
  inscrição foi despejada.

  O resultado era que assinatura morta de Edge nunca era podada: ficava no
  banco para sempre, falhava em todo envio, e o `notify_user` a contava como
  não entregue indefinidamente.

  A regra é escopada ao host: `400` de `notify.windows.com` vira
  `WebPushGoneError`; `400` de qualquer outro serviço continua `WebPushError`,
  porque ali significa requisição malformada — e apagar a inscrição por causa
  disso desinscreveria um aparelho vivo, que é o erro pior dos dois.

- **A docstring de `send_many` descrevia comportamento que o código não tinha.**
  Ela afirmava que toda falha era "also returned in the gone list when the
  endpoint is known"; o código só coleta `WebPushGoneError`. A lista alimenta o
  `prune`, então incluir falha transitória apagaria aparelho vivo — o código
  estava certo e a prosa, errada.

## [0.254.0] — 2026-08-25

### Added

- **`TokenUsage.cache_hit_tokens`: o prefixo que o provedor serviu do cache.**
  Quando o começo do prompt bate com uma chamada recente, a DeepSeek e a OpenAI
  servem essa parte de um cache e cobram bem mais barato por ela — a tabela da
  DeepSeek põe o token de acerto duas ordens de grandeza abaixo do de erro.
  `from_payload` descartava o campo, então todo prompt repetido era precificado
  pelo preço cheio.

  O número errado é pior que o número ausente: o total continuava plausível,
  só que alto. Nada no sistema tinha como notar.

  A fatia é **parte de** `input_tokens`, não uma parcela a mais — somar os dois
  cobra o prefixo duas vezes. O campo é o quarto do dataclass, então
  `TokenUsage(entrada, saida, total)` posicional continua valendo.

  ```python
  _texto, uso = await gen.generate_with_usage("Resuma isto.")
  uso.input_tokens        # 3000
  uso.cache_hit_tokens    # 2560 — destes 3000, não além deles
  ```

  Duas grafias na mesma família OpenAI-compatível, as duas lidas: a DeepSeek
  manda `prompt_cache_hit_tokens` na raiz do `usage`, a OpenAI manda
  `prompt_tokens_details.cached_tokens`. Ler só uma funciona no provedor em que
  se testou e cobra caro demais no outro.

- **`AIUsageStore` precifica o prefixo em cache.** `price_cache_hit_per_1k`
  entra no construtor e `estimate_cost` ganha `cache_hit_tokens=` keyword-only;
  `totals()` soma a fatia e `UsageTotals` a reporta.

  O default é `None`, **não** `0.0`, e a diferença é a linha inteira: `0.0` é um
  preço, e um store sem essa configuração passaria a dar o prefixo de graça,
  subestimando toda chamada repetida. `None` significa "não há tarifa separada"
  e deixa a fatia no preço de entrada normal — configuração ausente não deveria
  mexer em número nenhum.

  `cache_hit_tokens` maior que `input_tokens` é recusado por clamp, não por
  exceção: relatório absurdo do provedor não deve virar um termo negativo de
  preço cheio.

- **`extract_json_object(texto)`**, o gêmeo de `extract_json_list` para a
  resposta que é **um** registro — classificador de intenção, decisão de
  roteamento, extração de campos fixos. Mesma tolerância a cerca markdown e
  prosa em volta que o `parse_structured`, e o mesmo `None` de "gere de novo"
  no lugar da exceção: um laço de retry escrito em volta de `except ValueError`
  engole erro de verdade junto com deslize de formato.

### Migration

- `BaseAIUsageModel` ganhou a coluna `cache_hit_tokens`. Gere a migration
  (`tempest db revision`) antes de subir. A coluna é **nullable** de propósito:
  linha gravada antes dela não sabe o próprio split entre acerto e erro de
  cache, e afirmar zero ali cobraria uma chamada com desconto pelo preço cheio.
  Serviço que não usa `AIUsageStore` não é afetado.

## [0.253.0] — 2026-08-23

### Added

- **`tempest_fastapi_sdk.testing.fakes`: um substituto dirigível para cada
  costura de terceiro.** Oito fakes — `FakePixProvider`, `FakeTextBackend`,
  `FakeModerationBackend`, `FakePushDispatcher`, `FakeEmailUtils`,
  `FakeGeocodingBackend`, `FakeRoutingBackend`, `FakeWebSearchBackend` — que
  implementam a costura e não falam com ninguém: sem credencial, sem conta de
  sandbox, sem rede. Dois usos, um objeto: rodar o serviço local sem se
  cadastrar em nada, e afirmar sobre um fluxo em teste sem mock escrito à mão.

  O SDK já tinha 16 substitutos in-memory, mas todos de **infra** (Redis, banco,
  fila). Nenhum de **terceiro remoto** — cada costura tinha só a implementação
  real, então validar um checkout, um email de ativação ou um fluxo de agente
  exigia credencial ou mock.

  O que separa isto de mock é o **steering**. Um mock responde a chamada que
  você programou; estes guardam estado e deixam o teste movê-lo:

  ```python
  charge = await provider.create_pix_charge(request)   # pending
  event = provider.advance(charge.provider_charge_id, PaymentStatus.PAID)
  event.type   # PixEventType.CHARGE_PAID
  ```

  `advance` alcança o que o provedor real não dá sob demanda: `PAID` num sandbox
  exige alguém escaneando um QR code, e `CHARGED_BACK` exige alguém abrindo uma
  disputa. Cada fake tem o seu (`flag`, `add_place`, `add_route`, `add_results`,
  `queue`), e todos aceitam `fail_next(erro)` — o ramo que falha, com a exceção
  que o cliente real levanta, e sem inventar exceção nova.

  O que aconteceu fica inspecionável: `calls` em todos, mais `outbox`, `sent` /
  `sent_to`, `prompts`, `charges`, `queries`, `routes`, `checked`.

  Três decisões que valem registro:

  - **`FakeEmailUtils` é subclasse de `EmailUtils`**, não implementação de
    protocolo, porque `UserAuthService` é tipado contra a classe concreta — é a
    herança que faz o fake passar por `UserAuthService(email=...)` com o
    type-checker satisfeito. Só o `send` é substituído; `render_template`
    continua sendo o real, então um teste afirma sobre o mesmo HTML que a
    produção renderiza.
  - **`FakeRoutingBackend` delega a `estimate_travel`**, o estimador offline que
    o SDK já ships, em vez de inventar aritmética própria que poderia divergir
    dele. Medido: mesma distância e mesma duração do estimador, com `source`
    marcado como `"fake"`.
  - **Resolução é lazy (PEP 562)**, como em `integrations`: pedir o fake de Pix
    não importa genai, push nem geo. Há guard para isso — um subprocesso
    importa `testing.fakes` e conta os módulos `genai` carregados, e o número
    tem de ser zero.

  Guard de conformidade em `tests/testing/test_fakes_contract.py`: para cada
  fake, todo callable da costura existe, os nomes de parâmetro e a anotação de
  retorno batem por `inspect.signature`, e é `async` exatamente onde a costura é
  `async` — um substituto sync passaria numa checagem por nome e bloquearia o
  event loop. Mais um teste que exige que todo fake exportado esteja na tabela,
  para fake novo não shippar sem cobertura. Medido: renomeando um parâmetro de
  `max_results` para `limit`, o guard falha com
  `['self', 'query', 'limit'] == ['self', 'query', 'max_results']`.

  Receita nova, bilíngue: `docs/recipes/fakes.md`. Todo exemplo dela foi
  executado e a saída publicada é a medida.

## [0.252.0] — 2026-08-23

### Fixed

- **O extra `[genai-audio]` passa a declarar o runtime que o código importa,
  e o guard de import para de apagar o diagnóstico** ([#191]). `coqui-tts`
  deixa torch, torchaudio e torchcodec atrás dos extras dele (`[cpu]`,
  `[cuda]`, `[codec]`), então instalar `[genai-audio]` dava um TTS que não
  importava. Medido no próprio repo, com `uv sync --all-extras` (coqui-tts
  0.27.5, torch 2.13.0):

  ```text
  >>> import TTS.api
  ModuleNotFoundError: No module named 'torchaudio'
  ```

  Com torchaudio e torchcodec instalados, o resolve trazia transformers
  5.14.1 e o import morria mais adiante, porque `TTS.api` carrega a camada
  Tortoise de forma eager:

  ```text
  ImportError: cannot import name 'isin_mps_friendly' from 'transformers.pytorch_utils'
  ```

  Medido nas duas pontas: `isin_mps_friendly` **existe** na transformers
  5.0.0 e **não existe** da 5.1.0 em diante; com `transformers 4.57.6` o
  `import TTS.api` volta a passar. O extra agora declara `torch>=2.2.0`,
  `torchaudio>=2.2.0`, `torchcodec>=0.8.0` e `transformers<5` — o teto fica
  confinado a este extra, como o `httpx<1.0.0` do `[genai-image]`. Consumidor
  que fixava esses quatro pins à mão pode removê-los.

  O guard piorava o diagnóstico: `except ImportError` pega **qualquer** falha
  do import, e as três causas acima chegavam nele com a mensagem que nomeia a
  correção. Trocar por "instale `[genai-audio]`" respondia todas elas com a
  única instrução que não resolve — o extra já estava instalado. Agora a
  mensagem original é citada:

  ```text
  ImportError: Text-to-speech could not import Coqui TTS: No module named
  'torchaudio'. The [genai-audio] extra installs coqui-tts together with
  torch, torchaudio, torchcodec and transformers<5; ...
  ```

  `_require_faster_whisper` recebeu a mesma forma.

  Dois pontos menores no mesmo caminho: `resolve_audio_device("auto")` tratava
  torch ausente como "sem GPU" e caía para CPU **em silêncio** — máquina com
  GPU transcrevia na CPU e a lentidão parecia ser do faster-whisper; agora sai
  `logger.warning` nomeando a causa. E a docstring de `TextToSpeech` passa a
  dizer que o XTTS v2 é licence-gated: lido no coqui-tts 0.27.5,
  `ModelManager.tos_agreed` aceita só `tos_agreed.txt` ao lado dos pesos ou
  `COQUI_TOS_AGREED` igual à **string** `"1"`, e sem isso `ask_tos` chama
  `input()` — dentro de `asyncio.to_thread`, onde não há tty.

  Guards novos em `tests/genai/audio/test_audio_runtime_extra.py`: um lê
  `[project.optional-dependencies]` e exige os cinco pacotes declarados, um
  exige que o teto de transformers continue lá, e dois provam que a mensagem
  do upstream sobrevive ao re-raise nos dois guards. Mais um em
  `tests/genai/audio/test_audio.py` para o warning do device.

- **A revogação de família no reuso de refresh token deixa de ser descartada
  pelo rollback da request** ([#186]). `_lookup_refresh_record` detectava o
  replay, chamava `_revoke_family` e levantava `InvalidTokenException` — mas a
  revogação só passava por `flush()`. Numa request FastAPI a exceção sai pelo
  teardown da dependency de sessão, a unit of work é revertida, e a revogação
  vai com ela.

  Medido no repro da issue, antes:

  ```text
  replay rejected: 401: refresh token reuse detected
  rows in family: 2 | revoked: 0
  BUG: descendant of the replayed family still refreshes
  ```

  Depois:

  ```text
  replay rejected: 401: refresh token reuse detected
  rows in family: 2 | revoked: 2
  OK: descendant refused
  ```

  Detecção sem consequência era o pior dos três resultados possíveis: parece
  que o roubo foi tratado. Um atacante com token descendente mantinha a sessão
  viva indefinidamente — exatamente o cenário que a docstring do método diz
  cobrir.

  A ordem da correção é o desenho: lê o `family_id` **antes** (o rollback
  expira a instância, e ler coluna expirada em contexto async levanta
  `MissingGreenlet` em vez de recarregar), faz `rollback()` da sessão da
  request, aplica o `UPDATE` e comita. O rollback é deliberado: a escrita que a
  request tinha em stage já estava condenada, e comitá-la como efeito colateral
  de uma decisão de segurança seria surpresa. Fica na sessão do caller de
  propósito — uma sessão nova exigiria uma segunda conexão, e no SQLite a
  transação de leitura aberta do caller bloquearia o commit dela, trocando a
  revogação por erro de lock justamente na configuração que todo serviço usa
  em teste.

  Três testes de regressão em `tests/auth/test_refresh_db.py`, todos
  atravessando a fronteira de sessão que a request tem: a família volta
  revogada de uma sessão nova, o descendente é recusado, e a escrita alheia em
  stage **não** é comitada. O teste antigo não podia pegar isto — ele comitava
  depois do `pytest.raises`, na mesma sessão, que é a única coisa que uma
  request real nunca faz. Provado que os três falham sem a correção.

- **`make_jwt_user_dependency` deixa de entregar `None` ao handler quando o
  `user_loader` recusa o subject** ([#187]). Com `soft=False`, a dependency já
  levantava 401 para token ausente e para JWT sem o claim de subject — mas se
  o token decodificava e o loader devolvia `None`, esse `None` era **retornado
  para o handler**, e a rota respondia 200 com um usuário que o loader tinha
  recusado.

  Medido no repro da issue, antes:

  ```text
  no token              -> 401 (expected 401)
  owns, declined        -> 200 (expected 401)
  shared, declined      -> 200 (expected 401)
  ```

  Depois, com os dois ramos — o que abre sessão própria e o que compartilha a
  do request — devolvendo 401, e `soft=True` continuando a entregar `None`.

  O loader é documentado como *"the single seam where the service maps
  `payload[subject_claim]` to an actual user"*, então é o lugar natural para
  recusar: conta desativada, subject que não existe mais, id malformado.
  Recusar ali não recusava a request. Na prática, **desativar uma conta não
  tinha efeito até o access token expirar**, para quem confiava na dependency
  para isso.

  A regra agora mora num lugar só (`_resolved`), aplicada pelos dois ramos: um
  subject recusado tem o mesmo desfecho de um subject ausente. `soft=True`
  segue devolvendo `None`, que é o propósito do flag; levantar
  `UnauthorizedException`/`NotFoundException` de dentro do loader continua
  funcionando para quem quer escolher a mensagem.

  Cinco casos de regressão em `tests/api/test_jwt_dependency.py`, cobrindo os
  dois ramos em `soft=False` e `soft=True`, mais um que fixa que a recusa é
  sobre `None` e não sobre usuário qualquer. Provado que os dois casos de 401
  falham sem a correção.

- **A CSP default de `make_spa_router` deixa de bloquear o próprio bundle da
  SPA** ([#188]). O router usava `DEFAULT_STATIC_SECURITY_HEADERS` como default
  de `security_headers=`, e aquele conjunto existe para servir **arquivo que
  não se confia**: `default-src 'none'; sandbox`. Apontado para uma SPA
  compilada, ele bloqueia o bundle e a folha de estilo da própria página, e o
  `sandbox` sem `allow-scripts` bloqueia execução de script — documento em
  branco.

  Medido em browser real (Playwright), com o default antigo:

  ```text
  Loading the stylesheet '/assets/app.css' violates the following Content
  Security Policy directive: "default-src 'none'"
  Blocked script execution in '/' because the document's frame is sandboxed
  and the 'allow-scripts' permission is not set
  ```

  O `sandbox` é tão total que o próprio `page.evaluate` do Playwright não roda
  na página — a demonstração mais direta possível de que nenhum script executa
  ali.

  Com o default novo: zero mensagem no console, `#root` com o texto que o
  script escreveu, `background` do CSS externo aplicado, e o atributo `style`
  inline aplicado (que é o caso do React).

  Novos `DEFAULT_SPA_CONTENT_SECURITY_POLICY` e `DEFAULT_SPA_SECURITY_HEADERS`,
  exportados na raiz: `default-src 'self'`, `script-src 'self'`, `style-src
  'self' 'unsafe-inline'`, `img-src`/`font-src` com `data:`, `connect-src
  'self'`, `form-action 'self'`, `base-uri 'self'`, `object-src 'none'`,
  `frame-ancestors 'none'`, mais `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: strict-origin-when-cross-origin` e
  `Cross-Origin-Resource-Policy: same-origin`.

  `'unsafe-inline'` fica em `style-src` porque React escreve atributo `style`
  inline e política que quebra a UI é política deletada; fica **restrita a
  estilo**, com `script-src` em `'self'`. Quem controla a árvore de
  componentes pode apertar para `style-src-attr` e passar em
  `security_headers=`.

  `DEFAULT_STATIC_SECURITY_HEADERS` não muda — continua sendo o default certo
  para `HardenedStaticFiles`, que é para o que ele foi feito. Um teste fixa que
  os dois conjuntos **não** são iguais, porque igualá-los de novo devolve a
  página branca.

- **`tempest new` deixa de escrever placeholder de template no `Dockerfile` e
  no `.dockerignore`** ([#189]). Os dois templates são compartilhados com
  `tempest generate dockerfile`, que preenche `__SPA_HEADER__`,
  `__SPA_STAGE__`, `__SPA_COPY__`, `__SPA_IGNORE__` e `__SYSTEM_DEPS__`. O
  contexto do scaffold não tinha nenhuma dessas chaves, então todo projeto novo
  saía com os marcadores literais e o `docker build` morria na primeira linha
  que carregava um, **antes de qualquer camada**:

  ```text
  ERROR: dockerfile parse error on line 8: unknown instruction: __SPA_HEADER__#
  ```

  Medido com `docker build --check` no projeto scaffoldado, antes e depois: o
  erro acima, e depois `Check complete, no warnings found.`

  O mesmo contexto ausente escondia um segundo defeito: `__SYSTEM_DEPS__`
  nunca era renderizado, então um scaffold `--extras pdf` produzia imagem
  **sem** Pango e sem fontconfig — e o WeasyPrint levanta `OSError` do cffi no
  primeiro render, não no build, então a imagem parecia boa até alguém pedir um
  PDF.

  Verificado ponta a ponta com Docker de verdade: `tempest new` →
  `docker build` (imagem de 343 MB) → container de pé, com
  `/health/liveness`, `/health/readiness` e `/docs` respondendo 200.

  Guard novo em `tests/cli/test_scaffold_placeholders.py`, sobre a **forma** e
  não sobre os cinco nomes: qualquer `__UPPER_CASE__` remanescente em qualquer
  arquivo gerado falha, em cinco combinações de extras. Marcador novo em
  template novo herda a checagem sem ninguém lembrar de estender. Dunder do
  Python (`__init__`, `__all__`) não é falso positivo, e há um caso que prova
  que o padrão casa exatamente os cinco marcadores que shipparam.

- **`:memory:` do SQLite volta a aceitar sessões sobrepostas, sem perder a
  atomicidade do savepoint** ([#180]). Desde a v0.200.0 o manager emite `BEGIN`
  explícito em todo engine SQLite — necessário para o `RELEASE SAVEPOINT` parar
  de comitar — e o SQLAlchemy escolhe `StaticPool` para `:memory:`, que é
  **uma** conexão para todas as sessões. Junto, isso derrubava qualquer par de
  sessões sobrepostas:

  ```text
  sqlite3.OperationalError: cannot start a transaction within a transaction
  ```

  A saída mais óbvia — não emitir o `BEGIN` quando a conexão é compartilhada —
  foi medida e **troca um defeito pelo outro**:

  | `:memory:` | sessão sobreposta | savepoint liberado |
  | --- | --- | --- |
  | com `BEGIN` (v0.200.0 a v0.251.0) | `cannot start a transaction` | atômico |
  | sem `BEGIN` | ok | **vaza** (fica durável) |
  | cache compartilhado + pool normal | ok | atômico |

  Então o manager passa a reescrever a URL in-memory para um banco de **cache
  compartilhado** (`file:<nome-único>?mode=memory&cache=shared&uri=true`) com
  pool normal, mantendo uma conexão viva enquanto existir — banco de cache
  compartilhado é destruído quando a última conexão fecha. Nome único por
  manager, então dois managers continuam isolados (medido).

  Pool informado pelo caller **nunca** é sobrescrito:
  `poolclass=StaticPool` restaura a topologia antiga, incluindo a falha. Guia de
  migração em `docs/migration.md`, seção 0.252.0.

  Novos `is_memory_sqlite_url` e `shared_memory_url`, exportados na raiz. Sete
  casos de regressão em `tests/db/test_connection.py`, cobrindo as duas
  propriedades, a visibilidade entre sessões, o isolamento entre managers e o
  escape hatch.

- **`extract_pdf_text` passa a tratar `max_chars` como teto duro, e a sempre
  devolver texto do documento** ([#175]). O corte só acontecia em fronteira de
  página e o aviso entrava **por cima** do orçamento, então qualquer
  `max_chars` menor que a primeira página devolvia só a anotação. Medido numa
  página de 239 caracteres: para **todo** `max_chars` de 1 a 254 a resposta era
  exatamente a mesma linha de aviso, e de 1 a 45 ela estourava o teto pedido:

  ```text
  max_chars=40  -> len=46  '\n=== DOCUMENT TRUNCATED AFTER PAGE 0 OF 1 ===\n'
  max_chars=100 -> len=46  '\n=== DOCUMENT TRUNCATED AFTER PAGE 0 OF 1 ===\n'
  ```

  Três defeitos numa linha: nenhum caractere de documento, 46 caracteres para
  um teto de 40, e uma "página 0" que não existe. Quem dimensionava o prompt
  por `max_chars` entregava documento vazio ao modelo — e a string não era
  vazia, então `if not text` também não pegava.

  Depois, no mesmo documento:

  ```text
  max_chars=40  -> len=40  'Recibo 4021 Recibo 4021 Recibo 4021 Reci'
  max_chars=61  -> len=61  'Recibo 4021 Recibo 4\n\n=== PAGE 1 OF 1 TRUNCATED MID-PAGE ===\n'
  max_chars=100 -> len=100 'Recibo 4021 [...] Recibo 4021\n\n=== PAGE 1 OF 1 TRUNCATED MID-PAGE ===\n'
  ```

  A ordem de preferência é páginas inteiras enquanto couberem; depois a
  página 1 cortada, anunciada pelo novo `DEFAULT_PARTIAL_PAGE_NOTICE`; e, se
  nenhum aviso couber deixando **um terço** do orçamento para texto, o
  orçamento inteiro vai para o texto — sem aviso e sem marcador de página.

  Duas propriedades que a implementação errou antes de acertar, cada uma com
  guard próprio:

  - **Orçamento maior nunca devolve menos documento.** Renderizar o marcador
    sempre que ele *cabia* criava um segundo degrau: `max_chars=16` devolvia
    16 caracteres de documento e `max_chars=17` devolvia **um**, com o
    marcador comendo o resto. `test_more_budget_never_buys_less_document`
    fixa o degrau único entre 1 e 300 — e falha com
    `AssertionError: [17, 61]` na forma anterior.
  - **`MID-PAGE` só quando o corte foi no meio.** Escolher o aviso pelo ramo
    do código, e não pelo corte, fazia um documento de 3 páginas em
    `max_chars=120` entregar a página 1 **inteira** sob aviso de corte no
    meio. Agora a linha honesta (`AFTER PAGE 1 OF 3`, 106 caracteres) é
    preferida, e no orçamento que segura a página mas não esse aviso o corte
    tira um caractere para o `MID-PAGE` ficar verdadeiro.
    `test_mid_page_is_only_claimed_when_the_page_was_really_cut` varre 1..400.

  `DEFAULT_PARTIAL_PAGE_NOTICE` exportado na raiz e em
  `tempest_fastapi_sdk.pdf`, com o parâmetro `partial_page_notice` para
  sobrescrever. Receita `docs/recipes/pdf.md` reescrita: o bloco que
  documentava o teto furado virou a tabela antes/depois.

- **`BatchScheduler` deixa de quebrar um lote que já estava inteiro na fila**
  ([#176]). O loop cronometrava **toda** retirada, inclusive a de item já
  enfileirado: cada tentativa passava por `asyncio.wait_for` com o que
  restava da janela de `max_wait_ms`. Sob carga, a janela de 20 ms queimava
  entre duas retiradas e o lote saía partido — perda de throughput
  exatamente sob a carga que batching existe para atacar.

  Era isso que fazia `test_coalesces_concurrent_submits` falhar na suíte
  completa e passar isolado. Reproduzido sem tocar no teste, pondo o event
  loop sob carga (uma task que bloqueia em fatias de 5 ms), com os cinco
  itens já na fila:

  ```text
  antes:  batches: [[0, 1, 2], [3, 4]]   batch count: 2
  depois: batches: [[0, 1, 2, 3, 4]]     batch count: 1
  ```

  A correção é ler primeiro o que já está lá: `get_nowait()` em laço, e a
  espera cronometrada só quando a fila esvazia de fato. Item que já chegou
  não depende mais de o agendador ganhar a vez dentro da janela.

  A hipótese de "teste frágil" foi descartada por medição: a asserção
  quebrada mostrava dois lotes de itens **todos já enfileirados**, que é o
  defeito que o teste existe para pegar. Guard novo
  `test_queued_items_coalesce_under_loop_load` fixa o caso com a carga
  embutida, e falha com `assert 2 == 1` na forma anterior.

[#176]: https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/176
[#186]: https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/186
[#187]: https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/187
[#188]: https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/188
[#189]: https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/189
[#180]: https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/180
[#191]: https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/191
[#175]: https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/175

## [0.251.0] — 2026-08-23

### Fixed

- **A assinatura de webhook do Mercado Pago passa a seguir o algoritmo do
  provedor, e um caminho inteiro de notificação deixa de ser rejeitado.** A
  especificação vendorizada não descreve a assinatura
  (`grep -c "x-signature"` devolve `0`), então o algoritmo agora é **portado do
  validador oficial** — `mercadopago/sdk-nodejs`,
  `src/utils/webhook/index.ts`, commit `99857f33` de 2026-08-03, o módulo para
  o qual a documentação do provedor aponta o integrador.

  O defeito principal: **o manifesto omite par ausente**, e nós rendereizávamos
  um template fixo. Entrega sem `data.id` assina `request-id:...;ts:...;` do
  lado do provedor e assinava `id:;request-id:...;ts:...;` do nosso — hash
  diferente, verificação falhando **sempre**, para toda notificação sem
  `data.id`. Quem tratava a rejeição como notificação inválida estava
  descartando notificação legítima.

  Quatro regras do upstream que também faltavam: chave do header é
  case-insensitive (`TS=`, `V1=`); valor só-espaço é valor ausente, o que muda
  quais pares entram no manifesto; `ts` não-numérico é header malformado; e o
  header pode carregar mais de uma versão de hash.

  Digests conferidos contra vetores calculados com `openssl dgst -sha256
  -hmac` — outra implementação de HMAC que não a do Python — e os três casos
  de manifesto fixados byte a byte. **Continua sem medição contra entrega
  real:** portado da implementação do provedor não é o mesmo que verificado
  contra notificação que ele mandou.

- **`uv.lock` fora de versão passa a ser pegável.** A v0.247.0 shippou com o
  lock em `0.246.0` (como v0.236.0, v0.237.0 e v0.238.0 antes dela) e nada
  reclamou. O Makefile documentava que nenhum guard era possível, porque
  `uv run` reescreve o lock em disco antes de qualquer teste ler. Medido hoje,
  e é verdade:

  ```bash
  sed -i '0,/^version = "0.250.0"$/s//version = "0.1.0"/' uv.lock
  uv run python -c "pass"
  grep -A2 '^name = "tempest-fastapi-sdk"' uv.lock | grep version   # 0.250.0
  ```

  Guard possível, então, lendo o **commit** em vez do disco:
  `tests/test_lock_version_guard.py` usa `git show HEAD:uv.lock`, que é
  exatamente o conteúdo que uma tag entrega e que `uv run` não alcança. O
  workflow de release passou a comparar a tag com as **três** versões
  (`pyproject.toml`, `__init__.py`, `uv.lock`), não só com duas.

### Changed

- **Breaking:** `DEFAULT_MANIFEST_TEMPLATE` e o parâmetro
  `manifest_template=` foram removidos — existiam porque o algoritmo era
  desconhecido, e template não expressa a regra de omissão. No lugar,
  `build_manifest(data_id=..., request_id=..., timestamp=...)`.
- **Breaking:** `parse_signature_header` devolve `SignatureHeader`
  (`.timestamp`, `.hashes`, `.digest(versions)`) em vez de
  `tuple[str, str]`, porque um header pode carregar `v1` **e** `v2`.
- `verify_signature` ganha `versions=` (default `("v1",)`, então versão nova
  falha fechada), `tolerance_seconds=` para janela anti-replay e `now=` para
  teste. Migração para `v2` deixa de exigir release.

  Guia de migração: `docs/migration.md`, seção 0.251.0.

### Added

- **`SignatureHeader`, `build_manifest` e `DEFAULT_SIGNATURE_VERSIONS`**
  exportados no namespace do Mercado Pago. `build_manifest` existe para o
  integrador conferir a string exata que a assinatura cobre.
- Documentado que **notificação de QR Code não é assinada** — o upstream diz
  isso explicitamente, e passar essas entregas por `verify_signature` falha
  sempre.

## [0.250.0] — 2026-08-23

### Added

- **O QR do Pix deixa de ser descartado em silêncio** —
  `tempest_fastapi_sdk.integrations.payment.mercado_pago` ganha `PixPayment`,
  `PixPointOfInteraction`, `PixTransactionData`, `create_pix_payment`,
  `get_pix_payment`, `parse_pix_payment` e `PAYMENTS_PATH`.

  ```python
  payment = await create_pix_payment(
      http,
      body={"transaction_amount": 19.9, "payment_method_id": "pix",
            "payer": {"email": "comprador@example.com"}},
      idempotency_key=uuid.uuid4(),
  )
  payment.qr_code          # o copia-e-cola
  payment.qr_code_base64   # PNG em base64
  ```

  O defeito que isto corrige, medido: a especificação vendorizada não declara
  `point_of_interaction` no recurso de pagamento — `grep -c` devolve `2`, e as
  duas ocorrências são o corpo de **requisição** do transaction intent de
  Payouts, onde o objeto tem só `type`. Como o `BaseSchema` é
  `extra="ignore"`, o `Payment` gerado descartava na validação o objeto que a
  API devolve: o QR chegava no corpo HTTP e sumia no modelo, sem erro e sem
  log. Reproduzido em teste nos dois sentidos — o corpo carrega o QR, o
  `Payment` validado não, e `QR_CODE not in payment.model_dump_json()`.

  `create_pix_payment` faz a **mesma** requisição que
  `MercadoPagoClient.create_payment`, mesmos headers e mesma serialização; só
  o modelo de resposta difere. `PixPayment` é uma **vista**: id, status,
  valor, expiração e o objeto do QR, sem importar os schemas gerados — ler um
  QR não paga os 0,76 s de construir os 324 modelos. Verificado que o
  lazy-loading continua intacto após a mudança.

  Nomes e tipos de campo são portados do SDK Node oficial do provedor
  (`mercadopago/sdk-nodejs`, `src/clients/payment/commonTypes.ts`, commit
  `c2d3c6ae` de 2026-07-27), porque a especificação não os descreve. O
  conjunto de campos está fixado por teste, então drift upstream falha em vez
  de divergir calado.

  `financial_institution` aceita `int` **ou** `str` por divergência medida
  entre as duas fontes: o SDK Node tipa `number`, a especificação tipa
  `string` na API de Orders. Levantar `ValidationError` ali perderia o
  pagamento inteiro por um campo que ninguém reconcilia. `e2e_id` **não**
  entrou: a especificação o declara na API de Orders e o SDK Node não o tem
  em `TransactionData`, então prometê-lo aqui seria inventar nome de fio.

### Fixed

- **`openpix-regen` e `mercadopago-regen` entram no `.PHONY`** do Makefile.
  Inofensivo hoje — não existe arquivo com esses nomes — e inconsistente com
  todos os outros alvos.

## [0.249.0] — 2026-08-23

### Added

- **Integração Mercado Pago** —
  `tempest_fastapi_sdk.integrations.payment.mercado_pago`: **324 schemas** e
  **143 operações** geradas da OpenAPI **oficial** do provedor
  (`github.com/mercadopago/openapi`, Apache-2.0, commit `73bc0e49` de
  2026-08-04), mais as peças que a especificação não descreve.

  ```python
  payment = await client.create_payment(
      body={"transaction_amount": 19.9, "payment_method_id": "pix"},
      x_idempotency_key=uuid.uuid4(),
  )
  ```

  Caminho de codegen, como o OpenPix — e não o do Stripe, o que foi decidido
  por medição: a spec inteira do Mercado Pago tem 261 KB contra 847 KB da do
  OpenPix já vendorizada, e os schemas gerados importam em 0,76 s / 107 MB
  contra 0,67 s / 107 MB dos do OpenPix. O argumento que fechou o codegen no
  Stripe (3,3 MB de schemas, 5,8 s, 492 MB) não se aplica.

  Diferente do OpenPix, 142 das 143 operações têm `operationId`, então os
  nomes de método são do provedor: `create_preference`, `create_payment`,
  `get_order`.

  Escrito à mão, porque a spec não diz: `DEFAULT_BASE_URL`,
  `MercadoPagoEvent`, os helpers de dinheiro e a verificação de webhook.

- **`MercadoPagoSettings`** — `MERCADOPAGO_ACCESS_TOKEN` e
  `MERCADOPAGO_WEBHOOK_SECRET`, com `mercado_pago_kwargs()` devolvendo
  `base_url` e o header `Authorization: Bearer <token>` — com prefixo, ao
  contrário do OpenPix, que manda o AppID cru.

  **Sem campo de ambiente, de propósito.** Medido: a spec declara um único
  `servers`. O que separa uma cobrança de teste de uma real é qual token
  você segura, e um enum de ambiente sugeriria uma proteção que não existe.

### Fixed

- **Docstring gerada de nome longo cabia na régua e voltava fora dela.** O
  emissor de schemas quebrava o resumo em uma linha de conteúdo de 88
  colunas, e o `ruff format` puxava o `"""` de fecho de volta para ela sem
  reconferir o orçamento — 91 colunas no arquivo entregue, `E501` em código
  gerado que passa pelos mesmos gates do resto. Medido no enum
  `OrderTransactionPaymentPaymentMethodTransactionSecurityStatus`, de 61
  caracteres. O emissor agora reescreve três colunas mais estreito quando a
  quebra colapsa em uma linha, forçando a segunda, que o formatter deixa em
  paz. Guard na classe `TestSchemaDocstring` de
  `tests/openapi/test_hostile_spec.py`,
  com o formatter de verdade no loop.

- **Path longo de operação ficava acima da régua.** Mercado Pago tem rota de
  113 caracteres (`/instore/qr/seller/collectors/{user_id}/...`); o
  `ruff format` nunca quebra string, então o gerador passou a dividir o
  `path = ...` em fragmentos concatenados na fronteira de `/`, sem cortar
  placeholder no meio.

### Note

Três coisas que a implementação mediu e que valem antes de usar:

- **Dinheiro é em reais**, não em centavos como no OpenPix — 39 propriedades
  monetárias tipadas `number`. `to_cents` recusa fração de centavo em vez de
  arredondar, porque arredondar esconderia a divergência.
- **O QR do Pix não está em `/v1/payments`.** `qr_code`, `qr_code_base64`,
  `digitable_line` e `e2e_id` existem em um único schema,
  `OrderTransactionPayment`, da API de Orders. Como o `BaseSchema` é
  `extra="ignore"`, um Pix criado por `/v1/payments` teria o
  `point_of_interaction` que a API devolve descartado silenciosamente.
- **A assinatura de webhook ainda não foi medida contra o provedor.** A spec
  não a descreve (`grep -c "x-signature"` devolve `0`). O HMAC está testado
  nos dois sentidos e contra adulteração; o que falta é confirmar que o
  manifesto é o mesmo que o Mercado Pago assina. Por isso
  `manifest_template=` é parâmetro: quem medir pode corrigir sem esperar
  release.

## [0.248.0] — 2026-08-23

### Added

- **Módulo `app_errors`** — o lugar onde o frontend deposita o erro que
  quebrou na mão do usuário. `make_app_error_model` (tabela abstrata +
  fábrica), `AppErrorService`, `make_app_error_router` e os schemas.

  Duas regras vêm de um serviço que rodou isso em produção, e cada uma
  existe porque a alternativa falha em silêncio:

  - **Um relato truncado vale mais que um relato perdido.** Payload acima do
    limite da coluna é cortado com marcador, nunca recusado: quem envia é um
    app que acabou de quebrar e não tem caminho de tratamento para um 422, e
    o relato mais interessante — o que veio com stack trace grande — é
    exatamente o que a recusa jogaria fora.
  - **`user_id` vem do token, nunca do corpo.** `AppErrorReportSchema` não
    tem o campo; quem o preenche é o serviço. A separação em dois schemas é
    o que torna impossível atribuir o próprio erro à conta de outra pessoa.

  A tabela: `user_id` nullable (erro de login acontece antes de existir
  usuário — o caso mais difícil de depurar pelo app), FK `ON DELETE SET
  NULL` em vez do `CASCADE` do resto do schema (o relato descreve defeito da
  aplicação, não do usuário), e índice próprio em `created_at`, porque a
  leitura padrão é "mais recente primeiro" numa tabela que cresce sem limite
  natural.

  A listagem é **opt-in**: sem `admin_dependency` a rota `GET` não é
  montada, já que ela devolve stack trace e identificador de aparelho. Quem
  usa o `AdminSite` não precisa dela.

  O corte de datas é semiaberto (`created_at >= start` e `< end + 1 dia`),
  não `func.date(created_at)`: função na coluna descarta o índice.

  Receita: `docs/recipes/app-errors.md`.

- **`FailOpenRateLimitStore`** — em
  `api/middlewares/rate_limit.py`, envolve uma store de contadores para que
  uma indisponibilidade **deixe a requisição passar**, registrando a falha em
  `WARNING`.

  Nasceu de uma medição feita ao escrever a receita acima: com uma store
  cujo `hit` levanta, a exceção **propaga** pelo `RateLimitMiddleware` e o
  chamador recebe erro. Para a maioria dos endpoints isso se defende. Para
  um endpoint de relato de erro é o contrário: o momento em que a store está
  mal é o momento em que os erros disparam, e recusar os relatos ali destrói
  a evidência do incidente que está sendo reportado.

  O teste fixa as duas metades — que o middleware nu propaga, e que o
  wrapper serve a requisição, loga o aviso e continua limitando quando a
  store está saudável.
## [0.247.0] — 2026-08-23

### Changed

- **O gerador de cliente OpenAPI passa a emitir header declarado como
  argumento da chamada**, em vez de descartá-lo com a nota "passe via
  `HTTPClient default_headers`". Header que a especificação declara **na
  operação** é valor por requisição:

  ```python
  await client.create_payment(
      body=PaymentRequest(transaction_amount=19.9),
      x_idempotency_key=uuid.uuid4(),
  )
  ```

  Para a maioria dos headers o descarte era inconveniente; para uma chave de
  idempotência era defeito. `default_headers` manda o mesmo valor em toda
  requisição, então a segunda cobrança seria deduplicada em cima da primeira e
  o cliente veria um pagamento onde fez dois.

  O que a mudança produz, medido na especificação oficial do Mercado Pago
  (109 paths, 143 operações): as notas de construto não modelado caem de **9
  para 5** — as quatro de header somem, sobram as de `multipart`,
  `octet-stream` e `text/csv`. A distribuição também justifica a forma
  escolhida: **127 operações sem header, 15 com um, 1 com dois**, então cada
  um vira um argumento keyword-only nomeado, não uma estrutura de
  agrupamento.

  `None` não manda header vazio: um `X-Idempotency-Key: ` em branco não é o
  mesmo que ausente, e provedor que valida o header responderia 400 em toda
  chamada que não optou por ele.

- **`cookie` continua sendo descartado**, agora sozinho, e a nota diz por quê:
  cookie é estado de conexão, não valor de chamada.

- A docstring gerada para um header opcional dizia "Omitted from the query
  when None". Agora diz "request headers" para header e "query" para query.

### Fixed

- A docstring de `BaseRepository.paginate` anunciava a chave `size` no dicionário
  de retorno; o método devolve `page_size`. Quem seguisse a documentação
  escrevia um `KeyError` — aconteceu ao montar o `AppErrorService`.

### Note

- **O gerado do OpenPix não muda.** `make openpix-regen` produz diff vazio: a
  especificação do OpenPix declara 105 operações e **zero** parâmetros de
  header, então a mudança não alcança o único cliente gerado que o SDK ships
  hoje. O drift test roda igual.

## [0.246.0] — 2026-08-23

### Added

- **Contrato canônico de Pix** — `integrations/payment/base.py`. Um serviço
  que cobra por Pix passa a depender de `PixProvider` e a receber `PixCharge`,
  em vez da forma do provedor.

  A razão é divergência medida entre os provedores que o SDK já traz: OpenPix
  declara centavos dentro de um `float` e tem três estados de cobrança;
  Mercado Pago declara reais dentro de um `float` e tem nove estados de
  pagamento, cinco de pedido e quatro de transação, em três schemas. Escrever
  `charge.status == "COMPLETED"` não acopla o serviço ao Pix — acopla a um
  endpoint de um provedor.

  ```python
  charge = await provider.create_pix_charge(
      PixChargeRequest(amount_cents=1990, reference="pedido-1042")
  )
  ```

  A superfície: `PixProvider` (um `Protocol` puro, como todo seam de provedor
  daqui), `PixChargeRequest`, `PixCharge`, `PixPayer`, `PixPaymentEvent`,
  `PaymentStatus` e `PixEventType`.

  Três decisões que o contrato fixa:

  - **Dinheiro é `int` de centavos na fronteira.** `float` não atravessa o
    contrato; converter para a unidade de cada provedor é do adapter.
  - **Dois estados, não um.** `status` é o canônico; `provider_status` guarda
    a string crua, para log e suporte.
  - **`raw` guarda o payload.** Sem ele, `BaseSchema` sendo `extra="ignore"`
    descartaria em silêncio tudo que o provedor manda além do contrato.

- **`OpenPixPixProvider`** — `integrations/payment/adapters/openpix.py`, a
  primeira tradução. `STATUS_MAP` cobre os três estados do OpenPix e um teste
  percorre o enum gerado falhando em qualquer valor não mapeado, para que uma
  regeneração que acrescente estado quebre a CI em vez de reportar cobrança
  não paga como pendente.

  Duas honestidades que o adapter documenta em vez de esconder: o cancelamento
  do OpenPix responde só `{"status", "id"}`, então os demais campos voltam
  vazios em vez de disparar uma segunda ida à API que o chamador não pediu; e
  `paid_at` só é preenchido pelo caminho de webhook, porque `paidAt` aparece
  nos exemplos da especificação mas não no schema `Charge`.

### Changed

- `PixCharge` e `PixPaymentEvent` desligam o `use_enum_values` que o
  `BaseSchema` liga. Com o default, `charge.status` guardaria a string `"paid"`
  e `charge.status is PaymentStatus.PAID` seria `False` em toda cobrança,
  silenciosamente, enquanto `==` continuaria funcionando. `model_dump(mode="json")`
  segue devolvendo `"paid"`.

## [0.245.0] — 2026-08-22

### Added

- **`OpenPixSettings`** — o mixin que a única integração de pagamento embutida
  não tinha. O SDK ships 20 classes de settings, cada capacidade com credencial
  ganhando a sua mais um helper `*_kwargs()`; OpenPix ficou de fora, então a
  receita ensinava a reescrever `OPENPIX_APP_ID` e a resolução de base URL à
  mão, com um `if` que aceitava qualquer typo como "sandbox".

  Traz `OPENPIX_APP_ID` e `OPENPIX_ENVIRONMENT`, e
  `settings.openpix_kwargs()` devolve `base_url` mais o header de autorização:

  ```python
  client = OpenPixClient(HTTPClient(**settings.openpix_kwargs()))
  ```

  Isso também tira do call site do usuário a pegadinha que a receita precisava
  de um admonition para avisar: o AppID vai em `Authorization` **cru**, sem
  prefixo `Bearer`.

  Duas decisões de desenho, ambas com teste:

  - **Sandbox é o default.** Apontar para produção sem querer cobra dinheiro de
    verdade; apontar para sandbox sem querer falha barulhento. E o campo é
    `Literal["production", "sandbox"]`, então `prod` é `ValidationError` na
    subida do serviço, não uma cobrança no ambiente errado.
  - **O campo é string, não o enum.** `settings` não pode importar o namespace
    `integrations`, que é lazy para `import tempest_fastapi_sdk` não pagar por
    373 nomes gerados. `openpix_kwargs()` importa `OpenPixEnvironment` na
    chamada, então a base URL continua com fonte única — há teste que percorre
    o enum e compara, para uma cópia dos hosts não passar. Medido: importar o
    SDK e a classe carrega **zero** módulo de `integrations`.
## [0.244.0] — 2026-08-22

### Changed

- **Floor: `tempestweb>=0.67.0`** (was `0.66.0`), nos extras `ssr`, `all` e
  `dev`. A v0.243.0 escolheu o piso pela versão em que `create_app(theme=...)`
  passou a **aceitar** o argumento. Aceitar não é pintar: o componente resolve
  a cor na construção e a assa num `style` inline, e o que liga a paleta da
  sessão ao componente é o `tempest-core` 0.12.0, que instala o tema em volta
  da chamada da `view`. A `tempestweb` 0.66.0 pina `tempest-core>=0.11.0`; a
  0.67.0 pina `>=0.12.0`. Com o piso antigo, `build_web_app(theme=...)` podia
  resolver para uma combinação válida em que a página pintava baseline em
  silêncio.

  Medido nas duas pontas, com a mesma seed vermelha:

  | tempest-core | `filled_button` numa sessão com tema | resultado |
  | --- | --- | --- |
  | 0.11.0 | `rgb(88,71,133)` | baseline, tema ignorado |
  | 0.12.0 | `rgb(191,13,13)` | `primary` da paleta |

- **`uv.lock` atualizado para o que está publicado**: `tempestweb` 0.78.0 e
  `tempest-core` 0.14.0 (eram 0.66.0 e 0.11.0). O piso **não** sobe junto: fica
  em `>=0.67.0`, que é a menor versão em que o comportamento documentado
  acontece. Subir o piso até 0.78.0 obrigaria todo consumidor a atualizar sem o
  SDK precisar de nada de 0.68–0.78. Piso é o mínimo que funciona; lock é o que
  a CI exercita.

  Salto de 12 minors medido, não deduzido: suíte inteira verde
  (6144 passed), `mypy` e `ruff` limpos, e a afirmação da doc re-medida nas duas
  pontas — `tempest-core` 0.12.0 (o piso) e 0.14.0 (o atual) resolvem um
  `filled_button` para o `primary` da paleta, e um `theme=` explícito no widget
  vence nos dois.

### Fixed

- **A doc do `theme=` descrevia um workaround que nunca foi necessário.** A
  v0.243.0 documentou que a `view` precisava repassar o tema ao widget
  (`Button(..., theme=app.theme)`) e que os helpers de
  `tempestweb.components` não repassavam. Isso valia para o `tempest-core`
  0.11.0, que era o que estava instalado quando a afirmação foi medida — e
  nunca valeu para o piso que a release entregava. Docstring, as quatro
  receitas e o README passam a descrever o comportamento real: nenhuma
  mudança de call site, e um `theme=` explícito num widget ainda vence.

## [0.243.0] — 2026-08-21

### Added

- **`build_web_app(..., theme=...)`** — a Mode B app can ship its own palette.
  The theme is forwarded to every session's `App`, where `view` reads it back
  as `app.theme`. This is the half a stylesheet cannot cover: a widget bakes
  its resolved colors into an inline `style`, so rebranding only the CSS
  custom properties leaves those at the Material baseline. Needs tempestweb
  0.66.0, where the session and `create_app` learned the same argument; the
  floor moves accordingly.

    A escopo desta entrada foi corrigido na 0.244.0: o parágrafo original
    dizia que a `view` precisava repassar o tema ao widget. Isso descrevia o
    `tempest-core` 0.11.0; a partir do 0.12.0 — piso da `tempestweb` 0.67.0 —
    os componentes que a `view` constrói resolvem contra a paleta sem mudança
    de call site. Um rebrand completo continua sendo as duas metades: este
    argumento mais `tempestweb.html.theme_css(theme)` no head do shell, para
    o que a folha base pinta.

### Changed

- **Floor: `tempestweb>=0.66.0`** (was `0.64.0`), across the `ssr`, `all`
  and `dev` extras — the three that carry it.

## [0.242.0] — 2026-08-21

### Added

- **`BaseJobModel.progress` + `BaseJobModel.stage`, and
  `JobStore.report_progress(job_id, progress=..., stage=...)`** — the
  column a progress bar reads and the one conditional `UPDATE` that moves
  it. A status answers "is it done yet?"; it never answered "how much
  longer?", which is the question of whoever has been watching a screen
  for a minute and a half.

    The statement carries its own guard rails, because the caller cannot:
    `WHERE status = 'running' AND progress < :value`. Two ticks can be in
    flight at once and the one that arrives second is not the one that
    measured second, so the bar can only move forward; and a cancelled job
    is never repainted. No read before the write — the same shape as
    `claim`, for the same SQLite lock-promotion reason.

    `succeed()` sets `progress = 1.0`. `fail()` and `cancel()` leave it
    where it stopped: a reading that died at 40% shows 40%.

    **Migration:** two columns on every table that subclasses
    `BaseJobModel` (`ALTER TABLE <jobs> ADD COLUMN stage VARCHAR(64) NOT
    NULL DEFAULT ''` / `ADD COLUMN progress FLOAT NOT NULL DEFAULT 0`).

- **`PhasePlan` + `Phase` + `ProgressTracker`
  (`tempest_fastapi_sdk.tasks`)** — measured phases turned into the number
  on the row. The two dishonest bars are well known: one that crawls on a
  timer tells a story unrelated to the work, and one that jumps 0 → 100 at
  the end is a spinner wearing a percentage. This is the third way — the
  caller measures its phases, declares them, and the tracker interpolates
  inside the phase that is running while pinning the boundaries to real
  events.

    ```python
    PLAN = PhasePlan.from_seconds({"pdf": 1.0, "table": 36.0, "reading": 23.0})
    tracker = ProgressTracker(store, job.id, plan=PLAN)
    table = await tracker.run("table", backend.chat_structured(msgs, Schema))
    ```

    Medians are the weights, so re-measuring is editing numbers rather
    than recomputing shares. A phase never fills — interpolation stops at
    `ceiling_margin` (0.95) of its span — and `tracker.report(phase,
    done=...)` overrides it when the work can actually count. The tick
    that writes progress is the same tick `run_cancellable` uses to ask
    about cancellation: same row, same interval, one query each way.

- **`JobStore.list_recent(statuses=...)`** — "queued or running" is one
  question. Asking it as two calls makes the two halves disagree the
  moment a worker claims a job between them. Passing both `status` and
  `statuses` raises `ValueError` instead of letting one win silently.

- **`JobStore.watch(emit_on=...)`** — watching only the status yields
  three times for a whole reading (claimed, finished, nothing in
  between), so a bar driven by it never moves. `emit_on=("status",
  "progress", "stage")` yields on every tick the worker wrote. Default is
  unchanged, and an unknown column name raises instead of never firing.

- **`TextGenerator.chat_structured(messages, schema)` + the
  `StructuredTextBackend` protocol** — the local transformers backend now
  answers the same structured call as `OllamaGenerator`, so a service that
  reads documents into schemas types against the protocol and runs on
  either without a line changing at the call site. Both classes are
  verified against the runtime-checkable protocol in
  `tests/genai/test_structured_parity.py`.

- **`stop_event` on local generation and on `run_cancellable`** — a thread
  cannot be cancelled from outside, so cancelling the coroutine that
  awaits `asyncio.to_thread(...)` left the GPU decoding a reply nobody
  would read. `generate`, `chat`, `generate_structured` and
  `chat_structured` accept a `threading.Event` wired to a transformers
  stopping criterion (asked after every token), and `run_cancellable` sets
  it before cancelling the task. Measured both ways in
  `tests/genai/test_structured_parity.py`: with the event the thread
  observes the stop and returns early; without it the thread runs to
  completion after the await is abandoned. A stopped generation raises the
  new `GenerationStoppedError`.

## [0.241.0] — 2026-08-20

### Changed

- **`[tool.pydantic-mypy] init_typed = true`** — in the SDK's own
  `pyproject.toml` and in the `pyproject.toml` written by `tempest new`.
  Declaring `plugins = ["pydantic.mypy"]` and nothing else type-checks **no
  argument of any model constructor**: the plugin runs and synthesizes one
  keyword-only parameter per field, but `init_typed` defaults to `False`, so
  each of those parameters is annotated `Any`. Measured with mypy 2.3.0 and
  pydantic 2.13.4 against a two-field `BaseSchema` subclass, before the
  setting existed here:

  ```text
  note: Revealed type is "def (__pydantic_self__: Probe, *,
    name: Any, age: Any, **kwargs: Any)"
  Success: no issues found in 1 source file
  ```

  The file passing that check contained `Probe(name="x", age="doze")`. With
  the setting on, the same file reports `error: Argument "age" to "Probe"
  has incompatible type "str"; expected "int"  [arg-type]`, and the
  revealed signature carries the real annotations. Pyright and Pylance load
  no plugin — they read the annotations directly and always flagged these
  call sites — so until now the editor and the gate disagreed and the gate
  was the one that was wrong.

  Turning it on cost no fixes here: `mypy tempest_fastapi_sdk` reports
  `Success: no issues found in 409 source files` with `init_typed` enabled.
  The caveat is real but wanted in a service — the setting refuses input
  pydantic would coerce, so a `Decimal` field handed `"1.5"` is now an
  `arg-type` error even though the value still builds as `Decimal('1.5')`
  at runtime. Documented next to the setting in the typing recipe.

  A service scaffolded before this release needs the block pasted into its
  own `pyproject.toml`: mypy reads plugin config **only** from the config
  file, so `tempest check` cannot layer it the way it layers `--strict`.
  Guard: `tests/test_pydantic_mypy_guard.py` fails when either config
  declares the plugin without the setting. Closes #166.

## [0.240.0] — 2026-08-18

### Added

- **`tempest user create --set <column>=<value>`** — the columns a concrete
  `UserModel` adds are now seedable from the CLI. `create` only ever wrote
  `email`, `hashed_password`, `is_admin` and `is_active`, so a model that
  adds one required column of its own — the shape the admin recipe teaches,
  `class UserModel(BaseUserModel)` with columns for the domain — could not
  be seeded at all: the insert went out with `NULL` in the new column and
  the database refused it. Measured against a model adding
  `display_name: Mapped[str] = mapped_column(String(64), nullable=False)`,
  the pre-fix command exited 1 with an uncaught `ConflictException` reading
  `could not insert user: (sqlite3.IntegrityError) NOT NULL constraint
  failed: cli_rich_users.display_name`.

  `--set` is repeatable and validated before anything opens a connection:
  an unknown key exits 2 listing the model's accepted columns, and
  `email` / `hashed_password` / `is_admin` are refused by name, pointing at
  the flag that owns them (a second spelling of the same value would
  otherwise win or lose depending on merge order, and a password in
  `--set` would skip the length check and land in shell history). Values
  are converted to the column's Python type — `bool`, `int`, `float`,
  `Decimal`, `UUID`, ISO-8601 `date`/`time`/`datetime`, enums by value then
  by member name (matching how `TempestEnum` stores them), and JSON for
  `JSON` columns — and a value the type rejects exits 2 naming the column
  and the reason.

  A required column with no default that `--set` did not cover is
  **prompted for** when stdin is a terminal, one prompt per column,
  following what the `--admin`/`--no-admin` prompt already does; without a
  TTY the run exits 2 listing every column the insert would have sent as
  `NULL`. Closes #163.

### Fixed

- **A rejected write in the admin re-renders the page instead of answering
  500.** Every create/edit whose save failed died in the error path — the
  one the operator needs — with `MissingGreenlet`. The save rolls the
  session back, and a rollback expires **every** object in the identity
  map, including the signed-in principal, loaded before the write and not
  touched by it; the error page then read `principal.email` to render the
  header, which is sync IO from async code.

  `expire_on_commit=False` does not cover this, which is worth writing down
  because it is the obvious first guess:
  `SessionTransaction._restore_snapshot` (SQLAlchemy 2.0.51,
  `orm/session.py:1126`) expires all states unconditionally, while the
  `expire_on_commit` test lives in `_remove_snapshot`, the commit path
  (same file, line 1138). The
  four write views that render after a failure now reload what the page
  reads — the principal, plus the parent row for the inline formset —
  awaiting the load so it happens inside the greenlet SQLAlchemy requires.

  Reproduced end to end in `tests/admin/test_form_error_rollback.py`
  against a unique constraint: create, edit, CSV import and the inline
  formset each answered `500` before and answer `400` with the message in
  the form after; neutralize the four reloads and all four tests fail with
  `MissingGreenlet`. The inline and import paths were never reported, and
  were broken the same way. The access policy is part of the reproduction
  on purpose: a policy that reads a principal column (`principal.is_admin`)
  is the second thing the expired object breaks. Closes #164.

- **`exc.message` reports the message that was raised.** `AppException`
  assigned the constructor's message to `detail` only, so `message` fell
  through to the class attribute: `ConflictException(message="Conflict
  creating Widget").message` answered `"Resource conflict"`. Every caller
  reading it off a caught exception reported the generic default — the
  admin form banner showed `Resource conflict` for every integrity error
  instead of naming the model, and the auth flow's activation / password
  reset pages rendered `reason` as `Invalid token` where the service had
  raised `InvalidTokenException(message="token expired")` or
  `"token already used"`. The instance
  attribute is now set from the same value as `detail`; reading `message`
  off the **class** is unchanged, which is what `error_responses()` does to
  build OpenAPI examples.

## [0.239.0] — 2026-08-17

### Added

- **`StageMap` / `StageStatus` (`tempest_fastapi_sdk.tasks`)** — several
  independent stages tracked on the record itself, rather than in their own
  job rows. `JobStore` is right when the work *is* the thing; it is the
  wrong shape when the work decorates a record the interface already
  fetches, because a second table then means a second query and a join to
  render one page.

  Status columns on the record are the usual answer, and they rot in a
  specific way: each stage grows its own copy of "set running" and "mark
  failed", a fix has to land N times, and a copy-pasted stage that kept a
  neighbour's column name compiles, imports, and reports the neighbour's
  state. `StageMap` is that table written once — declare the stages and the
  naming convention, and it resolves the columns and performs the
  transitions.

  What it deliberately does **not** do: declare columns (the project writes
  its own `mapped_column`, so migrations and types stay where a reader
  expects them), touch a database (every operation is a pure mutation, so
  the transitions test without one), or hold preconditions (whether a stage
  may start is domain logic).

  - **`owns(record, stage, expected)`** is the check before writing a
    result, and it is about ownership rather than cancellation: a status
    that is no longer `expected` covers both the user cancelling and a
    newer run having restarted the stage, and writing is wrong in both.
  - **`cancel` is partial and idempotent**, returning
    `(cancelled, ignored)` — a screen polling for status routinely asks to
    cancel something that finished a moment ago.
  - **Marking without a `result` keeps the previous one**, so cancelling a
    regeneration preserves the answer already there instead of making
    cancellation worse than never asking.
  - **Two stages resolving to the same column is refused at construction**,
    and an `error=` paired with a non-failure status is refused too.

### Fixed

- **`make release` now stages `uv.lock`.** Bumping seds `pyproject.toml`,
  which invalidates the lock's record of this package's own version.
  `make check` refreshes it as a side effect — `uv run` re-locks before
  running anything — but nothing staged it, so every tagged commit since
  the release target was rewritten shipped a lock one version behind its
  pyproject: v0.236.0, v0.237.0 and v0.238.0 all drifted.

  No guard covers it, and the Makefile says why: any test asserting the two
  agree runs under `uv run`, which repairs the lock on disk before the test
  can read it, so the guard could never fail. Measured by corrupting the
  lock and watching a bare `uv run python -c pass` put it back.

## [0.238.0] — 2026-08-17

### Added

- **Per-subject AI usage accounting (`tempest_fastapi_sdk.genai`)** —
  `BaseAIUsageModel`, `AIUsageStore`, and the `UsageTotals` / `ServiceUsage`
  / `DailyUsage` / `SubjectUsage` result types. `GenAIMetrics` already
  publishes token counters to Prometheus, which answers "how is the fleet
  doing right now"; it cannot answer "which account burned the budget last
  month", because the series is per-process, resets on deploy, and carries
  no user dimension — adding one would make the cardinality unusable. This
  is the other shape: one row per paid call, queried with ordinary SQL.

  `record` stores a call, `record_duration` stores local inference (which
  costs wall-clock rather than tokens), and `totals` / `by_service` /
  `per_day` / `top_subjects` are the aggregations an admin screen draws.
  Each opens its own short session, like `JobStore` — the recording call
  runs inside a worker as often as inside a request.

  Three decisions each pinned by a test:

  - **A call the provider did not price writes no row.** `usage=None` means
    the response carried no usage, which is not the same as a free call; a
    zeroed row would count toward the call count and the active-user count
    while contributing nothing. `TokenUsage(0, 0, 0)` writes nothing
    either — it is what a short-circuit that never reached the model looks
    like.
  - **The price is never stored.** Cost is computed from the tokens at read
    time, so correcting a price fixes the whole history instead of leaving
    old rows priced with a number nobody remembers setting.
  - **Cost comes back unrounded.** Any fixed precision is wrong at some
    scale: token prices live around `0.0001` per 1000, so rounding to cents
    reports zero for nearly every single call, while a monthly total wants
    cents. Formatting stays at the boundary. (Found by a test: the first
    implementation rounded to four decimals and reported `0.0004` for a
    real `0.00042`.)

  Duration rows carry `service=NULL` and are excluded from token sums and
  per-service shares, so local inference never becomes a 0% slice on every
  chart.

## [0.237.0] — 2026-08-17

### Added

- **Cancelling a job that is already running
  (`tempest_fastapi_sdk.tasks`)** — `JobStatus.CANCELLED`,
  `JobStore.cancel` / `is_cancelled` / `cancellation_watch`, and
  `run_cancellable` + `StageInterruptedError`. The queue could start long
  work and report on it; it could not stop it. Nothing in TaskIQ — or in
  any broker the SDK speaks — offers "kill the task with this id", so this
  is cooperative: the request side writes `CANCELLED` and answers
  immediately, and the worker reads that at checkpoints and gives up.

  `run_cancellable` is the checkpoint that runs **during** the work rather
  than between steps. It races the coroutine against the predicate on an
  interval and, when the predicate says stop, cancels the coroutine for
  real — an in-flight request is aborted and the worker is free within the
  poll interval, rather than finishing a call whose result nobody wants.
  Verified by asserting the wrapped coroutine received `CancelledError`,
  not merely that the wrapper stopped waiting: remove the cancel and three
  tests fail.

  Details that each cost somebody a discovery:

  - **`cancel` is idempotent.** Nothing to stop answers `None` instead of
    raising — an unknown id, or a job already done, failed or cancelled. A
    double-click, or a click that races the job finishing on its own, is
    not an error.
  - **A failing predicate does not discard the work.** The predicate
    usually reads a database; treating an unreachable one as "cancelled"
    would throw away good work during exactly the incident where redoing
    it costs the most. The round is skipped and the next one retries.
  - **`succeed` refuses to land on top of a cancellation**, raising the new
    `JobCancelledError` — a subclass of `JobAlreadyFinishedError`, so
    existing handlers keep working, and a distinct type because the two
    mean opposite things: the parent says your concurrency is wrong, this
    one says the system did what it was told.
  - **`CANCELLED` is terminal but is not a failure.** It joins
    `TERMINAL_JOB_STATUSES` — the poll stops, the payload is dropped — but
    an interface highlighting `FAILED` should leave it alone, and an alert
    that pages on failures should not fire.
  - **It only works on genuinely cancellable awaitables.** Work handed to
    `asyncio.to_thread` is not: cancelling the coroutine abandons the
    wrapper while the thread runs to completion, still holding the CPU.
    The docstring says so, and says what to do instead.

## [0.236.0] — 2026-08-17

### Added

- **`OpenAICompatGenerator` (`tempest_fastapi_sdk.genai`)** — text
  generation against any `/chat/completions` endpoint. The wire format is
  the common denominator — DeepSeek, Groq, Together, OpenRouter, Mistral,
  vLLM's server, TGI's OpenAI route and Azure all document an
  OpenAI-compatible one — so a single client reaches them by swapping
  `base_url` and `model`. That list is what those providers advertise, not
  a matrix this repo runs: the tests drive an `httpx.MockTransport` and pin
  the request built and the response read, not any live provider. It satisfies `TextBackend` —
  whose own docstring names "a hosted API" as the case it exists to be
  filled with — so it drops into `make_genai_router` and `AIChatPipeline`
  beside the local `TextGenerator` and the `OllamaGenerator`. Until now the
  SDK could only talk to a local daemon or local weights.

  Built on the SDK's `HTTPClient`, so every call gets retry with backoff, a
  per-host circuit-breaker and `X-Request-ID` propagation. No vendor SDK:
  this is one POST with a bearer token, and a wrapper would bring its own
  bounds and buy nothing. An empty `api_key` raises at construction rather
  than deferring to a 401 from inside the first background job.

  `extra_body` carries provider extensions without a branch per vendor, and
  is merged **under** the computed fields so it cannot redirect a call to
  another model. The case it was built for, reported by a downstream
  service against DeepSeek and not reproduced in this repo: a hybrid
  reasoning model with thinking on by default spends `max_tokens` on the
  hidden chain before the real content, so a budget sized for the answer is
  exhausted there and the completion comes back empty —
  `extra_body={"thinking": {"type": "disabled"}}` turns it off.

- **`TokenUsage` (`tempest_fastapi_sdk.genai`)** — what a call cost, as the
  provider reported it. `OpenAICompatGenerator.generate_with_usage` /
  `chat_with_usage` return it alongside the text; plain `generate` still
  returns a bare `str`, because that is what the protocol declares —
  exposing usage is an addition, not a change. `GenAIMetrics` already
  observed tokens for Prometheus, but that is per-process and ephemeral;
  this is the value you can persist for per-user accounting.

  Two decisions worth knowing: `total_tokens` is carried from the response
  rather than recomputed, because no provider is obliged to bill
  `input + output` (cached-prefix discounts show up exactly that way); and
  a response with no `usage` yields `None`, never a zeroed usage — "the
  provider did not say" is a different statement from "the call was free".
  `__add__` sums a job made of several calls.

- **`generate_structured_list` + `StructuredFormatError`
  (`tempest_fastapi_sdk.genai`)** — generate a list of schema items,
  retrying when the output holds no usable array. Retrying at the same
  temperature is close to pointless, since greedy decoding is
  deterministic and attempt two reproduces attempt one; the first attempt
  stays greedy and each retry adds `temperature_step`.

  Only a **structural** failure spends an attempt. An array that parses
  with one malformed item is handled by `skip_invalid` instead, and `[]` is
  a success — the model answered, and the answer is no items — so neither
  burns a generation. `StructuredFormatError` subclasses `ValueError` and
  carries the last raw completion, so the log says what the model wrote
  rather than only that it was wrong. Takes any backend exposing
  `generate(prompt, config=...)`.

## [0.235.0] — 2026-08-17

### Fixed

- **A cold `SpeechToText` shared by two callers now builds one model, not
  two.** `load()` was `if self.is_loaded: return` followed by the
  constructor — idempotent from one thread, and it is called from
  `_transcribe_sync`, which runs on a worker thread. The only thing
  between two callers was `asyncio.Semaphore(max_concurrent)`, which
  admits `max_concurrent` of them by construction. Measured with two
  threads against a counting stub: **two** `WhisperModel` constructions,
  not one. `self._model` keeps only the last one written, so the loser's
  copy stays referenced by nothing the class can reach and its weights sit
  in the process for as long as the garbage collector has not run.

  The guard is a `threading.Lock`, because the callers to exclude are
  threads. The `is_loaded` test lives inside it rather than being repeated
  outside as a fast path — one uncontended acquire measured at ~157 ns
  (CPython 3.11, 2M iterations) against a transcription measured in
  seconds, and double-checked locking is the shape this bug already hid in
  once. `unload()` takes the same lock and now also clears the batched
  pipeline, which holds the model and would otherwise keep it alive.

  `tests/genai/audio/test_stt_loading.py` reproduces it: restore the
  pre-fix `load()` and the constructor count asserts `2 == 1`.

- **A stray trailing bracket no longer breaks structured extraction.**
  `_extract_json` sliced from the first `{` to the **last** `}`. One extra
  `}` after an otherwise perfect payload made `json.loads` reject a
  decodable answer, and retrying reproduced it exactly, because the defect
  was in the cut and not in the generation. (The shape comes from a
  downstream service that hit it against DeepSeek; this release does not
  measure how often a given model produces it.) Extraction now scans for a balanced span, skipping brackets
  inside string values. A non-greedy regex was not the fix: it stops at
  the first closer and truncates any payload containing a nested object or
  array.

### Added

- **`extract_json_list` and `parse_structured_list`
  (`tempest_fastapi_sdk.genai`)** — the array counterparts of
  `parse_structured`, which looks for an object and so does not serve a
  prompt asking for "a list of items". `parse_structured_list` validates
  each item, with `skip_invalid=True` to drop one malformed entry instead
  of losing the other nine. `extract_json_list` returns the raw list, or
  `None` when there is no decodable array — a signal deliberately distinct
  from `[]`: `None` means "generate it again", `[]` means "the model
  answered, and the answer is no items". A caller that cannot tell them
  apart either retries a call that succeeded or gives up on a recoverable
  formatting slip.

- **`SpeechToText(batch_size=...)`** — decodes VAD-detected speech spans
  in parallel through faster-whisper's `BatchedInferencePipeline` instead
  of one after another. Same model, same weights, same precision; only the
  scheduling of the decode changes, at the cost of peak memory
  proportional to the value. Requires `vad_filter=True` (the VAD produces
  the spans a batch is made of) and raises `ValueError` at construction
  when it is off, rather than at the first transcription.

- **`SpeechToText(cpu_threads=..., num_workers=...)`** — CTranslate2's
  `intra_threads` and `inter_threads`, previously not reachable through
  the SDK. Defaults (`0`, `1`) are faster-whisper's own, so nothing
  changes for callers who do not set them.

- **`SpeechToText(condition_on_previous_text=...)`** — feeds each window
  the previous window's text as context. Defaults to `True`, which is
  faster-whisper's own default, so upgrading does not silently change
  anybody's transcripts. Turning it off is what you want alongside
  `batch_size`: it serializes spans that would otherwise decode in
  parallel, and it is the documented path by which one bad span
  contaminates the ones after it.

- **`SpeechToText.transcribe(on_progress=...)`** — called with
  `(seconds_done, total_seconds)` as the decode advances. faster-whisper
  returns a generator, so without it a long file spends minutes
  indistinguishable from a hang. It runs on the worker thread, which the
  docstring says, so a callback touching the event loop needs
  `loop.call_soon_threadsafe`.

## [0.234.0] — 2026-08-16

### Fixed

- **A generated model can be constructed with its Python field names
  again — under a type-checker, not just at runtime.** Every aliased field
  was emitted as `Field(alias="correlationID")`, and `alias` is the field
  specifier that renames the parameter in the `__init__` a checker
  synthesizes. Measured with basedpyright against the published 0.233.0
  wheel:

  ```python
  ChargePayload(correlation_id="order-1", value=1990)
  # error: No parameter named "correlation_id"
  # error: Argument missing for parameter "correlationID"
  ```

  It ran fine — `populate_by_name=True` is set on every generated model —
  so nothing failed until a consumer ran pyright, Pylance or basedpyright.
  Measured again with `validate_by_name`, which does not change it either;
  mypy accepts both spellings regardless, which is how it shipped.

  The wire name is now written twice, as `validation_alias` (reading) and
  `serialization_alias` (writing). All three directions are pinned by
  tests: construction by Python name, validation from the provider's
  spelling, and `model_dump(by_alias=True)` still emitting it. The same fix
  applies to the two hand-written schemas that carried an alias
  (`push`, `webpush`), and `tests/test_alias_guard.py` fails if a plain
  `alias=` comes back — one keyword, indistinguishable at runtime, that
  nothing else in the gate would notice.

## [0.233.0] — 2026-08-16

### Fixed

- **A component whose top level is `oneOf`/`anyOf` no longer generates an
  empty model that drops the caller's data.** The parser flattened `allOf`
  and ignored the other two combinators, so a named component built from
  `oneOf` became a class with **no fields** — and `BaseSchema`'s
  `extra="ignore"` then discarded everything passed to it. Measured against
  the vendored OpenPix specification: a charge created with

  ```python
  ChargePayload(
      correlation_id="order-1",
      value=1990,
      customer=CustomerPayload(name="Ana", email="ana@example.com"),
  )
  ```

  went on the wire as `{"correlationID": "order-1", "value": 1990.0,
  "customer": {}}`. No exception, no warning — the customer simply was not
  there. Two OpenPix components were affected, `CustomerPayload` and
  `PaymentCreatePayload`, and both are request payloads.

  A component like that now becomes one of two things:

  - **untitled object variants merge into a single model** carrying every
    variant's properties, required only where *every* variant requires it
    (OpenPix spells "name plus one of taxID, email or phone" as three
    near-identical objects, so `CustomerPayload` is one model with `name`
    required);
  - **titled variants, or a discriminator, stay apart**, one class each,
    named from the specification's `title`, with the component name kept as
    a union alias over them (`PaymentCreatePayload =
    PaymentCreatePayloadPixKey | PaymentCreatePayloadQrCode | ...`).

- **`allOf: [<oneOf component>, {flag}]` no longer collapses to the flag.**
  Merging asked the union for `properties`, a union has none, and the whole
  payload vanished: OpenPix's `POST /api/v1/payment` body was generated as a
  model with the single field `autoApprove`, unable to express any payment
  at all. The `allOf` is now distributed into the union, so each variant
  keeps its own fields **and** gains the flag.

- **Every generated OpenPix name is now listed in the package's `__all__`**,
  so a strict type-checker accepts it as a re-export. Measured with
  basedpyright against the installed wheel: `from
  tempest_fastapi_sdk.integrations.payment.openpix import ChargePayload`
  reported *"ChargePayload" is not exported from module ... Import from
  "...openpix.schemas" instead*. The `TYPE_CHECKING` wildcard made the
  symbol visible but did not mark it re-exported. mypy accepted the wildcard
  either way, which is why this shipped. The list is generated by
  `scripts/regen_openpix.py` and pinned by the drift suite.

- **A long `# openapi: unsupported` note no longer overruns the line
  budget.** Continuation lines are re-emitted with a four-character `"#   "`
  prefix that the wrapping did not reserve, so a long enough note produced
  generated code failing `E501` in the consumer's own lint run.

### Changed

- **OpenPix: 373 schemas** (was 358). The 15 new names are the payment
  variants, the union aliases over them, and the customer address the old
  empty models had swallowed.
- Two generated enums were renamed, because the classes that register them
  are now generated earlier: `PaymentType` ->
  `PaymentCreatePayloadPixKeyType` and `PaymentDestinationAliasType` ->
  `PaymentCreatePayloadPixKeyDestinationAliasType`. Same members, same
  values. See the [migration guide](docs/migration.md).
- `from tempest_fastapi_sdk.integrations.payment.openpix import *` now pulls
  every generated name (and pays the lazy load) instead of the eleven
  hand-written ones. Importing names explicitly is unaffected.

### Documentation

- **The OpenPix recipe was rewritten as two task-shaped pages.**
  [OpenPix (Pix via Woovi)](docs/recipes/openpix.md) now teaches the whole
  charge flow — suggested layered architecture, opening a charge, the
  verified webhook, the API read-back that actually authorizes releasing,
  reconciliation, expiry and refunds — and
  [OpenPix (subscriptions and plans)](docs/recipes/openpix-subscriptions.md)
  covers recurring billing: why the plan lives in your database and the
  subscription in theirs, `RECURRENT` vs `PIX_RECURRING` (Pix Automático),
  the cycle charges, instalments, cancellation, and the access-expires-by-date
  state machine. Both in PT-BR and EN-US.

## [0.232.0] — 2026-08-16

### Added

- **Stripe integration** (`tempest_fastapi_sdk.integrations.payment.stripe`),
  no extra required.

  ```python
  from tempest_fastapi_sdk.integrations.payment.stripe import (
      StripeClient,
      stripe_http_client,
      to_minor_units,
  )

  client = StripeClient(stripe_http_client("sk_test_..."))
  intent = await client.payment_intents.create(
      {
          "amount": to_minor_units("199.90", "brl"),
          "currency": "brl",
          "metadata": {"order_id": "1042"},
      }
  )
  ```

  - `StripeClient` over the SDK's `HTTPClient`, with a generic
    `StripeResource` covering create / retrieve / update / delete / list
    plus `auto_paginate` for nine resources (customers, payment intents,
    refunds, products, prices, subscriptions, invoices, Checkout sessions,
    events). Every write carries an `Idempotency-Key` — a retried charge
    without one bills the customer twice.
  - `stripe_http_client` pins `Stripe-Version`, so an account upgraded in
    the dashboard cannot silently change response shapes under a service
    that never changed.
  - Money that respects **zero-decimal** and **three-decimal** currencies:
    `to_minor_units` / `from_minor_units` / `currency_exponent`. ¥1050 is
    `1050`, not `105000`.
  - Webhook verification over the payload Stripe actually signs
    (`f"{t}.{body}"`, not the body), with a replay window, secret
    rotation, and `sign_payload` so tests do not re-derive it wrongly. An
    unknown event type never fails the route.
  - `StripeEvent` — 265 event types, generated from the specification
    (`make stripe-fetch` / `make stripe-regen`) with a drift test.
  - `StripeError` surfacing `type` / `code` / `decline_code` / `param` /
    `request_id`, instead of a bare status.

- **`form_encode`** (`tempest_fastapi_sdk.form_encode`) — flattens a
  nested payload into the bracket notation form-encoded APIs read
  (`metadata[user_id]=42`, `items[0][price]=price_123`). Booleans go out
  lower-case, `None` is dropped rather than sent empty (an empty string
  *clears* a field on these APIs), `Decimal` keeps its exact text.

- **The OpenAPI generator now reads the request body's media type.** An
  operation declaring `application/x-www-form-urlencoded` emits
  `data=form_encode(payload)`; JSON operations are unchanged. Before this,
  a client generated against Stripe had **100% of its writes rejected** —
  all 588 of them declare form encoding and none declares JSON.

### Fixed

- **The OpenAPI parser aborted on re-entrant specifications.** Note sinks
  were removed from the stack **by equality**, and two sinks that have
  collected nothing are both `[]` — so a nested parse (a field's type
  resolving a `$ref`, whose component's fields open their own sinks)
  removed the wrong one and the outer block died with
  `ValueError: list.remove(x): x not in list`. Removal is now by identity.
  Parsing Stripe's specification is what surfaced it; the regression test
  fails on the previous code.

### Notes

The Stripe client is **hand-written**, unlike OpenPix, and the reason is
measured on the `2026-07-29.dahlia` specification: generating the full
surface produces a `schemas.py` of 3.3 MB / 81k lines whose import costs
**5.8 s and 492 MB of RSS**, and slicing by resource does not help because
`/v1/prices` alone reaches 864 of the 1440 component schemas. What still
comes from the specification — API version, base URL, the event list —
comes through `scripts/regen_stripe.py`, which also records the numbers.

Closes #156.

## [0.231.0] — 2026-08-16

### Added

- **Unified push** (`tempest_fastapi_sdk.push`). The SDK shipped Web Push and
  nothing for mobile, so a product with a site and an app carried two
  notification APIs and a caller that had to know which kind of device it was
  talking to. Now it says "notify this user" once.

  ```python
  from tempest_fastapi_sdk import (
      BaseRepository,
      DeviceService,
      FCMTransport,
      PushPayloadSchema,
      WebPushTransport,
  )

  service: DeviceService[DeviceModel] = DeviceService(
      BaseRepository(session, model=DeviceModel),
      [WebPushTransport(dispatcher), FCMTransport(auth=firebase)],
  )
  result = await service.notify_user(user_id, PushPayloadSchema(title="Hi"))
  ```

  - `PushDispatcher` — a one-method `Protocol`, in the shape `UploadStorage`
    uses for storage, so the service depends on the contract and a fake
    transport in tests inherits nothing.
  - `WebPushTransport` adapts the existing `WebPushDispatcher`;
    `FCMTransport` delivers to iOS and Android through
    `firebase_admin.messaging`, reusing the `[firebase]` extra and the
    service account `FirebaseAuth` already loaded (`FCMTransport(auth=...)`,
    via the new `FirebaseAuth.app` property).
  - `BaseDeviceTokenModel` / `make_device_token_model` — one table for
    browsers and phones: web rows keep `p256dh` / `auth`, mobile rows carry
    the FCM registration token, `token` is unique and `last_seen_at` is
    refreshed on every re-registration.
  - `DeviceService` — idempotent registration (a handset that changes hands
    moves to the new user), concurrent fan-out where **one device failing
    never aborts the others**, and unified pruning: HTTP 404/410 on the web
    and `UnregisteredError` / `SenderIdMismatchError` on FCM both delete the
    row.
  - `PushFanoutResult` reports `delivered` / `pruned` / `failed` / `skipped`.
    `skipped` is not `pruned`: a web-only service keeps its iOS rows instead
    of deleting devices it merely cannot reach yet.
  - `make_push_router` — `POST /register`, `POST /unregister`, and the
    existing `GET /vapid-public-key`.
  - `PushSettings` joins `WebPushSettings` and `FirebaseSettings` and
    resolves a real trap: both declare `enabled`, so composing them by hand
    lets the MRO silently pick the Web Push one and a mobile-only service
    reads `enabled is False` with FCM configured. It exposes `web_enabled` /
    `mobile_enabled` and an `enabled` that means "can notify anyone".
  - Device tokens never reach a log line or a response — `mask_push_token`
    keeps a 12-character SHA-256 prefix, the same treatment `_mask_endpoint`
    gave Web Push endpoints.

  **Nothing breaks.** `tempest_fastapi_sdk.webpush` is untouched — same
  module, same names, same behaviour — and `tests/webpush/` passes without a
  single edit, which is what proves it.

  Measured, not deduced (`firebase-admin` 7.5.0): `Message.token` is marked
  deprecated in favour of `fid`, but the two encode **different wire fields**
  (`{"token": ...}` vs `{"fid": ...}`), so an FCM registration token stays in
  `token` and a test asserts the serialized message still carries it. Also
  `APNSConfig`, not `ApnsConfig`.

  One deliberate divergence from issue #157: FCM's `InvalidArgumentError` is
  **not** treated as "device gone". FCM raises it both for a bad token and
  for a malformed payload, and pruning on it would delete a user's whole
  fleet the first time a notification body is wrong.

  Recipe: [Push (web + mobile)](docs/recipes/push.md). Closes #157.

## [0.230.0] — 2026-08-16

### Added

- **Firebase ID token verification** (`tempest_fastapi_sdk.auth`, extra
  `[firebase]`). For the shape the SDK did not cover yet: the client signs in
  with Firebase and the API receives an ID token it has to prove is genuine.

  ```python
  from fastapi import APIRouter, Depends

  from tempest_fastapi_sdk import FirebaseAuth, FirebaseIdentity

  firebase = FirebaseAuth(credentials_path="credentials.json")
  router = APIRouter()


  @router.get("/me")
  async def me(
      identity: FirebaseIdentity = Depends(firebase.get_identity),
  ) -> dict[str, str]:
      """Return the verified caller."""
      return {"uid": identity.uid, "email": identity.email or ""}
  ```

  - `FirebaseAuth` owns the idempotent app initialization every service
    otherwise re-implements: `firebase_admin.initialize_app()` raises
    `ValueError` on the second call, so two instances with the same
    `app_name` now share one app, and distinct names talk to distinct
    projects. Verification runs in `asyncio.to_thread`, since Google's
    verifier is synchronous.
  - The credential comes from `credentials_json` (inline, for deployments
    without a mounted volume), `credentials_path`, or the environment's
    application-default credential — in that order. Configuration failures
    raise `FirebaseCredentialError` (a `RuntimeError`, not an
    `AppException`, because it happens at construction).
  - `FirebaseIdentity` is a frozen dataclass (`uid`, `email`,
    `email_verified`, `phone_number`, `provider`, plus the full `claims`),
    so handlers never receive a raw `dict[str, Any]`.
  - Dependencies: `get_identity` and `get_uid` (strict),
    `get_optional_identity` (soft — `None` instead of raising, pairs with
    `require_authenticated`).
  - `FirebaseUserResolver[UserT]` maps a verified identity onto the
    project's own user object; a resolver answering `None` is a 401, not an
    empty response.
  - Each failure gets its own `code`: `FIREBASE_TOKEN_MISSING`,
    `FIREBASE_TOKEN_INVALID`, `FIREBASE_TOKEN_EXPIRED`,
    `FIREBASE_TOKEN_REVOKED`, `FIREBASE_UNAVAILABLE`, and
    `FIREBASE_USER_DISABLED` at **403** — the caller proved who they are, so
    it is the one failure the soft variant still raises.
  - `FirebaseSettings` (`FIREBASE_PROJECT_ID`, `FIREBASE_CREDENTIALS_PATH`,
    `FIREBASE_CREDENTIALS_JSON`) + `firebase_kwargs()`, which drops empty
    values instead of forwarding an empty path.

  Measured, not deduced: on `firebase-admin` 7.5.0, `ExpiredIdTokenError`
  and `RevokedIdTokenError` are **subclasses** of `InvalidIdTokenError`, so
  the `except` ordering is what keeps the three codes distinct — a
  parametrized test pins it. A clean venv with the extra installs **33
  packages, 52 MB**, which is why `[firebase]` stays out of `[all]` and the
  import is lazy: `import tempest_fastapi_sdk` and
  `from tempest_fastapi_sdk.auth import FirebaseAuth` both work without it,
  and only construction raises `ImportError` naming the extra.

  The suite runs offline — a locally generated RSA key builds a syntactically
  valid service account (`initialize_app` never contacts Google), and
  `verify_id_token` is patched on the real `firebase_admin.auth` module so
  the mapping is exercised against the genuine exception classes. One test
  patches nothing and feeds a non-JWT to the real verifier, which rejects it
  structurally before any network call.

  Recipe: [Auth Firebase (ID token)](docs/recipes/firebase-auth.md).
  Closes #155.

## [0.229.0] — 2026-08-15

Everything here came out of building a real document service on the SDK
(a public-procurement budget tool) and finding the same four gaps.

### Added

- **Spreadsheet generation** (`tempest_fastapi_sdk.spreadsheet`, extra
  `[spreadsheet]`). The counterpart of `tempest_fastapi_sdk.pdf`: a PDF is what
  you send when the numbers are final, an `.xlsx` is what you send when the
  recipient has to sort, filter and re-total them.

  ```python
  workbook = new_workbook("Orçamento")
  writer = SheetWriter(
      workbook["Orçamento"],
      columns=[
          Column("Item", width=48, wrap=True),
          Column("Valor", width=18, number_format=BR_CURRENCY_FORMAT),
      ],
  )
  writer.title_block(["PREFEITURA MUNICIPAL", "Pregão 1/2026"])
  writer.header_row()
  writer.write_row(["Serviço de instalação", Decimal("2930.00")])
  writer.total_row(["Total", Decimal("2930.00")])
  data = workbook_to_bytes(workbook)
  ```

  `SheetWriter` owns the row cursor, so no call site tracks `(row, column)`
  pairs — insert a line at the top and nothing below it has to be renumbered.
  `Column` declares title, width, number format and alignment **once**, which
  is what stops the format from drifting between the first row and the
  thousandth.

  The `BR_*` number formats embed the pt-BR language code (`[$R$-416]`). This
  is not cosmetic: Excel resolves a plain `#,##0.00` with the locale of
  *whoever opens the file*, so a workbook built in São Paulo renders
  `1,234.56` on an en-US machine. The mask pins the convention inside the
  document.

  `SheetStyle` is plain data — hex colours and integer sizes, no `openpyxl`
  objects — so a project's theme is definable, testable and comparable without
  the extra installed. `new_workbook` also drops the stray `Sheet` tab that
  `openpyxl` always creates.

- **PDF text extraction** (`tempest_fastapi_sdk.pdf.extract_pdf_text` /
  `extract_pdf_pages`, extra `[pdf-read]`). The inverse of the renderer, and
  the first step of every "hand a document to a model" pipeline.

  Text layer only, no OCR — and a scanned PDF returns `""` rather than a blank
  document, because handing a model an empty prompt is how a confident answer
  gets invented about a page nobody read. Page boundaries survive as markers,
  so an extracted figure can cite where it came from, and truncation at
  `max_chars` cuts at the last **complete** page and says so in the text.

  It is a separate extra from `[pdf]` on purpose: rendering pulls WeasyPrint
  plus Pango and fontconfig from the system, and a service that only reads
  should carry none of that.

- **Brazilian currency helpers** (`tempest_fastapi_sdk.utils.currency`, no
  extra). `parse_currency_br` reads an amount as a document prints it
  (`"R$ 2.930,00"`) back into an exact `Decimal`, accepting both separator
  conventions; it returns `None` — not zero — when no digit is present, which
  keeps "no price printed" distinguishable from "printed R$ 0,00".

  `format_currency_br`, `format_percent_br`, `format_quantity_br` and
  `quantize_money` render for prose without going through `locale` (which is
  process-global, container-dependent and not thread-safe). Money is quantized
  `ROUND_HALF_UP`, matching Brazilian accounting practice rather than
  `Decimal`'s banker's rounding, which is what lets a generated document
  reproduce a hand-built one cent for cent.

- **Decimal ratio fields** — `DecimalRatioField` (`0..1`),
  `DecimalPercentField` (`0..100`) and `SignedDecimalRatioField` (`<= 1`,
  may go negative). The SDK shipped `RatioField` / `PercentField` annotated on
  `float`, so `Decimal("0.28")` was silently coerced and the first
  multiplication by a `PriceField` raised `TypeError` — or, once someone
  "fixed" it with a cast, moved a cent. Use the decimal ones wherever the
  fraction multiplies money.

### Fixed

- **`OllamaGenerator.generate_structured` returned an empty result on
  reasoning models.** It posted to `/api/generate` with the schema in
  `format`; on a harmony model such as `gpt-oss` the daemon answers `200 OK`
  with a non-zero `eval_count` and an **empty** `response`, because the reply
  lands in a reasoning channel that endpoint does not surface. Measured
  against `gpt-oss:20b`: `/api/generate` without `format` works,
  `/api/generate` with `format` returns empty, `/api/chat` with `format`
  returns the JSON in `message.content`. The call now goes to `/api/chat`,
  where non-reasoning models behave identically, and raises `ValueError`
  instead of returning nothing when the content comes back empty.

  It also takes a `system=` argument now. An instruction concatenated ahead of
  a long document is ignored by the model — measured: 0 items extracted from a
  24k-character tender with the instruction in the same turn, 20 items with it
  in its own system turn.

### Changed

- `pdf.formatting.format_cents` now delegates to
  `utils.currency.format_currency_br`. Same output (a test fixes the two
  against each other across the sign boundary); one implementation.
## [0.228.0] — 2026-08-15

### Added

- **`BaseJobModel` + `JobStore` — long work with a status the interface
  can show.** A queue hands a call to a worker; it answers none of what
  the person waiting is asking: has anything picked this up, is it
  running, what did it produce, why did it stop. TaskIQ's result backend
  is keyed by task id and holds a return value — what a screen needs is a
  **row**. This is the symmetric half of the transactional outbox: that
  one is a message to publish, this one is work to execute. Closes #151.

  `BaseJobModel` is abstract — subclass it, pick a `__tablename__`, and
  get `kind` / `status` / `params` / `payload` / `result_id` / `error` /
  `attempts` / `max_attempts` / `started_at` / `finished_at` on top of
  the usual `BaseModel` columns. `JobStore[JobT]` wraps it with
  `enqueue`, `claim`, `succeed`, `fail`, `get`, `list_recent`,
  `reclaim_stale` and `watch`, each in its own short transaction because
  its callers are a request handler, a worker that grinds for minutes,
  and a screen polling every couple of seconds.

  Four decisions worth naming:

  - **`claim` is a conditional `UPDATE`, not a read-then-write.** Two
    workers racing for one id cannot both win; the loser gets `None`
    rather than an error. Measured under a barrier that releases both
    contenders together: the naive read-then-write shape does not hand
    the job to both, it raises `sqlite3.OperationalError: database is
    locked` — lock promotion, the one contention `busy_timeout` cannot
    wait out (v0.227.0). The test asserting that is in the suite, so the
    race test cannot pass against an implementation with no conditional
    update.
  - **`succeed` / `fail` drop the payload**, or the table of finished
    jobs becomes a pile of documents.
  - **`reclaim_stale()` frees the job whose worker died** — the failure a
    queue cannot see, since the task is gone but the row is not. Bounded
    by `max_attempts` so a job that kills its worker is closed as
    `FAILED` (with `STALE_JOB_ERROR`) instead of readmitted forever. Two
    disjoint `UPDATE`s and no `SELECT` first, for the same lock-promotion
    reason.
  - **`watch()` holds no session between ticks.** It is the
    `while True: sleep; get` every application writes by hand, minus the
    part that is easy to get wrong — a watcher holding its transaction
    open is a watcher blocking the worker it is watching.

  `JobNotFoundError` and `JobAlreadyFinishedError` are a `LookupError`
  and a `RuntimeError`, not `AppException` subclasses: the store runs in
  the worker as often as in a request, and a worker has no HTTP status to
  answer with. Translate at the boundary with `not_found_exception(...)`.
  New recipe: "Jobs (trabalho longo com status)" / "Jobs (long work with
  status)".

## [0.227.0] — 2026-08-15

Three defects an application only meets on the day it grows a worker.

### Added

- **SQLite runs in WAL, with a 30-second busy timeout.** Web process and
  `taskiq worker` on one `app.db` is where development SQLite stops
  working: in the default rollback journal a reader and a writer exclude
  each other. Measured across two processes, one holding a read
  transaction open while the other inserts — `journal_mode=delete`: the
  writer waits out the whole `busy_timeout` and fails with
  `sqlite3.OperationalError: database is locked`; `journal_mode=wal`: it
  commits immediately. `AsyncDatabaseManager` now applies both to every
  SQLite engine it builds, tunable through `sqlite_wal=` /
  `sqlite_busy_timeout=` and through `DATABASE_SQLITE_WAL` /
  `DATABASE_SQLITE_BUSY_TIMEOUT` on `DatabaseSettings`, and ignored on
  every other backend. `enable_sqlite_wal` is public for engines built by
  hand. The docstring names the contention WAL does **not** fix — a
  transaction that reads first and writes later, where promoting the lock
  fails at once and no timeout applies. Closes #152.

- **`TaskQueue.on_startup` / `on_shutdown` — the worker's lifespan.**
  FastAPI's `lifespan` does not run in the worker, so there was nowhere
  to open the database, the message broker or an HTTP client, and nowhere
  to close them: the worker worked by accident on lazy connects and never
  disposed its pool. Hooks take no arguments (TaskIQ's state stays
  reachable at `queue.broker.state`), accept sync or async callables, and
  register on TaskIQ's `WORKER_*` events by default so the web process —
  which has its own lifespan — is left alone; `scope="client"` /
  `"both"` covers the rest. For resources that already speak
  `connect`/`disconnect`, `TaskQueue.rabbitmq(url, resources=[db,
  broker])` (or `queue.use(db)`) does both ends in one line, opening left
  to right and closing right to left. The new `LifecycleResource`
  protocol is what `AsyncDatabaseManager`, `MessageBroker` and
  `AsyncMinIOClient` already satisfy. The in-memory broker runs both
  sides' events in one process, so worker hooks are testable without a
  worker. Closes #153.

- **`not_found_exception(...)` / `conflict_exception(...)`.** A
  domain-identifiable 404 was ~30 lines differing from the next
  aggregate's by three strings — and its signature carried a trap:
  `BaseRepository` raises the configured class as
  `exception_class(message=...)`, so the constructor one writes first
  (taking the record id, because the id is what the caller holds) turns
  **every repository miss into a `TypeError`** — a 500 where the 404
  belongs. The factories return classes that accept both call shapes,
  declare `code` in the class body (so `error_responses()` documents them
  and `InheritedErrorCodeWarning` stays quiet), file the identifier under
  a named `details` key, derive the class name from the code, and take
  message templates so the wording stays in the project's language.
  Closes #154.

### Changed

- **Floors raised: `tempest-cli>=0.3.0`, `tempestweb>=0.64.0`.**
  `tempest-cli` 0.3.0 bundles `ruff`, so `tempest lint` / `fix` /
  `format` / `fmt-check` run without a second install, and its tool
  lookup no longer picks a dead pyenv shim. `tempestweb` 0.64.0 fixes
  component keys derived from the caller's key — two fields of the same
  kind on one screen were indistinguishable to the event router, so an
  edit could apply to the wrong field.

- **`tempestweb` and `tempest_core` type-check for real.** Both ship
  `py.typed` as of `tempestweb` 0.64.0, so their `ignore_missing_imports`
  overrides are gone from `pyproject.toml`; mypy now checks the SDK's
  `ssr` and `ui` layers against the real signatures instead of `Any`.
  Clean on the first run — 380 source files, no issues.

- **The repository documents the constructor contract where it bites.**
  `not_found_exception=`'s own docstring now states that the class is
  instantiated as `exception_class(message=...)` and what happens when it
  is not accepted, instead of leaving the note further up the class
  docstring.

## [0.226.0] — 2026-08-15

### Changed

- **The quality gate moved to its own package.** `ruff`, `mypy` and
  `pytest` never had anything to do with FastAPI, but reaching
  `tempest check` meant installing the whole SDK: 38.7 MB of required
  dependencies (SQLAlchemy alone is 20.6 MB) and ~0.5 s of import time
  per invocation — measured with `python -X importtime`, of which the
  module doing the work accounts for 16 µs. Closes #150.

  `lint`, `fix`, `format`, `fmt-check`, `type`, `test`, `check` and
  `pr-prompt` now live in
  [`tempest-cli`](https://pypi.org/project/tempest-cli/), a package whose
  only runtime dependency is `typer`. The SDK declares it as a
  dependency and mounts the same commands through
  `tempest_cli.main.register_commands(app)` — **one implementation, two
  CLIs**, so they cannot drift.

  **Nothing changes for existing projects.** `tempest check` is the same
  command with the same flags; `[tool.tempest] typing_strictness` is
  read the same way; and `from tempest_fastapi_sdk.cli.lint import ...` /
  `from tempest_fastapi_sdk.cli.pr_prompt import ...` keep resolving —
  those modules re-export the shared implementation.

  Whoever does not use FastAPI can now install the gate alone:

  ```bash
  uv add --dev tempest-cli
  tempest-cli check
  ```

- **`[tool.tempest]` is now read by each key's owner.**
  `typing_strictness` belongs to the gate and moved with it;
  `commands` (the project management commands mounted under `tempest`)
  is an SDK concept and stays here, read by the new
  `tempest_fastapi_sdk.cli.config.load_project_commands()`. The table in
  your `pyproject.toml` is unchanged — both keys keep working exactly as
  before.

  The one visible consequence: `TempestConfig` no longer carries a
  `commands` attribute, since the shared config has no business knowing
  about FastAPI management commands. Code reading
  `load_tempest_config().commands` should call `load_project_commands()`
  instead.

- `tempest_fastapi_sdk.openapi` formats generated code through
  `tempest_cli.resolve_tool("ruff")` — the same PATH / `uv run` lookup
  the gate uses, promoted from a private helper rather than duplicated.

## [0.225.0] — 2026-08-15

### Added

- **`OllamaGenerator.chat_structured(messages, schema)`** — structured
  output from a **message list**, so the instruction stays in a `system`
  turn and the content being read stays in `user`. Closes #148.

  ```python
  invoice = await generator.chat_structured(
      [
          {"role": "system", "content": "Extract the invoice fields."},
          {"role": "user", "content": document},
      ],
      Invoice,
  )
  ```

  Only `generate_structured(prompt, schema)` existed, and there was no
  path through `chat()` to reach it: `chat()` routes every keyword into
  `options`, and Ollama reads `format` at the **top level** — so a schema
  passed to `chat()` is ignored silently, returning `200 OK` with free
  text. `chat_structured` posts `format` where the daemon reads it, and
  raises `TypeError` on an explicit `format=` keyword rather than letting
  it be dropped.

- **`build_web_app(..., shell=...)` and `make_web_app_router(...,
  shell=...)`** — the app shell is the only part of the HTML an
  application owns, and until now it was whatever `tempestweb build`
  emitted: `lang="en"`, no description meta, no favicon, nowhere for a
  CSP nonce. Closes #149.

  ```python
  app = build_web_app("dist/server", shell=my_shell)
  ```

  Accepts a `str` (the document), a `Path` (read from disk) or a callable
  invoked **per request** — which is what makes a per-response nonce
  possible; the callable may declare a `Request` parameter or none. On
  the static router the override also answers the SPA fallback, so a deep
  link renders the same document. A `str` carrying no `<` is rejected:
  that is a path written where a document was expected, and it would
  otherwise serve a blank page with no error anywhere.

## [0.224.0] — 2026-08-15

### Added

- **`ui` layer** (`tempest_fastapi_sdk.ui`, extra `[ssr]`). An interface
  layer that sits **beside** `controllers` / `services` / `schemas` in a
  service, and mirrors it one-to-one in the SDK: `ui.pages` (one class
  per screen), `ui.layout` (structural containers), `ui.components`
  (reusable pieces), `ui.forms`, `ui.css`. It answers only "what does
  this look like" — a page receives data a controller already loaded and
  never performs I/O.

  ```python
  app.include_router(make_css_router(app_stylesheet()))


  class HomePage(BasePage):
      total: int

      def body(self) -> Widget:
          return Card(title="Vendas", children=[Text(content=f"{self.total}")])
  ```

  Bundled components: `Card`, `Alert`, `DataTable` (columns, headers and
  cell text derived from the row schema), `Pagination` +
  `pagination_for(BasePaginationSchema)`, `EmptyState`, `NavBar`;
  layout: `Shell` (header/main/footer landmarks) and `Grid` (CSS grid,
  auto-fitting without a media query). All of them render class names
  rather than inline styles, so the whole look lives in one stylesheet.

- **Forms generated from Pydantic schemas** (`tempest_fastapi_sdk.ui.forms`).
  The schema that validates the request also describes the form.

  ```python
  result = await parse_form(SignupSchema, request)
  if not result.ok:
      return html_response(
          form_for(SignupSchema, action="/signup",
                   values=result.values, errors=result.errors),
          title="Cadastro", status_code=422,
      )
  ```

  `form_for` emits an accessible `<form>` — `<label for>` bound to the
  control, `aria-invalid` on a failing field, `aria-describedby` pointing
  at hint and message, and native `minlength` / `max` / `step` /
  `pattern` derived from the field metadata. `parse_form` handles what
  HTML expresses differently: an unchecked checkbox means `False`, a key
  the body never carried stays out of the payload (so the schema default
  applies and a required field reports `Field required` against itself),
  an empty optional becomes `None`, and repeated keys or textarea lines
  become a `list`. `FormResult` carries per-field errors plus the raw
  input, so the re-render keeps what the reader typed with no extra
  server-side state. `exclude=` + `extra=` keep server-owned values out
  of the browser's reach. Per-field overrides through
  `json_schema_extra={"ui": {...}}`; `form_spec_for` + `render_form`
  expose the generated form as plain data to patch before rendering.

  Nested models and binary fields raise `UnsupportedFieldError` rather
  than rendering a control that cannot round-trip.

- **Typed CSS** (`tempest_fastapi_sdk.ui.css`). `Rule` + `Media` +
  `StyleSheet` cover what an inline `Style` cannot: selectors,
  pseudo-classes and media queries.

  ```python
  sheet = StyleSheet(
      theme=ThemeTokens(),
      rules=[
          Rule(".card", declarations={"background": theme.color("surface")}),
          Media.min_width(768, [Rule(".card", declarations={"padding": "24px"})]),
      ],
  )
  app.include_router(make_css_router(sheet))
  ```

  `ThemeTokens` adapts `tempest_core`'s `TokenSet` — the same one the
  client renderer uses — into CSS custom properties: 39 colour roles in
  light and dark (`prefers-color-scheme` **and** `[data-theme]`), plus
  spacing, shape, typography and motion scales. `make_css_router`
  renders the sheet once, at construction, and serves it with a
  content-derived `ETag` (a matching `If-None-Match` gets a `304`).
  `StyleSheet.cls("crad")` raises instead of silently rendering an
  unstyled element, and `app_stylesheet()` composes tokens + reset +
  form rules + component rules in one call.

- `html_response` gained `stylesheets=` (rendered as
  `<link rel="stylesheet">`) and `head=` (raw markup appended to the
  document head).

- `tempest new --extras "ssr"` and `tempest generate --src` now scaffold
  the whole `src/ui/` layer (`styles.py`, `layout/base.py`,
  `components/stat.py`, `pages/home.py`) plus `api/routers/web.py`.

- **Every scaffolded project now ships a `CLAUDE.md`** — the rules an AI
  agent (or a new teammate) must follow so services stay alike: the
  dependency direction between layers, the exact seven-step order for a
  new domain (schema → model → repository → service → controller →
  provider → router), how to raise SDK exceptions instead of building
  responses, the pagination envelope, the `ui` layer rules, a table of
  what **not** to reimplement, the code conventions, the commands, and a
  definition of done that ends in `tempest check`.

  `tests/cli/test_scaffold_runtime.py` keeps it honest: it writes the
  document's own example domain into a scaffolded project and runs it —
  `POST` returns 201, a duplicate returns 409 carrying
  `code="PRODUCT_NAME_TAKEN"`, and the paginated listing comes back as
  `{items, total, page, page_size, pages}`. Every SDK symbol the
  document imports is resolved against the package, so a rename fails
  here rather than in someone's project.

### Changed

- `Page` moved from `tempest_fastapi_sdk.ssr.page` to
  `tempest_fastapi_sdk.ui.pages`, where it sits next to the components,
  layouts and forms a page is built from.
  `from tempest_fastapi_sdk.ssr import Page` keeps working and returns
  the very same class.

### Notes

- Measured, and pinned in `tests/ui/test_core_contract.py`: under the
  `tempestweb` HTML renderer, `tempest_core`'s `Form` renders as a
  `<div>`, `Input` renders **without a `name`** (so nothing is
  submitted) and `Dropdown` / `TextArea` render as empty `<div>`s —
  those widgets belong to the reactive client. `ui.forms` therefore
  emits form elements through the documented `tag`/`attrs` escape hatch.
- `Style` validates colours as hex literals, so a token reference
  (`var(--t-color-primary)`) goes in `Rule.declarations`, never in
  `Style`.

## [0.223.0] — 2026-08-14

The voice stack was built as four increments (0.219.0-0.222.0) and this
release is the first of them to reach PyPI. They are kept as separate
entries below because they document distinct pieces of work, but only
this version exists as a package: the concurrency defect fixed here was
introduced in 0.219.0, so publishing the increments in order would have
shipped it four times before the fix.

### Added

- **Face detection and recognition** (`tempest_fastapi_sdk.faces`, extra
  `[faces]`). `FaceRecognizer` finds faces, aligns each one and turns it into a
  comparable vector; `compare_faces` says whether two vectors are the same
  person.

  ```python
  recognizer = FaceRecognizer()
  faces = await recognizer.recognize("group.jpg")     # largest first
  same = compare_faces(faces[0].embedding, faces[1].embedding)
  ```

  **Measured separation** on a six-person group photo with the default pack:
  the same face across a re-encoded (0.962), rotated (0.952) and tightly
  cropped (0.877) version, against a maximum of **0.180** across all fifteen
  pairs of different people. The default threshold of 0.45 sits in the middle
  of a gap of nearly 0.7, which makes it a safe default rather than a tuned one
  — the opposite of the speaker-diarization case, and the docs say so because
  the intuition does not transfer.

  `detect()` returns boxes, scores and landmarks and **no vectors**, for the
  questions that do not need biometrics: is there a face, how many people,
  where to crop.

  `embed_face()` is the enrolment shape and **refuses** an image with no face
  or with only a tiny one. At enrolment a bad vector is not a bad answer, it is
  a permanently wrong profile.

- **Two model packs, defaulting to the small one.** Measured: `buffalo_s` is
  16 MB and 15 ms against `buffalo_l` at 191 MB and 54 ms, and its separation
  differs by 0.02 at either bound. Twelve times smaller for that is not a trade
  worth refusing; the large pack is one keyword away for small, dim or turned
  faces. Weights are fetched by `ensure_models()`, which belongs in a build
  step.

### Changed

- **`insightface` was measured and rejected.** It packages this pipeline, and
  it installs **558 MB across 24 packages** — and the `opencv-python` it
  requires links against five GL libraries, so a slim container would need
  system graphics libraries to recognise a face. Running the same ONNX models
  directly costs `onnxruntime` + `numpy` + `pillow`, which the SDK already
  carries, and no system libraries at all. The price is the SCRFD decoding and
  the alignment transform, which are closed-form geometry rather than a long
  tail of correctness.

  Rejected before it: `facenet-pytorch` (pins `torch<2.3.0`, capping every
  consumer), `deepface` (TensorFlow) and `face-recognition` (dlib, compiled).

- **`faces` and the audio decode path carry their real types** instead of
  `Any`: `Image.Image` for every image parameter and return, and
  `npt.NDArray[...]` for the arrays. Verified load-bearing rather than
  cosmetic — mypy now rejects `align_face("not-an-image", ...)` with
  `incompatible type "str"; expected "Image"`, where `Any` accepted it.

### Fixed

- **A tight crop is detectable.** A 112×112 portrait whose face touches the
  frame returned **zero** detections; the detector needs context around a face
  and an already-tight crop has none to give. Every image now gets a 20% margin
  before inference, and one below 320 px is upscaled first — measured to take
  that case from zero faces to one, matching the original at 0.877.

- **A face too small to embed says so** rather than returning a vector that
  describes its own upscaling. Below 40 px on a side the face comes back with
  an empty `embedding`, so a caller can tell "nobody recognisable" from "no
  face at all".

- **Two concurrent transcriptions no longer read each other's speaker
  count.** `ConversationTranscriber.transcribe(num_speakers=...)` assigned the
  count onto the shared diarizer before using it, so two overlapping requests
  both saw whichever was written last — the caller who asked for two speakers
  was clustered into five, with no error anywhere. Reproduced, then pinned:
  `tests/genai/audio/test_concurrency.py` asserts `[2, 5]` and returned
  `[5, 5]` against the old code. The count is now a per-call argument,
  `SpeakerDiarizer.diarize(audio, num_speakers=...)`, and the transcriber
  passes it through instead of mutating the object.

  The same path also called `diarizer.unload()` to make the new count take
  effect, dropping ~46 MB of loaded models out from under any request running
  beside it. It is gone; a test asserts it is never called.

  The engine carries its cluster count in its own config, so `set_config` and
  `process` are now held under a `threading.Lock`: two worker threads doing
  that pair on one engine would interleave, which `max_concurrent > 1` makes
  reachable.

- **An enrolment recording is decoded once.**
  `VoiceEmbedder.embed_for_enrollment` decoded the audio to measure its
  duration and then decoded it again to embed it. Decoding is the expensive
  half of that call, so a 30 s enrolment was resampled twice for one vector.
  The length check moved into the worker that already has the samples.

## [0.222.0] — 2026-08-14 (not published; folded into 0.223.0)

### Changed

- **`SpeakerDiarizer` estimates the speaker count by default**
  (`num_speakers="auto"`). Naming the participants is no longer required to get
  a correct split.

  Diarization has to answer two questions and only one is easy: turn boundaries
  come from the segmentation model, but *how many distinct voices* comes from
  no model at all. Measured on a twelve-recording benchmark whose count is
  correct **by construction** — turns cut from distinct recordings, rather than
  from the diarizer's own output, which is the circular benchmark this work
  started with and discarded:

  | method | exact | mean error |
  | --- | --- | --- |
  | threshold 0.5 | 4/10 | 1.90 |
  | threshold 0.7 | 8/10 | 0.40 |
  | threshold 0.9 | 8/10 | 0.20 |
  | **automatic** | **12/12** | **0.00** |

  `num_speakers=<int>` remains exact and cheapest — it skips the second pass —
  and stays the recommendation when the count is known. `num_speakers=None`
  keeps threshold-only clustering, now documented as the weakest option.

- **The clustering threshold is no longer the primary knob**, so v0.219.0's
  "no threshold is right on all recordings" caveat is resolved rather than
  documented: the estimate does not use one.

### Added

- **`estimate_speaker_count`** — spectral gap over the turn-embedding affinity
  matrix. A threshold asks *how close is close enough*, whose answer moves with
  the microphone, the language and the room; the gap asks *where does this
  matrix split*, which is a property of the recording.

- **`affinity_report`** — the eigenvalues, the gaps and the winning margin
  behind an estimate. "It said four" is not something a person can act on;
  two near-equal gaps mean the answer could as easily have been three, which is
  the moment to pass the count explicitly.

### Fixed

- **A monologue is no longer reported as a conversation.** The gap search
  always finds a split, including where there is none: a real six-turn
  dictation came back as two speakers, which would turn a voice note into a
  conversation.

  A single voice is *uniformly* similar to itself — even its most distant pair
  of turns is close — while two voices produce genuinely distant pairs.
  Measured across the twelve recordings, the 10th percentile of pairwise
  similarity was 0.490-0.667 for one speaker and -0.080-0.166 for more than
  one, and `SOLO_COHESION_P10` sits in the middle of that gap with at least
  0.14 of margin on each side. It is a property of the bundled model's
  similarity scale, not a universal constant, and the docstring says so.

## [0.221.0] — 2026-08-14 (not published; folded into 0.223.0)

### Added

- **`make_voice_router`** — opt-in FastAPI routes for the voice pipeline:
  `POST /voice/transcribe` (upload a recording, get it back split by speaker)
  plus `POST` / `GET` / `DELETE` `/voice/profiles`.

  The listing and deletion routes exist because the person owns this data, not
  as a convenience. And the listing **never returns the embedding**: they need
  to know a profile exists, not to receive a copy of their own biometric
  template over HTTP.

- **`tempest voice models|diarize|transcribe`** — the same pipeline from a
  shell. `diarize` never loads Whisper, which makes it the quick way to check
  whether the speaker count and threshold are right before paying for
  transcription. `models` fetches the weights, which belongs in a build step.

### Changed

- **`profiles=` without `current_user_id=` raises at wiring time.** Enrolling
  or erasing against a user id taken from the request body would let any caller
  write biometric data into somebody else's account. Failing when the app is
  built is the only useful moment to fail — in production it is already
  shipped.

- **Uploads are bounded (25 MiB default) while reading, not after.** Audio is
  held in memory to be decoded, so an unbounded upload exhausts the worker; and
  measuring after reading the whole body means the oversized upload already
  occupied the memory it was supposed to be denied.

## [0.220.0] — 2026-08-14 (not published; folded into 0.223.0)

### Added

- **Voice identification — recognising *who* a speaker is**
  (`tempest_fastapi_sdk.genai.audio`). Diarization separates speaker 0 from
  speaker 1; this puts a name on them by matching against enrolled profiles.

  `VoiceEmbedder` turns a recording into a voiceprint, `VoiceProfileService`
  enrols, identifies and deletes, and `ConversationTranscriber.transcribe(
  identify_with=..., session=..., user_ids=...)` labels a whole conversation in
  one call.

  Measured end to end with real voices: enrolling from one turn and identifying
  a **different** turn by the same person scored 0.687 and 0.734, while an
  unenrolled speaker returned `None`.

  Identification runs **once per speaker cluster**, on that cluster's longest
  turn — every turn of a cluster is the same voice by construction, so
  embedding each would pay the model N times for one answer and could label two
  turns of one person differently.

- **`BaseVoiceProfileModel` + `make_voice_profile_model`** — the enrolled
  voiceprint with its consent record. The raw audio is deliberately absent:
  nothing here writes a recording, and the vector cannot be played back, so a
  leak of this table costs far less than a leak of the enrolment clips.

### Changed

- **Enrolment refuses to run without recorded consent.** A voiceprint
  identifies a person like a fingerprint template; under the LGPD it is
  sensitive personal data (Art. 5, II) needing specific, highlighted consent
  (Art. 11, I), which general terms of service do not provide. So
  `consent_reference` is required, a blank one raises `ConsentRequired`, and
  the evidence is stored on the same row as the vector rather than trusted to a
  flag somewhere else. This is not configurable.

- **`forget_user()` is a method, not an example.** Deleting biometric data is
  an unconditional right (Art. 18, VI) and must not depend on each project
  writing the `WHERE` clause correctly.

- **A profile written by another embedding model is never compared.**
  Similarity between vectors from different models is a number with no meaning,
  and a meaningless number that looks like a score is worse than an error.
  `model_name` is recorded per row and `stale_profiles()` finds the profiles a
  model swap invalidated — otherwise those people silently stop being
  recognised with nothing to indicate why.

- **Enrolment refuses audio below 3 seconds.** A profile built from one word
  matches almost anyone, and unlike a bad match, a bad *profile* keeps being
  wrong until somebody deletes it.

## [0.219.0] — 2026-08-14 (not published; folded into 0.223.0)

### Added

- **Speaker diarization — who spoke when** (`tempest_fastapi_sdk.genai.audio`,
  extra `[genai-diarization]`). Transcription already answered *what was said*;
  `SpeakerDiarizer` cuts a recording into turns and clusters the voices, and
  `ConversationTranscriber` joins the two into a transcript attributed line by
  line.

  ```python
  transcriber = ConversationTranscriber(
      stt=SpeechToText(model_size="small"),
      diarizer=SpeakerDiarizer(num_speakers=2),
  )
  conversation = await transcriber.transcribe("call.wav", language="pt")
  print(conversation.transcript())     # "Falante 0: ...\nFalante 1: ..."
  print(conversation.by_speaker())     # {0: "...", 1: "..."}
  ```

  **The engine is `sherpa-onnx`, measured against the alternative.**
  `pyannote.audio` 4.0.7 declares 21 runtime dependencies — `torch>=2.8`,
  `lightning`, `matplotlib`, three OpenTelemetry packages and a client for its
  vendor's paid API — and its pretrained pipeline is gated on HuggingFace, so a
  container build needs a token and a manually accepted licence. `sherpa-onnx`
  declares one dependency, runs on ONNX Runtime with no PyTorch, and its models
  are open. On a 57-second four-speaker recording it separated all four at RTF
  0.125 on CPU.

  **The recording is transcribed once, not once per turn**, then attributed by
  timeline overlap. Handing Whisper two-second clips throws away the context it
  uses for punctuation and costs an inference per turn. The cost of that choice
  is stated rather than hidden: a Whisper span straddling a speaker change
  lands wholly on whoever holds more of it, and speech the diarizer dropped as
  too short comes back as `speaker = -1` instead of vanishing.

  Models are not bundled — 46 MB does not belong in a wheel most services
  install for other reasons. `ensure_models()` fetches them once, honoring
  `TEMPEST_VOICE_MODEL_DIR` so a deployment can bake them into an image layer.

- **`DiarizedTranscription` / `SpeakerTurn`** with `transcript()` (labelled
  lines) and `by_speaker()` (everything one person said).

### Changed

- **The clustering threshold defaults to 0.9, not sherpa-onnx's 0.5.** Swept
  over three reference recordings, no single value is correct on all of them:
  0.5 produced seven clusters for four speakers, while 0.9 is right on two of
  three and fails by *merging* rather than by inventing participants. The
  measurement table is in the docstring and the recipe, and the docs say
  plainly that passing `num_speakers` is the difference between right and
  wrong, not an optimization.

- **`sherpa-onnx-core` is declared explicitly in the extra.** `sherpa-onnx`
  keeps its compiled libraries there and its wheels declare the dependency, but
  its sdist metadata does not — and uv locks from the sdist, so `uv sync`
  installed the wrapper alone and the first call died with
  `ImportError: libonnxruntime.so: cannot open shared object file`. Naming it
  is what makes the extra installable; it is not redundant.

### Fixed

- **Speaker indices are dense.** The clustering returns whatever its internal
  bookkeeping produced — a four-speaker recording yielded `0, 1, 2, 4, 7, 8,
  9`. Passed through, the gaps read as participants who were present and
  silent, and `num_speakers` stopped matching the largest index. Turns are now
  renumbered `0..n-1` in order of first appearance.

## [0.218.0] — 2026-08-13

### Added

- **PDF generation from HTML templates** (`tempest_fastapi_sdk.pdf`, extra
  `[pdf]` = `weasyprint` + `jinja2`). Every service eventually issues a
  document, and what breaks is never the rendering: it is the printed total
  disagreeing with its own lines, the amount in words being wrong, the table
  header vanishing on page 2, the logo failing to load without anyone noticing.

  `PdfRenderer` renders an HTML string, a template, or a **typed document**;
  `make_pdf_router` serves them over HTTP; `tempest pdf list|schema|render`
  drives them from a shell.

  ```python
  pdf: bytes = await PdfRenderer().render_document(
      ReceiptDocument(
          issue_date=date(2026, 8, 13),
          issuer=Party(name="Acme LTDA", document="12345678000195"),
          payer=Party(name="Ana Souza"),
          amount_cents=125000,
          reference="consultoria de julho/2026",
      ),
  )
  ```

  **Five bundled documents, each with a Pydantic schema**: `ReceiptDocument`,
  `QuoteDocument`, `ReportDocument`, `ContractDocument`, `VoucherDocument`. The
  schema is the reason bundling templates is worth anything — an HTML file
  alone tells you nothing about the keys it needs, so the first missing field
  shows up as a blank space in a signed document. Totals are **computed from
  the items**, never accepted, and a discount above the subtotal is refused
  rather than printed as a negative price.

  **The engine is WeasyPrint**, for CSS Paged Media: repeating headers,
  `página X de Y`, controlled page breaks. A browser-based renderer costs a
  150 MB browser in the image; a pure-Python one cannot paginate a report and
  would have pinned `reportlab<5` on every consumer. WeasyPrint's own
  requirements are lower bounds only.

  **Brazilian formatting is part of the module, not the templates**:
  `format_cents`, `format_date`/`format_date_long`, `format_document`,
  `format_quantity`, and `valor_por_extenso` — conventional on a *recibo*, and
  exactly the thing that gets written from memory, wrongly. Every connector
  case is pinned by test, including `um milhão de reais` versus `dois milhões
  e quinhentos mil reais`.

  **Reproducible output needs `SOURCE_DATE_EPOCH`.** WeasyPrint writes no
  creation date and no document identifier, but the embedded font subset stamps
  a timestamp into its `head` table, so two renders seconds apart differ —
  measured as three hashes across three runs of one container. Pinning
  `SOURCE_DATE_EPOCH`, the convention `fontTools` honors, makes the output
  byte-identical across processes; the test asserts that across a real process
  boundary, because two renders inside one process match trivially. Even
  pinned, the bytes depend on the font and WeasyPrint versions, so a hash does
  not travel between images.

  Rendering is CPU-bound, so every call goes through a worker thread behind a
  semaphore (`max_concurrent`, default 4) and the event loop never stalls.

- **`AssetPolicy` — templates fetch nothing by default.** An HTML renderer
  resolves URLs on the page's behalf, so a document carrying user data turns
  `<img src>` into both a local-file read (`file:///etc/passwd`) and an SSRF
  (`http://169.254.169.254/`). `data:` URIs always pass because they fetch
  nothing; a local directory has to be named, and the check is on the
  **resolved** path so neither `../` nor a symlink escapes it. Refusal is loud:
  the fetcher carries `_fail_on_errors`, so the render aborts at the first
  refusal instead of producing an invoice with a hole where the logo was.
  `strict_assets=False` restores the lenient behavior and logs what it dropped.

  `Branding.logo_data_uri` accepts only `data:` — a URL would be refused at
  render time and silently produce a document with no logo. `accent_color`,
  `page_size` and `margin` are shape-constrained because they are written into
  the stylesheet, where a value carrying `;` or `}` could close the rule.

- **`tempest generate --dockerfile` emits the system packages** when the
  project pins `[pdf]`. WeasyPrint draws text through Pango and resolves fonts
  through fontconfig; a `python:slim` image has neither, and the failure
  appears at the first render rather than at build time. `fonts-dejavu-core` is
  in the list because a container with no font lays the document out correctly
  and draws every glyph as a box.

  Recipe: `docs/recipes/pdf.md`.

### Changed

- **Every public re-export now uses the PEP 484 `from x import Y as Y` form.**
  794 import lines across 17 `__init__.py` files were rewritten. Nothing
  changes at runtime; what changes is that basedpyright and Pylance in strict
  mode stop reporting "private import usage" when a consumer imports a
  documented symbol — a diagnostic the SDK was putting in their editor.

  The rule had been written in `CLAUDE.md` for months and was violated 794
  times, which is why it now has `tests/test_reexport_guard.py`. Scope is the
  names in `__all__`: a helper an `__init__.py` imports for its own use is not
  a re-export, and aliasing it would say the opposite.

- **`tests/test_vacuous_guard.py`** fails a test whose name or docstring claims
  to have crossed a boundary — "across processes", "across replicas",
  "survives a restart" — while the body stays in one process. That is the shape
  that let this release's own determinism claim ship: the test compared two
  renders inside a single process, where matching means nothing.

  It deliberately does **not** police the vocabulary of guarantees. The first
  draft flagged `deterministic` / `reproducible` / `idempotent` and hit 22
  tests, of which roughly twenty were correct as written — idempotence is
  `f(f(x)) == f(x)`, an in-process property, and one flagged test asserts
  bcrypt is *non*-deterministic. A guard whose hits are mostly noise teaches
  people to add skip markers.

### Fixed

- **The report's grand total no longer repeats on every page.** It was a
  `<tfoot>`, which is `table-footer-group` and repeats by definition — so the
  total printed at the foot of page 2 above rows that summed to something else.
  It is now the last row of the body, still column-aligned, and a test reads
  the text of each rendered page to hold it there.

## [0.217.0] — 2026-08-13

### Added

- **WebAuthn / passkeys** (`tempest_fastapi_sdk.auth.webauthn`, extra
  `[webauthn]`). TOTP proves the user holds a shared secret, and a phishing
  page that forwards the code in real time defeats it. WebAuthn binds the
  assertion to the **origin** that asked for it, so a credential registered for
  `app.example.com` produces nothing a page on `app-example.com` can use. That
  property, not "passwordless", is why this shipped.

  `WebAuthnService` runs both ceremonies — register begin/complete,
  authenticate begin/complete — plus credential listing and removal.
  `make_auth_router(webauthn=...)` mounts six routes when
  `AUTH_WEBAUTHN_ENABLED` is on; enabling it without the service raises at
  wiring time rather than 500-ing per request, matching how
  `recovery_code_model` gates MFA.

  ```python
  webauthn = WebAuthnService(
      user_model=UserModel,
      credential_model=UserWebAuthnCredentialModel,
      auth_settings=settings,
  )
  app.include_router(
      make_auth_router(service, session_factory=db.session_dependency, webauthn=webauthn),
  )
  ```

  `BaseWebAuthnCredentialModel` + `make_web_authn_credential_model` hold the
  table. It stores public keys — unlike a password hash, a full leak of it
  authenticates nobody. `credential_data` is kept as the opaque blob `fido2`
  round-trips, so an upstream format change never needs a migration of parsed
  columns.

  **What the SDK checks beyond the library.** The signature counter: one that
  did not advance since the last assertion is the spec's cloned-authenticator
  signal, and `fido2` verifies the signature without tracking it. Authenticators
  that always report `0` — most platform passkeys — are exempt, because for
  them the counter carries no information. Also: the challenge is popped on use
  (a captured response is spent), a credential ID is unique per *table* rather
  than per account, an inactive account cannot log in, and deletion is scoped to
  the owner so a foreign ID answers 404 exactly like one that does not exist.

  **`authenticate_begin` never reveals whether an account exists.** An unknown
  email produces a normal ceremony with an empty credential list; answering
  differently would make the endpoint an enumeration oracle.

  **Passkey login does not go through the MFA challenge**, deliberately: a
  passkey with user verification already proves possession *and* a local factor,
  which is what the second step exists for.

- **`AUTH_WEBAUTHN_*` settings** — `ENABLED`, `RP_ID`, `RP_NAME`,
  `ALLOWED_ORIGINS`, `USER_VERIFICATION`, `RESIDENT_KEY`,
  `CHALLENGE_TTL_SECONDS`. `RP_ID` is the security boundary and an empty one is
  refused at construction. `ALLOWED_ORIGINS` exists because the `fido2` default
  (`https://<rp_id>` and subdomains) is right in production and wrong on a Vite
  dev server; when set it replaces that rule entirely, so it is an explicit
  decision rather than a silent relaxation.

- **`MemoryWebAuthnChallengeStore` / `RedisWebAuthnChallengeStore`** for the
  state between the two halves of a ceremony. The Redis one uses `GETDEL`, so
  read-and-delete is one operation and two concurrent completions cannot both
  find the state.

- **A software authenticator in the test suite.** The tests drive `fido2`'s real
  verification with genuine artifacts — attestation object, authenticator data,
  ES256 signature — because the properties worth testing here (origin binding,
  single-use challenge, advancing counter) are exactly the ones a mocked
  verifier would assert away. It supports being wrong on purpose (replaying a
  counter, signing for a lookalike origin) so the suite can assert the server
  rejects it.

  Recipe: `docs/recipes/webauthn.md`.

### Changed

- **New optional dependency `fido2>=2.0.0`** in the `[webauthn]` extra. It
  declares `cryptography!=35,<52,>=2.6`; the upper bound is accepted because it
  is confined to an optional extra and nothing in the base install resolves it,
  and because WebAuthn is real engineering — CBOR, COSE keys, attestation
  formats, signature verification — not a preset table the SDK should own. Same
  criterion applied to `diffusers` in v0.177.0.

## [0.216.0] — 2026-08-13

### Added

- **Token buckets and per-plan quotas** (`tempest_fastapi_sdk.api.middlewares.quota`).
  The rate limiter answered one question — *did this key exceed N requests in
  the last W seconds?* — and two shapes a paid API needs were not expressible:
  a burst a well-behaved client is allowed to spend at once, and several
  limits applied together (60/min under a 1000/day ceiling, per tier).

  `RateLimitRule` describes one limit: a sliding window, or a **token bucket**
  when `burst` is set, refilling `max_requests / window_seconds` tokens per
  second up to `burst`. A `RateLimitPolicy` resolves which rules apply —
  `StaticRateLimitPolicy` for a single tier, `PlanRateLimitPolicy` for a
  mapping of plan to rules, fed by `plan_by_jwt_claim` / `plan_by_header`.

  ```python
  app.add_middleware(
      RateLimitMiddleware,
      policy=PlanRateLimitPolicy(
          {
              "free": [RateLimitRule(60, 60.0), RateLimitRule(1_000, 86_400.0)],
              "pro": [RateLimitRule(600, 60.0, burst=100)],
          },
          resolve=plan_by_jwt_claim(jwt, "plan"),
          default_plan="free",
      ),
  )
  ```

  **A rejected request spends nothing.** A caller blocked by the daily ceiling
  must not burn a token from the per-minute limit, or the minute drains
  without a single request served. `MemoryQuotaStore` decides every rule under
  one lock before writing any; `RedisQuotaStore` does it inside **one** Lua
  script, which is the only way the whole list is atomic across replicas — a
  client-side loop over single-rule calls is not.

  `PlanRateLimitPolicy` validates its configuration at construction (empty
  mapping, `default_plan` outside it, a plan with no rules) because each of
  those only surfaces in production as unlimited traffic. An unknown plan
  *name* falls back to the default instead of raising: a request is the wrong
  place to discover a typo, and the safe answer is to limit it.

  `key_by_plan_principal` prefixes the resolved plan onto the principal key,
  so an upgrade writes to fresh counters rather than inheriting exhausted
  ones.

- **`RateLimit-Limit` / `RateLimit-Remaining` / `RateLimit-Reset` headers** on
  every response, describing the **tightest** rule — the number a client
  should pace itself against. `RateLimit-Reset` is emitted only where it is
  known (always in policy mode, only on 429 in single-window mode): the
  sliding-window store reports no reset for an accepted request, and a guessed
  number is worse than an absent header. Turn the whole set off with
  `limit_headers=False`.

- **`lupa` in the dev group**, so `fakeredis` executes Lua. The atomicity this
  feature promises lives *inside* the Redis script; a fake that re-implements
  its contract in Python asserts nothing about it. Both stores now run the
  same behavioral suite, the Redis half against the real script.

### Fixed

- **A slow token bucket no longer refills for free.** The bucket key's TTL is
  the time to fill the bucket (`capacity / rate`), not the rule's window. With
  a window-derived TTL, a bucket refilling at 10 tokens/minute with
  `burst=1000` — over an hour to fill — expired long before that, and the next
  request found a full bucket. The in-process store has the same fix: it
  records, per key, the instant its state stops differing from absent, and
  sweeps against that instead of a fixed age.

## [0.215.0] — 2026-08-09

### Changed

- **BREAKING — `tempest_fastapi_sdk.openpix` moved to
  `tempest_fastapi_sdk.integrations.payment.openpix`.** Grouping third-party
  clients by *what the provider does* keeps the top-level namespace clean and
  gives the next provider an obvious home. No deprecation shim: the old path
  existed for a matter of hours, in v0.214.0 alone, and a shim for it would be
  the exact clutter the move is about. Update the import; nothing else about
  the surface changed.

### Added

- **The whole OpenPix API now ships in the SDK.** v0.214.0 required every
  service to run `tempest openapi-client` and keep the output; that meant the
  same generation and the same hand-written layer maintained in every
  repository. All 358 schemas and 105 operations are now checked in:

  ```python
  from tempest_fastapi_sdk.integrations.payment.openpix import (
      Charge, OpenPixClient, OpenPixEnvironment,
  )
  ```

  The generated half is produced by `scripts/regen_openpix.py` from the
  specification pinned in `vendor/openpix-openapi.yaml` (`make
  openpix-regen`), and **a test fails if the files on disk drift from what
  that script produces** — checked-in generated code is only trustworthy if
  hand edits are loud.

  **Loaded lazily.** Building 358 Pydantic models costs the better part of a
  second, and importing the package for `to_cents` should not pay it, so the
  generated modules resolve through PEP 562. Measured: 2 ms to import the
  package, ~200 ms on the first generated name, ~0.03 ms after. A test asserts
  in a subprocess that importing the package leaves `…openpix.schemas` out of
  `sys.modules`.

  `vendor/openpix-openapi.yaml` is build-time input and stays out of the
  wheel.

### Fixed

- **Generated clients no longer produce a `no-any-return` per operation.**
  The emitted `_validate` helper was typed `-> Any`, and every generated
  method returns its result — so a strict `mypy` run reported one error per
  operation: **98** on the OpenPix specification, in the consumer's own gate.
  It is now generic (`def _validate(annotation: type[_T], data: Any) -> _T`),
  which is also more honest: the call really does return the annotation it was
  given. Found by embedding the generated code inside the SDK, where `make
  check` type-checks it.

## [0.214.0] — 2026-08-09

### Added

- **`tempest_fastapi_sdk.openpix` — the thin typed layer over a generated
  OpenPix integration.** Submodule import, no extra, and deliberately **not**
  a second copy of the generated package: `tempest openapi-client` already
  turns the OpenPix specification into 358 schemas and 105 operations, and
  those stay in the consuming service. Embedding them would double the SDK to
  freeze a third party's spec into a release.

  What it supplies is the four things the specification does not say, and that
  every OpenPix integration re-derives by hand:

  - **`OpenPixEnvironment`** — production is `api.openpix.com.br`, testing is
    `api.woovi-sandbox.com`. Different domains; neither spells the other.
    Both read from the spec's `servers` block.
  - **`to_cents` / `reais_to_cents` / `cents_to_reais`** — the specification
    says *"Value in cents of this charge"* and types the field `number`, so a
    generated model hands you the float `1990.0`. `to_cents` narrows it
    exactly and **refuses a fraction** rather than rounding: the field is
    already cents, so a fraction means the caller passed reais, and rounding
    would hide that behind a plausible number. `reais_to_cents` rounds
    half-up, which is what money expects and *not* what the built-in `round`
    does (`round(0.005 * 100)` is `0`).
  - **`OpenPixEvent`** — all 28 webhook events, ported verbatim from
    `WebhookEventEnum`. The `OPENPIX:` prefix is not uniform (charge and
    dispute events carry it, the Pix-automatic family does not); a test pins
    both so the next reader does not "fix" it.
  - **`make_openpix_webhook_dependency()`** — the piece nobody had. The SDK
    already shipped `RSAWebhookSignatureVerifier` and the spec already had the
    events; nothing tied them to the header name and the public key. The
    dependency verifies, decodes and yields a typed `OpenPixWebhookEvent`.

  **Two behaviours chosen to keep a service up.** An unrecognized event does
  not fail the request — OpenPix adds events, and a 500 on one you have never
  seen turns their release into your outage. A non-JSON body that *verified*
  is still delivered: it came from OpenPix, and rejecting it would discard a
  delivery the provider considers sent.

  `OpenPixWebhookEvent` is a frozen dataclass, not a `BaseSchema`. That is
  load-bearing: `BaseSchema` sets `use_enum_values=True`, which stores the
  event as a bare `str`, and the documented
  `event.event is OpenPixEvent.CHARGE_COMPLETED` would then be **silently
  false on every delivery**.

  **Security, stated plainly.** OpenPix's published key is **RSA-1024**
  (verified on load: 1024 bits, exponent 65537), below the 2048-bit floor NIST
  has recommended since 2013. A valid signature is evidence the delivery came
  from OpenPix — **not authorization to move money**. The recipe tells you to
  re-read the charge from the API before acting on `CHARGE_COMPLETED`, and to
  keep the handler idempotent, since the signature covers the body alone and a
  captured delivery replays forever. The key is overridable
  (`webhook_verifier(public_key_pem=...)`) so a rotation does not strand
  consumers on an SDK release, and `decode_public_key` handles the base64 form
  the provider actually publishes, checking the result is a PEM so a truncated
  paste fails immediately rather than as a signature mismatch in production.

  Recipe: `docs/recipes/openpix.md`.

## [0.213.0] — 2026-08-09

### Fixed

Generating against the **real OpenPix specification** (847 KB, 358 schemas,
105 operations) — the one whose prose motivated v0.211.0 — produced output
with 12 `E501` errors, 8 of which survived `ruff format`. The hostile-spec
suite had not caught them because they need long *names*, and the names
that overrun are the ones the generator **synthesizes itself**.

- **The `name: Annotation = Field(` line was never measured.** Inline
  schemas get a class name built by concatenating the whole path
  (`PostApiV1DecodeEmvResponseEmvMerchantAccountInformationPix`), so the
  annotation alone can overrun before any argument is reached. `ruff
  format` then wraps the assignment and **re-indents the arguments one
  level deeper** — so every string the emitter had pre-split to fit column
  88 came out at 92. One defect, two symptoms, and the second one made the
  first look like a splitting bug.

  The emitter now picks the same shape `ruff format` would, in the same
  order it tries them: the plain head while it fits, then the wrapped
  assignment with arguments at 12, then a broken annotation with arguments
  back at 8. A whole-subscript annotation (`list[Item]`) breaks **inside
  its brackets** rather than being parenthesized, because that is the form
  ruff keeps.

- **`ruff format` joined split strings back together.** v0.211.0 forced
  every split to yield at least two adjacent literals, on the reasoning
  that a lone parenthesized literal is un-parenthesized. That is true only
  when the collapsed line then *fits* — which is a line the emitter never
  splits. On real enum values the forced second piece was joined back, so
  the file failed `ruff format --check`. Splits are now exactly as deep as
  the budget requires.

- **A long `examples` list was never split.** The splitter gave up on
  anything that was not a string, leaving a 229-character line. Lists and
  dicts are now exploded only where they overrun, and the key prefix is
  **measured** rather than glued on afterwards — checking the value alone
  and prepending `"OPENPIX:…": ` afterwards left two lines over on the
  first attempt.

- **`return _validate(LongResponse, response.json())` was never
  measured**, and inline response schemas are named after their path.

- **Synthesized class names are capped** (`MAX_CLASS_NAME = 55`). The
  binding constraint is not the `class` statement but a docstring
  `Attributes:` entry, where the annotation composes around the name
  (`dict[str, Name] | None` adds 18 columns at an indent of 12) and `ruff
  format` breaks neither. Measured on the OpenPix specification: 6 of 358
  names truncated, no new collision.

**Known limit, stated rather than papered over.** A very long field name
next to a long single-identifier annotation (`x: SomeLongClassName =
Field(`) has no formatting that fits — `ruff format` collapses it, because
a bare identifier has nothing to split. Only shorter names help. Every
annotation with a union or a subscript in it does have a stable shape, and
the emitter now produces it.

## [0.212.0] — 2026-08-09

Both entries below came from auditing the `openapi-client` recipe against
the code rather than from a bug report. No test catches prose that promises
a feature, which is exactly how one of them survived several releases.

### Added

- **`# openapi: unsupported` markers in the generated code.** The docs had
  promised this comment for releases while the package emitted **zero** of
  them — the only record of a gap was the command's summary, which is
  terminal output that scrolls away. Someone opening `schemas.py` months
  later and finding an `Any` had no way to learn why short of regenerating
  from the same specification.

  Now every gap is attributed to the field, parameter or operation that
  raised it (`FieldIR` / `ParameterIR` / `OperationIR.unsupported`, filled
  by a new `_Parser.capture()`), and the emitters write it above the
  affected line:

  ```python
  # openapi: unsupported — `not` in ThingWeird rendered as Any (no Python
  #   equivalent)
  weird: Any | None = None
  ```

  Greppable on purpose: `grep -rn "openapi: unsupported" src/integrations/`
  lists everything an integration lost. Two details are load-bearing — the
  comment goes **above** the line rather than trailing it, so a long reason
  wraps instead of overrunning the budget and `ruff format` has nothing to
  move; and the per-target de-duplication is independent of the summary's,
  because the summary must not repeat itself while two fields hitting the
  same gap both need marking. A gap with nothing in the output to mark (a
  dropped `header` parameter) stays summary-only.

### Fixed

- **The command summary's header claimed something false.** It hardcoded
  `N construct(s) could not be modelled (rendered as Any, marked in the
  output)`, but roughly half the notes describe a different treatment
  entirely — a `header` parameter is skipped, a discriminator is ignored, a
  path placeholder is synthesized. None of those becomes `Any`, and until
  this release nothing was marked in the output at all. Each individual
  note was always accurate; only the header lied. It now says each line
  states what was generated instead.

## [0.211.0] — 2026-08-09

### Fixed

`tempest openapi-client` run against a real third-party specification
produced a package that did not import, did not lint, and in one case
silently changed what the specification said. Every case below is now a
test in `tests/openapi/test_hostile_spec.py`, generated with
`run_format=False` on purpose — the `ruff --fix` pass the command runs
afterwards was hiding three of them, so they only ever reached a caller
passing `--no-format`.

- **A description with a quote, a newline or a backslash emitted a broken
  literal.** The emitter double-quoted a value by interpolating it
  **raw**, guarded by `"'" in repr(value)` — which matches `repr`'s own
  delimiter, not an apostrophe in the text, so every quote-free string
  took that path. A description carried over from a YAML block scalar
  emitted an unterminated string and `schemas.py` did not import; one
  containing `\b` or `\x41` changed value in silence. Text is now rendered
  by a real literal writer, containers included, so a string nested in an
  object `example` gets the same treatment.

- **Quote style now matches what `ruff format` normalizes to.** Text
  carrying more `"` than `'` comes out single-quoted. The project's rule
  is double quotes, but `ruff format` prefers whichever escapes less, so
  the double-quoted form was correct code that failed the consumer's
  `ruff format --check` on their first run.

- **`\#` in the spec's prose emitted a docstring that warns.** It is not
  a Python escape — `W605`, and a `SyntaxWarning` from 3.12. The docstring
  is now `r"""` when any of its prose carries a backslash, and the
  decision is taken before wrapping, since the `r` costs a column on the
  line already closest to the budget.

- **An over-long description or enum value overran the line budget.**
  `ruff format` never breaks a string, so the emitter has to split it —
  into **two or more** adjacent literals, because a lone parenthesized
  literal is joined straight back onto the long line. Concatenating the
  emitted pieces reproduces the specification's wording character for
  character. An enum **member name** is derived from the value, so a
  provider spelling a status out in a sentence overran before the value
  was reached at all; the name is now capped (the value never is).

- **A summary longer than one line was left long.** Both emitters wrapped
  only some of their prose, and `ruff format` does not rewrap a docstring.
  Wrapped continuation lines also no longer hang one level deeper than the
  `Attributes:` heading that follows them.

- **`transaction` and `Transaction` in the same spec produced
  `Transaction_2`**, which is not CapWords and fails the consumer's
  `N801`. Class collisions now resolve to `Transaction2`; field
  collisions keep `reference_2`.

- **A property named `2fa` was emitted verbatim** and the module did not
  parse. It becomes `field_2fa` with `alias="2fa"`. The prefix is
  `field_`, not `_`: a leading underscore makes Pydantic treat the
  attribute as **private**, so the field would vanish from the model
  rather than merely be renamed.

- **Path parameters and the path template did not have to agree.** Three
  cases, each reported in the command summary rather than guessed at:
  a `path` parameter the template never interpolates is now **dropped**
  (the caller passed an identifier and the request dropped it on the
  floor — the one failure mode the generator must not produce in
  silence); a placeholder no parameter declares is **synthesized** as a
  required `str` (the emitted path is an f-string, so skipping it left the
  method referencing an undefined name); and path parameters are ordered
  by their position in the **template**, since they are the generated
  method's only positional arguments and a spec listing them out of order
  handed a caller reading `/{a}/{b}` a signature spelled `(b, a)`.

- **The path and every wire name now go through the literal writer too.**
  Both come from the specification, and one carrying a quote or a
  backslash emitted a module that does not parse.

## [0.210.0] — 2026-08-09

### Added

- **`tempest pr-prompt` — the prompt that makes an AI write the PR
  description.** The branch is green and the description is the step
  everyone skips. Any assistant writes a good one; what it lacks are the
  two things that live in the repository — the template the team agreed
  on and the diff the branch produced. The command assembles both and
  writes the prompt to stdout, so it pipes into whichever assistant the
  user runs:

  ```bash
  tempest pr-prompt | claude -p
  tempest pr-prompt develop --lang en --out pr_prompt.txt
  ```

  The **repository's own template wins** — `.github/pull_request_template.md`
  and the other conventional spellings, GitLab's included — because it is
  the contract that project's reviewers read; `--template` overrides it,
  and a bundled PT-BR / EN-US template covers the repository that has
  none. Around it go the rules that stop the model from returning the
  template with its placeholders still in it (no undecided `Sim/Não`, no
  italic instruction text, no section dropped, no invented migration or
  env var), and the branch context: commit subjects, `--name-status` and
  bounded patch excerpts.

  Three decisions the shape depends on:

  - Patches are read as **`base...head`** — the merge-base diff the forge
    shows on the pull request. The two-dot form would attribute every
    commit that landed on the base since the branch started to this PR.
  - Files are excerpted **most-changed first**, not alphabetically:
    `--max-files` spent in git's order goes to `.github/` and
    `CHANGELOG.md` before reaching the file the PR is about.
  - **Nothing is cut silently.** A truncated patch is marked, the files
    left without an excerpt become a line in the prompt, and the cut
    respects line boundaries — half a diff line reads as code that does
    not exist.

  Only the excerpts are bounded: the commit list and the changed-file
  list always go in whole, so the model always knows *what* changed and
  only *how* it changed is sampled. `--full` excerpts every file with its
  whole patch, and refuses to run next to `--max-files` / `--max-chars`
  rather than silently overriding a number the caller typed.

  Public surface in `tempest_fastapi_sdk.cli.pr_prompt`:
  `generate_pr_prompt`, `collect_context`, `build_prompt`,
  `resolve_template`, `bundled_template`, `files_by_churn`,
  `diff_excerpts`, `commit_subjects`, `changed_files`, `resolve_base`,
  `current_branch`, `repository_name`, `repository_root`, plus
  `PullRequestContext` / `ResolvedTemplate` / `DiffExcerpt` /
  `PromptLanguage` / `GitError`. Recipe: `docs/recipes/cli.md`.

## [0.209.0] — 2026-08-09

### Fixed

- **`prefetch` reaches the class-based consumer path.** `mq.on(channel,
  prefetch=N)` translated the cap into the FastStream `Channel` that
  carries `basic.qos`; `Consumer` / `@subscribe` did not, and FastStream
  has no `prefetch` keyword — so the same knob that works on the
  decorator raised `TypeError: RabbitRegistrator.subscriber() got an
  unexpected keyword argument 'prefetch'` on the class path. A team that
  had adopted `Consumer` could only cap a consumer by hand-building
  `channel=Channel(prefetch_count=N)`, which is the FastStream detail the
  facade exists to hide.

  `subscribe(channel, prefetch=N)` and `Consumer(prefetch=N)` are now
  named, typed keywords, and a class-level `prefetch` covers every
  binding the consumer declares (a `@subscribe` naming its own wins):

  ```python
  class OrdersConsumer(Consumer):
      prefetch = 32                      # every binding below

      @subscribe("relatorios.gerar", prefetch=1)
      async def gerar(self, pedido: Report) -> None: ...

      @subscribe("orders.paid")
      async def on_paid(self, event: OrderPaid) -> None: ...
  ```

  Verified against a real RabbitMQ, not against the local object:
  `rabbitmqctl list_consumers queue_name prefetch_count` reports the caps
  the class declared. Checking that the keyword was accepted would pass
  for an implementation that swallowed it.

- **The constructor-form `Consumer` forwards subscriber options.**
  `@subscribe` took `**options` (so `exchange=` and friends reached
  FastStream) while `Consumer.__init__` accepted only `channel` and
  `schema` and registered `options={}`. Naming an exchange — or anything
  else the transport takes — forced the grouped form or the decorator.
  `Consumer(channel=..., schema=..., **options)` now forwards them, the
  same passthrough `@subscribe` already had.

- **`retry=` works on the class-based task path — it was silently
  ignored.** `@tq.task(retry=RetryPolicy(...))` rendered the policy into
  the `retry_on_error` / `max_retries` labels TaskIQ's middleware reads.
  `@task_method(retry=...)` did not: the policy object went through
  `**options` and landed as a `retry` label nothing looks at, so the task
  **never retried** and nothing raised. Worse than the `prefetch` case
  above, which at least failed loudly.

  Measured against a real RabbitMQ with a `taskiq worker` process and a
  task that always fails: the decorator path ran it twice, the class path
  once. After the fix both run twice.

  `retry` is now a named keyword on `task_method()` and `TaskDef`, plus a
  class attribute covering every task the definition declares (a
  `@task_method` naming its own wins), and `TaskQueue.register` renders
  it into labels exactly as `task()` does. `TaskDef(name=..., **options)`
  also forwards extra labels, which only `@task_method` could before.

- **The documented `taskiq worker` / `taskiq scheduler` commands could
  not start.** README, the queue recipe and three docstrings taught
  `taskiq worker src.tasks:tq.broker` (and `…:tq.scheduler`,
  `…:scheduler.scheduler`). TaskIQ's CLI resolves `module:attr` with a
  plain `getattr`, so every dotted form raises `AttributeError: module
  'src.tasks' has no attribute 'tq.broker'` before the worker starts.
  The docs now bind the objects to module-level names
  (`broker = tq.broker`) and point the CLI at those.

## [0.208.0] — 2026-08-08

### Added

- **`Publisher` — the class-based publish side of the queue.** `Consumer`
  has let a team group handlers in a class since the facade shipped, and
  the publish side had no equivalent: `await mq.publish("orders.paid", event)`
  took the channel as a loose string and the payload as `Any`, so nothing
  connected the two ends of what is, in practice, one contract.

  ```python
  class OrderPaidPublisher(Publisher[OrderPaid]):
      channel = ORDERS_PAID
      schema = OrderPaid

  orders = mq.publisher_for(OrderPaidPublisher)
  await orders.publish(OrderPaid(order_id="abc"))
  ```

  Three properties the loose call could not have. `Publisher[OrderPaid]`
  makes `publish` **take an `OrderPaid`**, so the wrong model is a type
  error rather than a message the consumer rejects in production. The
  declared `schema` is **enforced on the way out** — the consumer is a
  process away and can only reject what already left. And a `QueueSpec`
  on `channel` goes through the same binding `on()` uses, so a service
  that **only publishes** still registers the dead-letter exchange it
  names; without that the queue carries `x-dead-letter-exchange` pointing
  at an exchange nobody declared, and rejected messages are dropped at
  routing time.

  `Publisher.publish` deliberately goes through `MessageBroker.publish`
  rather than FastStream's own publisher object, so it keeps the
  `message_id` deduplication keys on and the `traceparent` /
  `x-request-id` headers tracing rides. A publisher built on the raw
  object would look identical and silently break both.
  `MessageBroker.publisher()` is unchanged and still returns the raw
  object, which is what puts the channel in the generated AsyncAPI.

  Class attributes are not the only way in: `channel` and `schema` are
  also constructor parameters, on `Publisher` itself and on
  `publisher_for`, where they override what the class declares. That is
  what lets one subclass serve a channel only known at runtime — per
  tenant, per environment — without a subclass per value. They are named
  parameters rather than part of `**options`, so the type checker sees
  them and a publish option sharing one of those names is not swallowed.

- `Subscription` is now exported from `tempest_fastapi_sdk.queue`. It was
  already the documented return type of `Consumer.subscriptions()` and
  reachable only by importing the submodule.

### Fixed

- **`MessageBroker` kept five keywords hidden in `**options`.** The four
  transport constructors popped `declare_topology` out of the forwarded
  keyword arguments, and `rabbitmq()` / `on()` popped `prefetch` — so a
  parameter the facade consumes itself was invisible to the type checker,
  absent from autocomplete, and findable only by reading the source. On
  `redis()`, `kafka()` and `nats()` it was not even in the docstring: a
  supported parameter nobody could discover. It also meant a future
  FastStream keyword of the same name would be swallowed by the facade
  instead of reaching the broker. All five are now named keyword-only
  parameters. Source-compatible — they were already keyword-only in
  practice.

  This is the same defect the audit found on `publisher_for`, in the same
  file, and the audit missed it — so `tests/test_kwargs_guard.py` now
  walks the package with `ast` and fails on any function that reads a key
  out of its own catch-all. The suite also asserts the guard fires on the
  shape that shipped, because a guard that cannot fail is one nobody
  should trust.

- **The class-based path could not declare topology.** `Consumer.channel`,
  `subscribe()` and `Subscription.channel` were typed `str`, while
  `MessageBroker.register` binds them through the same code path as
  `on()` — which has always accepted `str | QueueSpec`. Passing a spec
  worked at runtime and failed the type checker, so anyone using classes
  had to drop back to the decorator to get a dead-letter exchange or a
  quorum queue. The annotations now match what the code does.

## [0.207.0] — 2026-08-08

### Fixed

- **The retry budget was spent in half the attempts.** `delivery_attempt()`
  summed every `x-death` entry, but RabbitMQ keeps **one entry per (queue,
  reason) pair** and the delayed-retry chain dead-letters a message twice
  per round: `rejected` leaving the main queue, then `expired` leaving the
  waiting queue when its TTL fires. The counter therefore advanced by two
  per retry, so `ConsumerRetryPolicy(max_attempts=3)` gave up after the
  second delivery. Entries whose reason is `expired` are now skipped — an
  expiry is the waiting room emptying itself, not a delivery that failed.
  An entry with no `reason` at all still counts, so a non-conforming
  header keeps retries bounded rather than making them infinite.

- **A concurrent delivery could dead-letter a message no handler ran.**
  With `deduplicate()` and `dead_letter()` both installed, the
  `ConcurrentDeliveryError` raised when a sibling worker holds the claim
  was caught as an ordinary handler failure: it consumed an attempt and,
  on the delivery that exhausted the budget, sent the message to the dead
  queue. It is now re-raised untouched, so the copy is simply rejected and
  offered again.

- **Deduplication inflated the metric an alert is built on.** The same
  `ConcurrentDeliveryError` counted as `queue_messages_total{status="error"}`.
  It gets its own `duplicate` status, so a busy channel with healthy
  handlers no longer pages on its own deduplication working.

- **`consume_span` leaked the request id when closing the span failed.**
  `clear_request_id` ran after the span's `__exit__` rather than in a
  `finally`, so a tracer or exporter raising on exit left the contextvar
  set. The worker task is reused across consumes, so the next message
  would carry the previous request's id — worse than no correlation,
  because it reads as real.

- **`publish()` introspected the broker signature twice per message.**
  `_publish_accepts` ran `inspect.signature` on every publish for both
  `message_id` and `headers`, and the "logs once" warning in its docstring
  was emitted per call. Answers are cached on the underlying function, so
  the check happens once per process and the warning once per broker
  class. The warning also named `method` instead of the broker, since
  `type()` of a bound method is `method`.

### Changed

- `QueueType.QUORUM` documented itself as not supporting queue TTL, while
  `retry_queues(queue_type=QueueType.QUORUM)` builds a retry queue that
  needs exactly that. Quorum queues do support message TTL; the real gap
  is `x-max-priority`, which is what `QueueSpec.__post_init__` already
  refuses. `UnsupportedTopologyError` likewise claimed to be raised at
  publish time — `publish()` only reads the channel name, since the
  topology belongs to the declaration.

- `MessageBroker.dead_letter()` and `.deduplicate()` took their defaults
  from literals (`3`, `86_400`) that duplicated `DEFAULT_MAX_ATTEMPTS` and
  `DEFAULT_DEDUP_TTL_SECONDS`, and were free to drift from them.

## [0.206.0] — 2026-08-08

### Added

- **Trace and request-id propagation across the queue** (#129). A request
  opened a trace, published an event and answered 201; the consumer that
  charged the card, wrote the ledger and sent the mail showed up as three
  orphan traces with no parent and no relation to each other. The SDK
  traced FastAPI, SQLAlchemy, httpx and genai — the queue was the one hop
  left uninstrumented, and it is the hop where the causal link is lost.

  `publish()` injects the W3C `traceparent` and the current request id
  into the message headers **when the transport takes a `headers`
  keyword** — checked independently of `message_id`, because Redis
  accepts one and not the other, so a "is this RabbitMQ?" branch would
  have been wrong; `MessageBroker.enable_tracing()` opens a span
  per consume carrying the messaging semantic conventions
  (`messaging.system`, `messaging.destination.name`,
  `messaging.operation`) and marks it as failed when the handler raises.

  The publishing trace is attached as a **link**, not as a parent. The
  convention recommends it for asynchronous consumption and the reason is
  practical: a consumer can run minutes after the publish, and a child
  span of that duration stretches the request's trace and makes its
  latency unreadable.

  The consumer also **adopts the publisher's request id** for the
  duration of the handler, so the worker's log lines carry the id of the
  request that caused them. That is worth more day to day than the span
  — it makes `grep` enough to correlate — and it is the half that works
  with no `[otel]` extra at all.

## [0.205.0] — 2026-08-08

### Added

- **Consume-side deduplication** (#127). The facade has always said
  delivery is at-least-once and handlers should be idempotent, and
  offered nothing to make one. `MessageBroker.deduplicate(store, ...)`
  runs each message id at most once across redeliveries, with
  `MemoryDedupStore` for a single replica and `RedisDedupStore` for more
  than one — where `claim` is a single `SET NX EX`, so the atomicity two
  workers racing on the same id depend on comes from Redis rather than
  from a lock of our own.

  Marking is **two-phase**: the first delivery claims `in_flight` and
  runs, success marks `done` and the next delivery is skipped, and a
  **failure releases the key** so a retry actually retries. Without that
  third step the feature would trade "processed twice" for "processed
  never", which is worse than the problem it solves.

  A delivery arriving while another worker holds the claim raises
  `ConcurrentDeliveryError` so the broker rejects it. Acknowledging would
  be the dangerous choice — the in-flight worker may still fail, and the
  copy that could have retried would be gone.

  **Documented as not exactly-once**, because it is not: the mark and the
  handler's effect are not atomic, and a crash between them leaves a claim
  that expires and a message that runs again. The docs say so, and say
  that when the effect is a row keyed by something the domain owns,
  `INSERT ... ON CONFLICT DO NOTHING` is idempotent with no extra moving
  part and is the better answer. This middleware is for effects that are
  not rows.

### Changed

- `MessageBroker.publish()` fills `message_id` with a fresh UUID when the
  caller does not pass one **and the transport accepts the keyword**.
  `RedisBroker.publish` has no `message_id` parameter and no `**kwargs`,
  so sending one unconditionally would turn every publish on that
  transport into a `TypeError`; the signature is introspected once and
  the keyword is dropped where it does not fit. A signature that cannot
  be read declines and logs — losing deduplication costs a feature,
  sending an unsupported keyword costs the publish. Without a stable id there is no key to
  deduplicate on and a redelivery is indistinguishable from a new event.
  An explicit `message_id` is kept untouched, which is how you key
  deduplication on something the domain owns instead.

## [0.204.0] — 2026-08-08

### Added

- **`prefetch` on the broker and per consumer** (#128). Nothing in the
  facade capped how many unacknowledged messages RabbitMQ pushes, so the
  broker delivered as fast as the consumer acked. That shows up three
  ways, all in production: a slow handler accumulates messages in process
  memory, the first replica to connect takes the batch and leaves its
  siblings idle, and the unacked backlog sits in RAM until the pod is
  OOM-killed and the whole lot is redelivered.

  `MessageBroker.rabbitmq(url, prefetch=32)` caps the connection;
  `@mq.on(channel, prefetch=1)` overrides it for one consumer, which is
  what keeps a heavy handler from hoarding deliveries without throttling
  the cheap ones beside it. FastStream carries `basic.qos` on a `Channel`
  object rather than as a scalar, so the flat keyword is translated; an
  explicit `channel` / `default_channel` always wins, because rebuilding
  one the caller configured would drop the confirms and QoS they also set
  on it.

  **The default is unchanged — still uncapped.** Picking a number without
  measuring is the mistake `DEFAULT_INTRA_OP_THREADS` made before it was
  re-justified: too small serializes consumption, too large recreates the
  problem, and the right value depends on the handler's latency. The knob
  is exposed and documented; changing the default is a separate, measured
  decision.

### Fixed

- **`MessageBroker.on()` and `.publisher()` shadowed FastStream's own
  `channel=` keyword.** Both take the channel as their first parameter,
  which made `mq.on("orders.paid", channel=Channel(...))` a
  `TypeError: got multiple values for argument 'channel'` — so the raw
  AMQP channel, the escape hatch for anything the facade does not expose,
  was unreachable. The parameter is positional-only now. Calls that pass
  the channel positionally — every documented form — are unaffected;
  `mq.on(channel="orders.paid")` has to drop the keyword.

## [0.203.0] — 2026-08-08

### Added

- **Dead letters, delayed retry and metrics on the event path** (#126).
  The consumer ack policy is `REJECT_ON_ERROR`: a handler that raises
  issues `basic.reject` with `requeue=False`. That avoids a poison-message
  loop and, on its own, means the message is **discarded** — no error
  surface, no dead queue, no metric. `TaskQueue` solved this for background
  tasks in v0.157/v0.158; `MessageBroker` had nothing equivalent, 5 public
  symbols against 29.

  `MessageBroker.dead_letter(sink, max_attempts=...)` hands every terminal
  failure to the **same** `DeadLetterSink` protocol the task path uses, so
  `DbDeadLetterSink`, the admin panel and `make_requeue_action` work
  unchanged and a dead task and a dead event land on one screen. The
  mapping is deliberate: `task_name` carries the channel, `task_id` the
  broker's message id, `kwargs["body"]` the raw body. The sink fires once,
  on the delivery that exhausts the budget — read from AMQP's `x-death`
  header, so a consumer restart does not reset it. The exception is always
  re-raised, so the broker still rejects and any dead-letter routing still
  applies.

  `retry_queues(channel, ConsumerRetryPolicy(...), ...)` builds the
  delayed-retry topology and `MessageBroker.declare_retry_topology()`
  declares **and binds** it. Binding is not optional: a chain whose
  queues are declared but not bound routes a rejected message into an
  exchange with nothing behind it, where RabbitMQ drops it silently.
  Measured against a real broker — with the bindings the message returns
  on schedule (1.5s gaps for a 1.5s TTL), without them it is delivered
  once and vanishes. The topology itself is built out of `QueueSpec`: the main queue dead-letters
  into a retry queue whose only job is to hold the message, and that
  queue's TTL returns it to the main exchange when it expires. The
  **broker** does the waiting, so a worker restart mid-delay changes
  nothing — unlike an in-process retry loop, which dies with the pod. The
  `rabbitmq_delayed_message_exchange` plugin is simpler and requires the
  plugin installed, which several managed offerings (including the free
  CloudAMQP tier) do not provide; this builds on stock AMQP instead.

  `QueueMetrics` mirrors `TaskMetrics` on the shared registry:
  `queue_messages_total{channel,status}` and
  `queue_message_duration_seconds{channel}`, installed with
  `enable_metrics()`.

  **Documented together on purpose:** the topology alone retries forever,
  because AMQP counts redeliveries but does not stop on them. What
  enforces `max_attempts` is the dead-letter middleware.

## [0.202.0] — 2026-08-08

### Added

- **`QueueSpec` — declarative queue topology on `MessageBroker`** (#125).
  A channel was a plain string, which is the right default and is also
  everything the facade could express. The properties that decide whether
  a queue survives a broker restart, where a rejected message goes and how
  long it lives are not part of a name — on RabbitMQ they live in the queue
  declaration, and reaching them meant dropping to `broker.broker` and
  losing the `Consumer` / `register()` layer built around it.

  `QueueSpec(name=..., durable=..., dead_letter=..., message_ttl_ms=...,
  max_priority=..., max_length=..., queue_type=...)` carries that as typed
  data and is accepted anywhere a channel string is, so adoption is
  per-channel. `DeadLetterSpec`, `QueueType` (classic/quorum) and
  `UnsupportedTopologyError` come with it.

  **A field the transport cannot express raises.** `dead_letter` on Kafka
  is an error naming the field, not a silent drop — a dropped
  `dead_letter` produces a queue that looks configured and discards every
  failure, which is the defect the type exists to prevent. Same call as
  `op.replace_enum` raising on an unsupported dialect. A bare
  `QueueSpec(name=...)` asks for nothing beyond a name and stays portable
  everywhere.

  `connect()` declares the dead-letter exchanges the registered specs
  name, as durable topic exchanges — with the type passed as
  `ExchangeType.TOPIC`, since FastStream reads `exchange.type.value` when
  declaring and a bare `"topic"` string is an `AttributeError` at
  startup. RabbitMQ accepts a queue pointing at
  an `x-dead-letter-exchange` that does not exist and then discards at
  routing time, so declaring it is what makes the setting mean anything.
  `MessageBroker.rabbitmq(url, declare_topology=False)` opts out where the
  broker is managed and the application cannot declare.

  Impossible combinations are refused at construction: `max_priority` on a
  quorum queue raises at import, pointing at the line that wrote it, since
  RabbitMQ implements priorities only for classic queues.

## [0.201.1] — 2026-08-08

### Fixed

- **`@asynccontextmanager` functions annotated their return as
  `AsyncIterator`, which typeshed now deprecates.** The upstream stub
  gained a second overload and marks the `AsyncIterator` one
  `@deprecated`: *"Annotating the return type as `-> AsyncIterator[Foo]`
  with `@asynccontextmanager` is deprecated. Use `-> AsyncGenerator[Foo]`
  instead."*

  The reason is that `AsyncIterator` is too wide for what the decorator
  needs. Anything with `__anext__` satisfies it, while
  `asynccontextmanager` also calls `athrow` and `aclose` — it requires a
  real generator, which is what `AsyncGenerator` says. Runtime behavior is
  untouched; the cost is a strikethrough in Pylance today and a mypy error
  once its vendored typeshed catches up (mypy 2.3.0 still ships the old
  single signature, which is why `make check` was silent).

  Corrected in eleven shipped modules — `transaction` / `savepoint`
  (v0.200.0), `explain_queries` (v0.200.0), `AsyncRedisManager
  .get_client_context`, the four `lifespan` helpers on `MessageBroker` /
  `AsyncQueueManager` / `TaskQueue` / `AsyncTaskScheduler` /
  `AsyncTaskBrokerManager`, and `test_database` / `test_session` — and in
  the `tempest new` app template, which stamps the annotation into every
  generated service.

  The two-parameter `AsyncGenerator[T, None]` is used rather than the
  one-parameter form. Both work here (the PEP 696 default lives in the
  stub, so the short form subscripts fine on the supported 3.11 floor and
  mypy accepts it), but the explicit send type does not depend on a
  third-party checker implementing that default.

  Untouched on purpose: `AsyncIterator` is still correct for the FastAPI
  dependency generators (`client_dependency`, `broker_dependency`), for
  real streams (`Agent.stream`, `AIChatPipeline`), and for the
  `Callable[[], AsyncIterator[AsyncSession]]` parameters the routers
  accept — none of those go through `asynccontextmanager`.

### Changed

- Documentation examples follow the same correction across 35 pages, and
  ten `lifespan` examples in the queue/tasks and SSE recipes that carried
  **no** return annotation now have one, which the repository's
  typed-examples rule already required.

- `ruff` no longer formats Markdown code blocks (`extend-exclude`); see
  v0.201.0.

## [0.201.0] — 2026-08-08

### Security

- **Dependency audit: 31 known advisories across 8 packages, down to 1.**
  Refreshing the lock cleared everything with a published fix. The
  remaining one has none: `chromadb` PYSEC-2026-311, a pre-authentication
  code-injection in the ChromaDB **server**'s collections endpoint when
  `trust_remote_code` is set. The SDK's `ChromaVectorStore` uses
  `chromadb.PersistentClient` — embedded, no HTTP server — so nothing the
  SDK does reaches it; anyone running `chroma run` themselves is exposed
  and has no version to move to.

- **Lower bounds raised where the flaw is reachable.** A floor is what
  actually protects a consumer, since the lock ships to nobody:

  - `pydantic-settings>=2.14.2` (base dependency) — GHSA-4xgf-cpjx-pc3j,
    `NestedSecretsSettingsSource` follows a symlink out of `secrets_dir`,
    reading files outside it and bypassing `secrets_dir_max_size`. The SDK
    never sets `secrets_dir`, but `BaseAppSettings` is a
    `pydantic-settings` class consumers extend and configure.
  - `cryptography>=50.0.0` (`[webpush]` and two more) — PYSEC-2026-3552,
    a Bleichenbacher oracle in PKCS#7 `EnvelopedData` decryption. Not
    reachable through the SDK, which uses the library only to verify
    webhook RSA signatures; raised as hygiene on a cryptographic
    dependency.
  - `pillow>=12.3.0` (`[genai-image]`, `[vision]`, `[all]`) — fifteen
    image-parsing advisories. Reachable here, because processing
    third-party images is exactly what those extras are for.

  `torch` was deliberately **left at `>=2.2.0`**. PYSEC-2025-194 is memory
  corruption in `torch.jit.script`, local, and the SDK never calls it;
  a `>=2.13` floor is the most expensive constraint in this project —
  it cuts CUDA and platform combinations and forces multi-gigabyte wheels
  — which an unreachable local flaw does not justify.

### Fixed

- **The binary tree-ensemble defect was blamed on the wrong package.** It
  was recorded as a `skl2onnx` conversion bug: a binary tree classifier
  exported to ONNX returned a decision score in `[-1, 1]` where a
  probability was expected. Holding `skl2onnx` 1.20.0, scikit-learn 1.9.0
  and `onnx` 1.22.0 byte-identical and moving only the runtime settles it
  — the graph was always correct and **`onnxruntime`** evaluated it wrong:
  maximum absolute error of **1.0** against `predict_proba` on 1.27.0,
  **9.5e-08** on 1.28.0.

  `onnxruntime` floors move to `>=1.28.0` so a normal install cannot hit
  it. The export still checks the running version through the new
  `BINARY_TREE_FIXED_IN_ONNXRUNTIME` constant, because a floor only binds
  the resolver — an environment assembled around it would otherwise be
  wrong in silence. The recipe and the covers list are corrected too;
  attributing a defect to the wrong layer sends the next reader to the
  wrong changelog.

- `discover_models` narrowed through a `bool` predicate, so every
  attribute read after it needed re-asserting once typeshed tightened
  `inspect.getmembers`. `_is_concrete_model` is a `TypeGuard` now.

### Changed

- `mkdocs`-only: `pymdown-extensions>=11.0.1` (CVE-2026-67422). Docs
  group, never resolved by a consumer.

## [0.200.0] — 2026-08-08

### Added

- **Transaction control on `BaseRepository`.** Every write method used to
  end in an unconditional `COMMIT`, which is right for one statement and
  wrong the moment a business rule spans two — `orders.add()` was already
  durable when `items.add_all()` failed.

  `transaction(session)` groups a block into a single commit, and the depth
  counter lives in `session.info` rather than on the repository, so **every**
  repository bound to that `AsyncSession` joins the same block. That is what
  makes a service orchestrating several repositories work without threading
  context between them. Nesting is re-entrant; only the outermost exit
  commits.

  The repository gained `commit()` / `flush()` / `rollback()` so a service
  never has to reach for `session` — and so a durable point can be stated
  outright instead of calling `update()` for its commit side effect.
  `commit()` degrades to a flush inside an open block, which makes the call
  safe to leave in place when someone later wraps the code; `rollback()`
  inside a block raises `RuntimeError`, because it would discard other
  repositories' work while the caller believes it is undoing its own step.
  `autocommit=False` makes a whole repository explicit; it disables only the
  implicit commit, never an explicit `commit()`.

  `savepoint()` isolates a step you intend to recover from. **This exposed a
  real backend divergence:** the `pysqlite` driver under `aiosqlite` opens
  transactions implicitly and emits no `BEGIN`, so SQLite treats the
  `SAVEPOINT` as the outermost transaction and its `RELEASE` becomes a
  **commit** — a nested block that exits cleanly turned durable even when the
  outer block was rolled back afterwards. Invisible on the failure path,
  which is why an initial probe missed it. `AsyncDatabaseManager` now applies
  SQLAlchemy's documented remedy to every SQLite engine it builds
  (`enable_sqlite_savepoints`), and the RELEASE path is pinned by a test, so
  the test backend and the production backend agree about atomicity.
  Recipe: `docs/recipes/transactions.md`.

- **Text search, portable and full-text.** Two layers. `search()` is
  tokenized escaped `ILIKE` — identical on PostgreSQL and SQLite, no index,
  no extension, no migration; words combine with `AND`, columns with `OR`,
  and the user's own `%` / `_` are escaped so a term like `100%` searches
  for the character instead of matching every row. `full_text_search()` uses
  `to_tsvector` / `websearch_to_tsquery` / `ts_rank` on PostgreSQL —
  stemming, stop words, the `"quoted phrase"` and `-excluded` syntax users
  already type, per-field weighting through `TextSearchWeight`, results
  ordered by relevance — and degrades to the portable layer elsewhere, with
  `supports_full_text` telling the caller which one it got.

  The condition builders (`search_condition()` / `full_text_condition()`)
  return the clause instead of executing it, and `where=` now accepts a raw
  SQLAlchemy clause as well as a `Q`, so a search paginates and counts
  through the existing `paginate()` / `count()` rather than needing its own.
  Column references accept either a name or the mapped attribute
  (`ArticleModel.title`), and `TextSearchLanguage` / `TextSearchWeight` /
  `TokenMatch` are enums — no magic strings at the call site.

  The text-search configuration is inlined as a SQL literal rather than
  bound as a parameter: asyncpg prepares statements server-side and
  PostgreSQL cannot infer `regconfig` for an untyped placeholder, so the
  parameterized form would have failed on the production driver while
  passing every test here. Recipe: `docs/recipes/text-search.md`.

- **Enum columns that are type-safe in the database, not just in Python.**
  `Mapped[MyEnum]` on a `BaseModel` now routes through `TempestEnum`, which
  changes three SQLAlchemy defaults that each cost real safety: it stores the
  member **value** rather than its name (so a report or a sibling service
  reads `in_progress`, not `IN_PROGRESS`), it emits a `CHECK` constraint on
  backends without a native enum type (the stock default gives SQLite a bare
  `VARCHAR`, so the test database accepts data production rejects), and it
  names the PostgreSQL type `order_status_enum` instead of `orderstatus`,
  which collides with a table or column of that name. `enum_column()` spells
  the same thing out when the column needs `default` / `index` /
  `server_default`. Recipe: `docs/recipes/enum-columns.md`.

- **Alembic enum migrations that actually work.** Three separate gaps, all
  closed. `sync_enum_types` detects a changed member list, which
  autogenerate misses on both backends — PostgreSQL keeps the labels in
  `pg_enum` and SQLite inside a `CHECK`, neither of which is compared, and
  SQLite's `VARCHAR(n)` only moves when the *longest* value changes, so not
  even `compare_type` notices. `op.replace_enum(...)` performs the change by
  renaming the old type, creating the new one, casting every dependent
  column and dropping the old — ordinary DDL that runs inside Alembic's
  transaction, unlike the `ALTER TYPE ... ADD VALUE` everyone reaches for
  first, which cannot run in a transaction block on older servers and can
  neither remove nor reorder. Column defaults are read from
  `information_schema` and restored after the cast, `value_map=` keeps rows
  through a rename, and `reverse()` gives `downgrade` for free.

  `render_enum_types` fixes the third gap: Alembic rendered `TempestEnum` as
  a dotted path into this package in a file that imports only `alembic.op`
  and `sqlalchemy as sa`, so **every migration touching an enum column
  failed on import**. It now renders a plain `sa.Enum` with the values
  spelled out, which also makes the migration a real snapshot. Both hooks
  are wired into the generated `env.py`.

  Detection is deliberately conservative: an enum the backend cannot report
  on is skipped rather than diffed against a guess, since a wrong
  `replace_enum` would drop values from live rows. Offline (`--sql`) mode on
  PostgreSQL raises instead of silently emitting a script that loses the
  column default.

- **`explain_queries()` — query plans for a block of code.** Wrap the code,
  get one plan per statement: `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` on
  PostgreSQL with cost, measured time and actual-versus-estimated rows;
  `EXPLAIN QUERY PLAN` on SQLite, reported honestly as
  `ExplainDetail.PLAN_ONLY` with `total_cost` and `duration_ms` left `None`
  rather than defaulted to zero. Statements are recorded during the block
  and explained on exit, so the explaining never perturbs what is being
  measured, and plans captured before a failure survive it.

  **Writes are never re-executed.** `EXPLAIN ANALYZE` runs what it explains,
  so only `SELECT` is analyzed; anything else — including an unrecognized
  raw `text()`, classified as a write on purpose — is explained without
  `ANALYZE`. Plans are fetched at the driver level because routing them
  through `session.execute` applies the *wrapped* statement's result mapping
  to the plan rows, handing a UUID processor an integer node id.
  `report.slowest` falls back from measured time to planner cost so it still
  answers "which one first?" on a backend that times nothing. Recipe:
  `docs/recipes/query-plans.md`.

### Changed

- **BREAKING: `Mapped[SomeEnum]` now stores the member value, not its
  name.** A column declared as `Mapped[OrderStatus]` on a `BaseModel`
  previously wrote `IN_PROGRESS`; it now writes `in_progress`. Enums whose
  members are spelled `OPEN = "OPEN"` are unaffected. If yours are not, the
  existing rows hold the old spelling and need a data migration —
  `op.replace_enum(..., value_map={"IN_PROGRESS": "in_progress"})` performs
  exactly that. On SQLite the column also gains a `CHECK` constraint it did
  not have, and on PostgreSQL the generated type name gains an `_enum`
  suffix. A column that must keep the previous behavior can declare it
  explicitly with `mapped_column(sqlalchemy.Enum(...))`, which always wins
  over the annotation map.

- `where=` on `get` / `get_or_none` / `first` / `list` / `paginate` /
  `cursor_paginate` / `count` / `exists` / `delete_many` (and their
  `TenantScopedRepository` counterparts) accepts `WhereClause`, which is
  `Q | ColumnElement[bool]` — a `Q` still resolves lazily, and a ready-made
  clause now flows straight through. Purely widening; existing calls are
  unchanged.

## [0.199.0] — 2026-08-02

### Fixed

- **Every model load printed a deprecation warning from `transformers`.**
  The five loaders passed `torch_dtype=` to `from_pretrained`, which
  `transformers` renamed to `dtype`:

  ```text
  [transformers] `torch_dtype` is deprecated! Use `dtype` instead!
  ```

  One line per load, in the logs of every service that hosts a model, for a
  keyword the caller never chose. The SDK now picks the name the installed
  version wants.

  The choice is version-gated rather than switched outright because the SDK
  supports `transformers>=4.44`, and the boundary was measured against the
  released wheels instead of assumed: **4.55.0 rejects `dtype` and never
  warns; 4.56.0 accepts it and starts warning about `torch_dtype`; 5.x keeps
  both.** Sending `dtype` to an older release would have been worse than the
  warning — unknown keywords are forwarded to the config, so the precision
  would be dropped in silence. `DTYPE_KWARG_RENAMED_IN` carries that
  provenance, and a test pins both sides of the boundary.

  Scoped to the `transformers` loaders (`TextGenerator`,
  `VisionTextGenerator`, `Embedder`, `Reranker`, `ClassifierModerator`).
  `ImageGenerator` goes through `diffusers`, which took `dtype` much later
  than the `diffusers>=0.31` this SDK supports, so the image path still
  sends `torch_dtype` on purpose.

### Added

- **`GENAI_CACHE_DIR`, `GENAI_OFFLINE` and `GENAI_HF_TOKEN`, plus the
  `GenAISettings` mixin.** Where the weights live and whether a load may
  use the network are deployment facts, and they were only expressible as
  arguments — repeated at every `TextGenerator(...)`, `Embedder(...)`,
  `SpeechToText(...)` in the codebase.

  The three now have environment variables that act as **defaults**, so a
  service sets them once in compose or the chart. **An argument always
  wins, in both directions**: with `GENAI_OFFLINE=true` in the
  environment, an explicit `local_files_only=False` still reaches the
  network. That is why `local_files_only` became `bool | None` — `None`
  means "take the environment", which a plain `False` could never express.

  `GenAISettings` is the typed face of the same three variables, for
  services that want them in `Settings` and in `tempest check-config`. The
  loaders read the environment directly, so declaring it is optional.

### Changed

- **The Model weights recipe explains the Hub's rate-limit warning.** It
  shows up on a run that downloads nothing, which reads like a bug: with
  `local_files_only=False` the load still contacts the Hub to resolve the
  revision, and that anonymous request is what prints it. Setting
  `local_files_only=True` removes the request and the warning — now
  documented with the before/after.

## [0.198.0] — 2026-08-02

### Added

- **`GET /auth/me`, mounted by `make_auth_router`.** Resolving the bearer
  token to its account is the most generic thing an authenticated API
  does, and the SDK made every project hand-write it — the auth recipe
  literally shipped the snippet to copy. It is now part of the bundled
  router: no wiring, no schema to define, no route to maintain.

  The response model defaults to the new **`AuthUserSchema`**, which
  covers exactly the columns `BaseUserModel` guarantees (`id`,
  `is_active`, `created_at`, `updated_at`, `email`, `is_admin`,
  `last_login_at`). The handler returns the ORM instance and lets
  FastAPI serialize through the model, so `hashed_password` cannot reach
  the wire even though the row carries it — a test asserts exactly that.

  A project whose user table has extra columns subclasses the schema and
  passes it as `me_response_model=`:

  ```python
  class UserResponseSchema(AuthUserSchema):
      name: str | None = None


  app.include_router(
      make_auth_router(
          auth_service,
          session_factory=get_db,
          me_response_model=UserResponseSchema,
      )
  )
  ```

  The endpoint reuses the router's existing authenticated-user
  dependency, so it accepts the access-token cookie in the `cookie` /
  `both` delivery modes with no extra configuration, and answers **401**
  for a missing or invalid token and **404** when the token is valid but
  the account is gone.

## [0.197.0] — 2026-08-02

### Added

- **`[websocket]` extra** — pulls `websockets`, the protocol driver a
  bare `uvicorn` lacks. Without it the handshake answers **404** and the
  reason (`No supported WebSocket library detected`) only reaches the
  server log; worse, Starlette's `TestClient` implements WS itself, so a
  full test suite passes while the deployed server rejects every
  connection. `make_websocket_router`'s docstring and the recipe now say
  so up front.

- **`WebSocketHub.send_many({user_id: envelope, ...})`** — a different
  frame per user, dispatched with `asyncio.gather`. `broadcast` sends one
  payload to everyone and `send_to` serves one user, which left
  per-recipient fan-out (fog of war in a game, a personalized feed) as N
  sequential `send_to` calls, each waiting on the previous socket. The
  cost is now the slowest socket, not the sum.

### Fixed

- **The heartbeat never closed a half-open peer.** `_heartbeat_loop` only
  emitted pings: nothing read the `pong`, `WS_HEARTBEAT_TIMEOUT_SECONDS`
  was dead configuration, and no socket was ever closed with `4408` —
  while the module docstring promised exactly that. A peer that stopped
  answering (and never fails a `send`) pinned its hub slot forever.

  The router now records the last `pong` and closes with `4408` once the
  gap crosses `WS_HEARTBEAT_TIMEOUT_SECONDS`. **Clients must reply**
  `{"type": "pong", "data": {}}` to the router's ping; the frame is
  consumed by the router and never reaches the handler.

- **`WS_MAX_MESSAGE_BYTES` was announced and never applied.** The setting
  documented "reject inbound frames larger than this", but the router
  passed everything straight to the handler — an advertised defense that
  did not exist, which is worse than none. Oversized frames now close the
  socket with `1009` before the handler allocates the payload.

  Both are enforced by wrapping `ws.receive` once at accept time: every
  `receive_text` / `receive_bytes` / `receive_json` funnels through it,
  so the handler keeps owning the message loop.

- **The scaffolded `src/core/exceptions.py` taught the warned-about
  pattern.** It only re-exported `AppException` / `NotFoundException`, so
  the first subclass a reader wrote inherited a generic `code` and
  tripped `InheritedErrorCodeWarning` on the first run. The template now
  ships a worked `ItemNotFoundException` with `code = "ITEM_NOT_FOUND"`
  and explains why the class body is where `code` belongs.

## [0.196.0] — 2026-08-02

### Added

- **`TextModel`, `EmbeddingModel`, `RerankerModel`, `VisionModel`,
  `ImageModel`, `SpeechToTextModel`, `TextToSpeechModel`** — `StrEnum`s
  naming the model ids the SDK is exercised against, exported from
  `tempest_fastapi_sdk.genai`. Every constructor still takes a plain
  `str`; the enums exist so a typo is a `NameError` at import instead of
  a 404 mid-download, and so the choice itself is documented:

  ```python
  from tempest_fastapi_sdk.genai import TextGenerator, TextModel

  gen = TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT)
  ```

  The new recipe **Escolhendo o modelo / Choosing a model** carries the
  use-case table behind each pick — size, VRAM, language coverage, and
  the traps (E5 needs `query: ` / `passage: ` prefixes; a turbo image
  checkpoint wants ~4 steps where a full one wants ~30; changing the
  embedder invalidates an existing index).

- **`ChatBackend` and `ToolCallingBackend` protocols** in
  `tempest_fastapi_sdk.agents`, with `AgentBackend` as the union. `Agent`
  now takes `generator: AgentBackend` instead of `generator: Any`, so
  passing an embedder or a bare string is a type error at the call site
  rather than an `AttributeError` inside the loop. `TextGenerator` and
  `OllamaGenerator` satisfy `ToolCallingBackend` unchanged.

### Fixed

- **`tempest new` scaffolded services as installable packages.** The
  generated `pyproject.toml` carried a `[build-system]` table (hatchling)
  plus `[tool.hatch.build.targets.wheel] packages = ["src"]`. A service is
  deployed, never published — so every `uv sync` / `uv add` built and
  installed a wheel out of `src/` for nothing, and any deviation from the
  layout the build backend expects turned a routine dependency change into
  a build failure.

  The scaffold now declares `[tool.uv] package = false` instead. Imports are
  unaffected: `python main.py` runs from the project root, and
  `tests/__init__.py` makes pytest prepend that same root to `sys.path`.

  Existing projects apply the same fix by hand — drop the `[build-system]`
  and `[tool.hatch.build.targets.wheel]` tables, then add:

  ```toml
  [tool.uv]
  package = false
  ```

- **The `[admin]` extra was missing `python-multipart`.** The admin login
  route declares `identifier: str = Form(...)`, and FastAPI raises at import
  time when `python-multipart` is absent:

  ```text
  RuntimeError: Form data requires "python-multipart" to be installed.
  ```

  `tempest new` enables `admin` by default, so a freshly scaffolded service
  crashed inside `create_app()` unless `[upload]` happened to be installed
  too. `python-multipart>=0.0.12` is now a hard dependency of the extra.

- **186 documented examples awaited at module level.** Copy-pasting one
  into a file raised `SyntaxError: 'await' outside function` before it did
  anything — the docs promise runnable examples and these were not. Every
  offending fence across 75 pages (both languages plus the README) now
  wraps the awaiting part in `async def main()` and ends with
  `asyncio.run(main())`, or moves it into the FastAPI endpoint / lifespan
  it belongs to.

  `tests/test_docs_examples_compile.py` keeps it from coming back: it
  `compile()`s every Python fence in `docs/` and `README.md` and fails on
  async-context errors. It uses `compile()`, not `ast.parse()` — the
  "await outside function" rule is enforced by the symtable pass, so
  `ast.parse("x = await f()")` succeeds and a guard built on it would be
  silently vacuous.

- **The agents recipe told readers no extra was needed.** The install line
  said `uv add "tempest-fastapi-sdk"`, then every example injected a
  `TextGenerator` — which raises `ImportError: Text generation requires the
  optional [genai] extra.` on first instantiation. The page now installs
  `[genai]` and says which extra each tool pulls in, and the "tools over
  your local models" example instantiates every model it passes instead of
  referencing undefined names.

## [0.195.0] — 2026-08-02

### Fixed

- **The compact reader routed rows differently from scikit-learn when a
  value sat exactly on a split threshold.** `sklearn.tree` casts its input
  to float32 before traversing, so a threshold stored as
  `5.099999904632568` — a float32 value widened for storage — and an input
  of `5.1` compare **equal** there and go left. Comparing in float64 sends
  that row right instead.

  On a 20-tree iris forest that flipped one tree's vote for 2 of 105
  training rows, moving a probability by exactly 0.05. The export
  verification caught it and refused to write the file, which is the
  behaviour that format has verification for — but the reader was wrong, so
  the refusal was the symptom, not the bug.

  Both decoders now compare in float32. Pinned by a test that asserts the
  boundary rows exist in the dataset, so a fixture that stopped exercising
  the case would fail rather than pass quietly.

  Found while writing the beginner's first example for the docs: iris plus a
  random forest is the most obvious thing a newcomer runs, and it did not
  work.

### Documentation

- **A "Comece por aqui" opening for the modelops recipe**, in both
  languages: one block that **runs as written** (dataset included with
  scikit-learn) taking a model from `fit` to a published package to a
  prediction, plus a "which case is mine?" table routing the reader to the
  right section, plus a five-word glossary (ONNX, graph, quantise, drift,
  baseline) defined in plain language at the point a newcomer meets them.

  Every example on the page previously assumed `model`, `X_train` and
  `y_train` already existed — nothing was copy-pasteable, which the project's
  own docs standard requires.

## [0.194.0] — 2026-08-01

### Added

- **`export_sklearn_to_compact` — a model format that needs no inference
  runtime.** ONNX in a browser costs a 25.6 MB WebAssembly download (6.0 MB
  gzipped) before the first prediction, while the model itself is 20 KB. For
  an app whose only model is tabular, the runtime *is* the download — so
  this format drops the runtime instead of the model. A linear model is a
  dot product and a tree is a chain of comparisons; the reader in
  `tempest-react-sdk/tabular` is **1.49 KB brotli**.

  **Verified against scikit-learn, which is the only claim worth making about
  a reimplementation of someone else's arithmetic**: the exporter decodes the
  file it just wrote and compares with the estimator's own `predict` /
  `predict_proba`, refusing to ship a file that disagrees. Measured across
  seven families, the largest probability difference was 1.6e-05.

  Covers linear models (logistic, linear, ridge, SGD, linear SVC), trees,
  forests, extra-trees, their regressors, and `StandardScaler` /
  `MinMaxScaler` folded from a Pipeline. Anything else — gradient boosting,
  MLPs, PCA in the pipeline — raises `UnsupportedEstimatorError` naming the
  ONNX route, because a format that silently dropped a step would produce a
  model that runs and answers wrongly.

  **The file is data, never code**: no generated JavaScript, no `eval`,
  nothing a strict CSP forbids. `TMC1` is a magic, a JSON header padded to an
  8-byte boundary (a JavaScript `Float32Array` cannot view an unaligned
  offset) and typed-array sections. `read_compact` and `predict_compact` are
  the reference decoder the browser's is checked against.

- **`edge_pipeline(compact=True)` and `RuntimeArtifact`.** A package can now
  carry the same model twice — ONNX for coverage, compact for size — and the
  manifest's new `runtimes` list lets the consumer pick by what it can
  actually run. `tempest-react-sdk` reads that list and defaults to compact
  when present.

  The option raises rather than silently writing only ONNX when the
  estimator has no compact form: a package that quietly lacks the file the
  app expects fails later, in a browser, on someone else's phone.

### Fixed

- **The reference guard read a stale build.** `tests/conftest.py` rebuilt the
  docs when `docs/`, `mkdocs.yml` or the hooks changed — but `mkdocstrings`
  renders the API reference from **docstrings**, so exporting a new symbol
  changed the expected HTML without touching any watched input. The guard
  then reported brand-new exports as undocumented. The package source now
  counts as a docs input. Found by hitting it while adding the compact
  format, not by review.

- **The compact route returned `"0"` where ONNX returns `0`.** Two routes
  over one model that disagree on the type of a label break the day someone
  switches between them. The format now records scikit-learn's class dtype
  and both readers hand back the same type. Found by the cross-language test,
  not by review.

## [0.193.0] — 2026-08-01

### Added

- **`edge_pipeline_from_pickle` and `load_sklearn_artifact` — the bridge from
  what training hands over to what a browser can run.** A training pipeline
  produces `joblib.dump(...)`; a browser cannot use it, because a pickle is a
  Python program rather than data, and running one would need a Python
  runtime in the page. The bridge belongs to the **build**: read the pickle
  once, in your own environment, and publish the ONNX package that devices
  and browsers actually load.

  **Loading a pickle executes arbitrary code**, so `load_sklearn_artifact`
  takes a local path and refuses a URL with an explicit error. Not as
  protection — anyone can download first — but so the shape "load the model
  from this URL" never exists in the code, since that is what turns a model
  registry into a remote-code-execution surface.

  It does four things `joblib.load` does not:

  * **Recovers the column order** from the estimator's `feature_names_in_`,
    so a model fitted on a DataFrame carries its columns into the manifest.
    That is the field that catches the failure no runtime check can — the
    right features in the wrong order.
  * **Finds the estimator inside a dict** (`{"model": est, "auc": 0.91}` is
    what pipelines actually dump). One estimator resolves silently; two make
    it refuse and list what it found rather than guess. `key=` decides.
  * **Records provenance** — the pickle's name, SHA-256, size and the
    scikit-learn version that performed the conversion — in a new optional
    `source` block on the manifest, so an artifact running on a device can
    be traced back to the file that produced it.
  * **Refuses what cannot predict** with a direct message instead of letting
    the failure surface inside the converter.

- **`ArtifactSource` on `EdgeManifest`.** Optional and additive, so
  `schema_version` stays `1` and existing readers — including
  `tempest-react-sdk/tabular`, which ignores unknown fields by design — keep
  working unchanged.

### Documentation

- **A pickle section in the modelops recipe, both languages**, stating the
  trust boundary plainly (ONNX is data, a pickle is a program) and a
  measured caveat: on scikit-learn 1.9 a model pickled by one version and
  loaded by another emits **no warning and stores no version field**. The
  mismatch is silent, which is another argument for converting at build time
  rather than shipping the pickle.

## [0.192.0] — 2026-08-01

### Changed

- **`PredictionMonitor.observe` is 11x faster, because measuring it showed
  monitoring cost more than inference.** Running the SDK's own instruments
  over the serving stack found the drift binning at **67 us per single-row
  call against 7.5 us of actual inference** — the monitor was 9x the model.
  The cause was the obvious shape: a Python loop per feature, then per bin.

  It is now vectorised — one comparison against a padded edge matrix
  resolves every feature of every row, and a single `bincount` folds the
  batch into flat counters allocated once. Measured after: **6.0 us** for one
  row (from 67.1) and **49.7 us** for 64 rows (from ~104). Counters are a
  single `int64` array instead of a list of lists, so the constant-memory
  promise is now literally one allocation.

  No behaviour change: the same bins, the same PSI, the same reports. The
  window reset refills the existing array rather than rebuilding it.

### Documentation

- **A measured optimisation playbook** in the modelops recipe, both
  languages, produced with `benchmark_models` / `analyze_onnx` / `rank` /
  `PredictionMonitor` over 7 estimators on one dataset:

  * **The transport dominates, not the model.** One row over HTTP costs
    1.22 ms against 0.0075 ms of inference — 160x — measured with an
    in-process client, so a real network is worse. Batching moves the
    per-row cost from 1,223 us to 16 us (batch 512), a 74x win that no model
    choice can match. Per-row inference plateaus at ~1.5 us from batch 8.
  * **"Tabular means forest" cost 880x the size for worse accuracy** on this
    dataset: a 300-tree forest is 13.4 MB at 0.931, a small MLP is 15.3 KB
    at 0.966, in the same latency band. Not a law — the point is that
    `benchmark_models` answers it in three lines.
  * **gzip does not pay the same on every model**: 15% on a forest, 23% on a
    tree, **84% on a logistic regression**. The earlier "10-13%" figure is
    specific to tree ensembles, and the docs now say so.
  * **Cold start**: `read_manifest` 0.15 ms, `load_edge_package` 2.16 ms with
    the SHA-256 check and 2.00 ms without — so the digest check stays on by
    default, and the cheap manifest read is what makes polling for a new
    version viable.
  * **Energy is reported as `unavailable` rather than invented** on a host
    without powercap or NVML — stated in the playbook with the reason.

## [0.191.0] — 2026-08-01

### Added

- **`edge_pipeline` — from a fitted estimator to a directory two runtimes
  consume.** `edge_bundle` answers "what does each optimisation stage cost on
  my model"; this answers the question after it: what you actually publish,
  and how the thing running it knows what it got. Writes the graph, a gzipped
  copy, the drift baseline and a `manifest.json`, and you ship the directory
  whole.

  **The manifest is a cross-language contract**, with a pinned
  `schema_version`: the same directory is served as static assets to
  `tempest-react-sdk/tabular` in the browser. It carries the column order
  used at training (the field that catches two swapped columns — nothing
  else in the package does; a model fed the right columns in the wrong order
  answers confidently and wrongly), the classes in score order, the SHA-256,
  and a content-derived version so republishing identical bytes never looks
  like a new model.

- **`load_edge_package`** — one line to a predictor plus a `PredictionMonitor`
  already wired to the package's baseline, with the version stamped onto
  every report. Verifies the model against the manifest digest first, so a
  truncated download fails as a digest mismatch rather than as a protobuf
  error — or worse, than not at all.

- **`read_manifest`** — reads the package description without loading the
  graph, so a device can answer "is the published version the one I have"
  on a schedule.

- **`edge_pipeline` refuses to package an export that does not reproduce the
  estimator.** The known converter defects (binary tree ensembles on
  skl2onnx 1.20) produce a graph that runs smoothly and answers wrongly, so
  a silent pass is the one outcome this gate exists to prevent.

### Changed

- **Corrected the threading guidance in `DEFAULT_INTRA_OP_THREADS` and the
  modelops recipe.** The previous text justified the default of one thread
  with "on a small device the threads spend more time coordinating than
  computing". That was reasoned, not measured, and measuring does not
  support it: on a 300-tree forest, 1000 rows went from 16.6 ms at one
  thread to 2.3 ms at eight, and even a single row improved (0.019 ms to
  0.010 ms). The default is still one, for a different and real reason —
  in a service with concurrent requests, per-request threads oversubscribe
  the CPU and every request gets slower. The rule is now stated as the shape
  of the workload: a batch or a large ensemble wants threads, a small graph
  is indifferent (a logistic regression measured 0.213 ms vs 0.214 ms for
  1000 rows across 1 and 8 threads).

### Documentation

- **The measured edge-optimisation table**, in both languages. Run on real
  forests of 10 to 300 trees: graph optimisation changes the file by 0.1 KB
  (the `ai.onnx.ml` operators are single nodes, nothing to fuse), `.ort`
  conversion more than doubles it at every scale (381 KB to 878 KB; 12.1 MB
  to 27.0 MB), int8 quantisation does not apply at all, and gzip takes it to
  10-13%. Three of the four stages do not pay, which is why the pipeline
  runs only the ones that do.
- **Where size is actually decided**: a 50-tree forest at `max_depth=6` is
  257 KB at 0.881 test accuracy against 1444 KB at 0.922 unlimited — 5.6x
  the bytes for 4 points — while single-row latency moves from 0.0075 ms to
  0.0079 ms. On the edge the currency is bytes, not milliseconds, and the
  estimator is the lever.

## [0.190.0] — 2026-08-01

### Added

- **Edge monitoring — `PredictionMonitor`, because a device that answers in
  3 ms tells you nothing about whether the answers are right.** In
  production there are no labels, so accuracy is not measurable on the
  device. Three proxies are, and the module says they are proxies rather
  than pretending otherwise:

  * **Latency and volume** — the only signal that catches a thermally
    throttled device or a provider that silently fell back to CPU.
  * **Input drift** — live features against a training-set baseline, by
    Population Stability Index.
  * **Prediction distribution** — what the model is answering, against what
    it answered on the training data. This catches the failure input drift
    misses: features within their usual ranges, combined in a way that
    pushes every row to one class.

  Read together they diagnose: input moved with stable output is usually a
  harmless covariate shift; output moved with stable input means the model
  is extrapolating; both moved means retraining.

- **`baseline_from_samples`** — summarises the training set into bin edges
  and proportions, never the rows. A few kilobytes, no records, versionable
  next to the model. The docstring says to build it at training time:
  building it from production traffic would describe the already-drifted
  population as normal.

- **`population_stability_index`** with the conventional thresholds as named
  constants — `PSI_MODERATE = 0.1`, `PSI_SIGNIFICANT = 0.25`, from
  credit-scorecard practice, pinned by a test. **Documented as a convention,
  not a statistical test**: PSI has no p-value and no null distribution, so
  a crossing is a reason to look, not to act automatically. Below
  `MIN_ROWS_FOR_DRIFT` (100 rows) the verdict is `insufficient_data` rather
  than `stable` — with 30 rows across 10 bins an empty bin is the expected
  outcome of sampling, and "no traffic yet" is a different answer from "no
  drift".

  A constant training feature gets a narrow middle bin instead of degenerate
  edges: with one catch-all bin, drift on that feature could never be
  detected.

- **Constant memory, by construction.** Rows are counted into bins and
  discarded — `n_features x n_bins` counters regardless of traffic. Nothing
  retains a copy of the requests, so no feature value stays in memory to
  leak into a log or a crash dump. Drift is measured per window
  (`DEFAULT_WINDOW_ROWS`), so the numbers track recent traffic rather than
  everything since boot.

- **`PredictionMetrics`** — the same numbers as Prometheus metrics on the
  registry the SDK's `/metrics` endpoint already serves. Split from the
  monitor on purpose: counters and the latency histogram are cheap per
  request, drift gauges only change when a window closes. Insufficient
  samples publish no gauge, so a noisy PSI from 12 rows never becomes a
  spike on a dashboard.

- **`make_prediction_router(..., monitor=, metrics=)`** — records every
  request and mounts `GET /monitor`. A `422` is not counted: it never
  reached the model. `POST /model/sync` resets the monitor when the version
  actually changes, since mixing two versions into one latency percentile
  hides exactly the regression a fleet update needs to catch.

## [0.189.0] — 2026-08-01

### Added

- **`OnnxPredictor` — running an exported model on the device that has to
  run it.** Exporting produces a file; this is everything between that file
  and an answer, and it is code every consumer otherwise writes identically
  and gets subtly wrong: which input is the input (the name is not constant
  across exporters), which output is a label and which is a score (indexing
  `[1]` works until you serve a regressor), dtype coercion, and the warm-up
  that moves first-call allocation off the first real request.

  **Threads default to one intra-op.** ONNX Runtime's own default is one per
  core, which is right on a server and often wrong on a constrained device:
  on a 4-core SBC running one small model per request, the threads spend
  more time coordinating than computing. `DEFAULT_INTRA_OP_THREADS = 1`
  says so, and the docstring says to measure on the target device before
  raising it.

  `PredictorInfo.providers` reports the providers **actually in use**, not
  the ones requested — ONNX Runtime falls back to CPU silently, so a device
  you believe is on CUDA may not be.

- **`make_prediction_router`** — `POST /predict`, `GET /model` (what is
  loaded, which providers, how many threads), and `POST /model/sync`. A row
  of the wrong width is a `422`, not a `500`.

- **`RegistryModelSource` — updating a fleet without a deploy.** The device
  asks the existing `ArtifactRegistry` which version is current, downloads
  it if absent, and reloads. `sync()` is a no-op when the right version is
  already loaded, so it is safe on a schedule.

  **A bad rollout degrades to the previous version, never to nothing.**
  `OnnxPredictor.reload` builds the new session *before* dropping the old
  one, so a corrupt or unloadable file leaves the device serving the
  previous model. A fleet that can go silent from a deploy is worse than one
  that is occasionally out of date. One file per version is cached, so a
  rollback is a reload rather than a re-download, and nothing is deleted
  automatically — on a small disk that is the operator's call.

## [0.188.0] — 2026-08-01

### Added

- **`tempest_fastapi_sdk.modelops.sklearn` — take a scikit-learn model to
  something an edge device runs.** New extra `[modelops-sklearn]`
  (`skl2onnx`, which declares only lower bounds and so constrains nothing).
  The rest of the edge path already existed here; this is the missing front
  end.

  `skl2onnx` does the conversion. This module makes the decisions it leaves
  to you, each of which is easy to get wrong in a way that only shows up in
  production:

  * **float32 by default.** scikit-learn works in double precision, edge
    runtimes want single. The conversion is almost always right and it
    *changes the numbers*, so it is stated and verified rather than assumed.
  * **ZipMap off by default.** `skl2onnx` otherwise wraps classifier
    probabilities in a `ZipMap`, emitting a **dictionary per row**:
    convenient in Python, and unusable on a minimal runtime that does not
    implement the operator. The output is a plain tensor instead.
  * **`verify_sklearn_onnx`.** Runs the estimator and the graph over the
    same rows and compares — label agreement for classifiers, maximum
    absolute difference for regressors. An export that silently disagrees
    with the model you trained is worse than one that fails, because you
    ship it.

  `edge_bundle` chains export → verify → optimise → quantise → `.ort` and
  reports the size after each stage.

### Measured, and reported honestly

Running this against real estimators produced three findings the
documentation now states outright rather than leaving to be discovered:

- **Integer quantisation does not apply to most scikit-learn models.** Trees,
  linear models and scalers convert to `ai.onnx.ml` operators whose
  parameters are node attributes, not weight tensors; the quantiser refuses
  with `Failed to find proper ai.onnx domain`. `uses_ml_domain` detects this
  and the stage is skipped **with the reason** instead of failing opaquely.
- **The optimiser and `.ort` conversion often make these graphs bigger.**
  They are kilobytes; the metadata added outweighs what is saved. So
  `edge_bundle` ships the **smallest** artifact produced, not the last one —
  returning a larger file and calling it optimised would be a lie the tool
  tells by default.
- **Binary tree-ensemble classifiers convert incorrectly** with `skl2onnx`
  1.20 + scikit-learn 1.9: the probability output is a decision score in
  `[-1, 1]` and predicted labels disagree with the estimator on a
  significant fraction of rows. Multi-class trees and linear models are
  fine. No converter option changes it — `zipmap`, `raw_scores` and four
  target opsets were tried. `SklearnExport.warnings` now flags the
  combination at export time, and verification catches it.

  This is exactly what the verification step is for, and it is why the
  recipe treats verifying as mandatory rather than optional.

## [0.187.0] — 2026-08-01

### Added

- **A SQL console for the admin, with a policy in front of it** — new extra
  `[admin-sql]` (`sqlglot`, zero further dependencies). Opt-in via
  `make_admin_router(sql_shell=...)`; without it the route does not exist.

  **The honest framing, stated in the module docstring and the recipe:** a
  SQL filter in the application is **defence in depth, not a security
  boundary**. The analyser parses statements properly rather than matching
  strings, which stops the ordinary accidents — a `DROP` typed by someone who
  meant `SELECT`, an `UPDATE` with no `WHERE`, a query against a table holding
  card data. It will not stop a determined operator, because SQL has CTEs,
  subqueries, functions and dialect extensions and any parser-based allowlist
  is a game of coverage. The boundary that holds is the **database role**;
  point the console at a restricted connection and let the policy narrow
  further and produce readable refusals.

  `SqlShellPolicy` carries `capabilities` (READ / INSERT / UPDATE / DELETE /
  DDL / DROP / ADMIN), `allowed_tables`, `denied_tables`, `max_rows`,
  `max_statements`, `require_where` and `statement_timeout_ms`. Defaults are
  the safest useful thing: read-only, one statement, WHERE required,
  1000 rows.

  Design points worth stating: **deny beats allow**, so a table in
  `denied_tables` cannot be re-permitted by a broad rule elsewhere.
  **Unclassifiable statements land on `ADMIN`**, the most privileged
  capability, so a construct nobody anticipated needs the highest permission
  instead of passing as harmless. **Subqueries and CTEs are walked**, because
  a policy reading only the top-level `FROM` would miss exactly where a table
  gets hidden — while CTE *aliases* are subtracted, so
  `WITH recent AS (SELECT … FROM orders)` reports `orders` and not `recent`.
  **Multi-statement input is refused by default**, since that is how an
  allowed `SELECT` carries a `DROP` past someone skimming the box. Reads run
  in a rolled-back transaction.

  Every attempt is audited through `SqlAuditor` — **including refusals**,
  because what someone tried to run is usually more interesting than what
  worked. Auditor failures are swallowed so a broken sink cannot take the
  console down.

  The page shows the active policy before you type, warns when the console
  can write, and renders a policy refusal differently from a database error:
  "you may not do that" and "your SQL is wrong" lead to different fixes.

## [0.186.0] — 2026-08-01

### Added

- **Fact stores that survive a restart.** `InMemoryFactStore` loses
  everything when the process dies, which is the one thing durable memory
  must not do. Two backends now keep it, both behind the same four-method
  protocol so swapping is a constructor change:

  * **`DbFactStore`** over `BaseFactModel` / `make_fact_model` — a row per
    fact, for when facts are part of your domain and you want them in
    backups, in the admin, joined against a user. Each operation opens its
    own short transaction, so a fact the agent asserted mid-run survives a
    run that later fails. The unique index on `(subject, key)` is
    **yours to declare** in the migration — the SDK cannot know whether your
    table is shared or partitioned, and without it a write race leaves two
    rows.
  * **`RedisFactStore`** — one hash per subject, so listing is a single
    `HGETALL` and every operation is O(1). For preferences shared across
    replicas where a migration is more ceremony than the data deserves.

- **`tempest_fastapi_sdk.agents.testing` — so you can test your agent.** An
  agent's behaviour depends on what the model decides, which makes it feel
  untestable. But almost every bug worth catching is in *your* code — a tool
  that mishandles an argument, a budget that never fires, a skill whose tools
  never unlock — and all of it is testable by **scripting the model's
  decisions**.

  `ScriptedBackend` (with `replies` / `replies_with_tool` /
  `replies_with_tools` / `tool_call`) replays a plan you wrote and records
  `system_prompts`, `prompts` and `specs_seen` per turn — which is how you
  prove a skill's tools stayed hidden until loaded, or that facts were
  actually injected. `FailingBackend` checks a backend outage becomes
  `StopReason.ERROR` rather than an exception escaping into a request
  handler. `assert_completed` / `assert_used_tools` / `assert_artifact` /
  `tool_steps` / `failed_steps` cover the assertions, and `assert_completed`
  exists because the common mistake is checking only `run.output` — a
  budget-truncated run carries text too, so that assertion passes on
  half-finished work.

  These are the helpers the SDK's own 200+ agent tests use; they are exported
  because your agent deserves the same treatment, and because a test that
  boots a real model is slow, flaky and tests the wrong thing.

### Documentation

- **New recipe: testing and validating an agent** (`agents-testing.md`, both
  languages) — the minimal test, error recovery, budgets, proving on-demand
  skills and injected memory, backend outages, and a separate `@model` layer
  for the one question scripting cannot answer: does the model pick the right
  tool.

## [0.185.0] — 2026-08-01

### Added

- **Three agent memory layers, all opt-in.** "The agent should remember"
  hides three separate needs, and picking the wrong one is why memory
  features disappoint:

  * **Scratchpad** (`scratchpad_tools`, `scratchpad`) — lives for one run.
    A long run derives something several steps before it needs it; without
    somewhere to park it the model either re-derives (slow, and the second
    answer may differ) or carries it in the conversation, competing for
    attention. Notes live on `AgentContext.state` and vanish with the run,
    which is the feature: a note from an unrelated run turning up mid-task
    is worse than no notes.
  * **Facts** (`Fact`, `FactStore`, `InMemoryFactStore`, `fact_tools`,
    `facts_prompt`) — durable and **editable**. `subject=` isolates by user
    or tenant; `allow_forget=False` makes them read-only for the model,
    because a model that can delete what it disagrees with will.
    `facts_prompt` injects them rather than making the model ask — by the
    time it realises it needs the timezone it has usually answered in the
    wrong one.
  * **Recall** (`recall_prompt`) — durable and **fuzzy**, over the existing
    `ChatMemory`. Returns an empty string when the backend fails, so a
    vector-store outage never stops the agent.

  The distinction that matters is facts vs recall. A fact is asserted and
  exact — listable, editable, showable to the user. Recall is retrieved and
  approximate, which is powerful and unauditable. Storing "the user's plan
  is enterprise" in recall means nobody can correct it; storing a whole
  conversation as a fact means nothing useful comes back. The recipe leads
  with that table.

## [0.184.0] — 2026-08-01

### Added

- **`Skill` — capabilities the agent loads only when it decides to use
  them.** Every tool an agent can call sits in its prompt, and every line
  there costs context and dilutes attention. Ten well-documented
  capabilities is more instruction than a small local model can hold, and
  quality drops on all ten.

  A skill splits what the model needs to **choose** from what it needs to
  **do**: the name and one-line description are always in the prompt, while
  the full instructions *and the skill's own tools* arrive only after the
  model calls `load_skill`. A hundred capabilities cost a hundred short
  lines, and the one in use gets the whole page. `Agent(skills=[...])` wires
  the loader and the prompt block; the tool list is recomputed each turn, so
  a skill's tools genuinely do not exist until it is loaded — and calling
  one early gets the usual `unknown tool` observation the model recovers
  from.

  `skill_from_markdown` and `discover_skills` read skills from
  `<dir>/<name>/SKILL.md` with the same frontmatter format Claude Code
  uses, so one file works in both places and a deployment can add a
  capability without a code change. Tools stay in Python and are attached
  afterwards. A missing skills directory returns `[]` rather than raising,
  so a service starts fine without one. `loaded_skills(context)` reports
  what a run actually reached for — usually the first thing you want when it
  took a wrong turn.

## [0.183.0] — 2026-08-01

### Added

- **`@tool` — agent tools whose arguments are a Pydantic model.** Writing
  JSON-schema by hand next to a handler means two descriptions of the same
  thing, drifting apart from the first edit: the schema says `city`, the
  handler reads `arguments["town"]`, and nothing catches it until a model
  calls the tool. The decorator reads the args model off the handler's first
  annotation and generates the schema from it, so there is one description.
  The handler receives a **validated instance** (`args.city` is typed and
  checked), and because validation now runs *before* the handler, a model
  that invents an argument gets `invalid arguments for get_weather: city:
  Field required` back as an observation — precise enough to correct from —
  instead of a `KeyError` halfway through your code. Constraints (`ge`,
  `le`, enums) are enforced the same way. `typed_tool` is the non-decorator
  form; `schema_of` exposes the schema generation, which inlines Pydantic's
  `$defs`/`$ref` because local tool-calling models handle a flat object far
  more reliably. A malformed handler fails at **decoration**, not on first
  use.

- **`Agent.run_structured(goal, output=Model)` → `StructuredRun[Model]`.**
  An agent that ends in prose is useless to a pipeline. Rather than asking
  for JSON and parsing the reply, the agent is given a temporary
  `final_answer` tool shaped like your model, and calling it *is* how the
  model finishes — the arguments are the answer, already validated, carried
  by the tool-calling path the backend already handles well. `run.data` is
  an instance of your model; `run.parse_error` says why when it is `None`.
  `structured_verdict` composes with `run_until` to retry until the shape
  arrives.

  **Extraction pass.** Validating against a real local model showed the case
  that actually happens: Qwen2.5-0.5B solved the task, called the right
  tool, and then answered in prose anyway — leaving nothing to parse. So
  when the prose carries no JSON, the SDK makes one more call whose **only**
  tool is the answer tool, asking the model to restate what it already said
  in that shape. With nothing else to call and no reasoning left to do, even
  a 0.5B model fills the fields. It costs one model call against losing the
  entire run; `extraction_retry=False` disables it.

## [0.182.0] — 2026-08-01

### Added

- **Delegation — `agent_tool(agent)` / `team_tools(...)`.** There is no
  separate "team" object, and that is the design: an agent already knows how
  to pick a tool by name and read what it returns, so the cheapest way to
  hand work to a specialist is to make the specialist **a tool**. What
  delegation does need is three guards a plain tool does not:

  * **The clock is inherited.** `AgentContext.deadline` carries an absolute
    instant, and each run takes the **earlier** of its own budget and the
    one handed down. A sub-agent may finish sooner than its budget allows
    but never later than its parent's, because the parent is holding a
    request open.
  * **Depth is bounded.** `max_depth` (3 by default) turns A→B→A from an
    infinite loop into a refusal the model reads and works around.
  * **The child's work comes back.** Artifacts merge into the parent
    namespaced as `<agent>/<name>`, so two specialists writing `report.md`
    cannot clobber each other, and a truncated child is reported as
    `[stopped: timeout] …` rather than passing partial work off as complete.

  New `StepKind.AGENT` plus `AgentStep.children` / `.agent` /
  `.total_steps`: a delegation is the one step that can cost as much as a
  whole run, and a trace where the expensive step looks like a function call
  is a trace you misread.

- **Autonomous loops — `run_until` and `refine`.** A single run stops when
  the model says it is done, which often means "out of ideas".
  `run_until(agent, goal, until=...)` repeats until a **predicate you wrote**
  accepts — ordinary Python that can parse the JSON, import the module or
  call your service, which is a far harder gate than asking the model
  whether it is satisfied. `refine(worker, critic, goal)` runs
  generate-critique-revise with two agents; the critic approves with the
  exact token `APPROVED` (free-form judgement hedges and cannot be branched
  on) and never rewrites, keeping the work and the accountability with the
  worker. Both carry every round in `LoopResult` / `LoopIteration`, bound
  total wall-clock across rounds, and report `accepted=False` when nothing
  passed — running out of rounds is not approval.

### Fixed

- **A delegated agent did not actually inherit its parent's clock.** The
  effective deadline was computed onto the run state but never written back
  to the `AgentContext`, so `context.child()` handed the sub-agent `None`
  and it ran to its own budget. Caught by the test written for the
  behaviour; the deadline now lands on the context that delegation reads.

## [0.181.0] — 2026-08-01

### Added

- **`tempest_fastapi_sdk.agents` — AI agents over the models you already
  self-host.** An agent takes a **goal**, decides what to do, calls tools and
  reports what it did. That last part is what separates it from
  `AIChatPipeline`, which answers a chat turn: a run comes back with a
  step-by-step trace — arguments, outputs, timings, failures — plus the files
  it produced. Submodule import, no extra required; the weight lives in the
  objects you inject.

  **`Agent.run(goal)` → `AgentRun`**, or `Agent.stream(goal)` yielding each
  `AgentStep` as it lands. Three properties are deliberate, each avoiding a
  specific bug:

  * **A tool that raises does not end the run.** The failure is recorded on
    the step and handed back to the model as an observation — a model told
    "no artifact named 'chart.png'; available: bike.png" usually fixes itself
    next turn, where an escaping exception would discard the whole run.
  * **Every ceiling is enforced and named.** `AgentBudget` bounds steps,
    wall-clock and tool calls, and `StopReason` says which one fired. Steps
    alone do not bound a run — one tool call can hang — so `max_seconds`
    defaults to 120 rather than being optional. `AgentRun.succeeded` is
    `True` only for `COMPLETED`, because a truncated run still carries text
    and a caller that ignores the reason ships half-finished work as done.
  * **Binary results never enter the prompt.** A tool returns `ToolResult`
    (text for the model, `AgentArtifact` bytes for the caller), and artifacts
    are held **by name** on the run.

  **Ready-made tools over the local models** — `generate_image_tool`
  (`ImageGenerator`), `describe_image_tool` (`VisionTextGenerator`),
  `transcribe_audio_tool` (`SpeechToText`), `speak_tool` (`TextToSpeech`),
  `retrieve_tool` (`Retriever`), `web_search_tool` (`WebSearch`),
  `save_artifact_tool`, plus `text_tool` for hand-written ones. They chain
  through named artifacts: an agent draws `bike.png` and the next step asks
  the vision model about `bike.png`, with the bytes never touching disk and
  base64 never touching the prompt. `AgentTool.from_tool` adapts an existing
  `AIChatPipeline` tool.

  **Persistence is opt-in**: nothing by default, `InMemoryAgentRunSink` for a
  bounded ring buffer (bounded because runs carry their artifacts, and an
  unbounded list of image-generating runs is a slow memory leak), or
  `BaseAgentRunModel` / `make_agent_run_model` / `DbAgentRunSink` for a table.
  The table keeps the trace and the artifact **names**, not the bytes — a run
  table is not a blob store. A sink failure never fails the run.

  **`make_agent_router(agent, run_store=...)`** serves `POST /run`,
  `POST /run/stream` (SSE, one event per step and a trailing `done`),
  `GET /runs` and artifact download. Artifacts travel as metadata in JSON and
  as raw bytes with a real media type on their own endpoint, so an `<img
  src>` works and a megabyte image does not get inflated by base64.

  Validated end to end against a real local model: Qwen2.5-0.5B on CPU chose
  the tool, extracted its argument, read the result and answered — three
  steps, 5.1 seconds.

## [0.180.0] — 2026-08-01

### Added

- **`GenAIMetrics.observe_inventory(report)`** — the runtime inventory as
  Prometheus gauges: `genai_models_loaded{kind,device}`,
  `genai_models_known`, and `genai_gpu_vram_free_bytes{index}` when the
  report carried a hardware snapshot. The existing counters answer "how much
  work went through"; these answer "what is resident *now*", which is the
  question that explains an OOM. Labelled series are cleared on every call
  because a gauge here describes a snapshot: a model that unloaded between
  two observations has to stop being reported, or the stale series reads as
  residency. Feed it from a periodic task.

- **`make_model_cards(models, include_vram=True)`**
  (`tempest_fastapi_sdk.genai.admin`) — the same inventory as admin
  dashboard cards for `AdminSite(dashboard_cards=[...])`: resident count
  (`2 of 5`), a per-device breakdown, and free VRAM. The cards ignore the
  `AsyncSession` the dashboard passes them, since this data comes from
  process memory rather than the database, and they read the handles at
  **render** time — a registry that is empty when the site is built still
  reports correctly once models load. `include_vram=False` drops the only
  card that probes the host.

### Documentation

- **The `genai` API reference was a hole.** `docs/reference.md` documented
  `genai.hub`, `genai.image` and `genai.inventory` — the submodules added
  this week — while the module those sit on top of had no reference entry at
  all. The top-level `tempest_fastapi_sdk.genai` surface plus `genai.rag`
  and `genai.audio` are now rendered there, 269 symbols in total.

## [0.179.0] — 2026-08-01

### Added

- **`tempest_fastapi_sdk.genai.inventory` — what is loaded right now.** A
  self-hosted service can hold a language model, an embedder, a reranker and
  a diffusion pipeline at once, each holding gigabytes of VRAM for as long as
  it stays resident, and nothing could report which. `ModelRegistry` knew how
  many entries it held, not which; every loader kept its idle clock to
  itself. Pure Python — imports with no extra installed.

  `describe_model(handle, key=...)` turns any SDK loader (or any object that
  walks like one) into a `LoadedModel`, reading **attributes only**: calling
  it on a generator that has never loaded reports `loaded=False` and leaves
  it unloaded, which is what makes it safe inside a health check. What a
  handle does not expose comes back `None` rather than guessed, and a handle
  implementing only `is_loaded` still appears — vanishing from a memory audit
  is worse than appearing with blanks. `LoadedModel.idle_past_threshold`
  answers "is this due to be freed", `False` whenever any input is unknown.

  `runtime_report(models, ...)` puts those next to `probe_hardware()` in a
  `ModelRuntimeReport`, sorted **loaded first, longest-idle first** — the
  order an operator reads when a card is full. `probe=False` skips the NVML
  read, the only part of the call that costs anything.

- **`ModelRegistry.inventory()` / `.items()` / `.unload_idle()`.**
  `unload_idle()` calls each handle's `unload_if_idle()` and returns the keys
  it freed; **entries stay registered**, because a generator that dropped its
  weights is still the right object to hand out and reloads on next use.
  That makes it the method a periodic task wants, where `evict()` /
  `evict_all()` forget the entry entirely. Handles without the hook are
  skipped rather than unloaded on a guess.

- **`GET /models` on `make_genai_router(models=...)`**, accepting a
  `ModelRegistry` or a plain dict/list of handles.

- **A uniform idle clock on the three loaders that lacked one.**
  `ClassifierModerator` tracked `_last_used` without exposing it and had no
  `unload` at all; `SpeechToText` had `unload` but no idle clock;
  `OnnxEmbedder` had neither. All three now carry `seconds_idle` /
  `unload_if_idle` / `idle_unload_seconds` (`OnnxEmbedder` gains `unload`),
  matching the loaders that already had them.

### Fixed

- **`make_genai_router` refused an empty `ModelRegistry`.** The guard tested
  the injected objects for truthiness, and a registry defines `__len__`, so
  an empty one — the normal state at startup, before anything is loaded —
  read as "nothing injected" and raised. It now tests `is None`, so the
  endpoint can be mounted at the only moment you can mount it. An empty
  `dict`/`list` of handles was affected the same way.

## [0.178.0] — 2026-08-01

### Added

- **`ImageGenerator(pipeline_kwargs=...)`** — extra keywords forwarded to
  `from_pretrained`, applied last so they override what the SDK computes
  (including `torch_dtype`).

  Found by validating v0.177.0 against a real diffusion pipeline: the load
  itself takes decisions the SDK does not model, and `.pipeline` cannot
  express them because by the time you hold it the cost is already paid.
  The three that come up immediately are `{"safety_checker": None}` (Stable
  Diffusion 1.x/2.x repositories bundle an extra CLIP purely to filter — it
  costs memory, and a tiny test pipeline crashes on it outright),
  `{"variant": "fp16"}` (roughly halves the download) and
  `{"use_safetensors": True}` (refuse a pickle checkpoint). The dictionary
  is copied, so a later mutation by the caller cannot change what a
  subsequent load sends.

## [0.177.0] — 2026-08-01

### Added

- **`ImageGenerator` — the generative modality the SDK was missing.** It
  already generated text, understood images (VLM), embedded, transcribed and
  synthesized speech; it could not draw. `tempest_fastapi_sdk.genai.image`
  runs a HuggingFace diffusion pipeline on your own hardware, behind a new
  `[genai-image]` extra (`diffusers` + `pillow`); the module imports without
  it.

  It deliberately mirrors `TextGenerator` — same device/precision
  resolution, same lazy `load`, same `unload` / `unload_if_idle`, same Hub
  pinning keywords — so a service that already self-hosts a language model
  gains images without learning a second set of conventions.

  **`generate(prompt, config=...)`** returns `list[GeneratedImage]`, not
  loose bytes, because the **seed travels with the image**. Diffusion is
  deterministic given a seed, so returning the one actually used is the
  difference between an image you liked and an image you can reproduce —
  and when the caller passes none, only the generator knows which was drawn.
  **`edit(prompt, image, strength=...)`** redraws an existing image (path,
  bytes, PIL or NumPy) through `AutoPipelineForImage2Image.from_pipe`, which
  reuses the already-loaded UNet, VAE and text encoders: an SDXL pipeline is
  ~7 GB, and loading a second copy is how a service OOMs at the first edit.

  `ImageGenerationConfig` types `negative_prompt`, `width`, `height`,
  `steps`, `guidance_scale`, `seed` and `num_images`, forwarding only what
  you set. That matters more here than for text — a distilled turbo model
  wants 4 steps at guidance `0.0`, a full SDXL wants 30 at `7.5`, and
  applying one model's numbers to the other either wastes the compute or
  degrades the image.

  Concurrency defaults to **one** render: unlike an LLM, a single diffusion
  call already saturates the GPU, so running two concurrently makes both
  slower and doubles peak VRAM. `.pipeline` is the escape hatch for swapping
  the scheduler, attaching a LoRA or enabling a memory optimization the SDK
  does not wrap.

- **`make_genai_router(image_generator=...)` → `POST /image`.** The response
  body is the encoded image itself, typed from the generator's
  `image_format`, with the seed in an `X-Image-Seed` header so a client can
  reproduce the render. Only the first image is returned — batches go
  through the class.

### Note on dependencies

`diffusers` declares `httpx<1.0.0` and `huggingface-hub<2.0`. Neither bites
today (httpx is still on the 0.28 line) and, being an optional extra, the
bound only enters the resolution of whoever installs `[genai-image]` — it
does not reach a service that skips it. It is accepted because diffusion
schedulers, pipelines and VAE decoding are real engineering with a long tail
of correctness, not a preset table we could restate in tens of lines.

## [0.176.0] — 2026-08-01

### Added

- **`tempest_fastapi_sdk.genai.hub` — the weight lifecycle of a self-hosted
  HuggingFace model.** Loading a model by id is the easy half. The other half
  is everything the first `from_pretrained` call hides: which commit you
  actually got, how many gigabytes it wrote to which directory, whether the
  disk had room, and how to make the next boot reproduce the same weights
  without a network. New extra `[genai-hub]` (`huggingface-hub` alone, already
  contained in `[genai]`); the module imports with neither, because every Hub
  call resolves its dependency inside the function that needs it.

  **Pinning.** `resolve_revision(model_id, revision="main")` returns the
  immutable commit sha behind a branch or tag. An unpinned service loads
  whatever `main` holds the day it restarts — the author pushes, the pod
  cycles, and it is serving different weights without a line having changed.
  The function returns `None` rather than raising when the Hub is unreachable
  or the repository does not exist, so the caller decides whether to proceed
  unpinned or abort the deploy.

  **Fetching.** `download_model` materializes a revision on local disk with
  `allow_patterns`/`ignore_patterns` (most repositories ship the weights twice
  — `.bin` and `.safetensors` — so restricting the globs usually halves both
  the download and the disk), and returns a `ModelSnapshot` carrying the path,
  the byte total and the file count. It sizes the repository through
  `model_disk_bytes` — Hub metadata, no download — before writing anything and
  raises `OSError` when the filesystem cannot hold the estimate times a 1.1
  margin: failing in two seconds with a number beats failing forty minutes
  later with a half-written cache. `can_run` answers whether a model fits RAM
  or VRAM; this answers whether it fits the volume, and a healthy deploy asks
  both.

  **Cache.** `list_cached_models` / `cache_size_bytes` / `remove_cached_model`
  report and reclaim what the local cache holds. Weights are the biggest thing
  a self-hosted service writes to disk and nothing prunes them — every model
  ever loaded stays until removed. Removal reports the bytes freed, supports a
  `dry_run`, accepts a revision by sha *or* by ref name, and returns `0` for a
  model that is not cached, which is a successful no-op rather than an error.

- **`revision=`, `local_files_only=` and `trust_remote_code=` on every
  loader.** `TextGenerator`, `Embedder`, `VisionTextGenerator`,
  `ClassifierModerator` and `Reranker` take the same three keywords next to
  the `cache_dir=`/`hf_token=` they already had, each building a `ModelRef`
  and forwarding it — so there is no per-class way to pin. `ModelRef` emits
  only the values that differ from the default, which keeps an unpinned call
  byte-identical to what the SDK sent before and keeps the same dictionary
  usable with narrower loaders (`tokenizers.Tokenizer.from_pretrained` takes
  `revision` but not `trust_remote_code`).

  `local_files_only=True` makes a load purely local, so an air-gapped or
  deploy-frozen host fails immediately instead of quietly reaching the
  network. `trust_remote_code` stays opt-in per model: some architectures
  require it, and it executes Python from the same repository the weights came
  from.

  Two loaders differ, both because of the library underneath. `SpeechToText`
  gains `revision=`, `local_files_only=` and `hf_token=` mapped onto
  faster-whisper's own names (`download_root` / `use_auth_token`) and has no
  `trust_remote_code` — CTranslate2 loads weights, never repository Python.
  `OnnxEmbedder` already holds the graph on disk, so only its tokenizer can be
  pinned: `tokenizer_revision=` and `hf_token=`.

- **`tempest model pull` / `cache-list` / `cache-rm`.** The weight lifecycle
  from a Makefile, a `Dockerfile` or a deploy job. `pull --pin` downloads and
  prints the commit sha to feed back as `--revision`; `cache-list --revisions`
  breaks the cache down per commit; `cache-rm` confirms before deleting
  (`--yes` skips, `--dry-run` reports the size without touching anything).

### Changed

- The `[genai]` extra's README description no longer says model runners are
  upcoming — they have shipped since v0.98.

## [0.175.0] — 2026-07-31

### Added

- **`tempest_fastapi_sdk.modelops` — export, benchmark and quantize the models
  a service serves.** Three jobs that only make sense together: you quantize to
  make a model cheaper, you benchmark to find out whether it actually got
  cheaper, and you export to the format the target device runs. Two new extras
  so nobody installs weight they do not use — `[modelops]` (psutil +
  nvidia-ml-py) and `[modelops-onnx]` (onnx + onnxruntime). The module itself
  imports with neither; every heavy dependency is resolved inside the function
  that needs it and its absence raises an `ImportError` naming the extra.
  **Nothing in modelops constrains your `transformers` version** — see the
  quantization paragraph below for why that took deliberate work.

  **Benchmark.** `benchmark(call, ...)` times any zero-argument callable;
  `benchmark_onnx`, `benchmark_torch` and `benchmark_models` build on it, so an
  ONNX session, a torch module and a closure all produce the same
  `BenchmarkProfile`. The loop runs N warm-up calls and discards them, times N
  repetitions, and reports median plus IQR first — inference latency is
  heavy-tailed, and a mean hides the tail a p99 SLO cares about — alongside
  p95/p99, throughput, RSS peak and delta, CPU usage and GPU memory. Symbolic
  input dimensions are resolved from `dynamic_dims=` or `input_shapes=` and
  raise rather than being guessed: feeding a 1x1 image to a convolutional model
  produces a confidently wrong number.

  **Energy.** Four samplers behind one `PowerSampler` protocol, so the loop
  never branches on the host. `NvmlPowerSampler` prefers the driver's monotonic
  total-energy counter (`nvmlDeviceGetTotalEnergyConsumption`, Volta+) and falls
  back to integrating sampled power on older cards; `NvidiaSmiPowerSampler`
  polls the binary when `pynvml` is absent; `RaplEnergySampler` reads CPU
  package energy from the Linux powercap tree, summing package domains only and
  handling counter wraparound; `NullPowerSampler` measures nothing and reports
  it. Every sampler degrades into that last behaviour instead of raising, and
  every reading carries an `EnergySource` because a GPU figure and a RAPL figure
  are not the same quantity — and neither is wall-plug. A CPU run does not
  resolve a GPU sampler by default: attributing a shared card's idle draw and
  other processes' VRAM to a CPU model would be worse than reporting nothing.

  **Ranking.** `composite_scores` weighs min-max-normalized cost dimensions
  (`DEFAULT_COST_WEIGHTS` tuned for edge/mobile) and renormalizes twice so a
  missing measurement cannot distort the result: a dimension no profile measured
  is dropped entirely, and a dimension one profile lacks is skipped for that
  profile alone. `pareto_points` annotates the non-dominated set, skipping any
  axis either side did not measure, and degrading to a cost-only frontier when
  no `quality` was supplied — the SDK never invents a quality number. `rank`
  returns a `BenchmarkReport` carrying the sorted profiles, the frontier, the
  effective weights and the host description.

  **Export.** `export_torch_to_onnx` (opset, dynamic axes, fp16),
  `export_onnx_to_ort` (file or directory → `.ort`, `FIXED`/`RUNTIME` style,
  `target_platform`, type reduction) and `optimize_onnx_graph` (persist ONNX
  Runtime's fusions into a new `.onnx`). The ORT conversion also surfaces the
  `.required_operators.config` it writes, which is what lets a minimal ONNX
  Runtime build drop from tens of megabytes to a few.

  **Quantization.** `quantize_onnx_dynamic` (no calibration data),
  `quantize_onnx_static` (calibration reader built from any iterable of feed
  dicts, `QDQ`/`QOperator`, MinMax/Entropy/Percentile), the transformers-export
  path — `optimize_hf_onnx` (`O1`–`O4`) and `quantize_hf_onnx`
  (arm64/avx2/avx512/avx512_vnni) — plus `quantize_hf_bnb` for int4/int8
  weights that stay loadable by `AutoModelForCausalLM`.

  The transformers path runs on `onnxruntime.transformers` and
  `onnxruntime.quantization`, **not** on HuggingFace `optimum`. `optimum-onnx`
  declares `transformers<4.58`, and an upper bound on a published package
  propagates to every consumer; meanwhile `ORTOptimizer` and `ORTQuantizer`
  delegate to the same `onnxruntime` entry points this SDK already depends on,
  so the dependency bought a wrapper and cost a ceiling. The preset tables it
  did carry — the `O1`–`O4` levels, the per-ISA weight types, and the
  HuggingFace-to-fusion architecture map — are now explicit named constants in
  `modelops/quantize.py`, each documenting its provenance, and
  `tests/modelops/test_quantize.py` pins them against the installed runtime.
  `optimize_hf_onnx` gained `model_type=` (override the fusion type, or work on
  a bare graph) and `use_external_data_format=`; both functions now carry the
  export's `config.json` and tokenizer into the output directory.

  **Producing** the ONNX export is the one capability with no substitute
  outside `optimum`, and it is now a documented build step instead of a
  dependency: `uvx --from "optimum[onnxruntime]" optimum-cli export onnx ...`
  resolves the cap inside a throwaway environment that never touches your
  project. See `docs/recipes/modelops.md`.

  **Static analysis.** `analyze_onnx` sums parameters from the initializer
  dimensions without loading a weight; `analyze_ort` reports shapes and size
  (the serialized format drops the initializer table, so the parameter count is
  honestly `0`); `analyze_torch` counts parameters and, given an example input,
  forward GFLOPs; `analyze_model` dispatches on the suffix.

  Recipe: `docs/recipes/modelops.md`.

- **`tempest model` CLI** (`tempest_fastapi_sdk/cli/model.py`) —
  `analyze` / `bench` / `optimize` / `quantize` / `export-ort` / `hardware`,
  with `--json` on the reporting commands so the loop runs from a Makefile or a
  CI step. `tempest model hardware` reports what the host can measure, not just
  what it can run: whether NVML, the `nvidia-smi` fallback or the RAPL counters
  are actually readable here. A missing extra exits 2 with the install line
  rather than a traceback.

### Changed

- **`[all]` now includes `onnx`.** No modelops extra pulls `optimum`, so
  `uv sync --all-extras` keeps resolving `transformers` to the 5.x series and
  the GenAI suite stays exercised against it in CI.

- **`export_torch_to_onnx` checks for `onnxscript` up front.** From torch 2.9
  on, `torch.onnx.export` defaults to the dynamo exporter, which imports
  `onnxscript` lazily — a missing install surfaced as a `ModuleNotFoundError`
  raised from inside torch, midway through the export. It now raises the same
  actionable `ImportError` every other optional dependency in this module
  produces. `onnxscript` joined the `dev` group so CI exercises the path.

## [0.174.0] — 2026-07-30

Second half of the security sweep: the reachable-by-a-request crashes, and the
hardening items around them. None of these needed a valid session to hit.

### Fixed

- **A password over 72 bytes answered 500 instead of 422**
  (`auth/service.py`, `settings/mixins.py`, `utils/password.py`). bcrypt
  refuses input past 72 UTF-8 **bytes** — `hashpw` raises `ValueError` — and
  the policy only had a lower bound, so signup, password-reset confirm and
  password change all crashed on a long password. New
  `AUTH_PASSWORD_MAX_BYTES` (default `72`) rejects it as a validation error.
  Bytes, not characters: `"🔒" * 19` is 19 characters and 76 bytes.

- **`GET /logs` crashed on an offset-free `start` / `end`**
  (`api/routers/logs.py`). Pydantic hands back a naive `datetime` for
  `2026-01-01T00:00:00`, log timestamps are aware (`...Z`), and comparing the
  two raises `TypeError`. A bare bound is now read as UTC.

- **`GET /logs` read every selected file into memory whole**
  (`api/routers/logs.py`, `admin/router.py`). Filtering and pagination
  happened after the load, so a service whose log directory had grown to
  gigabytes took the worker down instead of answering. Each request now reads
  the newest `DEFAULT_MAX_RECORDS_PER_FILE` (20k) records per file through a
  bounded deque, tunable via `make_logs_router(max_records_per_file=...)`, and
  logs a warning when the cap bites. The admin logs page shares the fix.

- **An unknown `order_by` crashed pagination** (`db/repository.py`).
  `BasePaginationFilterSchema.order_by` is a plain `str` query parameter, and
  `paginate` passed it to a bare `getattr` — an unknown name raised
  `AttributeError`, and a name that exists but is not a column (`metadata`,
  `registry`) raised it one frame later on `.desc()`. Both are now resolved
  through the mapper's column set and raise `ValidationException` (422).
  `cursor_paginate` validated already but had the same non-column hole; it now
  shares the check.

- **A client-supplied filename could add a header to the response**
  (`utils/download.py`). `build_content_disposition` took the basename but left
  control characters in, so a name containing `\r\n` produced a header value
  with a real line break — which an ASGI server that does not validate header
  values (uvicorn on `httptools`) writes to the socket verbatim. Every C0/C1
  character is now stripped. In the documented usage the name is
  `UploadFile.filename`, so this is caller-controlled input.

- **`BodySizeLimitMiddleware` could crash the response it was protecting**
  (`api/middlewares/body_size.py`). The 413 was emitted in a `finally`, after
  the app had answered — and FastAPI does answer, converting the guard's
  `ClientDisconnect` into a `400`. The second `http.response.start` makes
  uvicorn raise `RuntimeError: Response already started`. The 413 is now sent
  the moment the count is exceeded, while the app is still reading the body,
  and anything the app sends afterwards is dropped. A streaming oversize body
  therefore answers `413` where it recently answered `400`.

- **`make_csrf_token_dependency` never set the cookie its docstring promised**
  (`api/middlewares/csrf.py`). It returned the token and stashed it on
  `request.state`, leaving the cookie absent — so the double-submit check the
  page was rendered for rejected the following `POST` with a 403. It now sets
  the cookie (`Secure` + `SameSite=Lax`, and deliberately not `HttpOnly`,
  since the client must read it), with `secure=` / `samesite=` / `max_age=`
  overrides.

### Security

- **`OAuthUser.email_verified`** (`api/oauth.py`). `email` was surfaced with no
  indication of whether the provider had verified it. A service linking a
  social login to an existing account by email hands over the victim's account
  when the address was never verified — GitHub's `GET /user` returns the public
  profile email, which GitHub does not require verifying. Google and generic
  OIDC now report the `email_verified` claim (string forms normalized); GitHub
  reports `None`, meaning unknown, rather than implying either answer.

- **Opt-in webhook replay window** (`api/webhooks.py`). The signature covers
  the body only, so a captured delivery stayed valid forever.
  `verifier.dependency(timestamp_header=..., max_age_seconds=...)` additionally
  requires a fresh unix-timestamp header. Opt-in because a provider that sends
  no such header would have its legitimate traffic rejected — and documented as
  bounding opportunistic replay only, since an attacker who rewrites the
  request can rewrite the timestamp too.

- **`PostGISRepositoryMixin.nearby` validates its column names**
  (`geo/db.py`). `latitude_field` / `longitude_field` are interpolated into a
  `text()` fragment (a column name cannot be a bind parameter). They are
  developer-supplied today, but nothing stopped a route from forwarding a query
  parameter; they are now checked against the mapper.

- **`tempest secrets rotate` writes `0600`** (`cli/secrets.py`). The rewritten
  `.env` and its `.env.bak` inherited the process umask, commonly `0644` —
  world-readable, for a file that now holds freshly minted secrets.

## [0.173.0] — 2026-07-30

### Security

- **A refresh token or an MFA-pending token no longer authorizes a request**
  (`tempest_fastapi_sdk/utils/token_types.py` (new),
  `api/dependencies/auth.py`, `auth/service.py`).

  `UserAuthService` signs three JWTs with the same secret: the access token, the
  refresh token, and the intermediate token that bridges the two steps of an MFA
  login. Nothing distinguished them once the signature verified, and
  `make_jwt_user_dependency` authorized any token carrying a `sub`. Since
  `POST /auth/login` hands the `mfa_token` back to a client that has proven only
  the password, that token worked as a bearer on every authenticated route — the
  second factor was skippable. The long-lived refresh token was accepted the same
  way, which defeats the point of a short access token.

  Every issued token now declares a `typ` (`ACCESS_TOKEN_TYPE` /
  `REFRESH_TOKEN_TYPE` / `MFA_TOKEN_TYPE`), and the bearer, current-user, role
  and permission dependencies accept `access` only. `accepted_typ=` widens that
  per call site. A token with no `typ` — one a project signed itself with
  `JWTUtils.encode()` — is still accepted, so upgrading does not invalidate live
  sessions; the legacy markers the SDK did stamp (`refresh: True`,
  `purpose: "mfa_pending"`) are recognized and rejected as access.
  `token_type_allowed()` exposes the same decision outside a dependency.

- **`ResponseCacheMiddleware` no longer serves one caller's response to
  another** (`api/middlewares/response_cache.py`).

  The cache key was `method|path|query` plus the `vary=` headers — nothing about
  who asked. An authenticated `GET` that did not set `Cache-Control: private`
  itself (most routes do not) was stored and replayed to the next caller on that
  path, anonymous ones included. The emitted `Cache-Control` also defaulted to
  `public`, telling browsers and CDNs to keep personalized bodies.

  A request carrying `Authorization` or `Cookie` now bypasses the shared store;
  it still gets its `ETag` and `304`, which are per-response. `Cache-Control`
  defaults to `private, max-age=N`. `cache_credentialed=True` opts a deployment
  back into caching credentialed traffic, folding a digest of those headers into
  the key so each caller gets its own entry.

- **`IdempotencyMiddleware` scopes each entry to the caller that created it**
  (`api/middlewares/idempotency.py`).

  The key was `(method, path, key)` with `key` chosen by the client, so two
  callers picking the same string shared an entry — and a replay returns the
  stored response, headers included. The key now also carries a digest of the
  request credentials; `principal_resolver=` replaces that with your own identity
  (an API-key id, a tenant header). A `Set-Cookie` is dropped from the stored
  copy, so a replay can never re-issue the original caller's session.

### Changed

- `IdempotencyMiddleware` no longer caches `5xx` responses, so a client retry
  after a transient failure actually reaches the handler instead of replaying the
  error for the entry's whole TTL. `cache_server_errors=True` restores the old
  behavior.
- Concurrent requests sharing an idempotency key are serialized within a process:
  the second waits and replays the first's response rather than running the
  handler again. Across replicas the store still only deduplicates completed
  requests.
- `ResponseCacheMiddleware` emits `X-Cache` only when a `store=` is configured.
  It previously reported `MISS` in ETag-only mode, where there is no cache to
  miss.


## [0.172.1] — 2026-07-30

### Fixed

- **The admin logs table is readable on a phone, and a copied row keeps its
  separators** (`tempest_fastapi_sdk/admin/static/admin.css`,
  `admin/templates/logs.html`). Two problems with the `0.172.0` traceback view,
  both found on real screens rather than in tests.

  Below 600px the four-column table only fit by scrolling sideways, and the
  fourth column is the one that matters — message, request context, traceback. An
  opened trace showed up as a tall blank row until the operator dragged the table
  across (measured at 414px: `table.scrollWidth` 676 against a 390px wrap). The
  logs table now stacks into cards at that breakpoint: `thead` is dropped and each
  cell names itself from a new `data-label`, so the message and its traceback sit
  in the viewport with no horizontal scroll (375px table in a 375px wrap). Scoped
  to this table — the other list views keep the scroll treatment, which suits a
  list you skim rather than read.

  The request-context chips (`path`, `method`, `status_code`, `request_id`) were
  separated only by a flex `gap`. That renders separation but contributes no
  character, so copying a row to paste into an issue yielded `status_code404` and
  `request_idd7332783-…`. The markup now carries a real space between the label
  and the value, and the visual gap widened, since an uppercase micro-label
  against a monospace value reads as one token.

  **Migration:** none.

## [0.172.0] — 2026-07-30

### Added

- **The admin logs page now shows the traceback of a 500, and exports the
  selection as markdown or JSON** (`tempest_fastapi_sdk/admin/router.py`,
  `admin/templates/logs.html`, `api/routers/logs.py`). Diagnosing an unhandled
  error from the panel was impossible: the table rendered four columns
  (timestamp, level, logger, message) and dropped every other field, so the
  `exception` value — the formatted traceback the SDK's own exception handlers
  write with `exc_info=True` — sat on disk and never reached the screen. The only
  way to read it was to shell into the server, and there was no way to hand it to
  anyone. This bit during the `0.171.1` investigation: the single clue to a
  production 500 was a log line with the trace stripped off.

  Each record carrying a traceback is now a collapsed disclosure: the record's
  own message is the `<summary>`, so clicking anywhere on the entry reveals the
  trace, with the request correlation fields (`path`, `method`, `status_code`,
  `request_id`) alongside it. Plain `<details>`/`<summary>` — the admin ships no
  JavaScript — collapsed by default so a page full of 500s stays scannable, and a
  record without a traceback grows no toggle at all.

  The traceback wraps rather than extending: a `<pre>` is sized by its longest
  line and sits in a table cell, which sizes to content, so `overflow-x` on the
  `<pre>` could not shrink it — the table grew past its scroll container (1364px
  against a 1012px wrap) and the trace could only be read by dragging the table
  sideways. Wrapping costs the alignment of the caret markers on screen; the
  export carries the exact unwrapped text.

  New `GET {prefix}/logs/export?format=md|json` downloads the **filtered**
  selection (the page's own `?source=` and `?q=`), newest first, capped at 500
  records:

  - `format=md` puts each traceback in a fenced ```` ```pytb ```` block, so it
    survives a paste into an issue or PR with its indentation intact. The header
    states the source, the record count, the active filter and — when the cap
    bites — how many records matched in total, so a partial export never reads as
    a complete one.
  - `format=json` emits the records verbatim, including every field the
    application attached through `extra=`, for tooling rather than reading.

  Page and export share one collector, so the file always matches what the
  operator was looking at. The export inherits the admin session guard — a
  traceback is exactly the payload that must not be world-readable.

- **`render_entries_markdown` / `render_entries_json`** are exported at the
  package level (`tempest_fastapi_sdk/api/routers/logs.py`) so a service can
  build its own export — a CLI command, a CI step, a chat-ops hook — off the same
  rendering the admin panel uses. `render_entries_json` falls back to `str()` for
  non-serializable values, so one exotic `extra=` value cannot fail a whole
  export.

## [0.171.1] — 2026-07-30

### Fixed

- **`make_auth_router` now loads the authenticated user on the request session,
  so `POST /auth/password-change` actually rotates the password**
  (`tempest_fastapi_sdk/auth/router.py`). The router built its authenticated-user
  dependency without the `session_dependency` seam `make_jwt_user_dependency`
  provides, so `_make_user_loader` opened a **private** session, fetched the user
  and returned it after that session closed. Every authenticated route of the
  bundled router therefore received a **detached** instance, and the damage was
  silent-then-loud: assigning `user.hashed_password` succeeded,
  `session.flush()` wrote **nothing** (the instance is absent from that session's
  identity map), and the following `session.refresh(user)` raised
  `InvalidRequestError: Instance is not persistent within this Session`. The
  endpoint answered **500** *and* left the old password valid — reported from
  production as "changing the password does nothing".

  `UserAuthService.current_user_dependency` already defaulted to
  `self.db.session_dependency` and the docs already promised an attached
  instance; only the bundled router bypassed it. The loader is now a
  two-argument `(user_id, session)` callable and the dependency is wired with
  `session_dependency=_session` — the **same** callable the route bodies depend
  on, because FastAPI caches a sub-dependency by its callable and a different
  wrapper would open a second session and detach the user again.

  The existing router tests could not catch this: they wire a `session_factory`
  that `yield`s one shared `AsyncSession`, making the loader's "private" session
  and the route's session the same object.
  `tests/auth/test_router_session_sharing.py` drives the router through a factory
  that opens a **new** session per call, the way
  `AsyncDatabaseManager.session_dependency` does in a real app, and asserts a
  single request opens exactly one session.

  Same root cause fixed for `POST /auth/mfa/confirm` and `POST /auth/mfa/disable`,
  which mutate `totp_secret` / `totp_enabled_at` through the same dependency.

- **`UserAuthService` methods that receive an already-loaded user now re-attach it
  to the session they write through** (`tempest_fastapi_sdk/auth/service.py`).
  `change_password`, `mfa_enroll`, `mfa_confirm` and `mfa_disable` take the user
  from their caller instead of fetching it, which made every one of them a session
  mismatch away from losing its writes — with the error surfacing at `refresh`,
  one line past the real cause. A private `_attach` helper returns the user
  untouched when it already belongs to the session and `merge`s it in otherwise
  (`merge` rather than a re-fetch by primary key, so pending in-memory changes are
  carried over instead of discarded). This makes the flows correct for callers
  driving the service directly — a background task, a CLI command, a test — not
  only through the bundled router.

  **Migration:** none. Behavior only becomes correct where it previously failed;
  no signature changed.

## [0.171.0] — 2026-07-28

### Changed

- **`BaseStrEnum` / `BaseIntEnum` now render their value under `str()` and
  f-strings** (``tempest_fastapi_sdk/core/enums.py``). Both bases are
  ``str``/``int`` + ``Enum`` mixins, and ``Enum`` owns ``__str__`` /
  ``__format__`` on those — so ``str(OrderStatus.PAID)`` returned
  ``"OrderStatus.PAID"`` even though ``OrderStatus.PAID == "paid"`` holds.
  Members compare, serialize and bind to the database as their value, but the
  moment one was interpolated into an f-string, a log line, a query string or
  written to a raw column, the member *name* leaked instead. The SDK had
  already paid for this once: the OpenAPI client generator needed an explicit
  ``.value`` so enum query params did not go out as
  ``status=CustomerStatus.PAST_DUE`` (``tests/openapi/test_generate.py``).
  ``_EnumHelpers`` — first in the MRO for both bases — now overrides
  ``__str__`` **and** ``__format__`` (f-strings go through the latter, so
  overriding only the former fixes nothing) to delegate to the value, matching
  ``enum.StrEnum``. Numeric format specs keep working on ``BaseIntEnum``
  (``f"{Priority.HIGH:03d}"`` -> ``"002"``), and ``repr()`` is untouched, so
  members stay debuggable.

  **Migration:** ``str(member)`` / ``f"{member}"`` now yield ``"paid"`` instead
  of ``"OrderStatus.PAID"``. Code that *wanted* the qualified name — log
  messages, debug output — should switch to ``repr(member)`` or
  ``member.name``. Everything that wanted the value (the overwhelming majority,
  and every place that was silently wrong) needs no change, and
  ``member.value`` behaves exactly as before.

## [0.170.3] — 2026-07-27

### Added

- **Docs signature guard** (``tests/test_docs_signature_guard.py``, runs in
  ``make check``). The existing guard proved a doc snippet *parses* and that
  every ``__all__`` name resolves — neither catches the example that passes a
  keyword the function does not accept, hands a keyword-only parameter
  positionally, or imports a symbol that no longer exists. Those raise
  ``TypeError``/``ImportError`` on the reader's first run, and the prose written
  around the invented parameter documents behavior that never existed. Four
  static checks now cover it: keywords exist in the real signature, positional
  arity fits (which also catches the ``f(obj, ..., kw=1)`` elision, whose
  literal ``Ellipsis`` is a real argument), SDK imports resolve, and no install
  snippet requires a version above the packaged one. Symbols resolve **per
  block, from that block's own imports**, so the two different ``RetryPolicy``
  classes (``tempest_fastapi_sdk`` for HTTP, ``.tasks`` for TaskIQ) are never
  confused; a snippet that uses a symbol without importing it is left alone.
- **Social login recipe** (``docs/recipes/oauth.md`` + ``.en.md``).
  ``GoogleOAuthClient`` / ``GitHubOAuthClient`` / ``OIDCProvider`` /
  ``OAuthUser`` / ``OAuthTokens`` / ``OAuthError`` / ``generate_oauth_state``
  had shipped for releases with no page of their own — they appeared only as
  names in the module tables. The recipe walks the whole flow (register the app,
  build the client once, ``state`` in an ``HttpOnly`` cookie, the callback,
  linking to a local user and minting your own token), plus the GitHub and
  generic-OIDC variants and the CSRF reasoning behind ``state``.

### Fixed

- **Documentation for shipped-but-unmentioned surface.** Each of these worked
  and was reachable only through the API reference:
  ``BodySizeLimitMiddleware`` (``max_bytes`` / ``exclude_paths``, the
  header-vs-streaming checks, the 413 envelope) in the HTTP recipe;
  ``CSRFMiddleware`` + ``make_csrf_token_dependency`` +
  ``generate_csrf_token`` in the security recipe — the sessions recipe already
  linked to a CSRF section that did not exist; ``DatabaseBackup`` (+
  ``BackupToolMissingError`` / ``UnsupportedBackupBackendError``) in the
  safe-deploys recipe; ``AuthCookieConfig`` / ``apply_auth_cookies`` /
  ``clear_auth_cookies`` in the auth recipe;
  ``make_web_push_subscription_model`` in the Web Push recipe;
  ``UploadStorage.write_stream`` + ``UploadResult`` in the uploads recipe;
  ``require_x_token`` in the HTTP recipe.
- **Doc examples that raised ``TypeError`` when copied** (found by the new
  guard): the README opaque-token section treated
  ``generate_opaque_token()`` as returning a string and passed a ``secret=``
  pepper neither hash nor verify accepts — and contradicted the security recipe,
  which correctly documents plain SHA-256 with no pepper;
  ``RSAWebhookSignatureVerifier`` was shown with ``encoding=`` /
  ``hash_algorithm=`` (the parameter is ``algorithm=``, and PSS padding is not
  supported); the React-SPA recipe passed ``session_factory`` positionally to
  ``make_auth_router``; the admin RBAC example passed a literal ``Ellipsis``,
  so a copy-paste failed on the required ``db`` / ``auth_backend`` /
  ``secret_key`` keywords.
- **``current_user_dependency`` session semantics.** The auth recipe said it
  "opens its own session"; it loads the user on the **request** session
  (``db.session_dependency`` by default), which is the point of the sharing and
  the reason ``session_dependency=`` must be the exact callable the
  repositories use. The HTTP recipe's hand-rolled loader now takes the shared
  session too, and documents the header → cookie → query lookup order.
- **Generic install floors** in ``docs/installation.md`` and the recipes
  landing now reference the current release instead of ``>=0.161.0`` /
  ``>=0.167.0``. Per-feature floors (``>=0.89.0`` on auth/MFA/metrics) stay as
  the version the feature landed in.
- **A red test on ``main``**: ``test_unparsable_source_falls_back_to_a_comma``
  asserted the ``_separator_before`` fallback using an offset inside the first
  line. Up to Python 3.11 the tokenizer emits ``ERRORTOKEN`` for an
  unterminated quote and only raises ``TokenError`` at EOF, so that offset
  broke out of the loop before the failure and exercised the normal path. The
  offset now sits past the malformed literal. Test-only — ``--fix`` behavior is
  unchanged.

## [0.170.2] — 2026-07-26

### Fixed

- **A bare call no longer resolves to an instance method.** ``f()`` and
  ``obj.f()`` were the same kind of edge, and they are not: a call with no
  receiver cannot reach a method. The cost showed up on the most ordinary code
  there is — a repository doing
  ``await self.session.execute(update(UserModel).where(...))`` with SQLAlchemy's
  imported ``update`` registered a call to ``update``, which then matched an
  unrelated ``CoinPackService.update`` and reported a coin pack's 404 on a
  *category* route. ``delete``, ``insert`` and ``select`` collide the same way,
  so any project using the expression API alongside a service method of the same
  name was affected. Bare calls now resolve against module-level functions only,
  which keeps imported helpers and ``@requires`` guards reachable; a call on an
  unannotated receiver keeps the wide resolution, since there the target really
  is unknown. New ``FunctionInfo.attr_calls`` holds that second kind.

  Two consequences worth expecting on upgrade: routes stop being blamed for
  exceptions from unrelated domains, and a declaration that only existed
  *because* of that blame now surfaces as ``unreachable``. In the service this
  was found on, both happened — one wrong ``undocumented`` disappeared and one
  wrong declaration got reported.

## [0.170.1] — 2026-07-26

Follow-up to 0.170.0, found by running it on the same service: the new
delegation chain broke at the classes the layering encourages most.

### Fixed

- **A pass-through layer no longer breaks the chain.** A service that overrides
  nothing has no `__init__` to read — `class CategoryService(BaseService[
  CategoryRepository, CategoryResponseSchema])` states what it delegates to
  *only* through the base's generic parameters. 0.170.0 read attributes and
  constructor parameters, so the walk stopped there and the route that deletes a
  category was reported as declaring two **unreachable** exceptions it actually
  raises. That is worse than silence: it invites deleting a correct declaration.
  New `GENERIC_DELEGATES` maps `BaseService[RepositoryT, …]` to `repository` and
  `BaseController[ServiceT, …]` to `service`; only those two bases are
  interpreted, since `BaseRepository[Model]`'s parameter is an ORM model and
  reading positions blindly would invent a delegation link to a table class.
- **An override further down the chain is now walked, not just mined.** Only
  constructor configuration was collected along the chain, so a repository whose
  own `delete` translates an `IntegrityError` into a domain 409 contributed
  nothing — nothing configures that exception, it is raised outright. Those
  methods are returned as ordinary call-graph targets now, so their `raise`
  statements and `Raises:` sections are read like any other.

## [0.170.0] — 2026-07-26

Two findings from `openapi-errors` on a real service, in opposite directions: it
was reporting exceptions a route cannot raise, and staying silent about the ones
it raises through the SDK's own methods.

### Added

- **A class handed to a base constructor now counts as raised.** A repository
  writes `not_found_exception=CoinPackNotFoundException` once and never names it
  again — the `raise` is inside `BaseRepository`, outside the scanned tree — so
  the 404 of every route reading or deleting that model went unreported. The
  analyzer now reads the exception kwargs passed to `super().__init__(...)` and
  attributes them to the inherited methods that raise them, walking the
  delegation chain a layered service creates (a controller's `service`, a
  service's `repository`, plus known bases). New `CONFIGURED_RAISERS` maps each
  kwarg to those methods, so a create conflict is never attributed to a `delete`
  nor a 404 to an `add`. Only `service` and `repository` are followed as
  delegation links (`DELEGATION_ATTRS`): following every annotated attribute
  made a service holding extra repositories donate their 404s to every route
  that reached it. `test_openapi_errors_configured.py` asserts the table against
  `BaseRepository` itself, transitively over `self.*` calls — which is how
  `soft_delete` and `restore` were found to also surface the update conflict,
  via `self.update(...)`.

### Fixed

- **A DELETE route no longer inherits another domain's exceptions.** The
  analyzer walked the whole function node for calls, decorators included, so
  `@router.delete("/{id}")` registered a call to `delete`. Every edge then
  resolved by unqualified name, so that call reached every `delete` in the
  project. In a service whose only `delete` was `CategoryRepository.delete`,
  *every* DELETE route was reported as raising `CategoryInUseException` and
  `CategoryNotFoundException` — and `--fix` wrote those wrong names into the
  decorators. `get` and `post` collide identically wherever a project defines
  methods with those names. Only the function body is walked now.
- **Calls resolve through the receiver's type when it is annotated.**
  `self.svc.f()`, `self.f()`, `super().f()` and a call on an annotated parameter
  (a route handler's `controller: UserController`) now resolve inside that
  class's hierarchy, walking bases breadth-first. Finding nothing there means
  the method is inherited from outside the scanned tree — the SDK's own
  `BaseController.delete`, say — so no edge is followed at all, instead of
  falling back to the project method that happens to share the name. A receiver
  that cannot be typed still resolves by name, keeping the deliberate
  over-approximation where precision is unavailable.
- **The permission checker resolves guards with the same edges.** Both commands
  now build one shared `CallGraph` (`build_call_graph`), so a guard's reachable
  exceptions and a route's are computed identically rather than by two indexes
  that had drifted apart in precision.

### Documentation

- `docs/recipes/openapi-errors.md` / `.en.md` — the "it's a guide, not a proof"
  warning now describes typed-versus-name resolution, states that a method
  inherited from outside the scanned tree is not followed, and records what
  0.170.0 fixed.

## [0.169.0] — 2026-07-26

### Added

- **`BaseRepository` accepts a conflict exception class per operation.** Every
  write raised the SDK's own `ConflictException`, whose `code` is the generic
  `"CONFLICT"` — so a duplicate name and a violated FK reached the client as the
  same 409, and `error_responses()` had nothing specific to document. Only the
  *message* was customizable (`create_conflict_message` and friends), and a
  message is not something a frontend can branch on. Each `*_message` kwarg now
  has a matching `*_exception`: `conflict_exception` as the blanket default plus
  `create_conflict_exception`, `update_conflict_exception`,
  `bulk_create_conflict_exception` and `bulk_update_conflict_exception` for the
  individual write paths. Resolution is most-specific-first (per-operation →
  blanket → `ConflictException`), the resolved classes are exposed as attributes
  like `not_found_exception` already was, and all ten `raise` sites across
  `add` / `add_all` / `save_with_outbox` / `add_audited` / `update` /
  `update_audited` / `update_many` / `bulk_update` / `bulk_create_values` /
  `bulk_upsert` honor them. Every kwarg is optional and the default path is
  unchanged, so existing repositories keep raising exactly what they raised
  before. The class is instantiated as `cls(message=...)` — the same contract
  `not_found_exception` has — so a subclass must accept a `message` keyword.

### Documentation

- `docs/recipes/database.md` / `.en.md` — new "exception classes per repository"
  tip: why a message alone is not enough, the coin-pack example, the
  kwarg-to-operation table, and the `cls(message=...)` contract.

## [0.168.3] — 2026-07-26

Second post-release finding from `--fix` running against a real project. The
first one only a foreign project could show; this one only a **formatted**
project could show.

### Fixed

- **`tempest openapi-errors --fix` no longer emits a double comma into a
  formatted decorator.** The insertion point is the closing parenthesis of the
  route decorator, and the appended text was prefixed with an unconditional
  `", "`. A decorator with more than one argument is wrapped over several lines
  by `ruff format`, which leaves a **trailing comma**, so the splice produced
  `status_code=201,\n, responses=error_responses(...)` — two commas in a row.
  `render_file` then called `ast.parse` on it and raised `SyntaxError`, aborting
  the whole run before anything was written: on a service with 18 drifting
  routes the command was unusable, and its own claim that "nothing depends on
  how the decorator happens to be formatted" was false. The separator is now
  derived from the source via `tokenize` — `""` when the call already ends with
  a comma or is empty, `", "` otherwise. Tokenizing rather than scanning text
  backwards is what makes it correct when a `,` or `#` sits inside a string
  literal (`description="a, b # c"`). New `_separator_before()` and
  `_char_offset_of()` (`tokenize` reports character columns where `ast` reports
  UTF-8 byte columns — mixing the two lands past the intended character on any
  non-ASCII line).
- **`RouteInfo.declares_empty_call` is no longer load-bearing.** The writer used
  it to decide the comma, but the flag only knows whether the call has zero
  arguments — it cannot see a trailing comma, which is the case that broke. It
  stays as descriptive metadata; its docstring now says so.

### Documentation

- `docs/recipes/openapi-errors.md` / `.en.md` — the "edits anchored on the AST"
  guarantee now explains how the separating comma is derived, and why it is
  tokenized rather than pattern-matched.

## [0.168.2] — 2026-07-26

The documentation-organization rule becomes executable. Documentation and
tooling only — no code change, no API change.

### Added

- **`tests/test_docs_organization.py`** (renamed from
  `tests/test_nav_ordering.py`, 15 tests) — the project rule "the docs stay
  organized and in order" is now asserted instead of reviewed. On top of the
  ordering checks the previous release added, it covers the structural half,
  each item of which has drifted at least once:
    - every page exists in **both languages** (`docs/<page>.md` +
      `docs/<page>.en.md`), and no `.en.md` is orphaned;
    - every page on disk is **reachable from its language's nav** — MkDocs
      warns for the default nav, the EN nav is ours to check;
    - the two navs cover the **same set** of pages, with no duplicates;
    - the alphabetical sections are alphabetical **in both languages**;
    - the Recipes landing tables are sorted **and list every recipe in the
      nav** (twelve were missing when last audited).
- **A "Regra de organização da documentação" section in `CLAUDE.md`** — the
  six-step checklist for adding or renaming a page (two languages, two navs,
  alphabetical position, landing table, reference stub, strict build), what the
  guard covers, and what is deliberately *not* alphabetical (top-level tabs,
  `learning/` pages, the landing tour, and the submodule grouping inside the
  reference).
- **The same rule on the contributor-facing pages** — `docs/contributing.md` +
  `.en.md` now state it under Docs, and
  `.github/PULL_REQUEST_TEMPLATE.md` grew three checklist items (both navs,
  landing table, the guard).

## [0.168.1] — 2026-07-26

Documentation navigation only — no code change, no API change.

### Changed

- **Everything a reader scans to find a page is alphabetical now.** The recipe
  list had grown to 53 entries in insertion order, which turns a lookup into a
  full pass over the list. Sorted, accent- and case-insensitively:
    - the `Receitas` / `Recipes` nav section (including the
      `Exemplos completos` / `Complete examples` sub-section, itself at its
      alphabetical position);
    - both tables on the Recipes landing (`Tema`/`Theme` with all 53 recipes,
      and the complete-examples table), each sorted in its own language;
    - the themed groups of `docs/reference.md`, with
      `## Superfície de topo` pinned first;
    - the module table in `README.md`;
    - the extras table in `docs/installation.md` + `.en.md`, with `[all]` last
      because it is the catch-all, not an entry to look up.
- **English gets its own nav.** `mkdocs-static-i18n` translates labels but
  cannot reorder a shared nav, so an alphabetical PT nav left the EN sidebar in
  Portuguese order (`File in the service` before `Versioned artifacts` before
  `Audit trail`). The `en` locale now declares a full `nav` of its own, sorted
  in English. `nav_translations` stays for the labels the plugin still resolves.

The top-level tabs and the learning-project pages are deliberately **not**
alphabetical: they follow a reading order (install before tutorial, business
rules before endpoint map), where sorting would be the regression.

### Added

- **`tests/test_nav_ordering.py`** — guards what the sweep above fixed: the two
  navs cover the same pages (no page reachable in one language only), no page
  is listed twice, the alphabetical sections are alphabetical in both
  languages, and the Recipes landing tables are sorted. Eight tests; a swapped
  entry fails them.

## [0.168.0] — 2026-07-26

Permission guards gain injected metadata: one generic guard, a specific check
per call site.

### Added

- **A guard may declare a second parameter `meta: dict[str, Any]`.** That is
  what turns `manager_only` / `auditor_only` / `admin_only` into one
  `has_role(user, meta)` whose call sites declare
  `@requires(has_role, meta={"role": "manager"})`. One-parameter guards are
  untouched — the second argument is passed only to guards that declare it, so
  both shapes mix freely in the same decoration.
- **`requires(..., meta=...)`** — literal metadata, copied at decoration time,
  so a later mutation of the passed mapping cannot change what the route
  enforces. Must be a `Mapping[str, Any]`; anything else is a
  `TempestPermissionError` at import.
- **`requires(..., include_args=True)`** — merges the arguments the decorated
  function was called with into the same mapping, so an ownership guard reads
  `meta["order_id"]` without the route handing it over. The user parameter is
  excluded (the guard already receives it), a parameter the caller omitted
  contributes its default so the guard sees the values the body will run with,
  a default that is a framework injection marker (`Depends(...)`) is dropped
  rather than passed as a value, and a `meta=` literal wins a name clash with
  an argument — the decoration is the explicit declaration. The mapping is
  fresh per call and shared by that call's guards, so one guard may write a key
  the next one reads without anything leaking into the next request.
- **`guard_metadata(fn)`** — reads the `meta=` literals off a decorated
  function, next to `declared_guards` / `guarded_user_param`. Exported from
  `tempest_fastapi_sdk` and `tempest_fastapi_sdk.authz`.
- **Four static checks in `tempest permissions`:** `meta-unused` (error — the
  decoration passes `meta=` / `include_args=True` but no guard declares a
  second parameter; held back when a guard in that decoration could not be
  resolved, since it may be the consumer), `guard-meta-missing` (warning — a
  two-parameter guard whose decoration supplies no metadata reads an empty
  dict), `guard-meta-annotation` (warning — the metadata parameter is annotated
  as something that cannot hold a `dict[str, Any]`) and `meta-key-collision`
  (warning — a `meta=` key shadows a parameter name under `include_args=True`,
  so that argument never reaches the guard).

### Changed

- **`guard-arity` accepts one or two required parameters.** A guard taking
  `(user, meta)` used to be rejected at import (`expected 1 (user)`); the
  contract is now one *or* two, and the message reads
  `expected 1 (user) or 2 (user, meta)`. Three or more is still an error. No
  existing single-parameter guard changes behavior.

## [0.167.2] — 2026-07-26

Documentation layout only — no code change, no API change.

### Changed

- **The tab bar is down from 13 tabs to 11, and every section is reachable from
  the landing page.** `Exemplos` and `SSR (páginas tipadas)` were loose
  top-level nav entries, which crowded the Material tab bar; the landing page
  meanwhile pointed only at Installation / Architecture / Tutorial, so Recipes,
  Reference and Learning projects existed on the bar and nowhere in the prose.
  Both fixed:
    - `Exemplos` is gone as a tab. The five integrated walkthroughs (Pix
      checkout, neighborhood marketplace, full store admin, fullstack web,
      GenAI flows) now live under **Recipes** as the `Exemplos completos` /
      `Complete examples` sub-section, and are indexed in a table on the
      Recipes landing. Their URLs are unchanged.
    - `SSR (páginas tipadas)` is now a recipe, listed in the Recipes index
      alongside the others. URL unchanged.
    - The **SDK tour** was merged into the Recipes landing
      (`docs/recipes/index.md`) as its opening section, so one page is the
      entry point instead of two competing for the role. `docs/tour.md` /
      `docs/tour.en.md` are removed and the `/tour/` URL is gone — inbound
      links now target the tour section of the Recipes landing.
    - The Recipes index table covers **all 53 recipes**; twelve were missing
      (`authz`, `permission-guards`, `introspection-auth`, `geo`, `genai`,
      `file-store`, `artifact-registry`, `openapi-errors`, `openapi-client`,
      `system-checks`, `management-commands`, `react-spa`) plus SSR.
    - The landing page gained a **"Por onde continuar" / "Where to go next"**
      card grid naming every section — Installation, Architecture, Tutorial,
      Recipes, Reference, Learning projects, Roadmap, Migration guide and
      Changelog — and its stale Status row (`630+ tests, ≥ 89 %`) now reads
      `2.650+ / ≥ 90 %`.
- **Contributing is issue-first.** The page now opens with the policy: the
  useful contribution is an issue, not a PR, because the public surface is
  versioned and the bilingual docs ship in the same commit as the code. It
  explains what makes an issue actionable, why the ordering exists, and what a
  PR needs *after* an issue is accepted. "Docs typo → PR straight against
  `docs/<page>.md`" is gone: a fix touching only one of the two language
  mirrors leaves the site inconsistent, so typos are issues too.

### Added

- **GitHub issue forms** under `.github/ISSUE_TEMPLATE/` — bug, feature / API
  idea, documentation and usage question — with `config.yml` disabling blank
  issues and pointing at the docs site and the private security contact. Plus a
  `PULL_REQUEST_TEMPLATE.md` carrying the project's PT-BR PR shape and a
  documentation checklist.

## [0.167.1] — 2026-07-26

### Fixed

- **`BaseController` now accepts services typed over a concrete update
  schema.** `ServiceT`'s bound was written `BaseService[Any, Any]`, which is not
  a partial application: PEP 696 fills the omitted `UpdateT` with its
  `BaseSchema` default, and since that parameter is invariant the bound admitted
  only services whose update schema was exactly `BaseSchema`. Every service
  written the documented way — `BaseService[Repo, Resp, MyUpdateSchema]` — was
  rejected by the type checker with `Type parameter "UpdateT@BaseService" is
  invariant, but "MyUpdateSchema" is not the same as "BaseSchema"`, forcing a
  `# type: ignore[type-var]` on the controller declaration. The bound is now
  `BaseService[Any, Any, Any]`, which leaves the invariant parameter open while
  keeping the `BaseSchema` constraint. Type-check only — runtime behavior is
  unchanged, and the `# type: ignore[type-var]` workarounds can be dropped.
- **The suite can now see this class of bug.** `mypy` runs over the package, not
  over `tests/`, so `tests/controllers/test_base.py` already declared exactly
  this service/controller pair and stayed green. New
  `tests/controllers/test_generic_bounds.py` inspects `ServiceT.__bound__`
  directly and runs `mypy --strict` over a downstream-shaped snippet, asserting
  no `[type-var]` diagnostic.

### Documentation

- `docs/architecture.md` / `docs/architecture.en.md` — the controllers & services
  section now shows the 3rd generic parameter on both layers and notes that the
  pair requires 0.167.1+.

## [0.167.0] — 2026-07-26

Permission guards: a decorator that runs plain `(user) -> user | None`
functions before a function body, plus the two-layer misuse check the user
asked for — fail-fast at import time, static gate in CI.

### Added

- **`@requires(*guards, user_param=None)`** (`tempest_fastapi_sdk.authz`,
  re-exported at the package root). Runs each guard on the user before the
  decorated body, left to right, on a route handler, a controller or a service
  method, sync or `async`. A guard receives the user, denies by raising an
  `AppException` subclass (so the status, the error `code` and the
  `{detail, code, details}` envelope come from `register_exception_handlers`),
  and returns the user or `None`; a non-`None` return replaces the user the next
  guard and the body see, which is how `require_authenticated` /
  `require_active` / `require_admin` narrow `UserT | None` to `UserT`.
  The user parameter is resolved from the annotations — the single parameter
  typed as a `BaseModel` / `BaseUserModel` subclass — with `user_param=` to
  name it explicitly or disambiguate two user models. The wrapper keeps the
  decorated signature (with annotations already evaluated, so FastAPI resolves
  them against the right module), leaving dependency injection and the OpenAPI
  schema untouched.
- **`TempestPermissionError`** — raised at decoration time (import time) for
  `@requires()` with no guard, a non-callable guard, a guard that does not take
  exactly one fillable parameter, an `async` guard under a synchronous function,
  or a decorated function whose user parameter cannot be resolved. The
  application refuses to start instead of running a check that never fires.
- **`GuardContractWarning`** — warned at call time when a guard raises outside
  the `AppException` hierarchy (the API layer would answer HTTP 500 with no
  error code) or returns a non-user value such as `False` (a predicate-style
  check whose denial cannot be honored). The original exception still
  propagates; the warning names the guard.
- **`declared_guards(fn)` / `guarded_user_param(fn)`** — read a function's
  guards and its user parameter without calling it, for a router audit or a
  test asserting every write route carries a guard.
- **`tempest permissions [--check] [--strict] [--path]`** — static contract
  check over the project source (`ast`, without importing the application), for
  what runtime validation cannot see: a guard whose `raise` no test exercises, a
  guard never wired. Errors: `no-guards`, `user-param-missing`,
  `user-param-ambiguous`, `guard-arity`, `guard-async-in-sync`,
  `guard-returns-bool`, `guard-foreign-exception`. Warnings:
  `guard-never-denies`, `guard-missing-annotation`, `guard-return-type`,
  `guard-unresolved`. `--check` exits non-zero on errors, `--strict` on warnings
  too. Guards resolve by name; an ambiguous or out-of-scope name is reported as
  `guard-unresolved` rather than checked against the wrong definition.

### Changed

- **`tempest openapi-errors` now follows `@requires` guards.** A guard denies by
  raising, so its exceptions are as reachable from the route as those of any
  function the body calls; they now show up as `undocumented` until the route
  declares them, and `--fix` writes them like any other.

## [0.166.1] — 2026-07-25

Post-release validation of `--fix` against the published wheel, in a project
that is not this repository. Three things only that setting can show.

### Fixed

- **`--fix` now formats with the target project's ruff config.** The scratch
  file it hands to ruff was created in the system temp directory, where ruff
  finds no `pyproject.toml` and falls back to its own defaults. A project on
  `line-length = 120`, or with custom `isort` sections, got output formatted to
  ruff's defaults — which its own `ruff format --check` then rejected, on a file
  the command had just written. The scratch file is now created next to the file
  being rewritten, so settings resolve the same way they do for the project.
  `normalize()` takes a `near=` directory for this.
- **Silent skip of that formatting is now reported.** `ruff` was resolved as
  "on `PATH`, else `uv run ruff`", and neither branch was verified. Invoked by
  absolute path from a venv (`/…/.venv/bin/tempest`), that venv's `bin/` is not
  on `PATH`, so resolution fell through to `uv run ruff` — which exists but
  cannot run outside a uv project, failing silently under `check=False`. The
  write then emitted an unsorted import and an over-long decorator with no hint
  why. New `ruff_runner()` adds `python -m ruff` for the importable-but-not-on-
  `PATH` case, probes every candidate with `--version`, and the command prints a
  `note:` line pointing at `tempest fix` when none works.
- **The `docs`-marked tests no longer trust a stale `site/`.** Both fixtures
  rebuilt only when the built file was *absent*, so an out-of-date local build
  was read as current: 14 tests failed on a clean `main` naming pages and
  symbols that were in fact present. CI never saw it, having no `site/` at all.
  A single `built_site` fixture now lives in `tests/conftest.py` and rebuilds
  whenever `mkdocs.yml`, `docs/` or `mkdocs_hooks/` is newer than the output.

### Added

- **`ruff_runner()`** in `tempest_fastapi_sdk.cli.openapi_fix` — returns a
  probed argv prefix that runs ruff, or `None`. Exported for callers that want
  to make the same "can I format this?" decision.

## [0.166.0] — 2026-07-25

### Added

- **`tempest openapi-errors --fix`** — writes the missing error declarations
  back into the routes the check was already pointing at. On a route that
  declares nothing it injects `responses=error_responses(...)` into the
  decorator plus the imports the new names need; on a route that already
  declares some of them it appends to the existing `error_responses(...)` or
  `@raises(...)` call, preserving the original order.
  - `--dry-run` prints a unified diff instead of writing, and runs on a dirty
    tree since it is read-only. Both paths go through the same
    `ruff check --select I --fix` + `ruff format` pass, so the preview is
    exactly what a real write produces.
  - A route that declares nothing always gets `error_responses`, never
    `@raises`: `@raises` is only read by `TempestAPIRouter`, so injecting it
    into a project on a plain `APIRouter` would produce a decorator that
    silently does nothing. An existing `@raises` is extended in place.
  - **It only ever adds.** `unreachable` findings are deliberately not acted
    on — reachability resolves by call name and cannot see a dynamic raise, so
    removing a declaration on its word could delete a correct one.
  - Every edit is anchored on an AST position (the closing parenthesis of a
    call node), never on a regex, so nothing depends on how the decorator is
    formatted and the rest of the file is untouched.
  - An exception whose defining module cannot be resolved unambiguously is
    reported as `unresolved` and its route is skipped, rather than writing an
    import that would break the application.
- **`tempest_fastapi_sdk.cli.openapi_fix`** — the module behind the flag:
  `plan_file` / `render_file` / `normalize` / `unified_diff` /
  `ensure_clean_worktree` / `FilePlan` / `DirtyWorkingTreeError`.

### Changed

- `RouteInfo` (in `cli.openapi_errors`) now records the source positions of a
  route's decorator and of any existing declaration call, which is what makes
  the merge possible. `exception_locations()` maps an exception class name to
  its defining file, dropping names defined in more than one file.

### Notes

- **Requires a clean git working tree to write.** With a clean tree `git diff`
  is the review and `git checkout` is the undo — the real safety net for a tool
  that edits code you wrote. A dirty tree exits 1 with instructions.

## [0.165.0] — 2026-07-25

### Changed

- **Docstring completeness sweep across the whole public surface.** Nothing in
  the API changed; what changed is that the rendered API reference now carries a
  complete parameter/return/raises table for every symbol it documents.
  - **115 explanatory comments moved out of function bodies into the enclosing
    docstring**, per the project rule that the *why* belongs in the docstring.
    They are now visible in the rendered reference instead of only to whoever
    opens the source.
  - **139 missing `Args:` / `Returns:` sections** added across 29 modules —
    mostly the repeated interface methods of the feature-flag backends, session
    stores, idempotency and response-cache stores, the tenant repository mixin
    and the storage backends.
  - **15 rendered symbols had no docstring at all**: the response-cache
    `Protocol` stubs, three OIDC URL properties, a nested token-counting helper,
    and nine auth/session route handlers.
  - **8 `Raises:` sections** on functions that raise from their own body.
  - `make_auth_router`'s `recovery_code_model` parameter was undocumented.

### Notes

- Three measurement corrections worth recording, since each one changed what
  "done" meant:
  - A line-based comment scan **misses trailing comments** (`assert x  # why`)
    and **counts YAML comments inside generated-file string literals** as code
    comments. Both were wrong: the first under-reported, the second would have
    stripped documentation out of the generated `docker-compose.yaml`. The sweep
    was redone with `tokenize`.
  - Section banners in a **class** body (`# Signup`, `# Login`) are outside the
    rule, which is about function/method bodies. Removing them would have made a
    1900-line service harder to navigate for no documentation gain.
  - A `Raises:` on a `make_*_dependency` **factory** would be false: the raise
    happens in the dependency it returns, which those docstrings already state
    under `Returns:`. Nine of seventeen reported gaps were this.

## [0.164.0] — 2026-07-25

### Added

- **The generated `Dockerfile` is fullstack-aware.** When the project holds a
  frontend, `tempest generate --dockerfile` (and `tempest new`) emit a Node
  stage that installs and builds it, and copy **only** the resulting `dist/`
  into the runtime image — neither `node_modules` nor the Node toolchain reach
  the final layer. The `.dockerignore` gains the matching
  `<spa>/node_modules/` + `<spa>/dist/` entries, since the stage produces the
  build and a local copy would ship the developer's machine output instead.
  - Detection is by `package.json` under `web/`, `frontend/`, `client/` or
    `ui/` (`SPA_CANDIDATE_DIRS`), exposed as `detect_spa_dir`. An **empty**
    directory deliberately does not count: emitting a stage for a placeholder
    folder would fail the image build inside `npm ci`, with an error pointing
    nowhere useful.
  - `--spa-dir <dir>` selects an unconventional layout; `--no-spa` forces a
    backend-only image. A `--spa-dir` holding no `package.json` exits 1 rather
    than silently degrading to a backend-only build.
  - `npm ci` requires a lockfile, so a freshly scaffolded frontend would break
    the first build; the stage falls back to `npm install` when
    `package-lock.json` is absent.
  - A project with no frontend renders a **byte-identical** Dockerfile to
    before, verified against the pre-change output.

## [0.163.0] — 2026-07-25

### Added

- **`make_spa_router(dist_dir)`** (`tempest_fastapi_sdk.api.spa`): serve a
  compiled React/Vite SPA from the FastAPI process, making a fullstack service
  one origin and one deployment. Mounting `StaticFiles` alone is not enough —
  a client-side router owns paths that exist in the browser and not on disk, so
  a bare static mount 404s on every deep link and refresh. This router adds the
  SPA fallback plus the details that are easy to get wrong:
  - `index.html` is served `no-store` while content-hashed assets are
    `immutable` for a year. Inverting this is the classic "users keep running
    the old bundle after a deploy" bug, since the document is the one file whose
    URL never changes.
  - API prefixes (`DEFAULT_EXCLUDED_PREFIXES`, overridable) are excluded from
    the fallback, so a typo'd endpoint stays a JSON 404 instead of a 200 with an
    HTML body — which surfaces in the client as a confusing parse error.
  - Only `GET`/`HEAD` fall back; `..` traversal cannot escape the build
    directory in any encoding; a missing build raises at wiring time rather than
    booting a service that 404s every page.
- **Bilingual recipe "React SPA inside FastAPI"** covering the three modes: dev
  with a Vite proxy, same-origin production, and the scaffold + multi-stage
  Dockerfile. Documents the shared contract with
  [`tempest-react-sdk`](https://github.com/mauriciobenjamin700/tempest-react-sdk)
  — `createViteConfig`, `createApiClient`, `createTempestAuth`, `AuthGuard`,
  and the `{detail, code, details}` error envelope both sides agree on.

### Fixed

- The SSE recipe imported `tempest-react-sdk` under a scoped name
  (`@mauriciobenjamin700/tempest-react-sdk`) that does not exist on npm; the
  package is published unscoped as `tempest-react-sdk`. The snippet could never
  have resolved.

## [0.162.0] — 2026-07-25

### Fixed

- **The API reference was missing 190 public symbols** while claiming, in its
  own opening paragraph, that every exported symbol was documented. Whole
  feature areas had a recipe but no reference at all — `tasks` (29), `geo` (29),
  `chat` (12), `reviews` (11), `queue` (4) — and the top-level directive carried
  an `!^[a-z_]+$` filter that silently excluded **every function** from it.
  Coverage is now 100% of `__all__` across the public modules, bar ten
  deliberately excluded names.
- **`llms.txt` was missing 25 of the 73 pages on the site.** The generating hook
  used a hard-coded page list that drifted, so every feature shipped after it was
  written (generative AI, geolocation, chat, reviews, vision, SSR, both OpenAPI
  tools) was invisible to LLM consumers while being perfectly visible on the
  site. Its summary also advertised ten extras when the package ships more than
  twenty.
- `docs/installation.md` and `docs/installation.en.md` had **drifted from each
  other** — the PT page pinned `>=0.137.0`, the EN mirror `>=0.133.1`. Both, and
  the `README.md` snippet, now reference the current release.
- Three links in `docs/admin-showcase.en.md` pointed at the **Portuguese** anchor
  slug of the SSE recipe, so they resolved to the top of the English page instead
  of the section.
- `Server-Sent Events (SSE)` and `Tipagem (estático + runtime)` had no
  `nav_translations` entry, leaving Portuguese labels in the English navigation.

### Changed

- `mkdocs_hooks/llmstxt.py` derives its sections from the MkDocs `nav` and its
  extras list from `[project.optional-dependencies]`, so neither can drift from
  the site again.
- `docs/reference.md` uses whole-module `:::` directives for `geo`, `vision`,
  `ssr`, `queue`, `tasks`, `chat` and `reviews` instead of hand-listed symbols —
  a namespace cannot fall behind its own exports.

### Added

- `tests/test_reference_coverage.py` — renders the reference page and asserts
  every `__all__` name across the public modules emitted an anchor. Markdown
  parsing cannot catch this class of gap (a `:::` directive may name a module,
  and filters may exclude names), so the guard checks the **built HTML**.
  Intentional exclusions live in an `ALLOWED_ABSENT` mapping that requires a
  written reason, with a companion test that fails once an entry becomes stale.
- `tests/test_llmstxt.py` — asserts the LLM index links every nav page, keeps the
  llmstxt title/summary shape, advertises the real extras, and that
  `llms-full.txt` carries inlined page bodies.
- A `docs` pytest marker for the two guards above, which trigger one `mkdocs
  build` per session.

## [0.161.0] — 2026-07-25

### Added

- **`tempest openapi-client <spec>`** — generate Pydantic schemas **and** a typed
  HTTP client from a third party's OpenAPI 3 specification, ending the manual
  transcription of their documentation (#97). Point it at a URL or a file (JSON,
  or YAML with the new `[openapi]` extra) and it writes a self-contained
  `<src|app>/integrations/<name>/` package. Options: `--name`, `--out`,
  `--header` (repeatable, for a spec behind authentication), `--path`,
  `--schemas-only`, `--force`, `--no-format`.
- **Generated schemas carry the specification's metadata.** One `BaseSchema`
  class per component, with `title` / `description` / `examples` on every
  `Field`, so the generated module doubles as the integration's documentation and
  survives the third party changing or retiring their docs site. Field names are
  Python-idiomatic with the wire name attached as an `alias` and
  `populate_by_name` enabled, so both spellings are accepted on input and
  `model_dump(by_alias=True)` returns the wire shape. Reserved words are resolved
  (`class` → `class_`), optional collections default to an empty list (never
  `list[X] | None`), string/integer enums become `BaseStrEnum` / `BaseIntEnum`
  subclasses, `allOf` is flattened, and recursive or mutually-recursive models
  get `model_rebuild()` calls. Nothing is invented where the specification
  documents nothing.
- **Generated client wraps an injected `HTTPClient`.** One `async` method per
  operation with typed path/query parameters, a validated request body and a
  validated response, plus a full Google-style docstring listing the documented
  error statuses. Because the transport is injected, the retry policy, circuit
  breaker and credentials stay with the caller and an `httpx.MockTransport`
  exercises the whole integration without a network.
- **`tempest_fastapi_sdk.openapi`** exposes the pieces for programmatic use:
  `load_spec`, `parse_spec`, `emit_schemas`, `emit_client`,
  `generate_integration`, `GenerationResult`, `default_output_dir`, `SpecError`
  and the `SpecIR` / `SchemaIR` / `FieldIR` / `ClientIR` / `OperationIR` /
  `ParameterIR` intermediate representation.
- **`[openapi]` extra** (`pyyaml`) for YAML specifications. JSON needs nothing
  beyond the standard library, and the download uses the already-base `httpx`.

### Notes

- **The emitter's output passes `ruff check` and `ruff format --check` before any
  formatting pass**, which the test suite asserts against the raw
  (`--no-format`) output. That means `--no-format`, or a machine with no ruff
  installed, still produces a usable package — the `ruff` pass the command runs
  by default is polish, not correctness.
- **Regenerating an unchanged specification produces a byte-for-byte identical
  file**, so the `git diff` after a `--force` is the integration's changelog:
  every line is a real change from the third party.
- **Unrepresentable constructs are never guessed.** `not`, external `$ref`,
  Swagger 2.0, non-JSON bodies and `header`/`cookie` parameters degrade to `Any`
  with a `# openapi: unsupported` marker and a line in the command's summary. A
  wrong schema that looks right is worse than a documented gap.

## [0.160.0] — 2026-07-25

### Added

- **`ErrorResponseSchema`** (`tempest_fastapi_sdk.schemas`): the
  `{detail, code, details}` envelope every SDK exception handler already emitted,
  now an exported Pydantic model. Before this a route wanting to declare
  `responses={409: ...}` had nothing to point at and had to retype the shape
  inline (#96).
- **`error_responses(*exception_classes)`** (`tempest_fastapi_sdk`): builds the
  FastAPI `responses=` mapping by reading `status_code` / `code` / `message` /
  `details_example` off each **class** — no instantiation. Groups by status code,
  since OpenAPI allows exactly one response object per status, and distinguishes
  the codes through an `examples` map that Swagger UI and ReDoc render as a
  selector — so two 404s with different codes both stay visible. `summary` comes
  from the class docstring, `detail` from the class `message` or from an
  optional `MessageCatalog` (`catalog=` / `locale=`), and `descriptions=`
  overrides a status's description.
- **`raises(*exception_classes)` + `TempestAPIRouter`**: the same declaration
  written next to the handler. `raises` tags the endpoint and returns it
  unchanged (no wrapper, so FastAPI still sees the original signature);
  `TempestAPIRouter` — a drop-in `APIRouter` — expands the tag into `responses=`
  **before** the route is constructed, so the model reaches
  `components.schemas` as a real `$ref`. An explicit `responses=` wins per
  status code. `declared_raises(endpoint)` / `RaisesSpec` / `RAISES_ATTRIBUTE`
  expose the tag for tooling.
- **`AppException.details_example`**: documentation-only class attribute feeding
  the OpenAPI example's `details` object. Never read at runtime.
- **`InheritedErrorCodeWarning`** (`tempest_fastapi_sdk.exceptions`): emitted at
  class creation when an `AppException` subclass declares neither `code` nor
  `message_key` and therefore answers with one of the SDK's generic identifiers
  (`"CONFLICT"`, `"NOT_FOUND"`, …). That is a silent defect — clients cannot tell
  the failure apart from any other with the same status, and `error_responses()`
  cannot document it. Inheriting a **domain** code (declared by a project-owned
  ancestor) is deliberate specialization and never warns. Silence it with
  `warnings.filterwarnings("ignore", category=InheritedErrorCodeWarning)`.
- **`tempest openapi-errors [--check]`**: static drift check between the
  exceptions each route declares and the ones reachable through
  `router -> controller -> service -> repository`. Parses the project with `ast`
  (never imports the application) and reads both `raise` statements and
  Google-style `Raises:` docstring sections. Reports `undocumented` (the
  documentation hole) and `unreachable` (an inflated list), exits non-zero with
  `--check` so it doubles as a CI gate; `--allow-unreachable` narrows the failure
  to undocumented only, `--path` selects what to scan (defaults to `./src` or
  `./app`). Calls resolve by name and dynamic raises are invisible — both blind
  spots are covered by the `Raises:` section, and the analyzer over-approximates
  rather than hiding a hole.

### Changed

- `AppException`'s documented pattern is now declaring `code` (and
  `status_code`) **in the class body**, with `__init__` building only `message` /
  `details`. Passing `code=` at the raise site still works identically at
  runtime, but hides the value from static introspection — `Exc.code` answers the
  inherited generic while `Exc("x").code` answers the real one, and reading it
  would require knowing each `__init__` signature. The class-body form is what
  makes `error_responses()` possible.

## [0.159.1] — 2026-07-25

### Added

- **`AppSettingsMeta`** (`tempest_fastapi_sdk.settings`): the metaclass
  `BaseAppSettings` now uses. It pre-checks base ordering and raises an
  actionable `TypeError` when `BaseAppSettings` is listed before one of its own
  subclasses (every SDK settings mixin), replacing Python's
  `Cannot create a consistent method resolution order (MRO) for bases ...` —
  which never names the fix — and the misleading `[metaclass]` error the
  pydantic mypy plugin emits on the same line. The new message names both
  classes and prints the corrected base list. It keeps the phrase
  `method resolution order (MRO)` so existing matches on Python's wording still
  hold.
- Doc guard `test_doc_settings_put_base_app_settings_last`: every fenced
  `python` block in `README.md` / `docs/**/*.md` that declares a class with
  `BaseAppSettings` in its bases must place it last. Wrong ordering is valid
  syntax that only fails at class creation, so the existing parse guard could
  not catch it — and the bilingual docs duplicate every snippet.

### Fixed

- **Docs demonstrated the forbidden settings base ordering** (#95). Since
  v0.138.1 every settings mixin subclasses `BaseAppSettings`, so the base must
  come **last**. `docs/recipes/email.md` / `.en.md` shipped a copy-pasteable
  `class Settings(BaseAppSettings, EmailSettings)` that raised `TypeError` on
  import; the `docs/tutorial.md` / `.en.md` project tree, the responsibility
  table and the matching `README.md` rows described `Settings(BaseAppSettings,
  mixins...)`. All corrected, plus a `!!! warning` on the tutorial settings
  page and a `0.138.1` section in `docs/migration.md` / `.en.md` stating the
  rule with the real `TypeError` text.
- **Pagination field named `size` instead of `page_size`** (#60).
  `BaseRepository.paginate` returns `{items, total, page, page_size, pages}`;
  the `README.md` repository table and `docs/recipes/chat.md` / `.en.md`
  listed the key as `size`.

## [0.159.0] — 2026-07-25

### Added

- **`ResponseCacheMiddleware`** (`tempest_fastapi_sdk.api.middlewares`): HTTP
  response caching in two layers.
  - **ETag + conditional GET (always on)** — every cacheable response gets a
    strong `ETag` (sha256 of the body) and a `Cache-Control`; a matching
    `If-None-Match` short-circuits to `304 Not Modified` with an empty body.
  - **Server-side cache (opt-in via `store=`)** — a cacheable `GET`/`HEAD`
    response is stored for `ttl_seconds` and a later matching request is served
    without running the handler (`X-Cache: HIT`); the stored ETag still drives
    conditional-GET `304`s.
  - Stores mirror the idempotency shape: `ResponseCacheStore` protocol +
    `MemoryResponseCacheStore` + `RedisResponseCacheStore` (raw client). Only
    safe methods and successful statuses are cached; `no-store`/`private`/
    `Set-Cookie` responses are never stored. `vary=` folds request headers into
    the key (and emits `Vary`); `cacheable=`/`exempt_paths=` scope it. First
    slice of the *HTTP performance layer* roadmap theme.

## [0.158.0] — 2026-07-24

### Added

- **Dead-letter panel + task inventory** (`tempest_fastapi_sdk.tasks`): persist
  terminally-failed tasks and see/re-run them in the admin.
  - `BaseDeadLetterModel` (abstract, over the SDK `BaseModel`) +
    `make_dead_letter_model(tablename=, class_name=)` — one row per terminal
    failure (task name/id, error + type, retries, args/kwargs as JSON).
  - `DbDeadLetterSink(db, model)` — a ready `DeadLetterSink` that writes each
    dead letter to that table (`tq.dead_letter(DbDeadLetterSink(db, model))`).
  - `make_dead_letter_admin_model(model, tq=None)` — a read-mostly `AdminModel`
    (list/filter by task + error type, search, export) with an optional
    **requeue** bulk action (`make_requeue_action`) that re-enqueues the
    selected calls with their stored `args`/`kwargs` and deletes the rows.
  - `task_inventory(tq)` → `list[TaskInfo]` (name / schedule / retry policy)
    read off the broker, for a "what tasks exist" view.

  Deliberately no live queue introspection — TaskIQ exposes none (Flower is
  Celery-specific), so the panel shows what is real and persisted: terminal
  failures and the declared task set. Everything imports without the `[tasks]`
  extra. Closes the *queue observability + genai tracing* roadmap theme.

## [0.157.0] — 2026-07-24

### Added

- **TaskQueue reliability + observability** (`tempest_fastapi_sdk.tasks`):
  opt-in retry, dead-letter, and per-task metrics, wired onto TaskIQ so
  application code never touches the broker middleware API.
  - **Retry** — `RetryPolicy(max_retries=, on_error=)` carried as task labels
    (`@tq.task(retry=RetryPolicy(...))`); `TaskQueue.enable_retries(...)`
    installs TaskIQ's `SimpleRetryMiddleware` that honours them.
  - **Dead-letter** — `DeadLetter` + `DeadLetterSink` protocol +
    `TaskQueue.dead_letter(sink, default_max_retries=)`: a task that fails with
    no retry configured, or after retries are exhausted, is handed to your sink
    exactly once (a `MessageBroker` channel, a DB row, an alert — the target is
    yours; the SDK assumes no backend). A failing sink is logged, never crashes
    the worker. `make_dead_letter_middleware` for manual wiring.
  - **Metrics** — `TaskMetrics(namespace=, registry=)` records
    `tasks_runs_total{task,status}` + `tasks_duration_seconds{task}` from a
    middleware into the shared Prometheus registry (`[prometheus]` extra);
    `TaskQueue.enable_metrics(...)`.

  Everything imports without the `[tasks]` extra (TaskIQ is only touched at
  wiring time). Second slice of the *queue observability + genai tracing*
  roadmap theme (after the genai spans in v0.156.0).

## [0.156.0] — 2026-07-24

### Added

- **OpenTelemetry spans on genai calls** (`tempest_fastapi_sdk.genai.genai_span`).
  Ambient tracing that reuses the global `TracerProvider` set up by
  `setup_tracing` — call it once at app startup and `TextGenerator` /
  `OllamaGenerator` (`generate` / `chat`), `Embedder.embed`, and the RAG
  `Retriever.search` / `.retrieve` emit spans automatically, alongside the
  existing FastAPI / SQLAlchemy / httpx instrumentation. Spans follow the
  OpenTelemetry **GenAI semantic conventions** (`gen_ai.system` /
  `gen_ai.operation.name` / `gen_ai.request.model`, plus
  `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` on the Ollama
  path); errors mark the span `ERROR` and record the exception. Zero-config
  and zero-cost by default: no-op when the `[otel]` extra is absent and
  non-recording when no provider is configured. Nothing needs to be injected
  into the generators. First slice of the *queue observability + genai
  tracing* roadmap theme.

## [0.155.0] — 2026-07-24

### Fixed

- **Constrained structured output works on transformers 5.x.**
  `build_prefix_allowed_tokens_fn` (used by
  `TextGenerator.generate_structured(constrained=True)`) no longer imports
  `lm-format-enforcer`'s `integrations.transformers` module, whose
  `PreTrainedTokenizerBase` import from `transformers.tokenization_utils` broke
  on transformers 5.x. The adapter is now built from the library's stable core
  (`JsonSchemaParser` + `TokenEnforcer` + `TokenEnforcerTokenizerData`).
  Validated on Qwen2.5-3B (GPU) end to end.
- **`VisionTextGenerator` loads on transformers 5.x.** Uses
  `AutoModelForImageTextToText` (falling back to the renamed
  `AutoModelForVision2Seq`), and `torchvision` — required by modern VLM
  processors (Qwen2-VL) — is now part of the `[genai-vlm]` extra. Validated
  loading + generating on Qwen2-VL-2B (GPU).

### Added

- `@gpu` behavioral validation suite (`tests/genai/test_gpu_validation.py`)
  now covers the constrained-structured and VLM paths on real models (was a
  skip); a `@model` test builds the lm-format-enforcer adapter on a real
  tokenizer under transformers 5.x.

## [0.154.0] — 2026-07-24

### Added

- **`HybridRetriever.retrieve`** + **`SupportsRetrieve` protocol.** The hybrid
  retriever gains the one-shot `retrieve(query, top_k) -> context` helper
  mirroring `Retriever`, and `make_genai_router(retriever=...)` now accepts any
  `SupportsRetrieve` (a `Retriever` — reranking through
  `Retriever(reranker=...)` — or a `HybridRetriever`), so both back the `/rag`
  endpoint unchanged. (Schema-constrained output stays a programmatic API via
  `generate_structured`, not a generic HTTP endpoint.)

## [0.153.0] — 2026-07-24

### Added

- **Metrics + cache on the local generators.** `TextGenerator` and `Embedder`
  now accept `metrics=` (a `GenAIMetrics`) and record request count + latency
  for `generate`/`chat` (op `generate`/`chat`) and `embed` (op `embed`) — the
  metrics were previously wired only into `OllamaGenerator`. `TextGenerator.chat`
  also honors the `generation_cache` (deterministic calls), matching
  `generate`.

## [0.152.0] — 2026-07-24

### Added

- **`AIChatPipeline` moderation + context truncation.** New constructor args:
  `moderator=` (a `ModerationBackend`) screens the user input before generating
  and the reply after — a flagged turn is answered with `blocked_message`
  instead (input check short-circuits the model); and `tokenizer=` +
  `max_context_tokens=` trim the oldest turns (via `truncate_messages`) so the
  built message list fits the model's window before generating. Both are
  opt-in; the pipeline is unchanged when they are unset.

## [0.151.1] — 2026-07-24

### Fixed

- **`import tempest_fastapi_sdk` now works on a base install.** The top-level
  package eagerly imports `auth` (which uses `httpx` in `auth.introspection`
  and `EmailStr` in `auth.schemas`), so importing the package — or any
  submodule like `tempest_fastapi_sdk.genai` — failed with `ModuleNotFoundError`
  unless an extra happened to pull `httpx` + `email-validator`. Both are now
  base dependencies, so `pip install tempest-fastapi-sdk[genai]` (and every
  other minimal install) imports cleanly. Long-standing issue, masked by
  `[all]`/dev environments.

## [0.151.0] — 2026-07-24

### Added

- **Content moderation** (`tempest_fastapi_sdk.genai`) — a pluggable layer to
  screen prompts and completions: `RuleModerator` (dependency-free whole-word
  block-list, the deterministic default) and `ClassifierModerator` (a local
  toxicity/text-classification model over transformers, `[genai]`, lazy +
  to_thread, `flagged_labels`/`threshold`). Both satisfy the
  `ModerationBackend` protocol and return a `ModerationResult`
  (`flagged`/`categories`/`score`), so a caller or `AIChatPipeline` can block
  or annotate per policy.
## [0.150.0] — 2026-07-24

### Added

- **`GenAIMetrics`** (`tempest_fastapi_sdk.genai`) — Prometheus counters +
  histogram for inference: request count, latency, and tokens in/out, labelled
  by model and operation (the `[prometheus]` extra; accepts an explicit
  registry so it composes with `PrometheusMiddleware` / `/metrics`). Opt-in
  via a `track(model, op)` async context manager or the `metrics=` argument on
  `OllamaGenerator` (which records `prompt_eval_count` / `eval_count` from the
  daemon response). No overhead when unset.
## [0.149.0] — 2026-07-24

### Added

- **`make_vision_router`** (`tempest_fastapi_sdk.vision`) — an opt-in FastAPI
  router mirroring `make_genai_router`: inject the loaded `Classifier` /
  `Detector` / `Segmenter` you have and it mounts only the matching endpoints
  (`POST /classify` / `/detect` / `/segment`, multipart `UploadFile`), mapping
  results through the existing `to_*_schemas` helpers. Raises `ValueError` when
  nothing is injected. No import-time `ort-vision-sdk` dependency (objects are
  injected already-constructed).
## [0.148.0] — 2026-07-24

### Added

- **Token counting + context management** (`tempest_fastapi_sdk.genai`) —
  `count_tokens(text, tokenizer)`, `count_message_tokens(messages, tokenizer)`
  and `truncate_messages(messages, max_tokens, tokenizer, keep_system=True)`.
  Truncation keeps system messages and the most recent turn, dropping the
  oldest until the chat fits. Works over any tokenizer exposing
  `encode(text) -> sequence` (HuggingFace `AutoTokenizer` qualifies), so the
  count uses the model's real vocabulary, not a heuristic.
## [0.147.0] — 2026-07-24

### Added

- **Generation cache** (`tempest_fastapi_sdk.genai`) — `InMemoryGenerationCache`
  / `RedisGenerationCache` + `GenerationCache` / `AsyncGenerationCache`
  protocols, wired into `TextGenerator` and `OllamaGenerator` via a
  `generation_cache=` argument. Only **deterministic** generations
  (`do_sample=False` / `temperature=0`) are cached — sampling calls always run
  the model. Keys hash model id + prompt + parameters (`make_generation_key`);
  `is_deterministic` and `cached_generate` are exposed for reuse. The generator
  awaits sync-or-async caches at one call site.
## [0.146.0] — 2026-07-24

### Added

- **`OnnxEmbedder`** (`tempest_fastapi_sdk.genai.OnnxEmbedder`) — torch-free
  text embeddings over ONNX Runtime (`onnxruntime` + `tokenizers`, the new
  `[genai-onnx]` extra). Runs a sentence-embedding model exported to ONNX on
  CPU with a light dependency set, satisfying the same `SupportsEmbed` protocol
  as `Embedder`, so it drops into a `Retriever` / `make_genai_router`
  unchanged. Pooling is the attention-mask-weighted mean of token embeddings
  (never diluted by padding); `normalize=True` L2-normalizes for cosine.
## [0.145.0] — 2026-07-24

### Added

- **`HybridRetriever`** (`tempest_fastapi_sdk.genai.rag`) — hybrid retrieval
  fusing dense vectors and sparse BM25 with Reciprocal Rank Fusion, so exact
  terms (proper nouns, codes, acronyms) a dense retriever misses are recovered.
  `index` builds both a dense store and an in-memory BM25 index (`rank-bm25`,
  added to the `[genai-rag]` extra); `search(query, top_k, candidates)` fuses
  the two rankings. `reciprocal_rank_fusion(rankings, k=60)` is exposed
  standalone for arbitrary rank lists.
## [0.144.0] — 2026-07-24

### Added

- **`Reranker`** (`tempest_fastapi_sdk.genai.rag.Reranker`) — a cross-encoder
  second stage for RAG. Scores each `(query, chunk)` pair jointly with an
  `AutoModelForSequenceClassification` model (e.g.
  `cross-encoder/ms-marco-MiniLM-L-6-v2`), far more precise than dense cosine.
  `Retriever(embedder, store, reranker=...)` over-fetches candidates
  (`search(..., rerank_candidates=20)`) and the reranker narrows them to
  `top_k`; without a reranker the retriever stays dense-only. Lazy load,
  `unload`/`unload_if_idle`, auto device/dtype (the `[genai]` extra). Adds the
  `SupportsRerank` protocol.
## [0.143.0] — 2026-07-24

### Added

- **`VisionTextGenerator`** — local vision-language generation over
  transformers (`AutoModelForVision2Seq` + `AutoProcessor`), the multimodal
  sibling of `TextGenerator`. `generate`/`chat` take optional `images` (path /
  `bytes` / `PIL.Image` / NumPy `ndarray`, normalized by `_load_image`) and stay
  text-compatible, giving the transformers path the multimodal reach
  `OllamaGenerator` already had via its `images` argument. Lazy load, idle
  unload, auto device/dtype. New `[genai-vlm]` extra (Pillow). Targets the
  common `processor(text=, images=)` interface (LLaVA, Qwen2-VL); other
  families may need a thin adapter.

## [0.142.0] — 2026-07-23

### Added

- **Schema-constrained structured output.** `OllamaGenerator.generate_structured`
  and `TextGenerator.generate_structured` take a Pydantic schema and return a
  validated instance. Ollama sends the schema as the daemon `format` field
  (native JSON-schema enforcement, no extra library) — the recommended route.
  The transformers path constrains decoding with `lm-format-enforcer` (new
  `[genai-structured]` extra) when `constrained=True`; on a version skew with
  the installed transformers it raises a clear error pointing to
  `constrained=False` (best-effort) or the Ollama backend. New
  `parse_structured(text, schema)` extracts + validates JSON from any raw
  completion (tolerating Markdown fences and surrounding prose), and
  `build_prefix_allowed_tokens_fn` builds the transformers constraint.

## [0.141.0] — 2026-07-23

### Added

- **`TextGenerator.chat_with_tools`** — tool calling on the local transformers
  backend. Renders the tokenizer chat template with `tools=`
  (transformers >= 4.44), generates, and parses the emitted tool calls
  (`<tool_call>{...}</tool_call>` for Qwen/Hermes, or a bare Llama-style JSON
  object) into the same `{"content", "tool_calls"}` shape `OllamaGenerator`
  returns. This closes the gap where `AIChatPipeline`'s bounded tool loop only
  worked with Ollama — the same pipeline now runs tools on local weights with
  no daemon. Use a tool-capable instruct model (e.g. `Qwen/Qwen2.5-*-Instruct`).

## [0.140.0] — 2026-07-23

### Added

- **`HTTPClient.stream`** — line-by-line streaming (e.g. NDJSON) with retry
  and circuit-breaker covering the stream open; a non-retried error status
  raises `httpx.HTTPStatusError` before the first line, so callers never
  inspect `status_code` on a stream.
- **`HTTPClient(transport=...)`** — inject an explicit
  `httpx.AsyncBaseTransport` (e.g. `httpx.MockTransport`) for tests without
  reaching into the private client.

### Changed

- **Ollama and SearXNG backends now run over `HTTPClient`.** `OllamaGenerator`,
  `OllamaEmbedder` and `SearxngBackend` build (or accept) an
  `HTTPClient` instead of a bare `httpx.AsyncClient`, so every daemon/search
  call gets retry, exponential backoff, a per-host circuit-breaker and
  `X-Request-ID` propagation. `OllamaGenerator`/`OllamaEmbedder` gain
  `transport=` and `retry_policy=` constructor arguments for the
  lazily-created client.
  **Breaking:** the `http_client=` parameter of these three classes now takes
  an `HTTPClient`, not an `httpx.AsyncClient`. Migrate by wrapping:
  `SearxngBackend(url, http_client=HTTPClient())` (tests can pass
  `transport=httpx.MockTransport(...)`). `ContentExtractor` is unchanged and
  still takes an `httpx.AsyncClient`.

## [0.139.0] — 2026-07-23

### Fixed

- **`GenerationConfig.seed` / `.stop` now take effect on the local
  transformers path.** `TextGenerator` previously dropped both fields
  (`to_generate_kwargs` strips them and nothing reapplied them), so a
  configured `seed` never made sampling reproducible and `stop` strings were
  ignored. The generator now reapplies `seed` via `transformers.set_seed`
  before generating and wires `stop` into `model.generate`'s `stop_strings`
  argument (transformers >= 4.44) — for both `generate`/`chat` and `stream`,
  with per-call overrides winning over the config. `OllamaGenerator` already
  honored both. No API change.

## [0.138.2] — 2026-07-23

### Changed

- **`BaseAppSettings` must now be the last base of a composed `Settings`.**
  This documents a constraint introduced by 0.138.1: because every mixin
  now inherits `BaseAppSettings`, Python's C3 linearization forbids
  listing `BaseAppSettings` **before** any mixin — a base cannot precede
  its own subclass. A `Settings` that listed `BaseAppSettings` in the
  middle of its bases (valid before 0.138.1, when mixins inherited raw
  `BaseSettings`) now raises `TypeError: Cannot create a consistent
  method resolution order (MRO)` at import. The fix is a one-line
  reorder — move `BaseAppSettings` to the end of the bases, which was
  already the documented convention. The value of `.env` loading is
  still order-independent; only the base *ordering* is now enforced by
  the interpreter.

### Docs

- The settings-composition recipe (`recipes/http`, PT-BR + EN) and the
  `settings/mixins` module docstring now state that `BaseAppSettings`
  **must** be the last base (not merely "by convention"), and explain
  the `TypeError` raised otherwise. Corrects the 0.138.1 note that
  implied any base ordering was safe.

## [0.138.1] — 2026-07-23

### Fixed

- **Settings mixins now always honor `.env`.** Every settings mixin
  (`ServerSettings`, `DatabaseSettings`, `RedisSettings`, … — all 16)
  now inherits `BaseAppSettings` instead of raw
  `pydantic_settings.BaseSettings`. Pydantic materializes a *complete*
  `model_config` onto every settings class, so a mixin that inherited
  raw `BaseSettings` carried `env_file=None`; when it was listed before
  `BaseAppSettings` in the bases (the documented order), that full
  config overwrote the canonical one and **`.env` was silently
  ignored** — every field fell back to its default (most visibly
  `DATABASE_URL` → the SQLite default) unless the variable was already
  exported into the process environment. Because the app reads real env
  vars in containers, the bug only surfaced locally (CLI, `.env`-driven
  runs). Inheriting `BaseAppSettings` keeps `env_file=".env"`,
  `extra="ignore"` and `case_sensitive=True` on the composed `Settings`
  regardless of base ordering. No consumer code change is required.

## [0.138.0] — 2026-07-20

### Added

- **`LocaleField`** (`tempest_fastapi_sdk.LocaleField`) — the schema-ready
  counterpart of the `Locale` enum, mirroring how `UFField` pairs with `UF`.
  It is `Annotated[Locale, BeforeValidator(normalize_locale_tag)]`, so a
  request field normalizes loose input (`"pt_BR"`, `"PT-BR"`, the bare
  primary subtag `"pt"`) into a `Locale` member and rejects unsupported tags
  with a `422`. Use the `Locale` enum for canonical values you already hold,
  and `LocaleField` on request schemas where the input is client-supplied.
- **`normalize_locale_tag`** (`tempest_fastapi_sdk.normalize_locale_tag`) — the
  loose-string → `Locale` normalizer behind `LocaleField` (also usable
  standalone), analogous to `normalize_uf`.

### Docs

- The fields recipe (PT-BR + EN) documents `LocaleField` with an Enum-vs-Field
  note cross-linked to `LocaleColumnMixin`.

## [0.137.0] — 2026-07-20

### Added

- **`Locale` enum** (`tempest_fastapi_sdk.Locale`) — a curated, dependency-free
  `BaseStrEnum` of common BCP-47 locale tags (`Locale.PT_BR == "pt-BR"`,
  `Locale.EN_US`, plus ~30 more). Each member is the tag itself, so it compares
  to and binds to a `String` column as that value.
- **`LocaleColumnMixin`** (`tempest_fastapi_sdk.LocaleColumnMixin`) — an opt-in
  SQLAlchemy mixin that adds a nullable `locale` column (BCP-47, `VARCHAR(35)`)
  to a model, so a user row can carry the language its notifications and
  localized text should render in without every project re-declaring the
  column. `NULL` means "no preference" — resolve to the app default (e.g. via
  `MessageCatalog`). Pairs with the `Locale` enum and the Web Push recipe.

### Docs

- The **database recipe** (PT-BR + EN) gains a "Locale" section documenting
  `LocaleColumnMixin` + `Locale` block-by-block, cross-linked from the Web Push
  recipe; the API reference lists the new mixin.

## [0.136.1] — 2026-07-20

### Docs

- **Rewrote the Web Push recipe (PT-BR + EN) to the layered
  `router → controller → service → repository` pattern.** Each code block
  now carries its file path and a block-by-block explanation, and the wiring
  matches the conventions used across Tempest services:
  - `WebPushDispatcher` is an infrastructure singleton built lazily in
    `resources.py` (never fails at import without VAPID keys).
  - Concrete `WebPushSubscriptionRepository` subclass instead of a bare
    `BaseRepository`; the SDK `WebPushSubscriptionService` is used as-is (no
    pass-through wrapper); a thin `WebPushController` holds the auth gate.
  - Per-layer DI providers with `session` resolved via `Depends(get_session)`
    (the previous `get_push_service(session)` example could not be used as a
    `Depends()` target).
  - Router uses a bare `/webpush` prefix mounted under `/api`, avoiding the
    `/api/api/...` double-prefix; `make_web_push_router` is framed as an
    opt-in shortcut that bypasses the controller layer.
- No public API changes — documentation only.

## [0.136.0] — 2026-07-20

### Added

- **New `<column>__<op>` filter operators**, available everywhere the
  convention filters are (dict filters, `Q`, and therefore every
  `BasePaginationFilterSchema` subclass via `get_conditions`):
  - `between` → `col BETWEEN lo AND hi`; value is an ordered two-item
    `(lo, hi)` list/tuple (a malformed value is skipped, not raised).
  - `iexact` → case-insensitive equality (`lower(col) == lower(value)`).
  - `like` / `ilike` → raw `LIKE` / `ILIKE` with the caller's own `%` / `_`
    wildcards, **not** escaped (use `contains` / `startswith` / `endswith`
    for escaped user input). `ilike` is always case-insensitive; plain `like`
    case-sensitivity is backend-defined — prefer `ilike` / `iexact` for
    portable case handling.
  - `not_in` → readability alias for the existing `notin`.

### Changed

- **Convention filters accept any non-string iterable for membership, not
  just `list`.** `build_filter_condition` — shared by `BaseRepository` dict
  filters and `Q` — now treats a `set`, `tuple`, `frozenset`, `range`, `dict`
  view or one-shot generator the same as a `list`, emitting `col.in_(values)`.
  A bare filter (`{"id": some_set}`) and the `__in` / `__notin` suffixes both
  benefit, so callers no longer wrap a `set` in `list(...)` just to hand it to
  a filter. The iterable is materialized once (so generators survive the
  count/page double-use), and `str` / `bytes` / `Mapping` stay scalars — a
  plain string value is still equality, never a character-wise `IN`.

## [0.135.0] — 2026-07-19

### Added

- **`query_param` on `UserAuthService.current_user_dependency`.** The service
  wrapper now forwards a `query_param` to `make_jwt_user_dependency`, so a
  cookieless client (e.g. the browser `EventSource`/SSE, which can't send an
  `Authorization` header) can authenticate off `?access_token=<jwt>` without
  dropping to the low-level factory. Unlike `cookie_name` it is never
  auto-derived — it's an opt-in escape hatch; enable it only over TLS with
  short-lived access tokens.

## [0.134.0] — 2026-07-19

### Added

- **`SSEData` type alias** (exported from the top level and `tempest_fastapi_sdk.sse`):
  `str | bytes | Mapping[str, Any] | Sequence[Any] | int | float | bool | None` —
  the payload contract for SSE publishers.

### Changed

- **Type the SSE `data` parameter with `SSEData`** instead of `Any` on
  `ServerSentEvent.data`, `EventStream.publish` and `SSEBroker.publish`. Runtime
  behavior is unchanged (`str`/`bytes` sent as-is, everything else JSON-encoded
  via `json.dumps(..., default=str)`); this only tightens the static type.
  Passing a bare object that relies solely on `default=str` serialization (e.g.
  a top-level `UUID`) now needs an explicit `str(...)`/dict wrap to satisfy the
  type checker.

## [0.133.1] — 2026-07-18

### Changed

- **Bump the `tempestweb` floor to `>=0.60.0`** in the `[ssr]` and `[all]` extras
  (previously `>=0.9.0`). The `tempest_fastapi_sdk.ssr` bridge
  (`Page`, `html_response`, `make_htmx_router`, `make_web_app_router`,
  `build_web_app`, `detect_build_mode`) is developed against the current
  `tempestweb` line; pinning the floor to the latest release keeps a fresh
  install from resolving a years-stale backend that predates those APIs.

## [0.133.0] — 2026-07-18

### Added

- **Batch object storage on `AsyncMinIOClient`** — three concurrent, order-aware
  helpers for the common "resolve one key per row on a list endpoint" pattern,
  replacing serial `await` loops over the single-key methods:
  - `presigned_get_urls(keys)` → `dict[str, str]` — signs many download URLs at
    once, deduplicating keys.
  - `put_objects(items)` → `dict[str, str]` — uploads many objects, each described
    by the new **`PutObjectItem`** dataclass (mirrors `put_object`'s per-object
    arguments: `content_type`, `metadata`, `length`, `part_size`).
  - `get_objects_bytes(keys)` → `dict[str, bytes]` — downloads many small objects,
    deduplicating keys.

  All three are **fail-fast** (the first failure aborts the batch and propagates)
  and bound their in-flight work with a semaphore via `max_concurrency` (default
  16; `0`/negative raises `ValueError`), so a large page cannot saturate the
  thread executor.
- **`StoredFileServiceMixin.file_urls(keys)`** — the batch counterpart of
  `file_url`. Drops `None`/empty keys, collapses duplicates and returns a
  `dict[str, str]` keyed by object key, so a page of rows resolves its presigned
  URLs in one bounded fan-out (`urls.get(row.key)` yields `None` for an empty
  key). `PutObjectItem` is now exported from the package root.

## [0.132.0] — 2026-07-15

### Added

- **`WebPushSettings.enabled`** — a property returning `bool(VAPID_PRIVATE_KEY)`,
  so services can gate dispatch on "is Web Push configured" without hand-rolling
  the check next to `webpush_kwargs()`.
- **`make_web_push_router(vapid_public_key=...)`** — optional; when set (a string
  or a zero-arg callable), mounts a public `GET {prefix}/vapid-public-key`
  returning `{"public_key": ...}` so the browser can subscribe without baking the
  key into the frontend build. Omitted by default.

## [0.131.0] — 2026-07-15

### Added

- **Versioned artifact registry** (`tempest_fastapi_sdk.artifacts`) — the generic
  core of a "DB-backed, activatable binary artifact" registry, for serving any
  versioned blob (ML models, rule bundles, config packs) from object storage:
  - `ArtifactVersionMixin` — SQLAlchemy 2.0 declarative mixin (`name`,
    `version`, `file_key`, `is_current`) to mix into a concrete `BaseModel`.
  - `ArtifactRegistry[TModel]` — `current(name)`, `list_current()` and
    `activate(version_id)` (sets `is_current` on one row and clears the
    same-`name` siblings in a single transaction).
  - `build_manifest_entries(registry, digest_source=...)` → `ArtifactManifestEntry`
    (`name`/`version`/`file_key`/`sha256`/`size`), serialization-agnostic so the
    app owns the wire shape and URL scheme.
  - `file_digest(path)` / `object_digest(client, bucket, key)` — streamed
    (1 MiB chunks) sha256 + size, memoized by immutable identity.
  - `make_activate_artifact_action(label=...)` — an admin-action factory
    (register via `actions=[action.handler]`) reusing the SDK admin context.

  Object serving/`object_digest` need the `[minio]` extra; the mixin, registry,
  `file_digest` and the admin action run on core deps. See the new
  *Artifact registry* recipe.

## [0.130.0] — 2026-07-15

### Added

- **Introspection-based bearer auth** (`tempest_fastapi_sdk.auth.IntrospectionAuth`)
  — a reusable dependency for services that do **not** issue their own tokens
  but validate an opaque bearer against an upstream userinfo/introspection
  endpoint (OAuth2 resource-server pattern). Validates a token by calling
  `userinfo_url` (a string or a lazily-resolved callable), caches successful
  lookups in-process for `cache_ttl_seconds` (evicting on 401/403), optionally
  gates access on an app-membership claim (`required_app` / `app_claim`), and
  extracts the user id from `subject_claim`. Expose `Depends(auth.get_claims)`
  and `Depends(auth.get_user_id)` on any route. See the new
  *Introspection auth* recipe.

### Changed

- **`WebPushSubscriptionService.notify_user(..., exclude_endpoints=...)`** — skip
  specific devices when fanning a payload out, the common case being a
  multi-device sync notification where the device that made the change must not
  notify itself. Excluded devices are never contacted and never pruned.
- **`CORSSettings.CORS_ORIGIN_REGEX` + `apply_cors(origin_regex=...)`** — allow a
  regex matched against the request `Origin` for session-varying origins (ngrok
  / Cloudflare dev tunnels, preview deploys). Empty disables it; unlike
  `["*"]`, it is compatible with `CORS_ALLOW_CREDENTIALS=True`.

## [0.129.0] — 2026-07-11

### Added

- **Typed SSR attribute builders** (`tempest_fastapi_sdk.ssr`) — `htmx()`,
  `aria()` and `data()` assemble a widget's open `attrs: dict[str, str]` from
  typed keyword arguments, moving `hx-*` / `aria-*` / `data-*` call sites from
  stringly-typed dicts to autocompleted, statically-checked code. Each returns
  exactly the plain `dict[str, str]` you'd write by hand (mergeable via
  `{**htmx(...), **aria(...), "id": "x"}`), so nothing is hidden. `htmx()`
  renders booleans as `"true"`/`"false"`, JSON-encodes a mapping passed to
  `vals`/`headers`, and turns `on={":after-request": "…"}` into
  `hx-on::after-request`; `data()` maps `user_id` → `data-user-id`. Pure
  functions, no extra dependency — the base `attrs` type stays `dict[str, str]`
  because the HTML attribute space is open.

## [0.128.0] — 2026-07-11

### Added

- **Serve a compiled `tempestweb` build from FastAPI** (`tempest_fastapi_sdk.ssr`)
  — host a `tempestweb build` artifact directly from an SDK service. Two entry
  points, each with the shape that fits the artifact:
  - `make_web_app_router(directory)` → an `APIRouter` that serves a **static**
    (wasm) SPA build with a single-page history fallback (unmatched paths →
    `index.html`), correct MIME for `.wasm`/`.mjs`/`.webmanifest`, `no-cache` on
    the shell + service worker (`Service-Worker-Allowed: /`), `asset_cache_control`
    for other assets, and path-traversal protection. No CSP is imposed (first-party
    code + Pyodide `wasm-unsafe-eval`); pass `security_headers=` to add one.
    Include it **last** so API routes win over the catch-all.
  - `build_web_app(directory)` → a `FastAPI` sub-app hosting a **server** build
    (WebSocket/SSE via `tempestweb.server.create_app`, `/static` client + shell at
    `/`) — the same wiring the artifact's generated `server.py` does, in-process.
  - `detect_build_mode(directory)` returns `"wasm"` or `"server"`.

  The SDK only *serves* an already-built `dist/` — building stays in the tempestweb
  CLI/CI flow. `tempestweb` is imported lazily (only the server path needs it).

## [0.127.0] — 2026-07-11

### Added

- **Admin in-place inline editing** — `Inline(editable=True, can_delete=True)`
  turns a read-only child table on the parent's detail view into an editable
  formset: one input row per existing child plus a blank row to add another,
  posting to `POST /admin/m/<parent>/<id>/inlines/<child>`. Existing rows are
  updated, a blank row with any value becomes a new child, and a checked delete
  box removes a row — all in one transaction. The parent foreign key is implied
  (forced to the parent, never rendered as an input), every child row is scoped
  to the parent (a mismatched foreign key is ignored, never cross-edited),
  upload/autocomplete columns stay on the child's own form, and validation
  errors re-render the formset in place with per-field messages. Requires the
  child model's own registered `AdminModel` and its `can_edit` (and `can_delete`
  for deletion). `build_form_fields`/`parse_submission` gained an `only=` filter
  and a new `inline_editable_names` helper backs the field selection.

## [0.126.0] — 2026-07-11

### Added

- **Model factories for tests** (`tempest_fastapi_sdk.testing`) — `ModelFactory`
  binds a `BaseModel` + default column values to a session so tests build rows
  tersely: `build()` (unsaved), `create(**overrides)` (add + flush + refresh) and
  `create_many(count)`. A default (or override) that is **callable** receives the
  row's incrementing index — how unique fields are generated — and `seq("u{n}@x")`
  is a helper for the common case. No magic: the factory never guesses required
  values, you declare the defaults; `flush` (not `commit`) keeps rows in the
  test's transaction. Framework-agnostic (no `pytest` import). `ModelFactory` and
  `seq` are exported from `tempest_fastapi_sdk.testing`.

## [0.125.0] — 2026-07-11

### Added

- **Outbound webhooks — `WebhookSender`** (`tempest_fastapi_sdk.api`) — the
  counterpart to `WebhookSignatureVerifier`: POSTs a JSON event to a subscriber
  URL, signs the exact body with the **same** verifier instance (so the receiver
  validates with that verifier), and retries transient failures — connection
  errors, `5xx`, `429` — with exponential backoff; other `4xx` are not retried.
  Sends `X-Webhook-Event` / `X-Webhook-Id` (unique uuid) / `X-Webhook-Timestamp`
  headers plus the signature. `send()` returns a `WebhookDelivery`
  (`delivered` / `status_code` / `attempts` / `error` / `delivery_id`);
  `send_many()` fans the same event out concurrently. The `httpx.AsyncClient` is
  injected (caller owns the lifecycle). `WebhookSender` and `WebhookDelivery` are
  re-exported at the package root and from `tempest_fastapi_sdk.api`. Pairs with
  the outbox (`OutboxRelay`) for at-least-once signed delivery.

## [0.124.0] — 2026-07-11

### Added

- **`BusinessMetrics`** (`tempest_fastapi_sdk.api`, `[prometheus]` extra) — a
  typed factory for application metrics bound to the shared Prometheus
  registry, so a service declares its own `counter` / `gauge` / `histogram`
  (orders placed, queue depth, job duration) without repeating the `registry=`
  wiring or touching the global default registry. Optional `namespace=` prefix;
  creation is de-duplicated by name (calling a factory twice with the same name
  returns the same metric instead of raising `Duplicated timeseries`). The
  returned objects are the real `prometheus_client` metrics — no wrapping, no
  magic — and land on the same `/metrics` endpoint as the built-in HTTP
  collectors. Re-exported at the package root and from `tempest_fastapi_sdk.api`.

## [0.123.0] — 2026-07-11

### Added

- **More `field__op` filter operators** (shared by `Q` and the `BaseRepository`
  dict filters): `in` / `notin` (`IN` / `NOT IN`), `isnull` (`IS NULL` when
  truthy, `IS NOT NULL` when falsy — note `isnull` is exempt from the
  "`None` value skips" rule since its value is a bool), and case-insensitive
  `contains` / `icontains` / `startswith` / `endswith` (`ILIKE`). These join the
  existing `gt` / `gte` / `lt` / `lte` / `ne`. Kept explicit (one branch per
  operator, no operator-name magic) so the supported set is greppable and typed.

## [0.122.0] — 2026-07-11

### Fixed

- **Admin CSS**: the components added this cycle (audit history timeline, inline
  tables, dashboard cards, lens tabs, autocomplete dropdown, CSV-import report)
  referenced an undefined `--tempest-border` variable — borders fell back to the
  text color. Defined `--tempest-border`, `--tempest-surface` and
  `--tempest-accent-soft` in `:root`, and repointed the dashboard cards and the
  autocomplete dropdown off the dark **sidebar** background vars
  (`--tempest-bg-soft` / `--tempest-bg`) onto the light `--tempest-surface`, so
  they no longer render as dark boxes on the light content area.

### Changed

- **Admin detail layout**: related-record inlines now render immediately after
  the record's own fields (before the Audit / History metadata), and `JSON`
  columns are pretty-printed in a monospaced block — matching the JSON edit
  widget — instead of showing a raw `dict` repr.

## [0.121.0] — 2026-07-11

### Added

- **Admin JSON + time field widgets** — the create/edit form now renders `JSON`
  columns as a monospaced JSON editor (pretty-printed on load, parsed +
  validated on submit — invalid JSON is a field error, not a stored string) and
  `Time` columns as an `<input type="time">`. Previously a JSON column fell
  through to a plain text input (storing the raw string) and a time column had
  no dedicated widget. Part of the admin refinement pass.

## [0.120.0] — 2026-07-11

### Added

- **Admin lenses (saved list-view presets)** — `AdminModel(lenses=[Lens(...)])`
  renders named presets as tabs above the list view. A `Lens(name, filters=...,
  order_by=..., label=...)` bundles filter conditions (same dict conventions as
  the repository) and an optional ordering; selecting it (`?lens=<slug>`) ANDs
  its filters under the user's own search/filters and applies its ordering
  unless the user clicked a column sort. An "All" tab clears the lens; the
  active lens is preserved across pagination, sort and export links. `Lens` is
  exported at the package root and from `tempest_fastapi_sdk.admin`. This closes
  the Tier 3 "lenses" item — the admin-panel evolution is now essentially
  complete (only in-place inline editing remains).

## [0.119.0] — 2026-07-11

### Added

- **Admin granular RBAC** — `make_admin_router(access_policy=...)` takes an
  optional `(principal, admin, AdminPermission)` → bool hook (sync or async)
  consulted for every model action. A denied `VIEW` / `CREATE` / `EDIT` /
  `DELETE` yields `403`; denied `VIEW` also hides the model from the dashboard
  and the sidebar nav, and denied `CREATE` / `EDIT` / `DELETE` hide the
  corresponding buttons/links. Enforced across list, detail, create, edit,
  delete, bulk (delete → `DELETE`, others → `EDIT`), export, import and FK
  autocomplete. The policy composes with — does not replace — the
  `AdminModel.can_create` / `can_edit` / `can_delete` flags (both must allow).
  With no policy the behavior is unchanged. `AdminPermission` (enum) and
  `AdminAccessPolicy` (type) are exported at the package root and from
  `tempest_fastapi_sdk.admin`. This closes the Tier 3 "granular RBAC" item of
  the admin-panel evolution.

## [0.118.0] — 2026-07-11

### Added

- **Admin CSV import** — `AdminModel(can_import=True)` exposes a CSV import page
  (`GET`/`POST {prefix}/m/{slug}/import`) that bulk-creates rows from an uploaded
  file. Each CSV row is validated and coerced with the same rules as the create
  form; valid rows are inserted best-effort (one bad row never aborts the rest)
  and the page reports the created count plus a per-row error table. A
  "Import CSV" link appears on the list view when enabled. Opt-in (default
  `False`) and also gated on `can_create`. This is the counterpart to the
  existing CSV/JSON export (Tier 3 of the admin-panel evolution).

## [0.117.0] — 2026-07-11

### Added

- **Admin dashboard business-metric cards** — `AdminSite(dashboard_cards=[...])`
  renders value / trend / partition cards at the top of the dashboard, each
  computed from the application's own data (distinct from the system CPU/RAM/disk
  panel). A `MetricCard(label, compute, help_text=...)` pairs a heading with an
  async `compute(session)` returning `MetricValue` (a number + optional unit),
  `MetricTrend` (current vs previous, exposing `delta` / `pct` / `direction`) or
  `MetricPartition` (labeled segments with a `total` + rendered bars). A card
  whose compute raises is skipped so one broken metric never blanks the page.
  `MetricCard` / `MetricValue` / `MetricTrend` / `MetricPartition` are exported
  at the package root and from `tempest_fastapi_sdk.admin`. This closes the
  Tier 2 "dashboard metrics" item of the admin-panel evolution.

## [0.116.0] — 2026-07-10

### Added

- **Admin inlines / nested relations** — `AdminModel(inlines=[Inline(Child,
  Child.parent_id)])` lists a model's 1-N children on its detail view as a
  compact table (Django `TabularInline` analog). Each row links to the child's
  own admin and an "Add" button pre-fills the parent foreign key via a create
  query param; the inline reuses the child admin's `list_display` and CRUD
  routes (falling back to read-only rows when the child is unregistered). The
  new `Inline` config (model + `fk_field` + optional `list_display`/`label`) is
  exported at the package root and from `tempest_fastapi_sdk.admin`. Rows are
  capped at 50 per inline. This closes the Tier 2 "inline / related editing"
  item of the admin-panel evolution (in-place editing on the parent form
  remains a follow-up).
- The admin create form now pre-fills editable fields from query parameters,
  which is what an inline "Add" link uses to seed the parent foreign key.

## [0.115.0] — 2026-07-10

### Added

- **Admin autocomplete FK fields** — `AdminModel(autocomplete_fields=[...])`
  renders the listed foreign-key columns as a typed HTMX search box instead of a
  `<select>` of every related row, removing the 1000-row cap and the plain-UUID
  fallback for large target tables. A new `GET /m/{slug}/autocomplete/{field}`
  endpoint (session-guarded) searches the referenced admin's `search_fields`
  (ILIKE, ORed, capped at 20 results) and returns an `<li>` option fragment; the
  edit form pre-fills the current row's label. The target table must have its
  own registered `AdminModel`. This closes the Tier 2 "autocomplete FK fields"
  item of the admin-panel evolution.

## [0.114.0] — 2026-07-10

### Added

- **Admin audit-history viewer** — `AdminModel(audit_model=...)` renders a
  per-row change timeline in the admin detail view, read from a
  `BaseAuditLogModel` table (matched on `entity` = the model name and
  `entity_id` = the row id, newest first, capped at 50 entries). Each entry
  shows the action (create/update/delete, color-coded), the actor and
  timestamp, and a field-by-field before/after diff. Pair it with
  `BaseRepository(audit_model=...)` + `add_audited` / `update_audited` /
  `delete_audited` so the trail is written. Without `audit_model` the detail
  view is unchanged (only the `created_by` / `updated_by` stamps). This closes
  the Tier 1 "audit history viewer" item of the admin-panel evolution.

## [0.113.0] — 2026-07-10

### Added

- **Management commands — project-registered `tempest <cmd>`** — a service can
  now plug its own commands into the `tempest` CLI, like Django's
  `manage.py <command>`. Expose a `typer.Typer` named `commands` (or `app`) in a
  discovered module — `src/commands.py`, `app/commands.py` or `commands.py`,
  auto-detected — and its commands appear as first-class `tempest <cmd>` entries
  sharing the SDK's help rendering. Override the location with
  `[tool.tempest] commands = "src.management"` (string or list) in
  `pyproject.toml`. A project command whose name collides with a built-in is
  skipped (with a stderr warning) so the SDK's commands always win; discovery is
  best-effort and never blocks the built-in commands. Nested Typer groups work
  (`tempest ops resync`). `mount_project_commands` in
  `tempest_fastapi_sdk.cli.commands` implements the discovery.

### Changed

- The `tempest` console script now points at `tempest_fastapi_sdk.cli.main:main`
  (a thin wrapper that mounts project commands before running) instead of the
  `app` object directly. The `app` Typer instance is unchanged for programmatic
  use.

## [0.112.0] — 2026-07-10

### Added

- **System checks (`tempest_fastapi_sdk.checks`) + `tempest check-config`** — a
  Django-style framework to validate configuration before serving traffic. A
  check is a `(context) -> Iterable[CheckMessage]` function registered with
  `@check(*tags)` / `register_check`; `CheckMessage` carries a `CheckLevel`
  (DEBUG/INFO/WARNING/ERROR/CRITICAL), message, hint and id, built via the
  `debug` / `info` / `warning` / `error` / `critical` helpers. `run_checks`
  collects messages; `run_system_checks` raises `SystemCheckError` when any
  reaches `fail_level` (default ERROR) — call it from a FastAPI lifespan to fail
  fast on a misconfigured deploy. `CheckRegistry` backs isolated sets;
  `default_registry` is the process-wide one.
  - **Built-in checks** flag common misconfigurations off any `*Settings` shape
    (best-effort via `getattr`): empty/weak signing secret (`JWT_SECRET` /
    `SECRET_KEY` / `TOKEN_SECRET`, < 32 chars), CORS `*` with credentials,
    SQLite `DATABASE_URL` while `DEBUG` is off, `DEBUG` enabled, `0.0.0.0` bind.
  - **`tempest check-config`** runs the checks against the project's settings
    (auto-detected from conventional locations, or `--settings module:attr`),
    with `--tag` filtering, `--import` for extra check modules, and
    `--fail-level`; exits non-zero when a message reaches the threshold.
  - The framework API (`CheckLevel`, `CheckMessage`, `CheckRegistry`,
    `SystemCheckError`, `check`, `register_check`, `run_checks`,
    `run_system_checks`) is re-exported at the package root.

## [0.111.0] — 2026-07-10

### Added

- **`F` / `Q` expression wrappers (`tempest_fastapi_sdk.db`)** — Django-style
  ergonomics over SQLAlchemy, wired into `BaseRepository`:
  - **`F`** references a column by name and builds arithmetic against it
    (`F("stock") - 1`, `100 - F("stock")`, `F("price") * F("qty")`). Passed as a
    `bulk_update` value it computes the new value in the database — an atomic
    update with no read-modify-write race.
  - **`Q`** captures the repository's dict-filter conventions (`name` ILIKE,
    `field__gte` comparisons, list `IN`, …) as an object combined with `&` / `|`
    / `~` for real `OR` / `NOT` trees. Pass it as the new `where=` argument on
    `get` / `get_or_none` / `first` / `list` / `count` / `exists` / `paginate` /
    `delete_many`; it is ANDed with any dict `filters`. `TenantScopedRepository`
    threads `where=` through its scoped overrides.
  - Both are re-exported at the package root and from `tempest_fastapi_sdk.db`.

### Changed

- `BaseRepository._apply_filters` now shares its per-field logic with `Q` via
  the new `build_filter_condition` helper (single source of truth for the
  filter conventions); behavior is unchanged.

## [0.110.0] — 2026-07-10

### Added

- **Object-level permissions (`tempest_fastapi_sdk.authz`)** — authorization
  that takes the row into account ("may **this** user edit **this** order?"),
  complementing the token-only static guard. Register a `(user, obj) -> bool`
  rule (sync or async) with `@permission("order.delete")` /
  `PermissionRegistry.register`; ask with `has_perm(user, perm, obj=...)` or
  enforce with `check_permission(...)` (raises `ForbiddenException`). Resolution:
  `None` user denied → superuser bypass (`is_superuser`, default `user.is_admin`)
  → object rules (any truthy grants) → static permission-set fallback
  (`permission_resolver`, default `user.permissions`); rules match exact strings
  or `order.*` / `*` wildcards. `make_permission_checker(perm, get_user=...,
  get_object=...)` builds a FastAPI route guard (omit `get_object` for a
  model-level check). `PermissionMixin` adds `await user.has_perm(perm,
  obj=...)`. Superuser predicate and resolver are injectable per
  `PermissionRegistry`; `PermissionRegistry.clear()` aids test isolation. The
  main API (`PermissionRegistry`, `PermissionMixin`, `has_perm`,
  `check_permission`, `permission`, `make_permission_checker`, `default_registry`)
  is re-exported at the package root. Imports without any extra (FastAPI is a
  core dependency).

## [0.109.0] — 2026-07-10

### Added

- **`BaseRepository` eager-loading via `with_=`** — every read method
  (`get`, `get_or_none`, `get_by_id`, `first`, `list`) now accepts
  `with_=["author", "orders.items"]` to eager-load relationships in the same
  query. Dotted paths traverse nested relationships; each hop uses
  `selectinload`, so N related rows cost one extra query per level (not N) and
  both collection and scalar relationships work. Kills the `MissingGreenlet`
  error from touching a relationship after the async session closed. An unknown
  relationship name raises `ValueError` up front.
- **`BaseRepository` lifecycle signals (`tempest_fastapi_sdk.db.signals`)** — a
  process-global registry emitting `PRE_SAVE` / `POST_SAVE` / `PRE_DELETE` /
  `POST_DELETE` around the unit-of-work write path. Register sync or async
  handlers per model with `connect` / `on_signal` (decorator) / `disconnect`;
  `RepositorySignal` enum + `SignalHandler` type + `clear_signals` (test
  isolation) are exported. Handlers registered on a base model apply to
  subclasses (MRO-resolved). `add` / `add_all` / `update` / `update_many` /
  `soft_delete` / `restore` / `delete` fire signals; the set-based bulk methods
  (`bulk_update`, `bulk_create_values`, `bulk_upsert`, `delete_many`,
  `delete_batch`) bypass them by design. A `PRE_SAVE` handler that raises vetoes
  the write (rollback + re-raise). `PRE_DELETE`/`POST_DELETE` only load the row
  when a delete handler is registered — zero overhead otherwise — and the row is
  detached before commit so its columns stay readable in `POST_DELETE`.
  `RepositorySignal` and `on_signal` are also re-exported at the package root.

### Changed

- `BaseRepository._raise_not_found` is now typed `NoReturn`, so type-checkers
  narrow correctly after a not-found guard (removed a redundant `cast`).

## [0.108.0] — 2026-07-10

### Added

- **Self-hosted AI chat, end to end (`tempest_fastapi_sdk.genai`)** — enough to
  run an LLM chat app in-process, so a separate inference service becomes an
  organizational choice rather than a necessity:
  - **`AIChatPipeline`** — composable orchestrator: memory recall → optional
    web-search augment → build messages (system + memory + context + history +
    user turn, images on the user turn) → generate (with a bounded
    tool-calling loop when tools + a tool-capable backend are present, else
    plain chat) → optional TTS → best-effort index of both turns into memory.
    `respond()` returns an `AIChatResult` (reply, sources, memory_hits,
    tool_calls_made, audio_base64); `stream()` yields tokens.
  - **`Tool`** (name/description/parameters/handler + `to_spec()`) for function
    calling, and **`make_ai_chat_router`** exposing `POST /chat` +
    `POST /chat/stream` (SSE). The router is stateless (history comes from the
    request).
  - **`ChatMemory`** — recency-aware, per-user long-term chat memory over a
    Chroma collection: `index()` embeds + upserts and evicts oldest over a
    per-user quota; `search()` does a metadata-filtered query scoped to the
    user (optionally excluding the current chat), applies a similarity floor,
    then blends a recency decay (`0.5 ** (age/halflife)`) before returning
    top-k `MemoryHit`s. Takes any `SupportsEmbed`.
  - **`ChromaVectorStore`** — a `VectorStore` backed by ChromaDB (ephemeral,
    persistent, or injected client) under the new `[genai-chroma]` extra.
  - **`OllamaGenerator` vision + tools** — `generate(images=[...])` and
    per-message `images` on `chat()` for multimodal models; `chat_with_tools()`
    returns the full Ollama message (content + `tool_calls`).
  - **`SpeechToText` parity** — `beam_size` / `vad_filter` (constructor
    defaults + per-call overrides) and `language_probability` on
    `Transcription`.
- New `[genai-chroma]` extra (`chromadb`). Install with
  `pip install tempest-fastapi-sdk[genai-chroma]`.

## [0.107.0] — 2026-07-06

### Added

- **Ollama backend for GenAI (`tempest_fastapi_sdk.genai.ollama`)** — run text
  generation and embeddings against a local (or remote) [Ollama](https://ollama.com)
  daemon over HTTP instead of loading HuggingFace weights with `torch`:
  - `OllamaGenerator` mirrors `TextGenerator`'s `generate` / `chat` / `stream`
    surface (talking to `/api/generate` and `/api/chat`), so it drops straight
    into `make_genai_router` with no other changes. No `torch`, no local
    weights, no `load()` step — Ollama owns model download and VRAM.
  - `OllamaEmbedder` implements the `SupportsEmbed` protocol (`/api/embed`),
    so it plugs into `Retriever` and the `/embed` endpoint in place of the
    `torch`-backed `Embedder` (e.g. `nomic-embed-text`).
  - `GenerationConfig` fields are mapped to Ollama `options`
    (`max_new_tokens` → `num_predict`, `repetition_penalty` → `repeat_penalty`,
    plus `seed`/`stop`; `do_sample=False` → greedy `temperature=0`).
  - New `[genai-ollama]` extra (just `httpx`). Install with
    `pip install tempest-fastapi-sdk[genai-ollama]`.
- **`TextBackend` protocol (`tempest_fastapi_sdk.genai.text`)** — the
  `runtime_checkable` text-generation surface (`generate` / `chat` / `stream`)
  that both `TextGenerator` and `OllamaGenerator` satisfy. Implement it to plug
  in any other engine (vLLM, TGI, a hosted API).

### Changed

- `make_genai_router` now type-hints `text_generator` as `TextBackend | None`
  and `embedder` as `SupportsEmbed | None` (was `TextGenerator | None` /
  `Embedder | None`). Backward compatible — the concrete classes still satisfy
  the widened protocols; the router only ever duck-typed them.

## [0.106.0] — 2026-07-06

### Added

- **Geolocation expansion (`tempest_fastapi_sdk.geo`)** — a big round of
  spatial features, all still no-paid-API:
  - **Offline geometry** (zero deps): `bounding_box` (the coarse SQL
    pre-filter for a radius), `within_radius` / `nearest` (in-memory
    proximity filter + k-nearest, generic via a `key=` extractor),
    `initial_bearing` + `destination_point` (projection along a bearing),
    `point_in_polygon` + `polygon_area_km2` (polygon geofences), and
    `path_length_km`.
  - **Database radius search** — `GeoPointMixin` (indexed `latitude` /
    `longitude` columns) + `GeoRepositoryMixin.nearby` (portable
    bounding-box pre-filter + Haversine refine, any DB) +
    `PostGISRepositoryMixin.nearby` (pushes the query into PostGIS
    `ST_DWithin`, no `geoalchemy2` dep); `make_geo_point_model` factory.
  - **Geocoding** — `GeocodingBackend` Protocol + `NominatimBackend`
    (address <-> coordinate via OpenStreetMap Nominatim, injected
    `httpx.AsyncClient`); `GeocodeResult` schema.
  - **Routing** — `OSRMBackend.matrix` (many-to-many distance/duration via
    the OSRM `table` service → `DistanceMatrix`), `OSRMBackend.route(...,
    with_geometry=True)` decoding the route polyline into
    `TravelEstimate.geometry`, per-mode OSRM profiles
    (`DEFAULT_MODE_PROFILES` + `mode_profiles=`).
  - **Polyline codec** — `encode_polyline` / `decode_polyline` (Google/OSRM
    algorithm, precision 5 or 6), pure Python.
  - **Travel modes** — added `TravelMode.BICYCLE` and
    `TravelMode.PEDESTRIAN` with duration factors.
  - **Brazil** — `uf_centroid` (offline approximate centre of each of the
    27 federative units) + `UF_CENTROIDS`; `cep_to_coordinate` (resolve a
    CEP via an injected geocoder).
  - New schemas `BoundingBox`, `GeocodeResult`, `DistanceMatrix`;
    `TravelEstimate.geometry` field.

## [0.105.0] — 2026-07-05

### Added

- **GenAI ergonomics** in `tempest_fastapi_sdk.genai`:
  - **`GenerationConfig`** — a typed Pydantic schema for generation
    parameters (`max_new_tokens` / `temperature` / `top_p` / `top_k` /
    `repetition_penalty` / `do_sample` / `seed` / `stop`). Pass it to
    `TextGenerator.generate` / `chat` / `stream` via `config=` instead of
    loose `**kwargs`; only the set fields layer over the defaults, and
    explicit `**kwargs` still win over the config.
  - **`make_genai_router`** — an opt-in FastAPI router that mounts only the
    endpoints backed by the GenAI objects you inject: `POST /generate`
    (+ `/generate/stream`, token-by-token SSE) and `/chat` for a
    `TextGenerator`, `/embed` for an `Embedder`, `/rag` for a `Retriever`,
    `/transcribe` for a `SpeechToText`, and `/tts` (returns `audio/wav`) for
    a `TextToSpeech`. Raises when handed nothing.
  - **`RedisEmbeddingCache`** — an async, Redis-backed `EmbeddingCache`
    shared across workers (JSON vectors, optional TTL). `Embedder` now
    accepts sync **or** async caches (it awaits `get`/`set` when they return
    an awaitable), so swapping `InMemoryEmbeddingCache` for
    `RedisEmbeddingCache` needs no call-site change. New `AsyncEmbeddingCache`
    Protocol documents the async shape.
- **Chat module (`tempest_fastapi_sdk.chat`)** — a reusable threaded-chat
  layer over the SDK primitives. Abstract tables `BaseConversationModel` /
  `BaseConversationParticipantModel` / `BaseMessageModel` (+ `make_*`
  factories), a `ChatService` (`start_conversation` / `post_message` /
  `list_messages` / `list_conversations` / `is_participant`), and an opt-in
  `make_chat_router`. When an `SSEBroker` is injected, every posted message
  is also published to the conversation's channel for real-time delivery,
  reusing the existing SSE fan-out.
- **Reviews module (`tempest_fastapi_sdk.reviews`)** — comments and
  0-to-5-star ratings on any polymorphic target (`target_type` +
  `target_id`). Abstract tables `BaseCommentModel` (threaded via
  `parent_id`) / `BaseRatingModel` (one vote per user, unique
  `(target_type, target_id, user_id)`) + `make_*` factories, a
  `ReviewService` (`add_comment` / `list_comments` / `rate` upsert /
  `get_user_rating` / `aggregate` → average + count + per-star
  distribution), and an opt-in `make_reviews_router`.
- **`RatingField`** in `tempest_fastapi_sdk.utils` — `Annotated[int, 0..5]`
  for a star score; re-exported at the package root.

## [0.104.0] — 2026-07-05

### Added

- **Geolocation (`tempest_fastapi_sdk.geo`)** — distance and travel-time
  estimates between two coordinates without any paid API. Two layers over
  shared schemas:
  - **Offline heuristic** (zero deps, zero network): `haversine_km` for the
    great-circle distance and `estimate_travel(origin, destination, mode)`
    for road distance (Haversine x circuity factor) and per-mode travel time
    (car average speed x mode factor). Returns a `TravelEstimate` with
    `source="heuristic"`.
  - **Real routing** (`OSRMBackend`, `[geo]` extra = `httpx`): talks to any
    OSRM server (public demo or self-hosted) for true road geometry via an
    injected `httpx.AsyncClient`; satisfies the `RoutingBackend` Protocol and
    returns a `TravelEstimate` with `source="osrm"`.
  - `TravelMode` enum (`CAR` / `MOTORCYCLE` / `BUS`); motorcycle and bus
    derive from the car by scaling the duration via
    `DEFAULT_MODE_DURATION_FACTORS`, so both layers work against a car-only
    profile. `Coordinate` (validated lat/long) + tunable
    `DEFAULT_CIRCUITY_FACTOR` / `DEFAULT_CAR_SPEED_KMH`. Submodule import like
    `vision`/`genai`; the heuristic imports without the extra.

## [0.103.0] — 2026-07-05

### Added

- **Audio language presets (PT-BR / EN-US)** in
  `tempest_fastapi_sdk.genai.audio`. A `Language` enum (`PT_BR` / `EN_US`)
  hides engine-specific identifiers:
  - `SpeechToText.transcribe(..., language=Language.PT_BR)` resolves the
    Whisper code (`"pt"` / `"en"`); still accepts a raw code or `None`
    (auto-detect).
  - `TextToSpeech.for_language(Language.PT_BR)` builds a voice with a
    sensible default Coqui model for the language; `synthesize(...,
    language=...)` accepts the enum too.
  - `LanguagePreset` + `preset_for(language)` expose the
    `whisper_language` / `tts_model` / `tts_language` mapping for
    inspection or override. Dependency-free (no `[genai-audio]` needed to
    import).

## [0.102.0] — 2026-07-05

### Added

- **Self-hosted audio (`tempest_fastapi_sdk.genai.audio`)** — voice in and
  out, on your own hardware (the leviathan pattern):
  - `SpeechToText` — transcription via **faster-whisper** (Whisper /
    CTranslate2). Lazy load, worker-thread inference, concurrency
    semaphore; auto device (CUDA/CPU) + compute type (float16/int8).
    `transcribe(audio, *, language=None, with_segments=True)` →
    `Transcription` (text, language, duration, timestamped `segments`).
    Accepts a path or `bytes`.
  - `TextToSpeech` — synthesis via **Coqui TTS**. Same lazy/threaded
    discipline. `synthesize(text, *, out_path=None, speaker=None,
    language=None, speaker_wav=None)` → WAV `bytes` (voice cloning via
    `speaker_wav` on XTTS models).
  - New `[genai-audio]` extra (`faster-whisper` + `coqui-tts`); everything
    imports lazily without it. Exported helpers `resolve_audio_device` /
    `resolve_compute_type` and the `Transcription` / `TranscriptionSegment`
    schemas.

## [0.101.0] — 2026-07-05

### Added

- **RAG over your own corpus — vector store + retriever**
  (`tempest_fastapi_sdk.genai.rag`), closing the RAG loop (index once,
  retrieve by similarity, don't re-embed per request):
  - `VectorStore` Protocol — `add(chunks, vectors)` + `search(vector,
    top_k)`.
  - `InMemoryVectorStore` — dict-backed cosine scan for dev/tests/small
    corpora.
  - `PgVectorStore` — Postgres + `pgvector`, reusing the service's existing
    database (table created on demand, cosine `<=>` search). Added
    `pgvector` to the `[genai-rag]` extra.
  - `Retriever` — ties an embedder + store: `index(chunks)`,
    `search(query, top_k)` (returns `Chunk`s with a `score`), and
    `retrieve(query)` → prompt-ready context. Works with any store via the
    `SupportsEmbed` / `VectorStore` protocols.
  - `Chunk` gained an optional `score` field (set by vector search).

## [0.100.0] — 2026-07-05

### Added

- **GenAI refinements** for RAG + semantic search:
  - `WebSearch.retrieve(query, *, extractor=None, ...)` — one-shot RAG:
    search → optional parallel body extraction → `build_context`, in a
    single call.
  - `ContentExtractor.extract_many(urls, *, concurrency=5)` — bounded
    concurrent page extraction, order preserved, failures absorbed.
  - `chunk_text(text, *, source, max_chars, overlap, ...)` — a generic,
    dependency-free chunker (any string, not just PDFs); `PdfReader.chunks`
    now uses it. Exported from `tempest_fastapi_sdk.genai.rag`.
  - `Embedder(normalize=True)` L2-normalizes returned vectors, and
    `cosine_similarity(a, b)` ranks them — semantic-search essentials
    (exported from `tempest_fastapi_sdk.genai`).

## [0.99.0] — 2026-07-05

### Added

- **Self-hosted GenAI — embeddings + scale**, slice 4 (completes the
  planned module scope):
  - `Embedder` — local text → vectors over transformers (mean pooling),
    batched, with an optional per-text vector cache (`EmbeddingCache`
    Protocol + bundled `InMemoryEmbeddingCache`; a cache hit skips loading
    the model). Same device/precision resolution + `unload` /
    `unload_if_idle` as `TextGenerator`.
  - `BatchScheduler` — coalesce concurrent inference calls into one
    batched handler call (`max_batch` / `max_wait_ms`); each caller still
    awaits its own result. Pure asyncio, model-agnostic, imports without
    the `[genai]` extra.
  - `ModelRegistry` — share loaded models by id with LRU eviction
    (`unload()` on evict), so call sites don't load the same model twice.
  - All exported from `tempest_fastapi_sdk.genai`.

## [0.98.0] — 2026-07-05

### Added

- **Self-hosted GenAI — local LLM text generation
  (`tempest_fastapi_sdk.genai.TextGenerator`)**, slice 3. Loads a
  HuggingFace causal LM once and generates on your own hardware:
  - `generate(prompt, ...)`, `chat(messages, ...)` (tokenizer chat
    template) and `stream(prompt, ...)` (token-by-token) — all async,
    running the blocking model in `asyncio.to_thread`.
  - Automatic device (`auto` → CUDA → MPS → CPU) and precision (`auto` →
    bf16 on GPU, fp32 on CPU) resolution; int8/int4 `quantization` via
    BitsAndBytesConfig (`[genai-quant]`).
  - Lazy `load()` on first use; `unload()` frees VRAM; `unload_if_idle()`
    + `idle_unload_seconds` reclaim memory between bursts (call it from a
    `@tq.interval` task — no background thread).
  - Exported helpers `resolve_device` / `auto_dtype_name`. `torch` /
    `transformers` are imported lazily, so the module and its resolution
    helpers import without the `[genai]` extra.

## [0.97.0] — 2026-07-05

### Added

- **Self-hosted GenAI — RAG context (`tempest_fastapi_sdk.genai.rag`)**,
  slice 2: feed a local LLM with web + PDF knowledge, without shipping
  data to a third party.
  - **Web search** — `WebSearchBackend` Protocol + `SearxngBackend` (the
    leviathan pattern: SearXNG JSON API over an injected `httpx` client)
    + `WebSearch` facade. Returns `SearchResult`s.
  - **Content extraction** — `ContentExtractor` fetches a URL and pulls
    the clean article body via `trafilatura`; failures surface as
    `ExtractionResult(failed=True)`, never raised.
  - **PDF reading** — `PdfReader` (PyMuPDF, detailed reading-order
    extraction) → `Document` (text + per-page + metadata) and overlapping
    `Chunk`s (`read` / `chunks`).
  - **Context assembly** — `build_context(question, sources)` renders
    `SearchResult`s and/or `Chunk`s into one prompt-ready, source-labeled
    block (mix web + PDF, optional per-source truncation).
  - New `[genai-rag]` extra (httpx + trafilatura + pymupdf); everything
    imports lazily without it. Import from `tempest_fastapi_sdk.genai.rag`.

## [0.96.0] — 2026-07-05

### Added

- **Self-hosted GenAI — capacity check (`tempest_fastapi_sdk.genai`)**,
  the first slice of running HuggingFace models on your own hardware.
  Before downloading gigabytes of weights, check whether the host can run
  a model:
  - `probe_hardware()` → `HardwareInfo` (CPU, total/available RAM, CUDA
    GPUs with per-device VRAM, Apple MPS, free disk). Degrades gracefully
    without `psutil` / `torch`.
  - `can_run(...)` → `CapacityReport` (`fits`, chosen `device`, estimated
    vs available bytes, `headroom_pct`, and a concrete `suggestion` when
    it doesn't fit — quantize, offload, or pick a smaller model).
  - `recommend(...)` picks the first precision (`bfloat16` → `int8` →
    `int4`) that fits.
  - `estimate_model_bytes` / `bytes_per_param` (the estimation math) and
    `fetch_num_params` (reads a model's parameter count from the Hub via
    `huggingface_hub`, without downloading weights). `ModelDtype` enum.
  - New `[genai]` extra (transformers + torch + accelerate + safetensors +
    huggingface-hub) and `[genai-quant]` (bitsandbytes). The capacity
    functions import **without** the extra — `torch` is only used to probe
    real GPUs. Import from `tempest_fastapi_sdk.genai`.
  - Upcoming slices: `TextGenerator` (+ quantization), `Embedder`,
    model/result caching, `BatchScheduler`, and RAG context (web search +
    PDF reading).

## [0.95.0] — 2026-07-05

### Added

- **PIX key field + helpers** (`tempest_fastapi_sdk.utils`). `PixKeyField`
  is an `Annotated` schema type that validates any of the five BACEN PIX
  key types (CPF, CNPJ, e-mail, E.164 phone, random UUID) in one field
  and normalizes to a canonical form (CPF/CNPJ → digits, e-mail →
  lowercase, phone → `+55…`, random → lowercase UUID). Companions:
  `PixKeyType` (the enum), `detect_pix_key_type(value)` (returns the type
  or `None`), `is_valid_pix_key(value)` and `normalize_pix_key(value)`.
  Detection is by shape plus CPF/CNPJ check digits — all exported from the
  package root.

### Changed

- The **validated-fields recipe** was expanded (both locales): a full
  schema + route + 422 walkthrough, a "compose your own field" section,
  common gotchas (`CentsField` vs `PriceField`, `PercentField` vs
  `RatioField`), and the new PIX-key section.

## [0.94.0] — 2026-07-05

### Added

- **Human-friendly cron helpers** (`tempest_fastapi_sdk.tasks`) — schedule
  periodic tasks without writing cron syntax:
  - `Cron` — ready-made expressions (`Cron.EVERY_WEEKDAY_9AM`,
    `Cron.EVERY_5_MINUTES`, …).
  - `CronOffset` — timezone offsets by place (`CronOffset.BRASILIA` =
    `-03:00`, plus `FERNANDO_DE_NORONHA` / `MANAUS` / `ACRE` / `UTC`).
  - `Weekday` — day-of-week tokens.
  - Builder functions — `daily`, `weekdays`, `weekends`, `hourly`,
    `every_minute`, `every_n_minutes`, `weekly`, `monthly` — each
    returning a plain cron string with range validation.
  - `@tq.cron(...)` / `AsyncTaskScheduler.cron(...)` accept these
    directly, coercing enum members to their plain string value. The
    module has no third-party dependency (imports without `[tasks]`).
- **Class-based message consumers** (`tempest_fastapi_sdk.queue`) — an
  alternative to the `@mq.on` decorator, in two explicit styles:
  `Consumer` with a constructor `channel=` + Pydantic `schema=` (no
  annotation-sniffing) and overridden `handle`, or grouped `@subscribe`
  methods (one class, many channels). Wire with `MessageBroker.register`.
- **Class-based background tasks** (`tempest_fastapi_sdk.tasks`) —
  symmetric to consumers: `TaskDef` with an overridden `run` (name in the
  constructor) or grouped `@task_method` methods, wired with
  `TaskQueue.register` (returns a `Task` or a `dict[str, Task]`).

### Changed

- **`AsyncBrokerManager` renamed to `AsyncQueueManager`** — a clearer name
  for the thin lifecycle wrapper around an injected broker, matching the
  `Async*Manager` family. `AsyncBrokerManager` stays as a backward-compatible
  alias. `MessageBroker` remains the recommended batteries-included facade.

## [0.93.0] — 2026-07-05

### Added

- **Typed facades over FastStream and TaskIQ** — application code no
  longer imports `faststream` or `taskiq`.
  - **`MessageBroker`** (`tempest_fastapi_sdk.queue`) — transport-agnostic
    pub/sub over FastStream behind a single **channel** concept. Pick the
    transport with a constructor (`MessageBroker.rabbitmq(url)` / `.redis`
    / `.kafka` / `.nats`), declare consumers with `@mq.on("channel")`
    (the handler's Pydantic type hint validates the message), and publish
    channel-first with `await mq.publish("channel", model)`. `.broker`
    stays as the escape hatch.
  - **`TaskQueue`** (`tempest_fastapi_sdk.tasks`) — TaskIQ broker +
    scheduler folded into one object. `TaskQueue.rabbitmq(url)` / `.redis`
    / `.memory()`; `@tq.task` returns a typed **`Task`** with
    `await task.enqueue(...)` (to a worker) and `await task.run(...)`
    (inline, no broker); periodic tasks via `@tq.cron(...)` /
    `@tq.interval(...)`; `start_scheduler()` / `stop_scheduler()` for
    dev, with `tq.broker` / `tq.scheduler` exposed for the standalone
    `taskiq worker` / `taskiq scheduler` CLIs.
  - Both facades keep the SDK-standard lifecycle (`connect` / `disconnect`
    / `lifespan` / `health_check` / `is_connected`).
  - The `OutboxRelay` `publish` callable plugs straight into
    `MessageBroker.publish` (channel-first).

### Changed

- The **Queue & Tasks** recipe was rewritten in the tiangolo didactic
  style around the new facades, and its stale claim that the SDK ships no
  outbox primitive was corrected (it ships `BaseOutboxModel` /
  `OutboxRelay` / `save_with_outbox`).

### Deprecated

- `AsyncBrokerManager`, `AsyncTaskBrokerManager` and `AsyncTaskScheduler`
  remain fully functional but are superseded by `MessageBroker` /
  `TaskQueue`; new code should prefer the facades.

## [0.92.0] — 2026-07-05

### Added

- **Email change / re-verification / recovery flow** on `UserAuthService`
  + `make_auth_router`, mirroring the password reset/change surface:
  - **Change email (authenticated)** — `request_email_change` (verifies
    the current password, stages the new address, emails a confirmation
    link to the NEW address) + `confirm_email_change` (consumes the
    token, flips the email, and — when `AUTH_EMAIL_CHANGE_NOTIFY_OLD`
    is on — sends a security notice to the OLD address). Routes:
    `POST /auth/email-change/request` (202) and
    `POST /auth/email-change/confirm`.
  - **Re-verify current email** — `request_email_verification` /
    `confirm_email_verification` (resend a verification link to the
    current address; confirming marks the account active). Routes:
    `POST /auth/email-verify/request` (202) and
    `POST /auth/email-verify/confirm`.
  - **Recovery (lost mailbox access)** — `request_email_recovery`, an
    **unauthenticated** entry point that proves identity with the
    account password **plus a valid MFA code when TOTP is enrolled**,
    then emails the confirmation link to the new address. Always returns
    a generic `202` for soft failures (unknown email, wrong password,
    bad/missing MFA code) so it can't enumerate accounts. Route
    `POST /auth/email-recovery/request` is **opt-in** via
    `AUTH_EMAIL_RECOVERY_ENABLED` (off by default).
  - **Old-email security notice** on a confirmed change, toggled by
    `AUTH_EMAIL_CHANGE_NOTIFY_OLD` (default `True`).
  - **Backend HTML pages** (when `AUTH_BACKEND_LINKS=True`):
    `GET /auth/email-change/{token}` and `GET /auth/email-verify/{token}`
    render self-contained success/error pages — no frontend needed.
  - New schemas exported from the package root:
    `EmailChangeRequestSchema`, `EmailChangeConfirmSchema`,
    `EmailRecoveryRequestSchema`, `EmailChangeResponseSchema`,
    `EmailChangeToken`, `EmailVerificationToken`.
  - New `UserTokenPurpose.EMAIL_CHANGE`; the existing
    `EMAIL_VERIFICATION` purpose now backs the re-verify flow.
  - 14 bundled bilingual templates (PT-BR + EN-US): `email_change.html`,
    `email_verification.html`, `email_changed_notice.html`, plus
    `email_change_success/error.html` and
    `email_verification_success/error.html`.
  - Localized subjects/bodies for the three new emails.
  - New settings: `AUTH_EMAIL_CHANGE_TTL_SECONDS`,
    `AUTH_EMAIL_VERIFICATION_TTL_SECONDS`,
    `AUTH_EMAIL_CHANGE_URL_TEMPLATE`,
    `AUTH_EMAIL_VERIFICATION_URL_TEMPLATE`, `AUTH_EMAIL_CHANGE_TEMPLATE`,
    `AUTH_EMAIL_VERIFICATION_TEMPLATE`,
    `AUTH_EMAIL_CHANGED_NOTICE_TEMPLATE`, `AUTH_EMAIL_CHANGE_NOTIFY_OLD`,
    `AUTH_EMAIL_RECOVERY_ENABLED`, `AUTH_EMAIL_CHANGE_SUCCESS_TEMPLATE`,
    `AUTH_EMAIL_CHANGE_ERROR_TEMPLATE`,
    `AUTH_EMAIL_VERIFICATION_SUCCESS_TEMPLATE`,
    `AUTH_EMAIL_VERIFICATION_ERROR_TEMPLATE`.

### Changed

- **`BaseUserTokenModel` gains a nullable `payload` column**
  (`VARCHAR(320)`) carrying flow context — the pending new email for an
  `EMAIL_CHANGE` token. **Requires a migration** in consuming projects
  (additive nullable column, safe). See the migration guide.

## [0.91.0] — 2026-07-05

### Added

- **SSE backpressure — bounded queue + overflow policy.**
  `EventStream` (and `SSEBroker`-created streams) now cap the buffered
  events at `max_queue` (default `1000`) instead of growing without
  limit when a client stalls. When the buffer fills, `overflow` decides
  what gives:
  - `"drop_oldest"` (default) — evict the stalest event, keep the
    freshest data.
  - `"drop_newest"` — discard the incoming event, keep the backlog.
  - `"block"` — apply real backpressure (the producer waits for a slot).
  `EventStream.dropped_events` counts events lost to overflow for
  metrics / logs. `max_queue=0` restores the pre-0.91 unbounded
  behavior. The `close()` sentinel is never dropped or blocked, so a
  stream can always terminate. `SSEBroker(max_queue=..., overflow=...)`
  applies the same policy to every stream it opens.
- **SSE lifecycle helpers — no more hand-rolled `try/finally`.**
  - `sse_response(..., on_disconnect=...)` runs a cleanup callback
    (awaited if a coroutine) when the client disconnects or the stream
    ends — the one place guaranteed to fire — so a bound producer task
    is cancelled or a channel unregistered without boilerplate.
  - `EventStream.response(*, on_disconnect=..., status_code=...,
    headers=...)` wraps `stream()` in an SSE response in one call.
  - `SSEBroker.response(channel, ...)` bundles the whole per-connection
    lifecycle: `register` + `sse_response` + `unregister`-on-disconnect.
    This removes the leak-prone manual wrapper the recipe used to need.
- **JWT via query string for cookieless clients.**
  `make_bearer_token_dependency` and `make_jwt_user_dependency` gained a
  `query_param` argument (e.g. `query_param="access_token"`). Token
  lookup order becomes header → cookie → query string. This unblocks
  browser `EventSource` (SSE), whose constructor accepts neither headers
  nor a body. Documented with a security warning: use short-lived access
  tokens only, over TLS, and scrub the value from access logs. Prefer a
  session cookie (`withCredentials`) whenever the client shares the
  API's origin.
- **`OverflowPolicy`** is exported from `tempest_fastapi_sdk.sse` and the
  package root.

### Fixed

- `tempest_fastapi_sdk/sse/__init__.py` re-exports now use the explicit
  `X as X` alias form required by the repo's re-export convention (was a
  structural defect flagged by strict type-checkers).

## [0.90.0] — 2026-07-04

### Added

- **Unified file store (`FileStoreUtils`).** A single facade over the
  three pieces a service usually wires by hand — `UploadUtils`
  (validate + persist), `DownloadUtils` (serve bytes through the API)
  and the presigned-URL helpers of `AsyncMinIOClient` — behind one
  object with one configuration, targeting one storage backend.
  - The backend is picked once from `source`: a directory path for
    local disk (`[upload]` extra), or an `AsyncMinIOClient` for
    MinIO/S3 (`[minio]` extra).
  - Convenience surface: `save` / `replace` / `delete` / `exists`,
    `download` / `file_response` / `stream` / `resolve`, `validate`,
    and `presigned_get_url` / `presigned_put_url` (the latter return
    `None` on the local backend, keeping the call site uniform).
  - Escape hatches for the internal pieces: `uploader`, `downloader`,
    `backend` and `client`.
  - A single `UploadStorage` backend is built and shared with the
    upload half; on MinIO the same client instance is reused by the
    download half, so the connection pool is shared, not duplicated.
- **`UploadUtils` accepts an injected backend.** New keyword-only
  `backend: UploadStorage | None` bypasses the `source`-based backend
  selection, letting `FileStoreUtils` build one backend and share it.
  `source` is now optional when `backend` is given; passing neither
  raises `ValueError`. The `[upload]` extra is only required when the
  local backend is actually selected (MinIO-only use no longer needs
  `aiofiles`).

### Docs

- New bilingual recipe **File store (unificado) / Unified file store**
  (`docs/recipes/file-store.md` + `.en.md`) and an API-reference stub
  for `FileStoreUtils` (plus `DownloadUtils`).

## [0.89.0] — 2026-07-04

### Added

- **Typed server-side rendering (`tempest_fastapi_sdk.ssr`, extra
  `[ssr]`).** A first-class SSR surface: FastAPI routes return typed
  Python components rendered to HTML — full-stack, typed, no template
  language.
  - `Page` — a typed component base (`tempest_core` `Component`). Declare
    typed fields, implement `body() -> Widget`, and optionally override
    `shell(body)` to wrap every page in a shared header/nav/footer layout
    inherited through normal Python inheritance. `render()` composes
    `shell(body())` for you.
  - `html_response(widget, *, title=None, status_code=200, htmx=False,
    document=True, lang="pt-BR")` — renders a widget tree to a FastAPI
    `HTMLResponse`. `document=True` emits a full HTML5 document (requires
    `title`); `document=False` emits a bare fragment for HTMX partial
    swaps. `htmx=True` injects a locally-served HTMX `<script>` tag
    (never a CDN).
  - `make_htmx_router(prefix="/_ssr")` — serves a **bundled** HTMX 2.x
    (shipped inside the wheel) at `GET {prefix}/htmx.js` with an
    `application/javascript` media type — CSP- and offline-friendly, no
    external host contacted.
  - The renderer (`tempestweb`) is imported lazily, so
    `import tempest_fastapi_sdk` never hard-requires the extra; install
    with `pip install "tempest-fastapi-sdk[ssr]"`.

## [0.88.0] — 2026-07-03

### Added

- **Split-endpoint presigned URLs for MinIO/S3.** `AsyncMinIOClient`
  gained `public_endpoint` / `public_secure` args, and `MinIOSettings`
  the matching `MINIO_PUBLIC_ENDPOINT` / `MINIO_PUBLIC_SECURE`. When set,
  `presigned_get_url` / `presigned_put_url` are signed against the public
  host (so the browser can reach them) while every server-side operation
  keeps using the internal `MINIO_ENDPOINT` (fast private network). A
  second `minio.Minio` client signs the URLs — the host is part of the
  SigV4 signature, so it must be signed against the public endpoint
  rather than rewritten afterwards. Fully opt-in: without
  `MINIO_PUBLIC_ENDPOINT`, presigned URLs are signed with `MINIO_ENDPOINT`
  as before. A `https://` scheme (or trailing path) on the public
  endpoint is tolerated and stripped; `https://` implies HTTPS.

## [0.87.1] — 2026-07-02

### Fixed

- **`UserAuthService.current_user_dependency()` now honours cookie
  delivery.** When `AUTH_TOKEN_DELIVERY` is `"cookie"` or `"both"` it
  auto-derives the access-token cookie name from
  `AUTH_ACCESS_COOKIE_NAME`, so any business route guarded by the
  dependency authenticates off the cookie the bundled login set — the
  `Authorization` header still wins when present. Previously the
  dependency was bearer-only, so cookie-mode clients hit
  `401 Authorization token is missing or invalid` on protected routes
  even with the cookies in the browser. A new `cookie_name=` argument
  lets callers force a specific cookie (or `None` for header-only).

## [0.87.0] — 2026-07-02

### Added

- **Configurable token delivery for the auth router.** `make_auth_router`
  now supports three ways of handing back the JWT pair, selected by
  `AUTH_TOKEN_DELIVERY` (or the `token_delivery=` argument):
  - `"bearer"` (default) — tokens in the JSON body only. Unchanged,
    fully backward-compatible behaviour.
  - `"cookie"` — `access_token` / `refresh_token` set as `HttpOnly`
    cookies on `/auth/login`, `/auth/refresh`, `/auth/logout`; the body
    omits the token values (safer against XSS). `POST /auth/refresh`
    reads the refresh token from the cookie and rotates the pair;
    `POST /auth/logout` clears the cookies (and revokes the refresh
    family when a `refresh_token_model` is wired).
  - `"both"` — the bearer endpoints stay at `/auth/*` and a parallel
    cookie set is mounted at `/auth/cookie/*`, so one backend can serve
    web (cookie) and mobile/API (bearer) clients.
- New `AUTH_COOKIE_SECURE`, `AUTH_COOKIE_SAMESITE`, `AUTH_COOKIE_DOMAIN`,
  `AUTH_ACCESS_COOKIE_NAME` and `AUTH_REFRESH_COOKIE_NAME` settings tune
  the cookie security attributes.
- New public exports: `TokenDelivery`, `AuthCookieConfig`,
  `apply_auth_cookies`, `clear_auth_cookies`.
- `make_bearer_token_dependency` / `make_jwt_user_dependency` gained a
  `cookie_name=` argument: when set, the access token is read from that
  cookie if the `Authorization` header is absent (header still wins), so
  the same guarded routes work in cookie mode.

### Notes

- Activation, signup auto-login and the MFA-verify step still return the
  JWT pair in the body regardless of `AUTH_TOKEN_DELIVERY` — cookie
  delivery covers the login / refresh / logout session lifecycle.

## [0.86.0] — 2026-06-28

### Added

- **Admin rich list filters.** `list_filter` fields now auto-pick a
  widget by column type instead of rendering a useful dropdown only for
  booleans: **enum** columns become a member dropdown, **foreign keys**
  (whose target has a registered `AdminModel`) become a related-row
  dropdown, **date/datetime** columns become an inclusive date-range
  (two date inputs → `<field>__gte` / `<field>__lte`), and any other
  column becomes a text input (equality). Booleans keep the Yes/No
  dropdown. All filters preserve search / sort / pagination in the URL.

## [0.85.0] — 2026-06-28

### Added

- **Admin file / image upload fields.** `AdminModel` gained
  `upload_fields=[...]` + `upload_storage=...`: listed String columns
  render as file inputs in the create/edit form (which auto-switches to
  `multipart/form-data`), the posted file is streamed to the storage
  backend (`LocalUploadStorage` / `MinIOUploadStorage`), and the returned
  storage key is written to the column. On edit, omitting the file keeps
  the current value; on create, a missing file for a non-nullable column
  is a required-field error. Registering `upload_fields` without
  `upload_storage` raises `ValueError`.

## [0.84.0] — 2026-06-28

### Added

- **Admin custom actions (`@admin_action`).** Beyond the three hardcoded
  bulk operations (activate / deactivate / delete), the admin now takes
  user-defined actions: decorate an async function with `@admin_action`
  and register it via `AdminModel(actions=[...])`. Each renders in the
  list view's bulk dropdown (namespaced `custom:<name>` so it can't
  collide with the built-ins), runs on the checked rows, and flashes a
  banner from its `AdminActionResult`. The handler receives an
  `AdminActionContext` (selected ids, a request-scoped repository, the DB
  session, the request, the admin session, and the acting principal) and
  stays directly callable/testable — the decorator only attaches
  metadata. Exported from `tempest_fastapi_sdk` and
  `tempest_fastapi_sdk.admin` (`admin_action`, `AdminAction`,
  `AdminActionContext`, `AdminActionResult`).

## [0.83.0] — 2026-06-28

### Added

- **Computer-vision integration via the `[vision]` extra
  (`ort-vision-sdk`).** New `tempest_fastapi_sdk.vision` submodule wraps
  the ONNX Runtime inference library with the FastAPI layer it lacks:
  Pydantic response schemas (`DetectionSchema`, `ClassificationSchema`,
  `SegmentationSchema`, `BoundingBoxSchema`, `ClassProbabilitySchema`)
  and mappers (`to_detection_schemas`, `to_classification_schema`,
  `to_segmentation_schemas`) that convert a model result into them. The
  `Detector` / `Classifier` / `Segmenter` task classes are re-exported
  **lazily** — accessing one without the extra raises a clear
  `ImportError` pointing at `[vision]`; the schemas and mappers carry no
  such dependency. Like `cache` / `queue` / `tasks`, vision is
  submodule-only (`from tempest_fastapi_sdk.vision import Detector`).

## [0.82.1] — 2026-06-28

### Fixed

- **Docs: removed code that the API never offered.** A doc audit
  (cross-checking every `tempest_fastapi_sdk` import and example against
  the package) found and fixed drift in the README:
  - The brute-force throttling recipe invented `MemoryThrottleBackend` /
    `RedisThrottleBackend`, `throttle.check()` returning a `ThrottleStatus`
    enum, `record_failure()`, and `lock_seconds=` — none exist. Rewritten
    to the real API (`AttemptThrottle(backend, max_attempts=, window_seconds=)`
    with `raise_if_blocked` / `hit` / `reset` / `status`).
  - The client-IP recipe used `trusted_proxies=` / `accept_private=` — the
    real parameter is `trusted_header=`.
  - The cookie recipe treated `SameSite` as a `BaseStrEnum` (`SameSite.LAX`)
    with `same_site=` / `key=` kwargs and an auto-`Secure` claim; `SameSite`
    is a `Literal` alias and `set_cookie` takes positional `name`/`value`
    + `samesite=`.
  - `AsyncRedisManager` was imported from the top level in several recipes
    (cache / sessions / security) — it lives in `tempest_fastapi_sdk.cache`
    (submodule-only, like `queue` / `tasks`). No code changes.

## [0.82.0] — 2026-06-28

### Added

- **`SSEBroker` — multi-worker SSE fan-out.** The SSE recipe described a
  Redis Pub/Sub bridge for broadcasting across workers but shipped no
  primitive; `SSEBroker` is that primitive. It keeps a per-channel
  registry of local `EventStream`s and fans `publish(channel, ...)` out
  to them. Pass a `[cache]` Redis client and the same broker publishes
  via Redis `PUBLISH` while a background `run()` task `PSUBSCRIBE`-s the
  channel prefix and relays every message to each worker's local
  streams — so `publish` becomes cross-process with no call-site change
  (`register` / `unregister` / `publish` are identical in both modes).
  Exported from `tempest_fastapi_sdk` and `tempest_fastapi_sdk.sse`. The
  SSE recipe now shows the in-memory and Redis-lifespan setups.

## [0.81.2] — 2026-06-28

### Changed

- **Docs: API reference stubs for symbols the top-surface filter skips.**
  The auto-generated reference's top block excludes lowercase names, so
  the session's new free functions were missing — added explicit
  `mkdocstrings` entries for `strict_types` / `typed` /
  `require_annotations` (new "Core" section) and `uf_choices` /
  `region_choices` / `city_choices` (Utils). No code changes.

## [0.81.1] — 2026-06-28

### Changed

- **Docs: dedicated Server-Sent Events (SSE) recipe.** Promoted SSE from
  a section of the real-time recipe to its own bilingual page covering a
  single endpoint, the connection-lifecycle pattern, event anatomy
  (`event`/`id`/`retry`/heartbeat comments), a broadcast-to-many "hub"
  (with the multi-worker Pub/Sub caveat), and alignment with
  `tempest-react-sdk`'s `createEventStream` / `useEventStream`. The
  real-time recipe now links to it. No code changes.

## [0.81.0] — 2026-06-28

### Added

- **`make_web_push_router` — opt-in subscribe/unsubscribe router.**
  Mounts ``POST {prefix}/subscribe`` and ``POST {prefix}/unsubscribe``
  (default prefix ``/api/push``) wired straight to a
  `WebPushSubscriptionService`, mirroring `make_auth_router`. Both accept
  the raw ``PushSubscription.toJSON()`` body, so `tempest-react-sdk`'s
  `WebPushClient` `onSubscribe` / `onUnsubscribe` callbacks hit them
  directly. The caller injects `service_factory`, `session_factory` and a
  `current_user_id` dependency; the request ``User-Agent`` is stored as
  the device label by default. Exported from `tempest_fastapi_sdk` and
  `tempest_fastapi_sdk.webpush`.

## [0.80.0] — 2026-06-28

### Added

- **Web Push subscription storage + service.** The webpush module gained
  the missing persistence layer so apps no longer hand-roll it:
  `BaseWebPushSubscriptionModel` (abstract table, one row per user device,
  unique `endpoint`) + `make_web_push_subscription_model(user_table=...)`
  factory, mirroring the `BaseUserTokenModel` pattern; and
  `WebPushSubscriptionService` (generic over the concrete model) with
  `subscribe` (idempotent upsert keyed by endpoint), `unsubscribe`,
  `list_for_user`, `prune`, and `notify_user` — which fans a payload out
  to every device and **auto-prunes the ones the push service reports as
  gone (404/410)**. The wire shape matches `PushSubscription.toJSON()`,
  so it lines up 1:1 with `tempest-react-sdk`'s `WebPushClient`
  `onSubscribe` / `onUnsubscribe` callbacks. Exported from
  `tempest_fastapi_sdk`, `tempest_fastapi_sdk.webpush` and
  `tempest_fastapi_sdk.db`.

## [0.79.0] — 2026-06-27

### Changed

- **`BaseService` / `BaseController` `update` payload is now generically
  typed.** Both classes gained an optional third type parameter
  `UpdateT` (bound to `BaseSchema`, default `BaseSchema`) so `update`
  accepts the project's own update schema instead of the bare
  `BaseSchema`: `BaseService[Repo, Resp, MyUpdateSchema]` /
  `BaseController[Service, Resp, MyUpdateSchema]`. The default keeps every
  existing two-argument subclass working unchanged. Implemented with a
  PEP 695/696 `TypeVar` default via `typing_extensions` (already present
  through Pydantic).

## [0.78.0] — 2026-06-27

### Added

- **`BaseService.update` (and `BaseController.update`).** The service
  skeleton now ships a generic update method: fetch by primary key, copy
  the fields present in the payload (`data.to_dict()`, which drops unset
  and ``None`` values) onto the instance, persist via
  ``repository.update`` and return the mapped response. Because unset
  fields are skipped, the same method serves full (PUT) and partial
  (PATCH) updates. ``BaseController.update`` forwards to it, matching the
  existing pass-through layer. Override either when an update needs
  orchestration.

## [0.77.0] — 2026-06-27

### Added

- **Generic validated field types (`tempest_fastapi_sdk.utils.fields`).**
  A base set of `Annotated` Pydantic types that bake a validation rule
  into the type, following the `*Field` convention (so the schema reads as
  what it is instead of repeating `Field(gt=0, ...)`): integers
  `PositiveIntField`, `NonNegativeIntField`, `CentsField` (money in minor
  units), `PortField`; floats `PositiveFloatField`, `NonNegativeFloatField`,
  `PercentField` (0..100), `RatioField` (0..1), `LatitudeField`,
  `LongitudeField`; `PriceField` (non-negative `Decimal`, 2 places); and
  strings `NonEmptyStrField` (trim + non-empty), `SlugField`,
  `HexColorField`. Exported from `tempest_fastapi_sdk` and
  `tempest_fastapi_sdk.utils`.

## [0.76.0] — 2026-06-27

### Changed

- **BR field types now carry a `Field` suffix.** The Pydantic
  annotated-type aliases were renamed to make their role obvious at the
  import site, matching `UFField` / `CityNameField`: `CPF` -> `CPFField`,
  `CNPJ` -> `CNPJField`, `CPFOrCNPJ` -> `CPFOrCNPJField`,
  `PhoneBR` -> `PhoneBRField`, `CEP` -> `CEPField`. The old names remain
  exported as **deprecated aliases** (identical types), so existing
  imports keep working; prefer the `*Field` names. Slated for removal in
  a future major.

## [0.75.0] — 2026-06-27

### Added

- **Frontend `<select>` choices for BR localities (`ChoiceBR`,
  `uf_choices`, `region_choices`, `city_choices`).** The bundled
  states/cities dataset already backed the `UFField` / `CityNameField`
  validation fields; these helpers make the *other* role first-class —
  feeding dropdowns. Each returns `list[ChoiceBR]`, a typed Pydantic
  schema (`value`/`label`) that serializes as
  `{"value": ..., "label": ...}` and shows up typed in OpenAPI:
  `uf_choices()` pairs each acronym (the same value `UFField` validates)
  with the full state name, `region_choices()` lists the 5 IBGE
  macro-regions, and `city_choices(uf)` lists a state's municipalities.
  Exported from `tempest_fastapi_sdk` and `tempest_fastapi_sdk.utils`.

## [0.74.0] — 2026-06-27

### Added

- **Runtime type-enforcement decorators (`strict_types`, `typed`,
  `require_annotations`).** Type hints are erased at runtime; these close
  the gap. `strict_types` validates arguments and return against the
  annotations with no coercion (a `str` where `int` is annotated raises);
  `typed` does the same but coerces when Pydantic safely can
  (`"1"` -> `1`); both are built on `pydantic.validate_call` (already a
  dependency). `require_annotations` enforces at decoration time that a
  function *is* annotated, raising `TypeError` listing any unannotated
  parameter / return — `self`/`cls` and `*args`/`**kwargs` are exempt and
  `Any` counts as a valid annotation. Exported from `tempest_fastapi_sdk`
  and `tempest_fastapi_sdk.core`.
- **`[tool.tempest] typing_strictness` knob for the CLI gates.** A new
  config field (`lenient` / `standard` / `strict`, default `standard`)
  controls how strictly `tempest lint` / `fix` / `type` / `check` enforce
  typing: it layers ruff ANN rules and mypy flags on top of the project's
  own config without relaxing it. Override per run with
  `--strictness/-s`. `ANN401` (which flags `Any`) is never enabled at any
  level — the point is that things ARE annotated, not that they avoid
  `Any`. Read via `tempest_fastapi_sdk.cli.config` (`TempestConfig`,
  `load_tempest_config`).

### Changed

- **ruff `ANN` is now enabled in the SDK and in `tempest new` templates.**
  Generated projects ship `select = [..., "ANN"]` with
  `ignore = [..., "ANN401", "ANN002", "ANN003"]` plus the
  `[tool.tempest] typing_strictness = "standard"` knob, so a fresh
  service requires annotations out of the box while leaving `Any`
  allowed. Generated templates were also fixed to pass their own
  `tempest check` cleanly (import ordering, `known-first-party = ["src"]`,
  sorted `__all__`).

## [0.73.0] — 2026-06-27

### Added

- **`BaseStrEnum` / `BaseIntEnum` gained `choices`, `from_value`,
  `has_value`, and `has_key`.** Alongside the existing `values` /
  `keys` / `to_dict`, the shared `_EnumHelpers` mixin now exposes:
  `choices()` returning `(value, name)` pairs for HTML `<select>` /
  form widgets; `from_value(value, *, default=...)` a lenient
  constructor that resolves a member from a raw value or member name
  (exact, then case-insensitive), raising `ValueError` on no match or
  returning an explicit `default` when supplied; and the `has_value` /
  `has_key` membership predicates. Both bases inherit them, so every
  service enum gets the helpers for free.

### Changed

- **Email recipe documents production SMTP and credential handling.**
  The `email` recipe (PT-BR + EN) gained a "Production" section: read
  every `SMTP_*` field from the environment (never hardcode or commit
  the password), use a Gmail App Password with 2FA, a verified
  `SMTP_FROM_ADDR` domain (SPF/DKIM/DMARC), and a provider/port/TLS
  table (Gmail 587 STARTTLS vs 465 implicit TLS, AWS SES, SendGrid,
  MailHog). Mirrors the two real production setups in use.

## [0.72.0] — 2026-06-26

### Added

- **`AdminTheme` — typed theming for the admin panel.** A new
  `AdminTheme` dataclass carries appearance overrides (accent /
  accent_hover / danger colors, header & sidebar backgrounds, page
  background, border radius, font family, logo image + alt, favicon,
  footer text, dark mode, and a `custom_css_url` escape hatch) through
  typed, documented parameters. Pass it via `AdminSite(theme=...)`; the
  SDK injects a `<style>` block of `:root` overrides after `admin.css`
  (so it wins) plus the favicon / logo / footer chrome. `AdminTheme()`
  is a no-op that reproduces the stock look, so existing sites are
  unchanged. String fields reject `< > { } "` at construction to keep a
  value from breaking the injected markup. Exported from
  `tempest_fastapi_sdk` and `tempest_fastapi_sdk.admin`.

## [0.71.1] — 2026-06-26

### Fixed

- **File logging no longer crashes the app on a non-writable filesystem.**
  `configure_logging` now treats file logging as best-effort: if `log_dir`
  cannot be created or its files cannot be opened (read-only mount, missing
  write permission, hardened container, serverless, CI), the file handlers
  are skipped, a warning is emitted (to the logger when stdout is on, else
  straight to `stderr`), and the service keeps running with stdout logging
  instead of dying at import time with
  `PermissionError: [Errno 13] ... 'logs'`. `_build_file_handlers` also
  closes any handlers it opened before a mid-build failure so no file
  descriptors leak.
- **Scaffold `Dockerfile` fixed so the non-root `app` user can write
  `logs/`.** `WORKDIR /app` created `/app` as `root` before the
  `COPY --chown=app:app`, and `--chown` only sets ownership on the copied
  *contents* — not on the pre-existing `/app` directory node — so the `app`
  user could not create `logs/` (or the SQLite `app.db`) inside it and the
  container crash-looped at startup. The template now runs
  `RUN mkdir -p /app/logs && chown -R app:app /app` after the copy. Existing
  projects: regenerate with `tempest generate --dockerfile --force` or add
  that line by hand.

## [0.71.0] — 2026-06-26

### Added

- **`Dockerfile` + `.dockerignore` in the scaffold** — `tempest new`
  now ships a multi-stage, uv-based `Dockerfile` (builder stage installs
  deps into `/app/.venv`; final stage copies only the venv + source and
  runs as a non-root `app` user) plus a `.dockerignore` that keeps the
  build context lean and never bakes `.env` / `*.db` / `logs/` into the
  image. The final stage sets `SERVER_HOST=0.0.0.0` so the container is
  reachable without a `.env`.
- **`tempest generate --dockerfile`** — regenerate the `Dockerfile` +
  `.dockerignore` in an existing project. The `EXPOSE` / `SERVER_PORT`
  is read from the project's `.env` / `.env.example` (`SERVER_PORT`),
  falling back to `8000`. Refuses to overwrite without `--force`, like
  the other generators, and composes with `--docker` / `--src`.

### Notes

- The generated `docker-compose.yaml` stays infra-only (no `app`
  service); the `Dockerfile` is standalone. Add an `app:` service with
  `build: .` by hand if you want a one-command stack.

## [0.70.1] — 2026-06-26

### Fixed

- **`AlembicHelper.current()` async fallback now triggers** — the 0.70.0
  fix only caught the missing-DBAPI error around `engine.connect()`, but
  SQLAlchemy 2.0 imports the DBAPI eagerly inside `create_engine()`, so
  asyncpg-only projects still crashed with `ModuleNotFoundError: No
  module named 'psycopg2'`. The guard now also wraps `create_engine`, so
  `current()` / `tempest db current` correctly fall back to the async
  driver.

## [0.70.0] — 2026-06-26

### Fixed

- **`AlembicHelper.current()` on async-only projects** — `current()`
  built a sync engine from the stripped URL (`postgresql://…`), which
  defaults to the `psycopg2` driver. Projects that install only an async
  DBAPI (e.g. `asyncpg`) crashed with `ModuleNotFoundError: No module
  named 'psycopg2'` (and so did `tempest db current`). It now falls back
  to reading `alembic_version` through the async driver when no sync
  DBAPI is available.

### Added

- **`AlembicHelper.stamp(..., purge=True)` + `tempest db stamp --purge`**
  — clear `alembic_version` before stamping. Required after a manual
  squash where the recorded revision no longer exists in the script
  directory: a plain stamp fails with `Can't locate revision`, while
  `--purge` drops the stale pointer and stamps the new baseline cleanly.

## [0.69.0] — 2026-06-25

### Added

- **`tempest db squash` + `AlembicHelper.squash(...)`** — collapse the
  whole migration history into a single fresh root revision. Migration
  files accumulate without bound as a project evolves; `squash` runs
  `downgrade base` on the configured (development) database, moves the
  old revisions into `alembic/versions/_squashed_<oldhead>/` (a
  subdirectory Alembic ignores — pass `--no-backup` / `backup=False` to
  delete instead), autogenerates one root migration from
  `BaseModel.metadata`, and re-applies it. The CLI requires `--yes`
  because the flow drops every table in the target database. Production
  databases are untouched — reconcile them with the new
  `tempest db stamp head` after deploying the collapsed tree.
- **`tempest db stamp <revision>`** — CLI surface for the existing
  `AlembicHelper.stamp`. Marks an already-populated database (e.g.
  production after a squash) as migrated without recreating tables.
  Defaults to `head`.
- **`tempest db backup` / `tempest db restore` + `DatabaseBackup`** —
  snapshot a database to a file and back, dispatching per dialect.
  PostgreSQL uses `pg_dump` / `pg_restore` (custom `-Fc` by default, or
  plain `.sql` via `psql` — chosen from the file extension); SQLite
  copies the database file. Backups default to a timestamped path under
  `backups/`. `restore` is a clean restore by default (drops + recreates
  so it is a faithful copy) and requires `--yes`; pass `--no-clean` to
  apply on top of the current schema. The Postgres password is passed
  via `PGPASSWORD` so it never appears in `ps`. `DatabaseBackup`,
  `BackupToolMissingError` and `UnsupportedBackupBackendError` are
  re-exported at the top level.

## [0.68.0] — 2026-06-21

### Added

- **`AdminSite.automap(source, ...)` + `discover_models(source, ...)`** —
  register every concrete `BaseModel` under a package in one call
  instead of one `register` per table. Point it at a dotted module path
  (`"src.db.models"`) or a module; abstract bases (no `__tablename__`)
  are skipped automatically. Supports `exclude=` (class / class name /
  table name), `skip_registered=` (default `True`, so hand-tuned admins
  registered first are preserved), and `**admin_kwargs` applied
  uniformly. `discover_models` is re-exported at the top level.
- **`AdminSite(brand=...)`** — optional centered header brand text,
  exposed to templates via the new `AdminSite.brand_text` property
  (falls back to `title` when unset, so existing sites are unchanged).

### Changed

- **Admin panel layout** — the header brand is now **centered** on
  screen, and on desktop (≥769px) the sidebar is **fixed full-height and
  overlays the header and footer** (raised `z-index`); the mobile
  off-canvas behavior is unchanged. Bundled-CSS only, no config.

## [0.67.0] — 2026-06-21

### Added

- **`backfill_non_nullable_defaults` Alembic hook** — autogenerate now
  gives every **added** `NOT NULL` column a `server_default` derived from
  its scalar Python `default=`, so adding a non-nullable column to a table
  that already has rows backfills them instead of raising
  `NotNullViolationError: column "x" contains null values` on PostgreSQL.
  Covers `bool` / `int` / `float` / `str` / `Enum` (uses `.value`); leaves
  callable / SQL-expression defaults (`uuid4`, `func.now()`) and
  default-less columns untouched (those need a hand-written data
  migration). `CreateTableOp` columns are never touched. Re-exported at
  the top level and from `tempest_fastapi_sdk.db`.

### Changed

- **The scaffolded `env.py` now composes both revision hooks** —
  `compose_hooks(reorder_base_columns_first, backfill_non_nullable_defaults)`
  — so freshly generated migrations are both column-ordered and
  backfill-safe out of the box. **Existing projects:** update your
  `alembic/env.py` import + `process_revision_directives` wiring to pick
  up the new hook (see the "A new `NOT NULL` column no longer explodes"
  admonition in the Database recipe).

## [0.66.2] — 2026-06-21

### Changed

- **Docs: the auth-flow refresh section now points to the built-in
  DB-backed refresh tokens** instead of telling readers to roll their
  own table. The "both tokens rotate" warning now clarifies that
  stateless is just the default, and a new tip links to the
  `docs/recipes/refresh-tokens.md` recipe (opt-in `refresh_token_model`
  with rotation, reuse detection and `POST /auth/logout`). Docs-only.

## [0.66.1] — 2026-06-21

### Changed

- **Docs: the "Receitas" / "Recipes" nav is now sorted alphabetically**
  by label (the landing `recipes/index.md` stays first), so readers can
  scan and find a recipe predictably. Docs-only change — no public API
  delta.

## [0.66.0] — 2026-06-21

### Added

- **DB-backed (opaque) refresh tokens with rotation, reuse detection and
  revocation** — opt-in via a new `refresh_token_model=` argument on
  `UserAuthService`. When wired, the refresh token becomes an **opaque**
  value whose SHA-256 hash is persisted (the access token stays a
  stateless JWT). Every `POST /auth/refresh` marks the presented token
  single-use and mints a new one in the same rotation **family**;
  replaying an already-rotated token is treated as theft and **revokes
  the whole family** (`401`). Without the model the service keeps the
  legacy stateless JWT refresh behavior — **no breaking change**.
- **`BaseUserRefreshTokenModel` + `make_user_refresh_token_model`** — the
  abstract opaque-refresh-token row (`token_hash`, `family_id`,
  `expires_at`, `used_at`, `revoked_at`) and the one-call factory to bind
  a concrete table to the project's user table, mirroring
  `BaseUserTokenModel` / `BaseUserRecoveryCodeModel`. Re-exported at the
  top level.
- **`UserAuthService.issue_token_pair(session, user, *, family_id=None)`**
  — async issuance path used by the router at every login-equivalent
  step; opaque+persisted when a refresh-token model is wired, stateless
  JWT otherwise.
- **`UserAuthService.revoke_refresh_token(session, *, refresh_token,
  all_sessions=False)`** — logout: revoke the token's family (or every
  active token of the user). Idempotent.
- **`POST /auth/logout`** on the bundled router — revokes a DB-backed
  refresh token (family, or all sessions with `all_sessions=true`).
  Mounted **only** when a `refresh_token_model` is wired; absent in
  stateless mode. Request body: new `LogoutSchema` (re-exported at the
  top level).
- **Recipe — "Refresh tokens (rotação/revogação)"** (`docs/recipes/
  refresh-tokens.md` + `.en.md`), wired into the docs nav.

### Changed

- **`UserAuthService.refresh_tokens`** now branches on whether a
  refresh-token model is wired: DB-backed rotation + reuse detection when
  present, the previous stateless JWT decode path when absent. The
  `POST /auth/refresh` endpoint commits the rotation and its docs cover
  both modes.

## [0.65.0] — 2026-06-21

### Added

- **`POST /auth/refresh` on the bundled auth router** — exchange a valid
  refresh token for a brand-new `access_token` + `refresh_token` pair
  **without** re-entering email + password. The token must carry the
  `refresh` claim (a replayed *access* token is rejected with `401`), the
  subject must resolve to an **active** user (inactive → `403`), and an
  expired / malformed / wrongly-signed token returns `401`. Both tokens
  rotate on success. Response reuses `LoginResponseSchema`.
- **`UserAuthService.refresh_tokens(session, *, refresh_token)`** — the
  public service method behind the endpoint, returning
  `(user, access_token, refresh_token)` for callers that drive the flow
  without the router.
- **`RefreshSchema`** — request body for the new endpoint, re-exported at
  the top level (`from tempest_fastapi_sdk import RefreshSchema`).

## [0.64.1] — 2026-06-21

### Fixed

- **`StoredFileServiceMixin` now composes cleanly with `BaseService` under
  strict mypy.** The mixin declared `repository: BaseRepository[ModelType]`,
  which clashed with `BaseService`'s own generic `repository` attribute and
  made `class X(BaseService[...], StoredFileServiceMixin[...])` fail type
  checking (`Definition of "repository" ... is incompatible`). The mixin no
  longer re-types the host-provided `repository`; its public methods stay
  precisely typed via `ModelType`.

## [0.64.0] — 2026-06-21

### Added

- **`StoredFileServiceMixin[Model]`** — a service mixin that encodes the
  single-key stored-file flow once, parameterized by field name:
  - `set_file(ref, file, *, field, subdir=..., filename=..., keep_original_name=...)`
    resolves the entity (detach-safe), uploads the new file and deletes the
    old one via `UploadUtils.replace` (new written before old deleted),
    writes the key back and commits.
  - `clear_file(ref, *, field)` deletes the object and nulls the field
    (no-op, no commit, when the field is already empty).
  - `file_url(key, *, expires=...)` returns a presigned download URL, or
    `None` for an empty key.

  Removes the ~13-line boilerplate every service reimplements for avatars,
  banners, covers and attachments. Reads its `upload_utils` and `storage`
  collaborators off `self`, so the owning service keeps configuration (size
  limits, allowed types, bucket). Covers the common "one key field →
  presigned URL" case; resize/thumbnail pipelines, multi-variant assets and
  galleries are out of scope (compose `UploadUtils` directly). See the
  **Arquivo no serviço (mixin)** recipe.
- **`SupportsUpload`** and **`SupportsPresign`** — structural-typing
  protocols describing the collaborators `StoredFileServiceMixin` needs
  (satisfied by `UploadUtils` and `AsyncMinIOClient`), so importing the
  mixin never pulls the optional `[upload]` / `[minio]` extras.

## [0.63.0] — 2026-06-21

### Changed

- **`UserAuthService.current_user_dependency()` now loads the authenticated
  user on the request-scoped session** (`db.session_dependency` by default)
  instead of opening its own short-lived session through `load_user`.
  Previously the returned `UserModel` was **detached** — mutating it and
  committing/refreshing on the request's repository session raised
  `InvalidRequestError: Instance is not persistent within this Session`,
  and lazy-relationship access raised `DetachedInstanceError`. The user is
  now attached to the same session repositories use. **Breaking** only for
  apps whose repositories do not share the auth service's session callable;
  pass `session_dependency=` to point both at the same provider. See the
  **Migration guide** (`docs/migration.md`). The single-argument `user_loader` path
  of `make_jwt_user_dependency` is unchanged; the new behavior is opt-in via
  the new `session_dependency=` parameter (which `current_user_dependency`
  now passes by default).
- **`BaseRepository.resolve()` re-attaches detached instances** via
  `session.merge()` instead of returning them as-is. A detached model passed
  to a mutating service is brought back into the active session, so the
  subsequent `update()` commits instead of raising. `merge` issues a
  `SELECT` only when the row is not already in the session's identity map.

### Added

- **`make_jwt_user_dependency(..., session_dependency=...)`** — when given a
  request-scoped session provider, the dependency injects it and calls the
  two-argument loader `user_loader(subject, session)`, sharing the session
  with the request's repositories.
- **`UserAuthService.current_user_dependency(session_dependency=...)`** —
  override the session provider shared with repositories (defaults to
  `self.db.session_dependency`). Now raises `RuntimeError` eagerly when the
  service was built without `db=`.

## [0.62.0] — 2026-06-20

### Added

- **`BasePaginationFilterSchema.get_pagination_conditions()`** and
  **`CursorPaginationFilterSchema.get_pagination_conditions()`** — the
  counterpart to `get_conditions()`. Where `get_conditions()` strips the
  pagination keys to expose the domain filters, this returns **only** the
  pagination/sort keys (`page`/`page_size`/`order_by`/`ascending` for
  offset, `cursor`/`limit`/`order_by`/`ascending` for cursor). A service
  can now forward a filter schema to `paginate` / `cursor_paginate`
  without manually unpacking the model:

    ```python
    data = await repo.paginate(
        filters=f.get_conditions(),
        **f.get_pagination_conditions(),
    )
    ```

  This replaces the `**filter_schema` idiom, which leaked domain filters
  (e.g. `is_active`) into keyword arguments the repository does not accept.

## [0.61.0] — 2026-06-15

### Added

- **`POST /auth/password-change`** — a `make_auth_router` endpoint for an
  **authenticated** user to change their own password while logged in.
  Requires a valid bearer `access_token`; the user re-enters their
  `current_password` (mismatch → **401**) and the `new_password` is
  validated against the configured password policy (violations → **422**).
  Returns **204**; existing tokens stay valid (no session revocation).
  Distinct from the email-token reset flow.
    - **`UserAuthService.change_password(session, *, user,
      current_password, new_password)`** — the backing service method.
    - **`PasswordChangeSchema`** — request body
      (`current_password` + `new_password`), exported at the package root.

## [0.60.0] — 2026-06-15

### Added

- **`BaseRepository.resolve(id_or_instance)`** — accepts either a
  primary-key `UUID` or an already-loaded model instance and always
  returns the instance (`get_by_id` when a `UUID`, pass-through
  otherwise). Removes the `if isinstance(x, UUID): ...` boilerplate every
  service reimplements for methods that take `UUID | Model`.
- **`BaseRepository.exists_excluding(filters, *, exclude_id)`** — "is this
  value already used by *another* row?" The uniqueness check needed when
  updating a unique field (email / phone / username): plain `exists`
  would match the row itself; this excludes `exclude_id`. Pass
  `exclude_id=None` (the create case) to behave like `exists`.
- **`UploadUtils.replace(old_key, file, ...)`** — save a new object and
  delete the one it replaces, in one call. The new file is persisted
  **first** (so a validation/write failure leaves the old object intact),
  then `old_key` is deleted through the **same** configured backend —
  avoiding the save-through-one-backend, delete-through-another mistake.
  `old_key=None` skips the delete, so the same call serves first uploads
  and replacements.

## [0.59.1] — 2026-06-14

### Changed

- **Database recipe rewritten in the tutorial-first (tiangolo) pattern.**
  The `docs/recipes/database.*` page was shallow — it covered only the
  mixins, a hand-rolled cursor query, and Alembic, and skipped most of
  the DB layer. It is now a progressive, nine-section guide with
  complete runnable examples, admonitions and per-section recaps:
    - **`BaseModel`** — the four canonical columns, `NAMING_CONVENTION`,
      auto `__tablename__`, and the `to_dict` / `update_from_dict` /
      equality helpers.
    - **`AsyncDatabaseManager`** — engine/pool config, the per-request
      `session_dependency`, lifespan wiring, `health_check`,
      `db_url_safe`.
    - **`BaseRepository`** — direct vs. subclass usage, the mappers, and
      the full async CRUD surface (`get` / `get_or_none` / `first` /
      `list` / `exists` / `count` / `add` / `update` / `delete*` /
      `soft_delete` / `restore`).
    - **Convention-based filters** — `name` ILIKE, `bool`, `list`,
      `date`, `start_in` / `end_in`, and the `<col>__<op>` comparison
      suffixes.
    - **Bulk operations** — `add_all` / `update_many` vs.
      `bulk_create_values` / `bulk_update` / `bulk_upsert`.
    - **Pagination** — both the built-in `paginate` (offset) and
      `cursor_paginate` (cursor), replacing the old hand-rolled cursor
      example that reinvented logic the SDK already ships.
    - **`SlowQueryLogger`** — new section.
  No public API changed — documentation only.

## [0.59.0] — 2026-06-14

### Added

- **Bilingual auth emails and backend pages (`AUTH_DEFAULT_LOCALE`).**
  The bundled activation / password-reset **emails** and the
  backend-only **HTML pages** now ship in two languages out of the box —
  Brazilian Portuguese (`pt-BR`, the new default) and US English
  (`en-US`) — so a service gets fully localized account flows with zero
  custom templates.
    - **`AUTH_DEFAULT_LOCALE`** setting (default `"pt-BR"`) — language of
      the bundled emails and pages. Normalized case-insensitively, so
      `PT-BR`, `pt_br` and `ptbr` all resolve to `pt-BR`; `EN`, `en_US`
      and `enus` to `en-US`.
    - **Emails** always render in `AUTH_DEFAULT_LOCALE` (subject, plain
      body and HTML), since they have no request context.
    - **Backend HTML pages** (`AUTH_BACKEND_LINKS=True`) prefer the
      browser's `Accept-Language` header and fall back to
      `AUTH_DEFAULT_LOCALE`, so the same backend serves a Portuguese or
      English page per visitor.
    - New public helpers under `tempest_fastapi_sdk.auth`:
      `normalize_locale`, `negotiate_locale`, `format_expires_at`,
      `SUPPORTED_LOCALES` and `DEFAULT_AUTH_LOCALE`.
    - `EmailUtils.render_template(...)` and `render_auth_page(...)` gained
      an optional `locale=` argument selecting a per-locale template
      subdirectory.
- **Token-expiry timestamps in emails are now short and readable.** The
  activation / reset emails render the expiry as `21/06/2026 23:25 (UTC)`
  (pt-BR) or `2026-06-21 23:25 (UTC)` (en-US) — no seconds, no
  microseconds — instead of the raw
  `2026-06-21 23:25:49.742054+00:00`.

### Changed

- **`make_auth_router` endpoints now carry rich OpenAPI summaries and
  descriptions.** Every signup / activation / login / password-reset /
  MFA route (and the backend HTML pages) documents its request, response,
  status codes, side effects and related settings directly in `/docs`.
- **Bundled auth templates moved to per-locale subdirectories**
  (`auth/templates/<locale>/<name>.html`). Projects overriding templates
  via `template_dir` keep working unchanged (the flat layout is still
  searched); to override a single language, place the file under
  `template_dir/<locale>/`.

### Migration

- **No action required** for projects that keep the defaults — they now
  send Portuguese emails/pages instead of English. Set
  `AUTH_DEFAULT_LOCALE=en-US` to restore English as the default.
- **`EmailUtils.render_template("activation.html")` with no `template_dir`
  and no `locale`** now resolves the bundled template from the default
  locale (`pt-BR`) instead of English. Pass `locale="en-US"` to render
  the English bundled template.

## [0.58.1] — 2026-06-14

### Fixed

- **CLI no longer crashes with `ModuleNotFoundError: No module named
  'click'`.** `tempest_fastapi_sdk.cli.main` imports the public `click`
  package (for `click.echo` / `click.secho` / `click.UsageError` /
  `click.exceptions.Abort` in the full-help group), but `click` was only
  ever present transitively through Typer. Newer Typer releases vendor
  their own Click copy under `typer._click` and no longer pull the public
  `click` package, so installs that resolved such a Typer broke at CLI
  startup (`tempest new`, every `tempest …` command). `click>=8.0.0` is
  now a direct dependency, guaranteeing the public package is always
  importable regardless of how Typer ships Click.

## [0.58.0] — 2026-06-14

### Added

- **Per-entity audit trail** — an append-only log of who changed what,
  with a before/after diff, beyond the timestamp-only `AuditMixin`.
    - **`BaseAuditLogModel`** — abstract `audit_log` table (subclass and
      pick `__tablename__`, like `BaseOutboxModel`): `entity`,
      `entity_id`, `action`, `actor`, `changes` (JSON diff) and optional
      `context`. Ships `new_entry` / `for_create` / `for_update` /
      `for_delete` constructors and the `AuditAction` enum.
    - **`snapshot_model(instance)`** / **`diff_snapshots(before, after)`**
      — capture a model's columns as a JSON-able dict and diff two
      snapshots into `{field: {"before", "after"}}`.
    - **`BaseRepository` opt-in hook** — pass `audit_model=...` and use
      `add_audited(model, *, actor, context)`,
      `update_audited(model, before, *, ...)` (pair with `snapshot()`
      taken before mutating) and `delete_audited(model, *, ...)`. The
      business row and the audit row commit in the **same transaction**,
      so the trail can never reference a rolled-back change.
    - All symbols exported from `tempest_fastapi_sdk.db` and the package
      top level. Fully backward compatible: repositories without
      `audit_model` are unchanged.

## [0.57.0] — 2026-06-14

### Added

- **Feature flags** — toggle features without a redeploy (rollouts,
  kill-switches, beta gating). New `tempest_fastapi_sdk.flags` module,
  all exported at the package top level.
    - **`FeatureFlags(backend, default=False)`** service —
      `is_enabled(name, default=...)`, `enable` / `disable` / `set`,
      and `all()`.
    - **Pluggable backends** — `MemoryFeatureFlagBackend` (dev/tests),
      `EnvFeatureFlagBackend` (static, read-only, `FEATURE_<NAME>`),
      `RedisFeatureFlagBackend` (runtime toggles in a Redis hash,
      shared across replicas) and `CompositeFeatureFlagBackend`
      (layered — a Redis override beats an env default). The
      `FeatureFlagBackend` protocol + `coerce_flag` helper are public.
    - **`make_flag_dependency(flags, name, *, enabled=True,
      status_code=404, ...)`** — a FastAPI dependency that gates a
      route on a flag, raising the SDK envelope (404 by default, so a
      disabled feature looks absent; `enabled=False` inverts it into a
      kill-switch).

## [0.56.0] — 2026-06-14

### Added

- **Tag / namespace invalidation for `@cached`** — cache entries can be
  dropped on mutation instead of only expiring by TTL.
    - **`@cached(..., namespace=..., tags=...)`** — `namespace` is one
      coarse bucket per decorator; `tags` are fine-grained labels, given
      as a static sequence or a per-call builder
      `(args, kwargs) -> Sequence[str]` (e.g. `f"user:{id}"`). On each
      write the entry key is added to a Redis set per label, and those
      registry sets inherit the entry TTL so they self-prune.
    - **`CacheInvalidator(redis, key_prefix=...)`** — drops every entry
      under a label via `invalidate_namespace(ns)`,
      `invalidate_tag(tag)`, `invalidate_tags(*tags)` (deduped) and
      `invalidate_keys(*keys)` (raw keys); each returns the number of
      entries deleted. Bind it with the same `key_prefix` the matching
      decorators use.
    - **`namespace_registry_key`** / **`tag_registry_key`** helpers
      expose the registry set naming.
    - Fully backward compatible: without `namespace` / `tags` no
      registry sets are written and behavior is unchanged. New symbols
      are exported from `tempest_fastapi_sdk.cache`.

## [0.55.0] — 2026-06-14

### Added

- **Localized error envelopes (i18n) for `AppException`** — the error
  `detail` can now be resolved per request locale instead of being
  English-only, without callers hand-translating each `raise`.
    - **`MessageCatalog`** — maps `(locale, key) -> template`, with
      case-insensitive locale matching and primary-subtag fallback
      (a catalog holding `pt-BR` answers `pt`, and vice versa).
      `resolve()` interpolates `message_params` via `str.format`
      (a missing param returns the template instead of raising);
      `negotiate()` picks the best locale from an `Accept-Language`
      header; `merge()` overlays domain codes / new locales onto a
      base catalog without mutating it.
    - **`default_message_catalog()`** — PT-BR (default) + EN-US strings
      for every built-in exception code (`NOT_FOUND`, `CONFLICT`,
      `UNAUTHORIZED`, `FORBIDDEN`, `VALIDATION_ERROR`,
      `TOO_MANY_REQUESTS`, `INVALID_TOKEN`, `TOKEN_EXPIRED`,
      `FILE_TOO_LARGE`, `INVALID_FILE_TYPE`, `INTERNAL_SERVER_ERROR`).
    - **`parse_accept_language()`** + **`DEFAULT_LOCALE`** (`"pt-BR"`).
    - **`AppException` gains `message_key` / `message_params`** — the
      catalog key (defaults to the exception `code`) and template
      values. **`register_exception_handlers(app, catalog=...,
      default_locale=...)`** and `make_app_exception_handler` accept the
      catalog and localize `detail` from the negotiated locale.
    - Fully backward compatible: with no `catalog` the literal
      `message` is used exactly as before; a missing translation falls
      back to the exception's own `detail`. All new symbols are
      exported at the package top level.

## [0.54.0] — 2026-06-14

### Added

- **Per-principal & distributed rate limiting** — `RateLimitMiddleware`
  gains a pluggable store and ready-made key extractors, so limits can
  be per user / tenant / API key and shared across replicas.
    - **Pluggable store** — `RateLimitStore` protocol with
      `MemoryRateLimitStore` (default, in-process) and
      `RedisRateLimitStore` (distributed). The Redis store uses an
      atomic Lua sliding-window log over a sorted set — no race between
      count and add — and `fail_open=True` (default) allows the request
      on a transient Redis error. `RateLimitResult` carries the
      `allowed` / `remaining` / `retry_after` decision.
    - **Key extractors** — `key_by_ip`, `key_by_jwt_subject`
      (per-user via the `sub` claim), `key_by_jwt_claim` (per arbitrary
      claim, e.g. `tenant_id`) and `key_by_header` (e.g. an API key).
      Because the middleware runs before FastAPI dependencies, the
      `key_by_jwt_*` factories decode the bearer from the raw request
      (`decode_or_none`) and fall back to the client IP for anonymous
      traffic.
    - Fully backward compatible: the default behavior (in-process,
      per-IP) is unchanged; `store=` and the `key_by_*` factories are
      opt-in. All new symbols are exported at the package top level.

## [0.53.0] — 2026-06-13

### Added

- **Brazilian states & municipalities dataset** — an offline,
  dependency-free table of every federative unit and its cities, plus
  Pydantic building blocks. No network calls, no external API.
    - **`UF`** — a `StrEnum` with the 27 federative unit acronyms
      (`UF.SP`, `UF.RJ`, …).
    - **`Region`** — the five official IBGE macro-regions
      (`Norte`, `Nordeste`, `Centro-Oeste`, `Sudeste`, `Sul`).
      Every UF is statically mapped to its region.
    - **`StateBR`** / **`CityBR`** — schemas for a state (acronym +
      full name + region + alphabetically sorted municipalities) and a
      single city (name + UF).
    - **Query helpers** — `list_states()`, `get_state(uf)`,
      `cities_by_uf(uf)`, `states_by_region(region)`. The single-UF
      lookups accept any-case acronyms or a `UF` member.
    - **Validators/normalizers** — `is_valid_uf` / `normalize_uf`
      (case- and whitespace-insensitive), `is_valid_city` /
      `normalize_city` (also accent-insensitive, returning the
      canonical proper-case name).
    - **Annotated types** — `UFField` (coerces any-case acronyms to a
      `UF`) and `CityNameField` (trims a city name) ready to drop into
      schema fields.
    - Dataset bundled as `utils/data/br_locations.json` (27 states,
      5606 entries) and loaded lazily on first access. Municipality
      names come from the official IBGE list (current spellings,
      including post-2005 municipalities); the Distrito Federal is
      represented by its 36 administrative regions rather than a single
      Brasília row, for address-form use. All symbols are exported at
      the package top level.

## [0.52.0] — 2026-06-12

### Added

- **Delta-sync primitives on `BaseRepository`** — the backbone of
  offline-first / mobile / PWA backends, so projects stop copy-pasting
  cursor logic per service.
    - **Comparison filter operators.** Filter keys now accept a
      `<column>__<op>` suffix where `<op>` is `gt` / `gte` / `lt` /
      `lte` / `ne` (e.g. `{"updated_at__gt": watermark}` →
      `updated_at > watermark`). Timestamp-precise, unlike the
      whole-day `start_in` / `end_in`. A `None` value skips the
      condition, like every other filter. Works in every method that
      takes `filters` (`list`, `paginate`, `cursor_paginate`, `count`,
      `changes_since`, …).
    - **`BaseRepository.cursor_paginate(..., query=...)`.** New
      optional `query: Select | None` parameter mirroring `paginate`,
      so a hand-built `Select` (joins, `IS NULL` predicates the filter
      dict can't express) can still be cursor-paginated.
    - **`BaseRepository.changes_since(since, *, filters=None,
      cursor=None, limit=50, order_by="updated_at",
      include_deleted=True)`.** Returns rows changed strictly after a
      high-water mark, ascending by `updated_at` and tie-broken by
      `id`, cursor-paginated. Includes soft-deleted tombstones by
      default (so deletions propagate to the client) and returns a
      `server_time` the client persists as the next `since` —
      clock-skew-proof because it is captured server-side before the
      query runs.
- **`SyncFilterSchema` / `SyncPaginationSchema`** — request/response
  DTOs mirroring `changes_since` (the response carries `server_time`).
  Exported at the package top level.

## [0.51.0] — 2026-06-12

### Changed

- **Refreshed the dependency lock to latest compatible.** Notably
  `fastapi` 0.136.1 → 0.136.3, `starlette` 1.0.0 → 1.3.1,
  `sqlalchemy` 2.0.49 → 2.0.50, `typer` 0.26.4 → 0.26.7, `uvicorn`
  0.47 → 0.49, `cryptography` 48 → 49, `redis` 7.4 → 8.0,
  `faststream` 0.6.7 → 0.7.1. Project `>=` floors are unchanged except
  `faststream` (see below).
- **`faststream[rabbit]` floor raised to `>=0.7.1`** (was `>=0.5.30`).
  faststream 0.7 renamed `Broker.close()` to `Broker.stop()`;
  `AsyncBrokerManager.disconnect()` now calls `broker.stop()`, which
  does not exist on faststream < 0.7. Services pinning the `[queue]`
  extra must allow `faststream >= 0.7.1`.

### Fixed

- **redis 8.0 type stubs.** `@cached`'s `deserializer` parameter is now
  typed `Callable[[str | bytes], Any]` (redis returns `bytes` unless the
  client sets `decode_responses=True`; `json.loads` accepts both), and a
  stale `# type: ignore` on `RedisCacheManager.ping()` was removed.

## [0.50.0] — 2026-06-12

### Added

- **Imperative authorization guards — `require_authenticated`,
  `require_active`, `require_admin`.** Projects no longer hand-write
  `if user is None: raise ...` / `if not user.is_admin: raise ...`
  helpers. The new guards (in `tempest_fastapi_sdk.auth.guards`,
  re-exported at the top level) take the `UserT | None` a `soft=True`
  authenticated-user dependency yields, raise the canonical
  `UnauthorizedException` (401) / `ForbiddenException` (403) on failure,
  and **return the user narrowed to non-`None` and to its concrete
  subclass** so the caller drops the `| None` for the rest of the
  function: `user = require_admin(current)`. Generic over
  `BaseUserModel` via a bound `TypeVar`. Also mirrored as static methods
  on `UserAuthService` (`auth_service.require_admin(user)`) so a service
  already in scope guards without an extra import. The auth-flow recipe
  documents the path.

## [0.49.0] — 2026-06-12

### Added

- **`UserAuthService.current_user_dependency()` — built-in
  authenticated-user dependency.** Projects no longer hand-write a
  `load_user` callable plus a second `JWTUtils` to read the
  `current_user` from a bearer token. The service now exposes
  `get_user(subject, session)` (session-explicit), `load_user(subject)`
  (opens its own session from the `db=` handle), and
  `current_user_dependency(*, soft=False)` which wraps
  `make_jwt_user_dependency` with the service's **own** `JWTUtils` and
  `load_user` — so the token is verified with the same secret it was
  signed with, eliminating the divergent-secret footgun. Wiring
  collapses to `get_current_user = auth_service.current_user_dependency()`
  / `auth_service.current_user_dependency(soft=True)`. Requires the
  service to be built with `db=` (already an accepted constructor arg).
  The auth-flow and HTTP recipes now teach this path.

## [0.48.0] — 2026-06-11

### Changed

- **Scaffolded services read their API `title` / `version` /
  `description` from `.env`.** `tempest new` previously hardcoded
  `FastAPI(title="<project>", version="0.1.0")` and the health-router
  version in `src/api/app.py`. The scaffolded `Settings` now carries
  `TITLE`, `DESCRIPTION` and `VERSION` fields (each with `title` /
  `description` / `examples`), `app.py` reads them
  (`FastAPI(title=settings.TITLE, version=settings.VERSION, ...)`,
  `make_health_router(version=settings.VERSION)`,
  `AdminSite(title=f"{settings.TITLE} admin")`), and `.env.example`
  ships a documented `TITLE` / `VERSION` / `DESCRIPTION` block — so the
  OpenAPI docs and admin header are configurable without editing code.

## [0.47.0] — 2026-06-11

### Added

- **`tempest db seed`** — runs a project seed callable
  (default `src.db.seeds:seed`, dotted `module:callable`, sync or async,
  taking one `AsyncSession`) inside a managed session: commit on
  success, rollback on error. The SDK only wires the session lifecycle;
  the callable owns what gets inserted. Prints the row count when the
  callable returns an `int`.
- **`tempest secrets rotate`** — generates fresh URL-safe secrets for
  the keys a service signs/authenticates with (`JWT_SECRET` /
  `TOKEN_SECRET` by default; override with `--keys`) and rewrites the
  matching `.env` lines **in place** (existing keys replaced, missing
  keys appended) after a `.env.bak` backup. `--print` writes nothing and
  emits the values to stdout; `--length` sets the entropy; `--no-backup`
  skips the backup.

### Docs

- CLI recipe (bilingual) gains **`db seed`** and **`secrets rotate`**
  sections; README CLI section and recipes index updated.

## [0.46.0] — 2026-06-11

### Added

- **`AlembicHelper.safe_upgrade(revision="head", *, force=False)`** —
  runs the upgrade only after scanning each pending migration's
  `upgrade()` for data-destroying calls (`op.drop_table` /
  `op.drop_column` / `op.drop_constraint` and `batch_op` variants). When
  any are found it raises **`DestructiveMigrationError`** (carrying the
  offending `(revision, operation)` pairs) and leaves the database
  untouched; `force=True` logs and proceeds. Source-based scanning is
  dialect-agnostic (no false positives on SQLite batch rebuilds) and
  ignores drops in `downgrade()`. `pending_destructive_ops()` exposes the
  scan without running anything (CI-friendly).
- **`GracefulShutdownMiddleware`** — tracks in-flight requests and, once
  draining, replies `503` + `Retry-After` to new requests so a load
  balancer deregisters the instance. `begin_drain()` / `wait_drained()`
  (bounded by `drain_timeout`) are driven from the lifespan shutdown
  (uvicorn owns `SIGTERM`); an opt-in `install_signal_handlers()` chains
  the previous handler for servers that manage signals themselves. Wired
  via `app.add_middleware(BaseHTTPMiddleware, dispatch=shutdown.dispatch)`.

### Docs

- New bilingual recipe **Deploy seguro / Safe deploys** covering
  `safe_upgrade` and `GracefulShutdownMiddleware`, added to the nav, the
  recipes index, and the API reference.

## [0.45.0] — 2026-06-11

### Added

- **`TenantScopedRepository[ModelType]`** — a `BaseRepository` locked
  to a single tenant for shared-schema multi-tenancy. Bind a
  `tenant_id` (and optional `tenant_field`, default `"tenant_id"`) at
  construction; it injects `WHERE tenant_id = ?` into **every** read
  (`get`/`get_or_none`/`get_by_id`/`exists`/`first`/`list`/`count`/
  `paginate`/`cursor_paginate`/`delete_many`) and stamps the tenant id
  onto **every** write (`add`/`add_all`). `delete` / `delete_batch` add
  the tenant predicate to the `DELETE` so a guessed id from another
  tenant matches nothing — cross-tenant access (even probing existence
  by id) is impossible through the repository. The constructor raises
  `AttributeError` at boot if the model lacks the tenant column.
  `tenant_column` property exposes the mapped column for custom queries.

### Docs

- New bilingual recipe **Multi-tenant**, added to the nav (under
  Database), the recipes index, and the API reference.

## [0.44.0] — 2026-06-11

### Added

- **Transactional outbox.** New `BaseOutboxModel` (abstract — the
  project subclasses it and picks `__tablename__`) carrying `topic`,
  `payload` (JSON), `status`, `attempts` / `max_attempts`,
  `available_at`, `sent_at` and `last_error`. `OutboxModel.new_event(
  topic, payload)` builds a pending row.
- **`BaseRepository.save_with_outbox(model, event)`** inserts the
  business row and the outbox event in the **same transaction**, so an
  event can never reference a rolled-back row (and a committed row
  always has its event durably queued) — the fix for the dual-write
  problem.
- **`OutboxRelay`** drains pending rows and publishes them through a
  caller-supplied async `publish` callable (no hard broker dependency
  — works with `AsyncBrokerManager`, a webhook, a test spy). Marks each
  row `sent`; on failure increments `attempts`, records `last_error`,
  reschedules with exponential backoff, and marks `failed` once the
  attempt budget is spent. Locks the batch with `FOR UPDATE SKIP
  LOCKED` on PostgreSQL/MySQL (multi-worker safe), falling back to a
  plain select on SQLite. `drain_once()` for one batch (tests/cron),
  `run(poll_interval=...)` for the loop.
- **`OutboxStatus`** enum (`pending` / `sent` / `failed`).

### Docs

- New bilingual recipe **Outbox transacional / Transactional outbox**,
  added to the nav, the recipes index, and the API reference.

## [0.43.0] — 2026-06-11

### Added

- **Distributed tracing with OpenTelemetry.** `setup_tracing(app,
  service_name=..., otlp_endpoint=...)` installs an OTLP/gRPC span
  exporter and auto-instruments FastAPI (incoming requests),
  SQLAlchemy (queries, via `sqlalchemy_engine=db.engine`) and httpx
  (outbound calls) so one trace follows a request across services.
  `otlp_endpoint=None` falls back to a console exporter for local
  debugging; `sample_ratio` controls head-based sampling;
  `resource_attributes` merges extra span attributes. Behind the new
  `[otel]` extra — importing the SDK without it costs nothing and
  never crashes. Complements `RequestIDMiddleware` (which correlates
  logs).
- **`SlowQueryLogger`** — attaches `before/after_cursor_execute`
  listeners to a SQLAlchemy engine (sync or async) and logs every
  statement slower than `threshold_ms`. Bind parameters are omitted
  by default (PII); `log_parameters=True` and `explain=True` (runs
  `EXPLAIN`) are opt-in for development. No extra required. Exposed
  at the top level and from `tempest_fastapi_sdk.db`.
- **`AsyncDatabaseManager.engine`** — public property returning the
  live `AsyncEngine`, so instrumentation (`SlowQueryLogger`, the OTel
  SQLAlchemy instrumentor) can attach to it directly.

### Docs

- New bilingual recipe **Observabilidade / Observability** covering
  `setup_tracing` and `SlowQueryLogger`, added to the nav, the
  recipes index, and the API reference.

## [0.42.0] — 2026-06-11

### Added

- **`tempest user promote` / `tempest user revoke`** — flip `is_admin`
  for an existing user, found by email (case-insensitive), without
  hand-written SQL. `promote` grants `/admin` access (`is_admin=True`),
  `revoke` removes it. Both exit `1` with `no user found` when no user
  matches the email.
- **`tempest generate --src`** — add the optional source layers
  triggered by the project's pinned SDK extras to an existing project:
  `[queue]` → `<root>/queue/` (FastStream broker + handlers stub),
  `[tasks]` → `<root>/tasks/` (TaskIQ broker + jobs stub). The source
  root (`src` or `app`) is auto-detected and generated imports point at
  it. Idempotent — existing files are kept unless `--force` is passed.
  `--docker` and `--src` can be combined in one invocation.
- **`tempest new` now scaffolds the chosen extras' source layers.**
  `tempest new svc --extras auth,queue` ships `src/queue/` out of the
  box (and `src/tasks/` with `[tasks]`); projects without those extras
  get no placeholder packages.

### Changed

- **Usage errors now print the offending command's full `--help`.**
  An unknown command, an invalid option, or a missing required argument
  renders the complete help (every parameter, default and description)
  before the error line, instead of Click's terse `Try '... --help'`
  hint. Quality-gate exit codes still propagate unchanged.
- **`tempest user create` prompts for the admin flag interactively.**
  When neither `--admin` nor `--no-admin` is passed in an interactive
  terminal, it asks `Should this user be an administrator? [y/N]`.
  Non-interactive runs (CI, pipes, scripts) skip the prompt and default
  to a regular user — pass `--admin` explicitly to create an admin
  without a TTY. The flag is now `--admin/--no-admin` (tri-state).

## [0.41.0] — 2026-06-07

### Changed (breaking)

- **`UploadUtils` and `DownloadUtils` now take the backend at construction**
  — a local folder **or** an `AsyncMinIOClient` — so callers stop passing it
  on every call and the same code works for disk or object storage:
  `UploadUtils("var/uploads")` / `UploadUtils(minio)`,
  `DownloadUtils("var/uploads")` / `DownloadUtils(minio)`.
    - `UploadUtils.save(...)` **dropped the per-call `storage=` argument**;
      it now returns the storage **key** (relative `Path`), not an absolute
      local path. `UploadUtils.delete(key)` is now **async**. The first
      constructor parameter was renamed `upload_dir` → `source`
      (`UploadSettings.upload_kwargs()` updated to match).
    - Migration: `UploadUtils(tmp) + save(file, storage=MinIOUploadStorage(c))`
      → `UploadUtils(c) + save(file)`; `path = save(...)` consumers that read
      the file back should store the key and use `DownloadUtils.download(key)`.

### Added

- **Download objects from MinIO/S3 through the app.**
  `AsyncMinIOClient.download_response(key, ...)` stats + streams an object
  into a ready `StreamingResponse` (Content-Disposition / type / length) —
  no disk, no full-memory load. `DownloadUtils(minio).download(key)` wraps
  it so local and MinIO downloads share one call (`download()`), while
  `file_response`/`resolve` stay local-only.

### Changed

- **Scaffold: infra singletons moved to `src/api/dependencies/resources.py`.**
  `tempest new` now builds the database manager once in `resources.py`
  (`db = AsyncDatabaseManager(**settings.database_kwargs())`) and exposes
  `get_db` / `get_session` providers; `app.py` imports `db` instead of
  constructing it inline, keeping the factory thin. Storage/mail follow the
  same shape (commented, opt-in with `[minio]`/`[email]`). The generated
  admin now enables the logs page (`show_logs=True`). Docs (architecture,
  tutorial, admin recipe) teach the same pattern.

### Fixed

- **Admin "+ New" button was white-on-white** (invisible): the
  `.tempest-admin-list__actions a` rule outweighed `.tempest-admin-list__new`,
  so the accent background was lost while the text stayed white. Scoped the
  button rule to win specificity.
- **Admin desktop sidebar didn't span the full height** when page content
  was short. The layout is now a sticky-footer flex column so the sidebar
  always reaches the footer.

### Added

- **Docs: three previously-missing util recipes** (bilingual) — `Downloads`
  (`DownloadUtils` + `build_content_disposition`), `HTTP client (outbound)`
  (`HTTPClient` + `RetryPolicy` + circuit-breaker, which had zero recipe
  coverage), and `Utilities` (`utcnow`/`to_utc`, `modify_dict`,
  `get_client_ip`, opaque tokens). Added to the nav and the recipes index.

## [0.39.0] — 2026-06-07

### Added

- **Admin: application logs page.** `make_admin_router(show_logs=True,
  log_dir=...)` mounts `GET {prefix}/logs`, reading the structured JSON
  files written by `configure_logging` and rendering them filtered (by
  source + message substring), paginated, with color-coded level badges.
  Opt-in (default `False`) since the payload exposes tracebacks; it adds a
  "Logs" entry to the sidebar and shows an empty state when no files
  exist.
- **Admin: sidebar navigation + mobile burger.** Every authenticated page
  now has a persistent left sidebar (Dashboard, one link per registered
  model, and Logs when enabled), with the current page highlighted. On
  desktop it is always visible; on mobile (≤768px) it becomes an
  off-canvas drawer toggled by a burger button and dismissed via a scrim —
  pure CSS, no JavaScript.
- **`*_kwargs()` helpers on settings mixins** that mirror an SDK
  constructor, so wiring is a one-liner instead of repeating field names:
  `DatabaseSettings.database_kwargs()` → `AsyncDatabaseManager`,
  `RedisSettings.redis_kwargs()` → `AsyncRedisManager`,
  `JWTSettings.jwt_kwargs()` → `JWTUtils`,
  `UploadSettings.upload_kwargs()` → `UploadUtils`,
  `WebPushSettings.webpush_kwargs()` → `WebPushDispatcher`,
  `MinIOSettings.minio_kwargs()` → `AsyncMinIOClient` (joining the
  existing `EmailSettings.email_kwargs()`). Each is splat-tested against
  the real constructor. Settings consumed by helpers that already accept a
  `settings=` object (`run_server`, `apply_cors`) keep that path.

## [0.38.1] — 2026-06-07

### Fixed

- **`EmailUtils` no longer hard-fails against a plain SMTP server.**
  `send()` forced `start_tls=True`, so any server that doesn't advertise
  STARTTLS — including the bundled MailHog dev server on `:1025` — crashed
  with `SMTPException: SMTP STARTTLS extension not supported by server.`
  (and the `/auth/password-reset/request` endpoint returned 500). STARTTLS
  is now **opportunistic** (`start_tls=None`): the connection upgrades only
  when the server advertises STARTTLS, and is left plain otherwise. This
  fixes existing services whose `.env` predates the 0.38.0 `.env.example`
  correction — no `SMTP_USE_TLS=false` needed for MailHog anymore. Setting
  `SMTP_USE_TLS=false` still forces plain with no upgrade attempt; implicit
  TLS (`SMTP_USE_SSL` / port 465) is unchanged.

## [0.38.0] — 2026-06-07

### Fixed

- **Email config from a generated `.env` silently did nothing, then
  crashed against MailHog.** The `[email]` block that `tempest new` /
  `tempest generate --docker` wrote to `.env.example` used `EMAIL_*`
  names (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`,
  `EMAIL_USE_STARTTLS`), but `EmailSettings` reads `SMTP_*`. The values
  were ignored, leaving `SMTP_USE_TLS` at its `True` default, so STARTTLS
  was forced and aiosmtplib raised `SMTPException: SMTP STARTTLS extension
  not supported by server.` against plain MailHog. The block now emits the
  correct `SMTP_HOST` / `SMTP_PORT` / `SMTP_FROM_ADDR` plus
  `SMTP_USE_TLS=false` + `SMTP_USE_SSL=false` for MailHog.

### Added

- **`EmailSettings.email_kwargs()`** — maps the `SMTP_*` settings onto the
  `EmailUtils` constructor (`SMTP_USE_TLS`→`use_starttls`,
  `SMTP_USE_SSL`→`use_tls`), so the long-documented
  `EmailUtils(**settings.email_kwargs())` recipe actually works. The
  method was referenced in the docstring but never existed.
- **Recipes for transactional email and Web Push** on the docs site
  (`recipes/email.md` + `recipes/webpush.md`, bilingual). Web Push moved
  out of the buried "Real-time" section into its own discoverable page.

### Changed

- **Every `*Settings` docstring now lists its fields.** All 16 settings
  classes in `settings/mixins.py` gained a Google-style `Attributes:`
  section enumerating each field — i.e. the exact environment-variable
  name, its type, purpose, and default — so users no longer have to read
  the source to find which env var to set.

## [0.37.0] — 2026-06-07

### Added

- **`[sqlite]` and `[postgres]` install extras** for the async database
  drivers. The SDK keeps `sqlalchemy[asyncio]` as a core dependency but
  ships **no DBAPI driver by default** — the driver is a deploy choice.
  Install `tempest-fastapi-sdk[sqlite]` (`aiosqlite`, dev default) or
  `[postgres]` (`asyncpg`, production); both are bundled into `[all]`.
  Without one, the engine raised `ModuleNotFoundError` on first
  connection.

### Changed

- **`tempest new` scaffold now ships a working DB driver.** The generated
  `pyproject.toml` pins `aiosqlite` as a **runtime** dependency (it was
  previously dev-only, so the default SQLite URL failed under a no-dev
  install) and carries a commented `asyncpg` line next to it, ready to
  uncomment when switching `DATABASE_URL` to PostgreSQL — matching the
  commented Postgres URL already in `.env.example`.

- **`tempest new` / `tempest generate --docker`: credentials now resolve
  from `.env`, not hardcoded in the compose.** Every `environment:` block
  in the generated `docker-compose.yaml` now uses the `${VAR:-default}`
  form so Docker Compose reads the value from the `.env` next to the
  compose file, keeping secrets out of a VCS-tracked compose. The
  `:-default` preserves zero-config dev boot before `.env.example` is
  copied to `.env`. Affected variables: `POSTGRES_USER` /
  `POSTGRES_PASSWORD` / `POSTGRES_DB`, `RABBITMQ_DEFAULT_USER` /
  `RABBITMQ_DEFAULT_PASS` / `RABBITMQ_DEFAULT_VHOST`, `MINIO_ROOT_USER` /
  `MINIO_ROOT_PASSWORD` (plus the MinIO bootstrap container's `mc alias`).
  `env_block_for` now emits these credential keys into `.env.example` so
  the generated `.env` carries them with their defaults.

## [0.36.0] — 2026-06-06

Admin panel brought to Django-admin parity: the list view, write CRUD,
bulk actions, dashboard, login, and audit trail all landed across the
phases below.

### Added

- **Admin list view — Phase 1 (read-only enhancements + responsive).**
    - Clickable **column sorting** on the list view
      (`?sort=<column>&dir=asc|desc`), validated against the displayed
      real columns; the admin's configured `ordering` remains the
      default.
    - **CSV / JSON export** endpoint
      (`GET /admin/m/{slug}/export.csv` / `.json`) honoring the active
      search / filters / sort. New `make_admin_router(export_max_rows=…)`
      caps export size (default 5000).
    - **Responsive admin UI** — bundled templates + CSS now adapt to
      mobile (≤600px): stacked header, full-width search/filters/actions,
      horizontal-scroll table wrappers, single-column detail grid.
      Verified at 390px (mobile) and 1280px (desktop).

- **Admin write CRUD — Phase 2a (create / edit / delete).**
    - `GET/POST /admin/m/{slug}/new` (create), `GET/POST
      /admin/m/{slug}/{identity}/edit` (edit), and `POST
      /admin/m/{slug}/{identity}/delete` (delete), each gated by new
      `AdminModel(can_create=…, can_edit=…, can_delete=…)` flags
      (default `True`; a disabled view returns `404`).
    - **CSRF-protected** mutations — every write form carries the
      session CSRF token, verified server-side (`403` on mismatch).
    - **Type-aware field widgets** — text / textarea (long strings) /
      number / checkbox / `datetime-local` / date / enum `select`,
      derived from the column types, with required-field + per-field
      validation errors re-rendered on the form, and integrity errors
      surfaced inline.
    - Detail view gains Edit / Delete controls; list view gains a
      "+ New" button. All responsive (verified at 390px / 1280px).

- **Admin bulk actions — Phase 2b.**
    - List view gains row checkboxes + a select-all toggle and a bulk
      action bar. `POST /admin/m/{slug}/bulk` applies **delete**
      (`can_delete`), **activate** / **deactivate** (`can_edit`, toggling
      the `is_active` flag) to the selected rows, CSRF-verified.
      Backed by `BaseRepository.delete_batch` / `bulk_update`.
      Responsive (verified at 390px / 1280px).

- **Admin foreign-key select — Phase 2c.**
    - A foreign-key column whose referenced table has its own
      `AdminModel` now renders as a **dropdown of the related rows**
      (Django's FK select) on the create/edit form, instead of a raw
      UUID input. Option labels come from the referenced admin's first
      `search_fields` entry (falling back to a `name`/`title`/`email`
      attribute, then the id). Capped at 1000 rows. FKs to unmanaged
      tables stay plain UUID inputs.

- **Admin dashboard — counts + metrics (Phase 3a).**
    - The dashboard now renders each registered model as a card with its
      **live row count** and Browse / + New links, plus a **system
      metrics panel** (CPU / RAM / disk) via `MetricsUtils`. The panel
      is on by default, silently omitted when the `[metrics]` extra is
      absent, and disabled with `make_admin_router(show_metrics=False)`.
      Responsive card grids (verified at 390px / 1280px).

- **Admin MFA login — Phase 3b.**
    - The admin login now supports a TOTP second factor. After the
      password step, a principal with MFA enabled gets a short
      `mfa_pending` session and is redirected to `GET/POST /admin/mfa`
      (a CSRF-protected TOTP challenge); only a valid code upgrades the
      session to full access. `AdminAuthBackend` gains `mfa_enabled` /
      `verify_mfa` (default off); `UserModelAuthBackend` implements them
      against `MFAMixin`'s `totp_secret` / `totp_enabled_at` via
      `TOTPHelper` (new `mfa_issuer` / `mfa_window` ctor args). Pending
      sessions are denied every admin page until the challenge passes.

- **Admin audit trail — Phase 3c.**
    - Create/edit through the admin now **stamps** `created_by` /
      `updated_by` (from `AuditMixin`) with the acting admin's id. The
      detail view gained an **Audit panel** showing created/updated
      timestamps and — when the model has the audit columns — the actor,
      with the stored UUID resolved to a display name via the auth
      backend. Models without `AuditMixin` show the timestamps only.

    File-upload widget and inline/related editing remain tracked as later
    admin phases on the roadmap.

## [0.35.0] — 2026-06-06

### Added

- **MFA / TOTP (RFC 6238)** — the bundled auth flow now supports
  two-factor authentication with Authenticator apps. New `[mfa]`
  extra (`pyotp>=2.9.0`). Public surface:
    - `TOTPHelper(issuer=...)` — stateless TOTP issuer/verifier
      (`generate_secret`, `provisioning_uri`, `verify` with a
      configurable clock-drift window). Lazy-imports `pyotp`, so
      `import tempest_fastapi_sdk` still works without the extra.
    - `BaseUserRecoveryCodeModel` + `make_user_recovery_code_model`
      — single-use recovery-code table (stores only the SHA-256
      hash of each code, mirroring `BaseUserTokenModel`).
    - `MFAMixin` — opt-in SQLAlchemy mixin adding `totp_secret` +
      `totp_enabled_at` columns (plus an `is_mfa_active` property).
      Mix it into the concrete user model
      (`class UserModel(MFAMixin, BaseUserModel)`) only when MFA is
      adopted, so projects that never enable it carry no dead
      columns. `totp_enabled_at IS NULL` means MFA is
      staged-but-not-active, so login stays single-step until the
      user confirms.
    - `UserAuthService` MFA methods: `is_mfa_enrolled`,
      `issue_mfa_token`, `mfa_enroll`, `mfa_confirm`, `mfa_verify`,
      `mfa_disable`.
    - `make_auth_router(..., recovery_code_model=...)` mounts four
      endpoints behind the `AUTH_MFA_ENABLED` kill-switch:
      `POST /auth/mfa/enroll`, `/auth/mfa/confirm`,
      `/auth/mfa/disable`, `/auth/mfa/verify`. `POST /auth/login`
      now returns `mfa_required=True` + a short-lived `mfa_token`
      (instead of the JWT pair) for enrolled users; step 2 swaps
      `mfa_token` + code for the real tokens.
    - New schemas: `MFAEnrollResponseSchema`, `MFAConfirmSchema`,
      `MFADisableSchema`, `MFAVerifySchema`. `LoginResponseSchema`
      gains `mfa_required` + `mfa_token` (and `access_token` /
      `refresh_token` are now nullable for the MFA-pending case).
    - New `AuthSettings`: `AUTH_MFA_ENABLED`, `AUTH_MFA_ISSUER`,
      `AUTH_MFA_RECOVERY_CODES_COUNT`, `AUTH_MFA_TOKEN_TTL_SECONDS`,
      `AUTH_MFA_VERIFY_WINDOW`.
- **Optional password complexity** — new
  `AUTH_PASSWORD_REQUIRE_COMPLEXITY` flag (default `False`). When off,
  any password meeting `AUTH_PASSWORD_MIN_LENGTH` is accepted; when on,
  signup + reset additionally require at least one lowercase letter,
  one uppercase letter, one digit, and one special (non-alphanumeric)
  character, and the effective length floor is raised to at least 8
  (a configured `AUTH_PASSWORD_MIN_LENGTH` below 8 is ignored while
  complexity is on). Enforced server-side on both `service.signup` and
  `service.confirm_password_reset`.

### Fixed

- **Password minimum length is now honored end-to-end.**
  `SignupSchema.password` and `PasswordResetConfirmSchema.new_password`
  hardcoded `min_length=12`, which overrode `AUTH_PASSWORD_MIN_LENGTH`
  on the router path — a project lowering the floor still got a 422
  from Pydantic, and raising it above 12 was enforced only by the
  service. The schemas now reject only empty strings;
  `AUTH_PASSWORD_MIN_LENGTH` is the single source of truth (now `ge=1`,
  fully configurable down to 4 or any value, default 12), applied
  server-side.

### Notes

- Enabling MFA requires a migration: mix `MFAMixin` into the concrete
  user model (`class UserModel(MFAMixin, BaseUserModel)`) to add the
  `totp_secret` / `totp_enabled_at` columns, and create the
  recovery-code table. `AUTH_MFA_ENABLED=True` without passing
  `recovery_code_model` to `make_auth_router` raises at
  router-build time.

## [0.34.0] — 2026-06-04

### Added

- **Server-side session module** — new `tempest_fastapi_sdk.sessions`
  package ships a full alternative to the JWT auth flow. Public
  surface:
    - `SessionStore` Protocol + `MemorySessionStore` (dev/tests) +
      `RedisSessionStore` (production). Stores keep sessions by the
      SHA-256 hash of the cookie id; the plaintext only lives in the
      Set-Cookie. Redis store gets TTL eviction for free; both
      stores expose `get` / `set` / `delete` / `delete_by_user` /
      `list_by_user`.
    - `Session` schema — `session_id` (hashed), `user_id`,
      `created_at`, `expires_at`, `last_seen_at`, `ip`,
      `user_agent`, `data` (free-form JSON bag).
    - `SessionAuth` service — authenticates credentials via
      `PasswordUtils` + `UserModel`, mints sessions, slides TTL on
      resolve, rotates on login (anti-fixation), revokes one or all
      sessions per user.
    - `SessionMiddleware` — reads the cookie, populates
      `request.state.session` so handlers never re-resolve.
    - `make_session_dependency(required=...)` — FastAPI dependency
      returning the resolved session or raising
      `UnauthorizedException` when required.
    - `make_session_router(service, session_factory=..., prefix=...)` —
      bundled 5-endpoint router: `POST /auth/session/login`,
      `POST /auth/session/logout`, `GET /auth/session/me`,
      `GET /auth/session/list`, `DELETE /auth/session/{id}`.
    - `SessionLoginSchema`, `SessionResponseSchema`,
      `SessionSummarySchema` — typed DTOs for the router.
- **`SessionSettings` mixin** — `SESSION_TTL_SECONDS`,
  `SESSION_SLIDING`, `SESSION_COOKIE_{NAME,DOMAIN,PATH,SECURE,HTTPONLY,SAMESITE}`,
  `SESSION_ROTATE_ON_LOGIN`. Every field carries
  `title`/`description`/`examples`.

### Documentation

- `docs/recipes/sessions.{md,en.md}` — new bilingual recipe with
  the JWT-vs-session decision table, setup wiring, endpoints,
  store comparison (Memory vs Redis vs custom), middleware
  semantics, security model (hash-at-rest, anti-fixation,
  SameSite, anti-enumeration, instant revocation) and when NOT to
  use sessions.
- `docs/reference.md` — new `tempest_fastapi_sdk.sessions` section
  with `mkdocstrings` entries for `SessionAuth`,
  `make_session_router`, `SessionMiddleware`,
  `make_session_dependency`, `SessionStore` /
  `MemorySessionStore` / `RedisSessionStore`, every schema.
- `mkdocs.yml` — recipe added to the navigation in both languages
  with the matching i18n translation entry.

### Tests

- 16 new cases under `tests/sessions/` covering the
  `MemorySessionStore` lifecycle (set/get/expire/delete/list +
  user-scoped wipe), `SessionAuth` (authenticate / login /
  resolve+slide / rotate / revoke_all + anti-enumeration), and
  router integration (login sets cookie, `me` returns session,
  `me` returns 401 without cookie, logout clears cookie, list
  marks current).

### Migration

- v0.34.0 is purely additive. No public-API breaking change.
  Existing JWT flows keep working untouched; sessions are a
  separate opt-in path.

## [0.33.0] — 2026-06-04

### Added

- **WebSocket router** (``tempest_fastapi_sdk.make_websocket_router`` +
  ``WebSocketHub``). New ``tempest_fastapi_sdk.websockets`` module
  ships the three concerns every WebSocket endpoint has to get right:
    - **Bearer auth at the handshake** via ``?token=<jwt>`` query
      string OR ``Sec-WebSocket-Protocol: bearer,<jwt>`` subprotocol
      (preferred — does not leak to proxy logs). The
      ``bearer_resolver`` callable maps the token to a user UUID;
      ``None`` closes the socket with code ``4401`` before the
      handler runs.
    - **Heartbeat ping/pong** with timeout. The router emits
      ``{"type": "ping"}`` every ``WS_HEARTBEAT_SECONDS`` (default
      ``30``) and closes with code ``4408`` when the matching
      ``{"type": "pong"}`` does not arrive within
      ``WS_HEARTBEAT_TIMEOUT_SECONDS`` (default ``60``).
    - **In-process registry** (``WebSocketHub``) tracking every
      live connection by user UUID + topic subscriptions. Exposes
      ``send_to(user_id, envelope)``, ``broadcast(envelope,
      topic=None)``, ``subscribe`` / ``unsubscribe``, ``online_users()``
      and ``connection_count()`` — usable from any HTTP handler in
      the same FastAPI app. Per-user cap ``WS_MAX_CONNECTIONS_PER_USER``
      (default ``5``) evicts the oldest connection with code ``4429``
      when exceeded. Dead peers are evicted transparently on
      ``send_json`` failure.
- **``WSEnvelope`` schema** — canonical ``{type, data, request_id}``
  envelope for SDK-managed frames (``ping``/``pong``) and the
  recommended shape for application messages.
- **``WebSocketConnection`` dataclass** — public handle returned by
  ``WebSocketHub.register`` so handlers can pass a stable
  ``connection_id`` to ``subscribe`` / ``unsubscribe``.
- **``WebSocketSettings`` mixin** — ``WS_HEARTBEAT_SECONDS``,
  ``WS_HEARTBEAT_TIMEOUT_SECONDS``, ``WS_MAX_CONNECTIONS_PER_USER``,
  ``WS_MAX_MESSAGE_BYTES`` with full ``title``/``description``/
  ``examples`` metadata.

### Documentation

- ``docs/recipes/websocket.{md,en.md}`` — new bilingual recipe
  covering setup, query-vs-subprotocol auth comparison, JavaScript
  client snippet with heartbeat + reconnect, broadcast / send_to /
  topic patterns, every close-code table, settings reference, and
  the single-process vs multi-replica trade-offs.
- ``docs/reference.{md,en.md}`` — new section
  ``tempest_fastapi_sdk.websockets`` with ``mkdocstrings`` entries
  for ``WebSocketHub``, ``WebSocketConnection``,
  ``make_websocket_router``, ``WSEnvelope``.
- ``mkdocs.yml`` — recipe added to the navigation in both languages
  with the matching i18n translation entry.

### Migration

- v0.33.0 is purely additive. No public-API breaking change. The
  new module imports lazily; existing services that don't mount
  the router pay no startup cost.

## [0.32.1] — 2026-06-04

### Changed

- **Top-level `__all__` now re-exports the bundled auth surface.** Adds
  ``UserAuthService``, ``make_auth_router``, ``BaseUserTokenModel``,
  ``UserTokenPurpose``, ``make_user_token_model``, ``AuthSettings``,
  every auth schema (``SignupSchema``/``SignupResponseSchema``/
  ``LoginSchema``/``LoginResponseSchema``/``ActivationToken``/
  ``ActivationResponseSchema``/``PasswordResetToken``/
  ``PasswordResetRequestSchema``/``PasswordResetResponseSchema``/
  ``PasswordResetConfirmSchema``) to the public re-export list. Runtime
  imports already worked; this satisfies strict re-export checkers
  (pyright/basedpyright/Pylance strict) without project-level
  ``pyrightconfig.json``.

### Documentation

- **Full audit + fix pass against the actual SDK code.** Every recipe,
  tutorial section, README block and learning-project example was
  cross-checked against the source; whatever didn't match was rewritten.
  Highlights of what changed:
    - ``docs/tutorial.{md,en.md}`` / ``README.md``: router section now
      calls ``controller.signup`` / ``controller.get_by_id`` / a real
      ``controller.paginate(...)`` invocation — the previous
      ``controller.create`` / ``controller.get`` / ``controller.list_paginated``
      names did not exist on ``BaseController`` and would have raised
      ``AttributeError`` on every endpoint.
    - ``page_size`` consistently replaces the bogus ``size`` query/JSON
      key throughout pagination snippets — the real
      ``BasePaginationFilterSchema`` field is ``page_size`` and the
      ``paginate(...)`` dict returns ``"page_size"``, not ``"size"``.
    - ``BaseUserModel`` examples no longer claim a ``password_hash``
      column — the real column is ``hashed_password`` and the docs now
      construct rows with it.
    - ``docs/recipes/testing.{md,en.md}`` rewritten: ``async with
      TestClient(app)`` (which doesn't work — ``TestClient`` is sync)
      replaced by ``httpx.AsyncClient(transport=ASGITransport(app=app))``;
      ``test_database`` / ``test_session`` / ``create_test_engine``
      signatures and return shapes corrected to match the helpers
      actually shipped under ``tempest_fastapi_sdk.testing``.
    - ``docs/recipes/security.{md,en.md}`` fully rewritten: every claim
      pointed at fictional API (``RedisThrottleBackend``,
      ``MemoryThrottleBackend``, ``ThrottleStatus.LOCKED``,
      ``throttle.check()``, ``throttle.record_failure()``,
      ``.attempts_left``, ``set_cookie(key=..., same_site=SameSite.LAX)``,
      ``get_client_ip(..., trusted_proxies={...})``,
      ``accept_private=False``, an HMAC-pepper claim on
      ``hash_opaque_token(..., secret=...)``, a ``Referrer-Policy`` in
      ``DEFAULT_STATIC_SECURITY_HEADERS``). New recipe documents the
      real ``AttemptThrottle`` / ``ThrottleStatus`` / ``set_cookie`` /
      ``clear_cookie`` / ``get_client_ip`` surface.
    - ``docs/recipes/http.{md,en.md}``: ``JWT_TTL_HOURS`` (does not
      exist) replaced by ``JWT_ACCESS_TTL_SECONDS``;
      ``RSAWebhookSignatureVerifier(encoding="base64",
      hash_algorithm="sha256")`` (also fictional) rewritten to the real
      ``algorithm="sha256"`` kwarg; ``request.client.host or "anon"``
      rewritten to handle ``request.client is None`` safely;
      ``controller.list_paginated`` replaced with a real
      ``controller.paginate(...)`` call.
    - ``docs/recipes/auth-flow.{md,en.md}``: ``SignupResponseSchema``
      example body now matches the real shape
      (``user_id``/``activation_required``/``activation_url``/
      ``access_token``/``refresh_token`` — no fictional ``email`` /
      ``is_active`` fields); ``tempest db init`` prereq is called out
      before ``tempest db revision``; UUID example replaces the bogus
      ULID-style placeholder.
    - ``docs/recipes/uploads.{md,en.md}``: phantom
      ``settings.UPLOAD_BACKEND`` field (it isn't on ``UploadSettings``)
      now has to be declared on the project's own ``Settings`` subclass
      in a copy-pasteable snippet; ``UploadFile.filename`` (typed
      ``str | None``) is now fallen back to ``"upload.bin"`` before
      being passed where ``str`` is required; the ``UploadUtils.__init__``
      mkdir side-effect is explicitly called out.
    - ``docs/recipes/metrics.{md,en.md}`` no longer stops at
      ``MetricsUtils`` — a full Prometheus exposition section was added
      covering ``PrometheusMiddleware`` + ``make_prometheus_registry`` +
      ``make_prometheus_router``, the ``[prometheus]`` extra, scrape
      config, and the rationale for not mounting the JSON snapshot on
      ``/metrics``.
    - ``docs/recipes/queue-tasks.{md,en.md}``: ``NameError`` in the
      outbox dispatcher fixed (``broker``/``queue_broker`` shadowing
      that would crash on import).
    - ``docs/recipes/realtime.{md,en.md}``: missing
      ``StreamingResponse`` import added; producer pattern rewritten to
      cancel on client disconnect (the previous fire-and-forget pattern
      leaked tasks).
    - ``docs/recipes/admin.{md,en.md}``: ``settings.ADMIN_SECRET_KEY``
      (doesn't exist) replaced with the scaffold's ``settings.JWT_SECRET``;
      ``__tablename__ = "user"`` replaced with the scaffold's actual
      ``"users"``.
    - ``docs/recipes/database.{md,en.md}``: ``filters={"deleted_at":
      None}`` (silently skipped — returns deleted rows) replaced with a
      raw ``select(...).where(col.is_(None))`` query; the tuple
      comparison ``(col_a, col_b) > (val_a, val_b)`` (invalid in
      SQLAlchemy) replaced with ``tuple_(col_a, col_b) > tuple_(...)``;
      the cursor example now decodes ``state["value"]`` back to
      ``datetime`` so Postgres tuple comparison doesn't fail on a
      str-vs-timestamp clash; missing ``select`` / ``AsyncSession`` /
      ``Any`` imports added.
    - ``docs/recipes/cli.{md,en.md}``: default
      ``--extras`` value corrected from ``auth`` to the real
      ``auth,admin``; ``--model myapp.models.user:User`` example
      renamed to ``UserModel`` with a comment explaining the
      ``BaseUserModel`` subclass requirement.
    - ``docs/recipes/cache.{md,en.md}``: the previously
      free-floating ``await cache.connect()`` is now shown inside a real
      ``@asynccontextmanager`` lifespan — without it ``cache.client``
      raises ``RuntimeError`` on first use.
    - ``docs/recipes/logging.{md,en.md}``: malformed
      ``"2026-05-16T20:14:33.412+00:00Z"`` timestamp (impossible — the
      formatter strips ``+00:00`` and appends ``Z``) corrected to
      ``"2026-05-16T20:14:33.412Z"``.
    - ``docs/recipes/br-helpers.{md,en.md}``: ``request.json()``
      (a coroutine in FastAPI/Starlette) now ``await``-ed inside an
      ``async def`` handler with the ``Request`` import included.
    - ``docs/recipes/storage.{md,en.md}``: stale "v0.24.0 will introduce
      `S3Backend`" promise replaced with a pointer to the uploads recipe
      where ``MinIOUploadStorage`` already lives.
    - ``docs/architecture.{md,en.md}``: ``paginate(...)`` row in the
      BaseService table now lists ``page_size`` instead of ``size``;
      a note next to ``UserController(UserService(UserRepository(session)))``
      explains the required ``BaseRepository`` subclass with
      ``model=UserModel``.
    - ``docs/installation.{md,en.md}``: version pins bumped from
      ``>=0.19.0`` to ``>=0.32.0``; ``tempest user create`` example now
      passes the required ``--email`` flag instead of relying on a
      non-existent prompt.
    - ``README.md``: SDK version pin in the pyproject snippet bumped
      from ``>=0.13.1`` to ``>=0.32.0``; all the same router /
      pagination / model-field fixes applied.

### Migration

- Zero breaking changes — this is a pure documentation + re-export
  audit on top of the v0.32.0 surface.

## [0.32.0] — 2026-06-04

### Added

- **Backend-only auth mode** (``AuthSettings.AUTH_BACKEND_LINKS=True``).
  When enabled, ``make_auth_router`` mounts three extra HTML endpoints
  on top of the JSON ones already exposed — a project can run the
  full signup → activate → reset cycle without any frontend route
  handling tokens. New endpoints:
    - ``GET /auth/activate/{token}`` — consumes the activation token
      and renders an HTML success page (or an error page on
      bad / expired / used tokens).
    - ``GET /auth/password-reset/{token}`` — peeks the token (does
      NOT consume it) and renders an HTML form with the new-password
      input + confirmation.
    - ``POST /auth/password-reset/{token}`` (``application/x-www-form-urlencoded``)
      — processes the form, validates the password floor, confirms
      the reset, and renders success or error HTML.
- **Five new bundled Jinja2 templates** under
  ``tempest_fastapi_sdk/auth/templates``:
  ``activation_success.html``, ``activation_error.html``,
  ``password_reset_form.html``, ``password_reset_success.html``,
  ``password_reset_error.html``. All responsive, inline-styled,
  mobile-friendly. Shadow them by dropping same-named files into the
  ``template_dir`` you pass to ``make_auth_router``.
- **``make_auth_router(template_dir=...)``** parameter — point the
  router at a project-owned directory whose templates override the
  bundled defaults. Only consulted when ``AUTH_BACKEND_LINKS=True``.
- **``UserAuthService.peek_token(session, token, purpose)``** — new
  service method that validates a token and returns the
  ``(token_record, user)`` pair **without** marking ``used_at``. Used
  by ``GET /auth/password-reset/{token}`` to render the form before
  the user actually submits.
- **``AuthSettings`` gains six fields** documenting the new flow:
    - ``AUTH_BACKEND_LINKS: bool`` (default ``False``)
    - ``AUTH_LOGIN_URL: str | None`` (default ``None``) — URL for the
      "Go to login" button rendered on success/error pages
    - ``AUTH_ACTIVATION_SUCCESS_TEMPLATE`` /
      ``AUTH_ACTIVATION_ERROR_TEMPLATE``
    - ``AUTH_PASSWORD_RESET_FORM_TEMPLATE`` /
      ``AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE`` /
      ``AUTH_PASSWORD_RESET_ERROR_TEMPLATE``
- **``tempest_fastapi_sdk.auth.page_renderer.render_auth_page``** —
  standalone Jinja2 renderer reused by the router; doesn't require
  ``EmailUtils`` to be wired (only the ``[email]`` extra for Jinja2
  itself).

### Changed

- ``make_auth_router`` signature now accepts the optional
  ``template_dir: str | None = None`` keyword. Existing call sites
  remain source-compatible.
- All JSON endpoints (``POST /auth/signup``,
  ``POST /auth/activate/{token}``, ``POST /auth/login``,
  ``POST /auth/password-reset/request``,
  ``POST /auth/password-reset/confirm``) stay mounted exactly as in
  v0.31.x — Backend-only Mode E is purely additive.

### Documentation

- ``docs/recipes/auth-flow.{md,en.md}`` gains the new **Mode E
  (backend-only)** section: ``.env`` block, Mermaid sequence
  diagram of the activation flow, bundled-template reference table,
  override walkthrough, trade-offs callout (zero frontend dep, no
  JWT auto-delivery, requires ``[email]`` extra). The "Four operating
  modes" section was renamed to "Five operating modes" and the TOC
  entry updated.

### Migration

- v0.32.0 has **no breaking changes**. To opt into backend-only
  mode, flip ``AUTH_BACKEND_LINKS=True`` in the ``.env`` and update
  ``AUTH_ACTIVATION_URL_TEMPLATE`` / ``AUTH_PASSWORD_RESET_URL_TEMPLATE``
  to point at your backend instead of your frontend. Everything else
  is wired automatically.

## [0.31.4] — 2026-06-04

### Changed

- **Explicit re-exports across `settings/`, `auth/`, and `db/` `__init__.py`.**
  Every symbol is now re-exported using the PEP 484
  ``from x import Y as Y`` form **in addition to** ``__all__``.
  Reason: third-party consumers run a mixed bag of type-checkers
  (mypy, pyright, pylance, basedpyright) at different strictness
  levels and without project-aware ``pyrightconfig.json``. Either
  form alone is theoretically PEP 484 compliant, but basedpyright
  and Pylance strict still flag bare ``from foo import Bar`` inside
  ``__init__.py`` as "private import usage" unless the symbol is
  aliased with ``as Bar``. Pairing the two patterns silences every
  IDE with no project-level config required. No behavior change —
  same runtime imports, same public surface, same wheel contents.

### Documentation

- **Auth-flow recipe rewritten end-to-end** (`docs/recipes/auth-flow.{md,en.md}`):
    - New table of contents at the top.
    - New "Email anatomy" section disambiguating the three concepts
      that confused readers (opaque token vs URL template vs Jinja2
      template) with a Mermaid sequence diagram of the full flow.
    - "Operating modes" expanded from three to **four** explicit
      modes (A. production / B. dev with local SMTP / C. dev without
      SMTP / D. CI), each with a copy-paste `.env` block.
    - New **"Mailhog vs smtp4dev"** comparison table + ready-to-use
      `docker-compose.yaml` snippets for both containers — the
      recipe now covers local SMTP interception out of the box.
    - "Customizing templates" rewritten with clearer prose, the full
      context-variable table, and a copy-paste minimal
      `emails/activation.html` example.
- ``CLAUDE.md`` gains a new "Explicit re-exports in every
  ``__init__.py``" rule documenting the dual ``as Y`` + ``__all__``
  pattern and flagging bare re-exports as a structural defect.

## [0.31.3] — 2026-06-04

### Documentation

- **Comprehensive documentation refresh** to reflect the v0.23.0 → v0.31.2
  surface. No code change.
- ``README.md``: extras table now lists ``[http]`` + ``[prometheus]``;
  module map covers ``BaseUserTokenModel``, ``UserTokenPurpose``,
  ``BASE_COLUMN_ORDER``, ``reorder_base_columns_first``, ``compose_hooks``,
  ``AuthSettings``, ``tempest_fastapi_sdk.auth``, ``utils.http_client``,
  ``utils.storage_backends``. Roadmap section rewritten with every shipped
  release v0.23.0 → v0.31.2.
- ``docs/index.{md,en.md}``: module map updated with the new exports
  (``auth``, ``storage``, ``IdempotencyMiddleware``,
  ``BodySizeLimitMiddleware``, ``CSRFMiddleware``, ``PrometheusMiddleware``,
  OAuth clients, ``HTTPClient``, bulk repo ops). Hero paragraph rewritten
  to mention the new layers.
- ``docs/installation.{md,en.md}``: extras table now lists ``[minio]``,
  ``[http]``, ``[prometheus]``; CLI section documents ``tempest generate``,
  ``tempest db <subcommand>``, ``tempest user <subcommand>``.
- ``docs/tutorial.{md,en.md}``: new "Auth flow already ships" admonition
  pointing readers at the bundled ``UserAuthService`` + ``make_auth_router``
  shortcut.
- ``docs/recipes/auth-flow.{md,en.md}`` (new): full PT-BR + EN-US recipe
  covering the bundled signup / activate / login / password-reset flow —
  ``UserTokenModel`` concretization, settings flags, the three operating
  modes (production, dev without SMTP, CI tests), template overrides,
  security guarantees.
- ``docs/roadmap.{md,en.md}``: full rewrite with Tier S / A / B status
  tables (Status + Where columns), every release detailed v0.23.0 →
  v0.31.2, "What's next" section for v0.32.0+ (OpenTelemetry) and
  v0.33.0+ (outbox).
- ``docs/reference.{md,en.md}``: ``mkdocstrings`` entries added for
  ``BodySizeLimitMiddleware``, ``CSRFMiddleware``, OAuth clients,
  ``PrometheusMiddleware`` + ``make_prometheus_router``, ``HTTPClient``
  + ``RetryPolicy`` + ``CircuitOpenError``, the full
  ``tempest_fastapi_sdk.auth`` module (service, router, schemas),
  ``reorder_base_columns_first``, ``compose_hooks``.
- ``docs/learning/marketplace/index.{md,en.md}``: stack table now points
  at ``UserAuthService`` + ``make_auth_router`` as the default auth path,
  ``BaseRepository.bulk_create_values`` / ``bulk_upsert`` for stock seed,
  ``PrometheusMiddleware``, ``BodySizeLimitMiddleware``, OAuth clients,
  ``CSRFMiddleware``, ``HTTPClient``. Implementation order's step 1
  rewritten to use the bundled auth recipe.
- ``mkdocs.yml``: ``Auth flow (signup/reset)`` entry added to nav + i18n
  translation map.
- ``CLAUDE.md``: "What the SDK currently covers" section rewritten as a
  structured category bullet list (Auth, DB, Observability, HTTP layer,
  Pagination, Settings, SSE, Throttle, Upload, MinIO/S3, Email, WebPush,
  Cache, Queue/tasks, BR validators, Admin panel, CLI).

## [0.31.2] — 2026-06-04

### Changed

- **``UserAuthService`` method signatures now type ``session``
  as ``sqlalchemy.ext.asyncio.AsyncSession``** instead of
  ``Any``. All seven public methods (``signup``, ``activate``,
  ``login``, ``request_password_reset``,
  ``confirm_password_reset``, ``_issue_token``, ``_consume_token``)
  declare the real type so mypy + IDE autocomplete can flag
  wrong shapes at the call site. No behavior change — only the
  annotations tightened. Aligned with the v0.25.1
  "avoid ``Any`` in SDK code" rule that the auth module had
  drifted away from when it landed in v0.31.0.

## [0.31.1] — 2026-06-04

### Changed

- **``ActivationToken`` and ``PasswordResetToken`` are now
  Pydantic ``BaseSchema`` subclasses** instead of ``dataclass``
  instances. Keeps the auth module aligned with the SDK's
  gold-standard DTO convention — every "thing returned to the
  caller" is a Pydantic schema with full
  ``title``/``description``/``examples`` metadata. The fields
  are the same; the constructor signature is the same. Callers
  that destructure via attribute access (``activation.token``,
  ``activation.url``) keep working unchanged.
- Both token schemas moved from ``tempest_fastapi_sdk.auth.service``
  to ``tempest_fastapi_sdk.auth.schemas`` — re-exports at the
  package root are unchanged, so existing imports
  (``from tempest_fastapi_sdk import ActivationToken``) keep
  resolving.
- **Every auth schema now carries a thorough class-level
  docstring** describing the flow that uses it, the meaning of
  each attribute, and the security / behavior contract (e.g.
  why ``PasswordResetResponseSchema.message`` is always
  identical, why ``ActivationToken.token`` is only handed back
  once).

## [0.31.0] — 2026-06-04

### Added

- **Bundled auth flow** — new ``tempest_fastapi_sdk.auth`` module
  ships service + router + schemas + templates so signup,
  activation, login, and password reset land end-to-end with a
  single ``include_router`` call:

  - ``UserAuthService`` — generic over the concrete ``UserModel``
    + ``UserTokenModel``. Methods: ``signup``, ``activate``,
    ``login``, ``request_password_reset``,
    ``confirm_password_reset``, ``issue_jwt_pair``. Every method
    accepts the active ``AsyncSession`` so the caller controls
    transaction boundaries.
  - ``make_auth_router(service, session_factory=…)`` mounts
    ``POST /auth/signup``, ``POST /auth/activate/{token}``,
    ``POST /auth/login``,
    ``POST /auth/password-reset/request``,
    ``POST /auth/password-reset/confirm``.
  - DTOs: ``SignupSchema`` / ``SignupResponseSchema``,
    ``LoginSchema`` / ``LoginResponseSchema``,
    ``ActivationResponseSchema``,
    ``PasswordResetRequestSchema`` /
    ``PasswordResetResponseSchema``,
    ``PasswordResetConfirmSchema``. Every field carries
    ``title`` / ``description`` / ``examples`` per the SDK
    convention.

- **``BaseUserTokenModel``** (abstract) for one-shot activation /
  reset tokens, plus ``make_user_token_model(user_table, …)`` for
  test fixtures. The plaintext token is returned exactly once;
  only the SHA-256 hash is persisted (via existing
  ``generate_opaque_token`` / ``hash_opaque_token`` helpers).
  Tokens carry a ``purpose`` (``UserTokenPurpose`` StrEnum:
  ``activation`` / ``password_reset`` / ``email_verification``)
  + ``expires_at`` + ``used_at``.

- **``AuthSettings`` mixin** exposing every knob the bundled flow
  needs:

  - ``AUTH_AUTO_ACTIVATE`` — skip activation email entirely
    (dev / CI mode); user is born active and the signup
    response carries JWTs immediately.
  - ``AUTH_RETURN_TOKEN_IN_RESPONSE`` — surface the activation /
    reset link in the JSON body instead of (or in addition to)
    sending the email. Toggles automatically when
    ``EmailUtils`` isn't wired.
  - ``AUTH_ACTIVATION_TTL_SECONDS`` (default 7d) /
    ``AUTH_PASSWORD_RESET_TTL_SECONDS`` (default 1h).
  - ``AUTH_ACTIVATION_URL_TEMPLATE`` /
    ``AUTH_PASSWORD_RESET_URL_TEMPLATE`` — front-end URL
    skeleton with ``{token}`` placeholder.
  - ``AUTH_ACTIVATION_TEMPLATE`` /
    ``AUTH_PASSWORD_RESET_TEMPLATE`` — Jinja2 template names.
  - ``AUTH_PASSWORD_MIN_LENGTH`` (default 12).

- **Default email templates** bundled under
  ``tempest_fastapi_sdk/auth/templates/activation.html`` and
  ``password_reset.html``. ``EmailUtils.render_template`` falls
  back to the SDK directory when the caller-supplied
  ``template_dir`` doesn't ship one with the same name, so the
  bundled flow renders out of the box. Override by placing a
  matching file in the project's template directory.

### Changed

- ``[email]`` extra now pulls ``email-validator`` so the
  Pydantic ``EmailStr`` fields used by ``SignupSchema`` / login
  / reset DTOs validate without a separate dependency.
- ``EmailUtils.render_template`` now accepts callers without an
  explicit ``template_dir`` — the SDK's bundled auth templates
  are reachable by default.

### Security

- Password-reset request endpoint always returns 202 and a
  generic message. Probing emails can no longer enumerate
  account existence.
- Activation + reset tokens are stored hashed (SHA-256, 48
  bytes of entropy on the plaintext). One-shot — ``used_at``
  prevents replay. TTL-bounded.

## [0.30.3] — 2026-06-04

### Fixed

- **Noisy lint output after ``tempest db revision``.** The
  post-write hooks ran in the wrong order — ``ruff_fix`` first,
  ``ruff_format`` second — so the linter loudly complained about
  ``W291`` (trailing whitespace in the docstring header) and
  ``E501`` (long ``sa.Column`` lines) that the formatter would
  fix on the very next hook. The final file was correct but
  stdout looked like the revision failed. Two adjustments:

  - Hooks reordered to ``ruff_format, ruff_fix`` so the formatter
    wraps lines + strips whitespace **before** the linter sees
    them.
  - Both hooks pass ``--quiet`` so the "found N errors / N fixed
    / M remaining" preamble is suppressed when nothing actionable
    is left.

  Existing projects: re-run ``tempest db init`` against an empty
  ``alembic.ini`` to regenerate, or hand-edit
  ``[post_write_hooks]`` to match the new layout.

## [0.30.2] — 2026-06-04

### Security

- **``alembic.ini`` no longer stamps the database URL.** The
  generated ini ships with ``sqlalchemy.url = `` empty so
  credentials never enter version control. Both the SDK
  ``env.py`` template and ``AlembicHelper.config`` resolve the
  URL at runtime:

  1. ``db_url`` passed to the constructor or via
     ``--database-url`` on the CLI.
  2. ``DATABASE_URL`` env var (loaded from ``.env``).
  3. ``src.core.settings.settings.DATABASE_URL`` in scaffolded
     projects.

  When none of the three is set the env.py raises
  ``RuntimeError("DATABASE_URL is empty. Set it on the
  environment, in src/core/settings.py, or pass --database-url
  to the CLI.")`` so missing config fails loudly instead of
  silently connecting to whatever was left on the ini.

### Migration for existing projects

Open ``alembic.ini`` and blank the ``sqlalchemy.url`` line:

```ini
sqlalchemy.url =
```

Then rotate the leaked credential at the database (assume the
secret is compromised the moment it landed in a Git commit).
Append ``alembic.ini`` to a code-search / CI hook so the line
stays empty across future PRs.

If your ``alembic/env.py`` was older than v0.21.x and does not
import ``tempest_fastapi_sdk.db.alembic_hooks``, rerun
``tempest db init`` against an empty file to regenerate it, or
copy the new template from
``tempest_fastapi_sdk/db/_alembic_templates/env.py.template``.

## [0.30.1] — 2026-06-04

### Added

- **``reorder_base_columns_first`` Alembic hook** in
  ``tempest_fastapi_sdk.db.alembic_hooks``. Wired into the
  scaffolded ``env.py`` so every autogenerated migration emits
  ``id`` → ``is_active`` → ``created_at`` → ``updated_at`` at the
  top of every ``op.create_table``, followed by the table's own
  constraints + subclass columns in their original relative
  order. The 4 ``BaseModel`` columns now ship in the documented
  order without manual editing.
- **``compose_hooks(*hooks)``** helper for chaining multiple
  ``process_revision_directives`` callables.
- **``BASE_COLUMN_ORDER``** tuple re-exported at the package
  root for tools that want to mirror the convention elsewhere.

### Docs

- Existing projects: copy the new ``env.py`` snippet from
  ``tempest_fastapi_sdk/db/_alembic_templates/env.py.template``
  (the ``process_revision_directives=reorder_base_columns_first``
  argument is added to both ``context.configure`` calls), or
  re-run ``tempest db init`` against an empty ``alembic.ini``
  to regenerate. Future ``tempest db revision --autogenerate``
  picks up the hook automatically.

## [0.30.0] — 2026-06-04

### Added

- **`tempest db` subcommand group** — Alembic wrapper backed by
  the existing ``AlembicHelper``. Commands:

  - ``tempest db init`` — scaffold ``alembic.ini`` + ``alembic/env.py``.
  - ``tempest db revision -m "<msg>" [--manual]`` — create a new
    migration (autogenerate by default).
  - ``tempest db upgrade [target]`` — apply pending migrations
    (``head`` by default).
  - ``tempest db downgrade [target]`` — roll back (default
    ``-1``, i.e. one step).
  - ``tempest db current`` — print the applied revision.
  - ``tempest db history [-v]`` — list revisions newest → oldest.

  ``DATABASE_URL`` resolves in this order: ``--database-url`` flag
  → ``DATABASE_URL`` env var →
  ``src.core.settings.settings.DATABASE_URL`` →
  ``sqlalchemy.url`` from ``alembic.ini``. The async driver
  suffix is stripped automatically before Alembic runs.

- **`tempest user` subcommand group** — seed and inspect users
  via the project's concrete ``UserModel`` (default
  ``src.db.models:UserModel``, overridable with ``--model``).
  Bootstraps the first admin row so ``/admin`` login works
  without manual SQL.

  - ``tempest user create --email X --password Y [--admin]``
    — insert one user. Omitting ``--password`` reads it
    interactively (no shell history leak). Password ≥ 8 chars
    enforced.
  - ``tempest user list [--admin]`` — print
    ``id  email  +admin/...  active/inactive`` per row.

### Docs

- ``docs/recipes/cli{,.en}.md`` adds full sections for
  ``tempest db`` and ``tempest user`` with flag reference + the
  ``DATABASE_URL`` resolution order.
- ``docs/learning/marketplace/index{,.en}.md`` setup block now
  runs ``tempest db revision`` + ``tempest db upgrade`` +
  ``tempest user create --admin`` between the docker compose up
  and the ``uv run python main.py`` so ``/admin`` login works on
  first run.
- ``README.md`` Command-line interface recipe grows the same
  two sections.

## [0.29.1] — 2026-06-04

### Fixed

- **Scaffold no longer ships an empty ``user`` table** — the
  scaffolded ``src/db/models/__init__.py`` was empty, so
  Alembic's ``--autogenerate`` found no models in
  ``BaseModel.metadata`` and never emitted the ``user`` table.
  The result: ``/admin`` login failed because the table didn't
  exist. The fix:

  - New ``src/db/models/user.py.tmpl`` ships a concrete
    ``UserModel(BaseUserModel)`` mapped to the ``users`` table.
  - ``src/db/models/__init__.py.tmpl`` re-exports ``BaseModel``
    + ``UserModel`` so Alembic sees the metadata.
  - ``src/api/app.py.tmpl`` now wires ``AdminSite`` +
    ``AdminModel(UserModel)`` + ``UserModelAuthBackend`` +
    ``make_admin_router`` out of the box.
  - ``tempest new`` default extras bumped from ``auth`` to
    ``auth,admin`` so the admin wiring boots without a manual
    extras tweak.

  Upgrade path for an already-scaffolded project: copy the new
  ``UserModel`` definition into ``src/db/models/user.py``,
  re-export from ``src/db/models/__init__.py``, then run
  ``uv run alembic revision --autogenerate -m "user table"``
  followed by ``uv run alembic upgrade head``.

## [0.29.0] — 2026-06-04

### Fixed

- **Postgres 18 mount path.** v0.26.0 bumped the pinned image to
  ``postgres:18-alpine`` but kept the historical
  ``postgres-data:/var/lib/postgresql/data`` mount. Postgres 18+
  reorganized the data layout — the image now refuses to start
  with ``Error: in 18+, these Docker images are configured to
  store database data in a format which is compatible with
  "pg_ctlcluster" (...) Counter to that, there appears to be
  PostgreSQL data in: /var/lib/postgresql/data (unused
  mount/volume)``. The generator now emits
  ``postgres-data:/var/lib/postgresql`` (no ``/data`` suffix);
  Postgres creates the version-specific subdirectory inside.

  Upgrade path for existing projects:

  ```bash
  docker compose down -v          # WIPES local data — back up first
  tempest generate --docker --force
  docker compose up -d
  ```

### Added

- **``CSRFMiddleware`` + ``make_csrf_token_dependency``** — full
  double-submit-cookie CSRF guard for cookie-authenticated
  endpoints. Unsafe verbs (``POST`` / ``PUT`` / ``PATCH`` /
  ``DELETE``) must carry both the ``csrf_token`` cookie and a
  matching ``X-CSRF-Token`` header; mismatch returns 403 with
  the SDK envelope ``{"code": "CSRF_VALIDATION_FAILED"}``.

  Safe methods always pass. ``exclude_paths`` lets bearer-auth
  ``/api/`` routes skip the check (JWT bearer is not subject to
  CSRF since the browser doesn't auto-attach it).

  ``generate_csrf_token(n_bytes=32)`` mints fresh tokens;
  ``make_csrf_token_dependency()`` returns a FastAPI dependency
  that the login/template endpoint can call to seed the cookie.

- **OAuth2 / OIDC providers** under ``tempest_fastapi_sdk.api.oauth``:

  - ``GoogleOAuthClient`` — Google identity, OIDC-compatible,
    default scopes ``openid email profile``.
  - ``GitHubOAuthClient`` — GitHub OAuth (not OIDC; user info
    via ``GET /user``), default scopes ``read:user user:email``.
  - ``OIDCProvider`` — generic discovery-driven OIDC client for
    Auth0 / Keycloak / Okta / Microsoft Entra / Cognito. Pass
    the authorize / token / userinfo URLs explicitly.

  All providers share the same surface — ``build_authorize_url(state, **extra)``,
  ``exchange_code(code) -> OAuthTokens``, ``fetch_user(tokens) -> OAuthUser``.
  Identity is normalized to ``OAuthUser(provider, subject, email,
  name, picture, raw)`` so the application sees one shape
  regardless of IdP. CSRF-grade state via ``generate_oauth_state()``.

  Built on the v0.28.0 ``HTTPClient`` for retries + circuit-breaker
  on the IdP — handy when Auth0 / Google occasionally hiccup.
  Requires the ``[http]`` extra.

## [0.28.0] — 2026-06-04

### Added

- **Prometheus ``/metrics`` endpoint + middleware.** New
  ``tempest_fastapi_sdk.api.routers.metrics`` module exposes:

  - ``PrometheusMiddleware`` — tracks
    ``http_requests_total{method, path, status}`` (Counter),
    ``http_request_duration_seconds{method, path}`` (Histogram),
    ``http_requests_in_progress{method}`` (Gauge). Uses the
    matched route template as the ``path`` label so cardinality
    stays bounded.
  - ``make_prometheus_registry()`` — fresh ``CollectorRegistry``
    decoupled from the default singleton.
  - ``make_prometheus_router(registry=…, path="/metrics",
    dependencies=…)`` — ``GET /metrics`` rendering the exposition
    format. Pair with ``Depends(require_x_token)`` in production.

  Requires the new ``[prometheus]`` extra (``prometheus-client``).

- **``HTTPClient`` — typed httpx wrapper** at
  ``tempest_fastapi_sdk.utils.http_client``:

  - Bounded retries with exponential backoff
    (``RetryPolicy(max_attempts, backoff_initial_seconds,
    backoff_max_seconds, retry_statuses)``); retries on network
    errors + configurable 5xx/429.
  - Per-host circuit breaker — trips after ``failure_threshold``
    consecutive failures, half-open after
    ``recovery_seconds``; raises ``CircuitOpenError`` while open.
  - ``X-Request-ID`` propagation from the
    ``request_id_ctx`` contextvar to outbound requests so
    correlation flows downstream.
  - Verb-level conveniences (``get``/``post``/``put``/``patch``/
    ``delete``) on top of the unified ``request()`` core.

  Requires the new ``[http]`` extra (``httpx``).

- **``BodySizeLimitMiddleware``** — short-circuits oversized
  requests at the ASGI layer:

  - Header check on ``Content-Length`` (fast path).
  - Streaming check for chunked / unknown-length bodies.
  - ``exclude_paths`` lets specific routes (e.g. media uploads)
    opt out and enforce their own per-endpoint limit.
  - Responds ``413`` with the SDK envelope
    ``{"code": "REQUEST_BODY_TOO_LARGE", "details": {"max_bytes": …}}``.

- **``BaseRepository.bulk_create_values(rows)``** — single
  ``INSERT … VALUES (…), (…)`` round-trip for batch persistence
  without unit-of-work overhead.

- **``BaseRepository.bulk_upsert(rows, conflict_columns,
  update_columns=None)``** — dialect-aware
  ``INSERT … ON CONFLICT DO UPDATE``. Picks Postgres or SQLite
  syntax automatically; raises ``NotImplementedError`` on other
  dialects so the caller can fall back to a
  ``SELECT FOR UPDATE`` loop.

### Changed

- ``[all]`` extra now includes ``httpx`` and
  ``prometheus-client``.

## [0.27.0] — 2026-06-04

### Added

- **New documentation section: Learning Projects** (PT-BR + EN-US).
  Didactic projects built end-to-end on the SDK so users can learn
  how the primitives compose in a realistic scenario.

- **First learning project: 🛒 Marketplace** — Mercado Livre /
  Shopee–style multi-tenant sales platform without external
  integrations. Covers the full SDK stack:

  - **Business rules** — every domain invariant numbered (U-01…
    G-04) with rationale. 41 rules across 10 sections.
  - **Domain model** — UML class diagram, ER diagram, enum
    diagrams (Mermaid), per-entity invariant table, and entity →
    SDK primitive mapping.
  - **Critical flows** — sequence diagrams for the 5 trickiest
    flows (signup, member invitation, product creation with
    images, idempotent checkout, shipping with SSE), plus state
    machines for ``Order`` and ``Invitation``.
  - **Endpoint map** — full REST surface as a table (method +
    path + auth role + idempotency + status + description).

  Exercises: ``BaseUserModel``, ``PasswordUtils``, ``JWTUtils``,
  ``make_jwt_user_dependency``, ``make_role_dependency``,
  ``BaseRepository[T]``, ``generate_opaque_token``,
  ``EmailUtils.render_template``, ``UploadUtils`` +
  ``MinIOUploadStorage``, ``IdempotencyMiddleware``,
  ``EventStream`` / ``sse_response``, ``AsyncTaskBrokerManager``,
  ``AsyncBrokerManager``, ``AsyncRedisManager`` + ``@cached``,
  ``MetricsUtils``, ``register_exception_handlers`` + the
  ``AppException`` hierarchy, ``configure_logging`` +
  ``make_logs_router``.

### Docs

- ``docs/learning/index{,.en}.md`` — section index with the
  catalog of learning projects (Marketplace shipped; library,
  scheduling, recurring billing planned).
- ``docs/learning/marketplace/{index,business-rules,domain,flows,api}{,.en}.md``
  — 10 new bilingual pages.
- ``mkdocs.yml`` adds the section to PT nav and the i18n
  ``nav_translations`` block (now 31 navigation elements,
  was 23).

## [0.26.0] — 2026-05-31

### Added

- **`tempest generate --docker`** — regenerate
  ``docker-compose.yaml`` (and refresh the ``.env.example`` service
  block) in an existing project. Reads the project's
  ``pyproject.toml`` to discover the currently pinned SDK extras
  unless ``--extras`` is given explicitly. Refuses to overwrite a
  hand-edited compose file without ``--force``. The ``.env.example``
  addendum is idempotent — re-running the command does not
  duplicate the service blocks.

  Flags:

  - ``--docker`` — selects the compose generator.
  - ``--path / -p`` — project root (default: cwd).
  - ``--extras`` — override discovered extras.
  - ``--name`` — override the container-name prefix.
  - ``--force / -f`` — overwrite existing compose file.

- **All Pydantic schemas and settings mixins now ship
  ``title`` + ``description`` + ``examples`` metadata** on every
  field. JSON-Schema consumers (FastAPI ``/docs``, ``/redoc``,
  IDE tooling, ``pydantic.model_json_schema()``) render rich
  metadata out of the box; OpenAPI examples populate the
  Swagger UI examples picker without further configuration.

  Surfaces covered:

  - ``settings.mixins`` — every ``*Settings`` mixin
    (``ServerSettings``, ``LogSettings``, ``DatabaseSettings``,
    ``RedisSettings``, ``RabbitMQSettings``, ``JWTSettings``,
    ``CORSSettings``, ``EmailSettings``, ``UploadSettings``,
    ``TokenSettings``, ``WebPushSettings``, ``TaskIQSettings``,
    ``MinIOSettings``).
  - ``schemas.pagination`` — ``BasePaginationFilterSchema``,
    ``BasePaginationSchema``, ``CursorPaginationFilterSchema``,
    ``CursorPaginationSchema``.
  - ``schemas.response`` — ``BaseResponseSchema``.
  - ``schemas.logs`` — ``LogEntrySchema``.
  - ``webpush.schemas`` — ``WebPushKeysSchema``,
    ``WebPushSubscriptionSchema``, ``WebPushPayloadSchema``.

### Changed

- **`docker-compose.yaml` image tags bumped** to the current
  major releases on Docker Hub:

  - ``postgres:16-alpine`` → ``postgres:18-alpine``. Postgres 14+
    has used ``scram-sha-256`` by default; no client-side change
    required.
  - ``redis:7-alpine`` → ``redis:8-alpine``. Note Redis 8.0+
    ships under a tri-license (RSALv2 / SSPLv1 / AGPLv3); the
    earlier ``<=7.2.4`` line was 3-Clause BSD. Internal use is
    unaffected; redistribution may need to pick a compatible
    license tier.
  - ``rabbitmq:3-management-alpine`` →
    ``rabbitmq:4-management-alpine``. ``RABBITMQ_DEFAULT_USER`` /
    ``RABBITMQ_DEFAULT_PASS`` remain functional;
    ``RABBITMQ_DEFAULT_VHOST=/`` made explicit in the rendered
    compose.

  Per-service tightening:

  - ``redis`` now boots with ``--appendonly yes`` so dev data
    survives container restarts; ``start_period: 5s`` lets the
    healthcheck wait for the AOF rewrite path.
  - ``rabbitmq`` healthcheck uses ``rabbitmq-diagnostics -q ping``
    (quiet) with ``start_period: 30s`` to absorb the broker's
    cold boot.
  - ``postgres`` healthcheck gains ``start_period: 10s``.

## [0.25.1] — 2026-05-31

### Changed

- **Tighter typing across the SDK's public surface — `Any` removed
  from most signatures.** The recent backend/protocol additions
  landed with `Any` in places where a concrete type or
  `Protocol` is just as ergonomic:

  - `UploadStorage.write_stream(..., validator=…)` and
    `UploadUtils.save(..., storage=…)` now accept the explicit
    `ContentValidator = Callable[[bytes], bool]` and
    `UploadStorage | None` types. Mypy and IDEs can now flag
    wrong shapes instead of waving them through.
  - `RedisIdempotencyStore(client=…)` takes a new `_RedisLike`
    `Protocol` (async `get(key)` / `set(key, value, ex)`) so the
    cache is decoupled from `redis-py` for type-checking while
    still accepting any compatible client.
  - `make_app_exception_handler` / `make_http_exception_handler`
    / `make_unhandled_exception_handler` return typed
    `Callable[[Request, ExcT], Awaitable[JSONResponse]]` aliases
    (`AppExceptionHandler`, `HTTPExceptionHandler`,
    `UnhandledExceptionHandler`).
  - `AsyncMinIOClient.__aexit__` and `ObjectStat.raw` annotate
    their real types (`TracebackType | None`,
    `minio.datatypes.Object`) via `TYPE_CHECKING` imports.
  - `EmailUtils._jinja_env`, `_aiofiles`, `_aiosmtplib` use
    `ModuleType | None` / `jinja2.Environment | None` instead of
    `Any`.

  The behavior is unchanged — only the types tightened, so
  callers see better autocomplete and downstream refactors stay
  honest.

### Removed

- `tempest_fastapi_sdk.utils.storage_backends._stream_upload_file`
  helper (was private, unused).

## [0.25.0] — 2026-05-31

### Added

- **`tempest new` now generates a `docker-compose.yaml`** wired
  with only the supporting infrastructure the picked extras
  require. The mapping:

  - `[cache]` → Redis 7 (alpine)
  - `[queue]` or `[tasks]` → RabbitMQ 3 with the management UI
    exposed at `http://localhost:15672`
  - `[minio]` → MinIO + a one-shot bootstrap container that
    creates the `uploads` bucket
  - `[email]` → MailHog (catches outbound SMTP, UI at
    `http://localhost:8025`)

  Postgres is always wired (the SDK's DB primitives are core), so
  every scaffolded project gets a one-command path to a real
  database via `docker compose up -d`. The scaffolded `.env` keeps
  SQLite as the default URL so the smoke run works without Docker.

- **`.env.example` gains a service-aware addendum** matching the
  same extras → environment variables. Picking `--extras cache,minio`
  writes `REDIS_URL`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY` (etc.)
  into `.env.example` so the developer can copy the file straight
  to `.env` and the service connects to the compose-spawned
  containers without further editing.

- **`tempest_fastapi_sdk.cli.docker_compose` module** exposes
  `generate(project_name, extras)` and `env_block_for(extras)` as
  public helpers. Both pin image tags to specific versions that
  are smoke-tested with the SDK; bumping the pins should go
  through the smoke suite before being released.

### Changed

- The post-scaffold "Next steps" hint printed by `tempest new`
  now reminds the developer to run `docker compose up -d` before
  `uv run python main.py`.

## [0.24.0] — 2026-05-31

### Added

- **`UploadUtils` pluggable storage backends.** New
  ``UploadStorage`` protocol under ``tempest_fastapi_sdk.utils``
  with two ready implementations:

  - ``LocalUploadStorage(base_dir)`` — disk-backed, matches the
    historical ``UploadUtils`` behavior.
  - ``MinIOUploadStorage(client, bucket=None)`` — wraps the
    ``AsyncMinIOClient`` shipped in v0.23.0, requires the
    ``[minio]`` extra.

  ``UploadUtils.save()`` now accepts an optional ``storage=``
  keyword. When provided, the validated upload is sent to the
  backend instead of the local filesystem — validation pipeline
  (extension / MIME / size / magic bytes / ``content_validator``)
  is identical for both targets. Calls without ``storage=``
  continue to write to ``upload_dir`` unchanged.

- **``IdempotencyMiddleware``** under
  ``tempest_fastapi_sdk.api.middlewares``. Caches the full response
  for ``POST`` / ``PUT`` / ``PATCH`` / ``DELETE`` requests keyed
  by ``(method, path, Idempotency-Key)`` so client retries don't
  re-execute the handler. Opt-in per request — endpoints without
  the header pass through.

  Two stores ship out of the box:

  - ``MemoryIdempotencyStore`` — async-lock-guarded dict with TTL
    eviction. Single-replica only.
  - ``RedisIdempotencyStore(client, prefix="idem:")`` — backed by
    an async Redis client. Required in multi-replica deployments.

  Custom backends can implement the ``IdempotencyStore`` protocol.

- **``EmailUtils.render_template(template_name, context)``.**
  Optional Jinja2 template rendering for transactional emails.
  Pass ``template_dir`` at construction time, then call
  ``render_template`` to produce the HTML / text body fed into
  ``send()``. HTML autoescaping is enabled for ``.html`` / ``.htm``
  / ``.xml`` templates so caller-supplied values can't break out
  into markup.

### Changed

- ``[email]`` extra now ships Jinja2 alongside ``aiosmtplib`` so
  ``render_template`` works without a separate ``[admin]``
  dependency. Existing installs should re-pull the extra:
  ``pip install -U "tempest-fastapi-sdk[email]"``.

### Fixed

- ``tests/utils/test_lazy_extras.py::hide_module`` fixture now
  fully restores ``sys.modules`` on teardown. The previous version
  only saved entries matching the ``_hide`` target, so tests that
  reimported the whole ``tempest_fastapi_sdk`` package leaked the
  freshly-built class objects into later tests — surfaced as
  ``pytest.raises(...)`` failing to catch exceptions that were
  raised under a different (re-imported) class identity.

## [0.23.0] — 2026-05-31

### Added

- **MinIO / S3 object storage module.** New
  `tempest_fastapi_sdk.storage` package exporting
  `AsyncMinIOClient` and `ObjectStat`. Async-friendly facade over
  the official `minio` package — every blocking call is wrapped in
  `asyncio.to_thread`, so the FastAPI event loop stays responsive
  while uploads/downloads run in the executor.

  Operations covered:

  - **Buckets** — `bucket_exists`, `ensure_bucket`, `list_buckets`,
    `remove_bucket`.
  - **Objects** — `put_object` (bytes or file-like), `fput_object`
    (from disk), `get_object_bytes`, `fget_object`, `stream_object`
    (chunked async iterator), `stat_object`, `list_objects`
    (prefix + recursion), `remove_object`, `copy_object`.
  - **Presigned URLs** — `presigned_get_url`, `presigned_put_url`
    with `timedelta` expiry.

  The full synchronous `minio.Minio` client stays accessible via
  the `.client` attribute when you need surface beyond the wrapper
  (SSE-KMS, lifecycle XML, bucket replication, etc.).

- **`MinIOSettings` mixin** under `tempest_fastapi_sdk.settings`,
  exposing `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`,
  `MINIO_SECURE`, `MINIO_REGION`, `MINIO_DEFAULT_BUCKET`. Re-exported
  at the package root.

- **New `[minio]` extra** — `pip install
  "tempest-fastapi-sdk[minio]"`. The `minio` package is lazy-loaded
  at `AsyncMinIOClient.__init__` so projects without storage don't
  pay the import cost.

### Docs

- New `docs/recipes/storage{,.en}.md` recipe with end-to-end
  examples: `UploadFile` upload, streaming download, presigned
  upload (direct from browser), presigned download, prefix
  listing, copy/move.
- `docs/reference.md` adds entries for `AsyncMinIOClient` and
  `ObjectStat`.

## [0.22.1] — 2026-05-31

### Fixed

- **File-descriptor leak in `configure_logging`.** Calling
  `configure_logging` twice (normal in tests, and in any service
  that supports hot-reload) removed the previous file handlers
  without closing them. After ~100 reconfigure cycles the kernel
  refused new file opens with ``OSError: [Errno 24] Too many open
  files``. The previous handlers are now `close()`-d before
  `removeHandler` so each call releases its FDs.

### Security

- **`UploadUtils.delete()` now refuses paths outside `upload_dir`.**
  Before, `utils.delete("/etc/passwd")` would happily `unlink()`
  whatever the caller passed. Any service that forwarded a
  user-supplied filename to `delete()` was effectively giving the
  caller an `rm` primitive bounded only by process permissions.
  The method now resolves the input against `upload_dir`, treats
  relative inputs as relative to `upload_dir`, and raises
  `InvalidFileTypeException` (with `reason="escapes upload_dir"`)
  for anything that escapes — including absolute paths to
  unrelated directories and `..`-style traversal.

  Callers that already passed paths returned by
  `UploadUtils.save()` keep working unchanged; only path-traversal
  attempts begin to raise.

### Changed

- `make_app_exception_handler` docstring now accurately states that
  `log_level` defaults to `logging.INFO` (the previous text claimed
  `logging.ERROR`, which is what the function was *originally*
  intended to do but never matched the signature).

## [0.22.0] — 2026-05-31

### Changed

- **`configure_logging` now writes to stdout *and* `logs/` by
  default.** Before, file logging only activated when the caller
  passed `log_dir=...`; the default scaffold often forgot it and the
  service ran with no on-disk audit trail. The new defaults:

  - `log_dir` default is now `"logs"` (was `None`).
  - New `stdout: bool = True` flag controls the terminal handler.
  - New `file_output: bool = True` flag controls the per-level +
    `500.log` file handlers.
  - Passing `stdout=False, file_output=False` raises
    `ValueError` — that combination silences every handler and is
    almost always a mistake.

  Backwards compatibility: passing `log_dir=None` (the old sentinel
  for "no files") still works and produces stdout-only behavior.
  Test suites that don't want logs/ files in cwd should now pass
  `file_output=False` explicitly.

- **`LogUtils(name=..., level=..., json_output=...)` and
  `LogUtils.configure(...)`** forward the same three flags
  (`log_dir`, `stdout`, `file_output`) so the imperative wrapper
  stays aligned with `configure_logging`.

### Migration

If your service was relying on `configure_logging(level=..., json_output=...)`
to be stdout-only, either:

```python
# Option A — opt out explicitly:
configure_logging(level="INFO", file_output=False)

# Option B — keep stdout-only via the old sentinel:
configure_logging(level="INFO", log_dir=None)
```

If your test suite spins up `configure_logging` or `LogUtils`
in-process, pass `file_output=False` to avoid stray ``logs/``
folders in the working directory.

## [0.21.3] — 2026-05-31

### Changed

- **`AppException` and 4xx `HTTPException` now emit an `INFO`-level
  log line.** Before, both paths were silent — the response went
  out with the SDK envelope but operators saw nothing in stdout
  or `info.log` for a `401`, `404`, `422` (etc.), making "API
  returned 4xx but I see no trace" debugging painful. The new
  behavior:

  - `AppException` with `status_code < 500` → `INFO` log, no
    traceback, no `500.log` marker.
  - `AppException` with `status_code >= 500` → `log_level`
    (default `ERROR`) + traceback + `HTTP_500_MARKER` so the
    record lands in `500.log`.
  - `HTTPException` 4xx → `INFO` log, no traceback. 5xx
    behavior unchanged (already logged at `log_level` + 500.log).
  - Unhandled `Exception` catch-all unchanged.

  The log line includes the request method, path, status code,
  exception code (for `AppException`) and request id — enough to
  grep for in `info.log` without paging the operator at 3am.

### Added

- **`make_app_exception_handler(*, log_level)`** factory exposed
  via `tempest_fastapi_sdk.api.handlers` and re-exported at the
  package root. The existing `app_exception_handler` callable is
  kept as a thin wrapper for backwards compatibility.

## [0.21.2] — 2026-05-31

### Fixed

- **Alembic wiped `configure_logging` during lifespan.** The stock
  `alembic/env.py` (generated by `alembic init`) ends with
  `fileConfig(config.config_file_name)`. That call defaults to
  `disable_existing_loggers=True` AND honors `[logger_root]` in
  `alembic.ini`, which the SDK was writing as
  `level = WARN, handlers = stderr`. When `AlembicHelper.upgrade()`
  ran during `lifespan`, every SDK logger configured via
  `configure_logging` got disabled and root was reset to WARN +
  stderr. The 500 catch-all handler kept building responses but
  nothing reached stdout, `error.log` or `500.log` — operators saw
  the 500 in the browser and zero log lines.

  Two-part fix shipped in the SDK templates:

  1. **`env.py.template`** — the call is now guarded on the
     presence of a `[loggers]` section AND uses
     `disable_existing_loggers=False`, so it never wipes the host's
     logging tree:

     ```python
     if config.config_file_name is not None:
         import configparser

         _ini = configparser.ConfigParser()
         _ini.read(config.config_file_name, encoding="utf-8")
         if _ini.has_section("loggers"):
             fileConfig(config.config_file_name, disable_existing_loggers=False)
     ```

  2. **`AlembicHelper.init()`** stops emitting the
     `[loggers]/[handlers]/[formatters]/[logger_*]/[handler_*]/[formatter_*]`
     sections into the generated `alembic.ini`. Alembic's own
     loggers inherit from root (which the host configures), and the
     guarded `fileConfig` above no-ops when no `[loggers]` section
     exists.

  Upgrade path for existing projects: re-run `tempest new --force`
  (or `AlembicHelper.init`) to regenerate the templates, OR
  manually patch `alembic/env.py` to wrap the `fileConfig` call as
  shown above and remove the `[loggers]`/`[handlers]`/`[formatters]`
  blocks from `alembic.ini`.

## [0.21.1] — 2026-05-31

### Fixed

- **`raise HTTPException(500, ...)` bypassed the SDK 500 logger.**
  Starlette intercepts every `HTTPException` inside its own
  `ExceptionMiddleware` and routes it to a default handler that
  emits a bare `JSONResponse({"detail": exc.detail})` with no log
  entry. The 0.21.0 catch-all `Exception` handler never saw those
  raises, so `tempest-fastapi-sdk[0.21.0]` users hitting a 5xx
  endpoint reported `Internal Server Error` in the browser with
  zero output in stdout / `error.log` / `500.log`.

  Added a third handler — `make_http_exception_handler` registered
  for `starlette.exceptions.HTTPException` — that:

  - logs every 5xx (`status_code >= 500`) at ERROR with
    `exc_info=exc` and `HTTP_500_MARKER` so the record lands in
    both `error.log` and the dedicated `500.log`;
  - returns the SDK envelope (`detail` / `code` / `details`),
    preserving the original status code and any custom headers,
    so frontends consuming the same envelope across `AppException`
    and raw `HTTPException` don't need to branch;
  - leaves 4xx HTTPExceptions untouched (Starlette's default body
    and no log) since those represent normal client outcomes.

  `make_http_exception_handler` and the existing `log_traceback`
  / `log_level` knobs on `register_exception_handlers` are wired
  end-to-end; opt out of the trace with
  `register_exception_handlers(app, log_traceback=False)` when an
  APM is already capturing the stack.

## [0.21.0] — 2026-05-31

### Added

- **File logging to a `logs/` directory + a `/logs` reader endpoint.**
  `configure_logging` gained a `log_dir` parameter. When set (the
  scaffold defaults it to `"logs"` via `LOG_DIR`), the stdout handler
  is kept **and** one JSON file per level is written — `debug.log`,
  `info.log`, `warning.log`, `error.log`, `critical.log` — each
  receiving only its own level (exact match, never `level >=`). A
  dedicated **`500.log`** captures only uncaught-500 records: the
  catch-all exception handler now flags them with
  `HTTP_500_MARKER`, so grave failures are isolated and never buried.
  A 500 therefore appears in both `error.log` and `500.log`. File
  handlers always emit JSON regardless of `json_output`.

- **`make_logs_router` — a paginated, filterable, authenticated log
  reader.** `GET /logs` reads the on-disk JSON files and returns a
  `BasePaginationSchema[LogEntrySchema]` (newest first). Query params:
  `source` (`all` | each level | `500`), `q` (message substring),
  `start` / `end` (ISO-8601 range), `page`, `page_size`. Gated by a
  shared-secret `X-Token` header via `make_token_dependency` — an
  empty `TOKEN_SECRET` disables the check (dev only). New exports:
  `make_logs_router`, `LogSource`, `LogEntrySchema`. The `create_app`
  scaffold wires the router and passes `log_dir`/`token_secret`
  automatically.

### Fixed

- **`tempest fix` now always formats, even when lint violations remain.**
  `run_ruff_fix` ran `ruff check --fix` and short-circuited on its exit
  code before reaching `ruff format`. But `ruff check --fix` exits
  non-zero whenever *any* residual violation it cannot autofix is left
  (an over-length string/comment, an undefined name, …) — so a single
  unfixable line silently skipped the formatter for the whole file,
  leaving long **code** lines un-wrapped and extra blank lines intact.
  The formatter now runs unconditionally; the lint exit code is still
  surfaced afterwards so CI keeps failing on the leftover issues.

  Note: `ruff format` (and therefore `tempest fix`) never wraps long
  **string literals or comments** — this matches Black. Those `E501`
  lines stay and must be shortened by hand or silenced with
  `# noqa: E501`.

- **Autogenerated migrations are now lint-clean out of the box.**
  `AlembicHelper.init()` writes a `[post_write_hooks]` block into the
  generated `alembic.ini` that runs `ruff check --fix` followed by
  `ruff format` on every freshly created revision file. Previously the
  files Alembic emits failed `tempest lint` (`ruff check`) with `W291`
  (trailing whitespace in the docstring header when `down_revision` is
  `None` — `Revises: `) and `E501` (over-length `sa.Column(...)`
  lines):

  ```text
  W291 Trailing whitespace
   --> alembic/versions/...add_todos_table.py:4:9
  E501 Line too long (120 > 88)
   --> alembic/versions/...add_todos_table.py:30:89
  ```

  The hooks resolve the project's own `ruff` configuration, so every
  selected rule that is autofixable (`I`, `UP`, `W`, `E501`, …) is
  cleared at generation time. Requires `ruff` on `PATH` — already a
  dev dependency in every `tempest new` scaffold.

## [0.20.0] — 2026-05-31

### Changed

- **BREAKING — pagination uses `page_size` everywhere instead of `size`.**
  The field on `BasePaginationFilterSchema` was named `size` (default
  `10`) while the controller / service / repository keyword argument
  was named `page_size` (default `20`), forcing every consumer to
  rename the attribute on the way through:

  ```python
  # before — required renaming + a default-value gotcha
  result = await controller.paginate(
      filters=f.get_conditions(),
      page=f.page,
      page_size=f.size,
      ...,
  )
  ```

  Aligned the request schema, the response envelope and the
  repository return dict on a single name + default:

  - `BasePaginationFilterSchema.size` → `BasePaginationFilterSchema.page_size`
  - Default `10` → `20`
  - `BasePaginationFilterSchema.get_conditions()` strips
    `["page", "page_size", "order_by", "ascending"]`
  - `BasePaginationSchema.size` → `BasePaginationSchema.page_size`
  - `BaseRepository.paginate` return dict key `"size"` →
    `"page_size"`. `BaseService.paginate` and
    `BaseController.paginate` propagate the new key.
  - `build_pagination_link_header(size=..., size_param="size")` →
    `build_pagination_link_header(page_size=..., size_param="page_size")`.
    URLs now look like `?page=2&page_size=20` by default. Pass
    `size_param="size"` to keep the old query-string spelling
    without renaming the function argument.

  Migration: rename `size` to `page_size` on every consumer; if a
  service relied on the previous default of `10` items per page,
  pass `page_size=10` explicitly. The admin router's `_Pagination`
  helper now reads `result["page_size"]` from the repository
  response.

## [0.19.2] — 2026-05-31

### Added

- **Explicit `log_traceback` flag on the 500 catch-all handler.**
  The default is `True` — every uncaught exception emits the full
  traceback via `logger.log(..., exc_info=exc)` so the operator
  always has it. Set `log_traceback=False` only when an APM agent /
  Sentry / equivalent is already capturing the failure and the
  duplicated stack noise is unwanted. The flag is forwarded by
  `register_exception_handlers` and `make_unhandled_exception_handler`.

## [0.19.1] — 2026-05-31

### Fixed

- **Unhandled exceptions returned a bare `Internal Server Error`
  string with no log entry.** `register_exception_handlers` only
  wired a handler for `AppException`, so every uncaught `Exception`
  (e.g. `RuntimeError`, `KeyError`, downstream library failures)
  fell through to Starlette's default — which writes nothing beyond
  the access line and returns a six-word body. Operators were left
  blind to real failures.
  - Added a catch-all `Exception` handler that logs the full
    traceback at ERROR via the `tempest_fastapi_sdk.api.handlers`
    logger (so the application's `LogUtils` / `configure_logging`
    setup picks it up), attaches the active `X-Request-ID` for
    correlation, and returns the canonical SDK envelope:

    ```json
    {
        "detail": "Internal server error",
        "code": "INTERNAL_SERVER_ERROR",
        "details": {"request_id": "<id>"}
    }
    ```

  - `register_exception_handlers(app, include_traceback=True)`
    embeds the formatted traceback under `details.traceback` so
    development environments can surface the failure in the
    response body too. Production callers leave it off so module
    paths / SQL fragments / object reprs don't leak.
  - `register_exception_handlers(app, log_level=logging.WARNING)`
    overrides the log level when needed.
  - Reads the request ID from the contextvar first, then falls
    back to the `X-Request-ID` header — `BaseHTTPMiddleware`
    spawns a child task so the contextvar set in
    `RequestIDMiddleware.dispatch` doesn't always reach the
    handler.
  - New `make_unhandled_exception_handler` factory exported from
    `tempest_fastapi_sdk.api`.

### Documentation

- Repository recipe in `docs/recipes/database.md` and the README
  Alembic walk-through still showed the deprecated
  `class UserRepository(BaseRepository[UserModel]): model = UserModel`
  Django-style class-attribute pattern dropped in 0.16.0. Replaced
  with the constructor signature
  `super().__init__(session, model=UserModel)`.

## [0.19.0] — 2026-05-30

### Added

- **MkDocs Material documentation site** auto-deployed to GitHub
  Pages at <https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/>.
  Sixteen pages total: landing, installation, architecture (with
  Mermaid layering + request-lifecycle diagrams), the eleven-step
  tutorial as one linear page, twelve thematic recipe pages
  (Database, HTTP, Cache, Real-time, Queue & Tasks, Logging, Metrics,
  Admin, Testing, CLI, Security, Brazilian helpers), an auto-generated
  API Reference via `mkdocstrings`, a migration guide, a contributing
  guide and the bundled CHANGELOG.
- New `[docs]` dependency group (`mkdocs`, `mkdocs-material`,
  `mkdocstrings[python]`, `pymdown-extensions`,
  `mkdocs-include-markdown-plugin`) installed via
  `uv sync --group docs`.
- **`make docs-serve` / `make docs-build` / `make docs`** Makefile
  targets for local docs work (live reload at
  `http://127.0.0.1:8000`).
- **`.github/workflows/docs.yml`** publishes the site to GitHub Pages
  on every push to `main` that touches docs, the package, the README
  or the CHANGELOG.
- README now opens with a docs-site banner linking to Home / Tutorial
  / Recipes / API reference so readers landing on PyPI or GitHub
  reach the prose-rich version in one click.

## [0.18.0] — 2026-05-30

### Added

- **`tempest fix`** — one-shot "organize the project" CLI command that
  runs `ruff check --fix <target>` followed by `ruff format <target>`.
  Sorts and dedupes imports, drops unused imports, normalizes string
  quotes to double, strips trailing whitespace, then normalizes
  indentation / line length / blank lines / trailing newlines. Pass
  `--unsafe` to also apply ruff's unsafe-fixes pass.
- **`py.typed` marker** shipped inside the wheel so downstream mypy
  reads the SDK's inline type hints instead of bailing out with
  `Skipping analyzing "tempest_fastapi_sdk": module is installed, but
  missing library stubs or py.typed marker`. PEP 561-compliant.

## [0.16.2] — 2026-05-30

### Fixed

- **`tempest new .` still rejected the `.` shorthand when the cwd
  basename contained a hyphen.** 0.16.1 special-cased `.` but then
  validated the derived name (`Path.cwd().name`) with the strict
  Python-identifier regex `^[a-z][a-z0-9_]*$`, so a real-world cwd
  like `todolist-api` died with `error: project name must match
  ^[a-z][a-z0-9_]*$`. The derived name is now matched against a
  PEP 503 normalized distribution-name regex
  (`^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$`) — the same shape pyproject
  accepts under `[project] name`. Explicit names (`tempest new
  myproject`) keep the stricter Python-identifier rule because the
  string is also used as the package directory name.

### Changed

- **`tempest new` (no positional argument) now defaults to `.`.**
  Previously typer rejected the bare invocation with
  `Missing argument 'NAME'`. The default matches the
  scaffold-in-current-directory shape: running `tempest new` inside
  an empty project directory writes `main.py` / `pyproject.toml` /
  `src/` / `tests/` directly under that directory. Pass an explicit
  name to keep the legacy "create a new subdir" behavior.

## [0.16.1] — 2026-05-30

### Fixed

- **CLI required `jinja2` even though `[admin]` was not installed.** Importing
  `tempest_fastapi_sdk` eagerly walked into `admin/router.py`, which had a
  top-level `from fastapi.templating import Jinja2Templates`. Starlette's
  `templating` module raises `ImportError("jinja2 must be installed to use
  Jinja2Templates")` at import time, so `tempest --version` (or any other
  CLI command) blew up on environments that legitimately skipped the admin
  extra. The import is now deferred inside `make_admin_router`, raising a
  clear `Install with pip install tempest-fastapi-sdk[admin]` only when the
  router is actually constructed.
- **`tempest new .` rejected the `.` shorthand for "scaffold here".** The
  positional `name` was always run through the Python-identifier regex
  before resolving the target, so `tempest new .` died with
  `error: project name must match ^[a-z][a-z0-9_]*$`. The CLI now accepts
  `.` and treats it as "scaffold flatly in the current working directory";
  the package name is derived from the cwd's basename (still validated).
  `--path` is rejected alongside `.` because the target is unambiguous.

## [0.16.0] — 2026-05-30

Repository and exception APIs join the admin in dropping Django-style class-attribute configuration. The constructor signature is now the contract; subclasses survive only when they add behavior (custom queries, `except DomainError`) — never to "fill in" a required class attribute.

### Changed

- **BREAKING — `BaseRepository.model` / `not_found_exception` are constructor kwargs, not class attributes.** Plain CRUD works without a subclass:

  ```python
  repository = BaseRepository(session, model=UserModel)
  ```

  Subclasses kept around for custom queries forward both via `super().__init__`:

  ```python
  class UserRepository(BaseRepository[UserModel]):
      def __init__(self, session: AsyncSession) -> None:
          super().__init__(
              session,
              model=UserModel,
              not_found_exception=UserNotFoundError,
              not_found_message="Usuário não encontrado",
          )
  ```

  Replaces the previous `class UserRepository(BaseRepository[UserModel]): model = UserModel; not_found_exception: ClassVar[...] = ...` form. The synthesized `_build_default_repository_class` helper in `admin/config.py` is gone — `AdminModel.build_repository` now calls `BaseRepository(session, model=self.model)` directly.

- **BREAKING — `AppException.code` is a plain `str` class attribute and is overridable at the raise site via `code=`.** Same for `status_code=`. The `code: ClassVar[str]` annotation is removed from every shipped subclass (`NotFoundException`, `ConflictException`, `ForbiddenException`, `UnauthorizedException`, `ValidationException`, `TooManyRequestsException`, `InvalidTokenException`, `ExpiredTokenException`, `FileTooLargeException`, `InvalidFileTypeException`). Subclasses still exist for `isinstance` / `except DomainError` matching; class-level defaults still work; constructor wins when both are present:

  ```python
  raise NotFoundException(
      "Pedido não encontrado",
      code="ORDER_NOT_FOUND",
      details={"order_id": str(order_id)},
  )
  ```

## [0.15.0] — 2026-05-30

Admin configuration is now a plain typed instance instead of a Django-style subclass. The class form (`class UserAdmin(AdminModel[UserModel])` with `ClassVar` attributes and the `@site.register` decorator) is gone — register a constructed instance instead. Field options accept real SQLAlchemy column attributes, so typos surface in the editor rather than at runtime.

### Changed

- **BREAKING — `AdminModel` is an instance, not a subclass.** Replace

  ```python
  @site.register
  class UserAdmin(AdminModel[UserModel]):
      model = UserModel
      list_display: ClassVar[list[str]] = ["email", "is_admin"]
      ordering = "-created_at"
  ```

  with

  ```python
  site.register(AdminModel(
      model=UserModel,
      list_display=[UserModel.email, UserModel.is_admin],
      ordering=desc(UserModel.created_at),
  ))
  ```

  `list_display`, `list_filter`, `search_fields`, `readonly_fields` and `identity_field` accept SQLAlchemy column attributes (`UserModel.email`) **or** plain strings. `ordering` accepts a column (ascending), `desc(column)` / `asc(column)`, or a `"-field"` string. `AdminSite.register` / `get` / `require` / `iter_models` now take and return instances. The `@site.register` decorator form is removed.

### Added

- **`FieldRef` / `OrderRef`** — public type aliases for the admin field- and ordering-reference unions, exported from the package root.

### Fixed

- **Admin list-view descending `ordering` raised `AttributeError`.** A configured `"-created_at"` was passed verbatim to `paginate(order_by=...)`, which did `getattr(model, "-created_at")`. Ordering is now normalized to a `(column, ascending)` pair, so descending orders and `desc()` / `asc()` wrappers work correctly.

## [0.13.1] — 2026-05-30

### Fixed

- **PyPI wheel duplicate-filename rejection.** `tool.hatch.build.targets.wheel.force-include` was double-listing the admin templates and static assets (already picked up by the default package scan), producing a wheel that PyPI rejected with
  `400 Invalid distribution file. ZIP archive not accepted: Duplicate filename in local headers`. Removed the redundant directives; `admin/templates/` and `admin/static/` continue to be bundled by hatchling's default sdist/wheel rules.

## [0.13.0] — 2026-05-30

Django-style admin site — Phase 1 (read-only). Mount under `/admin` so the database port can stay private; operators sign in with a user row owned by the application instead of a shared admin password.

### Added

- **`BaseUserModel`** — abstract `BaseModel` subclass with `email` (unique,
  lowercased), `hashed_password`, `is_admin`, `last_login_at`, plus
  `set_password()` / `check_password()` / `normalize_email()` helpers.
- **`AdminAuthBackend`** ABC + **`UserModelAuthBackend`** default. Enforces
  `is_admin=True` and `is_active=True`, stamps `last_login_at`, exposes
  `principal_id` / `load_principal` / `display_name` so custom backends
  (LDAP, OAuth, external IAM) plug into the same flow.
- **`AdminSite`** — slug registry with `register`/`unregister`/`require`
  and decorator-style usage (`@site.register`).
- **`AdminModel[ModelT]`** — Django-flavored declarative configuration:
  `list_display`, `list_filter`, `search_fields`, `readonly_fields`,
  `ordering`, `page_size`, `identity_field`, `verbose_name(_plural)`,
  `repository_class`. Auto-synthesizes a default repository when one
  is not supplied.
- **`make_admin_router`** — wires the HTML routes: login / logout /
  dashboard / list (paginated + search + filter) / detail (read-only)
  / static. Jinja2 templates + minimal admin.css ship with the wheel.
- **`SignedCookieSessionStore`** — itsdangerous `TimestampSigner`, signed
  HttpOnly + Secure + SameSite=Lax cookie scoped to the admin prefix,
  8-hour default lifetime, per-session CSRF token.
- New optional extra **`[admin]`** (`jinja2`, `itsdangerous`).

## [0.11.0] — 2026-05-30

### Added

- **`BaseStrEnum` / `BaseIntEnum`** — shared enum bases under
  `tempest_fastapi_sdk.core.enums` with `values()` / `keys()` /
  `to_dict()` helpers so str- and int-valued enums no longer need a
  per-project base class. Exported from the package root.

### Changed

- **`BaseService.map_to_response` is now async-aware.** The base awaits
  the repository's `map_to_response` only when it returns an
  awaitable (`inspect.isawaitable`), so concrete services with async
  mappers no longer need to override the read methods. Existing sync
  mappers keep working unchanged.

## [0.10.0] — 2026-05-30

Security hardening primitives, hoisted from a downstream service so every
project inherits the same defenses instead of re-rolling them.

### Fixed

- **`RateLimitMiddleware` keyed on the transport peer behind a proxy.** The
  default key was `request.client.host`, which is the *reverse-proxy* IP
  once the app is fronted by one — collapsing every client into a single
  bucket (one abuser exhausts everyone's quota; the limit is effectively
  global). Added `trusted_ip_header=` so the key is the client IP resolved
  from a single edge-set header (e.g. `"x-real-ip"`). Default behavior is
  unchanged (peer IP) for the no-proxy case.

### Added

- **`get_client_ip` / `get_client_ip_from_scope`** — spoof-resistant client
  IP resolution. Trusts only a single, explicitly named edge-set header
  (never the client-controlled `X-Forwarded-For`), falling back to the
  transport peer.
- **`AttemptThrottle`** + **`TooManyRequestsException` (429)** — a
  backend-agnostic fixed-window failure counter for login / OTP / code
  verification flows. Keyed by any string, counts only failures, raises a
  429 with `Retry-After` when the budget is exhausted, and fails open on a
  backend outage. Works with any async Redis-like client (`ThrottleBackend`
  protocol).
- **`generate_opaque_token` / `hash_opaque_token` / `verify_opaque_token`**
  — single-use opaque tokens hashed at rest (SHA-256) with constant-time
  verification, for password reset / email verification / magic links.
  Pure standard library.
- **`HardenedStaticFiles`** — a `StaticFiles` subclass that stamps
  `X-Content-Type-Options: nosniff`, a locked-down `Content-Security-Policy`
  and `Cross-Origin-Resource-Policy` on every response, so serving
  user-uploaded files can't become a stored-XSS vector. Headers
  configurable via `DEFAULT_STATIC_SECURITY_HEADERS`.
- **`set_cookie` / `clear_cookie`** — secure-by-default cookie helpers
  (`HttpOnly`, `Secure`, `SameSite`) with matching set/clear flags so
  logout actually drops the cookie.
- **`RSAWebhookSignatureVerifier`** — asymmetric (RSA-SHA256/384/512)
  webhook signature verification for providers that sign with a private
  key and publish a public key (OpenPix/Woovi-style), complementing the
  existing HMAC `WebhookSignatureVerifier`. Requires `cryptography`.

## [0.9.0] — 2026-05-30

### Added

- **`UploadUtils` magic-byte content verification.** New opt-in
  `verify_magic_bytes=True` constructor flag sniffs the first bytes of every
  upload and rejects content whose real type does not match its declared
  `Content-Type` / the `allowed_mimetypes` allow-list — closing the polyglot
  hole where an HTML+JS payload served as `image/jpeg` passed the
  extension/MIME check. Recognizes JPEG, PNG, GIF, BMP, WebP and PDF.
- **`sniff_mime(prefix)` helper** (exported at the top level) — magic-byte
  MIME detector usable on its own to build custom `content_validator`
  predicates.
- **`UploadUtils.save(..., content_validator=...)`** — optional predicate run
  on the first chunk; returning `False` aborts the save and removes the
  partial file before any further bytes are written.
- **`UploadUtils.save(..., filename=...)`** — explicit, deterministic final
  filename (e.g. `f"{user_id}.jpg"`), reduced to its basename and guarded
  against path traversal. Takes precedence over `keep_original_name`.

All four additions are backwards compatible — existing `UploadUtils` calls
behave exactly as in 0.8.0 unless the new options are passed.

## [0.8.0] — 2026-05-17

### Breaking changes

- **`ServerSettings` field rename.** `HOST`, `PORT` and `DEBUG` were renamed to
  `SERVER_HOST`, `SERVER_PORT` and `SERVER_DEBUG`, and a new `SERVER_RELOAD`
  field was added. `LOG_LEVEL` and `LOG_JSON` moved out to a new
  `LogSettings` mixin.
  - **Migration:** rename the matching env vars in every `.env` /
    deployment manifest and replace `settings.HOST` / `settings.PORT` /
    `settings.DEBUG` / `settings.LOG_LEVEL` / `settings.LOG_JSON`
    accordingly. Mix `LogSettings` into `Settings` if the service was
    relying on `ServerSettings` for the log fields.
  - See the [Migration guide 0.7 → 0.8](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/)
    in the README for the full checklist.

### Added — Settings mixins (Tier 1)

- `LogSettings` (`LOG_LEVEL`, `LOG_JSON`) — extracted from `ServerSettings`.
- `EmailSettings` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `SMTP_FROM_ADDR`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_TIMEOUT_SECONDS`).
- `UploadSettings` (`UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`,
  `UPLOAD_ALLOWED_EXTENSIONS`, `UPLOAD_ALLOWED_MIMETYPES`).
- `TokenSettings` (`TOKEN_SECRET`).
- `WebPushSettings` (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`,
  `VAPID_SUBJECT`, `WEBPUSH_DEFAULT_TTL_SECONDS`).
- `TaskIQSettings` (`TASKIQ_BROKER_URL`, `TASKIQ_RESULT_BACKEND_URL`).

### Added — API helpers (Tier 2)

- `tempest_fastapi_sdk.run_server(app, *, settings=None, host=None,
  port=None, reload=None, **uvicorn_kwargs)` — canonical
  `src/server.py` entry point.
- `make_bearer_token_dependency(tokens, soft=False, ...)` —
  `Authorization: Bearer <jwt>` decoder returning the claims dict.
- `make_jwt_user_dependency(tokens, user_loader, *, soft=False,
  subject_claim="sub", ...)` — bearer + user loader in one factory.
- `is_valid_cep`, `normalize_cep`, `CEP`, `CEP_PATTERN` — Brazilian
  zipcode validators in `tempest_fastapi_sdk.utils.regex`.

### Added — Opt-in primitives (Tier 3)

- `tempest_fastapi_sdk.cache.cached(redis, ttl=300, key_prefix="",
  serializer=..., deserializer=..., skip_cache=...)` — Redis-backed
  function cache decorator.
- `make_tool_spec_router(spec, *, path="/tool-spec", tag="meta")` —
  `/tool-spec` manifest router; accepts dict / sync / async providers.
- `make_role_dependency(tokens, roles, *, require_all=False, ...)` and
  `make_permission_dependency(tokens, permissions, *, require_all=True,
  ...)` — JWT-claim-based authorization.

### Added — Advanced primitives (Tier 4)

- `tempest_fastapi_sdk.WebhookSignatureVerifier(secret, *, algorithm,
  header_name, encoding, prefix)` — HMAC webhook signature
  verification with FastAPI dependency factory.
- `tempest_fastapi_sdk.RateLimitMiddleware(max_requests, window_seconds,
  key_func, exempt_paths)` — in-process sliding-window rate limiter.
- `build_pagination_link_header(base_url, *, page, size, pages,
  extra_params, page_param, size_param)` — RFC 8288 `Link` header
  builder for offset paginated responses.

### Docs

- README — full reorganization, every new primitive has a recipe with
  full code samples. New sections: Periodic tasks scheduler,
  Programmatic server entry point, JWT bearer / current-user / role
  dependencies, CEP, Cache decorator, Tool-spec router, Webhook
  signature verification, Pagination Link headers, Rate limit
  middleware, Utility helpers, Outbox dispatcher pattern, Migration
  guide 0.7 → 0.8.
- Tutorial sections 1–11 realigned to the canonical layout mandated by
  the SDK consumers' shared `CLAUDE.md` (single `main.py` one-liner,
  `src/server.py` exposing `run()`, `src/api/app.py` with `create_app()`,
  `src/db/repositories/` location, mandatory `src/controllers/`
  pass-through, `src/api/dependencies/` package).
- Reference section — method tables for `AsyncDatabaseManager`,
  `AsyncRedisManager`, `AsyncBrokerManager`, `AsyncTaskBrokerManager`
  and `AsyncTaskScheduler`.

### Dev

- Added `uvicorn>=0.30.0` to the dev dependency group so `run_server`
  tests can monkey-patch `uvicorn.run`.

## [0.7.3] — 2026-05-17

- Hardened request-ID middleware, SSE writer, web-push dispatcher and
  database manager lifecycle.

## [0.7.2] — 2026-05-16

- Release packaging fix only.

## [0.7.1] — 2026-05-16

### Changed

- Optional extras (`[auth]`, `[email]`, `[upload]`, `[cache]`,
  `[webpush]`, `[metrics]`, `[queue]`, `[tasks]`) are now lazy-loaded
  at first instantiation, so `import tempest_fastapi_sdk` works when
  only a subset of extras is installed.

## [0.7.0] — 2026-05-15

### Added

- `LogUtils`, `configure_logging`, `JSONFormatter`,
  `RequestIDMiddleware` and the `request_id_ctx` contextvar.
- `MetricsUtils` (CPU / memory / disk / GPU snapshots).
- `AsyncBrokerManager` (FastStream wrapper, `[queue]` extra) and
  `AsyncTaskBrokerManager` (TaskIQ wrapper, `[tasks]` extra).

## [0.6.0] — 2026-05-13

### Added

- SSE primitives (`EventStream`, `ServerSentEvent`, `sse_response`).
- Web Push dispatch (`WebPushDispatcher`, `WebPushSubscriptionSchema`,
  `WebPushPayloadSchema`, `WebPushGoneError`, `[webpush]` extra).

## [0.5.0] — 2026-05-10

### Added

- `AsyncRedisManager` (`[cache]` extra).
- CORS helpers (`apply_cors`, `CORSSettings`).
- Composable settings mixins (`ServerSettings`, `DatabaseSettings`,
  `RedisSettings`, `RabbitMQSettings`, `JWTSettings`, `CORSSettings`).

## [0.4.0] — 2026-05-07

### Added

- `make_health_router`, audit / soft-delete mixins, cursor pagination.

## [0.3.0] — 2026-05-04

### Added

- `BaseController` + `BaseService` generics, DI scaffolding, logging,
  `tempest_fastapi_sdk.testing` helpers.

## [0.2.0]

### Changed

- Drop Python 3.10 support; SDK now targets Python ≥ 3.11.

## [0.1.0]

- Initial public release.

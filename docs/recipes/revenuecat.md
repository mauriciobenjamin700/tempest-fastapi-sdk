# RevenueCat (assinatura in-app)

App Flutter (ou nativo) que vende assinatura pela App Store e pelo Google Play
quase sempre passa pelo RevenueCat. O app compra; o RevenueCat avisa o seu
backend por **webhook** a cada mudança (compra, renovação, cancelamento,
expiração...). O backend precisa de três coisas que todo serviço acabava
escrevendo à mão:

1. **Autenticar** o webhook — e recusar tudo quando nenhum segredo está
   configurado.
2. **Gravar o evento uma vez só**, responder `200` rápido e processar
   depois — o RevenueCat reenvia, e às vezes manda o mesmo evento duas vezes.
3. **Desistir de um evento envenenado** depois de N tentativas, em vez de
   tentar de novo a cada varredura, para sempre.

O SDK entrega as três: o adapter em
`tempest_fastapi_sdk.integrations.subscriptions.revenuecat` e o inbox
genérico `WebhookInbox` em `tempest_fastapi_sdk.db`, que serve para qualquer
provedor.

!!! info "Instalação"
    Nenhum extra. O cliente usa o `HTTPClient` do SDK, e o `httpx` já vem no
    pacote base.

!!! note "De onde vem cada afirmação sobre o RevenueCat"
    Formato do evento, headers, tipos de evento e política de reenvio foram
    lidos da documentação oficial, não medidos contra uma conta real:
    [Webhooks](https://www.revenuecat.com/docs/integrations/webhooks),
    [Event Types and Fields](https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields),
    [Sample Events](https://www.revenuecat.com/docs/integrations/webhooks/sample-events)
    e a [REST API v1](https://www.revenuecat.com/docs/api-v1). As três
    páginas de webhook estão versionadas em `vendor/` com o sha256 fixado por
    teste, e a lista de tipos de evento do SDK é comparada com elas.

## O caminho mínimo

Um arquivo com o modelo do inbox, a tabela de assinaturas do seu produto, o
handler que aplica um evento e a rota:

```python
# src/billing.py
from datetime import datetime

from fastapi import FastAPI
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.db import (
    AsyncDatabaseManager,
    BaseModel,
    BaseWebhookEventModel,
    UtcDateTime,
    WebhookInbox,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    RevenueCatWebhook,
    make_revenuecat_webhook_router,
    normalize,
)
from tempest_fastapi_sdk.settings import DatabaseSettings, RevenueCatSettings


class Settings(DatabaseSettings, RevenueCatSettings):
    """Banco + credenciais do RevenueCat, lidos do ambiente."""


class WebhookEventModel(BaseWebhookEventModel):
    """Inbox de webhooks: uma linha por evento distinto."""

    __tablename__ = "webhook_events"


class SubscriptionModel(BaseModel):
    """Estado atual da assinatura de cada usuário do app."""

    __tablename__ = "subscriptions"

    app_user_id: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


settings = Settings()
db = AsyncDatabaseManager(**settings.database_kwargs())
inbox: WebhookInbox[WebhookEventModel] = WebhookInbox(db, model=WebhookEventModel)


async def apply_event(event: WebhookEventModel, session: AsyncSession) -> None:
    """Grava o estado que o evento deixou, na mesma transação do inbox.

    Args:
        event (WebhookEventModel): A linha reivindicada do inbox.
        session (AsyncSession): A sessão que também marca o evento como
            processado.
    """
    snapshot = normalize(RevenueCatWebhook.model_validate(event.payload).event)
    if snapshot.app_user_id is None:
        return
    current = (
        await session.execute(
            select(SubscriptionModel).where(
                SubscriptionModel.app_user_id == snapshot.app_user_id
            )
        )
    ).scalar_one_or_none()
    if current is None:
        current = SubscriptionModel(app_user_id=snapshot.app_user_id)
        session.add(current)
    current.status = snapshot.status.value
    current.product_id = snapshot.product_id
    current.period_end = snapshot.period_end


app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_revenuecat_webhook_router(
        inbox=inbox,
        secret=settings.REVENUECAT_WEBHOOK_SECRET,
    )
)
```

E a varredura que processa o que chegou, a cada 5 minutos:

```python
# src/tasks/billing.py
from tempest_fastapi_sdk.tasks import Cron, TaskQueue

from src.billing import apply_event, db, inbox

tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/", resources=[db])


@tq.cron(Cron.EVERY_5_MINUTES)
async def sweep_webhook_inbox() -> None:
    """Processa todo evento do inbox que já pode ser processado."""
    await inbox.drain_once(apply_event)
```

No painel do RevenueCat, em **Integrations → Webhooks**, aponte a URL para
`https://<seu-host>/webhooks/revenuecat` e preencha o campo de
*authorization header* com o mesmo valor de `REVENUECAT_WEBHOOK_SECRET`.

O que acontece com uma entrega, rodado com o código acima (SQLite, três POSTs
de um `INITIAL_PURCHASE` em trial com um `$email` nos `subscriber_attributes`
— sem header, com header, de novo com header —, uma `drain_once`, e depois a
linha de `subscriptions` e a linha do inbox impressas: status, e se o payload
gravado tem `subscriber_attributes`):

```text
401 {'detail': 'Invalid RevenueCat webhook credentials', 'code': 'UNAUTHORIZED', 'details': {}}
200 {'recorded': True, 'event_id': 'evt-1'}
200 {'recorded': False, 'event_id': 'evt-1'}
processed 1 retrying 0
user-1 trial monthly 2030-03-17 17:46:40+00:00
processed False
```

O reenvio respondeu `200` com `recorded: False`: o RevenueCat só precisa
parar de reenviar, e nada foi gravado de novo.

## Peça por peça

### 1. A rota não faz regra de negócio

`make_revenuecat_webhook_router` autentica, grava o evento no inbox com
`provider="revenuecat"` e `external_id=event.id`, e responde `200`. Só isso.

Por quê: segundo a [documentação](https://www.revenuecat.com/docs/integrations/webhooks),
qualquer status diferente de `200` é falha, o RevenueCat reenvia "up to 5
times" (depois de 5, 10, 20, 40 e 80 minutos) e desconecta em 60 segundos. A
própria página recomenda adiar o processamento para depois da resposta.

!!! tip "Processar na hora, sem esperar a varredura"
    Passe `on_recorded`: ele recebe o id da linha do inbox de cada evento
    **novo** (não do reenvio). Enfileire a task ali. Se o enfileiramento
    falhar, a rota ainda responde `200` — o evento já está gravado e a
    varredura pega.

    ```python
    from uuid import UUID

    from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
        make_revenuecat_webhook_router,
    )
    from tempest_fastapi_sdk.tasks import TaskQueue

    from src.billing import apply_event, inbox

    tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")


    @tq.task
    async def process_webhook_event(event_id: str) -> None:
        """Processa um evento do inbox.

        Args:
            event_id (str): O id da linha do inbox.
        """
        await inbox.process(UUID(event_id), apply_event)


    async def enqueue(event_id: UUID) -> None:
        """Manda o evento recém-gravado para o worker.

        Args:
            event_id (UUID): O id da linha do inbox.
        """
        await process_webhook_event.enqueue(str(event_id))


    router = make_revenuecat_webhook_router(
        inbox=inbox, secret="valor-do-painel", on_recorded=enqueue
    )
    ```

### 2. Autenticação: header e, se quiser, HMAC

O RevenueCat oferece duas formas, as duas documentadas na página de webhooks:

| Forma | Header | O que prova |
| --- | --- | --- |
| *Authorization header* (o valor que você digita no painel) | `Authorization` | que quem mandou conhece o segredo |
| *HMAC webhook signing* (opcional, por integração) | `X-RevenueCat-Webhook-Signature: t=<ts>,v1=<hex>` | isso **e** que o corpo não mudou |

```python
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    make_revenuecat_webhook_router,
)
from tempest_fastapi_sdk.settings import RevenueCatSettings

from src.billing import inbox

settings = RevenueCatSettings()
router = make_revenuecat_webhook_router(
    inbox=inbox,
    secret=settings.REVENUECAT_WEBHOOK_SECRET,
    signing_secret=settings.REVENUECAT_WEBHOOK_SIGNING_SECRET,
)
```

Um segredo está configurado quando tem valor. Cada segredo preenchido é
conferido, e com os dois preenchidos a entrega precisa passar nos dois; o que
estiver vazio só desliga a própria checagem. Por isso o mesmo código serve
para "só header", "só HMAC" e "os dois", trocando apenas as variáveis de
ambiente. Regras que o SDK aplica e que os testes fixam:

- comparação em tempo constante (`hmac.compare_digest`);
- o header `Authorization` vale com ou sem o prefixo `Bearer `;
- **nenhum segredo preenchido recusa tudo** (`401`) — uma variável de
  ambiente esquecida não vira um endpoint aberto;
- `secret=None` e `signing_secret=None` juntos levantam `ValueError` na
  montagem: não existe rota sem autenticação;
- a assinatura HMAC fora da janela de 5 minutos é recusada. Segundo a
  documentação, o `t` é recalculado a cada tentativa, inclusive nos
  reenvios, então a janela só cobre relógio e latência.

!!! info "Segredo por requisição"
    `secret` e `signing_secret` também aceitam uma função
    `Callable[[Request], str]` — útil para rotação ou para um serviço com
    vários projetos RevenueCat. O vazio é lido por requisição: a função que
    devolve `""` desliga aquela checagem só naquela entrega.

### 3. O inbox: uma vez, com teto de tentativas

`WebhookInbox` é genérico — não conhece o RevenueCat. Ele serve igual para
Stripe, OpenPix, Mercado Pago ou o zap-api: grave com o id de evento que o
provedor promete estável entre reenvios.

| Passo | O que faz | Por que assim |
| --- | --- | --- |
| `record(provider, external_id, event_type, payload)` | `INSERT ... ON CONFLICT DO NOTHING` (PostgreSQL e SQLite) | o reenvio devolve `None` em vez de levantar |
| `claim(id)` | `UPDATE ... WHERE status = 'received'` | de dois workers, só um ganha |
| `process(id, handler)` | roda o handler **na mesma transação** que marca `processed` | efeito no banco e marca entram juntos, ou nada entra |
| `fail(id, erro)` | volta para `received` com backoff exponencial | dependência fora do ar ganha tempo para voltar |
| `max_attempts` | ao atingir, vira `failed` e sai da varredura | evento envenenado não roda para sempre |
| `reclaim_stale()` | linha presa em `processing` volta (ou falha, sem orçamento) | worker que morreu no meio não prende o evento |

```python
from tempest_fastapi_sdk.db import WebhookInbox

from src.billing import WebhookEventModel, db

inbox: WebhookInbox[WebhookEventModel] = WebhookInbox(
    db,
    model=WebhookEventModel,
    max_attempts=5,
    backoff_base_seconds=60.0,
    stale_after=600.0,
)
```

!!! warning "Efeito fora do banco não entra na transação"
    E-mail, push, chamada a outra API: se o handler faz isso e depois falha,
    a próxima tentativa faz de novo. Torne esse efeito idempotente pelo
    `event.external_id`.

Evento que chegou a `failed` fica na tabela para alguém olhar (`last_error`
diz o motivo). Para reprocessar depois de corrigir a causa, volte o `status`
para `received` e zere `attempts`.

### 4. De evento para estado: `normalize`

São 21 tipos de evento na documentação. `normalize(event)` devolve um
`SubscriptionSnapshot` com um `SubscriptionStatusEnum` e os campos que
importam (`app_user_id`, `product_id`, `entitlement_ids`, `period_end`,
`grace_period_end`, `reason`, `event_at`...):

| Evento | Status |
| --- | --- |
| `INITIAL_PURCHASE`, `RENEWAL`, `UNCANCELLATION`, `NON_RENEWING_PURCHASE`, `PRODUCT_CHANGE`, `SUBSCRIPTION_EXTENDED`, `REFUND_REVERSED` | `TRIAL` se `period_type` é `TRIAL`, senão `ACTIVE` |
| `CANCELLATION` com `CUSTOMER_SUPPORT` | `REFUNDED` |
| `CANCELLATION` com `BILLING_ERROR`, `BILLING_ISSUE` | `BILLING_ISSUE` |
| `CANCELLATION` (outro motivo) | `CANCELLED` — o período corre até o fim |
| `SUBSCRIPTION_PAUSED` | `PAUSE_SCHEDULED` — a documentação manda **não** cortar acesso aqui |
| `EXPIRATION` com `SUBSCRIPTION_PAUSED` | `PAUSED` |
| `EXPIRATION` | `EXPIRED` |
| `TEMPORARY_ENTITLEMENT_GRANT` | `TEMPORARY_GRANT` (até 24 h, durante falha da loja) |
| `TEST`, `TRANSFER`, `EXPERIMENT_ENROLLMENT`, `VIRTUAL_CURRENCY_TRANSACTION`, `SUBSCRIBER_ALIAS`, `PURCHASE_REDEEMED`, `INVOICE_ISSUANCE`, `PRICE_INCREASE_CONSENT_*` | `NO_CHANGE` |
| tipo que esta versão não conhece | `UNKNOWN` — nunca levanta |

O que cada status **significa** para o seu produto (qual plano libera o quê,
quanto dura a carência, o que o reembolso corta) fica no seu serviço. O SDK
entrega o estado, não o entitlement.

!!! tip "Entrega fora de ordem"
    `snapshot.event_at` é quando o RevenueCat gerou o evento. Guarde o último
    aplicado por usuário e ignore um evento mais velho que chega atrasado.

### 5. Coleta mínima

`subscriber_attributes` é onde o app guarda `$email`, telefone e o que mais
anexou ao cliente — o exemplo de `INITIAL_PURCHASE` da documentação traz um
`$email`. Ele continua legível em `event.subscriber_attributes`, mas fica
**fora do `model_dump()`**, e é o `model_dump()` que a rota grava no inbox.
No exemplo acima, o `processed False` da última linha é isso: o campo não
chegou ao banco.

### 6. Reconciliação pela API

A [página de webhooks](https://www.revenuecat.com/docs/integrations/webhooks)
recomenda reler o cliente por `GET /subscribers` depois de qualquer webhook.
`RevenueCatClient.get_subscriber` faz essa chamada com uma chave **secreta**:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    RevenueCatClient,
)
from tempest_fastapi_sdk.settings import RevenueCatSettings


async def main() -> None:
    """Lê o estado atual de um usuário direto do RevenueCat."""
    settings = RevenueCatSettings()
    async with HTTPClient(**settings.revenuecat_kwargs()) as http:
        info = await RevenueCatClient(http).get_subscriber("user-1456")
    for name, entitlement in info.subscriber.entitlements.items():
        print(name, entitlement.expires_date)


asyncio.run(main())
```

!!! danger "O endpoint é *Get or Create*"
    A referência chama a rota de "Get or Create Customer": um `app_user_id`
    que o RevenueCat nunca viu é **criado**. Reconcilie só ids que vieram de
    um webhook ou da sua tabela de usuários. É também por isso que
    `tempest integrations verify revenuecat` sai com código 2 sem chamar
    nada: verificar a chave por essa rota criaria um cliente.

O `app_user_id` vai para o path codificado num segmento só, e `""`, `"."` e
`".."` levantam `ValueError` antes de qualquer requisição — o ponto não é
escapado, e até a v0.334.0 `".."` subia um nível no path com a chave secreta.
O `FakeRevenueCatClient` recusa os mesmos três.

Para teste, `FakeRevenueCatClient` responde o que você semear e falha quando
você mandar:

```python
import asyncio

import httpx

from tempest_fastapi_sdk.testing.fakes import FakeRevenueCatClient


async def main() -> None:
    """Semeia um cliente, lê, e força uma falha de rede."""
    fake = FakeRevenueCatClient()
    fake.set_subscriber("user-1", {"entitlements": {"pro": {}}})
    info = await fake.get_subscriber("user-1")
    print(list(info.subscriber.entitlements))

    fake.fail_next(httpx.ConnectError("down"))
    try:
        await fake.get_subscriber("user-1")
    except httpx.ConnectError as exc:
        print("falhou:", exc)


asyncio.run(main())
```

```text
['pro']
falhou: down
```

Um `app_user_id` que ninguém semeou levanta `httpx.HTTPStatusError` (404),
o mesmo tipo que o cliente real levanta para resposta não-2xx — em vez de
criar o cliente como o endpoint real faria.

## Configuração

| Variável | Para quê | Vazia significa |
| --- | --- | --- |
| `REVENUECAT_WEBHOOK_SECRET` | valor do *authorization header* do painel | o header `Authorization` não é conferido; com `REVENUECAT_WEBHOOK_SIGNING_SECRET` também vazia, toda entrega é recusada (`401`) |
| `REVENUECAT_WEBHOOK_SIGNING_SECRET` | segredo do HMAC, se ativado | a assinatura não é conferida; com `REVENUECAT_WEBHOOK_SECRET` também vazia, toda entrega é recusada (`401`) |
| `REVENUECAT_API_KEY` | chave secreta (`sk_...`) para `GET /v1/subscribers` | a chamada sai com `Bearer ` vazio; a referência documenta `401` para chave inválida |

Composto com `EnvironmentSettings`, `RevenueCatSettings` derruba o boot com
`ENV=production` se **nenhum** dos dois segredos de webhook estiver
preenchido — em produção, isso significaria recusar toda compra e renovação:

```text
ValidationError 1 validation error for Settings
  Value error, ENV=production refuses development values:
- REVENUECAT_WEBHOOK_SECRET: empty, so every RevenueCat webhook is refused
```

## RevenueCat desligado (`REVENUECAT_ENABLED=false`)

`REVENUECAT_ENABLED=false` faz o guard de produção pular a regra dos dois
segredos vazios e põe `"revenuecat"` em `settings.disabled_integrations()`.
Só isso: a dependency do webhook lê os segredos, não a flag, então com os
dois vazios ela continua recusando toda entrega com `401` — nada é aceito
em silêncio. Veja [Ligar e desligar integrações](system-checks.md#ligar-e-desligar-integracoes-deploy-por-partes).

## Recap

- `make_revenuecat_webhook_router` autentica, grava no inbox e responde
  `200`; cada segredo preenchido é conferido, e sem nenhum preenchido toda
  entrega é recusada.
- `WebhookInbox` grava cada evento uma vez, processa com claim, junta efeito
  e marca na mesma transação e para em `max_attempts` — e serve para qualquer
  provedor.
- `normalize` transforma os 21 tipos de evento em um estado; tipo novo vira
  `UNKNOWN`.
- `subscriber_attributes` não é gravado.
- `RevenueCatClient.get_subscriber` reconcilia — lembrando que o endpoint
  cria o cliente se ele não existir.

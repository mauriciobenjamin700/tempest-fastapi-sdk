# Push (web + mobile num fluxo só)

Um produto que tem site **e** app acaba com duas APIs de notificação: uma
para o navegador, outra para o celular, e um caller que precisa saber de
que tipo é cada aparelho antes de mandar qualquer coisa. O módulo
`tempest_fastapi_sdk.push` existe para apagar essa diferença.

Você diz "notifica esse usuário". O SDK lê os aparelhos dele, manda cada
um pelo transporte certo, e **apaga exatamente os que o provedor
renegou** — 404/410 no Web Push, `UNREGISTERED` no FCM. Uma regra, dois
vocabulários.

!!! info "Instalação"
    - Web: extra `[webpush]` — `uv add "tempest-fastapi-sdk[webpush]"`.
    - Mobile: extra `[firebase]` — `uv add "tempest-fastapi-sdk[firebase]"`
      (o mesmo extra e a mesma service account da
      [Auth Firebase](firebase-auth.md); uma credencial serve às duas
      features).
    - Só web, só mobile, ou os dois: você instala o que usa.
      `import tempest_fastapi_sdk.push` funciona sem nenhum dos dois.

!!! info "Já uso `webpush`, quebra?"
    Não. `tempest_fastapi_sdk.webpush` continua exportando
    `WebPushDispatcher`, `WebPushSubscriptionService`,
    `make_web_push_router` e os schemas — mesmo código, mesmos nomes. O
    módulo `push` é **adição**, não substituição: use quando quiser um
    caminho só para navegador e celular. A receita de
    [Web Push](webpush.md) continua valendo para quem só tem navegador.

## O caminho mínimo

### 1. A tabela de aparelhos

```python
# src/db/models/device.py
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID

from tempest_fastapi_sdk import BaseDeviceTokenModel


class DeviceModel(BaseDeviceTokenModel):
    """Um aparelho — navegador ou celular — que recebe notificação."""

    __tablename__ = "device_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

Uma tabela para os dois mundos: linha de navegador guarda `p256dh` /
`auth`; linha de celular deixa esses campos `NULL` e põe o token de
registro do FCM em `token`.

### 2. Os transportes

```python
# src/api/dependencies/push.py
from tempest_fastapi_sdk import (
    FCMTransport,
    FirebaseAuth,
    WebPushDispatcher,
    WebPushTransport,
)

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)

transports = [
    WebPushTransport(WebPushDispatcher(**settings.webpush_kwargs())),
    FCMTransport(auth=firebase),
]
```

`FCMTransport(auth=...)` reaproveita o app Firebase que a verificação de
ID token já inicializou — uma service account carregada, duas features.

### 3. O serviço e a rota

```python
# src/api/routers/push.py
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, DeviceService, make_push_router

from src.api.dependencies.auth import current_user_id
from src.api.dependencies.push import transports
from src.core.settings import settings
from src.db.models import DeviceModel
from src.db.session import get_session


def device_service(session: AsyncSession) -> DeviceService[Any]:
    """Constrói o serviço de aparelhos para a requisição."""
    repository: BaseRepository[Any] = BaseRepository(session, model=DeviceModel)
    return DeviceService(repository, transports)


router = make_push_router(
    service_factory=device_service,
    session_factory=get_session,
    current_user_id=current_user_id,
    vapid_public_key=settings.VAPID_PUBLIC_KEY,
)
```

Pronto: `POST /api/push/register`, `POST /api/push/unregister` e
`GET /api/push/vapid-public-key`.

### 4. Notificar

```python
import asyncio
from typing import Any
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository, DeviceService, PushPayloadSchema

from src.api.dependencies.push import transports
from src.db.models import DeviceModel
from src.db.session import get_session


async def main() -> None:
    """Run this example."""
    async for session in get_session():
        repository: BaseRepository[Any] = BaseRepository(session, model=DeviceModel)
        service: DeviceService[Any] = DeviceService(repository, transports)
        result = await service.notify_user(
            UUID("2f1b0f1e-0f4a-4e35-9a5f-2c8a2f9a1234"),
            PushPayloadSchema(
                title="Pedido confirmado",
                body="Seu pedido #1042 saiu para entrega.",
                tag="order:1042",
                data={"url": "/orders/1042"},
            ),
        )
        print(result.as_dict())


asyncio.run(main())
```

Saída típica com três aparelhos, um deles morto:

```json
{"delivered": 2, "pruned": ["ios/9f2c1a0b3d4e"], "failed": [], "skipped": []}
```

## Como funciona, peça por peça

### O contrato: um método

```python
from typing import Protocol

from tempest_fastapi_sdk import PushDevice, PushPayloadSchema


class PushDispatcher(Protocol):
    """Entrega uma notificação para um aparelho."""

    platforms: frozenset[str]

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Entrega o payload no aparelho."""
        ...
```

É um `Protocol`, no mesmo formato que `UploadStorage` usa para storage:
o serviço depende do contrato, nunca de um backend concreto. Um teste
passa um transporte falso sem herdar de nada; um provedor novo (APNs
direto, Huawei Push) entra sem tocar no serviço.

O interessante — fan-out, poda, falha parcial — é trabalho do **serviço**,
não do transporte. É o que impede as duas metades de divergirem.

### O payload que atravessa os dois

`PushPayloadSchema` é a interseção que sobrevive nos dois provedores:

| Campo | No navegador | No FCM |
| --- | --- | --- |
| `title` / `body` | `Notification` | `messaging.Notification` |
| `image` | `icon` da notificação | `notification.image` |
| `tag` | `tag` (coalescência) | `android.collapse_key` + `apns-collapse-id` |
| `data` | payload do `notificationclick` | `data` |

!!! warning "`data` é `dict[str, str]`, e isso não é capricho"
    O FCM **recusa** valores que não sejam string. O schema estreita o
    tipo aqui para o erro aparecer na borda, com nome de campo, em vez de
    virar rejeição do provedor no meio de um fan-out.

### A poda: uma regra, dois códigos

Esse é o ponto do módulo. Quando o provedor diz que o aparelho não existe
mais, a linha sai do banco — mas cada provedor fala isso do seu jeito:

| Provedor | Sinal | Vira |
| --- | --- | --- |
| Web Push | HTTP 404 / 410 | `PushDeviceGoneError` → linha apagada |
| FCM | `UnregisteredError` | `PushDeviceGoneError` → linha apagada |
| FCM | `SenderIdMismatchError` (token de outro projeto) | `PushDeviceGoneError` → linha apagada |
| Qualquer um | qualquer outra falha | `PushError` → linha **mantida**, tenta de novo |

!!! danger "`InvalidArgumentError` do FCM **não** poda — de propósito"
    O FCM levanta `InvalidArgumentError` tanto para token ruim quanto
    para **payload malformado**, e o tipo da exceção não separa os dois.
    Tratar isso como "aparelho morto" apagaria a frota inteira do usuário
    na primeira vez que um corpo de notificação sair errado. O custo da
    escolha oposta é uma tentativa falha por fan-out até o cliente
    re-registrar — o erro mais barato dos dois. Isso diverge da proposta
    original da issue #157, de propósito.

### Falha de um aparelho não aborta os outros

As entregas rodam concorrentes e independentes. O resultado diz o que
aconteceu com cada uma:

```python
import asyncio
from typing import Any
from uuid import UUID

from tempest_fastapi_sdk import (
    BaseRepository,
    DeviceService,
    PushFanoutResult,
    PushPayloadSchema,
)

from src.api.dependencies.push import transports
from src.db.models import DeviceModel
from src.db.session import get_session


async def main() -> None:
    """Run this example."""
    async for session in get_session():
        repository: BaseRepository[Any] = BaseRepository(session, model=DeviceModel)
        service: DeviceService[Any] = DeviceService(repository, transports)
        result: PushFanoutResult = await service.notify_user(
            UUID("2f1b0f1e-0f4a-4e35-9a5f-2c8a2f9a1234"),
            PushPayloadSchema(title="Oi"),
        )
        print(result.delivered)   # quantos aceitaram
        print(result.pruned)      # apagados (mascarados)
        print(result.failed)      # falharam e continuam na tabela
        print(result.skipped)     # sem transporte configurado


asyncio.run(main())
```

!!! tip "Token de aparelho nunca aparece em log"
    Um token de registro é credencial: quem tem, notifica aquele
    aparelho. Tudo que o resultado expõe — e tudo que o SDK loga — passa
    por `mask_push_token`, que guarda 12 caracteres de SHA-256. É o mesmo
    tratamento que `_mask_endpoint` já dava ao endpoint do Web Push.

### `skipped` não é `pruned`

Um serviço que configurou **só** Web Push e tem linhas de iOS no banco
não apaga essas linhas: o aparelho está vivo, o que falta é fiação. Elas
saem em `skipped`, e passam a ser entregues no dia em que o
`FCMTransport` for adicionado.

### Recortar o fan-out

```python
import asyncio
from typing import Any
from uuid import UUID

from tempest_fastapi_sdk import (
    BaseRepository,
    DeviceService,
    PushPayloadSchema,
    PushPlatform,
)

from src.api.dependencies.push import transports
from src.db.models import DeviceModel
from src.db.session import get_session


async def main() -> None:
    """Run this example."""
    async for session in get_session():
        repository: BaseRepository[Any] = BaseRepository(session, model=DeviceModel)
        service: DeviceService[Any] = DeviceService(repository, transports)
        await service.notify_user(
            UUID("2f1b0f1e-0f4a-4e35-9a5f-2c8a2f9a1234"),
            PushPayloadSchema(title="Só no navegador"),
            platforms=[PushPlatform.WEB],
            exclude_tokens=["https://push.example/o-aparelho-que-causou-o-evento"],
        )


asyncio.run(main())
```

`exclude_tokens` é o caso de sincronização multi-aparelho: quem fez a
mudança não deve notificar a si mesmo. Aparelho excluído não é contatado
**nem podado**.

### Registro é idempotente por token

Registrar duas vezes o mesmo token atualiza a linha e renova
`last_seen_at`, em vez de duplicar. E se o aparelho trocar de dono
(logout + login no mesmo celular), a linha **muda de usuário** — sem
isso, a próxima notificação iria para a conta anterior.

### Configuração

```python
from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.settings import PushSettings


class Settings(PushSettings, BaseAppSettings):
    """Settings de um serviço que notifica navegador e celular."""


settings = Settings()
print(settings.web_enabled, settings.mobile_enabled, settings.enabled)
```

`PushSettings` junta `WebPushSettings` e `FirebaseSettings` — e existe
por causa de uma armadilha real: os dois declaram `enabled`, então
compondo na mão o MRO escolhe silenciosamente o do Web Push, e um serviço
só-mobile lê `enabled is False` com o FCM perfeitamente configurado. Aqui
`enabled` responde "dá para notificar alguém?", e as duas metades ficam
legíveis em `web_enabled` / `mobile_enabled`.

## Testando

O contrato é um `Protocol`, então um transporte falso é uma classe com um
método:

```python
from tempest_fastapi_sdk import PushDevice, PushDeviceGoneError, PushPayloadSchema


class FakeTransport:
    """Aceita tudo, menos os tokens que você mandar renegar."""

    platforms: frozenset[str] = frozenset({"web", "ios", "android"})

    def __init__(self, gone: set[str]) -> None:
        """Guarda os tokens que devem ser renegados."""
        self.gone: set[str] = gone
        self.sent: list[str] = []

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Registra a entrega ou renega o aparelho."""
        if device.token in self.gone:
            raise PushDeviceGoneError("gone", masked_token=device.masked_token)
        self.sent.append(device.token)
```

A suíte do SDK vai além nos transportes de verdade: o teste do FCM
constrói a mensagem com as classes reais do `firebase_admin` e afirma que
o JSON serializado carrega `token`. Isso pega um detalhe medido — na
7.5.0 o `Message.token` está **deprecated** em favor de `fid`, mas os
dois são **campos de wire diferentes** (`{"token": ...}` vs
`{"fid": ...}`), e um token de registro do FCM pertence a `token`. Seguir
a depreciação ao pé da letra mandaria o campo errado.

## Recap

- Um `PushDispatcher` (`Protocol`), dois transportes: `WebPushTransport`
  e `FCMTransport`.
- Uma tabela (`BaseDeviceTokenModel`) e um serviço (`DeviceService`) para
  navegador e celular.
- Poda unificada: 404/410 e `UNREGISTERED`/`SenderIdMismatch` apagam a
  linha; qualquer outra falha mantém e tenta de novo. `InvalidArgument`
  do FCM **não** poda, para um payload ruim não apagar a frota.
- Falha de um aparelho não aborta os outros; o `PushFanoutResult` diz
  quem entregou, quem foi podado, quem falhou e quem ficou sem
  transporte.
- Token nunca aparece em log nem em resposta — só o hash mascarado.
- `tempest_fastapi_sdk.webpush` continua igual; `push` é adição.

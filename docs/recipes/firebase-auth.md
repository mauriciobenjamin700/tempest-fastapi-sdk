# Auth Firebase (ID token)

O app mobile faz login no Firebase, recebe um **ID token** e manda esse
token pra sua API. O backend não emitiu nada — ele só precisa **provar
que o token é real** antes de resolver quem é o usuário.

`FirebaseAuth` embala esse caminho inteiro:

- inicializa o app do `firebase_admin` **uma vez só**, mesmo que você
  construa o autenticador em dois módulos diferentes;
- verifica o ID token fora do event loop (o verificador do Google é
  síncrono);
- entrega uma **identidade tipada** (`FirebaseIdentity`), não um
  `dict[str, Any]`; e
- traduz cada falha do Firebase para a hierarquia de exceções do SDK,
  com um `code` diferente pra cada uma.

!!! info "Instalação"
    Precisa do extra `[firebase]` — `uv add "tempest-fastapi-sdk[firebase]"`.

    Ele é **pesado**: `firebase-admin` traz `grpcio`, `protobuf`,
    `google-api-core`, `google-auth` e os clientes de Firestore/Storage.
    Medido com `firebase-admin` 7.5.0 numa venv limpa: **33 pacotes,
    52 MB**. Por isso ele fica **fora do extra `[all]`**, e o import é
    preguiçoso — `import tempest_fastapi_sdk` continua funcionando sem
    ele instalado.

!!! info "Quando usar isto"
    Use quando **o cliente já chega com um ID token do Firebase**
    (app Flutter/React Native, ou web usando o Firebase JS SDK).

    - Se o **seu** serviço é quem faz login e emite tokens, você quer
      [Auth flow](auth-flow.md).
    - Se você recebe um bearer **opaco** e valida perguntando a um
      `userinfo` upstream, você quer
      [Auth por introspecção](introspection-auth.md).

## O caminho mínimo

Construa uma vez, na camada de dependências:

```python
# src/api/dependencies/auth.py
from tempest_fastapi_sdk import FirebaseAuth

from src.core.settings import settings

firebase = FirebaseAuth(
    credentials_path=settings.FIREBASE_CREDENTIALS_PATH,
    project_id=settings.FIREBASE_PROJECT_ID,
)
```

E use os métodos direto como dependências:

```python
# src/api/routers/profile.py
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import FirebaseAuth, FirebaseIdentity

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("/me")
async def me(
    identity: FirebaseIdentity = Depends(firebase.get_identity),
) -> dict[str, str]:
    """Devolve o usuário autenticado pelo ID token do Firebase."""
    return {"uid": identity.uid, "email": identity.email or ""}


@router.get("/uid")
async def uid(user_id: str = Depends(firebase.get_uid)) -> dict[str, str]:
    """Quando você só precisa do id, não da identidade inteira."""
    return {"uid": user_id}
```

Pronto. Requisição sem `Authorization` recebe **401**; token expirado,
adulterado ou de outro projeto recebe **401** — cada um com o seu
`code`.

!!! tip "Ligue os handlers de exceção"
    `FirebaseAuth` levanta subclasses de `UnauthorizedException` e
    `ForbiddenException` do próprio SDK. Chame
    `register_exception_handlers(app)` (o `create_app()` do SDK já faz)
    pra que virem 401/403 com corpo `{"detail": ..., "code": ...}` em
    vez de 500.

## Como funciona, peça por peça

### Inicialização idempotente

`firebase_admin.initialize_app()` **levanta `ValueError` na segunda
chamada**. É por isso que todo serviço acaba com o mesmo bloco
`get_app()` / `except ValueError` espalhado por três arquivos.

`FirebaseAuth` é dono desse bloco. Construir duas vezes com o mesmo
`app_name` reaproveita o app existente:

```python
from tempest_fastapi_sdk import FirebaseAuth

from src.core.settings import settings

first = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)
second = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)
```

Isso não é teoria: `tests/auth/test_firebase.py` constrói dois
autenticadores e afirma que ambos apontam para o **mesmo** app.

Precisa falar com **dois projetos Firebase** no mesmo processo? Dê nomes
diferentes:

```python
from tempest_fastapi_sdk import FirebaseAuth

from src.core.settings import settings

consumers = FirebaseAuth(
    credentials_path=settings.FIREBASE_CREDENTIALS_PATH,
    app_name="consumers",
)
drivers = FirebaseAuth(
    credentials_path=settings.FIREBASE_DRIVERS_CREDENTIALS_PATH,
    app_name="drivers",
)
```

### De onde vem a credencial

Três canais, nesta ordem de precedência:

1. `credentials_json` — o JSON da service account **inline**, pra deploy
   que injeta segredo por variável de ambiente e não monta volume;
2. `credentials_path` — o arquivo da service account no disco;
3. nada — cai na credencial default do ambiente
   (`GOOGLE_APPLICATION_CREDENTIALS`, ou o metadata server quando você
   roda dentro da infraestrutura do Google).

```python
import os

from tempest_fastapi_sdk import FirebaseAuth

firebase = FirebaseAuth(
    credentials_json=os.environ["FIREBASE_CREDENTIALS_JSON"],
    project_id="meu-app-3f21c",
)
```

JSON inválido, arquivo ausente ou ambiente sem credencial default viram
`FirebaseCredentialError` — que é um `RuntimeError`, **não** uma
`AppException`. O motivo: isso é erro de configuração, acontece na
construção, e nunca no meio de uma requisição.

### A identidade tipada

`FirebaseIdentity` é um `dataclass` congelado. O handler nunca vê o dict
cru:

```python
import asyncio

from tempest_fastapi_sdk import FirebaseAuth, FirebaseIdentity

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)


async def main() -> None:
    """Run this example."""
    identity: FirebaseIdentity = await firebase.verify("eyJhbGciOi...")
    print(identity.uid)
    print(identity.email, identity.email_verified)
    print(identity.phone_number)
    print(identity.provider)          # "google.com", "password", "phone", ...
    print(identity.claims["role"])    # custom claims continuam acessíveis


asyncio.run(main())
```

`claims` guarda **tudo** que o token trouxe, incluindo custom claims que
você setou com `set_custom_user_claims`. Os campos nomeados são o que
99% das rotas usam; `claims` é a saída pro resto.

### Os erros, um `code` por falha

| Situação | Exceção | HTTP | `code` |
| --- | --- | --- | --- |
| Sem header `Authorization` | `FirebaseTokenMissingError` | 401 | `FIREBASE_TOKEN_MISSING` |
| Token malformado, assinatura errada, outro projeto | `FirebaseTokenInvalidError` | 401 | `FIREBASE_TOKEN_INVALID` |
| Token expirado | `FirebaseTokenExpiredError` | 401 | `FIREBASE_TOKEN_EXPIRED` |
| Token revogado (só com `check_revoked=True`) | `FirebaseTokenRevokedError` | 401 | `FIREBASE_TOKEN_REVOKED` |
| Usuário desabilitado (só com `check_revoked=True`) | `FirebaseUserDisabledError` | **403** | `FIREBASE_USER_DISABLED` |
| Certificados do Google inacessíveis | `FirebaseUnavailableError` | 401 | `FIREBASE_UNAVAILABLE` |

!!! note "Por que a ordem dos `except` importa"
    No `firebase-admin` 7.5.0, `ExpiredIdTokenError` e
    `RevokedIdTokenError` são **subclasses** de `InvalidIdTokenError`
    (medido, não deduzido). Uma implementação que capturasse a classe
    mãe primeiro colapsaria os três casos em `FIREBASE_TOKEN_INVALID` —
    e o cliente perderia a informação que decide entre "renove o token"
    e "faça login de novo". O teste parametrizado do SDK trava essa
    ordem.

!!! warning "Usuário desabilitado é 403, não 401"
    Ele **provou** quem é; o que falta é permissão. Por isso é a única
    falha que a variante soft (abaixo) continua levantando.

### A variante soft — rota que serve anônimo e logado

`get_optional_identity` devolve `None` em vez de levantar:

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import FirebaseAuth, FirebaseIdentity

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)

router = APIRouter(prefix="/api/feed", tags=["feed"])


@router.get("/")
async def feed(
    identity: FirebaseIdentity | None = Depends(firebase.get_optional_identity),
) -> dict[str, bool]:
    """Personaliza quando há token, e serve anônimo quando não há."""
    return {"personalized": identity is not None}
```

Sem header → `None`. Token que não verifica → `None` também (logado em
`DEBUG` com o `code`, **nunca** com o token). Usuário desabilitado →
continua 403.

Precisa estreitar o tipo dentro do handler? Use o guard do SDK:

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import FirebaseAuth, FirebaseIdentity
from tempest_fastapi_sdk.auth import require_authenticated

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("/")
async def create_order(
    maybe: FirebaseIdentity | None = Depends(firebase.get_optional_identity),
) -> dict[str, str]:
    """Aceita a requisição só quando o token veio e verificou."""
    identity: FirebaseIdentity = require_authenticated(maybe)
    return {"uid": identity.uid}
```

### Do `uid` ao **seu** usuário

O SDK não decide como um `uid` do Firebase vira usuário do seu banco —
isso é regra sua (lookup por coluna, provisionamento just-in-time,
chamada a outro serviço). `FirebaseUserResolver` é a costura:

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import (
    FirebaseAuth,
    FirebaseIdentity,
    FirebaseUserResolver,
)

from src.core.settings import settings
from src.db.models import UserModel
from src.db.repositories import UserRepository

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)
repository = UserRepository()


async def load_user(identity: FirebaseIdentity) -> UserModel | None:
    """Mapeia a identidade verificada para o usuário local."""
    return await repository.get_by_firebase_uid(identity.uid)


users: FirebaseUserResolver[UserModel] = FirebaseUserResolver(firebase, load_user)

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/")
async def account(user: UserModel = Depends(users.get_user)) -> dict[str, str]:
    """A rota já recebe o usuário do banco, com o tipo concreto."""
    return {"id": str(user.id)}
```

Resolver que devolve `None` significa "essa identidade não tem usuário
aqui" — vira 401 com `FIREBASE_TOKEN_INVALID` e `details={"uid": ...}`,
não uma resposta vazia. `get_optional_user` é a versão soft.

!!! note "`repository` e `UserModel` são glue da sua aplicação"
    `UserRepository.get_by_firebase_uid(...)` representa a sua camada de
    dados — não faz parte do SDK. Troque pela chamada real do projeto.

### `check_revoked` — o custo de saber na hora

Por padrão a verificação é **local**: assinatura + claims contra os
certificados públicos do Google, que o `firebase_admin` busca e cacheia.
Nesse modo, revogar uma sessão no console não derruba o token até ele
expirar.

`check_revoked=True` faz cada verificação também perguntar ao backend do
Firebase se o token foi revogado e se o usuário está desabilitado —
**uma ida à rede por requisição**:

```python
from tempest_fastapi_sdk import FirebaseAuth

from src.core.settings import settings

firebase = FirebaseAuth(
    credentials_path=settings.FIREBASE_CREDENTIALS_PATH,
    check_revoked=True,
    clock_skew_seconds=5,
)
```

`clock_skew_seconds` dá folga pro relógio do cliente adiantado — útil
quando o app roda em celular com hora manual.

### Configuração via settings

O mixin `FirebaseSettings` já traz as três variáveis, com título e
descrição no padrão dos outros:

```python
from tempest_fastapi_sdk import BaseAppSettings, FirebaseAuth
from tempest_fastapi_sdk.settings import FirebaseSettings


class Settings(FirebaseSettings, BaseAppSettings):
    """Settings da aplicação."""


settings = Settings()
firebase = FirebaseAuth(**settings.firebase_kwargs())
```

| Variável | Para quê |
| --- | --- |
| `FIREBASE_PROJECT_ID` | Projeto dos tokens. Opcional quando a service account já carrega. |
| `FIREBASE_CREDENTIALS_PATH` | Caminho do arquivo de service account. |
| `FIREBASE_CREDENTIALS_JSON` | O mesmo JSON inline, pra deploy sem volume montado. |

`firebase_kwargs()` **descarta valores vazios**, então variável não
setada deixa o default do construtor de pé em vez de mandar caminho
vazio. `settings.enabled` diz se há service account explícita — note que
`False` não impede verificar: a credencial default do ambiente ainda
funciona.

## Testando

Patch em `verify_id_token`, no módulo real — assim o mapeamento de erro
é exercitado contra as classes de exceção verdadeiras, com a herança
verdadeira:

```python
from typing import Any

import pytest
from firebase_admin import auth as firebase_auth

from tempest_fastapi_sdk import FirebaseAuth, FirebaseTokenExpiredError

CLAIMS: dict[str, Any] = {"uid": "uid-123", "email": "person@example.com"}


async def test_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token expirado vira o code do SDK, não uma exceção do Google."""

    def fake_verify(id_token: str, **_: Any) -> dict[str, Any]:
        """Simula o verificador do Google."""
        raise firebase_auth.ExpiredIdTokenError("expired", None)

    monkeypatch.setattr(firebase_auth, "verify_id_token", fake_verify)
    firebase = FirebaseAuth(credentials_path="tests/fixtures/service-account.json")

    with pytest.raises(FirebaseTokenExpiredError) as error:
        await firebase.verify("token")

    assert error.value.code == "FIREBASE_TOKEN_EXPIRED"
```

!!! tip "Nada de rede, nada de credencial real"
    A suíte do SDK gera uma chave RSA local e monta um arquivo de
    service account sintático — `initialize_app` aceita, porque ele não
    conversa com o Google. E um token que não é um JWT é recusado
    **sem tocar a rede** (medido): o verificador confere a estrutura
    antes de qualquer busca de certificado.

## Recap

- `FirebaseAuth` verifica **ID token do Firebase**; quem emite o token é
  o Firebase, não o seu serviço.
- Inicialização é **idempotente** por `app_name` — construir duas vezes
  reaproveita o mesmo app; nomes distintos falam com projetos distintos.
- Credencial vem de JSON inline, arquivo, ou credencial default do
  ambiente — nessa ordem. Falha de configuração é
  `FirebaseCredentialError`.
- `get_identity` / `get_uid` são estritos; `get_optional_identity` é a
  variante soft que devolve `None`. Usuário desabilitado é 403 nos dois.
- Cada falha tem `code` próprio; a ordem dos `except` preserva a
  diferença entre expirado, revogado e inválido.
- `FirebaseUserResolver[UserT]` liga a identidade ao usuário do seu
  banco sem o SDK decidir a regra.
- O extra `[firebase]` é pesado (33 pacotes, 52 MB medidos) e fica fora
  de `[all]`; o import é preguiçoso, então só instanciar exige ele.

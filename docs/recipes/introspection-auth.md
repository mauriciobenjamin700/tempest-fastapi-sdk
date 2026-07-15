# Auth por introspecção (resource server)

Nem todo serviço emite os próprios tokens. Muitas vezes o seu serviço é
apenas um **resource server** no padrão OAuth2: ele recebe um *bearer*
opaco emitido por um provedor de identidade upstream e precisa validá-lo
perguntando pro provedor **quem é o dono do token**. Você não decodifica
JWT nem valida assinatura — você chama um endpoint `userinfo` /
introspecção e confia na resposta.

`IntrospectionAuth` embala exatamente esse padrão:

- valida o bearer chamando um `GET <userinfo_url>` com
  `Authorization: Bearer <token>`,
- **cacheia** as respostas boas em processo por um TTL curto, pra uma
  rajada de requisições com o mesmo token não martelar o upstream,
- opcionalmente **restringe o acesso** a uma claim de aplicação
  (`access_apps` por padrão), e
- extrai o id do usuário da claim de subject (`sub` por padrão).

!!! info "Quando usar isto"
    Use quando **outro serviço** (o IAGRO, um Keycloak, um Auth0, o seu
    próprio serviço de identidade) emite os tokens e o seu serviço só
    precisa aceitá-los. Se o **seu** serviço é quem faz login e emite
    tokens, você quer o `UserAuthService` + `make_auth_router` (veja
    [Auth flow](auth-flow.md)), não isto.

## O caminho mínimo

Instancie uma vez, apontando pro endpoint userinfo do provedor, e use os
dois métodos como dependências do FastAPI:

```python
# src/api/dependencies/auth.py
from tempest_fastapi_sdk import IntrospectionAuth

from src.core.settings import settings

auth = IntrospectionAuth(
    userinfo_url=settings.IAGRO_USERINFO_URL,   # ex.: https://id.iagro.gov/users/me
    required_app="famacha",                     # gate de acesso por app
)
```

```python
# src/api/routers/animals.py
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from src.api.dependencies.auth import auth

router = APIRouter(prefix="/api/animals", tags=["animals"])


@router.get("/me")
async def whoami(
    claims: dict[str, Any] = Depends(auth.get_claims),
) -> dict[str, Any]:
    """Devolve as claims cruas do usuário autenticado."""
    return claims


@router.get("/")
async def list_animals(
    user_id: UUID = Depends(auth.get_user_id),
) -> list[str]:
    """Lista os animais do usuário — id já resolvido do token."""
    return await service.list_for(user_id)
```

Pronto. Uma requisição sem `Authorization` recebe **401**; um token que
o upstream rejeita recebe **401**; um usuário sem `famacha` no
`access_apps` recebe **403**.

!!! tip "Ligue os handlers de exceção"
    `IntrospectionAuth` levanta `UnauthorizedException` (401) e
    `ForbiddenException` (403) do próprio SDK. Chame
    `register_exception_handlers(app)` (o `create_app()` do SDK já faz)
    pra que virem os status HTTP certos em vez de 500.

## Como funciona, peça por peça

### `get_claims` — o coração

`get_claims` é a dependência principal. Ela:

1. lê o bearer via `HTTPBearer(auto_error=False)` — **sem** header →
   `UnauthorizedException`;
2. chama `fetch_userinfo(token)` (com cache);
3. se `required_app` estiver setado, exige que ele esteja em
   `claims.get(app_claim) or []`, senão `ForbiddenException`;
4. devolve o dict de claims.

```python
claims: dict[str, Any] = await auth.get_claims(credentials)
# {"sub": "…", "access_apps": ["famacha"], "email": "…", ...}
```

### `get_user_id` — o atalho comum

Na maioria das rotas você só quer o id do usuário, não o dict inteiro.
`get_user_id` depende do mesmo bearer, chama `get_claims` por dentro e
faz `UUID(str(claims["sub"]))`:

```python
user_id: UUID = await auth.get_user_id(credentials)
```

Subject ausente ou que não é um UUID válido vira `UnauthorizedException`.

!!! note "Por que `get_user_id` não declara `Depends(self.get_claims)`?"
    Argumentos default são avaliados **na definição do método**, quando
    a instância `self` ainda não existe — então você não consegue
    escrever `Depends(self.get_claims)` na assinatura. A solução: o
    `get_user_id` depende do **bearer diretamente** e chama
    `await self.get_claims(credentials)` no corpo. Continua funcionando
    liso como `Depends(auth.get_user_id)`.

### O cache

Toda resposta `200` é guardada por `cache_ttl_seconds` (30 por padrão),
com relógio em `time.monotonic()` e chave no token cru. Dentro do TTL, a
segunda chamada **não** bate no upstream. Um `401`/`403` **remove** o
token do cache na hora. `cache_ttl_seconds=0` desliga o cache.

```python
auth = IntrospectionAuth(
    userinfo_url=settings.IAGRO_USERINFO_URL,
    cache_ttl_seconds=60,   # tolere até 60s de token revogado
)
```

!!! warning "TTL é uma janela de revogação"
    Enquanto uma claim está cacheada, uma revogação upstream não é vista.
    Escolha um TTL curto (segundos a poucos minutos) pra equilibrar
    carga no provedor e frescor. `0` desliga o cache e sempre revalida.

### URL preguiçosa (callable)

`userinfo_url` aceita uma `str` **ou** um callable sem argumentos,
resolvido **a cada chamada**. Isso deixa você passar uma property de
settings que só é lida em runtime (útil quando a URL vem do ambiente
tarde, ou muda entre tenants):

```python
auth = IntrospectionAuth(
    userinfo_url=lambda: settings.IAGRO_USERINFO_URL,
)
```

### Claims customizadas

Se o seu provedor usa nomes diferentes, ajuste:

```python
auth = IntrospectionAuth(
    userinfo_url=settings.IDP_USERINFO_URL,
    required_app="famacha",
    app_claim="apps",        # em vez de "access_apps"
    subject_claim="user_id", # em vez de "sub"
)
```

### Cliente HTTP

Por padrão a instância cria **um** `httpx.AsyncClient` compartilhado
(preguiçoso, com `httpx.Timeout(timeout)`) e o reaproveita. Você pode
injetar o seu — útil em testes ou pra compartilhar pool/limites:

```python
import httpx

client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
auth = IntrospectionAuth(
    userinfo_url=settings.IDP_USERINFO_URL,
    http_client=client,
)
```

O cache é **por instância**, não global — a aplicação pode criar várias
`IntrospectionAuth` (uma por upstream) sem que uma vaze estado na outra.

## Testando

Injete um `httpx.AsyncClient` com `MockTransport` pra não tocar a rede:

```python
import httpx
import pytest
from fastapi.security import HTTPAuthorizationCredentials

from tempest_fastapi_sdk import IntrospectionAuth


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer tok"
    return httpx.Response(200, json={"sub": "…", "access_apps": ["famacha"]})


@pytest.mark.asyncio
async def test_valid_token() -> None:
    auth = IntrospectionAuth(
        userinfo_url="https://id.example.com/users/me",
        required_app="famacha",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    claims = await auth.get_claims(creds)
    assert claims["access_apps"] == ["famacha"]
```

## Recap

- `IntrospectionAuth` é pro padrão **resource server**: valida bearers
  opacos contra um `userinfo` upstream — não emite tokens.
- `get_claims` e `get_user_id` são **bound methods** usáveis direto como
  `Depends(auth.get_claims)` / `Depends(auth.get_user_id)`.
- Sem credenciais → 401; token rejeitado/upstream fora → 401; app não
  liberada → 403; subject inválido → 401.
- Cache em processo por token com TTL em `time.monotonic()`; `401`/`403`
  evicta; `cache_ttl_seconds=0` desliga.
- `userinfo_url` pode ser `str` ou callable (resolvido por chamada);
  claims (`app_claim`, `subject_claim`) e o cliente `httpx` são
  configuráveis. Cache e cliente são **por instância**.

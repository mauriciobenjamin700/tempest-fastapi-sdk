# Pegar o `current_user` da requisição

Use para injetar o usuário autenticado nas suas rotas, com auth opcional (`soft=True`) e guards dentro do service.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md).

`make_auth_router` **emite** o JWT pair (login/activate devolvem `access_token` + `refresh_token`). Mas e depois? Quando o frontend manda `Authorization: Bearer <access_token>` nas **suas próprias** rotas, você precisa de uma dependency que decodifica o token e resolve o usuário.

Desde a v0.49.0, o próprio `UserAuthService` constrói essa dependency — `current_user_dependency()`. Ela:

1. Procura o token em três lugares, na ordem **header → cookie → query string** (primeiro hit ganha): `Authorization: Bearer <jwt>` via `HTTPBearer`, depois o cookie (`cookie_name`) e por último o parâmetro de query (`query_param`).
2. Decodifica e verifica o JWT com **o mesmo `JWTUtils` que o service usou pra assinar** — sem segundo segredo pra manter sincronizado.
3. Pega o `sub` (id do usuário) do payload, abre uma sessão a partir do `db=` e devolve o `UserModel` persistido.
4. Recusa a conta desativada com `403` *(v0.305.0+)* — ver abaixo.
5. Responde `401` quando o `sub` não acha conta — conta apagada depois de o token ser emitido *(v0.344.0+; antes era `404`, que o front não lê como "sessão encerrada")*. Com `soft=True`, devolve `None`.

## 1. Declare a dependency uma vez

O service já tem `user_model`, `JWTUtils` e a sessão — então não precisa escrever `load_user` à mão. Junte as duas variantes no `src/api/dependencies/auth.py`:

```python
# src/api/dependencies/auth.py
from src.api.dependencies.resources import auth_service

get_current_user = auth_service.current_user_dependency()
get_current_user_or_none = auth_service.current_user_dependency(soft=True)
```

!!! info "Requer `db=` no `UserAuthService`"
    `current_user_dependency` precisa do `db=` (o `AsyncDatabaseManager` do [Setup mínimo](setup.md#setup-minimo)) — sem ele, levanta `RuntimeError`. Como reusa o `self.jwt` interno, o token é validado com o **mesmo** segredo que assinou — o footgun de `JWT_SECRET` divergente some.

    O usuário é carregado na sessão **do request** (`db.session_dependency` por default), a mesma que os seus repositórios usam. Ou seja: a instância vem *attached* e você pode mutar/`refresh` sem tomar `InvalidRequestError: Instance is not persistent within this Session`. Se os seus repositórios dependem de outro callable de sessão (um `get_session` local, por exemplo), passe **esse mesmo callable** em `session_dependency=` — o FastAPI cacheia sub-dependency por callable, então um wrapper diferente abre uma segunda sessão e volta a destacar o usuário.

    A mesma garantia vale para as rotas autenticadas do próprio `make_auth_router` (`/auth/password-change`, `/auth/mfa/*`): elas carregam o usuário na sessão do request desde a **0.171.1**. Antes disso o router abria uma sessão privada e devolvia a instância já *detached*, então a escrita era descartada em silêncio e o `refresh` seguinte estourava — `/auth/password-change` respondia 500 mantendo a senha antiga.

!!! warning "Conta desativada não passa, mesmo com token válido (v0.305.0+)"
    O `login` já recusa conta inativa, mas um token emitido **antes** da
    desativação continua válido até expirar — o access TTL inteiro. Por isso a
    dependency confere `is_active` depois de carregar o usuário e responde
    `403` `ACCOUNT_INACTIVE` (o mesmo `AuthExceptions.account_inactive` do login); até a 0.304.0 ela devolvia a linha e cada rota precisava
    lembrar do `require_active`. As rotas autenticadas do `make_auth_router`
    (`/auth/me`, troca de senha e de e-mail, MFA, passkeys) fazem a mesma
    conferência.

    - `soft=True` devolve `None` para conta inativa, como para token inválido.
    - `inactive_exception=SuaExcecao` troca o `403` pela sua classe.
    - `require_active=False` volta ao comportamento antigo — para a rota que
      precisa atender a conta inativa (reativação, por exemplo).

    Medido em `tests/auth/test_current_user_active.py`: token emitido, conta
    desativada, request com o token → `403`; com `require_active=False` →
    `200`.

!!! tip "A mensagem do `401` na sua língua"
    Token ausente, inválido ou de conta que não existe mais responde `401`
    `UNAUTHORIZED` com `detail` em inglês
    (`"Authorization token is missing or invalid"`, a constante
    `MISSING_TOKEN_MESSAGE`). Serviço que responde em PT-BR passa a própria
    frase — é ela que o app mostra ao usuário:

    ```python
    from tempest_fastapi_sdk import UserAuthService

    def build_current_user(auth_service: UserAuthService):
        return auth_service.current_user_dependency(
            unauthorized_message="Sua sessão expirou. Entre de novo.",
        )
    ```

    O `code` continua `UNAUTHORIZED`: o front decide pelo `code`, a frase é
    só o texto. Medido em `tests/auth/test_current_user_active.py`.

!!! tip "Não é só o header: cookie e query string também valem"
    A dependency tenta **header → cookie → query string** e para no primeiro hit, então a mesma linha acima serve cliente bearer e cliente cookie.

    - **Cookie — automático.** Com `AUTH_TOKEN_DELIVERY` em `"cookie"` ou `"both"`, o `cookie_name` é **auto-derivado** de `AUTH_ACCESS_COOKIE_NAME`, o mesmo cookie que o `/auth/login` bundled setou. Zero wiring extra: rota guardada por essa dependency já aceita o cookie. Em delivery bearer-only fica `None` (só header). Passe `cookie_name="..."` pra forçar um nome.
    - **Query string — opt-in.** Nunca é auto-derivada. Serve pra cliente que não consegue mandar header **nem** cookie. Para **SSE**, não ligue isto na mão: o `auth_service.sse_router(broker)` troca a sessão por um ticket de stream que não vale em nenhuma outra rota. O `query_param` cru fica para o resto:

    ```python
    # src/api/dependencies/auth.py
    from src.api.dependencies.resources import auth_service

    get_current_user = auth_service.current_user_dependency(
        cookie_name="access_token",
        query_param="access_token",
    )
    ```

    Detalhe do fluxo SSE na [receita SSE »](../sse.md#autenticacao).

!!! warning "Token na URL vaza"
    Query string entra em log de acesso, histórico do browser e header `Referer`. Habilite `query_param` só sobre TLS, só com **access token de vida curta** (nunca refresh token), e limpe o valor do formato de log. Se o cliente compartilha a origem da API, prefira cookie de sessão (`withCredentials`).

??? note "Sem `UserAuthService`? Monte a dependency na mão"
    Se o seu serviço não usa o flow bundled, a primitiva `make_jwt_user_dependency` aceita qualquer `JWTUtils` + um `user_loader` async de um argumento:

    ```python
    from uuid import UUID

    from tempest_fastapi_sdk import JWTUtils, make_jwt_user_dependency

    from src.api.dependencies.resources import db
    from src.core.settings import settings
    from src.db.models import UserModel
    from src.db.repositories import UserRepository

    tokens: JWTUtils = JWTUtils(
        secret=settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


    async def load_user(subject: str) -> UserModel:
        """Resolve o subject do JWT (uma string UUID) para o usuário persistido."""
        async with db.get_session_context() as session:
            repo: UserRepository = UserRepository(session)
            return await repo.get_by_id(UUID(subject))


    get_current_user = make_jwt_user_dependency(tokens, load_user)
    get_current_user_or_none = make_jwt_user_dependency(tokens, load_user, soft=True)
    ```

    Atenção: aqui o `tokens` **precisa** usar o mesmo `JWT_SECRET` / `JWT_ALGORITHM` do login, senão todo token válido é rejeitado.

## 2. Injete na rota com `Depends`

```python
# src/api/routers/users.py
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user
from src.db.models import UserModel
from src.schemas import UserResponseSchema

router: APIRouter = APIRouter(prefix="/users", tags=["users"])


@router.get("/profile")
async def profile(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    """Devolve o usuário dono do bearer token da requisição."""
    return UserResponseSchema.model_validate(current)
```

`current` **é** o `UserModel` que o service resolveu — tipado, persistido, pronto pra uso. Token ausente ou inválido → `401 UnauthorizedException` antes do corpo da rota rodar.

!!! tip "Pro `/me` puro, não escreva nada"
    O router empacotado já monta `GET /auth/me` desde a v0.198.0, com
    `AuthUserSchema` (as colunas que `BaseUserModel` garante) como
    resposta. Só escreva o seu quando a rota fizer algo **além** de
    devolver a conta — agregar contadores, juntar o perfil de outra
    tabela. Se for só uma coluna extra na mesma tabela, subclasse o
    schema em vez de reescrever a rota:

    ```python
    from tempest_fastapi_sdk import AuthUserSchema, make_auth_router


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

!!! note "O seu `session_factory` não precisa garantir nada"

    As páginas HTML renderizam depois do commit que consome o token, e o
    `async_sessionmaker` do SQLAlchemy usa `expire_on_commit=True` por
    default — o que fazia a página ler coluna expirada e responder **500**
    (`MissingGreenlet`) até a v0.265.0. Agora o router recarrega a linha
    **só quando ela expirou**, checando `inspect(user).expired`, que não
    toca no banco. Com o `session_factory` do `AsyncDatabaseManager`
    (`expire_on_commit=False`) não há query extra nenhuma.

## 3. Auth opcional — `soft=True`

Para rotas que funcionam logado **e** anônimo (ex.: feed público que personaliza se houver login), use a variante `soft` — ela devolve `None` em vez de levantar:

```python
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user_or_none
from src.db.models import UserModel
from src.schemas import PostResponseSchema
from src.services import FeedService

feed_service = FeedService()

router = APIRouter()


@router.get("/feed")
async def feed(
    current: UserModel | None = Depends(get_current_user_or_none),
) -> list[PostResponseSchema]:
    """Feed público; personaliza o ranking quando há usuário logado."""
    if current is None:
        return await feed_service.public()
    return await feed_service.personalized(current.id)
```

!!! tip "Role e permission são o próximo passo"
    Quando a rota precisa de **papel** (`admin`) ou **permissão** (`users:write`) e não só "estar logado", troque por `make_role_dependency` / `make_permission_dependency`. Veja a [receita HTTP »](../http.md) — mesma `JWTUtils`, mesmo padrão de `Depends`.

## 4. Guards imperativos — checagem dentro do service / controller

As deps acima gateiam a **rota** (antes do handler rodar). Mas e quando você já tem o user em mãos, mais fundo na pilha (service, controller), e quer só **assertar** uma condição antes de continuar? Desde a v0.50.0 o SDK traz três guards prontos — sem reescrever `if user is None: raise ...` em todo serviço:

```python
from tempest_fastapi_sdk import (
    require_active,
    require_admin,
    require_authenticated,
)
```

| Guard | Levanta se | Status HTTP |
|-------|------------|-------------|
| `require_authenticated(user)` | `user is None` | 401 `UnauthorizedException` |
| `require_active(user)` | `None`, ou `not user.is_active` | 401 / 403 `ForbiddenException` |
| `require_admin(user)` | `None`, ou `not user.is_admin` | 401 / 403 `ForbiddenException` |

O detalhe que importa: cada um **devolve o user já estreitado** — sem `None` e com o tipo concreto preservado — então o resto da função para de ver `| None`:

```python
from tempest_fastapi_sdk import require_admin

from src.db.models import UserModel


class ReportService:
    async def delete_all(self, current: UserModel | None) -> None:
        """Só admin apaga relatórios."""
        admin: UserModel = require_admin(current)  # 401/403, ou devolve tipado
        await self.repository.purge(by=admin.id)   # `admin` não é mais `| None`
```

Combina direto com o `current_user_dependency(soft=True)`: a rota passa `UserModel | None`, e o guard decide no service.

!!! tip "Já tem o `auth_service`? Use os mirrors estáticos"
    Os mesmos guards existem como staticmethods em `UserAuthService` — `auth_service.require_admin(current)` — pra quando você já injeta o service e não quer um import extra. Mesma semântica, mesma exceção.

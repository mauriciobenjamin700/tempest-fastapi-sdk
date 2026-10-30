# Login e cadastro server-side

Seu serviço serve o próprio HTML — um painel, um portal, uma ferramenta interna — e precisa de login, cadastro e ativação de conta **sem** um frontend separado. O `make_auth_router` fala JSON com uma SPA; aqui você monta as mesmas regras como **páginas**: o navegador recebe um formulário, envia um `<form>` POST sem JavaScript, e a sessão é um cookie.

`make_auth_pages_router` entrega as páginas prontas sobre o mesmo `UserAuthService` e uma sessão opaca e revogável (`SessionAuth`). Os templates padrão saem em pt-BR e en-US, e você sobrescreve qualquer um.

## O que você vai montar

- `GET/POST /login` — e-mail e senha; conta com MFA passa por `/login/mfa`.
- `GET/POST /signup` — formulário gerado do `signup_schema`.
- `GET /activate/{token}` — o link do e-mail ativa a conta **e já abre a sessão**.
- `GET/POST /activation/request` — reenvio do link de ativação.
- `POST /logout` — revoga a sessão no servidor.
- Uma dependency que devolve o **usuário** da sessão para as suas rotas.

## Instalação

```bash
uv add "tempest-fastapi-sdk[auth,email,sqlite]"
```

`[auth]` traz o bcrypt e o PyJWT; `[email]` traz o Jinja2 que renderiza as páginas; `[sqlite]`, o driver do exemplo.

## O exemplo completo

```python
# app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseUserModel,
    MemorySessionStore,
    SessionAuth,
    UserAuthService,
    make_auth_pages_router,
    make_session_user_dependency,
    make_user_token_model,
    redirect_to_login,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings import AuthSettings, JWTSettings, SessionSettings


class UserModel(BaseUserModel):
    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")

db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")

auth_service = UserAuthService(
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=AuthSettings(
        AUTH_ACTIVATION_URL_TEMPLATE="http://localhost:8000/activate/{token}",
    ),
    jwt_settings=JWTSettings(JWT_SECRET="change-me-to-32-or-more-characters"),
    email=None,
)

session_auth = SessionAuth(
    user_model=UserModel,
    store=MemorySessionStore(),
    settings=SessionSettings(SESSION_COOKIE_SECURE=False),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Cria as tabelas na subida e fecha a conexão na descida."""
    await db.connect()
    await db.create_tables()
    yield
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)
app.include_router(
    make_auth_pages_router(
        auth_service,
        db.session_dependency,
        session_auth=session_auth,
    )
)

current_user = Depends(
    make_session_user_dependency(
        UserModel,
        db.session_dependency,
        session_auth=session_auth,
        on_missing=redirect_to_login("/login"),
    )
)


@app.get("/")
async def home(user: UserModel = current_user) -> dict[str, str]:
    """Só quem tem sessão chega aqui."""
    return {"hello": user.email}
```

Rode com `uv run uvicorn app:app` e abra `http://localhost:8000/`. O que acontece, medido com o `TestClient`:

```text
GET  /                      → 303 /login?next=/
GET  /signup                → 200 (formulário)
POST /signup                → 200 "Confira seu e-mail" (com email=None, o link aparece na página)
GET  /activate/{token}      → 303 /   + cookie de sessão
GET  /                      → 200 {"hello": "ana@example.com"}
POST /logout                → 303 /login
GET  /                      → 303 /login?next=/
POST /login (sem o token)   → 403
```

O idioma da página segue o `?lang=`, depois o `Accept-Language` do navegador, depois o `AUTH_DEFAULT_LOCALE` (`pt-BR` por padrão).

## Peça por peça

### A sessão: `SessionAuth`

As páginas abrem uma sessão **opaca**: o cookie carrega um id aleatório, e o store guarda só o hash dele. É o que permite o logout revogar de verdade — um cookie copiado para de funcionar no instante do `POST /logout`. `MemorySessionStore` serve para dev e testes; em produção, com mais de uma réplica, use `RedisSessionStore`. O cookie segue os `SESSION_*` do `SessionSettings` (`HttpOnly` sempre, `Secure` e `SameSite` configuráveis).

!!! warning "`SESSION_COOKIE_SECURE=False` só em HTTP local"
    O exemplo desliga o `Secure` porque roda em `http://localhost`. Em produção, atrás de HTTPS, deixe o padrão (`True`). Um cookie `Secure` não volta numa conexão HTTP puro, e aí todo formulário responde `403` (o token CSRF também é cookie).

### O router de páginas

`make_auth_pages_router(service, session_factory, session_auth=...)` monta as rotas na raiz do site (`AUTH_PAGES_PREFIX=""`). Para pôr sob outro caminho, mude o `AUTH_PAGES_PREFIX` (por exemplo `/account`). Não use o mesmo prefixo do `make_auth_router` (`/auth`): os dois responderiam `POST /auth/login`.

Nenhuma rota de página aparece no OpenAPI (`include_in_schema=False`).

!!! warning "Montou também o `make_auth_router`? Compartilhe o orçamento"
    Cada router cria o próprio `AttemptThrottle` em memória quando você não passa um. Com os dois montados, um e-mail ganharia as tentativas de `/auth/login` **e** as de `/login`. Passe a mesma instância aos dois (e, com mais de um worker, um backend Redis):

    ```python
    from fastapi import FastAPI

    from tempest_fastapi_sdk import (
        AttemptThrottle,
        InMemoryThrottleBackend,
        make_auth_pages_router,
        make_auth_router,
    )

    from app import auth_service, db, session_auth

    app = FastAPI()

    login_throttle = AttemptThrottle(
        InMemoryThrottleBackend(), max_attempts=5, window_seconds=900
    )

    app.include_router(
        make_auth_router(
            auth_service,
            session_factory=db.session_dependency,
            login_throttle=login_throttle,
        )
    )
    app.include_router(
        make_auth_pages_router(
            auth_service,
            db.session_dependency,
            session_auth=session_auth,
            login_throttle=login_throttle,
        )
    )
    ```

### O link de ativação aponta para a página

`AUTH_ACTIVATION_URL_TEMPLATE` é a URL que vai no e-mail. Aponte para o `GET /activate/{token}` das páginas, no domínio público do serviço:

```dotenv
AUTH_ACTIVATION_URL_TEMPLATE=https://app.example.com/activate/{token}
```

Quem clica fica ativado **e logado** (`amr=["email"]`). Uma conta com MFA vai para `/login/mfa` antes da sessão abrir.

!!! info "Por que o link já loga"
    É a escolha que poupa um passo: o e-mail acabou de provar que a pessoa controla aquela caixa. A contrapartida é a de todo *magic link*, nas duas direções. Quem recebe o link de outra pessoa entra na conta dela. E um atacante pode cadastrar uma conta, não ativar, e mandar **o próprio** link para alguém: quem clica fica logado na conta do atacante e pode digitar ali dados que ele depois lê (o *login CSRF* do formulário, por outro caminho). O token é de uso único e expira em `AUTH_ACTIVATION_TTL_SECONDS`. Se esse risco pesa no seu produto, mostre de que conta se trata logo após a ativação.

### Protegendo suas rotas

`make_session_user_dependency(UserModel, session_factory, session_auth=...)` devolve o **usuário**, não a sessão. Ele relê a conta a cada request: uma conta desativada depois do login perde o acesso no clique seguinte, sem esperar a sessão expirar.

- `on_missing=redirect_to_login("/login")` manda o navegador para o login com `?next=` (o caminho e a query de onde ele estava), e o login devolve ele lá.
- `require_mfa=True` exige que a sessão tenha passado pelo segundo fator (`otp` no `amr`); sem isso, `403`.

## MFA

Com `AUTH_MFA_ENABLED=true`, passe o modelo de códigos de recuperação:

```python
from tempest_fastapi_sdk import make_auth_pages_router, make_user_recovery_code_model

from app import auth_service, db, session_auth

UserRecoveryCodeModel = make_user_recovery_code_model(user_table="users")

router = make_auth_pages_router(
    auth_service,
    db.session_dependency,
    session_auth=session_auth,
    recovery_code_model=UserRecoveryCodeModel,
)
```

Uma conta com TOTP confirmado sai do `POST /login` para `/login/mfa` **sem sessão**. A etapa pendente fica num cookie `HttpOnly` de curta duração, nunca na URL. O código passa pelo mesmo `mfa_verify` da API JSON: o código TOTP é de uso único e tem orçamento por conta. A sessão aberta depois registra `amr=["pwd", "otp"]`.

## Cadastro com campos seus

O formulário de `/signup` é gerado do `signup_schema`, o mesmo do `make_auth_router`. Campo extra que também é coluna do usuário é gravado antes do insert; o `on_signup` roda depois, na mesma transação:

```python
from pydantic import Field

from tempest_fastapi_sdk import SignupSchema, make_auth_pages_router

from app import auth_service, db, session_auth


class ProductSignupSchema(SignupSchema):
    """E-mail, senha, nome — e o telefone."""

    phone: str = Field(title="Telefone", max_length=20)


router = make_auth_pages_router(
    auth_service,
    db.session_dependency,
    session_auth=session_auth,
    signup_schema=ProductSignupSchema,
)
```

O `title` do campo vira o rótulo. Um erro de validação re-renderiza o formulário com `422`, mensagem por campo e o que foi digitado — menos a senha. Com `AUTH_SIGNUP_ENABLED=false`, `/signup` nem é montado (`404`).

## Sobrescrevendo os templates

Passe `template_dir` e coloque nele um arquivo com o mesmo nome. A busca é: `template_dir/<locale>/<nome>`, `template_dir/<nome>` e, por último, a cópia do SDK.

| Template | Setting | Contexto |
|---|---|---|
| `login.html` | `AUTH_LOGIN_PAGE_TEMPLATE` | `email`, `next`, `error` |
| `login_mfa.html` | `AUTH_LOGIN_MFA_PAGE_TEMPLATE` | `next`, `error` |
| `signup.html` | `AUTH_SIGNUP_PAGE_TEMPLATE` | `fields`, `values`, `errors`, `error`, `min_length` |
| `signup_done.html` | `AUTH_SIGNUP_DONE_TEMPLATE` | `email`, `activation_url` |
| `activation_request.html` | `AUTH_ACTIVATION_REQUEST_PAGE_TEMPLATE` | `email`, `error` |
| `activation_request_done.html` | `AUTH_ACTIVATION_REQUEST_DONE_TEMPLATE` | `email` |

Todo template recebe também `csrf_field`, `csrf_token`, `prefix` e `signup_enabled`. **Todo `<form method="post">` precisa do campo hidden**, ou o envio volta `403`:

```html
<input type="hidden" name="{{ csrf_field }}" value="{{ csrf_token }}">
```

Para mudar só o visual, sobrescreva o `_auth_layout.html`: todas as páginas estendem dele, e o CSS mora no bloco `styles`.

!!! tip "Prefere a camada `ui` tipada?"
    As páginas padrão são Jinja2 para não exigir o extra `[ssr]`. Num serviço com a camada `ui`, você pode escrever as suas com `form_for`/`parse_form` e chamar o `UserAuthService` e o `SessionAuth` no router. O que não pode faltar é o que este router já faz: CSRF por campo de formulário (`tempest_fastapi_sdk.auth.FormCSRF`), `next` só para caminho do próprio site (`is_local_path`) e os mesmos orçamentos de tentativa.

## Segurança: o que já vem feito

Cada item abaixo tem teste em `tests/auth/test_auth_pages.py`.

- **CSRF em todo POST, inclusive o login.** Token aleatório num cookie `HttpOnly` e repetido num campo hidden, comparados em tempo constante (*double-submit*). Sem ele, uma página de outro site poderia logar o navegador da vítima na conta do atacante (*login CSRF*). A recusa vem antes de tudo e não gasta tentativa. Com cookie `Secure`, o nome leva o prefixo `__Host-`, que o navegador só aceita sem `Domain`: um subdomínio irmão não consegue plantar o cookie (e o campo) para passar na checagem.
- **`next` sem open redirect.** Só caminho do próprio site (`/x`). `https://...`, `//host` e `/\host` são ignorados, e a redireção vai para o `AUTH_PAGES_LOGIN_REDIRECT`. Os redirects dos settings são validados na subida.
- **Recusa genérica.** Senha errada, conta inexistente e conta inativa respondem a mesma página `401` ("E-mail ou senha inválidos."). Com `AUTH_REVEAL_INACTIVE_ACCOUNT=true`, a senha certa de uma conta inativa responde `403` com a mensagem do serviço, como na API JSON.
- **Orçamentos.** Login por e-mail (a mesma regra da API JSON, pelo mesmo código) e por IP (`login_ip_throttle`), cadastro e reenvio por IP, MFA por conta. O `429` re-renderiza a página com `Retry-After`.
- **Reenvio sem enumeração pelo conteúdo.** `/activation/request` mostra a mesma página com ou sem conta. O **tempo** difere: só uma conta pendente espera o envio do e-mail — a API JSON tem a mesma característica.
- **O cadastro revela e-mail já usado.** Um e-mail existente volta `409` no campo, como na API JSON. Se isso importa no seu produto, desligue o cadastro público (`AUTH_SIGNUP_ENABLED=false`).
- **Sessão.** Rotação no login (`SESSION_ROTATE_ON_LOGIN`), revogação real no logout.

!!! note "E o formulário de redefinir senha?"
    As páginas de reset continuam nas páginas de link do `make_auth_router` (`AUTH_BACKEND_LINKS=true`). Elas não pedem token CSRF: o POST só funciona com o token secreto que veio na URL do e-mail, e quem tem esse token já consegue redefinir a senha sem forjar nada.

    O `make_auth_pages_router` registra o `session_auth.revoke_all` no
    `UserAuthService` (`add_session_revoker`), então redefinir a senha —
    e também trocar a senha ou o e-mail, ou os resets de MFA — derruba os
    cookies de sessão que as páginas abriram. Um cookie copiado antes do
    reset para de funcionar junto com a senha antiga.

## Testando

O `TestClient` faz o que o navegador faz: abre o formulário, lê o hidden e envia.

```python
import re

from fastapi.testclient import TestClient

from app import app


def test_signup_and_activation_sign_in() -> None:
    """Cadastro, link de ativação e sessão — sem JavaScript."""
    with TestClient(app, base_url="http://localhost:8000") as client:
        page = client.get("/signup")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert token is not None
        done = client.post(
            "/signup",
            data={
                "csrf_token": token.group(1),
                "email": "ana@example.com",
                "password": "strong-pass-12",
            },
        )
        link = re.search(r'href="http://localhost:8000(/activate/[^"]+)"', done.text)
        assert link is not None
        client.get(link.group(1))
        assert client.get("/").json() == {"hello": "ana@example.com"}
```

## Recap

- `make_auth_pages_router` monta login, MFA, cadastro, ativação, reenvio e logout como HTML, sobre o mesmo `UserAuthService`.
- A sessão é `SessionAuth`: opaca, revogável, configurada pelos `SESSION_*`.
- `make_session_user_dependency` protege suas rotas e devolve o usuário, relido a cada request; `redirect_to_login` leva de volta com `next`.
- Todo POST leva o `csrf_token`; um template seu precisa do campo hidden.
- Aponte o `AUTH_ACTIVATION_URL_TEMPLATE` para `/activate/{token}`, e o link do e-mail já entra logado.

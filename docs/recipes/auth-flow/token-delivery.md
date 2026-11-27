---
description: "Use para decidir se o par de JWT sai no corpo (bearer), em cookie ou nos dois com `AUTH_TOKEN_DELIVERY`; contém o modo cookie e o modo both."
---

# Entrega de token: bearer, cookie ou os dois

Use para decidir se o par de JWT sai no corpo, em cookie ou nos dois.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md).

*(v0.87.0+)*

Por padrão o login devolve `access_token` / `refresh_token` **no corpo** da resposta e o cliente reenvia como `Authorization: Bearer <token>`. É ótimo pra apps mobile/API, mas um SPA no browser precisa guardar o token em algum lugar acessível por JS — exposto a XSS. O `AUTH_TOKEN_DELIVERY` deixa você escolher.

| Modo | O que muda | Pra quem |
|------|------------|----------|
| `bearer` *(default)* | Tokens **só no body**. Comportamento histórico, retrocompatível. | Mobile, APIs, clientes que mandam `Authorization`. |
| `cookie` | Tokens setados como cookies **`HttpOnly`** nos mesmos paths (`/auth/login`, `/auth/refresh`, `/auth/logout`); o body volta com os tokens `null`. | SPA no browser — o token nunca fica visível pro JS (defesa contra XSS). |
| `both` | Endpoints bearer ficam em `/auth/*` **e** um conjunto cookie paralelo é montado em `/auth/cookie/*`. | Um backend que serve web (cookie) **e** mobile (bearer) ao mesmo tempo. |

!!! danger "Cookie `Secure` exige HTTPS"
    Com `AUTH_COOKIE_SECURE=true` (default) o browser **só** reenvia o cookie por HTTPS. Servindo o backend em **HTTP puro**, o cookie é descartado e a sessão nunca persiste (login parece funcionar mas nada fica logado). Em produção, ponha TLS na frente e mantenha `true`; em HTTP local de dev, `AUTH_COOKIE_SECURE=false`.

## Modo cookie

```bash
# .env
AUTH_TOKEN_DELIVERY=cookie
AUTH_COOKIE_SECURE=true          # false só em HTTP puro
AUTH_COOKIE_SAMESITE=lax         # "none" (+Secure) se o SPA for cross-site
```

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import UserAuthService, make_auth_router

from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

auth_service = UserAuthService(
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
)
app = FastAPI()


app.include_router(make_auth_router(auth_service, session_factory=db.session_dependency))
```

Fluxo no frontend — **não guarda token nenhum**, só chama os endpoints com `credentials: "include"`:

```javascript
// login: o browser grava os cookies HttpOnly sozinho
await fetch("/auth/login", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});

// requests autenticadas: o cookie access viaja automático
await fetch("/api/me", { credentials: "include" });

// renovar sessão: o cookie refresh é lido no backend, sem body
await fetch("/auth/refresh", { method: "POST", credentials: "include" });

// logout: limpa os cookies (e revoga o refresh se houver refresh_token_model)
await fetch("/auth/logout", { method: "POST", credentials: "include" });
```

A dependency de `current_user` (veja [Pegando o `current_user`](current-user.md)) passa a **ler o access token do cookie** automaticamente quando a entrega envolve cookie — o header `Authorization` ainda tem prioridade se vier.

## Modo both

```bash
AUTH_TOKEN_DELIVERY=both
```

Monta os dois conjuntos, sem colisão de rota:

```text
# bearer (body):
POST /auth/login
POST /auth/refresh
POST /auth/logout

# cookie (HttpOnly):
POST /auth/cookie/login
POST /auth/cookie/refresh
POST /auth/cookie/logout
```

!!! info "O que continua no body"
    A entrega cookie cobre o ciclo **login / refresh / logout**. Ativação (`POST /auth/activate/{token}`), auto-login no signup (`AUTH_AUTO_ACTIVATE`) e o `POST /auth/mfa/verify` ainda retornam o par JWT no body, independente do `AUTH_TOKEN_DELIVERY`.

!!! tip "Ajuste fino dos cookies: `cookie_config=`"
    As settings `AUTH_COOKIE_*` cobrem o caso comum. Quando você precisa de
    controle por atributo — nome, `max_age`, path do refresh, `Domain` — passe um
    `AuthCookieConfig` no `make_auth_router`; ele ganha das settings:

    ```python
    from tempest_fastapi_sdk import AuthCookieConfig, make_auth_router

    app.include_router(
        make_auth_router(
            auth_service,
            session_factory=db.session_dependency,
            token_delivery="cookie",
            cookie_config=AuthCookieConfig(
                access_name="session",
                refresh_name="session_refresh",
                access_max_age=900,
                refresh_max_age=1209600,
                samesite="none",
                secure=True,
                domain=".exemplo.com",
            ),
        ),
    )
    ```

    Espelhe `access_max_age` no `JWT_ACCESS_TTL_SECONDS` e `refresh_max_age` no
    `JWT_REFRESH_TTL_SECONDS`: cookie que vive mais que o token deixa o browser
    mandando credencial morta, e o inverso desloga antes da hora. O
    `refresh_path` é estreitado pelo router pro endpoint de refresh, então o
    token longo não viaja em request comum. `http_only=True` é o ponto do modo
    cookie — não desligue.

    Fora do `make_auth_router`, as mesmas regras valem via
    `apply_auth_cookies(response, access_token=..., refresh_token=..., config=...)`
    e `clear_auth_cookies(response, config=...)` — útil em rota de login própria
    (o callback de OAuth da [receita de login social](../oauth.md), por exemplo).

!!! tip "CORS com credenciais"
    Pra um SPA em outra origem enviar/receber cookies, o backend precisa de `allow_credentials=True` no CORS **e** `AUTH_COOKIE_SAMESITE=none` + `AUTH_COOKIE_SECURE=true` (portanto HTTPS). Same-origin (frontend servido do mesmo domínio da API) roda com o default `lax`.

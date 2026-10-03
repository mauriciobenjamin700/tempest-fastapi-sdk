# SPA React dentro do FastAPI

Esta receita monta uma stack fullstack com **um único deploy**: uma SPA React
servida pelo próprio processo FastAPI, com o [`tempest-react-sdk`](https://github.com/mauriciobenjamin700/tempest-react-sdk)
do lado do cliente falando com este SDK do lado do servidor. 🚀

Três modos, na ordem em que você vai usá-los:

1. **Desenvolvimento** — `vite dev` na 5173 fazendo proxy de `/api` para o
   FastAPI na 8000. Hot reload funcionando.
2. **Produção mesma origem** — `make_spa_router` serve o `dist/` do Vite pelo
   FastAPI. Sem CORS, sem cookie `SameSite=None`, um container.
3. **Scaffold** — `create-tempest-app` cria o `web/` dentro do projeto, e um
   `Dockerfile` multi-stage compila a SPA e copia o build.

!!! info "Por que mesma origem é o default recomendado"
    Servir a SPA e a API na mesma origem elimina de uma vez CORS, preflight,
    `SameSite=None; Secure` no cookie de refresh e a configuração de CSRF que
    vem com ele. O navegador nunca faz uma requisição cross-origin, então
    metade da superfície de configuração de auth simplesmente não existe.

## 1. Desenvolvimento — Vite com proxy

### O lado React

```bash
npx create-tempest-app web
cd web && npm install
```

O template já vem com o `vite.config.ts` apontando para o helper do SDK.
Descomente o proxy:

```typescript
// web/vite.config.ts
import { createViteConfig } from "tempest-react-sdk/vite";

export default createViteConfig({
    proxy: { "/api": "http://127.0.0.1:8000" },
});
```

`createViteConfig` já liga o `@vitejs/plugin-react`, o alias `@` → `src` e os
defaults de dev server (porta 5173, host `127.0.0.1`). Uma string no `proxy` é
expandida para `{ target, changeOrigin: true }`; passe um objeto para controlar
tudo.

### O lado FastAPI

Nada a fazer além de subir na 8000 — é o default do scaffold:

```bash
python main.py     # uvicorn em 127.0.0.1:8000
```

!!! tip "Com proxy você não precisa de CORS em dev"
    O navegador só conversa com a 5173; é o Vite que fala com a 8000, do lado
    do servidor. Então não há requisição cross-origin e o `CORSSettings` pode
    ficar desligado — inclusive em dev. Se você preferir chamar a API direto
    (sem proxy), aí sim precisa de CORS:

    ```python
    from tempest_fastapi_sdk import apply_cors

    apply_cors(app, origins=["http://127.0.0.1:5173"], allow_credentials=True)
    ```

### O cliente HTTP

```typescript
// web/src/lib/api.ts
import { createApiClient } from "tempest-react-sdk";

export const api = createApiClient({
    baseURL: import.meta.env.VITE_API_URL ?? window.location.origin,
    withCredentials: true,
});
```

Usar a **origem atual** como `baseURL` é o que faz o mesmo código servir os dois
modos sem `if`: em dev a origem é a do Vite (5173) e o proxy encaminha `/api`
para o FastAPI; em produção a SPA e a API estão na mesma origem e o caminho
resolve direto. O `VITE_API_URL` fica como escape para o caso de origens
separadas.

!!! danger "Dois detalhes de URL que quebram silenciosamente"
    **`baseURL` tem que ser absoluta.** O cliente monta a URL com
    `new URL(path, baseURL)`, e uma base relativa não é uma URL válida:

    ```typescript
    createApiClient({ baseURL: "/api" });   // ❌ TypeError: Invalid URL
    ```

    **Um `path` iniciado por `/` ignora o path da base.** Isso é semântica de
    `URL`, não do SDK:

    ```typescript
    const api = createApiClient({ baseURL: "https://x.dev/api" });
    await api.get("/users");   // → https://x.dev/users        (perdeu o /api)
    await api.get("users");    // → https://x.dev/api/users     ✅
    ```

    Escolha um dos dois e seja consistente: origem na base + prefixo no path
    (`baseURL: origin` + `get("/api/users")`), ou prefixo na base + path
    relativo (`baseURL: origin + "/api"` + `get("users")`). Misturar os dois é
    o que produz um 404 que parece bug de rota no backend.

## 2. Produção — FastAPI serve o build

```bash
cd web && npm run build     # gera web/dist/
```

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import make_spa_router, register_exception_handlers

from src.api.routers import users_router


def create_app() -> FastAPI:
    """Build the application, API first and the SPA last.

    Returns:
        FastAPI: The configured application.
    """
    app = FastAPI(title="My Service")
    register_exception_handlers(app)
    app.include_router(users_router, prefix="/api/users", tags=["users"])
    app.include_router(make_spa_router("web/dist"))
    return app


app = create_app()
```

!!! danger "`make_spa_router` vai por último — sempre"
    Ele registra um catch-all `GET /{path:path}`. O FastAPI casa rotas na
    ordem de registro, então **qualquer router incluído depois dele fica
    inalcançável**. Inclua toda a API primeiro.

### O que o router resolve

Montar `StaticFiles` sozinho não serve uma SPA. Um router client-side é dono de
caminhos como `/users/42`, que existem no navegador e **não** no disco — então
um mount estático puro devolve 404 em todo deep link e em todo refresh de
página. A peça que falta é o **fallback de SPA**.

Além disso, três detalhes que são fáceis de errar:

| Detalhe | Comportamento |
| --- | --- |
| Cache do `index.html` | `no-store, must-revalidate` |
| Cache dos assets com hash | `public, max-age=31536000, immutable` |
| Caminhos de API | Excluídos do fallback — 404 JSON, não HTML |
| Métodos | Só `GET`/`HEAD` caem no fallback |
| Path traversal | `..` (em qualquer codificação) não escapa do `dist/` |
| Headers de segurança | CSP de **aplicação** (`DEFAULT_SPA_SECURITY_HEADERS`) |

!!! warning "O bug clássico do cache invertido"
    O `index.html` é o **único** arquivo cujo nome não muda entre deploys. Se
    ele for cacheado, o navegador continua carregando o bundle antigo depois
    de um deploy — e os assets novos, com hash novo, nunca são pedidos. Por
    isso o documento é `no-store` e os assets são `immutable`: exatamente o
    contrário do que a intuição sugere.

!!! check "Por que 404 de API não pode virar HTML"
    Se `/api/typo` devolvesse o `index.html` com **200**, o cliente receberia
    HTML onde espera JSON e reportaria um erro de parse. Quem for depurar vai
    olhar o cliente, não a rota que não existe. Por isso os prefixos em
    `DEFAULT_EXCLUDED_PREFIXES` (`/api`, `/docs`, `/openapi.json`, `/health`,
    `/metrics`, `/logs`, `/admin`, `/auth`, `/redoc`) nunca caem no fallback.

    Usa outro prefixo? Passe o seu:

    ```python
    app.include_router(
        make_spa_router("web/dist", excluded_prefixes=("/api", "/graphql", "/rpc"))
    )
    ```

### A CSP que vem por default

O router serve uma **aplicação**, então a política é "tudo da mesma origem, nada de fora":

```text
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; font-src 'self' data:; connect-src 'self';
form-action 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'
```

Mais `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` e `Cross-Origin-Resource-Policy: same-origin`. Tudo isso é `DEFAULT_SPA_SECURITY_HEADERS`, e é o default de `security_headers=`.

!!! danger "Não passe `DEFAULT_STATIC_SECURITY_HEADERS` aqui (era o default até a v0.251.0)"
    Aquele conjunto é para **arquivo que você não confia** — upload de terceiro, anexo — e bloqueia execução por desenho: `default-src 'none'; sandbox`. Apontado para uma SPA compilada, ele bloqueia o bundle e a folha de estilo **da própria página**, e o `sandbox` sem `allow-scripts` bloqueia execução de script. Resultado: documento em branco.

    Medido em browser real (Playwright), com o default antigo:

    ```text
    Loading the stylesheet '/assets/app.css' violates the following Content
    Security Policy directive: "default-src 'none'"
    Blocked script execution in '/' because the document's frame is sandboxed
    and the 'allow-scripts' permission is not set
    ```

    Com o default novo: zero mensagem no console, script executou, CSS externo aplicado e atributo `style` inline aplicado.

    **Desde a v0.277.0 o `make_spa_router` recusa isso**, com `ValueError`, em
    vez de servir a página em branco. A checagem é sobre a **forma** da
    política — `sandbox` sem `allow-scripts`, ou `default-src 'none'` sem
    `script-src` —, então um equivalente escrito à mão cai no mesmo lugar.
    `security_headers` continua sendo override cru: quem realmente quer a
    política estrita passa `allow_blocking_headers=True`, o que separa "eu quis
    isso" de "copiei um snippet antigo".

!!! info "Por que `'unsafe-inline'` continua em `style-src`"
    React — e as bibliotecas de componente construídas sobre ele — escreve atributo `style` inline. Política que quebra a UI é política que é deletada. Ela fica **restrita a estilo**: `script-src` segue `'self'`, então `<script>` injetado ou handler inline continua recusado.

    Quem controla a própria árvore de componentes pode apertar para `style-src 'self'` mais `style-src-attr 'unsafe-inline'` e passar o resultado em `security_headers=`.

### Falha cedo, não em staging

```pycon
>>> make_spa_router("web/dist")
FileNotFoundError: SPA build directory not found: /app/web/dist. Run the
frontend build (e.g. `npm run build`) before starting the app, or point
`dist_dir` at the right path.
```

Levantar no wiring é deliberado. A alternativa é um serviço que **sobe
normalmente** e responde 404 em toda página — coisa que costuma ser descoberta
em staging, ou pior.

## 3. Auth compartilhada entre os dois SDKs

Mesma origem torna o fluxo de cookie o caminho mais simples: o refresh token
fica num cookie `HttpOnly` que o JavaScript não alcança.

### Servidor

```python
# src/api/app.py

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


app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        token_delivery="cookie",
        prefix="/api/auth",
    ),
)
```

Com `token_delivery="cookie"` os endpoints `/login`, `/refresh` e `/logout`
gravam e leem o par de tokens em cookies. O cookie de refresh é escopado no
caminho base da auth, então ele chega em `/refresh` e `/logout` mas **não**
viaja em toda chamada de API.

!!! warning "O prefixo vai no `make_auth_router`, não no `include_router`"
    O router de auth já nasce com `prefix="/auth"`, e é desse valor que ele
    tira o `Path` do cookie de refresh. Passar `prefix="/api/auth"` ao
    `include_router` **soma** os dois: as rotas viram `/api/auth/auth/login`
    e o cookie sai com `Path=/auth`, que o navegador nunca envia para
    `/api/auth/auth/refresh`. Com `prefix="/api/auth"` no
    `make_auth_router`, as rotas ficam em `/api/auth/login` e o cookie em
    `Path=/api/auth`.

### Cliente

```typescript
// web/src/lib/auth.ts
import { createTempestAuth } from "tempest-react-sdk";

export const auth = createTempestAuth({
    baseURL: import.meta.env.VITE_API_URL ?? window.location.origin,
    loginPath: "/api/auth/login",
    refreshPath: "/api/auth/refresh",
    mePath: "/api/auth/me",
    withCredentials: true,
});
```

`createTempestAuth` **constrói o próprio cliente** — não recebe um pronto. Ele
devolve `{ useAuthStore, api, login, logout, refresh }`, e é esse `auth.api`
que você deve usar nas chamadas autenticadas: ele já vem com bearer + o ciclo
`401 → refresh → retry` deduplicado entre chamadas concorrentes.

`withCredentials: true` é o que permite o refresh por cookie `HttpOnly`; sem
ele o navegador não envia o cookie e o `refresh` falha.

E protegendo rotas — o `AuthGuard` é agnóstico de router e recebe o estado
explicitamente:

```tsx
// web/src/App.tsx
import { AuthGuard } from "tempest-react-sdk";
import { Navigate } from "react-router";

import { auth } from "./lib/auth";

export function App() {
    const isAuthenticated = auth.useAuthStore((state) => state.isAuthenticated);
    return (
        <AuthGuard
            isAuthenticated={isAuthenticated}
            fallback={<Navigate to="/login" />}
        >
            <Dashboard />
        </AuthGuard>
    );
}
```

!!! tip "O envelope de erro é o mesmo contrato dos dois lados"
    O `TempestApiError` do `tempest-react-sdk` desserializa exatamente o
    envelope `{detail, code, details}` que o
    [`register_exception_handlers`](openapi-errors.md) emite. Faça branch no
    `code`, nunca no `detail` — que muda com o locale negociado:

    ```typescript
    import { isApiError } from "tempest-react-sdk";

    try {
        await api.post("/jobs/x/candidates", body);
    } catch (error) {
        if (isApiError(error) && error.code === "CANDIDATE_ALREADY_EXISTS") {
            showAlreadyApplied();
        }
    }
    ```

    Rode [`tempest openapi-client`](openapi-client.md) contra a **sua própria**
    spec e o front ganha os schemas e os `code` tipados de graça.

## 4. Scaffold e container

### Estrutura

```text
meu-servico/
├── main.py
├── pyproject.toml
├── src/                    # o backend (ver Arquitetura)
│   └── api/app.py          # inclui make_spa_router("web/dist") por último
└── web/                    # a SPA, do create-tempest-app
    ├── package.json
    ├── vite.config.ts
    └── src/
```

### Dockerfile multi-stage

O gerador já cuida disso. Com um `web/package.json` presente, o
`tempest generate --dockerfile` detecta a SPA e emite o stage Node:

```bash
tempest generate --dockerfile --force
```

```text
Regenerated Dockerfile
Regenerated .dockerignore
  SPA stage: builds web/ and copies web/dist into the image.
```

A detecção é por `package.json` — em `web/`, `frontend/`, `client/` ou `ui/`.
Um diretório vazio **não** conta, senão o build da imagem morreria dentro do
`npm ci`. Para outro layout, `--spa-dir apps-web`; para forçar imagem
backend-only num projeto que tem frontend, `--no-spa`.

O `.dockerignore` gerado ganha `web/node_modules/` e `web/dist/` junto — o
`dist/` é ignorado de propósito, porque é o stage Node que o produz. Copiar um
`dist/` local faria a imagem carregar o build da sua máquina em vez do
reproduzível.

O arquivo gerado, para um projeto `meu_servico` com a SPA em `web/`, é este
(colado da saída do gerador):

```dockerfile
# Dockerfile — generated by `tempest new` for meu_servico.
#
# Multi-stage build using uv (https://docs.astral.sh/uv/):
#   * builder stage installs dependencies into /app/.venv
#   * final stage copies only the venv + source, runs as a non-root user
#
# Build:  docker build -t meu_servico .
#
# A single-page app was detected in web/, so this build is
# fullstack: a Node stage compiles it and only the emitted dist/ is
# copied into the final image — node_modules and the Node toolchain
# never reach the runtime. Serve it from FastAPI with
# `make_spa_router("web/dist")`, included after every API router.
#
# Run:    docker run --rm -p 8000:8000 --env-file .env meu_servico
#
# The image binds SERVER_HOST=0.0.0.0 (see the final stage) so the app is
# reachable from outside the container even without a .env file. The infra
# in docker-compose.yaml (Postgres, etc.) is separate — point DATABASE_URL
# at it via --env-file or `environment:` when you wire this image in.

# ---- spa --------------------------------------------------------------------
FROM node:22-alpine AS spa

WORKDIR /spa

# Install with the lockfile first so this layer caches on dependency
# changes only. `npm ci` needs a lockfile; the glob keeps the COPY
# working before one is committed, and the fallback covers that case.
COPY web/package.json web/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY web/ ./
RUN npm run build

# ---- builder ----------------------------------------------------------------
FROM python:3.13-slim AS builder

# uv ships as a static binary; pin the tag in production for reproducibility.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy bytecode-compiled packages instead of symlinking — the venv must be
# self-contained when copied across stages.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (without the project) so this layer is cached
# and only re-runs when pyproject.toml / uv.lock change. uv.lock is optional
# in a fresh scaffold — the glob keeps the COPY from failing when it is absent.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

# Now copy the source and install the project itself.
COPY . .
RUN uv sync --no-dev

# ---- final ------------------------------------------------------------------
FROM python:3.13-slim

# Run as an unprivileged user. /app is owned by it so the app can write
# logs/ and the default SQLite app.db without root.
RUN useradd --create-home --uid 1000 app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

# WORKDIR created /app as root before the COPY, and `COPY --chown` only sets
# ownership on the copied *contents* — not on the pre-existing /app directory
# node itself. So the app user cannot create new entries (the runtime logs/
# dir, the default SQLite app.db, etc.) inside /app and the app crashes at
# startup with `PermissionError: [Errno 13] ... 'logs'`. Chown the directory
# and pre-create logs/ so the SDK's file logging (LOG_DIR=logs) works.
RUN mkdir -p /app/logs && chown -R app:app /app

# The compiled SPA, from the Node stage. Nothing else crosses over, so
# the runtime image carries no Node runtime and no node_modules.
COPY --from=spa --chown=app:app /spa/dist /app/web/dist

# Put the venv on PATH so `python` resolves to the project interpreter.
# SERVER_HOST=0.0.0.0 binds all interfaces inside the container so the app
# is reachable from the host (overridable via .env / `environment:`).
ENV PATH="/app/.venv/bin:$PATH" \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000

USER app
EXPOSE 8000

CMD ["python", "main.py"]
```

O build da SPA acontece no stage `spa` e só o `dist/` viaja para a imagem
final, então nem `node_modules` nem o toolchain do Node entram no runtime. O
processo roda como `app`, sem root, e o `logs/` já existe antes do primeiro
log.

!!! warning "`SERVER_HOST` no container: o `--env-file` vence o `ENV`"
    A imagem já fixa `SERVER_HOST=0.0.0.0` no stage final, então ela é
    alcançável de fora sem configuração. O risco está no `.env`: o
    `.env.example` do scaffold traz `SERVER_HOST=127.0.0.1`, e uma variável
    de `--env-file` (ou de `environment:` no compose) **sobrescreve** o
    `ENV` da imagem. Um `.env` copiado do exemplo e passado com
    `docker run --env-file .env` volta o bind para `127.0.0.1`, e o
    container só aceita conexão de dentro dele. Tire o `SERVER_HOST` do
    `.env` que vai para o container, ou ponha `SERVER_HOST=0.0.0.0` nele.

### `.dockerignore`

O gerador acrescenta ao `.dockerignore` padrão:

```text
web/node_modules/
web/dist/
```

O `dist/` é ignorado de propósito: o stage `spa` o gera. Copiar um `dist/`
local para dentro do build faria a imagem carregar o build da sua máquina em
vez do build reproduzível.

## Recapitulando

1. **Dev**: `createViteConfig({ proxy: { "/api": "http://127.0.0.1:8000" } })`.
   Sem CORS, com hot reload.
2. **Prod**: `app.include_router(make_spa_router("web/dist"))` **por último**.
   Uma origem, um container.
3. **`baseURL` absoluta** (a origem atual) no `createApiClient` faz o mesmo
   código servir os dois modos — e lembre que um path com `/` inicial ignora o
   path da base.
4. **Cache invertido de propósito**: documento `no-store`, assets `immutable`.
5. **Auth por cookie** com `token_delivery="cookie"` + `createTempestAuth` /
   `AuthGuard`.
6. **Faça branch no `code`** do envelope de erro, e gere o cliente tipado com
   `tempest openapi-client`.

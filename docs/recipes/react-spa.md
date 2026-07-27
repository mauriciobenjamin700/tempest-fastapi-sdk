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
from tempest_fastapi_sdk import make_auth_router

from src.api.dependencies.resources import db

app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        token_delivery="cookie",
    ),
    prefix="/api/auth",
    tags=["auth"],
)
```

Com `token_delivery="cookie"` os endpoints `/login`, `/refresh` e `/logout`
gravam e leem o par de tokens em cookies. O cookie de refresh é escopado no
caminho base da auth, então ele chega em `/refresh` e `/logout` mas **não**
viaja em toda chamada de API.

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

O arquivo gerado tem esta forma:

O build da SPA acontece num stage Node e só o `dist/` viaja para a imagem
final, então nem `node_modules` nem o toolchain do Node entram no runtime:

```dockerfile
# --- stage 1: compila a SPA -------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- stage 2: runtime Python ------------------------------------------
FROM python:3.13-slim
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/
COPY main.py ./
COPY --from=web /web/dist ./web/dist

EXPOSE 8000
CMD ["uv", "run", "python", "main.py"]
```

!!! warning "`SERVER_HOST` no container"
    O default do scaffold é `127.0.0.1`, que num container só aceita conexões
    de dentro dele. Sirva com `SERVER_HOST=0.0.0.0` no ambiente do container —
    é seguro aqui porque a exposição é controlada pelo mapeamento de portas.

### `.dockerignore`

```text
web/node_modules
web/dist
```

O `dist/` é ignorado de propósito: o stage Node o gera. Copiar um `dist/` local
para dentro do build faria a imagem carregar o build da sua máquina em vez do
build reproduzível.

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

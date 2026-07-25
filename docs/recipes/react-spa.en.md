# A React SPA inside FastAPI

This recipe builds a fullstack stack with **a single deployment**: a React SPA
served by the FastAPI process itself, with [`tempest-react-sdk`](https://github.com/mauriciobenjamin700/tempest-react-sdk)
on the client talking to this SDK on the server. 🚀

Three modes, in the order you will use them:

1. **Development** — `vite dev` on 5173 proxying `/api` to FastAPI on 8000.
   Hot reload intact.
2. **Same-origin production** — `make_spa_router` serves Vite's `dist/` from
   FastAPI. No CORS, no `SameSite=None` cookie, one container.
3. **Scaffold** — `create-tempest-app` creates `web/` inside the project, and a
   multi-stage `Dockerfile` compiles the SPA and copies the build.

!!! info "Why same-origin is the recommended default"
    Serving the SPA and the API from one origin removes CORS, preflight,
    `SameSite=None; Secure` on the refresh cookie and the CSRF configuration
    that comes with it — all at once. The browser never makes a cross-origin
    request, so half the auth configuration surface simply does not exist.

## 1. Development — Vite with a proxy

### The React side

```bash
npx create-tempest-app web
cd web && npm install
```

The template already ships a `vite.config.ts` pointing at the SDK helper.
Uncomment the proxy:

```typescript
// web/vite.config.ts
import { createViteConfig } from "tempest-react-sdk/vite";

export default createViteConfig({
    proxy: { "/api": "http://127.0.0.1:8000" },
});
```

`createViteConfig` already wires `@vitejs/plugin-react`, the `@` → `src` alias
and dev-server defaults (port 5173, host `127.0.0.1`). A string in `proxy` is
expanded to `{ target, changeOrigin: true }`; pass an object to control
everything.

### The FastAPI side

Nothing to do beyond running on 8000 — the scaffold default:

```bash
python main.py     # uvicorn on 127.0.0.1:8000
```

!!! tip "With a proxy you need no CORS in dev"
    The browser only ever talks to 5173; Vite talks to 8000 server-side. There
    is no cross-origin request, so `CORSSettings` can stay off even in
    development. If you would rather call the API directly (no proxy), then you
    do need CORS:

    ```python
    from tempest_fastapi_sdk import apply_cors

    apply_cors(app, origins=["http://127.0.0.1:5173"], allow_credentials=True)
    ```

### The HTTP client

```typescript
// web/src/lib/api.ts
import { createApiClient } from "tempest-react-sdk";

export const api = createApiClient({
    baseURL: import.meta.env.VITE_API_URL ?? window.location.origin,
    withCredentials: true,
});
```

Using the **current origin** as `baseURL` is what makes one piece of code serve
both modes with no branching: in dev the origin is Vite's (5173) and the proxy
forwards `/api` to FastAPI; in production the SPA and the API share an origin
and the path resolves directly. `VITE_API_URL` stays as the escape hatch for
separate origins.

!!! danger "Two URL details that break quietly"
    **`baseURL` must be absolute.** The client builds the URL with
    `new URL(path, baseURL)`, and a relative base is not a valid URL:

    ```typescript
    createApiClient({ baseURL: "/api" });   // ❌ TypeError: Invalid URL
    ```

    **A `path` starting with `/` discards the base's path.** That is `URL`
    semantics, not the SDK's:

    ```typescript
    const api = createApiClient({ baseURL: "https://x.dev/api" });
    await api.get("/users");   // → https://x.dev/users        (lost the /api)
    await api.get("users");    // → https://x.dev/api/users     ✅
    ```

    Pick one and stay consistent: origin in the base + prefix in the path
    (`baseURL: origin` + `get("/api/users")`), or prefix in the base +
    relative path (`baseURL: origin + "/api"` + `get("users")`). Mixing the two
    is what produces a 404 that looks like a backend routing bug.

## 2. Production — FastAPI serves the build

```bash
cd web && npm run build     # produces web/dist/
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

!!! danger "`make_spa_router` goes last — always"
    It registers a catch-all `GET /{path:path}`. FastAPI matches routes in
    registration order, so **anything included after it becomes
    unreachable**. Include the whole API first.

### What the router solves

Mounting `StaticFiles` alone does not serve a SPA. A client-side router owns
paths like `/users/42`, which exist in the browser and **not** on disk — so a
bare static mount answers 404 on every deep link and every page refresh. The
missing piece is the **SPA fallback**.

Beyond that, three details that are easy to get wrong:

| Detail | Behavior |
| --- | --- |
| `index.html` cache | `no-store, must-revalidate` |
| Hashed asset cache | `public, max-age=31536000, immutable` |
| API paths | Excluded from the fallback — JSON 404, not HTML |
| Methods | Only `GET`/`HEAD` fall back |
| Path traversal | `..` (in any encoding) cannot escape `dist/` |

!!! warning "The classic inverted-cache bug"
    `index.html` is the **only** file whose name does not change between
    deploys. Cache it and the browser keeps loading the old bundle after a
    deploy — the new, freshly hashed assets are never even requested. That is
    why the document is `no-store` and the assets are `immutable`: exactly the
    opposite of what intuition suggests.

!!! check "Why an API 404 must not become HTML"
    If `/api/typo` returned `index.html` with a **200**, the client would get
    HTML where it expects JSON and report a parse error. Whoever debugs it
    looks at the client, not at the route that does not exist. So the prefixes
    in `DEFAULT_EXCLUDED_PREFIXES` (`/api`, `/docs`, `/openapi.json`,
    `/health`, `/metrics`, `/logs`, `/admin`, `/auth`, `/redoc`) never fall
    back.

    Using a different prefix? Pass your own:

    ```python
    app.include_router(
        make_spa_router("web/dist", excluded_prefixes=("/api", "/graphql", "/rpc"))
    )
    ```

### Fail early, not in staging

```pycon
>>> make_spa_router("web/dist")
FileNotFoundError: SPA build directory not found: /app/web/dist. Run the
frontend build (e.g. `npm run build`) before starting the app, or point
`dist_dir` at the right path.
```

Raising at wiring time is deliberate. The alternative is a service that
**boots fine** and answers 404 on every page — something usually discovered in
staging, or worse.

## 3. Auth shared across both SDKs

Same-origin makes the cookie flow the simplest path: the refresh token lives in
an `HttpOnly` cookie JavaScript cannot reach.

### Server

```python
# src/api/app.py
from tempest_fastapi_sdk import make_auth_router

app.include_router(
    make_auth_router(auth_service, session_factory, token_delivery="cookie"),
    prefix="/api/auth",
    tags=["auth"],
)
```

With `token_delivery="cookie"` the `/login`, `/refresh` and `/logout` endpoints
write and read the token pair as cookies. The refresh cookie is scoped to the
auth base path, so it reaches `/refresh` and `/logout` but does **not** ride
along on every API call.

### Client

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

`createTempestAuth` **builds its own client** — it does not take a ready-made
one. It returns `{ useAuthStore, api, login, logout, refresh }`, and that
`auth.api` is the one to use for authenticated calls: it already carries bearer
auth plus the `401 → refresh → retry` cycle, deduplicated across concurrent
callers.

`withCredentials: true` is what allows the `HttpOnly` cookie refresh; without
it the browser does not send the cookie and `refresh` fails.

And guarding routes — `AuthGuard` is router-agnostic and takes the state
explicitly:

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

!!! tip "The error envelope is the same contract on both sides"
    `tempest-react-sdk`'s `TempestApiError` deserializes exactly the
    `{detail, code, details}` envelope
    [`register_exception_handlers`](openapi-errors.md) emits. Branch on `code`,
    never on `detail` — which changes with the negotiated locale:

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

    Run [`tempest openapi-client`](openapi-client.md) against **your own** spec
    and the frontend gets the schemas and typed `code` values for free.

## 4. Scaffold and container

### Layout

```text
my-service/
├── main.py
├── pyproject.toml
├── src/                    # the backend (see Architecture)
│   └── api/app.py          # includes make_spa_router("web/dist") last
└── web/                    # the SPA, from create-tempest-app
    ├── package.json
    ├── vite.config.ts
    └── src/
```

### Multi-stage Dockerfile

The generator handles this. With a `web/package.json` present,
`tempest generate --dockerfile` detects the SPA and emits the Node stage:

```bash
tempest generate --dockerfile --force
```

```text
Regenerated Dockerfile
Regenerated .dockerignore
  SPA stage: builds web/ and copies web/dist into the image.
```

Detection is by `package.json` — under `web/`, `frontend/`, `client/` or
`ui/`. An empty directory does **not** count, otherwise the image build would
die inside `npm ci`. For another layout use `--spa-dir apps-web`; to force a
backend-only image in a project that has a frontend, `--no-spa`.

The generated `.dockerignore` gains `web/node_modules/` and `web/dist/` too —
`dist/` is ignored on purpose because the Node stage produces it. Copying a
local `dist/` in would ship your machine's build instead of the reproducible
one.

The generated file looks like this:

The SPA build happens in a Node stage and only `dist/` travels to the final
image, so neither `node_modules` nor the Node toolchain enters the runtime:

```dockerfile
# --- stage 1: build the SPA -------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- stage 2: Python runtime ------------------------------------------
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

!!! warning "`SERVER_HOST` in a container"
    The scaffold default is `127.0.0.1`, which inside a container only accepts
    connections from within it. Serve with `SERVER_HOST=0.0.0.0` in the
    container environment — safe here because exposure is controlled by the
    port mapping.

### `.dockerignore`

```text
web/node_modules
web/dist
```

`dist/` is ignored on purpose: the Node stage produces it. Copying a local
`dist/` into the build would ship your machine's build instead of the
reproducible one.

## Recap

1. **Dev**: `createViteConfig({ proxy: { "/api": "http://127.0.0.1:8000" } })`.
   No CORS, hot reload intact.
2. **Prod**: `app.include_router(make_spa_router("web/dist"))` **last**. One
   origin, one container.
3. **An absolute `baseURL`** (the current origin) in `createApiClient` makes one
   piece of code serve both modes — and remember a leading-`/` path discards the
   base's path.
4. **Deliberately inverted caching**: document `no-store`, assets `immutable`.
5. **Cookie auth** with `token_delivery="cookie"` + `createTempestAuth` /
   `AuthGuard`.
6. **Branch on the envelope's `code`**, and generate the typed client with
   `tempest openapi-client`.

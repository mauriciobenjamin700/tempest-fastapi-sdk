# HTTP layer

Middlewares, dependencies, routers and middleware composition for the API surface.

Here you assemble a service's entire HTTP surface from the primitives `tempest_fastapi_sdk` ships — without hand-writing middleware, exception handlers or bootstrap glue. Each subpage is independent: open only the one you need right now. This recipe covers ~12 primitives:

- **`create_app()` + `register_exception_handlers`** — the canonical bootstrap and the standardized error envelope (with optional i18n via `MessageCatalog`).
- **`RequestIDMiddleware`** — `X-Request-ID` correlation on every log line.
- **`apply_cors`** — CORS from `CORSSettings`.
- **`make_health_router` / `make_token_dependency`** — liveness/readiness + the `X-Token` shared-secret guard.
- **JWT / bearer / role / permission dependencies** — gate routes by token and by role.
- **`RateLimitMiddleware`** — sliding window, key by IP/user/tenant, memory or Redis store.
- **`make_rate_limit_dependency`** — the same limit on one route, with a key that can read the body (`key_by_body_field`).
- **`BodySizeLimitMiddleware`** — hard cap on the request body, answering 413 before any parsing.
- **`WebhookSignatureVerifier` / `RSAWebhookSignatureVerifier`** — validate signed webhooks (HMAC or RSA).
- **`build_pagination_link_header`** — GitHub-style RFC 8288 `Link` header.
- **`make_tool_spec_router`** — machine-readable manifest at the root prefix.
- **`run_server`** — programmatic uvicorn entry point.
- **`BaseAppSettings` + `*Settings` mixins** — composable env-var configuration.

!!! tip "The last two subpages are full flows"
    Authentication, uploads and transactional email appear there in condensed form; each has a dedicated, deeper recipe — see the [Recap](#recap-next-steps) at the end of the page.

## Where each task lives

Use this table to open only the subpage for the task at hand; they are listed in reading order.

| Subpage | What it solves |
| --- | --- |
| [App bootstrap and the error envelope](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/http/bootstrap.md) | Use to create the app with `create_app`, get liveness/readiness, put every 4xx and the 422 in the error envelope, notify failures and localize messages. |
| [JWT bearer, current user and role dependencies](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/http/jwt.md) | Use to guard routes by token, role and permission, with the `JWTUtils` built in `create_app`. |
| [Rate limiting (middleware and per route)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/http/rate-limit.md) | Use to limit requests by IP, user, tenant or plan, with state in Redis, token bucket, `RateLimit-*` headers and a limit on a single route. |
| [Body size limit and HTTP cache (ETag / 304)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/http/body-cache.md) | Use to refuse a large body with 413 before parsing and answer 304 with ETag without mixing authenticated requests. |
| [Webhooks: verify and send](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/http/webhooks.md) | Use to validate incoming signed webhooks (HMAC or RSA) and deliver outgoing webhooks with `WebhookSender`. |
| [Pagination links, tool-spec, server and settings](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/http/extras.md) | Use to emit the pagination `Link` header, expose `/tool-spec`, start uvicorn with `run_server` and compose settings from mixins. |
| [End-to-end authentication](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/http/auth.md) | Use to see signup, login and a protected route with `PasswordUtils` and `JWTUtils` in one example. |
| [File upload and transactional email](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/http/upload-email.md) | Use for an avatar endpoint with validation and cleanup and a password-reset flow with `EmailUtils`. |

## Recap / next steps

You now know the whole HTTP surface: app bootstrap, exception handlers with i18n, auth dependencies, rate limiting, webhook verification, pagination headers, tool-spec, the server entry point and settings composition. The last two subpages (authentication; uploads and email) are a summary — each has a dedicated recipe that goes deeper:

- [Authentication flow »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/auth-flow.md) — the full bundled flow (`UserAuthService` + `make_auth_router`): signup, activation, login, password reset, refresh tokens and `current_user`.
- [File uploads »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/uploads.md) — pluggable storage backends (`LocalUploadStorage` / `MinIOUploadStorage`), presigned URLs and content validation.
- [Transactional email »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/email.md) — `EmailUtils` with Jinja2 templates, the bundled defaults (`activation.html`, `password_reset.html`) and how to shadow them.
- [Private downloads »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/downloads.md) — serving files behind auth with `DownloadUtils` (`file_response` / `stream`), without leaking public links.

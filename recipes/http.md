# Camada HTTP

Middlewares, dependências, routers e composição de middleware para a superfície da API.

Você vai montar aqui a superfície HTTP inteira de um serviço a partir dos primitivos que o `tempest_fastapi_sdk` entrega — sem escrever middleware, exception handler ou glue de bootstrap na mão. Cada seção é independente: pegue só a que você precisa agora. Esta receita cobre ~12 primitivos:

- **`create_app()` + `register_exception_handlers`** — bootstrap canônico e o envelope de erro padronizado (com i18n opcional via `MessageCatalog`).
- **`RequestIDMiddleware`** — correlação `X-Request-ID` em cada linha de log.
- **`apply_cors`** — CORS a partir de `CORSSettings`.
- **`make_health_router` / `make_token_dependency`** — liveness/readiness + o guarda de segredo compartilhado `X-Token`.
- **Dependências JWT / bearer / role / permission** — controle de rota por token e por papel.
- **`RateLimitMiddleware`** — janela deslizante, chave por IP/usuário/tenant, store em memória ou Redis.
- **`make_rate_limit_dependency`** — o mesmo limite em uma rota só, com chave que pode ler o corpo (`key_by_body_field`).
- **`BodySizeLimitMiddleware`** — teto de bytes no corpo do request, com 413 antes de qualquer parse.
- **`WebhookSignatureVerifier` / `RSAWebhookSignatureVerifier`** — validação de webhooks assinados (HMAC ou RSA).
- **`build_pagination_link_header`** — header `Link` RFC 8288 no estilo GitHub.
- **`make_tool_spec_router`** — manifesto legível por máquina no prefixo raiz.
- **`run_server`** — ponto de entrada programático do uvicorn.
- **`BaseAppSettings` + mixins `*Settings`** — configuração componível por env var.

!!! tip "As três últimas seções são flows completos"
    Autenticação, upload e e-mail transacional aparecem aqui em forma resumida; cada uma tem uma receita dedicada e mais profunda — veja o [Recap](#recap-proximos-passos) no fim da página.

## Onde está cada tarefa

Use esta tabela para abrir só a subpágina da tarefa que você tem; elas estão na ordem de leitura.

| Subpágina | O que resolve |
| --- | --- |
| [Bootstrap da aplicação e envelope de erro](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http/bootstrap.md) | Use para criar a app com `create_app`, ter liveness/readiness, pôr todo 4xx e o 422 no envelope de erro, notificar falhas e localizar mensagens. |
| [Dependências JWT bearer, usuário atual e role](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http/jwt.md) | Use para proteger rotas por token, papel e permissão, com o `JWTUtils` criado no `create_app`. |
| [Rate limit (middleware e por rota)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http/rate-limit.md) | Use para limitar requisições por IP, usuário, tenant ou plano, com estado no Redis, token bucket, headers `RateLimit-*` e limite numa rota só. |
| [Limite de body e cache HTTP (ETag / 304)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http/body-cache.md) | Use para recusar corpo grande com 413 antes do parse e responder 304 com ETag sem misturar requisições autenticadas. |
| [Webhooks: verificar e enviar](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http/webhooks.md) | Use para validar webhooks assinados (HMAC ou RSA) que chegam e entregar webhooks de saída com `WebhookSender`. |
| [Link de paginação, tool-spec, servidor e settings](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http/extras.md) | Use para emitir o header `Link` de paginação, expor `/tool-spec`, subir o uvicorn com `run_server` e compor settings com mixins. |
| [Autenticação de ponta a ponta](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http/auth.md) | Use para ver signup, login e rota protegida com `PasswordUtils` e `JWTUtils` num exemplo só. |
| [Upload de arquivos e e-mail transacional](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http/upload-email.md) | Use para um endpoint de avatar com validação e limpeza e um fluxo de reset de senha com `EmailUtils`. |

## Recap / próximos passos

Você agora conhece a superfície HTTP inteira: bootstrap do app, exception handlers com i18n, dependências de auth, rate limit, verificação de webhook, headers de paginação, tool-spec, o ponto de entrada do servidor e a composição de settings. As três últimas seções (autenticação, upload, e-mail) foram um resumo — cada uma tem uma receita dedicada que vai mais fundo:

- [Fluxo de autenticação »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow.md) — o flow bundled completo (`UserAuthService` + `make_auth_router`): signup, ativação, login, reset de senha, refresh tokens e `current_user`.
- [Upload de arquivos »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/uploads.md) — backends de storage plugáveis (`LocalUploadStorage` / `MinIOUploadStorage`), URLs pré-assinadas e validação de conteúdo.
- [E-mail transacional »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/email.md) — `EmailUtils` com templates Jinja2, os defaults embutidos (`activation.html`, `password_reset.html`) e como sobrescrevê-los.
- [Downloads privados »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/downloads.md) — servir arquivos atrás de auth com `DownloadUtils` (`file_response` / `stream`), sem vazar links públicos.

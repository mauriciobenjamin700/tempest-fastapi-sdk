# Receitas

Passo a passo curtos no estilo "quero conectar X". Cada página começa com **qual problema resolve**, **quando recorrer a ela** e um exemplo de código completo que você pode copiar literalmente.

!!! tip "Quando ler o quê"
    - Só precisa consultar uma assinatura? Pule para a **[Referência »](../reference.md)**.
    - Construindo um serviço novo do zero? Siga primeiro o **[Tutorial »](../tutorial.md)** linear.
    - Conectando uma peça específica do SDK? Você está no lugar certo — escolha a receita abaixo.

| Tema | Cobre |
| --- | --- |
| **[Banco de dados »](database.md)** | `BaseRepository`, `BaseModel`, `AuditMixin` / `SoftDeleteMixin`, paginação por cursor, helper de Alembic |
| **[Camada HTTP »](http.md)** | `apply_cors`, `RequestIDMiddleware`, `RateLimitMiddleware`, `make_health_router`, dependências de JWT / role / permissão, verificador de assinatura de webhook, headers Link de paginação, router de tool-spec |
| **[HTTP client (saída) »](http-client.md)** | `HTTPClient` — httpx tipado com retry/backoff, circuit-breaker, X-Request-ID; `RetryPolicy`, `CircuitOpenError` |
| **[Cache »](cache.md)** | `AsyncRedisManager`, decorator `@cached` |
| **[Tempo real »](realtime.md)** | Server-Sent Events (`EventStream`, `sse_response`) |
| **[Fila e Tarefas »](queue-tasks.md)** | FastStream (`AsyncBrokerManager`), TaskIQ (`AsyncTaskBrokerManager`), `AsyncTaskScheduler`, outbox transacional |
| **[Email transacional »](email.md)** | `EmailUtils` — SMTP, corpo texto/HTML, anexos, templates Jinja2 |
| **[Web Push »](webpush.md)** | `WebPushDispatcher`, schemas VAPID, broadcast com poda |
| **[Logging »](logging.md)** | `LogUtils`, logging JSON estruturado, propagação de request-ID |
| **[Métricas »](metrics.md)** | `MetricsUtils` — snapshots de CPU / RAM / disco / GPU |
| **[Painel admin »](admin.md)** | `AdminSite`, `AdminModel`, `make_admin_router`, `BaseUserModel` |
| **[Downloads »](downloads.md)** | `DownloadUtils` — `file_response`, `stream`, `build_content_disposition`, anti path-traversal |
| **[Utilitários »](utilities.md)** | `utcnow`/`to_utc`, `modify_dict`, `get_client_ip`, tokens opacos (`generate_opaque_token`) |
| **[Testes »](testing.md)** | `test_session`, `test_database`, SQLite em memória, fixtures pytest |
| **[CLI »](cli.md)** | `tempest new` / `lint` / `fix` / `format` / `type` / `test` / `check` |
| **[Segurança »](security.md)** | `AttemptThrottle`, helpers de token opaco, `HardenedStaticFiles`, headers de segurança |
| **[Helpers brasileiros »](br-helpers.md)** | validação + normalização de CPF / CNPJ / CEP / telefone |

## Anatomia de uma receita

Toda receita segue o mesmo formato de quatro seções para você bater o olho:

1. **O que resolve** — um parágrafo em linguagem simples.
2. **Quando usar** — lista de situações + quando *não* usar.
3. **O código** — completo, executável, com anotações `# 1. setup` / `# 2. wire` / `# 3. test`.
4. **Pegadinhas** — ressalvas de produção, defaults de segurança, notas de escala.

Se você encontrar uma receita que não segue esse formato, [abra uma issue](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new) — tratamos regressões de doc como regressões de código.

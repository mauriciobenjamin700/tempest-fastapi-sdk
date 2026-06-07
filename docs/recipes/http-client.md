# HTTP client (saída)

`HTTPClient` é um wrapper tipado sobre o `httpx.AsyncClient` para **chamar
serviços externos** com retry + backoff exponencial, circuit-breaker,
timeouts padrão e propagação do `X-Request-ID`. É a contraparte de saída do
[middleware HTTP](http.md) (que cuida do tráfego de entrada). Requer o extra
`[http]` (`httpx`).

## Uso básico

O client é seguro pra compartilhar entre requests no mesmo event loop — ele
reusa o connection pool interno. Use como async context manager (ou guarde
um singleton em [`resources.py`](../architecture.md) e feche no lifespan).

```python
from typing import Any

from tempest_fastapi_sdk import HTTPClient

client = HTTPClient(base_url="https://api.example.com", timeout=10.0)


async def fetch_user(user_id: str) -> dict[str, Any]:
    """GET /users/{id} no serviço externo."""
    async with client:
        response = await client.get(f"/users/{user_id}")
        response.raise_for_status()
        return response.json()
```

Métodos: `get` / `post` / `put` / `patch` / `delete` (e `request` genérico),
todos repassando kwargs pro httpx (`json=`, `params=`, `headers=`, ...) e
devolvendo um `httpx.Response`.

## Retry + backoff + circuit-breaker

Passe um `RetryPolicy` e ajuste os limites do breaker na construção:

```python
from tempest_fastapi_sdk import CircuitOpenError, HTTPClient, RetryPolicy

client = HTTPClient(
    base_url="https://api.example.com",
    timeout=5.0,
    retry_policy=RetryPolicy(
        max_attempts=3,                # 1 tentativa + 2 retries
        backoff_initial_seconds=0.5,   # 0.5s, 1s, 2s... (exponencial)
        backoff_max_seconds=8.0,       # teto por espera
    ),
    failure_threshold=5,               # abre o circuito após 5 falhas seguidas
    recovery_seconds=30.0,             # meio-aberto após 30s
    default_headers={"X-Api-Key": "..."},
    propagate_request_id=True,         # encaminha o X-Request-ID do request atual
)


async def call() -> None:
    try:
        async with client:
            await client.post("/charge", json={"amount": 100})
    except CircuitOpenError:
        # O circuito está aberto — não martele o upstream caído.
        ...
```

- **Retry**: refeito em erros transitórios (timeouts, 5xx, falhas de conexão)
  até `max_attempts`, com backoff exponencial limitado por `backoff_max_seconds`.
- **Circuit-breaker**: após `failure_threshold` falhas consecutivas o circuito
  **abre** e as chamadas levantam `CircuitOpenError` imediatamente (sem tocar
  a rede) até passar `recovery_seconds`, quando entra em meio-aberto pra testar.
- **Request-ID**: com `propagate_request_id=True`, o `X-Request-ID` do request
  em curso (via `RequestIDMiddleware`) é repassado ao upstream, costurando os
  logs ponta-a-ponta.

!!! tip "Guarde como singleton em resources.py"
    Crie o `HTTPClient` uma vez (em `src/api/dependencies/resources.py`),
    exponha um `get_http_client`, e feche no lifespan com `await client.aclose()`
    — assim o connection pool é reaproveitado entre requests.

## Recap

- `HTTPClient` = `httpx.AsyncClient` tipado + retry/backoff/circuit-breaker + X-Request-ID.
- Extra `[http]`. Métodos `get/post/put/patch/delete/request` → `httpx.Response`.
- `RetryPolicy(max_attempts, backoff_initial_seconds, backoff_max_seconds)` controla o retry.
- `failure_threshold` / `recovery_seconds` controlam o breaker; `CircuitOpenError` quando aberto.
- Compartilhe um singleton e feche com `aclose()` no shutdown.

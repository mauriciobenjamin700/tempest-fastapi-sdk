# Logging

Nesta receita você configura logs JSON estruturados com correlação por request, um arquivo por nível de severidade e um endpoint HTTP para lê-los. O objetivo é que cada linha de log seja parseável, rastreável até a requisição que a originou e inspecionável sem SSH no servidor.

`configure_logging` instala um handler JSON no logger raiz que emite registros JSON de uma linha carregando o request ID ativo. `LogUtils` é uma fachada fina que adiciona métodos por nível aceitando `**fields` estruturados.

```python
from tempest_fastapi_sdk import LogUtils, configure_logging
from tempest_fastapi_sdk.core import get_request_id

# Imperativo — chame uma vez durante o bootstrap.
configure_logging(level="INFO", json_output=True)

# Fachada — útil para singletons de serviço.
log = LogUtils("app.users", level="INFO")
log.info("user_created", user_id=str(user.id), email=user.email)
log.warning("login_throttled", ip="1.2.3.4", attempts=5)

try:
    risky()
except RuntimeError:
    log.exception("risky_failed", op="reconcile")  # appends traceback

# Exponha o ID de correlação fora da linha de log, se necessário.
request_id = get_request_id()
```

Saída JSON (uma linha — formatada aqui para legibilidade):

```json
{
  "timestamp": "2026-05-16T20:14:33.412Z",
  "level": "INFO",
  "logger": "app.users",
  "message": "user_created",
  "request_id": "d83e4b0c-7c2f-4bd6-aaa1-7d4f6cf5e5e9",
  "user_id": "9c1a5b2d-...",
  "email": "ana@example.com"
}
```

O middleware aceita um nome de header customizado (`RequestIDMiddleware(app, header_name="X-Correlation-ID")`); o mesmo header é ecoado de volta em toda resposta.


## Arquivos por nível + `500.log` isolado

**Por padrão, o SDK escreve simultaneamente no stdout E em `logs/`** (um arquivo JSON por nível). Cada arquivo recebe **apenas o seu próprio nível** (correspondência exata — um `ERROR` nunca cai no `warning.log`), então toda severidade vira um fluxo isolado e fácil de inspecionar com `grep`.

```python
from tempest_fastapi_sdk import configure_logging

# Defaults — stdout + logs/{debug,info,warning,error,critical,500}.log
configure_logging(level="INFO")

# Customizar diretório
configure_logging(level="INFO", log_dir="/var/log/myapp")

# Desligar arquivos (stdout puro — útil em serverless ou FS read-only)
configure_logging(level="INFO", file_output=False)

# Desligar stdout (sidecar coleta de disco)
configure_logging(level="INFO", stdout=False)
```

!!! warning "Não desligue os dois"
    `configure_logging(stdout=False, file_output=False)` lança
    `ValueError` — silenciar todos os handlers deixa a aplicação
    cega.

!!! check "Log em arquivo é best-effort — nunca derruba o boot"
    Se o `log_dir` não puder ser criado ou seus arquivos não puderem ser
    abertos (FS read-only, falta de permissão de escrita, container
    endurecido, serverless, CI), o SDK **pula** os handlers de arquivo,
    emite um aviso (no logger quando o stdout está ligado, senão direto no
    `stderr`) e segue rodando só com stdout — em vez de morrer no import com
    `PermissionError: [Errno 13] ... 'logs'`. Para abrir mão do log em
    arquivo de forma explícita, passe `file_output=False`.

O resultado em disco:

```text
logs/
├── debug.log      # só registros DEBUG
├── info.log       # só registros INFO
├── warning.log    # só registros WARNING
├── error.log      # só registros ERROR (um 500 também cai aqui)
├── critical.log   # só registros CRITICAL
└── 500.log        # só erros 500 não tratados (isolado)
```

!!! danger "Erros 500 são graves — por isso ganham arquivo próprio"
    O handler catch-all registrado por `register_exception_handlers`
    marca toda exceção não tratada com o `extra` `http_500=True`. O
    `configure_logging(log_dir=...)` roteia esses registros para um
    `500.log` dedicado, **além** do `error.log`. Assim a falha mais
    grave nunca fica soterrada no meio dos outros erros.

!!! tip "Sempre nos logs, nunca no body"
    O traceback vai para os arquivos/terminal via logging — **não** para
    o corpo da resposta. O body de um 500 é só o envelope genérico
    (`{"detail": "Internal server error", "code": "INTERNAL_SERVER_ERROR"}`).
    Veja [Camada HTTP](http.md) para os flags `log_traceback` /
    `include_traceback`.

!!! note "Arquivos são sempre JSON"
    Os handlers de arquivo usam o `JSONFormatter` independente de
    `json_output`, para que o endpoint `/logs` consiga parseá-los. O
    `json_output` controla apenas o formato do stdout.

No scaffold, o diretório vem de `LOG_DIR` (padrão `"logs"`; deixe vazio para desativar o log em arquivo). Adicione `logs/` ao `.gitignore`.


## Lendo logs por HTTP — `make_logs_router`

`make_logs_router` monta `GET /logs`, que lê os arquivos JSON em disco e devolve um `BasePaginationSchema[LogEntrySchema]` paginado (mais recentes primeiro).

```python
from tempest_fastapi_sdk import make_logs_router

app.include_router(
    make_logs_router(log_dir="logs", token_secret=settings.TOKEN_SECRET),
)
```

!!! warning "Proteja o endpoint em produção"
    O payload expõe tracebacks e metadados de request. O endpoint é
    protegido por um header de segredo compartilhado `X-Token` via
    `make_token_dependency`. Um `TOKEN_SECRET` vazio **desativa** a
    checagem (apenas dev) — nunca exponha `/logs` sem auth em produção.

Exemplos de consulta:

```bash
# Últimos 20 registros de todos os níveis
curl -H "X-Token: $TOKEN_SECRET" "http://localhost:8000/logs"

# Só os 500 isolados, página 1, 50 por página
curl -H "X-Token: $TOKEN_SECRET" "http://localhost:8000/logs?source=500&page_size=50"

# Erros mencionando "timeout" numa janela de tempo
curl -H "X-Token: $TOKEN_SECRET" \
  "http://localhost:8000/logs?source=error&q=timeout&start=2026-05-31T00:00:00Z"
```

Parâmetros de query:

| Parâmetro | Valores | Descrição |
| --- | --- | --- |
| `source` | `all` (padrão), `debug`, `info`, `warning`, `error`, `critical`, `500` | Qual arquivo ler. `all` mescla todos os níveis; `500` retorna só os 500 isolados. |
| `q` | texto | Substring (case-insensitive) na mensagem. |
| `start` / `end` | ISO-8601 | Limita os registros a uma janela de tempo. |
| `page` / `page_size` | inteiros | Paginação (1-indexada). |

!!! check "Recap"
    - `configure_logging(log_dir=...)` → stdout **+** um arquivo por nível.
    - Exatidão por nível: cada arquivo só recebe a sua severidade.
    - `500.log` isola erros 500 não tratados (marcador `http_500`).
    - `make_logs_router` serve esses arquivos paginados e autenticados.

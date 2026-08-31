# Logging

Nesta receita você configura logs JSON estruturados com correlação por request, um arquivo por nível de severidade e um endpoint HTTP para lê-los. O objetivo é que cada linha de log seja parseável, rastreável até a requisição que a originou e inspecionável sem SSH no servidor.

`configure_logging` instala um handler JSON no logger raiz que emite registros JSON de uma linha carregando o request ID ativo. `LogUtils` é uma fachada fina que adiciona métodos por nível aceitando `**fields` estruturados.

```python
from tempest_fastapi_sdk import LogUtils, configure_logging
from tempest_fastapi_sdk.core import get_request_id

from src.db.models import UserModel


def risky() -> None:
    """Blow up, so the log shows a real traceback."""
    raise RuntimeError("boom")


user = UserModel(name="Ana", email="ana@example.com")


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

!!! tip "Adotando num serviço que já loga em `%`-style"
    Os métodos por nível aceitam os posicionais do `logging`, então call site
    existente entra sem reescrita — e continua com interpolação **lazy** e com
    o template estável que a ferramenta de log agrega:

    ```python
    from tempest_fastapi_sdk import LogUtils

    log = LogUtils("app.email", level="INFO")

    log.info("Email enviado com sucesso para %s", "ana@example.com")
    log.error("Falha ao enviar para %s: %s", "bruno@x.com", "timeout")

    log.error("Falha ao enviar para %s: %s", "bruno@x.com", "timeout", op="send")
    ```

    A última linha mostra que os dois estilos convivem: os posicionais montam
    a mensagem, e o `**fields` continua virando chave de topo no JSON.

    `funcName`/`lineno` apontam para o **seu** call site, não para dentro da
    fachada — o default é `stacklevel=2`. Quem embrulha o `LogUtils` numa
    camada própria passa `stacklevel=3` (ou mais) para atravessar os frames
    extras.

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

# Teto de crescimento: cada arquivo rotaciona em ~10 MB, guardando 5 gerações
configure_logging(level="INFO", max_bytes=10_000_000, backup_count=5)

# Sem rotação — quando o logrotate do host (ou um sidecar) é o dono da retenção
configure_logging(level="INFO", max_bytes=0)
```

!!! danger "Os arquivos rotacionam por padrão — e é por isso"
    `FileHandler` puro cresce sem teto. Num serviço com uma linha de log por
    request, rodando em host de longa duração, o `info.log` é o que enche o
    disco — e disco cheio derruba o serviço **e** o que mais dividir a
    partição. Por isso o default é `RotatingFileHandler` com
    `max_bytes=10_000_000` e `backup_count=5`: ~60 MB por nível, teto duro.

    O outro lado deste par já tinha o teto: o `make_logs_router` limita a
    leitura a 20 mil registros por arquivo, adicionado depois que um serviço
    com diretório de log em gigabytes respondeu com worker morto. Isto é o lado
    de quem escreve.

    Os arquivos rotacionados (`info.log.1`, `info.log.2`, …) **não** são lidos
    pelo `/logs`: o endpoint lê os nomes exatos, então ele mostra a janela
    corrente. Retenção mais longa é trabalho de coletor.

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
from fastapi import FastAPI

from tempest_fastapi_sdk import make_logs_router

from src.core.settings import settings

app = FastAPI()


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
| `start` / `end` | ISO-8601 | Limita os registros a uma janela de tempo. Um valor sem offset (`2026-05-31T00:00:00`, ou só a data) é lido como UTC. |
| `page` / `page_size` | inteiros | Paginação (1-indexada). |

!!! info "A leitura é limitada por arquivo"
    Cada request lê os **20 000 registros mais recentes** de cada arquivo
    selecionado (`DEFAULT_MAX_RECORDS_PER_FILE`), não o arquivo inteiro. O
    endpoint ordena do mais novo pro mais antigo e pagina, então o que ficou de
    fora não era alcançável de qualquer forma — e sem o limite um diretório de
    log de vários gigabytes ia inteiro pra memória a cada request, derrubando o
    worker antes de responder. Ajuste com
    `make_logs_router(max_records_per_file=...)`; quando o corte acontece, um
    `WARNING` é logado nomeando o `source`.

!!! check "Recap"
    - `configure_logging(log_dir=...)` → stdout **+** um arquivo por nível.
    - Exatidão por nível: cada arquivo só recebe a sua severidade.
    - `500.log` isola erros 500 não tratados (marcador `http_500`).
    - `make_logs_router` serve esses arquivos paginados e autenticados.

## Uma linha por request — `AccessLogMiddleware`

`configure_logging` formata o que você loga e `RequestIDMiddleware` amarra o id
de correlação, mas nada disso emite a **linha por request** que faz o
`make_logs_router` valer a leitura. Esse é o trabalho do `AccessLogMiddleware`:

```python
# src/api/app.py
import logging

from fastapi import FastAPI
from tempest_fastapi_sdk import AccessLogMiddleware, RequestIDMiddleware

app: FastAPI = FastAPI()

app.add_middleware(AccessLogMiddleware, level=logging.INFO)
app.add_middleware(RequestIDMiddleware)
```

Cada request vira um registro cujo `message` é a linha familiar
(`GET /api/users 200 12.4ms`) e cujos detalhes vão como campos de verdade —
`http_method`, `http_path`, `http_query`, `http_status`, `duration_ms`,
`client_ip` — via `extra=`. É essa diferença que faz o `JSONFormatter` gravar
chaves em vez de uma string interpolada, e portanto que faz o `GET /logs`
conseguir filtrar por elas.

!!! info "A ordem de registro não importa (desde a v0.277.0)"
    O `request_id` vem de duas fontes, porque nenhuma sozinha sobrevive às
    duas ordens: o context var que o `RequestIDMiddleware` amarra, e o header
    que ele carimba na resposta. Medido:

    ```text
    AccessLogMiddleware por dentro do RequestIDMiddleware   context var: setado
                                                            header:      ainda não escrito
    AccessLogMiddleware por fora do RequestIDMiddleware     context var: limpo
                                                            header:      presente
    ```

    O `RequestIDMiddleware` é um `BaseHTTPMiddleware`: carimba o header
    **depois** que a app retorna, então o wrapper de `send` de um middleware
    interno já rodou; e limpa o context var ao desenrolar, então um externo
    não acha nada lá. Ler as duas cobre as duas.

    Até a v0.276.0 isto era um `!!! danger` dizendo para registrar na ordem
    certa. Aviso sobre passo mecânico com uma resposta certa é código não
    escrito — a regra do repositório pegou o próprio SDK.

### Nível: `ERROR` é onde a falha está

Resposta abaixo de `500` sai no `level` que você configurou (default `INFO`).
Resposta `5xx` sai em `ERROR` — tanto a que a aplicação renderizou quanto a que
escapou de um handler. Achar request que falhou filtrando por nível é o motivo
de escrever essas linhas.

A exceção que escapa é o caso que mais precisa de log e o que mais falta nas
versões escritas à mão: o handler estourou antes de mandar qualquer coisa, então
não existe status para ler. O middleware registra `500`, acrescenta o campo
`error` com o nome da classe da exceção, e **re-levanta** — quem decide o que o
cliente vê continua sendo o seu tratamento de erro.

### Stream não é request de uma hora

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import AccessLogMiddleware

app: FastAPI = FastAPI()

app.add_middleware(
    AccessLogMiddleware,
    exempt_paths=("/api/sse", "/api/metrics"),
)
```

`exempt_paths` casa por **prefixo**, então `("/api/sse",)` cobre
`/api/sse/stream`. Uma conexão SSE aberta por uma hora sairia no log, no
fechamento, como um request que demorou uma hora.

### Segredo na URL já foi logado

Um endpoint depreciado que recebia um token equivalente a bearer como parâmetro
de path chega ao middleware com o token na URL — recusar o request no handler
**não** desfaz o log. `redact` é a costura para isso, aplicada ao path e à query
separadamente:

```python
# src/api/app.py
import re

from fastapi import FastAPI
from tempest_fastapi_sdk import AccessLogMiddleware

_LEGACY_TOKEN_PATH: re.Pattern[str] = re.compile(
    r"^(?P<prefix>/api)?/auth/google/[^/]+$"
)

app: FastAPI = FastAPI()


def redact_path(value: str) -> str:
    """Replace the secret segment of a legacy path before it is logged."""
    if _LEGACY_TOKEN_PATH.match(value):
        return _LEGACY_TOKEN_PATH.sub(r"\g<prefix>/auth/google/<redacted>", value)
    return value


app.add_middleware(AccessLogMiddleware, redact=redact_path)
```

### Atrás de proxy

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import AccessLogMiddleware

app: FastAPI = FastAPI()

app.add_middleware(AccessLogMiddleware, trusted_ip_header="x-real-ip")
```

`client_ip` sai de `get_client_ip_from_scope`. Nunca aponte
`trusted_ip_header` para um `X-Forwarded-For` cru: esse header é **acrescentado**
ao que o cliente mandou, então a entrada mais à esquerda é escolhida pelo
atacante — e o log passaria a atribuir requests ao endereço que ele quis. Veja
[Segurança »](security.md).

!!! check "Recap"
    - `AccessLogMiddleware` é ASGI puro: lê o status do `http.response.start` e
      deixa o caminho de exceção intocado.
    - O `request_id` entra na linha em qualquer ordem de registro: do context
      var quando ele é interno, do header da resposta quando é externo.
    - `5xx` (renderizado ou escapado) sai em `ERROR`; o resto no `level`
      configurado.
    - `exempt_paths` casa por prefixo — é o que tira o SSE do log.
    - `redact` reescreve path e query antes do registro existir.

## Recap

- `configure_logging` escreve JSON estruturado no stdout **e** em `logs/`, um
  arquivo por nível, cada arquivo com apenas o seu próprio nível.
- `500.log` fica isolado de propósito: o arquivo que você abre primeiro no
  incidente não vem misturado com o resto.
- O id da requisição entra em toda linha, então uma reclamação de usuário vira
  um `grep` — é isso que separa log estruturado de log bonito.
- `make_logs_router` monta `GET /logs` paginado sobre esses arquivos, mais
  recentes primeiro, para ler sem acesso ao disco do container.

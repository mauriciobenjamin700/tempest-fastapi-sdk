# Guia de migração {#migration-guide}

Passo a passo das breaking changes agrupadas por release minor. Siga a versão que corresponde àquela **da qual** você está atualizando.


0.8.0 renomeia todos os campos de `ServerSettings`, extrai os campos de log para um novo mixin `LogSettings` e adiciona outras onze primitivas. As renomeações são as únicas mudanças **breaking** — toda primitiva nova é opt-in.

#### 1. Renomeie as env vars {#1-rename-env-vars}

| Antigo | Novo | Mixin |
| --- | --- | --- |
| `HOST` | `SERVER_HOST` | `ServerSettings` |
| `PORT` | `SERVER_PORT` | `ServerSettings` |
| `DEBUG` | `SERVER_DEBUG` | `ServerSettings` |
| *(novo)* | `SERVER_RELOAD` | `ServerSettings` |
| `LOG_LEVEL` | `LOG_LEVEL` | **movido para** `LogSettings` |
| `LOG_JSON` | `LOG_JSON` | **movido para** `LogSettings` |

`sed` mecânico em todo `.env` / `docker-compose.yml` / manifesto de deploy:

```bash
sed -i \
  -e 's/^HOST=/SERVER_HOST=/' \
  -e 's/^PORT=/SERVER_PORT=/' \
  -e 's/^DEBUG=/SERVER_DEBUG=/' \
  .env .env.example .env.test
```

`LOG_LEVEL` e `LOG_JSON` mantêm os nomes — apenas o mixin muda.

#### 2. Renomeie as referências no código {#2-rename-code-references}

```bash
# `settings.HOST` → `settings.SERVER_HOST`, same for PORT/DEBUG
grep -rn "settings\.\(HOST\|PORT\|DEBUG\)\b" src/ tests/
```

Substitua cada correspondência pela forma `SERVER_*`. Se um serviço estava usando a
antiga flag `settings.DEBUG` para comportamento de debug em nível de aplicação, troque
para `settings.SERVER_DEBUG`; se ela só estava sendo lida para o
auto-reload do uvicorn, troque para `settings.SERVER_RELOAD`.

#### 3. Inclua `LogSettings` no `Settings` do projeto {#3-mix-logsettings-into-the-project-settings}

```diff
 from tempest_fastapi_sdk import (
     BaseAppSettings,
     CORSSettings,
     DatabaseSettings,
     JWTSettings,
+    LogSettings,
     RabbitMQSettings,
     RedisSettings,
     ServerSettings,
 )


 class Settings(
     ServerSettings,
+    LogSettings,
     DatabaseSettings,
     RedisSettings,
     RabbitMQSettings,
     JWTSettings,
     CORSSettings,
     BaseAppSettings,
 ):
     ...
```

Pule este passo se o serviço nunca leu `settings.LOG_LEVEL` /
`settings.LOG_JSON` — `configure_logging` aceita os valores como
keyword arguments diretamente.

#### 4. (Opcional) Adote as novas primitivas {#4-optional-adopt-the-new-primitives}

Escolha o que se encaixa. Nenhuma delas é obrigatória.

- Substitua o `uvicorn.run(...)` escrito à mão em `src/server.py` por
  [`run_server(...)`](recipes/http.md#programmatic-server-entry-point).
- Substitua o `get_current_user` escrito à mão por
  [`make_jwt_user_dependency(tokens, load_user)`](recipes/http.md#jwt-bearer-current-user-role-dependencies).
- Mova os campos `SMTP_*` / `UPLOAD_*` / `TOKEN_SECRET` / `VAPID_*` /
  `TASKIQ_*` do `Settings` do projeto para o
  mixin correspondente do SDK ([Composição de mixins de Settings](recipes/http.md#settings-mixins-composition)).
- Adote o
  [`padrão de dispatcher Outbox`](recipes/queue-tasks.md#outbox-dispatcher-pattern) se
  você já grava side-effects a partir da mesma transação das suas
  linhas de domínio.

#### 5. Verifique {#5-verify}

```bash
uv sync                      # picks up new pyproject deps
uv run pytest -q             # full suite
uv run ruff check src tests  # confirm no `HOST`/`PORT`/`DEBUG` references slipped
```

Se o `pytest` falhar com um `ValidationError` do Pydantic referenciando
`HOST` / `PORT` / `DEBUG`, alguma env var não foi renomeada (verifique o
ambiente do processo ou o `.env`).

---


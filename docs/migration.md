# Guia de migração

Passo a passo das mudanças que quebram compatibilidade, agrupadas por release minor. Siga a versão que casa com aquela **de onde** você está atualizando.


A 0.8.0 renomeia todos os campos de `ServerSettings`, extrai os campos de log para um novo mixin `LogSettings` e adiciona onze outros primitivos. As renomeações são as únicas mudanças **que quebram** — todo primitivo novo é opt-in.

#### 1. Renomeie as variáveis de ambiente

| Antiga | Nova | Mixin |
| --- | --- | --- |
| `HOST` | `SERVER_HOST` | `ServerSettings` |
| `PORT` | `SERVER_PORT` | `ServerSettings` |
| `DEBUG` | `SERVER_DEBUG` | `ServerSettings` |
| *(nova)* | `SERVER_RELOAD` | `ServerSettings` |
| `LOG_LEVEL` | `LOG_LEVEL` | **movida para** `LogSettings` |
| `LOG_JSON` | `LOG_JSON` | **movida para** `LogSettings` |

`sed` mecânico em todo `.env` / `docker-compose.yml` / manifesto de deploy:

```bash
sed -i \
  -e 's/^HOST=/SERVER_HOST=/' \
  -e 's/^PORT=/SERVER_PORT=/' \
  -e 's/^DEBUG=/SERVER_DEBUG=/' \
  .env .env.example .env.test
```

`LOG_LEVEL` e `LOG_JSON` mantêm os nomes — só o mixin muda.

#### 2. Renomeie as referências no código

```bash
# `settings.HOST` → `settings.SERVER_HOST`, idem para PORT/DEBUG
grep -rn "settings\.\(HOST\|PORT\|DEBUG\)\b" src/ tests/
```

Substitua cada ocorrência pela forma `SERVER_*`. Se um serviço usava a
flag antiga `settings.DEBUG` para comportamento de debug a nível de
aplicação, troque para `settings.SERVER_DEBUG`; se ela era lida apenas
para o auto-reload do uvicorn, troque para `settings.SERVER_RELOAD`.

#### 3. Misture `LogSettings` no `Settings` do projeto

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
`settings.LOG_JSON` — `configure_logging` aceita os valores diretamente
como argumentos nomeados.

#### 4. (Opcional) Adote os novos primitivos

Escolha o que se encaixa. Nenhum deles é obrigatório.

- Substitua o `uvicorn.run(...)` escrito à mão no `src/server.py` por
  [`run_server(...)`](recipes/http.md#ponto-de-entrada-programatico-do-servidor).
- Substitua o `get_current_user` escrito à mão por
  [`make_jwt_user_dependency(tokens, load_user)`](recipes/http.md#dependencias-jwt-bearer-usuario-atual-role).
- Mova os campos `SMTP_*` / `UPLOAD_*` / `TOKEN_SECRET` / `VAPID_*` /
  `TASKIQ_*` do `Settings` do projeto para o mixin correspondente do
  SDK ([Composição de mixins de settings](recipes/http.md#composicao-de-mixins-de-settings)).
- Adote o
  [`padrão de outbox dispatcher`](recipes/queue-tasks.md#padrao-outbox-dispatcher) se
  você já escreve efeitos colaterais a partir da mesma transação que
  grava as linhas de domínio.

#### 5. Verifique

```bash
uv sync                      # pega as novas deps do pyproject
uv run pytest -q             # suite completa
uv run ruff check src tests  # confirma que nenhuma referência a `HOST`/`PORT`/`DEBUG` escapou
```

Se o `pytest` falhar com um `ValidationError` do Pydantic referenciando
`HOST` / `PORT` / `DEBUG`, alguma variável de ambiente não foi renomeada
(olhe o ambiente do processo ou o `.env`).

---

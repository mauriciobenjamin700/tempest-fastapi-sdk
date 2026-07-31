# Guia de migração

Passo a passo das mudanças que quebram compatibilidade, agrupadas por release minor. Siga a versão que casa com aquela **de onde** você está atualizando. As seções estão listadas da mais nova para a mais antiga, então num salto de várias versões leia e aplique-as de baixo para cima.

## 0.173.0 — token só vale onde foi emitido pra valer, e cache não é mais compartilhado

Três correções de segurança mudam comportamento de default. Nenhuma exige mexer em código, mas vale conferir se você dependia do comportamento antigo.

### Refresh e MFA-pendente deixam de autorizar rota

`make_bearer_token_dependency`, `make_jwt_user_dependency`, `make_role_dependency`, `make_permission_dependency` e `UserAuthService.current_user_dependency()` aceitam agora **só** token de tipo `access`.

Antes, os três JWTs que o `UserAuthService` emite com o mesmo segredo verificavam identicamente, então o refresh token e o `mfa_token` do passo 1 do login funcionavam como bearer em qualquer rota autenticada — o segundo fator era contornável com só a senha.

Você é afetado se **de propósito** mandava um refresh token para uma rota comum:

```python
from tempest_fastapi_sdk import REFRESH_TOKEN_TYPE, make_bearer_token_dependency

# Volta a aceitar aquele tipo naquela rota específica:
require_refresh = make_bearer_token_dependency(tokens, accepted_typ=(REFRESH_TOKEN_TYPE,))
```

Token assinado à mão com `JWTUtils.encode()` e **sem** `typ` continua aceito — a atualização não derruba sessão ativa. Só os marcadores que o próprio SDK estampava (`refresh: True`, `purpose: "mfa_pending"`) passam a ser rejeitados como access.

### `ResponseCacheMiddleware`: `private` por padrão, credencial não usa o store

Dois defaults mudaram:

- O `Cache-Control` emitido passou de `public, max-age=N` para `private, max-age=N`. Se você servia conteúdo genuinamente compartilhado e contava com cache de CDN, declare de novo: `cache_control="public, max-age=N"`.
- Requisição com `Authorization` ou `Cookie` não lê nem escreve no store compartilhado (`ETag`/`304` continuam). Para recuperar cache em rota autenticada, passe `cache_credentialed=True` — a credencial entra na chave, então cada chamador tem a sua entrada.

O header `X-Cache` também só aparece quando existe `store=`; antes vinha `MISS` mesmo no modo só-ETag.

### `IdempotencyMiddleware`: chave escopada por chamador

A chave passou de `(method, path, key)` para `(chamador, method, path, key)`, com o chamador vindo de um digest de `Authorization`/`Cookie`. Reuse da chave de outra pessoa não devolve mais a resposta dela.

Se o seu cliente troca de credencial entre o pedido original e o retry (rotação de token no meio do backoff), o retry deixa de bater na entrada anterior. Nesse caso aponte a identidade para algo estável:

```python
app.add_middleware(
    IdempotencyMiddleware,
    store=store,
    principal_resolver=lambda request: request.headers.get("x-api-key-id", ""),
)
```

Também mudou: `5xx` não é mais cacheado (`cache_server_errors=True` restaura), `Set-Cookie` fica fora da cópia guardada, e requisições concorrentes com a mesma chave no mesmo processo são serializadas.

## 0.138.1 — `BaseAppSettings` tem que ser a **última** base

A 0.138.1 passou a fazer **todo mixin de settings herdar `BaseAppSettings`** (antes eles estendiam `pydantic_settings.BaseSettings` cru). Isso conserta o `.env` deixando de ser carregado quando um mixin aparecia antes da base — o `model_config` canônico agora é materializado em cada mixin, independente da ordem.

Em troca, a ordem das bases deixou de ser estilo e virou **regra dura**: como os mixins são subclasses de `BaseAppSettings`, a linearização C3 do Python proíbe a base preceder a própria subclasse.

```python
# docs-guard: skip — os dois primeiros exemplos são o erro que a seção descreve
# ❌ quebra em tempo de import
class Settings(DatabaseSettings, BaseAppSettings, RedisSettings): ...

# ❌ também quebra
class Settings(BaseAppSettings, DatabaseSettings): ...

# ✅ BaseAppSettings por último
class Settings(DatabaseSettings, RedisSettings, BaseAppSettings): ...
```

Antes da 0.159.1 o sintoma era o `TypeError` cru do pydantic, que não indica a correção:

```text
TypeError: Cannot create a consistent method resolution order (MRO) for bases BaseAppSettings, RedisSettings
```

e o `mypy` (com o plugin do pydantic) acusava duas vezes na mesma linha, sendo a segunda enganosa — sugere conflito de metaclasse quando a causa é só a posição de uma base:

```text
settings.py:4: error: Cannot determine consistent method resolution order (MRO) for "Settings"  [misc]
settings.py:4: error: Metaclass conflict: the metaclass of a derived class must be a (non-strict) subclass of the metaclasses of all its bases  [metaclass]
```

A partir da 0.159.1, `BaseAppSettings` usa a metaclasse [`AppSettingsMeta`](reference.md), que pré-checa a posição das bases e troca a mensagem por uma instrução:

```text
TypeError: Settings: BaseAppSettings must be the LAST base — RedisSettings already subclasses it, so listing BaseAppSettings before it is an invalid method resolution order (MRO). Move BaseAppSettings to the end of the base list: class Settings(RedisSettings, BaseAppSettings).
```

### Verifique

```bash
# procure Settings com BaseAppSettings fora do fim da lista de bases
grep -rn "class Settings(" -A 12 src/core/settings.py
```

- Mova `BaseAppSettings` para o **último** item da lista de bases.
- A ordem **entre os mixins** continua livre — só a posição da base importa.
- Nenhuma mudança de env var, de campo ou de valor: é exclusivamente ordem de herança.

## 0.92.0 — coluna `payload` no token de usuário

A 0.92.0 adiciona o fluxo de **troca / re-verificação / recuperação de e-mail**. Para carregar o e-mail pendente até a confirmação, `BaseUserTokenModel` ganhou uma coluna nova:

```python
payload: Mapped[str | None] = mapped_column(String(320), nullable=True, default=None)
```

Como sua tabela `user_tokens` herda de `BaseUserTokenModel`, a coluna aparece automaticamente no modelo — mas o banco precisa de uma **migration**. É aditiva e segura (coluna anulável, sem default obrigatório):

```bash
# gere e aplique
tempest db revision -m "add payload to user_tokens"
tempest db upgrade
```

Ou, na mão:

```sql
ALTER TABLE user_tokens ADD COLUMN payload VARCHAR(320) NULL;
```

!!! info "Só isso"
    Nenhuma renomeação, nenhum default backfill. Fluxos existentes (ativação, reset de senha) continuam gravando `payload = NULL`. O novo fluxo de e-mail é totalmente opt-in — a recuperação (`POST /auth/email-recovery/request`) só é montada com `AUTH_EMAIL_RECOVERY_ENABLED=True`.

### Verifique

- Rode a migration antes de subir a 0.92.0 (a coluna precisa existir).
- Se você escreve `src/db/models/user_token.py` à mão em vez de usar `make_user_token_model`, a coluna vem da base abstrata — não precisa redeclarar, só migrar.

## 0.63.0 — usuário autenticado carregado na sessão de request

Antes da 0.63.0, `UserAuthService.current_user_dependency()` carregava o usuário autenticado chamando `load_user`, que abria a **própria** sessão (via `db.get_session_context()`) e a fechava ao terminar. O `UserModel` entregue à rota ficava **detached**: mutá-lo e dar `commit`/`refresh` na sessão de request (a dos seus repositories) levantava
`InvalidRequestError: Instance is not persistent within this Session`.

A partir da 0.63.0 a dependência carrega o usuário na **sessão de request** (`db.session_dependency` por padrão), via `get_user(subject, session)`. O usuário fica anexado à mesma sessão que os repositories usam, então leituras de relacionamentos lazy e escritas funcionam sem reanexar nada.

!!! warning "Compatibilidade"
    A dependência de auth e seus repositories precisam compartilhar o **mesmo callable** de sessão para o cache de sub-dependências do FastAPI casar. Quem segue o padrão recomendado já está coberto:

    ```python
    # resources.py
    get_session = db.session_dependency          # um único objeto, reutilizado
    ```

    Se você embrulha a sessão num provider próprio (`async def get_session(): ...`), passe-o explicitamente para a dependência, senão ela abre uma segunda sessão e o usuário volta a ficar detached:

    ```python
    get_current_user = auth.current_user_dependency(session_dependency=get_session)
    ```

!!! info "Defesa adicional"
    `BaseRepository.resolve()` agora reanexa instâncias detached via `session.merge()`. Mesmo que algum fluxo ainda receba um usuário detached, o `resolve` o traz de volta à sessão ativa em vez de quebrar — então serviços que faziam workarounds manuais (re-fetch por id antes de mutar) podem removê-los.

### Verifique

- Remova qualquer workaround do tipo "re-fetch por id antes de mutar o usuário autenticado" — não é mais necessário.
- Se você passava um `user_loader` de um argumento para `make_jwt_user_dependency`, ele continua funcionando. Para compartilhar a sessão de request, passe `session_dependency=` e use um loader de dois argumentos `(subject, session)`.

## 0.8.0 — renomeação de `ServerSettings`

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
  [`Outbox`](recipes/outbox.md) se
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

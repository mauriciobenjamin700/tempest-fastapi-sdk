# System checks (`tempest check-config`)

Valide a configuração **antes** de servir tráfego — segredo de assinatura
vazio, CORS `*` com credenciais, SQLite em produção. Um framework de
checks no estilo Django: funções que inspecionam suas settings e emitem
mensagens; a CLI (ou um hook de startup) roda todas e falha se alguma for
séria.

## O problema

Um deploy com `JWT_SECRET` vazio sobe feliz e só quebra (ou pior, aceita
tokens forjados) em produção. Erros de config não aparecem nos testes —
eles dependem do ambiente. Faltava um lugar para declarar "isto tem que
ser verdade pra subir".

## Rodando os checks embutidos

A SDK já traz checks para os deslizes mais comuns. Rode contra as
settings do projeto:

```bash
tempest check-config
```

A CLI auto-detecta o objeto de settings em locais convencionais
(`src.core.settings:settings`, `app.core.settings:settings`, …). Aponte
manualmente quando precisar:

```bash
tempest check-config --settings src.core.settings:settings
```

Saída típica:

```text
WARNING: (security.W004) JWT_SECRET still holds the default declared by its settings field — a deployment that never set it signs tokens with a value that lives in source control.
	HINT: Generate one with `tempest secrets rotate`.
INFO: (deployment.I001) SERVER_DEBUG is enabled.
	HINT: Ensure SERVER_DEBUG is off in production (it leaks internals).
2 message(s), 0 at/above ERROR.
```

Sai com código **≠ 0** quando alguma mensagem atinge o `--fail-level`
(padrão `error`) — então serve como gate de CI e checagem pré-deploy.
Suba a régua para tratar avisos como bloqueio:

```bash
tempest check-config --fail-level warning
```

Checks embutidos (todos best-effort — pulam silenciosamente quando o
atributo não existe nas suas settings):

| id | Nível | O quê |
|----|-------|-------|
| `security.W001` / `W002` | WARNING | `JWT_SECRET` / `SECRET_KEY` / `TOKEN_SECRET` vazio ou < 32 chars |
| `security.W003` | WARNING | CORS `*` **com** credenciais |
| `security.W004` | WARNING | segredo ainda igual ao default declarado no próprio campo |
| `database.W001` | WARNING | `DATABASE_URL` SQLite com o debug desligado |
| `deployment.I001` | INFO | `SERVER_DEBUG` (ou `DEBUG`) ligado |
| `deployment.I002` | INFO | bind em `0.0.0.0` |

!!! warning "O segredo que passava era o único garantidamente errado"
    Até a v0.271.0 o `security.W002` só reprovava segredo com menos de
    32 caracteres — e o placeholder que o próprio SDK ships em
    `JWTSettings.JWT_SECRET` tem **exatos** 32. Um deploy que esqueceu
    de definir `JWT_SECRET` assinava token com um valor publicado no
    código-fonte, e o `tempest check-config` dizia que estava tudo bem.

    O `security.W004` compara o valor com
    `model_fields[nome].default`, nunca com uma cópia da string — então
    ele continua valendo no dia em que o placeholder mudar.

!!! note "Qual campo o check de debug lê"
    `deployment.I001` e `database.W001` resolvem o debug por precedência
    de nome: primeiro `SERVER_DEBUG` (que é como o mixin
    `ServerSettings` chama o campo), depois `DEBUG`, para o projeto que
    declarou um na mão continuar funcionando. Até a v0.271.0 os dois
    liam só `DEBUG` — o `I001` nunca disparava, e o `database.W001`
    avisava ao contrário, reclamando de SQLite justamente com o debug
    **ligado**.

## Escrevendo o seu check

Um check é uma função que recebe o contexto (suas settings) e devolve
mensagens. Decore com `@check`:

```python
from tempest_fastapi_sdk.checks import check, error, CheckMessage


@check("security")
def stripe_key_present(settings: object) -> list[CheckMessage]:
    """Falha o deploy se a chave da Stripe não estiver configurada."""
    if not getattr(settings, "STRIPE_API_KEY", ""):
        return [
            error(
                "STRIPE_API_KEY is not set.",
                hint="Export it before deploying the billing service.",
                id="billing.E001",
            )
        ]
    return []
```

Os construtores `debug` / `info` / `warning` / `error` / `critical`
montam a `CheckMessage` com o nível certo. A tag (`"security"`) permite
rodar um subconjunto:

```bash
tempest check-config --tag security
```

!!! note "Checks precisam ser importados para registrar"
    O `@check` registra no import do módulo. A CLI importa suas settings
    (e o que elas importarem), então checks definidos junto das settings
    carregam sozinhos. Para módulos soltos, use `--import`:

    ```bash
    tempest check-config --import src.checks --import src.billing.checks
    ```

## Falhando rápido no startup

Rode os checks no lifespan para um deploy mal-configurado **não** servir
tráfego:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from tempest_fastapi_sdk.checks import run_system_checks, SystemCheckError

from src.core.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    try:
        run_system_checks(settings)   # levanta em ERROR+
    except SystemCheckError as exc:
        # logue exc.messages e aborte o boot
        raise
    yield
```

`run_system_checks` levanta `SystemCheckError` quando alguma mensagem
atinge o `fail_level` (padrão `ERROR`); `run_checks` faz o mesmo mas só
devolve a lista, sem levantar.

## Guard de produção: `EnvironmentSettings`

Os checks acima **avisam**. Para o que nunca pode subir em produção — SQLite,
o `JWT_SECRET` placeholder, CORS `*` — avisar não basta: o deploy precisa
cair. É isso que o mixin `EnvironmentSettings` faz. Ele traz o campo `ENV`
(`development`, `test` ou `production`) e, com `ENV=production`, recusa
construir o settings enquanto algum mixin composto guardar valor de
desenvolvimento:

```python
import os

from pydantic import ValidationError
from tempest_fastapi_sdk import (
    BaseAppSettings,
    DatabaseSettings,
    EnvironmentSettings,
    JWTSettings,
    ServerSettings,
)


class Settings(
    EnvironmentSettings,
    ServerSettings,
    DatabaseSettings,
    JWTSettings,
    BaseAppSettings,
):
    """Settings do serviço, com o guard de produção."""


os.environ["ENV"] = "production"

try:
    Settings()
except ValidationError as exc:
    print(exc)
```

Sem `DATABASE_URL` nem `JWT_SECRET` no ambiente, a saída lista **todos** os
campos de uma vez — pelo nome, nunca pelo valor:

```text
1 validation error for Settings
  Value error, ENV=production refuses development values:
- DATABASE_URL: points at SQLite (the development default)
- JWT_SECRET: still the public placeholder declared by JWTSettings [type=value_error]
```

Com `ENV=development` (o default) ou `ENV=test`, nada muda.

### O que cada mixin recusa

O guard só olha os mixins que você compõe. Cada um declara as próprias
regras no método `production_violations()`:

| Mixin | Recusado em produção |
| --- | --- |
| `ServerSettings` | `SERVER_DEBUG=true`, `SERVER_RELOAD=true` |
| `DatabaseSettings` | `DATABASE_URL` com dialeto `sqlite` |
| `JWTSettings` | `JWT_SECRET` igual ao default declarado no mixin |
| `CORSSettings` | `"*"` em `CORS_ORIGINS`; com `CORS_ALLOW_CREDENTIALS=true`, também `"null"` em `CORS_ORIGINS` e um `CORS_ORIGIN_REGEX` que casa com uma origem reservada `.invalid` (`.*`, `https://.*`; regex parcialmente ancorado como `https://.*example\.com` não é detectado) — o Starlette devolve a própria `Origin` do chamador com `Access-Control-Allow-Credentials: true` |
| `TokenSettings` | `TOKEN_SECRET` vazio (desliga o `X-Token`) |
| `TaskIQSettings` | `TASKIQ_BROKER_URL` vazio (cai no broker em memória) |
| `StorageSettings` | chaves `minioadmin` do default, `STORAGE_SECURE=false` (exceto bucket privado atrás de endpoint público HTTPS — veja [Storage](storage.md#bucket-privado-atras-de-https)), `STORAGE_PUBLIC_SECURE=false` explícito |
| `EmailSettings` | host `mailpit`/`mailhog`, loopback na porta `1025`, `SMTP_HOST` default sem `SMTP_USERNAME`, `SMTP_FROM_ADDR` default ou com domínio reservado |
| `RevenueCatSettings` | os dois segredos de webhook vazios |
| `AuthSettings` | `AUTH_AUTO_ACTIVATE=true` e `AUTH_RETURN_TOKEN_IN_RESPONSE=true` (sempre); template de link do e-mail (`AUTH_*_URL_TEMPLATE`) em host local/reservado ou `http://` (com o e-mail ligado) |

!!! info "O placeholder vem do mixin, não de um literal"
    O `JWT_SECRET` é comparado com `JWTSettings.model_fields["JWT_SECRET"].default`.
    Se o default mudar, a checagem acompanha.

### Acrescentando (ou dispensando) uma regra

`production_violations()` é cooperativo: sobrescreva no seu `Settings`,
chame `super()` e trabalhe sobre a lista. Assim você soma uma regra do
serviço — ou tira uma que aceitou de propósito, como SQLite num serviço de
nó único:

```python
import os

from pydantic import Field, ValidationError
from tempest_fastapi_sdk import BaseAppSettings, DatabaseSettings, EnvironmentSettings


class Settings(EnvironmentSettings, DatabaseSettings, BaseAppSettings):
    """Serviço de nó único: SQLite em produção é decisão tomada."""

    PUBLIC_URL: str = Field(default="http://localhost:8000")

    def production_violations(self) -> list[str]:
        """Aceita SQLite e exige HTTPS na URL pública.

        Returns:
            list[str]: As violações dos mixins, menos a do SQLite, mais a
            regra do serviço.
        """
        violations: list[str] = [
            entry
            for entry in super().production_violations()
            if not entry.startswith("DATABASE_URL:")
        ]
        if not self.PUBLIC_URL.startswith("https://"):
            violations.append("PUBLIC_URL: not HTTPS")
        return violations


os.environ["ENV"] = "production"

try:
    Settings()
except ValidationError as exc:
    print(exc)

os.environ["PUBLIC_URL"] = "https://api.example.com"
print(Settings().ENV)
```

```text
1 validation error for Settings
  Value error, ENV=production refuses development values:
- PUBLIC_URL: not HTTPS [type=value_error]
production
```

!!! tip "Erro de validação não imprime o ambiente"
    O `BaseAppSettings` liga `hide_input_in_errors=True`.
    Num `BaseSettings` o *input* de um erro é o ambiente inteiro — um campo
    obrigatório faltando imprimia `input_value={'JWT_SECRET': ...}` no
    traceback do boot. Agora a mensagem traz só o campo e o motivo. Quem
    quiser o input de volta sobrescreve `hide_input_in_errors` no próprio
    `model_config`.

### Ligar e desligar integrações (deploy por partes)

Nem todo serviço sobe para produção com tudo pronto. O banco e o JWT estão
configurados, mas o bucket S3, o SMTP e a conta do Firebase chegam numa
fase seguinte. Sem saída, o guard recusa o boot por causa das integrações
que você **ainda nem usa** — e a tentação é preencher valor falso só para
passar, que é exatamente o que o guard existe para impedir.

A saída explícita é um interruptor por integração:

| Mixin | Interruptor | Default |
| --- | --- | --- |
| `StorageSettings` | `STORAGE_ENABLED` | `true` |
| `EmailSettings` | `EMAIL_ENABLED` | `true` |
| `FirebaseSettings` | `FIREBASE_ENABLED` | `true` |
| `RevenueCatSettings` | `REVENUECAT_ENABLED` | `true` |

Com o interruptor em `false`, três coisas acontecem, sempre juntas:

1. O guard **pula** as regras daquele mixin — o boot passa.
2. O nome da integração aparece em `settings.disabled_integrations()`, para
   você logar no boot ou mostrar no readiness. Desligar nunca é invisível.
3. O cliente construído a partir das mesmas settings **recusa toda
   chamada** com `503 INTEGRATION_DISABLED`, em vez de tentar falar com
   o default de desenvolvimento.

Um `.env` de produção com banco e JWT prontos, e o resto desligado:

```text
ENV=production
DATABASE_URL=postgresql+asyncpg://app:s3nha@db:5432/app
JWT_SECRET=troque-por-um-segredo-longo-de-verdade-0123456789
STORAGE_ENABLED=false
EMAIL_ENABLED=false
FIREBASE_ENABLED=false
```

E o serviço, sem nenhum `if` sobre os interruptores — a mesma linha de
construção de sempre:

```python
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BaseAppSettings,
    DatabaseSettings,
    EmailSettings,
    EmailUtils,
    EnvironmentSettings,
    FirebaseSettings,
    JWTSettings,
    StorageSettings,
    register_exception_handlers,
)

logger = logging.getLogger("app")


class Settings(
    EnvironmentSettings,
    DatabaseSettings,
    JWTSettings,
    StorageSettings,
    EmailSettings,
    FirebaseSettings,
    BaseAppSettings,
):
    """Settings do serviço: banco e JWT prontos, o resto ainda não."""


settings = Settings()
print("desligadas:", sorted(settings.disabled_integrations()))
print("push mobile:", settings.enabled)

storage = AsyncMinIOClient(**settings.storage_kwargs())
mailer = EmailUtils(**settings.email_kwargs())

app = FastAPI()
register_exception_handlers(app)


@app.post("/avatar")
async def upload_avatar() -> dict[str, str]:
    """Grava o avatar no bucket."""
    await storage.put_object("avatar.png", b"\x89PNG")
    return {"status": "ok"}


response = TestClient(app).post("/avatar")
print(response.status_code, response.json())
```

O boot passa, e a rota que usa o bucket responde `503` com o nome da
integração:

```text
desligadas: ['email', 'firebase', 'storage']
push mobile: False
503 {'detail': 'The storage integration is disabled in this environment', 'code': 'INTEGRATION_DISABLED', 'details': {'integration': 'storage'}}
```

Troque `STORAGE_ENABLED=false` por `true` sem configurar o bucket, e o
boot volta a cair, como antes:

```text
- STORAGE_ACCESS_KEY: still the MinIO default credential
- STORAGE_SECRET_KEY: still the MinIO default credential
- STORAGE_SECURE: storage traffic over plain HTTP (allowed only for a private STORAGE_ENDPOINT behind an HTTPS STORAGE_PUBLIC_ENDPOINT) [type=value_error]
```

O que cada integração faz quando desligada:

| Integração | Construção | Em uso |
| --- | --- | --- |
| `storage` | `AsyncMinIOClient(**settings.storage_kwargs())` recebe `enabled=False`; não cria (nem importa) o cliente `minio` | toda operação — inclusive o atributo `client` — levanta `IntegrationDisabledException`; um `SubjectObjectStorage` por cima herda a recusa |
| `email` | `EmailUtils(**settings.email_kwargs())` recebe `enabled=False`; não exige o extra `[email]` | `send` e `send_many` levantam `IntegrationDisabledException` antes de montar a mensagem; `render_template` continua funcionando |
| `firebase` | `settings.enabled` (e `PushSettings.mobile_enabled`) vira `False`; `firebase_kwargs()` devolve só `{"enabled": False}` | `FirebaseAuth` não inicializa app e responde `503` em `verify` e nas dependências; um `FCMTransport` sobre ele não reivindica plataforma: o push mobile é **descartado** (o `DeviceService` pula o aparelho) e o transporte loga um aviso ao ser construído |
| `revenuecat` | só o guard e o `disabled_integrations()` mudam | o webhook continua lendo os próprios segredos: com os dois vazios, segue recusando toda entrega com `401` |

!!! info "A chave `enabled` só aparece com a integração desligada"
    `storage_kwargs()` e `email_kwargs()` acrescentam `enabled=False`
    **apenas** quando o interruptor está em `false`. Com o default, o
    dicionário é exatamente o de antes — quem faz splat dele numa classe
    própria não quebra ao atualizar o SDK.

!!! warning "O e-mail ligado ganhou regra de produção"
    Com `EMAIL_ENABLED=true` e `ENV=production`, o guard agora recusa o
    que só pode ser desenvolvimento: `SMTP_HOST` cujo primeiro rótulo é
    `mailpit` ou `mailhog`; `SMTP_PORT=1025` junto de host de loopback
    (`localhost`, `127.0.0.1`, `::1`) — a porta que o Mailpit publica e a
    que o `tempest new` escreve; `SMTP_HOST` ainda no default declarado
    (`localhost`) sem `SMTP_USERNAME`; e `SMTP_FROM_ADDR` igual ao default
    declarado, vazio, sem `@`, ou com
    domínio reservado (`example.com`, `example.net`, `example.org`,
    `localhost` e seus subdomínios, ou qualquer coisa sob `.example`, `.invalid`,
    `.localhost`, `.test`). O default `noreply@example.com` está nessa
    lista. Um relay local (Postfix repassando ao provedor) segue subindo
    com `SMTP_USERNAME` preenchido, ou endereçado como `127.0.0.1` em outra
    porta que não `1025`. O Gmail (`smtp.gmail.com:587`, STARTTLS, senha de
    app em `SMTP_USERNAME`/`SMTP_PASSWORD`, remetente real) passa sem
    violação.
    Serviço que compõe `EmailSettings` sem configurar o SMTP passa a
    precisar de `EMAIL_ENABLED=false` para subir em produção.

O fluxo de autenticação também respeita o interruptor do e-mail: com
`EmailUtils(enabled=False)` ligado ao `UserAuthService`, toda rota do
`make_auth_router` que enviaria um link responde `503
INTEGRATION_DISABLED` **antes** de gravar qualquer coisa — veja
[Fluxo de autenticação](auth-flow.md#e-mail-desligado).

## Recap

- `tempest check-config` roda os checks contra suas settings; sai ≠ 0 no
  `--fail-level` (padrão `error`).
- Embutidos cobrem segredo, CORS, SQLite-em-prod, DEBUG, bind.
- `@check("tag")` registra o seu; `debug`/`info`/`warning`/`error`/
  `critical` montam a mensagem.
- `run_system_checks(settings)` no lifespan aborta um boot mal-configurado.
- `EnvironmentSettings` com `ENV=production` **derruba** o boot enquanto um
  mixin composto guardar valor de desenvolvimento, listando os campos sem
  imprimir valor; `production_violations()` + `super()` soma ou dispensa
  regra.
- `STORAGE_ENABLED`, `EMAIL_ENABLED`, `FIREBASE_ENABLED` e
  `REVENUECAT_ENABLED` em `false` desligam a integração: o guard a pula,
  `disabled_integrations()` a nomeia e o cliente construído das settings
  responde `503 INTEGRATION_DISABLED` em toda chamada.

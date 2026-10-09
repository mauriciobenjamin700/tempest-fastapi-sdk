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
| `CORSSettings` | `"*"` em `CORS_ORIGINS` |
| `TokenSettings` | `TOKEN_SECRET` vazio (desliga o `X-Token`) |
| `TaskIQSettings` | `TASKIQ_BROKER_URL` vazio (cai no broker em memória) |
| `StorageSettings` | chaves `minioadmin` do default, `STORAGE_SECURE=false`, `STORAGE_PUBLIC_SECURE=false` explícito |

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

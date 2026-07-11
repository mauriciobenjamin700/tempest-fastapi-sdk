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
WARNING: (security.W001) JWT_SECRET is empty — token verification is effectively disabled.
	HINT: Set a random secret in production (dev-only when empty).
INFO: (deployment.I001) DEBUG is enabled.
	HINT: Ensure DEBUG is off in production (it leaks internals).
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
| `database.W001` | WARNING | `DATABASE_URL` SQLite com `DEBUG` desligado |
| `deployment.I001` | INFO | `DEBUG` ligado |
| `deployment.I002` | INFO | bind em `0.0.0.0` |

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
from collections.abc import AsyncIterator

from fastapi import FastAPI

from tempest_fastapi_sdk.checks import run_system_checks, SystemCheckError

from src.core.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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

## Recap

- `tempest check-config` roda os checks contra suas settings; sai ≠ 0 no
  `--fail-level` (padrão `error`).
- Embutidos cobrem segredo, CORS, SQLite-em-prod, DEBUG, bind.
- `@check("tag")` registra o seu; `debug`/`info`/`warning`/`error`/
  `critical` montam a mensagem.
- `run_system_checks(settings)` no lifespan aborta um boot mal-configurado.

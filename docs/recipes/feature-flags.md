# Feature flags

Ligue e desligue funcionalidades **sem redeploy**: rollouts graduais, kill-switches, beta por trás de um flag. O SDK traz um serviço `FeatureFlags` sobre backends plugáveis (env estático, Redis em runtime, ou os dois em camadas) e uma dependência FastAPI que protege rotas.

## Começo rápido

```python
from tempest_fastapi_sdk import FeatureFlags, MemoryFeatureFlagBackend

flags = FeatureFlags(MemoryFeatureFlagBackend({"new_checkout": True}))

if await flags.is_enabled("new_checkout"):
    ...                                                       # caminho novo
```

`is_enabled(name)` devolve o valor do flag, ou o `default` do serviço (`False`) quando o flag não existe. Passe `default=True` por chamada para inverter isso pontualmente. Toggle com `enable` / `disable` / `set`, e liste tudo com `all()`.

## Backends

| Backend | Quando usar |
| --- | --- |
| `MemoryFeatureFlagBackend(initial=...)` | Testes e dev (em processo). |
| `EnvFeatureFlagBackend(prefix="FEATURE_")` | Config **estática** — `new_checkout` lê `FEATURE_NEW_CHECKOUT`. Read-only (`set` levanta). |
| `RedisFeatureFlagBackend(redis_client, key="feature_flags")` | Toggle em **runtime**, compartilhado entre réplicas (um hash Redis). |
| `CompositeFeatureFlagBackend([redis, env])` | Camadas: o override do Redis vence o default do env. |

!!! info "Valores aceitos como verdadeiros"
    `1`, `true`, `yes`, `on`, `t`, `y` (case-insensitive) viram `True`; qualquer outra coisa é `False`. Vale para env e Redis.

### Produção: Redis sobre env

O padrão recomendado usa o env como default estático (versionado no deploy) e o Redis como override de runtime (o time liga/desliga sem subir release):

```python
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    CompositeFeatureFlagBackend,
    EnvFeatureFlagBackend,
    FeatureFlags,
    RedisFeatureFlagBackend,
)


def build_flags(redis: Redis) -> FeatureFlags:
    """Monta o serviço de flags com override Redis sobre default de env.

    Args:
        redis (Redis): Cliente async de Redis conectado.

    Returns:
        FeatureFlags: O serviço pronto para injetar.
    """
    backend = CompositeFeatureFlagBackend(
        [
            RedisFeatureFlagBackend(redis, key="feature_flags"),  # runtime
            EnvFeatureFlagBackend(prefix="FEATURE_"),             # default
        ]
    )
    return FeatureFlags(backend)
```

## Protegendo uma rota

`make_flag_dependency(flags, name)` devolve uma dependência async que deixa a rota passar só quando o flag está ligado. Caso contrário levanta `AppException` no envelope do SDK — `404` por padrão, para a feature simplesmente "não existir":

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import make_flag_dependency

from src.api.dependencies.resources import get_flags

router = APIRouter()
flags = get_flags()


@router.get(
    "/checkout/v2",
    dependencies=[Depends(make_flag_dependency(flags, "new_checkout"))],
)
async def checkout_v2() -> dict[str, bool]:
    """Só responde quando ``new_checkout`` está ligado."""
    return {"ok": True}
```

Para um **kill-switch** de algo legado, inverta o gate com `enabled=False` (a rota só responde enquanto o flag está desligado):

```python
@router.get(
    "/legacy",
    dependencies=[
        Depends(make_flag_dependency(flags, "legacy_disabled", enabled=False)),
    ],
)
async def legacy() -> dict[str, bool]:
    """Para de responder no instante em que ``legacy_disabled`` é ligado."""
    return {"ok": True}
```

`status_code`, `detail` e `code` são configuráveis — use `status_code=403` quando quiser sinalizar "existe mas é proibido" em vez de esconder com `404`.

## Recapitulando

- `FeatureFlags(backend, default=False)` — `is_enabled` / `enable` / `disable` / `set` / `all`.
- Backends: `Memory` (dev), `Env` (estático read-only), `Redis` (runtime), `Composite` (camadas).
- `make_flag_dependency(flags, name, enabled=True, status_code=404)` protege rotas.
- Override Redis sobre default de env é o padrão de produção — toggle sem redeploy.

# 9. Detectar queries lentas

Use para logar queries acima de um limite com `SlowQueryLogger`.

Parte da receita [Banco de dados](../database.md).

`SlowQueryLogger` registra um listener na engine e emite uma linha de log
para toda instrução acima de um limiar. Anexe uma vez no boot:

```python
# src/api/app.py — depois de db.connect()

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk.db import SlowQueryLogger

from src.api.dependencies.resources import db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Connect, instrument slow queries, then serve."""
    await db.connect()
    slow = SlowQueryLogger(db.engine, threshold_ms=200.0)
    slow.attach()
    yield
    await db.disconnect()
```

| Parâmetro | Padrão | Para quê |
| --- | --- | --- |
| `threshold_ms` | `500.0` | Instruções neste tempo ou acima são logadas. |
| `level` | `logging.WARNING` | Nível das linhas de slow-query. |
| `log_parameters` | `False` | Inclui os bind params na linha. **Só em dev** — podem carregar PII. |
| `explain` | `False` | Roda `EXPLAIN` e anexa o plano. Custa um round-trip por query lenta. |

!!! danger "`log_parameters=True` só em desenvolvimento"
    Os bind parameters podem conter segredos e PII. Mantenha `False` em
    produção — o padrão já é seguro.

**Recap:** `SlowQueryLogger(db.engine, threshold_ms=...).attach()` no
lifespan transforma queries lentas em linhas de log acionáveis, com
`EXPLAIN` opcional para investigar planos.

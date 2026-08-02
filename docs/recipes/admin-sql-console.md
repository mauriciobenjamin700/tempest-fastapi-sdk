# Console SQL no admin

Todo painel administrativo sério acaba criando um destes — phpMyAdmin,
Adminer, native query do Metabase, Django SQL Explorer — porque uma hora
alguém precisa de uma resposta que a list view não dá. Este é esse console,
com as grades de proteção que o tornam sobrevivível.

```bash
uv add "tempest-fastapi-sdk[admin,admin-sql]"
```

!!! danger "Leia isto antes de habilitar"
    **Filtro de SQL na aplicação é defesa em profundidade, não fronteira de
    segurança.**

    O analisador aqui faz *parsing* de verdade (via `sqlglot`) em vez de
    casar strings, e isso barra os acidentes comuns: um `DROP` digitado por
    quem só queria `SELECT`, um `UPDATE` sem `WHERE`, uma consulta numa
    tabela com dados de cartão. Ele **não** vai barrar um operador
    determinado com tempo — SQL tem CTE, subquery, função, extensão de
    dialeto e truque de comentário, e qualquer allowlist baseada em parser é
    um jogo de cobertura.

    A fronteira que **realmente** segura é o usuário do banco:

    ```sql
    CREATE ROLE admin_console LOGIN PASSWORD '…';
    GRANT CONNECT ON DATABASE app TO admin_console;
    GRANT SELECT ON orders, customers, invoices TO admin_console;
    ```

    Aponte o console para *essa* conexão e use a política abaixo para
    estreitar mais e produzir uma recusa legível em vez de um erro de banco.
    Assim as duas camadas se somam. Só com a política, ela é um quebra-molas.

## Ligar

O console é **desligado por padrão** — sem `sql_shell=`, a rota nem existe.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import AsyncDatabaseManager, UserModelAuthBackend
from tempest_fastapi_sdk.admin import (
    AdminSite,
    SqlCapability,
    SqlShellPolicy,
    SqlShellService,
    make_admin_router,
)

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel
from src.services.audit import record_sql_attempt

auth_backend = UserModelAuthBackend(UserModel)

console_db = AsyncDatabaseManager(settings.READONLY_DATABASE_URL)

app = FastAPI()


console = SqlShellService(
    console_db,                       # a conexão restrita, não a do app
    policy=SqlShellPolicy(
        capabilities={SqlCapability.READ},
        denied_tables={"users", "user_tokens"},
        max_rows=500,
    ),
    dialect="postgres",
    auditor=record_sql_attempt,
)

app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=auth_backend,
        secret_key=settings.SECRET_KEY,
        sql_shell=console,
    ),
)
```

Aparece um item "SQL console" na barra lateral, sob "System".

## A política

| Campo | Default | O que faz |
| --- | --- | --- |
| `capabilities` | `{READ}` | Famílias de statement permitidas. |
| `allowed_tables` | vazio (todas) | Quando preenchido, **só** essas tabelas. |
| `denied_tables` | vazio | Nunca acessíveis — **deny ganha de allow**. |
| `max_rows` | `1000` | Linhas buscadas antes de truncar. |
| `max_statements` | `1` | Statements por submissão. |
| `require_where` | `True` | Recusa `UPDATE`/`DELETE` sem `WHERE`. |
| `statement_timeout_ms` | `10000` | Timeout no servidor, onde o dialeto suporta. |

### Capacidades

São separadas como um operador pensa em risco, não como o SQL agrupa
palavras-chave:

| Capacidade | Cobre |
| --- | --- |
| `READ` | `SELECT`, `WITH … SELECT`, `EXPLAIN`, `SHOW` |
| `INSERT` | insere linhas |
| `UPDATE` | altera linhas |
| `DELETE` | remove linhas |
| `DDL` | `CREATE`, `ALTER`, `COMMENT` |
| `DROP` | `DROP` e `TRUNCATE` — perda irreversível |
| `ADMIN` | `GRANT`, `REVOKE`, `SET`, **e tudo que o analisador não souber classificar** |

!!! check "O desconhecido cai na capacidade mais privilegiada"
    Um construto que ninguém previu vira `ADMIN`, então precisa da permissão
    mais alta em vez de passar como inofensivo. É o default seguro: um
    parser que não reconhece algo não deve deixá-lo passar.

### Deny ganha de allow

```python
from tempest_fastapi_sdk.admin import SqlShellPolicy


policy = SqlShellPolicy(
    allowed_tables={"users", "orders"},
    denied_tables={"users"},
)
policy.table_allowed("users")   # False
policy.table_allowed("orders")  # True
```

Ordem deliberada: colocar uma tabela no `denied_tables` não pode ser desfeito
por uma regra de allow ampla em outro lugar.

### Tabelas escondidas são encontradas

O analisador percorre a árvore inteira, então subquery, CTE e join contam:

```python
from tempest_fastapi_sdk.admin import analyze_sql

analyze_sql("SELECT * FROM orders WHERE id IN (SELECT id FROM secrets)")[0].tables
```

```text
['orders', 'secrets']
```

Uma política que olhasse só o `FROM` de topo perderia exatamente onde alguém
esconde uma tabela que não deveria ler.

!!! note "Alias de CTE não conta como tabela"
    `WITH recent AS (SELECT * FROM orders) SELECT * FROM recent` reporta
    `['orders']`, não `recent`. Do contrário um `allowed_tables` recusaria
    uma consulta que só lê o que devia.

### Multi-statement é recusado por padrão

```text
SELECT 1; DROP TABLE users
```

```text
2 statements submitted; the policy allows 1
```

É assim que um `SELECT` permitido carrega um `DROP` de carona para quem só
bateu o olho na caixa de texto. Abra com `max_statements` se você realmente
precisa.

### `UPDATE`/`DELETE` sem `WHERE`

```text
update without a WHERE clause is refused; add one, or disable
require_where in the policy
```

O acidente mais comum de console. Ligado por padrão.

## O que o operador vê

A página mostra a política **antes** de digitar — capacidades, tabelas,
limite de linhas — para os limites serem conhecidos em vez de descobertos por
recusa. Um console que pode escrever exibe um aviso.

Recusa e erro de banco são renderizados diferente de propósito: "você não
pode fazer isso" e "seu SQL está errado" levam a correções diferentes.

## Auditoria

```python
import logging

from src.db.models import SqlAudit

logger = logging.getLogger(__name__)


async def record_sql_attempt(entry: SqlAudit) -> None:
    """Persist every console attempt, allowed or refused."""
    logger.warning(
        "sql_console",
        extra={
            "principal": entry.principal,
            "allowed": entry.allowed,
            "capability": entry.capability,
            "tables": entry.tables,
            "reason": entry.reason,
            "sql": entry.sql,
        },
    )
```

**Toda** tentativa é auditada, inclusive as recusadas — o registro do que
alguém *tentou* rodar costuma ser mais interessante que a lista do que
funcionou.

!!! warning "Falha do auditor não derruba o console"
    Por design, para um sink quebrado não tirar a ferramenta do ar. Mas um
    console que você não consegue auditar é um console que você deveria
    desligar — logue ali dentro.

## Leitura roda em transação revertida

Com uma política somente-leitura, cada statement roda numa transação que é
revertida ao final. Um `SELECT` que acabe mutando — uma função com efeito
colateral, uma peculiaridade de dialeto — não deixa nada para trás.

## Usar fora do admin

O serviço não depende da página:

```python
import asyncio

from tempest_fastapi_sdk.admin import SqlShellDenied, SqlShellPolicy, SqlShellService

from src.api.dependencies.resources import db

policy = SqlShellPolicy(allowed_tables=["users", "orders"])


service = SqlShellService(db, policy=policy, dialect="postgres")


async def main() -> None:
    """Run this example."""
    try:
        result = await service.execute("SELECT 1", principal="job@internal")
    except SqlShellDenied as exc:
        print("refused:", exc)


asyncio.run(main())
```

## Checklist antes de ligar em produção

- [ ] O console aponta para um **role restrito**, não para o usuário da app.
- [ ] `denied_tables` cobre as tabelas de credencial, token e dado pessoal.
- [ ] `capabilities` é o mínimo que resolve o caso — comece em `{READ}`.
- [ ] O `auditor` grava em algo que você lê depois.
- [ ] O acesso ao admin já exige MFA (`[mfa]`) para quem alcança a página.
- [ ] `statement_timeout_ms` está definido, num dialeto que o respeita.

## Recapitulando

- **Desligado por padrão**; sem `sql_shell=` a rota não existe.
- **A política explica e estreita; os GRANTs impõem.** Use as duas.
- **Deny ganha de allow**, e o desconhecido cai em `ADMIN`.
- **Subquery e CTE são inspecionados**; alias de CTE não vira tabela.
- **Toda tentativa é auditada**, inclusive a recusada.

Veja também: [Painel admin](admin.md) para o resto do painel e
[Segurança](security.md) para o que cerca o acesso a ele.

# Guards de permissão (`@requires`)

Você já tem o usuário na mão — veio de uma dependência, de um parâmetro de
serviço — e quer garantir uma invariante antes de rodar o corpo da função:
"tem que estar ativo", "tem que ser o dono do pedido", "tem que ser admin".

O `@requires` faz isso com funções normais, sem framework, sem registry, sem
string mágica. 🚀

## O problema

Sem decorator, a checagem vira ruído no começo de cada função:

```python
from uuid import UUID

from tempest_fastapi_sdk import ForbiddenException, UnauthorizedException

from src.db.models import UserModel


async def delete_order(order_id: UUID, user: UserModel | None) -> None:
    if user is None:
        raise UnauthorizedException(message="Authentication required")
    if not user.is_active:
        raise ForbiddenException(message="User account is inactive")
    if order_id not in user.owned_orders:
        raise ForbiddenException(message="Not the order owner")
    ...
```

Três problemas: repetição em toda rota, mistura de autorização com regra de
negócio, e nada impede que alguém escreva `if not allowed: return None` em vez
de levantar exceção — a rota devolve 200 numa negação.

## A solução em 2 passos

### 1. Escreva o guard

Um guard é uma função comum: **recebe o usuário**, **devolve o usuário** (ou
`None`) e **nega levantando** uma `AppException`.

```python
from tempest_fastapi_sdk import ForbiddenException

from src.db.models import UserModel


def order_owner(user: UserModel) -> UserModel:
    """Assert the user owns the order under edit.

    Args:
        user (UserModel): The authenticated user.

    Returns:
        UserModel: The same user.

    Raises:
        NotOrderOwnerException: When the user does not own the order.
    """
    if not user.owns_current_order:
        raise NotOrderOwnerException()
    return user
```

!!! warning "Guard nega levantando, nunca devolvendo `False`"
    `return False` **não** nega nada — o `@requires` ignora o valor e avisa com
    `GuardContractWarning`, e o `tempest permissions` reporta
    `guard-returns-bool` como erro. O motivo é a padronização de erros: quem
    levanta uma `AppException` ganha status HTTP, `code` e envelope
    `{detail, code, details}` de graça pelos handlers do SDK.

### 2. Decore a função

```python
from uuid import UUID

from fastapi import Depends
from tempest_fastapi_sdk import error_responses, requires
from tempest_fastapi_sdk.auth import require_active

from src.api.dependencies import get_current_user
from src.api.guards import order_owner
from src.core.exceptions import NotOrderOwnerException
from src.db.models import UserModel


@router.delete(
    "/orders/{order_id}",
    responses=error_responses(NotOrderOwnerException),
)
@requires(require_active, order_owner)
async def delete_order(
    order_id: UUID,
    user: UserModel = Depends(get_current_user),
) -> None:
    """Delete an order the caller owns.

    Args:
        order_id (UUID): The order to delete.
        user (UserModel): The authenticated, active, owning user.
    """
    await controller.delete(order_id)
```

Pronto. Os guards rodam da esquerda para a direita antes do corpo; o corpo só
executa se todos passarem.

!!! tip "Ordem dos decorators"
    `@requires` vai **abaixo** do decorator de rota. O router precisa registrar
    a função já protegida.

## De onde vem o usuário

O `@requires` acha o parâmetro do usuário **pela anotação**: aquele cujo tipo é
uma subclasse de `BaseModel` / `BaseUserModel`. Você não configura nada no caso
comum.

Quando há mais de um usuário na assinatura, aponte qual:

```python
@requires(can_ban_users, user_param="target")
async def ban_user(
    actor: UserModel = Depends(get_current_user),
    target: UserModel = Depends(get_target_user),
) -> None:
    """Ban the target user.

    Args:
        actor (UserModel): The moderator performing the ban.
        target (UserModel): The user being banned.
    """
    ...
```

??? info "Detalhes técnicos — resolução da anotação"
    A ordem é: `user_param=` explícito → o único parâmetro cuja anotação
    resolve para um modelo de usuário → (só quando nenhuma anotação resolveu)
    o único parâmetro cujo nome está em `USER_PARAM_NAMES`
    (`user`, `current_user`, `actor`, `requester`, `principal`) ou cuja
    anotação em texto menciona `User`. Esse último passo existe porque
    `from __future__ import annotations` + import sob `TYPE_CHECKING` deixa a
    anotação impossível de avaliar em tempo de decoração.

    Se nada resolver — ou se dois candidatos empatarem — o import falha com
    `TempestPermissionError`. Melhor a aplicação não subir do que subir com uma
    checagem que não roda.

## O retorno do guard estreita o tipo

Um guard que devolve o usuário substitui o usuário visto pelo próximo guard **e
pelo corpo da função**. É assim que os guards do SDK
(`require_authenticated` / `require_active` / `require_admin`) transformam
`UserT | None` em `UserT`:

```python
@requires(require_active)
async def me(user: UserModel | None = Depends(get_current_user_soft)) -> UserModel:
    """Return the authenticated user.

    Args:
        user (UserModel | None): Filled by the soft dependency; guaranteed
            non-None inside the body.

    Returns:
        UserModel: The active user.
    """
    return user
```

Devolver `None` é permitido e significa "não mexi no usuário".

## Metadata: um guard genérico, vários call sites

Um guard pode declarar um **segundo parâmetro** `meta: dict[str, Any]`. É isso que transforma um guard genérico numa checagem específica por rota — em vez de escrever `manager_only`, `auditor_only`, `admin_only`, você escreve `has_role` uma vez e cada call site diz qual papel exige.

```python
from typing import Any

from tempest_fastapi_sdk import ForbiddenException, requires

from src.db.models import UserModel


def has_role(user: UserModel, meta: dict[str, Any]) -> UserModel:
    """Assert the user holds the role the route declared.

    Args:
        user (UserModel): The authenticated user.
        meta (dict[str, Any]): Metadata injected by ``@requires``.

    Returns:
        UserModel: The same user.

    Raises:
        MissingRoleException: When the declared role is missing.
    """
    if meta["role"] not in user.roles:
        raise MissingRoleException(role=meta["role"])
    return user


@router.post("/reports/close-month")
@requires(has_role, meta={"role": "manager"})
async def close_month(user: UserModel = Depends(get_current_user)) -> None:
    """Close the accounting month.

    Args:
        user (UserModel): The authenticated manager.
    """
    ...
```

Guards de **um** parâmetro continuam exatamente como antes — o segundo argumento só vai para quem o declara. Você pode misturar os dois numa mesma decoração.

### `include_args=True`: o guard vê os argumentos da chamada

`meta=` carrega literais fixados na decoração. Quando a checagem depende do **recurso** da requisição, ligue `include_args=True` e os argumentos da chamada (path params, body, outras dependências) entram no mesmo dicionário:

```python
def order_owner(user: UserModel, meta: dict[str, Any]) -> UserModel:
    """Assert the user owns the order named by the metadata.

    Args:
        user (UserModel): The authenticated user.
        meta (dict[str, Any]): Metadata injected by ``@requires``.

    Returns:
        UserModel: The same user.

    Raises:
        NotOrderOwnerException: When the user does not own the order.
    """
    if meta["order_id"] not in user.order_ids:
        raise NotOrderOwnerException()
    return user


@router.delete("/orders/{order_id}")
@requires(require_active, order_owner, include_args=True)
async def delete_order(
    order_id: UUID,
    user: UserModel = Depends(get_current_user),
) -> None:
    """Delete an order the caller owns.

    Args:
        order_id (UUID): The order to delete.
        user (UserModel): The authenticated, active, owning user.
    """
    ...
```

O guard recebe `{"order_id": UUID(...)}` sem a rota precisar repassar nada.

!!! info "Regras da mesclagem"
    - O **usuário sai** do dicionário — o guard já o recebe no primeiro parâmetro.
    - Parâmetro que o chamador omitiu contribui com o **default**, então o guard vê os valores com que o corpo vai rodar. Default que é marcador de injeção (`Depends(...)`) é **descartado**, nunca entregue como valor.
    - Chave declarada em `meta=` **ganha** de um argumento com o mesmo nome: a decoração é a declaração explícita, o argumento é dado ambiente. O `tempest permissions` avisa (`meta-key-collision`) quando isso acontece, porque o argumento nunca chega ao guard.
    - O dicionário é **novo em cada chamada** e compartilhado pelos guards daquela chamada — um guard pode gravar uma chave que o próximo lê, e nada vaza para a próxima requisição.

### Erros de configuração

| Situação | O que acontece |
| --- | --- |
| `meta=` não é um mapping | `TempestPermissionError` no import |
| `meta=` / `include_args=True` sem nenhum guard de 2 parâmetros | `TempestPermissionError` no import — a configuração não faria nada |
| guard de 2 parâmetros e decoração sem `meta=`/`include_args=` | roda com `{}`; `tempest permissions` avisa `guard-meta-missing` |
| guard com 3+ parâmetros obrigatórios | `TempestPermissionError` no import (`expected 1 (user) or 2 (user, meta)`) |

Para auditar o que uma rota declarou:

```python
from tempest_fastapi_sdk import guard_metadata

assert guard_metadata(close_month) == {"role": "manager"}
```

`guard_metadata` devolve só os literais de `meta=` — o que `include_args=True` mescla existe por chamada e não dá para ler do objeto função.

## Funciona em qualquer camada

Nada aqui depende do FastAPI. O mesmo decorator vale para controller e service,
sync ou `async`:

```python
from uuid import UUID

from tempest_fastapi_sdk import requires

from src.api.guards import order_owner
from src.db.models import UserModel


class OrderService:
    """Business logic for orders."""

    @requires(order_owner)
    async def delete(self, order_id: UUID, user: UserModel) -> None:
        """Delete an order the caller owns.

        Args:
            order_id (UUID): The order to delete.
            user (UserModel): The owning user.
        """
        await self.repository.delete(order_id)
```

Guards `async` só podem decorar funções `async` — o contrário levantaria uma
corrotina nunca aguardada, então o import falha com `TempestPermissionError`.

## O linter pega o erro por você

Duas camadas, porque cada uma vê o que a outra não vê.

### Em tempo de import — `TempestPermissionError`

O decorator valida na hora em que o módulo é importado. A aplicação **não sobe**
com:

| Situação | Mensagem |
| --- | --- |
| `@requires()` sem guard | `needs at least one guard` |
| guard não-callable | `is not callable` |
| guard com 2 parâmetros obrigatórios | `takes 2 required params, expected 1 (user)` |
| guard `async` em função sync | `is async but ... is not` |
| nenhum parâmetro de usuário | `no parameter annotated with a user model` |
| dois parâmetros de usuário | `several parameters are user models` |
| `meta=`/`include_args=` sem consumidor | `no guard declares a second parameter` |

### Em tempo de chamada — `GuardContractWarning`

O que só aparece rodando: guard que levanta `ValueError` (a API responderia 500
sem `code`) e guard que devolve `False` (a negação seria ignorada). O
`@requires` **avisa** e deixa a exceção original propagar — ele não muda o
resultado de uma chamada que só está observando.

!!! tip "Trate como erro nos testes"
    Rode a suíte com `-W error::tempest_fastapi_sdk.authz.GuardContractWarning`
    (ou `filterwarnings = ["error"]` no `pyproject.toml`) e um guard fora do
    contrato quebra o teste em vez de virar linha de log.

### Em CI — `tempest permissions`

O que nenhuma das duas alcança: guard cujo `raise` está atrás de um `if` que
nenhum teste exercita, ou guard nunca importado. O comando lê o contrato do
código-fonte com `ast`, sem importar a aplicação:

```bash
tempest permissions                    # relatório informativo (exit 0)
tempest permissions --check            # exit 1 se houver erro (gate de CI)
tempest permissions --check --strict   # falha também nos warnings
tempest permissions --path src --path libs
```

```text
src/api/routers/orders.py:41  delete_order
  error: guard-foreign-exception: guard 'order_owner' raises ValueError, which is
    not an AppException subclass; the API layer answers it as HTTP 500 without an
    error code
  warning: guard-missing-annotation: guard 'order_owner' has no return annotation
2 finding(s), 1 error(s).
```

Códigos reportados:

| Código | Severidade | O que é |
| --- | --- | --- |
| `no-guards` | erro | `@requires()` sem guard: tudo passa |
| `user-param-missing` | erro | nenhum parâmetro é modelo de usuário |
| `user-param-ambiguous` | erro | vários candidatos, sem `user_param=` |
| `guard-arity` | erro | guard não recebe 1 parâmetro (user) nem 2 (user, meta) |
| `meta-unused` | erro | `meta=`/`include_args=` sem nenhum guard que receba |
| `guard-async-in-sync` | erro | guard `async` em função sync |
| `guard-returns-bool` | erro | guard-predicado: o `False` é ignorado |
| `guard-foreign-exception` | erro | levanta fora da hierarquia `AppException` |
| `guard-never-denies` | warning | nada no grafo de chamadas levanta |
| `guard-missing-annotation` | warning | parâmetro ou retorno sem anotação |
| `guard-return-type` | warning | retorno não é o usuário, `None` ou a união |
| `guard-meta-missing` | warning | guard pede metadata e a decoração não passa nenhuma |
| `guard-meta-annotation` | warning | 2º parâmetro anotado como algo que não recebe `dict[str, Any]` |
| `meta-key-collision` | warning | chave de `meta=` encobre um parâmetro sob `include_args=True` |
| `guard-unresolved` | warning | guard é lambda, está fora do escopo ou o nome bate com várias definições |

!!! note "Reporta em vez de adivinhar"
    Um guard cujo nome existe em dois módulos vira `guard-unresolved`, não uma
    checagem contra a definição errada. Mesma política do `openapi-errors`:
    super-reportar é aceitável, dar um veredito confiante e errado não é.

## Integração com os erros do OpenAPI

Um guard nega levantando, então a exceção dele é tão alcançável quanto a de
qualquer função que o corpo chama. O `tempest openapi-errors` lê os guards do
`@requires` ao montar o conjunto alcançável:

```bash
tempest openapi-errors --check
```

```text
src/api/routers/orders.py:41  DELETE /orders/{order_id}
  undocumented: NotOrderOwnerException
```

`--fix` escreve `responses=error_responses(NotOrderOwnerException)` na rota,
igual a qualquer outra exceção do fluxo. Detalhes na receita
[Erros no OpenAPI »](openapi-errors.md).

## Auditar os guards de uma rota

```python
from tempest_fastapi_sdk import declared_guards, guarded_user_param

assert declared_guards(delete_order) == (require_active, order_owner)
assert guarded_user_param(delete_order) == "user"
```

Útil num teste que garante que toda rota de escrita tem pelo menos um guard.

## `@requires` vs. as outras ferramentas de autorização

| Ferramenta | Pergunta que responde | Onde vive |
| --- | --- | --- |
| `make_permission_dependency` | "o token carrega `orders:write`?" | dependência de rota, antes do handler |
| `has_perm` / `make_permission_checker` | "esse usuário pode nesse objeto?" | registry de regras `(user, obj) -> bool` |
| `@requires` | "esse usuário passa por essas invariantes?" | qualquer função que já recebe o usuário |

As três se compõem: um guard pode chamar `check_permission(user, "order.delete",
obj=order)` e ganhar o registry inteiro dentro do `@requires`.

## Recap

- **Guard** = `(user) -> user | None` — ou `(user, meta) -> user | None` — que nega levantando `AppException`.
- `@requires(g1, g2)` roda os guards na ordem, abaixo do decorator de rota.
- O parâmetro do usuário sai da anotação; `user_param=` desempata.
- `meta={...}` parametriza um guard genérico; `include_args=True` entrega os argumentos da chamada ao guard.
- Retorno não-`None` substitui o usuário — é assim que o tipo estreita.
- Funciona em router, controller e service, sync ou `async`.
- Erro de uso: `TempestPermissionError` no import, `GuardContractWarning` na
  chamada, `tempest permissions --check` na CI.
- As exceções dos guards entram no `error_responses(...)` via
  `tempest openapi-errors`.

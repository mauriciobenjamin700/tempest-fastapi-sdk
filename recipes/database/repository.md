# 3. O repository

Use para o CRUD async com `BaseRepository`: modo direto ou subclasse, erro de constraint, eager-loading, signals e expressões `F`/`Q`.

Parte da receita [Banco de dados](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database.md).

`BaseRepository[Model]` é o coração da camada. Ele encapsula o CRUD async,
os filtros, as operações em lote e a paginação. Há dois jeitos de usá-lo.

## Modo direto — CRUD puro

Quando você não tem query custom, instancie direto:

```python
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


repository = BaseRepository(session, model=UserModel)


async def main() -> None:
    """Run this example."""
    user = await repository.get_by_id(user_id)


asyncio.run(main())
```

## Modo subclasse — quando há queries próprias

Subclassifique para adicionar consultas do domínio e os mappers que
traduzem ORM ↔ DTO. São três: `map_to_model` (padrão `self.model(**data)`),
`map_to_response` e `map_to_schema` — os dois últimos levantam
`NotImplementedError` até você sobrescrever, então implemente os que o seu
código chama (o exemplo implementa `map_to_response` e `map_to_model`).
**O construtor é o contrato** — você repassa `model`
para `super().__init__`, não há atributos de classe mágicos:

```python
# src/db/repositories/user.py
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel
from src.schemas import UserResponse


class UserRepository(BaseRepository[UserModel]):
    """Data access for the user domain."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session and the user model.

        Args:
            session (AsyncSession): The async database session.
        """
        super().__init__(
            session,
            model=UserModel,
            not_found_message="Usuário não encontrado",
            create_conflict_message="E-mail já cadastrado",
        )

    def map_to_response(self, instance: UserModel) -> UserResponse:
        """Map an ORM row to its API response schema.

        Args:
            instance (UserModel): The persisted user row.

        Returns:
            UserResponse: The serializable response DTO.
        """
        return UserResponse.model_validate(instance)

    def map_to_model(self, data: dict[str, Any]) -> UserModel:
        """Build an ORM instance from a plain payload.

        Args:
            data (dict[str, Any]): Column-value pairs.

        Returns:
            UserModel: The unpersisted instance.
        """
        return UserModel(**data)
```

!!! tip "Mensagens de erro por repository"
    Os kwargs `not_found_message`, `create_conflict_message`,
    `update_conflict_message`, `bulk_create_conflict_message` e
    `bulk_update_conflict_message` customizam o texto das exceções. Sem
    eles, o SDK gera mensagens a partir de `Model.__name__` — o nome da
    classe inteiro, sufixo incluído: `"UserModel not found"`,
    `"Conflict creating UserModel"`.

!!! tip "Classes de exceção por repository"
    Cada `*_message` tem um `*_exception` correspondente. A mensagem sozinha
    não dá para o cliente ramificar: o `ConflictException` padrão responde
    `code = "CONFLICT"`, então uma chave duplicada de coin pack fica
    indistinguível de qualquer outro 409 — e `error_responses()` não consegue
    documentá-la. Passando uma subclasse de domínio (que **declara seu próprio
    `code` no corpo da classe**), o 409 fica identificável sem o repository
    saber nada do domínio:

    ```python
    from tempest_fastapi_sdk import BaseRepository, ConflictException

    from src.core.exceptions import CoinPackNotFoundException
    from src.db.models import CoinPackModel


    class CoinPackAlreadyExistsException(ConflictException):
        """Raised when a coin pack name is already taken."""

        code: str = "COIN_PACK_ALREADY_EXISTS"


    class CoinPackRepository(BaseRepository[CoinPackModel]):
        """Data access for coin packs."""

        def __init__(self, session: AsyncSession) -> None:
            """Initialize the repository.

            Args:
                session (AsyncSession): The async database session.
            """
            super().__init__(
                session,
                model=CoinPackModel,
                not_found_exception=CoinPackNotFoundException,
                create_conflict_exception=CoinPackAlreadyExistsException,
            )
    ```

    A resolução é do mais específico para o mais genérico —
    `create_conflict_exception` se informado, senão `conflict_exception`, senão
    `ConflictException` — então um kwarg só (`conflict_exception=`) cobre todas
    as escritas, ou cada escrita usa a sua:

    | Kwarg | Cobre |
    | --- | --- |
    | `create_conflict_exception` | `add`, `save_with_outbox`, `add_audited` |
    | `update_conflict_exception` | `update`, `update_audited` |
    | `bulk_create_conflict_exception` | `add_all`, `bulk_create_values`, `bulk_upsert` |
    | `bulk_update_conflict_exception` | `update_many`, `bulk_update`, `update_returning` |
    | `conflict_exception` | fallback de todas as quatro |

    A classe é instanciada como `cls(message=...)`, o mesmo contrato que
    `not_found_exception` já tem — então ela precisa aceitar o keyword
    `message`. Declarar `code` no corpo da classe e receber `message` opcional
    atende aos dois. Todos os kwargs são opcionais: omitindo-os, o
    comportamento é o de antes (`ConflictException` genérico). Disponível a
    partir de 0.169.0.

## Qual constraint recusou — `parse_integrity_error`

Trocar `ConflictException` por uma classe sua responde *houve conflito*. Para
responder *em qual campo*, é preciso ler o que o servidor disse — e o que ele
diz tem a forma do driver, não da sua aplicação.

```python
from typing import Any

from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk import parse_integrity_error


def to_response(error: IntegrityError) -> dict[str, Any]:
    """Traduz um erro de integridade no envelope que o cliente parseia."""
    failure = parse_integrity_error(error)
    return {
        "code": failure.kind.value,
        "field": failure.column,
        "constraint": failure.constraint,
    }
```

`IntegrityViolationEnum` classifica em `unique`, `foreign_key`, `not_null`,
`check` e `unknown`. `IntegrityFailure` carrega o que o servidor informou:
`constraint`, `table`, `columns` (na ordem listada) e `column`, que é o atalho
para o caso de uma coluna só.

!!! warning "Campo vazio significa 'este servidor não disse'"
    Nunca 'não existe'. Os dois dialetos falam de jeitos diferentes, e as
    diferenças não são cosméticas:

    | | Postgres | SQLite |
    | --- | --- | --- |
    | unique | nomeia constraint, tabela e colunas | nomeia `tabela.coluna`; sem constraint |
    | not null | nomeia coluna e tabela | nomeia `tabela.coluna` |
    | check | nomeia a constraint e a tabela | devolve a **expressão** quando a constraint não tem nome |
    | foreign key | nomeia constraint, tabela e coluna | diz só `FOREIGN KEY constraint failed` |

    Uma ausência medida que vale repetir: FK no SQLite não traz nada além do
    tipo.

!!! info "No Postgres, os campos do driver vêm antes do texto"
    O `asyncpg` expõe o diagnóstico do servidor como atributos da exceção —
    `sqlstate`, `detail`, `constraint_name`, `table_name`, `column_name` —, e
    o `parse_integrity_error` lê esses primeiro, caindo para a frase só no que
    eles deixam vazio. O texto não é um portador estável. Medido contra
    Postgres 16 com `asyncpg` 0.31.0:

    | SQLAlchemy | `str(error.orig)` | onde fica a exceção do driver |
    | --- | --- | --- |
    | 2.0.52 (o piso do SDK) | primeira frase **mais** a linha `DETAIL:` | `error.orig.__cause__` |
    | 2.1.1 | só a primeira frase | `error.orig.orig` (e `__cause__`) |

    Lendo só o texto, uma unique no 2.1.1 voltava com `columns=()`. Os campos
    são lidos pelo nome do atributo: o `asyncpg` não é importado, e uma
    instalação sem `[postgres]` não precisa dele. A `table` de uma unique vem
    do `table_name`; uma mensagem solta, sem a exceção do driver atrás, ainda
    deixa `None`, porque a frase não diz e dividir o nome da constraint numa
    convenção seria chute. O `sqlstate` (`23505`, `23503`, `23502`, `23514`)
    também classifica uma violação cuja frase o servidor traduziu: com
    `lc_messages=de_DE.utf8`, nas duas versões do SQLAlchemy, uma unique
    voltou `unique` com constraint e tabela, e com `columns=()`, porque o
    `DETAIL` também vem traduzido.

!!! info "Lê `error.orig`, não `str(error)`"
    `str()` numa `IntegrityError` do SQLAlchemy anexa `[SQL: <statement>]`, e
    um statement contém dado do usuário. Um valor que contivesse o texto
    `UNIQUE constraint failed: x.y` seria parseado como nome de coluna. A
    função lê a exceção do DBAPI, cuja mensagem é só a frase do servidor.

!!! check "Nunca levanta"
    Mensagem desconhecida volta como `IntegrityViolationEnum.UNKNOWN` com o texto
    em `failure.message`. Um parser de prosa de erro que levanta transforma um
    409 tratado num 500 não tratado, que é pior do que o conflito genérico que
    ele veio melhorar.

## O CRUD que você ganha

Lembrando da convenção de coleções do projeto: lookups de **registro
único** levantam 404; lookups de **coleção** devolvem `[]`.

```python
import asyncio
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

id1, id2, id3 = uuid4(), uuid4(), uuid4()
repository = BaseRepository(session, model=UserModel)
user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")
user_or_id = user_id


async def main() -> None:
    """Run this example."""
    # Leitura — registro único (404 quando não acha)
    user = await repository.get_by_id(user_id)
    user = await repository.get({"email": "a@b.com"})

    # Leitura — pode não existir (None, sem 404)
    user = await repository.get_or_none({"email": "a@b.com"})
    first = await repository.first({"is_active": True})

    # Leitura — coleção (sempre [], nunca 404)
    users = await repository.list({"is_active": True})

    # Existência / contagem
    exists = await repository.exists({"email": "a@b.com"})
    total = await repository.count({"is_active": True})

    # "Esse valor já é de OUTRO registro?" — validação de unicidade no update
    taken = await repository.exists_excluding({"email": "a@b.com"}, exclude_id=user.id)

    # id-ou-instância → instância (sem if isinstance espalhado nas services)
    user = await repository.resolve(user_or_id)

    # Escrita
    created = await repository.add(
        UserModel(name="Ana", email="ana@x.com", password_hash="...")
    )
    updated = await repository.update(user)  # commita mutações numa instância anexada

    # Remoção
    await repository.delete(user_id)  # hard delete (404 se não existe)
    await repository.delete_many({"is_active": False})  # retorna contagem
    await repository.delete_batch([id1, id2, id3])  # por PK, retorna contagem

    # Soft-delete via flag is_active (não precisa do SoftDeleteMixin)
    await repository.soft_delete(user_id)  # is_active = False
    await repository.restore(user_id)  # is_active = True


asyncio.run(main())
```

!!! note "`update` espera uma instância anexada"
    O fluxo típico é: `get_by_id` → mutar com `update_from_dict` →
    `repository.update(instance)`. Não construa um modelo solto e mande
    para o `update` — ele persiste mutações de algo já carregado na
    sessão.

!!! tip "`resolve` e `exists_excluding` — dois ajudantes que você vai usar sempre"
    **`resolve(id_ou_instância)`** resolve o velho dilema: seu método
    recebe `UUID | UserModel` e você não quer escrever
    `if isinstance(x, UUID): ... else: ...` em toda service. O
    `resolve` faz isso por você — passa um `UUID`, ele busca (404 se não
    existir); passa uma instância, ele devolve a mesma. Uma linha:

    ```python
    user_model = await self.repository.resolve(user)  # user é UUID OU UserModel
    ```

    **`exists_excluding(filtros, exclude_id=...)`** responde a pergunta
    "esse e-mail/telefone/username já é de **outra** pessoa?" — exatamente
    o que você precisa ao **atualizar** um campo único. O `exists` normal
    diria `True` até para o próprio registro; o `exists_excluding` ignora
    o id que você passar:

    ```python
    if await self.repository.exists_excluding(
        {"phone": new_phone}, exclude_id=user.id
    ):
        raise UserWithPhoneExistsException(phone=new_phone)
    ```

    Passe `exclude_id=None` no cadastro (quando ainda não há registro a
    excluir) — aí ele se comporta igual ao `exists`.

**Recap:** instancie direto para CRUD puro, subclassifique para queries +
mappers. 404 só em lookup único; coleção devolve `[]`. `soft_delete`
mexe na flag `is_active`; o `SoftDeleteMixin` (seção 6) adiciona um
carimbo `deleted_at` quando você precisa de auditoria temporal.

## Eager-loading de relacionamentos com `with_`

Acessar um relacionamento (`user.orders`) **depois** que a sessão async
fechou levanta o temido `MissingGreenlet` — o SQLAlchemy tentaria uma
query lazy num contexto que não pode mais aguardar I/O. A solução é
carregar o relacionamento junto, na mesma query. Todo método de leitura
(`get`, `get_or_none`, `get_by_id`, `first`, `list`) aceita `with_=`:

```python
import asyncio
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel


async def main() -> None:
    """Run this example."""
    user_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # Carrega o user e seus pedidos numa só ida ao banco
        user = await repository.get_by_id(user_id, with_=["orders"])
        for order in user.orders:      # sem lazy load, sem MissingGreenlet
            print(order.total)

        # Vários relacionamentos + aninhado (pontilhado)
        user = await repository.get_by_id(
            user_id,
            with_=["profile", "orders.items"],   # orders → e os items de cada order
        )

        # Também funciona em coleções
        users = await repository.list({"is_active": True}, with_=["orders"])


asyncio.run(main())
```

Cada caminho usa `selectinload`: N relacionados custam **uma** query
extra por nível (um `SELECT ... IN (...)`), não N — nada de row
multiplication de `JOIN`, e funciona tanto para coleções quanto para
escalares.

!!! warning "Nome errado falha alto"
    Um segmento de `with_` que não seja um relacionamento do modelo
    naquele ponto levanta `ValueError` na hora — não um erro silencioso
    em runtime. `with_=["orders.ghost"]` → `ValueError: Order has no
    relationship 'ghost'`.

## Signals de ciclo de vida

Quando você quer reagir a uma escrita — invalidar cache, enfileirar um
evento, sincronizar um índice de busca, disparar um domain event — sem
espalhar callbacks por toda service, registre um **signal**. O
repository emite quatro momentos no caminho unit-of-work:

```python
from tempest_fastapi_sdk import RepositorySignalEnum, on_signal
from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.db import connect, disconnect

from src.core.settings import settings
from src.db.models import UserModel
from src.services.search import SearchIndex

cache = AsyncRedisManager(settings.REDIS_URL, decode_responses=True)
search_index = SearchIndex()


# Forma decorator
@on_signal(UserModel, RepositorySignalEnum.POST_SAVE)
async def index_user(user: UserModel) -> None:
    """Reindexa o user na busca depois que a linha commitou."""
    await search_index.upsert(user.id, user.name)


# Forma imperativa (mesma coisa)
async def bust_cache(user: UserModel) -> None:
    """Derruba a entrada de cache do user depois que a linha commitou."""
    await cache.client.delete(f"user:{user.id}")

connect(UserModel, RepositorySignalEnum.POST_SAVE, bust_cache)
disconnect(UserModel, RepositorySignalEnum.POST_SAVE, bust_cache)  # remove
```

!!! note "`search_index` é ilustrativo; o `cache` não"
    `search_index.upsert(...)` é um placeholder do seu projeto (um cliente
    de busca) — não faz parte do SDK. Troque pelo objeto real do seu domínio.

    O `cache` é um `AsyncRedisManager` de verdade, e é por isso que a chamada
    passa por `cache.client`: o manager cuida do ciclo de vida, e os comandos
    Redis vivem no client. Construindo o handler antes do lifespan, use
    `cache.client_proxy` — `cache.client` levanta `RuntimeError` até o
    `connect()` rodar.

Os quatro momentos:

| Signal | Quando dispara | Uso típico |
|--------|----------------|------------|
| `PRE_SAVE` | antes do `INSERT`/`UPDATE` commitar | validação transversal; **levantar aqui veta a escrita** (rollback + re-raise) |
| `POST_SAVE` | depois de commitar + refresh | reindex, cache-bust, evento de domínio |
| `PRE_DELETE` | antes do delete de linha única | limpar dependências externas |
| `POST_DELETE` | depois de o delete commitar | notificar que o registro sumiu |

Handlers podem ser sync **ou** `async` — um retorno awaitable é
aguardado. Registrar num modelo base vale para as subclasses (resolvido
pela MRO da instância).

!!! danger "Signals cobrem só o caminho unit-of-work"
    `add` / `add_all` / `update` / `update_many` / `soft_delete` /
    `restore` / `delete` disparam signals. Os métodos bulk set-based
    (`bulk_update`, `bulk_create_values`, `bulk_upsert`, `delete_many`,
    `delete_batch`) emitem **uma** instrução SQL e **fazem bypass** dos
    signals por design — eles nunca materializam as linhas afetadas.
    `soft_delete`/`restore` disparam `PRE_SAVE`/`POST_SAVE` (são um
    `UPDATE`), não os signals de delete.

!!! tip "Isolamento em testes"
    O registro é global ao processo. Em testes, chame
    `clear_signals()` (de `tempest_fastapi_sdk.db.signals`) no teardown
    de uma fixture para um handler de um teste não vazar para o
    seguinte.

## Expressões `F` e `Q`

Para quem vem do Django: `F` referencia uma coluna dentro da query e `Q`
compõe condições com `OR`/`NOT`. Os dois plugam direto no repository.

**`F` — atualização atômica no banco.** Decrementar estoque com
read-modify-write tem race: dois requests leem `10`, ambos gravam `9`.
`F("stock") - 1` calcula no banco, numa instrução — sem lost update:

```python
import asyncio
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel

from tempest_fastapi_sdk import F


async def main() -> None:
    """Run this example."""
    product_id = UUID("6f1c3d84-2a55-4d0b-9d7e-0c1a2b3c4d5e")
    pid = product_id
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # stock = stock - 1, no banco
        await repository.bulk_update({"id": product_id}, {"stock": F("stock") - 1})

        # aritmética dos dois lados e entre colunas
        await repository.bulk_update({"id": pid}, {"stock": 100 - F("stock")})
        await repository.bulk_update({"id": pid}, {"total": F("price") * F("qty")})


asyncio.run(main())
```

**`update_returning` — a decisão e o valor novo na mesma instrução.**
`bulk_update` devolve só a contagem. Quando a escrita é condicional e você
precisa saber o resultado — debitar só se houver saldo, e com o saldo novo
em mãos —, `update_returning` roda o mesmo `UPDATE ... WHERE` com
`RETURNING`. Linha nenhuma devolvida significa que a condição não casou;
não existe leitura antes, então não existe janela para outra requisição:

```python
import asyncio
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository, F

from db_setup import db
from src.db.models import UserModel


async def main() -> None:
    """Run this example."""
    user_id = UUID("6f1c3d84-2a55-4d0b-9d7e-0c1a2b3c4d5e")
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        rows = await repository.update_returning(
            {"id": user_id, "wallet_cents__gte": 500},
            {"wallet_cents": F("wallet_cents") - 500},
            returning=("wallet_cents",),
        )
        if rows:
            print("novo saldo:", rows[0]["wallet_cents"])
        else:
            print("recusado: saldo abaixo de 500")


asyncio.run(main())
```

O `where=` aceita um `Q` ou uma cláusula SQLAlchemy pronta, para o que o
dict não expressa — a [carteira](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/wallet.md) desconta o saldo retido com
uma subquery correlata ali. `RETURNING` exige PostgreSQL, ou SQLite 3.35+.

**`Q` — `OR` / `NOT` que o dict de filtros não expressa.** O dict ANDeia
tudo; `Q` combina com `&` / `|` / `~` e entra via `where=`:

```python
import asyncio

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel

from tempest_fastapi_sdk import Q


async def main() -> None:
    """Run this example."""
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # status open OU pending
        abertos = await repository.list(where=Q(status="open") | Q(status="pending"))

        # ativos que NÃO são guest
        ativos = await repository.list(where=Q(is_active=True) & ~Q(role="guest"))

        # combina com o dict (AND): estoque >= 5 E (open OU closed)
        rows = await repository.list(
            {"stock__gte": 5}, where=Q(status="open") | Q(status="closed")
        )


asyncio.run(main())
```

!!! warning "`~Q(...)` não devolve a linha com `NULL`"
    A negação vira `role != 'guest'` em SQL, e `NULL != 'guest'` é
    `NULL`, não verdadeiro: a linha com `role` nulo **fica de fora** junto
    com a `guest`. Com três usuários (`guest`, `admin` e `role=None`),
    `~Q(role="guest")` devolve só o `admin`. Se a linha nula deve entrar,
    peça explicitamente: `~Q(role="guest") | Q(role=None)` devolve `admin` e
    o nulo (`None` numa coluna simples é `IS NULL`). O `role__ne="guest"`
    tem a mesma semântica do `~Q` aqui — também exclui o nulo.

`Q` usa as mesmas convenções do dict de filtros (`name` ILIKE,
`campo__gte`, iterável → `IN`, …), então `Q(priority__gte=5, name="ana")` é
o `AND` dessas condições. `where=` funciona em `list` / `first` / `get` /
`get_or_none` / `count` / `exists` / `paginate` / `delete_many`.

Operadores de sufixo `campo__op` disponíveis (no `Q` **e** no dict):

| Sufixo | SQL | Exemplo |
|--------|-----|---------|
| `gt` `gte` `lt` `lte` `ne` | comparação | `Q(priority__gte=5)` |
| `in` `notin` `not_in` | `IN` / `NOT IN` (valor = qualquer iterável não-string: `list`/`set`/`tuple`/gerador, ou um escalar, embrulhado numa lista de um item; `not_in` é alias de `notin`) | `Q(status__in={"open", "paid"})` |
| `between` | `col BETWEEN lo AND hi` (valor = par ordenado `(lo, hi)` em `list`/`tuple`; qualquer outro formato é recusado) | `Q(price__between=(10, 20))` |
| `iexact` | igualdade case-insensitive (`lower(col) == lower(v)`) | `Q(email__iexact="Ana@X.com")` |
| `like` `ilike` | `LIKE` / `ILIKE` cru, com os wildcards do chamador (`%`/`_`) **sem escape** | `Q(sku__ilike="ab_-%")` |
| `isnull` | `IS NULL` (True) / `IS NOT NULL` (False) | `Q(closed_at__isnull=True)` |
| `contains` `icontains` | `ILIKE %v%` (valor escapado) | `Q(name__contains="ana")` |
| `startswith` `endswith` | `ILIKE v%` / `%v` (valor escapado) | `Q(sku__startswith="SKU-")` |

!!! warning "Case-sensitivity do `like` depende do backend"
    `ilike` é sempre case-insensitive. Já o `like` puro segue a semântica de
    `LIKE` do banco: o SQLite ignora maiúsculas/minúsculas para ASCII, o
    PostgreSQL não. Para comparação de caixa **portável**, use `ilike` ou
    `iexact`.

!!! note "SQLAlchemy puro continua ali"
    `F`/`Q` são açúcar tipado sobre expressões que o SQLAlchemy já tem.
    Precisou de algo que eles não cobrem? Use `select(...)` direto — o
    repository não fica no caminho.

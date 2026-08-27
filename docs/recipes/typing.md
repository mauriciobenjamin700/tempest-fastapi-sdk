# Forçar tipagem (estático + runtime)

Type hints ajudam no editor e no mypy, mas são **apagados em runtime** —
nada impede um chamador de passar um `str` onde você anotou `int` depois
que o código sobe. Esta receita cobre as duas formas de fechar essa
brecha:

- **(A) Forçar que a anotação exista** — disciplina de quem escreve,
  resolvida pelos linters (custo zero em runtime).
- **(B) Garantir que o valor em runtime bate com a anotação** — validação
  de verdade, com custo por chamada.

!!! tip "Regra de ouro"
    `Any` é uma anotação **válida** — o errado é **não anotar**. Toda
    estratégia aqui exige que as coisas *estejam* anotadas; nenhuma
    proíbe `Any`.

## (A) Forçar anotação com os linters

O SDK já liga a regra `ANN` do ruff (força anotação) e o mypy estrito.
Em qualquer projeto gerado pelo `tempest new`, isso vem configurado no
`pyproject.toml`:

```toml
[tool.ruff.lint]
# ANN força anotar tudo. ANN401 (proibir Any) fica DESLIGADO de propósito.
select = ["E", "W", "F", "I", "B", "C4", "UP", "N", "SIM", "RUF", "ANN"]
ignore = ["B008", "B006", "ANN401", "ANN002", "ANN003"]
```

Aí é só rodar os gates da CLI:

```bash
tempest lint     # ruff check (inclui ANN)
tempest type     # mypy
tempest check    # tudo: lint + fmt-check + type + test
```

Uma função sem anotação passa a falhar o gate:

```python
def soma(a, b):        # falta tipo em a, b e no retorno
    return a + b
# ruff: ANN001 Missing type annotation for function argument `a`
#       ANN201 Missing return type annotation for public function `soma`
```

## O plugin do pydantic só checa construtor com `init_typed`

`plugins = ["pydantic.mypy"]` sozinho **não checa argumento nenhum** de
construtor de modelo. O plugin roda — é ele que sintetiza um parâmetro
keyword-only por campo — mas `init_typed` nasce `False`, então cada um
desses parâmetros sai anotado `Any`:

```python
from typing import reveal_type

from tempest_fastapi_sdk.schemas import BaseSchema

# docs-guard: skip — a chamada recusada abaixo é o assunto da seção


class Probe(BaseSchema):
    """A schema with two typed fields."""

    name: str
    age: int


reveal_type(Probe.__init__)
Probe(name="x", age="doze")
```

Com o plugin declarado e mais nada (mypy 2.3.0, pydantic 2.13.4):

```text
note: Revealed type is "def (__pydantic_self__: Probe, *, name: Any, age: Any, **kwargs: Any)"
Success: no issues found in 1 source file
```

Ligar é um bloco no `pyproject.toml`:

```toml
[tool.mypy]
plugins = ["pydantic.mypy"]

[tool.pydantic-mypy]
init_typed = true
warn_required_dynamic_aliases = true
```

O mesmo arquivo, depois:

```text
note: Revealed type is "def (__pydantic_self__: Probe, *, name: str, age: int, **kwargs: Any)"
error: Argument "age" to "Probe" has incompatible type "str"; expected "int"  [arg-type]
```

Pylance e pyright não carregam plugin nenhum — leem a anotação direto e
sempre marcaram essas chamadas. Sem o setting, o editor e a CI discordam, e
quem está errado é a CI.

!!! check "Projeto novo já nasce com isso"
    O `tempest new` escreve o bloco desde a v0.241.0, e o próprio SDK passou
    a usá-lo: ligar não custou correção nenhuma nos 409 arquivos do pacote —
    é uma classe de erro que nunca foi reportada, não um backlog.

!!! warning "Serviço scaffoldado antes da v0.241.0"
    Cole o bloco no `pyproject.toml` do serviço à mão. O `tempest check` não
    tem como ligar por você: mypy lê config de plugin **só** do arquivo de
    config, e não expõe flag de linha de comando equivalente.

!!! note "O que o `init_typed` passa a recusar"
    Entrada que o pydantic **coagiria** em runtime. Um campo `Decimal`
    recebendo `"1.5"` vira `error: Argument "amount" ... incompatible type
    "str"; expected "Decimal"` no mypy, enquanto em runtime o valor continua
    construindo como `Decimal('1.5')`. Em serviço isso é o ponto — a
    anotação vira o contrato, e quem quer coagir escreve
    `Decimal("1.5")` no call site. Em biblioteca de construtor público,
    decida caso a caso.

## Configurar o rigor da tipagem (`[tool.tempest]`)

Quão rigorosos os gates são é um knob no `pyproject.toml`. Um único
campo controla as regras ANN do ruff **e** as flags do mypy que o
`tempest lint`/`fix`/`type`/`check` aplicam:

```toml
[tool.tempest]
typing_strictness = "standard"   # lenient | standard | strict
```

| Nível        | ruff (ANN)                          | mypy                                            |
| ------------ | ----------------------------------- | ----------------------------------------------- |
| `lenient`    | nada a mais                         | nada a mais                                     |
| `standard`   | exige anotações (ANN001/201/...)    | `--disallow-untyped-defs` `--disallow-incomplete-defs` |
| `strict`     | conjunto ANN completo               | `--strict`                                       |

As flags são **somadas** ao que já está em `[tool.ruff]` / `[tool.mypy]`
— nunca relaxam a config do projeto. `ANN401` (que pega `Any`) **nunca**
é ligado, em nível nenhum.

Dá pra sobrescrever por execução, sem editar o arquivo:

```bash
tempest check --strictness strict     # só nesta rodada
tempest lint -s lenient
```

!!! note "Sem `[tool.tempest]`?"
    Quando o campo não existe (ou não há `pyproject.toml`), o nível é
    `standard`. Projetos do `tempest new` já nascem com ele setado.

## (B) Garantir o valor em runtime

Para os pontos onde o dado vem de fora (mensagem de fila, resposta de
API externa, input de CLI, dado montado dinamicamente), as anotações não
bastam — você quer validar de verdade. O SDK expõe três decorators sobre
o `pydantic.validate_call` (que já é dependência, então não há nada novo
para instalar):

### `strict_types` — sem coerção

Rejeita qualquer valor que não seja **já** do tipo anotado. Argumentos
**e** retorno são validados.

```python
from tempest_fastapi_sdk import strict_types

# docs-guard: skip — a chamada recusada abaixo é o assunto da seção


@strict_types
def add(a: int, b: int) -> int:
    return a + b


add(1, 2)            # 3
add("1", 2)          # pydantic.ValidationError — "1" NÃO vira 1
```

### `typed` — com coerção segura

Igual, mas coage quando o pydantic consegue sem ambiguidade
(`"1"` -> `1`). Útil para input "stringly-typed".

```python
from tempest_fastapi_sdk import typed

# docs-guard: skip — a chamada recusada abaixo é o assunto da seção


@typed
def add(a: int, b: int) -> int:
    return a + b


add("1", 2)          # 3  (coagido)
add("abc", 2)        # pydantic.ValidationError — não dá pra coagir
```

### `require_annotations` — falha no import se faltar anotação

Não valida valores — garante que a função **está** anotada, falhando já
na importação (não depende de rodar o linter). `self`/`cls` e
`*args`/`**kwargs` são isentos; `Any` conta como anotação presente.

```python
from typing import Any

from tempest_fastapi_sdk import require_annotations


@require_annotations
def ok(value: Any) -> None:        # Any é válido
    return None


@require_annotations
def bad(a) -> int:                 # TypeError no import:
    return a                       # "bad: missing type annotation for parameter 'a'"
```

!!! warning "Onde usar os decorators de runtime"
    Eles têm **custo por chamada**. Use nas **bordas** (fila, API
    externa, CLI), não em todo método interno. Num serviço FastAPI o
    corpo da requisição já é validado pelo schema pydantic no router —
    revalidar internamente é overhead redundante.

## Recap

- `Any` é anotação válida; o errado é não anotar.
- **(A)** linters forçam que a anotação exista — `ANN` no ruff + mypy,
  rodados via `tempest lint`/`type`/`check`. Custo zero em runtime.
- O rigor é um knob: `[tool.tempest] typing_strictness` (`lenient` /
  `standard` / `strict`), com override `--strictness` por execução.
  `ANN401` nunca liga.
- **(B)** para garantir o valor em runtime nas bordas: `strict_types`
  (sem coerção), `typed` (coage), `require_annotations` (exige anotação
  no import). Todos sobre `pydantic.validate_call`.

## Enums base


`BaseStrEnum` / `BaseIntEnum` estendem o `Enum` da stdlib com helpers ajustados para o round-trip Pydantic + SQLAlchemy (lookup por valor, herança serializável `str` / `int` em JSON, `__contains__` que aceita valores crus). Use-os em todo enum que cruza a fronteira da API.

```python
from tempest_fastapi_sdk import BaseIntEnum, BaseStrEnum


class OrderStatus(BaseStrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class Priority(BaseIntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2

assert OrderStatus.PENDING == "pending"          # str inheritance
assert "paid" in OrderStatus                      # raw value membership
assert OrderStatus("paid") is OrderStatus.PAID    # canonical lookup
assert Priority.NORMAL + 1 == Priority.HIGH       # int math
assert str(OrderStatus.PAID) == "paid"            # text conversion = value
assert f"{Priority.HIGH:03d}" == "002"            # numeric specs keep working
```

Por herdarem de `str` / `int`, o Pydantic os serializa de forma transparente como o valor subjacente e o SQLAlchemy consegue persisti-los pela coluna `Enum` padrão sem um conversor extra.

!!! tip "`str(membro)` devolve o valor, não `\"Classe.MEMBRO\"`"
    Num mixin `str`/`Enum` cru, `str(OrderStatus.PAID)` e `f"{OrderStatus.PAID}"`
    devolvem `"OrderStatus.PAID"` — o clássico footgun que vaza o nome do membro
    para dentro de log, query string ou de um valor gravado em coluna crua. As
    bases do SDK sobrescrevem `__str__` **e** `__format__` para renderizar o
    valor, como faz o `enum.StrEnum`. Então `str(status)` é uma forma segura e
    explícita de chegar na representação armazenada — equivalente a
    `status.value`, e sem quebrar specs numéricos no `BaseIntEnum`.

    Mudou em **0.171.0**. Antes disso o `str()` devolvia `"Classe.MEMBRO"`; se
    algum código seu dependia desse formato (mensagens de log, por exemplo),
    troque por `repr(membro)` ou `membro.name`.

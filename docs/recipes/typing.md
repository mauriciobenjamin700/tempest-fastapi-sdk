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
```

Por herdarem de `str` / `int`, o Pydantic os serializa de forma transparente como o valor subjacente e o SQLAlchemy consegue persisti-los pela coluna `Enum` padrão sem um conversor extra.

# Enforce typing (static + runtime)

Type hints help your editor and mypy, but they are **erased at runtime**
-- nothing stops a caller from passing a `str` where you annotated `int`
once the code ships. This recipe covers the two ways to close that gap:

- **(A) Force annotations to exist** -- author discipline, handled by
  linters (zero runtime cost).
- **(B) Guarantee the runtime value matches the annotation** -- real
  validation, with a per-call cost.

!!! tip "Golden rule"
    `Any` is a **valid** annotation -- what's wrong is **not annotating**.
    Every strategy here requires things to *be* annotated; none forbids
    `Any`.

## (A) Force annotations with the linters

The SDK already enables ruff's `ANN` rule (require annotations) and
strict mypy. Every project scaffolded by `tempest new` ships it in
`pyproject.toml`:

```toml
[tool.ruff.lint]
# ANN forces annotating everything. ANN401 (forbid Any) is OFF on purpose.
select = ["E", "W", "F", "I", "B", "C4", "UP", "N", "SIM", "RUF", "ANN"]
ignore = ["B008", "B006", "ANN401", "ANN002", "ANN003"]
```

Then just run the CLI gates:

```bash
tempest lint     # ruff check (includes ANN)
tempest type     # mypy
tempest check    # everything: lint + fmt-check + type + test
```

A function without annotations now fails the gate:

```python
def add(a, b):         # missing types on a, b and the return
    return a + b
# ruff: ANN001 Missing type annotation for function argument `a`
#       ANN201 Missing return type annotation for public function `add`
```

## The pydantic plugin only checks constructors with `init_typed`

`plugins = ["pydantic.mypy"]` on its own checks **no argument** of any model
constructor. The plugin does run — it is what synthesizes one keyword-only
parameter per field — but `init_typed` defaults to `False`, so every one of
those parameters comes out annotated `Any`:

```python
from typing import reveal_type

from tempest_fastapi_sdk.schemas import BaseSchema

# docs-guard: skip — the rejected call below is what this section is about


class Probe(BaseSchema):
    """A schema with two typed fields."""

    name: str
    age: int


reveal_type(Probe.__init__)
Probe(name="x", age="doze")
```

With the plugin declared and nothing else (mypy 2.3.0, pydantic 2.13.4):

```text
note: Revealed type is "def (__pydantic_self__: Probe, *, name: Any, age: Any, **kwargs: Any)"
Success: no issues found in 1 source file
```

Turning it on is one block in `pyproject.toml`:

```toml
[tool.mypy]
plugins = ["pydantic.mypy"]

[tool.pydantic-mypy]
init_typed = true
warn_required_dynamic_aliases = true
```

The same file, afterwards:

```text
note: Revealed type is "def (__pydantic_self__: Probe, *, name: str, age: int, **kwargs: Any)"
error: Argument "age" to "Probe" has incompatible type "str"; expected "int"  [arg-type]
```

Pylance and pyright load no plugin at all — they read the annotations
directly and always flagged these call sites. Without the setting your
editor and your CI disagree, and CI is the one that is wrong.

!!! check "New projects ship it"
    `tempest new` writes the block as of v0.241.0, and the SDK itself now
    uses it: turning it on cost zero fixes across the package's 409 source
    files — it is a class of error that was never reported, not a backlog.

!!! warning "Service scaffolded before v0.241.0"
    Paste the block into your service's `pyproject.toml` by hand.
    `tempest check` cannot turn it on for you: mypy reads plugin config
    **only** from the config file, and exposes no command-line flag for it.

!!! note "What `init_typed` starts refusing"
    Input pydantic **would** coerce at runtime. A `Decimal` field handed
    `"1.5"` becomes `error: Argument "amount" ... incompatible type "str";
    expected "Decimal"` under mypy, while at runtime the value still builds
    as `Decimal('1.5')`. In a service that is the point — the annotation
    becomes the contract, and callers who want the coercion write
    `Decimal("1.5")` at the call site. In a library with public
    constructors, decide case by case.

## Configure typing strictness (`[tool.tempest]`)

How strict the gates are is a knob in `pyproject.toml`. One field
controls both ruff's ANN rules **and** the mypy flags that
`tempest lint`/`fix`/`type`/`check` apply:

```toml
[tool.tempest]
typing_strictness = "standard"   # lenient | standard | strict
```

| Level        | ruff (ANN)                          | mypy                                            |
| ------------ | ----------------------------------- | ----------------------------------------------- |
| `lenient`    | nothing extra                       | nothing extra                                   |
| `standard`   | require annotations (ANN001/201/...)| `--disallow-untyped-defs` `--disallow-incomplete-defs` |
| `strict`     | full ANN set                        | `--strict`                                       |

The flags are **layered on top of** your `[tool.ruff]` / `[tool.mypy]`
-- they never relax the project config. `ANN401` (which flags `Any`) is
**never** enabled, at any level.

Override per run without editing the file:

```bash
tempest check --strictness strict     # this run only
tempest lint -s lenient
```

!!! note "No `[tool.tempest]`?"
    When the field is absent (or there is no `pyproject.toml`), the level
    is `standard`. Projects from `tempest new` ship it pre-set.

## (B) Guarantee the runtime value

Where data comes from outside (queue message, external API response, CLI
input, dynamically built data), annotations are not enough -- you want
real validation. The SDK exposes three decorators over
`pydantic.validate_call` (already a dependency, so nothing new to
install):

### `strict_types` -- no coercion

Rejects any value that is not **already** the annotated type. Arguments
**and** the return are validated.

```python
from tempest_fastapi_sdk import strict_types

# docs-guard: skip — the rejected call below is what this section is about


@strict_types
def add(a: int, b: int) -> int:
    return a + b


add(1, 2)            # 3
add("1", 2)          # pydantic.ValidationError -- "1" is NOT coerced to 1
```

### `typed` -- safe coercion

Same, but coerces when Pydantic can do so unambiguously (`"1"` -> `1`).
Handy for stringly-typed input.

```python
from tempest_fastapi_sdk import typed

# docs-guard: skip — the rejected call below is what this section is about


@typed
def add(a: int, b: int) -> int:
    return a + b


add("1", 2)          # 3  (coerced)
add("abc", 2)        # pydantic.ValidationError -- cannot coerce
```

### `require_annotations` -- fail at import when an annotation is missing

Does not validate values -- it guarantees the function *is* annotated,
failing at import (no linter run needed). `self`/`cls` and
`*args`/`**kwargs` are exempt; `Any` counts as a present annotation.

```python
from typing import Any

from tempest_fastapi_sdk import require_annotations


@require_annotations
def ok(value: Any) -> None:        # Any is valid
    return None


@require_annotations
def bad(a) -> int:                 # TypeError at import:
    return a                       # "bad: missing type annotation for parameter 'a'"
```

!!! warning "Where to use the runtime decorators"
    They carry a **per-call cost**. Use them at the **boundaries**
    (queue, external API, CLI), not on every internal method. In a
    FastAPI service the request body is already validated by its Pydantic
    schema at the router -- re-validating internally is redundant
    overhead.

## Recap

- `Any` is a valid annotation; what's wrong is not annotating.
- **(A)** linters force annotations to exist -- `ANN` in ruff + mypy,
  run via `tempest lint`/`type`/`check`. Zero runtime cost.
- Strictness is a knob: `[tool.tempest] typing_strictness` (`lenient` /
  `standard` / `strict`), with a per-run `--strictness` override.
  `ANN401` never turns on.
- **(B)** to guarantee the runtime value at the boundaries:
  `strict_types` (no coercion), `typed` (coerces),
  `require_annotations` (require annotation at import). All over
  `pydantic.validate_call`.

## Base enums


`BaseStrEnum` / `BaseIntEnum` extend the stdlib `Enum` with helpers tuned for Pydantic + SQLAlchemy round-tripping (lookup by value, JSON-serializable `str` / `int` inheritance, `__contains__` that accepts raw values). Use them for every enum that crosses the API boundary.

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

Because they inherit from `str` / `int`, Pydantic serializes them transparently as their underlying value and SQLAlchemy can persist them via the standard `Enum` column without an extra converter.

!!! tip "`str(member)` gives you the value, not `\"Class.MEMBER\"`"
    On a bare `str`/`Enum` mixin, `str(OrderStatus.PAID)` and
    `f"{OrderStatus.PAID}"` return `"OrderStatus.PAID"` — the classic footgun
    that leaks the member name into a log line, a query string, or a value
    written to a raw column. The SDK bases override `__str__` **and**
    `__format__` to render the value, the way `enum.StrEnum` does. So
    `str(status)` is a safe, explicit way to reach the stored representation —
    equivalent to `status.value`, and without breaking numeric specs on
    `BaseIntEnum`.

    Changed in **0.171.0**. Before that, `str()` returned `"Class.MEMBER"`; if
    some code of yours relied on that format (log messages, say), switch it to
    `repr(member)` or `member.name`.

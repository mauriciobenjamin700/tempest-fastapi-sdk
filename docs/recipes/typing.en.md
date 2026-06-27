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

"""Model factories — build/persist ``BaseModel`` rows in tests tersely.

Framework-agnostic (no ``pytest`` import): a :class:`ModelFactory` binds
a model + a set of default column values to a session, so a test writes
``await users.create(email="a@b.com")`` instead of constructing the model
with every required field and wiring ``session.add`` / ``flush`` /
``refresh`` by hand.

No magic: the factory never guesses values for required columns — you
declare the defaults explicitly. A default may be a plain value or a
**callable taking the row index** (an auto-incrementing int), which is
how you make unique fields; :func:`seq` is a small helper for the common
``"user{n}@x.com"`` case.

```python
from tempest_fastapi_sdk.testing import ModelFactory, seq

users = ModelFactory(
    session,
    UserModel,
    email=seq("user{n}@example.com"),
    hashed_password="x",
    is_admin=False,
)
alice = await users.create(is_admin=True)      # one row, override a field
batch = await users.create_many(5)             # five rows, unique emails
draft = users.build(email="temp@x.com")         # unsaved instance
```
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from tempest_fastapi_sdk.db.model import BaseModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT", bound=BaseModel)


def seq(template: str, *, start: int = 0) -> Callable[[int], str]:
    """Return an index generator formatting ``template`` with ``{n}``.

    Use as a factory default to produce unique string values —
    ``email=seq("user{n}@x.com")`` yields ``user0@x.com``,
    ``user1@x.com``, … one per built row.

    Args:
        template (str): A format string containing ``{n}``.
        start (int): The first index value. Defaults to 0.

    Returns:
        Callable[[int], str]: A function mapping the row index to the
        formatted string.
    """
    return lambda index: template.format(n=index + start)


class ModelFactory(Generic[ModelT]):
    """Build and persist ``BaseModel`` instances with shared defaults.

    Bind a model and its default column values to a session once, then
    call :meth:`build` (unsaved), :meth:`create` (persisted) or
    :meth:`create_many`. Per-call keyword arguments override the
    defaults. A default (or override) that is **callable** is invoked
    with the row's auto-incrementing index, which is how unique fields
    are generated — see :func:`seq`.

    Attributes:
        session (AsyncSession): The session rows are added to.
        model (type[ModelT]): The model class built.
    """

    def __init__(
        self,
        session: AsyncSession,
        model: type[ModelT],
        **defaults: Any,
    ) -> None:
        """Initialize the factory.

        Args:
            session (AsyncSession): The async session to persist into.
            model (type[ModelT]): The ``BaseModel`` subclass to build.
            **defaults (Any): Default column values. A callable value is
                called with the row index (see :func:`seq`) to produce a
                per-row value.

        Raises:
            TypeError: When ``model`` is not a ``BaseModel`` subclass.
        """
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError("ModelFactory `model` must be a subclass of BaseModel")
        self.session: AsyncSession = session
        self.model: type[ModelT] = model
        self._defaults: dict[str, Any] = defaults
        self._index: int = 0

    def _resolve(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Merge defaults + overrides, calling index-generator callables.

        Args:
            overrides (dict[str, Any]): Per-call field overrides.

        Returns:
            dict[str, Any]: The concrete kwargs for one instance.
        """
        index = self._index
        self._index += 1
        merged = {**self._defaults, **overrides}
        return {
            key: value(index) if callable(value) else value
            for key, value in merged.items()
        }

    def build(self, **overrides: Any) -> ModelT:
        """Return an unsaved instance (defaults + overrides).

        Args:
            **overrides (Any): Field values overriding the defaults.

        Returns:
            ModelT: A new, un-persisted model instance.
        """
        return self.model(**self._resolve(overrides))

    async def create(self, **overrides: Any) -> ModelT:
        """Build, add + flush an instance, and return it refreshed.

        Uses ``flush`` (not ``commit``) so the row is visible within the
        test's transaction while leaving commit/rollback to the harness.

        Args:
            **overrides (Any): Field values overriding the defaults.

        Returns:
            ModelT: The persisted, refreshed instance.
        """
        instance = self.build(**overrides)
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def create_many(self, count: int, **overrides: Any) -> list[ModelT]:
        """Create ``count`` rows sharing ``overrides`` (unique per index).

        Args:
            count (int): How many rows to create.
            **overrides (Any): Field values overriding the defaults; a
                callable override still receives each row's index.

        Returns:
            list[ModelT]: The persisted, refreshed instances.
        """
        instances = [self.build(**overrides) for _ in range(count)]
        self.session.add_all(instances)
        await self.session.flush()
        for instance in instances:
            await self.session.refresh(instance)
        return instances


__all__: list[str] = [
    "ModelFactory",
    "seq",
]

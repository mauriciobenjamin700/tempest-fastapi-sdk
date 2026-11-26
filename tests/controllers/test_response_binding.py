"""Static-typing tests binding the controller's response type to its service.

Issue #838: ``BaseController`` declared its own ``ResponseT`` and reached it by
``cast`` over whatever the service returned, so a controller typed over the
wrong schema passed ``mypy --strict``::

    class UserService(BaseService[Any, UserResponseSchema]): ...
    class WrongController(BaseController[UserService, OrderResponseSchema]): ...

    (await controller.get_by_id(id)).total

At runtime that value is a ``UserResponseSchema`` with no ``total``. The
pass-throughs now return the response type of the service they delegate to, so
the misuse surfaces where the value is read.

The checks run ``mypy`` in a subprocess over snippets shaped like downstream
code. The basedpyright side was measured by hand (basedpyright 1.40.2 is not a
dev dependency): it reports the same ``Cannot access attribute "total" for class
"UserResponseSchema"`` on the wrong controller, reveals the response schema on
every form :data:`ACCEPTED_FORMS` exercises, and additionally refuses a service
whose declared response schema disagrees with the one its repository declares,
which ``mypy`` does not check (see :class:`TestServiceRepositoryBinding`).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.controllers.test_generic_bounds import _shared_mypy_cache

REPO_ROOT: Path = Path(__file__).resolve().parents[2]

PRELUDE: str = """\
from typing import Any
from uuid import UUID

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseController,
    BaseModel,
    BaseRepository,
    BaseResponseSchema,
    BaseSchema,
    BaseService,
)


class Account(BaseModel):
    __tablename__ = "account_response_binding_snippet"

    email: Mapped[str] = mapped_column(String(64), nullable=False)


class UserResponseSchema(BaseResponseSchema):
    email: str


class OrderResponseSchema(BaseSchema):
    total: int


class UserUpdateSchema(BaseSchema):
    email: str | None = None


class OtherUpdateSchema(BaseSchema):
    total: int | None = None


class UserRepository(BaseRepository[Account]):
    def map_to_response(self, instance: Account) -> UserResponseSchema:
        return UserResponseSchema.model_validate(instance)


class AsyncUserRepository(BaseRepository[Account]):
    async def map_to_response(self, instance: Account) -> UserResponseSchema:
        return UserResponseSchema.model_validate(instance)


class TypedUserRepository(BaseRepository[Account, UserResponseSchema]):
    def map_to_response(self, instance: Account) -> UserResponseSchema:
        return UserResponseSchema.model_validate(instance)
"""

WRONG_CONTROLLER: str = """\


class UserService(BaseService[Any, UserResponseSchema]):
    pass


class WrongController(BaseController[UserService, OrderResponseSchema]):
    pass


async def total(controller: WrongController, id: UUID) -> int:
    return (await controller.get_by_id(id)).total


async def listed(controller: WrongController) -> list[OrderResponseSchema]:
    return await controller.list()


class TypedService(
    BaseService[TypedUserRepository, UserResponseSchema, UserUpdateSchema]
):
    pass


class WrongUpdateController(
    BaseController[TypedService, UserResponseSchema, OtherUpdateSchema]
):
    pass


async def bad_update(
    controller: WrongUpdateController, id: UUID
) -> UserResponseSchema:
    return await controller.update(id, OtherUpdateSchema(total=1))
"""

ACCEPTED_FORMS: str = """\


class TwoArgService(BaseService[UserRepository, UserResponseSchema]):
    pass


class ThreeArgService(
    BaseService[UserRepository, UserResponseSchema, UserUpdateSchema]
):
    pass


class AsyncService(BaseService[AsyncUserRepository, UserResponseSchema]):
    pass


class TypedService(
    BaseService[TypedUserRepository, UserResponseSchema, UserUpdateSchema]
):
    pass


class TwoArgController(BaseController[TwoArgService, UserResponseSchema]):
    pass


class ThreeArgController(
    BaseController[ThreeArgService, UserResponseSchema, UserUpdateSchema]
):
    pass


class OneArgController(BaseController[TypedService]):
    pass


class OverridingController(BaseController[ThreeArgService, UserResponseSchema]):
    async def get_by_id(self, id: UUID) -> UserResponseSchema:
        return await super().get_by_id(id)


async def use(
    two: TwoArgController,
    three: ThreeArgController,
    one: OneArgController,
    overriding: OverridingController,
    async_service: AsyncService,
    id: UUID,
) -> list[UserResponseSchema]:
    reveal_type(await two.get_by_id(id))
    reveal_type(await three.update(id, UserUpdateSchema(email="a@b.c")))
    reveal_type(await one.list())
    reveal_type(await overriding.get_by_id(id))
    reveal_type(await async_service.get_or_none({"email": "a@b.c"}))
    return [await two.get_by_id(id), await one.get_by_id(id)]
"""


def _run_mypy(source: str, tmp_path: Path, tag: str) -> str:
    """Run ``mypy`` in a subprocess over ``source`` with this repo's config.

    Args:
        source (str): The module to check.
        tmp_path (Path): Directory the module is written to.
        tag (str): Cache name, unique per test so runs never share a cache.

    Returns:
        str: The checker's stdout, one diagnostic per line.
    """
    module: Path = tmp_path / f"downstream_{tag.replace('-', '_')}.py"
    module.write_text(source, encoding="utf-8")
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            str(module),
            "--strict",
            "--no-error-summary",
            "--hide-error-context",
            "--cache-dir",
            str(_shared_mypy_cache(tag)),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
        timeout=240,
    )
    return completed.stdout + completed.stderr


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestControllerResponseBinding:
    def test_wrong_controller_is_refused(self, tmp_path: Path) -> None:
        """The issue's reproduction must fail on ``get_by_id``, ``list`` and ``update``.

        Before #838 the pass-throughs returned the controller's own
        ``OrderResponseSchema`` and accepted its own ``UpdateT``, so
        ``mypy --strict`` reported nothing.
        """
        pytest.importorskip("mypy", reason="mypy is a dev-group dependency")
        output: str = _run_mypy(PRELUDE + WRONG_CONTROLLER, tmp_path, "binding-wrong")

        assert '"UserResponseSchema" has no attribute "total"' in output, output
        assert 'got "list[UserResponseSchema]"' in output, output
        assert 'expected "UserUpdateSchema"' in output, output

    def test_documented_forms_keep_their_response_type(self, tmp_path: Path) -> None:
        """Every documented generic form still checks and returns the schema.

        Two- and three-argument services and controllers, the one-argument
        controller, an async ``map_to_response`` and an override calling
        ``super()`` all check clean, and every pass-through reveals the
        response schema rather than ``Any``.
        """
        pytest.importorskip("mypy", reason="mypy is a dev-group dependency")
        output: str = _run_mypy(PRELUDE + ACCEPTED_FORMS, tmp_path, "binding-forms")

        errors: list[str] = [line for line in output.splitlines() if ": error:" in line]
        revealed: list[str] = [
            line.split("Revealed type is ", 1)[1]
            for line in output.splitlines()
            if "Revealed type is " in line
        ]
        assert not errors, output
        assert revealed == [
            '"downstream_binding_forms.UserResponseSchema"',
            '"downstream_binding_forms.UserResponseSchema"',
            '"list[downstream_binding_forms.UserResponseSchema]"',
            '"downstream_binding_forms.UserResponseSchema"',
            '"downstream_binding_forms.UserResponseSchema | None"',
        ], output


class TestServiceRepositoryBinding:
    def test_service_reads_the_repository_response_type(self) -> None:
        """``BaseService`` maps through the repository's declared response type.

        A repository declared ``BaseRepository[Model, Response]`` ties
        ``map_to_response`` to ``Response``; the service's methods carry a
        self-type requiring their response schema to be that one. basedpyright
        enforces it at the call site; ``mypy`` 2.3 does not check a self-type
        written with the class's own type variables, so this test pins the shape
        rather than a diagnostic.
        """
        from tempest_fastapi_sdk import BaseRepository, TenantScopedRepository

        assert len(BaseRepository.__parameters__) == 2
        assert len(TenantScopedRepository.__parameters__) == 2

    def test_no_unchecked_casts_or_any_mappers_remain(self) -> None:
        """Issue #838: drop ``cast``, service ignores and ``Any`` mappers."""
        from typing import Any, get_type_hints

        from tempest_fastapi_sdk import BaseRepository

        controllers_source: str = (
            REPO_ROOT / "tempest_fastapi_sdk" / "controllers" / "base.py"
        ).read_text(encoding="utf-8")
        services_source: str = (
            REPO_ROOT / "tempest_fastapi_sdk" / "services" / "base.py"
        ).read_text(encoding="utf-8")

        assert "cast(" not in controllers_source
        assert "type: ignore" not in services_source
        assert get_type_hints(BaseRepository.map_to_schema)["return"] is object
        assert get_type_hints(BaseRepository.map_to_response)["return"] is not Any

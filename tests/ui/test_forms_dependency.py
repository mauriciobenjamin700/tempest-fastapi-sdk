"""Tests for :func:`form_dependency`, the ``Depends`` form of ``parse_form`` (#345)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui.forms import FormResult, form_dependency


class LoginSchema(BaseModel):
    """The schema the dependency parses into."""

    email: str
    password: str = Field(min_length=8)
    tenant: str = "public"


LoginForm = Annotated[FormResult[LoginSchema], Depends(form_dependency(LoginSchema))]
ScopedLoginForm = Annotated[
    FormResult[LoginSchema],
    Depends(
        form_dependency(
            LoginSchema,
            exclude=["tenant"],
            extra={"tenant": "acme"},
            error_message=lambda error: f"bad:{error['type']}",
        ),
    ),
]
"""Module level on purpose: under ``from __future__ import annotations``
FastAPI resolves the annotation against the module globals, so a
dependency held in a local variable is not found."""


def _client() -> TestClient:
    """Build a client whose routes take the parsed form as a dependency.

    Returns:
        TestClient: A client with ``/login`` (plain) and ``/scoped``
        (``exclude=`` + ``extra=`` + ``error_message=``) routes.
    """
    app = FastAPI()

    @app.post("/login")
    async def login(result: LoginForm) -> dict[str, object]:
        """Echo the parse outcome."""
        return {"ok": result.ok, "errors": result.errors, "values": result.values}

    @app.post("/scoped")
    async def scoped_login(result: ScopedLoginForm) -> dict[str, object]:
        """Echo the parsed model, or the rewritten messages."""
        if not result.ok:
            return {"errors": result.errors}
        return result.unwrap().model_dump()

    return TestClient(app)


def test_route_receives_the_parsed_result_without_a_request() -> None:
    response = _client().post(
        "/login",
        data={"email": "a@b.c", "password": "12345678"},
    )
    assert response.json()["ok"] is True


def test_invalid_submission_reaches_the_route_as_errors() -> None:
    response = _client().post("/login", data={"email": "a@b.c", "password": "x"})
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert list(body["errors"]) == ["password"]
    assert body["values"]["password"] == "x"


def test_options_are_forwarded_to_parse_form() -> None:
    client = _client()
    ok = client.post(
        "/scoped",
        data={"email": "a@b.c", "password": "12345678", "tenant": "evil"},
    )
    assert ok.json()["tenant"] == "acme"
    bad = client.post("/scoped", data={"email": "a@b.c", "password": "x"})
    assert bad.json() == {"errors": {"password": ["bad:string_too_short"]}}


def test_dependency_does_not_leak_into_the_openapi_schema() -> None:
    app = FastAPI()

    @app.post("/login")
    async def login(result: LoginForm) -> dict[str, bool]:
        """Echo the outcome."""
        return {"ok": result.ok}

    operation = app.openapi()["paths"]["/login"]["post"]
    assert "requestBody" not in operation
    assert "parameters" not in operation


DOWNSTREAM_SNIPPET = '''\
"""A route typed the way the ui-forms recipe writes it."""

from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from tempest_fastapi_sdk.ui.forms import FormResult, form_dependency

app: FastAPI = FastAPI()


class LoginSchema(BaseModel):
    """Login payload."""

    email: str


LoginForm = Annotated[FormResult[LoginSchema], Depends(form_dependency(LoginSchema))]


@app.post("/login")
async def login(result: LoginForm) -> str:
    """Return the e-mail, typed through the dependency."""
    model: LoginSchema = result.unwrap()
    wrong: int = result.unwrap()
    return model.email
'''


def test_annotated_dependency_type_checks_under_mypy_strict(tmp_path: Path) -> None:
    """The dependency keeps the schema type: only the planted error fires.

    ``wrong: int = result.unwrap()`` is there to prove the result is not
    ``Any`` — an ``Any`` would let it through and the snippet would pass
    vacuously.
    """
    mypy_api = pytest.importorskip("mypy.api", reason="mypy is a dev-group dependency")
    module = tmp_path / "downstream_login.py"
    module.write_text(DOWNSTREAM_SNIPPET, encoding="utf-8")
    cache = Path(tempfile.gettempdir()) / "tempest-forms-dependency-mypy"

    stdout, stderr, _ = mypy_api.run(
        [
            str(module),
            "--strict",
            "--no-error-summary",
            "--hide-error-context",
            "--no-color-output",
            "--cache-dir",
            str(cache),
            "--python-executable",
            sys.executable,
        ],
    )

    errors = [line for line in stdout.splitlines() if ": error:" in line]
    assert len(errors) == 1, f"{stdout}\n{stderr}"
    assert "downstream_login.py:26:" in errors[0]
    assert "[assignment]" in errors[0]

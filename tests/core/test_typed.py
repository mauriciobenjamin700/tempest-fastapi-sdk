"""Tests for the runtime type-enforcement decorators."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import require_annotations, strict_types, typed


class TestStrictTypes:
    def test_valid_call_passes_through(self) -> None:
        @strict_types
        def add(a: int, b: int) -> int:
            return a + b

        assert add(1, 2) == 3

    def test_rejects_wrong_type_without_coercion(self) -> None:
        @strict_types
        def add(a: int, b: int) -> int:
            return a + b

        with pytest.raises(ValidationError):
            add("1", 2)  # type: ignore[arg-type]

    def test_validates_return_value(self) -> None:
        @strict_types
        def wrong() -> int:
            return "nope"  # type: ignore[return-value]

        with pytest.raises(ValidationError):
            wrong()

    def test_enforces_methods(self) -> None:
        class Service:
            @strict_types
            def double(self, n: int) -> int:
                return n * 2

        service = Service()
        assert service.double(3) == 6
        with pytest.raises(ValidationError):
            service.double("3")  # type: ignore[arg-type]


class TestTyped:
    def test_coerces_when_safe(self) -> None:
        @typed
        def add(a: int, b: int) -> int:
            return a + b

        assert add("1", 2) == 3  # type: ignore[arg-type]

    def test_rejects_uncoercible_value(self) -> None:
        @typed
        def add(a: int, b: int) -> int:
            return a + b

        with pytest.raises(ValidationError):
            add("abc", 2)  # type: ignore[arg-type]


class TestRequireAnnotations:
    def test_accepts_fully_annotated(self) -> None:
        @require_annotations
        def ok(a: int, b: str) -> bool:
            return bool(a) and bool(b)

        assert ok(1, "x") is True

    def test_any_is_a_valid_annotation(self) -> None:
        @require_annotations
        def takes_any(value: Any) -> None:
            return None

        assert takes_any(object()) is None

    def test_rejects_missing_parameter_annotation(self) -> None:
        with pytest.raises(TypeError, match="parameter 'a'"):

            @require_annotations
            def bad(a, b: int) -> int:  # type: ignore[no-untyped-def]  # noqa: ANN001
                return b

    def test_rejects_missing_return_annotation(self) -> None:
        with pytest.raises(TypeError, match="return type"):

            @require_annotations
            def bad(a: int):  # type: ignore[no-untyped-def]  # noqa: ANN202
                return a

    def test_self_and_varargs_are_exempt(self) -> None:
        class Service:
            @require_annotations
            def method(self, *args: int, **kwargs: int) -> int:
                return sum(args) + sum(kwargs.values())

        assert Service().method(1, 2, x=3) == 6

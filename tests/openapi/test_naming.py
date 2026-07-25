"""Tests for tempest_fastapi_sdk.openapi.naming."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.openapi.naming import (
    enum_member_name,
    field_name,
    method_name,
    to_pascal,
    to_snake,
    unique,
)


class TestToSnake:
    """Wire names become idiomatic Python names."""

    @pytest.mark.parametrize(
        ("wire", "expected"),
        [
            ("createdAt", "created_at"),
            ("CreatedAt", "created_at"),
            ("created_at", "created_at"),
            ("user-id", "user_id"),
            ("user.id", "user_id"),
            ("user id", "user_id"),
            ("HTTPStatusCode", "http_status_code"),
            ("ID", "id"),
            ("balanceCents2", "balance_cents2"),
            ("__weird__", "weird"),
        ],
    )
    def test_conversions(self, wire: str, expected: str) -> None:
        """Each shape a real specification uses converts correctly."""
        assert to_snake(wire) == expected

    def test_unusable_name_never_returns_empty(self) -> None:
        """An all-punctuation name still yields a valid identifier."""
        assert to_snake("***") == "field"


class TestToPascal:
    """Component names become class names."""

    @pytest.mark.parametrize(
        ("wire", "expected"),
        [
            ("Customer", "Customer"),
            ("customer_status", "CustomerStatus"),
            ("customer-status", "CustomerStatus"),
            ("Billing API", "BillingApi"),
            ("HTTPError", "HttpError"),
        ],
    )
    def test_conversions(self, wire: str, expected: str) -> None:
        """Names convert to PascalCase."""
        assert to_pascal(wire) == expected

    def test_leading_digit_is_prefixed(self) -> None:
        """A class name cannot start with a digit."""
        assert to_pascal("2fa") == "Model2fa"

    def test_unusable_name_never_returns_empty(self) -> None:
        """An all-punctuation name still yields a valid class name."""
        assert to_pascal("***") == "Model"


class TestFieldName:
    """Reserved words get out of the way; ordinary names do not change."""

    @pytest.mark.parametrize(
        ("wire", "expected"),
        [
            ("class", "class_"),
            ("from", "from_"),
            ("import", "import_"),
            ("lambda", "lambda_"),
            ("emailAddress", "email_address"),
        ],
    )
    def test_conversions(self, wire: str, expected: str) -> None:
        """Hard keywords are suffixed, everything else is just snake_cased."""
        assert field_name(wire) == expected

    def test_builtins_are_kept(self) -> None:
        """``id`` and ``type`` stay verbatim.

        A model attribute does not shadow the module namespace, and
        renaming the two most common wire field names would make every
        generated schema read worse.
        """
        assert field_name("id") == "id"
        assert field_name("type") == "type"

    @pytest.mark.parametrize("wire", ["match", "case", "type", "_"])
    def test_soft_keywords_are_kept(self, wire: str) -> None:
        """Soft keywords are not suffixed, on any Python version.

        They are contextual, so they are legal attribute names
        everywhere — and the soft-keyword list grows between releases
        (``type`` joined it in 3.12). Consulting it would make the
        generated field name depend on the interpreter that ran the
        generator, so the same specification would produce different code
        on 3.11 and 3.13. This regressed CI on exactly that difference.
        """
        assert not field_name(wire).endswith("_") or wire == "_"


class TestMethodName:
    """Operation names prefer the specification's own ``operationId``."""

    def test_operation_id_wins(self) -> None:
        """``operationId`` is the author's own name for the operation."""
        assert method_name("listCustomers", "get", "/customers") == "list_customers"

    def test_fallback_uses_method_and_path(self) -> None:
        """Without an ``operationId`` the name is derived from the route."""
        assert method_name(None, "delete", "/customers") == "delete_customers"

    def test_fallback_names_path_parameters(self) -> None:
        """Braces become readable words instead of being dropped."""
        assert (
            method_name(None, "get", "/users/{userId}/posts")
            == "get_users_by_user_id_posts"
        )

    def test_fallback_on_root_path(self) -> None:
        """A root path still produces a usable name."""
        assert method_name(None, "get", "/") == "get"


class TestEnumMemberName:
    """Enum values become valid, readable member names."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("active", "ACTIVE"),
            ("past_due", "PAST_DUE"),
            ("past-due", "PAST_DUE"),
            ("PastDue", "PAST_DUE"),
            (1, "VALUE_1"),
            ("2xx", "VALUE_2XX"),
            ("", "EMPTY"),
            ("*", "VALUE"),
        ],
    )
    def test_conversions(self, value: object, expected: str) -> None:
        """Each value shape yields a valid member name."""
        assert enum_member_name(value) == expected


class TestUnique:
    """Colliding names are suffixed rather than silently dropped."""

    def test_free_name_passes_through(self) -> None:
        """An unused name is returned as-is and reserved."""
        taken: set[str] = set()
        assert unique("user_id", taken) == "user_id"
        assert taken == {"user_id"}

    def test_collision_is_suffixed(self) -> None:
        """Two wire names collapsing onto one identifier both survive.

        ``user-id`` and ``userId`` both snake_case to ``user_id``; letting
        the second overwrite the first would silently drop a field.
        """
        taken: set[str] = set()
        assert unique("user_id", taken) == "user_id"
        assert unique("user_id", taken) == "user_id_2"
        assert unique("user_id", taken) == "user_id_3"

"""Tests for tempest_fastapi_sdk.utils.password.

Two halves. :class:`TestPasswordUtils` covers the bcrypt wrapper.
Everything below covers :func:`generate_password`, whose whole reason
to exist is that the obvious implementation — draw from one flat
alphabet, hope the character classes land — fails a quarter of the time
under ``AUTH_PASSWORD_REQUIRE_COMPLEXITY``. That failure is
intermittent, happens inside an OAuth callback, and is about a password
the user never typed, so it has to be caught here rather than in
production.

Every generator assertion runs the value through the **real**
``UserAuthService._enforce_password_policy``, not a re-implementation
of it, so a future policy change the generator cannot satisfy fails
here.
"""

from __future__ import annotations

import secrets

import pytest

from tempest_fastapi_sdk import (
    BaseUserModel,
    PasswordUtils,
    UserAuthService,
    generate_password,
    make_user_token_model,
)
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.utils.password import DEFAULT_GENERATED_PASSWORD_LENGTH


class TestPasswordUtils:
    def test_hash_returns_string(self) -> None:
        utils = PasswordUtils(rounds=4)  # Low rounds keep tests fast.
        result = utils.hash("hunter2")
        assert isinstance(result, str)
        assert result != "hunter2"

    def test_hash_is_non_deterministic(self) -> None:
        utils = PasswordUtils(rounds=4)
        a = utils.hash("hunter2")
        b = utils.hash("hunter2")
        assert a != b

    def test_verify_accepts_correct_password(self) -> None:
        utils = PasswordUtils(rounds=4)
        hashed = utils.hash("hunter2")
        assert utils.verify("hunter2", hashed) is True

    def test_verify_rejects_wrong_password(self) -> None:
        utils = PasswordUtils(rounds=4)
        hashed = utils.hash("hunter2")
        assert utils.verify("wrong", hashed) is False

    def test_verify_handles_malformed_hash(self) -> None:
        utils = PasswordUtils(rounds=4)
        # Garbage input should not raise — just return False.
        assert utils.verify("plain", "not-a-bcrypt-hash") is False

    def test_rounds_is_exposed(self) -> None:
        utils = PasswordUtils(rounds=6)
        assert utils.rounds == 6


class _PolicyUser(BaseUserModel):
    __tablename__ = "password_policy_users"


_PolicyUserToken = make_user_token_model(
    user_table="password_policy_users",
    tablename="password_policy_user_tokens",
    class_name="_PolicyUserToken",
)

SAMPLES: int = 500
"""Draws per policy combination.

Enough that a generator failing even 1% of the time is caught with
probability above 99%, and small enough that the whole module stays
under a second.
"""


def _service(**overrides: object) -> UserAuthService:
    """Build a service carrying the policy under test.

    Args:
        **overrides (object): ``AuthSettings`` field overrides.

    Returns:
        UserAuthService: A service whose ``_enforce_password_policy``
        applies exactly the requested policy.
    """
    return UserAuthService(
        user_model=_PolicyUser,
        token_model=_PolicyUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(_env_file=None, **overrides),  # type: ignore[arg-type]
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),  # type: ignore[arg-type]
    )


class TestGeneratedPasswordsSatisfyTheRealPolicy:
    """The generator's output is accepted by the policy that produced it."""

    @pytest.mark.parametrize("complexity", [True, False])
    @pytest.mark.parametrize("min_length", [1, 8, 12, 20, 64, 72])
    def test_every_draw_is_accepted(self, complexity: bool, min_length: int) -> None:
        service = _service(
            AUTH_PASSWORD_REQUIRE_COMPLEXITY=complexity,
            AUTH_PASSWORD_MIN_LENGTH=min_length,
        )
        for _ in range(SAMPLES):
            password = generate_password(
                min_length=min_length,
                max_bytes=72,
                require_complexity=complexity,
            )
            service._enforce_password_policy(password)

    def test_length_respects_both_bounds(self) -> None:
        password = generate_password(min_length=40, max_bytes=48)
        assert 40 <= len(password) <= 48

    def test_defaults_to_the_declared_length(self) -> None:
        assert len(generate_password()) == DEFAULT_GENERATED_PASSWORD_LENGTH

    def test_ascii_only_so_length_equals_byte_count(self) -> None:
        for _ in range(SAMPLES):
            password = generate_password()
            assert len(password.encode("utf-8")) == len(password)

    def test_draws_differ(self) -> None:
        drawn = {generate_password() for _ in range(SAMPLES)}
        assert len(drawn) == SAMPLES

    def test_unsatisfiable_policy_is_refused_loudly(self) -> None:
        with pytest.raises(ValueError, match="unsatisfiable"):
            generate_password(min_length=32, max_bytes=16)


class TestTheDefectThisGuardExistsFor:
    """A flat-alphabet generator fails the same policy, often."""

    def test_token_urlsafe_is_rejected_by_the_complexity_policy(self) -> None:
        """``secrets.token_urlsafe`` is not a compliant password source.

        Measured at 200 000 draws, ``token_urlsafe(32)`` was rejected
        26.54% of the time. This asserts only that *some* draw in a
        much smaller sample is rejected, because the point is the
        failure mode, not the rate: a generator that is usually fine is
        the defect.
        """
        service = _service(AUTH_PASSWORD_REQUIRE_COMPLEXITY=True)
        rejected = 0
        for _ in range(SAMPLES):
            try:
                service._enforce_password_policy(secrets.token_urlsafe(32))
            except ValidationException:
                rejected += 1
        assert rejected > 0

    def test_token_hex_never_satisfies_the_complexity_policy(self) -> None:
        """``token_hex`` has no uppercase and no special character."""
        service = _service(AUTH_PASSWORD_REQUIRE_COMPLEXITY=True)
        for _ in range(50):
            with pytest.raises(ValidationException):
                service._enforce_password_policy(secrets.token_hex(32))

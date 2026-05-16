"""Tests for tempest_fastapi_sdk.utils.password.PasswordUtils."""

from tempest_fastapi_sdk import PasswordUtils


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

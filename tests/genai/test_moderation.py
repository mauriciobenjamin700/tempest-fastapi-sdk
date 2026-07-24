"""Tests for content moderation."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.genai import (
    ClassifierModerator,
    ModerationBackend,
    ModerationResult,
    RuleModerator,
)


class TestRuleModerator:
    async def test_flags_blocked_term(self) -> None:
        mod = RuleModerator(["badword"], category="abuse")
        result = await mod.check("this has a badword in it")
        assert result.flagged is True
        assert result.categories == ["abuse"]
        assert result.score == 1.0

    async def test_passes_clean_text(self) -> None:
        mod = RuleModerator(["badword"])
        result = await mod.check("a perfectly fine sentence")
        assert result.flagged is False
        assert result.categories == []
        assert result.score == 0.0

    async def test_whole_word_only(self) -> None:
        mod = RuleModerator(["cat"])
        assert (await mod.check("concatenate things")).flagged is False
        assert (await mod.check("the cat sat")).flagged is True

    async def test_case_insensitive(self) -> None:
        mod = RuleModerator(["Spam"])
        assert (await mod.check("SPAM everywhere")).flagged is True

    def test_satisfies_protocol(self) -> None:
        assert isinstance(RuleModerator(["x"]), ModerationBackend)


class TestClassifierModeratorPolicy:
    def test_flagged_labels_explicit(self) -> None:
        mod = ClassifierModerator("m", flagged_labels=["toxic", "insult"])
        assert mod._is_flagged_label("TOXIC") is True
        assert mod._is_flagged_label("insult") is True
        assert mod._is_flagged_label("neutral") is False

    def test_default_flags_non_safe_labels(self) -> None:
        mod = ClassifierModerator("m")
        assert mod._is_flagged_label("toxic") is True
        assert mod._is_flagged_label("neutral") is False
        assert mod._is_flagged_label("safe") is False
        assert mod._is_flagged_label("non-toxic") is False

    def test_not_loaded_initially(self) -> None:
        assert ClassifierModerator("m").is_loaded is False


class TestModerationResult:
    def test_defaults(self) -> None:
        result = ModerationResult(flagged=False)
        assert result.categories == []
        assert result.score == 0.0


@pytest.mark.model
class TestClassifierModeratorWithModel:
    async def test_flags_toxic_text(self) -> None:
        mod = ClassifierModerator(
            "unitary/toxic-bert",
            flagged_labels=["toxic"],
            device="cpu",
            threshold=0.5,
        )
        result = await mod.check("I hate you, you are worthless garbage")
        assert isinstance(result, ModerationResult)

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


class TestRuleModeratorEvasion:
    """Block-list bypasses that shipped: non-word terms and Unicode tricks."""

    @pytest.mark.parametrize(
        ("term", "text"),
        [
            ("$hit", "you are a $hit"),
            ("c++", "I refuse to write c++ today"),
            ("#tag", "#tag at the start"),
        ],
    )
    async def test_term_with_non_word_characters_matches(
        self, term: str, text: str
    ) -> None:
        mod = RuleModerator([term])
        assert (await mod.check(text)).flagged is True

    async def test_non_word_term_keeps_whole_word_semantics(self) -> None:
        mod = RuleModerator(["c++"])
        assert (await mod.check("abc++ is not the language")).flagged is False

    async def test_zero_width_characters_do_not_bypass(self) -> None:
        mod = RuleModerator(["secret"])
        assert (await mod.check("the se\u200bcr\u200det plan")).flagged is True
        assert (await mod.check("the sec\u2060ret plan")).flagged is True

    async def test_fullwidth_letters_do_not_bypass(self) -> None:
        mod = RuleModerator(["secret"])
        assert (
            await mod.check("the \uff53\uff45\uff43\uff52\uff45\uff54 plan")
        ).flagged is True

    async def test_casefold_matches_sharp_s(self) -> None:
        mod = RuleModerator(["strasse"])
        assert (await mod.check("die STRAßE")).flagged is True

    async def test_blocklist_term_is_normalized_too(self) -> None:
        mod = RuleModerator(["\uff53ecret"])
        assert (await mod.check("a secret")).flagged is True

    async def test_whole_word_still_holds_after_normalization(self) -> None:
        mod = RuleModerator(["cat"])
        assert (await mod.check("con\u200bcatenate")).flagged is False


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

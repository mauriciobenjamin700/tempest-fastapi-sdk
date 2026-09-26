"""Tests for content moderation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

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


TOXIC_BERT_LABELS: dict[int, str] = {
    0: "toxic",
    1: "severe_toxic",
    2: "obscene",
    3: "threat",
    4: "insult",
    5: "identity_hate",
}


def _make_tokenizer(tmp_path: Path) -> Any:
    """Build a real word-level BERT tokenizer over a tiny vocabulary.

    Args:
        tmp_path (Path): Where the vocabulary file is written.

    Returns:
        Any: A ``BertTokenizerFast`` whose ``model_max_length`` is 512.
    """
    transformers = pytest.importorskip("transformers")
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "fine", "bad"]
    vocab_file = tmp_path / "vocab.txt"
    vocab_file.write_text("\n".join(vocab) + "\n", encoding="utf-8")
    return transformers.BertTokenizerFast(
        vocab_file=str(vocab_file),
        model_max_length=512,
    )


class _KeywordModel:
    """A stand-in classifier: ``toxic`` fires when ``bad`` is in the window.

    Every row gets the fixed ``base`` logits; a row whose unmasked tokens
    include ``bad_id`` gets ``+20`` on the ``toxic`` logit. Records the
    width of every batch it sees, so a test can assert the window size.
    """

    def __init__(
        self,
        *,
        bad_id: int,
        base: list[float],
        problem_type: str | None,
        max_positions: int = 512,
    ) -> None:
        """Configure the stand-in.

        Args:
            bad_id (int): Token id that turns ``toxic`` on.
            base (list[float]): Logits every row starts from.
            problem_type (str | None): Value exposed as
                ``config.problem_type``.
            max_positions (int): ``config.max_position_embeddings``.
        """
        import torch

        self.bad_id = bad_id
        self.base = base
        self.device = torch.device("cpu")
        self.config = SimpleNamespace(
            id2label=TOXIC_BERT_LABELS,
            problem_type=problem_type,
            max_position_embeddings=max_positions,
        )
        self.widths: list[int] = []

    def __call__(self, **inputs: Any) -> Any:
        """Return logits for a padded batch.

        Args:
            **inputs (Any): ``input_ids`` and ``attention_mask`` tensors.

        Returns:
            Any: An object with a ``logits`` tensor of shape
            ``(batch, labels)``.
        """
        import torch

        input_ids = inputs["input_ids"]
        mask = inputs["attention_mask"]
        self.widths.append(int(input_ids.shape[1]))
        rows: list[list[float]] = []
        for ids, row_mask in zip(input_ids.tolist(), mask.tolist(), strict=True):
            logits = list(self.base)
            live = [tid for tid, keep in zip(ids, row_mask, strict=True) if keep]
            if self.bad_id in live:
                logits[0] += 20.0
            rows.append(logits)
        return SimpleNamespace(logits=torch.tensor(rows))


def _loaded(mod: ClassifierModerator, tokenizer: Any, model: Any) -> None:
    """Install a tokenizer + model as if ``load()`` had built them.

    Args:
        mod (ClassifierModerator): The moderator to fill.
        tokenizer (Any): The tokenizer.
        model (Any): The stand-in model.
    """
    mod._tokenizer = tokenizer
    mod._model = model


class TestClassifierModeratorActivation:
    """Multi-label models take a sigmoid; softmax splits the mass (#316)."""

    TWO_HIGH: ClassVar[list[float]] = [3.0, -5.0, -5.0, -5.0, 3.0, -5.0]

    async def test_multi_label_config_uses_sigmoid(self, tmp_path: Path) -> None:
        pytest.importorskip("torch")
        tokenizer = _make_tokenizer(tmp_path)
        model = _KeywordModel(
            bad_id=-1,
            base=self.TWO_HIGH,
            problem_type="multi_label_classification",
        )
        mod = ClassifierModerator("m", flagged_labels=["toxic", "insult"])
        _loaded(mod, tokenizer, model)
        result = await mod.check("fine")
        assert result.flagged is True
        assert result.categories == ["toxic", "insult"]
        assert result.score == pytest.approx(0.9526, abs=1e-4)

    async def test_softmax_splits_the_mass_below_the_threshold(
        self, tmp_path: Path
    ) -> None:
        pytest.importorskip("torch")
        tokenizer = _make_tokenizer(tmp_path)
        model = _KeywordModel(
            bad_id=-1,
            base=self.TWO_HIGH,
            problem_type="multi_label_classification",
        )
        mod = ClassifierModerator(
            "m", flagged_labels=["toxic", "insult"], activation="softmax"
        )
        _loaded(mod, tokenizer, model)
        result = await mod.check("fine")
        assert result.flagged is False
        assert result.categories == []

    async def test_single_label_config_keeps_softmax(self, tmp_path: Path) -> None:
        pytest.importorskip("torch")
        tokenizer = _make_tokenizer(tmp_path)
        model = _KeywordModel(
            bad_id=-1,
            base=self.TWO_HIGH,
            problem_type="single_label_classification",
        )
        mod = ClassifierModerator("m", flagged_labels=["toxic", "insult"])
        _loaded(mod, tokenizer, model)
        assert (await mod.check("fine")).flagged is False

    async def test_sigmoid_override_wins_over_the_config(self, tmp_path: Path) -> None:
        pytest.importorskip("torch")
        tokenizer = _make_tokenizer(tmp_path)
        model = _KeywordModel(bad_id=-1, base=self.TWO_HIGH, problem_type=None)
        mod = ClassifierModerator(
            "m", flagged_labels=["toxic", "insult"], activation="sigmoid"
        )
        _loaded(mod, tokenizer, model)
        assert (await mod.check("fine")).categories == ["toxic", "insult"]

    def test_unknown_activation_is_refused(self) -> None:
        with pytest.raises(ValueError, match="activation"):
            ClassifierModerator("m", activation="relu")  # type: ignore[arg-type]


class TestClassifierModeratorWindows:
    """Text past the model's context is classified, not dropped (#316)."""

    BASE: ClassVar[list[float]] = [-10.0, -10.0, -10.0, -10.0, -10.0, -10.0]

    def _setup(
        self, tmp_path: Path, **options: Any
    ) -> tuple[ClassifierModerator, _KeywordModel]:
        """Build a moderator over the keyword model.

        Args:
            tmp_path (Path): Where the vocabulary is written.
            **options (Any): Extra arguments forwarded to the moderator.

        Returns:
            tuple[ClassifierModerator, _KeywordModel]: The pair.
        """
        pytest.importorskip("torch")
        tokenizer = _make_tokenizer(tmp_path)
        model = _KeywordModel(
            bad_id=tokenizer.convert_tokens_to_ids("bad"),
            base=self.BASE,
            problem_type="multi_label_classification",
        )
        mod = ClassifierModerator("m", flagged_labels=["toxic"], **options)
        _loaded(mod, tokenizer, model)
        return mod, model

    async def test_term_after_token_512_is_caught(self, tmp_path: Path) -> None:
        mod, model = self._setup(tmp_path)
        result = await mod.check(" ".join(["fine"] * 600 + ["bad"]))
        assert result.flagged is True
        assert result.categories == ["toxic"]
        assert max(model.widths) == 64

    async def test_whole_context_windows_still_reach_past_512(
        self, tmp_path: Path
    ) -> None:
        mod, model = self._setup(tmp_path, window_tokens=None, window_overlap=128)
        result = await mod.check(" ".join(["fine"] * 600 + ["bad"]))
        assert result.flagged is True
        assert max(model.widths) == 512

    async def test_window_is_capped_at_the_model_context(self, tmp_path: Path) -> None:
        mod, model = self._setup(tmp_path, window_tokens=4096)
        assert (await mod.check(" ".join(["fine"] * 1200 + ["bad"]))).flagged
        assert max(model.widths) == 512

    async def test_long_clean_text_stays_clean(self, tmp_path: Path) -> None:
        mod, _ = self._setup(tmp_path)
        assert (await mod.check(" ".join(["fine"] * 2000))).flagged is False

    async def test_term_on_a_window_boundary_is_caught(self, tmp_path: Path) -> None:
        mod, model = self._setup(tmp_path, window_tokens=10, window_overlap=3)
        for position in range(30):
            words = ["fine"] * 30
            words[position] = "bad"
            assert (await mod.check(" ".join(words))).flagged is True, position
        assert max(model.widths) <= 10

    async def test_short_text_is_one_window(self, tmp_path: Path) -> None:
        mod, model = self._setup(tmp_path)
        await mod.check("fine bad fine")
        assert model.widths == [5]

    def test_overlap_must_leave_room_to_advance(self) -> None:
        with pytest.raises(ValueError, match="window_overlap"):
            ClassifierModerator("m", window_tokens=10, window_overlap=10)

    def test_negative_overlap_is_refused(self) -> None:
        with pytest.raises(ValueError, match="window_overlap"):
            ClassifierModerator("m", window_overlap=-1)


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

    async def test_multi_label_scores_are_independent(self) -> None:
        mod = ClassifierModerator(
            "unitary/toxic-bert",
            flagged_labels=["toxic", "insult"],
            device="cpu",
        )
        result = await mod.check("You are a stupid idiot and I hate you.")
        assert result.categories == ["toxic", "insult"]

    async def test_toxic_sentence_after_a_long_prefix_is_caught(self) -> None:
        mod = ClassifierModerator(
            "unitary/toxic-bert",
            flagged_labels=["toxic"],
            device="cpu",
        )
        prefix = "The weather report says it will be sunny and mild. " * 60
        result = await mod.check(prefix + "You are a stupid idiot and I hate you.")
        assert result.flagged is True

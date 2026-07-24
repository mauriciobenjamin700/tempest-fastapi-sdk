"""Content moderation for genai input/output.

A pluggable moderation layer to screen user prompts and model completions.
Two backends:

* :class:`RuleModerator` — a dependency-free block-list matcher; predictable
  and the sensible default.
* :class:`ClassifierModerator` — a local text-classification model (e.g. a
  toxicity classifier) over ``transformers`` (the ``[genai]`` extra), lazy
  loaded and run in a worker thread.

Both satisfy the :class:`ModerationBackend` protocol and return a
:class:`ModerationResult`, so a caller (or an ``AIChatPipeline``) can check
input before generating and output after, then block or annotate per policy.
Self-hosted quality for non-English (PT-BR) toxicity models varies — treat the
classifier as best-effort and keep the rule backend as the deterministic floor.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import Field

from tempest_fastapi_sdk.genai.text import _require_transformers, resolve_device
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.schemas import HardwareInfo


class ModerationResult(BaseSchema):
    """The verdict of a moderation check.

    Attributes:
        flagged (bool): Whether the text violates policy.
        categories (list[str]): Matched category labels (empty when clean).
        score (float): Confidence of the strongest match in ``0..1``.
    """

    flagged: bool
    categories: list[str] = Field(default_factory=list)
    score: float = 0.0


@runtime_checkable
class ModerationBackend(Protocol):
    """Anything that screens a piece of text."""

    async def check(self, text: str) -> ModerationResult:
        """Return the moderation verdict for ``text``."""
        ...


class RuleModerator:
    """A dependency-free block-list moderator.

    Flags text containing any block-listed term (whole-word, case-insensitive).
    Predictable and fast — the deterministic default when a classifier's
    quality (especially in PT-BR) can't be trusted.

    Attributes:
        category (str): The category label reported on a match.
    """

    def __init__(self, blocklist: list[str], *, category: str = "blocked") -> None:
        """Initialize the moderator.

        Args:
            blocklist (list[str]): Terms that flag the text (whole-word,
                case-insensitive).
            category (str): The category label reported on a match.
        """
        self.category = category
        self._patterns = [
            re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
            for term in blocklist
            if term
        ]

    async def check(self, text: str) -> ModerationResult:
        """Flag ``text`` when it contains a block-listed term.

        Args:
            text (str): The text to screen.

        Returns:
            ModerationResult: ``flagged=True`` (score ``1.0``) on any match.
        """
        matched = any(pattern.search(text) for pattern in self._patterns)
        return ModerationResult(
            flagged=matched,
            categories=[self.category] if matched else [],
            score=1.0 if matched else 0.0,
        )


class ClassifierModerator:
    """A local text-classification moderator over ``transformers``.

    Runs a sequence-classification model (e.g. a toxicity classifier), maps its
    labels via the model config, and flags the text when a configured label's
    probability crosses ``threshold``. Lazy-loaded; inference runs in a worker
    thread. Best-effort — validate the model on your language before relying on
    it. Needs the ``[genai]`` extra.

    Attributes:
        model_id (str): The HuggingFace classifier id.
        threshold (float): Probability above which a label flags the text.
    """

    def __init__(
        self,
        model_id: str,
        *,
        flagged_labels: list[str] | None = None,
        threshold: float = 0.5,
        device: str = "auto",
        cache_dir: str | None = None,
        hf_token: str | None = None,
        hardware: HardwareInfo | None = None,
    ) -> None:
        """Configure the moderator (does not load weights yet).

        Args:
            model_id (str): HuggingFace sequence-classification model id.
            flagged_labels (list[str] | None): Label names that flag the text
                (case-insensitive); ``None`` flags any non-"neutral"/"ok" label.
            threshold (float): Minimum probability for a label to flag.
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            cache_dir (str | None): Weight cache directory.
            hf_token (str | None): Hub token for gated models.
            hardware (HardwareInfo | None): Injected snapshot (tests).
        """
        self.model_id = model_id
        self.flagged_labels = {label.lower() for label in (flagged_labels or [])}
        self.threshold = threshold
        self.device = resolve_device(device, hardware)
        self.cache_dir = cache_dir
        self.hf_token = hf_token
        self._model: Any = None
        self._tokenizer: Any = None
        self._last_used: float = time.monotonic()

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the weights are in memory."""
        return self._model is not None

    def load(self) -> None:  # pragma: no cover - needs torch + a real model
        """Load the classifier + tokenizer. Idempotent.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
        """
        if self.is_loaded:
            return
        _torch, transformers = _require_transformers()
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            token=self.hf_token,
        )
        self._model = transformers.AutoModelForSequenceClassification.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            token=self.hf_token,
        )
        self._model = self._model.to(self.device if self.device != "cpu" else "cpu")
        self._model.eval()

    def _is_flagged_label(self, label: str) -> bool:
        """Return ``True`` when ``label`` counts as a violation."""
        low = label.lower()
        if self.flagged_labels:
            return low in self.flagged_labels
        return low not in {"neutral", "ok", "non-toxic", "not_toxic", "safe", "clean"}

    async def check(self, text: str) -> ModerationResult:
        """Classify ``text`` and flag it per the label/threshold policy.

        Args:
            text (str): The text to screen.

        Returns:
            ModerationResult: The verdict with the matched categories + score.
        """
        return await asyncio.to_thread(self._check_sync, text)

    def _check_sync(
        self, text: str
    ) -> ModerationResult:  # pragma: no cover - needs torch
        """Blocking classification + policy mapping."""
        import torch

        self.load()
        inputs = self._tokenizer(
            text,
            truncation=True,
            return_tensors="pt",
        ).to(self._model.device)
        with torch.no_grad():
            logits = self._model(**inputs).logits[0]
        probs = torch.softmax(logits, dim=-1).tolist()
        id2label = self._model.config.id2label
        flagged: list[str] = []
        best = 0.0
        for index, prob in enumerate(probs):
            label = id2label[index]
            if self._is_flagged_label(label) and prob >= self.threshold:
                flagged.append(label)
                best = max(best, float(prob))
        self._last_used = time.monotonic()
        return ModerationResult(flagged=bool(flagged), categories=flagged, score=best)


__all__: list[str] = [
    "ClassifierModerator",
    "ModerationBackend",
    "ModerationResult",
    "RuleModerator",
]

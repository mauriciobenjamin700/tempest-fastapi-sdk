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
import unicodedata
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import Field

from tempest_fastapi_sdk.genai.hub import ModelRef
from tempest_fastapi_sdk.genai.text import _require_transformers, resolve_device
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils._lifecycle import ModelLifecycle

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
        """Return the moderation verdict for ``text``.

        Args:
            text (str): The content to screen.

        Returns:
            ModerationResult: The verdict, with the matched categories.
        """
        ...


def _normalize_for_matching(text: str) -> str:
    """Fold ``text`` into the form the block list is matched against.

    Applies NFKC (so fullwidth and other compatibility forms collapse onto
    their plain letters), drops every format character (Unicode category
    ``Cf`` — zero-width space/joiner/non-joiner, word joiner, BOM, bidi
    marks) and casefolds (so ``"STRAßE"`` matches ``"strasse"``). Both the
    block-listed terms and the screened text go through it, so the two
    sides always meet in the same form.

    Args:
        text (str): The raw text.

    Returns:
        str: The normalized text.
    """
    composed = unicodedata.normalize("NFKC", text)
    visible = "".join(char for char in composed if unicodedata.category(char) != "Cf")
    return visible.casefold()


class RuleModerator:
    r"""A dependency-free block-list moderator.

    Flags text containing any block-listed term as a whole word,
    case-insensitively. Before matching, both the terms and the text are
    NFKC-normalized, stripped of format characters (zero-width and bidi
    controls) and casefolded, so a zero-width space (U+200B) inside the
    word or its fullwidth spelling (U+FF53 U+FF45 ...) still hits
    ``"secret"``. Whole-word means "not glued to a word character on
    either side" (lookarounds, not ``\b``), which is what makes terms that
    start or end with punctuation — ``"$hit"``, ``"c++"`` — match at all.
    Predictable and fast — the deterministic default when a classifier's
    quality (especially in PT-BR) can't be trusted. Homoglyphs from other
    scripts (Cyrillic U+0435 standing in for Latin ``"e"``) are **not**
    folded; list those spellings explicitly.

    Attributes:
        category (str): The category label reported on a match.
    """

    def __init__(self, blocklist: list[str], *, category: str = "blocked") -> None:
        """Initialize the moderator.

        Args:
            blocklist (list[str]): Terms that flag the text (whole-word,
                case-insensitive, normalized as described on the class).
                Terms that normalize to an empty string are ignored.
            category (str): The category label reported on a match.
        """
        self.category = category
        terms = (_normalize_for_matching(term) for term in blocklist if term)
        self._patterns = [
            re.compile(rf"(?<!\w){re.escape(term)}(?!\w)") for term in terms if term
        ]

    async def check(self, text: str) -> ModerationResult:
        """Flag ``text`` when it contains a block-listed term.

        Args:
            text (str): The text to screen.

        Returns:
            ModerationResult: ``flagged=True`` (score ``1.0``) on any match.
        """
        normalized = _normalize_for_matching(text)
        matched = any(pattern.search(normalized) for pattern in self._patterns)
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
        revision: str | None = None,
        local_files_only: bool | None = None,
        trust_remote_code: bool = False,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
    ) -> None:
        """Configure the moderator (does not load weights yet).

        Args:
            model_id (str): HuggingFace sequence-classification model id.
            flagged_labels (list[str] | None): Label names that flag the text
                (case-insensitive); ``None`` flags any non-"neutral"/"ok" label.
            threshold (float): Minimum probability for a label to flag.
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            cache_dir (str | None): Where the downloaded weights are
                written and read back from. ``None`` uses the
                ``huggingface_hub`` default — ``$HF_HOME/hub``, or
                ``~/.cache/huggingface/hub`` when ``HF_HOME`` is unset —
                which is why the second run of a script starts instantly
                instead of downloading again. Point it at a mounted
                volume when the process is a container, so the layer does
                not re-download the model on every restart.
            hf_token (str | None): Hub token for gated or private
                repositories. ``None`` falls back to ``HF_TOKEN`` in the
                environment; without either, anonymous downloads work but
                are rate-limited (the Hub says so on stderr).
            revision (str | None): Branch, tag or commit sha to load;
                ``None`` follows the moving Hub default.
            local_files_only (bool | None): Load from the cache without
                touching the network — what an air-gapped or deploy-frozen
                host wants. ``None`` (the default) takes ``GENAI_OFFLINE``
                from the environment; passing the argument overrides it.
            trust_remote_code (bool): Allow the repository's own Python to
                run at load time.
            idle_unload_seconds (float | None): When set,
                :meth:`unload_if_idle` frees the classifier after this many
                idle seconds.
            hardware (HardwareInfo | None): Injected snapshot (tests).
        """
        self.model_id = model_id
        self.flagged_labels = {label.lower() for label in (flagged_labels or [])}
        self.threshold = threshold
        self.device = resolve_device(device, hardware)
        self.cache_dir = cache_dir
        self.hf_token = hf_token
        self.source = ModelRef(
            model_id=model_id,
            revision=revision,
            cache_dir=cache_dir,
            token=hf_token,
            local_files_only=local_files_only,
            trust_remote_code=trust_remote_code,
        )
        self.idle_unload_seconds = idle_unload_seconds
        self._model: Any = None
        self._tokenizer: Any = None
        self._lifecycle = ModelLifecycle(
            build=self._build,
            release=self._release,
            is_loaded=lambda: self._model is not None,
        )

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the weights are in memory."""
        return self._model is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the classifier was last in use.

        Reads ``0.0`` while a check is in flight.

        Returns:
            float: Idle time in seconds.
        """
        return self._lifecycle.seconds_idle()

    def unload(self) -> None:
        """Free the classifier and its memory. Safe when not loaded.

        While a check is in flight the release waits for it: the last call
        to finish drops the weights.
        """
        self._lifecycle.unload()

    def _release(self) -> None:
        """Drop the classifier and tokenizer."""
        self._model = None
        self._tokenizer = None

    def unload_if_idle(self) -> bool:
        """Free the classifier when idle past its configured threshold.

        Returns:
            bool: ``True`` when this call unloaded the model, ``False``
            when it was already free, still in use, or no
            ``idle_unload_seconds`` was configured.
        """
        return self._lifecycle.unload_if_idle(self.idle_unload_seconds)

    def load(self) -> None:
        """Load the classifier + tokenizer. Idempotent.

        Safe to call from several threads at once: concurrent callers on a
        cold instance wait for one build.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
        """
        self._lifecycle.load()

    def _build(self) -> None:  # pragma: no cover - needs torch + a real model
        """Load the tokenizer and weights; called once, under the load lock.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
        """
        _torch, transformers = _require_transformers()
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            self.model_id,
            **self.source.loader_kwargs(),
        )
        self._model = transformers.AutoModelForSequenceClassification.from_pretrained(
            self.model_id,
            **self.source.loader_kwargs(),
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

    def _check_sync(self, text: str) -> ModerationResult:
        """Load if needed and classify, holding the model for the whole call.

        Args:
            text (str): The text to screen.

        Returns:
            ModerationResult: The verdict.
        """
        with self._lifecycle.use():
            return self._classify(text)

    def _classify(self, text: str) -> ModerationResult:  # pragma: no cover - torch
        """Blocking classification + policy mapping."""
        import torch

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
        return ModerationResult(flagged=bool(flagged), categories=flagged, score=best)


__all__: list[str] = [
    "ClassifierModerator",
    "ModerationBackend",
    "ModerationResult",
    "RuleModerator",
]

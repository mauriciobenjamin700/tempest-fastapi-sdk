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
from typing import TYPE_CHECKING, Any, Literal, Protocol, get_args, runtime_checkable

from pydantic import Field

from tempest_fastapi_sdk.genai._lifecycle import ModelLifecycle
from tempest_fastapi_sdk.genai.hub import ModelRef
from tempest_fastapi_sdk.genai.text import _require_transformers, resolve_device
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.schemas import HardwareInfo

_Activation = Literal["auto", "sigmoid", "softmax"]
"""How :class:`ClassifierModerator` turns logits into per-label scores."""

_MULTI_LABEL_PROBLEM_TYPE: str = "multi_label_classification"
"""``config.problem_type`` value that marks independent labels (sigmoid)."""

_WINDOW_BATCH_SIZE: int = 16
"""Windows run through the model per forward pass, bounding peak memory."""

_DEFAULT_MAX_POSITIONS: int = 512
"""Window width when neither the tokenizer nor the config states a limit."""


def _split_windows(ids: list[int], size: int, overlap: int) -> list[list[int]]:
    """Cut ``ids`` into windows of at most ``size`` sharing ``overlap`` tokens.

    Consecutive windows start ``size - overlap`` tokens apart and the last one
    ends on the final token, so every token lands in at least one window and
    any span of up to ``overlap + 1`` tokens lands whole in one of them.

    Args:
        ids (list[int]): Token ids without special tokens.
        size (int): Maximum tokens per window.
        overlap (int): Tokens shared by consecutive windows; must be smaller
            than ``size``.

    Returns:
        list[list[int]]: The windows, in order; one (possibly empty) window
        when ``ids`` fits.

    Raises:
        ValueError: When ``overlap`` leaves no room to advance.
    """
    if overlap >= size:
        raise ValueError(
            f"window_overlap ({overlap}) must be smaller than the "
            f"{size} content tokens a window holds"
        )
    if len(ids) <= size:
        return [ids]
    step = size - overlap
    windows: list[list[int]] = []
    start = 0
    while True:
        windows.append(ids[start : start + size])
        if start + size >= len(ids):
            return windows
        start += step


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
    score crosses ``threshold``. Lazy-loaded; inference runs in a worker
    thread. Best-effort — validate the model on your language before relying on
    it. Needs the ``[genai]`` extra.

    Scores come from a sigmoid per label when the model is multi-label
    (``config.problem_type == "multi_label_classification"``, as in
    ``unitary/toxic-bert``) or has a single output, and from a softmax
    otherwise; ``activation=`` forces either. A softmax over independent
    labels splits the mass between them, so a text that is both toxic and
    insulting can score under the threshold on both.

    Text is never truncated: it is cut into short overlapping windows (64
    tokens by default), every window is classified, and each label keeps its
    highest score across windows — so content placed after a long harmless
    prefix is still screened. The windows are short on purpose: a classifier
    such as ``unitary/toxic-bert`` dilutes one toxic sentence inside a few
    hundred benign tokens below the threshold, so a window as wide as the
    model's context (512) still lets that sentence through. Cost grows with
    the number of windows.

    Attributes:
        model_id (str): The HuggingFace classifier id.
        threshold (float): Score above which a label flags the text.
        activation (str): ``"auto"``, ``"sigmoid"`` or ``"softmax"``.
        window_tokens (int | None): Tokens per window, special tokens
            included; ``None`` uses the model's whole context.
        window_overlap (int): Tokens shared by consecutive windows.
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
        activation: _Activation = "auto",
        window_tokens: int | None = 64,
        window_overlap: int = 16,
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
            activation (str): ``"auto"`` reads the model config —
                sigmoid for ``problem_type="multi_label_classification"`` or
                a single output, softmax otherwise. ``"sigmoid"`` /
                ``"softmax"`` force one, for a checkpoint whose config does
                not declare its problem type.
            window_tokens (int | None): Tokens per classified window, special
                tokens included, capped at the model's context (the
                tokenizer's ``model_max_length`` and the config's
                ``max_position_embeddings``, 512 for BERT). ``None`` uses
                that whole context — fewer forward passes, but a short
                violation diluted by the text around it scores lower.
            window_overlap (int): Tokens shared by consecutive windows, so a
                phrase of up to ``window_overlap + 1`` tokens cut by one
                window boundary lands whole in the next.

        Raises:
            ValueError: On an unknown ``activation``, a negative
                ``window_overlap``, or a ``window_tokens`` that leaves no
                room to advance past ``window_overlap``.
        """
        if activation not in get_args(_Activation):
            raise ValueError(
                f"activation must be one of {get_args(_Activation)}, got {activation!r}"
            )
        if window_overlap < 0:
            raise ValueError(f"window_overlap must be >= 0, got {window_overlap}")
        if window_tokens is not None and window_overlap >= window_tokens:
            raise ValueError(
                f"window_overlap ({window_overlap}) must be smaller than "
                f"window_tokens ({window_tokens})"
            )
        self.model_id = model_id
        self.activation: _Activation = activation
        self.window_tokens = window_tokens
        self.window_overlap = window_overlap
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

    def _uses_sigmoid(self) -> bool:
        """Return ``True`` when labels are scored independently.

        Returns:
            bool: The resolved activation for the loaded model.
        """
        if self.activation != "auto":
            return self.activation == "sigmoid"
        config = self._model.config
        if getattr(config, "problem_type", None) == _MULTI_LABEL_PROBLEM_TYPE:
            return True
        return len(config.id2label) == 1

    def _window_size(self) -> int:
        """Return how many tokens (special tokens included) a window holds.

        Returns:
            int: ``window_tokens`` capped at the model's context — the
            smaller of the tokenizer's ``model_max_length`` and the config's
            ``max_position_embeddings`` — or that context when
            ``window_tokens`` is ``None``.
        """
        limits = [
            limit
            for limit in (
                getattr(self._tokenizer, "model_max_length", None),
                getattr(self._model.config, "max_position_embeddings", None),
            )
            if isinstance(limit, int) and limit > 0
        ]
        context = min(limits) if limits else _DEFAULT_MAX_POSITIONS
        if self.window_tokens is None:
            return context
        return min(self.window_tokens, context)

    def _classify(self, text: str) -> ModerationResult:
        """Blocking windowed classification + policy mapping.

        Tokenizes without truncation, classifies every window in batches of
        ``_WINDOW_BATCH_SIZE`` and keeps each label's highest score.

        Args:
            text (str): The text to screen.

        Returns:
            ModerationResult: The verdict over the whole text.
        """
        import torch

        tokenizer = self._tokenizer
        ids: list[int] = tokenizer(
            text,
            add_special_tokens=False,
            truncation=False,
            verbose=False,
        )["input_ids"]
        content = self._window_size() - tokenizer.num_special_tokens_to_add()
        windows = [
            tokenizer.build_inputs_with_special_tokens(window)
            for window in _split_windows(ids, content, self.window_overlap)
        ]
        sigmoid = self._uses_sigmoid()
        best_scores: torch.Tensor | None = None
        for start in range(0, len(windows), _WINDOW_BATCH_SIZE):
            batch = tokenizer.pad(
                {"input_ids": windows[start : start + _WINDOW_BATCH_SIZE]},
                return_tensors="pt",
            ).to(self._model.device)
            with torch.no_grad():
                logits = self._model(**batch).logits
            scores = torch.sigmoid(logits) if sigmoid else torch.softmax(logits, -1)
            batch_best = scores.max(dim=0).values
            best_scores = (
                batch_best
                if best_scores is None
                else torch.maximum(best_scores, batch_best)
            )
        if best_scores is None:
            raise RuntimeError("no window was classified")
        id2label = self._model.config.id2label
        flagged: list[str] = []
        best = 0.0
        for index, score in enumerate(best_scores.tolist()):
            label = id2label[index]
            if self._is_flagged_label(label) and score >= self.threshold:
                flagged.append(label)
                best = max(best, float(score))
        return ModerationResult(flagged=bool(flagged), categories=flagged, score=best)


__all__: list[str] = [
    "ClassifierModerator",
    "ModerationBackend",
    "ModerationResult",
    "RuleModerator",
]

"""The checked-in Stripe facts and the code that reads them agree.

``events.py`` is generated from ``vendor/stripe-api-facts.yaml``, and the
pinned ``Stripe-Version`` comes from the same file. Both can be edited by
hand, and a hand edit to generated code is invisible until the next
regeneration silently reverts it — so this suite regenerates and compares.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml


def _repo_root() -> Path:
    """Locate the repository root from this file.

    Returns:
        Path: The first ancestor directory holding ``pyproject.toml``.

    Raises:
        RuntimeError: When no ancestor carries ``pyproject.toml``.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("repository root not found")


REPO_ROOT: Path = _repo_root()
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from regen_stripe import (  # noqa: E402
    EVENTS_PATH,
    FACTS_PATH,
    format_source,
    load_facts,
    member_name,
    render_events,
)

from tempest_fastapi_sdk.integrations.payment.stripe import (  # noqa: E402
    STRIPE_API_VERSION,
    STRIPE_BASE_URL,
    StripeEvent,
)


@pytest.fixture(scope="module")
def facts() -> dict[str, object]:
    """Return the vendored facts.

    Returns:
        dict[str, object]: The parsed facts file.
    """
    if not FACTS_PATH.exists():  # pragma: no cover - vendored in the repo
        pytest.skip("vendored Stripe facts are missing")
    return load_facts()


class TestGeneratedEvents:
    def test_events_module_matches_the_generator(
        self, facts: dict[str, object]
    ) -> None:
        """Regenerating produces exactly the file on disk.

        Args:
            facts (dict[str, object]): The vendored facts.
        """
        rendered = render_events(list(facts["event_types"]))  # type: ignore[arg-type]
        expected = format_source(rendered)

        assert EVENTS_PATH.read_text() == expected

    def test_every_vendored_event_is_a_member(self, facts: dict[str, object]) -> None:
        """Nothing from the specification was dropped on the way in.

        Args:
            facts (dict[str, object]): The vendored facts.
        """
        values = {member.value for member in StripeEvent}

        assert set(facts["event_types"]) == values  # type: ignore[arg-type]

    def test_member_names_are_derived_not_invented(self) -> None:
        """The naming rule is mechanical, so it can be checked in one line."""
        assert member_name("payment_intent.succeeded") == "PAYMENT_INTENT_SUCCEEDED"
        assert StripeEvent.PAYMENT_INTENT_SUCCEEDED.value == "payment_intent.succeeded"

    def test_the_wildcard_is_not_an_event(self) -> None:
        """``*`` subscribes to everything; it is not a delivery's type."""
        assert "*" not in {member.value for member in StripeEvent}


class TestPinnedFacts:
    def test_api_version_matches_the_vendored_spec(
        self, facts: dict[str, object]
    ) -> None:
        """Refreshing the facts without revisiting the code fails here.

        Args:
            facts (dict[str, object]): The vendored facts.
        """
        assert facts["api_version"] == STRIPE_API_VERSION

    def test_base_url_matches_the_vendored_spec(self, facts: dict[str, object]) -> None:
        """The host comes from ``servers[0]``, not from memory.

        Args:
            facts (dict[str, object]): The vendored facts.
        """
        assert facts["base_url"] == STRIPE_BASE_URL

    def test_facts_file_is_small_enough_to_review(self) -> None:
        """The point of distilling: a diff a human can actually read.

        The upstream specification is about 6.4 MB, which is why it is not
        vendored whole.
        """
        assert FACTS_PATH.stat().st_size < 64 * 1024

    def test_facts_file_parses_as_yaml(self) -> None:
        """A truncated fetch must fail here, not at the next regeneration."""
        parsed = yaml.safe_load(FACTS_PATH.read_text())

        assert set(parsed) == {"api_version", "base_url", "event_types"}

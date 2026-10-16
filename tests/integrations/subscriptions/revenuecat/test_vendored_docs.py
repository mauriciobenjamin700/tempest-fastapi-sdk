"""The enums and samples are pinned to RevenueCat's own pages.

``vendor/`` holds the three documentation pages as RevenueCat served them on
2026-10-15 (``https://www.revenuecat.com/docs/<path>.md``; see
``vendor/PROVENANCE.md``). These tests make the event-type table a fact the
suite checks rather than a list typed from memory:

* the sha256 of each file is pinned, so editing a vendored page by hand fails;
* :class:`RevenueCatEventTypeEnum` equals the set of ``**`TYPE`**`` bullets on
  the "Event Types and Fields" page, so a refresh that adds a type fails until
  the enum (and :func:`normalize`) learn it;
* the reasons table and the signature header name match the pages.

Refreshing: download the three pages again with ``curl -sL`` to the same
names, update ``PROVENANCE.md`` and :data:`PINNED_SHA256`, and fix what fails.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest

from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    REVENUECAT_SIGNATURE_HEADER,
    RevenueCatEventTypeEnum,
    RevenueCatReasonEnum,
    RevenueCatWebhook,
)
from tests.integrations.subscriptions.revenuecat.conftest import (
    EVENT_TYPES_PAGE,
    SAMPLES_PAGE,
    VENDOR,
)

PINNED_SHA256: dict[str, str] = {
    "revenuecat-webhook-event-types.md": (
        "e34cfe97441ade570b6aa57fc8cb30cad624ce702f3fcad1f4da5bbc44a6e982"
    ),
    "revenuecat-webhook-sample-events.md": (
        "4c08ea30c7a1c7402304596070cc30a7afb1bd478ec6a854cc9b1161729164ec"
    ),
    "revenuecat-webhooks.md": (
        "6afa1311aae8bd6295d2da4aa934b641b790ad68f4c270ab6072099342c58790"
    ),
}
"""sha256 of each vendored page, as downloaded on 2026-10-15."""


@pytest.mark.parametrize("name", sorted(PINNED_SHA256))
def test_the_vendored_page_is_the_bytes_revenuecat_served(name: str) -> None:
    digest = hashlib.sha256((VENDOR / name).read_bytes()).hexdigest()

    assert digest == PINNED_SHA256[name]


def _documented_event_types() -> set[str]:
    """Read every event-type bullet off the vendored page."""
    text = EVENT_TYPES_PAGE.read_text(encoding="utf-8")
    return set(re.findall(r"^- \*\*`([A-Z_]+)`\*\* — ", text, re.M))


def test_the_enum_is_exactly_the_documented_event_types() -> None:
    documented = _documented_event_types()

    assert len(documented) >= 20
    assert set(RevenueCatEventTypeEnum.values()) == documented


def test_the_reason_enum_is_exactly_the_documented_reasons() -> None:
    text = EVENT_TYPES_PAGE.read_text(encoding="utf-8")
    section = text.split("## Cancellation and Expiration Reasons", 1)[1]
    documented = set(re.findall(r"^\| `([A-Z_]+)` ", section, re.M))

    assert set(RevenueCatReasonEnum.values()) == documented


def test_the_signature_header_is_the_documented_one() -> None:
    text = (VENDOR / "revenuecat-webhooks.md").read_text(encoding="utf-8")

    assert f"`{REVENUECAT_SIGNATURE_HEADER}`" in text


def test_every_sample_parses(samples: list[dict[str, Any]]) -> None:
    assert len(samples) == 20
    for body in samples:
        webhook = RevenueCatWebhook.model_validate(body)
        assert webhook.api_version == "1.0"
        assert RevenueCatEventTypeEnum.has_value(webhook.event.type)


def test_the_samples_page_is_vendored() -> None:
    assert SAMPLES_PAGE.is_file()

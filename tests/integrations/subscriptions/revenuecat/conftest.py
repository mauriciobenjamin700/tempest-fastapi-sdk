"""Fixtures for the RevenueCat tests: the documentation's own sample payloads.

The samples come from ``vendor/revenuecat-webhook-sample-events.md``, the
"Sample Events" page RevenueCat publishes
(https://www.revenuecat.com/docs/integrations/webhooks/sample-events), read
as served rather than retyped, so a test runs against the bodies RevenueCat
documents.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

VENDOR: Path = Path(__file__).resolve().parents[4] / "vendor"
"""The repository's ``vendor/`` directory."""

SAMPLES_PAGE: Path = VENDOR / "revenuecat-webhook-sample-events.md"
"""The vendored "Sample Events" page."""

EVENT_TYPES_PAGE: Path = VENDOR / "revenuecat-webhook-event-types.md"
"""The vendored "Event Types and Fields" page."""


def load_samples() -> list[dict[str, Any]]:
    """Parse every ```` ```json ```` block of the vendored samples page.

    Returns:
        list[dict[str, Any]]: The webhook bodies, in page order.
    """
    text = SAMPLES_PAGE.read_text(encoding="utf-8")
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, re.S)]


@pytest.fixture(scope="session")
def samples() -> list[dict[str, Any]]:
    """Every documented sample body."""
    return load_samples()

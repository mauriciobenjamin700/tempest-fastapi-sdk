"""Refresh the Stripe facts and regenerate ``events.py`` from them.

Two steps, deliberately separate:

``--fetch``
    Download Stripe's ``openapi/spec3.yaml`` (about 6.4 MB) and distil the
    three facts this SDK actually consumes into
    ``vendor/stripe-api-facts.yaml``: the pinned API version, the base URL,
    and the full list of webhook event types. Needs the network.

default
    Read the vendored facts and rewrite
    ``integrations/payment/stripe/events.py``. Offline, deterministic, and
    what ``tests/integrations/payment/stripe/test_generated_drift.py``
    re-runs to prove the checked-in file was not hand-edited.

Why the whole specification is **not** vendored, and why no client is
generated from it — both measured on the 2026-07-29.dahlia spec:

* Generating the full surface yields ``schemas.py`` at **3.3 MB / 81k
  lines** and ``client.py`` at **983 KB**, and importing the schemas costs
  **5.8 s and 492 MB of RSS**. That is not a price a web service can pay to
  create one customer.
* Slicing by resource does not help. Stripe's object graph is almost
  fully connected through expandable fields and error envelopes:
  ``/v1/prices`` **alone** pulls 864 of the 1440 component schemas, and the
  ten core resources together pull 1020. There is no small subset.

So the generated-client path is closed by measurement, and the SDK ships a
hand-written client over :class:`~tempest_fastapi_sdk.utils.HTTPClient`
instead. What still comes from the specification comes from here.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
"""Repository root, resolved from this file rather than the cwd."""

SPEC_URL: str = (
    "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.yaml"
)
"""Upstream specification. Stripe publishes it in this repository."""

FACTS_PATH: Path = REPO_ROOT / "vendor" / "stripe-api-facts.yaml"
"""The distilled facts, vendored so regeneration and tests run offline."""

EVENTS_PATH: Path = (
    REPO_ROOT
    / "tempest_fastapi_sdk"
    / "integrations"
    / "payment"
    / "stripe"
    / "events.py"
)
"""The generated event enum."""

_HEADER: str = '''"""Stripe webhook event types, generated from the API specification.

Do not edit by hand: ``scripts/regen_stripe.py`` writes this file from
``vendor/stripe-api-facts.yaml``, and
``tests/integrations/payment/stripe/test_generated_drift.py`` fails when
the two disagree.

The specification does not enumerate ``event.type`` on the event object
itself — the authoritative list is the ``enabled_events`` parameter of
``POST /v1/webhook_endpoints``, which is where these values come from.
"""

from __future__ import annotations

from tempest_fastapi_sdk.core.enums import BaseStrEnum


class StripeEvent(BaseStrEnum):
    """A Stripe webhook event type.

    Members are named after the event string with dots and dashes turned
    into underscores, upper-cased. Use :meth:`BaseStrEnum.from_value` to
    parse a delivery, and :meth:`BaseStrEnum.has_value` to tell a known
    event from one Stripe added after this SDK release.
    """

'''
"""Everything above the generated members."""


def fetch_facts() -> dict[str, Any]:
    """Download the specification and distil the facts this SDK uses.

    Returns:
        dict[str, Any]: ``api_version``, ``base_url`` and the sorted
        ``event_types``.

    Raises:
        KeyError: When the specification no longer carries the fields this
            extraction depends on — better a loud failure here than a
            silently empty enum.
    """
    with urllib.request.urlopen(SPEC_URL) as response:
        document: dict[str, Any] = yaml.safe_load(response.read())

    api_version = str(document["info"]["version"])
    base_url = str(document["servers"][0]["url"]).rstrip("/")
    schema = document["paths"]["/v1/webhook_endpoints"]["post"]["requestBody"][
        "content"
    ]["application/x-www-form-urlencoded"]["schema"]
    enum: list[str] = schema["properties"]["enabled_events"]["items"]["enum"]
    event_types = sorted(value for value in enum if value != "*")
    if not event_types:
        raise KeyError("enabled_events enum was empty — extraction is out of date")
    return {
        "api_version": api_version,
        "base_url": base_url,
        "event_types": event_types,
    }


def load_facts() -> dict[str, Any]:
    """Read the vendored facts.

    Returns:
        dict[str, Any]: The parsed facts file.

    Raises:
        FileNotFoundError: When the facts have never been fetched.
    """
    if not FACTS_PATH.exists():
        raise FileNotFoundError(
            f"vendored facts missing: {FACTS_PATH} — run with --fetch first"
        )
    facts: dict[str, Any] = yaml.safe_load(FACTS_PATH.read_text())
    return facts


def member_name(event_type: str) -> str:
    """Return the enum member name for an event string.

    Args:
        event_type (str): The wire value, e.g. ``"payment_intent.succeeded"``.

    Returns:
        str: The member name, e.g. ``"PAYMENT_INTENT_SUCCEEDED"``.
    """
    return event_type.replace(".", "_").replace("-", "_").upper()


def render_events(event_types: list[str]) -> str:
    """Render the ``events.py`` source.

    Args:
        event_types (list[str]): The event strings, already sorted.

    Returns:
        str: The complete module source, ending in a newline.
    """
    lines = [_HEADER.rstrip("\n")]
    seen: set[str] = set()
    for event_type in event_types:
        name = member_name(event_type)
        if name in seen:
            continue
        seen.add(name)
        lines.append(f'    {name} = "{event_type}"')
    lines.extend(["", "", "__all__: list[str] = [", '    "StripeEvent",', "]", ""])
    return "\n".join(lines)


def format_source(source: str) -> str:
    """Return ``source`` as ``ruff format`` would write it.

    Args:
        source (str): The rendered module.

    Returns:
        str: The formatted module, or the input unchanged when ruff cannot
        be resolved — a missing formatter degrades polish, never
        correctness.

    The generated file is checked in **formatted**, so the drift test has
    to compare against the formatted text. Skipping this step is how a
    codegen drift test starts failing on whitespace the moment somebody
    runs the formatter over the repository, which is every commit here.
    """
    from tempest_cli import resolve_tool

    runner = resolve_tool("ruff")
    if runner is None:  # pragma: no cover - depends on the environment
        return source
    with tempfile.TemporaryDirectory() as staging:
        path = Path(staging) / "events.py"
        path.write_text(source)
        subprocess.run([*runner, "format", str(path)], check=False, capture_output=True)
        return path.read_text()


def main() -> int:
    """Run the requested step.

    Returns:
        int: Process exit code — ``0`` on success.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="download the specification and refresh vendor/stripe-api-facts.yaml",
    )
    arguments = parser.parse_args()

    if arguments.fetch:
        facts = fetch_facts()
        FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        FACTS_PATH.write_text(
            yaml.safe_dump(facts, sort_keys=True, allow_unicode=True, width=88)
        )
        print(f"  + {FACTS_PATH.relative_to(REPO_ROOT)}")

    facts = load_facts()
    EVENTS_PATH.write_text(format_source(render_events(list(facts["event_types"]))))
    print(f"  + {EVENTS_PATH.relative_to(REPO_ROOT)}")
    print(f"    api_version: {facts['api_version']}")
    print(f"    events: {len(facts['event_types'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

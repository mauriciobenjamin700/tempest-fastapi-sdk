"""Guard that every public symbol reaches the rendered API reference.

``docs/reference.md`` promises that every symbol exported by the SDK is
documented there. That promise silently broke as the SDK grew: 190 names
across ``__all__`` were absent, including whole feature areas (``geo``,
``tasks``, ``chat``, ``reviews``) that had recipes but no reference at all.

Parsing the markdown is not enough to catch that. A ``:::`` directive can
name a module and render every member, and ``mkdocstrings`` filters can
exclude whole classes of names (an ``!^[a-z_]+$`` filter silently dropped
every function from the top-level directive). So this guard asserts against
the **built HTML**: it renders the reference page and checks the anchor ids
mkdocstrings actually emitted.

The build is the slow part, so it runs once per session and the test is
marked ``docs`` for easy deselection.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

PUBLIC_MODULES: tuple[str, ...] = (
    "tempest_fastapi_sdk",
    "tempest_fastapi_sdk.admin",
    "tempest_fastapi_sdk.chat",
    "tempest_fastapi_sdk.flags",
    "tempest_fastapi_sdk.geo",
    "tempest_fastapi_sdk.modelops",
    "tempest_fastapi_sdk.openapi",
    "tempest_fastapi_sdk.queue",
    "tempest_fastapi_sdk.reviews",
    "tempest_fastapi_sdk.ssr",
    "tempest_fastapi_sdk.tasks",
    "tempest_fastapi_sdk.utils",
    "tempest_fastapi_sdk.vision",
)
"""Modules whose ``__all__`` the reference is expected to cover."""

ALLOWED_ABSENT: dict[str, str] = {
    "CEP": "deprecated pre-0.76 alias of CEPField; documenting it invites use",
    "CNPJ": "deprecated pre-0.76 alias of CNPJField",
    "CPF": "deprecated pre-0.76 alias of CPFField",
    "CPFOrCNPJ": "deprecated pre-0.76 alias of CPFOrCNPJField",
    "PhoneBR": "deprecated pre-0.76 alias of PhoneBRField",
    "AsyncBrokerManager": "deprecated alias of AsyncQueueManager (v0.94.0 rename)",
    "Classifier": "re-export of an ort-vision-sdk class; documented by that project",
    "Detector": "re-export of an ort-vision-sdk class; documented by that project",
    "Segmenter": "re-export of an ort-vision-sdk class; documented by that project",
    "__version__": "module attribute, not part of the callable API surface",
}
"""Symbols intentionally outside the reference, each with its reason.

Every entry is a deliberate exclusion, not a backlog item. Adding a name
here is a decision that needs the reason written down — which is the point
of keeping it as a mapping rather than a bare set.
"""


@pytest.fixture(scope="session")
def rendered_anchors(built_site: Path) -> frozenset[str]:
    """Return the reference page's anchor ids from the built site.

    Args:
        built_site (Path): The built ``site/`` directory, from ``conftest``,
            which rebuilds it whenever a docs input is newer. Reading a stale
            ``site/`` here used to report every symbol added since the last
            local build as missing from the reference.

    Returns:
        frozenset[str]: The trailing component of every
        ``id="tempest_fastapi_sdk..."`` anchor mkdocstrings emitted, which
        is the symbol name as a reader would search for it.
    """
    page = built_site / "reference" / "index.html"
    if not page.exists():  # pragma: no cover - unexpected layout
        pytest.skip("reference page was not built")
    ids = re.findall(r'id="(tempest_fastapi_sdk[^"]*)"', page.read_text())
    return frozenset(anchor.rsplit(".", 1)[-1] for anchor in ids)


@pytest.mark.docs
@pytest.mark.parametrize("module_name", sorted(set(PUBLIC_MODULES)))
def test_public_symbols_are_in_the_reference(
    module_name: str, rendered_anchors: frozenset[str]
) -> None:
    """Every ``__all__`` name is rendered, or explicitly excused.

    Args:
        module_name (str): The module whose ``__all__`` is checked.
        rendered_anchors (frozenset[str]): Symbols the reference renders.
    """
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - extra-dependent
        pytest.skip(f"{module_name} needs an extra absent here: {exc}")

    missing = [
        name
        for name in getattr(module, "__all__", ())
        if name not in rendered_anchors and name not in ALLOWED_ABSENT
    ]
    assert not missing, (
        f"{module_name}.__all__ exports these but the API reference does not "
        f"document them: {missing}. Add a `::: <module>.<name>` entry to "
        f"docs/reference.md (a bare `::: <module>` covers a whole namespace), "
        f"or add the name to ALLOWED_ABSENT with the reason."
    )


@pytest.mark.docs
def test_allowlist_has_no_stale_entries(rendered_anchors: frozenset[str]) -> None:
    """Nothing in the allowlist is actually documented already.

    Keeps the excuse list honest: once a symbol does get a reference entry,
    its exemption has to go, otherwise the list slowly stops describing
    reality.

    Args:
        rendered_anchors (frozenset[str]): Symbols the reference renders.
    """
    stale = sorted(name for name in ALLOWED_ABSENT if name in rendered_anchors)
    assert not stale, (
        f"These names are documented in the reference but still listed in "
        f"ALLOWED_ABSENT: {stale}. Remove their entries."
    )


@pytest.mark.docs
def test_allowlist_entries_carry_a_reason() -> None:
    """Every exemption states why, in prose a reviewer can weigh."""
    empty = sorted(
        name for name, reason in ALLOWED_ABSENT.items() if not reason.strip()
    )
    assert not empty, f"ALLOWED_ABSENT entries without a reason: {empty}"

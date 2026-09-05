"""The provider's ``x-mp-sdk-coverage`` is a third opinion, not the authority.

``spec3.sdk.yaml`` annotates every operation with the official SDKs that
implement it, which is machine-readable provider data about the exact
question :data:`OFFICIAL_SDK_CALLS` answers by hand. The tempting move is
to delete the hand-read inventory and read the annotation instead.

Measured 2026-09-05, that would be wrong: five of the 44 operations the
annotation marks ``python`` have no call site anywhere in ``mercadopago``
3.5.0 — which is the latest release on PyPI, so this is not a stale pin.
The annotation states intent; the sdist is the code that ships.

Offline by construction, like ``test_provenance.py``: nothing here goes to
the network. Re-measuring against what the provider serves today is
``make mercadopago-diff``, which prints any recorded disagreement that has
appeared or disappeared.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Locate the repository root from this file.

    Returns:
        Path: The first ancestor directory holding ``pyproject.toml``.

    Raises:
        RuntimeError: When no ancestor carries ``pyproject.toml``.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("no pyproject.toml above this file")


@pytest.fixture(scope="module")
def overlay() -> object:
    """Import the overlay module the scripts directory holds.

    Returns:
        object: The imported ``mercadopago_overlay`` module.
    """
    scripts = str(_repo_root() / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import mercadopago_overlay

    return mercadopago_overlay


class TestTheCrossCheckIsRecorded:
    """The measurement is in the source, with the date it was taken."""

    def test_the_annotated_variant_is_named(self, overlay: object) -> None:
        """A future reader must be able to re-fetch the same document.

        The v0.276.0 lesson was that guessing a spec filename and getting a
        ``404`` is not evidence of absence. The filename that answers ``200``
        is recorded rather than described.
        """
        url: str = overlay.SDK_COVERAGE_URL  # type: ignore[attr-defined]
        assert url.endswith("/spec3.sdk.yaml")
        assert "mercadopago/openapi" in url

    def test_the_measurement_carries_its_date(self, overlay: object) -> None:
        """A count without a date is not a measurement."""
        date: str = overlay.SDK_COVERAGE_DATE  # type: ignore[attr-defined]
        assert len(date) == 10
        assert date.count("-") == 2

    def test_the_totals_are_internally_consistent(self, overlay: object) -> None:
        """Agreement cannot exceed either side of the comparison."""
        totals: dict[str, int] = overlay.SDK_COVERAGE_TOTALS  # type: ignore[attr-defined]
        assert totals["python"] <= totals["annotated"]
        assert totals["agreeing"] <= totals["python"]
        assert totals["agreeing"] <= totals["ours"]


class TestTheAnnotationIsNotTheAuthority:
    """The recorded disagreements are what stop the inventory being deleted."""

    def test_disagreements_are_recorded_with_a_reason(self, overlay: object) -> None:
        """Each entry says why the SDK does not back the annotation."""
        disagreements: dict[tuple[str, str], str] = overlay.SDK_COVERAGE_DISAGREEMENTS  # type: ignore[attr-defined]
        assert disagreements
        for (method, path), reason in disagreements.items():
            assert method.isupper()
            assert path.startswith("/")
            assert reason.strip()

    def test_disagreements_are_disjoint_from_the_hand_read_inventory(
        self, overlay: object
    ) -> None:
        """A disagreement that we *do* call is a contradiction, not a note.

        This is the assertion that fails if someone adds one of these five
        to ``OFFICIAL_SDK_CALLS`` on the annotation's word alone.
        """
        normalise = overlay.normalise  # type: ignore[attr-defined]
        calls = {
            normalise(method, path)
            for method, path in overlay.OFFICIAL_SDK_CALLS  # type: ignore[attr-defined]
        }
        disagreements = set(overlay.SDK_COVERAGE_DISAGREEMENTS)  # type: ignore[attr-defined]
        assert calls & disagreements == set()

    def test_the_paths_are_already_normalised(self, overlay: object) -> None:
        """Recorded keys are spelled the way the comparison spells them."""
        normalise = overlay.normalise  # type: ignore[attr-defined]
        for method, path in overlay.SDK_COVERAGE_DISAGREEMENTS:  # type: ignore[attr-defined]
            assert normalise(method, path) == (method, path)

    def test_the_chargeback_case_is_the_documented_one(self, overlay: object) -> None:
        """``PUT /v1/chargebacks/{}`` is the entry with a source citation.

        The other four spell nothing in the package; this one sits next to
        two operations the SDK *does* call, which is what makes it the
        readable example in the module docstring.
        """
        disagreements: dict[tuple[str, str], str] = overlay.SDK_COVERAGE_DISAGREEMENTS  # type: ignore[attr-defined]
        assert ("PUT", "/v1/chargebacks/{}") in disagreements
        assert ("GET", "/v1/chargebacks/search") in {
            overlay.normalise(m, p)  # type: ignore[attr-defined]
            for m, p in overlay.OFFICIAL_SDK_CALLS  # type: ignore[attr-defined]
        }


class TestTheDiffScriptReportsIt:
    """The network re-measurement is wired into the target that has network."""

    def test_the_diff_script_imports_the_recorded_set(self) -> None:
        """``make mercadopago-diff`` compares against what is recorded."""
        source = (_repo_root() / "scripts" / "mercadopago_diff.py").read_text(
            encoding="utf-8"
        )
        assert "SDK_COVERAGE_DISAGREEMENTS" in source
        assert "def report_sdk_coverage(" in source

    def test_new_and_vanished_entries_are_both_reported(self) -> None:
        """Drift in either direction has to surface, not just additions."""
        source = (_repo_root() / "scripts" / "mercadopago_diff.py").read_text(
            encoding="utf-8"
        )
        assert "NEW — not recorded in the overlay" in source
        assert "recorded disagreements that are gone" in source

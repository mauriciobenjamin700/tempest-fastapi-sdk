"""Tests for the corrections applied to the vendored Mercado Pago document.

Mercado Pago publishes no OpenAPI document, so unlike OpenPix there is no
upstream that could carry a fix in on its own and retire a correction here.
Every disagreement is therefore pinned: what it changes, what it refuses to
change, and that it stays quiet on a document that no longer needs it.

The evidence behind each correction — the provider's own SDK, and the
unauthenticated probe that tells a missing route from a guarded one — is in
`vendor/mercadopago-evidence.md`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parents[4]
SCRIPTS: str = str(REPO_ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from mercadopago_overlay import (  # noqa: E402
    ADDED_OPERATIONS,
    DEAD_OPERATIONS,
    OFFICIAL_SDK_CALLS,
    PATH_CORRECTIONS,
    apply,
)


def _document(paths: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal document carrying the given paths.

    Args:
        paths (dict[str, Any]): The ``paths`` block.

    Returns:
        dict[str, Any]: A loadable OpenAPI 3 document.
    """
    return {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": paths,
        "components": {"schemas": {}},
    }


class TestEveryCorrectionCarriesItsEvidence:
    """A correction without a reason is a guess with better manners."""

    def test_each_correction_names_the_sdk(self) -> None:
        """The SDK is the authority, so every correction cites it.

        It cites the probe only where the probe applies. `404` is returned
        per method *and* path — measured 2026-08-28, `GET /v1/customers`
        answers 404 while `POST /v1/customers` is the endpoint the SDK
        creates customers with — so a `GET` probe is evidence about `GET`
        and nothing else. The customer correction is a `DELETE`, and rests
        on the SDK alone.
        """
        for correction in PATH_CORRECTIONS:
            assert "resources/" in correction.evidence
            assert correction.wrong != correction.right

    def test_every_removal_is_a_get(self) -> None:
        """Only the verb the probe used may be removed on the probe's word."""
        for dead in DEAD_OPERATIONS:
            assert dead.method == "get"
            assert "404" in dead.evidence

    def test_every_addition_names_the_sdk_call_site(self) -> None:
        """Path and verb are measured; the shape is not, and says so."""
        for operation in ADDED_OPERATIONS:
            assert operation.source.startswith("resources/")
            assert "modelled" in operation.description


class TestPathsTheApiDoesNotRoute:
    """Two paths the vendored document spells wrongly."""

    def test_deleting_a_customer_drops_the_delete_suffix(self) -> None:
        """`DELETE /v1/customers/{id}/delete` answered 404.

        The provider's own SDK calls `DELETE /v1/customers/<id>`, and
        measured 2026-08-28 an unauthenticated `GET /v1/customers/123`
        answers 401 while `/v1/customers/123/delete` answers 404 — the
        second path is not routed at all.
        """
        document = _document(
            {
                "/v1/customers/{id}": {"get": {"responses": {}}},
                "/v1/customers/{id}/delete": {"delete": {"responses": {}}},
            }
        )

        patched, report = apply(document)

        assert "delete" in patched["paths"]["/v1/customers/{id}"]
        assert "/v1/customers/{id}/delete" not in patched["paths"]
        assert "DELETE /v1/customers/{id}/delete -> /v1/customers/{id}" in (
            report.moved_paths
        )

    def test_the_verbs_already_on_the_destination_survive(self) -> None:
        """The reason corrections move one verb, not one path.

        `/v1/customers/{id}` already carries `get` and `put`. An earlier
        version of this overlay moved whole path items and skipped a
        destination that already existed — so this correction silently did
        nothing, and `make mercadopago-diff` still reported the operation
        as unmodelled.
        """
        document = _document(
            {
                "/v1/customers/{id}": {
                    "get": {"responses": {}},
                    "put": {"responses": {}},
                },
                "/v1/customers/{id}/delete": {"delete": {"responses": {}}},
            }
        )

        patched, _ = apply(document)

        assert set(patched["paths"]["/v1/customers/{id}"]) == {
            "get",
            "put",
            "delete",
        }

    def test_searching_invoices_uses_the_search_path(self) -> None:
        """`GET /authorized_payments` answered 404; the search path answers 401."""
        document = _document({"/authorized_payments": {"get": {"responses": {}}}})

        patched, report = apply(document)

        assert "/authorized_payments/search" in patched["paths"]
        assert "/authorized_payments" not in patched["paths"]
        assert report.moved_paths == (
            "GET /authorized_payments -> /authorized_payments/search",
        )


class TestTheOverlayRetiresOnItsOwn:
    """A document that no longer needs a correction does not get one."""

    def test_a_correct_document_is_not_moved(self) -> None:
        """Nothing to move, nothing moved.

        Additions still apply — they are what the SDK says exists, not a
        repair of something this document got wrong — so the assertion is
        about the correction families, not about the whole `paths` block.
        """
        document = _document({"/v1/customers/{id}": {"delete": {"responses": {}}}})

        patched, report = apply(document)

        assert report.moved_paths == ()
        assert report.removed_operations == ()
        assert patched["paths"]["/v1/customers/{id}"] == {"delete": {"responses": {}}}

    def test_a_verb_collision_is_reported_not_resolved(self) -> None:
        """Which of two operations is right is a question about the API."""
        document = _document(
            {
                "/v1/customers/{id}": {"delete": {"responses": {"200": {}}}},
                "/v1/customers/{id}/delete": {"delete": {"responses": {"204": {}}}},
            }
        )

        patched, report = apply(document)

        assert report.moved_paths == ()
        assert report.collisions == ("DELETE /v1/customers/{id} already declared",)
        assert patched["paths"]["/v1/customers/{id}"]["delete"]["responses"] == {
            "200": {}
        }

    def test_apply_leaves_its_input_untouched(self) -> None:
        """The vendored document stays what it is on disk."""
        document = _document({"/authorized_payments": {"get": {"responses": {}}}})

        apply(document)

        assert "/authorized_payments" in document["paths"]


class TestTheGeneratedResult:
    """The corrections reach the client a consumer imports."""

    def test_deleting_a_customer_hits_the_routed_path(self) -> None:
        """The method that could never have worked."""
        import inspect

        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            MercadoPagoClient,
        )

        source = inspect.getsource(MercadoPagoClient.delete_customer)

        assert 'path = f"/v1/customers/{id}"' in source
        assert "/delete" not in source

    def test_searching_invoices_hits_the_routed_path(self) -> None:
        """The collection path answered 404; the search path is the real one."""
        import inspect

        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            MercadoPagoClient,
        )

        methods = [
            getattr(MercadoPagoClient, name)
            for name in dir(MercadoPagoClient)
            if not name.startswith("_") and callable(getattr(MercadoPagoClient, name))
        ]
        sources = [inspect.getsource(method) for method in methods]

        assert any('path = "/authorized_payments/search"' in s for s in sources)
        assert not any('path = "/authorized_payments"' in s for s in sources)


class TestTheSdkIsTheAuthority:
    """Every operation the provider's SDK calls exists in what we generate.

    This is the rule in one assertion. It runs offline against
    `OFFICIAL_SDK_CALLS`, pinned from mercadopago 3.5.0; `make
    mercadopago-diff` reads the current release and reports the difference,
    so a newer SDK becomes work to do rather than silence.

    The converse is deliberately not asserted. The SDK is a thin wrapper
    over the resources most integrations use, and our document carries 82
    operations it never touches — settlement reports, post-purchase claims,
    in-store QR, terminals, wallet connect. Probing those answers 401 or
    403, not 404: silence from the SDK is not denial.
    """

    def _generated_operations(self) -> set[tuple[str, str]]:
        """Read what the overlay hands the generator.

        Returns:
            set[tuple[str, str]]: ``(METHOD, path)`` with every path
            parameter normalised to ``{}``, matching how
            `OFFICIAL_SDK_CALLS` is spelled.
        """
        import re

        import yaml

        document = yaml.safe_load(
            (REPO_ROOT / "vendor" / "mercadopago-openapi.yaml").read_text(
                encoding="utf-8"
            )
        )
        patched, _ = apply(document)
        found: set[tuple[str, str]] = set()
        for path, item in (patched.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            for method in item:
                if method in {"get", "post", "put", "patch", "delete"}:
                    normalised = re.sub(r"\{[^}]*\}", "{}", str(path))
                    found.add((method.upper(), normalised.rstrip("/") or "/"))
        return found

    def test_every_call_the_sdk_makes_is_modelled(self) -> None:
        """Zero in the direction that matters."""
        missing = OFFICIAL_SDK_CALLS - self._generated_operations()

        assert not missing, sorted(missing)

    def test_the_client_exposes_the_operations_that_were_added(self) -> None:
        """The additions reach the class a consumer imports."""
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            MercadoPagoClient,
        )

        for name in (
            "get_authenticated_user",
            "search_advanced_payments",
            "search_chargebacks",
            "list_disbursement_refunds",
            "create_disbursement_refunds",
            "create_disbursement_refund",
            "update_advanced_payment_release_date",
        ):
            assert hasattr(MercadoPagoClient, name), name

    def test_an_unmodelled_shape_stays_a_dict(self) -> None:
        """Path and verb are measured; body and response are not."""
        import inspect

        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            MercadoPagoClient,
        )

        signature = inspect.signature(MercadoPagoClient.create_disbursement_refunds)

        assert signature.return_annotation == "dict[str, Any]"
        assert signature.parameters["body"].annotation == "dict[str, Any]"


class TestOperationsTheApiDoesNotRoute:
    """A method that could never work does not ship."""

    def test_the_dead_gets_are_gone_from_the_client(self) -> None:
        """Three GETs answered 404 where their neighbours answered 401/403."""
        import inspect

        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            MercadoPagoClient,
        )

        sources = [
            inspect.getsource(getattr(MercadoPagoClient, name))
            for name in dir(MercadoPagoClient)
            if not name.startswith("_") and callable(getattr(MercadoPagoClient, name))
        ]

        for path in ('"/stores/{id}"', '"/post-purchase/v1/claims/reasons/'):
            assert not any(f"path = {path}" in source for source in sources), path

        integrator = [s for s in sources if 'path = "/instore/integrator"' in s]

        assert len(integrator) == 1, "only the PATCH survives the removed GET"
        assert '"PATCH"' in integrator[0]

    def test_the_patch_on_a_shared_path_survives(self) -> None:
        """404 is per method: the probe spoke for the GET, not the PATCH."""
        document = _document(
            {
                "/instore/integrator": {
                    "get": {"responses": {}},
                    "patch": {"responses": {}},
                }
            }
        )

        patched, report = apply(document)

        assert set(patched["paths"]["/instore/integrator"]) == {"patch"}
        assert "GET /instore/integrator" in report.removed_operations

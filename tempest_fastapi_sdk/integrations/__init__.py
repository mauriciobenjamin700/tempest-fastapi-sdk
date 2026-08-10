"""Ready-made clients for third-party services.

Everything under here talks to somebody else's API. The grouping is by
**what the third party does** (``payment``, and whatever follows), not by
vendor name, so a service that swaps providers changes one import segment
instead of hunting through a flat namespace.

Each integration is a subpackage that ships the provider's whole surface —
generated from their OpenAPI specification and checked in — next to the
hand-written pieces the specification does not describe. Nothing here is
imported by ``import tempest_fastapi_sdk``; reach for the provider you use.

.. code-block:: python

    from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient

!!! info "Why not generate it in each service"
    `tempest openapi-client` still exists and is the right tool for an API
    the SDK does not ship. What lives here are the integrations common
    enough that every service was running the same generation and
    maintaining the same hand-written layer on top.
"""

from tempest_fastapi_sdk.integrations import payment as payment

__all__: list[str] = ["payment"]

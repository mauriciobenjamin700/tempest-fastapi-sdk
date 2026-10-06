"""Response headers that neutralize a served file as an XSS vector.

The single home of :data:`DEFAULT_STATIC_SECURITY_HEADERS`, kept in a leaf
module with no import of its own so both halves that serve stored files can
read it: :mod:`tempest_fastapi_sdk.api.static` (``HardenedStaticFiles``) and
:mod:`tempest_fastapi_sdk.utils.download` (every download helper). ``utils``
sits below ``api`` in the import graph — ``api`` pulls ``db``, which pulls
``utils`` — so the download helpers cannot import the constant from
``api.static`` without a circular import.
"""

from __future__ import annotations

DEFAULT_STATIC_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-site",
}
"""Headers stamped on every response that serves a stored file.

* ``X-Content-Type-Options: nosniff`` — browsers stop guessing the MIME from
  the bytes, so a polyglot file with a benign extension (HTML+JS uploaded as
  ``.jpg``) is not rendered as HTML on retrieval.
* ``Content-Security-Policy: default-src 'none'; sandbox`` — even if a
  browser renders the file, embedded scripts cannot execute and the sandbox
  blocks forms, top-level navigation and same-origin access.
* ``Cross-Origin-Resource-Policy: same-site`` — bounds the file's
  readability to documents on the same site.
"""


__all__: list[str] = ["DEFAULT_STATIC_SECURITY_HEADERS"]

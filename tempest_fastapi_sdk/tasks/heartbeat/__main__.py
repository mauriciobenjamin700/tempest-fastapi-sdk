"""Entry point of ``python -m tempest_fastapi_sdk.tasks.heartbeat``.

Guarded by ``__name__`` so tooling that imports every module of the
package (the repository's guards walk it with ``pkgutil``) does not run
the checker and exit.
"""

import sys

from tempest_fastapi_sdk.tasks.heartbeat import main

if __name__ == "__main__":
    sys.exit(main())

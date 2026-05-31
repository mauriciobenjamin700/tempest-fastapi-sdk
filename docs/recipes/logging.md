# Logging


`configure_logging` installs a JSON handler on the root logger that emits one-line JSON records carrying the active request ID. `LogUtils` is a thin facade that adds level methods accepting structured `**fields`.

```python
from tempest_fastapi_sdk import LogUtils, configure_logging
from tempest_fastapi_sdk.core import get_request_id

# Imperative — call once during bootstrap.
configure_logging(level="INFO", json_output=True)

# Facade — handy for service-wide singletons.
log = LogUtils("app.users", level="INFO")
log.info("user_created", user_id=str(user.id), email=user.email)
log.warning("login_throttled", ip="1.2.3.4", attempts=5)

try:
    risky()
except RuntimeError:
    log.exception("risky_failed", op="reconcile")  # appends traceback

# Surface the correlation ID outside the log line if needed.
request_id = get_request_id()
```

JSON output (single line — formatted here for readability):

```json
{
  "timestamp": "2026-05-16T20:14:33.412+00:00Z",
  "level": "INFO",
  "logger": "app.users",
  "message": "user_created",
  "request_id": "d83e4b0c-7c2f-4bd6-aaa1-7d4f6cf5e5e9",
  "user_id": "9c1a5b2d-...",
  "email": "ana@example.com"
}
```

The middleware accepts a custom header name (`RequestIDMiddleware(app, header_name="X-Correlation-ID")`); the same header is echoed back on every response.


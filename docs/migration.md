# Migration guide

Breaking-change walkthroughs grouped by minor release. Stick to the version that matches what you're upgrading **from**.


0.8.0 renames every field on `ServerSettings`, extracts log fields to a new `LogSettings` mixin, and adds eleven other primitives. The renames are the only **breaking** changes — every new primitive is opt-in.

#### 1. Rename env vars

| Old | New | Mixin |
| --- | --- | --- |
| `HOST` | `SERVER_HOST` | `ServerSettings` |
| `PORT` | `SERVER_PORT` | `ServerSettings` |
| `DEBUG` | `SERVER_DEBUG` | `ServerSettings` |
| *(new)* | `SERVER_RELOAD` | `ServerSettings` |
| `LOG_LEVEL` | `LOG_LEVEL` | **moved to** `LogSettings` |
| `LOG_JSON` | `LOG_JSON` | **moved to** `LogSettings` |

Mechanical `sed` on every `.env` / `docker-compose.yml` / deployment manifest:

```bash
sed -i \
  -e 's/^HOST=/SERVER_HOST=/' \
  -e 's/^PORT=/SERVER_PORT=/' \
  -e 's/^DEBUG=/SERVER_DEBUG=/' \
  .env .env.example .env.test
```

`LOG_LEVEL` and `LOG_JSON` keep their names — only the mixin moves.

#### 2. Rename code references

```bash
# `settings.HOST` → `settings.SERVER_HOST`, same for PORT/DEBUG
grep -rn "settings\.\(HOST\|PORT\|DEBUG\)\b" src/ tests/
```

Replace each match with the `SERVER_*` form. If a service was using the
old `settings.DEBUG` flag for application-level debug behavior, switch
to `settings.SERVER_DEBUG`; if it was only being read for uvicorn
auto-reload, switch to `settings.SERVER_RELOAD`.

#### 3. Mix `LogSettings` into the project `Settings`

```diff
 from tempest_fastapi_sdk import (
     BaseAppSettings,
     CORSSettings,
     DatabaseSettings,
     JWTSettings,
+    LogSettings,
     RabbitMQSettings,
     RedisSettings,
     ServerSettings,
 )


 class Settings(
     ServerSettings,
+    LogSettings,
     DatabaseSettings,
     RedisSettings,
     RabbitMQSettings,
     JWTSettings,
     CORSSettings,
     BaseAppSettings,
 ):
     ...
```

Skip this step if the service never read `settings.LOG_LEVEL` /
`settings.LOG_JSON` — `configure_logging` accepts the values as
keyword arguments directly.

#### 4. (Optional) Adopt the new primitives

Pick what fits. None of these are required.

- Replace the hand-written `src/server.py` `uvicorn.run(...)` with
  [`run_server(...)`](#programmatic-server-entry-point-recipe).
- Replace the hand-written `get_current_user` with
  [`make_jwt_user_dependency(tokens, load_user)`](#jwt-bearer--current-user--role-dependencies-recipe).
- Move `SMTP_*` / `UPLOAD_*` / `TOKEN_SECRET` / `VAPID_*` /
  `TASKIQ_*` fields out of the project's `Settings` and onto the
  matching SDK mixin ([Settings mixins composition](#settings-mixins-composition-recipe)).
- Adopt the
  [`Outbox dispatcher pattern`](#outbox-dispatcher-pattern-recipe) if
  you already write side-effects from the same transaction as your
  domain rows.

#### 5. Verify

```bash
uv sync                      # picks up new pyproject deps
uv run pytest -q             # full suite
uv run ruff check src tests  # confirm no `HOST`/`PORT`/`DEBUG` references slipped
```

If `pytest` fails with a Pydantic `ValidationError` referencing
`HOST` / `PORT` / `DEBUG`, an env var was not renamed (look at the
process environment or `.env`).

---


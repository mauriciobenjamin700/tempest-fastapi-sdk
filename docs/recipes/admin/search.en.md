---
description: "Use for admin global search over `search_fields`; covers the per-model limit, what the search respects and the query limits."
---

# Global search

Use to search a term across every model with `search_fields` from the search box at the top of the panel.

Part of the [Admin site](../admin.md) recipe.

When some model the operator can see has `search_fields`, the panel's top
bar gains a search box. It goes to `GET /admin/search?q=`, which looks for
the term in **every** model with `search_fields`, with the list search's
own condition (each word an `ILIKE`, in any of the fields), and shows up
to `ADMIN_SEARCH_RESULTS_PER_MODEL` (5) rows per model, each linking to its
detail. When there are more, the group's **See all** opens the model's
list already filtered with `?q=`. The cap is adjustable:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import UserModelAuthBackend, make_admin_router

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()

app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET,
        search_results_per_model=10,
    ),
)
```

The search honours what the rest of the panel honours: a model without
`VIEW` in the `access_policy` is left out, and each model is queried with
its own `row_scope` — a row outside the scope does not show. A model with
a `repository_class` is queried through the repository's `paginate` (one
`COUNT` per model), with the page capped at its `max_page_size`. An
empty term (or only spaces) renders the page without running a query, and a
term longer than `ADMIN_SEARCH_MAX_QUERY_LENGTH` (200) characters or with
more than `ADMIN_SEARCH_MAX_TERMS` (8) words answers `400` with a notice,
also without a query — each word becomes one `ILIKE` per field of every
model.

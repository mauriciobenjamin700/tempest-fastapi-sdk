# `tempest_fastapi_sdk.schemas`

Os schemas Pydantic base: `BaseSchema`, paginação, envelope de resposta e filtros.

[« Admin, dados e API](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api.md)

---

- [`tempest_fastapi_sdk.schemas.base.BaseSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.base.BaseSchema)
- [`tempest_fastapi_sdk.schemas.base.BaseSchema.to_dict`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.base.BaseSchema.to_dict)
- [`tempest_fastapi_sdk.schemas.base.BaseSchema.to_json`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.base.BaseSchema.to_json)
- [`tempest_fastapi_sdk.schemas.response.BaseResponseSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.response.BaseResponseSchema)
- [`tempest_fastapi_sdk.schemas.pagination.DEFAULT_MAX_PAGE_SIZE`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.DEFAULT_MAX_PAGE_SIZE)
- [`tempest_fastapi_sdk.schemas.pagination.DEFAULT_MAX_CURSOR_LIMIT`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.DEFAULT_MAX_CURSOR_LIMIT)
- [`tempest_fastapi_sdk.schemas.pagination.BasePaginationFilterSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.BasePaginationFilterSchema)
- [`tempest_fastapi_sdk.schemas.pagination.BasePaginationFilterSchema.get_conditions`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.BasePaginationFilterSchema.get_conditions)
- [`tempest_fastapi_sdk.schemas.pagination.BasePaginationFilterSchema.get_pagination_conditions`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.BasePaginationFilterSchema.get_pagination_conditions)
- [`tempest_fastapi_sdk.schemas.pagination.BasePaginationSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.BasePaginationSchema)
- [`tempest_fastapi_sdk.schemas.pagination.CursorPaginationFilterSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.CursorPaginationFilterSchema)
- [`tempest_fastapi_sdk.schemas.pagination.CursorPaginationFilterSchema.get_conditions`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.CursorPaginationFilterSchema.get_conditions)
- [`tempest_fastapi_sdk.schemas.pagination.CursorPaginationFilterSchema.get_pagination_conditions`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.CursorPaginationFilterSchema.get_pagination_conditions)
- [`tempest_fastapi_sdk.schemas.pagination.CursorPaginationSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.CursorPaginationSchema)
- [`tempest_fastapi_sdk.schemas.pagination.SyncFilterSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.SyncFilterSchema)
- [`tempest_fastapi_sdk.schemas.search.DEFAULT_MAX_SEARCH_TERM`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.search.DEFAULT_MAX_SEARCH_TERM)
- [`tempest_fastapi_sdk.schemas.search.BaseSearchSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.search.BaseSearchSchema)
- [`tempest_fastapi_sdk.schemas.pagination.SyncPaginationSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.SyncPaginationSchema)
- [`tempest_fastapi_sdk.schemas.logs.LogEntrySchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.logs.LogEntrySchema)
- [`tempest_fastapi_sdk.schemas.logs.LogFilesClearedSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.logs.LogFilesClearedSchema)
- [`tempest_fastapi_sdk.schemas.errors.ErrorResponseSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.errors.ErrorResponseSchema)
- [`tempest_fastapi_sdk.schemas.link_headers.build_pagination_link_header`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.link_headers.build_pagination_link_header)
- [`tempest_fastapi_sdk.schemas.pagination.CompactPaginationFilterSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.CompactPaginationFilterSchema)
- [`tempest_fastapi_sdk.schemas.pagination.CompactPaginationSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.CompactPaginationSchema)
- [`tempest_fastapi_sdk.schemas.pagination.decode_cursor`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.decode_cursor)
- [`tempest_fastapi_sdk.schemas.pagination.encode_cursor`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/schemas/#tempest_fastapi_sdk.schemas.pagination.encode_cursor)


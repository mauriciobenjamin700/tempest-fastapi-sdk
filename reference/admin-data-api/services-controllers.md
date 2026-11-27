# `tempest_fastapi_sdk.services`, `tempest_fastapi_sdk.controllers`

As classes base da camada de serviço e de controller, e o mixin de serviço com arquivo armazenado.

[« Admin, dados e API](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api.md)

---

- [`tempest_fastapi_sdk.services.base.BaseService`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.base.BaseService)
- [`tempest_fastapi_sdk.services.base.BaseService.get_by_id`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.base.BaseService.get_by_id)
- [`tempest_fastapi_sdk.services.base.BaseService.get_or_none`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.base.BaseService.get_or_none)
- [`tempest_fastapi_sdk.services.base.BaseService.list`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.base.BaseService.list)
- [`tempest_fastapi_sdk.services.base.BaseService.paginate`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.base.BaseService.paginate)
- [`tempest_fastapi_sdk.services.base.BaseService.count`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.base.BaseService.count)
- [`tempest_fastapi_sdk.services.base.BaseService.exists`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.base.BaseService.exists)
- [`tempest_fastapi_sdk.services.base.BaseService.update`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.base.BaseService.update)
- [`tempest_fastapi_sdk.services.base.BaseService.delete`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.base.BaseService.delete)
- [`tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin)
- [`tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin.set_file`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin.set_file)
- [`tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin.clear_file`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin.clear_file)
- [`tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin.file_url`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin.file_url)
- [`tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin.file_urls`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin.file_urls)
- [`tempest_fastapi_sdk.controllers.base.Controller`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.controllers.base.Controller)
- [`tempest_fastapi_sdk.controllers.base.BaseController`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.controllers.base.BaseController)
- [`tempest_fastapi_sdk.controllers.base.BaseController.get_by_id`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.controllers.base.BaseController.get_by_id)
- [`tempest_fastapi_sdk.controllers.base.BaseController.list`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.controllers.base.BaseController.list)
- [`tempest_fastapi_sdk.controllers.base.BaseController.paginate`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.controllers.base.BaseController.paginate)
- [`tempest_fastapi_sdk.controllers.base.BaseController.count`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.controllers.base.BaseController.count)
- [`tempest_fastapi_sdk.controllers.base.BaseController.update`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.controllers.base.BaseController.update)
- [`tempest_fastapi_sdk.controllers.base.BaseController.delete`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.controllers.base.BaseController.delete)
- [`tempest_fastapi_sdk.services.file_mixin.SupportsPresign`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.SupportsPresign)
- [`tempest_fastapi_sdk.services.file_mixin.SupportsPresign.presigned_get_url`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.SupportsPresign.presigned_get_url)
- [`tempest_fastapi_sdk.services.file_mixin.SupportsPresign.presigned_get_urls`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.SupportsPresign.presigned_get_urls)
- [`tempest_fastapi_sdk.services.file_mixin.SupportsUpload`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.SupportsUpload)
- [`tempest_fastapi_sdk.services.file_mixin.SupportsUpload.replace`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.SupportsUpload.replace)
- [`tempest_fastapi_sdk.services.file_mixin.SupportsUpload.delete`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api/services-controllers/#tempest_fastapi_sdk.services.file_mixin.SupportsUpload.delete)


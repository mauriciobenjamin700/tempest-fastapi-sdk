# `tempest_fastapi_sdk.openapi`

Gerar schemas Pydantic e um cliente HTTP tipado a partir de um documento OpenAPI.

[« Geração de cliente OpenAPI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi.md)

---

- [`tempest_fastapi_sdk.openapi.generate.generate_integration`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.generate.generate_integration)
- [`tempest_fastapi_sdk.openapi.generate.GenerationResult`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.generate.GenerationResult)
- [`tempest_fastapi_sdk.openapi.generate.default_output_dir`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.generate.default_output_dir)
- [`tempest_fastapi_sdk.openapi.loader.load_spec`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.loader.load_spec)
- [`tempest_fastapi_sdk.openapi.loader.SpecError`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.loader.SpecError)
- [`tempest_fastapi_sdk.openapi.parse.parse_spec`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.parse.parse_spec)
- [`tempest_fastapi_sdk.openapi.emit_schemas.emit_schemas`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.emit_schemas.emit_schemas)
- [`tempest_fastapi_sdk.openapi.emit_client.emit_client`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.emit_client.emit_client)
- [`tempest_fastapi_sdk.openapi.ir.SpecIR`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.SpecIR)
- [`tempest_fastapi_sdk.openapi.ir.SchemaIR`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.SchemaIR)
- [`tempest_fastapi_sdk.openapi.ir.SchemaIR.needs_populate_by_name`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.SchemaIR.needs_populate_by_name)
- [`tempest_fastapi_sdk.openapi.ir.SchemaIR.config_arguments`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.SchemaIR.config_arguments)
- [`tempest_fastapi_sdk.openapi.ir.FieldIR`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.FieldIR)
- [`tempest_fastapi_sdk.openapi.ir.FieldIR.has_metadata`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.FieldIR.has_metadata)
- [`tempest_fastapi_sdk.openapi.ir.ClientIR`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.ClientIR)
- [`tempest_fastapi_sdk.openapi.ir.OperationIR`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.OperationIR)
- [`tempest_fastapi_sdk.openapi.ir.OperationIR.path_parameters`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.OperationIR.path_parameters)
- [`tempest_fastapi_sdk.openapi.ir.OperationIR.query_parameters`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.OperationIR.query_parameters)
- [`tempest_fastapi_sdk.openapi.ir.OperationIR.header_parameters`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.OperationIR.header_parameters)
- [`tempest_fastapi_sdk.openapi.ir.OperationIR.form_parameters`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.OperationIR.form_parameters)
- [`tempest_fastapi_sdk.openapi.ir.OperationIR.file_parameters`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.OperationIR.file_parameters)
- [`tempest_fastapi_sdk.openapi.ir.ParameterIR`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.ir.ParameterIR)
- [`tempest_fastapi_sdk.openapi.loader.parse_header_options`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.loader.parse_header_options)
- [`tempest_fastapi_sdk.openapi.generate.suggest_client_class`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi/openapi/#tempest_fastapi_sdk.openapi.generate.suggest_client_class)

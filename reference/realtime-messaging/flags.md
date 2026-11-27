# `tempest_fastapi_sdk.flags`

Feature flags: o serviço, os backends (memória, env, Redis, composto) e a dependência do FastAPI.

[« Tempo real e mensageria](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging.md)

---

- [`tempest_fastapi_sdk.flags.service.FeatureFlags`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.service.FeatureFlags)
- [`tempest_fastapi_sdk.flags.service.FeatureFlags.is_enabled`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.service.FeatureFlags.is_enabled)
- [`tempest_fastapi_sdk.flags.service.FeatureFlags.enable`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.service.FeatureFlags.enable)
- [`tempest_fastapi_sdk.flags.service.FeatureFlags.disable`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.service.FeatureFlags.disable)
- [`tempest_fastapi_sdk.flags.service.FeatureFlags.set`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.service.FeatureFlags.set)
- [`tempest_fastapi_sdk.flags.service.FeatureFlags.all`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.service.FeatureFlags.all)
- [`tempest_fastapi_sdk.flags.backends.FeatureFlagBackend`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.FeatureFlagBackend)
- [`tempest_fastapi_sdk.flags.backends.FeatureFlagBackend.get`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.FeatureFlagBackend.get)
- [`tempest_fastapi_sdk.flags.backends.FeatureFlagBackend.set`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.FeatureFlagBackend.set)
- [`tempest_fastapi_sdk.flags.backends.FeatureFlagBackend.all`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.FeatureFlagBackend.all)
- [`tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend)
- [`tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend.get`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend.get)
- [`tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend.set`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend.set)
- [`tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend.all`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend.all)
- [`tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend)
- [`tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend.get`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend.get)
- [`tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend.set`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend.set)
- [`tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend.all`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend.all)
- [`tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend)
- [`tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend.get`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend.get)
- [`tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend.set`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend.set)
- [`tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend.all`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend.all)
- [`tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend)
- [`tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend.get`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend.get)
- [`tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend.set`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend.set)
- [`tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend.all`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend.all)
- [`tempest_fastapi_sdk.flags.dependencies.make_flag_dependency`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.dependencies.make_flag_dependency)
- [`tempest_fastapi_sdk.flags.backends.coerce_flag`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags/#tempest_fastapi_sdk.flags.backends.coerce_flag)


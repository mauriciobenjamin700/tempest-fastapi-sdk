"""Feature flags — env / Redis backends, a service and a route guard."""

from tempest_fastapi_sdk.flags.backends import (
    CompositeFeatureFlagBackend as CompositeFeatureFlagBackend,
)
from tempest_fastapi_sdk.flags.backends import (
    EnvFeatureFlagBackend as EnvFeatureFlagBackend,
)
from tempest_fastapi_sdk.flags.backends import (
    FeatureFlagBackend as FeatureFlagBackend,
)
from tempest_fastapi_sdk.flags.backends import (
    MemoryFeatureFlagBackend as MemoryFeatureFlagBackend,
)
from tempest_fastapi_sdk.flags.backends import (
    RedisFeatureFlagBackend as RedisFeatureFlagBackend,
)
from tempest_fastapi_sdk.flags.backends import (
    coerce_flag as coerce_flag,
)
from tempest_fastapi_sdk.flags.dependencies import (
    make_flag_dependency as make_flag_dependency,
)
from tempest_fastapi_sdk.flags.service import FeatureFlags as FeatureFlags

__all__: list[str] = [
    "CompositeFeatureFlagBackend",
    "EnvFeatureFlagBackend",
    "FeatureFlagBackend",
    "FeatureFlags",
    "MemoryFeatureFlagBackend",
    "RedisFeatureFlagBackend",
    "coerce_flag",
    "make_flag_dependency",
]

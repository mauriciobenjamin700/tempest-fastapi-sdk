"""Shared utility helpers exposed at the module level.

The feature-rich helpers each require an optional extra
(:class:`PasswordUtils` → ``[auth]``, :class:`JWTUtils` → ``[auth]``,
:class:`EmailUtils` → ``[email]``, :class:`UploadUtils` → ``[upload]``,
:class:`MetricsUtils` → ``[metrics]``). The missing dependency is
deferred until first instantiation, so ``import tempest_fastapi_sdk``
keeps working when only a subset of extras is installed; the
``ImportError`` is raised with a clear hint the moment the helper is
actually constructed.
"""

from tempest_fastapi_sdk.utils.client_ip import (
    get_client_ip as get_client_ip,
)
from tempest_fastapi_sdk.utils.client_ip import (
    get_client_ip_from_scope as get_client_ip_from_scope,
)
from tempest_fastapi_sdk.utils.currency import CENT as CENT
from tempest_fastapi_sdk.utils.currency import HUNDRED as HUNDRED
from tempest_fastapi_sdk.utils.currency import (
    format_currency_br as format_currency_br,
)
from tempest_fastapi_sdk.utils.currency import (
    format_percent_br as format_percent_br,
)
from tempest_fastapi_sdk.utils.currency import (
    format_quantity_br as format_quantity_br,
)
from tempest_fastapi_sdk.utils.currency import (
    parse_currency_br as parse_currency_br,
)
from tempest_fastapi_sdk.utils.currency import (
    quantize_money as quantize_money,
)
from tempest_fastapi_sdk.utils.datetime import to_utc as to_utc
from tempest_fastapi_sdk.utils.datetime import utcnow as utcnow
from tempest_fastapi_sdk.utils.dict import modify_dict as modify_dict
from tempest_fastapi_sdk.utils.download import (
    DownloadUtils as DownloadUtils,
)
from tempest_fastapi_sdk.utils.download import (
    build_content_disposition as build_content_disposition,
)
from tempest_fastapi_sdk.utils.email import BulkEmailReport as BulkEmailReport
from tempest_fastapi_sdk.utils.email import EmailUtils as EmailUtils
from tempest_fastapi_sdk.utils.email import FailedRecipient as FailedRecipient
from tempest_fastapi_sdk.utils.fields import (
    CentsField as CentsField,
)
from tempest_fastapi_sdk.utils.fields import (
    DecimalPercentField as DecimalPercentField,
)
from tempest_fastapi_sdk.utils.fields import (
    DecimalRatioField as DecimalRatioField,
)
from tempest_fastapi_sdk.utils.fields import (
    HexColorField as HexColorField,
)
from tempest_fastapi_sdk.utils.fields import (
    LatitudeField as LatitudeField,
)
from tempest_fastapi_sdk.utils.fields import (
    LocaleField as LocaleField,
)
from tempest_fastapi_sdk.utils.fields import (
    LongitudeField as LongitudeField,
)
from tempest_fastapi_sdk.utils.fields import (
    NonEmptyStrField as NonEmptyStrField,
)
from tempest_fastapi_sdk.utils.fields import (
    NonNegativeFloatField as NonNegativeFloatField,
)
from tempest_fastapi_sdk.utils.fields import (
    NonNegativeIntField as NonNegativeIntField,
)
from tempest_fastapi_sdk.utils.fields import (
    PercentField as PercentField,
)
from tempest_fastapi_sdk.utils.fields import (
    PortField as PortField,
)
from tempest_fastapi_sdk.utils.fields import (
    PositiveFloatField as PositiveFloatField,
)
from tempest_fastapi_sdk.utils.fields import (
    PositiveIntField as PositiveIntField,
)
from tempest_fastapi_sdk.utils.fields import (
    PriceField as PriceField,
)
from tempest_fastapi_sdk.utils.fields import (
    RatingField as RatingField,
)
from tempest_fastapi_sdk.utils.fields import (
    RatioField as RatioField,
)
from tempest_fastapi_sdk.utils.fields import (
    SignedDecimalRatioField as SignedDecimalRatioField,
)
from tempest_fastapi_sdk.utils.fields import (
    SlugField as SlugField,
)
from tempest_fastapi_sdk.utils.file_store import FileStoreUtils as FileStoreUtils
from tempest_fastapi_sdk.utils.forms import form_encode as form_encode
from tempest_fastapi_sdk.utils.http_client import (
    REQUEST_ID_HEADER as REQUEST_ID_HEADER,
)
from tempest_fastapi_sdk.utils.http_client import (
    CircuitOpenError as CircuitOpenError,
)
from tempest_fastapi_sdk.utils.http_client import (
    HTTPClient as HTTPClient,
)
from tempest_fastapi_sdk.utils.http_client import (
    RetryPolicy as RetryPolicy,
)
from tempest_fastapi_sdk.utils.jwt import JWTUtils as JWTUtils
from tempest_fastapi_sdk.utils.locations import (
    UF as UF,
)
from tempest_fastapi_sdk.utils.locations import (
    ChoiceBR as ChoiceBR,
)
from tempest_fastapi_sdk.utils.locations import (
    CityBR as CityBR,
)
from tempest_fastapi_sdk.utils.locations import (
    CityNameField as CityNameField,
)
from tempest_fastapi_sdk.utils.locations import (
    Region as Region,
)
from tempest_fastapi_sdk.utils.locations import (
    StateBR as StateBR,
)
from tempest_fastapi_sdk.utils.locations import (
    UFField as UFField,
)
from tempest_fastapi_sdk.utils.locations import (
    cities_by_uf as cities_by_uf,
)
from tempest_fastapi_sdk.utils.locations import (
    city_choices as city_choices,
)
from tempest_fastapi_sdk.utils.locations import (
    get_state as get_state,
)
from tempest_fastapi_sdk.utils.locations import (
    is_valid_city as is_valid_city,
)
from tempest_fastapi_sdk.utils.locations import (
    is_valid_uf as is_valid_uf,
)
from tempest_fastapi_sdk.utils.locations import (
    list_states as list_states,
)
from tempest_fastapi_sdk.utils.locations import (
    normalize_city as normalize_city,
)
from tempest_fastapi_sdk.utils.locations import (
    normalize_uf as normalize_uf,
)
from tempest_fastapi_sdk.utils.locations import (
    region_choices as region_choices,
)
from tempest_fastapi_sdk.utils.locations import (
    states_by_region as states_by_region,
)
from tempest_fastapi_sdk.utils.locations import (
    uf_choices as uf_choices,
)
from tempest_fastapi_sdk.utils.log import LogUtils as LogUtils
from tempest_fastapi_sdk.utils.metrics import (
    CPUMetrics as CPUMetrics,
)
from tempest_fastapi_sdk.utils.metrics import (
    DiskMetrics as DiskMetrics,
)
from tempest_fastapi_sdk.utils.metrics import (
    GPUMetrics as GPUMetrics,
)
from tempest_fastapi_sdk.utils.metrics import (
    MemoryMetrics as MemoryMetrics,
)
from tempest_fastapi_sdk.utils.metrics import (
    MetricsUtils as MetricsUtils,
)
from tempest_fastapi_sdk.utils.metrics import (
    SystemMetrics as SystemMetrics,
)
from tempest_fastapi_sdk.utils.opaque_token import (
    generate_opaque_token as generate_opaque_token,
)
from tempest_fastapi_sdk.utils.opaque_token import (
    hash_opaque_token as hash_opaque_token,
)
from tempest_fastapi_sdk.utils.opaque_token import (
    verify_opaque_token as verify_opaque_token,
)
from tempest_fastapi_sdk.utils.password import PasswordUtils as PasswordUtils
from tempest_fastapi_sdk.utils.regex import (
    CEP as CEP,
)
from tempest_fastapi_sdk.utils.regex import (
    CEP_PATTERN as CEP_PATTERN,
)
from tempest_fastapi_sdk.utils.regex import (
    CNPJ as CNPJ,
)
from tempest_fastapi_sdk.utils.regex import (
    CNPJ_PATTERN as CNPJ_PATTERN,
)
from tempest_fastapi_sdk.utils.regex import (
    CPF as CPF,
)
from tempest_fastapi_sdk.utils.regex import (
    CPF_CNPJ_PATTERN as CPF_CNPJ_PATTERN,
)
from tempest_fastapi_sdk.utils.regex import (
    CPF_PATTERN as CPF_PATTERN,
)
from tempest_fastapi_sdk.utils.regex import (
    PHONE_BR_PATTERN as PHONE_BR_PATTERN,
)
from tempest_fastapi_sdk.utils.regex import (
    CEPField as CEPField,
)
from tempest_fastapi_sdk.utils.regex import (
    CNPJField as CNPJField,
)
from tempest_fastapi_sdk.utils.regex import (
    CPFField as CPFField,
)
from tempest_fastapi_sdk.utils.regex import (
    CPFOrCNPJ as CPFOrCNPJ,
)
from tempest_fastapi_sdk.utils.regex import (
    CPFOrCNPJField as CPFOrCNPJField,
)
from tempest_fastapi_sdk.utils.regex import (
    MobilePhoneBRField as MobilePhoneBRField,
)
from tempest_fastapi_sdk.utils.regex import (
    PhoneBR as PhoneBR,
)
from tempest_fastapi_sdk.utils.regex import (
    PhoneBRField as PhoneBRField,
)
from tempest_fastapi_sdk.utils.regex import (
    PhoneNumberBR as PhoneNumberBR,
)
from tempest_fastapi_sdk.utils.regex import (
    PixKeyField as PixKeyField,
)
from tempest_fastapi_sdk.utils.regex import (
    PixKeyType as PixKeyType,
)
from tempest_fastapi_sdk.utils.regex import (
    detect_pix_key_type as detect_pix_key_type,
)
from tempest_fastapi_sdk.utils.regex import (
    is_valid_cep as is_valid_cep,
)
from tempest_fastapi_sdk.utils.regex import (
    is_valid_cnpj as is_valid_cnpj,
)
from tempest_fastapi_sdk.utils.regex import (
    is_valid_cpf as is_valid_cpf,
)
from tempest_fastapi_sdk.utils.regex import (
    is_valid_cpf_cnpj as is_valid_cpf_cnpj,
)
from tempest_fastapi_sdk.utils.regex import (
    is_valid_mobile_phone_br as is_valid_mobile_phone_br,
)
from tempest_fastapi_sdk.utils.regex import (
    is_valid_phone_br as is_valid_phone_br,
)
from tempest_fastapi_sdk.utils.regex import (
    is_valid_pix_key as is_valid_pix_key,
)
from tempest_fastapi_sdk.utils.regex import (
    normalize_cep as normalize_cep,
)
from tempest_fastapi_sdk.utils.regex import (
    normalize_cnpj as normalize_cnpj,
)
from tempest_fastapi_sdk.utils.regex import (
    normalize_cpf as normalize_cpf,
)
from tempest_fastapi_sdk.utils.regex import (
    normalize_cpf_cnpj as normalize_cpf_cnpj,
)
from tempest_fastapi_sdk.utils.regex import (
    normalize_mobile_phone_br as normalize_mobile_phone_br,
)
from tempest_fastapi_sdk.utils.regex import (
    normalize_phone_br as normalize_phone_br,
)
from tempest_fastapi_sdk.utils.regex import (
    normalize_pix_key as normalize_pix_key,
)
from tempest_fastapi_sdk.utils.regex import (
    only_digits as only_digits,
)
from tempest_fastapi_sdk.utils.regex import (
    parse_phone_br as parse_phone_br,
)
from tempest_fastapi_sdk.utils.storage_backends import (
    LocalUploadStorage as LocalUploadStorage,
)
from tempest_fastapi_sdk.utils.storage_backends import (
    MinIOUploadStorage as MinIOUploadStorage,
)
from tempest_fastapi_sdk.utils.storage_backends import (
    UploadResult as UploadResult,
)
from tempest_fastapi_sdk.utils.storage_backends import (
    UploadStorage as UploadStorage,
)
from tempest_fastapi_sdk.utils.throttle import (
    AttemptThrottle as AttemptThrottle,
)
from tempest_fastapi_sdk.utils.throttle import (
    ThrottleBackend as ThrottleBackend,
)
from tempest_fastapi_sdk.utils.throttle import (
    ThrottleStatus as ThrottleStatus,
)
from tempest_fastapi_sdk.utils.token_types import (
    ACCESS_TOKEN_TYPE as ACCESS_TOKEN_TYPE,
)
from tempest_fastapi_sdk.utils.token_types import (
    MFA_TOKEN_TYPE as MFA_TOKEN_TYPE,
)
from tempest_fastapi_sdk.utils.token_types import (
    REFRESH_TOKEN_TYPE as REFRESH_TOKEN_TYPE,
)
from tempest_fastapi_sdk.utils.token_types import (
    token_type_allowed as token_type_allowed,
)
from tempest_fastapi_sdk.utils.totp import TOTPHelper as TOTPHelper
from tempest_fastapi_sdk.utils.upload import (
    UploadUtils as UploadUtils,
)
from tempest_fastapi_sdk.utils.upload import (
    sniff_mime as sniff_mime,
)

__all__: list[str] = [
    "ACCESS_TOKEN_TYPE",
    "CENT",
    "CEP",
    "CEP_PATTERN",
    "CNPJ",
    "CNPJ_PATTERN",
    "CPF",
    "CPF_CNPJ_PATTERN",
    "CPF_PATTERN",
    "HUNDRED",
    "MFA_TOKEN_TYPE",
    "PHONE_BR_PATTERN",
    "REFRESH_TOKEN_TYPE",
    "REQUEST_ID_HEADER",
    "UF",
    "AttemptThrottle",
    "BulkEmailReport",
    "CEPField",
    "CNPJField",
    "CPFField",
    "CPFOrCNPJ",
    "CPFOrCNPJField",
    "CPUMetrics",
    "CentsField",
    "ChoiceBR",
    "CircuitOpenError",
    "CityBR",
    "CityNameField",
    "DecimalPercentField",
    "DecimalRatioField",
    "DiskMetrics",
    "DownloadUtils",
    "EmailUtils",
    "FailedRecipient",
    "FileStoreUtils",
    "GPUMetrics",
    "HTTPClient",
    "HexColorField",
    "JWTUtils",
    "LatitudeField",
    "LocalUploadStorage",
    "LocaleField",
    "LogUtils",
    "LongitudeField",
    "MemoryMetrics",
    "MetricsUtils",
    "MinIOUploadStorage",
    "MobilePhoneBRField",
    "NonEmptyStrField",
    "NonNegativeFloatField",
    "NonNegativeIntField",
    "PasswordUtils",
    "PercentField",
    "PhoneBR",
    "PhoneBRField",
    "PhoneNumberBR",
    "PixKeyField",
    "PixKeyType",
    "PortField",
    "PositiveFloatField",
    "PositiveIntField",
    "PriceField",
    "RatingField",
    "RatioField",
    "Region",
    "RetryPolicy",
    "SignedDecimalRatioField",
    "SlugField",
    "StateBR",
    "SystemMetrics",
    "TOTPHelper",
    "ThrottleBackend",
    "ThrottleStatus",
    "UFField",
    "UploadResult",
    "UploadStorage",
    "UploadUtils",
    "build_content_disposition",
    "cities_by_uf",
    "city_choices",
    "detect_pix_key_type",
    "form_encode",
    "format_currency_br",
    "format_percent_br",
    "format_quantity_br",
    "generate_opaque_token",
    "get_client_ip",
    "get_client_ip_from_scope",
    "get_state",
    "hash_opaque_token",
    "is_valid_cep",
    "is_valid_city",
    "is_valid_cnpj",
    "is_valid_cpf",
    "is_valid_cpf_cnpj",
    "is_valid_mobile_phone_br",
    "is_valid_phone_br",
    "is_valid_pix_key",
    "is_valid_uf",
    "list_states",
    "modify_dict",
    "normalize_cep",
    "normalize_city",
    "normalize_cnpj",
    "normalize_cpf",
    "normalize_cpf_cnpj",
    "normalize_mobile_phone_br",
    "normalize_phone_br",
    "normalize_pix_key",
    "normalize_uf",
    "only_digits",
    "parse_currency_br",
    "parse_phone_br",
    "quantize_money",
    "region_choices",
    "sniff_mime",
    "states_by_region",
    "to_utc",
    "token_type_allowed",
    "uf_choices",
    "utcnow",
    "verify_opaque_token",
]

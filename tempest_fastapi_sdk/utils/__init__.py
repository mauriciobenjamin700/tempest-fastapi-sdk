"""Shared utility helpers exposed at the module level.

The feature-rich helpers each require an optional extra
(:class:`PasswordUtils` → ``[auth]``, :class:`JWTUtils` → ``[auth]``,
:class:`EmailUtils` → ``[email]``, :class:`UploadUtils` → ``[upload]``,
:class:`MetricsUtils` → ``[metrics]``). The missing dependency is
deferred until first instantiation, so ``import tempest_fastapi_sdk``
keeps working when only a subset of extras is installed; the
``ImportError`` is raised with a clear hint the moment the helper is
actually constructed.

Names load on first access (PEP 562) through :func:`__getattr__`, so importing
this package — or any of its submodules — does not import every submodule
it re-exports. Type checkers read them from the ``TYPE_CHECKING`` block.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tempest_fastapi_sdk.utils.client_ip import (
        get_client_ip as get_client_ip,
    )
    from tempest_fastapi_sdk.utils.client_ip import (
        get_client_ip_from_scope as get_client_ip_from_scope,
    )
    from tempest_fastapi_sdk.utils.client_ip import (
        parse_client_ip as parse_client_ip,
    )
    from tempest_fastapi_sdk.utils.color import is_light as is_light
    from tempest_fastapi_sdk.utils.color import (
        relative_luminance as relative_luminance,
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
        INLINE_SAFE_MEDIA_TYPES as INLINE_SAFE_MEDIA_TYPES,
    )
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
        AllowedImageMediaTypes as AllowedImageMediaTypes,
    )
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
    from tempest_fastapi_sdk.utils.fields import (
        image_media_type_field as image_media_type_field,
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
    from tempest_fastapi_sdk.utils.jwt import JWTUtils as JWTUtils
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
        RegionEnum as RegionEnum,
    )
    from tempest_fastapi_sdk.utils.locations import (
        StateBR as StateBR,
    )
    from tempest_fastapi_sdk.utils.locations import (
        UFEnum as UFEnum,
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
    from tempest_fastapi_sdk.utils.media_types import (
        DOCX_MEDIA_TYPE as DOCX_MEDIA_TYPE,
    )
    from tempest_fastapi_sdk.utils.media_types import (
        PPTX_MEDIA_TYPE as PPTX_MEDIA_TYPE,
    )
    from tempest_fastapi_sdk.utils.media_types import (
        XLSX_MEDIA_TYPE as XLSX_MEDIA_TYPE,
    )
    from tempest_fastapi_sdk.utils.media_types import (
        ImageMediaTypeEnum as ImageMediaTypeEnum,
    )
    from tempest_fastapi_sdk.utils.media_types import (
        guess_media_type as guess_media_type,
    )
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
    from tempest_fastapi_sdk.utils.password import (
        DEFAULT_GENERATED_PASSWORD_LENGTH as DEFAULT_GENERATED_PASSWORD_LENGTH,
    )
    from tempest_fastapi_sdk.utils.password import PasswordPolicy as PasswordPolicy
    from tempest_fastapi_sdk.utils.password import (
        PasswordPolicyViolation as PasswordPolicyViolation,
    )
    from tempest_fastapi_sdk.utils.password import PasswordUtils as PasswordUtils
    from tempest_fastapi_sdk.utils.password import (
        PasswordViolationCodeEnum as PasswordViolationCodeEnum,
    )
    from tempest_fastapi_sdk.utils.password import (
        check_password_policy as check_password_policy,
    )
    from tempest_fastapi_sdk.utils.password import (
        generate_password as generate_password,
    )
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
    from tempest_fastapi_sdk.utils.retry import (
        RetryLogger as RetryLogger,
    )
    from tempest_fastapi_sdk.utils.retry import (
        RetryPolicy as RetryPolicy,
    )
    from tempest_fastapi_sdk.utils.retry import (
        async_retry as async_retry,
    )
    from tempest_fastapi_sdk.utils.signed_url import (
        SIGNED_URL_EXPIRES_PARAM as SIGNED_URL_EXPIRES_PARAM,
    )
    from tempest_fastapi_sdk.utils.signed_url import (
        SIGNED_URL_SIGNATURE_PARAM as SIGNED_URL_SIGNATURE_PARAM,
    )
    from tempest_fastapi_sdk.utils.signed_url import sign_path as sign_path
    from tempest_fastapi_sdk.utils.signed_url import verify_path as verify_path
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
        InMemoryThrottleBackend as InMemoryThrottleBackend,
    )
    from tempest_fastapi_sdk.utils.throttle import (
        ThrottleBackend as ThrottleBackend,
    )
    from tempest_fastapi_sdk.utils.throttle import (
        ThrottleStatus as ThrottleStatus,
    )
    from tempest_fastapi_sdk.utils.throttle import (
        login_throttle_key as login_throttle_key,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        ACCESS_TOKEN_TYPE as ACCESS_TOKEN_TYPE,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        AMR_CLAIM as AMR_CLAIM,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        AMR_EMAIL_LINK as AMR_EMAIL_LINK,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        AMR_EXTERNAL as AMR_EXTERNAL,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        AMR_HARDWARE_KEY as AMR_HARDWARE_KEY,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        AMR_OTP as AMR_OTP,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        AMR_PASSWORD as AMR_PASSWORD,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        MFA_AMR as MFA_AMR,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        MFA_AMR_METHODS as MFA_AMR_METHODS,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        MFA_TOKEN_TYPE as MFA_TOKEN_TYPE,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        PASSWORD_AMR as PASSWORD_AMR,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        REFRESH_TOKEN_TYPE as REFRESH_TOKEN_TYPE,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        STREAM_TOKEN_TYPE as STREAM_TOKEN_TYPE,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        token_amr as token_amr,
    )
    from tempest_fastapi_sdk.utils.token_types import (
        token_type_allowed as token_type_allowed,
    )
    from tempest_fastapi_sdk.utils.totp import TOTPHelper as TOTPHelper
    from tempest_fastapi_sdk.utils.upload import (
        HEIC_BRANDS as HEIC_BRANDS,
    )
    from tempest_fastapi_sdk.utils.upload import (
        HEIF_STRUCTURAL_BRANDS as HEIF_STRUCTURAL_BRANDS,
    )
    from tempest_fastapi_sdk.utils.upload import (
        SNIFFABLE_MIMETYPES as SNIFFABLE_MIMETYPES,
    )
    from tempest_fastapi_sdk.utils.upload import (
        UPLOAD_READ_CHUNK_BYTES as UPLOAD_READ_CHUNK_BYTES,
    )
    from tempest_fastapi_sdk.utils.upload import (
        UploadUtils as UploadUtils,
    )
    from tempest_fastapi_sdk.utils.upload import (
        read_upload_capped as read_upload_capped,
    )
    from tempest_fastapi_sdk.utils.upload import (
        sniff_mime as sniff_mime,
    )


_LAZY_ATTRIBUTES: dict[str, str] = {
    "AllowedImageMediaTypes": "tempest_fastapi_sdk.utils.fields",
    "image_media_type_field": "tempest_fastapi_sdk.utils.fields",
    "get_client_ip": "tempest_fastapi_sdk.utils.client_ip",
    "get_client_ip_from_scope": "tempest_fastapi_sdk.utils.client_ip",
    "parse_client_ip": "tempest_fastapi_sdk.utils.client_ip",
    "CENT": "tempest_fastapi_sdk.utils.currency",
    "HUNDRED": "tempest_fastapi_sdk.utils.currency",
    "format_currency_br": "tempest_fastapi_sdk.utils.currency",
    "format_percent_br": "tempest_fastapi_sdk.utils.currency",
    "format_quantity_br": "tempest_fastapi_sdk.utils.currency",
    "parse_currency_br": "tempest_fastapi_sdk.utils.currency",
    "quantize_money": "tempest_fastapi_sdk.utils.currency",
    "to_utc": "tempest_fastapi_sdk.utils.datetime",
    "utcnow": "tempest_fastapi_sdk.utils.datetime",
    "modify_dict": "tempest_fastapi_sdk.utils.dict",
    "INLINE_SAFE_MEDIA_TYPES": "tempest_fastapi_sdk.utils.download",
    "is_light": "tempest_fastapi_sdk.utils.color",
    "relative_luminance": "tempest_fastapi_sdk.utils.color",
    "DownloadUtils": "tempest_fastapi_sdk.utils.download",
    "build_content_disposition": "tempest_fastapi_sdk.utils.download",
    "BulkEmailReport": "tempest_fastapi_sdk.utils.email",
    "EmailUtils": "tempest_fastapi_sdk.utils.email",
    "FailedRecipient": "tempest_fastapi_sdk.utils.email",
    "CentsField": "tempest_fastapi_sdk.utils.fields",
    "DecimalPercentField": "tempest_fastapi_sdk.utils.fields",
    "DecimalRatioField": "tempest_fastapi_sdk.utils.fields",
    "HexColorField": "tempest_fastapi_sdk.utils.fields",
    "LatitudeField": "tempest_fastapi_sdk.utils.fields",
    "LocaleField": "tempest_fastapi_sdk.utils.fields",
    "LongitudeField": "tempest_fastapi_sdk.utils.fields",
    "NonEmptyStrField": "tempest_fastapi_sdk.utils.fields",
    "NonNegativeFloatField": "tempest_fastapi_sdk.utils.fields",
    "NonNegativeIntField": "tempest_fastapi_sdk.utils.fields",
    "PercentField": "tempest_fastapi_sdk.utils.fields",
    "PortField": "tempest_fastapi_sdk.utils.fields",
    "PositiveFloatField": "tempest_fastapi_sdk.utils.fields",
    "PositiveIntField": "tempest_fastapi_sdk.utils.fields",
    "PriceField": "tempest_fastapi_sdk.utils.fields",
    "RatingField": "tempest_fastapi_sdk.utils.fields",
    "RatioField": "tempest_fastapi_sdk.utils.fields",
    "SignedDecimalRatioField": "tempest_fastapi_sdk.utils.fields",
    "SlugField": "tempest_fastapi_sdk.utils.fields",
    "FileStoreUtils": "tempest_fastapi_sdk.utils.file_store",
    "form_encode": "tempest_fastapi_sdk.utils.forms",
    "REQUEST_ID_HEADER": "tempest_fastapi_sdk.utils.http_client",
    "CircuitOpenError": "tempest_fastapi_sdk.utils.http_client",
    "HTTPClient": "tempest_fastapi_sdk.utils.http_client",
    "JWTUtils": "tempest_fastapi_sdk.utils.jwt",
    "UFEnum": "tempest_fastapi_sdk.utils.locations",
    "ChoiceBR": "tempest_fastapi_sdk.utils.locations",
    "CityBR": "tempest_fastapi_sdk.utils.locations",
    "CityNameField": "tempest_fastapi_sdk.utils.locations",
    "RegionEnum": "tempest_fastapi_sdk.utils.locations",
    "StateBR": "tempest_fastapi_sdk.utils.locations",
    "UFField": "tempest_fastapi_sdk.utils.locations",
    "cities_by_uf": "tempest_fastapi_sdk.utils.locations",
    "city_choices": "tempest_fastapi_sdk.utils.locations",
    "get_state": "tempest_fastapi_sdk.utils.locations",
    "is_valid_city": "tempest_fastapi_sdk.utils.locations",
    "is_valid_uf": "tempest_fastapi_sdk.utils.locations",
    "list_states": "tempest_fastapi_sdk.utils.locations",
    "normalize_city": "tempest_fastapi_sdk.utils.locations",
    "normalize_uf": "tempest_fastapi_sdk.utils.locations",
    "region_choices": "tempest_fastapi_sdk.utils.locations",
    "states_by_region": "tempest_fastapi_sdk.utils.locations",
    "uf_choices": "tempest_fastapi_sdk.utils.locations",
    "LogUtils": "tempest_fastapi_sdk.utils.log",
    "DOCX_MEDIA_TYPE": "tempest_fastapi_sdk.utils.media_types",
    "PPTX_MEDIA_TYPE": "tempest_fastapi_sdk.utils.media_types",
    "XLSX_MEDIA_TYPE": "tempest_fastapi_sdk.utils.media_types",
    "ImageMediaTypeEnum": "tempest_fastapi_sdk.utils.media_types",
    "guess_media_type": "tempest_fastapi_sdk.utils.media_types",
    "CPUMetrics": "tempest_fastapi_sdk.utils.metrics",
    "DiskMetrics": "tempest_fastapi_sdk.utils.metrics",
    "GPUMetrics": "tempest_fastapi_sdk.utils.metrics",
    "MemoryMetrics": "tempest_fastapi_sdk.utils.metrics",
    "MetricsUtils": "tempest_fastapi_sdk.utils.metrics",
    "SystemMetrics": "tempest_fastapi_sdk.utils.metrics",
    "generate_opaque_token": "tempest_fastapi_sdk.utils.opaque_token",
    "hash_opaque_token": "tempest_fastapi_sdk.utils.opaque_token",
    "verify_opaque_token": "tempest_fastapi_sdk.utils.opaque_token",
    "DEFAULT_GENERATED_PASSWORD_LENGTH": "tempest_fastapi_sdk.utils.password",
    "PasswordPolicy": "tempest_fastapi_sdk.utils.password",
    "PasswordPolicyViolation": "tempest_fastapi_sdk.utils.password",
    "PasswordUtils": "tempest_fastapi_sdk.utils.password",
    "PasswordViolationCodeEnum": "tempest_fastapi_sdk.utils.password",
    "check_password_policy": "tempest_fastapi_sdk.utils.password",
    "generate_password": "tempest_fastapi_sdk.utils.password",
    "CEP": "tempest_fastapi_sdk.utils.regex",
    "CEP_PATTERN": "tempest_fastapi_sdk.utils.regex",
    "CNPJ": "tempest_fastapi_sdk.utils.regex",
    "CNPJ_PATTERN": "tempest_fastapi_sdk.utils.regex",
    "CPF": "tempest_fastapi_sdk.utils.regex",
    "CPF_CNPJ_PATTERN": "tempest_fastapi_sdk.utils.regex",
    "CPF_PATTERN": "tempest_fastapi_sdk.utils.regex",
    "PHONE_BR_PATTERN": "tempest_fastapi_sdk.utils.regex",
    "CEPField": "tempest_fastapi_sdk.utils.regex",
    "CNPJField": "tempest_fastapi_sdk.utils.regex",
    "CPFField": "tempest_fastapi_sdk.utils.regex",
    "CPFOrCNPJ": "tempest_fastapi_sdk.utils.regex",
    "CPFOrCNPJField": "tempest_fastapi_sdk.utils.regex",
    "MobilePhoneBRField": "tempest_fastapi_sdk.utils.regex",
    "PhoneBR": "tempest_fastapi_sdk.utils.regex",
    "PhoneBRField": "tempest_fastapi_sdk.utils.regex",
    "PhoneNumberBR": "tempest_fastapi_sdk.utils.regex",
    "PixKeyField": "tempest_fastapi_sdk.utils.regex",
    "PixKeyType": "tempest_fastapi_sdk.utils.regex",
    "detect_pix_key_type": "tempest_fastapi_sdk.utils.regex",
    "is_valid_cep": "tempest_fastapi_sdk.utils.regex",
    "is_valid_cnpj": "tempest_fastapi_sdk.utils.regex",
    "is_valid_cpf": "tempest_fastapi_sdk.utils.regex",
    "is_valid_cpf_cnpj": "tempest_fastapi_sdk.utils.regex",
    "is_valid_mobile_phone_br": "tempest_fastapi_sdk.utils.regex",
    "is_valid_phone_br": "tempest_fastapi_sdk.utils.regex",
    "is_valid_pix_key": "tempest_fastapi_sdk.utils.regex",
    "normalize_cep": "tempest_fastapi_sdk.utils.regex",
    "normalize_cnpj": "tempest_fastapi_sdk.utils.regex",
    "normalize_cpf": "tempest_fastapi_sdk.utils.regex",
    "normalize_cpf_cnpj": "tempest_fastapi_sdk.utils.regex",
    "normalize_mobile_phone_br": "tempest_fastapi_sdk.utils.regex",
    "normalize_phone_br": "tempest_fastapi_sdk.utils.regex",
    "normalize_pix_key": "tempest_fastapi_sdk.utils.regex",
    "only_digits": "tempest_fastapi_sdk.utils.regex",
    "parse_phone_br": "tempest_fastapi_sdk.utils.regex",
    "RetryLogger": "tempest_fastapi_sdk.utils.retry",
    "RetryPolicy": "tempest_fastapi_sdk.utils.retry",
    "async_retry": "tempest_fastapi_sdk.utils.retry",
    "SIGNED_URL_EXPIRES_PARAM": "tempest_fastapi_sdk.utils.signed_url",
    "SIGNED_URL_SIGNATURE_PARAM": "tempest_fastapi_sdk.utils.signed_url",
    "sign_path": "tempest_fastapi_sdk.utils.signed_url",
    "verify_path": "tempest_fastapi_sdk.utils.signed_url",
    "LocalUploadStorage": "tempest_fastapi_sdk.utils.storage_backends",
    "MinIOUploadStorage": "tempest_fastapi_sdk.utils.storage_backends",
    "UploadResult": "tempest_fastapi_sdk.utils.storage_backends",
    "UploadStorage": "tempest_fastapi_sdk.utils.storage_backends",
    "AttemptThrottle": "tempest_fastapi_sdk.utils.throttle",
    "InMemoryThrottleBackend": "tempest_fastapi_sdk.utils.throttle",
    "ThrottleBackend": "tempest_fastapi_sdk.utils.throttle",
    "ThrottleStatus": "tempest_fastapi_sdk.utils.throttle",
    "login_throttle_key": "tempest_fastapi_sdk.utils.throttle",
    "ACCESS_TOKEN_TYPE": "tempest_fastapi_sdk.utils.token_types",
    "AMR_CLAIM": "tempest_fastapi_sdk.utils.token_types",
    "AMR_EMAIL_LINK": "tempest_fastapi_sdk.utils.token_types",
    "AMR_EXTERNAL": "tempest_fastapi_sdk.utils.token_types",
    "AMR_HARDWARE_KEY": "tempest_fastapi_sdk.utils.token_types",
    "AMR_OTP": "tempest_fastapi_sdk.utils.token_types",
    "AMR_PASSWORD": "tempest_fastapi_sdk.utils.token_types",
    "MFA_AMR": "tempest_fastapi_sdk.utils.token_types",
    "MFA_AMR_METHODS": "tempest_fastapi_sdk.utils.token_types",
    "MFA_TOKEN_TYPE": "tempest_fastapi_sdk.utils.token_types",
    "PASSWORD_AMR": "tempest_fastapi_sdk.utils.token_types",
    "REFRESH_TOKEN_TYPE": "tempest_fastapi_sdk.utils.token_types",
    "STREAM_TOKEN_TYPE": "tempest_fastapi_sdk.utils.token_types",
    "token_amr": "tempest_fastapi_sdk.utils.token_types",
    "token_type_allowed": "tempest_fastapi_sdk.utils.token_types",
    "TOTPHelper": "tempest_fastapi_sdk.utils.totp",
    "HEIC_BRANDS": "tempest_fastapi_sdk.utils.upload",
    "HEIF_STRUCTURAL_BRANDS": "tempest_fastapi_sdk.utils.upload",
    "SNIFFABLE_MIMETYPES": "tempest_fastapi_sdk.utils.upload",
    "UPLOAD_READ_CHUNK_BYTES": "tempest_fastapi_sdk.utils.upload",
    "UploadUtils": "tempest_fastapi_sdk.utils.upload",
    "read_upload_capped": "tempest_fastapi_sdk.utils.upload",
    "sniff_mime": "tempest_fastapi_sdk.utils.upload",
}
"""Exported name → submodule that defines it, for :func:`__getattr__`."""


if not TYPE_CHECKING:

    def __getattr__(name: str) -> Any:
        """Import an exported name on first access and cache it.

        Defined only when not type checking: a module-level ``__getattr__``
        makes a checker type **every** unknown attribute as ``Any``, so a
        misspelled ``tempest_fastapi_sdk.X`` would stop being an error in
        the consumer's code. Checkers read the names from the
        ``TYPE_CHECKING`` block instead.

        A name outside the map that is a submodule is imported and returned,
        as the eager imports used to leave every submodule reachable as an
        attribute (``tempest_fastapi_sdk.db.BaseModel`` after a bare
        ``import tempest_fastapi_sdk``).

        Args:
            name (str): The attribute being looked up.

        Returns:
            Any: The object the defining submodule exports under ``name``.

        Raises:
            AttributeError: When ``name`` is not exported here.
        """
        module_name = _LAZY_ATTRIBUTES.get(name)
        if module_name is None:
            submodule = f"{__name__}.{name}"
            if importlib.util.find_spec(submodule) is None:
                raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
            return importlib.import_module(submodule)
        value = getattr(importlib.import_module(module_name), name)
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    """List the exported names, loaded or not.

    Returns:
        list[str]: :data:`__all__` plus the module's own globals.
    """
    return sorted({*globals(), *__all__})


__all__: list[str] = [
    "ACCESS_TOKEN_TYPE",
    "AMR_CLAIM",
    "AMR_EMAIL_LINK",
    "AMR_EXTERNAL",
    "AMR_HARDWARE_KEY",
    "AMR_OTP",
    "AMR_PASSWORD",
    "CENT",
    "CEP",
    "CEP_PATTERN",
    "CNPJ",
    "CNPJ_PATTERN",
    "CPF",
    "CPF_CNPJ_PATTERN",
    "CPF_PATTERN",
    "DEFAULT_GENERATED_PASSWORD_LENGTH",
    "DOCX_MEDIA_TYPE",
    "HEIC_BRANDS",
    "HEIF_STRUCTURAL_BRANDS",
    "HUNDRED",
    "INLINE_SAFE_MEDIA_TYPES",
    "MFA_AMR",
    "MFA_AMR_METHODS",
    "MFA_TOKEN_TYPE",
    "PASSWORD_AMR",
    "PHONE_BR_PATTERN",
    "PPTX_MEDIA_TYPE",
    "REFRESH_TOKEN_TYPE",
    "REQUEST_ID_HEADER",
    "SIGNED_URL_EXPIRES_PARAM",
    "SIGNED_URL_SIGNATURE_PARAM",
    "SNIFFABLE_MIMETYPES",
    "STREAM_TOKEN_TYPE",
    "UPLOAD_READ_CHUNK_BYTES",
    "XLSX_MEDIA_TYPE",
    "AllowedImageMediaTypes",
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
    "ImageMediaTypeEnum",
    "InMemoryThrottleBackend",
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
    "PasswordPolicy",
    "PasswordPolicyViolation",
    "PasswordUtils",
    "PasswordViolationCodeEnum",
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
    "RegionEnum",
    "RetryLogger",
    "RetryPolicy",
    "SignedDecimalRatioField",
    "SlugField",
    "StateBR",
    "SystemMetrics",
    "TOTPHelper",
    "ThrottleBackend",
    "ThrottleStatus",
    "UFEnum",
    "UFField",
    "UploadResult",
    "UploadStorage",
    "UploadUtils",
    "async_retry",
    "build_content_disposition",
    "check_password_policy",
    "cities_by_uf",
    "city_choices",
    "detect_pix_key_type",
    "form_encode",
    "format_currency_br",
    "format_percent_br",
    "format_quantity_br",
    "generate_opaque_token",
    "generate_password",
    "get_client_ip",
    "get_client_ip_from_scope",
    "get_state",
    "guess_media_type",
    "hash_opaque_token",
    "image_media_type_field",
    "is_light",
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
    "login_throttle_key",
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
    "parse_client_ip",
    "parse_currency_br",
    "parse_phone_br",
    "quantize_money",
    "read_upload_capped",
    "region_choices",
    "relative_luminance",
    "sign_path",
    "sniff_mime",
    "states_by_region",
    "to_utc",
    "token_amr",
    "token_type_allowed",
    "uf_choices",
    "utcnow",
    "verify_opaque_token",
    "verify_path",
]

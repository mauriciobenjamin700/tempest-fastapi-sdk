---
description: "A hierarquia `AppException`, as exceções por domínio e os handlers que as viram resposta HTTP."
---

# `tempest_fastapi_sdk.exceptions`

A hierarquia `AppException`, as exceções por domínio e os handlers que as viram resposta HTTP.

[« Admin, dados e API](../admin-data-api.md)

---

::: tempest_fastapi_sdk.exceptions.base.AppException
::: tempest_fastapi_sdk.exceptions.base.InheritedErrorCodeWarning
::: tempest_fastapi_sdk.exceptions.not_found.NotFoundException
::: tempest_fastapi_sdk.exceptions.conflict.ConflictException
::: tempest_fastapi_sdk.exceptions.factories.not_found_exception
::: tempest_fastapi_sdk.exceptions.factories.conflict_exception
::: tempest_fastapi_sdk.exceptions.unauthorized.UnauthorizedException
::: tempest_fastapi_sdk.exceptions.forbidden.ForbiddenException
::: tempest_fastapi_sdk.exceptions.forbidden.MFARequiredException
::: tempest_fastapi_sdk.exceptions.validation.ValidationException
::: tempest_fastapi_sdk.exceptions.validation.OrderByNotAllowedException
::: tempest_fastapi_sdk.exceptions.validation.PageSizeTooLargeException
::: tempest_fastapi_sdk.exceptions.validation.PushEndpointNotPublicException
::: tempest_fastapi_sdk.exceptions.validation.UnknownFilterKeyException
::: tempest_fastapi_sdk.exceptions.validation.InvalidFilterValueException
::: tempest_fastapi_sdk.exceptions.value_errors.ValidationValueError
::: tempest_fastapi_sdk.exceptions.too_many_requests.TooManyRequestsException
::: tempest_fastapi_sdk.exceptions.integration.IntegrationDisabledException
::: tempest_fastapi_sdk.exceptions.integration.EmailDeliveryException
::: tempest_fastapi_sdk.exceptions.integration.ZapSendFailedException
::: tempest_fastapi_sdk.exceptions.integration.GoogleDriveException
::: tempest_fastapi_sdk.exceptions.integration.GoogleDriveFolderNotFoundException
::: tempest_fastapi_sdk.exceptions.storage.StorageIntegrityException
::: tempest_fastapi_sdk.exceptions.storage.PresignedUrlUnsupportedException
::: tempest_fastapi_sdk.exceptions.jwt.InvalidTokenException
::: tempest_fastapi_sdk.exceptions.jwt.ExpiredTokenException
::: tempest_fastapi_sdk.exceptions.signed_url.InvalidSignedURLException
::: tempest_fastapi_sdk.exceptions.signed_url.ExpiredSignedURLException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthProviderNotConfiguredException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthProviderDeniedException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthStateMismatchException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthCodeMissingException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthEmailMissingException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthEmailTakenException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthEmailUnverifiedException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthRegistrationDisabledException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthAccountInactiveException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthAccountNotLinkedException
::: tempest_fastapi_sdk.exceptions.upload.FileTooLargeException
::: tempest_fastapi_sdk.exceptions.upload.FileTooSmallException
::: tempest_fastapi_sdk.exceptions.upload.InvalidFileTypeException
::: tempest_fastapi_sdk.exceptions.upload.UploadTooLargeException
::: tempest_fastapi_sdk.exceptions.tus.TusVersionException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadNotFoundException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadExpiredException
::: tempest_fastapi_sdk.exceptions.tus.TusOffsetMismatchException
::: tempest_fastapi_sdk.exceptions.tus.TusContentTypeException
::: tempest_fastapi_sdk.exceptions.tus.TusInvalidHeaderException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadTooLargeException
::: tempest_fastapi_sdk.exceptions.tus.TusIdempotencyKeyReusedException
::: tempest_fastapi_sdk.exceptions.tus.TusBodyTimeoutException
::: tempest_fastapi_sdk.exceptions.tus.TusChunkTooSmallException
::: tempest_fastapi_sdk.exceptions.tus.TusQuotaExceededException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadCompletedException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadLockedException
::: tempest_fastapi_sdk.exceptions.image.ImageDecodeException
::: tempest_fastapi_sdk.exceptions.image.ImageTypeMismatchException
::: tempest_fastapi_sdk.exceptions.image.ImageFormatUnsupportedException
::: tempest_fastapi_sdk.exceptions.image.ImageTooLargeException
::: tempest_fastapi_sdk.exceptions.wallet.InsufficientBalanceException
::: tempest_fastapi_sdk.exceptions.wallet.ManualPayoutNotFoundException
::: tempest_fastapi_sdk.exceptions.wallet.ManualPayoutNotPendingException
::: tempest_fastapi_sdk.exceptions.wallet.ManualPayoutReceiptMissingException
::: tempest_fastapi_sdk.exceptions.wallet.PayoutRejectedException
::: tempest_fastapi_sdk.exceptions.wallet.PayoutUncertainException
::: tempest_fastapi_sdk.exceptions.payment.PixRefundExceedsBalanceException
::: tempest_fastapi_sdk.exceptions.payment.PixRefundReferenceConflictException
::: tempest_fastapi_sdk.exceptions.eventsourcing.WrongExpectedVersionException
::: tempest_fastapi_sdk.exceptions.i18n.MessageCatalog
::: tempest_fastapi_sdk.exceptions.i18n.default_message_catalog
::: tempest_fastapi_sdk.exceptions.i18n.parse_accept_language

::: tempest_fastapi_sdk.exceptions.i18n.DEFAULT_LOCALE
::: tempest_fastapi_sdk.exceptions.oauth.OAuthAudienceUnverifiableException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthTokenAudienceMismatchException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthTokenRejectedException

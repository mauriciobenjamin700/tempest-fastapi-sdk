# Migration guide

Breaking-change walkthroughs grouped by minor release. Stick to the version that matches what you're upgrading **from**. The subpages, and the release sections inside each one, are listed newest-first, so on a multi-version jump read and apply them bottom-up.

## Version ranges

One subpage per minor-version range, newest first.

| Subpage | What it solves |
| --- | --- |
| [Migration 0.37x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-37x.md) | Use when upgrading from a version older than these releases: 0.374.0, 0.373.0 and 0.372.0. |
| [Migration 0.36x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-36x.md) | Use when upgrading from a version older than these releases: 0.366.0 and 0.361.0. |
| [Migration 0.34x–0.35x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-34x-0-35x.md) | Use when upgrading from a version older than these releases: 0.353.0, 0.349.0, 0.344.0, 0.343.0, 0.342.0, 0.341.0 and 0.340.0. |
| [Migration 0.33x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-33x.md) | Use when upgrading from a version older than these releases: 0.336.0, 0.335.0, 0.332.0, 0.331.0 and 0.330.0. |
| [Migration 0.32x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-32x.md) | Use when upgrading from a version older than these releases: 0.327.0, 0.325.0, 0.323.0, 0.322.0, 0.321.0 and 0.320.0. |
| [Migration 0.31x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md) | Use when upgrading from a version older than these releases: 0.319.0, 0.318.0, 0.316.0, 0.314.0, 0.313.0, 0.312.0 and 0.311.0. |
| [Migration 0.30x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md) | Use when upgrading from a version older than these releases: 0.309.0, 0.308.0, 0.306.0, 0.305.0, 0.304.0, 0.302.0, 0.301.0 and 0.300.0. |
| [Migration 0.27x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-27x.md) | Use when upgrading from a version older than these releases: 0.274.0, 0.272.0 and 0.270.0. |
| [Migration 0.26x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-26x.md) | Use when upgrading from a version older than these releases: 0.269.0, 0.265.0, 0.264.0, 0.263.0, 0.261.0 and 0.260.0. |
| [Migration 0.25x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-25x.md) | Use when upgrading from a version older than these releases: 0.259.0, 0.258.0, 0.257.0, 0.256.0, 0.252.0 and 0.251.0. |
| [Migration 0.22x–0.23x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-22x-0-23x.md) | Use when upgrading from a version older than these releases: 0.234.0, 0.233.0 and 0.229.0. |
| [Migration 0.8–0.17x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-8-0-17x.md) | Use when upgrading from a version older than these releases: 0.174.0, 0.173.0, 0.138.1, 0.92.0, 0.63.0 and 0.8.0. |

## Every change, by release

Use this list to find a change by name; each item opens the section on its range's subpage.

- [0.374.0 — `Agent` and `AIChatPipeline` run tool calls written in the text](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-37x.md#03740-agent-and-aichatpipeline-run-tool-calls-written-in-the-text)
- [0.374.0 — strict schema in structured output and truncated images refused](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-37x.md#03740-strict-schema-in-structured-output-and-truncated-images-refused)
- [0.373.0 — `BaseController` and `BaseService` bind `ResponseT` to the layer below](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-37x.md#03730-basecontroller-and-baseservice-bind-responset-to-the-layer-below)
- [0.373.0 — `ObjectStat.raw` can be `None`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-37x.md#03730-objectstatraw-can-be-none)
- [0.373.0 — `SignedCookieSessionStore`'s `same_site` typed as a `Literal`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-37x.md#03730-signedcookiesessionstores-same_site-typed-as-a-literal)
- [0.373.0 — `BaseUserModel` without phantom columns](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-37x.md#03730-baseusermodel-without-phantom-columns)
- [0.373.0 — runtime breaks outside the checker and narrowed public types](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-37x.md#03730-runtime-breaks-outside-the-checker-and-narrowed-public-types)
- [0.372.0 — five new `tempest check` rules and `RuntimeError` without a URL in `AlembicHelper`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-37x.md#03720-five-new-tempest-check-rules-and-runtimeerror-without-a-url-in-alembichelper)
- [0.366.0 — `RoutingBackend` requires `route_through` and `matrix`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-36x.md#03660-routingbackend-requires-route_through-and-matrix)
- [0.361.0 — manual payout gains four columns](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-36x.md#03610-manual-payout-gains-four-columns)
- [0.353.0 — Gemini's `google_kwargs()` becomes `gemini_kwargs()`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-34x-0-35x.md#03530-geminis-google_kwargs-becomes-gemini_kwargs)
- [0.349.0 — Coqui in its own extra, oversized upload as 413 and a stricter `tempest check`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-34x-0-35x.md#03490-coqui-in-its-own-extra-oversized-upload-as-413-and-a-stricter-tempest-check)
- [0.344.0 — token of a deleted account](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-34x-0-35x.md#03440-token-of-a-deleted-account)
- [0.343.0 — every SDK enum ends in `Enum`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-34x-0-35x.md#03430-every-sdk-enum-ends-in-enum)
- [0.342.0 — required `ENV` and empty `X-Token` secret](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-34x-0-35x.md#03420-required-env-and-empty-x-token-secret)
- [0.341.0 — Mercado Pago webhook and `.xlsx` formulas](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-34x-0-35x.md#03410-mercado-pago-webhook-and-xlsx-formulas)
- [0.340.0 — sign-up, Web Push and SSE](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-34x-0-35x.md#03400-sign-up-web-push-and-sse)
- [0.336.0 — security fixes (real time)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-33x.md#03360-security-fixes-real-time)
- [0.336.0 — `password-change` returns a fresh pair, `set-password` deletes passkeys](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-33x.md#03360-password-change-returns-a-fresh-pair-set-password-deletes-passkeys)
- [0.336.0 — CORS, storage, text search and Stripe](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-33x.md#03360-cors-storage-text-search-and-stripe)
- [0.335.0 — `BaseUserModel` gains the `email_verified_at` column](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-33x.md#03350-baseusermodel-gains-the-email_verified_at-column)
- [0.335.0 — security fixes that change behaviour](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-33x.md#03350-security-fixes-that-change-behaviour)
- [0.332.0 — the DLQ pins the consumer's ack policy](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-33x.md#03320-the-dlq-pins-the-consumers-ack-policy)
- [0.331.0 — new admin look, capped storage uploads](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-33x.md#03310-new-admin-look-capped-storage-uploads)
- [0.330.0 — a push subscription no longer changes owner](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-33x.md#03300-a-push-subscription-no-longer-changes-owner)
- [0.327.0 — the #616 architecture rules, as warnings](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-32x.md#03270-the-616-architecture-rules-as-warnings)
- [0.325.0 — CSRF on the admin login and the `[cli-ops]` extra](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-32x.md#03250-csrf-on-the-admin-login-and-the-cli-ops-extra)
- [0.323.0 — the dead-letter topology comes up before the consumers](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-32x.md#03230-the-dead-letter-topology-comes-up-before-the-consumers)
- [0.322.0 — SMTP failure becomes `503 EMAIL_UNAVAILABLE` and the guard checks TLS × port](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-32x.md#03220-smtp-failure-becomes-503-email_unavailable-and-the-guard-checks-tls-port)
- [0.321.0 — `SubjectStorageClient` gains three methods](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-32x.md#03210-subjectstorageclient-gains-three-methods)
- [0.320.0 — production guard for e-mail and links](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-32x.md#03200-production-guard-for-e-mail-and-links)
- [0.319.0 — counts from `bulk_create_values` and `bulk_upsert`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03190-counts-from-bulk_create_values-and-bulk_upsert)
- [0.318.0 — log readers, empty `log_dir`, header IP and the `/admin` login](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03180-log-readers-empty-log_dir-header-ip-and-the-admin-login)
- [0.316.0 — `set-password` asks for the factor on an MFA account](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03160-set-password-asks-for-the-factor-on-an-mfa-account)
- [0.316.0 — the second item of `UserAuthService.signup` can be `ActivationWithheld`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03160-the-second-item-of-userauthservicesignup-can-be-activationwithheld)
- [0.314.0 — Python 3.12 is the new minimum](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03140-python-312-is-the-new-minimum)
- [0.314.0 — social login of an account with TOTP asks for the second factor](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03140-social-login-of-an-account-with-totp-asks-for-the-second-factor)
- [0.314.0 — activation and reset links stamp `amr=["email"]`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03140-activation-and-reset-links-stamp-amremail)
- [0.313.0 — a class ending in `Enum` no longer doubles the type suffix](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03130-a-class-ending-in-enum-no-longer-doubles-the-type-suffix)
- [0.313.0 — auth 429s carry their own code](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03130-auth-429s-carry-their-own-code)
- [0.313.0 — the refresh token stores the `amr`, and `/mfa/confirm` answers `200`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03130-the-refresh-token-stores-the-amr-and-mfaconfirm-answers-200)
- [0.312.0 — Mercado Pago: expired Pix and chargeback search](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03120-mercado-pago-expired-pix-and-chargeback-search)
- [0.311.0 — Mercado Pago: the Payments API left the SDK](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-31x.md#03110-mercado-pago-the-payments-api-left-the-sdk)
- [0.309.0 — other behaviour changes](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03090-other-behaviour-changes)
- [0.309.0 — an unknown `filters` key raises](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03090-an-unknown-filters-key-raises)
- [0.308.0 — the `tempest` CLI became the `[cli]` extra](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03080-the-tempest-cli-became-the-cli-extra)
- [0.306.0 — single-use TOTP codes need the `totp_last_step` column](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03060-single-use-totp-codes-need-the-totp_last_step-column)
- [0.306.0 — `make_logs_router` refuses an empty secret](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03060-make_logs_router-refuses-an-empty-secret)
- [0.306.0 — a download only goes `inline` for a safe type](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03060-a-download-only-goes-inline-for-a-safe-type)
- [0.305.0 — a deactivated account's token no longer authenticates](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03050-a-deactivated-accounts-token-no-longer-authenticates)
- [0.305.0 — a password reset whose user was removed is an invalid link](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03050-a-password-reset-whose-user-was-removed-is-an-invalid-link)
- [0.305.0 — signup writes the schema fields that are columns](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03050-signup-writes-the-schema-fields-that-are-columns)
- [0.304.0 — the Google Sheets CSV reader has a limit by default](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03040-the-google-sheets-csv-reader-has-a-limit-by-default)
- [0.304.0 — SQLite enforces foreign keys](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03040-sqlite-enforces-foreign-keys)
- [0.304.0 — a raw tool error no longer reaches the model](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03040-a-raw-tool-error-no-longer-reaches-the-model)
- [0.302.0 — a composite constraint carries every column in its name](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03020-a-composite-constraint-carries-every-column-in-its-name)
- [0.301.0 — CSS, `DataTable`, forms, SSR and sessions](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03010-css-datatable-forms-ssr-and-sessions)
- [0.300.0 — the chat attachment records who uploaded it](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03000-the-chat-attachment-records-who-uploaded-it)
- [0.300.0 — the other breaking changes: AI, chat, metrics and migrations](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-30x.md#03000-the-other-breaking-changes-ai-chat-metrics-and-migrations)
- [0.274.0 — only the newest link opens the account](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-27x.md#02740-only-the-newest-link-opens-the-account)
- [0.272.0 — the checks started seeing what was already wrong](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-27x.md#02720-the-checks-started-seeing-what-was-already-wrong)
- [0.270.0 — `PixCharge.raw` uses the wire's spelling](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-27x.md#02700-pixchargeraw-uses-the-wires-spelling)
- [0.270.0 — `PaymentStatus` gained `UNKNOWN`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-27x.md#02700-paymentstatus-gained-unknown)
- [0.269.0 — `Charge.expires_in` became an `int`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-26x.md#02690-chargeexpires_in-became-an-int)
- [0.265.0 — an optional array in a request body became `| None`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-26x.md#02650-an-optional-array-in-a-request-body-became-none)
- [0.264.0 — the language belongs to the flow, not to each end](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-26x.md#02640-the-language-belongs-to-the-flow-not-to-each-end)
- [0.263.0 — `AdminAuthBackend` became generic](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-26x.md#02630-adminauthbackend-became-generic)
- [0.261.0 — the WebSocket router sends a `hello` first](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-26x.md#02610-the-websocket-router-sends-a-hello-first)
- [0.260.0 — the OpenPix spec was refreshed, and every method was renamed](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-26x.md#02600-the-openpix-spec-was-refreshed-and-every-method-was-renamed)
- [0.259.0 — OpenPix money becomes `int`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-25x.md#02590-openpix-money-becomes-int)
- [0.258.0 — log files now rotate](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-25x.md#02580-log-files-now-rotate)
- [0.257.0 — the agent fakes rename the parameter to `tools`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-25x.md#02570-the-agent-fakes-rename-the-parameter-to-tools)
- [0.256.0 — the `RateLimitMiddleware` 429 becomes JSON](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-25x.md#02560-the-ratelimitmiddleware-429-becomes-json)
- [0.252.0 — SQLite `:memory:` gets one connection per session](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-25x.md#02520-sqlite-memory-gets-one-connection-per-session)
- [0.251.0 — the Mercado Pago webhook signature now follows the provider's algorithm](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-25x.md#02510-the-mercado-pago-webhook-signature-now-follows-the-providers-algorithm)
- [0.234.0 — a generated model is built with its Python names, and the type-checker agrees](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-22x-0-23x.md#02340-a-generated-model-is-built-with-its-python-names-and-the-type-checker-agrees)
- [0.233.0 — two generated OpenPix enums were renamed](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-22x-0-23x.md#02330-two-generated-openpix-enums-were-renamed)
- [0.229.0 — Ollama structured output moves from `/api/generate` to `/api/chat`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-22x-0-23x.md#02290-ollama-structured-output-moves-from-apigenerate-to-apichat)
- [0.174.0 — crashes become 422s, and `order_by` is validated](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-8-0-17x.md#01740-crashes-become-422s-and-order_by-is-validated)
- [0.173.0 — a token only works where it was meant to, and caches stop being shared](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-8-0-17x.md#01730-a-token-only-works-where-it-was-meant-to-and-caches-stop-being-shared)
- [0.138.1 — `BaseAppSettings` must be the **last** base](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-8-0-17x.md#01381-baseappsettings-must-be-the-last-base)
- [0.92.0 — `payload` column on the user token](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-8-0-17x.md#0920-payload-column-on-the-user-token)
- [0.63.0 — authenticated user loaded on the request session](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-8-0-17x.md#0630-authenticated-user-loaded-on-the-request-session)
- [0.8.0 — `ServerSettings` rename](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/migration/0-8-0-17x.md#080-serversettings-rename)

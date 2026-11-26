# Referência: Web, UI e utilitários

Páginas SSR e a camada `ui`, utilitários, geolocalização, controle do host, system checks e as ferramentas de teste.

[« Índice da referência](../reference.md)

---

## SSR / web

### `tempest_fastapi_sdk.ssr`

::: tempest_fastapi_sdk.ssr
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

::: tempest_fastapi_sdk.ssr.webapp.BuildMode
::: tempest_fastapi_sdk.ssr.webapp.ShellSource
::: tempest_fastapi_sdk.ssr.flashes.flash

---

## UI (páginas, componentes, formulários, CSS)

### `tempest_fastapi_sdk.ui`

::: tempest_fastapi_sdk.ui.stylesheet.app_stylesheet

### `tempest_fastapi_sdk.ui.pages`

::: tempest_fastapi_sdk.ui.pages.page.Page
::: tempest_fastapi_sdk.ui.pages.error.ErrorPage

### `tempest_fastapi_sdk.ui.layout`

::: tempest_fastapi_sdk.ui.layout.shell.Shell
::: tempest_fastapi_sdk.ui.layout.grid.Grid

### `tempest_fastapi_sdk.ui.components`

::: tempest_fastapi_sdk.ui.components.card.Card
::: tempest_fastapi_sdk.ui.components.alert.Alert
::: tempest_fastapi_sdk.ui.components.table.DataTable
::: tempest_fastapi_sdk.ui.components.table.TableColumn
::: tempest_fastapi_sdk.ui.components.pagination.Pagination
::: tempest_fastapi_sdk.ui.components.pagination.PaginationEnvelopeLike
::: tempest_fastapi_sdk.ui.components.pagination.pagination_for
::: tempest_fastapi_sdk.ui.components.empty.EmptyState
::: tempest_fastapi_sdk.ui.components.flash.FlashMessages
::: tempest_fastapi_sdk.ui.components.flash.FlashMessage
::: tempest_fastapi_sdk.ui.components.nav.NavBar
::: tempest_fastapi_sdk.ui.components.nav.NavItem
::: tempest_fastapi_sdk.ui.components.classes.ComponentClasses
::: tempest_fastapi_sdk.ui.components.styles.component_stylesheet

### `tempest_fastapi_sdk.ui.forms`

::: tempest_fastapi_sdk.ui.forms.render.form_for
::: tempest_fastapi_sdk.ui.forms.render.render_form
::: tempest_fastapi_sdk.ui.forms.render.render_field
::: tempest_fastapi_sdk.ui.forms.introspect.fields_for
::: tempest_fastapi_sdk.ui.forms.introspect.form_spec_for
::: tempest_fastapi_sdk.ui.forms.introspect.UnsupportedFieldError
::: tempest_fastapi_sdk.ui.forms.parse.parse_form
::: tempest_fastapi_sdk.ui.forms.parse.FormRequestLike
::: tempest_fastapi_sdk.ui.forms.parse.FormResult
::: tempest_fastapi_sdk.ui.forms.parse.form_dependency
::: tempest_fastapi_sdk.ui.forms.spec.FormSpec
::: tempest_fastapi_sdk.ui.forms.spec.FieldSpec
::: tempest_fastapi_sdk.ui.forms.spec.SelectOption
::: tempest_fastapi_sdk.ui.forms.spec.FormClasses
::: tempest_fastapi_sdk.ui.forms.styles.form_stylesheet

### `tempest_fastapi_sdk.ui.css`

::: tempest_fastapi_sdk.ui.css.rules.Rule
::: tempest_fastapi_sdk.ui.css.rules.Media
::: tempest_fastapi_sdk.ui.css.rules.StyleSheet
::: tempest_fastapi_sdk.ui.css.rules.cls
::: tempest_fastapi_sdk.ui.css.tokens.ThemeTokens
::: tempest_fastapi_sdk.ui.css.router.make_css_router
::: tempest_fastapi_sdk.ui.css.router.css_response
::: tempest_fastapi_sdk.ui.css.router.stylesheet_links

---

## System checks

### `tempest_fastapi_sdk.checks`

::: tempest_fastapi_sdk.checks.messages.CheckLevelEnum
::: tempest_fastapi_sdk.checks.messages.CheckMessage
::: tempest_fastapi_sdk.checks.registry.CheckRegistry
::: tempest_fastapi_sdk.checks.registry.check
::: tempest_fastapi_sdk.checks.registry.register_check
::: tempest_fastapi_sdk.checks.registry.run_checks
::: tempest_fastapi_sdk.checks.registry.run_system_checks
::: tempest_fastapi_sdk.checks.registry.SystemCheckError

---

## Testing

### `tempest_fastapi_sdk.testing`

::: tempest_fastapi_sdk.testing.factories.ModelFactory
::: tempest_fastapi_sdk.testing.factories.seq
::: tempest_fastapi_sdk.testing.database.make_test_database
::: tempest_fastapi_sdk.testing.database.make_test_session
::: tempest_fastapi_sdk.testing.privacy.assert_subject_graph_valid
::: tempest_fastapi_sdk.testing.admin.admin_login
::: tempest_fastapi_sdk.testing.admin.admin_login_sync
::: tempest_fastapi_sdk.testing.admin.admin_csrf_token
::: tempest_fastapi_sdk.testing.admin.SyncFormClient
::: tempest_fastapi_sdk.testing.admin.AsyncFormClient
::: tempest_fastapi_sdk.testing.admin.FormResponse
::: tempest_fastapi_sdk.testing.schemas.assert_schema_metadata
::: tempest_fastapi_sdk.testing.schemas.schema_metadata_findings
::: tempest_fastapi_sdk.testing.schemas.SchemaMetadataFinding
::: tempest_fastapi_sdk.testing.schemas.LEAF_METADATA
::: tempest_fastapi_sdk.testing.schemas.NESTED_METADATA

### `tempest_fastapi_sdk.testing.fakes`

::: tempest_fastapi_sdk.testing.fakes.payment.FakePixProvider
::: tempest_fastapi_sdk.testing.fakes.payment.FakeCardProvider
::: tempest_fastapi_sdk.testing.fakes.storage.FakeObjectStorageClient
::: tempest_fastapi_sdk.testing.fakes.payment.FakePayoutProvider
::: tempest_fastapi_sdk.testing.fakes.genai.FakeTextBackend
::: tempest_fastapi_sdk.testing.fakes.genai.FakeModerationBackend
::: tempest_fastapi_sdk.testing.fakes.push.FakePushDispatcher
::: tempest_fastapi_sdk.testing.fakes.push.SentPush
::: tempest_fastapi_sdk.testing.fakes.email.FakeEmailUtils
::: tempest_fastapi_sdk.testing.fakes.email.SentEmail
::: tempest_fastapi_sdk.testing.fakes.geo.FakeGeocodingBackend
::: tempest_fastapi_sdk.testing.fakes.geo.FakeRoutingBackend
::: tempest_fastapi_sdk.testing.fakes.search.FakeWebSearchBackend
::: tempest_fastapi_sdk.testing.fakes.mercado_pago.orders_api.FakeMercadoPagoOrdersAPI
::: tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery.MercadoPagoDelivery
::: tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery.build_mercado_pago_delivery
::: tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery.order_action
::: tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery.FAKE_WEBHOOK_SECRET
::: tempest_fastapi_sdk.testing.fakes.mercado_pago.server.make_fake_orders_app

---

## Utils

### `tempest_fastapi_sdk.utils`

::: tempest_fastapi_sdk.utils.password.PasswordUtils
::: tempest_fastapi_sdk.utils.password.PasswordPolicy
::: tempest_fastapi_sdk.utils.password.PasswordPolicyViolation
::: tempest_fastapi_sdk.utils.password.PasswordViolationCodeEnum
::: tempest_fastapi_sdk.utils.password.check_password_policy
::: tempest_fastapi_sdk.utils.password.generate_password
::: tempest_fastapi_sdk.utils.password.DEFAULT_GENERATED_PASSWORD_LENGTH
::: tempest_fastapi_sdk.utils.jwt.JWTUtils
::: tempest_fastapi_sdk.utils.token_types.token_type_allowed
::: tempest_fastapi_sdk.utils.token_types.token_amr
::: tempest_fastapi_sdk.utils.token_types.token_actor
::: tempest_fastapi_sdk.utils.token_types.ACT_CLAIM
::: tempest_fastapi_sdk.utils.totp.TOTPHelper
::: tempest_fastapi_sdk.utils.totp.provisioning_qr_terminal
::: tempest_fastapi_sdk.utils.totp.provisioning_qr_data_uri
::: tempest_fastapi_sdk.utils.totp.ProvisioningQrKind
::: tempest_fastapi_sdk.utils.totp.ProvisioningQrBackground
::: tempest_fastapi_sdk.utils.totp.PROVISIONING_QR_ERROR
::: tempest_fastapi_sdk.utils.totp.PROVISIONING_QR_BORDER
::: tempest_fastapi_sdk.utils.totp.PROVISIONING_QR_SCALE
::: tempest_fastapi_sdk.utils.email.EmailUtils
::: tempest_fastapi_sdk.utils.email.EmailAttachment
::: tempest_fastapi_sdk.utils.upload.UploadUtils
::: tempest_fastapi_sdk.utils.upload.SNIFFABLE_MIMETYPES
::: tempest_fastapi_sdk.utils.upload.sniff_mime
::: tempest_fastapi_sdk.utils.upload.HEIC_BRANDS
::: tempest_fastapi_sdk.utils.upload.HEIF_STRUCTURAL_BRANDS
::: tempest_fastapi_sdk.utils.upload.read_upload_capped
::: tempest_fastapi_sdk.utils.upload.UPLOAD_READ_CHUNK_BYTES
::: tempest_fastapi_sdk.utils.color.relative_luminance
::: tempest_fastapi_sdk.utils.color.is_light
::: tempest_fastapi_sdk.utils.download.DownloadUtils
::: tempest_fastapi_sdk.utils.download.INLINE_SAFE_MEDIA_TYPES
::: tempest_fastapi_sdk.utils.media_types.guess_media_type
::: tempest_fastapi_sdk.utils.media_types.ImageMediaTypeEnum
::: tempest_fastapi_sdk.utils.fields.image_media_type_field
::: tempest_fastapi_sdk.utils.fields.AllowedImageMediaTypes
::: tempest_fastapi_sdk.utils.fields.RawTextField
::: tempest_fastapi_sdk.utils.media_types.XLSX_MEDIA_TYPE
::: tempest_fastapi_sdk.utils.media_types.DOCX_MEDIA_TYPE
::: tempest_fastapi_sdk.utils.media_types.PPTX_MEDIA_TYPE
::: tempest_fastapi_sdk.utils.images.strip_image_metadata
::: tempest_fastapi_sdk.utils.images.DEFAULT_MAX_IMAGE_PIXELS
::: tempest_fastapi_sdk.utils.images.DEFAULT_JPEG_QUALITY
::: tempest_fastapi_sdk.utils.images.DEFAULT_WEBP_QUALITY
::: tempest_fastapi_sdk.utils.file_store.FileStoreUtils
::: tempest_fastapi_sdk.utils.metrics.MetricsUtils
::: tempest_fastapi_sdk.utils.log.LogUtils
::: tempest_fastapi_sdk.core.logging.configure_root_once
::: tempest_fastapi_sdk.core.logging.reinitialize_logging
::: tempest_fastapi_sdk.core.redaction.RedactionPolicy
::: tempest_fastapi_sdk.core.redaction.RedactionFilter
::: tempest_fastapi_sdk.core.redaction.REDACTED
::: tempest_fastapi_sdk.core.redaction.DEFAULT_REDACT_KEYS
::: tempest_fastapi_sdk.core.redaction.DEFAULT_REDACT_PATTERNS
::: tempest_fastapi_sdk.core.redaction.EMAIL_PATTERN
::: tempest_fastapi_sdk.core.redaction.BEARER_PATTERN
::: tempest_fastapi_sdk.core.redaction.JWT_PATTERN
::: tempest_fastapi_sdk.core.redaction.CAPABILITY_PATH_PATTERN
::: tempest_fastapi_sdk.core.redaction.CAPABILITY_PATH_SEGMENTS
::: tempest_fastapi_sdk.core.redaction.CAPABILITY_QUERY_PATTERN
::: tempest_fastapi_sdk.core.redaction.redact_capability_url
::: tempest_fastapi_sdk.utils.throttle.AttemptThrottle
::: tempest_fastapi_sdk.utils.throttle.InMemoryThrottleBackend
::: tempest_fastapi_sdk.utils.signed_url.sign_path
::: tempest_fastapi_sdk.utils.signed_url.verify_path
::: tempest_fastapi_sdk.utils.vapid.VAPID_SUBJECT_PATTERN
::: tempest_fastapi_sdk.utils.vapid.check_vapid_subject
::: tempest_fastapi_sdk.utils.vapid.is_valid_vapid_subject
::: tempest_fastapi_sdk.utils.signed_url.SIGNED_URL_EXPIRES_PARAM
::: tempest_fastapi_sdk.utils.signed_url.SIGNED_URL_SIGNATURE_PARAM
::: tempest_fastapi_sdk.utils.locations.UFEnum
::: tempest_fastapi_sdk.utils.locations.RegionEnum
::: tempest_fastapi_sdk.utils.locations.StateBR
::: tempest_fastapi_sdk.utils.locations.CityBR
::: tempest_fastapi_sdk.utils.locations.list_states
::: tempest_fastapi_sdk.utils.locations.get_state
::: tempest_fastapi_sdk.utils.locations.cities_by_uf
::: tempest_fastapi_sdk.utils.locations.states_by_region
::: tempest_fastapi_sdk.utils.locations.is_valid_uf
::: tempest_fastapi_sdk.utils.locations.normalize_uf
::: tempest_fastapi_sdk.utils.locations.is_valid_city
::: tempest_fastapi_sdk.utils.locations.normalize_city
::: tempest_fastapi_sdk.utils.locations.uf_choices
::: tempest_fastapi_sdk.utils.locations.region_choices
::: tempest_fastapi_sdk.utils.locations.city_choices
::: tempest_fastapi_sdk.utils.currency.parse_currency_br
::: tempest_fastapi_sdk.utils.currency.format_currency_br
::: tempest_fastapi_sdk.utils.currency.format_percent_br
::: tempest_fastapi_sdk.utils.currency.format_quantity_br
::: tempest_fastapi_sdk.utils.currency.quantize_money
::: tempest_fastapi_sdk.utils.currency.CENT
::: tempest_fastapi_sdk.utils.currency.HUNDRED

---

## Geolocalização

### `tempest_fastapi_sdk.geo`

::: tempest_fastapi_sdk.geo
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

::: tempest_fastapi_sdk.geo.distance.EARTH_RADIUS_KM
::: tempest_fastapi_sdk.geo.estimate.DEFAULT_CIRCUITY_FACTOR
::: tempest_fastapi_sdk.geo.estimate.DEFAULT_CAR_SPEED_KMH
::: tempest_fastapi_sdk.geo.estimate.DEFAULT_MODE_DURATION_FACTORS
::: tempest_fastapi_sdk.geo.routing.DEFAULT_OSRM_BASE_URL
::: tempest_fastapi_sdk.geo.routing.DEFAULT_MODE_PROFILES
::: tempest_fastapi_sdk.geo.routing.DEFAULT_OSRM_MAX_POINTS
::: tempest_fastapi_sdk.geo.geocoding.DEFAULT_NOMINATIM_BASE_URL
::: tempest_fastapi_sdk.geo.cache.DEFAULT_GEOCODE_CACHE_PRECISION
::: tempest_fastapi_sdk.geo.cache.DEFAULT_GEOCODE_CACHE_TTL_SECONDS
::: tempest_fastapi_sdk.geo.cache.DEFAULT_GEOCODE_CACHE_KEY_PREFIX
::: tempest_fastapi_sdk.geo.br.UF_CENTROIDS
::: tempest_fastapi_sdk.geo.municipalities.LOCATIVE_CUES
::: tempest_fastapi_sdk.geo.municipalities.MAX_NAME_TOKENS
::: tempest_fastapi_sdk.geo.municipalities.STATE_SEPARATORS
::: tempest_fastapi_sdk.geo.municipalities.UNCUED_SINGLE_WORD_FACTOR
::: tempest_fastapi_sdk.geo.br.CEP_PATTERN
::: tempest_fastapi_sdk.geo.live.LIVE_POSITION_MAX_CLOCK_SKEW
::: tempest_fastapi_sdk.geo.live.DEFAULT_LIVE_POSITION_KEY_PREFIX
::: tempest_fastapi_sdk.geo.live.DEFAULT_LIVE_POSITION_TTL_SECONDS
::: tempest_fastapi_sdk.geo.live.DEFAULT_LIVE_POSITION_INTERVAL_SECONDS
::: tempest_fastapi_sdk.geo.live.DEFAULT_LIVE_POSITION_MIN_DISTANCE_M
::: tempest_fastapi_sdk.geo.live.DEFAULT_LIVE_POSITION_EVENT
::: tempest_fastapi_sdk.geo.live.DEFAULT_LIVE_POSITION_CLEARED_EVENT
::: tempest_fastapi_sdk.geo.live.AudienceResolver
::: tempest_fastapi_sdk.geo.live.EtaTargetsResolver
::: tempest_fastapi_sdk.geo.live_router.LIVE_POSITION_INACTIVE_ERROR
::: tempest_fastapi_sdk.geo.road_graph.ROAD_GRID_CELL_M
::: tempest_fastapi_sdk.geo.map_matching.ROAD_MATCH_DEFAULTS
::: tempest_fastapi_sdk.geo.osm.OSM_DRIVABLE_HIGHWAYS
::: tempest_fastapi_sdk.geo.osm.OSM_GEOJSON_COORDINATE_DIGITS
::: tempest_fastapi_sdk.geo.osm.OSM_GRAPH_CACHE_SIZE
::: tempest_fastapi_sdk.geo.traffic.DEFAULT_TRAFFIC_EVENT
::: tempest_fastapi_sdk.geo.traffic.DEFAULT_TRAFFIC_PRIVACY
::: tempest_fastapi_sdk.geo.traffic.DEFAULT_TRAFFIC_SNAPSHOT_KEY
::: tempest_fastapi_sdk.geo.traffic.TRAFFIC_MIN_DEVICES_FLOOR
::: tempest_fastapi_sdk.geo.traffic.TrafficAudienceResolver
::: tempest_fastapi_sdk.geo.traffic.TrafficDimensionsResolver
::: tempest_fastapi_sdk.geo.traffic_router.TRAFFIC_INVALID_BBOX_ERROR
::: tempest_fastapi_sdk.geo.match_quality.DEFAULT_MATCH_QUALITY_WINDOW_SECONDS
::: tempest_fastapi_sdk.geo.match_quality.MATCH_QUALITY_MAX_DIMENSIONS
::: tempest_fastapi_sdk.geo.match_quality.MATCH_QUALITY_REFERENCES
::: tempest_fastapi_sdk.geo.match_quality.MATCH_QUALITY_REFERENCE_KUBICKA
::: tempest_fastapi_sdk.geo.match_quality.MATCH_QUALITY_REFERENCE_BRAZIL_OSM
::: tempest_fastapi_sdk.geo.match_quality.SNAP_DISTANCE_BUCKETS_M
::: tempest_fastapi_sdk.geo.match_quality.SAMPLING_INTERVAL_BUCKETS_S
::: tempest_fastapi_sdk.geo.match_quality.STRAIGHT_HALF_M
::: tempest_fastapi_sdk.geo.match_quality.STRAIGHT_MAX_TURN_DEG
::: tempest_fastapi_sdk.geo.match_quality.PARALLEL_MAX_DEG
::: tempest_fastapi_sdk.geo.match_quality.PARALLEL_MIN_OFFSET_M
::: tempest_fastapi_sdk.geo.match_quality.UNKNOWN_DIMENSION_VALUE
::: tempest_fastapi_sdk.geo.road_inference.DEFAULT_DENSITY_CELL_M
::: tempest_fastapi_sdk.geo.road_inference.DEFAULT_DENSITY_BLOCK_CELLS
::: tempest_fastapi_sdk.geo.road_inference.DEFAULT_DENSITY_EPOCH_SECONDS
::: tempest_fastapi_sdk.geo.road_inference.DEFAULT_OFF_ROAD_M

---

## Host (controle da máquina)

### `tempest_fastapi_sdk.hostbridge`

::: tempest_fastapi_sdk.hostbridge
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

::: tempest_fastapi_sdk.hostbridge.config.DEFAULT_COMMAND_TIMEOUT
::: tempest_fastapi_sdk.hostbridge.config.DEFAULT_MAX_FILE_READ_BYTES

"""`KafkaSettings` — protocol and mechanism mapped onto FastStream's security."""

from __future__ import annotations

import datetime
import logging
import ssl
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from tempest_fastapi_sdk import EnvironmentSettings
from tempest_fastapi_sdk.settings import (
    KafkaSaslMechanismEnum,
    KafkaSecurityProtocolEnum,
    KafkaSettings,
)

faststream_security = pytest.importorskip("faststream.security")
kafka_security = pytest.importorskip("faststream.kafka.security")

PASSWORD: str = "kafka-pa55word-sentinel"
"""A password distinctive enough that finding it in text is not a coincidence."""


class Isolated(KafkaSettings):
    """The mixin without the developer's ``.env``."""

    model_config = SettingsConfigDict(env_file=None)


class ProductionKafka(EnvironmentSettings, KafkaSettings):
    """The mixin under the production guard."""

    model_config = SettingsConfigDict(env_file=None)


def _sasl(protocol: str, mechanism: str = "PLAIN") -> Isolated:
    """Build settings for a SASL protocol with a username and password.

    Args:
        protocol (str): ``SASL_PLAINTEXT`` or ``SASL_SSL``.
        mechanism (str): The SASL mechanism.

    Returns:
        Isolated: The settings.
    """
    return Isolated(
        KAFKA_SECURITY_PROTOCOL=protocol,
        KAFKA_SASL_MECHANISM=mechanism,
        KAFKA_SASL_USERNAME="billing",
        KAFKA_SASL_PASSWORD=PASSWORD,
    )


def _client_certificate(directory: Path) -> tuple[Path, Path]:
    """Write a self-signed client certificate and its encrypted key.

    Args:
        directory (Path): Where the two PEM files go.

    Returns:
        tuple[Path, Path]: ``(certfile, keyfile)``; the key is encrypted
        with ``"key-pass"``.
    """
    x509 = pytest.importorskip("cryptography.x509")
    hashes = pytest.importorskip("cryptography.hazmat.primitives.hashes")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    oid = pytest.importorskip("cryptography.x509.oid")

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(oid.NameOID.COMMON_NAME, "billing")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    certfile = directory / "client.pem"
    keyfile = directory / "client.key"
    certfile.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"key-pass"),
        )
    )
    return certfile, keyfile


class TestDefaults:
    def test_defaults_reach_a_local_broker_without_security(self) -> None:
        settings = Isolated()

        assert settings.KAFKA_BOOTSTRAP_SERVERS == ["localhost:9092"]
        assert settings.KAFKA_SECURITY_PROTOCOL is KafkaSecurityProtocolEnum.PLAINTEXT
        assert settings.kafka_kwargs() == {"security": None}
        assert settings.kafka_ssl_context() is None

    def test_environment_variables_are_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", '["k1:9093", "k2:9093"]')
        monkeypatch.setenv("KAFKA_CLIENT_ID", "billing-api")
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "SCRAM-SHA-512")
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "billing")
        monkeypatch.setenv("KAFKA_SASL_PASSWORD", PASSWORD)

        settings = Isolated()

        assert settings.KAFKA_BOOTSTRAP_SERVERS == ["k1:9093", "k2:9093"]
        assert settings.KAFKA_SASL_MECHANISM is KafkaSaslMechanismEnum.SCRAM_SHA_512
        assert settings.KAFKA_SASL_PASSWORD.get_secret_value() == PASSWORD
        assert settings.kafka_kwargs()["client_id"] == "billing-api"

    def test_plaintext_kwargs_do_not_import_faststream(self) -> None:
        """The default path works where the ``[queue-kafka]`` extra is absent."""
        code = (
            "import sys\n"
            "sys.modules['faststream'] = None\n"
            "from tempest_fastapi_sdk.settings import KafkaSettings\n"
            "print(KafkaSettings(_env_file=None).kafka_kwargs())\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )

        assert completed.stdout.strip() == "{'security': None}"


class TestSecurityMapping:
    """Each protocol and mechanism reaches aiokafka as the right keywords."""

    def test_ssl_is_base_security_with_a_verifying_context(self) -> None:
        security = Isolated(KAFKA_SECURITY_PROTOCOL="SSL").kafka_kwargs()["security"]
        parsed = kafka_security.parse_security(security)

        assert type(security) is faststream_security.BaseSecurity
        assert parsed["security_protocol"] == "SSL"
        assert isinstance(parsed["ssl_context"], ssl.SSLContext)
        assert parsed["ssl_context"].verify_mode is ssl.CERT_REQUIRED
        assert parsed["ssl_context"].check_hostname is True

    @pytest.mark.parametrize(
        ("mechanism", "expected_class"),
        [
            ("PLAIN", "SASLPlaintext"),
            ("SCRAM-SHA-256", "SASLScram256"),
            ("SCRAM-SHA-512", "SASLScram512"),
        ],
    )
    @pytest.mark.parametrize(
        ("protocol", "has_tls"),
        [("SASL_PLAINTEXT", False), ("SASL_SSL", True)],
    )
    def test_sasl_mechanism_and_protocol(
        self,
        mechanism: str,
        expected_class: str,
        protocol: str,
        has_tls: bool,
    ) -> None:
        security = _sasl(protocol, mechanism).kafka_kwargs()["security"]
        parsed = kafka_security.parse_security(security)

        assert type(security) is getattr(faststream_security, expected_class)
        assert parsed["security_protocol"] == protocol
        assert parsed["sasl_mechanism"] == mechanism
        assert parsed["sasl_plain_username"] == "billing"
        assert parsed["sasl_plain_password"] == PASSWORD
        assert isinstance(parsed["ssl_context"], ssl.SSLContext) is has_tls

    def test_cafile_is_trusted(self, tmp_path: Path) -> None:
        certfile, _ = _client_certificate(tmp_path)

        context = Isolated(
            KAFKA_SECURITY_PROTOCOL="SSL", KAFKA_SSL_CAFILE=str(certfile)
        ).kafka_ssl_context()

        assert context is not None
        subjects = [
            dict(entry[0] for entry in ca["subject"]) for ca in context.get_ca_certs()
        ]
        assert {"commonName": "billing"} in subjects

    def test_client_certificate_with_encrypted_key_loads(self, tmp_path: Path) -> None:
        certfile, keyfile = _client_certificate(tmp_path)
        settings = Isolated(
            KAFKA_SECURITY_PROTOCOL="SSL",
            KAFKA_SSL_CERTFILE=str(certfile),
            KAFKA_SSL_KEYFILE=str(keyfile),
            KAFKA_SSL_KEY_PASSWORD="key-pass",
        )

        assert settings.kafka_ssl_context() is not None

    def test_wrong_key_password_fails_at_build(self, tmp_path: Path) -> None:
        certfile, keyfile = _client_certificate(tmp_path)
        settings = Isolated(
            KAFKA_SECURITY_PROTOCOL="SSL",
            KAFKA_SSL_CERTFILE=str(certfile),
            KAFKA_SSL_KEYFILE=str(keyfile),
            KAFKA_SSL_KEY_PASSWORD="wrong",
        )

        with pytest.raises(ssl.SSLError):
            settings.kafka_ssl_context()


class TestRefusedCombinations:
    def test_sasl_without_username(self) -> None:
        with pytest.raises(ValidationError, match="KAFKA_SASL_USERNAME"):
            Isolated(KAFKA_SECURITY_PROTOCOL="SASL_SSL")

    @pytest.mark.parametrize("protocol", ["PLAINTEXT", "SASL_PLAINTEXT"])
    def test_tls_file_without_tls(self, protocol: str) -> None:
        with pytest.raises(ValidationError, match="KAFKA_SSL_CAFILE"):
            Isolated(
                KAFKA_SECURITY_PROTOCOL=protocol,
                KAFKA_SASL_USERNAME="billing",
                KAFKA_SSL_CAFILE="/etc/kafka/ca.pem",
            )

    def test_keyfile_without_certfile(self) -> None:
        with pytest.raises(ValidationError, match="KAFKA_SSL_CERTFILE"):
            Isolated(
                KAFKA_SECURITY_PROTOCOL="SSL",
                KAFKA_SSL_KEYFILE="/etc/kafka/client.key",
            )

    def test_unknown_protocol(self) -> None:
        with pytest.raises(ValidationError):
            Isolated(KAFKA_SECURITY_PROTOCOL="TLS")

    def test_empty_server_list(self) -> None:
        with pytest.raises(ValidationError):
            Isolated(KAFKA_BOOTSTRAP_SERVERS=[])


class TestPasswordStaysOut:
    def test_repr_and_str_mask_the_password(self) -> None:
        settings = _sasl("SASL_SSL")

        assert PASSWORD not in repr(settings)
        assert PASSWORD not in str(settings)
        assert PASSWORD not in settings.model_dump_json()

    def test_logging_the_settings_masks_the_password(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        settings = _sasl("SASL_SSL")

        with caplog.at_level(logging.INFO):
            logging.getLogger("kafka-settings-test").info("settings: %s", settings)

        assert "**********" in caplog.text
        assert PASSWORD not in caplog.text

    def test_validation_error_does_not_echo_the_password(self) -> None:
        with pytest.raises(ValidationError) as caught:
            Isolated(
                KAFKA_SECURITY_PROTOCOL="SASL_SSL",
                KAFKA_SASL_PASSWORD=PASSWORD,
            )

        assert PASSWORD not in str(caught.value)


class TestProductionViolations:
    def test_sasl_ssl_with_password_is_accepted(self) -> None:
        settings = ProductionKafka(
            ENV="production",
            KAFKA_SECURITY_PROTOCOL="SASL_SSL",
            KAFKA_SASL_USERNAME="billing",
            KAFKA_SASL_PASSWORD=PASSWORD,
        )

        assert settings.production_violations() == []

    def test_plain_password_in_clear_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="KAFKA_SECURITY_PROTOCOL"):
            ProductionKafka(
                ENV="production",
                KAFKA_SECURITY_PROTOCOL="SASL_PLAINTEXT",
                KAFKA_SASL_USERNAME="billing",
                KAFKA_SASL_PASSWORD=PASSWORD,
            )

    def test_scram_over_plaintext_is_accepted(self) -> None:
        """SCRAM never sends the password itself, so only PLAIN is refused."""
        settings = ProductionKafka(
            ENV="production",
            KAFKA_SECURITY_PROTOCOL="SASL_PLAINTEXT",
            KAFKA_SASL_MECHANISM="SCRAM-SHA-256",
            KAFKA_SASL_USERNAME="billing",
            KAFKA_SASL_PASSWORD=PASSWORD,
        )

        assert settings.production_violations() == []

    def test_empty_password_under_sasl_is_refused(self) -> None:
        settings = Isolated(
            KAFKA_SECURITY_PROTOCOL="SASL_SSL",
            KAFKA_SASL_USERNAME="billing",
        )

        assert settings.production_violations() == [
            "KAFKA_SASL_PASSWORD: empty under a SASL security protocol"
        ]

    def test_violation_names_the_field_not_the_value(self) -> None:
        settings = _sasl("SASL_PLAINTEXT")

        violations = settings.production_violations()

        assert violations and all(PASSWORD not in entry for entry in violations)

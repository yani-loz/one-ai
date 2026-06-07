"""Unit tests for app.core.config — URL assembly, environment flags, and the
fail-closed production-secret guard (no DB)."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.exceptions import InsecureConfigurationError

_SECURE_JWT = "a-strong-random-secret-value-32-bytes-long!!"
_SECURE_DB_PASSWORD = "a-strong-db-password"


def test_database_url_uses_asyncpg_driver_and_parts() -> None:
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_host="h",
        postgres_port=1,
        postgres_db="d",
    )

    assert settings.database_url == "postgresql+asyncpg://u:p@h:1/d"


def test_is_production_true_for_production_env() -> None:
    settings = Settings(
        app_env="production", jwt_secret=_SECURE_JWT, postgres_password=_SECURE_DB_PASSWORD
    )

    assert settings.is_production is True


def test_is_production_false_for_local_env() -> None:
    assert Settings(app_env="local").is_production is False


def test_local_env_tolerates_insecure_default_secrets() -> None:
    # Only the explicit dev/test envs are exempt: dev convenience defaults are fine locally.
    settings = Settings(
        app_env="local",
        jwt_secret="dev-only-insecure-secret-change-me-in-prod",
        postgres_password="oneai",
    )

    assert settings.requires_secure_secrets is False


def test_test_env_tolerates_insecure_default_secrets() -> None:
    # app_env='test' is the other exempt env (CI/local suites run with dev defaults).
    settings = Settings(
        app_env="test",
        jwt_secret="dev-only-insecure-secret-change-me-in-prod",
        postgres_password="oneai",
    )

    assert settings.requires_secure_secrets is False


def test_requires_secure_secrets_false_only_for_dev_and_test() -> None:
    assert Settings(app_env="local").requires_secure_secrets is False
    assert Settings(app_env="test").requires_secure_secrets is False
    assert Settings(app_env="LOCAL").requires_secure_secrets is False  # case-insensitive


def test_requires_secure_secrets_true_for_staging_production_and_typos() -> None:
    secure = {"jwt_secret": _SECURE_JWT, "postgres_password": _SECURE_DB_PASSWORD}
    assert Settings(app_env="staging", **secure).requires_secure_secrets is True
    assert Settings(app_env="production", **secure).requires_secure_secrets is True
    # An unrecognized / typo'd env is treated as non-dev — fail closed, never exempt.
    assert Settings(app_env="prod", **secure).requires_secure_secrets is True
    assert Settings(app_env="developmnt", **secure).requires_secure_secrets is True


def test_production_with_default_jwt_secret_refuses_to_boot() -> None:
    with pytest.raises(InsecureConfigurationError, match="JWT_SECRET"):
        Settings(
            app_env="production",
            jwt_secret="dev-only-insecure-secret-change-me-in-prod",
            postgres_password=_SECURE_DB_PASSWORD,
        )


def test_production_with_default_postgres_password_refuses_to_boot() -> None:
    with pytest.raises(InsecureConfigurationError, match="POSTGRES_PASSWORD"):
        Settings(
            app_env="production",
            jwt_secret=_SECURE_JWT,
            postgres_password="oneai",
        )


def test_production_with_secure_secrets_boots() -> None:
    settings = Settings(
        app_env="production", jwt_secret=_SECURE_JWT, postgres_password=_SECURE_DB_PASSWORD
    )

    assert settings.jwt_secret == _SECURE_JWT


def test_staging_with_default_jwt_secret_refuses_to_boot() -> None:
    # The headline gap the generalization closes: staging previously slipped through the
    # production-only gate and booted signing tokens with the public dev secret.
    with pytest.raises(InsecureConfigurationError, match="JWT_SECRET"):
        Settings(
            app_env="staging",
            jwt_secret="dev-only-insecure-secret-change-me-in-prod",
            postgres_password=_SECURE_DB_PASSWORD,
        )


def test_unknown_env_with_default_jwt_secret_refuses_to_boot() -> None:
    # A typo'd / unrecognized app_env must fail closed, not silently fall back to dev defaults.
    with pytest.raises(InsecureConfigurationError, match="JWT_SECRET"):
        Settings(
            app_env="prod",
            jwt_secret="dev-only-insecure-secret-change-me-in-prod",
            postgres_password=_SECURE_DB_PASSWORD,
        )


def test_staging_with_secure_secrets_boots() -> None:
    # Positive control: it's the insecure SECRET that gates the boot, not the env name —
    # staging with real secrets must start.
    settings = Settings(
        app_env="staging", jwt_secret=_SECURE_JWT, postgres_password=_SECURE_DB_PASSWORD
    )

    assert settings.requires_secure_secrets is True
    assert settings.jwt_secret == _SECURE_JWT


def test_production_with_blank_jwt_secret_refuses_to_boot() -> None:
    # The blank-secret bypass (testing TC-SG-006): an empty key is NOT the dev default, but it
    # is not a valid HS256 key either — the strength floor must catch it, not let it slip through.
    with pytest.raises(InsecureConfigurationError, match="JWT_SECRET"):
        Settings(app_env="production", jwt_secret="", postgres_password=_SECURE_DB_PASSWORD)


def test_production_with_whitespace_only_jwt_secret_refuses_to_boot() -> None:
    # ' ' round-trips as a forgeable HS256 key in PyJWT (TC-SG-006); whitespace must strip to
    # zero length and be rejected, never counted toward the strength floor.
    with pytest.raises(InsecureConfigurationError, match="JWT_SECRET"):
        Settings(app_env="production", jwt_secret="   ", postgres_password=_SECURE_DB_PASSWORD)


def test_production_with_short_jwt_secret_refuses_to_boot() -> None:
    # A 31-byte key is one byte under the RFC 7518 HS256 floor (32 bytes) -> rejected.
    with pytest.raises(InsecureConfigurationError, match="JWT_SECRET"):
        Settings(app_env="production", jwt_secret="x" * 31, postgres_password=_SECURE_DB_PASSWORD)


def test_production_with_exactly_32_byte_jwt_secret_boots() -> None:
    # Boundary: exactly the HS256 minimum (32 bytes) is acceptable and boots.
    settings = Settings(
        app_env="production", jwt_secret="x" * 32, postgres_password=_SECURE_DB_PASSWORD
    )

    assert settings.jwt_secret == "x" * 32


def test_local_env_tolerates_short_jwt_secret() -> None:
    # The strength floor is a non-dev gate only: local/test still boot with a weak/short secret,
    # so the dev stack and CI are unaffected by the hardening.
    settings = Settings(app_env="local", jwt_secret="short", postgres_password="oneai")

    assert settings.requires_secure_secrets is False


def test_production_with_whitespace_padded_default_jwt_secret_refuses_to_boot() -> None:
    # Cross-vendor finding F1: the PUBLIC dev default with surrounding whitespace is != the raw
    # default, so it slipped the exact-match denylist yet still signed forgeable tokens. The
    # validator canonicalizes (strip) BEFORE the denylist, so the padded default is now caught —
    # a trailing newline copy-pasted from .env.example is a realistic, zero-brute-force misconfig.
    with pytest.raises(InsecureConfigurationError, match="JWT_SECRET"):
        Settings(
            app_env="production",
            jwt_secret=" dev-only-insecure-secret-change-me-in-prod\n",
            postgres_password=_SECURE_DB_PASSWORD,
        )


def test_production_with_trailing_newline_secret_canonicalizes_and_boots() -> None:
    # Canonicalization must NOT fail-close a legitimate secret that arrives with a trailing newline
    # (common from a Docker/Vault secret mount): it strips to the clean value, boots, and stores the
    # canonical form so token signing uses exactly what was validated (no validate-vs-sign drift).
    settings = Settings(
        app_env="production",
        jwt_secret=_SECURE_JWT + "\n",
        postgres_password=_SECURE_DB_PASSWORD,
    )

    assert settings.jwt_secret == _SECURE_JWT


def test_staging_with_short_jwt_secret_refuses_to_boot() -> None:
    # Parity: the strength floor is a property of requires_secure_secrets, not of production alone —
    # staging must reject a sub-32-byte secret exactly as production does.
    with pytest.raises(InsecureConfigurationError, match="JWT_SECRET"):
        Settings(app_env="staging", jwt_secret="x" * 31, postgres_password=_SECURE_DB_PASSWORD)

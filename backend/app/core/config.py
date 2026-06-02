"""
Role: Centralized application configuration loaded from environment / `.env`.
Used by: app.core.database, app.core.tenant, app.main, and the Alembic env.
Depends on: pydantic-settings (external); app.core.exceptions (InsecureConfigurationError,
            itself dependency-free — no import cycle).
Key invariants:
  - The ONLY place environment variables are read; no other module touches os.environ.
  - Secrets arrive via env (.env locally, Docker secrets in production) — never hardcoded.
  - get_settings() is cached: configuration is parsed once per process.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import InsecureConfigurationError

# Known-insecure local-dev defaults. Every non-dev environment must override both; the
# model validator on Settings refuses to boot if either still holds whenever app_env is
# anything other than the explicit dev/test envs (local | test) — so staging, production,
# or a typo'd env all fail closed instead of silently signing tokens with the public key.
_INSECURE_JWT_SECRET = "dev-only-insecure-secret-change-me-in-prod"
_INSECURE_POSTGRES_PASSWORD = "oneai"

# Minimum HS256 signing-key length. RFC 7518 §3.2: an HMAC key MUST be at least as long as
# the hash output — 256 bits / 32 bytes for HS256. The boot guard rejects any non-dev JWT
# secret below this (blank, whitespace-only, or short) even when it is NOT the literal dev
# default, because a weak key signs forgeable tokens just the same (testing TC-SG-006). No
# equivalent strength floor is imposed on POSTGRES_PASSWORD: a blank value can be legitimate
# (IAM / peer / cert auth) and a genuinely wrong password fails the DB connection loudly on
# its own — so a floor there would fail-close a valid deployment.
_MIN_JWT_SECRET_BYTES = 32


class Settings(BaseSettings):
    """Strongly-typed application settings.

    Contract: every field is typed and carries a safe local-dev default so the
    stack boots with zero config; production overrides each value via env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # — Runtime —
    # local | test → dev defaults tolerated. Anything else (staging | production | a typo)
    # MUST supply real secrets (see requires_secure_secrets) or the process refuses to boot.
    app_env: str = "local"
    app_name: str = "One AI"
    app_version: str = "0.1.0"

    # — PostgreSQL (single source of truth for DB credentials) —
    postgres_user: str = "oneai"
    postgres_password: str = _INSECURE_POSTGRES_PASSWORD
    postgres_db: str = "oneai"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # — Tenancy —
    # Fixed demo org used ONLY by the dev seed script (see SPEC §7). Tenant context
    # in every request now derives from the verified JWT claim, never this value.
    default_org_id: str = "00000000-0000-0000-0000-000000000001"

    # — Identity / JWT (app.identity) —
    # jwt_secret signs HS256 access tokens. The dev default below is INSECURE and MUST be
    # overridden via env in staging/production with a strong key of at least
    # _MIN_JWT_SECRET_BYTES (a shorter/blank one is refused at boot — never ship either).
    jwt_secret: str = _INSECURE_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7

    # — CORS — origins the browser is allowed to call the API from —
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8000"]

    # — Limits — coarse request-body ceiling (bytes); full enforcement at the proxy. —
    max_request_body_bytes: int = 1_048_576  # 1 MiB

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver), assembled from the parts above."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        """True when running in the production environment.

        Kept narrow (production-only) on purpose: scripts.seed_identity gates demo-seed
        refusal on this. The dev-secret boot guard uses requires_secure_secrets instead,
        which is broader — every non-dev env, not just production.
        """
        return self.app_env.lower() == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def requires_secure_secrets(self) -> bool:
        """True for every environment that must NOT run with the dev-default secrets.

        Fail-closed by design: ONLY the explicit dev/test envs (local | test) may use the
        convenience defaults. Staging, production, AND any unrecognized/typo'd app_env all
        require real secrets — so an unknown env boots loud rather than silently signing
        tokens with the public dev key. This is the whole-of-non-dev generalization of the
        old production-only gate (a staging box or a typo'd APP_ENV previously slipped through).
        """
        return self.app_env.lower() not in {"local", "test"}

    @model_validator(mode="after")
    def _forbid_insecure_defaults_outside_dev(self) -> Settings:
        """Refuse to boot outside dev/test on a dev-default OR weak signing secret.

        Fail-closed guard with two layers, active only when requires_secure_secrets is True
        (every env except local | test):
          - Denylist: the known dev-default JWT secret / DB password must never reach a
            non-dev env (staging, production, or an unrecognized app_env).
          - Strength: the JWT signing key must be a real HS256 key — at least
            _MIN_JWT_SECRET_BYTES (RFC 7518 §3.2). A blank, whitespace-only, or short key is
            rejected even though it is not the literal dev default, because it signs forgeable
            tokens just the same (testing TC-SG-006). POSTGRES_PASSWORD keeps the denylist-only
            check (see _MIN_JWT_SECRET_BYTES for why no strength floor applies there).

        Raises InsecureConfigurationError (a hard boot failure) naming each offending secret.
        Only app_env 'local' or 'test' is exempt.
        """
        if not self.requires_secure_secrets:
            return self

        insecure: list[str] = []
        if self.jwt_secret == _INSECURE_JWT_SECRET:
            insecure.append("JWT_SECRET (dev default)")
        elif len(self.jwt_secret.strip().encode("utf-8")) < _MIN_JWT_SECRET_BYTES:
            insecure.append(f"JWT_SECRET (blank or under {_MIN_JWT_SECRET_BYTES} bytes)")
        if self.postgres_password == _INSECURE_POSTGRES_PASSWORD:
            insecure.append("POSTGRES_PASSWORD (dev default)")

        if insecure:
            raise InsecureConfigurationError(
                f"Refusing to start with app_env={self.app_env!r} while using insecure "
                f"secret(s): {', '.join(insecure)}. Provide strong values via environment / "
                "secret manager. Only app_env 'local' or 'test' may use the dev defaults."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached Settings instance."""
    return Settings()

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

# Known-insecure local-dev defaults. Production must override both; the model
# validator on Settings refuses to boot if either still holds when running in prod.
_INSECURE_JWT_SECRET = "dev-only-insecure-secret-change-me-in-prod"
_INSECURE_POSTGRES_PASSWORD = "oneai"


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
    app_env: str = "local"  # local | staging | production
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
    # jwt_secret signs HS256 access tokens. The dev default below is INSECURE and
    # MUST be overridden via env in staging/production — never ship it.
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
        """True when running in the production environment."""
        return self.app_env.lower() == "production"

    @model_validator(mode="after")
    def _forbid_insecure_defaults_in_production(self) -> Settings:
        """Refuse to boot in production while a known dev-default secret is unchanged.

        Fail-closed guard: a production process must never sign tokens with the public
        default JWT key or use the default database password. Raises
        InsecureConfigurationError (a hard boot failure) naming each offending secret.
        """
        if not self.is_production:
            return self
        insecure = [
            name
            for name, value, default in (
                ("JWT_SECRET", self.jwt_secret, _INSECURE_JWT_SECRET),
                ("POSTGRES_PASSWORD", self.postgres_password, _INSECURE_POSTGRES_PASSWORD),
            )
            if value == default
        ]
        if insecure:
            raise InsecureConfigurationError(
                "Refusing to start in production with insecure default secret(s): "
                f"{', '.join(insecure)}. Provide them via environment / secret manager."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached Settings instance."""
    return Settings()

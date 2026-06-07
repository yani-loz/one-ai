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

# Dev-default passwords for the two least-privilege runtime DB roles (the RLS role split — see
# app.core.database). Like the secrets above, every non-dev environment MUST override both, or the
# model validator refuses to boot. Single-sourced: scripts.provision_roles sets each role's LOGIN
# password to THIS value and the app connects with it — one env var per role (ONEAI_APP_PASSWORD /
# ONEAI_GLOBAL_PASSWORD), so the connection password and the provisioned password cannot drift.
_INSECURE_ONEAI_APP_PASSWORD = "dev-only-oneai-app-pw-change-me"
_INSECURE_ONEAI_GLOBAL_PASSWORD = "dev-only-oneai-global-pw-change-me"

# Minimum HS256 signing-key length. RFC 7518 §3.2: an HMAC key MUST be at least as long as
# the hash output — 256 bits / 32 bytes for HS256. The boot guard rejects any non-dev JWT
# secret below this (blank, whitespace-only, or short) even when it is NOT the literal dev
# default, because a weak key signs forgeable tokens just the same (testing TC-SG-006). This
# is a LENGTH floor, not an entropy check: a long but low-entropy key (e.g. 32 identical bytes)
# still passes — operators must supply a high-entropy random value; the gate only rules out the
# obviously-too-short. No equivalent strength floor is imposed on POSTGRES_PASSWORD: a blank
# value can be legitimate (IAM / peer / cert auth) and a genuinely wrong password fails the DB
# connection loudly on its own — so a floor there would fail-close a valid deployment.
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
    # postgres_user is the OWNER/superuser role. It runs Alembic/DDL and provisions the two
    # runtime roles below — it does NOT serve HTTP. Runtime connections use app/global roles.
    postgres_user: str = "oneai"
    postgres_password: str = _INSECURE_POSTGRES_PASSWORD
    postgres_db: str = "oneai"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # — Least-privilege runtime DB roles (the RLS role split; see app.core.database) —
    # The running app connects as these, never as the owner above:
    #   oneai_app    — non-owner, NO BYPASSRLS -> tenant engine; RLS enforces on it.
    #   oneai_global — non-owner, BYPASSRLS    -> global engine; cross/pre-org flows.
    # Passwords are env-supplied (ONEAI_APP_PASSWORD / ONEAI_GLOBAL_PASSWORD); provision_roles
    # sets each role's LOGIN password to the SAME value, so they never drift from the connection.
    app_db_user: str = "oneai_app"
    oneai_app_password: str = _INSECURE_ONEAI_APP_PASSWORD
    global_db_user: str = "oneai_global"
    oneai_global_password: str = _INSECURE_ONEAI_GLOBAL_PASSWORD

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

    # — Connectors (app.connectors) —
    # AES-256-GCM key material encrypting stored connector credentials (e.g. IMAP app
    # passwords) at rest. The dev default below is INSECURE; non-dev envs must override it with
    # a strong value (e.g. `openssl rand -base64 32`). Unlike jwt_secret this is NOT checked by
    # the boot guard — it is validated lazily in connectors.security.credential_cipher only when
    # a connector is actually used, so a deployment with no connectors still boots. This literal
    # MUST equal _INSECURE_CONNECTOR_KEY in that module.
    connector_secret_key: str = "dev-only-insecure-connector-secret-change-me"

    # — CORS — origins the browser is allowed to call the API from —
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8000"]

    # — Limits — coarse request-body ceiling (bytes); full enforcement at the proxy. —
    max_request_body_bytes: int = 1_048_576  # 1 MiB

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async OWNER URL (asyncpg) — superuser/owner. Alembic/DDL + provisioning + tests only.

        The running app does NOT use this; runtime traffic flows through tenant_database_url /
        global_database_url so DB-level RLS has a non-superuser role to enforce against.
        """
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tenant_database_url(self) -> str:
        """Async TENANT URL — connects as the non-bypass `oneai_app` role.

        This is the role RLS enforces against: it is neither superuser nor table owner, so the
        ENABLE'd + FORCE'd org_isolation policies finally apply. Used by core.scoped_session.
        """
        return (
            f"postgresql+asyncpg://{self.app_db_user}:{self.oneai_app_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def global_database_url(self) -> str:
        """Async GLOBAL URL — connects as the BYPASSRLS `oneai_global` role.

        For the legitimately cross-org / pre-org flows (login resolves a user before any org is
        known; platform/erasure/audit span all orgs). Used by core.get_session.
        """
        return (
            f"postgresql+asyncpg://{self.global_db_user}:{self.oneai_global_password}"
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
        """Canonicalize the JWT secret, then refuse to boot outside dev/test on a dev-default
        OR weak signing secret.

        First strips surrounding whitespace off jwt_secret and stores it back, so validation and
        token-signing (identity/security/tokens.py) can never diverge: a trailing newline from a
        Docker/Vault secret mount is trimmed rather than fail-closed, and a whitespace-padded copy
        of the public dev default can no longer slip the raw-equality denylist while still signing
        forgeable tokens (testing TC-SG-006 / cross-vendor finding F1).

        Then a fail-closed guard with two layers, active only when requires_secure_secrets is True
        (every env except local | test):
          - Denylist: the known dev-default JWT secret / DB password must never reach a
            non-dev env (staging, production, or an unrecognized app_env).
          - Strength: the JWT signing key must be at least _MIN_JWT_SECRET_BYTES (RFC 7518 §3.2;
            a length floor, not an entropy check — see the constant). A blank, whitespace-only, or
            short key is rejected even though it is not the literal dev default, because it signs
            forgeable tokens just the same. POSTGRES_PASSWORD keeps the denylist-only check.

        Raises InsecureConfigurationError (a hard boot failure) naming each offending secret.
        Only app_env 'local' or 'test' is exempt.
        """
        # Canonicalize first so the value we validate is exactly the value tokens.py will sign with.
        self.jwt_secret = self.jwt_secret.strip()

        if not self.requires_secure_secrets:
            return self

        insecure: list[str] = []
        if self.jwt_secret == _INSECURE_JWT_SECRET:
            insecure.append("JWT_SECRET (dev default)")
        elif len(self.jwt_secret.encode("utf-8")) < _MIN_JWT_SECRET_BYTES:
            insecure.append(f"JWT_SECRET (blank or under {_MIN_JWT_SECRET_BYTES} bytes)")
        if self.postgres_password == _INSECURE_POSTGRES_PASSWORD:
            insecure.append("POSTGRES_PASSWORD (dev default)")
        if self.oneai_app_password == _INSECURE_ONEAI_APP_PASSWORD:
            insecure.append("ONEAI_APP_PASSWORD (dev default)")
        if self.oneai_global_password == _INSECURE_ONEAI_GLOBAL_PASSWORD:
            insecure.append("ONEAI_GLOBAL_PASSWORD (dev default)")

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

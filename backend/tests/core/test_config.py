"""Unit tests for app.core.config — URL assembly and environment flags (no DB)."""

from __future__ import annotations

from app.core.config import Settings


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
    assert Settings(app_env="production").is_production is True


def test_is_production_false_for_local_env() -> None:
    assert Settings(app_env="local").is_production is False

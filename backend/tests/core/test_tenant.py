"""Unit tests for app.core.tenant — tenant resolution + context (no DB)."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.core import tenant
from app.core.config import Settings
from app.core.exceptions import TenantContextMissingError
from app.core.tenant import get_current_org, resolve_org_id, set_current_org


async def test_resolve_org_id_uses_header_when_present() -> None:
    org_id = await resolve_org_id(x_org_id="11111111-1111-1111-1111-111111111111")

    assert org_id == UUID("11111111-1111-1111-1111-111111111111")


async def test_resolve_org_id_falls_back_to_default_in_local() -> None:
    org_id = await resolve_org_id(x_org_id=None)

    assert str(org_id) == Settings().default_org_id


async def test_resolve_org_id_invalid_value_raises() -> None:
    with pytest.raises(TenantContextMissingError):
        await resolve_org_id(x_org_id="not-a-uuid")


async def test_resolve_org_id_missing_in_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenant, "get_settings", lambda: Settings(app_env="production"))

    with pytest.raises(TenantContextMissingError):
        await resolve_org_id(x_org_id=None)


def test_get_current_org_returns_bound_value() -> None:
    org_id = UUID("22222222-2222-2222-2222-222222222222")
    set_current_org(org_id)

    assert get_current_org() == org_id

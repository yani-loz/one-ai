"""
Unit tests for app.core.tenant — tenant context propagation (no DB).

The X-Org-Id header seam (resolve_org_id) was removed in the identity refactor: org_id
now comes only from the verified JWT. These tests cover the remaining surface — the
ContextVar set/get and the missing-context guard.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest

from app.core.exceptions import TenantContextMissingError
from app.core.tenant import get_current_org, set_current_org


def test_get_current_org_without_context_raises() -> None:
    # A fresh OS thread starts with the ContextVar at its default (None), giving a
    # context with no tenant bound — independent of what other tests have set.
    def _read_unbound_org() -> None:
        get_current_org()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_read_unbound_org)
        with pytest.raises(TenantContextMissingError):
            future.result()


def test_set_current_org_then_get_returns_bound_value() -> None:
    org_id = UUID("22222222-2222-2222-2222-222222222222")

    set_current_org(org_id)

    assert get_current_org() == org_id


def test_set_current_org_overwrites_previous_value() -> None:
    set_current_org(UUID("11111111-1111-1111-1111-111111111111"))
    second_org = UUID("33333333-3333-3333-3333-333333333333")

    set_current_org(second_org)

    assert get_current_org() == second_org

"""
Role: Unit tests for the connector permission resolver (CO-01 AC1) — exhaustive table over
      entitlement × per-user override × org-wide policy, asserting the decision + denial reason.
Used by: pytest (tests/connectors/services). Pure — no DB, no network.
Depends on: app.connectors.services.connector_authz, app.connectors.enums (OverrideType).
"""

from __future__ import annotations

import pytest

from app.connectors.enums import OverrideType
from app.connectors.services.connector_authz import (
    ConnectorDenialReason,
    resolve_connector_access,
)


@pytest.mark.parametrize(
    ("entitled", "override", "org_wide_enabled", "expected_allowed", "expected_reason"),
    [
        # Not entitled → always denied (the hard ceiling), whatever the policy/override say.
        (False, None, False, False, ConnectorDenialReason.not_entitled),
        (False, None, True, False, ConnectorDenialReason.not_entitled),
        (False, OverrideType.grant, True, False, ConnectorDenialReason.not_entitled),
        (False, OverrideType.deny, False, False, ConnectorDenialReason.not_entitled),
        # Entitled + explicit deny → denied, even when org-wide is on (override wins).
        (True, OverrideType.deny, True, False, ConnectorDenialReason.admin_denied),
        (True, OverrideType.deny, False, False, ConnectorDenialReason.admin_denied),
        # Entitled + explicit grant → allowed, even when org-wide is off (the upgrade case).
        (True, OverrideType.grant, False, True, None),
        (True, OverrideType.grant, True, True, None),
        # Entitled + no override → fall back to the org-wide policy.
        (True, None, True, True, None),
        (True, None, False, False, ConnectorDenialReason.org_disabled),
    ],
)
def test_resolve_connector_access_table(
    entitled: bool,
    override: OverrideType | None,
    org_wide_enabled: bool,
    expected_allowed: bool,
    expected_reason: ConnectorDenialReason | None,
) -> None:
    decision = resolve_connector_access(
        entitled=entitled, override=override, org_wide_enabled=org_wide_enabled
    )

    assert decision.allowed is expected_allowed
    assert decision.denial_reason == expected_reason

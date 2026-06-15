"""
Role: The single connector permission-resolution rule (CO-01 §5 / AC1) — given a company's
      entitlement, a user's per-user override, and the org-wide policy for a connector type,
      decide whether that user MAY self-connect it. Pure domain logic, no I/O.
Used by: connectors.services.me_connector_service (fetches the three inputs from the entitlement
         reader + policy/override repos, then calls resolve_connector_access and raises the mapped
         403 on denial). connectors.services.connector_governance_service uses the same entitlement
         ceiling when an admin writes a policy.
Depends on: app.connectors.enums (OverrideType). Leaf — no DB, no network (so AC1 is a fast,
            exhaustive table-driven unit test).
Key invariants:
  - RESOLUTION ORDER (top-down, CO-01 §5 / §10.1): entitlement is the hard ceiling — if the
    company is not entitled, DENY regardless of anything below. Then the PER-USER OVERRIDE wins
    over the org-wide policy: an explicit `deny` excludes the user even when org-wide is on; an
    explicit `grant` lets the user connect even when org-wide is off. With no override, fall back
    to the org-wide policy.
  - The decision is data, not control flow: resolve_connector_access returns a
    ConnectorAccessDecision (allowed + a typed denial reason) so callers map the reason to the
    right friendly 403 (not-entitled vs admin-hasn't-enabled). It never raises.
  - Pure + deterministic: the same three inputs always yield the same decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.connectors.enums import OverrideType


class ConnectorDenialReason(StrEnum):
    """Why self-connect was denied — drives the friendly message the API returns.

    not_entitled: the company's plan doesn't include this type (Tier 1) ·
    admin_denied: an explicit per-user deny by a company admin ·
    org_disabled: org-wide off and no per-user grant (the admin hasn't enabled it for this user).
    """

    not_entitled = "not_entitled"
    admin_denied = "admin_denied"
    org_disabled = "org_disabled"


@dataclass(frozen=True, slots=True)
class ConnectorAccessDecision:
    """The outcome of permission resolution: allowed, with a typed reason when denied."""

    allowed: bool
    denial_reason: ConnectorDenialReason | None = None


def resolve_connector_access(
    *,
    entitled: bool,
    override: OverrideType | None,
    org_wide_enabled: bool,
) -> ConnectorAccessDecision:
    """Resolve whether a user may self-connect a connector type (CO-01 §5 / AC1).

    Contract: top-down — entitlement ceiling, then the per-user override (which wins over the
    org-wide policy), then the org-wide policy. Returns a decision; never raises.

    Args:
        entitled: the company is entitled to this connector type (Tier 1).
        override: the user's per-user override for this type, or None if no override exists.
        org_wide_enabled: the org-wide policy for this type (Tier 2 default reach).

    Returns:
        ConnectorAccessDecision(allowed=True) when the user may connect, else allowed=False with
        the typed denial_reason driving the API's friendly message.
    """
    if not entitled:
        return ConnectorAccessDecision(False, ConnectorDenialReason.not_entitled)
    if override is OverrideType.deny:
        return ConnectorAccessDecision(False, ConnectorDenialReason.admin_denied)
    if override is OverrideType.grant:
        return ConnectorAccessDecision(True)
    if org_wide_enabled:
        return ConnectorAccessDecision(True)
    return ConnectorAccessDecision(False, ConnectorDenialReason.org_disabled)

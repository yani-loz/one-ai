"""
Role: Unit tests for the address-level exclusion guards — role/shared mailboxes are not people;
      generic free-mail domains are not companies. These are the deterministic §5 day-one guards.
Used by: pytest (tests/entities). Pure, no DB.
Depends on: app.entities.services.address_rules.
"""

from __future__ import annotations

import pytest

from app.entities.services.address_rules import is_generic_email_domain, is_role_address


@pytest.mark.parametrize(
    "address",
    ["info@acme.com", "kontakt@acme.de", "noreply@acme.com", "support@x.com", "vertrieb@x.de",
     "buchhaltung@x.de", "info+sales@acme.com"],
)
def test_is_role_address_true_for_shared_mailboxes(address: str) -> None:
    assert is_role_address(address) is True


@pytest.mark.parametrize("address", ["boyan@acme.com", "j.smith@acme.com", "maria.weber@x.de"])
def test_is_role_address_false_for_humans(address: str) -> None:
    assert is_role_address(address) is False


@pytest.mark.parametrize(
    "domain", ["gmail.com", "googlemail.com", "gmx.de", "web.de", "t-online.de", "outlook.com"],
)
def test_is_generic_email_domain_true_for_free_providers(domain: str) -> None:
    assert is_generic_email_domain(domain) is True


@pytest.mark.parametrize("domain", ["acme.com", "ethera-tech.com", "siemens.de"])
def test_is_generic_email_domain_false_for_company_domains(domain: str) -> None:
    assert is_generic_email_domain(domain) is False

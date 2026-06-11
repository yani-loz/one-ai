"""
Role: Unit tests for the address/domain-level guards — token-matched role/automation localparts
      (audit H-4 incl. the 13 phantom-person localparts from the live corpus), generic free-mail
      domains incl. the Bulgarian set (M-7), IDN/punycode detection (M-8), and eTLD+1 registrable-
      domain folding (M-9, offline PSL).
Used by: pytest (tests/entities). Pure, no DB.
Depends on: app.entities.services.address_rules.
"""

from __future__ import annotations

import pytest

from app.entities.services.address_rules import (
    fold_to_registrable_domain,
    is_generic_email_domain,
    is_punycode_domain,
    is_role_address,
    is_suspicious_idn_domain,
)

# The exact 13 compound automation localparts that minted phantom persons in the 2026-06-10 audit
# (H-4) — every one slipped past the old exact-frozenset guard and must now token-match.
_AUDIT_PHANTOM_ADDRESSES = [
    "account-security-noreply@accountprotection.microsoft.com",
    "automated-notifications@nomail.ec.europa.eu",
    "calendar-notification@google.com",
    "comments-noreply@docs.google.com",
    "drive-shares-dm-noreply@google.com",
    "ec-no-reply-grant-management@nomail.ec.europa.eu",
    "ec-no-reply@nomail.ec.europa.eu",
    "eu-corporate-notification-system@ec.europa.eu",
    "invitation-do-not-reply@trello.com",
    "meetings-noreply@google.com",
    "messaging-digest-noreply@linkedin.com",
    "no-reply-cod6h9qg_bo7vatgemoytg@mail.anthropic.com",
    "security-noreply@linkedin.com",
]


@pytest.mark.parametrize(
    "address",
    ["info@acme.com", "kontakt@acme.de", "noreply@acme.com", "support@x.com", "vertrieb@x.de",
     "buchhaltung@x.de", "info+sales@acme.com"],
)
def test_is_role_address_true_for_shared_mailboxes(address: str) -> None:
    assert is_role_address(address) is True


@pytest.mark.parametrize("address", _AUDIT_PHANTOM_ADDRESSES)
def test_is_role_address_compound_automation_localpart_returns_true(address: str) -> None:
    # Audit H-4: compound localparts (token vocabulary or no-reply/do-not-reply token runs).
    assert is_role_address(address) is True


@pytest.mark.parametrize(
    "address",
    ["boyan@acme.com", "j.smith@acme.com", "maria.weber@x.de", "anna-maria.schmidt@acme.de"],
)
def test_is_role_address_false_for_humans(address: str) -> None:
    assert is_role_address(address) is False


@pytest.mark.parametrize("address", ["renotify@acme.com", "abounce@acme.com"])
def test_is_role_address_substring_lookalike_returns_false(address: str) -> None:
    # Audit H-4 false-positive guard: matching is by TOKEN boundary, never substring —
    # 'renotify' contains 'notify' and 'abounce' contains 'bounce' but neither is a token.
    assert is_role_address(address) is False


def test_is_role_address_no_at_sign_returns_false() -> None:
    assert is_role_address("not-an-address") is False


@pytest.mark.parametrize(
    "address",
    ["joao.sales@empresa.pt", "job.devries@bedrijf.nl", "joe.root@corp.co.uk",
     "petra.press@verlag.de"],
)
def test_is_role_address_broad_word_as_dotted_name_token_returns_false(address: str) -> None:
    # 2026-06-11 review refinement: Sales/Job/Root/Press are real human surnames/given names.
    # Broad functional words must NOT match dot-delimited tokens (the firstname.lastname
    # convention) — only the whole localpart or -_+ tokens.
    assert is_role_address(address) is False


@pytest.mark.parametrize(
    "address",
    ["it-support@acme.com", "sales_team@acme.de", "hr-jobs@x.de", "sales@empresa.pt"],
)
def test_is_role_address_broad_word_as_dash_token_or_whole_returns_true(address: str) -> None:
    # The same broad words still catch real compound role mailboxes (hyphen/underscore is
    # mailbox punctuation, not a name convention) and exact-localpart forms.
    assert is_role_address(address) is True


def test_is_role_address_automation_token_dotted_returns_true() -> None:
    # AUTOMATION tokens match under ANY delimiter including dots — never name fragments.
    assert is_role_address("meetings.noreply@google.com") is True


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("breeze.xn--n-1tb", True),  # ACE wire form (the audit's live homoglyph)
        ("breeze.nо", True),  # raw-Unicode EAI wire form — same logical spoof, no xn--
        ("breeze.no", False),  # the genuine ASCII domain stays mintable
        ("acme.com", False),
    ],
)
def test_is_suspicious_idn_domain_catches_both_wire_forms(domain: str, expected: bool) -> None:
    # 2026-06-11 review: SMTPUTF8/EAI delivers the domain un-punycoded, so the quarantine must
    # trigger on raw non-ASCII as well as on xn-- labels — else the spoof mints a company
    # depending on which wire form the sender picked.
    assert is_suspicious_idn_domain(domain) is expected


@pytest.mark.parametrize(
    "domain", ["gmail.com", "googlemail.com", "gmx.de", "web.de", "t-online.de", "outlook.com"],
)
def test_is_generic_email_domain_true_for_free_providers(domain: str) -> None:
    assert is_generic_email_domain(domain) is True


@pytest.mark.parametrize(
    "domain", ["abv.bg", "mail.bg", "dir.bg", "inbox.bg", "gbg.bg", "mail.orbitel.bg"],
)
def test_is_generic_email_domain_bulgarian_free_mail_returns_true(domain: str) -> None:
    # Audit M-7: abv.bg was the single largest "company" in the graph (22 unrelated people).
    assert is_generic_email_domain(domain) is True


@pytest.mark.parametrize("domain", ["acme.com", "ethera-tech.com", "siemens.de", "apis.bg"])
def test_is_generic_email_domain_false_for_company_domains(domain: str) -> None:
    assert is_generic_email_domain(domain) is False


@pytest.mark.parametrize(
    "domain", ["breeze.xn--n-1tb", "xn--mnchen-3ya.de", "mail.XN--firma-abc.com"],
)
def test_is_punycode_domain_ace_label_returns_true(domain: str) -> None:
    # Audit M-8: the Cyrillic-homoglyph breeze.xn--n-1tb is the live spoof-adjacent case.
    assert is_punycode_domain(domain) is True


@pytest.mark.parametrize("domain", ["breeze.no", "ibm.com", "evil-xn--mid.com"])
def test_is_punycode_domain_no_ace_label_returns_false(domain: str) -> None:
    # The ACE prefix is label-INITIAL — a label merely containing 'xn--' mid-string is not IDN.
    assert is_punycode_domain(domain) is False


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("bg.ibm.com", "ibm.com"),  # audit M-9: IBM was fractured into 5 companies
        ("ibm.com", "ibm.com"),
        ("mail.hostinger.com", "hostinger.com"),
        ("sub.deep.example.co.uk", "example.co.uk"),  # multi-label public suffix
        ("ethera-tech.com", "ethera-tech.com"),
    ],
)
def test_fold_to_registrable_domain_subdomain_folds_to_etld_plus_one(
    host: str, expected: str
) -> None:
    assert fold_to_registrable_domain(host) == expected


def test_fold_to_registrable_domain_saas_tenants_stay_distinct() -> None:
    # Audit M-9 ruling: *.atlassian.net tenants ARE distinct orgs (PSL-private-style suffix) —
    # they must NOT fold together.
    assert fold_to_registrable_domain("foo.atlassian.net") == "foo.atlassian.net"
    assert fold_to_registrable_domain("bar.atlassian.net") == "bar.atlassian.net"
    assert fold_to_registrable_domain("x.github.io") == "x.github.io"


def test_fold_to_registrable_domain_m365_default_domains_stay_distinct() -> None:
    # 2026-06-10 review fixup: the PSL dropped onmicrosoft.com, so without the supplementary
    # suffix every M365 org without a custom domain (user@<tenant>.onmicrosoft.com) folded into
    # ONE 'onmicrosoft.com' company — the module's own hardest invariant is "never an over-merge".
    assert fold_to_registrable_domain("tenant-a.onmicrosoft.com") == "tenant-a.onmicrosoft.com"
    assert fold_to_registrable_domain("tenant-b.onmicrosoft.com") == "tenant-b.onmicrosoft.com"
    # The MOERA hybrid-routing variant one level down must not re-merge either.
    assert (
        fold_to_registrable_domain("tenant-a.mail.onmicrosoft.com")
        == "tenant-a.mail.onmicrosoft.com"
    )


@pytest.mark.parametrize("host", ["localhost", "atlassian.net", "onmicrosoft.com"])
def test_fold_to_registrable_domain_unfoldable_returns_input(host: str) -> None:
    # No known suffix (localhost) or the input IS a bare suffix — never an empty key.
    assert fold_to_registrable_domain(host) == host

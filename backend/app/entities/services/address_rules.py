"""
Role: Address-level exclusion rules for entity resolution — decides which addresses must NOT become
      a Person (role / shared mailboxes) and which domains must NOT become a Company (generic free
      providers). These are the deterministic "bake-in from day one" guards from design §5.
Used by: app.entities.services.entity_resolver.
Depends on: stdlib only (operates on already-normalized addresses/domains from email_normalizer).
Key invariants:
  - is_role_address is an ADDRESS-level predicate, distinct from the message-level `is_automated`
    flag (which keys off Precedence/List-* headers). A plain mail from `info@`/`kontakt@` is not
    automated yet must still not mint a Person — so person-hood gates on THIS, not on is_automated.
  - is_generic_email_domain marks free/consumer providers (incl. DACH ones: gmx, web.de, t-online,
    bluewin, a1) so a personal `gmail.com` address does not create a bogus shared Company.
  - These are conservative starter lists, intentionally extensible; over-inclusion here only causes
    under-creation (a recoverable fragment), never an over-merge.
"""

from __future__ import annotations

# Local-parts of role / functional / shared mailboxes — NOT a single human. DACH-focused: German
# role names (kontakt, vertrieb, buchhaltung, …) sit alongside the English ones.
_ROLE_LOCALPARTS = frozenset(
    {
        # generic functional
        "info", "contact", "kontakt", "hello", "hallo", "office", "mail", "email", "team",
        "admin", "administrator", "webmaster", "postmaster", "hostmaster", "root", "system",
        # sales / commercial
        "sales", "vertrieb", "marketing", "newsletter", "news", "presse", "press",
        # support / service
        "support", "service", "help", "helpdesk", "feedback", "kundenservice",
        # finance / legal / hr
        "billing", "accounts", "accounting", "buchhaltung", "rechnung", "rechnungen", "finance",
        "hr", "jobs", "job", "career", "careers", "karriere", "bewerbung", "recruiting",
        "datenschutz", "privacy", "legal", "compliance", "abuse", "security",
        # automation / no-reply (overlaps the message-flag set deliberately — belt and suspenders)
        "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "do_not_reply",
        "mailer-daemon", "mailerdaemon", "bounce", "bounces", "notifications", "notification",
        "automated", "auto", "daemon", "mailer", "delivery",
        # reception / secretariat (DACH SMB)
        "empfang", "sekretariat", "reception", "anfrage", "anfragen",
    }
)

# Free / consumer email providers — a personal address here is NOT a company. Includes the major
# DACH free providers, which matters for the target market.
_GENERIC_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com",
        "outlook.com", "outlook.de", "hotmail.com", "hotmail.de", "hotmail.co.uk",
        "live.com", "live.de", "msn.com",
        "yahoo.com", "yahoo.de", "yahoo.co.uk", "ymail.com", "rocketmail.com",
        "icloud.com", "me.com", "mac.com",
        "aol.com", "aol.de",
        "proton.me", "protonmail.com", "pm.me",
        "gmx.de", "gmx.net", "gmx.com", "gmx.at", "gmx.ch",
        "web.de", "t-online.de", "freenet.de", "arcor.de", "online.de",
        "mail.com", "email.com", "mail.ru", "yandex.com", "yandex.ru",
        "zoho.com", "fastmail.com", "hey.com", "posteo.de", "mailbox.org", "tutanota.com",
        "bluewin.ch", "sunrise.ch", "hispeed.ch", "gmx.li",
        "a1.net", "aon.at", "chello.at", "magenta.at", "kabsi.at",
    }
)


def is_role_address(normalized_address: str) -> bool:
    """True if the address is a role / shared / functional mailbox (must not become a Person)."""
    if "@" not in normalized_address:
        return False
    localpart = normalized_address.rsplit("@", 1)[0]
    # Strip a '+suffix' for THIS classification only (not the match key) so `info+x@` still reads
    # as a role mailbox; the stored match key keeps the '+' intact. Lowercase defensively so the
    # guard self-defends regardless of caller normalization (parity with is_generic_email_domain).
    base_localpart = localpart.split("+", 1)[0].lower()
    return base_localpart in _ROLE_LOCALPARTS


def is_generic_email_domain(domain: str) -> bool:
    """True if the domain is a free/consumer provider (must not become a Company)."""
    return domain.strip().lower() in _GENERIC_DOMAINS

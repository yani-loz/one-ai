"""
Role: Address/domain-level rules for entity resolution — decides which addresses must NOT become
      a Person (role / shared / automation mailboxes), which domains must NOT become a Company
      (generic free providers, IDN/punycode spoof candidates), and how a full observed host folds
      to the registrable-domain company key (eTLD+1). These are the deterministic "bake-in from
      day one" guards from design §5 plus the 2026-06-10 audit fixes (H-4, M-7, M-8, M-9).
Used by: app.entities.services.entity_resolver.
Depends on: tldextract (configured OFFLINE — bundled public-suffix snapshot, never a network
            call at runtime) + stdlib re; operates on already-normalized addresses/domains from
            email_normalizer.
Key invariants:
  - is_role_address matches in two TOKEN tiers (audit H-4 + the 2026-06-11 surname refinement):
    AUTOMATION tokens (`noreply`, `mailer`, …) match under any `-_.+` delimiter and as phrases
    (`no-reply`, `do-not-reply`); BROAD functional words (`sales`, `support`, `job`, …) match the
    whole localpart or `-_+` tokens but never DOT tokens — `drive-shares-dm-noreply@` and
    `it-support@` are caught while `joao.sales@` / `job.devries@` / `renotify@` /
    `anna-maria.schmidt@` stay person-eligible (token boundaries, never substrings). It remains an
    ADDRESS-level predicate, distinct from the message-level `is_automated` flag.
  - is_generic_email_domain marks free/consumer providers (DACH: gmx, web.de, t-online, bluewin,
    a1; BG: abv.bg, mail.bg, dir.bg, … — audit M-7) so a personal address never creates a bogus
    shared Company.
  - is_suspicious_idn_domain flags BOTH IDN wire forms (audit M-8 + the 2026-06-11 EAI
    refinement): an ACE-prefixed (`xn--`) label OR raw non-ASCII characters (SMTPUTF8/EAI
    delivery) — the resolver quarantines those from company minting (homoglyph spoof surface);
    it never tries to merge them with an ASCII lookalike. is_punycode_domain remains the
    ACE-only predicate.
  - fold_to_registrable_domain returns the eTLD+1 company key (audit M-9: `bg.ibm.com` →
    `ibm.com`). PSL PRIVATE suffixes are honored (plus a supplementary set for SaaS suffixes the
    PSL dropped: `atlassian.net`, `onmicrosoft.com` + `mail.onmicrosoft.com`) so a tenant
    subdomain — `foo.atlassian.net`, an M365 default domain `tenant.onmicrosoft.com` — stays its
    own identity instead of merging every such org into one (2026-06-10 review fixup). Unfoldable
    input (no known suffix, or the input IS a bare suffix) returns itself, never an empty key.
  - These are conservative, intentionally extensible lists; over-inclusion here only causes
    under-creation (a recoverable fragment), never an over-merge.
"""

from __future__ import annotations

import re

import tldextract

# BROAD functional role words. These collide with real human names in the firstname.lastname
# convention (Sales is a PT/BR surname, Job a Dutch given name, Root an EN surname — 2026-06-11
# review), so they match the WHOLE localpart or a `-_+`-delimited token (`it-support`,
# `sales_team`) but NEVER a dot-delimited token (`joao.sales` stays a person).
# DACH-focused: German role names (kontakt, vertrieb, buchhaltung, …) sit beside the English ones.
_ROLE_WORDS_BROAD = frozenset(
    {
        # generic functional
        "info", "contact", "kontakt", "hello", "hallo", "office", "mail", "email", "team",
        "admin", "administrator", "webmaster", "postmaster", "hostmaster", "root", "system",
        # sales / commercial
        "sales", "vertrieb", "marketing", "news", "presse", "press",
        # support / service
        "support", "service", "help", "helpdesk", "feedback", "kundenservice",
        # finance / legal / hr
        "billing", "accounts", "accounting", "buchhaltung", "rechnung", "rechnungen", "finance",
        "hr", "jobs", "job", "career", "careers", "karriere", "bewerbung", "recruiting",
        "datenschutz", "privacy", "legal", "compliance", "abuse", "security",
        # automation words that double as plausible name fragments — exact/`-_+` tier only
        "auto",
        # reception / secretariat (DACH SMB)
        "empfang", "sekretariat", "reception", "anfrage", "anfragen",
    }
)

# AUTOMATION tokens — essentially never human-name fragments, so they match as a token under ANY
# delimiter including dots (`meetings.noreply@`, `drive-shares-dm-noreply@`).
_AUTOMATION_TOKENS = frozenset(
    {
        "noreply", "donotreply", "mailerdaemon", "bounce", "bounces",
        "notifications", "notification", "notify", "automated", "daemon", "mailer",
        "delivery", "newsletter",
    }
)

# Multi-token automation phrases that only exist split across delimiters (`no-reply`,
# `invitation-do-not-reply`); matched as CONTIGUOUS token runs after tokenization.
_ROLE_TOKEN_SEQUENCES = frozenset(
    {
        ("no", "reply"),
        ("do", "not", "reply"),
        ("mailer", "daemon"),
    }
)

# Free / consumer email providers — a personal address here is NOT a company. Includes the major
# DACH free providers plus the Bulgarian free-mail set (audit M-7; BG corpora are production-
# plausible per the CON-09 / CON-10 connector roadmap).
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
        # Bulgarian free-mail providers (audit M-7 — abv.bg was the #1 "company" in the graph)
        "abv.bg", "mail.bg", "dir.bg", "inbox.bg", "gbg.bg", "mail.orbitel.bg",
    }
)

# Localpart token delimiters. The FULL split (incl. dots) feeds the automation tier; the
# NAME-SAFE split keeps dot-joined chunks intact so `joao.sales` never tokenizes to `sales`
# (dots are the firstname.lastname convention; hyphens/underscores/plus are mailbox punctuation).
_LOCALPART_TOKEN_SPLIT = re.compile(r"[-_.+]")
_LOCALPART_NAME_SAFE_SPLIT = re.compile(r"[-_+]")

# Suffixes the live Public Suffix List has DROPPED from its private section but whose subdomains
# are still distinct SaaS-tenant orgs in real corpora (audit M-9 ruling: foo.atlassian.net and
# bar.atlassian.net are different organizations and must not fold together).
# onmicrosoft.com (fixup of the 2026-06-10 review): every Microsoft 365 org without a custom
# domain mails from user@<tenant>.onmicrosoft.com — folding those to the bare suffix merged every
# such DISTINCT company into one row (and, when the synced mailbox itself sits on a default M365
# domain, classified them all as internal colleagues). <tenant>.mail.onmicrosoft.com is the MOERA
# hybrid-routing variant; longest-suffix match keeps both tenant levels distinct.
_PSL_SUPPLEMENTARY_PRIVATE_SUFFIXES = (
    "atlassian.net",
    "onmicrosoft.com",
    "mail.onmicrosoft.com",
)

# OFFLINE eTLD+1 extractor: suffix_list_urls=() forbids any network fetch and falls back to the
# library's bundled PSL snapshot; include_psl_private_domains=True keeps PSL-private SaaS
# suffixes (github.io, …) as suffixes so each tenant subdomain is its own registrable identity.
_registrable_domain_extractor = tldextract.TLDExtract(
    suffix_list_urls=(),
    include_psl_private_domains=True,
    extra_suffixes=_PSL_SUPPLEMENTARY_PRIVATE_SUFFIXES,
)


def is_role_address(normalized_address: str) -> bool:
    """True if the address is a role / shared / automation mailbox (must not become a Person).

    Two-tier token matching (audit H-4 + the 2026-06-11 surname refinement):
    - AUTOMATION tokens (`noreply`, `mailer`, `daemon`, …) match under ANY delimiter (`-_.+`)
      and as contiguous phrases (`no-reply`, `do-not-reply`, `mailer-daemon`) — these are never
      human-name fragments, so `drive-shares-dm-noreply@` and `meetings.noreply@` are caught.
    - BROAD functional words (`sales`, `support`, `job`, `root`, …) match the whole localpart
      (`sales@`) or a `-_+`-delimited token (`it-support@`, `sales_team@`) but NEVER a
      dot-delimited token: `joao.sales@` / `job.devries@` / `joe.root@` follow the
      firstname.lastname convention and stay person-eligible.
    Token boundaries — never substrings — so `renotify@` / `abounce@` stay human-eligible.
    Lowercases defensively so the guard self-defends regardless of caller normalization.
    """
    if "@" not in normalized_address:
        return False
    localpart = normalized_address.rsplit("@", 1)[0].lower()
    if localpart in _ROLE_WORDS_BROAD or localpart in _AUTOMATION_TOKENS:
        return True
    full_tokens = _tokenize(localpart, _LOCALPART_TOKEN_SPLIT)
    if any(token in _AUTOMATION_TOKENS for token in full_tokens):
        return True
    name_safe_tokens = _tokenize(localpart, _LOCALPART_NAME_SAFE_SPLIT)
    if any(token in _ROLE_WORDS_BROAD for token in name_safe_tokens):
        return True
    return _has_role_token_sequence(full_tokens)


def is_generic_email_domain(domain: str) -> bool:
    """True if the domain is a free/consumer provider (must not become a Company)."""
    return domain.strip().lower() in _GENERIC_DOMAINS


def is_punycode_domain(domain: str) -> bool:
    """True if any DNS label of `domain` carries the IDN ACE prefix `xn--` (audit M-8).

    Punycode labels are the homoglyph-spoof surface (`breeze.xn--n-1tb` ≈ Cyrillic `breeze.nо`);
    the resolver quarantines such domains from company minting pending HiTL review. The prefix is
    label-initial by IDNA definition — a label merely containing `xn--` mid-string is not ACE.
    """
    return any(label.startswith("xn--") for label in domain.strip().lower().split("."))


def is_suspicious_idn_domain(domain: str) -> bool:
    """True if `domain` is IDN-shaped in EITHER wire form (audit M-8 + EAI refinement).

    Catches the ACE form (`xn--` label) AND the raw-Unicode form (SMTPUTF8/EAI delivery sends
    the domain un-punycoded, so a Cyrillic-о homoglyph carries no `xn--` label at all). The
    resolver must quarantine BOTH from company minting — otherwise the same logical spoof
    domain mints a company or not depending on which wire form the attacker picked.
    """
    cleaned = domain.strip().lower()
    return is_punycode_domain(cleaned) or not cleaned.isascii()


def fold_to_registrable_domain(domain: str) -> str:
    """Fold a full observed host to its eTLD+1 registrable domain — the company match key (M-9).

    `bg.ibm.com` → `ibm.com`; PSL-private SaaS suffixes keep tenant subdomains distinct
    (`foo.atlassian.net` → `foo.atlassian.net`, `tenant.onmicrosoft.com` →
    `tenant.onmicrosoft.com`). Resolution is fully offline (bundled PSL
    snapshot — never a network call). When the host has no known suffix (`localhost`) or IS a
    bare suffix itself (`atlassian.net`), the cleaned input is returned unchanged: the fold must
    never produce an empty key.
    """
    cleaned = domain.strip().lower()
    registrable = _registrable_domain_extractor(cleaned).top_domain_under_public_suffix
    return registrable or cleaned


def _tokenize(localpart: str, splitter: re.Pattern[str]) -> tuple[str, ...]:
    """Split a localpart into tokens on `splitter`, dropping empties."""
    return tuple(token for token in splitter.split(localpart) if token)


def _has_role_token_sequence(tokens: tuple[str, ...]) -> bool:
    """True if any contiguous token run matches a multi-token automation phrase."""
    for size in (2, 3):
        for start in range(len(tokens) - size + 1):
            if tokens[start : start + size] in _ROLE_TOKEN_SEQUENCES:
                return True
    return False

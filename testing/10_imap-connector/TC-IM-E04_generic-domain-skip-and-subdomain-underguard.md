# TC-IM-E04 — Generic free-mail domain skip + subdomain under-guard

| ID · Suite · Type · Mode |
|---|
| TC-IM-E04 · E (Persistence/RLS/entity graph) · Boundary · ingest |

| Result · Tag · Severity · Status |
|---|
| ⚠️ Pass-with-concern · 🆕 (subdomain over-merge note) · Low · Executed |

## Objective
Confirm the generic-domain skip-list (`address_rules._GENERIC_DOMAINS`, lines 45–61): a personal
`gmail.com` sender becomes a Person but **no** Company. Then probe the list's exact-match boundary:
a subdomain not in the list (`mail.gmail.com`) WOULD mint a Company (under-guard).

## Break hypothesis
`gmail.com` mistakenly mints a shared bogus Company; and/or a free-mail subdomain
(`mail.gmail.com`) — not in the exact-match skip-set — slips the guard and mints a Company, falsely
linking unrelated personal-subdomain senders as "colleagues."

## Steps
1. Ingest `From: Private Person <private-<S>@gmail.com>`; assert person=1, `company_domain(gmail.com)`=0.
2. In a fresh org, ingest `From: Sub Person <sub-<S>@mail.gmail.com>`; assert
   `company_domain(mail.gmail.com)`=1.

## Expected
- gmail.com → person yes, company no (✅ — `is_generic_email_domain` exact-matches `gmail.com`).
- mail.gmail.com → company minted (⚠️ — `_GENERIC_DOMAINS` is an exact-match `frozenset`; the
  subdomain is not a member, so `_resolve_company` creates a Company + CompanyDomain).

## Execution result (2026-06-09)
Harness: `testing/10_imap-connector/harness/entity_resolution_suite.py` (case E04)

```
  [PASS] e04_gmail_person_yes_company_no :: gmail person=1 (1), gmail company_domain=0 (0 — skip-list)
  [PASS] e04_subdomain_underguard_mints_company :: mail.gmail.com company_domain=1 (1 = under-guard: subdomain slips skip-list)
```

**Verdict:** ⚠️ **Pass-with-concern.** The intended behavior (gmail.com → no Company) holds exactly.
The subdomain under-guard reproduces: `mail.gmail.com` (and by extension any `*.gmail.com` /
free-mail subdomain) mints a Company. The skip-list is documented as a "conservative starter list,
intentionally extensible" in `address_rules.py:12-14`, and the docstring's framing is that
over-/under-inclusion here only **under-creates**, never over-merges. This case is the one genuine
exception to that claim: two unrelated personal senders on `*.gmail.com` subdomains get falsely
linked as colleagues under one Company — a mild **over-merge** on the company side.

**Tag:** 🆕 NEW · **Severity: Low** (data-quality only; org-scoped, no cross-tenant exposure;
recoverable — the raw addresses are retained, so a smarter domain-tier can re-split later). Not in
`docs/FIX_BEFORE_PROD.md`. Suggested fix: match generic domains on the registrable suffix
(eTLD+1) rather than an exact host, or add a `*.gmail.com`-style suffix check.

# TC-IM-E06 — Embedded-whitespace address vs the role guard

| ID · Suite · Type · Mode |
|---|
| TC-IM-E06 · E (Persistence/RLS/entity graph) · Adversarial · ingest |

| Result · Tag · Severity · Status |
|---|
| ⚠️ Pass-with-concern · 🆕 (latent normalize gap) · Low · Executed |

## Objective
Probe whether an address with embedded whitespace (`info @x.com`) dodges the role-address guard and
the empty-local/empty-domain guard while still storing a recipient row. `normalize_email`
(`email_normalizer.py:33`) does `strip().strip("<>").strip().lower()` — it trims the EDGES and
lowercases but does NOT strip INTERNAL whitespace.

## Break hypothesis
`info @x.com` normalizes to a key whose local-part is `info ` (trailing space) ≠ `info`, so
`is_role_address` returns False → the role guard is dodged and a Person is minted from a malformed
key; the recipient row also stores a malformed address.

## Steps
1. **Full ingest path:** ingest an email with `To: info @ws-<S>.test`; inspect the stored
   `email_recipient.address`, whether `person_id` was set, and any `person_email` row.
2. **Resolver seam (direct):** call `EntityResolver.resolve_participant` with `info @probe-<S>.test`
   and `in fo@probe-<S>.test` (a space INSIDE the local-part) and check whether persons are minted.

## Expected
NEW Low if it dodges the guard and mints a person; Pass if guarded.

## Execution result (2026-06-09)
Harness: `testing/10_imap-connector/harness/entity_resolution_suite.py` (case E06)

```
  [PASS] e06_full_path_getaddresses_cleans_then_guard_holds :: recipient stored addr='info@ws-e0a5f56938.test' person_id_set=False person_email=[] (getaddresses collapsed the space adjacent to '@' -> role guard held)
  [PASS] e06_normalize_email_internal_whitespace_latent_gap :: resolver('info @...')->person=True, resolver('in fo@...')->person=True (normalize_email keeps internal whitespace -> role guard dodged for a non-getaddresses key)
```

Supporting probe (`getaddresses` behavior):
```
raw='info @x.com'  -> getaddresses=[('', 'info@x.com')]   normalized='info@x.com' role=True
raw='info@ x.com'  -> getaddresses=[('', 'info@x.com')]   normalized='info@x.com' role=True
raw='in fo@x.com'  -> getaddresses=[('', 'in fo@x.com')]  normalized='in fo@x.com' role=False
direct normalize 'info @x.com' -> local='info ' role=False (dodges guard)
```

**Verdict:** ⚠️ **Pass-with-concern** — the break hypothesis does **NOT reproduce through the IMAP
connector.** Python's `email.utils.getaddresses` (used by `email_parser._extract_recipients` /
`_first_address`) collapses the whitespace adjacent to `@` BEFORE the resolver ever sees the address,
so `info @x.com` arrives as `info@x.com`, `is_role_address` returns True, and **no Person is minted**
(confirmed live: `person_id_set=False`, `person_email=[]`). The role guard holds on the real path.

**The NEW finding is the latent seam, not a connector bypass:** `normalize_email` does not strip
INTERNAL whitespace, so a key that reaches the resolver WITHOUT getaddresses pre-cleaning (driven
directly above, and reproducible for getaddresses-surviving inner spaces like `in fo@…`) dodges
`is_role_address` and mints a person from a malformed match key. **This is a data-quality gap
(malformed-key person creation), not a role-guard bypass, and it is NOT reachable via the current
IMAP parser** for the role-word cases.

**Tag:** 🆕 NEW · **Severity: Low** (data-quality; org-scoped; no cross-tenant exposure; not in
FIX_BEFORE_PROD). It becomes relevant only if a future caller feeds `normalize_email` an address that
did not pass through `getaddresses`. Suggested hardening: collapse/reject internal whitespace in
`normalize_email`, or validate the addr-spec shape before get-or-create.

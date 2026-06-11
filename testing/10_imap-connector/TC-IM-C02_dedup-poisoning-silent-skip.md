# TC-IM-C02 — Dedup poisoning: a reused Message-ID silently skips a genuine email

| ID · Suite · Type · Mode |
|---|
| TC-IM-C02 · C (Parse & data quality) · Adversarial · ingest |

| Result · Tag · Severity · Status |
|---|
| ❌ Fail · 🆕 NEW · Medium · Executed |

## Objective
Show that `dedup_key` = Message-ID, combined with the `exists()` short-circuit, lets a first email
carrying a chosen Message-ID suppress a *later, genuinely-different* email that reuses the same id.

## Break hypothesis
`parse_email` sets `dedup_key = message_id` when present. `EmailIngestService.ingest_email` returns
`SKIPPED` (no insert) whenever `(org, connection, dedup_key)` already exists. So an attacker who can
predict/reuse a Message-ID — e.g. plant a benign decoy carrying the id of a thread they want to
suppress, or replay a known thread id — causes the real email with that id to be **silently dropped**
(`SKIPPED`, no row, no error). Bounded: it requires controlling/reusing a Message-ID, so Medium not
Critical.

## Steps
1. Seed a run-stamped connection. Ingest a decoy `From: attacker@evil.example` with
   `Message-ID: <thread-…@bank.example>`.
2. Ingest a *different* email (`From: cfo@acme.com`, subject `WIRE THE 2M NOW`, distinct body)
   reusing the **same** Message-ID.
3. Count stored `email_message` rows for the org and inspect the surviving subject(s).

## Expected
First `STORED`, second `SKIPPED`, exactly **one** row stored (the decoy), the genuine email lost.

## Execution result (2026-06-09)
```
[FAIL] C02_reused_message_id_silently_skips_genuine_email :: first=stored second=skipped stored_count=1 subjects=['decoy'] (genuine 'WIRE THE 2M NOW' suppressed by the decoy's Message-ID)
```

**Verdict:** ❌ Fail — defect reproduced live. The genuine `WIRE THE 2M NOW` email was suppressed by
the decoy's reused Message-ID; only the decoy survived. Dedup keys on an attacker-influenceable
header with no content-hash cross-check, so a known/guessed Message-ID is a silent-suppression
primitive. No cross-tenant exposure (scoped to one org+connection), hence Medium.

**Tag:** 🆕 NEW · Severity Medium.

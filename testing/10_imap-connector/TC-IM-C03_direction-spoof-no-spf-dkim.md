# TC-IM-C03 — Direction spoof: From == mailbox → recorded as outbound (no SPF/DKIM)

| ID · Suite · Type · Mode |
|---|
| TC-IM-C03 · C (Parse & data quality) · Adversarial · pure |

| Result · Tag · Severity · Status |
|---|
| ⚠️ Pass-with-concern · 🆕 NEW · Low · Executed |

## Objective
Show that `direction` is derived purely by string-comparing `From:` against the mailbox address,
with no authenticity check, so a spoofed inbound email is recorded as **sent by the owner**.

## Break hypothesis
`derive_direction` (flags.py:54-64) returns `outbound` iff `normalize_for_compare(from_address) ==
normalize_for_compare(mailbox)`. There is no SPF / DKIM / DMARC verification anywhere in the parse
path. An attacker who sets `From:` to the owner's own address gets the email classified as
`outbound` (owner-sent), corrupting the sent/received provenance the product reasons over.

## Steps
Parse an email with `From: owner@acme.com` (== the synced mailbox), `To: victim@acme.com`,
subject `pay this invoice`. Inspect `parsed.direction`.

## Expected
`direction == 'outbound'` despite the email being an inbound spoof.

## Execution result (2026-06-09)
```
[FAIL] C03_forged_from_classified_outbound :: direction='outbound' (EXPECTED 'outbound' — inbound spoof recorded as SENT-BY-OWNER)
```
(The harness check is `direction != 'outbound'`; it reports `[FAIL]` precisely because the spoof
succeeded — the email was classified `outbound`.)

**Verdict:** ⚠️ Pass-with-concern — behaviour reproduced. Direction is authenticity-blind by design
(flags.py docstring: derived from From vs mailbox, folders dropped). For metadata classification this
is a low-severity data-quality risk, not a security boundary breach, but it means any "emails I sent"
view can be poisoned by a trivially spoofed From. Mitigation would be to consult
`Authentication-Results` / fold SPF-DKIM signals into the direction/automation logic before trusting
`outbound`.

**Tag:** 🆕 NEW · Severity Low.

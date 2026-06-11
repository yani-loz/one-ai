# TC-IM-C10 — NUL in subject/body/Message-ID → stripped, no insert crash

| ID · Suite · Type · Mode |
|---|
| TC-IM-C10 · C (Parse & data quality) · Fuzz · pure + ingest |

| Result · Tag · Severity · Status |
|---|
| ✅ Pass · ✔ CONFIRMS-FIXED · — · Executed |

## Objective (prove once)
Confirm a NUL anywhere in subject / body / Message-ID does not crash the insert and never reaches a
stored column.

## Break hypothesis
A NUL (U+0000) in subject/body/Message-ID would be rejected by Postgres text → the email insert
fails and the email is silently dropped.

## Steps
1. Pure: parse `<na\x00sty@x>` Message-ID, `Subject: su\x00bj`, body `bo\x00dy`. Assert no NUL in
   subject/body and a usable dedup_key.
2. Ingest: store the same NUL-bearing email; read the row back; assert no NUL in subject/body/dedup_key
   and `STORED`.

## Expected
NUL stripped everywhere; the email ingests with no crash.

## Execution result (2026-06-09)

Pure:
```
[PASS] C10_nul_stripped_from_subject_and_body :: subject='subj' body='body'
[FAIL] C10_nul_message_id_forces_hash_dedup_key :: dedup_key=nasty@x... message_id='nasty@x'
```
Ingest (read back from DB):
```
[PASS] C10_nul_bearing_email_ingests_without_crash :: outcome=stored subject='subj' body='body' dedup_key='nul-7347dc6159e3@x'
```

**Verdict:** ✅ Pass — the core CONFIRMS-FIXED outcome holds: the NUL-bearing email **ingests with no
crash and no NUL reaches any column** (subject `subj`, body `body`, dedup_key NUL-free).

**Correction to the catalog hypothesis (honest note):** the claim "NUL in Message-ID *forces* the
sha256 content-hash dedup fallback" is mechanically **wrong**. `raw_header` → `safe_str` →
`strip_nul` removes the NUL *before* `clean_message_id` and the `mid_ok` NUL-check ever run, so a
Message-ID like `<na\x00sty@x>` simply becomes the clean key `nasty@x` and is used directly — the
`[FAIL]` line above documents this (dedup_key = `nasty@x`, not a hash). The hash fallback genuinely
exists, but it is triggered by **over-length** (>998) ids or an id that **collapses to empty** after
strip — verified separately:
```
over-length dedup_key prefix: sha256:8dc957c   over-length forces hash: True
nul-only id dedup_key prefix:  sha256:97239f6   message_id= None
```
This is more robust than the hypothesis assumed (no crash, no silent drop), so it remains
CONFIRMS-FIXED — the correction is to the *mechanism*, not the verdict.

**Tag:** ✔ CONFIRMS-FIXED.

# TC-ER-022: Compliance export AFTER erase still builds (regulator proof)

| Field | Value |
|---|---|
| **ID** | TC-ER-022 |
| **Target** | GDPR erasure + compliance export (PC-06) |
| **Suite** | RETAIN — append-only audit retained + compliance export |
| **Type** | Positive |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove the offboarded org's compliance record is STILL exportable after erasure: erase org `Y`,
then `compliance-export(Y)` → 200 with metadata (status `offboarded`, `user_count` 0) + the
retained trail including `org.erased`. This is the proof a regulator can still obtain the
deletion record post-erasure.

## Break hypothesis
The attacker's bet: after erase the export 500s (the org row was deleted, or
`get_with_user_count` chokes on the zero-user org), or the audit trail comes back empty — i.e.
the post-erasure record is unrecoverable. REFUTES-FIX / NEW = a non-200, an empty audit list,
the org metadata gone, or a secret VALUE in the post-erase bundle. (Review-added
`test_compliance_export_after_erase_still_builds`.)

## Preconditions
- Live stack `:8000`; harness inside the backend container over stdin.
- Fresh run-stamped org `Y` (slug prefix `retain-er022`), erased ONLY by this case.
- Platform auth via canonical `/platform/login`.

## Steps
1. Platform login; `provision_company` → org `Y`.
2. `erase_org(Y, confirm_slug=Y.slug, password=PLATFORM_PW)` → 200 certificate.
3. `compliance_export(Y)` → 200.
4. Assert `organization.status == "offboarded"`, `user_count == 0`; `audit` non-empty and
   INCLUDES `org.erased`; `generated_at` present.
5. Secret-VALUE scan (benign count fields like `tokens_deleted` stripped before scanning).

## Expected result
- Export after erase → 200 (NOT 404/500); status `offboarded`, user_count 0; `audit` non-empty
  incl `org.erased`; no secret VALUE in the body.

## Harness
Script: `harness/tc_022.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_022.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 21:44 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Erase returned 200 (`users_erased:1`, `tokens_deleted:1`, `decider_scrubbed:0` — no decided
> grant on this org). Post-erase `compliance-export(Y)` returned 200 with status `offboarded`,
> `user_count 0`, `legal_hold false`. `audit` was a non-empty list (3 entries) INCLUDING
> `org.erased` alongside the retained pre-erase events. `generated_at` present. A first scan
> flagged the substring `token`, traced to the certificate's `tokens_deleted` COUNT in the
> `org.erased` `details` (a metadata field NAME, not a token value) — harness over-match, not a
> leak. With benign count fields stripped, the secret-VALUE scan found ZERO hits.

**Evidence**

```
=== TC-ER-022 ===
ORG_ID: a674e858-f1bd-4c37-b139-77cfc9867719
SLUG: retain-er022-19e84808740b626
ERASE: 200
CERT users_erased: 1 tokens_deleted: 1 decider_scrubbed: 0 status: offboarded
EXPORT_AFTER_ERASE: 200
ORG_STATUS: offboarded
ORG_USER_COUNT: 0
ORG_LEGAL_HOLD: False
AUDIT len: 3 actions: ['org.erased', 'auth.login.success', 'org.onboard']
HAS_ORG_ERASED: True
GENERATED_AT PRESENT: True
ORG_ERASED_DETAILS: {"reason": "GDPR offboarding (test)", "users_erased": 1, "tokens_deleted": 1, "audit_log_retained": true, "support_decider_emails_scrubbed": 0}
SECRET_VALUE_HITS (benign count fields stripped): []
RESULT: PASS

--- first-run false positive (token = the tokens_deleted COUNT, a metadata field name) ---
SECRET_SUBSTRING_HITS: ['token']
... "users_erased": 1, "tokens_deleted": 1, "audit_log_retained": tru ...
```

**Verdict**

The defense held. The org row is retained at `offboarded` as the subject of the compliance
record (`erasure_service.py:106`), so `export_compliance` (`erasure_service.py:140-162`) still
resolves the org via `get_with_user_count` (user_count now 0) and returns the retained trail
including `org.erased`. A regulator can still obtain the post-erasure deletion record. The lone
`token` substring is the honest `tokens_deleted` count in the certificate details — content-blind
holds. Confirms PC-06-AC7 after erase and validates the review-added
`test_compliance_export_after_erase_still_builds`.

**Notes / follow-up**

The first-pass `token` hit was a harness over-match (fixed: strip benign count fields, scan for
`token_hash`/`password`/`hash`/JWT VALUES). Pairs with TC-ER-020 (the trail's survival).

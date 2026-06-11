# TC-IM-C01 — Deep-nested multipart → RecursionError → silent permanent drop

| ID · Suite · Type · Mode |
|---|
| TC-IM-C01 · C (Parse & data quality) · Fuzz · pure + runner |

| Result · Tag · Severity · Status |
|---|
| ❌ Fail · 🆕 NEW · Medium · Executed |

## Objective
Prove a deeply-nested multipart email (~300 levels) breaks the parser's "NEVER raises on
stdlib-parseable input" contract, and that the failure has a real downstream consequence: in the
production sync path the crafted email is dropped **forever**, with the run still reporting success.

## Break hypothesis
`parse_email` recurses through the MIME tree (stdlib `message_from_bytes` + `get_body`/
`iter_attachments`). At ~300 nesting levels the default recursion limit (1000) is exceeded →
`RecursionError`. `EmailIngestService.ingest_email` has no try/except, so it propagates. In the
`ConnectorSyncRunner`, `_ingest_one`'s `except Exception` (RecursionError ⊂ RuntimeError ⊂ Exception)
catches it → returns `"failed"` → `tracker.fail(uid)` → the UID is marked *accounted* (cursor steps
over it) **and** persisted to `failed_uids` (so a later run also steps over it). The poison email is
never stored and never retried — cleared only on a UIDVALIDITY reset.

## Steps
1. Build an RFC822 message wrapping a `text/plain` part in 300 nested `multipart/mixed` boundaries.
2. Bare leg: call `parse_email(raw, mailbox)` and observe the exception.
3. Runner leg: seed a run-stamped connection (real AES-GCM ciphertext), claim it, open a run-ledger
   row, feed the poison email as `uid=42` through a `ConnectorSyncRunner` over an inline fake
   connector. Read back the email count, the ledger row, and the folder cursor.

## Expected
- Bare: `parse_email` raises `RecursionError` (contract says best-effort, not crash).
- Runner: `messages_stored=0`, `messages_failed=1`, run `status=succeeded`, cursor `last_seen_uid≥42`,
  `42 ∈ failed_uids` → the email is silently and permanently dropped.

## Execution result (2026-06-09)

Bare parse (suite_c_parse_quality.py):
```
[FAIL] C01_deep_multipart_raises_recursionerror :: raised=RecursionError (RecursionError EXPECTED — never-raises contract broken; runner-fail proven in ingest script)
```

Runner chain (suite_c_ingest_runner.py):
```
Sync: email uid 42 in folder INBOX failed to ingest — stepping over it
[FAIL] C01_runner_steps_over_recursionerror_email_forever :: emails=0 ledger(failed=1,stored=0,status=succeeded) cursor(last_seen=42,failed_uids=[42]) (uid 42 dropped FOREVER — cleared only on UIDVALIDITY reset)
```

**Verdict:** ❌ Fail — defect reproduced live end-to-end. The bare parser raises `RecursionError`
(violating the never-raises contract at `email_parser.py:9-11`), and the production runner silently
swallows it: the crafted email is **never stored, never retried, and the run reports `succeeded`** —
an attacker can post a single 300-deep MIME email and have it vanish with no error surfaced. Note the
parser docstring caveat ("a truly unparseable blob may still raise") partially excuses the *raise*,
so the load-bearing finding is the **silent permanent drop + green run status**, not the raise alone.
This overlaps the generic step-over-forever class tracked as TC-IM-B08; C01 is the concrete
attacker-crafted trigger.

**Tag:** 🆕 NEW · Severity Medium (no cross-tenant leak; bounded to a single crafted email, but a
real never-lose-mail breach with a success status hiding it).

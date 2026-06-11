| ID · Suite · Type · Mode | TC-IM-A02 · A (Connection plane & credential cipher) · Adversarial · pure + db |
|---|---|
| Result ⚠️Pass-with-concern · Tag 🆕NEW · Severity Info · Status Executed | |

## Objective
Show that the connector credential ciphertext carries **no associated data (AAD)** binding it to the row/org it belongs to, so a `secret_ciphertext` blob is **portable**: it decrypts under the shared process key regardless of which `connector_connection` row (and therefore which org) it sits on.

## Break hypothesis
`encrypt()` / `decrypt()` both pass `associated_data=None` (`credential_cipher.py:80`, `:94`). AES-GCM can authenticate a context string (e.g. `org_id || connection_id`) as AAD so that a blob only decrypts in its own context. Without it, the blob is self-contained: anyone able to write `secret_ciphertext` onto another org's row makes that row decrypt to the **other org's** IMAP credential under the single process-wide key. This is not an exploitable cross-tenant *read* on its own (it needs a direct DB write, which RLS + the app-layer org filter gate), but it removes a defense-in-depth layer: a single SQL-injection or a future ingest/global-engine bug that can write the ciphertext column has nothing stopping a blob from being replayed across orgs.

## Steps
1. **Pure:** encrypt a secret with one cipher instance; decrypt the **same bytes** with a *second* freshly-constructed cipher (same key) → same plaintext. Decrypt under a *different* key → `ConnectorSecretError` (so it's the key, not absence of auth, that gates it).
2. **Service/DB-level (the stronger proof):** seed two `connector_connection` rows in **two different orgs**, each with its own encrypted secret. Then, as the OWNER role, **transplant org A's `secret_ciphertext` onto org B's row** (a direct DB write). Decrypt org B's row under the live process key.

## Expected
The same blob decrypts under any cipher with the same key (no per-instance / per-row binding); org B's row, after the transplant, decrypts to **org A's** secret — demonstrating the blob is not bound to org/connection.

## Execution result (2026-06-09)
```
[PASS] a02_pure_blob_portable_same_key :: second-instance decrypt=True, wrong-key-blocks=True
[PASS] a02_transplanted_blob_decrypts_to_other_orgs_secret :: orgB row now decrypts to orgA secret? True (recovered='A-SECRET-c225cf88dea4')
    note: ids 0543e59b-...,772d8031-... cleaned up by stamp in finally
```
**Verdict:** ⚠️ Pass-with-concern. The portability property is confirmed at both the cipher and the row/DB level: nothing binds a credential blob to its org. **This is NOT an exploitable cross-tenant leak today** — moving a blob between rows requires a direct write to `secret_ciphertext`, and both RLS (`oneai_app` NOBYPASSRLS, migration 0009) and the app-layer `get_in_org` filter gate which row a tenant can touch. It is a **defense-in-depth gap**: AAD = `org_id`/`connection_id` would make a transplanted blob fail to decrypt, neutralizing any future column-write bug. **Tag:** 🆕 NEW · **Severity:** Info (no current exploit path; hardening recommendation — bind context as AAD).

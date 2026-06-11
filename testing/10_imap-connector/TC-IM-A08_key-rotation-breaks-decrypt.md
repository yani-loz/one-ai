| ID · Suite · Type · Mode | TC-IM-A08 · A (Connection plane & credential cipher) · Adversarial · pure + http |
|---|---|
| Result ⚠️Pass-with-concern (defect reproduced) · Tag 🆕NEW · Severity Low · Status Executed | |

## Objective
Show that `secret_key_version` is **stored** on every connection row but **never used to select a decrypt key** — the cipher always decrypts with the *current* process key. Therefore rotating `CONNECTOR_SECRET_KEY` silently breaks decrypt for **every existing connection**: its stored credential becomes unreadable, so `/test` (and sync) can no longer authenticate, with no migration/re-encrypt path.

## Break hypothesis
A key rotation is the standard incident response when a key may be compromised. The model stamps `secret_key_version` (`connector_connection.py:80-82`) "so an app-wide key rotation can later tell which rows used which key" (docstring) — but `CredentialCipher.decrypt` (`credential_cipher.py:83-96`) takes no version and uses `self._key` unconditionally; `SECRET_KEY_VERSION` is a hard-coded constant `1` and there is no keyring / decrypt-by-version. So a rotation makes all stored ciphertext undecryptable: a fail-shut (not a leak), but an operational footgun with no in-product recovery (re-encrypt requires the *original* plaintext, which is gone).

## Steps
1. **Pure:** confirm `key_version` is the same constant under two different keys; encrypt under key1, decrypt under a cipher built from key2 → `ConnectorSecretError` (no version dispatch exists).
2. **Service-level (stronger):** seed a connection whose ciphertext was made under the live key (`DEV_KEY`). Override the connectors dependency to build the cipher from a **different** key (`OTHER_KEY`, simulating a rotation) and `POST /connectors/{id}/test`. Observe the outcome.

## Expected
Pure: versions equal, rotated-key decrypt raises `ConnectorSecretError`. Service: `/test` fails to decrypt the stored credential, the service isolates it to `ok=False` → **HTTP 200 with `status='error'`** (a non-500 "connection broken" state) — the connection is now un-testable purely because the key rotated, despite carrying `secret_key_version=1`.

## Execution result (2026-06-09)
```
[PASS] a08_rotated_key_breaks_decrypt_pure :: key_version both=1, decrypt-under-rotated-key fails=True

# service path — the logged traceback below is the EXPECTED failure-isolation inside _verify:
Connection test failed unexpectedly for connection 3bf10ba4-...
  File ".../credential_cipher.py", line 94, in decrypt
    return AESGCM(self._key).decrypt(nonce, ciphertext, None).decode("utf-8")
cryptography.exceptions.InvalidTag
  ... raise ConnectorSecretError("Stored credential could not be decrypted.")
[PASS] a08_rotated_key_breaks_test_service_level :: status_code=200 body.status=error
```
**Verdict:** ⚠️ Pass-with-concern — defect reproduced (fail-shut, no exposure). A rotated `CONNECTOR_SECRET_KEY` renders every existing connection's stored credential undecryptable; `/test` reports `error` (HTTP 200 — the service's failure isolation holds, no 500), and there is no decrypt-by-version path to recover. The stored `secret_key_version` is currently inert metadata. **Recommendation:** make `decrypt` select the key by the row's `secret_key_version` from a small keyring (old key kept for read, new key for write), or add a documented re-encrypt migration. **Tag:** 🆕 NEW (not in `docs/FIX_BEFORE_PROD.md` — the doc covers JWT/DB-password rotation, *not* connector-secret decrypt-by-version) · **Severity:** Low (fail-shut, not a leak; bites only on an intentional rotation, but breaks every connection with no in-product recovery).

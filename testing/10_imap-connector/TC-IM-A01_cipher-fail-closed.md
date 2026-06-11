| ID · Suite · Type · Mode | TC-IM-A01 · A (Connection plane & credential cipher) · Negative · pure |
|---|---|
| Result ✅Pass · Tag — · Severity — (defense held) · Status Executed | |

## Objective
Prove the credential cipher (AES-256-GCM, `nonce(12) || GCM(ct+tag)`) fails **closed**: a wrong key, a 1-bit-tampered tag, and a truncated blob (`len <= 12`) each raise `ConnectorSecretError` — never returning garbage plaintext.

## Break hypothesis
GCM authentication could be skipped or an exception of the wrong type could leak through, letting a tampered/foreign blob decrypt to attacker-influenced bytes that then flow into an IMAP login as a "password."

## Steps
1. `CredentialCipher(STRONG_KEY).encrypt("imap-app-password-hunter2")` → round-trip back to plaintext.
2. Decrypt the blob under a cipher built from a **different** key.
3. Flip the last byte (the GCM tag) and decrypt under the original key.
4. Decrypt blobs of length `0`, `5`, `12` (all `<= _NONCE_BYTES`).

## Expected
Round-trip succeeds; steps 2–4 each raise `ConnectorSecretError` (the only mapped failure), no garbage, no other exception type.

## Execution result (2026-06-09)
```
[PASS] a01_round_trip :: decrypt==plaintext
[PASS] a01_wrong_key_fails_closed :: ConnectorSecretError (no garbage)
[PASS] a01_tampered_tag_fails_closed :: ConnectorSecretError on 1-bit flip
[PASS] a01_truncated_blob_fails_closed :: all <=12 -> ConnectorSecretError
```
**Verdict:** ✅ Pass. The cipher authenticates every blob; tamper/wrong-key/truncation all fail closed via `ConnectorSecretError` (`credential_cipher.py:90-96`). **Tag:** — (positive/contract — proves once).

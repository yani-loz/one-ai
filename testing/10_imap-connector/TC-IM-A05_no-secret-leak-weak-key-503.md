| ID · Suite · Type · Mode | TC-IM-A05 · A (Connection plane & credential cipher) · Negative · http |
|---|---|
| Result ✅Pass · Tag — · Severity — (defense held) · Status Executed | |

## Objective
`ConnectionResponse` exposes **no** secret material (no `password`, `secret_ciphertext`, or `secret_key_version`) and never echoes the submitted password; and a deployment with a **weak connector key in a non-dev env** fails closed with **503**, without echoing the key.

## Break hypothesis
A response model that serialized the ORM row directly would leak the ciphertext or key version; an unhandled weak-key error would surface as a 500 (or echo the key in the message), revealing config.

## Steps
1. Create a connection with a recognizable password; assert the 201 body has none of `{password, secret, secret_ciphertext, secret_key_version}` and the password string is absent from the raw text.
2. Override the connectors dependency's settings reader to `{connector_secret_key=DEV_KEY, requires_secure_secrets=True}` (simulating a non-dev env still on the dev key); `POST /connectors`; assert 503 and that `DEV_KEY` is not in the body.

## Expected
No secret field/value in the create response; weak-key create → 503; key not echoed.

## Execution result (2026-06-09)
```
[PASS] a05_response_carries_no_secret_field :: forbidden keys present=[]
[PASS] a05_response_does_not_echo_password :: pw in body? False
[PASS] a05_weak_key_returns_503 :: status=503
[PASS] a05_weak_key_not_echoed :: key echoed? False
```
**Verdict:** ✅ Pass. `ConnectionResponse` (`connector_schemas.py:40-92`) is an explicit allow-list of non-secret fields; `CredentialCipher.__init__` fails closed on the dev/weak key when `require_secure=True` → `ConnectorConfigurationError` → mapped to 503 (`exceptions.py`, error_handlers), message does not contain the key. **Tag:** — (positive/contract).

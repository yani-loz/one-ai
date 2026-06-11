# TC-IM-C07 — Charset lie / unknown charset / RFC 2047 bomb → never raises

| ID · Suite · Type · Mode |
|---|
| TC-IM-C07 · C (Parse & data quality) · Fuzz · pure |

| Result · Tag · Severity · Status |
|---|
| ✅ Pass · — · Info · Executed |

## Objective (prove once)
Confirm the never-raises robustness contract holds across three decode hazards.

## Break hypothesis
A bogus/unknown declared charset, a charset *lie* (declares utf-8, body is invalid utf-8), or a
pathological RFC 2047 encoded-word (bad base64 / unknown charset / truncated token) might raise
through the decode path.

## Steps
Parse three crafted emails: (a) `charset=not-a-real-charset`; (b) `charset=utf-8` with `\xff\xfe\x80`
bytes; (c) subject `=?utf-8?b?not_valid_base64!!?= =?bogus-cs?q?x?= =?utf-8?b?dHJ1bmM` (truncated).

## Expected
No exception; each yields a best-effort decoded body/subject (replacement chars allowed).

## Execution result (2026-06-09)
```
[PASS] C07_charset_and_rfc2047_decode_with_replacement_never_raises :: unknown_ok=True lie_ok=True rfc2047_ok=True raised=[]
```

**Verdict:** ✅ Pass — defence held. All three decoded with replacement, none raised; the
`errors='replace'` fallbacks (`_decode_text_part`, `safe_str`) work as designed. Routine-positive.

**Tag:** — (positive/contract).

# TC-OL-037: Legal-hold bool coercion breadth (Pydantic v2 lax-bool)

| Field | Value |
|---|---|
| **ID** | TC-OL-037 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Boundary / Fuzz |
| **Severity if it fails** | Info |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern |
| **Finding tag** | — (NA — pure schema characterization; no claimed fix, no auth impact) |

## Objective
Characterize how `LegalHoldUpdateRequest.legal_hold` coerces non-bool JSON inputs. The
test designer predicted `'yes'`/`2`/`'maybe'` → 422 and `'true'`/`1` may coerce. Record the
ACTUAL codes — Pydantic v2's lax-bool validator coerces a wider string set than a strict
`bool` would.

## Break hypothesis
The field accepts a non-bool value and silently mis-sets the hold (e.g. a typo'd string
coerces to a truthy value the operator didn't intend), or — more seriously — coercion of a
malformed value reaches the DB and yields a 500. A defect would be a 500, or a value that
gates auth (ruled out by TC-OL-039).

## Preconditions
Live stack. Fresh run-stamped org (`contract37-<stamp>`). Demo platform token. Restored to
`legal_hold=false` at the end.

## Steps
1. Platform-login; `provision_company(prefix="contract37")`.
2. PATCH legal-hold with `'yes'`, `2`, `'maybe'`, `'true'`, `1`, `False`; record each code.
3. PATCH with an extra field; PATCH with `{}`; assert both 422.
4. Restore `legal_hold=false`.

## Expected result (per the contract, not the designer's guess)
Pydantic v2 lax-bool: `{1,'1','on','t','true','y','yes'}`→True, `{0,'0','off','f','false','n','no'}`→False
(case-insensitive). Non-members like `2`/`'maybe'` → 422. Extra/missing → 422. No 500.

## Harness
Script: `harness/tc_037.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_037.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** — (NA)

**Actual behavior**

> `'yes'` coerced to **True (200)** — NOT the 422 the test designer predicted. `'true'` and
> `1` also coerced to True (200). `2` and `'maybe'` were rejected (422 `bool_parsing`).
> `False` → 200. Extra field → 422 `extra_forbidden`; missing → 422 `missing`. No 500 on any
> input. The validator is Pydantic v2's documented lax-bool set, broader than a strict bool.

**Evidence**

```
legal_hold='yes' -> 200 echoed=True   body={...,"legal_hold":...}
legal_hold=2     -> 422 echoed=None   {"type":"bool_parsing","loc":["body","legal_hold"],"msg":"Input should be a valid boolean, unable to interpret input","input":2}
legal_hold='maybe' -> 422 echoed=None {"type":"bool_parsing",...,"input":"maybe"}
legal_hold='true' -> 200 echoed=True
legal_hold=1     -> 200 echoed=True
legal_hold=False -> 200 echoed=False
extra_field -> 422 {"type":"extra_forbidden","loc":["body","extra"],...}
missing_legal_hold -> 422 {"type":"missing","loc":["body","legal_hold"],...}
```

**Verdict**

The defense held — but with a recorded caveat. `legal_hold: bool` in
`LegalHoldUpdateRequest` (`backend/app/identity/schemas/platform_schemas.py:94-99`) uses
Pydantic v2's default **lax** bool coercion, so string aliases like `'yes'`/`'true'` and
int `1` are accepted, contradicting the test designer's "→422" prediction for `'yes'`. This
is **not a defect and not REFUTES-FIX**: there is no claimed strict-bool fix, no 500 occurs,
and legal_hold does not gate authentication (proven by TC-OL-039), so the broader coercion
has no security consequence today. The behaviour is the standard framework contract; the
designer's expectation, not the code, was off. Tagged NA (pure schema characterization).

**Notes / follow-up**

If a stricter API contract is ever desired (reject `'yes'`/`1`, accept only JSON `true`/`false`),
use `StrictBool` on the field — but that is a product choice, not a security gap. Org
restored to `legal_hold=false`.

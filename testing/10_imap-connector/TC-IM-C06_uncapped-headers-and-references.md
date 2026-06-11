# TC-IM-C06 — Uncapped JSONB headers + unbounded references array

| ID · Suite · Type · Mode |
|---|
| TC-IM-C06 · C (Parse & data quality) · Boundary · pure |

| Result · Tag · Severity · Status |
|---|
| ⚠️ Pass-with-concern · 🆕 NEW · Low · Executed |

## Objective
Show that (a) a single huge `X-*` header is stored verbatim into `email_message.headers` (JSONB) and
(b) the `references` array is unbounded — both row-bloat / DoS levers.

## Break hypothesis
`build_headers` (headers.py:45-50) accumulates the **entire** header set verbatim with no per-value
or total cap. `split_references` returns every `<id>` token found, with no count cap. So a 1 MB `X-`
header and a 10k-entry References header both persist in full.

## Steps
Parse an email with `X-Bloat:` carrying ~1 MB and `References:` carrying 10,000 ids. Inspect the
stored header length and the `references` list length.

## Expected
`headers['X-Bloat']` ≈ 1 MB, `len(references)` == 10,000 → neither is capped.

## Execution result (2026-06-09)
```
[FAIL] C06_megabyte_header_stored_verbatim :: X-Bloat len=1048576 (CA-CONN-05 verbatim retention)
[FAIL] C06_references_array_uncapped :: references count=10000 (NEW — unbounded ARRAY, no cap)
```
(`[FAIL]` = no cap; the "defence held" assertion was that a cap applies.)

**Verdict:** ⚠️ Pass-with-concern — both reproduced live.
- The **verbatim full-header retention** (1 MB X- header survives) is the documented data-minimization
  trade-off **CA-CONN-05** in `docs/FIX_BEFORE_PROD.md` (📋 CONFIRMS-DOCUMENTED) — tracked, to be
  signed off or replaced with an allowlist before prod.
- The **unbounded `references` array** (10k ids stored) is *not* tracked anywhere — a genuinely-new
  row-bloat lever (an attacker can pad References arbitrarily). This is the 🆕 NEW half.

Both Low severity (tenant-scoped, no egress). Reported under a single `NEW` tag because the
references-array bloat is the untracked defect; the header half is noted as CONFIRMS-DOCUMENTED
(CA-CONN-05) in-text per the catalog's dual-tag guidance.

**Tag:** 🆕 NEW (references array) · also 📋 CONFIRMS-DOCUMENTED (CA-CONN-05 verbatim headers) ·
Severity Low.

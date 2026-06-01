# TC-PC-044: Slug pattern boundary — invalid slugs → 422

| Field | Value |
|---|---|
| **ID** | TC-PC-044 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | ONB — Onboarding contracts + input validation/fuzz |
| **Type** | Boundary |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
`org_slug` is bounded by `pattern=^[a-z0-9][a-z0-9-]*$`, `min_length=1`, `max_length=100`. Every
malformed slug must be rejected at the Pydantic boundary with **422** (never reaching the DB).

## Break hypothesis
A malformed slug (uppercase, leading hyphen, space, empty, or 101 chars) slips past validation and
either reaches the DB (causing a 500 or storing a bad slug) or is silently normalized.

## Preconditions
- Live stack; demo platform admin token.
- Cases: `"Bad"` (uppercase), `"-bad"` (leading hyphen), `"a b"` (space), `""` (empty),
  `"a"*101` (over max). Run-stamped emails.

## Steps
1. For each invalid slug, `onboard_org` with an otherwise-valid payload.
2. Assert 422 and capture the Pydantic error type per field.

## Expected result
All five → 422. Pattern violations → `string_pattern_mismatch`; empty → `string_too_short`;
101-char → `string_too_long`. No DB write, no 500.

## Harness
Script: `harness/tc_044.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_044.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 08:54 local
- **Result:** ✅ Pass
- **Finding tag:** — (pure contract/boundary test of an original schema constraint)

**Actual behavior**

> All five malformed slugs rejected with 422 and the precise validation type per field — none
> reached the service or DB.

**Evidence**

```
[uppercase 'Bad'] len=3 -> status=422
   422 detail loc/type: [(['body', 'org_slug'], 'string_pattern_mismatch')]
[leading-hyphen '-bad'] len=4 -> status=422
   422 detail loc/type: [(['body', 'org_slug'], 'string_pattern_mismatch')]
[space 'a b'] len=3 -> status=422
   422 detail loc/type: [(['body', 'org_slug'], 'string_pattern_mismatch')]
[empty ''] len=0 -> status=422
   422 detail loc/type: [(['body', 'org_slug'], 'string_too_short')]
[101-char (over max)] len=101 -> status=422
   422 detail loc/type: [(['body', 'org_slug'], 'string_too_long')]
```

**Verdict**

Defense held. `OrganizationCreateRequest.org_slug`
(`platform_schemas.py:43`: `Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")`)
rejects every malformed input at validation, returning 422 before the service runs. Tagged "—"
(pure contract test): the slug pattern is an original schema constraint, not a fixed audit finding,
so neither CONFIRMS-FIXED nor a NEW defect — the defense simply holds as specified.

**Notes / follow-up**

The pattern also implicitly blocks trailing-hyphen-only and unicode slugs; the five chosen cover the
documented constraint corners.

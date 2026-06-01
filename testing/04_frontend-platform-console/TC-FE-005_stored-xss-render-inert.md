# TC-FE-005: Stored-XSS render of org_name / full_name is inert ⭐

| Field | Value |
|---|---|
| **ID** | TC-FE-005 |
| **Target** | Frontend (console render surfaces) |
| **Suite** | XSS / output encoding |
| **Type** | Adversarial |
| **Severity if it fails** | High (stored XSS in the super-admin console) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Company `org_name` / `admin_full_name` carrying HTML/script markup must render as **inert escaped text**
everywhere they appear (onboard success heading + `CompanyCard` list), never as live DOM.

## Break hypothesis
The onboard success panel or company card renders the name via raw HTML, so an
`<img onerror>` / `<script>` payload executes (stored XSS in the highest-privilege screen).

## Preconditions
Platform admin. Backend `SafeName` allows printable `<>"` (only control chars rejected), so the payload
is stored and reaches the render path. Execution sentinel `window.__xss_fired` armed.

## Steps
1. Arm `window.__xss_fired=false`.
2. Onboard an org with `org_name` = `<img src=x onerror="window.__xss_fired=true">` and the same payload in
   `admin_full_name`; slug `xss-render-test`; valid email/password.
3. On the success panel: assert `__xss_fired===false`, **zero** injected `<img>` with that handler, and the
   payload visible as literal text.
4. Click Done → list refreshes → re-assert on the `CompanyCard`.

## Expected result
No execution, no injected element; the payload shown verbatim as escaped text in both surfaces.

## Harness
Playwright MCP: `browser_fill_form`, `browser_click`, `browser_evaluate` (sentinel + DOM scan), `browser_snapshot`.

---

## Execution result

- **Run at:** 2026-06-01 ~11:08 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior / Evidence**
```
typing payload into inputs: __xss_fired=false
SUCCESS PANEL (a11y snapshot): heading e153 = `<img src=x onerror="window.__xss_fired=true"> is live`  (rendered as TEXT)
  → xss_fired=false; injected_img_elements=0; payload_rendered_as_text=true
AFTER "Done" (CompanyCard list): xss_fired_after_list_render=false; injected_img_elements=0;
  payload_in_list_as_text=true; new "xss-render-test" card present (count=1)
```

**Verdict**
Defense held on both render surfaces. The org name flows through React's default text interpolation
(`OnboardSuccess.tsx` h2 `{company.organization.name}`; `CompanyCard` name `<p>`), which HTML-escapes it →
the `<img onerror>` became inert text, no element was created, the sentinel never fired. Grep confirmed
**no `dangerouslySetInnerHTML`/`innerHTML`/`eval`** anywhere in `frontend/src`. **Non-vacuous:** the
dangerous string demonstrably reached the heading render path and was neutralized.

**Notes / follow-up**
This created a persistent backend org `xss-render-test` (markup name) + its admin — left in place per the
"leave DB as-is" decision; clears on the next `TRUNCATE` + re-seed.

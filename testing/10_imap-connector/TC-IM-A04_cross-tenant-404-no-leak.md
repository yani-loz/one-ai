| ID · Suite · Type · Mode | TC-IM-A04 · A (Connection plane & credential cipher) · Adversarial · http |
|---|---|
| Result ✅Pass · Tag — · Severity — (defense held; cross-tenant surface) · Status Executed | |

## Objective
The hardest rule, on the connector plane: org B presenting org A's `connection_id` to **every** verb (`GET`, `/test`, `/disable`, `/enable`, `DELETE`) gets **404** — never a 403/200-empty existence leak, and **no A-owned data** (username, host, last_error) appears in any B response. B's `DELETE` must not touch A's row.

## Break hypothesis
A missing org scope on any one verb would let a company_admin read/mutate/delete another tenant's connection, or distinguish "exists but not yours" (403) from "doesn't exist" (404) — a cross-tenant existence oracle and a data leak (clause §З).

## Steps
1. Org A creates a connection (run-stamped username + host).
2. Org B (different forged token) calls all five verbs against A's `connection_id`.
3. Scan every B response body for A's username / host.
4. Org A re-`GET`s its connection (must still be 200 — B's delete didn't bite).

## Expected
All five B calls → 404; no A data in any B body; A's row survives.

## Execution result (2026-06-09)
```
[PASS] a04_cross_tenant_all_verbs_404 :: statuses={'get': 404, 'test': 404, 'disable': 404, 'enable': 404, 'delete': 404}
[PASS] a04_no_A_data_leaked_to_B :: username/host leaked to B? False
[PASS] a04_owner_row_survives_B_delete :: owner GET=200
```
**Verdict:** ✅ Pass. Every verb resolves through `ConnectorService.get_connection → repo.get_in_org(id, org_id)` (`connector_service.py:96-101`), so a foreign id is `None → ConnectionNotFoundError → 404` before any work, with no A data and no row mutation. Driven on the live RLS-enforced tenant engine. **Tag:** — (positive/contract; the non-negotiable held). No finding, so **Severity —**; the surface is the cross-tenant non-negotiable, so a *failure* here would have been High.

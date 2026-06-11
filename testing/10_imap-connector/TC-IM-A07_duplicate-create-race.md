| ID · Suite · Type · Mode | TC-IM-A07 · A (Connection plane & credential cipher) · Concurrency · http |
|---|---|
| Result ✅Pass · Tag — · Severity Low · Status Executed | |

## Objective
Concurrent identical `POST /connectors` for the same `(org, connector_type, username)` resolve to **exactly one 201**, the losers **409**, and **never a 500** — with exactly one persisted row. Prove the **unique-constraint backstop** (`uq_connector_connection_identity`) actually catches the race, not just the `exists()` pre-check.

## Break hypothesis
The service does an `exists()` read then an insert. Under true concurrency both racers can pass `exists()==False`, then both insert — if the resulting `IntegrityError` weren't translated, the loser would surface as an uncaught **500** (and/or a second row could persist).

## Steps
1. **Durable claim:** fire 8 truly-concurrent identical creates (each on its own tenant DB session) via `asyncio.gather`; tally status codes; count rows in the DB as the OWNER role.
2. **Backstop proof:** monkeypatch `ConnectorConnectionRepository.exists` (test-only, in-container) with a **2-party barrier** so both racing creates pass `exists()==False` *before either inserts* — guaranteeing the 409 comes from the unique-constraint `IntegrityError` path, not from the loser seeing a committed row. Fire 2 concurrent creates; assert `[201, 409]` and one row.

## Expected
8-way: exactly one 201, seven 409, zero 5xx, one row. Barrier: exactly `[201, 409]`, one row.

## Execution result (2026-06-09, stable across 2 runs)
```
[PASS] a07_concurrent_exactly_one_201_no_500 :: codes=[201, 409, 409, 409, 409, 409, 409, 409] (201=1 409=7 5xx=0) rows=1
[PASS] a07_exactly_one_row_persisted :: persisted rows=1
[PASS] a07_barrier_forces_integrityerror_409_not_500 :: pair=[201, 409] rows=1 (both passed exists()==False, so 409 came from the unique constraint)
```
**Verdict:** ✅ Pass. `ConnectorService.create_connection` catches the insert-race `IntegrityError` and re-raises `DuplicateConnectionError → 409` (`connector_service.py:84-90`); the unique constraint is the true backstop. The barrier variant forces the `IntegrityError` path deterministically (both racers confirmed past `exists()==False`), so the 409-not-500 guarantee is proven, not incidental. **Tag:** — (positive/contract).

"""TC-IM-E07 — The org GUC must not leak across pooled transactions on the tenant engine.

Break hypothesis: tenant isolation rides on `set_config('app.current_org_id', <org>, is_local=true)`
re-applied per transaction (database.py:90-102, after_begin listener). If `is_local` were wrong, or
the GUC survived a transaction/checkin, a pooled connection reused for org B could still carry org A's
GUC and leak A's rows to B (a cross-tenant exposure). This drives the real `oneai_app` (NOBYPASSRLS)
role and probes three leakage surfaces:
  (1) SEQUENTIAL on ONE connection: a txn as org A then a txn as org B on the SAME physical connection
      — the second must see ONLY B (the local GUC resets at COMMIT and is re-set per txn).
  (2) POST-TXN bleed: after an A-scoped txn commits, a NON-transactional read on the same connection
      sees ZERO of A's rows (the local GUC is gone -> NULL -> fail-closed, no leak).
  (3) INTERLEAVED concurrency: two connections, two orgs, transactions interleaved via asyncio —
      each sees ONLY its own org's rows throughout.

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/rls_guc_concurrency.py

Non-destructive: seeds two RUN-STAMPED throwaway orgs via the OWNER engine, asserts, cleans up its
own rows in a finally block. Never touches the demo org.
"""
from __future__ import annotations

import asyncio
import uuid

import asyncpg

from app.core.config import get_settings

S = get_settings()
STAMP = uuid.uuid4().hex[:12]
ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()
TAG = f"rls-e07-{STAMP}"

results: list[tuple[str, bool, str]] = []


def dsn(user: str, password: str) -> str:
    return f"postgresql://{user}:{password}@{S.postgres_host}:{S.postgres_port}/{S.postgres_db}"


OWNER = dsn(S.postgres_user, S.postgres_password)
APP = dsn(S.app_db_user, S.oneai_app_password)


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


async def seed(owner: asyncpg.Connection) -> None:
    for org in (ORG_A, ORG_B):
        await owner.execute(
            "INSERT INTO person (org_id, display_name, is_internal) VALUES ($1,$2,false)",
            org, f"{org} {TAG}",
        )


async def scoped_read(conn: asyncpg.Connection, org: uuid.UUID) -> set[str]:
    """Open a transaction, set the LOCAL org GUC (mirrors after_begin), read our TAG'd persons."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
        rows = await conn.fetch("SELECT org_id FROM person WHERE display_name LIKE $1", f"% {TAG}")
        return {str(r["org_id"]) for r in rows}


async def unscoped_read(conn: asyncpg.Connection) -> tuple[set[str], str]:
    """Read with NO transaction/GUC after a prior committed local-GUC txn.

    Returns (visible_orgs, mode). mode is 'empty' if the read returned zero rows (truly-unset GUC ->
    NULL), or 'errored' if the leftover empty-string GUC made ''::uuid raise. BOTH are fail-closed —
    a LEAK is only if a prior org's rows come back. We catch the asyncpg error so the harness can
    distinguish "fail-closed by error" from "fail-closed by empty" without aborting the run.
    """
    try:
        rows = await conn.fetch(
            "SELECT org_id FROM person WHERE display_name LIKE $1", f"% {TAG}"
        )
        return {str(r["org_id"]) for r in rows}, "empty"
    except asyncpg.PostgresError as exc:
        return set(), f"errored ({type(exc).__name__}: {str(exc)[:50]})"


async def interleaved(conn: asyncpg.Connection, org: uuid.UUID, hits: list[set[str]]) -> None:
    """A worker that opens its org's txn, yields to the peer mid-transaction, then reads."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
        await asyncio.sleep(0.05)  # force the scheduler to interleave with the peer's txn
        rows = await conn.fetch("SELECT org_id FROM person WHERE display_name LIKE $1", f"% {TAG}")
        await asyncio.sleep(0.05)
        rows2 = await conn.fetch("SELECT org_id FROM person WHERE display_name LIKE $1", f"% {TAG}")
        hits.append({str(r["org_id"]) for r in rows} | {str(r["org_id"]) for r in rows2})


async def main() -> None:
    owner = await asyncpg.connect(OWNER)
    app1 = await asyncpg.connect(APP)
    app2 = await asyncpg.connect(APP)
    try:
        await seed(owner)
        print(f"seeded ORG_A={ORG_A} ORG_B={ORG_B} (tag {TAG})")

        # (1) SEQUENTIAL reuse on ONE connection: A-scoped txn, then B-scoped txn — no carry-over.
        seen_a = await scoped_read(app1, ORG_A)
        seen_b = await scoped_read(app1, ORG_B)  # same physical connection, reused
        check(
            "sequential_same_conn_no_guc_carryover",
            seen_a == {str(ORG_A)} and seen_b == {str(ORG_B)},
            f"A-txn saw={seen_a}, then B-txn saw={seen_b} on the SAME connection",
        )

        # (2) POST-TXN bleed: after the committed B-txn above, an UNSCOPED read on app1 must NOT see
        #     any prior org's rows. is_local GUC resets at COMMIT; the leftover empty-string '' makes
        #     ''::uuid ERROR (fail-closed by error) rather than returning rows — also non-leaking.
        seen_unscoped, mode = await unscoped_read(app1)
        check(
            "post_txn_no_cross_org_bleed_fail_closed",
            seen_unscoped == set(),
            f"unscoped read after committed B-txn saw={seen_unscoped} via {mode} (LEAK only if A/B rows appear)",
        )

        # (3) INTERLEAVED concurrency: two connections, two orgs, transactions overlapping in time.
        hits_a: list[set[str]] = []
        hits_b: list[set[str]] = []
        await asyncio.gather(
            interleaved(app1, ORG_A, hits_a),
            interleaved(app2, ORG_B, hits_b),
        )
        check(
            "interleaved_two_orgs_each_sees_only_own",
            hits_a == [{str(ORG_A)}] and hits_b == [{str(ORG_B)}],
            f"A-worker saw={hits_a}, B-worker saw={hits_b} (each strictly its own org)",
        )

    finally:
        deleted = await owner.fetch(
            "DELETE FROM person WHERE display_name LIKE $1 RETURNING id", f"% {TAG}"
        )
        print(f"cleanup: deleted {len(deleted)} person rows")
        await owner.close()
        await app1.close()
        await app2.close()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT: {passed}/{len(results)} checks passed")
    print("VERDICT:", "GUC is transaction-local; no cross-txn leak" if passed == len(results)
          else "GUC LEAK — cross-tenant exposure across pooled transactions")


asyncio.run(main())

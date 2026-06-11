"""DQ measurement 1/3 — graph connectivity, resolution invariants, duplication, orphans.

Covers categories K (connectivity/orphan lens), A (resolution invariants), B (duplication/under-merge),
J (FK orphans). Read-only. Runs every metric for EACH org that holds data (LIVE dev org + every
DQ-SEED throwaway), labelling the output so the write-up can read per-org.

Faithful predicates: imports the REAL normalize_email / extract_domain / is_role_address /
is_generic_email_domain so a harness check is the production rule, not a re-implementation.

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/11_imap-data-quality/harness/10_measure_graph.py
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

import asyncpg

from app.core.config import get_settings
from app.entities.services.address_rules import is_generic_email_domain
from app.entities.services.email_normalizer import extract_domain, normalize_email

S = get_settings()
GLOBAL = (
    f"postgresql://{S.global_db_user}:{S.oneai_global_password}"
    f"@{S.postgres_host}:{S.postgres_port}/{S.postgres_db}"
)
DEV_ORG = "d1500000-0000-0000-0000-000000000001"


def pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.2f}%" if d else "n/a"


async def discover_orgs(conn: asyncpg.Connection) -> list[tuple[str, str]]:
    orgs: list[tuple[str, str]] = []
    if await conn.fetchval("SELECT count(*) FROM email_message WHERE org_id=$1", DEV_ORG):
        orgs.append(("LIVE", DEV_ORG))
    for r in await conn.fetch(
        "SELECT DISTINCT org_id FROM connector_connection WHERE display_name LIKE 'DQ-SEED%'"
    ):
        if await conn.fetchval("SELECT count(*) FROM email_message WHERE org_id=$1", r["org_id"]):
            orgs.append(("SEED", str(r["org_id"])))
    return orgs


async def measure(conn: asyncpg.Connection, label: str, org: str) -> None:
    print(f"\n{'=' * 70}\n=== {label} org {org} ===\n{'=' * 70}")
    n_person = await conn.fetchval("SELECT count(*) FROM person WHERE org_id=$1", org)
    n_company = await conn.fetchval("SELECT count(*) FROM company WHERE org_id=$1", org)
    n_msg = await conn.fetchval("SELECT count(*) FROM email_message WHERE org_id=$1", org)
    print(f"totals: persons={n_person} companies={n_company} messages={n_msg}")

    person_emails = await conn.fetch(
        "SELECT person_id, email FROM person_email WHERE org_id=$1", org
    )
    linked = {
        r["person_id"]
        for r in await conn.fetch(
            "SELECT DISTINCT person_id FROM person_company WHERE org_id=$1", org
        )
    }

    # — DQ-K01 person on non-generic domain with ZERO company link (near-invariant orphan) —
    pdoms: dict[object, list[str | None]] = defaultdict(list)
    for r in person_emails:
        pdoms[r["person_id"]].append(extract_domain(normalize_email(r["email"])))
    k01 = [
        pid for pid, doms in pdoms.items()
        if doms and all(d and not is_generic_email_domain(d) for d in doms) and pid not in linked
    ]
    print(f"[DQ-K01] non-generic person, no company link: {len(k01)} / {n_person} ({pct(len(k01), n_person)})")
    await _examples(conn, org, k01[:5], "person")

    # — DQ-K03 degree-1 singletons (person & company) —
    deg = await conn.fetch(
        """
        SELECT person_id, count(DISTINCT email_id) d FROM (
            SELECT from_person_id person_id, id email_id FROM email_message
              WHERE org_id=$1 AND from_person_id IS NOT NULL
            UNION ALL
            SELECT person_id, email_id FROM email_recipient
              WHERE org_id=$1 AND person_id IS NOT NULL
        ) t GROUP BY person_id
        """,
        org,
    )
    deg1 = sum(1 for r in deg if r["d"] == 1)
    referenced = len(deg)
    print(f"[DQ-K03] person degree-1: {deg1} / {referenced} referenced ({pct(deg1, referenced)}); "
          f"unreferenced persons={n_person - referenced}")
    cdeg = await conn.fetch(
        "SELECT company_id, count(DISTINCT person_id) d FROM person_company WHERE org_id=$1 "
        "GROUP BY company_id", org
    )
    cdeg1 = sum(1 for r in cdeg if r["d"] == 1)
    print(f"          company with exactly 1 person: {cdeg1} / {len(cdeg)} ({pct(cdeg1, len(cdeg))})")

    # — DQ-K04 blank / null display_name persons —
    k04 = await conn.fetchval(
        "SELECT count(*) FROM person WHERE org_id=$1 AND (display_name IS NULL OR btrim(display_name)='')",
        org,
    )
    print(f"[DQ-K04] blank/null display_name: {k04} / {n_person} ({pct(k04, n_person)})")

    # — DQ-A01 source-row person owns the normalized as-seen address —
    pe_set = {(r["person_id"], r["email"]) for r in person_emails}
    msgs = await conn.fetch(
        "SELECT id, from_person_id, from_address FROM email_message "
        "WHERE org_id=$1 AND from_person_id IS NOT NULL", org
    )
    a01_from = [m for m in msgs if (m["from_person_id"], normalize_email(m["from_address"] or "")) not in pe_set]
    recs = await conn.fetch(
        "SELECT email_id, person_id, address FROM email_recipient "
        "WHERE org_id=$1 AND person_id IS NOT NULL", org
    )
    a01_rec = [r for r in recs if (r["person_id"], normalize_email(r["address"] or "")) not in pe_set]
    print(f"[DQ-A01] from-link violations: {len(a01_from)}/{len(msgs)}; "
          f"recipient-link violations: {len(a01_rec)}/{len(recs)}  (INVARIANT: expect 0)")
    if a01_from:
        print(f"          e.g. from_address={a01_from[0]['from_address']!r}")
    if a01_rec:
        print(f"          e.g. recip address={a01_rec[0]['address']!r}")

    # — DQ-A03 person_email.email is its own normal form —
    a03 = [r["email"] for r in person_emails if r["email"] != normalize_email(r["email"])]
    print(f"[DQ-A03] non-normalized person_email keys: {len(a03)}/{len(person_emails)}  (INVARIANT: 0)")
    if a03:
        print(f"          e.g. {a03[:3]}")

    # — DQ-A04 person without an email; person_email orphaned from its person —
    a04a = await conn.fetchval(
        "SELECT count(*) FROM person p WHERE org_id=$1 "
        "AND NOT EXISTS (SELECT 1 FROM person_email pe WHERE pe.person_id=p.id)", org
    )
    a04b = await conn.fetchval(
        "SELECT count(*) FROM person_email pe WHERE org_id=$1 "
        "AND NOT EXISTS (SELECT 1 FROM person p WHERE p.id=pe.person_id)", org
    )
    print(f"[DQ-A04] persons w/o email: {a04a}; person_email w/o person: {a04b}  (INVARIANT: 0)")

    # — DQ-B01 Gmail dot/+subaddress duplicate persons (generic domains) —
    collapse: dict[tuple[str, str], set] = defaultdict(set)
    for r in person_emails:
        norm = normalize_email(r["email"])
        local, _, dom = norm.rpartition("@")
        if dom and is_generic_email_domain(dom) and local:
            base = local.split("+", 1)[0].replace(".", "")
            collapse[(base, dom)].add(r["person_id"])
    b01 = {k: v for k, v in collapse.items() if len(v) > 1}
    print(f"[DQ-B01] dot/+ collapsed groups with >1 person: {len(b01)}")
    for (base, dom), pids in list(b01.items())[:3]:
        print(f"          {base}@{dom} -> {len(pids)} persons")

    # — DQ-B02 same display_name, (likely) different humans —
    b02 = await conn.fetch(
        "SELECT display_name, count(*) c FROM person WHERE org_id=$1 "
        "AND display_name IS NOT NULL AND btrim(display_name)<>'' "
        "GROUP BY display_name HAVING count(*)>1 ORDER BY c DESC LIMIT 8", org
    )
    print(f"[DQ-B02] display_names shared by >1 person: {len(b02)} (top shown)")
    for r in b02[:5]:
        print(f"          {r['c']}x  {r['display_name']!r}")

    # — DQ-B03 company subdomain fragmentation (naive registrable = last 2 labels) —
    parent: dict[str, set] = defaultdict(set)
    for r in await conn.fetch("SELECT domain FROM company_domain WHERE org_id=$1", org):
        labels = str(r["domain"]).split(".")
        reg = ".".join(labels[-2:]) if len(labels) >= 2 else str(r["domain"])
        parent[reg].add(str(r["domain"]))
    b03 = {k: v for k, v in parent.items() if len(v) > 1}
    print(f"[DQ-B03] registrable domains split across >1 company_domain: {len(b03)}")
    for reg, doms in list(b03.items())[:4]:
        print(f"          {reg}: {sorted(doms)}")

    # — DQ-B05 duplicate messages: the SAME logical email stored as multiple rows. dedup keys on
    #   sha256(raw_bytes), so the same Message-ID across IMAP folders (byte-different) is NOT collapsed.
    #   Report the TRUE totals (not a LIMIT-capped sample): distinct ids, redundant rows, % of corpus.
    b05 = await conn.fetchrow(
        "SELECT count(*) total, count(DISTINCT message_id) FILTER (WHERE message_id IS NOT NULL) uniq, "
        "coalesce(sum(c-1),0) redundant FROM ("
        "  SELECT message_id, count(*) c FROM email_message WHERE org_id=$1 AND message_id IS NOT NULL "
        "  GROUP BY message_id) t", org
    )
    redundant = int(b05["redundant"])
    print(f"[DQ-B05] cross-folder duplicate rows: {redundant} redundant / {n_msg} total "
          f"({pct(redundant, n_msg)}); unique Message-IDs={b05['uniq']}")
    b05_top = await conn.fetch(
        "SELECT message_id, from_address, count(*) c FROM email_message WHERE org_id=$1 "
        "AND message_id IS NOT NULL GROUP BY message_id, from_address HAVING count(*)>1 "
        "ORDER BY c DESC LIMIT 4", org
    )
    for r in b05_top:
        print(f"          {r['c']}x  id={r['message_id']!r} from={r['from_address']!r}")

    # — DQ-B06 duplicate recipient edges per message —
    b06 = await conn.fetchval(
        "SELECT count(*) FROM (SELECT email_id, kind, lower(address) la, count(*) c "
        "FROM email_recipient WHERE org_id=$1 GROUP BY email_id, kind, lower(address) "
        "HAVING count(*)>1) t", org
    )
    n_rec = await conn.fetchval("SELECT count(*) FROM email_recipient WHERE org_id=$1", org)
    print(f"[DQ-B06] duplicate (email,kind,address) recipient edges: {b06} groups / {n_rec} rows")

    # — DQ-J01 orphan persons (no message reference at all) —
    j01 = n_person - referenced
    print(f"[DQ-J01] persons with zero message references: {j01} / {n_person} ({pct(j01, n_person)})")
    # — DQ-J02 orphan companies (no person_company link) —
    j02 = await conn.fetchval(
        "SELECT count(*) FROM company c WHERE org_id=$1 "
        "AND NOT EXISTS (SELECT 1 FROM person_company pc WHERE pc.company_id=c.id)", org
    )
    print(f"[DQ-J02] companies with zero person links: {j02} / {n_company} ({pct(j02, n_company)})")


async def _examples(conn: asyncpg.Connection, org: str, pids: list, table: str) -> None:
    if not pids:
        return
    rows = await conn.fetch(
        "SELECT display_name, (SELECT email FROM person_email pe WHERE pe.person_id=p.id LIMIT 1) email "
        "FROM person p WHERE org_id=$1 AND id = ANY($2::uuid[]) LIMIT 5",
        org, [str(p) for p in pids],
    )
    for r in rows:
        print(f"          e.g. name={r['display_name']!r} email={r['email']!r}")


async def main() -> None:
    conn = await asyncpg.connect(GLOBAL)
    try:
        orgs = await discover_orgs(conn)
        if not orgs:
            print("NO ORGS WITH DATA — run the live ingest and/or seed first.")
            return
        print(f"measuring {len(orgs)} org(s): {[o[0] for o in orgs]}")
        for label, org in orgs:
            await measure(conn, label, org)
    finally:
        await conn.close()


asyncio.run(main())

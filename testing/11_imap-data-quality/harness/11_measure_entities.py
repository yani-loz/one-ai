"""DQ measurement 2/3 — noise entities, lost signal, classification, provenance.

Covers C (spurious entities), D (under-creation / lost counterparties), E (flag correctness),
I (provenance / dead fields). Read-only. One run measures every org with data (LIVE + DQ-SEED).

Run: docker compose exec -T backend python - < testing/11_imap-data-quality/harness/11_measure_entities.py
"""
from __future__ import annotations

import asyncio
from collections import Counter

import asyncpg

from app.core.config import get_settings
from app.entities.services.address_rules import is_generic_email_domain, is_role_address
from app.entities.services.email_normalizer import extract_domain, normalize_email

S = get_settings()
GLOBAL = (
    f"postgresql://{S.global_db_user}:{S.oneai_global_password}"
    f"@{S.postgres_host}:{S.postgres_port}/{S.postgres_db}"
)
DEV_ORG = "d1500000-0000-0000-0000-000000000001"


def pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.2f}%" if d else "n/a"


def edit_distance_le1(a: str, b: str) -> bool:
    """True iff Levenshtein(a, b) <= 1 — cheap typo detector for freemail look-alikes."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long):
        if short[i] != long[j]:
            if skipped:
                return False
            skipped = True
            j += 1
        else:
            i += 1
            j += 1
    return True


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


async def all_addresses(conn: asyncpg.Connection, org: str) -> list[str]:
    addrs = [
        r["from_address"]
        for r in await conn.fetch(
            "SELECT from_address FROM email_message WHERE org_id=$1 AND from_address IS NOT NULL", org
        )
    ]
    addrs += [
        r["address"]
        for r in await conn.fetch("SELECT address FROM email_recipient WHERE org_id=$1", org)
    ]
    return addrs


async def measure(conn: asyncpg.Connection, label: str, org: str) -> None:
    print(f"\n{'=' * 70}\n=== {label} org {org} ===\n{'=' * 70}")
    n_person = await conn.fetchval("SELECT count(*) FROM person WHERE org_id=$1", org)
    n_company = await conn.fetchval("SELECT count(*) FROM company WHERE org_id=$1", org)
    n_msg = await conn.fetchval("SELECT count(*) FROM email_message WHERE org_id=$1", org)

    mailbox_domains = {
        extract_domain(normalize_email(c["username"]))
        for c in await conn.fetch("SELECT username FROM connector_connection WHERE org_id=$1", org)
    }
    print(f"totals: persons={n_person} companies={n_company} messages={n_msg} mailbox_domains={mailbox_domains}")

    existing_company_domains = {
        r["domain"] for r in await conn.fetch("SELECT domain FROM company_domain WHERE org_id=$1", org)
    }

    # — DQ-C01 automated/list senders still mint a person —
    c01 = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND is_automated=true "
        "AND from_person_id IS NOT NULL", org
    )
    c01_msg = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND is_automated=true", org
    )
    c01_persons = await conn.fetchval(
        "SELECT count(DISTINCT from_person_id) FROM email_message WHERE org_id=$1 "
        "AND is_automated=true AND from_person_id IS NOT NULL", org
    )
    print(f"[DQ-C01] automated msgs that minted a from-person: {c01}/{c01_msg} automated; "
          f"distinct automated-sender persons={c01_persons}")

    # — DQ-C02 persons only ever reply_to / sender (routing identities) —
    c02 = await conn.fetchval(
        """
        SELECT count(*) FROM (
            SELECT person_id FROM email_recipient WHERE org_id=$1 AND person_id IS NOT NULL
              AND kind IN ('reply_to','sender')
            EXCEPT
            SELECT person_id FROM email_recipient WHERE org_id=$1 AND person_id IS NOT NULL
              AND kind IN ('to','cc','bcc')
            EXCEPT
            SELECT from_person_id FROM email_message WHERE org_id=$1 AND from_person_id IS NOT NULL
        ) t
        """,
        org,
    )
    print(f"[DQ-C02] persons referenced ONLY as reply_to/sender: {c02}")

    # — DQ-C03 freemail-typo companies / DQ-C04 malformed-domain companies —
    generic_list = [
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
        "gmx.de", "gmx.net", "web.de", "t-online.de", "aol.com", "proton.me",
    ]
    c03, c04 = [], []
    for d in existing_company_domains:
        ds = str(d)
        if "." not in ds or ds.startswith("[") or "_" in ds or ds.endswith(".") or " " in ds:
            c04.append(ds)
        elif any(edit_distance_le1(ds, g) for g in generic_list):
            c03.append(ds)
    print(f"[DQ-C03] companies on freemail-typo domains: {len(c03)}  {c03[:6]}")
    print(f"[DQ-C04] companies on malformed domains (IP/single-label/underscore): {len(c04)}  {c04[:6]}")

    # — DQ-D01 role-address domains LOST from the company graph (high-value DACH counterparties) —
    addrs = await all_addresses(conn, org)
    role_dom_vol: Counter = Counter()
    any_dom_vol: Counter = Counter()
    for a in addrs:
        na = normalize_email(a)
        d = extract_domain(na)
        if not d or is_generic_email_domain(d):
            continue
        any_dom_vol[d] += 1
        if is_role_address(na):
            role_dom_vol[d] += 1
    d01_absent = sorted(
        ((d, v) for d, v in role_dom_vol.items() if d not in existing_company_domains),
        key=lambda x: -x[1],
    )
    print(f"[DQ-D01] non-generic domains seen ONLY via role addrs, absent from company graph: "
          f"{len(d01_absent)} (top by volume)")
    for d, v in d01_absent[:8]:
        print(f"          {v:5}x  {d}")

    # — DQ-D03 any high-volume non-generic domain absent from company graph —
    d03_absent = sorted(
        ((d, v) for d, v in any_dom_vol.items() if d not in existing_company_domains),
        key=lambda x: -x[1],
    )
    print(f"[DQ-D03] non-generic domains absent from company graph (any cause): {len(d03_absent)} (top)")
    for d, v in d03_absent[:8]:
        print(f"          {v:5}x  {d}")

    # — DQ-E01 is_internal correctness vs mailbox domain —
    real_mbx = {d for d in mailbox_domains if d and not is_generic_email_domain(d)}
    pe = await conn.fetch(
        "SELECT pe.person_id, pe.email, p.is_internal FROM person_email pe "
        "JOIN person p ON p.id=pe.person_id WHERE pe.org_id=$1", org
    )
    should_internal_false_flagged = 0  # own-domain person but is_internal=false
    should_external_true_flagged = 0  # not own-domain but is_internal=true
    pdom: dict[object, tuple[set, bool]] = {}
    for r in pe:
        d = extract_domain(normalize_email(r["email"]))
        doms, _ = pdom.get(r["person_id"], (set(), r["is_internal"]))
        doms.add(d)
        pdom[r["person_id"]] = (doms, r["is_internal"])
    for _pid, (doms, internal) in pdom.items():
        owns_mbx = any(d in real_mbx for d in doms)
        if real_mbx and owns_mbx and not internal:
            should_internal_false_flagged += 1
        if real_mbx and not owns_mbx and internal:
            should_external_true_flagged += 1
    print(f"[DQ-E01] is_internal drift: own-domain-but-external={should_internal_false_flagged}; "
          f"external-but-internal={should_external_true_flagged} (real mailbox domains={real_mbx})")

    # — DQ-E02 inbound direction from an internal person —
    e02 = await conn.fetchval(
        "SELECT count(*) FROM email_message m JOIN person p ON p.id=m.from_person_id "
        "WHERE m.org_id=$1 AND m.direction='inbound' AND p.is_internal=true", org
    )
    dir_counts = await conn.fetch(
        "SELECT direction, count(*) c FROM email_message WHERE org_id=$1 GROUP BY direction", org
    )
    print(f"[DQ-E02] inbound msgs from an internal person: {e02}; direction mix="
          f"{ {r['direction']: r['c'] for r in dir_counts} }")

    # — DQ-E03 automation header present but is_automated=false (false negatives) —
    e03 = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND is_automated=false "
        "AND (headers ? 'List-Unsubscribe' OR headers ? 'List-Id' OR headers ? 'List-unsubscribe')",
        org,
    )
    print(f"[DQ-E03] list-mail headers but is_automated=false: {e03}")

    # — DQ-I01 company_domain.source never set —
    i01 = await conn.fetchval(
        "SELECT count(*) FROM company_domain WHERE org_id=$1 AND source IS NULL", org
    )
    i01t = await conn.fetchval("SELECT count(*) FROM company_domain WHERE org_id=$1", org)
    pe_src = await conn.fetchval(
        "SELECT count(*) FROM person_email WHERE org_id=$1 AND source IS NOT NULL", org
    )
    pe_t = await conn.fetchval("SELECT count(*) FROM person_email WHERE org_id=$1", org)
    print(f"[DQ-I01] company_domain.source NULL: {i01}/{i01t} ({pct(i01, i01t)}); "
          f"person_email.source SET: {pe_src}/{pe_t}")

    # — DQ-I02 person_alias never written —
    i02 = await conn.fetchval("SELECT count(*) FROM person_alias WHERE org_id=$1", org)
    print(f"[DQ-I02] person_alias rows: {i02} (resolver writes none -> expect 0)")

    # — DQ-I03 language never populated —
    i03 = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND language IS NOT NULL", org
    )
    print(f"[DQ-I03] messages with language set: {i03}/{n_msg}")

    # — DQ-I04 headers JSONB bloat + sensitive headers —
    sz = await conn.fetchrow(
        "SELECT avg(pg_column_size(headers))::int avg, max(pg_column_size(headers)) max "
        "FROM email_message WHERE org_id=$1", org
    )
    sens = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND "
        "(headers ? 'DKIM-Signature' OR headers ? 'Authentication-Results' OR headers ? 'Received')",
        org,
    )
    print(f"[DQ-I04] headers JSONB bytes avg={sz['avg']} max={sz['max']}; "
          f"msgs carrying DKIM/Auth/Received headers: {sens}/{n_msg}")


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

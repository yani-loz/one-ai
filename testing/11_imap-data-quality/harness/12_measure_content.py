"""DQ measurement 3/3 — field/text quality, threading integrity, attachment content.

Covers F (field/text quality), G (threading), H (attachments). Read-only. One run measures every
org with data (LIVE + DQ-SEED).

Run: docker compose exec -T backend python - < testing/11_imap-data-quality/harness/12_measure_content.py
"""
from __future__ import annotations

import asyncio
import re

import asyncpg

from app.core.config import get_settings

S = get_settings()
GLOBAL = (
    f"postgresql://{S.global_db_user}:{S.oneai_global_password}"
    f"@{S.postgres_host}:{S.postgres_port}/{S.postgres_db}"
)
DEV_ORG = "d1500000-0000-0000-0000-000000000001"

# Mojibake (utf-8 decoded as latin-1): Ã followed by a continuation byte, plus the common Â / â‚¬.
MOJIBAKE_RE = r"Ã[-¿]|Â[-¿]|â‚¬|Ã¯Â¿Â½"
REPLACEMENT = "�"
RFC2047_RE = re.compile(r"=\?[^?]+\?[bBqQ]\?", re.IGNORECASE)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


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
    n_msg = await conn.fetchval("SELECT count(*) FROM email_message WHERE org_id=$1", org)
    n_att = await conn.fetchval("SELECT count(*) FROM email_attachment WHERE org_id=$1", org)
    print(f"totals: messages={n_msg} attachments={n_att}")

    # — DQ-F01 dirty display_name (RFC2047 leftover / control char / address-as-name) —
    names = await conn.fetch(
        "SELECT DISTINCT display_name FROM person WHERE org_id=$1 AND display_name IS NOT NULL "
        "AND btrim(display_name)<>''", org
    )
    rfc, ctrl, addr_as_name = [], [], []
    for r in names:
        nm = r["display_name"]
        if RFC2047_RE.search(nm):
            rfc.append(nm)
        if CONTROL_RE.search(nm):
            ctrl.append(nm)
        if "@" in nm and " " not in nm.strip():
            addr_as_name.append(nm)
    print(f"[DQ-F01] dirty display_name over {len(names)} distinct: rfc2047-leftover={len(rfc)} "
          f"control-char={len(ctrl)} address-as-name={len(addr_as_name)}")
    for sample, tag in ((rfc, "rfc2047"), (ctrl, "ctrl"), (addr_as_name, "addr")):
        if sample:
            print(f"          {tag} e.g. {sample[0]!r}")

    # — DQ-F02 body mojibake / replacement char —
    f02_moji = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND body_text ~ $2", org, MOJIBAKE_RE
    )
    f02_repl = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND strpos(body_text, $2) > 0",
        org, REPLACEMENT,
    )
    print(f"[DQ-F02] body mojibake-pattern msgs={f02_moji}; replacement-char msgs={f02_repl} / {n_msg}")

    # — DQ-F03 plain-part-wins hides rich HTML (tiny words, big bytes) —
    f03 = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND word_count < 5 AND size_bytes > 50000",
        org,
    )
    f03_ex = await conn.fetch(
        "SELECT subject, word_count, size_bytes FROM email_message WHERE org_id=$1 "
        "AND word_count < 5 AND size_bytes > 50000 ORDER BY size_bytes DESC LIMIT 4", org
    )
    print(f"[DQ-F03] word_count<5 AND size>50KB: {f03} / {n_msg}")
    for r in f03_ex:
        print(f"          wc={r['word_count']} size={r['size_bytes']} subj={r['subject']!r}")

    # — DQ-F04 degraded / failed parse rows —
    f04 = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND parse_status='failed'", org
    )
    f04_big = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND parse_status='failed' "
        "AND size_bytes > 50000", org
    )
    print(f"[DQ-F04] parse_status=failed: {f04} / {n_msg} ({pct(f04, n_msg)}); of which >50KB={f04_big}")

    # — DQ-F06 date sanity (epoch / far-future / sent>>received) —
    f06_epoch = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND sent_at < '1990-01-01'", org
    )
    f06_future = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND sent_at > now() + interval '2 days'", org
    )
    f06_skew = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND sent_at IS NOT NULL "
        "AND received_at IS NOT NULL AND sent_at > received_at + interval '2 days'", org
    )
    f06_nodate = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND sent_at IS NULL AND received_at IS NULL",
        org,
    )
    print(f"[DQ-F06] dates: epoch(<1990)={f06_epoch} future={f06_future} sent>>received={f06_skew} "
          f"both-null={f06_nodate}")

    # — DQ-F07 subject control char / RFC2047 leftover —
    f07_ctrl = await conn.fetchval(
        r"SELECT count(*) FROM email_message WHERE org_id=$1 AND subject ~ '[\x01-\x08\x0e-\x1f]'", org
    )
    f07_rfc = await conn.fetchval(
        "SELECT count(*) FROM email_message WHERE org_id=$1 AND subject ~* '=\\?[^?]+\\?[bq]\\?'", org
    )
    print(f"[DQ-F07] subject control-char={f07_ctrl}; rfc2047-leftover={f07_rfc}")

    # — DQ-G01 Message-ID collisions (same id, different content) —
    g01 = await conn.fetch(
        "SELECT message_id, count(*) c, count(DISTINCT from_address) frm, count(DISTINCT subject) subj, "
        "count(DISTINCT dedup_key) dk FROM email_message WHERE org_id=$1 AND message_id IS NOT NULL "
        "GROUP BY message_id HAVING count(DISTINCT dedup_key)>1 AND "
        "(count(DISTINCT from_address)>1 OR count(DISTINCT subject)>1) ORDER BY c DESC LIMIT 10", org
    )
    print(f"[DQ-G01] Message-IDs reused across DIFFERENT content: {len(g01)} (top shown)")
    for r in g01[:4]:
        print(f"          id={r['message_id']!r} copies={r['c']} distinct_from={r['frm']} distinct_subj={r['subj']}")

    # — DQ-G02 dangling references (refs / in_reply_to with no local message) —
    g02 = await conn.fetchrow(
        """
        SELECT count(*) total, count(*) FILTER (WHERE m2.message_id IS NULL) dangling FROM (
            SELECT unnest("references") ref FROM email_message
              WHERE org_id=$1 AND "references" IS NOT NULL
        ) r LEFT JOIN email_message m2 ON m2.org_id=$1 AND m2.message_id = r.ref
        """,
        org,
    )
    irt = await conn.fetchrow(
        "SELECT count(*) total, count(*) FILTER (WHERE m2.message_id IS NULL) dangling "
        "FROM email_message m1 LEFT JOIN email_message m2 "
        "ON m2.org_id=$1 AND m2.message_id=m1.in_reply_to "
        "WHERE m1.org_id=$1 AND m1.in_reply_to IS NOT NULL", org
    )
    print(f"[DQ-G02] references: {g02['dangling']}/{g02['total']} dangling; "
          f"in_reply_to: {irt['dangling']}/{irt['total']} dangling")

    # — DQ-H01 binary attachment text dropped (extracted_text NULL by content_type) —
    h01 = await conn.fetch(
        "SELECT content_type, count(*) c, count(*) FILTER (WHERE extracted_text IS NULL) nulls, "
        "sum(size_bytes) bytes FROM email_attachment WHERE org_id=$1 GROUP BY content_type "
        "ORDER BY c DESC LIMIT 15", org
    )
    doc_null = await conn.fetchval(
        "SELECT count(*) FROM email_attachment WHERE org_id=$1 AND extracted_text IS NULL AND "
        "(content_type ILIKE 'application/pdf' OR content_type ILIKE '%officedocument%' "
        "OR content_type ILIKE 'application/msword' OR content_type ILIKE '%ms-excel%')", org
    )
    print(f"[DQ-H01] business-doc attachments with NULL extracted_text: {doc_null}")
    print("          attachment content_type breakdown (type: count / null-text / bytes):")
    for r in h01[:12]:
        print(f"          {str(r['content_type'])[:48]:48} {r['c']:5} / {r['nulls']:5} / {r['bytes']}")

    # — DQ-H02 attachment text mojibake (utf-8-only decode) —
    h02 = await conn.fetchval(
        "SELECT count(*) FROM email_attachment WHERE org_id=$1 AND extracted_text IS NOT NULL "
        "AND (extracted_text ~ $2 OR strpos(extracted_text, $3) > 0)", org, MOJIBAKE_RE, REPLACEMENT
    )
    print(f"[DQ-H02] attachment extracted_text with mojibake/replacement: {h02}")

    # — DQ-H03 attachment metadata (RFC2047 filename, zero-byte) —
    h03_rfc = await conn.fetchval(
        "SELECT count(*) FROM email_attachment WHERE org_id=$1 AND filename ~* '=\\?[^?]+\\?[bq]\\?'", org
    )
    h03_zero = await conn.fetchval(
        "SELECT count(*) FROM email_attachment WHERE org_id=$1 AND size_bytes=0", org
    )
    print(f"[DQ-H03] attachment filename rfc2047-leftover={h03_rfc}; zero-byte attachments={h03_zero}")


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

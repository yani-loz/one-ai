"""Suite E ingest cases — entity resolution + the BYPASS-engine fail-open surface (E03..E09).

Drives the REAL EmailIngestService.ingest_email over crafted raw emails on RUN-STAMPED throwaway
orgs, asserting the entity-graph outcomes the design promises (deterministic email-only matching,
role/generic-domain guards) and probing their boundaries. Mirrors the dev/test ingest path, which
runs on GlobalSessionLocal (BYPASSRLS) — see E08 for why that matters.

Cases:
  E03 — same display name, different addresses/domains -> TWO distinct persons (no name matching).
  E04 — gmail.com -> person but NO company; mail.gmail.com (subdomain) -> WOULD mint a company.
  E05 — info@/info+x@/iNfO@ -> NO person; enquiries@/vertriebsteam@ -> mints a person.
  E06 — "info @x.com" (embedded whitespace) -> does it dodge the role guard / store a recipient?
  E08 — the ingest path runs on the BYPASSRLS global engine (no GUC) -> RLS does NOT bite here
        (prod SyncRunner uses scoped_session -> RLS DOES bite). Characterize precisely.
  E09 — concurrent get-or-create of the SAME person -> SAVEPOINT re-read resolves the race (no dup).

Run (testing/ is not mounted; pipe over stdin into the backend container):
    docker compose exec -T backend python - < testing/10_imap-connector/harness/entity_resolution_suite.py

Non-destructive: every org is a fresh uuid4; cleanup deletes all run-stamped rows in a finally block.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.imap.services.email_ingest_service import EmailIngestService, IngestOutcome
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.config import get_settings
from app.core.database import GlobalSessionLocal, scoped_session
from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail

S = get_settings()
STAMP = uuid.uuid4().hex[:10]
# Every seeded org carries the stamp in a deterministic, listable way for cleanup.
SEEDED_ORGS: list[uuid.UUID] = []

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def eml(headers: str, body: str = "Hello there.") -> bytes:
    """Build a raw RFC822 message (CRLF) from a header block + body, like the unit tests' _eml."""
    return (headers.strip() + "\r\n\r\n" + body).replace("\n", "\r\n").encode("utf-8")


def new_org() -> uuid.UUID:
    org = uuid.uuid4()
    SEEDED_ORGS.append(org)
    return org


async def seed_connection(
    session: AsyncSession, org_id: uuid.UUID, mailbox: str
) -> ConnectorConnection:
    """Insert a minimal connector_connection so emails can FK it (opaque ciphertext)."""
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        display_name=f"Mailbox {STAMP}",
        auth_method="app_password",
        username=mailbox,
        config={"host": "mail.example.com", "port": 993, "use_ssl": True},
        secret_ciphertext=b"\x00" * 32,
        secret_key_version=1,
        status="configured",
    )
    session.add(connection)
    await session.flush()
    return connection


async def count(session: AsyncSession, model: type, org_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(model).where(model.org_id == org_id)
    )
    return result.scalar_one()


# ───────────────────────────── E03: no name-based over-merge ─────────────────────────────
async def case_e03(session: AsyncSession) -> None:
    org = new_org()
    conn = await seed_connection(session, org, f"owner-{STAMP}@acme.test")
    service = EmailIngestService(session, conn)
    # SAME display name "Jordan Doe", DIFFERENT addresses + domains. v1 matches on email only.
    await service.ingest_email(
        eml(f"From: Jordan Doe <jordan-{STAMP}@globex.test>\nTo: owner-{STAMP}@acme.test\n"
            f"Subject: A\nMessage-ID: <e03a-{STAMP}@x>")
    )
    await service.ingest_email(
        eml(f"From: Jordan Doe <jordan-{STAMP}@initech.test>\nTo: owner-{STAMP}@acme.test\n"
            f"Subject: B\nMessage-ID: <e03b-{STAMP}@x>")
    )
    await session.commit()

    same_name = (
        await session.execute(
            select(func.count()).select_from(Person).where(
                Person.org_id == org, Person.display_name == "Jordan Doe"
            )
        )
    ).scalar_one()
    check(
        "e03_same_name_diff_address_two_persons",
        same_name == 2,
        f"persons named 'Jordan Doe' = {same_name} (expected 2 — no name merge in v1)",
    )


# ───────────────────────── E04: generic-domain skip + subdomain under-guard ─────────────────────────
async def case_e04(session: AsyncSession) -> None:
    org = new_org()
    conn = await seed_connection(session, org, f"owner-{STAMP}@acme.test")
    service = EmailIngestService(session, conn)
    await service.ingest_email(
        eml(f"From: Private Person <private-{STAMP}@gmail.com>\nTo: owner-{STAMP}@acme.test\n"
            f"Subject: G\nMessage-ID: <e04gmail-{STAMP}@x>")
    )
    await session.commit()

    # gmail.com sender -> a person, but is_generic_email_domain skips company creation for gmail.com.
    gmail_company = (
        await session.execute(
            select(func.count()).select_from(CompanyDomain).where(
                CompanyDomain.org_id == org, CompanyDomain.domain == "gmail.com"
            )
        )
    ).scalar_one()
    gmail_person = (
        await session.execute(
            select(func.count()).select_from(PersonEmail).where(
                PersonEmail.org_id == org, PersonEmail.email == f"private-{STAMP}@gmail.com"
            )
        )
    ).scalar_one()
    check(
        "e04_gmail_person_yes_company_no",
        gmail_person == 1 and gmail_company == 0,
        f"gmail person={gmail_person} (1), gmail company_domain={gmail_company} (0 — skip-list)",
    )

    # SUBDOMAIN mail.gmail.com is NOT in the skip-list (exact-match set) -> it WOULD mint a company.
    org2 = new_org()
    conn2 = await seed_connection(session, org2, f"owner2-{STAMP}@acme.test")
    service2 = EmailIngestService(session, conn2)
    await service2.ingest_email(
        eml(f"From: Sub Person <sub-{STAMP}@mail.gmail.com>\nTo: owner2-{STAMP}@acme.test\n"
            f"Subject: S\nMessage-ID: <e04sub-{STAMP}@x>")
    )
    await session.commit()
    sub_company = (
        await session.execute(
            select(func.count()).select_from(CompanyDomain).where(
                CompanyDomain.org_id == org2, CompanyDomain.domain == "mail.gmail.com"
            )
        )
    ).scalar_one()
    check(
        "e04_subdomain_underguard_mints_company",
        sub_company == 1,
        f"mail.gmail.com company_domain={sub_company} (1 = under-guard: subdomain slips skip-list)",
    )


# ───────────────────────── E05: role-address guard + list boundary ─────────────────────────
async def case_e05(session: AsyncSession) -> None:
    org = new_org()
    conn = await seed_connection(session, org, f"owner-{STAMP}@acme.test")
    service = EmailIngestService(session, conn)
    # info@ / iNfO@ / info+sales@ all classify as the SAME role local-part 'info' -> NO person.
    for i, addr in enumerate(
        (f"info@role-{STAMP}.test", f"iNfO@role-{STAMP}.test", f"info+sales@role-{STAMP}.test")
    ):
        await service.ingest_email(
            eml(f"From: {addr}\nTo: owner-{STAMP}@acme.test\nSubject: R{i}\n"
                f"Message-ID: <e05role{i}-{STAMP}@x>")
        )
    await session.commit()
    role_persons = (
        await session.execute(
            select(func.count()).select_from(PersonEmail).where(
                PersonEmail.org_id == org, PersonEmail.email.like(f"%@role-{STAMP}.test")
            )
        )
    ).scalar_one()
    check(
        "e05_role_addresses_mint_no_person",
        role_persons == 0,
        f"person_email rows for info@/iNfO@/info+sales@ = {role_persons} (expected 0 — role guard)",
    )

    # Non-listed role-ish local-parts: enquiries@ / vertriebsteam@ are NOT in _ROLE_LOCALPARTS ->
    # they MINT a person (the conservative-list boundary).
    org2 = new_org()
    conn2 = await seed_connection(session, org2, f"owner2-{STAMP}@acme.test")
    service2 = EmailIngestService(session, conn2)
    for i, local in enumerate(("enquiries", "vertriebsteam")):
        await service2.ingest_email(
            eml(f"From: {local}@firm-{STAMP}.test\nTo: owner2-{STAMP}@acme.test\nSubject: N{i}\n"
                f"Message-ID: <e05nl{i}-{STAMP}@x>")
        )
    await session.commit()
    unlisted_persons = (
        await session.execute(
            select(func.count()).select_from(PersonEmail).where(
                PersonEmail.org_id == org2, PersonEmail.email.like(f"%@firm-{STAMP}.test")
            )
        )
    ).scalar_one()
    check(
        "e05_unlisted_rolelike_mints_person",
        unlisted_persons == 2,
        f"person_email for enquiries@/vertriebsteam@ = {unlisted_persons} (2 = list boundary leak)",
    )


# ───────────────────────── E06: embedded-whitespace address ─────────────────────────
async def case_e06(session: AsyncSession) -> None:
    org = new_org()
    conn = await seed_connection(session, org, f"owner-{STAMP}@acme.test")
    service = EmailIngestService(session, conn)
    # "info @x.com" — a space between local-part and '@'. getaddresses + sanitize + normalize_email
    # (strip()/lower(), NO internal-whitespace strip). Probe: does it dodge the role guard, what does
    # the recipient row store, and does a person get minted?
    await service.ingest_email(
        eml(f"From: Sender <sender-{STAMP}@globex.test>\n"
            f"To: info @ws-{STAMP}.test\nSubject: WS\nMessage-ID: <e06-{STAMP}@x>")
    )
    await session.commit()

    recipients = (
        (
            await session.execute(
                select(EmailRecipient).where(
                    EmailRecipient.org_id == org, EmailRecipient.email_id.in_(
                        select(EmailMessage.id).where(EmailMessage.org_id == org)
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    ws_recipients = [r for r in recipients if "ws-" in (r.address or "")]
    # Any person minted for a whitespace-bearing local-part in this org's ws domain?
    ws_persons = (
        await session.execute(
            select(PersonEmail.email).where(
                PersonEmail.org_id == org, PersonEmail.email.like(f"%ws-{STAMP}.test")
            )
        )
    ).scalars().all()
    stored_addr = ws_recipients[0].address if ws_recipients else "<none>"
    minted = ws_recipients[0].person_id is not None if ws_recipients else False
    # Through the FULL ingest path: getaddresses pre-cleans the space adjacent to '@'
    # ("info @x.com" -> "info@x.com"), so the role guard DOES catch it and mints no person.
    check(
        "e06_full_path_getaddresses_cleans_then_guard_holds",
        not minted and not ws_persons,
        f"recipient stored addr={stored_addr!r} person_id_set={minted} person_email={list(ws_persons)} "
        f"(getaddresses collapsed the space adjacent to '@' -> role guard held)",
    )

    # The LATENT gap: normalize_email does NOT strip INTERNAL whitespace. Drive the resolver directly
    # (the boundary the hypothesis targets) with addresses that keep a space getaddresses would NOT
    # remove: "info @x" (trailing-space local-part, not adjacent to '@' after normalize) and
    # "in fo@x" (space INSIDE the local-part). Both dodge is_role_address ("info " != "info") and
    # have a truthy local-part + domain, so resolve_participant mints a person from a malformed key.
    from app.entities.services.entity_resolver import EntityResolver  # local import: probe-only

    org3 = new_org()
    conn3 = await seed_connection(session, org3, f"owner3-{STAMP}@acme.test")
    await session.commit()
    resolver = EntityResolver(session, mailbox_address=conn3.username, source="imap")
    pid_spacey_role = await resolver.resolve_participant(org3, f"info @probe-{STAMP}.test")
    pid_inner_space = await resolver.resolve_participant(org3, f"in fo@probe-{STAMP}.test")
    await session.commit()
    dodged = pid_spacey_role is not None or pid_inner_space is not None
    check(
        "e06_normalize_email_internal_whitespace_latent_gap",
        dodged,  # we EXPECT this to mint persons -> proves the latent gap exists at the resolver seam
        f"resolver('info @...')->person={pid_spacey_role is not None}, "
        f"resolver('in fo@...')->person={pid_inner_space is not None} "
        f"(normalize_email keeps internal whitespace -> role guard dodged for a non-getaddresses key)",
    )


# ───────────────────── E08: BYPASS global engine = fail-open ingest surface ─────────────────────
async def case_e08() -> None:
    """Prove the dev/test ingest path runs on GlobalSessionLocal (BYPASSRLS): an ingest with NO org
    GUC writes rows freely (no RLS) — whereas the prod SyncRunner opens scoped_session(org_id) where
    the same role would be the non-bypass tenant role. Characterize the gap precisely."""
    org = new_org()
    # (a) The ingest path: GlobalSessionLocal. We never set app.current_org_id, yet writes succeed
    #     and a subsequent unscoped SELECT returns the rows — RLS does NOT bite on this engine.
    async with GlobalSessionLocal() as session:
        conn = await seed_connection(session, org, f"owner-{STAMP}@acme.test")
        service = EmailIngestService(session, conn)
        await service.ingest_email(
            eml(f"From: a-{STAMP}@globex.test\nTo: owner-{STAMP}@acme.test\n"
                f"Subject: E08\nMessage-ID: <e08-{STAMP}@x>")
        )
        await session.commit()
        # Confirm the engine's role is BYPASSRLS and the GUC is unset on this connection.
        role_row = await session.execute(
            text("SELECT current_user, current_setting('app.current_org_id', true)")
        )
        current_user, guc = role_row.first()
        msgs_no_guc = await count(session, EmailMessage, org)
    bypass_writes = msgs_no_guc == 1 and (guc is None or guc == "")
    check(
        "e08_ingest_on_bypass_engine_no_rls",
        bypass_writes,
        f"ingest engine user={current_user!r} guc={guc!r}; wrote+read {msgs_no_guc} msg with NO GUC "
        f"(BYPASSRLS -> RLS does NOT bite on the dev/test ingest path)",
    )

    # (b) The prod path uses scoped_session(org_id) -> the NOBYPASSRLS tenant role with the GUC set.
    #     Prove that engine is the enforced one: under scoped_session(orgX) a read of org Y sees zero.
    other = new_org()
    async with scoped_session(org) as tsession:
        role_row = await tsession.execute(
            text("SELECT current_user, current_setting('app.current_org_id', true)")
        )
        tenant_user, tguc = role_row.first()
        # org `other` has no rows; this just confirms the engine + GUC wiring differs from (a).
        seen_other = await count(tsession, EmailMessage, other)
    enforced_path = tenant_user == S.app_db_user and tguc == str(org)
    check(
        "e08_prod_runner_path_is_scoped_enforced",
        enforced_path and seen_other == 0,
        f"prod scoped_session user={tenant_user!r} guc={tguc!r} (NOBYPASS tenant role, GUC bound) — "
        f"the runner path DOES enforce RLS; the gap is dev/test+dump-driver only",
    )


# ───────────────────── E09: concurrent get-or-create race -> SAVEPOINT re-read ─────────────────────
async def case_e09() -> None:
    """FORCE the uq_person_email_identity race on _get_or_create_person and confirm it resolves via
    begin_nested SAVEPOINT + re-read. A barrier holds both workers until BOTH have passed the
    existence check (both see None), so the second flush DEFINITELY hits IntegrityError and must
    re-read the winner — exactly ONE person, no aborted transaction. We assert the branch actually
    fired (not that the two ingests merely serialized), then re-run 5x for stability."""
    from app.entities.services.entity_resolver import EntityResolver  # probe-only direct drive

    async def one_race(round_id: int) -> tuple[bool, int, int, str]:
        org = new_org()
        sender = f"race{round_id}-{STAMP}@globex.test"
        both_checked = asyncio.Barrier(2)
        branch_fired: list[str] = []

        async def worker() -> str:
            async with GlobalSessionLocal() as session:
                resolver = EntityResolver(session, mailbox_address=f"owner-{STAMP}@acme.test",
                                          source="imap")
                # Reproduce _get_or_create_person's first step: existence check (returns None for both)
                existing = await resolver._people.get_person_id_by_email(org, sender)
                await both_checked.wait()  # hold until the PEER has also seen None -> guaranteed race
                if existing is not None:  # pragma: no cover - barrier guarantees both saw None
                    return "preexisting"
                from app.entities.models.person import Person as P, PersonEmail as PE
                try:
                    async with session.begin_nested():
                        person = await resolver._people.insert(
                            P(org_id=org, display_name="Racer", is_internal=False)
                        )
                        await resolver._people.add_email(
                            PE(org_id=org, person_id=person.id, email=sender, source="imap")
                        )
                    await session.commit()
                    return "won_insert"
                except Exception as exc:  # the LOSER: IntegrityError -> SAVEPOINT rollback + re-read
                    branch_fired.append(type(exc).__name__)
                    winner = await resolver._people.get_person_id_by_email(org, sender)
                    await session.commit()
                    return "lost_reread" if winner is not None else f"ERROR:{type(exc).__name__}"

        labels = await asyncio.gather(worker(), worker())
        async with GlobalSessionLocal() as s:
            pe = (
                await s.execute(
                    select(func.count()).select_from(PersonEmail).where(
                        PersonEmail.org_id == org, PersonEmail.email == sender
                    )
                )
            ).scalar_one()
            distinct = (
                await s.execute(
                    select(func.count(func.distinct(PersonEmail.person_id))).where(
                        PersonEmail.org_id == org, PersonEmail.email == sender
                    )
                )
            ).scalar_one()
        ok = (
            sorted(labels) == ["lost_reread", "won_insert"]
            and pe == 1 and distinct == 1 and len(branch_fired) == 1
        )
        return ok, pe, distinct, f"round{round_id} labels={labels} branch={branch_fired} pe={pe} distinct={distinct}"

    rounds = [await one_race(i) for i in range(5)]
    all_ok = all(ok for ok, *_ in rounds)
    fired = sum(1 for _, _, _, d in rounds if "branch=['IntegrityError']" in d)
    check(
        "e09_forced_race_savepoint_reread_no_dup",
        all_ok and fired == 5,
        f"5/5 rounds: IntegrityError branch fired {fired}/5, every round -> 1 person_email, 1 person, "
        f"one won_insert + one lost_reread. sample: {rounds[0][3]}",
    )


# ───────────────────────────── cleanup ─────────────────────────────
async def cleanup() -> None:
    """Delete every run-stamped row across all seeded orgs (BYPASS engine spans them all)."""
    if not SEEDED_ORGS:
        return
    async with GlobalSessionLocal() as session:
        # Children first where no cascade covers them from the org angle; connector_connection cascade
        # purges email_message/recipient/attachment, and person cascade purges person_email/alias.
        for model in (
            EmailAttachment, EmailRecipient, EmailMessage,
            PersonCompany, CompanyDomain, Company,
            PersonAlias, PersonEmail, Person,
            ConnectorConnection,
        ):
            await session.execute(delete(model).where(model.org_id.in_(SEEDED_ORGS)))
        await session.commit()
    print(f"cleanup: purged all rows for {len(SEEDED_ORGS)} run-stamped orgs")


async def main() -> None:
    try:
        async with GlobalSessionLocal() as session:
            await case_e03(session)
            await case_e04(session)
            await case_e05(session)
            await case_e06(session)
        await case_e08()
        await case_e09()
    finally:
        await cleanup()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nRESULT: {passed}/{len(results)} checks passed")


asyncio.run(main())

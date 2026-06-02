async def main():
    import asyncpg  # _common.py provides no DB helper; asyncpg used for atomic ground-truth reads

    db = await asyncpg.connect(host="db", user="oneai", password="oneai", database="oneai", port=5432)

    async def erase_with_pw(c, plat_token, org_id, *, confirm_slug, password, reason="GDPR offboarding (test)"):
        """Live /erase requires a sudo-style `password` re-auth field absent from the reviewed
        source + the _common.erase_org helper; inline the POST so the body carries it."""
        return await c.post(f"/platform/orgs/{org_id}/erase", headers=bearer(plat_token),
                            json={"reason": reason, "confirm_slug": confirm_slug, "password": password})

    async def gt(org_id):
        """Snapshot the erasure-relevant ground truth for one org."""
        users = await db.fetchval("SELECT count(*) FROM users WHERE org_id=$1", org_id)
        tokens = await db.fetchval(
            "SELECT count(*) FROM refresh_tokens WHERE subject_type='user' "
            "AND subject_id IN (SELECT id FROM users WHERE org_id=$1)", org_id)
        decider_set = await db.fetchval(
            "SELECT count(*) FROM support_grant WHERE org_id=$1 AND decided_by_email IS NOT NULL", org_id)
        status = await db.fetchval("SELECT status FROM organizations WHERE id=$1", org_id)
        erased_rows = await db.fetchval(
            "SELECT count(*) FROM audit_log WHERE action='org.erased' AND org_id=$1", org_id)
        return {"users": users, "tokens": tokens, "decider_set": decider_set,
                "status": status, "erased_rows": erased_rows}

    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "hold-er1")
        org_id = org["org_id"]
        slug = org["slug"]
        admin_token = org["admin_access"]
        oid = __import__("uuid").UUID(org_id)

        # — set up a support grant so there is a decided_by_email to scrub-check —
        rr = await request_support(c, plat, org_id)
        print("request_support:", rr.status_code, rr.text[:300])
        grant_id = rr.json()["id"]
        ar = await company_approve(c, admin_token, grant_id)
        print("company_approve:", ar.status_code, ar.text[:300])

        before = await gt(oid)
        print("BASELINE:", before)

        assert before["users"] == 1, f"setup: expected 1 user, got {before['users']}"
        assert before["tokens"] >= 1, f"setup: expected >=1 token, got {before['tokens']}"
        assert before["decider_set"] == 1, f"setup: decided_by_email not set ({before['decider_set']})"
        assert before["status"] == "active", f"setup: status {before['status']}"

        # — place the legal hold and VERIFY it took (this appends an org.legal_hold.set row) —
        ph = await patch_legal_hold(c, plat, org_id, True)
        det = await get_org_detail(c, plat, org_id)
        lh = det.json().get("legal_hold")
        print("patch_legal_hold(true):", ph.status_code, "| legal_hold now:", lh)
        assert ph.status_code == 200 and lh is True, "setup: legal hold did not take"

        # snapshot audit AFTER the hold-set, immediately before erase — isolates the erase's effect
        ab = await get_org_audit(c, plat, org_id)
        audit_len_before = len(ab.json()) if ab.status_code == 200 else None
        print("audit_len_before_erase:", audit_len_before)

        # — attempt the erase WITH the platform admin password (must 409 — held) —
        er = await erase_with_pw(c, plat, org_id, confirm_slug=slug, password=PLATFORM_PW)
        print("ERASE (held):", er.status_code, er.text[:400])

        after = await gt(oid)
        aa = await get_org_audit(c, plat, org_id)
        audit_len_after = len(aa.json()) if aa.status_code == 200 else None
        print("AFTER:", after, "audit_len_after:", audit_len_after)

        held_409 = er.status_code == 409
        untouched = (after["users"] == before["users"] and after["tokens"] == before["tokens"]
                     and after["decider_set"] == before["decider_set"] and after["status"] == "active"
                     and after["erased_rows"] == 0 and audit_len_after == audit_len_before)
        print("RESULT 409:", held_409, "| nothing_touched:", untouched)
        print("VERDICT:", "PASS" if (held_409 and untouched) else "FAIL")

        # — clear the hold; leave H un-erased for the auditor —
        pc = await patch_legal_hold(c, plat, org_id, False)
        print("cleanup patch_legal_hold(false):", pc.status_code, "| ORG_ID:", org_id, "SLUG:", slug)

    await db.close()


asyncio.run(main())

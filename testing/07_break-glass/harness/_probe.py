# Harness validation probe for Targets 07 + 08 — NOT a test case. Concatenate after _common.py:
#   cat testing/07_break-glass/harness/_common.py \
#       testing/07_break-glass/harness/_probe.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        plat, _ = await platform_login_pair(c)
        a = await provision_company(c, plat, "bgprobea")
        b = await provision_company(c, plat, "bgprobeb")
        print(f"1) provisioned A={a['org_id']} B={b['org_id']}")

        # — PC-05 break-glass lifecycle on A —
        req = await request_support(c, plat, a["org_id"])
        gid = req.json()["id"]
        print(f"2) request(A): {req.status_code} status={req.json().get('status')} is_active={req.json().get('is_active')} (expect 201/requested/False)")

        ina = await company_inbox(c, a["admin_access"])
        inb = await company_inbox(c, b["admin_access"])
        print(f"3) inbox A sees grant={any(g['id']==gid for g in ina.json())}(True)  inbox B sees grant={any(g['id']==gid for g in inb.json())}(False)")

        xap = await company_approve(c, b["admin_access"], gid)
        print(f"4) cross-tenant approve(B-admin, A's grant): {xap.status_code} (expect 404)")

        ap = await company_approve(c, a["admin_access"], gid)
        print(f"5) approve(A-admin): {ap.status_code} status={ap.json().get('status')} is_active={ap.json().get('is_active')} has_expiry={bool(ap.json().get('expires_at'))} (expect 200/approved/True)")

        ap2 = await company_approve(c, a["admin_access"], gid)
        print(f"6) approve again: {ap2.status_code} (expect 409 state-machine)")

        ct = await c.post(f"/platform/orgs/{a['org_id']}/support-requests", headers=bearer(a["admin_access"]), json={"reason": "x"})
        pt = await c.post(f"/support-access/{gid}/approve", headers=bearer(plat))
        print(f"7) audience: company->platform request={ct.status_code} platform->company approve={pt.status_code} (expect 401/401)")

        req2 = await request_support(c, plat, a["org_id"], reason="second")
        gid2 = req2.json()["id"]
        forged_company = forge_company_token(sub=str(uuid4()), org_id=a["org_id"], role="company_admin")
        fap = await c.post(f"/support-access/{gid2}/approve", headers=bearer(forged_company))
        print(f"8) FORGED company_admin token (org A) approves: {fap.status_code} (200 => consent forgeable via dev secret, documented)")

        aud = await get_org_audit(c, plat, a["org_id"])
        print(f"9) org A audit actions: {[e['action'] for e in aud.json()] if aud.status_code==200 else aud.text}")

        # — PC-06 erasure on fresh org C —
        cc = await provision_company(c, plat, "erprobec")
        await patch_legal_hold(c, plat, cc["org_id"], True)
        held = await erase_org(c, plat, cc["org_id"], confirm_slug=cc["slug"])
        print(f"10) erase under legal hold: {held.status_code} (expect 409 — nothing deleted)")
        await patch_legal_hold(c, plat, cc["org_id"], False)
        wrong = await erase_org(c, plat, cc["org_id"], confirm_slug="not-the-slug")
        print(f"11) erase wrong slug: {wrong.status_code} (expect 400)")
        cert = await erase_org(c, plat, cc["org_id"], confirm_slug=cc["slug"])
        cb = cert.json()
        print(f"12) erase: {cert.status_code} users_erased={cb.get('users_erased')} tokens_deleted={cb.get('tokens_deleted')} audit_retained={cb.get('audit_log_retained')} status={cb.get('status')} (users>0 proves the hold blocked nothing earlier)")
        gone = await login(c, cc["admin_email"], DEFAULT_PW)
        print(f"13) login erased-org admin: {gone.status_code} (expect 401 — user gone)")
        exp = await compliance_export(c, plat, cc["org_id"])
        print(f"14) compliance export after erase: {exp.status_code} audit_entries={len(exp.json().get('audit',[])) if exp.status_code==200 else exp.text}")

        d = await provision_company(c, plat, "erprobed")
        ghost = forge_platform_token()
        fe = await erase_org(c, ghost, d["org_id"], confirm_slug=d["slug"])
        print(f"15) FORGED platform token erases org D: {fe.status_code} (200 => forged-token erasure of any tenant, documented)")


asyncio.run(main())

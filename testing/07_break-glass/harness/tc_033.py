async def main() -> None:
    async with _client() as c:
        plat, _ = await platform_login_pair(c)
        a = await provision_company(c, plat, "aea33")
        admin = a["admin_access"]
        print(f"1) provisioned A={a['org_id']}")

        # grant 1: request -> approve  (support.requested + support.approved w/ expires_at)
        g1 = (await request_support(c, plat, a["org_id"], reason="audit g1")).json()["id"]
        await company_approve(c, admin, g1)
        # grant 2: request -> deny     (support.requested + support.denied)
        g2 = (await request_support(c, plat, a["org_id"], reason="audit g2")).json()["id"]
        await company_deny(c, admin, g2)
        # grant 3: request -> revoke   (platform-side revoke: support.requested + support.revoked)
        g3 = (await request_support(c, plat, a["org_id"], reason="audit g3")).json()["id"]
        await platform_revoke_request(c, plat, g3)
        print("2) grant1 request->approve, grant2 request->deny, grant3 request->revoke driven")

        aud = await get_org_audit(c, plat, a["org_id"])
        entries = aud.json()
        actions = [e["action"] for e in entries]
        needed = {"support.requested", "support.approved", "support.denied", "support.revoked"}
        superset = needed.issubset(set(actions))
        print(f"3) GET /platform/orgs/{{A}}/audit -> {aud.status_code}")
        print(f"   actions present: {actions}")
        print(f"   superset of {{requested,approved,denied,revoked}}? -> {superset}")

        approved = next((e for e in entries if e["action"] == "support.approved"), None)
        details = approved["details"] if approved else {}
        has_expiry = "expires_at" in details
        print(f"4) support.approved details = {details}")
        print(f"   has expires_at? -> {has_expiry}")

        ok = aud.status_code == 200 and superset and has_expiry
        print("PASS all four actions logged; support.approved carries expires_at"
              if ok else "FAIL audit emission incomplete")


asyncio.run(main())

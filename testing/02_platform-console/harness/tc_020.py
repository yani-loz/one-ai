async def main() -> None:
    print("== TC-PC-020 — company-aud token w/ REAL admin sub -> /platform/me (DISCRIMINATING) ==")
    async with _client() as c:
        # Control: real platform token returns the real admin identity (proves sub is live+active).
        p_access, _ = await platform_login_pair(c)
        me = await c.get("/platform/me", headers=bearer(p_access))
        print("[control] GET /platform/me (real platform token):", me.status_code)
        print("          body:", me.json())
        real_admin_id = me.json()["id"]
        print("[forge]   real admin id used as company-token sub:", real_admin_id)

        # Attack: company-aud token whose sub IS that real admin id. org_id is irrelevant
        # (the audience check fails before org_id is ever read). Any uuid is fine.
        forged = forge_company_token(sub=real_admin_id, org_id=str(uuid4()))
        attack = await c.get("/platform/me", headers=bearer(forged))
        print("[attack]  GET /platform/me (FORGED company-aud token, sub=real admin id):",
              attack.status_code)
        print("          body:", attack.json())

        ok = attack.status_code == 401 and me.status_code == 200
        print("RESULT:", "PASS — audience guard is load-bearing (401 is audience, not not-found)"
              if ok else f"FAIL — expected control 200 / attack 401, got {me.status_code}/{attack.status_code}")


asyncio.run(main())

async def main() -> None:
    print("== TC-PC-026 — company-aud token w/ role=platform_admin + REAL admin sub -> 401 (audience, not role) ==")
    async with _client() as c:
        # Control: fetch the REAL admin id (so the 401 cannot be a not-found false-green).
        p_access, _ = await platform_login_pair(c)
        me = await c.get("/platform/me", headers=bearer(p_access))
        real_admin_id = me.json()["id"]
        print("[control] GET /platform/me (real platform token):", me.status_code,
              "-> real admin id", real_admin_id)

        # Escalated COMPANY-aud token: role self-promoted to 'platform_admin', sub = REAL admin id.
        # aud is still 'company' -> the audience guard must reject regardless of the role claim.
        forged = forge_company_token(
            sub=real_admin_id, org_id=str(uuid4()), role="platform_admin"
        )
        attack = await c.get("/platform/me", headers=bearer(forged))
        print("[attack]  GET /platform/me (company-aud, role=platform_admin, sub=real admin):",
              attack.status_code)
        print("          body:", attack.json())

        ok = me.status_code == 200 and attack.status_code == 401
        print("RESULT:", "PASS — boundary is the AUDIENCE not the role (company token can't self-promote)"
              if ok else f"FAIL — control={me.status_code} attack={attack.status_code} (expected 200/401)")


asyncio.run(main())

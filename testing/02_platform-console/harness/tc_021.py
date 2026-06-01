async def main() -> None:
    print("== TC-PC-021 — company refresh rejected on /platform/refresh WITHOUT revoking (AC3b) ==")
    async with _client() as c:
        p_access, _ = await platform_login_pair(c)
        comp = await provision_company(c, p_access, "xdom")
        company_refresh = comp["admin_refresh"]
        print("[setup]   provisioned company:", comp["slug"], "email", comp["admin_email"])

        # ORDER IS LOAD-BEARING: present to /platform/refresh FIRST (must reject without revoking).
        pr = await c.post("/platform/refresh", json={"refresh_token": company_refresh})
        print("[attack]  POST /platform/refresh (company refresh):", pr.status_code)
        print("          body:", pr.json())

        # THEN the SAME token at /auth/refresh must still rotate -> proves it was NOT revoked.
        ar = await c.post("/auth/refresh", json={"refresh_token": company_refresh})
        print("[proof]   POST /auth/refresh (SAME company refresh):", ar.status_code)
        body = ar.json() if ar.status_code == 200 else ar.text
        if ar.status_code == 200:
            new_access = body.get("access_token")
            new_refresh = body.get("refresh_token")
            rotated = bool(new_access) and bool(new_refresh) and new_refresh != company_refresh
            print("          new pair issued? access?", bool(new_access),
                  "refresh?", bool(new_refresh), "rotated(diff)?", rotated)
        else:
            print("          body:", body)
            rotated = False

        ok = pr.status_code == 401 and ar.status_code == 200 and rotated
        print("RESULT:", "PASS — subject_type guard rejected WITHOUT revoking (token still rotated)"
              if ok else f"FAIL — platform={pr.status_code} auth={ar.status_code} rotated={rotated}")


asyncio.run(main())

async def main() -> None:
    print("== TC-OL-002 — pre-suspension refresh token blocked under suspension (AC3) ==")
    async with _client() as c:
        p_access, _ = await platform_login_pair(c)
        comp = await provision_company(c, p_access, "sus002")
        oid = comp["org_id"]
        pre_refresh = comp["admin_refresh"]  # minted BEFORE suspension
        print("[setup]   org", oid, "pre-suspension refresh captured")

        s = await patch_status(c, p_access, oid, STATUS_SUSPENDED)
        print(f"[suspend]  PATCH status=suspended: {s.status_code} status={s.json().get('status')}")

        r = await c.post("/auth/refresh", json={"refresh_token": pre_refresh})
        print(f"[refresh]  /auth/refresh pre-suspension token: {r.status_code} body={r.content!r}")

        await patch_status(c, p_access, oid, STATUS_ACTIVE)  # cleanup

        ok = r.status_code == 403
        print("RESULT:", "PASS — suspended org cannot extend its session; refresh blocked (403)"
              if ok else f"FAIL — refresh returned {r.status_code}, expected 403")


asyncio.run(main())

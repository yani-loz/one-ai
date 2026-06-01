async def main() -> None:
    print("== TC-OL-003 — does the refresh token SURVIVE the suspension 403? (HIGHEST VALUE) ==")
    async with _client() as c:
        p_access, _ = await platform_login_pair(c)
        comp = await provision_company(c, p_access, "sus003")
        oid = comp["org_id"]
        pre_refresh = comp["admin_refresh"]
        token_hash = sha256_hex(pre_refresh)
        # The token hash is the server's storage form — print it so a psql ground-truth
        # check (revoked_at IS NULL after the 403, before reactivation) can key on it.
        print(f"[setup]   org {oid}")
        print(f"[setup]   TOKEN_HASH={token_hash}")

        # Suspend, then present the pre-suspension refresh token -> expect 403.
        s = await patch_status(c, p_access, oid, STATUS_SUSPENDED)
        print(f"[suspend]  PATCH status=suspended: {s.status_code} status={s.json().get('status')}")
        r1 = await c.post("/auth/refresh", json={"refresh_token": pre_refresh})
        print(f"[403?]     1st present under suspension: {r1.status_code} body={r1.content!r}")

        # PAUSE here for the psql ground-truth probe (run separately, keyed on TOKEN_HASH).
        # Then reactivate and present the SAME token AGAIN — neutral observation:
        #   200 + rotated  => the staged revoke was rolled back (CORRECT, CONFIRMS-FIXED)
        #   401            => the failed refresh silently BURNED the session (NEW DEFECT)
        ra = await patch_status(c, p_access, oid, STATUS_ACTIVE)
        print(f"[react]    PATCH status=active: {ra.status_code} status={ra.json().get('status')}")
        r2 = await c.post("/auth/refresh", json={"refresh_token": pre_refresh})
        print(f"[2nd]      same token after reactivation: {r2.status_code} body={r2.content!r}")

        if r2.status_code == 200:
            new_refresh = r2.json().get("refresh_token")
            rotated = bool(new_refresh) and new_refresh != pre_refresh
            print(f"[rotated?] new refresh differs from old? {rotated}")
            verdict = ("SURVIVED -> CONFIRMS-FIXED (staged revoke rolled back on the 403)"
                       if rotated else "200 but NOT rotated -> ANOMALY, investigate")
        elif r2.status_code == 401:
            verdict = "BURNED -> NEW DEFECT (suspension-failed refresh silently revoked the session)"
        else:
            verdict = f"UNEXPECTED {r2.status_code}"
        print("RESULT:", f"1st={r1.status_code} 2nd={r2.status_code} -> {verdict}")


asyncio.run(main())

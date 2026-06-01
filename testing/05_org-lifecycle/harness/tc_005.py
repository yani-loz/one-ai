async def main() -> None:
    print("== TC-OL-005 — FULL blast radius: access token reaches company endpoints under suspension ==")
    async with _client() as c:
        p_access, _ = await platform_login_pair(c)
        comp = await provision_company(c, p_access, "sus005")
        oid = comp["org_id"]
        pre_access = comp["admin_access"]  # company-ADMIN access token, pre-suspension
        print("[setup]   org", oid, "pre-suspension company_admin access token captured")

        s = await patch_status(c, p_access, oid, STATUS_SUSPENDED)
        print(f"[suspend]  PATCH status=suspended: {s.status_code} status={s.json().get('status')}")

        # Access-token paths never re-check org status. New SESSIONS are blocked (login/refresh
        # = TC-001/002), but in-flight access tokens keep working until they expire (~15 min).
        me = await c.get("/auth/me", headers=bearer(pre_access))
        print(f"[me]       GET /auth/me:  {me.status_code}")
        users = await c.get("/users", headers=bearer(pre_access))
        print(f"[users]    GET /users:    {users.status_code} (count={len(users.json()) if users.status_code==200 else 'n/a'})")

        # An ADMIN MUTATION under suspension — create a user — also still works (worst case).
        new_email = f"blast-{stamp()}@oneai.dev"
        created = await c.post("/users", headers=bearer(pre_access),
                               json={"email": new_email, "full_name": "Blast User",
                                     "password": DEFAULT_PW, "role": "member"})
        print(f"[create]   POST /users (mutation): {created.status_code} email={new_email}")

        # login is the NEW-session path -> must be blocked (the immediate side).
        fresh = await login(c, comp["admin_email"], DEFAULT_PW)
        print(f"[login]    fresh login (new session): {fresh.status_code} (expect 403 — immediate cutoff)")

        await patch_status(c, p_access, oid, STATUS_ACTIVE)  # cleanup

        ungated = me.status_code == 200 and users.status_code == 200
        mutation = created.status_code == 201
        immediate = fresh.status_code == 403
        print("RESULT:", f"access-path ungated={ungated} mutation_allowed={mutation} new-session-blocked={immediate}")
        print("MEANING: suspension is IMMEDIATE for new sessions (login/refresh 403) but EVENTUAL"
              " (<= access-token TTL ~15 min) for in-flight access tokens — the no-access-token-denylist deferral.")


asyncio.run(main())

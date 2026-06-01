# Lead finisher for the SUSPEND-gap + forged cases the workflow didn't complete
# (004 me-asymmetry, 005 blast-radius, 006 offboarded-gap, 007 e2e, 008 reversible,
#  024 forged-DoS, 025 forged-legal-hold). Each case provisions its OWN run-stamped org.
#   cat testing/05_org-lifecycle/harness/_common.py \
#       testing/05_org-lifecycle/harness/_finish_suspend.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        plat, _ = await platform_login_pair(c)

        # 004 — /auth/me asymmetry: a PRE-suspension access token still works under suspension.
        comp = await provision_company(c, plat, "sus004")
        await patch_status(c, plat, comp["org_id"], STATUS_SUSPENDED)
        me = await c.get("/auth/me", headers=bearer(comp["admin_access"]))
        print(f"[004] /auth/me (pre-susp access) under suspension: {me.status_code} (expect 200 — asymmetry)")
        await patch_status(c, plat, comp["org_id"], STATUS_ACTIVE)

        # 005 — FULL blast radius: the pre-susp ADMIN access token still reaches /users under suspension.
        comp = await provision_company(c, plat, "sus005")
        await patch_status(c, plat, comp["org_id"], STATUS_SUSPENDED)
        users = await c.get("/users", headers=bearer(comp["admin_access"]))
        newlogin = await login(c, comp["admin_email"], DEFAULT_PW)
        print(f"[005] /users (pre-susp admin access) under suspension: {users.status_code} (expect 200); "
              f"NEW login under suspension: {newlogin.status_code} (expect 403) "
              "=> immediate for new sessions, eventual (<=access TTL) for in-flight")
        await patch_status(c, plat, comp["org_id"], STATUS_ACTIVE)

        # 006 — only 'suspended' blocks: offboarded + onboarding still log in.
        comp = await provision_company(c, plat, "sus006")
        for st in (STATUS_OFFBOARDED, STATUS_ONBOARDING):
            await patch_status(c, plat, comp["org_id"], st)
            r = await login(c, comp["admin_email"], DEFAULT_PW)
            print(f"[006] status={st} -> login: {r.status_code} (expect 200; only 'suspended' blocks — offboarded cutoff is PC-06)")
        await patch_status(c, plat, comp["org_id"], STATUS_ACTIVE)

        # 007 — end-to-end via the endpoint (AC5).
        comp = await provision_company(c, plat, "sus007")
        s = await patch_status(c, plat, comp["org_id"], STATUS_SUSPENDED)
        l1 = await login(c, comp["admin_email"], DEFAULT_PW)
        ra = await patch_status(c, plat, comp["org_id"], STATUS_ACTIVE)
        l2 = await login(c, comp["admin_email"], DEFAULT_PW)
        print(f"[007] e2e: PATCH suspend={s.status_code} -> login={l1.status_code} (403) -> "
              f"PATCH reactivate={ra.status_code} -> login={l2.status_code} (200)")

        # 008 — the gate fully lifts for refresh too after reactivation.
        comp = await provision_company(c, plat, "sus008")
        await patch_status(c, plat, comp["org_id"], STATUS_SUSPENDED)
        rblock = await c.post("/auth/refresh", json={"refresh_token": comp["admin_refresh"]})
        await patch_status(c, plat, comp["org_id"], STATUS_ACTIVE)
        _a2, r2 = await company_login_pair(c, comp["admin_email"])
        rot = await c.post("/auth/refresh", json={"refresh_token": r2})
        print(f"[008] suspend->refresh={rblock.status_code} (403); reactivate-> fresh-login refresh rotates={rot.status_code} (200)")

        # 024 — FORGED platform token suspends an org (availability kill). CONFIRMS_DOCUMENTED.
        comp = await provision_company(c, plat, "xdom024")
        ghost = forge_platform_token()
        fs = await patch_status(c, ghost, comp["org_id"], STATUS_SUSPENDED)
        gate = await login(c, comp["admin_email"], DEFAULT_PW)
        ra = await patch_status(c, plat, comp["org_id"], STATUS_ACTIVE)
        gate2 = await login(c, comp["admin_email"], DEFAULT_PW)
        print(f"[024] FORGED platform token PATCH suspended={fs.status_code} (200) -> real login now={gate.status_code} (403) "
              f"=> forged write reached the auth gate; reactivated via REAL token={ra.status_code} login restored={gate2.status_code} (200)  org={comp['org_id']}")

        # 025 — FORGED platform token sets a legal hold (compliance blast radius). CONFIRMS_DOCUMENTED.
        comp = await provision_company(c, plat, "xdom025")
        ghost = forge_platform_token()
        fh = await patch_legal_hold(c, ghost, comp["org_id"], True)
        rb = await get_org_detail(c, plat, comp["org_id"])
        print(f"[025] FORGED platform token PATCH legal-hold true={fh.status_code} (200); read-back legal_hold={rb.json().get('legal_hold')} (True)")
        await patch_legal_hold(c, plat, comp["org_id"], False)


asyncio.run(main())

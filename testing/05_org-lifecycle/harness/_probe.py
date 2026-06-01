# Harness validation probe — NOT a test case. Concatenate after _common.py and pipe into
# the backend container to prove every helper + preview the ⭐ suspend-gate cases:
#   cat testing/05_org-lifecycle/harness/_common.py \
#       testing/05_org-lifecycle/harness/_probe.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        p_access, _p_refresh = await platform_login_pair(c)
        print("1) platform_login_pair OK")

        comp = await provision_company(c, p_access, "probe")
        oid, email = comp["org_id"], comp["admin_email"]
        print(f"2) provision_company OK: org={oid} admin={email}")

        det = await get_org_detail(c, p_access, oid)
        print(f"3) GET detail: {det.status_code} fields={sorted(det.json().keys()) if det.status_code==200 else det.text}")

        # ⭐ SUSPEND GATE
        s = await patch_status(c, p_access, oid, STATUS_SUSPENDED)
        print(f"4) PATCH status=suspended: {s.status_code} -> status={s.json().get('status') if s.status_code==200 else s.text}")

        blocked = await login(c, email, DEFAULT_PW)
        print(f"5) suspended-org login (valid creds): {blocked.status_code} (expect 403)")

        me = await c.get("/auth/me", headers=bearer(comp["admin_access"]))
        print(f"6) /auth/me with PRE-suspension token: {me.status_code} (expect 200 — asymmetry)")

        rr = await c.post("/auth/refresh", json={"refresh_token": comp["admin_refresh"]})
        print(f"7) refresh PRE-suspension token under suspension: {rr.status_code} (expect 403)")

        oracle = await login(c, email, "wrong-password-xyz")
        print(f"8) suspended-org login (WRONG pw): {oracle.status_code} (expect 401 — NO oracle, not 403)")

        ra = await patch_status(c, p_access, oid, STATUS_ACTIVE)
        back = await login(c, email, DEFAULT_PW)
        print(f"9) reactivate -> login: patch={ra.status_code} login={back.status_code} (expect 200/200)")

        # offboarded does NOT block (deliberate PC-06 gap)
        await patch_status(c, p_access, oid, STATUS_OFFBOARDED)
        off = await login(c, email, DEFAULT_PW)
        print(f"10) offboarded -> login: {off.status_code} (expect 200 — only 'suspended' blocks)")
        await patch_status(c, p_access, oid, STATUS_ACTIVE)

        # FORGED platform token (dev secret) suspends an arbitrary org → DoS (documented)
        ghost = forge_platform_token()
        fs = await patch_status(c, ghost, oid, STATUS_SUSPENDED)
        print(f"11) FORGED platform token PATCH status=suspended: {fs.status_code} (200 => forged-token DoS)")
        await patch_status(c, p_access, oid, STATUS_ACTIVE)

        # enum validation
        bad = await patch_status(c, p_access, oid, "DELETED")
        case = await patch_status(c, p_access, oid, "Suspended")
        print(f"12) invalid status 'DELETED'={bad.status_code} 'Suspended'(case)={case.status_code} (expect 422/422)")

        unknown = await get_org_detail(c, p_access, str(uuid4()))
        print(f"13) GET detail unknown id: {unknown.status_code} (expect 404)")


asyncio.run(main())
